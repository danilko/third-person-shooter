package com.openworld.carrier.vehicle;

import com.openworld.character.*;
import com.openworld.character.Character;
import com.openworld.world.SpatialEntityGrid;
import com.openworld.world.StimulusManager;
import com.openworld.world.manager.ExplosionManager;
import com.openworld.game.EventBus;
import com.openworld.net.NetworkManager;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.*;
import godot.core.*;
import godot.core.MethodCallable;
import godot.global.GD;

import java.lang.Object;
import java.util.ArrayList;
import java.util.Collection;
import java.util.UUID;
import com.openworld.ai.vehicle.VehicleAIController;
import com.openworld.camera.VehicleCameraController;
import com.openworld.control.Controllable;
import com.openworld.control.Controller;
import com.openworld.control.PlayerController;
import com.openworld.control.UserCommand;
import com.openworld.game.GameManager;
import com.openworld.item.Pickup;
import com.openworld.net.NetworkController;
import com.openworld.net.RigidSnapshotInterpolator;
import com.openworld.net.VehicleNetworkController;
import com.openworld.net.VehicleSeatPolicy;
import com.openworld.weapon.IconRegistry;
import com.openworld.weapon.WeaponController;

/**
 * Arcade-drift vehicle — hybrid steering + bounded-impulse grip.
 *
 * Two distinct control modes:
 *
 *   NORMAL:  direct vehicle steering, camera auto-follows vehicle heading.
 *            Lateral grip pulls velocity toward heading (bounded impulse, stable).
 *
 *   DRIFT:   camera auto-rotates at fixed rate in drift direction (same as original).
 *            Vehicle yaw-aligns to (camFwd + driftAngle offset).
 *            Left/right steering adjusts the drift arc angle [5°–45°].
 *            Low grip lets velocity diverge from heading — the gap IS the slide.
 *            The camera sweeping ahead while the car body lags behind creates the
 *            visible "car sideways" effect without any camera-velocity blend tricks.
 *
 * Lateral grip in both modes uses a BOUNDED IMPULSE:
 *   correction = clamp(-lateralSlip, -gripAccel×dt, +gripAccel×dt)
 *   applyImpulse(vehRight × correction × mass)
 * This is frame-rate independent and never produces the runaway 200 kN+ forces
 * from the old (-slip/dt)×mass×damp formula.
 *
 * All physics constants and combat/damage/wreck config live in VehicleConfig.
 * Assign a .tres preset in the inspector; leave null to use built-in DEFAULTS.
 */
@Script(className = "Vehicle")
public class Vehicle extends RigidBody3D implements Controllable, NameplateTarget {

    /** Group tag for vehicles spawned at runtime by {@code ZoneManager} (ambient traffic, I3b) —
     *  as opposed to scene-authored vehicles that already exist on every peer. Only members of this
     *  group are announced over {@code MSG_VEHICLE_SPAWN} and replayed in the late-join baseline. */
    public static final String STREAMED_GROUP = "streamed_vehicle";

    // ── Inspector exports ─────────────────────────────────────────────────────

    @Export public CharacterInfo characterInfo;

    /**
     * Per-vehicle-type config (suspension, power, damage, wreck, etc.).
     * Null = shared DEFAULTS singleton with the original hard-coded values.
     * Swap a different .tres preset to change vehicle archetype with zero code changes.
     */
    @Export public VehicleConfig vehicleConfig;

    // Scene-structure paths — node positions are scene-specific, not config.
    @Export public NodePath wheelsPath     = new NodePath("Wheels");
    @Export public NodePath driverSeatPath = new NodePath("DriverSeat");
    @Export public NodePath vehicleCamPath = new NodePath("ActiveCamera");

    // Damage-source names stamped onto elimination events; also the IconRegistry keys
    // each peer registers the vehicle icon under so a vehicle kill resolves its icon
    // on remote peers (kept as shared constants so the damage site and registration
    // can never drift apart).
    public static final String DAMAGE_SOURCE_COLLISION = "Vehicle";
    public static final String DAMAGE_SOURCE_EXPLOSION = "Vehicle Explosion";

    // ── Shared DEFAULTS (one instance, never mutated) ─────────────────────────
    private static final VehicleConfig DEFAULTS = new VehicleConfig();

    /** Returns the active config, falling back to DEFAULTS when none is assigned. */
    public VehicleConfig getConfig() {
        return vehicleConfig != null ? vehicleConfig : DEFAULTS;
    }

    // ── Runtime state ─────────────────────────────────────────────────────────

    protected Controller             controller;
    protected Health                 healthNode;
    /** Driver (seat 0) — kept as a field alias of seatOccupants[0] so the many driver-centric
     *  call sites (nameplate colour, carjack, weapon routing, AI eviction) stay unchanged. */
    protected Character              occupant;

    // ── Multi-seat (driver = seat 0, passengers = 1..n) ───────────────────────
    /** Seat anchors from the scene's `Seats` children; falls back to the legacy single DriverSeat. */
    private final ArrayList<Node3D>  seatNodes = new ArrayList<>();
    private Character[]              seatOccupants = new Character[1];

    private Node3D                   driverSeatNode;
    private Camera3D                 vehicleCamera;
    private VehicleCameraController  camController;
    private WeaponController         vehicleWeaponController;
    private final ArrayList<VehicleWheel> wheels = new ArrayList<>();

    private boolean slipping    = false;
    private boolean braking     = false;
    private boolean handBraking = false;
    private boolean justEntered = false;
    /** The tick's gathered command — readable by carrier subclasses' applyLocomotion overrides. */
    protected UserCommand cmd = new UserCommand();

    private final java.util.HashSet<Character> activeCollisions = new java.util.HashSet<>();
    /** Counts down between VEHICLE_CRASH stimulus posts so a sustained scrape alerts AI at most ~1×/s (E2). */
    private double crashStimulusCooldown = 0.0;

    // ── Parked/idle stability (anti character-push + slope-creep) ─────────────
    // The body has a zero-friction physics material and wheel forces every frame, so it
    // never sleeps on its own — a CharacterBody3D depenetrating against it slides it, and
    // the parking *friction* coefficient can only slow slope-creep, never stop it. Parked
    // uses RigidBody SLEEPING, never freeze: freeze is the puppet mechanism, and the ~1 Hz
    // occupancy self-heal sweep re-runs applyAuthorityState → setFreezeEnabled(false) on
    // every locally simulated vehicle, which would silently un-park a freeze-based park.
    /** Continuous low-speed idle dwell (s) accumulated toward the parked transition. */
    private double parkTimer = 0.0;
    /** True while parked: wheel-force loop skipped, RigidBody asleep. */
    private boolean parked = false;
    /** Ground state from the last simulated wheel loop — read by the _integrateForces lock. */
    private boolean lastGrounded = false;

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    @Register
    @Override
    public void _ready() {
        if (characterInfo == null) characterInfo = new CharacterInfo();
        // Privatize a scene-embedded (shared) CharacterInfo before stamping our id. The sub-resource
        // in Vehicle.tscn is shared by every instantiation unless copied; an empty characterId means
        // "scene-supplied" (code-spawned bodies stamp a UUID before addChild), so copy it into a fresh
        // instance so the stamp below can't rewrite a sibling vehicle's identity (the traffic aliasing
        // bug). Done in code rather than resource_local_to_scene, which threw a JVM Shared Buffer Error.
        else if (characterInfo.characterId == null || characterInfo.characterId.isEmpty()) {
            characterInfo = CharacterInfo.copyOf(characterInfo);
        }
        // ONE vehicle identity model (mirrors Character._ready): a host-stamped UUID, never a
        // node path. Scene-placed vehicles used to derive their id from getPath() on the theory
        // it was peer-identical — but that broke across ownership migration / per-peer tree
        // differences (a client's exit request carried '/root/World/VehicleRoot', which the host
        // could not resolve → NO_VEHICLE). Now scene vehicles replicate exactly like the
        // pre-placed Player: the client frees its local copy on connect
        // (NetworkManager.removeLocalPrePlacedVehicles) and the host re-supplies it with THIS
        // UUID via the late-join baseline, so every peer keys off the same id. Runtime-spawned
        // vehicles still set their id BEFORE addChild, so this fallback only fires for the host's
        // (or single-player's) own scene/spawn body.
        if (characterInfo.characterId == null || characterInfo.characterId.isEmpty()) {
            characterInfo.characterId = UUID.randomUUID().toString();
        }
        addToGroup(new StringName("characters"), false);
        // Register in the spatial grid so AI target discovery finds vehicle occupants in O(k)
        // (PLAN.md Part D / D1). Stagger the first re-bucket like Character does.
        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) grid.register(this, getGlobalPosition());
        gridUpdateTimer = GD.randfRange(0f, (float) GRID_UPDATE_INTERVAL);
        setContactMonitor(true);
        setMaxContactsReported(8);

        Node h = getNodeOrNull("Health");
        if (h instanceof Health hn) {
            healthNode = hn;
            healthNode.died.connectUnsafe(
                    MethodCallable.createUnsafe(this, "onVehicleDestruction"),
                    godot.api.Object.ConnectFlags.DEFAULT);
            // Damage wakes a parked (sleeping) body so it reacts to what follows the hit.
            healthNode.hit.connectUnsafe(
                    MethodCallable.createUnsafe(this, "onVehicleDamaged"),
                    godot.api.Object.ConnectFlags.DEFAULT);
        }

        VehicleConfig cfg = getConfig();
        // Register the vehicle's kill-feed icon under the same damage-source keys it
        // stamps onto eliminations, so a vehicle kill resolves its icon on every peer
        // (the registry is local-only; textures never cross the wire — see IconRegistry).
        if (cfg.vehicleIcon != null) {
            com.openworld.weapon.IconRegistry.register(DAMAGE_SOURCE_COLLISION, cfg.vehicleIcon);
            com.openworld.weapon.IconRegistry.register(DAMAGE_SOURCE_EXPLOSION, cfg.vehicleIcon);
        }
        Node wheelsNode = getNodeOrNull("Wheels");
        if (wheelsNode != null) {
            for (Node child : wheelsNode.getChildren()) {
                if (child instanceof VehicleWheel w) {
                    w.setup(cfg);
                    wheels.add(w);
                }
            }
        } else if (requiresWheels()) {
            GD.printErr("[Vehicle] Wheels node missing — hover disabled!");
        }

        Node seat = getNodeOrNull(driverSeatPath.getPath());
        if (seat instanceof Node3D n) driverSeatNode = n;

        // Multi-seat: a `Seats` node's Node3D children are the seat anchors in index order
        // (Seat0 = driver). Legacy scenes without one keep working as single-seat via
        // DriverSeat. Seat0, when present, supersedes driverSeatPath as the driver anchor.
        Node seatsRoot = getNodeOrNull("Seats");
        if (seatsRoot != null) {
            for (Node child : seatsRoot.getChildren()) {
                if (child instanceof Node3D sn) seatNodes.add(sn);
            }
        }
        if (seatNodes.isEmpty() && driverSeatNode != null) seatNodes.add(driverSeatNode);
        if (!seatNodes.isEmpty()) driverSeatNode = seatNodes.get(0);
        seatOccupants = new Character[Math.max(1, seatNodes.size())];

        Node cam = getNodeOrNull(vehicleCamPath.getPath());
        if (cam instanceof Camera3D c) vehicleCamera = c;

        Node camCtrl = getNodeOrNull("CameraController");
        if (camCtrl instanceof VehicleCameraController vcc) camController = vcc;

        // Optional damage-tier emitters (DamageVfx/Smoke + DamageVfx/Fire) — degrade
        // gracefully when a vehicle scene ships without them.
        Node dv = getNodeOrNull("DamageVfx");
        if (dv != null) {
            if (dv.getNodeOrNull("Smoke") instanceof GPUParticles3D s) damageSmoke = s;
            if (dv.getNodeOrNull("Fire")  instanceof GPUParticles3D f) damageFire  = f;
        }

        Node wc = getNodeOrNull("WeaponController");
        if (wc instanceof WeaponController vwc) vehicleWeaponController = vwc;

        for (Node child : getChildren()) {
            if (child instanceof Controller c) { controller = c; break; }
        }
    }

    // ── Replication authority (Round 11 N3) ───────────────────────────────────

    /** True when this peer simulates the vehicle's physics — single-player, or this peer owns its locomotion. */
    public boolean isLocallySimulated() {
        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (!(netNode instanceof NetworkManager net) || !net.isNetworked()) return true;
        return net.isAuthorityFor(characterInfo);
    }

    /**
     * Reconciles this vehicle's controller + physics mode with its current locomotion
     * authority. Idempotent — called from occupancy application on every authority flip
     * (driver enter/exit) and lazily by NetworkManager on the first received vehicle
     * snapshot (covers scene-placed vehicles on a client, which have no join hook).
     *
     * Puppet (another peer simulates): attach a {@link VehicleNetworkController} and freeze
     * the RigidBody so local physics never fights the interpolator's kinematic placement.
     * Simulated: drop the puppet controller — seeding the live body's velocities from its
     * last sample so a handed-back vehicle coasts instead of stopping dead — and unfreeze.
     */
    public void applyAuthorityState() {
        if (isLocallySimulated()) {
            if (controller instanceof VehicleNetworkController vnc) {
                com.openworld.net.RigidSnapshotInterpolator.Sample last = vnc.latestSample();
                if (last != null) {
                    setLinearVelocity(new Vector3((float) last.linearVelocity().x(),
                            (float) last.linearVelocity().y(), (float) last.linearVelocity().z()));
                    setAngularVelocity(new Vector3((float) last.angularVelocity().x(),
                            (float) last.angularVelocity().y(), (float) last.angularVelocity().z()));
                }
                detachController();
                vnc.queueFree();
            }
            // Kotlin/JVM binding gotcha: Godot 4's `freeze` property is setFreezeEnabled.
            setFreezeEnabled(false);
        } else {
            if (controller == null) {
                attachController(new VehicleNetworkController());
            } else if (!(controller instanceof VehicleNetworkController)) {
                // A live controller on a body we don't own means an occupancy/ownership
                // mis-order — freeze anyway (replication wins) and leave evidence.
                GD.printErr("[Vehicle] " + characterInfo.characterId
                        + " lost authority while holding a live controller — freezing under it");
            }
            // Freeze (puppet) and parked-sleep are distinct mechanisms — never hold both, so
            // an authority handback always resumes from a clean (awake, unparked) slate.
            if (parked) unpark();
            parkTimer = 0.0;
            setFreezeEnabled(true);
        }
    }

    // ── Physics ───────────────────────────────────────────────────────────────

    // ── Spatial grid bookkeeping (PLAN.md Part D / D1) ───────────────────────
    private static final double GRID_UPDATE_INTERVAL = 0.25;
    private double gridUpdateTimer = 0.0;

    /** Throttled spatial-grid re-bucket — mirrors {@code Character.updateSpatialCell}. */
    private void updateSpatialCell(double delta) {
        gridUpdateTimer -= delta;
        if (gridUpdateTimer > 0.0) return;
        gridUpdateTimer = GRID_UPDATE_INTERVAL;
        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) grid.move(this, getGlobalPosition());
    }

    /** Drop out of the spatial grid when the vehicle leaves the tree (destroyed/despawned). */
    @Register
    @Override
    public void _exitTree() {
        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) grid.unregister(this);
    }

    @Register
    @Override
    public void _physicsProcess(double delta) {
        updateSpatialCell(delta);

        cmd.motor     = 0;
        cmd.steering  = 0;
        cmd.steerToTarget = false;
        cmd.handbrake = false;
        cmd.brake     = false;
        cmd.boost     = false;
        cmd.fire      = false;
        cmd.reload    = false;
        cmd.desiredWeapon = -1;

        if (controller != null && controller.isAuthority()) {
            UserCommand currentCmd = controller.gatherInput(delta);

            if (currentCmd.resetVehicle && getWeaponMode() != VehicleWeaponMode.PASSENGER_WEAPON) {
                resetOrientation();
            }
            if (currentCmd.enterExit && !justEntered) {
                requestExit();
            }
            justEntered = false;

            cmd.motor     = currentCmd.motor;
            cmd.steering  = currentCmd.steering;
            cmd.steerToTarget = currentCmd.steerToTarget;
            cmd.handbrake = currentCmd.handbrake;
            cmd.brake     = currentCmd.brake;
            cmd.boost     = currentCmd.boost;
            cmd.fire      = currentCmd.fire;
            cmd.reload    = currentCmd.reload;
            // Relay the passenger-weapon slot switch (PASSENGER_WEAPON mode): without this the
            // stale persistent cmd.desiredWeapon (-1) was forwarded every frame and number keys
            // never reached the seated occupant's WeaponController.
            cmd.desiredWeapon = currentCmd.desiredWeapon;
        }

        if (cmd.handbrake) {
            slipping    = true;
            handBraking = true;
        } else {
            handBraking = false;
        }
        braking = cmd.brake;

        // Wheel forces + center-of-mass tuning only run on the simulating peer (N3):
        // puppets are frozen and placed kinematically by VehicleNetworkController, which
        // also drives their wheel visuals from the replicated steering/speed.
        if (isLocallySimulated()) {
            VehicleConfig cfg = getConfig();
            float speed = (float) getLinearVelocity().length();
            boolean idle = isIdleInput();

            // A hard shove (ram, big blast impulse) crossed the threshold, or the driver
            // gave input — resume physics this frame.
            if (parked && (!idle || speed >= cfg.parkSpeedThreshold)) {
                unpark();
            }

            if (parked) {
                // An external nudge (small blast push, a body settling against us) can wake
                // the engine without crossing the speed threshold — re-assert sleep so the
                // zero-friction body never accumulates depenetration drift from a character
                // leaning on it.
                if (!isSleeping()) setSleeping(true);
            } else {
                boolean supported = applyLocomotion(cfg, delta);
                lastGrounded = supported;

                // Park evaluation: idle + supported + slow, held for parkDelaySeconds
                // (the dwell prevents sleep/wake flap at the threshold). AI traffic never
                // parks mid-drive — CruiseState always emits motor ≠ 0 and a junction hold
                // (BrakeState) emits brake = true, both of which fail isIdleInput().
                if (idle && supported && speed < cfg.parkSpeedThreshold) {
                    parkTimer += delta;
                    if (parkTimer >= cfg.parkDelaySeconds) {
                        parked = true;
                        setSleeping(true);
                    }
                } else {
                    parkTimer = 0.0;
                }
            }
        }

        // Drop a stale occupant pin BEFORE dereferencing it: under streamed traffic (PLAN.md I3c) a
        // seated AI driver can be freed/removed out from under us by a despawn race, and reading its
        // transform then throws `get_global_transform "!is_inside_tree"` → native use-after-free segfault.
        // isInstanceValid is checked first (short-circuit) so isInsideTree is never called on a freed node.
        // Every seat is validated + pinned (runs on every peer — puppets pin their riders too).
        for (int seat = 0; seat < seatOccupants.length; seat++) {
            Character rider = seatOccupants[seat];
            if (rider == null) continue;
            if (!GD.isInstanceValid(rider) || !rider.isInsideTree()) {
                seatOccupants[seat] = null;
                if (seat == 0) occupant = null;
                nameplateChanged.emit();
                continue;
            }
            Node3D anchor = seat < seatNodes.size() ? seatNodes.get(seat) : driverSeatNode;
            if (anchor == null) continue;
            rider.setGlobalPosition(anchor.getGlobalPosition());
            Vector3 occRot = rider.getGlobalRotation();
            occRot.setY((float) getGlobalRotation().getY());
            rider.setGlobalRotation(occRot);
        }

        // Weapon routing is authority-only (N3): on a puppet, the occupant's fire cues and
        // aim arrive via its own character snapshot stream — pushing the stale local vehicle
        // camera's aim target here would overwrite the replicated aim every physics tick.
        if (occupant != null && isLocallySimulated()) {
            VehicleWeaponMode mode = getWeaponMode();
            if (mode == VehicleWeaponMode.PASSENGER_WEAPON) {
                Vector3 aimTarget = camController != null
                        ? camController.getAimTarget()
                        : occupant.getGlobalPosition().plus(new Vector3(0f, 0f, -20f));
                occupant.applyPassengerWeaponInput(cmd.fire, cmd.reload, cmd.desiredWeapon, aimTarget);
            } else if (mode == VehicleWeaponMode.VEHICLE_WEAPON && vehicleWeaponController != null) {
                if (cmd.fire) vehicleWeaponController.onWeaponFire();
                else          vehicleWeaponController.onWeaponNotFire();
            }
        }
    }

    // ── Locomotion hook (carrier-type seam) ───────────────────────────────────

    /**
     * One simulated physics tick of locomotion — never called while parked or on puppets.
     * Base implementation = the raycast-wheel car. Carrier stubs ({@code Motorcycle},
     * {@code Boat}, {@code Airplane}) override or extend this one method; everything else
     * on this class (seats/occupancy, authority + puppet handling, Health/destruction/wreck,
     * snapshot replication, parked sleep, boost meter, damage tiers) is carrier-generic and
     * inherited unchanged. (A full {@code Carrier} base-class extraction — pure code motion —
     * stays open as a follow-up; this hook is the behavioural seam it would move along.)
     *
     * @return true when the body is supported (grounded / afloat) — feeds the parked
     *         evaluation and the _integrateForces static lock.
     */
    protected boolean applyLocomotion(VehicleConfig cfg, double delta) {
        boolean isGrounded = false;
        float speed = (float) getLinearVelocity().length();
        updateBoost(cfg, delta);
        // Throttle against the rolling direction is plain counter-thrust: S at speed
        // decelerates at full motor force and flows straight into (capped) reverse —
        // the reverseSpeedFraction ceiling in VehicleWheel is what keeps backing up slow.
        for (VehicleWheel w : wheels) {
            w.applyWheelPhysics((float) delta, (float) getPhysicsProcessDeltaTime(), cmd);
            w.applyWheelSteering((float) delta, cmd.steering, cmd.steerToTarget);
            w.applySkidMark();
            if (w.grounded()) isGrounded = true;
        }

        setCenterOfMassMode(CenterOfMassMode.CUSTOM);
        if (isGrounded) {
            setCenterOfMass(new Vector3(0f, -0.3f, 0f));
        } else {
            setCenterOfMass(Vector3.Companion.getDOWN().times(0.5f));
        }

        applyStabilityAssists(cfg, isGrounded, speed);
        // Quadratic aero drag (grounded or airborne) — the real high-speed limiter now
        // that rolling friction saturates; without it saturation would remove any ceiling.
        if (cfg.aeroDragCoefficient > 0f && speed > 1f) {
            applyCentralForce(getLinearVelocity().times(-cfg.aeroDragCoefficient * speed));
        }
        if (isGrounded) {
            applyFlatTirePull(cfg, speed);
            applyMomentumAlignment(cfg, delta);
            // Drift rotation (NFS-Carbon style): while the handbrake is held, steering
            // applies a direct yaw torque — full donuts and burnout spins under player
            // control, instead of the grip model's self-stalling equilibrium angle.
            // Authority needs motion OR throttle (a parked, brakeless car doesn't spin).
            if (cmd.handbrake && cfg.driftYawTorque > 0f && Math.abs(cmd.steering) > 0.01f) {
                double authority = GD.clamp(speed / 8.0 + Math.abs(cmd.motor), 0.0, 1.0);
                applyTorque(getGlobalBasis().getY()
                        .times(cmd.steering * cfg.driftYawTorque * authority));
            }
        }
        return isGrounded;
    }

    /**
     * Arcade corner-speed retention (the NFS/GTA "rail" turn): rotate the horizontal
     * velocity direction toward the body heading, preserving |v| — a gripping steered turn
     * redirects momentum instead of scrubbing it off through tire friction (without this a
     * full-speed corner bleeds ~25% speed; measured by the DriveTest harness). Skipped for
     * handbrake/slip so drift keeps its physics; flats weaken it (grip is what redirects).
     */
    private void applyMomentumAlignment(VehicleConfig cfg, double delta) {
        if (cfg.momentumAlignRate <= 0f || cmd.handbrake || isSlipping()) return;
        Vector3 vel   = getLinearVelocity();
        Vector3 horiz = new Vector3(vel.getX(), 0.0, vel.getZ());
        double  hs    = horiz.length();
        if (hs < 2.0) return;                       // parking/creep untouched
        Vector3 fwd = getGlobalBasis().getZ().times(-1);
        Vector3 fh  = new Vector3(fwd.getX(), 0.0, fwd.getZ());
        if (fh.length() < 1e-3) return;             // pointing straight up/down
        fh = fh.normalized();
        double dir = Math.signum(fh.dot(horiz));
        if (dir == 0.0) return;                     // pure sideways slide — nothing to align to

        double rate = cfg.momentumAlignRate;
        for (VehicleWheel w : wheels) {
            if (w.isFlat()) { rate *= cfg.flatGripScale; break; }
        }
        double w = Math.min(1.0, rate * delta);
        // Friction-circle budget: redirecting v at angular rate ω costs lateral accel v·ω,
        // so cap the per-tick rotation at maxLateralG — the assist must not corner harder
        // than the tires themselves are allowed to (same cap as the wheel lateral force).
        if (cfg.maxLateralG > 0f) {
            double angleGap = horiz.normalized().angleTo(fh.times(dir));
            double maxStep  = (cfg.maxLateralG * 9.81 / hs) * delta;   // ω_max·dt
            if (angleGap > 1e-4 && angleGap * w > maxStep) w = maxStep / angleGap;
        }
        Vector3 newDir = horiz.normalized().lerp(fh.times(dir), w).normalized();
        Vector3 newHoriz = newDir.times(hs);
        setLinearVelocity(new Vector3(newHoriz.getX(), vel.getY(), newHoriz.getZ()));
    }

    /** Wheel-less carrier subclasses (Boat/Airplane) suppress the missing-Wheels error. */
    protected boolean requiresWheels() { return true; }

    // ── High-speed stability assists (GTA-style anti-flip) ────────────────────

    /** Last angular-damp ground state applied, so the property is only written on change. */
    private Boolean lastDampGrounded = null;

    /**
     * Body-level stability, simulating peer only, after the wheel-force loop:
     * anti-roll bar (per-axle compression transfer), speed² downforce along −bodyUp
     * (banked roads press the car into their surface), grounded angular damping, and a
     * soft keep-upright torque. Airborne keeps low damping and no assists — jumps tumble
     * naturally, GTA-style. All tunables live in {@link VehicleConfig}.
     */
    protected void applyStabilityAssists(VehicleConfig cfg, boolean grounded, float speed) {
        if (lastDampGrounded == null || lastDampGrounded != grounded) {
            lastDampGrounded = grounded;
            setAngularDamp(grounded ? cfg.groundedAngularDamp : cfg.airborneAngularDamp);
        }
        if (cfg.antiRollStiffness > 0f) applyAntiRoll(cfg);
        if (!grounded) return;

        Vector3 bodyUp = getGlobalBasis().getY();
        if (cfg.downforceCoefficient > 0f) {
            float weight = (float) (getMass() * -getGravity().getY());
            float df = Math.min(cfg.downforceCoefficient * speed * speed, weight);
            applyCentralForce(bodyUp.times(-df));
        }
        if (cfg.uprightTorque > 0f) {
            Vector3 targetUp = desiredUpAxis();
            // Axis bodyUp × targetUp rotates bodyUp toward targetUp; magnitude sin(tilt)
            // scales the correction naturally (soft assist, not a hard constraint).
            if (bodyUp.dot(targetUp) < 0.995) {
                applyTorque(bodyUp.cross(targetUp).times(cfg.uprightTorque));
            }
        }
    }

    /**
     * The up axis the keep-upright assist pulls toward. World-up for cars; {@code Motorcycle}
     * tilts it into the turn so one assist both holds the bike up and banks it.
     */
    protected Vector3 desiredUpAxis() { return Vector3.Companion.getUP(); }

    /**
     * Anti-roll bar: per axle (wheels paired by local-Z proximity), transfer
     * antiRollStiffness × (compression difference) between the two sides — pushes the
     * compressed side up and pulls the extended side down, resisting body roll without
     * touching yaw. Default stiffness is 0 (off); an escalation lever beyond the
     * CoM-height lateral-force blend.
     */
    private void applyAntiRoll(VehicleConfig cfg) {
        for (int i = 0; i < wheels.size(); i++) {
            VehicleWheel a = wheels.get(i);
            for (int j = i + 1; j < wheels.size(); j++) {
                VehicleWheel b = wheels.get(j);
                // Same axle = opposite X sides at similar Z.
                if (Math.signum(a.getPosition().getX()) == Math.signum(b.getPosition().getX())) continue;
                if (Math.abs(a.getPosition().getZ() - b.getPosition().getZ()) > 0.5f) continue;
                float diff = a.getLastCompression() - b.getLastCompression();
                if (diff == 0f) continue;
                Vector3 bodyUp = getGlobalBasis().getY();
                Vector3 transfer = bodyUp.times(diff * cfg.antiRollStiffness);
                applyForce(transfer.times(-1), a.getGlobalPosition().minus(getGlobalPosition()));
                applyForce(transfer, b.getGlobalPosition().minus(getGlobalPosition()));
            }
        }
    }

    // ── Damage-tier VFX (smoke → fire, GTA-style) ─────────────────────────────
    // Runs on EVERY peer in _process, polled from the Health fraction — puppets receive
    // health in every snapshot (applyReplicatedHealth), so the tiers replicate with no
    // new message and no death-signal coupling (puppets never fire died).

    private static final double DAMAGE_VFX_INTERVAL = 0.25;
    private double damageVfxTimer = 0.0;
    private GPUParticles3D damageSmoke;
    private GPUParticles3D damageFire;

    @Register
    @Override
    public void _process(double delta) {
        damageVfxTimer -= delta;
        if (damageVfxTimer > 0.0) return;
        damageVfxTimer = DAMAGE_VFX_INTERVAL;
        refreshDamageTier();
    }

    private void refreshDamageTier() {
        if (healthNode == null || (damageSmoke == null && damageFire == null)) return;
        VehicleConfig cfg = getConfig();
        float frac = healthNode.maxHealth > 0f
                ? healthNode.getCurrentHealth() / healthNode.maxHealth : 1f;
        boolean smoke = frac > 0f && frac < cfg.damageSmokeFraction;
        boolean fire  = frac > 0f && frac < cfg.damageFireFraction;
        if (damageSmoke != null && damageSmoke.isEmitting() != smoke) damageSmoke.setEmitting(smoke);
        if (damageFire  != null && damageFire.isEmitting()  != fire)  damageFire.setEmitting(fire);
    }

    // ── NOS / booster ─────────────────────────────────────────────────────────
    // Authority-only physics multiplier (sprint key while driving): the wheels read the
    // accel/max-speed scale; puppets need nothing — the snapshot velocity carries the
    // result. The camera's speed-feel terms (VehicleCameraController) all ship at 0 since
    // 2026-08-31 and the speed-line overlay is deleted, so NOS currently reads as speed and
    // engine note only; a world-space trail effect on the CAR is the planned replacement.
    // Meter exposed via getBoostFraction() for a future HUD gauge.

    private double  boostMeter  = Double.NaN;   // lazily seeded from config (full tank)
    private boolean boostActive = false;

    protected void updateBoost(VehicleConfig cfg, double delta) {
        if (Double.isNaN(boostMeter)) boostMeter = cfg.boostCapacitySeconds;
        boostActive = cmd.boost && cmd.motor > 0.01f && boostMeter > 0.0
                && cfg.boostAccelMultiplier > 1f;
        if (boostActive) {
            boostMeter = Math.max(0.0, boostMeter - delta);
        } else {
            boostMeter = Math.min(cfg.boostCapacitySeconds,
                    boostMeter + cfg.boostRechargeRate * delta);
        }
    }

    public boolean isBoosting() { return boostActive; }

    /** 0..1 remaining boost — HUD gauge hook. */
    public float getBoostFraction() {
        float cap = getConfig().boostCapacitySeconds;
        return cap <= 0f || Double.isNaN(boostMeter) ? 0f : (float) (boostMeter / cap);
    }

    /** Motor-force multiplier the wheels apply this tick. */
    public float getBoostAccelScale() { return boostActive ? getConfig().boostAccelMultiplier : 1f; }

    /** Effective top speed this tick — boost raises the accel-curve ceiling. */
    public float getBoostMaxSpeed() {
        VehicleConfig cfg = getConfig();
        return cfg.maxSpeed * (boostActive ? cfg.boostMaxSpeedMultiplier : 1f);
    }

    // ── Damageable tires (flat state + replication accessors) ────────────────

    /**
     * Asymmetric flats yaw-pull the car toward the flat side, scaled by speed — the
     * player counter-steers against it (the classic shot-out-tire feel). Symmetric flats
     * (both sides) cancel; the grip/sag penalties still apply per wheel.
     */
    private void applyFlatTirePull(VehicleConfig cfg, float speed) {
        if (cfg.flatPullTorque <= 0f) return;
        int bias = 0;
        for (VehicleWheel w : wheels) {
            if (w.isFlat()) bias += (w.getPosition().getX() > 0f) ? 1 : -1;
        }
        if (bias == 0) return;
        float speedRatio = (float) GD.clamp(speed / Math.max(1e-3f, cfg.maxSpeed), 0.0, 1.0);
        // Positive yaw (about +Y) turns the nose toward −X, so a +X-side flat needs −yaw.
        applyTorque(getGlobalBasis().getY().times(-bias * cfg.flatPullTorque * speedRatio));
    }

    /** Bit i = wheel i (scene child order, peer-identical) is flat — rides the snapshot flags u8, bits 3–6. */
    public int getFlatMask() {
        int mask = 0;
        for (int i = 0; i < wheels.size() && i < 4; i++) {
            if (wheels.get(i).isFlat()) mask |= 1 << i;
        }
        return mask;
    }

    /** Puppet apply path — mirrors the authority's flat state (visual squash + sag), idempotent. */
    public void applyReplicatedFlatMask(int mask) {
        for (int i = 0; i < wheels.size() && i < 4; i++) {
            wheels.get(i).setFlat((mask & (1 << i)) != 0);
        }
    }

    // ── Parked helpers ────────────────────────────────────────────────────────

    /** True when the current command applies no drive intent — nothing pushes the car this tick.
     *  Epsilon comparisons, not == 0: analog stick drift must not hold the car awake forever. */
    private boolean isIdleInput() {
        return Math.abs(cmd.motor) < 0.01f && !cmd.brake && !cmd.handbrake
                && Math.abs(cmd.steering) < 0.05f;
    }

    /** Leave the parked state and resume wheel physics next frame. */
    private void unpark() {
        parked = false;
        parkTimer = 0.0;
        setSleeping(false);
    }

    /**
     * External wake: seat enter and weapon damage clear the parked sleep immediately.
     * (An explosion's applyCentralImpulse wakes the physics engine on its own; the parked
     * branch then unparks if the push crossed parkSpeedThreshold, else re-sleeps.)
     */
    public void wakeUp() {
        if (parked) unpark();
        else parkTimer = 0.0;
    }

    /** Damage wake (Health.hit): a shot parked car resumes physics (reacts to later pushes/sag). */
    @Register
    public void onVehicleDamaged(float damage) {
        wakeUp();
    }

    // ── Utilities ─────────────────────────────────────────────────────────────

    @Register
    @Override
    public void _integrateForces(PhysicsDirectBodyState3D state) {
        // Run-over damage is resolved only where the vehicle is simulated (N3): on puppets
        // the body is frozen (no contacts to report) and damage must stay single-sourced —
        // a client-side hit relays via Health.takeDamage from the simulating peer instead.
        if (!isLocallySimulated()) return;
        VehicleConfig cfg = getConfig();

        // Static parking / low-speed brake lock. A friction coefficient can only slow
        // slope-creep, never stop it — below a small speed epsilon the car is held by
        // zeroing velocity outright (frame-rate independent, cannot oscillate, invisible
        // at these speeds). Handbrake keeps its drift meaning: at speed it still only
        // kills lateral grip, the lock engages below parkingLockSpeed (GTA behaviour).
        // The park-candidate lock also holds an unoccupied car on a slope during the
        // parkDelaySeconds dwell, before sleep takes over.
        if (lastGrounded) {
            float lockSpeed = (float) state.getLinearVelocity().length();
            // Throttle overrides the parking lock: handbrake + gas at a standstill is a
            // burnout donut (drift yaw torque spins the car in place), not a parked car.
            boolean brakeLock = lockSpeed < cfg.parkingLockSpeed
                    && Math.abs(cmd.motor) < 0.01f && (cmd.handbrake || cmd.brake);
            boolean parkLock = lockSpeed < cfg.parkSpeedThreshold
                    && (parked || parkTimer > 0.0);
            if (brakeLock || parkLock) {
                state.setLinearVelocity(Vector3.Companion.getZERO());
                state.setAngularVelocity(Vector3.Companion.getZERO());
            }
        }

        if (cfg.vehicleCollisionMinSpeed <= 0) return;

        int count = state.getContactCount();
        java.util.HashSet<Character> currentContacts = new java.util.HashSet<>();

        for (int i = 0; i < count; i++) {
            Object obj = state.getContactColliderObject(i);
            Character character = null;
            if (obj instanceof Character c) {
                character = c;
            } else if (obj instanceof Node3D n3d) {
                Node owner = n3d.getOwner();
                if (owner instanceof Character c) character = c;
            }
            if (character == null || character == occupant || !character.isAlive()) continue;
            currentContacts.add(character);
        }

        float speed = (float) GD.abs(getGlobalBasis().getZ().dot(getLinearVelocity()));
        if (speed >= cfg.vehicleCollisionMinSpeed) {
            for (Character character : currentContacts) {
                if (!activeCollisions.contains(character)) {
                    applyVehicleCollisionDamage(character, speed, cfg);
                }
            }
        }

        // VEHICLE_CRASH stimulus (PLAN.md E2): a fast impact against anything (wall or body) alerts
        // nearby AI. Throttled so a sustained scrape posts ~1×/s rather than every physics frame.
        crashStimulusCooldown = Math.max(0.0, crashStimulusCooldown - state.getStep());
        if (count > 0 && speed >= cfg.vehicleCollisionMinSpeed && crashStimulusCooldown <= 0.0) {
            StimulusManager sm = StimulusManager.get();
            if (sm != null) {
                String faction = (occupant != null && occupant.getCharacterInfo() != null)
                        ? occupant.getCharacterInfo().faction : "";
                sm.post(StimulusManager.Type.VEHICLE_CRASH, getGlobalPosition(),
                        CRASH_HEARING_RADIUS, this, faction);
                crashStimulusCooldown = CRASH_STIMULUS_INTERVAL;
            }
        }

        activeCollisions.clear();
        activeCollisions.addAll(currentContacts);
    }

    /** Audible range (m) of a vehicle crash to AI, and the min seconds between crash stimulus posts (E2). */
    private static final float  CRASH_HEARING_RADIUS    = 200f;
    private static final double CRASH_STIMULUS_INTERVAL = 0.75;

    private void applyVehicleCollisionDamage(Character character, float speed, VehicleConfig cfg) {
        float damage = (speed - cfg.vehicleCollisionMinSpeed) * cfg.vehicleCollisionDamageScale;
        Node healthNode = character.getNodeOrNull("Health");
        if (!(healthNode instanceof Health health)) return;

        String attackerName    = (occupant != null) ? occupant.getCharacterInfo().displayName : "";
        String attackerFaction = (characterInfo != null) ? characterInfo.faction : "";
        health.takeDamage(null, damage, DAMAGE_SOURCE_COLLISION, cfg.vehicleIcon, attackerName, attackerFaction);

        Vector3 knockbackDir = getLinearVelocity().normalized();
        character.applyHitImpulse(null, knockbackDir, damage);
    }

    /** Returns the typed weapon mode from the config int. */
    public VehicleWeaponMode getWeaponMode() {
        VehicleWeaponMode[] values = VehicleWeaponMode.values();
        int idx = Math.max(0, Math.min(getConfig().weaponModeIndex, values.length - 1));
        return values[idx];
    }

    private void resetOrientation() {
        setLinearVelocity(new Vector3(0f, 0f, 0f));
        setAngularVelocity(new Vector3(0f, 0f, 0f));
        setRotation(new Vector3(0f, 0f, 0f));
        Vector3 p = getGlobalPosition();
        setGlobalPosition(new Vector3((float) p.getX(), (float) p.getY() + 7f, (float) p.getZ()));
    }

    // ── Controllable ──────────────────────────────────────────────────────────

    @Override public void applyCommand(UserCommand cmd, double delta) { }
    @Override public CharacterInfo getCharacterInfo()                 { return characterInfo; }

    /** Setter half of the exported {@code characterInfo} property. */
    public void setCharacterInfo(CharacterInfo value) {
        this.characterInfo = value;
    }

    // ── NameplateTarget ─────────────────────────────────────────────────────────
    // The carrier reuses ui/Nameplate.tscn unchanged: the plate finds its sibling "Health" and
    // "WeaponController" nodes itself, so health + weapon/ammo are the CARRIER's. Only the colour
    // is carrier-specific — it follows the DRIVER's faction, neutral when not ridden or the driver
    // is down. tryEnter/tryExit (run on every peer via the host-arbitrated seat change) emit
    // nameplateChanged, so the tint re-derives on every peer with no extra net message.

    public final Signal0 nameplateChanged = new Signal0(this, new StringName("nameplate_changed"));

    @Override
    public String getNameplateText() {
        if (characterInfo != null && !characterInfo.displayName.isEmpty()) return characterInfo.displayName;
        return getName().toString();
    }

    @Override
    public Color getNameplateColor() {
        // Driver seat occupant determines the colour; neutral when empty or the driver is defeated.
        // isInstanceValid guards against a freed occupant (streamed-traffic despawn race, PLAN.md I3c).
        if (occupant != null && GD.isInstanceValid(occupant) && occupant.isAlive() && occupant.characterInfo != null) {
            return Faction.color(occupant.characterInfo.faction);
        }
        return Faction.color(Faction.NEUTRAL);
    }

    @Override
    public Signal0 getNameplateChangedSignal() { return nameplateChanged; }

    // ── Enter / Exit ──────────────────────────────────────────────────────────
    //
    // Round 11 N3: networked enter/exit is HOST-ARBITRATED. requestEnter/requestExit are
    // the intent entry points (Player input, _physicsProcess exit branch); the host
    // validates (VehicleSeatPolicy) and broadcasts MSG_VEHICLE_OCCUPANCY, and every peer —
    // including the requester — executes the seat change by running tryEnter/tryExit from
    // that event (GameManager.applyVehicleOccupancy). Locomotion authority migrates INSIDE
    // the same event (no client-originated MSG_OWNERSHIP anymore). Single-player routes
    // requestEnter/requestExit straight to tryEnter/tryExit — zero behavioural diff.

    // ── Seat accessors (multi-seat) ───────────────────────────────────────────

    public int getSeatCount() { return seatOccupants.length; }

    public Character getSeatOccupant(int seat) {
        return (seat >= 0 && seat < seatOccupants.length) ? seatOccupants[seat] : null;
    }

    /** Occupancy snapshot for {@link VehicleSeatPolicy#pickSeat} (host-side seat selection). */
    public boolean[] buildSeatOccupancy() {
        boolean[] occupied = new boolean[seatOccupants.length];
        for (int i = 0; i < seatOccupants.length; i++) occupied[i] = seatOccupants[i] != null;
        return occupied;
    }

    /** Seat index this character occupies, or -1. */
    public int findSeatOf(Character c) {
        if (c == null) return -1;
        for (int i = 0; i < seatOccupants.length; i++) {
            if (seatOccupants[i] == c) return i;
        }
        return -1;
    }

    public boolean hasFreeSeat() {
        for (Character rider : seatOccupants) {
            if (rider == null) return true;
        }
        return false;
    }

    /** Ask to seat {@code c} (host picks the seat — driver first): direct in single-player, host-arbitrated when networked. */
    public void requestEnter(Character c) {
        if (c == null || !hasFreeSeat()) return;
        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (!(netNode instanceof NetworkManager net) || !net.isNetworked()) {
            int seat = VehicleSeatPolicy.pickSeat(buildSeatOccupancy(), VehicleSeatPolicy.SEAT_AUTO);
            if (seat >= 0) tryEnter(c, seat);
            return;
        }
        String occupantId = c.characterInfo != null ? c.characterInfo.characterId : "";
        if (net.isServer()) {
            if (getNodeOrNull("/root/GameManager") instanceof com.openworld.game.GameManager gm) {
                gm.processVehicleSeatRequest(NetworkManager.SERVER_PEER_ID,
                        characterInfo.characterId, occupantId, true, VehicleSeatPolicy.SEAT_AUTO);
            }
        } else {
            net.requestVehicleSeat(characterInfo.characterId, occupantId, true, VehicleSeatPolicy.SEAT_AUTO);
        }
    }

    /** Ask to unseat the driver: direct in single-player, host-arbitrated when networked. */
    public void requestExit() {
        requestExitOccupant(occupant);
    }

    /** Ask to unseat any specific rider (driver or passenger) — the host resolves the seat by characterId. */
    public void requestExitOccupant(Character c) {
        if (c == null || findSeatOf(c) < 0) return;
        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (!(netNode instanceof NetworkManager net) || !net.isNetworked()) {
            tryExit(findSeatOf(c));
            return;
        }
        String occupantId = c.characterInfo != null ? c.characterInfo.characterId : "";
        if (net.isServer()) {
            if (getNodeOrNull("/root/GameManager") instanceof com.openworld.game.GameManager gm) {
                gm.processVehicleSeatRequest(NetworkManager.SERVER_PEER_ID,
                        characterInfo.characterId, occupantId, false, VehicleSeatPolicy.SEAT_AUTO);
            }
        } else {
            net.requestVehicleSeat(characterInfo.characterId, occupantId, false, VehicleSeatPolicy.SEAT_AUTO);
        }
    }

    /**
     * Executes the seat change locally. Runs on EVERY peer (from the occupancy event when
     * networked), so it is puppet-aware: the controller hot-swap only happens for a live
     * (locally-driven) character — a puppet keeps its NetworkController on the character
     * (it still replicates aim/health/fire) and the vehicle keeps its VehicleNetworkController;
     * the camera only follows the LOCAL player's enter.
     */
    public void tryEnter(Character c) { tryEnter(c, 0); }

    /** Seat-aware enter — seat 0 is the driver (full hot-swap path below); higher seats are passengers. */
    public void tryEnter(Character c, int seatIndex) {
        if (c == null || seatIndex < 0 || seatIndex >= seatOccupants.length) return;
        if (seatOccupants[seatIndex] != null) return;
        if (seatIndex != 0) {
            enterPassenger(c, seatIndex);
            return;
        }
        occupant    = c;
        seatOccupants[0] = c;
        justEntered = true;
        wakeUp();   // a parked (sleeping) car resumes physics the moment someone takes the seat

        VehicleWeaponMode mode = getWeaponMode();
        boolean localDriver = c.getController() instanceof PlayerController;

        c.enterDriveState(mode, this);
        c.setGlobalRotation(new Vector3(0f, (float) getGlobalRotation().getY(), 0f));

        // PLAN.md I3c: an AI-driven traffic car already has its lane-follow brain
        // (VehicleAIController) on the vehicle, and the seated AI is a non-driving visible occupant —
        // so do NOT steal the occupant's controller (it keeps its own AIController, suppressed by the
        // drive-state physics-off, ready to resume on eviction). The hot-swap is only for the normal
        // case (player or AI taking the wheel of a car with no AI driver of its own).
        // A PLAYER TAKING SEAT 0 TAKES THE WHEEL. An ambient car drives itself through the
        // VehicleAIController on the vehicle, and the guard below deliberately refuses to hot-swap
        // a controller in while that brain is there — right for the AI RIDER that ZoneManager
        // seats in a traffic car (it is cargo, the brain keeps driving), wrong for a player, who
        // pressed the key precisely to drive. A carjack drops the brain via requestCarjack; a car
        // with an empty seat never went through that path, so it drops it here. Runs on every peer
        // (tryEnter is the occupancy-event executor), so the networked path is covered by the same
        // three lines instead of a second rule in the arbitration layer.
        if (c instanceof Player && isAiDriven()) removeAiDriverBrain();

        if (!(c.getController() instanceof NetworkController) && !isAiDriven()) {
            // A leftover puppet controller would be silently orphaned by attachController's
            // removeChild — applyAuthorityState normally clears it first (with velocity
            // seeding); this is the belt-and-braces for any other path.
            if (controller instanceof VehicleNetworkController vnc) {
                detachController();
                vnc.queueFree();
            }
            Controller ctrl = c.detachController();
            if (ctrl != null) attachController(ctrl);
        }

        if (mode == VehicleWeaponMode.PASSENGER_WEAPON && vehicleWeaponController != null) {
            RayCast3D vRay = vehicleWeaponController.getAimRay();
            if (vRay != null) {
                Node wc = c.getNodeOrNull("WeaponController");
                if (wc instanceof WeaponController wcn) wcn.overrideAimRay(vRay);
            }
        }

        if (localDriver) {
            if (camController != null) camController.setPassengerAimMode(mode == VehicleWeaponMode.PASSENGER_WEAPON);
            if (vehicleCamera != null) vehicleCamera.makeCurrent();
            emitEnterPrompt(false);
        }
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) bus.vehicleEntered.emit(this, c.characterInfo);
        nameplateChanged.emit();   // re-tint the carrier plate to the new driver's faction
    }

    /**
     * Passenger enter (seat 1..n): no controller hot-swap, no camera switch, no ownership —
     * the rider keeps their own controller and camera, is pinned to the seat each tick, and
     * (config-gated) fires their own weapon from the window, GTA drive-by style. Their input
     * keeps flowing because {@code enterDriveState(…, isDriver=false)} leaves the character's
     * processing on; {@code Character.applyInput} reduces it to weapon-use while seated.
     */
    private void enterPassenger(Character c, int seatIndex) {
        seatOccupants[seatIndex] = c;
        VehicleWeaponMode mode = getConfig().passengerSeatsCanShoot
                ? VehicleWeaponMode.PASSENGER_WEAPON : VehicleWeaponMode.NONE;
        c.enterDriveState(mode, this, false);
        c.setGlobalRotation(new Vector3(0f, (float) getGlobalRotation().getY(), 0f));
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) bus.vehicleEntered.emit(this, c.characterInfo);
        nameplateChanged.emit();
    }

    /** Executes the unseat locally — same puppet awareness as {@link #tryEnter}. */
    public void tryExit() { tryExit(0); }

    /** Seat-aware exit — placement mirrors the seat's side; passengers skip the driver-only teardown. */
    public void tryExit(int seatIndex) {
        if (seatIndex < 0 || seatIndex >= seatOccupants.length) return;
        if (seatOccupants[seatIndex] == null) return;
        if (seatIndex != 0) {
            exitPassenger(seatIndex);
            return;
        }
        Character c = occupant;
        occupant = null;
        seatOccupants[0] = null;

        boolean localDriver = controller instanceof PlayerController;

        if (getWeaponMode() == VehicleWeaponMode.PASSENGER_WEAPON) {
            Node wc = c.getNodeOrNull("WeaponController");
            if (wc instanceof WeaponController wcn) wcn.restoreAimRay();
        }

        c.setGlobalPosition(seatExitPosition(seatIndex));

        c.exitDriveState();

        // Only hand back what tryEnter hot-swapped in. A VehicleNetworkController stays (it belongs to
        // the vehicle's replication). A VehicleAIController also stays — it is the car's OWN lane-follow
        // brain (PLAN.md I3c Design B), never brought by the occupant; the occupant kept its own
        // controller while seated, so moving the driving brain onto it here would be wrong (and would
        // break the evicted driver's carjack reaction, which needs its AIController intact).
        if (!(controller instanceof VehicleNetworkController) && !(controller instanceof VehicleAIController)) {
            Controller ctrl = detachController();
            if (ctrl != null) c.attachController(ctrl);
        }

        if (localDriver) {
            if (camController != null) camController.setPassengerAimMode(false);
            c.makeCameraActive();
        }
        activeCollisions.add(c);
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) bus.vehicleExited.emit(c.characterInfo);
        nameplateChanged.emit();   // seat now empty → carrier plate falls back to neutral
    }

    /** Passenger unseat — restore + place; no controller/camera/ownership teardown to undo. */
    private void exitPassenger(int seatIndex) {
        Character c = seatOccupants[seatIndex];
        seatOccupants[seatIndex] = null;
        c.setGlobalPosition(seatExitPosition(seatIndex));
        c.exitDriveState();
        activeCollisions.add(c);
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) bus.vehicleExited.emit(c.characterInfo);
        nameplateChanged.emit();
    }

    /** Exit placement mirrored to the seat's side (left seats step out left, right seats right). */
    private Vector3 seatExitPosition(int seatIndex) {
        Vector3 right = getGlobalTransform().getBasis().getColumn(0);
        Node3D anchor = seatIndex < seatNodes.size() ? seatNodes.get(seatIndex) : driverSeatNode;
        float side = (anchor != null && anchor.getPosition().getX() > 0f) ? 1f : -1f;
        Vector3 base = anchor != null ? anchor.getGlobalPosition() : getGlobalPosition();
        return base.plus(right.times(1.5f * side)).plus(new Vector3(0f, 0.8f, 0f));
    }

    /**
     * Authority-side destruction (Health.died only ever fires where damage is applied —
     * applyReplicatedHealth never emits it, so a puppet can't reach this from a snapshot).
     * Ordered reliable sequence for clients, all on channel 0: occupancy-exit (inside the
     * exit grant) → WORLD_EVENT_VEHICLE_WRECK (cosmetics) → MSG_DESPAWN (node removal).
     */
    @Register
    public void onVehicleDestruction() {
        if (!isEmptyOfRiders()) {
            // FORCED unseat, never requestExit: the seat policy denies a host-initiated exit
            // for a client driver (NOT_OWNER), which would skip the occupancy broadcast and
            // free the driving client's controller inside the despawned vehicle. The forced
            // path bypasses the policy and broadcasts, so every peer unseats BEFORE the
            // wreck/despawn arrive on the same ordered channel. All seats are evicted.
            if (getNodeOrNull("/root/GameManager") instanceof com.openworld.game.GameManager gm) {
                gm.forceVehicleExit(this);
            }
            for (int seat = seatOccupants.length - 1; seat >= 0; seat--) {
                if (seatOccupants[seat] != null) tryExit(seat);   // no GameManager — at least free locally
            }
        }

        VehicleConfig cfg = getConfig();
        if (cfg.explosionRadius > 0f) {
            String attackerName    = (characterInfo != null) ? characterInfo.displayName : getName().toString();
            String attackerFaction = (characterInfo != null) ? characterInfo.faction     : "";
            Node m = getTree().getFirstNodeInGroup("explosion_manager");
            if (m instanceof ExplosionManager mgr) {
                mgr.triggerExplosion(getGlobalPosition(), cfg.explosionRadius, cfg.explosionMaxDamage,
                                     cfg.explosionPushForce, attackerName, attackerFaction,
                                     DAMAGE_SOURCE_EXPLOSION, cfg.vehicleIcon, this);
            }
        }
        spawnWreckScene(cfg);

        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (netNode instanceof NetworkManager net && net.isNetworked() && net.isServer()
                && characterInfo != null) {
            net.broadcastWorldEvent(com.openworld.game.GameManager.WORLD_EVENT_VEHICLE_WRECK,
                    characterInfo.characterId, 0f);
        }
        if (getNodeOrNull("/root/GameManager") instanceof com.openworld.game.GameManager gm) {
            gm.despawnAuthoritative(this, characterInfo);
        } else {
            queueFree();
        }
    }

    /**
     * Client-side mirror of a host-confirmed destruction (WORLD_EVENT_VEHICLE_WRECK):
     * explosion VFX + wreck scene, NEVER damage — the host already applied that
     * authoritatively, and a client running the damage loop would relay duplicate
     * MSG_DAMAGE_REQUESTs for every body in the radius.
     */
    public void playWreckCosmetics() {
        VehicleConfig cfg = getConfig();
        if (cfg.explosionRadius > 0f) {
            Node m = getTree().getFirstNodeInGroup("explosion_manager");
            if (m instanceof ExplosionManager mgr) mgr.spawnExplosion(getGlobalPosition());
        }
        spawnWreckScene(cfg);
    }

    /** Instantiates the wreck scene at this vehicle's transform with its timed cleanup. */
    private void spawnWreckScene(VehicleConfig cfg) {
        if (cfg.wreckScene == null) return;
        Node wreck = cfg.wreckScene.instantiate();
        getTree().getCurrentScene().addChild(wreck);
        if (wreck instanceof Node3D w) w.setGlobalTransform(getGlobalTransform());
        SceneTreeTimer t = getTree().createTimer(cfg.wreckDuration, true, false, false);
        t.connect(new StringName("timeout"),
                MethodCallable.createUnsafe(wreck, "queue_free"));
    }

    // ── EntranceArea signals ──────────────────────────────────────────────────

    private boolean isEmptyOfRiders() {
        for (Character rider : seatOccupants) {
            if (rider != null) return false;
        }
        return true;
    }

    @Register
    public void onEntranceBodyEntered(Node3D body) {
        Character c = resolveCharacter(body);
        if (c == null) return;
        // AI-driven car → "Carjack"; any free seat (empty car OR player-driven with room) →
        // "Enter" (multi-seat: passengers join a driven car). A full player-driven car offers
        // no prompt (you can't carjack a player).
        if (!isAiOccupied() && !hasFreeSeat()) return;
        if (occupant instanceof Player && !hasFreeSeat()) return;
        if (c instanceof Player p) { p.nearbyVehicle = this; emitEnterPrompt(true); }
    }

    @Register
    public void onEntranceBodyExited(Node3D body) {
        Character c = resolveCharacter(body);
        if (c == null) return;
        if (c instanceof Player p) { p.nearbyVehicle = null; emitEnterPrompt(false); }
    }

    private Character resolveCharacter(Node3D body) {
        if (body instanceof Character c) return c;
        Node owner = body.getOwner();
        return owner instanceof Character c ? c : null;
    }

    private void emitEnterPrompt(boolean inRange) {
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) {
            // Carries its own key hint: vehicles use "use_carrier" (F), while the HUD's
            // default "[ E ]" prefix is the pickup "interact" action.
            String text = !inRange ? "" : (isAiOccupied() ? "[ F ]  Carjack" : "[ F ]  Enter vehicle");
            bus.pickupInteractChanged.emit(inRange, text);
        }
    }

    // ── Controller hot-swap ───────────────────────────────────────────────────

    public Controller detachController() {
        if (controller == null) return null;
        Controller c = controller;
        removeChild(c);
        controller = null;
        return c;
    }

    /**
     * Replace the vehicle's controller, freeing the outgoing one (it has no other referent).
     * Callers retaining the old controller use {@link #detachController()} instead. Mirrors
     * Character.attachController — without the free a swapped-out controller leaks at exit.
     */
    public void attachController(Controller ctrl) {
        if (controller != null && controller != ctrl) {
            Controller old = controller;
            removeChild(old);
            old.queueFree();
        }
        controller = ctrl;
        addChild(ctrl);
    }

    public boolean isAlive() {
        return healthNode == null || !healthNode.isDead();
    }

    public Controller getController() { return controller; }

    public Character getOccupant()   { return occupant; }

    // ── Carjacking (PLAN.md I3c) ──────────────────────────────────────────────

    /** True when an AI (not a player) is in the driver seat — i.e. this car is a carjack target. */
    public boolean isAiOccupied() {
        return occupant != null && !(occupant instanceof Player);
    }

    /**
     * Is an AI BRAIN currently driving this car? The one owner of that question.
     *
     * It is NOT the same question as {@link #isAiOccupied}, and conflating the two is what made an
     * ambient traffic car un-drivable and un-exitable once a player got in. Design B puts the
     * lane-follow {@link VehicleAIController} on the VEHICLE, not on the seated AI — so a car
     * spawned by {@code ZoneManager} (or {@code DebugHarness} F4) with no rider at all is
     * AI-driven with an EMPTY seat: `isAiOccupied()` says false, the player takes the ordinary
     * enter path, and {@link #tryEnter}'s guard then refused the controller hot-swap because the
     * vehicle already had a brain. The player was seated in a car that kept driving itself and
     * whose exit key reached nothing (the exit branch runs off the vehicle's own controller, which
     * was still the AI's).
     */
    public boolean isAiDriven() {
        return controller instanceof VehicleAIController;
    }

    /**
     * Player intent to carjack an AI-driven car (PLAN.md I3c). Host-arbitrated like {@link #requestEnter}
     * (the car is host-owned in the synced model): single-player evicts + seats locally; a networked host
     * runs the carjack through the {@link GameManager} seat path (it detects an AI-occupied seat and evicts
     * first, replicating both the eviction and the player's enter over occupancy); a client forwards the
     * seat request and the host carries it out.
     */
    public void requestCarjack(Character player) {
        if (!isAiOccupied() || player == null) return;
        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (!(netNode instanceof NetworkManager net) || !net.isNetworked()) {
            doLocalCarjack(player);   // single-player — no policy, mirrors requestEnter's SP path
            return;
        }
        String playerId = player.characterInfo != null ? player.characterInfo.characterId : "";
        if (net.isServer()) {
            if (getNodeOrNull("/root/GameManager") instanceof GameManager gm) {
                gm.processVehicleSeatRequest(NetworkManager.SERVER_PEER_ID,
                        characterInfo.characterId, playerId, true, 0);   // carjack targets the wheel
            }
        } else {
            net.requestVehicleSeat(characterInfo.characterId, playerId, true, 0);
        }
    }

    /** Local (single-player) carjack: eject the AI driver, react it, drop the lane brain, seat the player. */
    private void doLocalCarjack(Character player) {
        Character ejected = occupant;
        tryExit();
        removeAiDriverBrain();
        if (ejected instanceof AICharacter ai) ai.reactToCarjack(player);
        tryEnter(player);
    }

    /**
     * Drop the lane-follow brain so a player can take the wheel of a carjacked traffic car. Design B
     * keeps the {@link VehicleAIController} on the <i>vehicle</i> (the seated AI never held it), so on a
     * carjack it must be freed here — otherwise {@link #tryEnter}'s guard would refuse to hot-swap the
     * player's controller in and the player couldn't drive.
     */
    public void removeAiDriverBrain() {
        if (isAiDriven()) {
            Controller old = detachController();
            if (old != null) old.queueFree();
        }
    }

    public boolean isSlipping()      { return slipping; }
    public void setSlipping(boolean s) { this.slipping = s; }
    public ArrayList<VehicleWheel> getWheels() { return wheels; }
    public boolean isBraking()       { return braking; }
    public boolean isHandbraking()   { return handBraking; }

    /**
     * The ACTUAL steer-wheel Y rotation (radians) — replicated so puppet wheels replay the
     * authority's pose exactly instead of re-integrating the raw input (N3).
     */
    public float getCurrentSteerAngle() {
        for (VehicleWheel w : wheels) {
            if (w.isSteer) return (float) w.getRotation().getY();
        }
        return 0f;
    }

    /** Current tick's motor input — replicated for puppet wheel visuals (N3). */
    public float getCurrentThrottle() { return cmd.motor; }
}

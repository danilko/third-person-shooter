package com.openworld.control;

import com.openworld.net.NetworkManager;
import com.openworld.carrier.vehicle.Vehicle;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Input;
import godot.api.Node;
import godot.api.Timer;
import godot.core.Vector3;
import com.openworld.character.Character;
import com.openworld.character.Player;
import com.openworld.movement.character.MovementController;
import com.openworld.movement.character.MovementState;
import com.openworld.movement.character.MovementType;
import com.openworld.movement.character.Stance;
import com.openworld.movement.character.StanceName;
import com.openworld.weapon.FirearmItem;

/**
 * Translates human keyboard/mouse input into a UserCommand each tick.
 *
 * Equivalent to Unreal's APlayerController / Source Engine's CBasePlayer command
 * generation. Lives as a child node of a Controllable (Player or Vehicle).
 * Detects the body type each tick via getControllable() and generates the
 * appropriate command fields:
 *   Player body → movement, combat, weapon, jump, stance fields
 *   Vehicle     → throttle, steering, handbrake, drift fields
 *
 * Hot-swap (Phase 5): call setTarget(newBody) before reparenting so gatherInput()
 * immediately generates the correct command type for the incoming body.
 *
 * Network (ownership-based authority): the local human owns this body and simulates
 * it fully locally; each tick the resulting state (not the command) is reported
 * upstream via NetworkManager.sendOwnedState — see the field-group comment below.
 */
@Script(className = "PlayerController")
public class PlayerController extends Controller {

    // Pre-built action name strings to avoid per-frame string concatenation in the input hot-path.
    // Slots 1–6 used on foot; slot 7 reserved for vehicle passenger mode.
    private static final String[] WEAPON_SLOT_ACTIONS = {"weapon_slot_0",
            "weapon_slot_1", "weapon_slot_2", "weapon_slot_3",
        "weapon_slot_4", "weapon_slot_5", "weapon_slot_6"
    };

    // ── Ownership-based authority ─────────────────────────────────────────────
    // The local human owns this body and simulates it locally (gatherInput → applyInput →
    // move_and_slide, fully responsive). Each tick it reports its OWN resulting state upstream
    // (NetworkManager.sendOwnedState); the host relays it to other peers, who interpolate. No
    // client prediction/reconciliation against a host re-simulation — there is no host
    // re-simulation of this body — which is what removed the dual-simulation teleporting.

    // ── Cached body references (re-resolved when body type changes) ───────────
    private Player     cachedPlayer;
    private Timer      cachedAimStayTimer;
    private Vehicle     cachedVehicle;

    // ── Stance / movement input mode (false = HOLD, true = TOGGLE) ────────────
    // The hold-vs-toggle decision lives entirely here now; Character.setStance is a
    // pure idempotent setter. Default movement is SPRINT (run); holding/toggling the
    // stealth key (Shift, the "sprint" action) drops to the slower WALK tier — the
    // quiet "stealth walk" the future reduced-audio/awareness system keys off (the
    // WALK MovementState carries the low noiseLevel).
    @Export public boolean stanceToggleMode  = false;
    @Export public boolean stealthToggleMode = false;

    private final ModalInput crouchInput  = new ModalInput("crouch");
    private final ModalInput crawlInput   = new ModalInput("crawl");
    private final ModalInput stealthInput = new ModalInput("sprint");
    private StanceName lastDesiredStance  = StanceName.UPRIGHT;

    /**
     * Minimum distance the spine-IK aim point may converge to. Aiming at a far target snaps the
     * aim point onto the actual crosshair hit, so the visible gun (and replicated puppet gun)
     * points where bullets land — closing the over-the-shoulder "gun isn't on me but I'm hit"
     * mismatch. Clamping to this floor keeps the spine from yanking onto a wall right in front
     * of the muzzle (the reason the aim point used to be a fixed far point instead).
     */
    private static final double MIN_AIM_CONVERGE_DISTANCE = 5.0;

    @Register
    @Override
    public void _ready() {
        resolveBody();
    }

    private void resolveBody() {
        Controllable c = getControllable();
        if (c instanceof Player p) {
            cachedPlayer       = p;
            cachedAimStayTimer = (Timer) p.getNode("AimStayTimer");
            cachedVehicle      = null;
        } else if (c instanceof Vehicle v) {
            cachedVehicle      = v;
            cachedPlayer       = null;
            cachedAimStayTimer = null;
        } else {
            cachedPlayer  = null;
            cachedVehicle = null;
        }
    }

    @Override
    public UserCommand gatherInput(double delta) {
        // Re-resolve if the body has changed (hot-swap or first tick after reparent).
        Controllable c = getControllable();
        if (c instanceof Player p && p != cachedPlayer) resolveBody();
        else if (c instanceof Vehicle v && v != cachedVehicle) resolveBody();

        if (cachedVehicle != null) return gatherVehicleInput();
        if (cachedPlayer  != null) return gatherCharacterInput();
        return new UserCommand();
    }

    // ── Character (on-foot) input ─────────────────────────────────────────────

    private UserCommand gatherCharacterInput() {
        UserCommand cmd = new UserCommand();
        Input inp = Input.INSTANCE;
        Player body = cachedPlayer;

        boolean isFps = body.isFpsMode;

        // ── Movement ──────────────────────────────────────────────────────────
        // Direction is converted to world-space HERE (at the source) so the wire
        // format always carries world-space intent.  MovementController no longer
        // rotates by camRotation for player bodies — it uses the direction as-is.
        float moveX = inp.getActionStrength("right") - inp.getActionStrength("left");
        float moveZ = inp.getActionStrength("back")  - inp.getActionStrength("forward");
        cmd.movementDirection.setX(moveX);
        cmd.movementDirection.setZ(moveZ);

        // Rotate to world-space at the source so the command always carries world-space intent.
        //
        // The frame is taken off the camera the human is ACTUALLY looking through — its world
        // basis, flattened — and not rebuilt from the rig's parts. The rig is
        // `setAsTopLevel(true)`, so its own world yaw is whatever the body's was at the instant
        // its `_ready()` ran; the previous form added `body.getRotation().getY()` back to
        // compensate, which is right only while the body's yaw has not changed since. Rotate a
        // body after `add_child()` — every code spawn path, and the AimDebug stand — and W walked
        // 90 degrees off what the screen showed (measured, tools/godot/probe_camera_frame.gd
        // case B). One owner, and it covers FPS and the vehicle seat for free: every camera mode
        // writes `ActiveCamera`'s global transform, so this reads the same view in all of them.
        //
        // Timing is unchanged: `_physics_process` runs parent-first, so the camera controllers
        // (children) have not written this tick yet and we read the previous tick's view —
        // exactly what `getCurrentYaw()` returned.
        if (cmd.movementDirection.lengthSquared() > 0.001 && body.activeCamera != null) {
            Vector3 camZ = body.activeCamera.getGlobalTransform().getBasis().getZ();
            Vector3 fwd = new Vector3(-camZ.getX(), 0.0, -camZ.getZ());   // a camera looks down -Z
            if (fwd.lengthSquared() > 1e-6) {
                fwd = fwd.normalized();
                Vector3 right = new Vector3(-fwd.getZ(), 0.0, fwd.getX());   // fwd x UP
                cmd.movementDirection = right.times(moveX).plus(fwd.times(-moveZ));
            }
        }

        if (cmd.movementDirection.lengthSquared() > 0.001) {
            // Default movement is SPRINT (run); holding/toggling the stealth key drops
            // to the slower, quieter WALK tier (CS-style "walk to stay quiet").
            cmd.movementType = stealthInput.poll(inp, stealthToggleMode)
                    ? MovementType.WALK : MovementType.SPRINT;
        }

        // ── Combat / aim ──────────────────────────────────────────────────────
        if (isFps) {
            cmd.wantCombat = true;
        } else {
            boolean aimOrFire = inp.isActionPressed("aim", false)
                             || inp.isActionPressed("fire", false);

            Timer aimStayTimer = cachedAimStayTimer;
            if (aimOrFire) {
                aimStayTimer.stop();
            } else if (body.isCombat() && (inp.isActionJustReleased("aim", false)
                                        || inp.isActionJustReleased("fire", false))) {
                aimStayTimer.start();
            }
            cmd.wantCombat = aimOrFire || (body.isCombat() && !aimStayTimer.isStopped());
        }

        // ── Aim target (spine IK look point — converges on the crosshair target) ──
        // AimTarget drives SpineAimModifier (and is replicated as the puppet's look point).
        // It is set to the ACTUAL world point the crosshair is on — the AimRay's collision
        // point — so the visible gun, the replicated remote gun, and the bullet all converge
        // on the same target. This closes the over-the-shoulder mismatch where the gun, aimed
        // at a fixed far point parallel to the offset camera ray, appeared to point beside a
        // victim who still got hit by the camera-origin bullet. Clamped to
        // MIN_AIM_CONVERGE_DISTANCE so aiming near a wall doesn't snap the spine onto the close
        // surface; falls back to the far point when nothing is hit. Hit detection itself is
        // unchanged — still the camera AimRay in FirearmItem.performHitscan (no muzzle/animation
        // coupling); this only aligns the visible aim with where bullets already go.
        if (cmd.wantCombat) {
            Vector3 rayDeg = body.aimRay.getRotationDegrees();
            body.aimRay.setRotationDegrees(new Vector3(rayDeg.getX(), 0.0f, 0.0f));
            body.aimRay.forceRaycastUpdate();   // refresh collision for the reset orientation
            Vector3 origin   = body.aimRay.getGlobalPosition();
            Vector3 farPoint = body.aimRay.toGlobal(body.aimRay.getTargetPosition());
            if (body.aimRay.isColliding()
                    && body.aimRay.getCollisionPoint().minus(origin).length() >= MIN_AIM_CONVERGE_DISTANCE) {
                cmd.aimTargetPosition = body.aimRay.getCollisionPoint();
            } else if (body.aimRay.isColliding()) {
                // Hit, but too close — clamp to the floor along the ray so the spine stays steady.
                cmd.aimTargetPosition = origin.plus(
                        farPoint.minus(origin).normalized().times((float) MIN_AIM_CONVERGE_DISTANCE));
            } else {
                cmd.aimTargetPosition = farPoint;
            }
        }

        // ── Weapon / body actions ─────────────────────────────────────────────
        cmd.fire    = inp.isActionPressed("fire", false);
        cmd.reload  = inp.isActionJustPressed("reload", false);
        cmd.drop    = inp.isActionJustPressed("drop", false);
        cmd.jump    = inp.isActionJustPressed("jump", false);

        // ── Stance (hold or toggle, configurable via stanceToggleMode) ────────
        // Resolve the desired stance from the modal crouch/crawl inputs; crawl (prone)
        // wins over crouch when both are active. Emit only on change so a held stance
        // doesn't re-issue every tick (preserves jump-to-stand). Character.setStance is
        // idempotent, so a repeat would be harmless regardless.
        boolean wantCrouch = crouchInput.poll(inp, stanceToggleMode);
        boolean wantCrawl  = crawlInput.poll(inp, stanceToggleMode);
        StanceName desired = wantCrawl  ? StanceName.CRAWL
                           : wantCrouch ? StanceName.CROUCH
                           : StanceName.UPRIGHT;
        if (desired != lastDesiredStance) {
            cmd.desiredStance = desired;
            lastDesiredStance = desired;
        }

        // ── Vertical swim intent (only consumed while in the SWIM stance) ─────────
        // Held jump = surface/ascend, held crouch or crawl = dive. Uses the held action
        // state (not the edge-triggered cmd.jump) so the player can hold to keep diving.
        boolean swimUp   = inp.isActionPressed("jump", false);
        boolean swimDown = wantCrouch || wantCrawl;
        cmd.swimVertical = (swimUp ? 1.0 : 0.0) - (swimDown ? 1.0 : 0.0);

        // ── Weapon slot quick-switch ──────────────────────────────────────────
        // Keys 0         → slot 0 (fist — always available)
        // Keys 1–6       → slots 1–6 (PRIMARY×2, SECONDARY, MELEE, THROWABLE, CONSUMABLE)
        for (int i = 0; i < WEAPON_SLOT_ACTIONS.length; i++) {
            if (inp.isActionJustPressed(WEAPON_SLOT_ACTIONS[i], false)) {
                cmd.desiredWeapon = i;
                break;
            }
        }

        // ── Vehicle enter (press F near a vehicle) ────────────────────────────
        // Dedicated "use_carrier" action — "interact" (E) stays for pickups only.
        cmd.enterExit = inp.isActionJustPressed("use_carrier", false);

        sendToNetwork(cmd);

        return cmd;
    }

    // ── Network transport (ownership-based) ───────────────────────────────────

    /**
     * Reports this body's OWN authoritative state upstream when networked as a non-host client.
     * Single-player and the host stay untouched (NetworkManager.sendOwnedState no-ops on the
     * host — its own body is broadcast directly). The {@code cmd} is unused for transport now:
     * we send where the body actually is, not the input that got it there (ownership-based
     * authority — see NETWORK_REWRITE_PLAN.md). Vehicle bodies aren't reported here yet
     * (driver-authority migration is Step 3).
     */
    private void sendToNetwork(UserCommand cmd) {
        if (cachedPlayer == null || cachedPlayer.characterInfo == null) return;
        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (!(netNode instanceof NetworkManager net)) return;
        net.sendOwnedState(cachedPlayer);
    }

    // ── Vehicle input ─────────────────────────────────────────────────────────

    /**
     * Maps keyboard to vehicle throttle/steering/handbrake using the same
     * directional action bindings as on-foot movement so no extra mappings
     * are needed in the input map.
     *
     *   forward / back  → throttle (+1 / -1)
     *   left / right    → steering (-1 / +1, negated to match Godot convention)
     *   jump            → handbrake
     *   drift           → drift mode (engage arcade slide)
     *   interact             → enter/exit vehicle (interact)
     */
    private UserCommand gatherVehicleInput() {
        UserCommand cmd = new UserCommand();
        Input inp = Input.INSTANCE;

        cmd.motor        = inp.getActionStrength("forward") - inp.getActionStrength("back");
        cmd.steering     = -(inp.getActionStrength("right") - inp.getActionStrength("left"));
        cmd.handbrake    = inp.isActionPressed("handbrake", false);
        cmd.brake        = inp.isActionPressed("brake", false);
        // Sprint doubles as the NOS/booster while driving (no extra input mapping needed).
        cmd.boost        = inp.isActionPressed("sprint", false);
        cmd.enterExit    = inp.isActionJustPressed("use_carrier", false);
        // Weapon inputs: relayed to the occupant by Vehicle when weaponMode != NONE.
        cmd.fire         = inp.isActionPressed("fire", false);
        cmd.reload       = inp.isActionJustPressed("reload", false);
        // When there is no PASSENGER_WEAPON, "reload" doubles as a flip-upright reset.
        cmd.resetVehicle = inp.isActionJustPressed("reload", false);

        // Weapon slot switching — relayed to the occupant for PASSENGER_WEAPON mode.
        // WEAPON_SLOT_ACTIONS[i] maps directly to slot i (same as the on-foot path); the previous
        // i+1 mapping switched one slot too high and could request the invalid slot 7.
        for (int i = 0; i < WEAPON_SLOT_ACTIONS.length; i++) {
            if (inp.isActionJustPressed(WEAPON_SLOT_ACTIONS[i], false)) {
                cmd.desiredWeapon = i;
                break;
            }
        }

        // Driver upstream (Round 11 N3): while driving, the on-foot sendToNetwork path never
        // runs (the character's physics is off), so report BOTH owned bodies from here — the
        // vehicle's locomotion, and the seated character's stream (aim/fireSeq/combat keep
        // passenger weapons replicating; its transform is seat-pinned on every peer anyway).
        // Per-entity throttling in NetworkManager keeps the two streams at ~30 Hz each.
        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (netNode instanceof NetworkManager net && cachedVehicle != null) {
            net.sendOwnedVehicleState(cachedVehicle);
            if (cachedVehicle.getOccupant() != null) net.sendOwnedState(cachedVehicle.getOccupant());
        }

        return cmd;
    }
}

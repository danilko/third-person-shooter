package com.openworld.carrier.vehicle;

import godot.annotation.Export;
import godot.annotation.Script;
import godot.api.Curve;
import godot.api.PackedScene;
import godot.api.Resource;
import godot.api.Texture2D;
import com.openworld.weapon.FirearmItem;

/**
 * Per-vehicle-type tuning resource. Assign different .tres presets per vehicle scene.
 * New vehicle type = new .tres file only; no code or scene edits needed.
 *
 * Separation of concerns (GTA/Battlefield pattern):
 *   Vehicle.tscn  — structural (wheel positions, seat, model, collision mesh)
 *   VehicleConfig.tres — behavioural (how it drives, fights, and explodes)
 *
 * If no config is assigned, Vehicle.getConfig() returns a static DEFAULTS instance
 * so existing scenes without an assigned config continue to work unchanged.
 *
 * ── The SIX feel knobs (tune these first; everything else is structural/secondary) ──
 *   maxSpeed          — top speed (m/s, hard governor); reverseSpeedFraction of it backwards
 *   acceleration      — pull strength (launchBoost adds the off-the-line punch)
 *   maxLateralG       — cornering grip: turn radius scales speed²/this (NFS/GTA arcs)
 *   momentumAlignRate — corner speed retention ("rail" feel; 0 = raw physics scrub)
 *   driftGrip         — how much the car slides while the handbrake is held
 *   driftYawTorque    — how fast steering rotates the car in a drift/burnout
 */
@Script(className = "VehicleConfig")
public class VehicleConfig extends Resource {

    // ── Suspension — applied to all wheels as shared defaults ─────────────

    /** Spring force per metre of compression (N/m). */
    @Export public float springStrength   = 10000f;

    /** Spring damping coefficient — resists oscillation. */
    @Export public float springDamping    = 4500f;

    /** Wheel visual and physics radius (metres). */
    @Export public float wheelRadius      = 0.4f;

    /** Natural rest position: distance from wheel centre to contact point when unloaded. */
    @Export public float restDistance     = 0.5f;

    /** How far the wheel can extend below rest position before the ray stops. */
    @Export public float overExtend       = 0.3f;

    /**
     * Number of suspension probe rays per wheel along the rolling (fore/aft) axis.
     * 1 = original single centre ray. Extra probes sample the contact patch so the wheel
     * rides over cracks, kerbs and edges smoothly instead of snagging or dropping when the
     * single ray falls into a gap — the suspension uses the HIGHEST contact found and the
     * AVERAGED ground normal, which also steadies skid/grip. 3 is a good value.
     */
    @Export public int suspensionSamples = 1;

    /** Fore/aft half-spread (m) of the multi-sample probes along the wheel's rolling axis. */
    @Export public float suspensionSampleSpread = 0.15f;

    // ── Traction ──────────────────────────────────────────────────────────

    /** Longitudinal rolling-friction coefficient (no throttle or brake). */
    @Export public float zTraction        = 0.05f;

    /** Longitudinal friction under braking or parking (≥ 5× zTraction). With the
     *  saturation below, 0.18 ≈ a constant 1.1 g stop — arcade-firm, not neck-snapping. */
    @Export public float zBrakeTraction   = 0.18f;

    /**
     * Speed (m/s) at which the longitudinal friction force saturates. The zForce formula is
     * ∝ forwardSpeed — physically wrong at highway speed (rolling resistance is ~constant,
     * brakes deliver a fixed ~1.5 g, neither scales with v forever) and it is what capped
     * top speed at ~74 km/h regardless of maxSpeed. Below this speed nothing changes
     * (parking friction, low-speed feel). With the default 6: braking ≈ constant 1.5 g,
     * rolling drag caps at a small constant; aeroDragCoefficient takes over at speed.
     */
    @Export public float longFrictionSaturationSpeed = 6f;

    /**
     * Aerodynamic drag (N per (m/s)²) opposing velocity — the real high-speed limiter once
     * rolling friction saturates. Terminal speed = where motor force at the curve plateau
     * equals aero + saturated rolling drag (the governor at maxSpeed cuts motor first).
     */
    @Export public float aeroDragCoefficient = 0.35f;

    /**
     * Friction-circle cap: maximum lateral acceleration (in g) the tires can generate.
     * Uncapped lateral force (∝ slip speed) let the car hold a 10 m circle at 70 km/h
     * (~2.4 g — impossible; measured by DriveTest). With ~1.1 g the turn radius grows with
     * speed² like NFS/GTA: full sharpness in a parking lot, wide arcs at highway speed.
     * Also budgets the momentumAlignRate assist. 0 = uncapped.
     */
    @Export public float maxLateralG = 1.35f;

    // ── Drift (space handbrake) — the whole model is these TWO knobs ──────
    // NFS-Carbon style: rotation is a direct steering-controlled yaw torque, decoupled
    // from tire grip. A grip-asymmetry model (front bite yaws the car) self-stalls: past
    // the initial angle the front grip becomes an ALIGNING force that resists further
    // rotation — an equilibrium drift angle, never a full circle/donut.

    /**
     * Uniform lateral grip while the handbrake is pulled — how much the car slides.
     * Momentum carries (the alignment assist is off during handbrake); rotation authority
     * comes entirely from driftYawTorque.
     */
    @Export public float driftGrip = 0.05f;

    /**
     * Yaw torque (N·m) applied per unit of steering while the handbrake is held — how fast
     * the car rotates in a drift. Against groundedAngularDamp this settles at a steady
     * rotation rate (~2 rad/s at defaults: a full donut in ~3 s). Works from a standstill
     * too when the throttle is open (burnout donuts — gas overrides the parking lock).
     */
    @Export public float driftYawTorque = 9000f;

    // ── Parking / idle stability ──────────────────────────────────────────

    /**
     * Speed (m/s) below which an idle (no drive input) grounded vehicle is a park
     * candidate: its velocity is held at zero and, after {@link #parkDelaySeconds},
     * the RigidBody is put to sleep so wheel forces stop and a character standing
     * against the zero-friction body can no longer shove it.
     */
    @Export public float parkSpeedThreshold = 0.5f;

    /** Continuous low-speed idle dwell (s) before the parked body sleeps (prevents sleep/wake flap). */
    @Export public float parkDelaySeconds = 0.5f;

    /**
     * Speed (m/s) below which handbrake / foot-brake-at-idle becomes a hard static lock
     * (velocity zeroed) instead of mere friction — the true parking brake on slopes.
     * Above this speed the handbrake keeps its drift meaning (lateral grip kill only).
     */
    @Export public float parkingLockSpeed = 1.5f;

    // ── Power ─────────────────────────────────────────────────────────────

    /** Top speed (m/s). Acceleration curve is sampled at (speed/maxSpeed). */
    @Export public float maxSpeed         = 20.0f;

    /** Peak motor force (N). Multiplied by accelerationCurve and throttle input. */
    @Export public float acceleration     = 9000.0f;

    /**
     * Force-vs-speed multiplier curve. X = speed ratio (0–1), Y = force multiplier (0–1).
     * Null = linear fallback: multiplier = max(0, 1 − speedRatio).
     */
    @Export public Curve accelerationCurve;

    /**
     * Extra motor-force multiplier at standstill (arcade launch punch — GTA/NFS cars
     * over-deliver torque off the line), fading linearly to 1.0 by launchBoostEndRatio
     * of maxSpeed. 1.0 = off. Top-speed behaviour is unaffected.
     */
    @Export public float launchBoost         = 1.3f;

    /** Speed ratio (speed/maxSpeed) by which launchBoost has fully faded to 1.0. */
    @Export public float launchBoostEndRatio = 0.3f;

    /** Reverse top speed as a fraction of maxSpeed (arcade: cars back up slowly — ~48 km/h
     *  on the 240 km/h default, instead of full forward speed backwards). */
    @Export public float reverseSpeedFraction = 0.2f;

    /**
     * Arcade corner-speed retention (the NFS/GTA "rail" assist): rate (s⁻¹) at which the
     * horizontal velocity direction rotates toward the body heading in a grounded, gripping
     * turn. Redirecting momentum instead of letting tire friction scrub it preserves |v|
     * through corners — without it a steered (non-drift) turn bleeds ~25% speed. Skipped
     * while handbraking/slipping so drifts stay drifts; scaled down by flatGripScale when
     * any tire is flat. 0 = off (pure tire-scrub physics).
     */
    @Export public float momentumAlignRate   = 4.0f;

    // ── Steering ──────────────────────────────────────────────────────────

    /** Steering return speed (rad/s) when the player releases the stick. */
    @Export public float tireMaxTurnSpeed   = 2.0f;

    /** Maximum steering angle from straight-ahead (degrees). */
    @Export public float tireMaxTurnDegrees = 25.0f;

    // ── NOS / booster ─────────────────────────────────────────────────────

    /** Motor-force multiplier while boosting. ≤ 1 disables the booster entirely. */
    @Export public float boostAccelMultiplier = 1.8f;

    /** Top-speed multiplier while boosting (raises the accel-curve ceiling). */
    @Export public float boostMaxSpeedMultiplier = 1.25f;

    /** Seconds of continuous boost in a full tank. */
    @Export public float boostCapacitySeconds = 4f;

    /** Seconds of boost regained per second while not boosting. */
    @Export public float boostRechargeRate = 0.5f;

    // ── Damageable tires ──────────────────────────────────────────────────

    /** Hit points per tire (each wheel's TireHit collider routes weapon damage here). */
    @Export public float tireMaxHealth = 60f;

    /** Fraction of a tire hit's damage that still reaches the vehicle body Health. */
    @Export public float tireDamagePassthrough = 0.25f;

    /** Effective rolling-radius scale of a flat (rides on the rim; also the visual squash). */
    @Export public float flatRadiusScale = 0.6f;

    /** Suspension rest-distance scale of a flat — the corner visibly sags. */
    @Export public float flatRestScale = 0.8f;

    /** Lateral grip multiplier on a flat wheel — the handling destabilizer. */
    @Export public float flatGripScale = 0.45f;

    /** Motor force multiplier on a flat driven wheel. */
    @Export public float flatMotorScale = 0.7f;

    /** Yaw pull torque (N·m at maxSpeed) toward the flat side when flats are asymmetric. */
    @Export public float flatPullTorque = 600f;

    // ── High-speed stability (GTA-style anti-flip) ────────────────────────

    /**
     * Fraction of tireMaxTurnDegrees still available at maxSpeed. Speed-limited steering
     * is the single biggest arcade-stability trick: full lock when slow, a few degrees
     * flat-out, so a top-speed swerve can't generate flip-level lateral force. 1 = off.
     * Kept moderate (0.45) now that maxLateralG is the real safety bound — stacking a
     * harsh angle limit on top of the friction circle made top-speed steering feel dead.
     */
    @Export public float steeringHighSpeedFraction = 0.45f;

    /** Speed ratio (speed/maxSpeed) at which the steering limit starts shrinking. */
    @Export public float steeringLimitStartRatio = 0.25f;

    /**
     * How far the lateral grip force's application point is lifted from the tire contact
     * toward center-of-mass height as speed rises (0 = always at the contact — full roll
     * lever arm; 1 = fully at CoM height — grip without roll torque).
     */
    @Export public float lateralForceHeightBlend = 0.85f;

    /**
     * Speed ratio (speed/maxSpeed) at which lateralForceHeightBlend reaches full strength.
     * Speed-limited steering keeps the tires in their peak-grip slip range, so real
     * cornering force (and its roll moment) peaks at MID speeds — the blend must be fully
     * up by then, not only at maxSpeed.
     */
    @Export public float lateralBlendFullRatio  = 0.5f;

    /**
     * Anti-roll bar: force (N) per metre of left/right suspension-compression difference,
     * transferred across each axle. 0 = off (escalation lever if the CoM-height blend
     * alone doesn't tame a tall body).
     */
    @Export public float antiRollStiffness = 0f;

    /** Downforce (N per (m/s)²) pressing the body along −bodyUp while grounded; capped at ~1× weight. */
    @Export public float downforceCoefficient = 4f;

    /** Angular damping while any wheel is grounded (calms roll/pitch jitter without killing yaw). */
    @Export public float groundedAngularDamp = 2f;

    /** Angular damping while fully airborne (low — jumps should tumble naturally). */
    @Export public float airborneAngularDamp = 0.5f;

    /** Soft keep-upright corrective torque (N·m at 90° tilt) while grounded. 0 = off. */
    @Export public float uprightTorque = 3000f;

    // ── Carrier stubs — motorcycle / boat / airplane (drivable prototypes) ─

    /** Motorcycle: max lean (deg) into a full-lock turn at speed (banks via the upright assist). */
    @Export public float motorcycleLeanDegrees = 28f;

    /** Boat: hull buoyancy-probe half extents (m) — 4 corner probes at ±width/±length. */
    @Export public float hullHalfWidth  = 1.0f;
    @Export public float hullHalfLength = 2.0f;

    /** Boat: buoyancy spring (N per metre submerged, per probe) and its vertical damping. */
    @Export public float buoyancyStrength = 15000f;
    @Export public float buoyancyDamping  = 3000f;

    /** Boat: linear damp while afloat (water resistance). */
    @Export public float waterDrag = 1.0f;

    /** Boat: yaw torque (N·m) at full rudder. */
    @Export public float rudderTorque = 9000f;

    /** Airplane: lift (N per (m/s)² of forward speed) along body-up, capped near 1.3× weight. */
    @Export public float liftCoefficient = 8f;

    /** Airplane: control-surface torques (N·m at full input). */
    @Export public float pitchTorque = 12000f;
    @Export public float rollTorque  = 9000f;

    // ── Combat / weapon ───────────────────────────────────────────────────

    /**
     * How the occupant fires while inside:
     *   0 = NONE             — no shooting
     *   1 = PASSENGER_WEAPON — occupant fires own weapon via vehicle camera
     *   2 = VEHICLE_WEAPON   — vehicle's own FirearmItem fires; occupant weapon disabled
     */
    @Export public int weaponModeIndex = 1;

    /** Passengers (seats 1..n) may fire their own weapon from the window (GTA drive-by). */
    @Export public boolean passengerSeatsCanShoot = true;

    /**
     * Drive-by aim SWEEP for a LEFT-side seat: how far the occupant's weapon may turn from the
     * carrier's forward, in degrees, positive to the RIGHT. Mirrored automatically for a
     * right-side seat ({@code Vehicle.seatAimSweep} reads the side off the seat marker's own X),
     * so one authored pair covers every seat in the car.
     *
     * <p><b>This is the only yaw limit on a seated occupant's aim</b> — the whole point of it. A
     * body belted into a seat cannot turn, so unlike an on-foot character (whose mesh yaws to the
     * aim point and therefore never reaches its bone limit) a seated one hits a limit constantly;
     * with two of them — this and {@code Stance.aimYawLimit} — the gun stopped at the tighter one
     * while the bullet kept going to the camera's point, up to 135 degrees apart. So `DriveCarrier`
     * ships {@code aimYawLimit = 180} (uncapped) and this decides it, once, for the bones AND the
     * bullet AND the reticle.
     *
     * <p><b>Full circle by default, and that is a deliberate departure from GTA.</b> Rockstar
     * restricts a left-hand-drive driver to their own window plus forward — which is why passenger
     * drive-bys exist at all — and this pair is how you author that ({@code -180 / +20} for a left
     * seat, mirrored automatically). It is not the default because the restriction only reads as a
     * RULE when the player can see it: here it read as a dead zone plus a body swing every time the
     * aim crossed it, i.e. as lag. The posture ({@link #postureHoldDeg}) turns the occupant toward
     * whichever side the target is on, so the far side is reachable by a body rather than only by a
     * clamp, and the bones are still only ever asked for the residual.
     *
     * <p>Narrow it per carrier when the geometry genuinely says so — an armoured van with one gun
     * port, a seat boxed in by a bulkhead. {@code Vehicle.warnIfAimSectorUnreachable} checks the
     * pair against what the posture and the rig can actually cover.
     */
    @Export public double drivebyAimMin = -180.0;

    /** The other end of {@link #drivebyAimMin}: how far the weapon may cross the bonnet. */
    @Export public double drivebyAimMax = 180.0;

    // ── Drive-by POSTURE (how the body gets there, vs. the sweep's "may it") ──

    /**
     * How much of the aim the BONES carry before the body starts turning, degrees.
     *
     * <p><b>This is the whole drive-by posture rule, and it is CONTINUOUS.</b> Beyond this the
     * occupant turns in the seat by exactly the excess — {@code body = sign(heading) *
     * min(rearAimBodyYaw, |heading| - postureHoldDeg)} — so the residual left to the spine and
     * collarbones sits at this value all the way round and the body tracks the aim smoothly.
     *
     * <p>It replaced an enter/exit threshold pair that snapped the body to a FIXED angle
     * ({@code rearAimBodyYaw}) and latched which way it had turned. That version had two faults a
     * player feels immediately and neither is fixable by tuning: between the exit threshold and the
     * full turn the body sat pinned at 150 degrees while the aim was near 80, and once latched it
     * held that side, so sweeping across the rear left the body facing the wrong way with the bones
     * covering the difference. A continuous rule needs no hysteresis to be stable either — it has no
     * step to chatter across.
     *
     * <p>Keep it at or below the rig's reach ({@code Stance.aimYawLimit}); the gun visibly lags the
     * reticle above it, and {@code Vehicle.warnIfAimSectorUnreachable} says so on entry.
     */
    @Export public double postureHoldDeg = 80.0;

    /**
     * The MOST the body may turn in the seat, degrees — a cap on the rule above, not a target. A
     * belted body cannot come round to 180.
     */
    @Export public double rearAimBodyYaw = 150.0;

    /**
     * How far the occupant slides toward the side they are turning, metres, at a full turn. A body
     * that pivots on the spot reads as a mannequin spinning; bracing across is what sells "turned
     * round to fire", and it gets the weapon clear of the seat back.
     */
    @Export public double rearAimSeatShift = 0.22;

    /**
     * How fast the body turns between postures, degrees per second. Fast enough to answer the
     * player, slow enough to read as a person turning.
     */
    @Export public double postureTurnSpeed = 360.0;

    /**
     * Beyond this heading magnitude the body KEEPS the side it is already turned to, degrees.
     *
     * <p>The only latch left, and it exists for one unavoidable discontinuity: directly behind, +179
     * and -179 are a degree apart in the world and opposite in the number, so "turn toward the
     * target" flips the body ~200 degrees for a degree of aim movement. Near the rear the two poses
     * are almost the same anyway, so holding the current side is both stable and correct; it releases
     * as soon as the aim comes forward of this.
     */
    @Export public double postureSideLatchDeg = 150.0;

    // ── Collision damage ──────────────────────────────────────────────────

    /** Minimum vehicle speed (m/s) needed to deal collision damage. 0 = disabled. */
    @Export public float vehicleCollisionMinSpeed    = 5.0f;

    /** Damage per m/s above vehicleCollisionMinSpeed. */
    @Export public float vehicleCollisionDamageScale = 100.0f;

    // ── Destruction explosion ─────────────────────────────────────────────

    /** Blast radius (metres) on destruction. 0 = no explosion. */
    @Export public float explosionRadius    = 6f;

    /** Maximum damage at blast centre; falls off quadratically to zero at radius. */
    @Export public float explosionMaxDamage = 100f;

    /** Physics push force applied to bodies caught in the blast. */
    @Export public float explosionPushForce = 25f;

    // ── Damage-tier VFX ───────────────────────────────────────────────────

    /** Health fraction below which the engine smokes (grey plume). */
    @Export public float damageSmokeFraction = 0.66f;

    /** Health fraction below which the engine burns (fire + heavy smoke). */
    @Export public float damageFireFraction = 0.33f;

    // ── Wreck ─────────────────────────────────────────────────────────────

    /**
     * Scene spawned at the vehicle's world transform on destruction.
     * Null = no wreck remains after the explosion.
     * Set per config preset so a sports car gets a different burnt shell than a tank.
     */
    @Export public PackedScene wreckScene;

    /** Seconds the wreck node stays in the scene before being removed. */
    @Export public float wreckDuration = 15f;

    // ── Identity ──────────────────────────────────────────────────────────

    /** Icon shown in the kill feed when this vehicle kills a character. */
    @Export public Texture2D vehicleIcon;

    public VehicleConfig() { super(); }
}

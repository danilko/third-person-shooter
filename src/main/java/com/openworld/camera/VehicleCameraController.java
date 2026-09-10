package com.openworld.camera;

import com.openworld.camera.CameraMode;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.*;
import godot.core.NodePath;
import godot.core.Transform3D;
import godot.core.Vector3;
import godot.global.GD;

import static godot.api.Input.INSTANCE;
import com.openworld.carrier.vehicle.Vehicle;

/**
 * Vehicle follow/FPS camera — mirrors the character Yaw/Pitch/Pivot/SpringArm pattern.
 *
 * Scene hierarchy expected:
 *   CameraController (this node, 180°Y, setAsTopLevel)
 *     Yaw / Pitch / Pivot (180°Y) / SpringArm / Proxy   ← shared TPS rig
 *   FPSCameraMount (Marker3D sibling — over the bonnet)
 *   Seats/Seat0/CockpitCameraMount (Marker3D — the driver's eye line; optional)
 *   ActiveCamera (Camera3D sibling — written each frame)
 *     AimRay (RayCast3D)
 *
 * THREE views, cycled by the `view` action in {@link #cycleView()}: TPS follow, cockpit, bonnet.
 * Two fields carry that, and the split is the point — {@link CameraMode} (is this first person?)
 * is MIRRORED with {@code Character.isFpsMode} across the enter/exit seam, so the view the player
 * chose on foot is the view they get in the seat and the view they get back when they climb out;
 * {@link VehicleEyeMount} (WHICH first person?) is carrier-local and remembered per vehicle.
 *
 * Four sub-modes:
 *   TPS follow  — yaw/pitch lerp to vehicle heading at independent speeds (yaw faster than
 *                 pitch). Slope is low-pass filtered so pitch never snaps on landings.
 *   TPS aim     — passengerAimMode + holding aim/fire. Mouse drives orientation only; spring
 *                 arm stays at followPitch so the camera position is stable. Pitch pivot is
 *                 effectively at the camera rather than the vehicle origin.
 *   FPS follow  — cockpit mode (Forza/NFS). Yaw locks to vehicle heading instantly; pitch
 *                 gently follows the vehicle nose angle. No mouse input in this sub-mode.
 *   FPS aim     — holding aim/fire in FPS mode. Mouse drives yaw/pitch freely.
 */
@Script(className = "VehicleCameraController")
public class VehicleCameraController extends Node3D {

    // ── Exports ───────────────────────────────────────────────────────────────

    @Export public double pitchMin            = -60.0;
    @Export public double pitchMax            =  80.0;
    @Export public double height              =  4.0;
    /** Degrees the TPS spring arm tilts downward at rest. Positive = arm extends upward-behind. */
    @Export public double followPitchDeg      =  15.0;

    /** Mouse sensitivity (degrees per raw pixel) — applies in FPS aim and TPS aim modes. */
    @Export public double yawSensitivity      =  0.07;
    @Export public double pitchSensitivity    =  0.07;
    /**
     * Extra sensitivity multiplier applied only in TPS aim mode.
     * TPS camera sits ~8-9 m behind the vehicle, so the same angular change feels smaller
     * than in FPS. Raise this value (default 2×) to compensate for the distance.
     */
    @Export public double tpsAimSensitivityMult = 2.0;

    /** TPS follow — lerp speed for yaw catching up to vehicle heading after releasing aim. */
    @Export public double yawRecoverySpeed    =  3.0;
    /**
     * TPS follow — lerp speed for pitch returning to the follow angle.
     * Intentionally slower than yaw to reduce motion sickness on sharp turns.
     */
    @Export public double pitchRecoverySpeed  =  1.5;
    /**
     * How fast the internal slope estimate tracks the vehicle's actual slope.
     * Lower values smooth out pitch spikes on landings and handbrake snap-yaws.
     */
    @Export public double slopeSmoothSpeed    =  2.0;

    /**
     * FPS cockpit — lerp speed for pitch following the vehicle nose angle.
     * Yaw always snaps instantly for direct steering feedback. Pitch is smoothed
     * to reduce vertigo on bumpy terrain.
     */
    @Export public double fpsPitchFollowSpeed =  5.0;

    @Export public double recoilRecoverySpeed =  8.0;

    /** The BONNET eye point — over the nose. See {@link VehicleEyeMount#BONNET}. */
    @Export public NodePath fpsCameraMountPath = new NodePath("FPSCameraMount");

    /**
     * The COCKPIT eye point — the driver's own eye line.
     *
     * <p>Authored as a {@code Marker3D} CHILD OF THE SEAT rather than a number in this class, so
     * the seat stays the one owner of where the driver is: move {@code Seat0} and the cockpit view
     * moves with it, with nothing here to keep in sync. It is also the only honest way to set the
     * height — a seated head's offset above the seat anchor differs per vehicle and per character
     * rig, so it is a thing to drag in the editor, not a constant to guess in Java.
     *
     * <p><b>Author it against the eye of a driver who is SEATED AND IN COMBAT.</b> Entering a seat
     * sets {@code combat = true}, which switches on the {@code NeckFront} blend and the aim
     * modifier — and both move {@code neck_01}, which is what {@code MarkerFPSCamera} hangs off. An
     * offset measured on a standing body with combat off is 0.10 m too far back and 0.06 m too low,
     * which puts the camera behind the driver's own neck looking at the back of their shoulders,
     * with the head hidden so nothing on screen explains it. {@code probe_vehicle_views.gd}
     * measures the real thing and asserts it.
     *
     * <p><b>A fixed seat-relative mount, deliberately, rather than following the head bone.</b> The
     * carrier camera's ray feeds {@code Character.applySeatedAimTarget}, which drives the aim
     * modifier, which drives {@code neck_01} — so a camera that followed that bone would close the
     * exact feedback loop that made first-person driving tangle (see
     * {@code FPSCameraController._physicsProcess}). A driving view wants to be steady anyway.
     *
     * <p>Left unresolved (a vehicle scene with no such marker) the cockpit view falls back to the
     * bonnet mount, so an older carrier scene keeps working and simply offers two views instead of
     * three.
     */
    @Export public NodePath cockpitCameraMountPath =
            new NodePath("../Seats/Seat0/CockpitCameraMount");

    // ── Speed feel (racing-game sense of speed) ───────────────────────────────
    // OFF BY DEFAULT SINCE 2026-08-31, on a walk-test report: the previous package — an 18 deg
    // FOV widening plus a 5 deg throttle surge, a 6 deg NOS kick, a 2 m spring-arm pull-back and a
    // full-screen peripheral speed-line shader — was "too much... impact the real driving
    // visual/hard to see road". The overlay is DELETED outright (scene nodes and
    // `SpeedLines.gdshader` are gone, not merely hidden, so nothing can switch it back on by
    // accident); the camera terms below are kept as knobs and shipped at 0, because a camera that
    // cannot react to speed at all is a decision to take deliberately rather than by deletion.
    //
    // The previous values are recorded in each field so the old feel is one edit away:
    // fovSpeedBoost 18, fovAccelBoost 5, fovNosBoost 6, armSpeedExtend 2.
    //
    // The replacement is planned to be WORLD-SPACE rather than screen-space — a trail light or a
    // wheel/exhaust effect that sits on the car and never covers the road. Nothing here is in its
    // way; it belongs on the vehicle, not on the camera.

    /** Extra FOV (degrees) added at fovReferenceSpeed. 0 disables the FOV kick. Was 18. */
    @Export public double fovSpeedBoost     = 0.0;

    /** Speed (m/s) at which the full FOV boost is reached. */
    @Export public double fovReferenceSpeed = 30.0;

    /** Lerp speed for FOV changes (also eases back down when slowing/exiting). */
    @Export public double fovLerpSpeed      = 4.0;

    /**
     * Extra FOV (degrees) at full forward acceleration — the launch/overtake "surge" every
     * arcade racer plays on throttle. Decays as acceleration flattens, independent of speed.
     * Was 5.
     */
    @Export public double fovAccelBoost     = 0.0;

    /** Forward acceleration (m/s²) at which the full fovAccelBoost is reached. */
    @Export public double accelReference    = 7.0;

    /** Extra FOV (degrees) while NOS is active (on top of the speed/accel terms). Was 6. */
    @Export public double fovNosBoost       = 0.0;

    /**
     * Metres the TPS spring arm extends at fovReferenceSpeed — the car shrinks in frame and
     * the world flows past faster (the GTA/Horizon speed pull-back). 0 disables. Was 2.
     */
    @Export public double armSpeedExtend    = 0.0;

    // ── Node refs ─────────────────────────────────────────────────────────────

    private Node3D      target;
    private Camera3D    activeCamera;
    private RayCast3D   aimRay;
    private Node3D      fpsCameraMount;
    private Node3D      cockpitCameraMount;

    private Node3D      yawNode;
    private Node3D      pitchNode;
    private Node3D      pivotNode;
    private SpringArm3D tpsSpringArm;
    private Node3D      tpsProxyNode;

    /** Rest FOV captured at _ready — the speed kick always eases back to this. */
    private float          baseFov = 70f;
    /** Rest spring-arm length captured at _ready — armSpeedExtend adds on top of this. */
    private double         baseSpringLength = 0.0;
    private double         lastSpeed        = 0.0;
    private double         smoothedAccel    = 0.0;

    // ── State ─────────────────────────────────────────────────────────────────

    private CameraMode cameraMode       = CameraMode.TPS;
    /** Which first-person eye point, when {@link #cameraMode} is FPS. Carrier-local, remembered. */
    private VehicleEyeMount eyeMount    = VehicleEyeMount.COCKPIT;
    private boolean    passengerAimMode = false;

    private double yaw          = 0.0;
    private double pitch        = 0.0;
    private double recoilPitch  = 0.0;
    private double recoilYaw    = 0.0;
    private double pendingYaw   = 0.0;
    private double pendingPitch = 0.0;
    /** Low-pass filtered slope — prevents pitch snap when the vehicle hits a slope abruptly. */
    private double smoothedSlope = 0.0;

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    @Register
    @Override
    public void _ready() {
        target = (Node3D) getOwner();

        Node ac = getParent().getNodeOrNull("ActiveCamera");
        if (ac instanceof Camera3D c) {
            activeCamera = c;
            Node ar = activeCamera.getNodeOrNull("AimRay");
            if (ar instanceof RayCast3D r) aimRay = r;
        }

        yawNode      = (Node3D)      getNode(new NodePath("Yaw"));
        pitchNode    = (Node3D)      getNode(new NodePath("Yaw/Pitch"));
        pivotNode    = (Node3D)      getNode(new NodePath("Yaw/Pitch/Pivot"));
        tpsSpringArm = (SpringArm3D) getNode(new NodePath("Yaw/Pitch/Pivot/SpringArm"));
        tpsProxyNode = (Node3D)      getNode(new NodePath("Yaw/Pitch/Pivot/SpringArm/Proxy"));

        Node m = getNodeOrNull(fpsCameraMountPath);
        if (m instanceof Node3D n) fpsCameraMount = n;

        Node cm = (cockpitCameraMountPath == null || cockpitCameraMountPath.isEmpty())
                ? null : getNodeOrNull(cockpitCameraMountPath);
        if (cm instanceof Node3D n) cockpitCameraMount = n;

        if (activeCamera != null) baseFov = activeCamera.getFov();
        if (tpsSpringArm != null) baseSpringLength = tpsSpringArm.getLength();
        if (target instanceof CollisionObject3D co) {
            if (tpsSpringArm != null) tpsSpringArm.addExcludedObject(co.getRid());
            if (aimRay != null)       aimRay.addException(co);
        }

        setAsTopLevel(true);
        yaw   = Math.toDegrees(target.getGlobalRotation().getY());
        pitch = followPitchDeg;
    }

    // ── Public API ────────────────────────────────────────────────────────────

    public void setPassengerAimMode(boolean enabled) { passengerAimMode = enabled; }
    public void setCameraMode(CameraMode mode)        { cameraMode = mode; }
    public CameraMode getCameraMode()                 { return cameraMode; }

    /**
     * This rig's aim ray — the one {@link #getAimTarget()} reads, so the one an occupant must be
     * excepted from. Exposed rather than duplicating the exception list here: what a body says must
     * not be hit belongs to the body ({@code Character.addAimExceptionsTo}).
     */
    public RayCast3D getAimRayNode()                  { return aimRay; }

    public VehicleEyeMount getEyeMount()              { return eyeMount; }
    public void setEyeMount(VehicleEyeMount mount)    { eyeMount = mount; }

    /**
     * True while this camera is rendering from behind the DRIVER'S OWN EYES — the one view of the
     * three for which the driver's head must be hidden. Asked by {@code Character
     * .refreshHeadVisibility} every frame, which is why it answers false when the cockpit mount is
     * missing (the bonnet fallback is not in anyone's skull) and false in TPS.
     */
    public boolean isCockpitView() {
        return cameraMode == CameraMode.FPS
                && eyeMount == VehicleEyeMount.COCKPIT
                && cockpitCameraMount != null;
    }

    /**
     * One press of {@code view} = the next of the THREE driving views, in the order a player would
     * expect to find them: TPS follow -> cockpit -> bonnet -> TPS.
     *
     * <p>One writer for both fields, so "which view am I in" cannot be assembled from two
     * independently-toggled halves. The cockpit step is skipped when the vehicle scene has no
     * cockpit mount, which keeps the cycle honest (a press always changes the picture) rather than
     * silently landing on a view that renders from the bonnet anyway.
     */
    public void cycleView() {
        if (cameraMode == CameraMode.TPS) {
            cameraMode = CameraMode.FPS;
            eyeMount   = (cockpitCameraMount != null) ? VehicleEyeMount.COCKPIT : VehicleEyeMount.BONNET;
        } else if (eyeMount == VehicleEyeMount.COCKPIT) {
            eyeMount   = VehicleEyeMount.BONNET;
        } else {
            cameraMode = CameraMode.TPS;
        }
    }

    /** Where the first-person camera sits this frame — the cockpit mount, else the bonnet one. */
    private Node3D eyeMountNode() {
        return (eyeMount == VehicleEyeMount.COCKPIT && cockpitCameraMount != null)
                ? cockpitCameraMount : fpsCameraMount;
    }

    /**
     * Note the sign, and that it is NOT a leftover. This rig is the vehicle's own — its own
     * {@code yaw}/{@code pitch}/{@code pitchMin}/{@code pitchMax} fields, its own scene under
     * {@code Vehicle.tscn}, and it still carries the {@code Pivot} 180-degree Y flip that negates
     * pitch downstream, so positive pitch means look DOWN here and a kick subtracts. AIM_PLAN.md
     * W3 unified the CHARACTER rig (where {@code ControlRotation} is now a world rotation with
     * positive pitch UP) and deliberately left this one alone: it shares no state with
     * {@code ControlRotation}, so it is self-consistent as it stands. Change it as a whole or not
     * at all — matching one half of it to the character convention is how a sign bug is born.
     */
    public void applyRecoil(double pitchKick, double yawKick) {
        recoilPitch -= pitchKick;
        recoilYaw   += yawKick;
    }

    /** Fallback aim distance (m) when there is no ray to read a length from. */
    private static final double AIM_FALLBACK_RANGE = 200.0;

    /**
     * The world point this carrier is aiming at.
     *
     * <p><b>Every fallback here points FORWARD, and none of them is the carrier's own origin.</b>
     * That used to be the no-ray answer, and it is the one answer that cannot be used: it puts the
     * aim point INSIDE the car, 0.8 m from the driver's own chest, where a
     * {@code Vehicle.clampSeatAim} heading is computed from a metre-long vector (noise), the
     * drive-by posture reacts to that noise, and the shot's sight leg — which reads this point
     * through {@code Character.applySeatedAimTarget} — is aimed at the vehicle the shooter is
     * sitting in. Measured on {@code probe_driveby_aim.gd}: 0.8 m against the 200 m the ray gives,
     * and a 64-degree error in the aim heading whenever it was hit.
     */
    public Vector3 getAimTarget() {
        if (aimRay != null && aimRay.isColliding()) return aimRay.getCollisionPoint();
        if (activeCamera != null) {
            double range = (aimRay != null) ? aimRay.getTargetPosition().length() : AIM_FALLBACK_RANGE;
            Vector3 fwd = activeCamera.getGlobalTransform().getBasis().getColumn(2).times(-1f);
            return activeCamera.getGlobalPosition().plus(fwd.times((float) range));
        }
        Vector3 fwd = target.getGlobalTransform().getBasis().getColumn(2).times(-1f);
        return target.getGlobalPosition().plus(fwd.times((float) AIM_FALLBACK_RANGE));
    }

    // ── Input ─────────────────────────────────────────────────────────────────

    @Register
    @Override
    public void _input(InputEvent event) {
        if (activeCamera == null || !activeCamera.isCurrent()) return;
        if (!(event instanceof InputEventMouseMotion m)) return;
        // Accumulate only while actively aiming:
        //   FPS follow: camera locked to vehicle — mouse ignored outside aim.
        //   TPS follow: camera auto-follows vehicle heading — mouse ignored outside aim.
        boolean doAccumulate = isAimingOrFiring()
                && (cameraMode == CameraMode.FPS || passengerAimMode);
        if (!doAccumulate) return;

        double tpsMult = (cameraMode == CameraMode.TPS) ? tpsAimSensitivityMult : 1.0;
        pendingYaw   -= m.getRelative().getX() * yawSensitivity   * tpsMult;
        pendingPitch += m.getRelative().getY() * pitchSensitivity * tpsMult;
        getViewport().setInputAsHandled();
    }

    // ── Physics ───────────────────────────────────────────────────────────────

    @Register
    @Override
    public void _physicsProcess(double delta) {
        if (activeCamera != null && activeCamera.isCurrent()
                && INSTANCE.isActionJustPressed("view", false)) {
            cycleView();
        }

        recoilPitch = GD.lerp(recoilPitch, 0.0, recoilRecoverySpeed * delta);
        recoilYaw   = GD.lerp(recoilYaw,   0.0, recoilRecoverySpeed * delta);

        Vector3 vehiclePos    = target.getGlobalPosition();
        double  vehicleYawDeg = Math.toDegrees(target.getGlobalRotation().getY());
        // Column 2 of the basis = vehicle local +Z (backward direction in world space).
        Vector3 vehicleZ      = target.getGlobalTransform().getBasis().getColumn(2);

        // Smooth the raw slope so the camera pitch target never jumps on abrupt landings
        // or handbrake snap-yaws. slopeSmoothSpeed controls how quickly the estimate catches up.
        double rawSlope      = Math.toDegrees(Math.asin(GD.clamp(-vehicleZ.getY(), -1.0, 1.0)));
        smoothedSlope        = GD.lerp(smoothedSlope, rawSlope, slopeSmoothSpeed * delta);
        double targetFollowPitch = GD.clamp(followPitchDeg - smoothedSlope, pitchMin, pitchMax);

        if (cameraMode == CameraMode.FPS) {
            Node3D eye = eyeMountNode();
            if (eye != null) setGlobalPosition(eye.getGlobalPosition());

            if (isAimingOrFiring()) {
                // FPS aim: full mouse control, same as character FPS.
                yaw   += pendingYaw;
                pitch += pendingPitch;
                pitch  = GD.clamp(pitch, pitchMin, pitchMax);
            } else {
                // FPS cockpit (Forza/NFS style): yaw snaps to vehicle heading immediately
                // so the driver always sees where they are steering. Pitch follows the
                // vehicle's nose angle with a gentle lerp to reduce vertigo on bumpy terrain.
                yaw   = vehicleYawDeg;
                double vehiclePitchDeg = Math.toDegrees(
                        Math.asin(GD.clamp(vehicleZ.getY(), -1.0, 1.0)));
                pitch = GD.lerp(pitch, vehiclePitchDeg, fpsPitchFollowSpeed * delta);
            }

            applyYawPitch(yaw + recoilYaw, GD.clamp(pitch + recoilPitch, pitchMin, pitchMax));
            if (activeCamera != null && pivotNode != null) {
                activeCamera.setGlobalTransform(pivotNode.getGlobalTransform());
            }

        } else if (passengerAimMode && isAimingOrFiring()) {
            // TPS aim: camera stays at the stable follow position; pitch/yaw drive orientation only.
            // Pitch pivots at the camera (not at the vehicle origin): spring arm is locked to
            // followPitch for position stability; aim pitch rotates pivotNode (forward-facing)
            // so the player can look freely up/down without the camera orbiting the vehicle.
            setGlobalPosition(new Vector3(vehiclePos.getX(), vehiclePos.getY() + height, vehiclePos.getZ()));
            yaw   += pendingYaw;
            pitch += pendingPitch;
            pitch  = GD.clamp(pitch, pitchMin, pitchMax);
            double effYaw   = yaw + recoilYaw;
            double effPitch = GD.clamp(pitch + recoilPitch, pitchMin, pitchMax);

            // Pass 1: set aim pitch → capture pivotNode orientation (forward-facing aim direction).
            applyYawPitch(effYaw, effPitch);
            Transform3D aimXform = (pivotNode != null) ? pivotNode.getGlobalTransform() : null;

            // Pass 2: restore pitch to follow angle → spring arm holds camera at TPS position.
            if (pitchNode != null) {
                Vector3 pr = pitchNode.getRotationDegrees();
                pr.setX(targetFollowPitch);
                pitchNode.setRotationDegrees(pr);
            }

            if (activeCamera != null && aimXform != null && tpsProxyNode != null) {
                aimXform.setOrigin(tpsProxyNode.getGlobalPosition());
                activeCamera.setGlobalTransform(aimXform);
            }

        } else {
            // TPS follow: yaw and pitch lerp at independent speeds.
            // Yaw recovery is faster (3 s⁻¹ default) so the camera pivots back promptly after a
            // turn. Pitch recovery is gentler (1.5 s⁻¹ default) so sudden slope changes read as
            // smooth tilts rather than jarring snaps.
            setGlobalPosition(new Vector3(vehiclePos.getX(), vehiclePos.getY() + height, vehiclePos.getZ()));
            yaw   = Math.toDegrees(GD.lerpAngle(
                    Math.toRadians(yaw), Math.toRadians(vehicleYawDeg), yawRecoverySpeed * delta));
            pitch = GD.lerp(pitch, targetFollowPitch, pitchRecoverySpeed * delta);
            applyYawPitch(yaw + recoilYaw, GD.clamp(pitch + recoilPitch, pitchMin, pitchMax));
            if (activeCamera != null && tpsProxyNode != null) {
                activeCamera.setGlobalTransform(tpsProxyNode.getGlobalTransform());
            }
        }

        pendingYaw   = 0.0;
        pendingPitch = 0.0;

        applySpeedFeel(delta);
    }

    /**
     * Speed-scaled FOV kick + acceleration surge + NOS kick + spring-arm pull-back. FOV uses a
     * smoothstep ease so the effect is felt through the city-speed band rather than only at the
     * top end; the acceleration surge plays the launch shove independent of absolute speed.
     *
     * <p>Every term ships at 0 (see the field block above), so this is inert until someone turns a
     * knob back on — the whole method still runs, and the FOV lerp still eases an already-boosted
     * camera back to rest, which is what makes turning one on mid-session behave.
     */
    private void applySpeedFeel(double delta) {
        if (activeCamera == null) return;
        boolean current = activeCamera.isCurrent();
        double speed = (target instanceof RigidBody3D rb) ? rb.getLinearVelocity().length() : 0.0;
        double t = GD.clamp(speed / Math.max(1e-3, fovReferenceSpeed), 0.0, 1.0);
        double ease = t * t * (3.0 - 2.0 * t);   // smoothstep — responds through the mid band

        // Forward-acceleration surge. Raw per-tick dv/dt is noisy (suspension, kerbs), so
        // low-pass it; only positive acceleration surges (braking is handled by ease-down).
        double rawAccel = (speed - lastSpeed) / Math.max(1e-4, delta);
        lastSpeed = speed;
        smoothedAccel = GD.lerp(smoothedAccel, GD.clamp(rawAccel, 0.0, accelReference),
                Math.min(1.0, 3.0 * delta));
        double surge = fovAccelBoost * (smoothedAccel / Math.max(1e-3, accelReference));

        double nos = (target instanceof Vehicle v && v.isBoosting()) ? fovNosBoost : 0.0;

        double targetFov = current ? baseFov + fovSpeedBoost * ease + surge + nos : baseFov;
        activeCamera.setFov((float) GD.lerp((double) activeCamera.getFov(),
                Math.min(targetFov, baseFov + 32.0),
                Math.min(1.0, fovLerpSpeed * delta)));

        // Speed pull-back: the arm extends with speed so the car shrinks in frame and the
        // world flows past faster. Arm is only consumed in TPS; writing it in FPS is inert.
        if (tpsSpringArm != null && armSpeedExtend > 0.0) {
            double targetLen = current ? baseSpringLength + armSpeedExtend * ease : baseSpringLength;
            tpsSpringArm.setLength((float) GD.lerp(
                    (double) tpsSpringArm.getLength(), targetLen,
                    Math.min(1.0, fovLerpSpeed * delta)));
        }

    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private void applyYawPitch(double effYaw, double effPitch) {
        if (yawNode != null) {
            Vector3 yr = yawNode.getRotationDegrees();
            yr.setY(effYaw);
            yawNode.setRotationDegrees(yr);
        }
        if (pitchNode != null) {
            Vector3 pr = pitchNode.getRotationDegrees();
            pr.setX(effPitch);
            pitchNode.setRotationDegrees(pr);
        }
    }

    private boolean isAimingOrFiring() {
        return INSTANCE.isActionPressed("aim",  false)
            || INSTANCE.isActionPressed("fire", false);
    }
}

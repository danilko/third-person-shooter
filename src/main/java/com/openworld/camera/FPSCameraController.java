package com.openworld.camera;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node3D;
import godot.core.Vector3;
import godot.global.GD;
import com.openworld.character.Character;

/**
 * First-person camera controller — positions the shared ActiveCamera at the character's eye point
 * each frame when {@code is_fps_mode} is true.
 *
 * <p>No Camera3D lives here. The single {@code Character.activeCamera} is written by whichever
 * controller is active: {@link TPSCameraController} writes when {@code !isFpsMode}, this node
 * writes when {@code isFpsMode}. AimRay and AimTarget follow ActiveCamera automatically.
 *
 * <h2>Orientation never comes from the bone</h2>
 * The view's rotation is {@link ControlRotation} — the same world rotation the TPS rig uses — so
 * switching view mode cannot change where the player is looking, and a skeleton modifier cannot
 * roll the horizon. The bone contributes POSITION only, and even that is filtered (below).
 *
 * <h2>Why a bone-mounted FPS camera shakes, and what is done about it</h2>
 * The mount ({@code MarkerFPSCamera}) hangs off a {@code BoneAttachment3D} on {@code neck_01}, so
 * its world position carries every source of neck motion at once: the locomotion clip's head bob,
 * the {@code WeaponChange}/{@code Reload} one-shots, and — because {@code neck_01} is one of
 * {@link com.openworld.character.ShoulderAimModifier}'s driven bones — the aim swing itself. Copied
 * straight through, that is a camera that jitters at footstep frequency and lurches sideways every
 * time you turn your head. Parenting a first-person camera rigidly to a head bone is the classic
 * version of this bug (Cyberpunk 2077 shipped a head-bob toggle for exactly it); the industry
 * answer — Arma, Tarkov, Star Citizen, and every engine that offers a "camera shake / head bob"
 * slider — is to treat the bone as a SIGNAL to be filtered, not as a transform to be copied:
 *
 * <ol>
 *   <li>Work in the MESH's frame, not the world's. The mount rides {@code MeshRoot}, which
 *       {@code MovementController} yaws to the aim, so running/turning is not motion to filter —
 *       in mesh space it is not motion at all. A world-space filter would instead make the camera
 *       trail the body while sprinting.</li>
 *   <li>Split the signal. A slow single-pole average ({@link #baselineFollow}) is the eye's REST
 *       point: it carries the mount's true height and forward offset for free, per stance, with
 *       nothing to author, and it tracks a stance change or a genuine head-turn within a fraction
 *       of a second. What is left over is the fast stuff — bob, recoil shove, clip noise.</li>
 *   <li>Scale and clamp the leftover ({@link #boneMotionScale}, {@link #maxBoneOffset}) so a big
 *       animation swing can never throw the camera, and so bob can be turned off outright
 *       (scale 0) as the accessibility option it is on every shipping game.</li>
 *   <li>Smooth the result ({@link #positionSmoothing}) to kill single-frame jitter.</li>
 * </ol>
 *
 * <p><b>The shoulder is not a concern here.</b> The over-the-shoulder offset is the TPS boom's
 * framing device — {@code CombatState.cameraShoulderOffset} applied along the TPS {@code Yaw}
 * node's X — and this rig neither reads it nor has a boom to hang it on: the eye point is on the
 * character's centre line, where a head is. {@code PlayerCameraController} correspondingly refuses
 * the shoulder-swap key while in FPS.
 *
 * <p>Expected child hierarchy:
 * <pre>
 *   FPSCameraController
 *     Yaw  (Node3D)
 *       Pitch  (Node3D)
 *         Pivot (Node3D)
 * </pre>
 */
@Script(className = "FPSCameraController")
public class FPSCameraController extends Node3D {

    /**
     * World-space anchor — the {@code MarkerFPSCamera} under the neck {@code BoneAttachment3D},
     * wired in code by {@code Character.wireFromMeshConfig} from {@code MeshConfig}.
     */
    @Export
    public Node3D fpsCameraMount;

    /**
     * The frame the mount's position is filtered in — {@code MeshRoot}. Wired in code beside the
     * mount (and NOT scene-exported: a scene-exported node reference can hand back a second JVM
     * wrapper whose Java fields are all at their defaults — see CLAUDE.md "Known Quirks").
     * Null falls back to the character body, which only costs the filter the mesh's own yaw.
     */
    public Node3D frameNode;

    /**
     * How much of the head bone's fast motion reaches the camera, 0..1. 0 is a perfectly rigid
     * eye point (the "head bob off" setting); 1 is the raw bone. 0.25 keeps enough for the walk
     * to read as a walk without the camera arguing with the crosshair.
     */
    @Export
    public double boneMotionScale = 0.25;

    /** Hard ceiling on that motion, metres, before scaling — a one-frame clip pop cannot throw the view. */
    @Export
    public double maxBoneOffset = 0.08;

    /** Rate (1/s) at which the resting eye point follows the mount. Sets the bob/rest split. */
    @Export
    public double baselineFollow = 6.0;

    /** Rate (1/s) of the final position smoothing — removes single-frame jitter. */
    @Export
    public double positionSmoothing = 25.0;

    /**
     * Eye height above the body origin used when there is no mount at all (a mesh whose
     * {@code MeshConfig} names no FPS marker). A degenerate path, but it must not be an early
     * return: a frozen camera left behind in the world is far worse than an approximate eye point.
     */
    @Export
    public double fallbackEyeHeight = 1.55;

    private Character character;
    private Node3D    yawNode;
    private Node3D    pitchNode;
    private Node3D    pivotNode;

    // Filter state, in frameNode's local space.
    private Vector3 baseline = Vector3.Companion.getZERO();
    private Vector3 smoothed = Vector3.Companion.getZERO();
    private boolean primed   = false;

    @Register
    @Override
    public void _ready() {
        yawNode   = (Node3D) getNode("Yaw");
        pitchNode = (Node3D) getNode("Yaw/Pitch");
        pivotNode = (Node3D) getNode("Yaw/Pitch/Pivot");
        setAsTopLevel(true);
        if (getParent() instanceof Character c) character = c;
    }

    @Register
    @Override
    public void _physicsProcess(double delta) {
        if (character == null) return;

        // THE CARRIER OWNS THE DRIVER'S VIEW, AND THIS RIG MUST GET OUT OF THE WAY OF IT.
        //
        // Not an optimisation — it breaks a FEEDBACK LOOP. `AimTarget` hangs off
        // `ActiveCamera/AimRay`, this rig writes `ActiveCamera` from the filtered NECK BONE, and
        // `ShoulderAimModifier` drives `neck_01` from that very target: bone -> camera -> aim
        // target -> bone, once per frame. On foot the loop exists and is tame, which is what the
        // filtering below is for (measured 14.7x less jerk than the raw mount). SEATED it is not:
        // a belted body cannot yaw, so the entire aim — up to `Stance.aimYawLimit`, plus a posture
        // swinging the body 150 degrees — is spent in the driven bones, and the loop gain goes with
        // it. That is the animation tangle a player sees when they drive in first person. For the
        // driver the camera this rig writes is not even on screen (the carrier's is current), so
        // the loop was pure cost.
        //
        // `primed = false` so the eye re-primes on the frame the driver steps out, instead of
        // lerping over from a baseline captured before they got in.
        if (character.carrierOwnsView()) {
            primed = false;
            return;
        }

        // ... the world basis stated outright, for the same reason and with the same words as
        // TPSCameraController's: this node is `setAsTopLevel(true)` too, so its frame would
        // otherwise be frozen at the body's `_ready()`-time yaw and `controlRotation` would not be
        // a world rotation in FPS mode either. One convention, both modes (AIM_PLAN.md W3).
        setGlobalRotation(Vector3.Companion.getZERO());
        setGlobalPosition(resolveEyePosition(delta));

        // Orientation: read from the character's canonical ControlRotation. pitchMin/pitchMax are
        // the STANCE's, published there by TPSCameraController.onSetStance — so prone cannot pitch
        // to a place the neck could not follow, and the two rigs cannot disagree about the limit.
        ControlRotation cr = character.controlRotation;
        double effYaw   = cr.yaw + cr.recoilYaw;
        double effPitch = GD.clamp(cr.pitch + cr.recoilPitch, cr.pitchMin, cr.pitchMax);

        Vector3 yr = yawNode.getRotationDegrees();
        yr.setY(effYaw);
        yawNode.setRotationDegrees(yr);

        Vector3 pr = pitchNode.getRotationDegrees();
        pr.setX(effPitch);
        pitchNode.setRotationDegrees(pr);

        // Write the FPS view transform to the shared ActiveCamera when in FPS mode.
        if (character.isFpsMode && character.activeCamera != null) {
            character.activeCamera.setGlobalTransform(pivotNode.getGlobalTransform());
        }
    }

    /**
     * The filtered eye point, world space. See the class doc for why each stage is here.
     */
    private Vector3 resolveEyePosition(double delta) {
        if (fpsCameraMount == null || !fpsCameraMount.isInsideTree()) {
            primed = false;
            return character.getGlobalPosition().plus(new Vector3(0.0, fallbackEyeHeight, 0.0));
        }
        Node3D frame = (frameNode != null && frameNode.isInsideTree()) ? frameNode : character;

        Vector3 local = frame.toLocal(fpsCameraMount.getGlobalPosition());
        if (!primed) {
            baseline = local;
            smoothed = local;
            primed   = true;
            return frame.toGlobal(local);
        }

        // Rest point: the mount's own slow average, so the eye sits where this mesh's head is in
        // this stance with nothing authored per stance.
        baseline = baseline.lerp(local, weight(baselineFollow, delta));

        // Everything faster than that is bob/aim-swing/clip noise: clamp it, then admit a fraction.
        Vector3 dev = local.minus(baseline);
        double len = dev.length();
        if (len > maxBoneOffset && len > 1e-6) dev = dev.times((float) (maxBoneOffset / len));

        Vector3 target = baseline.plus(dev.times((float) boneMotionScale));
        smoothed = smoothed.lerp(target, weight(positionSmoothing, delta));
        return frame.toGlobal(smoothed);
    }

    /**
     * Frame-rate-independent single-pole weight, {@code 1 - e^(-rate*dt)}. The bare
     * {@code rate * delta} form used elsewhere in this package is fine for a cosmetic decay but
     * goes unstable past {@code rate * delta = 1}, and an eye point is not a place to find that
     * out at a frame spike.
     */
    private static float weight(double rate, double delta) {
        if (rate <= 0.0) return 1.0f;
        return (float) (1.0 - Math.exp(-rate * delta));
    }

    public double getBoneMotionScale() { return boneMotionScale; }
    public void setBoneMotionScale(double v) { this.boneMotionScale = v; }

    public double getMaxBoneOffset() { return maxBoneOffset; }
    public void setMaxBoneOffset(double v) { this.maxBoneOffset = v; }

    public double getBaselineFollow() { return baselineFollow; }
    public void setBaselineFollow(double v) { this.baselineFollow = v; }

    public double getPositionSmoothing() { return positionSmoothing; }
    public void setPositionSmoothing(double v) { this.positionSmoothing = v; }

    public double getFallbackEyeHeight() { return fallbackEyeHeight; }
    public void setFallbackEyeHeight(double v) { this.fallbackEyeHeight = v; }

    public Node3D getFpsCameraMount() { return fpsCameraMount; }
    public void setFpsCameraMount(Node3D v) { this.fpsCameraMount = v; }
}

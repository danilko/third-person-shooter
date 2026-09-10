package com.openworld.character;

import godot.annotation.Export;
import godot.annotation.Script;
import godot.api.Skeleton3D;
import godot.api.SkeletonModifier3D;
import godot.core.Basis;
import godot.core.NodePath;
import godot.core.Transform3D;
import godot.core.VariantArray;
import godot.core.Vector3;
import godot.api.Node3D;

/**
 * Aims the SHOULDERS and HEAD at the aim target while leaving the spine exactly where the clip put
 * it.
 *
 * <p><b>Why this exists.</b> The stock {@code SpineAimModifier} (a {@code LookAtModifier3D} on
 * {@code spine_03}) has one strategy: rotate that bone until the chest points at the target. In
 * upright and crouch that is right and measures well. In a stance whose body must stay put — prone,
 * a vehicle seat, swimming — it is wrong in KIND: it hits the target by standing the torso up
 * (measured on AimDebugAuto: aiming up in crawl gave chest +55 deg, i.e. 55 deg above horizontal on
 * a body lying on the ground). The previous answer to that was to switch chest aiming off for those
 * stances, which is why they do not aim at all today.
 *
 * <p><b>The observation that makes this cheap.</b> {@code clavicle_l}, {@code clavicle_r} and
 * {@code neck_01} are all DIRECT CHILDREN of {@code spine_03}. So the aim rotation the spine
 * modifier would have applied at {@code spine_03} can instead be applied at those three children,
 * about each child's own origin: the arms (and therefore {@code hand_r}, {@code WeaponAttachment}
 * and the gun's {@code Muzzle}) and the head swing to the aim exactly as before, while the spine,
 * pelvis and legs do not move at all. Same delta, applied one level down.
 *
 * <p>It is a {@code SkeletonModifier3D} rather than more {@code LookAtModifier3D}s because a
 * look-at aims a bone's OWN forward axis at the target, and a clavicle's forward runs sideways
 * along the collarbone — pointing that at the target is not the wanted rotation. What is wanted is
 * "the delta the chest would have taken", so that is what this computes.
 *
 * <p>Order matters: this must sit AFTER the spine modifier in the skeleton's child list, because
 * skeleton modifiers run in tree order and this reads the pose the previous ones produced.
 *
 * <h2>It drives the SPINE too, and that is why there is one clamp and not two</h2>
 * {@code drivenBones = ["spine_03"]} — the reference bone itself — makes this do exactly what a
 * {@code LookAtModifier3D} on {@code spine_03} did: rotate that bone until its {@code +Z} points at
 * the target, with the clavicles and neck following as its children. Measured identical (upright
 * chest tracks the view 1:1 either way), and it is what {@code SpineAimModifier} is now, because
 * the limits below have to hold for BOTH stance families and a limit implemented twice is a limit
 * that disagrees with itself. The stock modifier could not carry them anyway: its
 * {@code use_angle_limitation} pair is symmetric, is measured from the bone's REST pose, and mixes
 * the two axes — a 60 deg cap took upright's chest FOLLOW from 0.94 to 0.47 while still allowing
 * the pose it was meant to stop (AIM_PLAN.md W1a).
 */
@Script(className = "ShoulderAimModifier")
public class ShoulderAimModifier extends SkeletonModifier3D {

    /** What to aim at — the same {@code AimTarget} marker the spine modifier uses. */
    @Export
    public NodePath targetNode = new NodePath();

    /**
     * The bone whose look-at delta is computed. Only the bones in {@link #drivenBones} are written
     * — which may or may not include this one: listing its own children leaves the spine where the
     * clip put it (prone, seated, swimming), listing the reference bone ITSELF is the ordinary
     * chest aim. See the class doc.
     */
    @Export
    public String referenceBone = "spine_03";

    /**
     * The bones the delta is applied to. Defaults to the two clavicles and the neck — i.e.
     * "shoulders and head". Each must be a child of {@link #referenceBone} for the spine to stay
     * still; the rotation is applied about each bone's own origin either way. {@code
     * ["spine_03"]} — the reference bone — is the chest aim, and is what {@code SpineAimModifier}
     * carries.
     */
    @Export
    public VariantArray<String> drivenBones = defaultBones();

    /**
     * How much of the aim rotation each {@link #drivenBones} entry takes, index-parallel, 0..1.
     * Empty (the default) means 1.0 for every bone, which is the ordinary case and what the chest
     * aim uses.
     *
     * <p><b>Why a prone body needs this and a standing one does not.</b> The delta is measured
     * from the REFERENCE bone's forward, and prone that bone points at the floor — so the rotation
     * handed to the clavicles is not the ~30 deg of aim elevation, it is the ~120 deg that takes
     * the chest's own forward up to the target. Applied whole to a collarbone that reads as a
     * dislocated shoulder. Standing, the reference bone already points roughly where the aim is,
     * the delta is small, and the weights have nothing to do.
     *
     * <p><b>There is no free lunch here and the numbers say so.</b> The gun rides {@code hand_r}
     * under {@code clavicle_r}, so the arm's share of the delta IS what puts the muzzle on the
     * target: every degree taken off the clavicle comes straight back as gun-off-aim, roughly 1:1
     * (measured — see the sweep in CLAUDE.md). Nothing else in the chain can absorb it, because
     * the elbow and wrist are not driven. So this is a dial between "the shoulder looks like a
     * shoulder" and "the gun points where the reticle is", and the shipped value is the measured
     * knee of that curve, not a preference. The real fix is an authored prone aim pose, where the
     * arms START where a prone shooter's arms are and the delta is small again (AIM_PLAN.md W4).
     *
     * <p>On the MODIFIER rather than on {@code Stance}: this is a fact about how THIS skeleton
     * distributes an aim, so it belongs with the mesh, and every stance that uses the shoulder aim
     * uses the same one. Move it to {@code Stance} — beside the limits — the day two stances
     * measurably want different splits.
     */
    @Export
    public VariantArray<Float> drivenWeights = new VariantArray<>(Float.class);

    public VariantArray<Float> getDrivenWeights() { return drivenWeights; }
    public void setDrivenWeights(VariantArray<Float> v) { this.drivenWeights = v; }

    /** {@link #drivenWeights} for bone {@code i}, defaulting to 1.0 when unset or out of range. */
    private float weightFor(int i) {
        if (drivenWeights == null || i >= drivenWeights.size()) return 1.0f;
        Float w = drivenWeights.get(i);
        if (w == null) return 1.0f;
        return (float) Math.max(0.0, Math.min(1.0, w));
    }

    private static VariantArray<String> defaultBones() {
        VariantArray<String> a = new VariantArray<>(String.class);
        a.add("clavicle_l");
        a.add("clavicle_r");
        a.add("neck_01");
        return a;
    }

    /**
     * Symmetric YAW cap in degrees, driven per stance from {@link
     * com.openworld.movement.character.Stance#aimYawLimit}. 0 or less means uncapped.
     *
     * <p>Clamped here rather than through a {@code LookAtModifier3D}'s
     * {@code use_angle_limitation}, because that limits the primary AND secondary rotations from
     * one pair of numbers and measurably ate the elevation (a 60 cap took upright's chest FOLLOW
     * from 0.94 to 0.47). Doing it explicitly keeps the two axes independent: the yaw is capped,
     * the elevation is left to the camera's own range.
     */
    @Export
    public float yawLimitDegrees = 80.0f;

    public float getYawLimitDegrees() { return yawLimitDegrees; }
    public void setYawLimitDegrees(float v) { this.yawLimitDegrees = v; }

    /**
     * How far above the horizon the aim may take these bones, degrees, driven per stance from
     * {@link com.openworld.movement.character.Stance#aimPitchMax}.
     *
     * <p><b>Asymmetric, and the asymmetry is the measurement.</b> A trunk bends FORWARD far more
     * easily than backward: swept on this rig, the chest tracks the view 1:1 upward (view +80 gave
     * chest +80.0 — a body arched 80 deg back) while downward it saturates at roughly view + 8
     * (view -80 gave chest -72.4, which is just bending over to look at the ground). So the cap
     * that matters is the UP one, and a single symmetric number would either allow the arch or
     * forbid ordinary looking-down.
     *
     * <p>This is the BONE limit, not the view limit. The camera keeps a wider range
     * ({@code Stance.viewPitchMax}) because a player must be able to look at a rooftop, and in
     * first person the neck is not what stops them. Past this the bones simply HOLD at the limit
     * and start tracking again when the target comes back down — which is the whole point: the
     * body stops at a pose a body can hold. Aiming is unaffected: the bullet is traced from the
     * muzzle to the point the CAMERA ray found ({@code FirearmItem}'s two-stage resolution), so
     * the shot still converges on the crosshair while the gun is visibly held a little lower.
     */
    @Export
    public float pitchLimitMax = 45.0f;

    /** How far BELOW the horizon the aim may take these bones, as a NEGATIVE number. See {@link #pitchLimitMax}. */
    @Export
    public float pitchLimitMin = -65.0f;

    public float getPitchLimitMax() { return pitchLimitMax; }
    public void setPitchLimitMax(float v) { this.pitchLimitMax = v; }

    public float getPitchLimitMin() { return pitchLimitMin; }
    public void setPitchLimitMin(float v) { this.pitchLimitMin = v; }

    /**
     * The node whose forward is "straight ahead" for {@link #yawLimitDegrees} — {@code MeshRoot},
     * the node {@code MovementController} yaws.
     *
     * <p>It cannot be derived from the reference bone. A prone chest points at the FLOOR, so its
     * horizontal projection is a near-zero vector whose direction is numerical noise, and clamping
     * against it put the crawl gun 21.2 deg off the aim on a pure elevation swing. The skeleton
     * node's own basis is no substitute either — it carries the glTF import's orientation (42.8
     * deg). Left empty, the yaw clamp is SKIPPED rather than applied against a bad reference.
     */
    @Export
    public NodePath bodyForwardNode = new NodePath();

    public NodePath getBodyForwardNode() { return bodyForwardNode; }
    public void setBodyForwardNode(NodePath v) { this.bodyForwardNode = v; }

    /** Below this the aim point is effectively on the bone and the direction is meaningless. */
    private static final double MIN_AIM_DISTANCE = 0.05;

    public NodePath getTargetNode() { return targetNode; }
    public void setTargetNode(NodePath v) { this.targetNode = v; }

    public String getReferenceBone() { return referenceBone; }
    public void setReferenceBone(String v) { this.referenceBone = v; }

    public VariantArray<String> getDrivenBones() { return drivenBones; }
    public void setDrivenBones(VariantArray<String> v) { this.drivenBones = v; }

    @Override
    public void _processModification() {
        Skeleton3D skel = getSkeleton();
        if (skel == null || targetNode == null || targetNode.isEmpty()) return;
        if (!(getNodeOrNull(targetNode) instanceof Node3D target)) return;

        int refIdx = skel.findBone(referenceBone);
        if (refIdx < 0) return;

        // Where the chest is and where it currently points. +Z is this rig's chest forward --
        // measured, not assumed: in the rest pose spine_03's +Z lands on the mesh forward and its
        // +X on the clavicle_l - clavicle_r axis. (Note that is the OPPOSITE sign to a camera.)
        Basis skelBasis = skel.getGlobalTransform().getBasis();
        Transform3D refGlobal = skel.getBoneGlobalPose(refIdx);
        Vector3 refOrigin = skel.getGlobalTransform().times(refGlobal.getOrigin());
        Vector3 from = skel.getGlobalTransform().getBasis().times(refGlobal.getBasis().getZ());
        Vector3 to = target.getGlobalPosition().minus(refOrigin);
        if (to.length() < MIN_AIM_DISTANCE || from.lengthSquared() < 1e-8) return;

        // Cap the YAW before deriving the delta, and cap it about the BODY'S UP AXIS, not the
        // reference bone's. That distinction is the whole correctness of this: in crawl spine_03 is
        // pitched ~88 degrees face-down, so its local x/z plane is very nearly the world VERTICAL
        // plane -- clamping "yaw" there clamps world PITCH. Measured, that took crawl's head FOLLOW
        // from 0.93 to 0.32 on a pure elevation swing. Rotating about the body's up instead cannot
        // touch elevation at all, by construction.
        //
        // "Straight ahead" comes from {@link #bodyForwardNode}; see its docs for why neither the
        // reference bone nor the skeleton node can supply it.
        Vector3 want = to.normalized();
        Vector3 up = new Vector3(0.0, 1.0, 0.0);
        Node3D frame = (bodyForwardNode == null || bodyForwardNode.isEmpty())
                ? null
                : (getNodeOrNull(bodyForwardNode) instanceof Node3D n ? n : null);
        Vector3 bodyFlat = Vector3.Companion.getZERO();
        if (frame != null) {
            bodyFlat = planar(frame.getGlobalTransform().getBasis().getZ().times(-1.0f), up);
            if (bodyFlat.lengthSquared() > 1e-6) bodyFlat = bodyFlat.normalized();
            else bodyFlat = Vector3.Companion.getZERO();
        }
        if (bodyFlat.lengthSquared() > 0.5 && yawLimitDegrees > 0.0f && yawLimitDegrees < 180.0f) {
            Vector3 flatW = planar(want, up);
            if (flatW.lengthSquared() > 1e-6) {
                double off = signedAngle(bodyFlat, flatW, up);
                double lim = Math.toRadians(yawLimitDegrees);
                if (Math.abs(off) > lim) {
                    // Turn the target back toward straight-ahead by the excess, about WORLD UP.
                    // Elevation survives untouched because the axis IS the up axis.
                    want = new Basis(up, -(off - Math.copySign(lim, off))).times(want).normalized();
                }
            }
        }

        // ELEVATION, clamped after the yaw and about the same world horizon, so the two axes stay
        // independent (which is exactly what a LookAtModifier3D's symmetric limit pair cannot do).
        // Rebuilt from a horizontal direction and an angle rather than rotated, because at the
        // extreme the horizontal part of `want` is what has gone: a target STRAIGHT OVERHEAD has no
        // bearing at all, so `atan2` on it is noise and the bones twist to a heading that means
        // nothing -- the "strange direction" this pass was opened for. The body's own forward is
        // the answer there: it is the heading the character already has, so the aim rises straight
        // up in front of it and comes back down onto the target the moment the target has a bearing
        // again.
        if (pitchLimitMax > pitchLimitMin) {
            double elev = Math.toDegrees(Math.asin(Math.max(-1.0, Math.min(1.0, want.getY()))));
            double capped = Math.max(pitchLimitMin, Math.min(pitchLimitMax, elev));
            if (capped != elev) {
                Vector3 flat = planar(want, up);
                if (flat.lengthSquared() > 1e-6) flat = flat.normalized();
                else flat = bodyFlat;                       // straight up/down: no bearing to keep
                if (flat.lengthSquared() > 0.5) {
                    double r = Math.toRadians(capped);
                    want = flat.times((float) Math.cos(r))
                               .plus(up.times((float) Math.sin(r)))
                               .normalized();
                }
            }
        }

        // The delta the chest WOULD have taken, kept as AXIS + ANGLE so a bone can take a
        // fraction of it (see drivenWeights). Scaling the angle about the same axis is the right
        // partial rotation -- it is the slerp from identity, on the shortest arc.
        Vector3 axis = rotationAxis(from.normalized(), want);
        if (axis == null) return;
        double angle = angleBetween(from.normalized(), want);

        // Apply it to each driven bone about that bone's OWN origin, so the shoulders and head
        // swing while the spine, pelvis and legs stay exactly as the clip authored them.
        Basis toLocal = skelBasis.inverse();
        Basis full = new Basis(axis, angle);
        for (int i = 0; i < drivenBones.size(); i++) {
            String name = drivenBones.get(i);
            if (name == null || name.isEmpty()) continue;
            int idx = skel.findBone(name);
            if (idx < 0) continue;
            float w = weightFor(i);
            if (w <= 0.0f) continue;                       // this bone keeps the clip's pose
            Basis delta = (w >= 0.999f) ? full : new Basis(axis, angle * w);
            Transform3D g = skel.getBoneGlobalPose(idx);
            // world-space rotate, brought back into skeleton space; the origin is untouched.
            Basis rotated = toLocal.times(delta).times(skelBasis).times(g.getBasis());
            skel.setBoneGlobalPose(idx, new Transform3D(rotated, g.getOrigin()));
        }
    }

    /** {@code v} with its component along {@code up} removed. */
    private static Vector3 planar(Vector3 v, Vector3 up) {
        return v.minus(up.times((float) v.dot(up)));
    }

    /** Signed angle from {@code a} to {@code b} about {@code axis}, radians, in [-pi, pi]. */
    private static double signedAngle(Vector3 a, Vector3 b, Vector3 axis) {
        Vector3 na = a.normalized(), nb = b.normalized();
        return Math.atan2(na.cross(nb).dot(axis), na.dot(nb));
    }

    /**
     * Axis of the shortest-arc rotation taking unit vector {@code a} onto unit vector {@code b},
     * or null when there is nothing to do. Split from the angle so a bone can take a FRACTION of
     * the rotation about the same axis ({@link #drivenWeights}).
     */
    private static Vector3 rotationAxis(Vector3 a, Vector3 b) {
        if (a.dot(b) > 0.999999) return null;
        Vector3 axis = a.cross(b);
        if (axis.lengthSquared() < 1e-12) {
            // Exactly opposed: any perpendicular axis is a valid 180-degree turn. Pick a stable one.
            axis = a.cross(new Vector3(0.0, 1.0, 0.0));
            if (axis.lengthSquared() < 1e-12) axis = a.cross(new Vector3(1.0, 0.0, 0.0));
        }
        return axis.normalized();
    }

    /** Angle of that same shortest-arc rotation, radians. */
    private static double angleBetween(Vector3 a, Vector3 b) {
        return Math.acos(Math.max(-1.0, Math.min(1.0, a.dot(b))));
    }
}

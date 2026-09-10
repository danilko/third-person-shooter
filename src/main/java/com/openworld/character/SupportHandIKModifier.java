package com.openworld.character;

import com.openworld.weapon.WeaponController;
import com.openworld.weapon.WeaponItem;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.Skeleton3D;
import godot.api.SkeletonModifier3D;
import godot.core.Basis;
import godot.core.NodePath;
import godot.core.Transform3D;
import godot.core.Vector3;

/**
 * Two-bone IK putting the SUPPORT HAND on the weapon it is holding.
 *
 * <h2>Why this exists: it is what stops grip archetypes multiplying</h2>
 *
 * A grip archetype ({@code WeaponItem.weaponPoseIndex}, CLAUDE.md W12) animates the CLASS of hold —
 * one-handed, two-handed, shoulder-carried. What it cannot express is that an SMG's foregrip is 12 cm
 * from the trigger and a long rifle's is 30, because that is a fact about the WEAPON. Without IK the
 * only way to place the off hand correctly is one clip per weapon, which is exactly the explosion
 * the archetype split exists to avoid. With it, one {@code rifle} pose serves an SMG, a shotgun, a
 * carbine and a foregrip variant, and a new weapon costs one marker and no animation.
 *
 * <h2>Self-managed, deliberately</h2>
 *
 * It asks the character's {@link WeaponController} for the weapon actually in hand and looks for a
 * child marker named {@link #supportPointName}. No equip path pushes anything here, so there is no
 * wiring to forget on one of the four {@code onWeaponEquip} call sites and nothing that can go stale
 * when a weapon is switched, dropped or replicated. A weapon that declares no support point leaves
 * the arm exactly as the clip authored it — which is the correct answer for a pistol.
 *
 * <h2>The solve</h2>
 *
 * Law of cosines on {@code upperarm_l -> lowerarm_l -> hand_l}, in SKELETON space (the space
 * {@code getBoneGlobalPose} speaks), with the bend plane taken from the CURRENT pose so the
 * animator's elbow direction survives — a pole vector would override that and is not wanted here,
 * where the clip already knows where the elbow belongs. Reach is clamped to the arm's own length, so
 * a target further away than the arm straightens the arm rather than dislocating it.
 *
 * <p>Ordering matters: this must sit AFTER {@link ShoulderAimModifier} among the skeleton's
 * children, because the aim swings the whole shoulder and the hand has to be placed on the weapon
 * as the weapon ends up, not as the clip left it.
 */
@Script(className = "SupportHandIKModifier")
public class SupportHandIKModifier extends SkeletonModifier3D {

    /** Shoulder end of the chain. */
    @Export public String upperBone = "upperarm_l";
    /** Elbow. */
    @Export public String lowerBone = "lowerarm_l";
    /** The bone actually placed on the target. */
    @Export public String endBone = "hand_l";

    /**
     * Child marker on the HELD WEAPON that the support hand should sit on. A weapon with no such
     * child is left alone, which is how a pistol (or any one-handed weapon) opts out — no flag, no
     * per-weapon table.
     */
    @Export public String supportPointName = "SupportPoint";

    /**
     * How much of the solve to apply, 0..1. Exists for blending the hand OFF the weapon — a reload
     * takes the support hand to the magazine, and a throw takes it away entirely — rather than for
     * dialling the solve down permanently.
     */
    @Export public float weight = 1.0f;

    /** Path to the character owning this skeleton; empty resolves the scene owner. */
    @Export public NodePath characterPath = new NodePath();

    private WeaponController weaponController;
    private boolean controllerResolved = false;

    public String getUpperBone() { return upperBone; }
    public void setUpperBone(String v) { this.upperBone = v; }
    public String getLowerBone() { return lowerBone; }
    public void setLowerBone(String v) { this.lowerBone = v; }
    public String getEndBone() { return endBone; }
    public void setEndBone(String v) { this.endBone = v; }
    public String getSupportPointName() { return supportPointName; }
    public void setSupportPointName(String v) { this.supportPointName = v; }
    public float getWeight() { return weight; }
    public void setWeight(float v) { this.weight = v; }
    public NodePath getCharacterPath() { return characterPath; }
    public void setCharacterPath(NodePath v) { this.characterPath = v; }

    /** The support marker on the weapon in hand, or null when there is nothing to reach for. */
    private Node3D resolveTarget() {
        if (!controllerResolved) {
            controllerResolved = true;
            Node explicit = (characterPath == null || characterPath.isEmpty())
                    ? null : getNodeOrNull(characterPath);
            if (explicit != null
                    && explicit.getNodeOrNull("WeaponController") instanceof WeaponController wc) {
                weaponController = wc;
            } else {
                // WALK UP, rather than trusting getOwner(). This node lives inside the CharacterVisuals
                // sub-scene, so its owner is that sub-scene's root -- and `WeaponController` is a child
                // of the CHARACTER, one level further out. getOwner() therefore resolves to a node that
                // has no such child, resolveTarget returns null forever, and the modifier is silently
                // inert with a correct-looking scene (measured: the arm moved 0.006 m).
                for (Node n = getParent(); n != null; n = n.getParent()) {
                    if (n.getNodeOrNull("WeaponController") instanceof WeaponController wc) {
                        weaponController = wc;
                        break;
                    }
                }
            }
        }
        if (weaponController == null) return null;
        WeaponItem held = weaponController.getCurrentWeaponItem();
        if (held == null || supportPointName == null || supportPointName.isEmpty()) return null;
        Node n = held.getNodeOrNull(new NodePath(supportPointName));
        return (n instanceof Node3D m) ? m : null;
    }

    @Override
    public void _processModification() {
        if (weight <= 0.0f) return;
        Skeleton3D skel = getSkeleton();
        if (skel == null) return;
        Node3D target = resolveTarget();
        if (target == null) return;                      // no support point: leave the clip alone

        int iu = skel.findBone(upperBone);
        int il = skel.findBone(lowerBone);
        int ie = skel.findBone(endBone);
        if (iu < 0 || il < 0 || ie < 0) return;

        Transform3D gu = skel.getBoneGlobalPose(iu);
        Transform3D gl = skel.getBoneGlobalPose(il);
        Transform3D ge = skel.getBoneGlobalPose(ie);
        Vector3 u = gu.getOrigin();
        Vector3 l = gl.getOrigin();
        Vector3 e = ge.getOrigin();

        // The target, brought from world into the space the bone poses are expressed in.
        Vector3 t = skel.getGlobalTransform().affineInverse().times(target.getGlobalPosition());

        double a = l.minus(u).length();          // upper arm
        double b = e.minus(l).length();          // forearm
        Vector3 toTarget = t.minus(u);
        double reach = toTarget.length();
        if (a < 1e-5 || b < 1e-5 || reach < 1e-5) return;

        // Clamped to what the arm can actually do: past a+b it straightens instead of stretching,
        // and inside |a-b| it folds instead of inverting.
        double c = Math.max(Math.abs(a - b) + 1e-4, Math.min(a + b - 1e-4, reach));
        Vector3 dir = toTarget.normalized();

        // Bend plane from the CURRENT pose, so the elbow keeps the direction the clip gave it.
        // Order is (end-upper) x (lower-upper): rotating `dir` by +alpha about that axis puts the
        // elbow back on the side it is already on, which the opposite order does not.
        Vector3 axis = e.minus(u).cross(l.minus(u));
        if (axis.lengthSquared() < 1e-10) {
            axis = dir.cross(gu.getBasis().getColumn(2));   // straight arm: any stable perpendicular
            if (axis.lengthSquared() < 1e-10) return;
        }
        axis = axis.normalized();

        double cosAlpha = (a * a + c * c - b * b) / (2.0 * a * c);
        double alpha = Math.acos(Math.max(-1.0, Math.min(1.0, cosAlpha)));

        Vector3 elbowSolved = u.plus(new Basis(axis, alpha).times(dir).times((float) a));
        Vector3 handSolved  = u.plus(dir.times((float) c));

        // Upper: swing the shoulder so the elbow lands where the solve wants it. Children (the
        // forearm and hand) follow, which is why the elbow is written first and read back below.
        Basis ru = arc(l.minus(u), elbowSolved.minus(u), weight);
        if (ru == null) return;
        skel.setBoneGlobalPose(iu, new Transform3D(ru.times(gu.getBasis()), u));

        // Lower: close the elbow onto the hand target, measured from where the forearm now points.
        Vector3 elbowActual = u.plus(ru.times(l.minus(u)));
        Basis rl = arc(ru.times(e.minus(l)), handSolved.minus(elbowSolved), weight);
        if (rl == null) return;
        skel.setBoneGlobalPose(il,
                new Transform3D(rl.times(ru).times(gl.getBasis()), elbowActual));
    }

    /**
     * Shortest-arc rotation from {@code from} onto {@code to}, scaled by {@code w}.
     *
     * <p>Scaling the ANGLE about the shared axis, not lerping two bases: that is the slerp from
     * identity along the shortest arc, and it is the same partial-rotation rule
     * {@link ShoulderAimModifier} uses for its driven-bone weights. Null when there is nothing to
     * do or the vectors are degenerate.
     */
    private static Basis arc(Vector3 from, Vector3 to, float w) {
        if (from.lengthSquared() < 1e-12 || to.lengthSquared() < 1e-12) return null;
        Vector3 f = from.normalized();
        Vector3 g = to.normalized();
        Vector3 axis = f.cross(g);
        double dot = Math.max(-1.0, Math.min(1.0, f.dot(g)));
        if (axis.lengthSquared() < 1e-12) {
            // Parallel: nothing to do. Anti-parallel is not reachable here — both vectors are
            // derived from the same arm, so they can never be a full turn apart.
            return new Basis();
        }
        return new Basis(axis.normalized(), Math.acos(dot) * w);
    }

    @Register
    @Override
    public void _exitTree() {
        weaponController = null;
        controllerResolved = false;
    }
}

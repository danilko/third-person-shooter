package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Marker3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.core.Vector3;
import godot.global.GD;
import com.openworld.util.WeightedPick;

import java.util.ArrayList;
import java.util.List;

/**
 * One directional <b>lane</b> (edge) of the traffic lane-graph (PLAN.md I3 / I3b).
 *
 * <p>Authored as this node's ordered {@link Marker3D} children (a centerline polyline). A
 * {@link com.openworld.ai.vehicle.VehicleAIController} follows it by pure pursuit, but over a
 * <b>Catmull-Rom-smoothed</b>, <b>lane-offset</b> path (not the raw vertices), sampled by arc length —
 * this rounds corners (no vertex snap) and keeps the car in its lane instead of on the centerline.
 *
 * <p><b>Connectivity</b> is normally <i>derived</i> from geometry by {@link LaneGraph} (lanes whose
 * endpoints meet form a junction; a car picks a random outgoing lane there). {@link #nextRoutes} is an
 * optional explicit override. At the end of a non-{@link #loop} lane the car follows {@link #endBehavior}:
 * <ul>
 *   <li>{@link #END_CHAIN} — continue onto a connected lane (graph / {@code nextRoutes}); if none, the
 *       car is despawned (dead-end).
 *   <li>{@link #END_UTURN} — reverse and drive back (the {@link #returnRoute}, else the graph reverse) —
 *       keeps cars circulating with no despawn pop.
 *   <li>{@link #END_DESPAWN} — {@code queue_free} immediately; the zone respawns one at another entry
 *       (GTA-style disposable traffic — avoids a pile-up at a true map edge).
 * </ul>
 */
@Script(className = "VehicleRoute")
public class VehicleRoute extends Node3D implements Lane {

    /** {@link #endBehavior} values — exported as String (a raw enum type breaks the registration scanner). */
    public static final String END_CHAIN   = "CHAIN";
    public static final String END_UTURN   = "UTURN";
    public static final String END_DESPAWN = "DESPAWN";

    /** True = a closed ring (cars circulate forever). False = a one-way lane ending in {@link #endBehavior}. */
    @Export public boolean loop = true;

    /** Optional comma-separated explicit successor lane names — overrides the geometry-derived
     *  {@link LaneGraph} connectivity when set. */
    @Export public String nextRoutes = "";

    /** Optional comma-separated weights parallel to {@link #nextRoutes} (baked straight-biased,
     *  e.g. "0.6,0.2,0.2"). Empty/malformed = uniform pick. */
    @Export public String nextWeights = "";

    /** Turn movement this route makes through a junction — "L"/"S"/"R" on a generated turn
     *  connector, "" on a plain lane. Read by the junction right-of-way logic (Phase 2). */
    @Export public String turn = "";

    /** Compass arm ("N"/"E"/"S"/"W") a car on this connector arrives from — the junction
     *  conflict-table key (Phase 2). "" on a plain lane. */
    @Export public String approach = "";

    /** Right-side lane offset (m) applied to the followed path — keeps cars in their lane rather than
     *  riding the marker centerline. Author opposing one-way lanes, or one centerline + ± offset. */
    @Export public float laneOffset = 0f;

    /** Lane width (m) — reserved for multi-lane / overtaking; currently informational. */
    @Export public float laneWidth = 3.5f;

    /** End-of-lane behaviour: {@link #END_CHAIN} / {@link #END_UTURN} / {@link #END_DESPAWN}. */
    @Export public String endBehavior = END_CHAIN;

    /** Optional explicit return lane name for {@link #END_UTURN} (else derived from {@link LaneGraph}). */
    @Export public String returnRoute = "";

    // ── Lane interface getters (thin wrappers over the public fields above, needed because an
    //    interface can't expose a field directly — no behavior change, same values) ───────────
    @Override public String getTurn() { return turn; }

    /** Setter half of the exported {@code turn} property. */
    public void setTurn(String value) {
        this.turn = value;
    }
    @Override public String getApproach() { return approach; }

    /** Setter half of the exported {@code approach} property. */
    public void setApproach(String value) {
        this.approach = value;
    }
    @Override public String getEndBehavior() { return endBehavior; }

    /** Setter half of the exported {@code endBehavior} property. */
    public void setEndBehavior(String value) {
        this.endBehavior = value;
    }
    @Override public String getReturnRoute() { return returnRoute; }

    /** Setter half of the exported {@code returnRoute} property. */
    public void setReturnRoute(String value) {
        this.returnRoute = value;
    }

    // Baked (Catmull-Rom smoothed + lane-offset) path cache — rebuilt only if the marker count changes.
    private List<Vector3> baked;
    private double[] cum;
    private double totalLen;
    private int bakedFromCount = -1;

    private static final int SUBDIVS = 8;   // spline samples per centerline segment

    public VehicleRoute() { super(); }

    // Registered with the ZoneManager route registry (register-with-AutoLoad idiom, like
    // Character ↔ SpatialEntityGrid) so every lane lookup is a map read, not a scene-tree walk.
    @Register
    @Override
    public void _ready() {
        ZoneManager mgr = ZoneManager.get();
        if (mgr != null) mgr.registerRoute(this);
    }

    @Register
    @Override
    public void _exitTree() {
        cachedEntry = null;   // global positions are per scene-instance
        ZoneManager mgr = ZoneManager.get();
        if (mgr != null) mgr.unregisterRoute(this);
    }

    /** Raw centerline marker positions in scene order (no smoothing / offset). */
    public List<Vector3> waypoints() {
        List<Vector3> pts = new ArrayList<>();
        for (Node child : getChildren()) {
            if (child instanceof Marker3D m) pts.add(m.getGlobalPosition());
        }
        return pts;
    }

    /** Number of centerline markers. */
    public int size() {
        int n = 0;
        for (Node child : getChildren()) if (child instanceof Marker3D) n++;
        return n;
    }

    /** First / last centerline point (no offset) — the lane's endpoints, used by {@link LaneGraph} to
     *  cluster junctions. Null when the lane has no markers. */
    public Vector3 startPoint() { List<Vector3> p = waypoints(); return p.isEmpty() ? null : p.get(0); }
    public Vector3 endPoint()   { List<Vector3> p = waypoints(); return p.isEmpty() ? null : p.get(p.size() - 1); }

    private Vector3 cachedEntry;

    /** {@link #startPoint()} cached for the lifetime of this tree entry — the spawn-time prefix
     *  query ({@code ZoneManager.findRoute}) distance-filters every registered lane, so it must
     *  not re-walk marker children (JVM-bridge calls) per candidate. Lanes are static content. */
    @Override
    public Vector3 entryPoint() {
        if (cachedEntry == null) cachedEntry = startPoint();
        return cachedEntry;
    }

    /** Unit XZ travel direction leaving the first / arriving at the last marker — {@code {x, z}},
     *  or null when under 2 markers. Drives the straightness-biased {@link LaneGraph} fallback. */
    public double[] startTangentXZ() { return tangentXZ(true); }
    public double[] endTangentXZ()   { return tangentXZ(false); }

    private double[] tangentXZ(boolean atStart) {
        List<Vector3> p = waypoints();
        if (p.size() < 2) return null;
        Vector3 a = atStart ? p.get(0) : p.get(p.size() - 2);
        Vector3 b = atStart ? p.get(1) : p.get(p.size() - 1);
        double dx = b.getX() - a.getX(), dz = b.getZ() - a.getZ();
        double len = Math.sqrt(dx * dx + dz * dz);
        return len < 1e-9 ? null : new double[]{dx / len, dz / len};
    }

    public boolean isLoop() { return loop; }

    /** Setter half of the exported {@code loop} property. */
    public void setLoop(boolean value) {
        this.loop = value;
    }

    // ── Smoothed, lane-offset arc-length sampler ─────────────────────────────────

    private void ensureBaked() {
        List<Vector3> pts = waypoints();
        if (baked != null && bakedFromCount == pts.size()) return;
        bakedFromCount = pts.size();
        baked = new ArrayList<>();
        int n = pts.size();
        if (n == 0) { baked.add(new Vector3()); cum = new double[]{0}; totalLen = 0; return; }
        if (n == 1) { baked.add(pts.get(0)); cum = new double[]{0}; totalLen = 0; return; }

        int segCount = loop ? n : (n - 1);
        List<Vector3> raw = new ArrayList<>();
        for (int i = 0; i < segCount; i++) {
            Vector3 p0 = pts.get(loop ? (i - 1 + n) % n : Math.max(0, i - 1));
            Vector3 p1 = pts.get(i);
            Vector3 p2 = pts.get((i + 1) % n);
            Vector3 p3 = pts.get(loop ? (i + 2) % n : Math.min(n - 1, i + 2));
            for (int k = 0; k < SUBDIVS; k++) raw.add(catmull(p0, p1, p2, p3, (double) k / SUBDIVS));
        }
        if (!loop) raw.add(pts.get(n - 1));   // close the open path on its final marker

        // Lane offset along the local right-normal of the smoothed path.
        int m = raw.size();
        for (int i = 0; i < m; i++) {
            Vector3 pt = raw.get(i);
            if (laneOffset != 0f) {
                Vector3 a = (!loop && i == 0)     ? raw.get(0)     : raw.get((i - 1 + m) % m);
                Vector3 b = (!loop && i == m - 1) ? raw.get(m - 1) : raw.get((i + 1) % m);
                double tx = b.getX() - a.getX(), tz = b.getZ() - a.getZ();
                double tl = Math.sqrt(tx * tx + tz * tz);
                if (tl > 1e-6) {
                    double rx = -tz / tl, rz = tx / tl;   // right normal (forward -Z ⇒ right +X)
                    pt = new Vector3(pt.getX() + rx * laneOffset, pt.getY(), pt.getZ() + rz * laneOffset);
                }
            }
            baked.add(pt);
        }

        cum = new double[baked.size()];
        totalLen = 0;
        for (int i = 1; i < baked.size(); i++) { totalLen += horiz(baked.get(i - 1), baked.get(i)); cum[i] = totalLen; }
        if (loop) totalLen += horiz(baked.get(baked.size() - 1), baked.get(0));   // closing segment
    }

    /** Total arc length of the smoothed, offset path (includes the closing segment for a loop). */
    public double total() { ensureBaked(); return totalLen; }

    /** Point at arc length {@code s} along the smoothed, offset path (wraps for a loop, clamps otherwise). */
    public Vector3 pointAtLength(double s) {
        ensureBaked();
        int m = baked.size();
        if (m == 1 || totalLen <= 1e-6) return baked.get(0);
        if (loop) { s %= totalLen; if (s < 0) s += totalLen; }
        else s = Math.max(0, Math.min(totalLen, s));
        for (int i = 1; i < m; i++) {
            if (s <= cum[i]) {
                double segLen = cum[i] - cum[i - 1];
                return lerp(baked.get(i - 1), baked.get(i), segLen <= 1e-9 ? 0 : (s - cum[i - 1]) / segLen);
            }
        }
        if (loop) {
            double segLen = totalLen - cum[m - 1];
            return lerp(baked.get(m - 1), baked.get(0), segLen <= 1e-9 ? 0 : (s - cum[m - 1]) / segLen);
        }
        return baked.get(m - 1);
    }

    /**
     * Arc length of the baked point nearest {@code pos}, searched only within ±{@code window} of
     * {@code aroundS} (local — this is what avoids the global-reprojection corner snap; pass
     * {@code aroundS < 0} for a full-path search on first acquire).
     */
    public double lengthAtNearest(Vector3 pos, double aroundS, double window) {
        ensureBaked();
        int m = baked.size();
        if (m == 1) return 0;
        double best = aroundS < 0 ? 0 : aroundS, bestD = Double.MAX_VALUE;
        for (int i = 0; i < m; i++) {
            if (aroundS >= 0) {
                double d = Math.abs(cum[i] - aroundS);
                if (loop) d = Math.min(d, totalLen - d);
                if (d > window) continue;
            }
            double dsq = horizSq(pos, baked.get(i));
            if (dsq < bestD) { bestD = dsq; best = cum[i]; }
        }
        return best;
    }

    // ── Explicit-successor override (geometry-derived connectivity lives in LaneGraph) ──

    /**
     * A weighted-random explicit successor from {@link #nextRoutes} / {@link #nextWeights}, or
     * null when none is set/resolves. Weights stay parallel through resolution: an unresolved
     * name (its district not streamed in yet) drops its weight with it, so the remaining
     * candidates keep their relative bias.
     */
    public VehicleRoute pickNextRoute() {
        if (loop || nextRoutes == null || nextRoutes.isBlank()) return null;
        float[] baked = WeightedPick.parseWeights(nextWeights);
        String[] names = nextRoutes.split(",");
        List<VehicleRoute> candidates = new ArrayList<>();
        List<Float> kept = new ArrayList<>();
        for (int i = 0; i < names.length; i++) {
            String nm = names[i].trim();
            if (nm.isEmpty()) continue;
            VehicleRoute r = resolveRoute(nm);
            if (r == null || r == this) continue;
            candidates.add(r);
            kept.add(baked != null && baked.length == names.length ? baked[i] : 1f);
        }
        if (candidates.isEmpty()) return null;
        float[] w = new float[kept.size()];
        for (int i = 0; i < w.length; i++) w[i] = kept.get(i);
        return candidates.get(WeightedPick.pick(candidates.size(), w, GD.randf()));
    }

    /**
     * Resolve a sibling lane by node name (used by {@link #nextRoutes} / {@link #returnRoute}) — a
     * {@code ZoneManager} registry read; falls back to a scene-tree scan only when the AutoLoad
     * is absent (test scenes).
     */
    public VehicleRoute resolveRoute(String name) {
        if (name == null || name.isBlank()) return null;
        String nm = name.trim();
        ZoneManager mgr = ZoneManager.get();
        if (mgr != null) {
            // The registry is Lane-typed (a PathLaneRoute can register too), but VehicleRoute's
            // own explicit nextRoutes/returnRoute chaining only ever makes sense VehicleRoute ->
            // VehicleRoute (a PathLaneRoute has no such field to chain into) -- cross-type
            // connectivity is LaneGraph's job, not this method's.
            Lane found = mgr.routeByName(nm);
            return found instanceof VehicleRoute vr ? vr : null;
        }
        Node scene = getTree() != null ? getTree().getCurrentScene() : null;
        return scene != null ? findRoute(scene, nm) : null;
    }

    private static VehicleRoute findRoute(Node node, String name) {
        if (node instanceof VehicleRoute r && node.getName().toString().equals(name)) return r;
        for (Node child : node.getChildren()) {
            VehicleRoute found = findRoute(child, name);
            if (found != null) return found;
        }
        return null;
    }

    // ── Geometry helpers ─────────────────────────────────────────────────────────

    private static Vector3 catmull(Vector3 p0, Vector3 p1, Vector3 p2, Vector3 p3, double t) {
        double t2 = t * t, t3 = t2 * t;
        double x = 0.5 * (2 * p1.getX() + (-p0.getX() + p2.getX()) * t
                + (2 * p0.getX() - 5 * p1.getX() + 4 * p2.getX() - p3.getX()) * t2
                + (-p0.getX() + 3 * p1.getX() - 3 * p2.getX() + p3.getX()) * t3);
        double z = 0.5 * (2 * p1.getZ() + (-p0.getZ() + p2.getZ()) * t
                + (2 * p0.getZ() - 5 * p1.getZ() + 4 * p2.getZ() - p3.getZ()) * t2
                + (-p0.getZ() + 3 * p1.getZ() - 3 * p2.getZ() + p3.getZ()) * t3);
        double y = p1.getY() + (p2.getY() - p1.getY()) * t;   // linear in Y (roads are near-flat)
        return new Vector3(x, y, z);
    }

    private static Vector3 lerp(Vector3 a, Vector3 b, double t) {
        return new Vector3(a.getX() + (b.getX() - a.getX()) * t,
                           a.getY() + (b.getY() - a.getY()) * t,
                           a.getZ() + (b.getZ() - a.getZ()) * t);
    }

    private static double horiz(Vector3 a, Vector3 b) { return Math.sqrt(horizSq(a, b)); }

    private static double horizSq(Vector3 a, Vector3 b) {
        double dx = a.getX() - b.getX(), dz = a.getZ() - b.getZ();
        return dx * dx + dz * dz;
    }
}

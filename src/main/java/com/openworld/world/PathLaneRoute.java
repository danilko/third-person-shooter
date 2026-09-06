package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Curve3D;
import godot.api.Node3D;
import godot.api.Path3D;
import godot.core.PackedVector3Array;
import godot.core.Vector3;
import godot.global.GD;

import com.openworld.util.WeightedPick;

import java.util.ArrayList;
import java.util.List;

/**
 * A directional lane backed by a native {@link Path3D}/{@link Curve3D} instead of the
 * {@link VehicleRoute} Marker3D-list + hand-rolled Catmull-Rom smoothing — built at bake time by
 * {@link WorldBaker} from a Blender-exported {@code .lanekit.json} sidecar (see
 * {@code assets/world_source/lib/intersection_kit.py}). Implements {@link Lane}, so it plugs into
 * the existing {@link LaneGraph} (endpoint-proximity junction derivation) and
 * {@link com.openworld.ai.vehicle.VehicleAIController} (arc-length lane following) unchanged —
 * a car already goes straight or turns depending on which successor lane it lands on at a
 * junction, via the same mechanism that already drives a {@link VehicleRoute} network; no new AI
 * decision-making.
 *
 * <p><b>Static/baked only.</b> Unlike {@code VehicleRoute} (whose marker children an editor user
 * might still be reshaping), a {@code PathLaneRoute}'s curve is fixed the moment it's built — its
 * arc-length cache is computed once in {@link #_ready()}, not rebuilt on demand.
 *
 * <p><b>Connectivity is geometry-derived by default</b> ({@link LaneGraph}), with an EXPLICIT
 * override where geometry is not enough: {@link #nextRoutes}/{@link #nextWeights}/
 * {@link #nextKinds} plus {@link #innerLane}/{@link #outerLane} are baked from the sidecar for
 * interchange pieces, because at a gore every lane end is within a few metres of every other and
 * proximity cannot tell a mainline continuing from a ramp departing. A plain lane leaves them
 * empty and stays entirely on the proximity path. It DOES
 * register with {@link ZoneManager}'s route registry (as of the road_kit_authoring district
 * integration — see {@code road_blender_godot.md} Phase 6), so a {@code PathLaneRoute} network can
 * participate in ambient/disposable-traffic zone spawn configs exactly like a {@code VehicleRoute}
 * network.
 */
@Script(className = "PathLaneRoute")
public class PathLaneRoute extends Node3D implements Lane {

    /** True = a closed ring. False = a one-way lane ending in {@link #endBehavior}. */
    @Export public boolean loop = false;

    /** Turn movement this lane makes through a junction — "L"/"S"/"R", "" on a plain lane. */
    @Export public String turn = "";

    /** Compass/arm label a car on this connector arrives from — "" on a plain lane. */
    @Export public String approach = "";

    /** Right-side lane offset (m) — reserved, mirrors {@code VehicleRoute.laneOffset}; a
     *  {@code PathLaneRoute}'s curve is already the exact per-lane centerline (baked in Blender),
     *  so this is 0 unless a future consumer wants an additional runtime offset. */
    @Export public float laneOffset = 0f;

    /** Lane width (m) — informational, from the exported sidecar's {@code lane_width}. */
    @Export public float laneWidth = 3.5f;

    // ── Explicit road-network connectivity (Phase 3) ──────────────────────────────────────────
    // Endpoint proximity ({@link LaneGraph}) is right for the overwhelming majority of joints and
    // WRONG at exactly one place: a gore, where every lane end of a mainline and a ramp sits
    // within a few metres of every other. Geometry there cannot tell a mainline continuing from a
    // ramp departing, so a car has no basis to choose and an AI has nothing to reason about. These
    // fields carry the authored answer, baked from the `.lanekit.json` sidecar.

    /** Comma-separated explicit successor lane node names — same format as
     *  {@code VehicleRoute.nextRoutes}, so {@code util.WeightedPick} parses both. Empty on a plain
     *  lane, which keeps it on the proximity path. */
    @Export public String nextRoutes = "";

    /** Comma-separated weights parallel to {@link #nextRoutes}. */
    @Export public String nextWeights = "";

    /** Comma-separated movement kinds parallel to {@link #nextRoutes} —
     *  {@code THROUGH} / {@code EXIT} / {@code ENTRY}. <b>This is what an AI reads to know an
     *  interchange is an interchange</b>: "the target took the EXIT" is not derivable from
     *  geometry at a gore. */
    @Export public String nextKinds = "";

    /** The lane immediately INBOARD of this one (toward the centreline) that a car may change
     *  into, or "". */
    @Export public String innerLane = "";

    /** The lane immediately OUTBOARD of this one (toward the road edge) that a car may change
     *  into, or "".
     *
     *  <p>In/out rather than left/right deliberately: it is measured against the driving divide,
     *  so it reads the same whichever side of the road the world drives on, and OUTBOARD is always
     *  the side an exit ramp is on. <b>Lane-change adjacency is not a nicety</b> — an auxiliary
     *  exit lane BEGINS mid-carriageway with nothing upstream to follow from, so it is reachable
     *  only by changing lanes. Without this the ramp is in the graph but unreachable. */
    @Export public String outerLane = "";

    /** The multi-piece structure this lane belongs to (e.g. {@code "IC_CHUO_split"}), or "". */
    @Export public String linkGroup = "";

    /** This piece's role in {@link #linkGroup} — {@code trunk} / {@code branch_a} / {@code branch_b}. */
    @Export public String linkRole = "";

    /** End-of-lane behaviour: {@link VehicleRoute#END_CHAIN} / {@link VehicleRoute#END_UTURN} /
     *  {@link VehicleRoute#END_DESPAWN}. */
    @Export public String endBehavior = VehicleRoute.END_CHAIN;

    /** Optional explicit return lane name for {@link VehicleRoute#END_UTURN} (else derived from
     *  {@link LaneGraph}) — not populated by the current sidecar export; reserved. */
    @Export public String returnRoute = "";

    /** Which district/overlay this lane's authored piece belongs to — from the exported
     *  sidecar's {@code zone_id} (see {@code lib/lane_kit.py:combine_pieces}), the
     *  property-based replacement for the old {@code "<stem>__"} name-prefix zone convention.
     *  "" for a lane whose sidecar predates this field. */
    @Export public String zoneId = "";

    // ---- .lanekit v2 -----------------------------------------------------------------------
    // All ADDITIVE: a lane baked from a v1 sidecar leaves every one of these at its default and
    // behaves exactly as before. v1 districts must keep working — that is the whole compatibility
    // contract of the schema bump.

    /** Design/posted speed for this lane, km/h. 0 = unknown, so the AI keeps its own default. */
    @Export public float speedLimit = 0f;

    /** {@code street} / {@code arterial} / {@code expressway} / {@code ramp}, or "". Lets the
     *  spawner make an arterial busy and a backstreet dead instead of keying density off the
     *  zone marker alone. */
    @Export public String roadClass = "";

    /** The pad this lane belongs to, for a connector — "" for a through lane. This is the key
     *  {@code JunctionArbiter} (roads-v2 Phase 2) needs, and emitting it now is what stops the
     *  whole world needing a re-bake when signals land. */
    @Export public String junctionId = "";

    /** Rise over run along the lane, and superelevation in radians. Advisory: they let the AI
     *  slow for a bend it has not entered yet. */
    @Export public float grade = 0f;
    @Export public float banking = 0f;

    /** May an ambient car be SPAWNED on this lane?
     *
     *  <p>Explicit, because inferring it from a blank {@code turn} letter is exactly how every
     *  one of the 351 island through lanes shipped unspawnable: the exporter omitted {@code turn},
     *  {@code WorldBaker} defaulted a {@code kind == "through"} lane to {@code "S"}, and
     *  {@code isSpawnCandidate} rejects any non-empty turn. A boolean cannot fail that way.
     *
     *  <p>{@link #spawnableExplicit} distinguishes "the sidecar said false" from "the sidecar is
     *  v1 and never said" — without it, defaulting to false would make every already-baked v1
     *  district stop spawning traffic. */
    @Export public boolean spawnable = false;
    @Export public boolean spawnableExplicit = false;

    private static final String PATH_CHILD_NAME = "Path3D";

    private Curve3D curve;
    private Vector3[] bakedGlobal;   // baked points pre-converted to world space (identity-transform fast path still correct)
    private double[]  cum;
    private double     totalLen;

    public PathLaneRoute() { super(); }

    @Register
    @Override
    public void _ready() {
        var pathNode = getNodeOrNull(PATH_CHILD_NAME);
        if (pathNode instanceof Path3D p3d) curve = p3d.getCurve();
        ensureBaked();
        ZoneManager mgr = ZoneManager.get();
        if (mgr != null) mgr.registerRoute(this);
    }

    @Register
    public void _exitTree() {
        ZoneManager mgr = ZoneManager.get();
        if (mgr != null) mgr.unregisterRoute(this);
    }

    /** The backing {@link Curve3D}, or null if the expected "Path3D" child is missing/unset. */
    public Curve3D getCurveResource() { return curve; }

    private Path3D pathChild() {
        var n = getNodeOrNull(PATH_CHILD_NAME);
        return n instanceof Path3D p3d ? p3d : null;
    }

    // ── Baked arc-length cache (built once — this route is static content) ──────────────────

    private void ensureBaked() {
        if (bakedGlobal != null) return;
        Path3D p3d = pathChild();
        if (curve == null || p3d == null) { bakedGlobal = new Vector3[0]; cum = new double[0]; totalLen = 0; return; }
        PackedVector3Array pts = curve.getBakedPoints();
        int n = pts.getSize();
        bakedGlobal = new Vector3[Math.max(n, 1)];
        if (n == 0) { bakedGlobal[0] = p3d.toGlobal(new Vector3(0, 0, 0)); cum = new double[]{0}; totalLen = 0; return; }
        for (int i = 0; i < n; i++) bakedGlobal[i] = p3d.toGlobal(pts.get(i));
        cum = new double[n];
        totalLen = 0;
        for (int i = 1; i < n; i++) { totalLen += horiz(bakedGlobal[i - 1], bakedGlobal[i]); cum[i] = totalLen; }
        if (loop) totalLen += horiz(bakedGlobal[n - 1], bakedGlobal[0]);
    }

    // ── Lane implementation ──────────────────────────────────────────────────────────────────

    @Override
    public Vector3 startPoint() {
        Path3D p3d = pathChild();
        if (curve == null || p3d == null || curve.getPointCount() == 0) return null;
        return p3d.toGlobal(curve.getPointPosition(0));
    }

    /** {@link #startPoint()}, but O(1) off the already-baked array (no curve/point-count re-read)
     *  — this route is static/baked-once content, so the plain baked cache doubles as the entry
     *  cache {@code ZoneManager.findRoute}'s per-candidate spawn scan needs. */
    @Override
    public Vector3 entryPoint() {
        ensureBaked();
        return bakedGlobal.length > 0 ? bakedGlobal[0] : null;
    }

    @Override
    public Vector3 endPoint() {
        Path3D p3d = pathChild();
        if (curve == null || p3d == null || curve.getPointCount() == 0) return null;
        return p3d.toGlobal(curve.getPointPosition(curve.getPointCount() - 1));
    }

    @Override
    public double[] startTangentXZ() { return tangentXZ(true); }

    @Override
    public double[] endTangentXZ() { return tangentXZ(false); }

    private double[] tangentXZ(boolean atStart) {
        Path3D p3d = pathChild();
        if (curve == null || p3d == null) return null;
        int n = curve.getPointCount();
        if (n < 2) return null;
        Vector3 a = p3d.toGlobal(curve.getPointPosition(atStart ? 0 : n - 2));
        Vector3 b = p3d.toGlobal(curve.getPointPosition(atStart ? 1 : n - 1));
        double dx = b.getX() - a.getX(), dz = b.getZ() - a.getZ();
        double len = Math.sqrt(dx * dx + dz * dz);
        return len < 1e-9 ? null : new double[]{dx / len, dz / len};
    }

    @Override
    public boolean isLoop() { return loop; }

    /** Setter half of the exported {@code loop} property. */
    public void setLoop(boolean value) {
        this.loop = value;
    }

    @Override
    public double total() { ensureBaked(); return totalLen; }

    @Override
    public Vector3 pointAtLength(double s) {
        ensureBaked();
        int m = bakedGlobal.length;
        if (m <= 1 || totalLen <= 1e-6) return m == 0 ? new Vector3() : bakedGlobal[0];
        if (loop) { s %= totalLen; if (s < 0) s += totalLen; }
        else s = Math.max(0, Math.min(totalLen, s));
        for (int i = 1; i < m; i++) {
            if (s <= cum[i]) {
                double segLen = cum[i] - cum[i - 1];
                return lerp(bakedGlobal[i - 1], bakedGlobal[i], segLen <= 1e-9 ? 0 : (s - cum[i - 1]) / segLen);
            }
        }
        if (loop) {
            double segLen = totalLen - cum[m - 1];
            return lerp(bakedGlobal[m - 1], bakedGlobal[0], segLen <= 1e-9 ? 0 : (s - cum[m - 1]) / segLen);
        }
        return bakedGlobal[m - 1];
    }

    @Override
    public double lengthAtNearest(Vector3 pos, double aroundS, double window) {
        ensureBaked();
        int m = bakedGlobal.length;
        if (m <= 1) return 0;
        double best = aroundS < 0 ? 0 : aroundS, bestD = Double.MAX_VALUE;
        for (int i = 0; i < m; i++) {
            if (aroundS >= 0) {
                double d = Math.abs(cum[i] - aroundS);
                if (loop) d = Math.min(d, totalLen - d);
                if (d > window) continue;
            }
            double dsq = horizSq(pos, bakedGlobal[i]);
            if (dsq < bestD) { bestD = dsq; best = cum[i]; }
        }
        return best;
    }

    /**
     * A weighted-random explicit successor from {@link #nextRoutes} / {@link #nextWeights}, or
     * null when none is set or resolves — in which case {@link LaneGraph}'s endpoint-proximity
     * derivation still applies, unchanged. Mirrors {@code VehicleRoute.pickNextRoute} exactly,
     * including keeping weights parallel through resolution so an unresolved name (its district
     * not streamed in yet) drops its weight with it and the survivors keep their relative bias.
     */
    @Override
    public Lane pickNextRoute() {
        if (loop || nextRoutes == null || nextRoutes.isBlank()) return null;
        float[] baked = WeightedPick.parseWeights(nextWeights);
        String[] names = nextRoutes.split(",");
        List<Lane> candidates = new ArrayList<>();
        List<Float> kept = new ArrayList<>();
        for (int i = 0; i < names.length; i++) {
            String nm = names[i].trim();
            if (nm.isEmpty()) continue;
            Lane r = resolveRoute(nm);
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
     * Resolve any lane by node name through the {@link ZoneManager} registry — the same
     * registry read {@code VehicleRoute.resolveRoute} uses, never a scene-tree scan (those were
     * the "periodic hitch in all movement" regression). Unlike {@code VehicleRoute}'s version this
     * one is deliberately NOT type-filtered: a baked interchange is all {@code PathLaneRoute}s,
     * but a hand-authored {@code VehicleRoute} feeding into one is a legitimate network and there
     * is no reason to refuse it.
     */
    @Override
    public Lane resolveRoute(String name) {
        if (name == null || name.isBlank()) return null;
        ZoneManager mgr = ZoneManager.get();
        return mgr == null ? null : mgr.routeByName(name.trim());
    }

    /** The movement kind ({@code THROUGH}/{@code EXIT}/{@code ENTRY}) for successor {@code lane},
     *  or "" if it is not an explicit successor of this one. What an AI asks when it needs to know
     *  whether a target leaving this lane took the ramp or stayed on the mainline. */
    @Register
    public String movementKindTo(String laneName) {
        if (nextRoutes == null || nextRoutes.isBlank() || laneName == null) return "";
        String[] names = nextRoutes.split(",");
        String[] kinds = nextKinds == null ? new String[0] : nextKinds.split(",");
        for (int i = 0; i < names.length; i++)
            if (names[i].trim().equals(laneName.trim()))
                return i < kinds.length ? kinds[i].trim() : "THROUGH";
        return "";
    }

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

    // ── Geometry helpers (deliberately duplicated from VehicleRoute's identical windowed-search
    //    loop rather than extracted into a shared helper — small, self-contained, and this is the
    //    only other call site) ────────────────────────────────────────────────────────────────

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

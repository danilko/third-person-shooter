package com.openworld.world;

import godot.api.Node;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The connected traffic lane-graph (PLAN.md I3b) — <b>nodes = junctions, edges = lanes</b>.
 *
 * <p>Derived from geometry, not hand-authored: every {@link Lane} in the active scene (a
 * {@link VehicleRoute} or a {@link PathLaneRoute} — any mix) is a directional lane; lanes whose
 * endpoints fall within {@link #JUNCTION_RADIUS} of each other share a junction.
 * {@link #successorsOf} then returns the lanes a car may continue onto at the end of a lane
 * (every outgoing lane at its end-junction, excluding the direct reverse), so a car picks a random turn
 * at each junction with no per-route wiring. This scales to any road network — a hand-authored
 * {@code VehicleRoute} network and a Blender-generated {@code PathLaneRoute} intersection connect
 * automatically wherever their endpoints coincide, since this pass doesn't care which concrete type
 * it's looking at.
 *
 * <p>Plain Java helper (like {@code SpawnPool}), not a {@code @Script} / AutoLoad. Cached per
 * scene via the scene-instance id (mirrors {@code ZoneManager.detectSceneReload}); routes are
 * static scene content, so the graph is built once per scene and reused.
 */
public final class LaneGraph {

    /** Endpoints within this distance (m, XZ) are treated as the same junction. Must comfortably exceed
     *  a road's lane spacing (opposing lanes ±1.75 m = 3.5 m apart) so paired lanes share a junction,
     *  while staying well under the gap between distinct junctions. */
    public static final double JUNCTION_RADIUS = 4.5;

    /** Set true to log the derived junction/lane counts once per scene build. */
    public static boolean debug = false;

    private static LaneGraph instance;

    private long sceneId = 0;
    private final Map<Lane, Integer> startJunction = new HashMap<>();
    private final Map<Lane, Integer> endJunction   = new HashMap<>();
    private final Map<Integer, List<Lane>> outgoing = new HashMap<>();

    private LaneGraph() {}

    /** Lanes a car may continue onto at the end of {@code lane} — outgoing lanes at its end-junction,
     *  minus the direct reverse. Empty when the lane dead-ends (no connected lane). */
    public static List<Lane> successorsOf(Lane lane) {
        // EXPLICIT SUCCESSORS WIN. Proximity is right almost everywhere and wrong at exactly one
        // place: a gore, where a mainline's and a ramp's lane ends all sit within JUNCTION_RADIUS
        // of each other, so this pass would return "any of them" and a car would fork at random --
        // and an AI could not tell which movement a target actually made. A lane that carries
        // authored successors uses them instead. Everything else falls through unchanged.
        List<Lane> explicit = explicitSuccessorsOf(lane);
        if (!explicit.isEmpty()) return explicit;
        LaneGraph g = forScene(lane);
        if (g == null) return new ArrayList<>();
        Integer ej = g.endJunction.get(lane);
        if (ej == null) return new ArrayList<>();
        Lane rev = g.reverseInternal(lane);
        List<Lane> result = new ArrayList<>();
        for (Lane o : g.outgoing.getOrDefault(ej, new ArrayList<>()))
            if (o != lane && o != rev) result.add(o);
        return result;
    }

    /** Authored successors of {@code lane}, resolved through the route registry — empty for any
     *  lane that carries none (every plain road), which is what keeps this additive. */
    private static List<Lane> explicitSuccessorsOf(Lane lane) {
        List<Lane> out = new ArrayList<>();
        if (!(lane instanceof PathLaneRoute p) || p.nextRoutes == null || p.nextRoutes.isBlank())
            return out;
        for (String nm : p.nextRoutes.split(",")) {
            Lane r = p.resolveRoute(nm);
            if (r != null && r != lane) out.add(r);
        }
        return out;
    }

    /** The lane running back the way {@code lane} came (starts at its end-junction, ends at its
     *  start-junction) — the U-turn target. Null when none is authored. */
    public static Lane reverseOf(Lane lane) {
        LaneGraph g = forScene(lane);
        return g == null ? null : g.reverseInternal(lane);
    }

    private Lane reverseInternal(Lane lane) {
        Integer sj = startJunction.get(lane), ej = endJunction.get(lane);
        if (sj == null || ej == null) return null;
        for (Lane o : outgoing.getOrDefault(ej, new ArrayList<>())) {
            if (o == lane) continue;
            Integer oej = endJunction.get(o);
            if (oej != null && oej.equals(sj)) return o;
        }
        return null;
    }

    /** {@code Lane} doesn't extend {@code Node} (it's implemented by two unrelated Node3D
     *  subclasses), so the scene-tree walk needs a concrete Node to start from — every real
     *  {@code Lane} implementor is one, so this cast always succeeds in practice. */
    private static LaneGraph forScene(Lane anyLane) {
        if (!(anyLane instanceof Node anyNode) || anyNode.getTree() == null) return null;
        Node scene = anyNode.getTree().getCurrentScene();
        if (scene == null) return null;
        long id = scene.getInstanceId();
        if (instance == null || instance.sceneId != id) {
            instance = new LaneGraph();
            instance.sceneId = id;
            instance.build(scene);
        }
        return instance;
    }

    private void build(Node scene) {
        startJunction.clear(); endJunction.clear(); outgoing.clear();
        List<Lane> lanes = new ArrayList<>();
        collect(scene, lanes);
        List<Vector3> junctions = new ArrayList<>();   // representative position per junction id
        for (Lane lane : lanes) {
            Vector3 sp = lane.startPoint(), ep = lane.endPoint();
            if (sp == null || ep == null) continue;
            int sj = junctionFor(sp, junctions);
            int ej = junctionFor(ep, junctions);
            startJunction.put(lane, sj);
            endJunction.put(lane, ej);
            outgoing.computeIfAbsent(sj, k -> new ArrayList<>()).add(lane);
        }
        if (debug) GD.print("LaneGraph: built " + lanes.size() + " lanes / " + junctions.size()
                + " junctions for scene " + scene.getName());
    }

    private static int junctionFor(Vector3 p, List<Vector3> junctions) {
        for (int i = 0; i < junctions.size(); i++) {
            double dx = junctions.get(i).getX() - p.getX(), dz = junctions.get(i).getZ() - p.getZ();
            if (Math.sqrt(dx * dx + dz * dz) <= JUNCTION_RADIUS) return i;
        }
        junctions.add(p);
        return junctions.size() - 1;
    }

    private static void collect(Node node, List<Lane> out) {
        if (node instanceof Lane r) out.add(r);
        for (Node c : node.getChildren()) collect(c, out);
    }
}

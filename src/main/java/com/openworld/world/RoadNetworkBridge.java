package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node;
import godot.api.Path3D;
import godot.core.NodePath;
import godot.core.StringName;
import godot.core.VariantArray;
import godot.global.GD;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Publishes a <b>road-generator</b> network to this project's traffic system.
 *
 * <p>The addon (`addons/road-generator`) grows its own lane objects — a {@code RoadLane} is a
 * {@code Path3D} generated under each {@code RoadPoint} whenever a road rebuilds, carrying
 * {@code lane_next}/{@code lane_prior}/{@code lane_left}/{@code lane_right} pointers. This project
 * drives cars through {@link Lane}, which {@link LaneGraph}, {@code VehicleAIController} and
 * {@link ZoneManager} are all written against. This node is the join: one {@link PathLaneRoute}
 * per {@code RoadLane}, pointed at the generated curve through {@link PathLaneRoute#sourcePath}.
 *
 * <p><b>Why an adapter and not a new {@code Lane} implementation.</b> {@code PathLaneRoute}
 * already wraps a native {@code Curve3D} and already registers with {@code ZoneManager}; its
 * arc-length cache, {@code pointAtLength}, junction hand-over and spawn filtering are the parts
 * that actually took work to get right. Re-implementing {@code Lane} against {@code RoadLane}
 * would have duplicated all of it and given the two copies room to disagree — the exact defect
 * shape this codebase keeps cataloguing. The addon's lanes are consumed, not re-derived.
 *
 * <p><b>Why the lanes are not reparented.</b> The generator owns those nodes and destroys and
 * re-creates them on every rebuild, so renaming one to {@code "Path3D"} (the child name
 * {@code PathLaneRoute} looks for by default) or moving it under a lane node would be undone the
 * next time an artist drags a {@code RoadPoint}. {@code sourcePath} holds an absolute path to the
 * live node instead, and {@link #rebuild()} re-reads the network when the roads change.
 *
 * <p><b>Timing.</b> {@code RoadContainer} builds its geometry through
 * {@code call_deferred("_dirty_rebuild_deferred")}, so the lanes do not exist during {@code
 * _ready}. This node waits {@link #buildDelayFrames} frames before reading, and reports how many
 * lanes it found — a silent zero is the failure mode worth naming, because an empty lane graph
 * looks exactly like a working one until no car ever spawns.
 */
@Script(className = "RoadNetworkBridge")
public class RoadNetworkBridge extends Node {

    /** The addon's {@code RoadManager}. Empty = search this node's own children. */
    @Export public NodePath roadManagerPath = new NodePath("");

    public NodePath getRoadManagerPath() { return roadManagerPath; }
    public void setRoadManagerPath(NodePath v) { roadManagerPath = v; }

    /** Frames to wait before reading the network — the generator builds deferred. */
    @Export public int buildDelayFrames = 3;

    public int getBuildDelayFrames() { return buildDelayFrames; }
    public void setBuildDelayFrames(int v) { buildDelayFrames = v; }

    /** Lane width handed to each published lane (informational, mirrors the addon's own). */
    @Export public float laneWidth = 4.0f;

    public float getLaneWidth() { return laneWidth; }
    public void setLaneWidth(float v) { laneWidth = v; }

    /** Whether published lanes are legal ambient-spawn points for {@link ZoneManager}. */
    @Export public boolean spawnable = true;

    public boolean getSpawnable() { return spawnable; }
    public void setSpawnable(boolean v) { spawnable = v; }

    @Export public boolean debugLog = true;

    public boolean getDebugLog() { return debugLog; }
    public void setDebugLog(boolean v) { debugLog = v; }

    private int frames = 0;
    private boolean built = false;
    private final List<PathLaneRoute> published = new ArrayList<>();

    public RoadNetworkBridge() { super(); }

    @Register
    @Override
    public void _ready() {
        setProcess(true);
    }

    @Register
    public void _process(double delta) {
        if (built) return;
        if (++frames < buildDelayFrames) return;
        setProcess(false);
        rebuild();
    }

    /** Drop every published lane and read the addon's network again. Safe to call repeatedly. */
    @Register
    public void rebuild() {
        for (PathLaneRoute r : published) {
            if (GD.isInstanceValid(r)) {
                removeChild(r);
                r.queueFree();
            }
        }
        published.clear();
        built = true;

        Node mgr = resolveManager();
        if (mgr == null) {
            if (debugLog) GD.print("RoadNetworkBridge: no RoadManager found — no lanes published");
            return;
        }

        List<Node> lanes = collectLanes(mgr);
        // RoadLane node -> the name of the PathLaneRoute publishing it, so the second pass can
        // translate the addon's NodePath pointers into this project's name-based successors.
        Map<Long, String> byLane = new HashMap<>();

        for (Node laneNode : lanes) {
            if (!(laneNode instanceof Path3D p3d)) continue;
            PathLaneRoute route = new PathLaneRoute();
            route.setName(new StringName(uniqueName(laneNode.getName().toString())));
            route.laneWidth = laneWidth;
            route.spawnable = spawnable;
            route.spawnableExplicit = true;
            route.loop = false;
            // Absolute path: the generator re-creates these nodes on every rebuild, so a relative
            // path from a node that does not exist yet would not resolve. Set BEFORE addChild —
            // addChild runs _ready immediately, and _ready is where the curve is read.
            route.sourcePath = p3d.getPath();
            addChild(route);
            published.add(route);
            byLane.put(laneNode.getInstanceId(), route.getName().toString());
        }

        int chained = 0;
        for (int i = 0; i < lanes.size(); i++) {
            Node laneNode = lanes.get(i);
            String selfName = byLane.get(laneNode.getInstanceId());
            if (selfName == null) continue;
            Node next = followPointer(laneNode, "lane_next");
            if (next == null) continue;
            String nextName = byLane.get(next.getInstanceId());
            if (nextName == null) continue;
            for (PathLaneRoute r : published) {
                if (r.getName().toString().equals(selfName)) {
                    r.nextRoutes = nextName;
                    chained++;
                    break;
                }
            }
        }

        if (debugLog) {
            GD.print("RoadNetworkBridge: published " + published.size() + " lanes from "
                    + lanes.size() + " RoadLanes, " + chained + " chained by lane_next");
            if (published.isEmpty()) {
                GD.print("  (none — check the RoadContainer has generate_ai_lanes enabled and"
                        + " has rebuilt; a silent zero here means no car will ever spawn)");
            }
        }
    }

    /** How many lanes this bridge currently publishes — the number a test should assert on. */
    @Register
    public int publishedCount() { return published.size(); }

    private Node resolveManager() {
        if (roadManagerPath != null && !roadManagerPath.getPath().isEmpty()) {
            return getNodeOrNull(roadManagerPath);
        }
        for (Node child : getChildren()) {
            if (child.hasMethod(new StringName("get_containers"))) return child;
        }
        return null;
    }

    /** {@code RoadManager.get_containers() -> RoadContainer.get_segments() -> RoadSegment.get_lanes()} */
    private List<Node> collectLanes(Node manager) {
        List<Node> out = new ArrayList<>();
        Object containers = manager.call(new StringName("get_containers"));
        for (Object c : asNodes(containers)) {
            Node container = (Node) c;
            if (!container.hasMethod(new StringName("get_segments"))) continue;
            for (Object s : asNodes(container.call(new StringName("get_segments")))) {
                Node seg = (Node) s;
                if (!seg.hasMethod(new StringName("get_lanes"))) continue;
                for (Object l : asNodes(seg.call(new StringName("get_lanes")))) {
                    out.add((Node) l);
                }
            }
        }
        return out;
    }

    private List<Object> asNodes(Object value) {
        List<Object> out = new ArrayList<>();
        if (value instanceof VariantArray<?> arr) {
            for (Object o : arr) if (o instanceof Node) out.add(o);
        }
        return out;
    }

    /** Resolve one of the addon's lane pointers ({@code lane_next} etc.) to the node it names. */
    private Node followPointer(Node lane, String property) {
        Object raw = lane.get(new StringName(property));
        if (!(raw instanceof NodePath np) || np.getPath().isEmpty()) return null;
        return lane.getNodeOrNull(np);
    }

    private String uniqueName(String base) {
        String name = "Lane_" + base;
        String candidate = name;
        int n = 1;
        while (getNodeOrNull(candidate) != null) {
            candidate = name + "_" + (n++);
        }
        return candidate;
    }
}

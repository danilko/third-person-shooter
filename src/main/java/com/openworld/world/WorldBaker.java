package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.annotation.Tool;
import godot.api.Area3D;
import godot.api.BoxShape3D;
import godot.api.CollisionShape3D;
import godot.api.Curve3D;
import godot.api.FileAccess;
import godot.api.JSON;
import godot.api.Marker3D;
import godot.api.Mesh;
import godot.api.MeshInstance3D;
import godot.api.MultiMesh;
import godot.api.MultiMeshInstance3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.PackedScene;
import godot.api.Path3D;
import godot.api.ResourceSaver;
import godot.core.Dictionary;
import godot.core.Error;
import godot.core.StringName;
import godot.core.Transform3D;
import godot.core.VariantArray;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Bakes a Blender-authored world (PLAN.md I6a). Loads an imported `.blend`/`.glb` scene, walks it, turns
 * <b>named</b> objects into gameplay nodes (the {@code BLENDER_CONVENTIONS.md} prefix table), and saves a
 * native {@code .tscn} via {@link PackedScene#pack} + {@link ResourceSaver}. The game then loads that
 * native scene at runtime — no per-load walk of the source.
 *
 * <p>Chosen over {@code EditorScenePostImport} because godot-kotlin-jvm exposes no editor API in this
 * project (verified), so the conversion must run as ordinary Java that reuses the game classes
 * ({@link VehicleRoute}, {@link Zone}, …). Run it from a dev key ({@code DebugHarness}) or a
 * {@code BakeWorld} scene with {@link #bakeOnReady}; re-run when the source changes.
 *
 * <p><b>Prefix → node</b>: {@code lane_<route>_<n>} → one {@link VehicleRoute} per route (ordered
 * {@link Marker3D} children); {@code spawn_<faction>_<n>} → a {@link SpawnConfig} on the nearest
 * {@code zone_}; {@code zone_<id>}/{@code region_<id>} → {@link ZoneMarker} + {@link Zone}
 * (+ {@link RegionConfig}); {@code water_<id>} → {@link WaterVolume} in group {@code "water"};
 * {@code bounds_<id>} → {@link WorldBounds} (the logic wall);
 * {@code intersection_<id>} → {@link IntersectionZone}. Everything else (meshes, {@code -col} collision)
 * is kept untouched. Parameters come from Blender custom properties (node metadata) with defaults.
 *
 * <p>The output is the <i>augmented source tree</i>: geometry/hierarchy kept as-is, gameplay nodes added,
 * consumed marker empties freed. {@code owner} is set on every node before packing (or {@code pack()}
 * silently drops it).
 */
@Tool
@Script(className = "WorldBaker")
public class WorldBaker extends Node {

    @Export public String sourceScenePath =
            "res://src/main/resources/com/openworld/world/WorldSource.tscn";
    @Export public String outputScenePath =
            "res://src/main/resources/com/openworld/world/World_baked.tscn";
    /** Bake automatically when this node enters the tree (for a dedicated BakeWorld scene). */
    @Export public boolean bakeOnReady = false;
    /**
     * After an auto-bake (the {@link #bakeOnReady} path only), quit the process. Lets the BakeWorld
     * scene run as a one-shot CLI batch job — {@code godot --headless … BakeWorld.tscn} bakes and exits.
     * Never fires from the {@link #bake()} method, so {@code DebugHarness} F5 / in-editor {@code @Tool}
     * invocations don't kill the running game.
     */
    @Export public boolean quitWhenDone = false;
    /**
     * Base directory for resolving {@code instance_<assetId>} markers whose {@code asset_path} meta is
     * absent: the asset is loaded from {@code kitDir + assetId + ".tscn"}. A marker carrying an explicit
     * {@code asset_path} (res:// path) custom property overrides this — the recommended bridge, since it
     * survives Blender's {@code .NNN} duplicate renaming (see BLENDER_CONVENTIONS "Nested instancing").
     */
    @Export public String kitDir =
            "res://src/main/resources/com/openworld/world/kit/";

    /**
     * Optional path to a Blender-exported {@code .lanekit.json} sidecar (e.g.
     * {@code assets/world_source/lib/intersection_kit.py}'s {@code export_json}) — one
     * {@link PathLaneRoute} is built per entry in its {@code lanes} array, added straight under
     * the baked scene root. Blank (the default) = skip; this is opt-in per-bake rather than
     * auto-derived from {@link #sourceScenePath} (unlike {@code resolveGeometryPath}'s sibling-
     * {@code .scn} preference) since there is currently exactly one real fixture to test against —
     * auto-derivation is a natural follow-up once this is proven on more than the prototype.
     */
    @Export public String lanekitPath = "";

    @Register
    @Override
    public void _ready() {
        if (bakeOnReady) {
            bake();
            if (quitWhenDone) getTree().quit();   // ResourceSaver.save is synchronous → file is on disk
        }
    }

    /** Bake {@link #sourceScenePath} → {@link #outputScenePath} (callable from a dev key / editor toggle). */
    @Register
    public void bake() {
        bakeScene(this, sourceScenePath, outputScenePath, kitDir, lanekitPath);
    }

    /** Back-compat 3-arg overload (DebugHarness F5): bake with the default kit directory, no lanekit sidecar. */
    public static void bakeScene(Node host, String srcPath, String outPath) {
        bakeScene(host, srcPath, outPath, "res://src/main/resources/com/openworld/world/kit/");
    }

    /** Back-compat 4-arg overload: bake with no lanekit sidecar. */
    public static void bakeScene(Node host, String srcPath, String outPath, String kitDir) {
        bakeScene(host, srcPath, outPath, kitDir, "");
    }

    /**
     * Load {@code srcPath}, convert named objects, and save a native scene to {@code outPath}. {@code host}
     * must be in the tree (the source is parented to it so global transforms resolve during conversion).
     * {@code kitDir} resolves {@code instance_<assetId>} markers lacking an {@code asset_path} meta.
     * {@code lanekitPath} (blank = skip) additionally builds {@link PathLaneRoute}s from a
     * {@code .lanekit.json} sidecar — see {@link #lanekitPath}.
     */
    public static void bakeScene(Node host, String srcPath, String outPath, String kitDir, String lanekitPath) {
        Object loaded = GD.load(srcPath);
        if (!(loaded instanceof PackedScene src)) {
            GD.printErr("WorldBaker: could not load source scene '" + srcPath + "'");
            return;
        }
        Node root = src.instantiate();
        if (root == null) { GD.printErr("WorldBaker: source instantiate failed"); return; }
        host.addChild(root);   // in-tree → getGlobalTransform works during conversion

        // Instance roots must be owned by the pack root but NOT have their internals re-owned, or pack()
        // inlines (flattens) the instance instead of recording an `instance=` reference.
        Set<Node> instanceRoots = new HashSet<>();
        int[] counts = convert(root, kitDir, instanceRoots);
        stripEagerLodLow(root);

        int pathLaneCount = (lanekitPath != null && !lanekitPath.isBlank())
                ? loadLaneKitSidecar(root, lanekitPath) : 0;

        setOwnerRecursive(root, root, instanceRoots);
        PackedScene packed = new PackedScene();
        Error err = packed.pack(root);
        if (err == Error.OK) {
            Error save = ResourceSaver.save(packed, outPath, ResourceSaver.SaverFlags.FLAG_NONE);
            GD.print("WorldBaker: baked '" + srcPath + "' → '" + outPath + "' (" + (save == Error.OK ? "saved" : "save FAILED " + save)
                    + ") — routes=" + counts[0] + " zones=" + counts[1] + " spawns=" + counts[2]
                    + " water=" + counts[3] + " junctions=" + counts[4] + " instances=" + counts[5]
                    + " pathlanes=" + pathLaneCount);
        } else {
            GD.printErr("WorldBaker: pack() failed: " + err);
        }
        host.removeChild(root);
        root.queueFree();
    }

    /**
     * {@code buildZone()}'s {@code root.addChild(marker)} adds each {@link ZoneMarker} to an
     * already-live tree, so its {@code _ready()} — and the {@code instantiateLodLow()}/
     * {@code buildDebugVisuals()} it calls — fires synchronously during conversion, before
     * {@code pack()}. Left alone that bakes two things permanently into the master {@code .tscn}
     * that should never ship: whatever LOD_LOW tier happens to exist on disk *at bake time* (making
     * a re-bake's LOD_LOW content depend on district build order — {@link ZoneManager} already
     * loads it lazily at runtime, so the baked-in copy is a pure liability), and the translucent
     * zone-volume box + load/unload rings — a single-zone dev aid that, baked once per district,
     * becomes dozens of large overlapping transparent rings embedded across the whole map (real
     * geometry + material sub-resources the editor then has to load/render just to open the scene).
     * Strip both here, right after conversion and before {@code pack()}. A no-op for per-district
     * bakes (they never contain a {@code ZoneMarker}; those only come from the master's
     * {@code zone_}/{@code region_} markers).
     */
    private static void stripEagerLodLow(Node node) {
        if (node instanceof ZoneMarker marker) {
            marker.removeLodLow();
            marker.removeDebugVisuals();
        }
        for (Node child : node.getChildren()) stripEagerLodLow(child);
    }

    // ── Conversion ──────────────────────────────────────────────────────────────

    /** Returns {routes, zones, spawns, water, junctions, instances} counts; fills {@code instanceRoots}. */
    private static int[] convert(Node root, String kitDir, Set<Node> instanceRoots) {
        // Collect named nodes first (mutating the tree mid-walk is unsafe).
        List<Node3D> all = new ArrayList<>();
        collectNode3D(root, all);

        // lane_<route>_<n> grouped by route, ordered by <n>.
        Map<String, List<Node3D>> lanes = new LinkedHashMap<>();
        List<Node3D> zones = new ArrayList<>();
        List<Node3D> spawns = new ArrayList<>();
        List<Node3D> waters = new ArrayList<>();
        List<Node3D> boundsMarkers = new ArrayList<>();
        List<Node3D> junctions = new ArrayList<>();
        List<Node3D> instances = new ArrayList<>();
        List<Node3D> mmeshes = new ArrayList<>();
        for (Node3D n : all) {
            String name = n.getName().toString();
            if (name.startsWith("mmesh_"))             mmeshes.add(n);   // GPU-instanced visual bulk
            // an empty asset_path (e.g. a not-yet-assigned reserve_manual slot) is NOT an instance
            else if (name.startsWith("instance_") || !metaString(n, "asset_path", "").isEmpty()) instances.add(n);
            else if (name.startsWith("lane_"))         lanes.computeIfAbsent(routeOf(name), k -> new ArrayList<>()).add(n);
            else if (name.startsWith("zone_") || name.startsWith("region_")) zones.add(n);
            else if (name.startsWith("spawn_"))        spawns.add(n);
            else if (name.startsWith("water_"))        waters.add(n);
            else if (name.startsWith("bounds_"))       boundsMarkers.add(n);
            else if (name.startsWith("intersection_")) junctions.add(n);
        }

        // Zones first (spawns attach to the nearest one).
        List<ZoneMarker> markers = new ArrayList<>();
        for (Node3D z : zones) markers.add(buildZone(root, z));

        int routeCount = 0;
        for (Map.Entry<String, List<Node3D>> e : lanes.entrySet()) { buildRoute(root, e.getKey(), e.getValue()); routeCount++; }
        for (Node3D s : spawns)    attachSpawn(markers, s);
        for (Node3D w : waters)    buildWater(root, w);
        for (Node3D b : boundsMarkers) buildBounds(root, b);
        for (Node3D j : junctions) buildJunction(root, j);

        int instanceCount = 0;
        for (Node3D in : instances) {
            Node r = buildInstance(root, in, kitDir);
            if (r != null) { instanceRoots.add(r); instanceCount++; }
        }

        int mmeshCount = buildMultiMeshes(root, mmeshes, kitDir);

        // Free the consumed marker empties (their gameplay node now stands in for them).
        for (List<Node3D> g : lanes.values()) for (Node3D n : g) freeEmpty(n);
        for (Node3D n : zones)     freeEmpty(n);
        for (Node3D n : spawns)    freeEmpty(n);
        for (Node3D n : waters)    freeEmpty(n);
        for (Node3D n : boundsMarkers) freeEmpty(n);
        for (Node3D n : junctions) freeEmpty(n);
        for (Node3D n : instances) freeEmpty(n);   // proxy geometry under the marker goes with it
        for (Node3D n : mmeshes)   freeEmpty(n);

        GD.print("WorldBaker: mmesh instances collapsed = " + mmeshCount + " (in "
                + " MultiMeshInstance3D nodes)");
        return new int[]{ routeCount, markers.size(), spawns.size(), waters.size(), junctions.size(), instanceCount };
    }

    /**
     * Collapse {@code mmesh_<piece>} markers into one {@link MultiMeshInstance3D} per unique asset
     * (GPU instancing: one node + one draw call for thousands of identical pieces, vs. a scene
     * instance each). This is what keeps a dense district's node count — and, since a MultiMesh
     * carries <b>no collision</b>, its physics-body count — bounded: the visual bulk (wall panels,
     * ground/road tiles, sidewalks) rides here; solidity comes from separate coarse collision proxies.
     * Returns the total instance count folded in.
     *
     * <p><b>Requires a real RenderingServer</b> — {@code set_instance_transform} routes through the RS,
     * and the {@code --headless} dummy renderer drops the transform buffer (instances collapse to
     * origin). So bake district pieces WITHOUT {@code --headless} (a windowed run, {@code xvfb-run},
     * {@code DebugHarness} F5, or editor F6); then the MultiMesh serializes natively into the {@code .tscn}.
     */
    private static int buildMultiMeshes(Node root, List<Node3D> markers, String kitDir) {
        Map<String, List<Node3D>> byAsset = new LinkedHashMap<>();
        for (Node3D m : markers) {
            String path = instanceAssetPath(m, kitDir);
            if (!path.isEmpty()) byAsset.computeIfAbsent(path, k -> new ArrayList<>()).add(m);
        }
        int total = 0;
        for (Map.Entry<String, List<Node3D>> e : byAsset.entrySet()) {
            Mesh mesh = loadVisualMesh(e.getKey());
            if (mesh == null) { GD.printErr("WorldBaker: mmesh — no visual mesh in '" + e.getKey() + "'"); continue; }
            List<Node3D> ms = e.getValue();
            MultiMesh mm = new MultiMesh();
            mm.setTransformFormat(MultiMesh.TransformFormat.TRANSFORM_3D);   // must precede instanceCount
            mm.setMesh(mesh);
            mm.setInstanceCount(ms.size());
            for (int i = 0; i < ms.size(); i++) mm.setInstanceTransform(i, ms.get(i).getGlobalTransform());
            MultiMeshInstance3D mmi = new MultiMeshInstance3D();
            mmi.setName(new StringName("MM_" + assetStem(e.getKey())));
            mmi.setMultimesh(mm);
            root.addChild(mmi);
            total += ms.size();
        }
        return total;
    }

    /** Load a kit asset scene and return its first visual mesh resource (kit leaves are at origin). */
    private static Mesh loadVisualMesh(String path) {
        Object loaded = GD.load(path);
        if (!(loaded instanceof PackedScene ps)) return null;
        Node inst = ps.instantiate();
        if (inst == null) return null;
        Mesh mesh = findMesh(inst);
        inst.free();   // node freed; the ref-counted Mesh resource survives via the returned reference
        return mesh;
    }

    private static Mesh findMesh(Node n) {
        if (n instanceof MeshInstance3D mi && mi.getMesh() != null) return mi.getMesh();
        for (Node c : n.getChildren()) { Mesh m = findMesh(c); if (m != null) return m; }
        return null;
    }

    private static String assetStem(String path) {
        int slash = path.lastIndexOf('/'); int dot = path.lastIndexOf('.');
        return path.substring(slash + 1, dot > slash ? dot : path.length());
    }

    /**
     * Swap an {@code instance_<assetId>} marker (or any node carrying an {@code asset_path} meta) for a
     * real {@link PackedScene} instance at the marker's transform. The instanced root keeps its
     * {@code scene_file_path} (so {@code pack()} records an {@code instance=} reference, not inlined
     * geometry) and is returned so the owner pass can avoid re-owning its internals.
     */
    private static Node buildInstance(Node root, Node3D marker, String kitDir) {
        String path = instanceAssetPath(marker, kitDir);
        if (path.isEmpty()) {
            GD.printErr("WorldBaker: instance marker '" + marker.getName() + "' has no asset_path / resolvable id");
            return null;
        }
        Object loaded = GD.load(path);
        if (!(loaded instanceof PackedScene ps)) {
            GD.printErr("WorldBaker: instance asset not found or not a scene: '" + path + "'");
            return null;
        }
        Node inst = ps.instantiate();
        if (inst == null) { GD.printErr("WorldBaker: instantiate failed for '" + path + "'"); return null; }
        inst.setSceneFilePath(path);   // pack() records an instance only when scene_file_path is set
        root.addChild(inst);
        if (inst instanceof Node3D n3) {
            n3.setGlobalTransform(marker.getGlobalTransform());   // full transform — rotation/scale matter for walls
        }
        return inst;
    }

    /** Resolve an instance marker's source scene: {@code asset_path} meta wins; else {@code kitDir + id}. */
    private static String instanceAssetPath(Node3D marker, String kitDir) {
        String meta = metaString(marker, "asset_path", "");
        if (!meta.isEmpty()) return meta;
        String name = marker.getName().toString();
        if (!name.startsWith("instance_")) return "";
        String id = name.substring("instance_".length());
        int dot = id.lastIndexOf('.');   // strip Blender's .NNN duplicate suffix (Wall_4m.001 → Wall_4m)
        if (dot > 0 && isInt(id.substring(dot + 1))) id = id.substring(0, dot);
        String dir = kitDir.endsWith("/") ? kitDir : kitDir + "/";
        return dir + id + ".tscn";
    }

    private static void buildRoute(Node root, String route, List<Node3D> empties) {
        empties.sort((a, b) -> Integer.compare(indexOf(a.getName().toString()), indexOf(b.getName().toString())));
        VehicleRoute vr = new VehicleRoute();
        vr.setName(new StringName(route));
        vr.loop        = metaBool(empties.get(0), "loop", false);
        vr.laneOffset  = metaFloat(empties.get(0), "lane_offset", 1.75f);
        vr.endBehavior = metaString(empties.get(0), "end_behavior", VehicleRoute.END_DESPAWN);
        vr.nextRoutes  = metaString(empties.get(0), "next_routes", "");
        vr.nextWeights = metaString(empties.get(0), "next_weights", "");
        vr.turn        = metaString(empties.get(0), "turn", "");
        vr.approach    = metaString(empties.get(0), "approach", "");
        vr.laneWidth   = metaFloat(empties.get(0), "lane_width", 3.5f);
        vr.returnRoute = metaString(empties.get(0), "return_route", "");
        root.addChild(vr);
        for (Node3D e : empties) {
            Marker3D m = new Marker3D();
            vr.addChild(m);
            m.setGlobalPosition(e.getGlobalPosition());
        }
    }

    /**
     * Load a {@code .lanekit.json} sidecar (see {@link #lanekitPath}) and build one
     * {@link PathLaneRoute} per entry in its {@code lanes} array, each holding a {@code Path3D}
     * child with a {@code Curve3D} populated straight from the entry's sampled {@code points}
     * (already exact world-space, from {@code intersection_kit.py}'s corner-fillet math — no
     * further smoothing needed). {@code arms}/{@code ports} are parsed only to look up each lane's
     * {@code lane_width} and to validate the file shape; they are not otherwise consumed yet —
     * they exist for a future cross-piece linker (see road_blender_godot.md). Returns the number
     * of lanes built (0 on any structural problem, logged via {@code GD.printErr}, never throws —
     * a malformed/missing sidecar must not abort the rest of the bake).
     */
    private static int loadLaneKitSidecar(Node root, String path) {
        if (!FileAccess.fileExists(path)) {
            GD.printErr("WorldBaker: lanekitPath set but file not found: '" + path + "'");
            return 0;
        }
        String text = FileAccess.getFileAsString(path);
        Object parsed = JSON.parseString(text);
        if (!(parsed instanceof Dictionary<?, ?> dict)) {
            GD.printErr("WorldBaker: lanekit sidecar '" + path + "' did not parse to a JSON object");
            return 0;
        }
        Object lanesObj = dict.get("lanes");
        if (!(lanesObj instanceof VariantArray<?> lanesArr)) {
            GD.printErr("WorldBaker: lanekit sidecar '" + path + "' has no 'lanes' array");
            return 0;
        }
        Map<String, Float> laneWidthByArm = new LinkedHashMap<>();
        Object armsObj = dict.get("arms");
        if (armsObj instanceof VariantArray<?> armsArr) {
            for (Object a : armsArr) {
                if (a instanceof Dictionary<?, ?> armDict) {
                    String name = jsonString(armDict, "name", null);
                    if (name != null) laneWidthByArm.put(name, jsonFloat(armDict, "lane_width", 3.5f));
                }
            }
        }

        int built = 0;
        for (Object laneObj : lanesArr) {
            if (laneObj instanceof Dictionary<?, ?> laneDict
                    && buildPathLaneRoute(root, laneDict, laneWidthByArm) != null) built++;
        }
        GD.print("WorldBaker: lanekit sidecar '" + path + "' → " + built + " PathLaneRoute(s)");
        return built;
    }

    private static PathLaneRoute buildPathLaneRoute(Node root, Dictionary<?, ?> laneDict,
                                                       Map<String, Float> laneWidthByArm) {
        String id = jsonString(laneDict, "id", null);
        Object pointsObj = laneDict.get("points");
        if (id == null || !(pointsObj instanceof VariantArray<?> pointsArr) || pointsArr.size() < 2) {
            GD.printErr("WorldBaker: skipping malformed lanekit lane entry (missing id/points)");
            return null;
        }
        String kind = jsonString(laneDict, "kind", "");
        String fromArm = jsonString(laneDict, "from_arm", "");

        PathLaneRoute lane = new PathLaneRoute();
        lane.setName(new StringName(id));
        lane.loop = jsonBool(laneDict, "loop", false);
        lane.turn = jsonString(laneDict, "turn", "through".equals(kind) ? "S" : "");
        lane.approach = fromArm;
        lane.endBehavior = VehicleRoute.END_CHAIN;   // geometry-derived connectivity (LaneGraph) picks the successor
        lane.laneWidth = laneWidthByArm.getOrDefault(fromArm, 3.5f);
        lane.zoneId = jsonString(laneDict, "zone_id", "");
        // Explicit road-network connectivity (Phase 3). Absent on a plain lane, which leaves the
        // fields empty and keeps it on LaneGraph's endpoint-proximity path exactly as before --
        // these are ADDITIVE, only interchange pieces carry them.
        lane.nextRoutes = jsonStringList(laneDict, "next");
        lane.nextWeights = jsonStringList(laneDict, "next_weights");
        lane.nextKinds = jsonStringList(laneDict, "next_kinds");
        lane.innerLane = jsonString(laneDict, "inner_lane", "");
        lane.outerLane = jsonString(laneDict, "outer_lane", "");
        lane.linkGroup = jsonString(laneDict, "link_group", "");
        lane.linkRole = jsonString(laneDict, "link_role", "");

        // ---- .lanekit v2 fields. Every one is optional: a v1 sidecar leaves them at their
        // defaults and the lane behaves exactly as it did before the schema bump.
        lane.speedLimit = jsonFloat(laneDict, "speed_limit", 0f);
        lane.roadClass = jsonString(laneDict, "road_class", "");
        lane.junctionId = jsonString(laneDict, "junction_id", "");
        lane.grade = jsonFloat(laneDict, "grade", 0f);
        lane.banking = jsonFloat(laneDict, "banking", 0f);
        lane.spawnableExplicit = present(laneDict.get("spawnable"));
        lane.spawnable = jsonBool(laneDict, "spawnable", false);
        if (laneDict.get("lane_width") != null && present(laneDict.get("lane_width"))) {
            lane.laneWidth = jsonFloat(laneDict, "lane_width", lane.laneWidth);
        }

        Curve3D curve = new Curve3D();
        // v2 first: `curve` is a list of {p, in, out} with REAL bezier handles, so the Curve3D is
        // a curve rather than the polyline handed straight back by getBakedPoints(). Blender
        // already has these tangents — v1 was throwing them away, not lacking them — and the
        // control points sit at the authored stations, roughly a 5x drop in point count.
        Object curveObj = laneDict.get("curve");
        if (curveObj instanceof VariantArray<?> curveArr && curveArr.size() >= 2) {
            for (Object cObj : curveArr) {
                if (!(cObj instanceof Dictionary<?, ?> cDict)) continue;
                Vector3 p = jsonVec3(cDict, "p", null);
                if (p == null) continue;
                curve.addPoint(p, jsonVec3(cDict, "in", Vector3.Companion.getZERO()),
                        jsonVec3(cDict, "out", Vector3.Companion.getZERO()));
            }
        }
        if (curve.getPointCount() < 2) {                 // v1 sidecar, or a malformed curve block
            curve = new Curve3D();
            for (Object ptObj : pointsArr) {
                if (!(ptObj instanceof VariantArray<?> pt) || pt.size() < 3) continue;
                curve.addPoint(new Vector3(((Number) pt.get(0)).doubleValue(),
                        ((Number) pt.get(1)).doubleValue(), ((Number) pt.get(2)).doubleValue()));
            }
        }
        curve.setClosed(lane.loop);
        // NO UP VECTOR. Godot bakes one by propagating a frame along the curve, and on a climbing
        // road it rotates that frame about an axis derived from consecutive tangents -- which on
        // the shrine touge's benched hairpins comes out 0.05% off unit and prints
        // "The axis Vector3 (...) must be normalized" once per lane (19 of them on a clean load).
        // Nothing here reads it: `PathLaneRoute` takes `getBakedPoints()` for XZ arc length and
        // there is no `PathFollow3D` on a lane anywhere in the project. Turning the propagation off
        // removes the whole computation rather than silencing its complaint.
        curve.setUpVectorEnabled(false);

        Path3D path3d = new Path3D();
        path3d.setName(new StringName("Path3D"));
        path3d.setCurve(curve);
        lane.addChild(path3d);   // off-tree -- lane._ready() (fires once root.addChild(lane) below
                                  // brings the whole subtree live) finds Path3D already populated
        root.addChild(lane);
        return lane;
    }

    /** A JSON array of scalars as the comma-separated string {@code VehicleRoute.nextRoutes} /
     *  {@code util.WeightedPick} already parse — so the sidecar can stay a natural JSON list while
     *  the exported Godot property keeps the one format both route types share. Missing/empty
     *  yields "", which is the "no explicit connectivity here" signal. */
    private static String jsonStringList(Dictionary<?, ?> d, String key) {
        Object v = d.get(key);
        if (!(v instanceof VariantArray<?> arr) || arr.size() == 0) return "";
        StringBuilder sb = new StringBuilder();
        for (Object o : arr) {
            if (!present(o)) continue;
            if (sb.length() > 0) sb.append(',');
            sb.append(o.toString());
        }
        return sb.toString();
    }

    /** A JSON {@code [x, y, z]} as a {@code Vector3} — already in Godot axes, converted at the one
     *  conversion site on the Blender side. */
    private static Vector3 jsonVec3(Dictionary<?, ?> d, String key, Vector3 def) {
        Object v = d.get(key);
        if (!(v instanceof VariantArray<?> a) || a.size() < 3) return def;
        try {
            return new Vector3(((Number) a.get(0)).doubleValue(), ((Number) a.get(1)).doubleValue(),
                    ((Number) a.get(2)).doubleValue());
        } catch (Exception e) { return def; }
    }

    private static String jsonString(Dictionary<?, ?> d, String key, String def) {
        Object v = d.get(key);
        return present(v) ? v.toString() : def;
    }

    private static float jsonFloat(Dictionary<?, ?> d, String key, float def) {
        Object v = d.get(key);
        if (!present(v)) return def;
        try { return ((Number) v).floatValue(); } catch (Exception e) { return def; }
    }

    private static boolean jsonBool(Dictionary<?, ?> d, String key, boolean def) {
        Object v = d.get(key);
        if (!present(v)) return def;
        try { return (Boolean) v; } catch (Exception e) { return def; }
    }

    private static ZoneMarker buildZone(Node root, Node3D empty) {
        String id = idOf(empty.getName().toString());
        Zone zone = new Zone();
        zone.zoneId = id;
        zone.size = metaVec3(empty, "size", new Vector3(80f, 10f, 80f));
        zone.loadRadius = metaFloat(empty, "load_radius",
                (float) (Math.max(zone.size.getX(), zone.size.getZ()) / 2.0 + 150.0));
        zone.unloadRadius = metaFloat(empty, "unload_radius", zone.loadRadius + 150f);
        zone.geometryPath = metaString(empty, "geometry", "");   // resolved lazily at stream time
        zone.lodLowGeometryPath = metaString(empty, "geometry_lod_low", "");   // eager, always-resident tier
        zone.regionConfig = buildRegion(empty);
        // Ambient-traffic recipe (roads-v2 Phase 1): traffic_count cars on the lanes whose route
        // name starts with traffic_route (a PREFIX — ZoneManager.findRoute distributes spawns
        // across the matching lanes near the zone). No meta = no traffic for this zone.
        int trafficCount = (int) metaFloat(empty, "traffic_count", 0f);
        String trafficRoute = metaString(empty, "traffic_route", "");
        if (trafficCount > 0 && !trafficRoute.isEmpty()) {
            VehicleSpawnConfig vc = new VehicleSpawnConfig();
            vc.count = trafficCount;
            vc.routeName = trafficRoute;
            vc.cruiseThrottle = metaFloat(empty, "traffic_throttle", vc.cruiseThrottle);
            zone.vehicleSpawnConfigs.add(vc);
        }
        ZoneMarker marker = new ZoneMarker();
        marker.setName(new StringName("ZoneMarker_" + id));
        marker.zone = zone;
        root.addChild(marker);
        marker.setGlobalPosition(empty.getGlobalPosition());
        return marker;
    }

    /**
     * Build a {@link RegionConfig} for a {@code region_*} zone (or any zone carrying RegionConfig metas).
     * All tunables are scalars/strings → a clean glTF {@code extras} round-trip. Returns null when the
     * zone is a plain {@code zone_*} with no region metadata (a no-op RegionConfig would still apply
     * defaults, so leave it null to mean "no region").
     */
    private static RegionConfig buildRegion(Node3D empty) {
        boolean isRegion = empty.getName().toString().startsWith("region_");
        boolean hasMeta = hasAnyMeta(empty, "region_name", "ambient_ai_density", "vehicle_density",
                "ai_lod_bias", "light_temperature", "fog_density", "faction_table");
        if (!isRegion && !hasMeta) return null;
        RegionConfig rc = new RegionConfig();
        rc.regionName       = metaString(empty, "region_name", idOf(empty.getName().toString()));
        rc.ambientAiDensity = metaFloat(empty, "ambient_ai_density", 1.0f);
        rc.vehicleDensity   = metaFloat(empty, "vehicle_density", 1.0f);
        rc.aiLodBias        = metaFloat(empty, "ai_lod_bias", 1.0f);
        rc.lightTemperature = metaFloat(empty, "light_temperature", 0.0f);
        rc.fogDensity       = metaFloat(empty, "fog_density", 0.0f);
        String tablePath = metaString(empty, "faction_table", "");
        if (!tablePath.isEmpty() && GD.load(tablePath) instanceof com.openworld.character.FactionTable ft)
            rc.factionTable = ft;
        return rc;
    }

    private static void attachSpawn(List<ZoneMarker> markers, Node3D empty) {
        ZoneMarker nearest = nearestMarker(markers, empty.getGlobalPosition());
        if (nearest == null || nearest.zone == null) return;
        SpawnConfig cfg = new SpawnConfig();
        cfg.faction = factionOf(empty.getName().toString());
        cfg.count = (int) metaFloat(empty, "count", 3f);
        nearest.zone.spawnConfigs.add(cfg);
    }

    /**
     * {@code water_<id>} → a {@link WaterVolume}, which is the whole of "the world has water in it":
     * any {@code Character} overlapping it swims or wades, and {@code Boat} floats to its surface.
     *
     * <p>IT IS A {@code WaterVolume}, NOT A BARE {@code Area3D}, AND ITS MASK IS SET. Both halves
     * were missing and each was silently fatal on its own: a scriptless area has no
     * {@code body_entered} handler, so nothing ever called {@code Character.setInWater}; and an
     * area left on the default mask (layer 1, world) never sees a character body at all, since
     * characters are on {@code CollisionLayers.CHARACTER}. The swim system — {@code SwimState},
     * the buoyancy spring, the depth-based wade-vs-swim decision — has been complete since I1 and
     * had no world to run in. Same shape as the {@code -noped} gap in {@code NavBaker}: an
     * authoring convention whose runtime half was never built.
     *
     * <p>The area's own layer is cleared: it detects, and nothing needs to detect it.
     */
    private static void buildWater(Node root, Node3D empty) {
        WaterVolume area = new WaterVolume();
        area.setName(new StringName("Water_" + idOf(empty.getName().toString())));
        area.setCollisionLayer(0L);
        area.setCollisionMask(com.openworld.util.CollisionLayers.CHARACTER);
        CollisionShape3D cs = new CollisionShape3D();
        BoxShape3D box = new BoxShape3D();
        box.setSize(metaVec3(empty, "size", new Vector3(10f, 4f, 10f)));
        cs.setShape(box);
        area.addChild(cs);
        root.addChild(area);
        area.setGlobalPosition(empty.getGlobalPosition());
        area.addToGroup(new StringName("water"), true);
    }

    /**
     * {@code bounds_<id>} → {@link WorldBounds}, the logic wall. {@code size} is the playable
     * square (x and z; the y is ignored) and {@code floor} the kill-Z.
     *
     * <p>A marker rather than a constant somewhere, for the same reason a zone is: a different
     * world has a different edge, and the edge belongs with the world data that defines it.
     */
    private static void buildBounds(Node root, Node3D empty) {
        WorldBounds bounds = new WorldBounds();
        bounds.setName(new StringName("WorldBounds"));
        Vector3 size = metaVec3(empty, "size", new Vector3(4032f, 4000f, 4032f));
        bounds.setHalfExtent((float) (Math.min(size.getX(), size.getZ()) * 0.5));
        bounds.setFloorY(metaFloat(empty, "floor", -64f));
        bounds.setSoftMargin(metaFloat(empty, "soft_margin", 80f));
        root.addChild(bounds);
        bounds.setGlobalPosition(empty.getGlobalPosition());
    }

    private static void buildJunction(Node root, Node3D empty) {
        IntersectionZone zone = new IntersectionZone();
        zone.setName(new StringName("Intersection_" + idOf(empty.getName().toString())));
        CollisionShape3D cs = new CollisionShape3D();
        BoxShape3D box = new BoxShape3D();
        box.setSize(metaVec3(empty, "size", new Vector3(7f, 4f, 7f)));
        cs.setShape(box);
        zone.addChild(cs);
        root.addChild(zone);
        zone.setGlobalPosition(empty.getGlobalPosition());
    }

    // ── Helpers ─────────────────────────────────────────────────────────────────

    private static void collectNode3D(Node node, List<Node3D> out) {
        if (node instanceof Node3D n3) out.add(n3);
        for (Node child : node.getChildren()) collectNode3D(child, out);
    }

    /**
     * Set owner = scene root on every descendant (required for pack() to include it), but **stop at
     * instance roots**: own the instance root itself, then leave its internal nodes owned by the
     * instance (as {@code instantiate()} set them) so {@code pack()} stores an {@code instance=}
     * reference rather than inlining the instanced subtree's geometry.
     */
    private static void setOwnerRecursive(Node node, Node owner, Set<Node> instanceRoots) {
        for (Node child : node.getChildren()) {
            child.setOwner(owner);
            if (instanceRoots.contains(child)) continue;   // preserve the instance — don't re-own internals
            setOwnerRecursive(child, owner, instanceRoots);
        }
    }

    private static void freeEmpty(Node3D empty) {
        if (GD.isInstanceValid(empty)) { empty.getParent().removeChild(empty); empty.queueFree(); }
    }

    private static ZoneMarker nearestMarker(List<ZoneMarker> markers, Vector3 pos) {
        ZoneMarker best = null;
        double bestD = Double.MAX_VALUE;
        for (ZoneMarker m : markers) {
            Vector3 p = m.getGlobalPosition();
            double dx = p.getX() - pos.getX(), dz = p.getZ() - pos.getZ();
            double d = dx * dx + dz * dz;
            if (d < bestD) { bestD = d; best = m; }
        }
        return best;
    }

    // name parsing: "<prefix>_<body>[_<n>]"
    private static String afterPrefix(String name) { int i = name.indexOf('_'); return i < 0 ? name : name.substring(i + 1); }
    private static String idOf(String name)         { return afterPrefix(name); }
    /** route part of lane_<route>_<n> (body minus the trailing _<n>). */
    private static String routeOf(String name) {
        String body = afterPrefix(name);
        int u = body.lastIndexOf('_');
        return (u > 0 && isInt(body.substring(u + 1))) ? body.substring(0, u) : body;
    }
    private static String factionOf(String name) {   // spawn_<faction>_<n>
        String body = afterPrefix(name);
        int u = body.lastIndexOf('_');
        return (u > 0 && isInt(body.substring(u + 1))) ? body.substring(0, u) : body;
    }
    private static int indexOf(String name) {
        int u = name.lastIndexOf('_');
        return (u >= 0 && isInt(name.substring(u + 1))) ? Integer.parseInt(name.substring(u + 1)) : 0;
    }
    private static boolean isInt(String s) { try { Integer.parseInt(s); return true; } catch (Exception e) { return false; } }

    // metadata accessors (Blender custom properties → node metadata), with defaults.

    /**
     * Resolve a marker parameter by key, supporting BOTH source shapes so one baker handles
     * native and Blender-authored sources:
     * <ul>
     *   <li>a native {@code .tscn} marker stores each param as its own node meta ({@code metadata/size});</li>
     *   <li>a Blender→glTF import stores all of a node's Custom Properties together under a single
     *       {@code "extras"} Dictionary meta — Godot's glTF importer does <b>not</b> split them into
     *       per-key metas (verified on 4.6). A per-key lookup must therefore fall back to that dict,
     *       or every glTF-authored param silently defaults (the world-master bake bug).</li>
     * </ul>
     * Returns {@code null} when the key is absent in both.
     */
    private static Object metaRaw(Node3D n, String key) {
        StringName k = new StringName(key);
        if (n.hasMeta(k)) return n.getMeta(k);
        if (n.hasMeta(EXTRAS) && n.getMeta(EXTRAS) instanceof Dictionary<?, ?> d) {
            Object v = d.get(key);
            if (present(v)) return v;
        }
        return null;
    }
    private static final StringName EXTRAS = new StringName("extras");
    /** Godot's untyped Dictionary.get() returns {@code kotlin.Unit} (not null) for a missing key. */
    private static boolean present(Object v) {
        return v != null && !"kotlin.Unit".equals(v.getClass().getName());
    }
    private static boolean hasMetaKey(Node3D n, String key) { return metaRaw(n, key) != null; }

    private static boolean metaBool(Node3D n, String key, boolean def) {
        Object v = metaRaw(n, key);
        if (v == null) return def;
        try { return (Boolean) v; } catch (Exception e) { return def; }
    }
    private static float metaFloat(Node3D n, String key, float def) {
        Object v = metaRaw(n, key);
        if (v == null) return def;
        try { return ((Number) v).floatValue(); } catch (Exception e) { return def; }
    }
    private static String metaString(Node3D n, String key, String def) {
        Object v = metaRaw(n, key);
        if (v == null) return def;
        try { return v.toString(); } catch (Exception e) { return def; }
    }
    private static boolean hasAnyMeta(Node3D n, String... keys) {
        for (String key : keys) if (hasMetaKey(n, key)) return true;
        return false;
    }
    private static Vector3 metaVec3(Node3D n, String key, Vector3 def) {
        Object v = metaRaw(n, key);
        if (v == null) return def;
        if (v instanceof Vector3 vec) return vec;
        // A JSON array `[x, y, z]` (glTF extras / .tscn Array) imports as a Godot Array, not a Vector3.
        if (v instanceof VariantArray<?> arr && arr.size() >= 3) {
            try {
                return new Vector3(((Number) arr.get(0)).doubleValue(),
                        ((Number) arr.get(1)).doubleValue(), ((Number) arr.get(2)).doubleValue());
            } catch (Exception e) { return def; }
        }
        return def;
    }
}

package com.openworld.debug;

import com.openworld.world.PathLaneRoute;
import com.openworld.world.WorldBaker;
import com.openworld.world.ZoneManager;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.PackedScene;
import godot.global.GD;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * Headless regression smoke test for P6.4 (road_blender_godot.md Phase 6): bakes a MULTI-piece
 * combined {@code .lanekit.json} (produced by {@code tools/save_lane_kit.py}, not a single
 * piece's own {@code export_*_json}) via {@link WorldBaker} and confirms every built
 * {@link PathLaneRoute} carries the {@code zoneId} its sidecar entry was tagged with — the
 * property-based zone tag {@code lib/lane_kit.py:combine_pieces} stamps on every lane, consumed
 * by {@link ZoneManager#registerRoute}/the new zone-id-equality path in its (private)
 * {@code findRoute}. Unlike {@link PathLaneRouteTestHost} (one hand-built junction, driving
 * behavior), this test is about the COMBINER pipeline: many pieces, one sidecar, one zone. Run
 * with:
 *
 *   godot --headless res://src/main/resources/com/openworld/debug/LaneKitCombineTest.tscn
 *
 * Grep for "LKCTEST verdict" (PASS iff at least one PathLaneRoute is built, every one carries the
 * expected non-empty zoneId, and {@link ZoneManager#getRoutes()} reflects the same count —
 * i.e. every baked lane actually registered).
 *
 * {@link #lanekitPath}/{@link #expectedZoneId} are {@code @Export} (overridable per-scene, e.g.
 * for a different district's {@code .lanekit.json}) — default to the debug_road fixture this
 * test originally shipped against, so the existing {@code LaneKitCombineTest.tscn} is unaffected.
 */
@Script(className = "LaneKitCombineTestHost")
public class LaneKitCombineTestHost extends Node3D {

    private static final String SRC = "res://src/main/resources/com/openworld/debug/EmptyBakeSource.tscn";
    private static final String OUT = "res://src/main/resources/com/openworld/debug/LaneKitCombineBaked.tscn";
    // The user-designated AI-drive test fixture (road_blender_godot.md Phase 6) — a small
    // connected network (multiple intersections/segments/one transition), combined by
    // tools/save_lane_kit.py into one sidecar, exactly the multi-piece case P6.4 added.
    @Export
    public String lanekitPath =
            "/data/danilko/git/third-person-shooter/assets/world_source/debug_road.lanekit.json";
    @Export
    public String expectedZoneId = "debug_road";

    @Register
    @Override
    public void _ready() {
        WorldBaker.bakeScene(this, SRC, OUT, "res://src/main/resources/com/openworld/world/kit/", lanekitPath);

        java.lang.Object loaded = GD.load(OUT);
        if (!(loaded instanceof PackedScene packed)) { GD.printErr("LKCTEST: bake output missing"); finish(false); return; }
        Node baked = packed.instantiate();
        if (baked == null) { GD.printErr("LKCTEST: instantiate failed"); finish(false); return; }
        addChild(baked);

        Map<String, Integer> zoneCounts = new HashMap<>();
        Set<PathLaneRoute> found = new HashSet<>();
        collectLanes(baked, found);
        for (PathLaneRoute p : found) zoneCounts.merge(p.zoneId, 1, Integer::sum);

        GD.print("LKCTEST baked PathLaneRoute count=" + found.size());
        GD.print("LKCTEST zoneId histogram=" + zoneCounts);

        boolean nonEmpty = !found.isEmpty();
        boolean allExpectedZone = found.stream().allMatch(p -> expectedZoneId.equals(p.zoneId));

        ZoneManager mgr = ZoneManager.get();
        int registered = mgr != null ? mgr.getRoutes().size() : -1;
        GD.print("LKCTEST ZoneManager.getRoutes() size=" + registered);
        boolean allRegistered = mgr != null && registered == found.size();

        GD.print(String.format("LKCTEST SUMMARY nonEmpty=%s allExpectedZone=%s allRegistered=%s",
                nonEmpty, allExpectedZone, allRegistered));
        finish(nonEmpty && allExpectedZone && allRegistered);
    }

    private void collectLanes(Node n, Set<PathLaneRoute> out) {
        if (n instanceof PathLaneRoute p) out.add(p);
        for (Node c : n.getChildren()) collectLanes(c, out);
    }

    private void finish(boolean pass) {
        GD.print("LKCTEST verdict=" + (pass ? "PASS" : "CHECK"));
        if (getTree() != null) getTree().quit();
    }
}

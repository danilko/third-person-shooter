package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.annotation.Tool;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.PackedScene;
import godot.api.ResourceLoader;
import godot.api.ResourceSaver;
import godot.core.StringName;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * P6.10 (road_blender_godot.md Phase 6) — offline `@Tool` batch utility (same
 * {@code bakeOnReady}/{@code quitWhenDone} host-scene idiom as {@link WorldBaker}/{@link NavBaker}/
 * {@link PieceBinaryConverter}) that assembles a STATIC, non-streamed preview scene from a
 * chosen SET of already-built districts, each placed at the exact world position its own
 * {@link ZoneMarker} in the real master carries — answers "can't see districts assembled
 * together in the Godot editor" (confirmed real: {@code hosts/WorldMaster.tscn} has zero static
 * {@code District_*} references; every district is 100% runtime-streamed, so simply opening that
 * scene in the editor shows nothing but region markers).
 *
 * <p><b>Deliberately selective, not "load all 36 at once"</b> (revised scope, user follow-up
 * 2026-07-27 — see road_blender_godot.md): {@link #districtStems} is a comma-separated allow-list
 * (blank = every district the master has a zone marker for, for the rare case you really do want
 * everything). Pass just the district(s) you're actively debugging — the output is a small,
 * fast-to-open scene instead of the full map.
 *
 * <p><b>Positions are read from the real baked master, never recomputed</b> — {@link
 * #masterScenePath} (default {@code World_master.tscn}, already built by {@code
 * tools/build_world.sh}) is instantiated once, its {@link ZoneMarker} children give
 * {@code (zoneId, geometryPath, globalPosition)} directly (the exact same numbers
 * {@link ZoneManager} streams districts at in the real game — zero risk of a hand-rederived
 * position drifting from {@code lib/world_grid.py}'s own math, since none is rederived here).
 *
 * <p>Each selected district's own already-baked {@code .tscn} (full detail — buildings, roads,
 * collision, the same file that streams in-game) is instanced as a PLAIN child at that position —
 * no {@link ZoneMarker}/{@link Zone} wrapper, no streaming machinery, nothing that would
 * make {@link ZoneManager} try to load/unload it. Genuinely static: open the output scene in
 * the editor and the districts are just there.
 *
 * <p>Run with:
 *   {@code godot --headless res://src/main/resources/com/openworld/world/hosts/BuildWorldPreview.tscn}
 * or set {@link #districtStems}/{@link #outputScenePath} on that host's node in the editor and
 * press the {@code buildPreview} button (via the Inspector's "Call method" / a dev key) for an
 * in-editor run — the {@code @Tool} annotation lets this class run inside the editor process too,
 * not just headless.
 */
@Tool
@Script(className = "WorldPreviewBuilder")
public class WorldPreviewBuilder extends Node {

    @Export public String masterScenePath =
            "res://src/main/resources/com/openworld/world/master/World_master.tscn";

    /** Comma-separated district stems to include (e.g. "District_city_1_1,District_industry_5_1")
     *  — blank means every district the master has a zone marker for. */
    @Export public String districtStems = "";

    @Export public String outputScenePath =
            "res://src/main/resources/com/openworld/world/WorldPreview.tscn";

    /** Build automatically when this node enters the tree (for a dedicated batch-build host). */
    @Export public boolean bakeOnReady = false;

    /** After an auto-build (the {@link #bakeOnReady} path only), quit the process — same
     *  one-shot-CLI-batch-job idiom as {@link WorldBaker#quitWhenDone}. */
    @Export public boolean quitWhenDone = false;

    @Register
    @Override
    public void _ready() {
        if (bakeOnReady) {
            buildPreview();
            if (quitWhenDone) getTree().quit();   // ResourceSaver.save is synchronous -> file is on disk
        }
    }

    /** Build {@link #outputScenePath} from {@link #masterScenePath}'s zone markers, filtered to
     *  {@link #districtStems} (callable from a dev key / editor Inspector button, mirroring
     *  {@link WorldBaker#bake()}). */
    @Register
    public void buildPreview() {
        buildPreviewScene(this, masterScenePath, districtStems, outputScenePath);
    }

    /**
     * @param host   must be in the tree — the master is parented to it so global transforms
     *               resolve, mirroring {@link WorldBaker#bakeScene(Node, String, String, String, String)}.
     * @param stemsCsv comma-separated allow-list; blank = every zone marker found.
     */
    public static void buildPreviewScene(Node host, String masterPath, String stemsCsv, String outPath) {
        Object loaded = GD.load(masterPath);
        if (!(loaded instanceof PackedScene masterPacked)) {
            GD.printErr("WorldPreviewBuilder: could not load master scene '" + masterPath + "'");
            return;
        }
        Node masterRoot = masterPacked.instantiate();
        if (masterRoot == null) { GD.printErr("WorldPreviewBuilder: master instantiate failed"); return; }
        host.addChild(masterRoot);   // in-tree -> getGlobalPosition() resolves during collection

        Set<String> allow = new LinkedHashSet<>();
        if (stemsCsv != null) {
            for (String s : stemsCsv.split(",")) {
                String t = s.trim();
                if (!t.isEmpty()) allow.add(t);
            }
        }

        List<ZoneMarker> markers = new ArrayList<>();
        collectZoneMarkers(masterRoot, markers);
        markers.sort(java.util.Comparator.comparing(m -> m.zone != null ? m.zone.zoneId : ""));

        Node3D previewRoot = new Node3D();
        previewRoot.setName(new StringName("WorldPreview"));
        host.addChild(previewRoot);   // in-tree BEFORE any setGlobalPosition() call below, same
                                        // reason WorldBaker keeps its own root in-tree throughout
        int included = 0, skippedMissing = 0, skippedFilter = 0;
        for (ZoneMarker marker : markers) {
            Zone zone = marker.zone;
            if (zone == null || zone.zoneId == null || zone.zoneId.isEmpty()) continue;
            if (!allow.isEmpty() && !allow.contains(zone.zoneId)) { skippedFilter++; continue; }
            if (zone.geometryPath == null || zone.geometryPath.isEmpty()) { skippedMissing++; continue; }
            if (!ResourceLoader.INSTANCE.exists(zone.geometryPath, "")) {
                GD.print("WorldPreviewBuilder: skipping '" + zone.zoneId + "' -- geometry not "
                        + "built yet (" + zone.geometryPath + ")");
                skippedMissing++;
                continue;
            }
            Object geoLoaded = GD.load(zone.geometryPath);
            if (!(geoLoaded instanceof PackedScene geoPacked)) {
                GD.printErr("WorldPreviewBuilder: '" + zone.geometryPath + "' did not load as a PackedScene");
                skippedMissing++;
                continue;
            }
            Node instance = geoPacked.instantiate();
            if (instance == null) { skippedMissing++; continue; }
            instance.setName(new StringName(zone.zoneId));
            previewRoot.addChild(instance);
            if (instance instanceof Node3D n3d) n3d.setGlobalPosition(marker.getGlobalPosition());
            // Owner stamped on the INSTANCE ROOT ONLY, its own internals left untouched -- same
            // technique WorldBaker's own `instanceRoots` exclusion uses (see its class javadoc):
            // owning every descendant too would make pack() FLATTEN each district's full content
            // (every building/road mesh individually) into this preview file instead of recording
            // a lightweight `instance=` PackedScene reference -- confirmed directly (an earlier
            // version of this method did recurse, and the output file listed every single
            // MeshInstance3D of every included district by name instead of one instance line per
            // district). A disposable preview scene has even less reason to pay that cost than
            // WorldBaker's real per-district bakes do.
            instance.setOwner(previewRoot);
            included++;
        }

        PackedScene packed = new PackedScene();
        godot.core.Error err = packed.pack(previewRoot);
        if (err == godot.core.Error.OK) {
            godot.core.Error save = ResourceSaver.save(packed, outPath, ResourceSaver.SaverFlags.FLAG_NONE);
            GD.print("WorldPreviewBuilder: built '" + outPath + "' (" + (save == godot.core.Error.OK
                    ? "saved" : "save FAILED " + save) + ") -- included=" + included
                    + " skipped(filter)=" + skippedFilter + " skipped(missing)=" + skippedMissing);
        } else {
            GD.printErr("WorldPreviewBuilder: pack() failed: " + err);
        }
        host.removeChild(previewRoot);
        previewRoot.queueFree();
        host.removeChild(masterRoot);
        masterRoot.queueFree();
    }

    private static void collectZoneMarkers(Node node, List<ZoneMarker> out) {
        if (node instanceof ZoneMarker marker) out.add(marker);
        for (Node child : node.getChildren()) collectZoneMarkers(child, out);
    }
}

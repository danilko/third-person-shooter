package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.DirAccess;
import godot.api.Node;
import godot.api.PackedScene;
import godot.api.ProjectSettings;
import godot.api.ResourceSaver;
import godot.core.Error;
import godot.global.GD;

import java.io.File;

/**
 * One-shot batch tool: resave every baked district {@code .tscn} in {@link #piecesDir} as a
 * sibling binary {@code .scn}. The baked districts are multi-MB <b>text</b> scenes (base64-inlined
 * {@code ArrayMesh}/collider data) whose parse dominates stream-in time; the binary variant of the
 * same scene loads several times faster and is what {@code ZoneManager.resolveGeometryPath}
 * prefers when it exists — no master re-bake, no {@code geometry_path} edits needed. The
 * {@code .tscn} stays the git-diffable source of truth; the {@code .scn} is a derived artifact,
 * safe to delete and regenerate at any time.
 *
 * <p>Mirrors {@link WorldBaker}'s one-shot-scene pattern ({@code hosts/ConvertPieces.tscn}):
 * <pre>
 *   &lt;godot-jvm-binary&gt; --path &lt;root&gt; res://src/main/resources/com/openworld/world/hosts/ConvertPieces.tscn
 * </pre>
 * Run it <b>windowed</b> (or under {@code xvfb-run}), not {@code --headless} — district pieces can
 * carry {@code MultiMeshInstance3D} bulk, and the headless dummy renderer drops MultiMesh transform
 * buffers on the load/save round-trip (same caveat as baking, see {@code WorldBaker.buildMultiMeshes}).
 * Re-run after any district re-bake; unchanged districts are skipped by file mtime.
 */
@Script(className = "PieceBinaryConverter")
public class PieceBinaryConverter extends Node {

    @Export public String piecesDir =
            "res://src/main/resources/com/openworld/world/pieces/";
    /** Convert automatically when this node enters the tree (for the ConvertPieces scene). */
    @Export public boolean convertOnReady = false;
    /** After an auto-convert, quit the process (one-shot CLI batch job — WorldBaker idiom). */
    @Export public boolean quitWhenDone = false;

    @Register
    @Override
    public void _ready() {
        if (convertOnReady) {
            convertAll();
            if (quitWhenDone) getTree().quit();   // ResourceSaver.save is synchronous → files are on disk
        }
    }

    /** Convert every {@code *.tscn} in {@link #piecesDir} whose {@code .scn} is missing or stale. */
    @Register
    public void convertAll() {
        DirAccess dir = DirAccess.open(piecesDir);
        if (dir == null) {
            GD.printErr("PieceBinaryConverter: cannot open '" + piecesDir + "'");
            return;
        }
        String base = piecesDir.endsWith("/") ? piecesDir : piecesDir + "/";
        int converted = 0, skipped = 0, failed = 0;
        dir.listDirBegin();
        for (String f = dir.getNext(); f != null && !f.isEmpty(); f = dir.getNext()) {
            if (dir.currentIsDir() || !f.endsWith(".tscn")) continue;
            String src = base + f;
            String dst = base + f.substring(0, f.length() - ".tscn".length()) + ".scn";
            // mtime skip via the real filesystem (this runs in-project, so res:// globalizes cleanly).
            File srcFile = new File(ProjectSettings.globalizePath(src));
            File dstFile = new File(ProjectSettings.globalizePath(dst));
            if (dstFile.exists() && srcFile.exists() && dstFile.lastModified() >= srcFile.lastModified()) {
                skipped++;
                continue;
            }
            if (!(GD.load(src) instanceof PackedScene ps)) {
                GD.printErr("PieceBinaryConverter: load failed, skipping '" + src + "'");
                failed++;
                continue;
            }
            Error err = ResourceSaver.save(ps, dst, ResourceSaver.SaverFlags.FLAG_NONE);
            if (err == Error.OK) {
                converted++;
                GD.print("PieceBinaryConverter: " + f + " → " + dst.substring(base.length()));
            } else {
                GD.printErr("PieceBinaryConverter: save FAILED for '" + dst + "': " + err);
                failed++;
            }
        }
        dir.listDirEnd();
        GD.print("PieceBinaryConverter: done — " + converted + " converted, " + skipped
                + " up-to-date, " + failed + " failed");
    }
}

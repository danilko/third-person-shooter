package com.openworld.debug;

import com.openworld.world.Zone;
import com.openworld.world.ZoneManager;
import com.openworld.world.ZoneMarker;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.*;
import godot.core.Dictionary;
import godot.core.RID;
import godot.core.StringName;
import godot.core.Vector3;
import godot.core.VariantArray;
import godot.global.GD;

/**
 * One-shot diagnostic (NOT a permanent regression test) for the user-reported "character falls
 * through the ground in District_industry_5_1 after F1/manual teleport" bug. Streams the real
 * district in at its true master-world marker position (1260, 0, 756) — read directly out of
 * World_master.tscn, not guessed — then raycasts straight down from where
 * {@code DebugHarness.teleportToNextZone()} (F1) actually lands the player (marker position + 3m
 * Y) to find out exactly what, if anything, is under it.
 *
 * Run with:
 *   godot --headless res://src/main/resources/com/openworld/debug/F1LandingDiagnostic.tscn
 *
 * Grep for "F1DIAG".
 */
@Script(className = "F1LandingDiagnosticHost")
public class F1LandingDiagnosticHost extends Node3D {

    private static final String DISTRICT =
            "res://src/main/resources/com/openworld/world/pieces/District_industry_5_1.tscn";
    private static final Vector3 MARKER_POS = new Vector3(1260f, 0f, 756f);   // from World_master.tscn
    private static final Vector3 F1_LANDING = new Vector3(1260f, 3f, 756f);    // marker + (0,3,0)

    private ZoneMarker marker;
    private double timer = 0.0;
    private boolean raycastDone = false;
    private com.openworld.character.Player player;
    private double minYSeenEarly = Double.MAX_VALUE;
    private boolean loggedFirstFrame = false;

    @Register
    @Override
    public void _ready() {
        Zone zone = new Zone();
        zone.zoneId = "District_industry_5_1";
        zone.geometryPath = DISTRICT;
        zone.size = new Vector3(504f, 40f, 504f);
        zone.loadRadius = 402f;
        zone.unloadRadius = 552f;

        marker = new ZoneMarker();
        marker.setName(new StringName("ZoneMarker_District_industry_5_1"));
        marker.zone = zone;
        addChild(marker);
        marker.setGlobalPosition(MARKER_POS);

        // No Player needed for a raycast-only diagnostic -- but ZoneManager's streaming
        // distance check is nearest-PLAYER-based (PlayerRegistry), so without one nothing ever
        // streams in. Spawn one and teleport it to the F1 landing point directly, same as the
        // real bug repro, so this also mirrors "does physics actually catch the fall" not just
        // "is there a collider directly under the drop point".
        java.lang.Object res = ResourceLoader.INSTANCE.load(
                "res://src/main/resources/com/openworld/character/Player.tscn", "",
                ResourceLoader.CacheMode.REUSE);
        if (res instanceof PackedScene packed
                && packed.instantiate() instanceof com.openworld.character.Player p) {
            player = p;
            addChild(p);
            p.setGlobalPosition(F1_LANDING);
            GD.print("F1DIAG: player spawned at F1 landing point " + F1_LANDING
                    + " -- SAME FRAME the zone starts streaming (the real F1/teleport race, not "
                    + "a settled-scene check)");
        } else {
            GD.printErr("F1DIAG: could not spawn Player");
        }

        GD.print("F1DIAG: zone marker placed at " + MARKER_POS + " (matches World_master.tscn)");
    }

    @Register
    @Override
    public void _physicsProcess(double delta) {
        timer += delta;

        // Per-frame trace for the first 3 seconds -- this is the actual race window: does the
        // player free-fall meaningfully before the zone's incrementally-streamed geometry
        // (GEO_ENTER, budget-sliced) finishes entering the tree? A settled 6-8s check (below)
        // would never see this even if it happens.
        if (player != null && timer <= 3.0) {
            double y = player.getGlobalPosition().getY();
            if (y < minYSeenEarly) minYSeenEarly = y;
            if (!loggedFirstFrame) {
                loggedFirstFrame = true;
                GD.print("F1DIAG: frame 1 marker childCount=" + marker.getChildCount());
            }
            if (((int) (timer * 20)) % 4 == 0) {   // every ~0.2s
                GD.print(String.format("F1DIAG: t=%.2f markerChildCount=%d playerY=%.3f",
                        timer, marker.getChildCount(), y));
            }
        }

        // Once the zone has had time to stream in (ZoneManager eval tick is 0.5s, plus the
        // GEO_ENTER budget-sliced entry), raycast straight down from well above the F1 landing
        // point and report exactly what (if anything) is hit.
        if (!raycastDone && timer >= 6.0) {
            raycastDone = true;
            World3D world = getWorld3d();
            PhysicsDirectSpaceState3D space = world != null ? world.getDirectSpaceState() : null;
            if (space == null) {
                GD.printErr("F1DIAG: no PhysicsDirectSpaceState3D available");
            } else {
                Vector3 from = new Vector3(F1_LANDING.getX(), 500f, F1_LANDING.getZ());
                Vector3 to = new Vector3(F1_LANDING.getX(), -500f, F1_LANDING.getZ());
                VariantArray<RID> exclude = new VariantArray<>(RID.class);
                PhysicsRayQueryParameters3D q =
                        PhysicsRayQueryParameters3D.Companion.create(from, to, 1L, exclude);
                Dictionary<java.lang.Object, java.lang.Object> hit = space.intersectRay(q);
                if (hit == null || hit.isEmpty()) {
                    GD.print("F1DIAG: RAYCAST FOUND NOTHING under (" + F1_LANDING.getX() + ", *, "
                            + F1_LANDING.getZ() + ") from Y=500 to Y=-500 -- CONFIRMS free-fall (no "
                            + "collider anywhere in that column across a 1000m span, layer 1).");
                } else {
                    java.lang.Object collider = hit.get("collider");
                    java.lang.Object position = hit.get("position");
                    GD.print("F1DIAG: RAYCAST HIT collider=" + collider + " at position=" + position);
                }
            }
        }

        if (timer >= 8.0) {
            // Also report the player's own settled position/velocity for a second data point.
            for (com.openworld.character.Player p : com.openworld.game.PlayerRegistry.getPlayers()) {
                GD.print("F1DIAG: player position after 8s of physics = " + p.getGlobalPosition());
            }
            GD.print("F1DIAG: minimum Y seen in the first 3s (the race window) = " + minYSeenEarly);
            GD.print("F1DIAG done");
            if (getTree() != null) getTree().quit();
        }
    }
}

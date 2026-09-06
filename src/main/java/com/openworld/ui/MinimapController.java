package com.openworld.ui;

import com.openworld.character.Character;
import com.openworld.character.NameplateTarget;
import com.openworld.character.Player;
import com.openworld.game.PlayerRegistry;
import com.openworld.game.WaypointStore;
import com.openworld.world.SpatialEntityGrid;
import com.openworld.world.ZoneManager;
import com.openworld.world.ZoneMarker;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Camera3D;
import godot.api.Control;
import godot.api.Node;
import godot.api.Node3D;
import godot.core.Basis;
import godot.core.Color;
import godot.core.PackedVector2Array;
import godot.core.Vector2;
import godot.core.Vector3;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Always-on corner minimap (PLAN.md I5) — a procedural radar, not a rendered camera view. Each frame it
 * draws, north-up and centred on the local player: nearby Characters/Vehicles from {@link SpatialEntityGrid}
 * (D1) as faction-coloured blips ({@link NameplateTarget#getNameplateColor()}), the local player as a
 * heading triangle (facing = the viewport camera's forward, correct on foot and in a vehicle), zone/region
 * outlines from {@link ZoneManager}, and GPS waypoints from {@link WaypointStore} (local + teammates,
 * clamped to the rim so the destination is always visible). Pure display — no input, no game-state writes.
 *
 * <p>Wired by {@code HUDManager.wirePlayer} (ownership-gated) like {@code WeaponProgress}; not table-managed.
 */
@Script(className = "MinimapController")
public class MinimapController extends Control {

    /** World metres from the player edge-to-centre shown on the radar. */
    @Export public float rangeMeters = 60f;
    /** Blip radius in px. */
    @Export public float blipRadius = 3f;
    /** Background disc colour. */
    @Export public Color backgroundColor = new Color(0f, 0f, 0f, 0.45f);
    /** Local-player heading-triangle colour. */
    @Export public Color selfColor = new Color(1f, 1f, 1f, 1f);
    /** Zone/region outline colour. */
    @Export public Color regionColor = new Color(0.4f, 0.8f, 1f, 0.5f);

    private Character player;

    /** Bind to the local player (called by HUDManager.wirePlayer). */
    public void wirePlayer(Player p) { player = p; }

    @Register
    @Override
    public void _ready() {
        setMouseFilter(Control.MouseFilter.IGNORE);
    }

    @Register
    @Override
    public void _process(double delta) {
        queueRedraw();   // redraw every frame; cheap (a few dozen shapes)
    }

    @Register
    @Override
    public void _draw() {
        Vector2 size = getSize();
        float cx = (float) size.getX() * 0.5f;
        float cy = (float) size.getY() * 0.5f;
        Vector2 center = new Vector2(cx, cy);
        float radiusPx = Math.min(cx, cy);
        float scale = radiusPx / rangeMeters;   // px per metre

        drawCircle(center, radiusPx, backgroundColor, true, -1f, true);

        if (player == null || !godot.global.GD.isInstanceValid(player)) return;
        Vector3 origin = player.getGlobalPosition();

        // Region outlines (zone load rings) within view.
        ZoneManager wzm = ZoneManager.get();
        if (wzm != null) {
            for (ZoneMarker m : wzm.getMarkers()) {
                if (m == null || !godot.global.GD.isInstanceValid(m) || m.zone == null) continue;
                Vector2 c = worldToScreen(m.getGlobalPosition(), origin, center, scale);
                float rr = m.zone.loadRadius * scale;
                if (distance(c, center) - rr <= radiusPx) drawCircle(c, rr, regionColor, false, 1f, true);
            }
        }

        // Nearby entities as faction-coloured blips (skip self; skip out-of-range).
        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) {
            List<Node> near = new ArrayList<>();
            grid.queryRadius(origin, rangeMeters, near);
            for (Node n : near) {
                if (n == player || !(n instanceof Node3D n3) || !(n instanceof NameplateTarget nt)) continue;
                Vector2 c = worldToScreen(n3.getGlobalPosition(), origin, center, scale);
                if (distance(c, center) > radiusPx) continue;
                drawCircle(c, blipRadius, nt.getNameplateColor(), true, -1f, true);
            }
        }

        // GPS waypoints (local + teammates), clamped to the rim so off-range targets still show direction.
        for (Map.Entry<String, Vector3> e : WaypointStore.entries().entrySet()) {
            Color col = waypointColor(e.getKey());
            Vector2 c = clampToDisc(worldToScreen(e.getValue(), origin, center, scale), center, radiusPx - 2f);
            drawWaypoint(c, col);
        }

        // Local player heading triangle (camera forward projected to XZ).
        drawHeading(center, headingScreenDir());
    }

    // ── helpers ────────────────────────────────────────────────────────────────

    /** North-up world→screen: +X world = right, +Z world = down (so −Z/north points up). */
    private Vector2 worldToScreen(Vector3 world, Vector3 origin, Vector2 center, float scale) {
        float dx = (float) (world.getX() - origin.getX()) * scale;
        float dz = (float) (world.getZ() - origin.getZ()) * scale;
        return new Vector2((float) center.getX() + dx, (float) center.getY() + dz);
    }

    /** Camera-forward XZ as a screen-space unit vector (matches the north-up mapping); (0,-1) if unknown. */
    private Vector2 headingScreenDir() {
        Camera3D cam = getViewport() != null ? getViewport().getCamera3d() : null;
        if (cam == null) return new Vector2(0f, -1f);
        Basis b = cam.getGlobalBasis();
        Vector3 fwd = b.getZ().times(-1f);
        Vector2 d = new Vector2((float) fwd.getX(), (float) fwd.getZ());
        float len = (float) Math.sqrt(d.getX() * d.getX() + d.getY() * d.getY());
        return len < 1e-3f ? new Vector2(0f, -1f) : new Vector2((float) d.getX() / len, (float) d.getY() / len);
    }

    private void drawHeading(Vector2 center, Vector2 dir) {
        float s = 7f;
        Vector2 perp = new Vector2(-(float) dir.getY(), (float) dir.getX());
        Vector2 tip  = add(center, mul(dir, s));
        Vector2 bl   = add(sub(center, mul(dir, s * 0.5f)), mul(perp, s * 0.55f));
        Vector2 br   = sub(sub(center, mul(dir, s * 0.5f)), mul(perp, s * 0.55f));
        PackedVector2Array tri = new PackedVector2Array();
        tri.pushBack(tip); tri.pushBack(bl); tri.pushBack(br);
        drawColoredPolygon(tri, selfColor, new PackedVector2Array(), null);
    }

    private void drawWaypoint(Vector2 c, Color col) {
        float s = 5f;
        PackedVector2Array diamond = new PackedVector2Array();
        diamond.pushBack(new Vector2((float) c.getX(), (float) c.getY() - s));
        diamond.pushBack(new Vector2((float) c.getX() + s, (float) c.getY()));
        diamond.pushBack(new Vector2((float) c.getX(), (float) c.getY() + s));
        diamond.pushBack(new Vector2((float) c.getX() - s, (float) c.getY()));
        drawColoredPolygon(diamond, col, new PackedVector2Array(), null);
    }

    /** Faction colour of the character that owns this waypoint (white if not found). */
    private Color waypointColor(String characterId) {
        for (Player p : PlayerRegistry.getPlayers()) {
            if (p != null && godot.global.GD.isInstanceValid(p) && p.characterInfo != null
                    && characterId.equals(p.characterInfo.characterId)) {
                return p.getNameplateColor();
            }
        }
        return new Color(1f, 1f, 1f, 1f);
    }

    private static Vector2 clampToDisc(Vector2 p, Vector2 c, float r) {
        float dx = (float) (p.getX() - c.getX());
        float dy = (float) (p.getY() - c.getY());
        float d = (float) Math.sqrt(dx * dx + dy * dy);
        if (d <= r || d < 1e-3f) return p;
        float k = r / d;
        return new Vector2((float) c.getX() + dx * k, (float) c.getY() + dy * k);
    }

    private static float distance(Vector2 a, Vector2 b) {
        float dx = (float) (a.getX() - b.getX());
        float dy = (float) (a.getY() - b.getY());
        return (float) Math.sqrt(dx * dx + dy * dy);
    }

    private static Vector2 add(Vector2 a, Vector2 b) { return new Vector2((float) (a.getX() + b.getX()), (float) (a.getY() + b.getY())); }
    private static Vector2 sub(Vector2 a, Vector2 b) { return new Vector2((float) (a.getX() - b.getX()), (float) (a.getY() - b.getY())); }
    private static Vector2 mul(Vector2 a, float s)   { return new Vector2((float) a.getX() * s, (float) a.getY() * s); }
}

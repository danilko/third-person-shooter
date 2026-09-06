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
import godot.api.Control;
import godot.api.Input;
import godot.api.InputEvent;
import godot.api.InputEventMouseButton;
import godot.api.Node;
import godot.api.Node3D;
import godot.core.Color;
import godot.core.PackedVector2Array;
import godot.core.Vector2;
import godot.core.Vector3;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Full-screen toggled map (PLAN.md I5) — a wider, north-up procedural radar (same model as
 * {@link MinimapController}) opened with the {@code map} action (M). Click anywhere to drop a GPS
 * waypoint ({@link Player#setWaypoint}, which records + replicates it). Shows region outlines, all
 * grid entities, the player, and every player's waypoint (faction-coloured).
 *
 * <p>Overlay discipline mirrors {@code WeaponRadialMenu}: while open it sets the local
 * {@code Character.inputBlocked = true} and mouse mode {@code VISIBLE} (so a click can't fire the gun —
 * {@code Character._physicsProcess} feeds an empty command while blocked) and moves itself to the front
 * to capture clicks. <b>Does not pause the simulation</b> (host-authoritative co-op keeps running).
 */
@Script(className = "WorldMapManager")
public class WorldMapManager extends Control {

    /** World metres from centre to map edge (a zoomed-out view). */
    @Export public float rangeMeters = 250f;
    @Export public Color backgroundColor = new Color(0.02f, 0.03f, 0.05f, 0.85f);
    @Export public Color regionColor = new Color(0.4f, 0.8f, 1f, 0.6f);
    @Export public Color selfColor = new Color(1f, 1f, 1f, 1f);
    @Export public float blipRadius = 4f;

    private Player player;
    private boolean open = false;

    public void wirePlayer(Player p) { player = p; }

    @Register
    @Override
    public void _ready() {
        setMouseFilter(Control.MouseFilter.STOP);   // capture clicks while open
        setVisible(false);
    }

    @Register
    @Override
    public void _input(InputEvent event) {
        if (event.isActionPressed("map", false, false)) toggle();
        else if (open && event.isActionPressed("ui_cancel", false, false)) close();
    }

    /** Clicks land here (local coords) only while the STOP control is visible/front-most. */
    @Register
    @Override
    public void _guiInput(InputEvent event) {
        if (!open || player == null) return;
        if (event instanceof InputEventMouseButton mb && mb.isPressed()) {
            if (mb.getButtonIndex() == godot.core.MouseButton.LEFT) {
                Vector3 world = screenToWorld(mb.getPosition());
                if (world != null) player.setWaypoint(world);
            } else if (mb.getButtonIndex() == godot.core.MouseButton.RIGHT) {
                player.clearWaypoint();   // right-click clears the destination
            }
        }
    }

    private void toggle() { if (open) close(); else open(); }

    private void open() {
        if (player == null) return;
        open = true;
        Node parent = getParent();
        if (parent != null) parent.moveChild(this, parent.getChildCount() - 1);
        Input.setMouseMode(Input.MouseMode.VISIBLE);
        player.inputBlocked = true;
        setVisible(true);
        queueRedraw();
    }

    private void close() {
        open = false;
        if (player != null) player.inputBlocked = false;
        Input.setMouseMode(Input.MouseMode.CAPTURED);
        setVisible(false);
    }

    @Register
    @Override
    public void _process(double delta) {
        if (open) queueRedraw();
    }

    @Register
    @Override
    public void _draw() {
        if (!open) return;
        Vector2 size = getSize();
        drawRect(new godot.core.Rect2(0.0, 0.0, size.getX(), size.getY()), backgroundColor, true, -1f, false);
        if (player == null || !godot.global.GD.isInstanceValid(player)) return;

        Vector2 center = size.times(0.5f);
        float radiusPx = Math.min((float) center.getX(), (float) center.getY()) - 10f;
        float scale = radiusPx / rangeMeters;
        Vector3 origin = player.getGlobalPosition();

        ZoneManager wzm = ZoneManager.get();
        if (wzm != null) {
            for (ZoneMarker m : wzm.getMarkers()) {
                if (m == null || !godot.global.GD.isInstanceValid(m) || m.zone == null) continue;
                Vector2 c = worldToScreen(m.getGlobalPosition(), origin, center, scale);
                drawCircle(c, m.zone.loadRadius * scale, regionColor, false, 1f, true);
            }
        }

        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) {
            List<Node> near = new ArrayList<>();
            grid.queryRadius(origin, rangeMeters, near);
            for (Node n : near) {
                if (n == player || !(n instanceof Node3D n3) || !(n instanceof NameplateTarget nt)) continue;
                drawCircle(worldToScreen(n3.getGlobalPosition(), origin, center, scale),
                        blipRadius, nt.getNameplateColor(), true, -1f, true);
            }
        }

        for (Map.Entry<String, Vector3> e : WaypointStore.entries().entrySet()) {
            drawWaypoint(worldToScreen(e.getValue(), origin, center, scale), waypointColor(e.getKey()));
        }

        // Player marker at centre.
        drawCircle(center, 5f, selfColor, true, -1f, true);
    }

    // ── helpers ────────────────────────────────────────────────────────────────

    private Vector2 worldToScreen(Vector3 world, Vector3 origin, Vector2 center, float scale) {
        return new Vector2((float) center.getX() + (float) (world.getX() - origin.getX()) * scale,
                           (float) center.getY() + (float) (world.getZ() - origin.getZ()) * scale);
    }

    /** Invert the north-up map transform: a clicked pixel → world XZ at the player's height. */
    private Vector3 screenToWorld(Vector2 px) {
        if (player == null) return null;
        Vector2 size = getSize();
        Vector2 center = size.times(0.5f);
        float radiusPx = Math.min((float) center.getX(), (float) center.getY()) - 10f;
        float scale = radiusPx / rangeMeters;
        if (scale < 1e-5f) return null;
        Vector3 origin = player.getGlobalPosition();
        float wx = (float) origin.getX() + ((float) px.getX() - (float) center.getX()) / scale;
        float wz = (float) origin.getZ() + ((float) px.getY() - (float) center.getY()) / scale;
        return new Vector3(wx, (float) origin.getY(), wz);
    }

    private void drawWaypoint(Vector2 c, Color col) {
        float s = 7f;
        PackedVector2Array diamond = new PackedVector2Array();
        diamond.pushBack(new Vector2((float) c.getX(), (float) c.getY() - s));
        diamond.pushBack(new Vector2((float) c.getX() + s, (float) c.getY()));
        diamond.pushBack(new Vector2((float) c.getX(), (float) c.getY() + s));
        diamond.pushBack(new Vector2((float) c.getX() - s, (float) c.getY()));
        drawColoredPolygon(diamond, col, new PackedVector2Array(), null);
    }

    private Color waypointColor(String characterId) {
        for (Player p : PlayerRegistry.getPlayers()) {
            if (p != null && godot.global.GD.isInstanceValid(p) && p.characterInfo != null
                    && characterId.equals(p.characterInfo.characterId)) {
                return p.getNameplateColor();
            }
        }
        return new Color(1f, 1f, 1f, 1f);
    }
}

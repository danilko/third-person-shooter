package com.openworld.debug;

import com.openworld.character.Player;
import com.openworld.game.PlayerRegistry;
import com.openworld.world.IntersectionZone;
import com.openworld.world.Lane;
import com.openworld.world.VehicleRoute;
import com.openworld.world.ZoneManager;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.BaseMaterial3D;
import godot.api.BoxShape3D;
import godot.api.CollisionShape3D;
import godot.api.ImmediateMesh;
import godot.api.Mesh;
import godot.api.MeshInstance3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.StandardMaterial3D;
import godot.core.Color;
import godot.core.StringName;
import godot.core.Vector3;
import godot.global.GD;

/**
 * Debug-only 3D overlay of the live traffic graph (F3 via {@code DebugHarness}): every registered
 * {@link Lane} — a {@link VehicleRoute} or a {@code PathLaneRoute}, drawn identically — along its
 * <b>driven</b> path ({@link Lane#pointAtLength} — smoothed/offset or native-baked, exactly what
 * cars follow), colored by role, plus yellow {@link IntersectionZone} box outlines. Makes a broken
 * junction snap or a missing turn connector visible at a glance instead of inferred from
 * route-finished reclaim logs.
 *
 * <p>Colors: plain lane <b>green</b>; junction turn connectors <b>orange</b> (L) / <b>white</b> (S)
 * / <b>magenta</b> (R); a DESPAWN route end gets a short <b>red</b> cross; direction chevrons every
 * ~20 m. Routes are distance-culled around the local player and the mesh rebuilds at 2 Hz — a
 * single {@link ImmediateMesh} LINES surface with vertex colors ({@code no_depth_test} so lanes
 * read through buildings).
 */
@Script(className = "RouteDebugOverlay")
public class RouteDebugOverlay extends Node3D {

    private static final double REFRESH_INTERVAL = 0.5;
    private static final float CULL_DIST = 600f;      // XZ radius around the local player
    private static final int MAX_ROUTES = 400;        // hard cap per rebuild
    private static final double SAMPLE_STEP = 4.0;    // m along the driven path
    private static final double CHEVRON_EVERY = 20.0; // m between direction chevrons
    private static final double LIFT = 0.5;           // m above the lane markers

    private static final Color LANE = new Color(0.2, 1.0, 0.3, 0.9);
    private static final Color TURN_L = new Color(1.0, 0.6, 0.1, 0.95);
    private static final Color TURN_S = new Color(1.0, 1.0, 1.0, 0.95);
    private static final Color TURN_R = new Color(1.0, 0.2, 1.0, 0.95);
    private static final Color JUNCTION = new Color(1.0, 1.0, 0.2, 0.9);
    private static final Color DESPAWN_END = new Color(1.0, 0.15, 0.15, 1.0);

    private ImmediateMesh mesh;
    private double refreshTimer = 0.0;
    private boolean surfaceOpen = false;

    @Register
    @Override
    public void _ready() {
        mesh = new ImmediateMesh();
        MeshInstance3D inst = new MeshInstance3D();
        inst.setMesh(mesh);
        inst.setMaterialOverride(makeMaterial());
        addChild(inst);
    }

    @Register
    @Override
    public void _process(double delta) {
        if (!isVisible()) return;
        refreshTimer -= delta;
        if (refreshTimer > 0.0) return;
        refreshTimer = REFRESH_INTERVAL;
        rebuild();
    }

    private void rebuild() {
        mesh.clearSurfaces();
        surfaceOpen = false;

        Vector3 center = localPlayerPos();
        ZoneManager mgr = ZoneManager.get();
        if (center == null || mgr == null) return;

        int drawn = 0;
        for (Lane r : mgr.getRoutes().values()) {
            if (drawn >= MAX_ROUTES) break;
            if (!(r instanceof Node3D n) || !GD.isInstanceValid(n)) continue;
            Vector3 entry = r.entryPoint();
            if (entry == null || distXZ(entry, center) > CULL_DIST) continue;
            drawRoute(r);
            drawn++;
        }
        drawJunctions(center);

        if (surfaceOpen) { mesh.surfaceEnd(); surfaceOpen = false; }
    }

    private void drawRoute(Lane r) {
        double total = r.total();
        if (total < 1e-3) return;
        Color color = colorFor(r);

        Vector3 prev = lift(r.pointAtLength(0));
        double nextChevron = CHEVRON_EVERY;
        for (double s = SAMPLE_STEP; s < total + SAMPLE_STEP; s += SAMPLE_STEP) {
            Vector3 cur = lift(r.pointAtLength(Math.min(s, total)));
            segment(prev, cur, color);
            if (s >= nextChevron) {
                chevron(cur, prev, color);
                nextChevron += CHEVRON_EVERY;
            }
            prev = cur;
        }
        if (!r.isLoop() && VehicleRoute.END_DESPAWN.equals(r.getEndBehavior())) cross(prev);
    }

    /** Two short wings angled back from the travel direction — an "this way" arrowhead at {@code p}. */
    private void chevron(Vector3 p, Vector3 prevP, Color color) {
        double tx = p.getX() - prevP.getX(), tz = p.getZ() - prevP.getZ();
        double len = Math.sqrt(tx * tx + tz * tz);
        if (len < 1e-6) return;
        tx /= len; tz /= len;
        double rx = -tz, rz = tx;   // right normal in XZ
        Vector3 backL = new Vector3(p.getX() - tx * 1.2 + rx * 0.8, p.getY(), p.getZ() - tz * 1.2 + rz * 0.8);
        Vector3 backR = new Vector3(p.getX() - tx * 1.2 - rx * 0.8, p.getY(), p.getZ() - tz * 1.2 - rz * 0.8);
        segment(p, backL, color);
        segment(p, backR, color);
    }

    /** Small red X marking a DESPAWN route end (cars vanish here — fine at map edges, a bug mid-town). */
    private void cross(Vector3 p) {
        segment(new Vector3(p.getX() - 1.0, p.getY(), p.getZ() - 1.0),
                new Vector3(p.getX() + 1.0, p.getY(), p.getZ() + 1.0), DESPAWN_END);
        segment(new Vector3(p.getX() - 1.0, p.getY(), p.getZ() + 1.0),
                new Vector3(p.getX() + 1.0, p.getY(), p.getZ() - 1.0), DESPAWN_END);
    }

    private void drawJunctions(Vector3 center) {
        if (getTree() == null) return;
        for (Object o : getTree().getNodesInGroup(new StringName(IntersectionZone.GROUP))) {
            if (!(o instanceof IntersectionZone iz) || !GD.isInstanceValid(iz)) continue;
            Vector3 pos = iz.getGlobalPosition();
            if (distXZ(pos, center) > CULL_DIST) continue;
            Vector3 half = boxHalfExtents(iz);
            boxOutline(pos, half);
        }
    }

    /** The zone's BoxShape3D half-extents, or an 8 m-square fallback. */
    private Vector3 boxHalfExtents(IntersectionZone iz) {
        for (Node child : iz.getChildren()) {
            if (child instanceof CollisionShape3D cs && cs.getShape() instanceof BoxShape3D bs) {
                Vector3 s = bs.getSize();
                return new Vector3(s.getX() * 0.5, s.getY() * 0.5, s.getZ() * 0.5);
            }
        }
        return new Vector3(4.0, 1.0, 4.0);
    }

    /** Axis-aligned wireframe box (junction zones are grid-aligned; rotation ignored — debug aid). */
    private void boxOutline(Vector3 c, Vector3 h) {
        double x0 = c.getX() - h.getX(), x1 = c.getX() + h.getX();
        double z0 = c.getZ() - h.getZ(), z1 = c.getZ() + h.getZ();
        double y0 = c.getY() - h.getY(), y1 = c.getY() + h.getY();
        for (double y : new double[]{y0, y1}) {
            segment(new Vector3(x0, y, z0), new Vector3(x1, y, z0), JUNCTION);
            segment(new Vector3(x1, y, z0), new Vector3(x1, y, z1), JUNCTION);
            segment(new Vector3(x1, y, z1), new Vector3(x0, y, z1), JUNCTION);
            segment(new Vector3(x0, y, z1), new Vector3(x0, y, z0), JUNCTION);
        }
        segment(new Vector3(x0, y0, z0), new Vector3(x0, y1, z0), JUNCTION);
        segment(new Vector3(x1, y0, z0), new Vector3(x1, y1, z0), JUNCTION);
        segment(new Vector3(x1, y0, z1), new Vector3(x1, y1, z1), JUNCTION);
        segment(new Vector3(x0, y0, z1), new Vector3(x0, y1, z1), JUNCTION);
    }

    private void segment(Vector3 a, Vector3 b, Color color) {
        if (!surfaceOpen) {
            // Deferred until the first segment: surfaceEnd() errors on an empty surface.
            mesh.surfaceBegin(Mesh.PrimitiveType.LINES, null);
            surfaceOpen = true;
        }
        mesh.surfaceSetColor(color);
        mesh.surfaceAddVertex(a);
        mesh.surfaceSetColor(color);
        mesh.surfaceAddVertex(b);
    }

    private Color colorFor(Lane r) {
        String turn = r.getTurn();
        return switch (turn == null ? "" : turn) {
            case "L" -> TURN_L;
            case "S" -> TURN_S;
            case "R" -> TURN_R;
            default -> LANE;
        };
    }

    private static Vector3 lift(Vector3 p) {
        return new Vector3(p.getX(), p.getY() + LIFT, p.getZ());
    }

    private static float distXZ(Vector3 a, Vector3 b) {
        double dx = a.getX() - b.getX(), dz = a.getZ() - b.getZ();
        return (float) Math.sqrt(dx * dx + dz * dz);
    }

    /** The local player's position (any valid player as fallback), or null when none exists yet. */
    private Vector3 localPlayerPos() {
        Player fallback = null;
        for (Player p : PlayerRegistry.getPlayers()) {
            if (!GD.isInstanceValid(p)) continue;
            if (p.isLocalOwnedPlayer()) return p.getGlobalPosition();
            if (fallback == null) fallback = p;
        }
        return fallback != null ? fallback.getGlobalPosition() : null;
    }

    /** Unshaded, vertex-colored, depth-test-free line material (ZoneMarker debug-mat idiom). */
    private StandardMaterial3D makeMaterial() {
        StandardMaterial3D mat = new StandardMaterial3D();
        mat.setTransparency(BaseMaterial3D.Transparency.ALPHA);
        mat.setShadingMode(BaseMaterial3D.ShadingMode.UNSHADED);
        mat.setCullMode(BaseMaterial3D.CullMode.DISABLED);
        mat.setFlag(BaseMaterial3D.Flags.ALBEDO_FROM_VERTEX_COLOR, true);
        mat.setFlag(BaseMaterial3D.Flags.DISABLE_DEPTH_TEST, true);
        return mat;
    }
}

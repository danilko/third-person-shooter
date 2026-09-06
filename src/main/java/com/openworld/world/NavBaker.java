package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.CollisionObject3D;
import godot.api.NavigationMesh;
import godot.api.NavigationMeshSourceGeometryData3D;
import godot.api.NavigationRegion3D;
import godot.api.NavigationServer3D;
import godot.api.Node;
import godot.api.PackedScene;
import godot.api.ResourceSaver;
import godot.core.Error;
import godot.core.StringName;
import godot.global.GD;

import java.util.ArrayList;
import java.util.List;

/**
 * Bakes a {@link NavigationRegion3D} into an already-baked district {@code .tscn} (PLAN.md I6
 * "stays Godot-side: pedestrian navmesh bake"), from that scene's own collision geometry
 * ({@code StaticBody3D}/{@code ConcavePolygonShape3D} — the coarse collision proxies
 * {@code WorldBaker}'s visual bulk rides alongside, per CLAUDE.md's Combat/Weapon System note that
 * a {@code MultiMesh} carries no collision of its own).
 *
 * <p>Uses the runtime (non-editor) navmesh-baking API — the same class of workaround
 * {@code WorldBaker} already established as the only option here (godot-kotlin-jvm 0.15.0-4.6
 * exposes no editor API — see CLAUDE.md Known Quirks). <b>Deliberately calls the two-step
 * {@link NavigationServer3D#parseSourceGeometryData}/{@link NavigationServer3D#bakeFromSourceGeometryData}
 * pair, not the single-call {@code NavigationMeshGenerator.bake()} convenience wrapper</b> — that
 * wrapper was verified (empirically, against a trivial hand-built ground plane) to silently return
 * zero polygons in this godot-kotlin-jvm binding, while the explicit two-step call it should be
 * equivalent to works correctly. {@code STATIC_COLLIDERS} parsing means this needs only
 * {@code PhysicsServer3D}-side shape data, not a live {@code RenderingServer} — unlike
 * {@code WorldBaker}'s MultiMesh step, this bake works fine under {@code --headless}.
 *
 * <p>Deliberately a separate class from {@code WorldBaker}: that one converts a glTF source into a
 * native scene; this one enriches an <b>already-native</b>, already-baked scene with a second
 * artifact (a nav region) built from ITS OWN geometry — a different operation with a different
 * input, run as its own pass after the glTF bake completes and is saved to disk.
 *
 * <p>Baked in the district's own local frame (each district's content sits at local origin per
 * {@code build_district.py}), so the region's baked polygons translate correctly for free when
 * {@code ZoneManager} streams the district in at its zone's world position — a
 * {@code NavigationRegion3D} is a normal descendant node and inherits its ancestors' transform like
 * any other. {@code useEdgeConnections} stays on (the default) so adjacent, edge-abutting
 * districts' regions merge into one traversable navmesh at their seam once both are streamed in —
 * per Godot's own edge-connection-margin merging, not anything district-specific here.
 */
@Script(className = "NavBaker")
public class NavBaker extends Node {

    @Export public String scenePath = "";
    @Export public boolean bakeOnReady = false;
    @Export public boolean quitWhenDone = false;
    /**
     * Half-extent of the baking clip box, in metres. {@code 0} (the default) means
     * {@link #DISTRICT_HALF_EXTENT} — one 504 m district, which is what every piece was until the
     * island rebuild's BASE piece, a single 1512 m object holding the whole world's ground and
     * roads. Baked against the district constant, that piece got a navmesh over its middle 503 m
     * and nothing anywhere else: AI could walk the castle and not the island. Set it per bake
     * (see {@code build_piece.sh}'s {@code NAV_HALF}) for anything that is not one district.
     *
     * <p>It is an override rather than "derive it from the scene's own AABB", because the district
     * value is not a measurement — it is the SEAM RULE ({@link #NAV_CLIP_INSET}), and a district
     * whose roads cross its border would silently widen its own clip and overlap its neighbour's
     * navmesh, which is the duplicate-region case that produces the runtime edge errors.
     */
    @Export public float clipHalfExtent = 0f;

    /**
     * Recast cell size in metres. {@code 0} (the default) DERIVES it from the clip extent so the
     * voxel grid stays inside {@link #MAX_NAV_CELLS} on a side.
     *
     * <p>Why it has to be derived: {@link #CELL_SIZE} is 0.5 m, which over one 504 m district is a
     * 1007-cell grid and fine. Over the island rebuild's 4032 m base piece it is 8062 cells a side
     * — and Recast came back with a navmesh of <b>zero vertices</b>, silently, with no error of any
     * kind. A nav bake that produces nothing looks exactly like a nav bake that had nothing to do.
     *
     * <p>The derivation leaves a district byte-identical (503 / 2048 = 0.25, below the 0.5 floor),
     * and gives the 4 km piece ~2 m cells. That is coarse against a 0.4 m agent radius — it will
     * not resolve a doorway — which is the honest shape of the trade: a world-sized navmesh is for
     * open ground, and fine-grained navigation belongs to the per-district town chunks that stream.
     */
    @Export public float navCellSize = 0f;

    public float getNavCellSize() { return navCellSize; }

    public void setNavCellSize(float v) { navCellSize = v; }

    public float getClipHalfExtent() { return clipHalfExtent; }

    public void setClipHalfExtent(float v) { clipHalfExtent = v; }

    /** Roughly a human character's capsule (CLAUDE.md's stance/movement scale) — not tuned per district. */
    private static final float AGENT_HEIGHT = 1.8f;
    private static final float AGENT_RADIUS = 0.4f;
    private static final float AGENT_MAX_CLIMB = 0.5f;
    private static final float AGENT_MAX_SLOPE_DEG = 46f;

    /**
     * Rasterization voxel size (engine default is 0.25/0.25). Coarsened for the DEM-terrain
     * districts: the real PLATEAU TIN is dense and irregular, and at the default resolution the
     * polygonizer emits a handful of duplicate free edges where TIN detail and building collision
     * proxies interleave — Godot then logs "Navigation region synchronization had N edge error(s)"
     * on every region sync at runtime. Halving the resolution re-polygonizes those spots away and
     * shrinks the mesh; centimetre-precision walkability is meaningless on 504 m district chunks.
     */
    private static final float CELL_SIZE = 0.5f;
    /** Widest Recast voxel grid, in cells per side, {@link #navCellSize}'s derivation aims under. */
    private static final float MAX_NAV_CELLS = 2048f;
    private static final float CELL_HEIGHT = 0.5f;

    /**
     * Clip the bake to the district's own 504 m grid cell ({@code world_grid.DISTRICT}, content
     * centered at local origin). The PLATEAU TIN + border-building colliders spill 10–25 m past the
     * cell edge, so an unclipped bake overlaps its neighbours' — and two loaded neighbours then
     * rasterize the same map cells at the seam, which is Godot's "More than 2 edges tried to occupy
     * the same map rasterization space" edge error.
     *
     * <p>{@code NAV_CLIP_INSET}: the clip is pulled a further 0.5 m INSIDE the cell on every side.
     * Regions that abut <i>exactly</i> still share border rasterization cells, and wherever a third
     * edge lands in one of those cells (notably 4-district corners) the same ">2 edges" warning
     * fires. The inset leaves a deliberate 1 m gap between neighbouring navmeshes so they never
     * share a cell; the map's edge-connection pass (see {@code navigation/3d/
     * default_edge_connection_margin = 1.5} in project.godot, raised from the 0.25 default to
     * bridge this gap) still connects the two free edges, so cross-district pathing is unaffected.
     */
    private static final float DISTRICT_HALF_EXTENT = 252f;
    private static final float NAV_CLIP_INSET = 0.5f;
    private static final float CLIP_Y_MIN = -100f;
    private static final float CLIP_Y_MAX = 1000f;

    /**
     * The road kit's "nobody may walk here" marker, read here and nowhere else on the Godot side.
     *
     * <p>{@code point_build.collision_name} stamps it into the name of every carriageway
     * {@code -colonly} proxy (and every gore whose flanks both refuse pedestrians) <i>specifically</i>
     * so this bake skips them — it is what stops an on-ramp baking as a continuous walkable slope
     * onto the expressway. The authoring contract was written when the marker was invented and the
     * runtime half was never built, so until now every carriageway on the island baked into the very
     * navmesh the marker exists to keep it out of, and AI pathed down the middle of the road
     * (WORLD_REBUILD_PLAN.md {@code W4}).
     *
     * <p>The suffix survives the glTF import: Godot strips only the trailing {@code -colonly} when
     * it turns the mesh into a {@code StaticBody3D}, so the baked node is named
     * {@code <road>_road-road-noped}. Matching the token anywhere in the name (not as a suffix)
     * is deliberate for that reason.
     *
     * <p>It is not road-only. What the marker means <i>here</i> is "solid, but not walkable
     * ground", and anything the world builds that is both takes it — the island's
     * {@code Seabed-noped-colonly} is the second user: you stand on the sea floor rather than
     * falling out of the world, and no AI may plan a route across the bottom of the bay.
     */
    private static final String NO_PED_TOKEN = "-noped";

    @Register
    @Override
    public void _ready() {
        if (bakeOnReady) bake();
    }

    public void bake() {
        bakeScene(this, scenePath, clipHalfExtent > 0f ? clipHalfExtent : DISTRICT_HALF_EXTENT,
                  navCellSize);
        if (quitWhenDone && getTree() != null) getTree().quit();
    }

    /**
     * Loads {@code scenePath} (an already-baked native district scene), adds a
     * {@code NavigationRegion3D} baked from its own static-collider geometry, and re-saves over
     * the same path. {@code host} must be in the tree (mirrors {@code WorldBaker.bakeScene}'s contract).
     */
    public static void bakeScene(Node host, String scenePath, float halfExtent, float cellSize) {
        Object loaded = GD.load(scenePath);
        if (!(loaded instanceof PackedScene src)) {
            GD.printErr("NavBaker: could not load source scene '" + scenePath + "'");
            return;
        }
        Node root = src.instantiate();
        if (root == null) { GD.printErr("NavBaker: source instantiate failed"); return; }
        host.addChild(root);

        // Idempotence: drop any BakedNav a previous run left behind BEFORE parsing source geometry —
        // re-baking must replace the region, not stack a second identical navmesh on top of it
        // (overlapping duplicates are exactly what trips the runtime "edge error(s)" warning).
        Node stale = root.getNodeOrNull(new godot.core.NodePath("BakedNav"));
        if (stale != null) { root.removeChild(stale); stale.queueFree(); }

        NavigationMesh navMesh = new NavigationMesh();
        navMesh.setParsedGeometryType(NavigationMesh.ParsedGeometryType.STATIC_COLLIDERS);
        navMesh.setAgentHeight(AGENT_HEIGHT);
        navMesh.setAgentRadius(AGENT_RADIUS);
        navMesh.setAgentMaxClimb(AGENT_MAX_CLIMB);
        navMesh.setAgentMaxSlope(AGENT_MAX_SLOPE_DEG);
        float cs = cellSize > 0f ? cellSize
                : Math.max(CELL_SIZE, (2f * halfExtent) / MAX_NAV_CELLS);
        navMesh.setCellSize(cs);
        navMesh.setCellHeight(CELL_HEIGHT);
        float clipHalf = halfExtent - NAV_CLIP_INSET;
        navMesh.setFilterBakingAabb(new godot.core.AABB(
                new godot.core.Vector3(-clipHalf, CLIP_Y_MIN, -clipHalf),
                new godot.core.Vector3(2 * clipHalf, CLIP_Y_MAX - CLIP_Y_MIN, 2 * clipHalf)));

        // Two explicit steps (see class doc for why, not NavigationMeshGenerator.bake()): scan
        // root's whole subtree for STATIC_COLLIDERS geometry, then bake polygons from it.
        //
        // THE `-noped` BODIES ARE MASKED OUT, NOT DETACHED. Godot's STATIC_COLLIDERS parser tests
        // each body's own collision_layer against the navmesh's geometry collision_mask, so
        // clearing the layer for the duration of the parse is a two-line, fully reversible way to
        // say "not this one" — where lifting the nodes out of the tree would have to put them back
        // at the right index AND restore every descendant's owner before pack(), any slip in which
        // silently drops geometry from the SAVED scene rather than just from the navmesh.
        List<CollisionObject3D> noPed = collectNoPed(root);
        long[] savedLayers = new long[noPed.size()];
        for (int i = 0; i < noPed.size(); i++) {
            savedLayers[i] = noPed.get(i).getCollisionLayer();
            noPed.get(i).setCollisionLayer(0L);
        }
        NavigationMeshSourceGeometryData3D srcData = new NavigationMeshSourceGeometryData3D();
        NavigationServer3D.INSTANCE.parseSourceGeometryData(navMesh, srcData, root);
        NavigationServer3D.INSTANCE.bakeFromSourceGeometryData(navMesh, srcData);
        for (int i = 0; i < noPed.size(); i++) noPed.get(i).setCollisionLayer(savedLayers[i]);
        int vertexCount = navMesh.getVertices().getSize();

        NavigationRegion3D navRegion = new NavigationRegion3D();
        navRegion.setName(new StringName("BakedNav"));
        navRegion.setUseEdgeConnections(true);
        root.addChild(navRegion);
        navRegion.setOwner(root);
        navRegion.setNavigationMesh(navMesh);

        PackedScene packed = new PackedScene();
        Error err = packed.pack(root);
        if (err == Error.OK) {
            Error save = ResourceSaver.save(packed, scenePath, ResourceSaver.SaverFlags.FLAG_NONE);
            GD.print("NavBaker: baked nav for '" + scenePath + "' (" + (save == Error.OK ? "saved" : "save FAILED " + save)
                    + ") — vertices=" + vertexCount + " clipHalf=" + clipHalf + " cell=" + cs
                    + " skipped=" + noPed.size() + " " + NO_PED_TOKEN + " body(ies)");
        } else {
            GD.printErr("NavBaker: pack() failed: " + err);
        }
        host.removeChild(root);
        root.queueFree();
    }

    /**
     * Every {@link CollisionObject3D} in {@code root}'s subtree whose name carries
     * {@link #NO_PED_TOKEN}. Whole subtree, not root's direct children: a baked piece is flat today
     * but a district streamed as a chunk hierarchy would not be, and a marker that only works at
     * one depth is a marker that stops working silently.
     */
    private static List<CollisionObject3D> collectNoPed(Node root) {
        List<CollisionObject3D> out = new ArrayList<>();
        collectNoPed(root, out);
        return out;
    }

    private static void collectNoPed(Node node, List<CollisionObject3D> out) {
        if (node instanceof CollisionObject3D body
                && node.getName().toString().contains(NO_PED_TOKEN)) {
            out.add(body);
        }
        for (Node child : node.getChildren()) collectNoPed(child, out);
    }
}

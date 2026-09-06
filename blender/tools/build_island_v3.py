#!/usr/bin/env python3
"""
build_island_v3.py -> assets/world_source/island_v3.blend

The OVERALL 2D MAP for Tokyo-Bay Island v3, built in Blender at true game scale
(2016 x 2016 m, centre origin, X east / Y north) from two pure-Python sources:

    tools/island_v3_geom.py   — the SHAPE of the island (coast, water, terrain, centrelines)
    tools/island_v3_plan.py   — the PLANNING RULES (block gradient, ramps, parcels, support)

This is a **layout source**, in exactly the sense BLENDER_CONVENTIONS.md means it: a
traceable, true-scale plan you author road_kit_authoring pieces ON TOP OF. It is not the
shipped world and it is not baked — nothing here is meant to survive into Godot untouched.

WHY FLAT BY DEFAULT: the ground is drawn as a flat 2D plan so it reads as a map and so a
piece dropped on it lands predictably. The ELEVATED network (expressway deck, ramps, rail
viaduct, bridges) is always placed at its TRUE Z, because the whole point of §6 below is
that what goes underneath a surface is derived from how high that surface sits. Pass
`--relief` to step the terrain bands to their real elevations as well.

SUPPORT (§6 of tokyo-bay-island-v3-cityplanning.md) is the idea worth reading the file for:
there is no separate "highway builder" and "ground road builder". Every surface is drawn the
same way, and `island_v3_plan.support_kind(surface_z, ground_z)` decides — per sample, from
one number — whether it gets nothing, an embankment, or a pier line. Change a height and the
understructure changes with it. This script bakes that decision once for the layout preview;
the live authoring version of the same rule belongs in Geometry Nodes so it re-evaluates
while a height is dragged (see the addon work in the companion doc).

RUN:
  blender --background --python blender/tools/build_island_v3.py
  blender --background --python blender/tools/build_island_v3.py -- --relief --streets --parcels
  blender --background assets/world_source/island_v3.blend --python blender/tools/render.py -- _island 0 0 2600
"""
import bpy, bmesh, os, sys, math

BLENDER_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))    # blender/
REPO        = os.path.dirname(BLENDER_SRC)
ROOT        = os.path.join(REPO, "assets", "world_source")
sys.path.insert(0, os.path.join(BLENDER_SRC, "lib"))
sys.path.insert(0, os.path.join(REPO, "tools"))

import kit_common as kc
import assemble as asm
import island_v3_geom as G
import island_v3_plan as P
import island_v3_terrain as IT


# --------------------------------------------------------------------------- layers
# Z bands, chosen only so coincident flat plates never z-fight. They carry no meaning
# beyond draw order — the REAL heights in this file are P.DECK_Z / P.RAIL_Z / terrain.
# `Z_SEA` is `island_v3_terrain.SEA_LEVEL_Z` — the water surface is a WORLD fact now (the base
# piece's plates read the same number), not a band in this file's draw order. `Z_WATER` stays a
# band and stays POSITIVE, because this builder draws the top-down PLAN, where water has to draw
# over land to be seen at all. That is exactly why it must never be used by a piece you walk
# around in: carried into `build_island_base` it put the bay 5 cm above its own shore.
Z_SEA, Z_LAND, Z_WATER, Z_ZONE = IT.SEA_LEVEL_Z, 0.00, 0.05, 0.12
Z_BLOCK, Z_T3, Z_T2, Z_MARK = 0.18, 0.22, 0.30, 0.40

#: Half-widths now live with the plan (`island_v3_plan.ROAD_HALF`) — the bridge decks are derived
#: from them and two owners of "how wide is a T2" is exactly the kind of drift this rebuild is
#: about. Kept as a local alias so the call sites stay readable.
HALF = P.ROAD_HALF

# --------------------------------------------------------------------- ground heightfield
#: Heightfield resolution, in BUILT metres — a fixed world-space size, deliberately NOT carried
#: through `G.SCALE`.
#:
#: It was scale-carried once, on the theory that the island should always be resolved by the same
#: number of cells. That is the wrong invariant: what a player sees is metres, not cells. At
#: `SCALE` 2.0 it silently became a 24 m grid, and the place that shows is the COASTLINE — the
#: marching-squares clip resolves the shore to one cell, so the world's most looked-at edge gets
#: chunky exactly when the world gets bigger. The contour field itself is smooth over hundreds of
#: metres and would not care either way.
GROUND_CELL = 12.0

#: How deep the ground's coastal skirt hangs — the wall you see at a cliff, not a volume.
#:
#: It IS `island_v3_terrain.SHORE_Z`, and that is the whole reason the seabed and the land meet
#: instead of nearly meeting: the skirt's foot and the seabed's shoreline are the same number, at
#: the same XY (`_edge_crossing` gives both sheets the identical vertex), so there is no step and
#: no gap to fall through at the waterline. It used to be "0.4 m under the `Sea` plate", which was
#: a rule about hiding an edge under an opaque plate — right while the sea had no floor.
GROUND_SKIRT_Z = IT.SHORE_Z

#: The COARSEST ground cell, in metres — the quadtree root. `GROUND_CELL` is the finest, and every
#: leaf is one of the sizes between them, halving. Must be `GROUND_CELL * 2**k` and must divide the
#: world; `_quadtree_leaves` halves it until it does rather than build a ragged edge.
#:
#: 192 m (16x) is four levels, which is as coarse as this island has any use for: the harbour apron
#: and the airport platform are the only surfaces big and flat enough to take a root cell whole.
GROUND_COARSE_CELL = 192.0

#: How far the real surface may sit from the plane through a cell's own four corners before that
#: cell splits, in metres. This is the ONE number that trades ground triangles against ground
#: accuracy, and it is expressed the way the error is actually felt — as a height.
#:
#: MEASURED on the built island, quadtree surface against the uniform 12 m grid (8000 on-land
#: raycasts), and against `GROUND_TOLERANCE`:
#:
#:     see WORLD_REBUILD_PLAN.md, "The ground is a QUADTREE" — re-run that table if you change this.
GROUND_TOLERANCE = 0.25

#: Planar-dissolve angle, in DEGREES, for `_dissolve_planar`. **0 = off, and off is the default**
#: now that the ground is a quadtree.
#:
#: It was 1.0 for one day and it worked — 87% of the mesh gone for 8 cm at the 95th percentile. But
#: `bmesh.ops.dissolve_limit` merges any coplanar-enough faces it can reach, so what comes out is
#: long ragged n-gons at whatever angle the coastline and the contour rings happen to meet: correct
#: geometry, unreadable topology, and nothing a chunked-LOD scheme or a hand edit can hold on to.
#: The quadtree buys the same order of saving in SQUARES. Kept because it is the right tool for a
#: mesh that is not a heightfield (an imported scan, a hand-modelled landmark), not for this one.
GROUND_DISSOLVE_DEG = 0.0

#: Crease angle, in DEGREES, above which the ground stops being shaded smooth. See
#: `_shade_smooth_by_angle`. 40 deg is comfortably above anything the contour field produces
#: (`P.MAX_GRADE` tops out around 4 deg) and comfortably below the ~90 deg joint where the coastal
#: skirt drops away, so in practice it separates exactly those two and nothing else.
GROUND_CREASE_DEG = 40.0

#: Bisection steps used to place a coastline vertex on a cell edge. 8 halvings of a 12 m cell is
#: 5 cm, well under anything the blockout cares about. The bisection is run from the INSIDE point
#: toward the OUTSIDE one, so the two cells sharing an edge compute the identical vertex and the
#: mesh stays watertight.
GROUND_EDGE_BISECT = 8


# ------------------------------------------------------------------------- ground fn
def make_ground_fn(relief):
    """THE GROUND, as one function — `island_v3_terrain.Terrain`.

    It used to be a local stack of ellipse tests returning the tallest band a point was inside,
    which is a PRISM: flat-topped, sheer-sided, and a 120 m wall at its own outline. The contour
    interpolation that replaced it lives in `tools/island_v3_terrain.py` so that the mesh below,
    the §6 support rule, and `check_island_ground.py` all read the same surface. Flat mode still
    returns real values for the reclaimed platforms, because those genuinely are at a different
    level and the bridge/ramp geometry depends on it.
    """
    return IT.Terrain(relief=relief)


# ------------------------------------------------------------------------- §6 support
def build_support(name, pts3, half_w, coll, ground, sample=P.PIER_SPACING):
    """THE uniform rule, applied. Walk the surface, ask `support_kind` at each station, and
    emit what it asks for — nothing, an embankment skirt, or a pier bent. One function
    serves the expressway deck, the ramps, the rail viaduct and the bridges, because from
    here they are all just "a surface at some height over some ground"."""
    made = {P.SUPPORT_NONE: 0, P.SUPPORT_FILL: 0, P.SUPPORT_PIER: 0,
            P.SUPPORT_CUT: 0, P.SUPPORT_TUNNEL: 0}
    run = 0.0
    for i, (x, y, z) in enumerate(pts3):
        if i:
            run += math.dist(pts3[i - 1][:2], (x, y))
        if i and run < sample:
            continue
        run = 0.0
        gz = ground(x, y)
        kind = P.support_kind(z, gz)
        made[kind] += 1
        if kind == P.SUPPORT_PIER:
            a = pts3[max(0, i - 1)]; b = pts3[min(len(pts3) - 1, i + 1)]
            tx, ty = b[0] - a[0], b[1] - a[1]
            L = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / L, tx / L
            off = max(0.0, half_w - 2.0)
            # soffit — the structural depth the deck actually needs
            kc.box(f"{name}_soffit_{i}", x - half_w, x + half_w, y - 1.2, y + 1.2,
                   z - P.DECK_THICK, z - 0.05, coll, "concrete")
            for s in (-1.0, 1.0):
                px, py = x + nx * off * s, y + ny * off * s
                h = P.PIER_SECTION / 2.0
                kc.box(f"{name}_pier_{i}{'L' if s < 0 else 'R'}",
                       px - h, px + h, py - h, py + h, gz, z - P.DECK_THICK, coll, "concrete")
        elif kind == P.SUPPORT_FILL:
            toe = P.fill_footprint(z, gz, half_w)
            kc.box(f"{name}_fill_{i}", x - toe, x + toe, y - sample / 2, y + sample / 2,
                   gz - 0.2, z - 0.1, coll, "dirt")
    return made


# --------------------------------------------------------------------------- builders
def _edge_crossing(p_in, p_out):
    """Where the coastline crosses the segment from an ON-LAND point to an OFF-LAND one.

    ORIENTATION-FREE ON PURPOSE. The two cells that share this edge walk it in opposite
    directions, so a bisection that started from "the first corner" would give each of them a
    slightly different vertex and open a crack down every coastal cell. Bisecting from the inside
    point toward the outside one is the same computation whichever cell asks.
    """
    ax, ay = p_in
    bx, by = p_out
    for _ in range(GROUND_EDGE_BISECT):
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
        if G.on_land(mx, my):
            ax, ay = mx, my
        else:
            bx, by = mx, my
    return ((ax + bx) / 2.0, (ay + by) / 2.0)


def _clip_cell(corners, land, keep_land=True):
    """One grid cell clipped to the coastline — plain marching-squares over the 4 corners.

    `keep_land` picks WHICH SIDE is kept: the island (the ground sheet) or the sea (the seabed).
    `_edge_crossing` is still handed its arguments LAND-FIRST either way, and that is the load-
    bearing detail — it bisects from the on-land point outward, so anchoring it on the side being
    kept would give the two sheets two slightly different vertices at the same crossing and open a
    crack down the entire coast, exactly the failure the orientation rule was written for.
    """
    inside = list(land) if keep_land else [not v for v in land]
    if all(inside):
        return corners
    if not any(inside):
        return None
    out = []
    for i in range(4):
        j = (i + 1) % 4
        if inside[i]:
            out.append(corners[i])
        if inside[i] != inside[j]:
            a, b = (corners[i], corners[j]) if land[i] else (corners[j], corners[i])
            out.append(_edge_crossing(a, b))
    return out if len(out) >= 3 else None


def _triangulate(me):
    """Every ground face a TRIANGLE. Not cosmetic — three things downstream need it.

    A quadtree leaf is a quad, or an n-gon of up to 8 when hanging nodes are folded in, and on a
    heightfield NONE of them is planar. Blender's boolean solver triangulates such a face itself,
    and on a 48 m eight-sided cell it made a choice that **dropped the face**: `point_build`'s road
    cut punched a 40 m hole clean through the ground beside Chuo-dori, with the leaf present and the
    quadtree's own coverage check clean. The 12 m grid's quads were small enough to get away with it
    and that is all — the defect was latent there too.

    The other two: `obj.ray_cast` (which is how everything from `check_island_ground` to the road's
    own ground sampler asks where the ground is) tessellates n-gons on the fly, and the glTF
    exporter triangulates on the way out. Doing it here means all three see the same surface that
    was measured, instead of three independent guesses at it.

    It costs nothing: an n-gon of v vertices is v-2 triangles either way.
    """
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    me.update()


def _shade_smooth_by_angle(me, degrees):
    """Smooth shading everywhere except across creases sharper than `degrees`.

    This is the other half of the decimation answer, and it is the half that decides how the
    result LOOKS. A heightfield's triangles are describing a surface that is genuinely smooth --
    flat shading draws every one of them, so a 12 m grid reads as the faceted low-poly thing it
    literally is, and the instinct that follows is "add triangles". Per-vertex normals remove the
    facets without adding a single one, which is why the mesh can be dissolved by 87% and still
    read as terrain.

    The crease rule is what keeps a cliff a cliff: the coastal skirt meets the ground at ~90 deg,
    and smoothing that joint would round the shoreline into the sea plate. Run AFTER the dissolve
    -- edge indices do not survive it, and the angles it leaves are the ones worth judging.
    """
    for poly in me.polygons:
        poly.use_smooth = True
    lim = math.cos(math.radians(degrees))
    faces = {}
    for poly in me.polygons:
        for key in poly.edge_keys:
            faces.setdefault(key, []).append(poly.normal)
    for edge in me.edges:
        got = faces.get(tuple(sorted(edge.vertices)))
        edge.use_edge_sharp = bool(got and len(got) == 2 and got[0].dot(got[1]) < lim)
    me.update()


def _dissolve_planar(me, degrees):
    """Merge coplanar-within-`degrees` faces in place. See `GROUND_DISSOLVE_DEG`.

    `bmesh.ops.dissolve_limit`, not the DECIMATE modifier: the two give byte-identical results here
    (verified 9704 v / 18781 tri either way) and the bmesh call needs no object, no view layer and
    no operator context, so it works wherever `build_ground` is called from.
    """
    if degrees <= 0.0:
        return
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(degrees),
                             use_dissolve_boundaries=False,
                             verts=bm.verts[:], edges=bm.edges[:], delimit={'NORMAL'})
    bm.to_mesh(me)
    bm.free()
    me.update()


def _quadtree_leaves(field, n, grid, inside_mask):
    """Cover the world in SQUARES whose size adapts to the terrain. See `GROUND_COARSE_CELL`.

    Returns `(leaves, cover)` — leaves as `(bi, bj, k)` in FINEST-cell units (so `k == 1` is a
    `GROUND_CELL` cell), and `cover` mapping every finest cell to the `k` of the leaf holding it,
    which is what `_leaf_ring` needs to find its hanging nodes.

    Three rules decide a split, and the first two are about correctness rather than looks:

      * NOTHING INSIDE, MARGIN INCLUDED -> no leaf at all. Nothing to build. (`inside_mask`
        is the land for the ground sheet and its complement for the seabed — one traversal,
        two sheets, so the two can never resolve the same coast differently.)
      * NOT ALL-LAND, MARGIN INCLUDED -> split. The margin is why a coarse cell can never end up
        beside a coastline cell: `_clip_cell` puts a vertex wherever the shore crosses a cell edge,
        at a fraction no coarse neighbour would ever place one, and the two would then disagree
        along a shared edge — a T-junction that the skirt pass reads as TWO boundary edges and
        walls twice. Requiring the block plus one finest cell of margin to be land keeps every
        clipped cell surrounded by finest cells, so the shore is resolved exactly as the uniform
        grid resolved it.
      * RELIEF OVER `GROUND_TOLERANCE` -> split. This is the only aesthetic one, and it is the
        whole saving: a block whose interior is within a few centimetres of the plane through its
        own corners has nothing to say that its corners have not already said.

    Then a 2:1 BALANCE pass, which is not optional: a cell may only neighbour cells within one
    level of itself, so a shared edge carries at most one hanging node and `_leaf_ring` can place
    it at the midpoint. Without it a 192 m cell against a 12 m one leaves a 15-vertex edge on one
    side and a bare segment on the other.
    """
    root = max(1, int(round(GROUND_COARSE_CELL / GROUND_CELL)))
    while root > 1 and (n % root):                      # the world must tile by whole root cells
        root //= 2

    # Summed-area over the land flags so "is this whole block on land" is O(1) at any size.
    sat = [[0] * (n + 2) for _ in range(n + 2)]
    for i in range(n + 1):
        row, prev = sat[i + 1], sat[i]
        for j in range(n + 1):
            row[j + 1] = (1 if inside_mask[i][j] else 0) + prev[j + 1] + row[j] - prev[j]

    def land_box(i0, j0, i1, j1):
        """(#land corners, #corners) over the inclusive corner box, clamped to the world."""
        i0, j0 = max(0, i0), max(0, j0)
        i1, j1 = min(n, i1), min(n, j1)
        if i1 < i0 or j1 < j0:
            return 0, 0
        got = sat[i1 + 1][j1 + 1] - sat[i0][j1 + 1] - sat[i1 + 1][j0] + sat[i0][j0]
        return got, (i1 - i0 + 1) * (j1 - j0 + 1)

    def relief(bi, bj, k):
        """Worst gap between the real surface and the plane through the block's four corners."""
        ax, ay = grid[bi][bj]
        side = k * GROUND_CELL
        h00 = field(ax, ay)
        h10 = field(ax + side, ay)
        h01 = field(ax, ay + side)
        h11 = field(ax + side, ay + side)
        worst = 0.0
        for u in (0.25, 0.5, 0.75):
            for v in (0.25, 0.5, 0.75):
                lin = (h00 * (1 - u) * (1 - v) + h10 * u * (1 - v)
                       + h01 * (1 - u) * v + h11 * u * v)
                worst = max(worst, abs(field(ax + u * side, ay + v * side) - lin))
        return worst

    leaves = []

    def recurse(bi, bj, k):
        if k > 1:
            got, total = land_box(bi - 1, bj - 1, bi + k + 1, bj + k + 1)
            if got == 0:
                return                                  # all outside, margin included — no face
            half = k // 2
            if got != total or relief(bi, bj, k) > GROUND_TOLERANCE:
                for (di, dj) in ((0, 0), (half, 0), (0, half), (half, half)):
                    recurse(bi + di, bj + dj, half)
                return
        leaves.append((bi, bj, k))

    for bi in range(0, n, root):
        for bj in range(0, n, root):
            recurse(bi, bj, root)

    while True:
        cover = {}
        for (bi, bj, k) in leaves:
            for i in range(bi, bi + k):
                for j in range(bj, bj + k):
                    cover[(i, j)] = k
        split, keep = [], []
        for leaf in leaves:
            bi, bj, k = leaf
            finer = False
            if k > 1:
                for i in range(bi, bi + k):
                    for jj in (bj - 1, bj + k):
                        got = cover.get((i, jj))
                        finer = finer or (got is not None and got < k // 2)
                for j in range(bj, bj + k):
                    for ii in (bi - 1, bi + k):
                        got = cover.get((ii, j))
                        finer = finer or (got is not None and got < k // 2)
            (split if finer else keep).append(leaf)
        if not split:
            return leaves, cover
        half = lambda v: v // 2                                            # noqa: E731
        for (bi, bj, k) in split:
            h = half(k)
            keep += [(bi + di, bj + dj, h) for (di, dj) in ((0, 0), (h, 0), (0, h), (h, h))]
        leaves = keep


def _leaf_ring(bi, bj, k, grid, cover):
    """A quadtree leaf's outline: its four corners, plus a HANGING NODE on any edge a finer
    neighbour splits. 2:1 balance bounds that at one per edge, so the ring is 4 to 8 points.

    The hanging node is what keeps the mesh watertight where cell sizes change: the finer
    neighbour already has a vertex at this edge's midpoint, and a coarse cell that ignored it
    would leave a crack the skirt pass then walls. Adding it costs one collinear vertex.
    """
    h = k // 2
    ring = []

    def finer(cells):
        return any((cover.get(c) or k) < k for c in cells)

    ring.append(grid[bi][bj])
    if finer([(i, bj - 1) for i in range(bi, bi + k)]):
        ring.append(grid[bi + h][bj])
    ring.append(grid[bi + k][bj])
    if finer([(bi + k, j) for j in range(bj, bj + k)]):
        ring.append(grid[bi + k][bj + h])
    ring.append(grid[bi + k][bj + k])
    if finer([(i, bj + k) for i in range(bi, bi + k)]):
        ring.append(grid[bi + h][bj + k])
    ring.append(grid[bi][bj + k])
    if finer([(bi - 1, j) for j in range(bj, bj + k)]):
        ring.append(grid[bi][bj + h])
    return ring


def _face_upward(me):
    """A GROUND SHEET FACES UP. Returns True if it had to be flipped.

    `recalc_face_normals` makes a mesh's normals CONSISTENT, and infers which way is "out" from
    the shape as a whole. The ground gets that right for free because its skirt closes it
    downward; the seabed has no skirt, so it is an open sheet with nothing to infer from — and it
    came out with all 21 764 faces pointing DOWN. Two things then fail silently and neither looks
    like a normals bug: the sea floor is invisible from above (backface culling) and Recast will
    not rasterize a down-facing triangle as walkable, so it contributes no navmesh at all. The
    collision proxy still worked perfectly, which is what made it look fine in every test that
    only asked whether you could stand on it.
    """
    if sum(p.normal.z for p in me.polygons) >= 0.0:
        return False
    me.flip_normals()
    me.update()
    return True


def _world_grid(margin=0.0):
    """The finest cell grid, its land mask, and `n` — computed once and shared by both sheets, so
    the ground and the seabed are cut from the SAME samples of `G.on_land`.

    `margin` pads the square outward by whole `GROUND_CELL`s (see
    `island_v3_terrain.SEABED_MARGIN`): the sea floor has to reach past the boundary a player can
    be turned back at. Padding in WHOLE cells is what keeps one grid honest — every interior cell
    lands on exactly the coordinates the unpadded grid would have given, so the ground sheet built
    on the padded grid is byte-identical to the one built without it, and the coastline both sheets
    are cut to is the same set of samples rather than two agreeing computations.
    """
    pad = int(math.ceil(margin / GROUND_CELL))
    n = int(math.ceil(G.WORLD / GROUND_CELL)) + 2 * pad
    x0 = y0 = -G.ORIGIN - pad * GROUND_CELL
    grid = [[(x0 + i * GROUND_CELL, y0 + j * GROUND_CELL) for j in range(n + 1)]
            for i in range(n + 1)]
    land = [[G.on_land(*grid[i][j]) for j in range(n + 1)] for i in range(n + 1)]
    return n, grid, land


def _build_sheet(name, coll, field, mat_key, keep_land, skirt_z, n, grid, land):
    """ONE quadtree heightfield, clipped to one side of the coastline. Both the island's ground and
    the sea's floor are this function; only `field`, which side is kept, and whether the free edge
    is walled differ.

    `skirt_z` of `None` means NO SKIRT, which is the seabed's answer: its only free edges are the
    shoreline (where the land's own skirt already comes down to meet it) and the world square's
    outer boundary (2 km from any land, and a boundary the world needs to answer for itself, not
    with a wall hidden under the sea)."""
    verts, index, faces = [], {}, []

    def vid(pt):
        key = (round(pt[0], 3), round(pt[1], 3))
        got = index.get(key)
        if got is None:
            got = index[key] = len(verts)
            verts.append((key[0], key[1], field(key[0], key[1])))
        return got

    inside_mask = land if keep_land else [[not v for v in col] for col in land]
    leaves, cover = _quadtree_leaves(field, n, grid, inside_mask)
    for (bi, bj, k) in leaves:
        if k == 1:
            corners = (grid[bi][bj], grid[bi + 1][bj], grid[bi + 1][bj + 1], grid[bi][bj + 1])
            flags = (land[bi][bj], land[bi + 1][bj], land[bi + 1][bj + 1], land[bi][bj + 1])
            poly = _clip_cell(corners, flags, keep_land)
        else:
            poly = _leaf_ring(bi, bj, k, grid, cover)
        if not poly:
            continue
        ring = [vid(p) for p in poly]
        ring = [v for m, v in enumerate(ring) if v != ring[m - 1]]          # drop repeats
        if len(ring) >= 3:
            faces.append(tuple(ring))

    # THE SKIRT. A boundary edge is one used by a single face — coast, bay bank, lagoon bank, all
    # the same thing to this loop, which is why none of them needs its own code.
    skirt = 0
    if skirt_z is not None:
        used = {}
        for f in faces:
            for k in range(len(f)):
                e = (f[k], f[(k + 1) % len(f)])
                used[(min(e), max(e))] = used.get((min(e), max(e)), 0) + 1
        low = {}

        def below(v):
            got = low.get(v)
            if got is None:
                got = low[v] = len(verts)
                verts.append((verts[v][0], verts[v][1], skirt_z))
            return got
        for (a, b), count in used.items():
            if count == 1:
                faces.append((a, b, below(b), below(a)))
                skirt += 1

    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    kc.recalc_normals(me)
    _face_upward(me)
    _dissolve_planar(me, GROUND_DISSOLVE_DEG)
    _triangulate(me)
    _shade_smooth_by_angle(me, GROUND_CREASE_DEG)
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    obj.data.materials.append(kc.mat(mat_key))
    # THE MESH's counts, not the python lists' — `_dissolve_planar` ran between them.
    return len(me.vertices), len(me.polygons), skirt


def build_ground(terrain, coll, world=None, carve=None):
    """ONE continuous ground mesh, cut to the coastline — the whole of WORLD_REBUILD_PLAN.md step 1.

    It REPLACES `Land_Main`, `Land_Harbour`, `Land_Airport` and every `Massif_band_*`/`Spur_band_*`
    prism: after this there is exactly one object in the world that answers "how high is the ground
    here", which is the same rule `point_build.is_terrain` follows for a district.

    Three things it does that the prisms did not:

      * it is a HEIGHTFIELD — a `GROUND_CELL` grid whose vertices carry `terrain.height`, so the
        contour interpolation is what you drive on, not a stack of plateaus;
      * it is CUT TO `G.on_land`, so the bay and the lagoon are real holes. That is what makes
        `BAY_BRIDGE` load-bearing rather than decorative, and it is why `check_island_ground.py`
        now has to know what a bridge is;
      * it carries a SKIRT down to `GROUND_SKIRT_Z` on every boundary edge (coast, bay, lagoon),
        so a cliff reads as a cliff and the ground is never a paper surface seen edge-on;
      * and where a `carve` is supplied it is CUT TO THE ROADS -- not by a boolean on the mesh, but
        by the roads acting as a CEILING on the field this samples (`island_v3_terrain.Carve`).
        The verge that makes that exact on THIS grid is one `GROUND_CELL`, and it is the caller's
        to supply because it is a property of the mesh rather than of the field.

    The holes stay holes. `build_seabed` fills what is under them with a SEPARATE sheet, so
    "is there land here" and "is there something to stand on here" remain two questions with two
    answers — the first is what every road tool and `check_island_ground.py` reads.
    """
    n, grid, land = world or _world_grid()
    # THE ROADS ARE A CEILING ON THE GROUND, and this is the ONLY place in the world handed them --
    # see `island_v3_terrain.Carve` for why the field the router, the grader and the support rule
    # read has to stay the natural one. `carve` of None is the natural ground, unchanged.
    return _build_sheet("Ground", coll, IT.carved_field(terrain.height, carve), "leaf", True,
                        GROUND_SKIRT_Z, n, grid, land)


def build_seabed(terrain, coll, world=None):
    """THE GROUND UNDER THE WATER — the same quadtree, the other side of the coastline.

    WHY (2026-08-30, from a walk-test): the sea had no floor, so stepping off any shore — or off
    Hama-dori where it crosses the bay on nothing but its own tarmac — dropped the player out of
    the world for good. That is not the deliberate no-safety-floor decision CLAUDE.md records; it
    is its opposite. A safety floor is an invisible collision lid a metre under the visual ground,
    which traps a body with no way back; a seabed is the terrain continuing past the waterline —
    visible, sloped, walkable back up — and it is what every open-world coast ships.

    It is NOT terrain, deliberately, and the collection name matters: `point_build.is_terrain`
    matches a `TERRAIN`/`GROUND`/`MANUAL` collection, and a seabed inside one would be drapeable
    ground. Hama-dori would then have been draped 2 m UNDER the bay instead of reported as
    crossing open water, and W1 would have closed itself by hiding.
    """
    n, grid, land = world or _world_grid()
    return _build_sheet("Seabed", coll, IT.seabed, "dirt", False, None, n, grid, land)


def build_water_and_land(relief, ground):
    sea = kc.get_coll("SEA"); land = kc.get_coll("LAND"); water = kc.get_coll("WATER")
    h = G.ORIGIN + 120.0
    kc.box("Sea", -h, h, -h, h, Z_SEA - 0.4, Z_SEA, sea, "accent")
    # The ground surface is TERRAIN; LAND keeps the islets, which are scenery rocks and carry
    # nothing. Both are `check_island_ground.GROUND_COLLECTIONS`.
    nv, nf, nskirt = build_ground(ground, kc.get_coll("TERRAIN"))
    for i, islet in enumerate(G.ISLETS):
        kc.prism(f"Islet_{i}", islet, Z_SEA, G._s(1.5), land, "trim")
    kc.prism("Water_Bay", G.BAY, Z_SEA, Z_WATER, water, "accent")
    kc.prism("Water_Lagoon", G.LAGOON, Z_SEA, Z_WATER, water, "accent")
    # The river DRAPES. It is a ribbon on the ground, and the ground is no longer flat — drawn at
    # a constant Z it would tunnel through the massif's flank at its own source.
    kc.flat_ribbon("Water_River", [(x, y, ground.height(x, y) + Z_WATER) for (x, y) in G.RIVER],
                   16.0, water, "accent")
    print("  ground: %d verts, %d faces (%d skirt), quadtree %.0f-%.0f m at %.2f m tolerance"
          % (nv, nf, nskirt, GROUND_CELL, GROUND_COARSE_CELL, GROUND_TOLERANCE))
    return nv


def drape(pts_xy, ground, lift):
    """A flat map layer, laid ON the ground instead of at a constant Z.

    Every 2D plate in this file used to be drawn at its own `Z_*` band, which was correct while
    the ground WAS a constant Z. It is not any more: a zone outline at +0.12 over the spur is
    120 m underground. The `Z_*` bands survive as what they always were — a draw order — but they
    are now an offset from the terrain, not an absolute.
    """
    return [(x, y, ground.height(x, y) + lift) for (x, y) in pts_xy]


#: A footprint prism whose ground varies by more than this is not on flat land, and a flat plate
#: there is a lie either way — a paddy laid across a 90% flank is not a paddy. Parcels past it are
#: DROPPED and counted, rather than drawn as fins sticking out of the mountain.
FOOTPRINT_MAX_RELIEF = 3.0


def footprint_relief(poly, ground):
    """(lowest ground under this footprint, how much it varies across it).

    LOWEST, not the centroid: a plate sat at the average of a slope FLOATS over half of itself,
    which reads as a bug. Sat at the minimum it is partly buried, which reads as a plate on a
    hillside — the truthful picture, and the one that degrades gracefully.
    """
    zs = [ground.height(x, y) for (x, y) in poly]
    if not zs:
        return 0.0, 0.0
    return min(zs), max(zs) - min(zs)


def build_zones_and_blocks(streets, ground):
    zc = kc.get_coll("ZONES")
    n_zone = n_street = 0
    for (zname, rects, _f, _d, _r, _i, _col) in G.ZONES:
        for k, (x0, y0, x1, y1) in enumerate(rects):
            # OUTLINE, not a filled plate — a zone is an envelope you author inside, and a
            # solid plate buries the coast and terrain you need to see while doing it.
            ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
            kc.flat_ribbon(f"zone_{zname}_{k}", drape(ring, ground, Z_ZONE),
                           3.0, zc, "line_y")
            n_zone += 1
    if not streets:
        return n_zone, 0
    sc = kc.get_coll("T3")
    for zi, (zname, rects, *_rest) in enumerate(G.ZONES):
        if zname == "farm":
            continue
        for k, rect in enumerate(rects):
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            quarter, spec = P.block_spec_at(cx, cy, zname)
            for j, line in enumerate(P.street_grid(rect, spec, seed=zi * 17 + k)):
                kc.flat_ribbon(f"T3_{zname}{k}_{quarter}_{j}",
                               drape(line, ground, Z_T3), HALF["T3"], sc, "asphalt")
                n_street += 1
    return n_zone, n_street


def build_castle(ground):
    c = kc.get_coll("CASTLE")
    gz, _ = footprint_relief(G.MOAT, ground)
    kc.prism("Moat_water", G.MOAT, gz + Z_SEA, gz + Z_WATER, c, "accent")
    kc.prism("Castle_ground", G.CASTLE, gz + Z_LAND, gz + G._s(3.0), c, "trim")
    kc.box("Tenshu", *[G._s(v) for v in (-140, -100, 60, 96)],
           gz + G._s(3.0), gz + G._s(27.0), c, "roof")
    for nm, poly in (("Park_Shiba", G.SHIBA_PK), ("Park_Shrine", G.SHRINE_PK)):
        pz, _ = footprint_relief(poly, ground)
        kc.prism(nm, poly, pz + Z_LAND, pz + Z_ZONE, c, "leaf")
    # the castle-town rings — the §2 gradient made visible, so block sizes can be checked
    ring = kc.get_coll("PLANNING")
    for r, q in P.CASTLE_RINGS:
        pts = [(P.CASTLE_C[0] + r * math.cos(a * math.pi / 32),
                P.CASTLE_C[1] + r * math.sin(a * math.pi / 32)) for a in range(64)]
        kc.flat_ribbon(f"castle_ring_{int(r)}_{q or 'moat'}",
                       drape(pts + [pts[0]], ground, Z_MARK), 1.2, ring, "line_y")


def build_roads(ground):
    t2 = kc.get_coll("T2")
    kc.flat_ribbon("T2_RING", drape(P.RING + [P.RING[0]], ground, Z_T2),
                   HALF["T2"], t2, "asphalt")
    for name, pts in G.ARTERIALS:
        kc.flat_ribbon(f"T2_{name.replace(' ', '_')}", drape(pts, ground, Z_T2),
                       HALF["T2"], t2, "asphalt")

    t1 = kc.get_coll("T1"); sup = kc.get_coll("SUPPORT")
    stats = {}
    deck = P.loop_deck()
    kc.flat_ribbon("T1_LOOP", deck, HALF["T1"], t1, "asphalt")
    stats["LOOP"] = build_support("LOOP", deck, HALF["T1"], sup, ground)

    for rid, p3, par, grade, ok, kind in P.ramps():
        kc.flat_ribbon(f"T1_{rid}", p3, HALF["RAMP"], t1, "asphalt")
        stats[rid] = build_support(rid, p3, HALF["RAMP"], sup, ground, sample=22.0)
        if not ok:
            print(f"  WARNING: {rid} grade {grade*100:.1f}% exceeds "
                  f"{P.MAX_GRADE['ramp']*100:.0f}% — lengthen the run, do not steepen it")

    spiral, sg = P.spiral_ramp(G.AIRPORT_BRIDGE[1])
    kc.flat_ribbon("T1_SPIRAL_AIRPORT", spiral, HALF["RAMP"], t1, "asphalt")
    stats["SPIRAL"] = build_support("SPIRAL", spiral, HALF["RAMP"], sup, ground, sample=22.0)

    # at-grade T1 continuations: these DRAPE, so the same support call gives them nothing —
    # which is the point of the rule being uniform.
    for nm, pts in (("WESTRAD", G.WESTRAD), ("PORTSPUR", G.PORTSPUR), ("TOUGE", G.TOUGE),
                    ("AIRPORT_ROAD", G.AIRPORT_ROAD)):
        p3 = [(x, y, ground(x, y) + 0.25) for (x, y) in pts]
        kc.flat_ribbon(f"T1_{nm}", p3, HALF["RAMP"], t1, "asphalt")
        stats[nm] = build_support(nm, p3, HALF["RAMP"], sup, ground)
    return stats


def build_rail(ground):
    rc = kc.get_coll("RAIL"); sup = kc.get_coll("SUPPORT")
    stats = {}
    for nm, pts in (("RAIL_MAIN", G.RAIL_MAIN), ("RAIL_BRANCH", G.RAIL_BRANCH),
                    ("RAIL_AIRPORT", G.RAIL_AIRPORT)):
        total = G.plen(pts) or 1.0
        run, p3 = 0.0, []
        for i, (x, y) in enumerate(pts):
            if i:
                run += math.dist(pts[i - 1], (x, y))
            p3.append((x, y, P.rail_z_at(nm, run / total, ground(x, y))))
        kc.flat_ribbon(nm, p3, HALF["RAIL"], rc, "rail")
        stats[nm] = build_support(nm, p3, HALF["RAIL"], sup, ground)
        tight = [r for r in P.curvature_radii(pts) if r < P.RAIL_MIN_RADIUS]
        if tight:
            print(f"  WARNING: {nm} has {len(tight)} vertex/vertices under the "
                  f"{P.RAIL_MIN_RADIUS:.0f} m mainline radius (tightest {min(tight):.0f} m) "
                  f"— ease it or accept it as a local line")
    return stats


def build_bridges(ground):
    bc = kc.get_coll("BRIDGES"); sup = kc.get_coll("SUPPORT")
    for nm, (a, b), z, half in P.BRIDGES:
        n = max(2, int(math.dist(a, b) / 20.0))
        p3 = [(a[0] + (b[0]-a[0])*i/n, a[1] + (b[1]-a[1])*i/n, z) for i in range(n + 1)]
        kc.flat_ribbon(nm, p3, half, bc, "concrete")
        build_support(nm, p3, half, sup, ground, sample=40.0)
        print(f"  {nm}: {math.dist(a, b):.0f} m span at +{z:.0f} m")
    (rx0, ry0), (rx1, ry1) = G.RUNWAY
    n = 24
    kc.flat_ribbon("Runway", [(rx0 + (rx1-rx0)*i/n, ry0 + (ry1-ry0)*i/n, P.ISLAND_Z + 0.1)
                              for i in range(n + 1)], 22.5, bc, "asphalt")


def build_parcels(ground):
    pc = kc.get_coll("PARCELS")
    n = steep = 0
    for (zname, rects, *_r) in G.ZONES:
        if zname != "farm":
            continue
        for i, rect in enumerate(rects):
            grain = P.FARM_RECT_GRAIN[i]
            for j, poly in enumerate(P.parcels(rect, grain, seed=i)):
                pz, relief = footprint_relief(poly, ground)
                if relief > FOOTPRINT_MAX_RELIEF:
                    steep += 1
                    continue
                kc.prism(f"parcel_{grain}_{i}_{j}", poly,
                         pz + Z_BLOCK, pz + Z_BLOCK + 0.03, pc, "leaf")
                n += 1
    if steep:
        print("  parcels: %d dropped as too steep to be a field (> %.0f m of relief across "
              "the plot) — the flank terraces are a modelling job, not a flat plate"
              % (steep, FOOTPRINT_MAX_RELIEF))
    return n


def build_markers(ground):
    mk = kc.get_coll("MARKERS")
    for label, x, y, kind in G.LANDMARKS:
        e = bpy.data.objects.new(f"slot_{label.split('—')[0].strip().replace(' ', '_')}", None)
        e.empty_display_type = 'ARROWS'; e.empty_display_size = 30.0
        e.location = (x, y, ground.height(x, y) + Z_MARK)
        e["landmark"] = label; e["kind"] = kind
        mk.objects.link(e)
    for sid, x, y, note in G.SECTORS:
        e = bpy.data.objects.new(f"sector_{sid}", None)
        e.empty_display_type = 'SPHERE'; e.empty_display_size = 22.0
        e.location = (x, y, P.DECK_Z + 4.0)
        e["sector"] = sid; e["note"] = note
        mk.objects.link(e)
    for name, (sx, sy) in P.STATIONS:
        e = bpy.data.objects.new(f"station_{name}", None)
        e.empty_display_type = 'CONE'; e.empty_display_size = 26.0
        e.location = (sx, sy, ground.height(sx, sy) + P.RAIL_Z)
        e["station"] = name; e["walk_radius"] = P.STATION_WALK
        mk.objects.link(e)
    for rid, gore, touch, kind, note in P.INTERCHANGES:
        e = bpy.data.objects.new(f"ic_{rid}", None)
        e.empty_display_type = 'PLAIN_AXES'; e.empty_display_size = 18.0
        e.location = (gore[0], gore[1], P.DECK_Z)
        e["interchange"] = rid; e["kind"] = kind; e["serves"] = note
        mk.objects.link(e)


def build_grid():
    gc = kc.get_coll("GRID")
    for gy in range(G.GRID_N):
        for gx in range(G.GRID_N):
            theme = G.theme_at(gx, gy)
            cx = gx * G.DISTRICT + G.DISTRICT / 2 - G.ORIGIN
            cy = gy * G.DISTRICT + G.DISTRICT / 2 - G.ORIGIN
            e = bpy.data.objects.new(f"region_Piece_{gx}_{gy}", None)
            e.empty_display_type = 'CUBE'; e.empty_display_size = G.DISTRICT / 2.0
            e.location = (cx, cy, 0.0)
            e["size"] = [G.DISTRICT, 40.0, G.DISTRICT]
            e["theme"] = theme.lower()
            e["built"] = theme.lower() != "void"
            gc.objects.link(e)


# ------------------------------------------------------------------------------- main
def parse_args():
    import argparse
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="build_island_v3.py")
    ap.add_argument("--relief", action="store_true",
                    help="step the terrain bands to real elevations (default: flat 2D plan)")
    ap.add_argument("--streets", action="store_true",
                    help="generate T3 local streets from the block gradient (heavier)")
    ap.add_argument("--parcels", action="store_true",
                    help="generate farmland parcels across the three grains (heavier)")
    ap.add_argument("--full", action="store_true", help="relief + streets + parcels")
    a = ap.parse_args(argv)
    if a.full:
        a.relief = a.streets = a.parcels = True
    return a


def build(opts):
    kc.setup_units()
    asm.wipe_scene()
    for name in ("SEA", "LAND", "WATER", "TERRAIN", "ZONES", "PLANNING", "CASTLE",
                 "T1", "T2", "T3", "RAIL", "BRIDGES", "SUPPORT", "PARCELS",
                 "MARKERS", "GRID"):
        kc.get_coll(name)

    ground = make_ground_fn(opts.relief)
    n_land = build_water_and_land(opts.relief, ground)
    n_zone, n_street = build_zones_and_blocks(opts.streets, ground)
    build_castle(ground)
    road_stats = build_roads(ground)
    rail_stats = build_rail(ground)
    build_bridges(ground)
    n_parcel = build_parcels(ground) if opts.parcels else 0
    build_markers(ground)
    build_grid()

    asm.add_camera_sun(kc.get_coll("MARKERS"), target=(0.0, 0.0, 0.0),
                       cam_loc=(0.0, -G.WORLD * 0.75, G.WORLD * 0.85), lens=32)

    tot = {}
    for st in list(road_stats.values()) + list(rail_stats.values()):
        for k, v in st.items():
            tot[k] = tot.get(k, 0) + v
    print("ISLAND v3: %.0fx%.0f m  relief=%s  land=%d  zones=%d  T3=%d  parcels=%d"
          % (G.WORLD, G.WORLD, opts.relief, n_land, n_zone, n_street, n_parcel))
    print("  support stations derived: " +
          "  ".join(f"{k}={v}" for k, v in sorted(tot.items()) if v))


def main():
    build(parse_args())
    if bpy.app.background:
        kc.save_blend(ROOT, "island_v3.blend")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""island_v3_terrain.py — ONE CONTINUOUS GROUND for island v3, derived from the contour rings.

    python3 tools/island_v3_terrain.py            # self-tests + the arterial ground report

WHY THIS EXISTS. `island_v3_geom.MASSIF` / `SPUR` are nested closed rings with heights — a
contour map. `build_island_v3.py` used to emit each ring as its own PRISM, so the ground was a
stack of flat-topped plateaus with sheer sides, and `check_island_ground.py` measured what that
does to a road laid on it: four of the seven arterials crossed an 80–120 m **vertical wall**
(800–1200% grade). `point_solve` derives every support from `delta = surface_z - ground_z`, so
building there computes piers and trenches off a cliff face.

Nested rings with heights ARE a contour map, so turning them into a surface is well defined:

    between two adjacent contours the height is LINEAR IN EUCLIDEAN DISTANCE to each of them.

That is the textbook contour→DEM rule and it is the one that makes GRADE controllable: the slope
between two rings is exactly `dz / (the gap between them)`, so a number in this file is a grade on
the ground, not a shape that happens to come out somewhere.

THE ONE FREE PARAMETER is `FOOT_GRADE`. The plan's outermost contour for the massif is already
**+120 m** — there is no 0 m contour in the data, which is precisely why the prisms had a 120 m
wall at their own outline. So this module DERIVES one: a **toe ring**, offset outward from the
outermost contour by `z / FOOT_GRADE`, at `BASE_Z`. Raising `FOOT_GRADE` makes the mountain's foot
steeper and its footprint smaller; lowering it spreads a gentle apron over more of the island.
Every "route around it or climb it" decision in `WORLD_REBUILD_PLAN.md` step 1 is a trade against
this number, so it is stated once, here.

THIS MODULE IS THE ONE OWNER OF "where is the ground". `build_island_v3.py` builds its ground mesh
from `Terrain.height` AND feeds the same function to the §6 support rule, so the mesh a road is
laid on and the ground its piers are derived from can never disagree.
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import island_v3_geom as G                                                   # noqa: E402
import island_v3_plan as P                                                   # noqa: E402

# --------------------------------------------------------------------------- constants
#: Sea-level ground. The whole flat part of the island sits here; a contour set is a bump on it.
BASE_Z = 0.0

#: The grade of the derived TOE ring — the mountain's own foot. See the module docstring: this is
#: the single number the "route around it / climb it" trade is made against.
#:
#: These are STEEP, and the reason is the plan's own data. Measure the spacing it already ships:
#: the massif climbs +120 m between its +120 and +240 contours in 130 m of run east-west and 72 m
#: north-south — 92% and 167%. The spur climbs +60 m in 105/125 m — 57% and 48%. A gentle apron
#: bolted onto that is a shelf around a spire, so each foot is set just GENTLER than the interval
#: above it (a mountain base is concave) and no gentler:
#:
#:     MASSIF 0.80 against an interior of 0.92–1.67
#:     SPUR   0.60 against an interior of 0.48–0.57  (a 140 m hill has no room for an apron
#:                                                    before it runs into the city)
#:
#: What the number really buys is FOOTPRINT, which is flat land: at 0.80 the massif reaches its
#: 0 m toe 150 m outside its +120 contour, which leaves the farm valley's floor at y ~= 430–690;
#: at 0.25 it would be 480 m and there would be no valley.
FOOT_GRADE = {"MASSIF": 0.80, "SPUR": 0.60}

#: How finely a contour ring is sampled. `G.ellipse`'s default 44 is a drawing resolution; a
#: distance field wants the ring dense enough that the polyline IS the ellipse.
RING_SAMPLES = 96

#: THE COAST IS A CUT, NOT A TAPER — and this is a decision, so it is written down.
#:
#: The plan puts the massif's +380 summit at (-300, 790) with the island's north shore ~130 m
#: away, and its +120 contour reaches y = 1040 against a coast at y ~= 945. The mountain
#: genuinely overhangs the sea. Two ways to deal with that were tried and both were worse than
#: leaving it alone:
#:
#:   * `G.pull_ashore` (what the old prism builder did) projects every off-land ring vertex
#:     radially onto the coastline, which lands the toe, the +120 and the +240 contours on the
#:     SAME stretch of shore with no gap between them. That is the wall again, moved to the water.
#:   * a shore clamp ("the ground may climb no faster than k as you walk inland") reads well and
#:     then eats the mountain: at a walkable 0.60 the +380 summit came out at 74 m.
#:
#: So the contour field is evaluated as authored and the ground MESH is cut to the coastline.
#: Where a hill reaches the water the result is a sea cliff of whatever height the plan's own
#: contours put there — dramatic, honest, and nowhere near a road. Every district north of the
#: massif is `void`/`mtn` in `G.MATRIX`.

#: A reclaimed platform (`Land_Harbour` +2, `Land_Airport` +4) is a REAL STEP and stays one — but
#: only against the sea, where its edge is a quay wall with the ocean at the bottom. Where it meets
#: other land it is a road surface, so it ramps. The standard is the mainline limit, because
#: Chuo-dori and Hama-dori (both mainlines) drive across the harbour's landward edge.
#:
#: THE 3/4 IS NOT TIMIDITY. Build a ramp at exactly the limit and a 10 m sample of it reads 3.9%
#: against a 4.0% limit — a gate that passes on rounding. A taper cut at three quarters of the
#: number it has to satisfy can never be the thing that fails.
PLATFORM_EDGE_GRADE = P.MAX_GRADE["mainline"] * 0.75

#: How far outboard the inland-vs-sea test samples, and how finely a platform outline is resampled
#: before each piece of it is classified. One probe per 500 m edge would decide the whole quay from
#: its midpoint; 20 m pieces let the taper follow the actual neck.
PLATFORM_PROBE = 12.0
PLATFORM_EDGE_STEP = 20.0

#: ---------------------------------------------------------------- the sea has a FLOOR
#: WHERE THE WATER IS, relative to the flat land at `BASE_Z`. One owner: `build_island_v3.Z_SEA`
#: and the base piece's water plates both read it from here.
#:
#: It is NEGATIVE, and that is the whole of the first half of the 2026-08-30 report ("the ocean
#: seems to flow higher than common ground"): the plan builder's `Z_WATER = +0.05` is a DRAW-ORDER
#: band from a top-down diagram, where water has to draw OVER land to be seen at all. Carried into
#: a piece you walk around in, it put the bay and the lagoon 5 cm above their own shore.
SEA_LEVEL_Z = -0.60

#: THE SEABED WHERE IT MEETS THE LAND — the foot of the land's coastal skirt.
#: `build_island_v3.GROUND_SKIRT_Z` IS this number, so the two meshes meet at the shore instead of
#: agreeing to about a metre.
#:
#: IT IS ABOVE `SEA_LEVEL_Z`, AND THAT IS THE BEACH. The land's own edge is at `BASE_Z` (0.00) and
#: the water is 0.60 m below it, so a seabed that started at the waterline would put a 1 m wall
#: around the entire island: you could swim to the shore and never get out of the water, because
#: 1 m is nearly three times `MovementController.stepHeight` (0.35). Starting the sea floor 0.20 m
#: under the land instead gives a single climbable step down onto DRY sand, which then slopes ~26 m
#: before it passes `SEA_LEVEL_Z` and becomes water. One number, and the island gets a beach you
#: can walk both ways.
#:
#: The proper long-term answer is a coastal taper on the LAND side (real coasts reach sea level on
#: their own, they do not stop 0.6 m above it); this is the honest version of that until the
#: terrain is authored for it.
SHORE_Z = -0.20

#: How far the sea floor reaches PAST the world square, in metres — the OCEAN, and its whole size.
#:
#: THE GROUND DOES THE WORK, THE WALL IS THE LAST RESORT — but only just enough ground. Two
#: walk-test readings set this, in order:
#:
#:   * at 288 the wall was doing the ground's job: the floor stopped 288 m outside the boundary,
#:     the boundary sat at the world square, and from a coast at ~+-1330 m you met an invisible wall
#:     700 m offshore, which is where a fence reads as a fence;
#:   * at 1728 (+-3744 m of floor, the +-3800 m budget) the ground did all the work and the sea was
#:     **2.0 km wide at the median heading** -- "so empty".
#:
#: 288 with the wall inset from the floor's edge (below) is the answer to both, and the numbers are
#: measured rather than picked: sea **4.6 km across**, water from the last land to the wall
#: **202 m at the tightest heading, 498 m at the median**, over 1 km on only the two diagonals
#: where a square sea round a roughly round island is furthest out.
#:
#: WHY NOT 3.5 km, which is what was asked for: the LAND does not fit inside it. The main coastline
#: is ~+-1330 m, but the offshore airport reaches y = -1976 m and the north coast y = +1960, so the
#: land's own bounding box is **3.72 x 3.94 km**. A +-1750 m sea would leave 226 m of the airport
#: and the north shore hanging over the edge of the world with nothing under them.
#:
#: 288 is `96 * 3`, and the multiple matters: the padded grid has to stay a whole number of quadtree
#: ROOT cells (`WORLD + 2*margin` divisible by 192) or the outer ring refines for nothing. The next
#: step down, 192, puts the wall 88 m from the furthest land -- inside its own 80 m soft band, so a
#: player standing on the airport's south quay would already feel the push.
SEABED_MARGIN = 288.0

#: How far INSIDE the sea floor's outer edge the logic wall stands, in metres.
#:
#: A wall on the last triangle of the world has a hole under it the moment anything overshoots by a
#: frame's motion, so `WorldBounds` is inset — but only by enough to be standing on something. The
#: ocean itself is `SEABED_MARGIN`; this is the shoulder at the end of it.
BOUNDS_INSET = 144.0

#: Deep water, and the run it takes to get there from the shore. Layout, so both carry `G.SCALE`
#: (a shelf gradient is then unchanged by a rescale, exactly like the platforms' edge taper).
#:
#: WHY THERE IS A SEABED AT ALL, when CLAUDE.md says in as many words that this project has
#: deliberately NO world-spanning safety floor. Those are opposite things and the difference is
#: the whole design:
#:
#:   * a SAFETY FLOOR is collision-only, invisible, and a metre under the visual ground. You fall
#:     through a gap, land on nothing you can see, and there is no way back up. It was removed
#:     for exactly that, and nothing here brings it back — this surface does not exist under the
#:     island at all (`Terrain.surface` returns the land where there IS land).
#:   * a SEABED is the ordinary continuation of the terrain past the waterline: visible, sloped,
#:     and reachable — you walk down it and you walk back up it. It is what GTA V, RDR2 and every
#:     open-world coast ships, and the reason none of them can drop you out of the world at the
#:     beach.
#:
#: The depth is a function of DISTANCE FROM THE NEAREST SHORE, not of position, and that one
#: choice does the work: the open sea reaches the full floor depth, while a 100 m ria like the bay
#: is never more than ~50 m from its own bank and so stays a shallow inlet with no special case
#: written for it.
SEA_FLOOR_Z = G._s(-12.0)
SHELF_RUN = G._s(175.0)

#: The reclaimed plates' own heights. Layout, so they carry `G.SCALE` — see the note on
#: `island_v3_plan.DECK_Z`. The taper run is derived from the height, so the EDGE GRADE is
#: unchanged by a rescale and the arterials that cross it stay legal for free.
PLATFORM_SPECS = [("harbour", G.HARBOUR, G._s(2.0)), ("airport", G.AIRPORT, P.ISLAND_Z)]


# ----------------------------------------------------------------------------- geometry
def seg_dist(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-12:
        return math.dist((px, py), a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


def poly_dist(poly, x, y):
    """Distance from a point to a CLOSED polyline (the contour itself, not its interior)."""
    n = len(poly)
    return min(seg_dist(x, y, poly[i], poly[(i + 1) % n]) for i in range(n))


def shore_dist(x, y):
    """Distance to the nearest SHORELINE, in metres.

    The shoreline is the boundary of every land polygon AND of every water body cut into one — a
    `min` over both lists, because those boundaries are the same thing seen from two sides. That
    is why the bay needs no special case: a point in the middle of it is ~50 m from `G.BAY`'s own
    bank, so it reads as shallow water, while a point 2 km out is 2 km from `G.MAIN` and reads as
    open sea.
    """
    return min(poly_dist(p, x, y) for p in (G.LAND + G.WATER))


def _smoothstep(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def _frange_m(a, b, step):
    v = a
    while v <= b:
        yield v
        v += step


def seabed(x, y):
    """The ground UNDER the water: `SHORE_Z` at the waterline, easing to `SEA_FLOOR_Z` over
    `SHELF_RUN`.

    Smoothstep rather than linear, and that is not decoration: a linear ramp puts a crease along
    the whole coast at the shore end and another one along the shelf break, and a crease in the
    ground is what `_shade_smooth_by_angle` reads as a cliff. Eased at both ends, the beach leaves
    the shore flat and the shelf arrives flat.
    """
    return SHORE_Z + (SEA_FLOOR_Z - SHORE_Z) * _smoothstep(shore_dist(x, y) / SHELF_RUN)


def bbox(poly, pad=0.0):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def resample_closed(poly, step):
    """Walk a closed polygon at a fixed spacing — so a 520 m edge is not one decision."""
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        L = math.dist(a, b)
        k = max(1, int(L / step))
        for j in range(k):
            t = j / float(k)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


# --------------------------------------------------------------------------------- hills
class Hill:
    """One contour set (`MASSIF` or `SPUR`) turned into a height function.

    Rings run OUTERMOST (lowest) to INNERMOST (highest), with a derived toe ring at `BASE_Z`
    prepended. A ring that runs out to sea is left there — see the module docstring on why the
    coast is a CUT and not a taper.
    """

    def __init__(self, name, spec, foot_grade=None):
        self.name = name
        self.cx, self.cy = float(spec["cx"]), float(spec["cy"])
        rot = float(spec.get("rot", 0.0))
        bands = [(float(rx), float(ry), float(z)) for (rx, ry, z, _tag) in spec["bands"]]
        grade = FOOT_GRADE[name] if foot_grade is None else foot_grade
        # THE DERIVED 0 m CONTOUR. Without it the outermost band's own outline is a cliff of its
        # full height -- which is exactly what the prisms were.
        self.toe_run = bands[0][2] / grade
        rings = [(bands[0][0] + self.toe_run, bands[0][1] + self.toe_run, BASE_Z)] + bands
        # Plain ellipses, NOT `pull_ashore`d: a contour that runs out to sea is still a contour,
        # and the coast clamp in `Terrain.height` is the one owner of what happens at the water.
        self.rings = [(G.ellipse(self.cx, self.cy, rx, ry, n=RING_SAMPLES, rot=rot), z)
                      for (rx, ry, z) in rings]
        self.bbox = bbox(self.rings[0][0])

    def height(self, x, y):
        """`None` where this hill has no say — outside its toe."""
        x0, y0, x1, y1 = self.bbox
        if x < x0 or x > x1 or y < y0 or y > y1:
            return None
        # INNERMOST containing ring. Scanned in full rather than stopping at the first miss: the
        # nesting is the plan's to guarantee, not this function's to assume.
        k = -1
        for i, (poly, _z) in enumerate(self.rings):
            if G.inside(poly, x, y):
                k = i
        if k < 0:
            return None
        if k == len(self.rings) - 1:
            return self.rings[k][1]                       # summit plateau — the innermost contour
        p_out, z_out = self.rings[k]
        p_in, z_in = self.rings[k + 1]
        d_out = poly_dist(p_out, x, y)
        d_in = poly_dist(p_in, x, y)
        t = d_out / max(d_out + d_in, 1e-6)
        return z_out + (z_in - z_out) * t


# ----------------------------------------------------------------------------- platforms
class Platform:
    """A reclaimed plate (`HARBOUR` +2, `AIRPORT` +4) — a real step with a DELIBERATE edge.

    Each ~20 m piece of its outline is classified once: probe `PLATFORM_PROBE` metres outboard,
    and if that lands on other land the edge is LANDWARD (it ramps at `PLATFORM_EDGE_GRADE`),
    otherwise it is a SEA WALL and keeps its full step. The airport is offshore, so nothing about
    it ramps; the harbour is fused to the mainland by a neck, so its north edge does.
    """

    def __init__(self, name, poly, z):
        self.name, self.poly, self.z = name, list(poly), float(z)
        self.run = self.z / PLATFORM_EDGE_GRADE if PLATFORM_EDGE_GRADE > 0 else 0.0
        ring = resample_closed(self.poly, PLATFORM_EDGE_STEP)
        n = len(ring)
        self.landward = []
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy) or 1.0
            nx, ny = dy / L, -dx / L
            if G.inside(self.poly, mx + nx, my + ny):
                nx, ny = -nx, -ny
            px, py = mx + nx * PLATFORM_PROBE, my + ny * PLATFORM_PROBE
            if G.on_land(px, py) and not G.inside(self.poly, px, py):
                self.landward.append((a, b))
        self.bbox = bbox(self.poly)

    def height(self, x, y):
        x0, y0, x1, y1 = self.bbox
        if x < x0 or x > x1 or y < y0 or y > y1:
            return None
        if not G.inside(self.poly, x, y):
            return None
        if not self.landward or self.run <= 0.0:
            return self.z
        d = min(seg_dist(x, y, a, b) for (a, b) in self.landward)
        return self.z * min(1.0, d / self.run)


# ------------------------------------------------------------------------------- terrain
class Terrain:
    """`height(x, y)` — the ground, everywhere, as one number.

    `relief=False` keeps the flat 2D-plan mode `build_island_v3.py` has always had: the platforms
    are still real (they genuinely are at a different level and the bridges depend on it), the
    hills are simply not there.
    """

    def __init__(self, relief=True):
        self.hills = [Hill("MASSIF", G.MASSIF), Hill("SPUR", G.SPUR)] if relief else []
        self.platforms = [Platform(n, p, z) for (n, p, z) in PLATFORM_SPECS]

    def height(self, x, y):
        z = BASE_Z
        for h in self.hills:
            hz = h.height(x, y)
            if hz is not None and hz > z:
                z = hz
        for pl in self.platforms:
            pz = pl.height(x, y)
            if pz is not None and pz > z:
                z = pz
        return z

    def surface(self, x, y):
        """The ground you can STAND on, anywhere in the world square — land where there is land,
        seabed where there is not.

        Kept separate from `height`, which stays what it has always been: the terrain FIELD, asked
        only where `G.on_land` says there is land. Everything that routes, drapes or grades a road
        wants that one — `ground_at` returns None over water on purpose, and it is what
        `check_island_ground.py` reads to report a road crossing open water. Merging the two would
        make every bay crossing look supported and silently retire that gate.

        So: `height` answers "how high is the land here", `surface` answers "what would I land on
        if I fell here". Only the ground MESH asks the second one.
        """
        return self.height(x, y) if G.on_land(x, y) else seabed(x, y)

    __call__ = height


# --------------------------------------------------------------------------- the road carve
#: How far BELOW the road surface the ground is carved, in metres.
#:
#: Not zero, and not a rendering nudge: the road mesh is opaque and covers its own corridor, so all
#: this has to do is keep the two surfaces from coplanar z-fighting where the road is dead flat on
#: dead flat ground. 0.30 m is comfortably under `MovementController.stepUpLedge` (0.35 m), so if a
#: body ever does stand on the carved ground beside the tarmac it can step back up onto it.
CARVE_CLEARANCE = 0.30

#: THE VERGE: how far past the paved edge the cap stays FLAT, in metres. **It must be at least the
#: ground mesh's own finest cell**, and the caller supplies it for that reason -- it is a property
#: of the MESH, not of the field.
#:
#: WHY IT EXISTS, and why it is not a fudge. The carved FIELD can never stand above the road: it is
#: a `min`. A MESH can, because it samples the field at grid nodes and interpolates across
#: triangles, so a cell straddling the paved edge has one corner capped to road level and the next
#: partway up the batter, and the triangle between them cuts over the carriageway. Widen the flat
#: shelf by one full cell and every node within the paved width -- and the first node outside it --
#: is capped to road level, so no triangle spanning the road can have a raised corner. That is an
#: argument, not a tuning, and it is why the number is `cell` rather than something dialled in.
#:
#: Measured on the island's whole network, 1 185 377 samples at 0.25 m across every road and 2 m
#: along it, ground standing proud of the road surface:
#:
#:     verge 0    x cell   450 samples proud, worst 3.508 m
#:     verge 0.5  x cell    35 samples proud, worst 0.941 m
#:     verge 1.0  x cell     0 samples proud, worst 0.000 m
#:
#: Subdividing instead converges far too slowly to be the answer -- 12 m -> 3 m cells took the
#: worst intrusion only 3.508 m -> 0.647 m, still past `MovementController.stepUpLedge` (0.35 m),
#: for 16x the vertices. The error is proportional to the batter's own gradient (1:1), so it is the
#: cell size that has to go, and the verge removes the cell from the road's edge entirely.
#:
#: A 12 m flat shelf either side is also not a cost worth avoiding: it is a VERGE, it sits below
#: the road, and every real road has one.
CARVE_VERGE = 12.0

#: Bucket size for the corridor index, metres. One bucket holds every segment whose reach touches
#: it, so a query looks in one bucket and finds a handful. Sized near the longest reach so the
#: buckets stay few and each holds few.
CARVE_BUCKET = 64.0


class Carve:
    """THE ROADS, AS A CEILING ON THE GROUND -- `min(natural, cap)`, never `max`.

    WHY A DEFORMATION AND NOT A BOOLEAN (`W13`, 2026-09-05, after four attempts at the boolean).
    The ground used to be cut by hanging a BOOLEAN modifier per road band on the terrain mesh, and
    every failure that cost was a property of asking an exact-CSG solver to classify a solid that
    passes through itself: a switchback's cutter overlaps its own loops, the solver has no defined
    inside for a doubly-covered region, and it answers by leaving the ground standing with nothing
    to see. Measured, from identical inputs -- the same network, the same terrain, the same sampled
    `ground_z` byte for byte -- a second Build took the touge from 2 buried stations to 9. That is
    not a bug to chase; it is the tool.

    It is also not what the industry does. Open-world terrain is a heightfield, a heightfield has
    no topology to cut, and the operation everywhere from Unreal's `Deform Landscape to Splines` to
    a Houdini road HDA is to write the road's elevation INTO the field. `Terrain.height` has been
    exactly that field since step 1, so the carve belongs here rather than in a mesh operator.

    CARVE-ONLY, AND THAT IS THE LOAD-BEARING HALF. Unreal's deform SETS the height -- it raises and
    lowers. Copying that here would be wrong twice over:

      * FILL ALREADY HAS AN OWNER. `road_support` derives NONE/FILL/PIER/CUT from
        `surface_z - ground_z` and builds the embankment toes and pier columns from it. Ground that
        rises to meet the road would build the same embankment a second time, a second way, with
        nothing to report the disagreement -- the defect shape this whole model is a reaction to.
      * IT WOULD FILL THE BAY. `W1` carries Hama-dori 18.20 m over open water on columns to
        -8.32 m. A rule that raises ground toward the road has to be special-cased away from every
        bridge and viaduct; `min` needs no case at all, because it CANNOT raise ground.

    So the two are on opposite signs of one number and can never overlap: **the terrain owns the
    cut and its batter, `road_support` owns the fill and the piers.**

    ONE DIRECTION OF DERIVATION, AND IT MATTERS. A road's own profile is derived FROM the ground
    (`bench_profile`, `grade_cone`, `seed_district_roads.height_profile` all sample `Terrain
    .height`). Carve the field those read and the next pass derives a lower road, which carves
    deeper, forever. So a `Carve` is a separate VIEW of the field (`carved_field`), handed only to
    the thing that builds the ground MESH -- never to the router, the grader or the support rule,
    which go on reading the natural ground. Alignment against the original terrain, terrain
    deformed to the finished alignment, never back. That is the same order a real road is built in.

    A HAIRPIN NEEDS NO CASE. Two legs of a switchback both propose a cap where their corridors
    overlap and the lower one wins, which draws the cut face between them; at either leg's own
    centreline that leg's cap is the lower, so each is supported. The nose of ground standing
    between the legs is what a switchback bench actually looks like. This is the single strongest
    reason to believe the operator: the geometry that defeated the boolean four times falls out of
    `min` with nothing written for it.
    """

    __slots__ = ("slope", "clearance", "verge", "_buckets", "_segs")

    def __init__(self, corridors, slope=None, clearance=CARVE_CLEARANCE, verge=CARVE_VERGE):
        """`corridors` is an iterable of `(polyline, half_width)`.

        The polyline is the road's finished centreline -- `(x, y, z)` per station, or
        `(x, y, z, half)` where the width varies along the run, which it does wherever a lane opens
        or a median tapers. `half_width` is the fallback for a plain 3-tuple."""
        self.slope = P.CUT_SLOPE if slope is None else float(slope)
        self.clearance = float(clearance)
        self.verge = float(verge)
        self._segs = []
        self._buckets = {}
        for poly, half in corridors:
            for a, b in zip(poly, poly[1:]):
                if a[0] == b[0] and a[1] == b[1]:
                    continue
                ha = (a[3] if len(a) > 3 else half) + self.verge
                hb = (b[3] if len(b) > 3 else half) + self.verge
                reach = max(ha, hb) + MAX_BENCH * self.slope
                i = len(self._segs)
                self._segs.append((a[:3], b[:3], float(ha), float(hb), reach))
                self._bucket(a, b, reach, i)

    def _bucket(self, a, b, reach, i):
        lo_x = min(a[0], b[0]) - reach
        hi_x = max(a[0], b[0]) + reach
        lo_y = min(a[1], b[1]) - reach
        hi_y = max(a[1], b[1]) + reach
        for bx in range(int(math.floor(lo_x / CARVE_BUCKET)), int(math.floor(hi_x / CARVE_BUCKET)) + 1):
            for by in range(int(math.floor(lo_y / CARVE_BUCKET)), int(math.floor(hi_y / CARVE_BUCKET)) + 1):
                self._buckets.setdefault((bx, by), []).append(i)

    def cap(self, x, y):
        """The highest the ground may stand here, or None where no road reaches.

        Inside the paved half-width the cap is the road surface less `clearance`; outside it rises
        at `slope` (run per unit of rise, `road_support.CUT_SLOPE`), so `min(natural, cap)` stops
        the batter exactly where it daylights and NO daylight point has to be computed. That is the
        whole reason the batter needs no geometry: it is the ramp, clipped by the ground itself."""
        best = None
        for i in self._buckets.get((int(math.floor(x / CARVE_BUCKET)),
                                    int(math.floor(y / CARVE_BUCKET))), ()):
            a, b, ha, hb, reach = self._segs[i]
            d, z, t = _seg_point_z(a, b, x, y)
            if d > reach:
                continue
            half = ha + (hb - ha) * t
            c = z - self.clearance
            if d > half:
                # RISE = RUN / SLOPE. `road_support.CUT_SLOPE` is run per unit of rise
                # (`cut_footprint` = `half + rise * CUT_SLOPE`), so going the other way divides.
                # It is 1.0 today, which is exactly why getting this backwards would cost nothing
                # until the day someone authors a gentler batter and every trench doubles in width.
                c += (d - half) / self.slope
            if best is None or c < best:
                best = c
        return best

    def __len__(self):
        return len(self._segs)


def _seg_point_z(a, b, x, y):
    """`(perpendicular distance, the segment's own z at the nearest point, the parameter t)`."""
    ax, ay, az = a
    bx, by, bz = b
    dx, dy = bx - ax, by - ay
    t = ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(x - px, y - py), az + t * (bz - az), t


def carved_field(field, carve):
    """`field` with `carve` as a ceiling. The ONLY thing that should ever be handed this is the
    builder of the ground MESH -- see `Carve`'s "one direction of derivation"."""
    if carve is None or not len(carve):
        return field

    def height(x, y):
        z = field(x, y)
        c = carve.cap(x, y)
        return z if c is None or c >= z else c
    return height


# ------------------------------------------------------------------------- hill roads
#: How sharp a hairpin is, in metres of turning radius -- see the value chosen below
#: (`HAIRPIN_RADIUS`, after the generator that pays for it).
HAIRPIN_RADIUS = 12.0

#: Points used to draw one hairpin. Enough that the resampler and the road kit see an ARC.
HAIRPIN_POINTS = 7


def gradient(height, x, y, d=10.0):
    """(dz/dx, dz/dy) by central difference. `d` is deliberately a road-ish length, not an epsilon:
    the contour field is smooth over hundreds of metres and a 1 mm difference would just amplify
    the ring polylines' own faceting into a direction that jitters every step."""
    return ((height(x + d, y) - height(x - d, y)) / (2.0 * d),
            (height(x, y + d) - height(x, y - d)) / (2.0 * d))


def climb_direction(height, x, y, grade, sense):
    """The unit heading that gains exactly `grade` of height per metre travelled, or None where the
    ground is gentler than that (there, go straight up the fall line and let the caller stop).

    THE WHOLE SWITCHBACK IS THIS ONE PIECE OF TRIGONOMETRY. Write the heading as
    `cos(phi) * uphill + sin(phi) * contour`; the height gained per metre is then `|grad| cos(phi)`,
    so asking for `grade` fixes `phi = acos(grade / |grad|)` and nothing else is free. On a 92%
    flank a road limited to 8% runs 85 degrees off the fall line -- which is to say it runs very
    nearly along the contour, which is what a mountain road looks like from the air. `sense` (+/-1)
    picks which way along the contour, and flipping it IS the hairpin.
    """
    gx, gy = gradient(height, x, y)
    m = math.hypot(gx, gy)
    if m < 1e-9:
        return None
    if m <= grade:
        return (gx / m, gy / m)                 # gentler than the limit: straight up is legal
    c = grade / m
    phi = math.acos(max(-1.0, min(1.0, c)))
    ux, uy = gx / m, gy / m                     # uphill
    tx, ty = -uy * sense, ux * sense            # along the contour, `sense` picks the way
    return (ux * math.cos(phi) + tx * math.sin(phi),
            uy * math.cos(phi) + ty * math.sin(phi))


def _hairpin(px, py, heading, uphill, radius, points):
    """A half-turn of `radius` at (px, py), from `heading` back to its reverse.

    A switchback that simply reverses in place is not a road -- the road kit sweeps a curve through
    the stations, and two points a step apart with opposite headings make a crease, not a bend. The
    turn is a real semicircle so both legs leave it tangentially.

    IT TURNS TOWARD THE HILL. The centre goes one radius along whichever perpendicular points
    UPHILL, because the next leg is the one above this one; turning the other way walks the road
    back down the mountain, which is what a fixed left-hand turn did -- with a short enough leg it
    hairpinned often enough to descend to sea level while reporting a perfectly legal grade the
    whole way.
    """
    hx, hy = heading
    nx, ny = -hy, hx                            # left of travel
    sense = 1.0                                 # ...and a left-hand centre turns anticlockwise
    if nx * uphill[0] + ny * uphill[1] < 0.0:   # ...unless downhill is on the left
        nx, ny = -nx, -ny
        sense = -1.0
    cx, cy = px + nx * radius, py + ny * radius
    a0 = math.atan2(py - cy, px - cx)
    out = []
    for k in range(1, points + 1):
        # THE SWEEP MUST MATCH THE SIDE THE CENTRE IS ON (2026-09-06, user-reported: "the curve is
        # not in a correct turn"). The centre already goes uphill, left or right as the slope
        # decides -- but the arc was ALWAYS walked anticlockwise, so a right-hand hairpin went the
        # long way round its own circle. Measured with the hill on the right: the road left the
        # incoming heading with a **167.1 deg cusp** instead of half a step (12.9 deg), came out at
        # -12.9 deg instead of reversing to 180, and landed 24 m on the wrong side. It reversed on
        # the spot and carried on the way it came -- which is not a hairpin, and is what the road
        # kit was sweeping a crease through. Half the shrine touge's turns are right-hand.
        a = a0 + sense * math.pi * k / points
        out.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return out


def switchback(height, start, target_z, grade, leg=260.0, step=20.0,
               inside=None, max_length=12000.0, radius=HAIRPIN_RADIUS, heading=None):
    """A grade-legal alignment from `start` up to `target_z`. Returns the polyline.

    This is the answer WORLD_REBUILD_PLAN.md `W2` names -- "the grade limit is on the road, not on
    the hill: a longer road at the same grade climbs" -- made constructive. It walks the height
    field at exactly `grade` (see `climb_direction`), turns a hairpin every `leg` metres or
    whenever the next step would leave `inside`, and stops when it reaches `target_z`, runs out of
    room, or exceeds `max_length`.

    IT IS NOT A SEARCH, and that is deliberate. A grid A* over the terrain cannot express this
    problem: on a 92% flank the legal headings lie within about 5 degrees of the contour and a grid
    only offers 8 or 16 of them, so every edge it can take is illegal and it returns nothing --
    which reads as "there is no route up this hill" and is how the plan came to record that as a
    fact. Walking the continuous field has no such quantisation.

    Returns `(pts, arc_indices)`. The arc indices are the hairpin points, and they are handed to
    `simplify` as `protect` -- a tolerance may drop what the WALK emitted and may never drop the
    turn the generator deliberately drew. See `simplify`.

    The z is NOT returned. The road's height is the seeder's business
    (`seed_district_roads.height_profile` drapes it and then imposes the grade cone), and a
    generator that also decided height would be a second owner of it -- the cone would then be
    correcting a profile that thought it was already right. What this returns is a LINE ON THE MAP
    whose ground happens to rise at the road's own limit, which is exactly what makes the cone's
    job small: it fills the dip at each hairpin and leaves the legs alone.
    """
    x, y = start
    pts = [(x, y)]
    arcs = set()                 # indices of hairpin arc points -- see `simplify(protect=...)`
    sense = 1.0
    run = 0.0
    total = 0.0
    last = heading
    while height(x, y) < target_z and total < max_length:
        d = climb_direction(height, x, y, grade, sense)
        if d is None:
            # FLAT GROUND HAS NO CONTOUR TO FOLLOW. A foot on the level is the ordinary case (that
            # is where a hill road leaves the network), and stopping there would make the whole
            # alignment one point. Carry the heading we came in on until the ground has something
            # to say.
            if last is None:
                break
            d = last
        last = d
        nx, ny = x + d[0] * step, y + d[1] * step
        blocked = (inside is not None and not inside(nx, ny))
        if blocked or run >= leg:
            gx, gy = gradient(height, x, y)
            m = math.hypot(gx, gy) or 1.0
            turn = _hairpin(x, y, d, (gx / m, gy / m), radius, HAIRPIN_POINTS)
            # A hairpin that would leave the region is not a hairpin, it is the end of the road.
            if inside is not None and not all(inside(px, py) for (px, py) in turn):
                break
            arcs.update(range(len(pts), len(pts) + len(turn)))
            pts.extend(turn)
            total += math.pi * radius
            if len(turn) >= 2:
                hx, hy = turn[-1][0] - turn[-2][0], turn[-1][1] - turn[-2][1]
                n = math.hypot(hx, hy) or 1.0
                last = (hx / n, hy / n)
            x, y = turn[-1]
            sense = -sense
            run = 0.0
            continue
        x, y = nx, ny
        pts.append((x, y))
        run += step
        total += step
    return pts, arcs


def alignment_grade(height, pts):
    """(climb, length, average grade) of the GROUND along a polyline. The number a road built on
    it has to satisfy: a profile can be raised at a dip, but nothing can make a road climb faster
    than its limit over its whole length."""
    climb = 0.0
    length = 0.0
    prev = height(*pts[0])
    for a, b in zip(pts, pts[1:]):
        z = height(*b)
        climb += max(0.0, z - prev)
        prev = z
        length += math.dist(a, b)
    return climb, length, (climb / length if length else 0.0)


def hill_road(height, start, target_z, limit, leg=500.0, step=20.0, inside=None,
              radius=HAIRPIN_RADIUS, tries=10, heading=None):
    """A switchback alignment whose OVERALL ground grade is inside `limit`, not just its legs.

    THE HAIRPINS ARE PART OF THE CLIMB, and forgetting that is what makes a generated mountain road
    unbuildable. A half-turn of radius `R` on a slope `m` moves the road `2R` ACROSS the slope, so it
    gains `2R*m` of height in `pi*R` of path -- on the spur (58%) that is 21 m in 57 m, a 37% grade,
    and eight of them contributed more than half of the whole 280 m climb. Walk the legs at the
    limit and the alignment's average comes out at 10.1% against an 8.1% road.

    Nothing downstream can absorb that. `seed_district_roads.height_profile` imposes a grade cone,
    which can only RAISE a station -- so an alignment that climbs faster than its own limit is
    corrected by lifting its lower end until the whole thing fits, and the first measurement of this
    road was a **57 m fill at the foot of the mountain**: a sky-road, green by every check, produced
    by a generator and a corrector that were each right on their own.

    So the LEG grade is searched, not assumed: bisect it down until the alignment's own average
    (`alignment_grade`) is inside the limit. What is left over is the local swing at each hairpin,
    which is a real benched turn -- and because the cone only ever fills, it comes out as a piered
    hairpin rather than a cutting, which is both buildable and what a Japanese mountain road
    actually looks like.

    Returns `(pts, arc_indices)` -- the alignment and which of its points are hairpin arc, so the
    caller's `simplify` can protect the turn it deliberately drew.
    """
    lo, hi = 0.0, limit
    best = None
    for _ in range(tries):
        g = 0.5 * (lo + hi)
        pts, arcs = switchback(height, start, target_z, g, leg=leg, step=step, inside=inside,
                               radius=radius, heading=heading)
        climb, length, avg = alignment_grade(height, pts)
        reached = height(*pts[-1]) >= target_z - 1.0
        if reached and avg <= limit:
            best = (pts, arcs)
            lo = g                      # legal: try to get there faster (shorter road)
        else:
            hi = g
        if hi - lo < 1e-4:
            break
    return best if best is not None else (pts, arcs)


def simplify(pts, tol=6.0, protect=()):
    """Douglas-Peucker. The walk emits a point every `step`; a station list wants the SHAPE.

    `protect` is a set of INDICES into `pts` that may never be dropped, and it is not an
    optimisation -- it is the difference between a hairpin and a fold.

    A HAIRPIN ARC IS NOT A SHAPE TO APPROXIMATE; IT IS THE SHAPE (2026-09-06, user-reported: "the
    curve is not in a correct turn"). Douglas-Peucker keeps a point only when it stands further
    than `tol` from the chord across its neighbours, and an arc's own sagitta is tiny by
    construction: one step of a `HAIRPIN_POINTS`-point semicircle at `HAIRPIN_RADIUS` stands
    `R(1 - cos(pi/2N))` = **0.30 m** off its chord, and a whole quarter-circle only 3.5 m. Against
    `hill_roads`' 3.0 m tolerance that deleted every arc point but the apex, so a 7-point
    semicircle came through as THREE points and the alignment folded back on itself: measured on
    the shrine touge, **6 stations turning more than 100 deg, worst 147.3 deg, tightest implied
    radius 5.6 m** -- against the 12 m the hairpin was drawn at. The road kit then swept a crease.

    It is the same rule `seed_district_roads.simplify` already keeps for junction mouths ("a stop
    line is not a shape"), one level up: a tolerance may remove what the WALK happened to emit, and
    may never remove what the generator deliberately drew."""
    keep = set(protect)

    def walk(i0, i1):
        a, b = pts[i0], pts[i1]
        if i1 - i0 < 2:
            return [i0, i1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        worst, at = -1.0, i0
        for i in range(i0 + 1, i1):
            if i in keep:                       # a protected point forces the split at itself
                worst, at = float("inf"), i
                break
            if L < 1e-9:
                d = math.dist(pts[i], a)
            else:
                d = abs(dy * pts[i][0] - dx * pts[i][1] + b[0] * a[1] - b[1] * a[0]) / L
            if d > worst:
                worst, at = d, i
        if worst <= tol:
            return [i0, i1]
        return walk(i0, at)[:-1] + walk(at, i1)

    if len(pts) < 3:
        return list(pts)
    return [pts[i] for i in walk(0, len(pts) - 1)]


#: Hairpin radius, metres. 12, not 18, and the difference is 6 m of pier: a half-turn moves the
#: road `2R` across the slope, so on the spur's 58% flank every extra metre of radius is 1.16 m of
#: height the grade cone then has to fill. 12 m is a real touge hairpin and it is what brought the
#: worst fill on the shrine road from 20.2 m down to 14.7 m.
HAIRPIN_RADIUS = 12.0


def hill_roads(terrain=None):
    """`island_v3_geom.HILL_ROADS`, realised: each one's authored approach followed by a derived
    climb. The names and the endpoints are the plan's; the shape between them is the terrain's."""
    t = terrain or Terrain(relief=True)
    out = []
    for name, approach, target_z in G.HILL_ROADS:
        limit = P.MAX_GRADE[G.arterial_class(name)]
        foot = approach[-1]
        head = None
        if len(approach) >= 2:
            hx, hy = foot[0] - approach[-2][0], foot[1] - approach[-2][1]
            n = math.hypot(hx, hy) or 1.0
            head = (hx / n, hy / n)
        climb, arcs = hill_road(t.height, foot, target_z, limit, inside=G.on_land, heading=head)
        # THE HAIRPINS ARE PROTECTED. A 3 m tolerance is right for the walk (it emits a point every
        # 20 m along a contour and most of them say nothing) and catastrophic for an arc, whose
        # points stand 0.30 m off their own chord by construction. See `simplify`.
        out.append((name, list(approach[:-1]) + simplify(climb, 3.0, protect=arcs)))
    return out


#: Road classes whose grade is judged over the WHOLE ALIGNMENT rather than sample by sample.
#:
#: A mountain road is BENCHED: its hairpins are cut-and-fill platforms, so the ground across a turn
#: swings far more steeply than the road ever does. Measuring a touge the way a trunk road is
#: measured reports 85% at every hairpin and calls the only legal alignment on the hill a failure.
#: What actually has to hold is the pair below -- the average grade over the length (nothing can
#: make a road climb faster than its limit end to end) and a bound on how much structure the
#: benching costs.
BENCHED_CLASSES = ("touge",)

#: How much fill a benched road may need at its worst station, metres. 25 m is a tall viaduct pier
#: and a generous ceiling; the shrine road sits at 15. It exists to catch the failure this was
#: found by -- an alignment climbing faster than its own limit, which the grade cone "fixes" by
#: lifting the foot of the mountain until the whole road fits, giving a 57 m sky-road that every
#: per-sample check passes.
MAX_BENCH = P.CUT_MAX          # ONE OWNER: `road_support.CUT_MAX`, re-exported by `island_v3_plan`.
#: A bench deeper than this is a tunnel, and that is the SAME number the support rule uses to
#: decide it -- they were 25.0 and 3.0 and the touge fell in the gap between them (`W13`).


def grade_cone(stations, zs, limit):
    """Raise a height profile until no span exceeds `limit`. THE one owner of that operation.

    Two passes, and both are needed: forward bounds how fast the profile may DROP going forward,
    backward bounds it going back, and together they bound `|dz| <= limit * span` everywhere. It
    only ever raises, which is what makes a road over water a bridge and a hairpin a pier rather
    than a cutting.

    `seed_district_roads.height_profile` builds the road with this and `report` predicts the road
    with it. While they were two copies the gate could pass an alignment the builder would turn
    into a viaduct in the sky, which is exactly what happened to the first shrine road.
    """
    z = list(zs)
    n = len(stations)
    for i in range(1, n):
        z[i] = max(z[i], z[i - 1] - limit * (math.dist(stations[i - 1], stations[i]) or 1.0))
    for i in range(n - 2, -1, -1):
        z[i] = max(z[i], z[i + 1] - limit * (math.dist(stations[i], stations[i + 1]) or 1.0))
    return z


#: Station spacing the seeder lays roads at, metres, and the closest two stations may sit.
STATION_SPACING = 70.0
STATION_MIN_SPAN = 10.0


def stations(pts, spacing=STATION_SPACING, keep=STATION_MIN_SPAN):
    """Where a road's stations go: evenly by arc length, AND on every authored vertex.

    THE ONE OWNER, shared with `seed_district_roads.resample`. `bench_depth` below predicts the
    fill the seeder will build, and it can only do that if it puts the stations in the same places
    -- while they were two rules the model said 14.7 m of pier and the build produced 25.6 m,
    because one of them stepped over the hairpins and the other did not.

    An authored vertex matters because on a derived alignment the vertices ARE the shape: the
    shrine road's hairpins are 38 m arcs and the even spacing is 70 m, so an even-only rule cuts
    every corner. Anything within `keep` of its neighbour is dropped -- a vertex 30 cm from an even
    station is the same station, not a second one.
    """
    cum, acc = [0.0], 0.0
    for a, b in zip(pts, pts[1:]):
        acc += math.dist(a, b)
        cum.append(acc)
    total = cum[-1]
    if total < spacing:
        return [pts[0], pts[-1]]

    def at(s):
        i = 0
        while i + 2 < len(cum) and cum[i + 1] < s:
            i += 1
        span = cum[i + 1] - cum[i]
        t = 0.0 if span <= 1e-9 else (s - cum[i]) / span
        a, b = pts[i], pts[i + 1]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    n = max(1, int(round(total / spacing)))
    # ROUNDED BEFORE THE SET, to a micron. The last even mark is `total * n / n` and the last
    # cumulative length is `total` -- the same number arrived at two ways, and in floating point
    # they differ in the last bit often enough. `set` then keeps both, the "never drop the ends"
    # rule keeps the second, and the road ends with two stations 1e-13 m apart: `station_coincident`,
    # "a zero-length taper", which refuses the build outright.
    marks = sorted({round(total * k / n, 6) for k in range(n + 1)} | {round(c, 6) for c in cum})
    out, last = [], None
    for k, s in enumerate(marks):
        if last is not None and s - last < keep and k != len(marks) - 1:
            continue
        out.append(at(s))
        last = s
    return out


def grade_cone_down(stations, zs, limit):
    """`grade_cone`'s mirror: LOWER a profile until no span exceeds `limit`. The greatest
    grade-legal profile that stays at or under the ground, where `grade_cone` is the least one that
    stays at or over it."""
    z = list(zs)
    n = len(stations)
    for i in range(1, n):
        z[i] = min(z[i], z[i - 1] + limit * (math.dist(stations[i - 1], stations[i]) or 1.0))
    for i in range(n - 2, -1, -1):
        z[i] = min(z[i], z[i + 1] + limit * (math.dist(stations[i], stations[i + 1]) or 1.0))
    return z


def bench_profile(stations, zs, limit):
    """A grade-legal profile that CUTS INTO the high ground and FILLS the low — a bench.

    THE ROAD SHOULD BE IN THE MOUNTAIN, NOT ON STILTS OVER IT (2026-08-31, from a walk-test of the
    shrine road). `grade_cone` only ever raises, which is exactly right for a bay crossing — you do
    not cut a bridge into water — and exactly wrong for a hill: it answered every hairpin with a
    pier, and 71 of the touge's 90 stations came out elevated, up to 16.3 m, standing over a
    hillside the ground cut had already punched a road-shaped slot through.

    The midpoint of the two envelopes is the fix, and it is grade-legal for free: the constraint
    `|dz| <= limit * span` is convex, so the average of two profiles that satisfy it satisfies it
    too. Measured on the shrine road it turns +16.3/-0.0 into a symmetric **+8.1/-8.1**, at an
    unchanged 8.10% worst grade — half the pier, and the other half is now a cutting.

    What it does NOT do is batter the cut faces. `road_support` grows an embankment toe for FILL
    and columns for PIER and builds nothing at all for CUT, while the ground cut is a vertical
    prism over the road's own footprint — so a benched stretch comes out in a vertical-walled slot
    rather than between sloped faces. That is the piece of hand work the road kit still owes a
    mountain road.
    """
    up = grade_cone(stations, zs, limit)
    down = grade_cone_down(stations, zs, limit)
    return [0.5 * (a + b) for a, b in zip(up, down)]


def bench_depth(height, pts, limit, spacing=STATION_SPACING):
    """The worst STRUCTURE a road on this alignment needs — the deeper of its cut and its fill, in
    metres, from `bench_profile` at the seeder's own stations. It is the number that says whether a
    hill road is a road or a viaduct in the sky, and it has to be the same function the seeder
    builds with or the gate is predicting a road nobody is going to make."""
    st = stations(pts, spacing)
    ground = [height(x, y) for (x, y) in st]
    z = bench_profile(st, ground, limit)
    d = [a - b for a, b in zip(z, ground)]
    return max(max(d), -min(d))


def road_network(terrain=None):
    """EVERY road the island builds -- the plan's authored arterials plus the derived hill roads.

    One owner, because three things ask it and they must not diverge: `seed_district_roads` builds
    it, `island_v3_reach` measures what it serves, and `report`/`check_island_ground.py` hold each
    road to its own grade class. While the hill roads lived only in the seeder, the reach gate was
    scoring a network the piece did not build."""
    return list(G.ARTERIALS) + hill_roads(terrain)


# -------------------------------------------------------------------------------- report
def ground_at(x, y, height):
    """The ground under one station, or `None` where there is none — over water, or off the map.

    `Terrain.height` answers everywhere, because it is a field; the LAND is where that field is
    realised as a mesh, and `G.on_land` is the one owner of that (it is what the ground mesh is
    cut to, water holes and all).
    """
    return height(x, y) if G.on_land(x, y) else None


def profile(pts, height, step=10.0):
    """Ground under a polyline, sampled every `step` metres — the shape `check_island_ground.py`
    measures, computed from the MODEL so a routing change can be tried in milliseconds."""
    out, s = [], 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        n = max(1, int(seg / step))
        for k in range(n):
            t = k / float(n)
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            out.append((s, ground_at(x, y, height), x, y))
            s += seg / n
    if pts:
        px, py = pts[-1]
        out.append((s, ground_at(px, py, height), px, py))
    return out


def worst_grade(prof, step=10.0):
    """(worst step in metres, the sample it happens at, total ascent).

    A sample with no ground under it (`None`) BREAKS the walk rather than being skipped over:
    there is no ground grade across a river mouth, and pretending the two banks are adjacent
    would invent one.
    """
    worst, at, climb = 0.0, None, 0.0
    for a, b in zip(prof, prof[1:]):
        if a[1] is None or b[1] is None:
            continue
        d = b[1] - a[1]
        climb += max(0.0, d)
        if abs(d) > worst:
            worst, at = abs(d), b
    return worst, at, climb


def report(step=10.0, terrain=None):
    """The arterial table, from the model. Mirrors `check_island_ground.py`'s verdicts so the two
    cannot drift; that one is still the gate, because it measures the BUILT mesh."""
    t = terrain or Terrain(relief=True)
    net = road_network(t)
    print("%-14s %-8s %7s %6s %6s %8s %7s  %s"
          % ("road", "class", "length", "zmin", "zmax", "step", "carried", "verdict"))
    bad = 0
    for name, pts in net:
        kind = G.arterial_class(name)
        limit = P.MAX_GRADE[kind]
        prof = profile(pts, t.height, step)
        zs = [p[1] for p in prof if p[1] is not None]
        holes = [p for p in prof if p[1] is None]
        carried = [p for p in holes if P.bridge_at(p[2], p[3])]
        w, at, _climb = worst_grade(prof, step)
        grade = w / step
        verdict = "ok"
        if len(carried) < len(holes):
            p = [h for h in holes if not P.bridge_at(h[2], h[3])][0]
            verdict = "NO GROUND x%d, e.g. (%.0f, %.0f) -- bridge it or move it" % (
                len(holes) - len(carried), p[2], p[3])
        elif kind in BENCHED_CLASSES:
            _c, _l, avg = alignment_grade(t.height, pts)
            bench = bench_depth(t.height, pts, limit)
            if avg > limit + 1e-9:
                verdict = "average grade %.1f%% > %.1f%% over its own length" % (
                    avg * 100.0, limit * 100.0)
            elif bench > MAX_BENCH:
                verdict = "benched %.1f m > %.1f m -- this is a viaduct, not a road" % (
                    bench, MAX_BENCH)
            else:
                verdict = "ok (benched: avg %.1f%%, worst cut/fill %.1f m)" % (avg * 100.0, bench)
        elif grade > limit + 1e-9:
            verdict = "grade %.1f%% > %.1f%% (%s) at (%.0f, %.0f)" % (
                grade * 100.0, limit * 100.0, kind, at[2], at[3])
        if not verdict.startswith("ok"):
            bad += 1
        print("%-14s %-8s %7.0f %6.1f %6.1f %8.2f %7d  %s"
              % (name, kind, prof[-1][0], min(zs), max(zs), w, len(carried), verdict))
    print("\n%d of %d road(s) sit on ground their own class can follow"
          % (len(net) - bad, len(net)))
    return bad


# --------------------------------------------------------------------------- self-tests
def _selftest():
    t = Terrain(relief=True)
    massif, spur = t.hills

    # a derived toe ring exists and is the 0 m contour
    assert massif.rings[0][1] == BASE_Z and spur.rings[0][1] == BASE_Z
    assert len(massif.rings) == len(G.MASSIF["bands"]) + 1

    # the summit is the innermost contour's height, not something interpolated past it
    peak = G.MASSIF["bands"][-1][2]
    assert abs(t.height(G.MASSIF["cx"], G.MASSIF["cy"]) - peak) < 1e-6

    # THE WHOLE POINT: no wall. Walk a ray from outside the toe to the summit and assert every
    # 10 m step is a slope, not a cliff -- the prisms failed this at 120 m, at their own outline.
    #
    # The bound is the plan's OWN steepest contour spacing, not a taste: the massif's +120 and
    # +240 rings are 72 m apart on this axis, so the interior genuinely climbs at 167% and a
    # 10 m sample genuinely steps 16.7 m. What must not exist is a step with no run under it.
    cx, cy = G.MASSIF["cx"], G.MASSIF["cy"]
    ray = [(y, t.height(cx, y)) for y in (cy - G.WORLD * 0.6 + k * 10.0 for k in range(300))
           if G.on_land(cx, y)]
    steepest = max(abs(b[1] - a[1]) for a, b in zip(ray, ray[1:]))
    # The bound is 10 m of run at the steepest gap the plan ships, plus slack — expressed as a
    # RATIO of the contour data so it survives `G.SCALE` instead of being a metre count.
    steepest_interval = max((b[2] - a[2]) / min(a[0] - b[0], a[1] - b[1]) for a, b in
                            zip(G.MASSIF["bands"], G.MASSIF["bands"][1:]))
    limit = 10.0 * steepest_interval + 1.0
    assert steepest < limit, "wall of %.1f m on the massif's south axis" % steepest

    # ...and the FOOT is at FOOT_GRADE, which is the number the whole trade is made against:
    # the climb from 0 to the outermost contour takes the toe run, not a single sample.
    rise = [y for (y, z) in ray if z > 0.5]
    top = [y for (y, z) in ray if z >= massif.rings[1][1] - 0.5]
    assert rise and top, "the massif's south axis never leaves sea level"
    assert (top[0] - rise[0]) > massif.toe_run * 0.8, \
        "the toe climbed %.0f m of run, expected ~%.0f" % (top[0] - rise[0], massif.toe_run)

    # continuity at the toe: on the outermost ring itself the ground is still ~sea level
    px, py = massif.rings[0][0][0]
    assert t.height(px, py) < 1.0


    # THE BEACH IS WALKABLE IN BOTH DIRECTIONS, and this is the assertion that says so. The land
    # stops at `BASE_Z` and the water is at `SEA_LEVEL_Z`, so if the sea floor started at the
    # waterline the island would be ringed by a 0.8 m wall -- swimmable to, and impossible to climb
    # out of, because `MovementController.stepHeight` is 0.35 m. Three things have to hold:
    STEP_HEIGHT = 0.35              # MovementController.stepHeight, quoted so the link is visible
    assert SEA_LEVEL_Z < SHORE_Z < BASE_Z, \
        "the shore must be DRY (above the water) and BELOW the land, or there is no beach"
    assert BASE_Z - SHORE_Z <= STEP_HEIGHT, \
        "the step from beach to land is %.2f m, over stepHeight %.2f" % (BASE_Z - SHORE_Z, STEP_HEIGHT)
    #   ...and the dry strip is a beach, not a lip: walk out until the floor passes the water line.
    beach = next(d for d in _frange_m(0.0, SHELF_RUN, 1.0)
                 if SHORE_Z + (SEA_FLOOR_Z - SHORE_Z) * _smoothstep(d / SHELF_RUN) <= SEA_LEVEL_Z)
    assert beach > 10.0, "the dry beach is only %.0f m wide" % beach

    # flat away from everything
    assert t.height(0.0, 0.0) == BASE_Z

    # platforms: the harbour ramps where it meets the neck, the airport is all sea wall
    harb = [p for p in t.platforms if p.name == "harbour"][0]
    air = [p for p in t.platforms if p.name == "airport"][0]
    assert harb.landward, "harbour has no landward edge -- the neck went missing"
    assert not air.landward, "airport is offshore; it should be sea wall all round"
    hx, hy = (sum(p[0] for p in G.HARBOUR) / len(G.HARBOUR),
              sum(p[1] for p in G.HARBOUR) / len(G.HARBOUR))
    assert abs(t.height(hx, hy) - harb.z) < 1e-6                 # deep inside the harbour

    # the ramp really is at the platform grade, not a step — walked across the landward edge
    ex, ey = harb.landward[0][0]
    zs = [t.height(ex, ey - harb.run + k * 10.0)
          for k in range(int(2 * harb.run / 10.0) + 1)]
    for a, b in zip(zs, zs[1:]):
        assert abs(b - a) <= 10.0 * PLATFORM_EDGE_GRADE + 1e-6, "harbour edge steps %.2f m" % (b - a)

    # THE BAY IS A HOLE, and the bridge over it is what makes Hama-dori legal. `on_land` excludes
    # `G.WATER`, so the ground mesh has a real 240 m gap at the bay -- exactly where `BAY_BRIDGE`
    # is authored. Every one of Hama-dori's off-land stations must be on that deck.
    hama = [pts for (nm, pts) in G.ARTERIALS if nm == "Hama-dori"][0]
    holes = [p for p in profile(hama, t.height, 10.0) if p[1] is None]
    assert holes, "Hama-dori no longer crosses the bay -- has the water gone?"
    loose = [p for p in holes if not P.bridge_at(p[2], p[3])]
    assert not loose, "%d of Hama-dori's %d bay stations are on no deck, e.g. (%.0f, %.0f)" % (
        len(loose), len(holes), loose[0][2], loose[0][3])

    # ---- A HAIRPIN IS A TANGENTIAL HALF-TURN, ON EITHER SIDE. Both halves were defects
    # (2026-09-06): the arc was always swept anticlockwise however the centre was placed, so a
    # right-hand turn went the long way round its own circle and entered with a 167 deg cusp; and
    # `hill_roads`' Douglas-Peucker then deleted every arc point but the apex, because an arc's
    # sagitta (0.30 m per step at R=12, N=7) is far under its 3.0 m tolerance.
    for uphill, side in (((0.0, 1.0), "left"), ((0.0, -1.0), "right")):
        turn = _hairpin(0.0, 0.0, (1.0, 0.0), uphill, HAIRPIN_RADIUS, HAIRPIN_POINTS)
        step_deg = 180.0 / HAIRPIN_POINTS
        entry = math.degrees(math.atan2(turn[0][1], turn[0][0]))
        assert abs(entry) < step_deg, \
            "a %s-hand hairpin must leave the heading tangentially, not with a %.0f deg cusp" % (
                side, abs(entry))
        # a half-turn reverses the road and moves it 2R ACROSS the slope, toward the hill
        ex, ey = turn[-1][0] - turn[-2][0], turn[-1][1] - turn[-2][1]
        assert abs(abs(math.degrees(math.atan2(ey, ex))) - 180.0) < step_deg, \
            "a %s-hand hairpin must come out reversed" % side
        assert abs(turn[-1][0]) < 1e-6 and abs(abs(turn[-1][1]) - 2 * HAIRPIN_RADIUS) < 1e-6, \
            "a %s-hand hairpin must land 2R across, on the uphill side" % side
        assert turn[-1][1] * uphill[1] > 0.0, "...and the hill is which side that is"
        # every point is ON the circle -- it is an arc, not a chord walk
        cy = uphill[1] * HAIRPIN_RADIUS
        for (x, y) in turn:
            assert abs(math.hypot(x - 0.0, y - cy) - HAIRPIN_RADIUS) < 1e-6

    # A PROTECTED POINT SURVIVES ANY TOLERANCE. An arc's own sagitta is below every sane tolerance,
    # so without this the generator's turn is deleted by the simplifier that follows it.
    arc = [(0.0, 0.0)] + _hairpin(0.0, 0.0, (1.0, 0.0), (0.0, 1.0),
                                  HAIRPIN_RADIUS, HAIRPIN_POINTS)
    assert len(simplify(arc, 3.0)) < len(arc), "the tolerance really is coarser than the arc"
    kept = simplify(arc, 3.0, protect=range(1, len(arc)))
    assert len(kept) == len(arc), "a protected arc must survive: %d of %d" % (len(kept), len(arc))
    print("OK: a hairpin is tangential on both sides, lands 2R uphill, and survives simplify")

    # ---- THE ROAD CARVE. See `Carve` for why it is a `min` and why the verge is one ground cell.
    road = [(0.0, 0.0, 10.0), (200.0, 0.0, 10.0)]
    bare = Carve([(road, 8.0)], verge=0.0)
    # inside the pavement the cap is the road less the clearance; outside it rises at CUT_SLOPE...
    assert abs(bare.cap(100.0, 0.0) - (10.0 - CARVE_CLEARANCE)) < 1e-9
    assert abs(bare.cap(100.0, 8.0) - (10.0 - CARVE_CLEARANCE)) < 1e-9
    assert abs(bare.cap(100.0, 12.0) - (10.0 - CARVE_CLEARANCE + 4.0 / P.CUT_SLOPE)) < 1e-9
    # ...and it agrees with the support rule's own footprint: a 4 m rise reaches `cut_footprint`.
    assert abs(P.CUT_MAX and (8.0 + 4.0 * P.CUT_SLOPE) - 12.0) < 1e-9
    # past MAX_BENCH of rise there is nothing left to say
    assert bare.cap(100.0, 8.0 + MAX_BENCH * P.CUT_SLOPE + 1.0) is None
    # RISE = RUN / SLOPE: a gentler batter makes the trench WIDER, never narrower.
    gentle = Carve([(road, 8.0)], slope=2.0, verge=0.0)
    assert gentle.cap(100.0, 20.0) < bare.cap(100.0, 20.0)
    # the verge holds the cap FLAT for one more cell, which is the whole mesh-sampling argument
    verged = Carve([(road, 8.0)], verge=CARVE_VERGE)
    assert abs(verged.cap(100.0, 8.0 + CARVE_VERGE) - (10.0 - CARVE_CLEARANCE)) < 1e-9
    assert verged.cap(100.0, 8.0 + CARVE_VERGE + 4.0) > verged.cap(100.0, 8.0 + CARVE_VERGE)
    # a width that varies along the run is honoured per station, not averaged
    taper = Carve([([(0.0, 0.0, 10.0, 4.0), (100.0, 0.0, 10.0, 14.0)], 8.0)], verge=0.0)
    assert taper.cap(100.0, 9.0) < taper.cap(0.0, 9.0), "the wide end must reach further"
    # CARVE-ONLY: it may never raise ground. That is what keeps the bay a bay.
    high = carved_field(lambda x, y: -50.0, Carve([(road, 8.0)]))
    assert high(100.0, 0.0) == -50.0, "a carve must never fill"
    # and a hairpin needs no case: the LOWER leg wins between two legs, each leg clears itself
    legs = Carve([([(0.0, 0.0, 100.0), (100.0, 0.0, 100.0)], 8.0),
                  ([(0.0, 24.0, 108.0), (100.0, 24.0, 108.0)], 8.0)], verge=0.0)
    assert abs(legs.cap(50.0, 0.0) - (100.0 - CARVE_CLEARANCE)) < 1e-9      # lower leg clear
    assert abs(legs.cap(50.0, 24.0) - (108.0 - CARVE_CLEARANCE)) < 1e-9     # upper leg clear too
    assert legs.cap(50.0, 12.0) > 100.0, "the nose of ground between the legs must survive"

    print("island_v3_terrain: self-tests OK  "
          "(massif toe %.0f m, spur toe %.0f m, carve verge %.0f m)"
          % (massif.toe_run, spur.toe_run, CARVE_VERGE))


if __name__ == "__main__":
    _selftest()
    print()
    report()

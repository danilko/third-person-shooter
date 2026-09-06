"""seed_district_roads.py -- lay a district's road network FROM THE ISLAND V3 PLAN.

    blender --background --python-exit-code 1 \
            assets/world_source/pieces/Piece_3_2.blend \
            --python blender/tools/seed_district_roads.py -- --piece Piece_3_2 [--save]

WHY THIS EXISTS. `island_v3_to_roadkit.py` did this job for the previous road model and is dead
code now: it drives `rka.build_segment_from_curve` and `rka.build_intersection`, both deleted in
the point/port rewrite. The island PLAN survived that rewrite -- `tools/island_v3_geom.ARTERIALS`
is still the authored layout, in true world metres -- so what was actually lost was the bridge
between the plan and the authoring tool. This is that bridge, rebuilt for the point graph.

WHAT IT DOES, and what it deliberately does NOT do:

    1. clip     every arterial to the district's own square, in the district's LOCAL frame
                (a piece's `.blend` is authored around its own origin; `piece_registry` holds
                where that origin sits in the world).
    2. resample coarse plan polylines -- an arterial is 7-10 points across the whole island --
                to a station every `--spacing` metres, THROUGH the authored points, so the chain
                has stations where a road needs them and still passes through the plan exactly.
    3. author   one road per clipped run, built by pressing the SAME OPERATORS AN ARTIST PRESSES
                (`rka.new_road`, `rka.extend_road`) -- never by writing the data model directly.
                That is `Add Sample Network`'s own rule and it exists for a reason: a fixture that
                writes the model can be perfect while the gestures it bypasses are broken.
    4. cross     where two seeded roads intersect inside the district, split both chains at the
                crossing and `rka.make_intersection` the four mouths.

It does NOT build, validate, export or bake -- those are the artist's own buttons, and a seeder
that ran them would hide which step failed. It also does not touch the district's existing
`STREET` content: the old baked `_Road` mesh stays until you delete it, so you can see the new
network against the layout it replaces.

THE TIERS ARE THE PLAN'S OWN (`island_v3_to_roadkit.TIERS`), not re-invented here: T2 is a 2+2
arterial with a 3 m median and 4 m footways at 50 km/h.
"""
import bpy, os, sys, math, argparse

HERE = os.path.dirname(os.path.realpath(__file__))
BLENDER_SRC = os.path.dirname(HERE)
REPO = os.path.dirname(BLENDER_SRC)
for p in (os.path.join(BLENDER_SRC, "lib"), os.path.join(BLENDER_SRC, "addons"),
          os.path.join(REPO, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import island_v3_geom as G                                                   # noqa: E402
import island_v3_plan as P                                                    # noqa: E402
import island_v3_terrain as IT                                                # noqa: E402
import piece_registry as pr                                                  # noqa: E402
from road_kit_authoring import point_model as pm                             # noqa: E402
from road_kit_authoring import point_solve as psolve                         # noqa: E402

HALF = 252.0                       #: a district's half-extent, metres (504 m cell)

#: The shortest span the seeder will leave between a junction mouth and the next station along.
#: Anything closer is not a span, it is a duplicate point wearing a station's name.
MIN_SPAN = 10.0

#: LANE WIDTH IS AN ARCADE NUMBER, NOT A HIGHWAY ONE (2026-08-31, from a walk-test: "the current
#: vehicle will occupy entire road").
#:
#: Measured: the car's hull is 2.00 m wide and its wheels sit at +-1.1 m, so its real footprint on
#: the road is ~2.4 m. In a 3.25 m lane that is 74% of the lane with 0.42 m of air either side --
#: correct to the book (a real lane is 3.25-3.5 m and a real car is 1.8 m) and wrong for the game,
#: because a 2.4 m car in a 3.25 m lane drives like a lorry in a tunnel. GTA V and the
#: arcade-racing house style run visibly wider than life for exactly this reason: the lane is the
#: player's margin for error, and generosity there is what makes a car feel placeable at speed.
#:
#: 4.5 m leaves 1.05 m either side (the car takes 53% of its lane). That is the wide end of what
#: was asked for and it is the end the reference games sit at.
LANE_WIDTH = 4.5

#: The plan's own tier table, copied rather than imported: `island_v3_to_roadkit` is dead code
#: against the current addon and importing it would resurrect that dependency.
#:
#: Total paved width follows from it, and `island_v3_plan.ROAD_HALF` carries the same number for
#: the plan diagram's ribbons -- T2 is `4*LANE_WIDTH + median + 2*walk` = 29.0 m (half 14.5),
#: T3 is `2*LANE_WIDTH + 2*walk` = 16.0 m (half 8.0).
#: `kerb` is the RISE of the kerb line, and with it the height the footway is lifted to -- the
#: "gutter/curb on ground roads" a walk-test asked for (2026-09-04) and `W3`'s finding from the
#: other side. 0.15 m is the schema default and the standard kerb; `MovementController.stepUpLedge`
#: is what makes it walkable, so it can be a real step rather than a painted line.
T2 = dict(lane_width=LANE_WIDTH, lanes_fwd=2, lanes_bwd=2, median_width=3.0, walk=4.0, kerb=0.15,
          speed=50.0)
T3 = dict(lane_width=LANE_WIDTH, lanes_fwd=1, lanes_bwd=1, median_width=0.0, walk=3.5, kerb=0.15,
          speed=40.0)

#: Which plan roads to seed, and at what tier. Everything the plan calls an arterial is T2; a
#: `touge` is a MOUNTAIN road and is T3 -- one lane each way, no median. Widening a hairpin stack to
#: four lanes would not be a road, it would be a car park on a cliff, and every extra metre of
#: width is another metre of pier under the outside of every turn.
def tier_for(name):
    return T3 if G.arterial_class(name) == "touge" else T2


def _deck_half(tier):
    """Half the road's WHOLE built width -- carriageway, median and both footways (`W16`)."""
    lanes = tier["lanes_fwd"] + tier["lanes_bwd"]
    return (lanes * tier["lane_width"] + tier["median_width"]) / 2.0 + tier["walk"]


# ------------------------------------------------------------------------------- geometry

def clip_runs(pts, cx, cy, half=HALF, step=8.0):
    """The parts of a world-space polyline inside the district, in LOCAL coordinates.

    IT EMITS THE POLYLINE'S OWN VERTICES plus a point wherever it crosses the district boundary --
    not a sample every `step` metres. `step` is only how finely the CROSSING is resolved; the
    segments between vertices are straight and a sample in the middle of one says nothing.

    It used to return every 8 m sample, and that was harmless right up until `resample` started
    preserving authored vertices (`island_v3_terrain.stations`): by the time it ran there were no
    authored vertices left to tell from the fill, so every 8 m sample looked like one and survived
    the 10 m spacing floor. The island came out with **1 504 stations instead of 367** -- four times
    the road geometry, four times the build, and a station every 10 m on a straight arterial.

    Still sampled rather than analytically clipped, because a plan polyline can enter, leave and
    re-enter one district and each visit is its own road; that part was always right.
    """
    def local(p):
        return (p[0] - cx, p[1] - cy)

    def inside(p):
        return abs(p[0]) <= half and abs(p[1]) <= half

    marks = []
    for a, b in zip(pts, pts[1:]):
        la = local(a)
        marks.append((la, inside(la)))
        n = max(1, int(math.dist(a, b) / step))
        prev_in = inside(la)
        for i in range(1, n):
            t = i / float(n)
            p = local((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            now_in = inside(p)
            if now_in != prev_in:
                marks.append((p, now_in))       # the boundary, resolved to `step`
            prev_in = now_in
    if pts:
        lz = local(pts[-1])
        marks.append((lz, inside(lz)))

    runs, cur = [], []
    for p, pin in marks:
        if pin:
            if not cur or cur[-1] != p:
                cur.append(p)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) > 1]


#: How far the built road may move when a station is dropped as carrying no shape, in metres.
#: 0.25 m is a quarter of the 1.05 m a 2.4 m car has either side of it in a 4.5 m lane, and well
#: under the 0.35 m `MovementController.stepUpLedge` -- a deviation you cannot see and cannot trip
#: over. It bounds the WHOLE simplification, not one step, because `simplify` is Douglas-Peucker:
#: a local three-point test drops stations one at a time and each new chord is measured against the
#: already-moved line, so the road can walk arbitrarily far from where it was authored.
STATION_MERGE_TOL = 0.25

#: The same bound for the GROUND under the station, and it is looser on purpose. A station's
#: `ground_z` is what `road_support` sizes its piers and embankment from, so a dead-straight deck
#: over a bay must NOT collapse to its two shore stations -- the seabed between them would be
#: read as a straight line and every pier cut to the wrong depth. 1.0 m keeps a station wherever
#: the terrain is doing something while still merging a flat reclaimed plain.
GROUND_MERGE_TOL = 1.0


def _dev(p, a, b):
    """Perpendicular distance from `p` to the segment `a`-`b`, in 3D."""
    ax, ay, az = a
    ux, uy, uz = b[0] - ax, b[1] - ay, b[2] - az
    d2 = ux * ux + uy * uy + uz * uz
    if d2 < 1e-12:
        return math.dist(p, a)
    t = ((p[0] - ax) * ux + (p[1] - ay) * uy + (p[2] - az) * uz) / d2
    t = max(0.0, min(1.0, t))
    q = (ax + ux * t, ay + uy * t, az + uz * t)
    return math.dist(p, q)


def simplify(pts, tol, protect=()):
    """Douglas-Peucker over a 3D polyline -> the sorted indices to KEEP.

    THE OPPOSITE NUMBER TO `resample`. That one lays a station every `spacing` metres so a road HAS
    stations where it needs them; this one takes back the ones that turned out to carry no shape.
    A plan arterial is 7-10 authored points across the whole island and the fill between them is
    laid on a straight line, so on a straight leg every fill station is exactly collinear and says
    nothing -- the island's Port road came out with a station every 67 m down 232 m of dead-straight
    reclaimed quay. (Hand-merged in `Island_base.blend`, 2026-09-04; this is that edit derived.)

    Douglas-Peucker and not a pairwise walk: the recursion always measures against the chord
    between two points that are BEING KEPT, so `tol` bounds the distance from the original
    polyline outright. A local "is this point on the line between its neighbours" test measures
    each candidate against a line that already reflects previous removals, and a long shallow
    curve then walks off by many times the tolerance one legal step at a time.

    `protect` is the indices that must survive whatever their deviation -- the junction mouths.
    A mouth is not a shape, it is a STOP LINE: an agreement between two roads about where the pad
    hands over, and dropping one because the street runs straight through it would delete the
    crossing. Ends are always kept.
    """
    n = len(pts)
    if n < 3:
        return list(range(n))
    keep = {0, n - 1}
    keep.update(i for i in protect if 0 <= i < n)
    # Split at every protected index first, then recurse inside each stretch -- so a protected
    # point is a real endpoint of the chords its neighbours are measured against, never something
    # the recursion happens to keep as well.
    anchors = sorted(keep)
    stack = list(zip(anchors, anchors[1:]))
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        worst, at = -1.0, -1
        for i in range(lo + 1, hi):
            d = _dev(pts[i], pts[lo], pts[hi])
            if d > worst:
                worst, at = d, i
        if worst > tol:
            keep.add(at)
            stack.append((lo, at))
            stack.append((at, hi))
    return sorted(keep)


def resample(pts, spacing, keep=MIN_SPAN):
    """Even stations along a polyline, ends included, AND EVERY AUTHORED VERTEX.

    ONE OWNER, in `island_v3_terrain.stations`, because `bench_depth` predicts the fill this build
    will produce and can only do that if it puts the stations where this does. The module header
    has claimed "THROUGH the authored points" since this file was written; for years the code laid
    `total/spacing` stations by arc length and let the shape fall where it may. Invisible on a plan
    arterial (7 gentle points across the island), fatal on a DERIVED alignment whose shape IS its
    vertices -- the shrine road's hairpins are 38 m arcs against a 70 m spacing, so every corner was
    cut, the ground was sampled ACROSS each turn instead of round it, and the grade cone filled the
    difference: a 25.6 m pier where the model said 14.7.
    """
    return IT.stations(pts, spacing, keep)


def _nearest_segment(chain, p):
    """Index of the chain segment closest to `p`. Derived on demand, never cached: the chain is
    rewritten by every mouth splice, and an index taken before that is a claim about a list that no
    longer exists."""
    best, at = None, 0
    for i in range(len(chain) - 1):
        a, b = chain[i], chain[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 <= 1e-12:
            d = math.dist(p, a)
        else:
            t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
            d = math.hypot(p[0] - (a[0] + dx * t), p[1] - (a[1] + dy * t))
        if best is None or d < best:
            best, at = d, i
    return at


#: How far PAST a segment's own end an intersection may sit and still be that segment's, in
#: METRES. It is a tolerance for "exactly on the end", not a search radius -- see `seg_intersect`.
TOUCH = 0.05


def seg_intersect(a0, a1, b0, b1):
    """XY intersection point of two segments, or None.

    THE BOUNDS ARE IN METRES, NOT IN PARAMETER SPACE, and that is the whole content of this
    function. `t` and `u` are fractions of two segments of very different lengths, so a single
    number cannot mean the same thing to both -- and the case that matters here is not a near
    miss, it is a road that ENDS ON ANOTHER ROAD'S VERTEX, which is the plan's own rule ("an
    arterial terminates on another arterial, never in mid-air", `island_v3_geom.ARTERIALS`).

    A touch like that solves to `t = 1` and `u = 0` exactly -- in exact arithmetic. `resample`
    walks the polyline accumulating float error, so the station that should land ON the shared
    vertex lands about 5e-7 m off it, and the strict `0.0 <= t <= 1.0` then reads
    `t = 1.0000000006` (0.6 NANOMETRES past the end) and returns None. Measured on the island's
    own network: Nogyo x Kitahama came out at `t = 0.99999999` and was found; Hama x Kuko and
    Port x Futo came out the other side of 1.0 by the same margin and were not. Whether two roads
    that meet at a shared vertex are connected AT ALL was decided at the ninth decimal place, and
    what it cost is written up in `WORLD_REBUILD_PLAN.md` `W14`: the airport link was authored as
    a dangling stub lying on Hama-dori's carriageway, and the harbour apron as an unreachable
    island whose first station sits exactly on Port road's last one with no link between them --
    with the road gate green, because a gate that only reads the authored graph cannot see a
    junction that was never authored.

    `TOUCH` is 5 cm: five orders of magnitude above that drift, and three below `MIN_SPAN`, so it
    can never turn two roads that genuinely pass close by into a crossing. The double hit a
    vertex touch produces (both segments meeting there intersect the other chain) is already
    `crossings`' job to dedupe."""
    d1 = (a1[0] - a0[0], a1[1] - a0[1])
    d2 = (b1[0] - b0[0], b1[1] - b0[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return None
    t = ((b0[0] - a0[0]) * d2[1] - (b0[1] - a0[1]) * d2[0]) / den
    u = ((b0[0] - a0[0]) * d1[1] - (b0[1] - a0[1]) * d1[0]) / den
    et = TOUCH / (math.hypot(*d1) or 1.0)
    eu = TOUCH / (math.hypot(*d2) or 1.0)
    if -et <= t <= 1.0 + et and -eu <= u <= 1.0 + eu:
        t = max(0.0, min(1.0, t))
        return (a0[0] + d1[0] * t, a0[1] + d1[1] * t)
    return None


#: How far two roads meeting end-to-end may turn and still be butt-joined as one corridor, in
#: DEGREES. Above it the joint is a CORNER and its lanes do not chain -- see the joint branch in
#: `seed` (`W15`). Nothing switches on it yet; it is what makes the log name the bad ones.
JOINT_BEND_DEG = 30.0


def _chain_bend(objs):
    """The deflection, in degrees, between the two roads meeting at a joint -- 0 = straight through.

    MEASURED AT THE SHARED STATION, from each road's OWN chain, not from a corner point the two are
    set back from. The earlier version took a vector from the place centroid to each mouth, which
    is only meaningful while the mouths stand either side of it; once `fillet_corner` made the two
    chains END on the same point that reading collapsed and reported a confident 180 deg on three
    corners whose real deflection is 7."""
    if len(objs) != 2:
        return None
    dirs = []
    for o in objs:
        chain = None
        for c in pm.road_collections():
            pts = pm.point_objects(c)
            if o in pts:
                chain = pts
                break
        if not chain or len(chain) < 2:
            return None
        i = chain.index(o)
        other = chain[i - 1] if i else chain[1]
        a = o.matrix_world.translation
        b = other.matrix_world.translation
        v = (a.x - b.x, a.y - b.y) if i else (b.x - a.x, b.y - a.y)
        n = math.hypot(*v)
        if n < 1e-6:
            return None
        dirs.append((v[0] / n, v[1] / n))
    dot = max(-1.0, min(1.0, dirs[0][0] * dirs[1][0] + dirs[0][1] * dirs[1][1]))
    ang = math.degrees(math.acos(dot))
    return min(ang, 180.0 - ang)


#: Two crossings closer than this are ONE place (and a crossing this close to a chain's end is at
#: that end, so the road contributes a single arm). It was `setback * 1.5` while the setback was a
#: flat 14 m; the setback is solved per place now, so the number that decides what a PLACE is has
#: to stand on its own -- a clustering radius that grew with the pad would merge two genuine
#: crossings the moment one of them turned out to be shallow.
CROSSING_NEAR = 21.0

#: The fillet a seeded mouth is authored with -- `point_model.POINT_FIELDS`' own `fillet_radius`
#: default. `point_solve.solve_junction` takes the SMALLEST of a pad's arms, and every arm the
#: seeder makes carries this one, so predicting the pad's kerb radius needs no lookup.
SEED_FILLET_RADIUS = 6.0


class _PlannedArm(object):
    """One arm of a crossing that has not been authored yet, shaped like a `point_solve.Mouth`.

    `solved_setback` reads six numbers off a mouth and nothing else -- bearing, the two paved
    half-widths, the two footway halves, the lane width and the two lane counts -- all of which
    are known from the tier and the plan alignment before a single Empty exists. So the seeder can
    ask for the stop-line distance `Auto Setback` will solve LATER and place its mouths there
    straight away, instead of at a provisional 14 m that nothing downstream agrees with.
    """
    __slots__ = ("uid", "bearing", "half_in", "half_out", "walk_in", "walk_out",
                 "lane_width", "lanes_in", "lanes_out")

    def __init__(self, uid, out_dir, tier):
        self.uid = uid
        self.bearing = math.degrees(math.atan2(out_dir[1], out_dir[0]))
        lanes = tier["lanes_fwd"] + tier["lanes_bwd"]
        # `lane_profile.paved_extents` on a symmetric section: half the carriageway plus half the
        # median, the same number either side. The seeder authors nothing asymmetric.
        half = (lanes * tier["lane_width"] + tier["median_width"]) / 2.0
        self.half_in = self.half_out = half
        # HALF the footway, because that is what a `Mouth` carries (`point_solve.build_mouth`
        # divides by two) and `corner_setback` doubles it back. Getting this wrong is a pad sized
        # for a road 4 m narrower than it is -- `WORLD_REBUILD_PLAN.md` `W16` from the other side.
        self.walk_in = self.walk_out = tier["walk"] / 2.0
        self.lane_width = tier["lane_width"]
        self.lanes_in, self.lanes_out = tier["lanes_bwd"], tier["lanes_fwd"]


def place_setbacks(places, chains, tiers, margin=2.0):
    """`{place_index: stop-line distance}` -- what `Auto Setback` will solve for each crossing.

    THE SEEDER USED TO GUESS THIS AND THE GUESS WAS ALWAYS LOW. Every mouth went in at a flat 14 m
    and every station within `14 + MIN_SPAN` of the crossing was pruned as belonging to the
    junction; `bpy.ops.rka.auto_setback()` then solved the real distance -- 17.9 to 35.5 m over
    the island's ten pads -- and walked each mouth out over the top of whatever had survived a
    window computed from the wrong number. The island shipped an ordinary station **0.19 m** past
    Rinkai-dori's mouth at the Chuo crossing, two more at 5.7 and 6.9 m, and two either side of
    the Kuko pad that had to be deleted by hand. The gate could not see any of it: both stations
    carry the same profile, so `check_tapers` returns before it measures the span.

    So ask. `point_solve.solved_setback` is the one owner and it needs only the arms, which the
    plan alignment and the tier already give. The prune window is then the real one by
    construction, and `Auto Setback` afterwards finds the mouths where it would have put them and
    moves nothing.

    Computed BEFORE any splicing, from the unspliced chains: an arm's bearing is a fact about the
    plan's alignment, and the splices are local to their own crossing.
    """
    out = {}
    for k, pl in enumerate(places):
        p = pl["at"]
        arms = []
        for nm in pl["roads"]:
            st = chains.get(nm)
            if not st or len(st) < 2:
                continue
            seg = _nearest_segment(st, p)
            a, b = st[seg], st[seg + 1]
            d = math.dist(a, b) or 1.0
            u = ((b[0] - a[0]) / d, (b[1] - a[1]) / d)
            tier = tiers.get(nm, T2)
            # A road that merely PASSES through contributes two arms, one either side; one that
            # starts or ends here contributes the single arm that has road behind it. Same rule
            # the splice below applies, asked here so the pad is sized for the arms it will get.
            if math.dist(p, st[-1]) < CROSSING_NEAR:
                arms.append(_PlannedArm("%s-" % nm, (-u[0], -u[1]), tier))
            elif math.dist(p, st[0]) < CROSSING_NEAR:
                arms.append(_PlannedArm("%s+" % nm, u, tier))
            else:
                arms.append(_PlannedArm("%s-" % nm, (-u[0], -u[1]), tier))
                arms.append(_PlannedArm("%s+" % nm, u, tier))
        out[k] = psolve.solved_setback(arms, SEED_FILLET_RADIUS, margin=margin) if arms else 0.0
    return out


#: A corner's centreline turning radius, as a multiple of the road's own DECK half-width.
#:
#: DERIVED FROM THE SECTION, never a constant (`WORLD_REBUILD_PLAN.md` `W16`: a number standing for
#: "the road's width" must be derived from the whole section, footway included). At 3.0 the INNER
#: deck edge turns through `R - half = 2 * half` -- 29 m on a T2 arterial, 16 m on a T3 street --
#: which is a city-block corner rather than a fold. The touge's `HAIRPIN_RADIUS` (12 m) is the
#: tightest thing in the world and it is a mountain switchback on the narrower section; an arterial
#: rounding a coastal headland is not that.
CORNER_RADIUS_FACTOR = 3.0

#: The most of a road the fillet may eat, as a fraction of that chain's length. A corner that
#: needs more tangent than the road has is a corner authored in the wrong place, and shortening
#: the radius is the honest answer -- it is reported.
CORNER_MAX_CHAIN_FRACTION = 0.4

#: Arc resolution, degrees per emitted point. `simplify` thins this back down afterwards (a
#: 43.5 m radius keeps a station roughly every 9 m at `STATION_MERGE_TOL`), so this only has to be
#: fine enough that the arc is not itself a polygon.
CORNER_ARC_STEP_DEG = 5.0


def fillet_corner(a_chain, b_chain, radius, arc_step=CORNER_ARC_STEP_DEG):
    """Round the corner where `a_chain` ENDS and `b_chain` BEGINS. Mutates both; returns the
    shared point they now meet at, or None if there is no corner to round.

    THE TWO ROADS ARE MADE TO MEET TANGENTIALLY -- that is the whole idea, and it is why this is
    not "merge them into one road".

    `W15`: where two plan arterials meet end-to-end the seeder links them SEGMENT-to-SEGMENT, and a
    RUN never spans two road collections (`point_solve.road_runs` walks one road's own points), so
    each sweeps its own carriageway to the joint at its OWN heading. Two sections cut on planes
    98-124 deg apart agree nowhere: measured on the island, the two chains ended **24-28 m apart**
    with a link across the gap and no road built in it, and the outer lane ends sat
    `2 * offset * sin(bend/2)` -- up to 12.8 m -- from each other against a `CHAIN_TOL` of 4.5.

    Two repairs were rejected before this one. A **2-arm pad** is refused by the model in as many
    words (`check_junctions`: "a pad of 2 arms is a segment connection, not a junction"),
    `lane_movements` wires only the innermost lane across it, and every mouth comes out 40-53 deg
    off its pad centre. **Merging the two roads into one** works geometrically and costs a street
    its name: Nishihama-dori would be swept as part of Rinkai-dori, and every lane id, every
    `traffic_route` prefix and every future zone reference with it.

    So the bend stays where the model already handles it -- inside a road, as ordinary stations --
    and only the JOINT moves: both chains are trimmed back by the tangent length, a circular arc is
    laid between the tangent points, and the arc is SPLIT AT ITS MIDPOINT so each road carries half
    of the turn. At that midpoint both roads run in the same direction, which is exactly the
    straight-through joint the model already builds correctly. A street name changing halfway round
    a bend is what streets do.
    """
    if len(a_chain) < 2 or len(b_chain) < 2:
        return None
    c = a_chain[-1]
    da = _unit(a_chain[-2], c)
    db = _unit(b_chain[0], b_chain[1])
    if da is None or db is None:
        return None
    cross = da[0] * db[1] - da[1] * db[0]
    dot = max(-1.0, min(1.0, da[0] * db[0] + da[1] * db[1]))
    theta = math.acos(dot)                       # the DEFLECTION, 0 = straight through
    if theta < math.radians(1.0) or theta > math.radians(179.0):
        return None
    half = theta / 2.0
    want = radius * math.tan(half)
    # A fillet may not eat more road than there is. Both sides are asked and the smaller wins,
    # then the radius is recomputed from it so the arc still MEETS both tangents.
    room = min(_chain_length(a_chain), _chain_length(b_chain)) * CORNER_MAX_CHAIN_FRACTION
    t = min(want, room)
    if t < 1.0:
        return None
    radius = t / math.tan(half)
    pa = (c[0] - da[0] * t, c[1] - da[1] * t)
    pb = (c[0] + db[0] * t, c[1] + db[1] * t)
    sign = 1.0 if cross > 0 else -1.0            # +1 = the road turns left
    # The centre is one radius off the tangent point, perpendicular to that tangent, on the
    # inside of the turn.
    cx = pa[0] - da[1] * radius * sign
    cy = pa[1] + da[0] * radius * sign
    a0 = math.atan2(pa[1] - cy, pa[0] - cx)
    steps = max(2, int(math.ceil(math.degrees(theta) / arc_step)))
    arc = []
    for i in range(steps + 1):
        ang = a0 + sign * theta * (i / float(steps))
        arc.append((cx + radius * math.cos(ang), cy + radius * math.sin(ang)))
    # Trim each chain back to its tangent point, dropping anything the fillet swallowed.
    a_chain[:] = [q for q in a_chain[:-1] if math.dist(q, c) > t + MIN_SPAN] + [pa]
    b_chain[:] = [pb] + [q for q in b_chain[1:] if math.dist(q, c) > t + MIN_SPAN]
    mid = steps // 2
    a_chain.extend(arc[1:mid + 1])
    b_chain[:0] = arc[mid:-1]
    return arc[mid]


def _unit(a, b):
    d = math.dist(a, b)
    if d < 1e-9:
        return None
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _chain_length(ch):
    return sum(math.dist(p, q) for p, q in zip(ch, ch[1:]))


def crossings(chains, tol=MIN_SPAN):
    """`[(name_a, i_a, name_b, i_b, point)]` -- where two seeded chains cross, by segment index.

    ONE CROSSING PER PLACE. A crossing that lands on a chain's own VERTEX is found twice -- both
    segments meeting at that vertex intersect the other chain, at the same point -- and the splice
    downstream then inserts two mouths either side of it and calls the pad with six arms, two pairs
    of which are coincident (`station_coincident`, "a zero-length taper", which is the gate doing
    its job on a seeder bug).
    
    It was latent for as long as the plan's roads crossed each other in mid-span. `W2`'s four new
    arterials each START on an existing road's vertex, deliberately -- that is this file's own
    "never in mid-air" rule -- so every one of them landed on it.
    """
    out = []
    names = list(chains)
    for i, na in enumerate(names):
        for nb in names[i + 1:]:
            A, B = chains[na], chains[nb]
            found = []
            for ia in range(len(A) - 1):
                for ib in range(len(B) - 1):
                    p = seg_intersect(A[ia], A[ia + 1], B[ib], B[ib + 1])
                    if p is None:
                        continue
                    if any(math.dist(p, q) <= tol for q in found):
                        continue            # the same place, reached along the other segment
                    found.append(p)
                    out.append((na, ia, nb, ib, p))
    return out


# ------------------------------------------------------------------------------- authoring

#: How far a junction pad must stand clear of the ground under its OWN footprint, in metres.
#:
#: Zero would already be correct in principle -- at the max the pad is flush with the highest point
#: and everything else is below it -- so this is margin against the sampler, not against the model:
#: the ground is probed on a 2 m grid and a slope can rise a little between probes. 0.25 m is well
#: inside `MovementController.stepUpLedge` (0.35 m), so even if a hummock did poke through it would
#: be a step rather than a wall.
PAD_CLEARANCE = 0.25

#: How finely the ground under a pad is probed, in metres.
PAD_PROBE_STEP = 2.0

#: Below this, the ground is ON the pad, not above it, and there is nothing to lift.
#:
#: The same 5 cm and the same idea as `point_edges.BURIED_TOL` -- a tolerance for *exactly on*,
#: not a margin. It is not a guess: measured over the island's nine pads, the eight that sit on
#: flat ground read a raw burial of **0.000000 to 0.000122 m** (raycast noise, four orders of
#: magnitude below anything a wheel or a boot can find) and the one on a slope reads **0.924**.
#: Without it every junction in the world was lifted by the full `PAD_CLEARANCE` for a tenth of a
#: millimetre, putting a needless 25 cm hump at eight crossings that had nothing wrong with them.
PAD_BURY_TOL = 0.05


class _PadPoint(object):
    """Just enough of a `point_solve.Mouth` for `_idw_z` -- which reads `.pos` and nothing else."""
    __slots__ = ("pos",)

    def __init__(self, pos):
        self.pos = pos


def pad_lifts(places, mouths, joint_at, sample, margin=PAD_CLEARANCE, step=PAD_PROBE_STEP):
    """`{place_index: metres}` -- how far each junction's mouths must rise to clear the ground.

    A PAD IS A PLANE THROUGH ITS MOUTHS AND THE GROUND UNDER IT IS NOT ONE. Each mouth is a station
    draped onto the terrain, so a pad meets every approach at that approach's own elevation
    (`point_solve._idw_z`, and that is the right rule) -- but between the mouths the surface is
    interpolated while the ground goes on doing whatever it does. Where the ground rises inside the
    footprint, the pad is UNDER it.

    THE COLLIDER IS WHY THAT MATTERED. (Historic: the ground used to be cut by a BOOLEAN, and the
    collider was deliberately left uncut. Since 2026-09-06 the road DEFORMS the height field
    instead — `island_v3_terrain.Carve` — and the collider is a copy of the carved ground, so this
    paragraph describes what the lift was found under, not how the ground works now.)
    `point_build.cut_ground` punched a vertical prism through the
    terrain over every band including the pad, so the burial is invisible -- but the island's
    collision mesh (`Ground-colonly`) is deliberately in a collection the cut does NOT recognise,
    because continuous collision under the whole island is the base layer's whole job
    (`build_island_base`). So the player walks on ground the eye says is not there, standing above
    a road that has vanished under their feet, and at 0.5 m that is a wall rather than a step.
    Measured at the island's port crossing, where four arterials meet on ground that falls 2.09 m
    across the pad: the interior stood **~0.5 m** proud of the pad surface. Hand-lifted by the user
    2026-09-04; this is that edit derived.

    A UNIFORM offset per pad, never per mouth: `_idw_z` is linear in the mouth heights, so adding
    the same number to all of them raises the whole surface by exactly that number and the pad
    keeps its tilt -- one pass is exact, no iteration. Raising them by different amounts would
    re-shape the pad to fit a hummock, which is not what a junction does.

    Joints are skipped: two roads meeting tangentially have no pad to bury.
    """
    out = {}
    for k, pl in enumerate(places):
        if k in joint_at:
            continue
        pts = mouths.get(k) or []
        if len(pts) < 3:
            continue
        zs = [sample(x, y) for (x, y) in pts]
        if any(z is None for z in zs):
            continue                       # over water, or off the terrain: not a graded pad
        arms = [_PadPoint((x, y, z)) for (x, y), z in zip(pts, zs)]
        cx = sum(x for (x, _y) in pts) / len(pts)
        cy = sum(y for (_x, y) in pts) / len(pts)
        # The pad's fillets bulge a little past the mouths, so probe a touch wider than they sit.
        r = max(math.dist((cx, cy), q) for q in pts) * 1.1
        worst = 0.0
        n = max(1, int(r / step))
        for ix in range(-n, n + 1):
            for iy in range(-n, n + 1):
                x, y = cx + ix * step, cy + iy * step
                if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
                    continue
                g = sample(x, y)
                if g is None:
                    continue
                worst = max(worst, g - psolve._idw_z(arms, (x, y)))
        if worst > PAD_BURY_TOL:
            out[k] = worst + margin
    return out


def height_profile(stations, sample, kind, floors=None):
    """A Z for every station: the ground where there is ground, the BRIDGE DECK where the plan
    already spans the water, and a grade-legal climb between the two.

    `floors` is `{station index: minimum z}` -- a junction pad that has to stand clear of its own
    ground (`pad_lifts`). It is applied BEFORE the cone on purpose, so the approaches grow the
    grade-legal ramp up to the raised pad instead of stepping at the stop line.

    THIS IS WHERE `W1` LIVED. The fallback used to be a bare `0.0` for any station the ground
    sampler missed, so Hama-dori crossed 466 m of the bay dead flat at sea level on nothing but
    its own tarmac. Two owners of "is this crossing carried": `island_v3_plan.BRIDGES` said yes
    (`island_v3_terrain.report` counts those 47 stations as `carried`) and the seeder had never
    heard of it. `P.bridge_at` is that one owner, asked here.

    The envelope is the standard two-pass grade cone, and it is what turns a deck height into a
    ROAD: forward, no station may sit more than `limit x span` below its predecessor; backward,
    the same the other way. Together that bounds every span's grade by the road's own class limit
    (`P.MAX_GRADE[kind]`), so a +18 m bay crossing grows the ~450 m of 4% approach it needs at each
    end instead of a cliff at the abutment. Raising the approaches above the terrain is not a side
    effect to apologise for — it is the viaduct, and `road_support.support_kind` reads the same
    `delta` and grows the embankment and then the pier line out of it with no bridge-specific code
    anywhere.
    """
    limit = P.MAX_GRADE.get(kind, 0.06)
    z = []
    for (x, y) in stations:
        g = sample(x, y)
        deck = P.bridge_at(x, y)
        if g is not None:
            z.append(g)
        elif deck is not None:
            z.append(deck[1])
        else:
            # No ground and no bridge: unchanged, and deliberately so — this is the shape
            # `check_island_ground.py` reports as a hole, and inventing a height would hide it.
            z.append(0.0)
    for i, floor in (floors or {}).items():
        if 0 <= i < len(z):
            z[i] = max(z[i], floor)
    # ONE OWNER OF THE CONE (`island_v3_terrain`). `report`/`bench_depth` predict what this will
    # build with the same functions; while they were two copies the gate could pass an alignment
    # this then lifted 57 m into the air.
    #
    # A BENCHED ROAD CUTS AS WELL AS FILLS. The plain cone only raises, which is right for a bay
    # crossing (you do not cut a bridge into water) and wrong for a hill -- it answers every
    # hairpin with a pier and stands the road over a mountain instead of in it. `bench_profile`
    # takes the midpoint of the two envelopes, which is grade-legal by convexity and turns the
    # shrine road's +16.3 m of pier into a symmetric +8.1/-8.1 m bench.
    if kind in IT.BENCHED_CLASSES:
        return IT.bench_profile(stations, z, limit)
    return IT.grade_cone(stations, z, limit)


def ground_under(x, y, sample):
    """What a PILLAR at this station would have to reach.

    The terrain where the raycast finds terrain; the SEA FLOOR (`island_v3_terrain.seabed`) where
    it does not. The sampler cannot answer over water on purpose — the seabed is deliberately not
    in a terrain collection, so that a road is never draped onto it (that is what would have hidden
    `W1` instead of fixing it) — but a bridge's columns still have to land on something real, and
    the world model knows exactly what and where it is.
    """
    g = sample(x, y)
    return g if g is not None else IT.seabed(x, y)


def author_road(name, stations, zs, grounds, tier):
    """One road, built by pressing the operators an artist presses."""
    x, y = stations[0]
    bpy.ops.rka.new_road(name=name, x=x, y=y, z=zs[0],
                         lanes_fwd=tier["lanes_fwd"], lanes_bwd=tier["lanes_bwd"],
                         lane_width=tier["lane_width"], median_width=tier["median_width"],
                         road_class="arterial", design_speed=tier["speed"])
    coll = pm._local(bpy.data.collections, name)
    first = sorted(pm.point_objects(coll), key=lambda o: o.name)[0]
    for k, (px, py) in enumerate(stations[1:], start=1):
        # `Extend Road` appends along the chain tangent, then the point is DRAGGED onto its
        # station -- which is exactly the two-step an artist does, and keeps the operator the
        # only thing that ever creates a point.
        for o in bpy.context.selected_objects:
            o.select_set(False)
        last = sorted(pm.point_objects(coll), key=lambda o: o.name)[-1]
        last.select_set(True)
        bpy.context.view_layer.objects.active = last
        bpy.ops.rka.extend_road()
        new = sorted(pm.point_objects(coll), key=lambda o: o.name)[-1]
        new.matrix_world.translation = (px, py, zs[k])
    # THE CROSS-SECTION IS THE ROAD'S, NOT THE STATION'S -- and writing it on the station is
    # writing it where nothing reads it.
    #
    # Every station the seeder authors is `INHERIT`, and `point_model.resolve_point` gives an
    # INHERIT station its ROAD BASE with only the four `DELTA_FIELDS` (the lane counts) taken from
    # the point. So the footway widths written here, per point, on every station, since this file
    # was written, were discarded by the very next read: measured on the built island,
    # `rka_walk_hl` is 0.0 on every sample of every road. THE ISLAND HAS NEVER HAD A PAVEMENT.
    # That is `W3` seen from the other end -- what it measured as "0.160 m proud" and read as the
    # carriageway is the RAISED MEDIAN, the only edge furniture with a width to build.
    #
    # The tell was in the old comment ("they are a POINT field, so they are set on every station"),
    # which is true of the schema and false of the model: a field being per-point does not make
    # the point its owner. `new_road` already sets the lane counts, lane width, median and design
    # speed on the base for exactly this reason; the footway and the kerb belong beside them.
    base = coll.rka_road.base
    base.left_walk_width = base.right_walk_width = tier["walk"]
    base.left_kerb_height = base.right_kerb_height = tier["kerb"]
    # The BARRIER needs nothing here: its height is the road's (`RoadData.barrier_height`, 1.0 by
    # default) and WHERE it stands is derived per sample by `point_solve.solve_road` -- a road
    # nobody may walk on is fenced end to end, and one they may walk on is fenced where it is off
    # the ground (`delta >= BARRIER_MIN_DELTA`, or on piers). That is the "bridge and higher road
    # points get a wall" rule, and it was already right; with a footway width it now stands at the
    # OUTBOARD edge of the pavement instead of on the kerb line.
    #
    # `ground_z` IS a per-station fact and stays one: Build samples it unconditionally but a MISS
    # leaves whatever was there (`point_build.sample_ground`: "a road over water keeps whatever it
    # had"), so a station over the bay would hand the support solver a 0 and grow columns that
    # stop at the water line. What is written here is not an invented number — it is the sea
    # floor, from the same model the seabed mesh is built from.
    for k, o in enumerate(sorted(pm.point_objects(coll), key=lambda o: o.name)):
        if k < len(grounds):
            o.rka_pt.ground_z = grounds[k]
            o.rka_pt.has_ground_z = True
    coll.rka_road.road_class = "arterial"
    return coll, first


def seed(center, half, spacing=70.0, only="", links=0, setback=14.0):
    """Author every plan arterial that crosses the square `center` +- `half`, as point-graph roads.

    THE REGION IS A PARAMETER, not a district. This was written for one 504 m piece and its square
    was a module constant; the world rebuild's first playable test is the WHOLE island's road
    network in one blend (`build_island_base.py`), which is the same job over a 1512 m square. A
    seeder that can only see 504 m at a time would have had to be copied to do it, and then there
    would be two owners of "how a plan road becomes an authored road".

    `setback` is only the FALLBACK stop-line distance now -- where a mouth actually goes is solved
    per crossing by `place_setbacks`, from the arms the plan gives it.

    Returns `(collection_names, n_crossings)`.
    """
    cx, cy = center
    from road_kit_authoring import point_build as pb
    ground_fn = pb.ground_sampler(bpy.context.scene)


    wanted = {t.strip() for t in only.split(",") if t.strip()}
    links = max(0, int(links))
    chains, order = {}, []
    for name, pts in IT.road_network():
        if wanted and name not in wanted:
            continue
        for k, run in enumerate(clip_runs(pts, cx, cy, half)):
            st = resample(run, spacing)
            if len(st) < 2:
                continue
            rid = name.replace(" ", "_").replace("-", "_").lower()
            if k:
                rid = "%s_%d" % (rid, k + 1)
            chains[rid] = st
            order.append((rid, name))
    if not chains:
        return [], 0

    # LOCAL STREETS. The island plan is a TRUNK network: it says where the arterials go and says
    # nothing about the blocks between them, so a district can hold two of its roads that never
    # meet. A city district needs the streets between the trunks, and those are authored, not
    # planned. They are laid perpendicular to the LONGEST arterial present, evenly across the
    # region, and every arterial they reach becomes a crossing.
    if links:
        spine = max(chains.values(), key=lambda c: sum(math.dist(a, b) for a, b in zip(c, c[1:])))
        a, b = spine[0], spine[-1]
        d = math.dist(a, b) or 1.0
        ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
        nx, ny = -uy, ux                                   # perpendicular to the spine
        for k in range(links):
            t = (k + 1) / float(links + 1)
            mid = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            far = half * 2.0
            run = clip_runs([(mid[0] - nx * far + cx, mid[1] - ny * far + cy),
                             (mid[0] + nx * far + cx, mid[1] + ny * far + cy)], cx, cy, half)
            if not run:
                continue
            st = resample(run[0], spacing)
            if len(st) >= 2:
                chains["local_%d" % (k + 1)] = st
                order.append(("local_%d" % (k + 1), None))

    # SPLIT AT CROSSINGS BEFORE AUTHORING. A crossing does not split a street in this model -- but
    # each street must have a STATION at the stop line, and the pad is built from those mouths. So
    # the station list gains a point either side of every crossing, and those become the mouths.
    xs = crossings(chains)
    # INSERT FROM THE FAR END BACKWARDS, AND RE-FIND THE SEGMENT EACH TIME. The crossing indices
    # were computed against the ORIGINAL chain, and every splice rewrites the chain under them -- a
    # road crossed twice had its second pair spliced into the wrong segment, putting all four
    # mouths of that pad on one line. The descending walk was the first half of the answer and it
    # was only ever an ordering ARGUMENT about which mutations invalidate which indices; the moment
    # the island grew from 8 crossings to 15 it produced an `IndexError` on a chain that a previous
    # splice had shortened past the index a later one still held.
    #
    # A stale index is the defect, not the order it is used in. The crossing POINT is the durable
    # fact -- it is a place on the ground, not an offset into a list -- so the segment is found
    # from it, against the chain as it is now (`_nearest_segment`). The descending order stays
    # because it still keeps the two mouths of one pad from stepping on each other.
    # ONE PLACE, ONE JUNCTION. `crossings` is pairwise, so a spot where THREE roads meet arrives as
    # three separate crossings -- and the pairwise splice then cut the same chain three times, an
    # arm's length apart, giving two junctions on top of each other and a pair of coincident
    # stations (`station_coincident`, "a zero-length taper", which stopped the build outright).
    # It was latent while the plan's roads only ever crossed in pairs; `W2`'s new arterials each
    # terminate on another road's endpoint, which is exactly where a third road already ends.
    #
    # So the crossings are CLUSTERED into places first, each chain is cut once per place at the
    # cluster's centroid, and every mouth of that place goes into one junction.
    places = []
    for cr in xs:
        for pl in places:
            if math.dist(cr[4], pl["at"]) <= CROSSING_NEAR:
                pl["members"].append(cr)
                break
        else:
            places.append({"at": cr[4], "members": [cr]})
    for pl in places:
        pts = [m[4] for m in pl["members"]]
        pl["at"] = (sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts))
        pl["roads"] = sorted({m[0] for m in pl["members"]} | {m[2] for m in pl["members"]})

    # WHERE THE STOP LINE GOES IS SOLVED, NOT ASSUMED -- see `place_setbacks`. `setback` survives
    # as the FALLBACK only (a place whose arms cannot be read), because a seeder that silently
    # authored a 0 m setback would be worse than one that authored a wrong one.
    tiers = {rid: (tier_for(plan_name) if plan_name else T3) for rid, plan_name in order}
    solved = place_setbacks(places, chains, tiers)

    mouths = {}

    # ROUND THE CORNERS FIRST, and only then splice the crossings -- a fillet rewrites the ends of
    # two chains, and the splice re-finds its segment from the crossing POINT against the chain as
    # it is now, so doing it in this order costs nothing. `W15`: a joint is two roads that both END
    # at one place, and until now they were set back from it and linked across a 24-28 m hole at
    # 98-124 deg. `fillet_corner` makes them meet TANGENTIALLY instead; see there.
    joint_at = {}
    for k, pl in enumerate(places):
        if len(pl["roads"]) != 2:
            continue
        na, nb = pl["roads"]
        ca, cb = chains.get(na), chains.get(nb)
        if not ca or not cb:
            continue
        # Which end of each chain is here decides who is `a` (arrives) and who is `b` (leaves);
        # a road that merely PASSES this place is not a joint at all.
        def _ends(ch):
            return (math.dist(ch[0], pl["at"]) < CROSSING_NEAR,
                    math.dist(ch[-1], pl["at"]) < CROSSING_NEAR)
        sa, ea = _ends(ca)
        sb_, eb = _ends(cb)
        if not (sa or ea) or not (sb_ or eb):
            continue
        if ea and sb_:
            a_ch, b_ch, a_nm, b_nm = ca, cb, na, nb
        elif eb and sa:
            a_ch, b_ch, a_nm, b_nm = cb, ca, nb, na
        elif ea and eb:
            b_ch = list(reversed(cb)); a_ch, a_nm, b_nm = ca, na, nb
            chains[nb] = b_ch
            cb = b_ch
        else:                                    # both are heads -- reverse A instead
            a_ch = list(reversed(ca)); chains[na] = a_ch
            ca = a_ch
            b_ch, a_nm, b_nm = cb, na, nb
        radius = CORNER_RADIUS_FACTOR * max(_deck_half(tiers.get(a_nm, T2)),
                                            _deck_half(tiers.get(b_nm, T2)))
        mid = fillet_corner(a_ch, b_ch, radius)
        if mid is not None:
            joint_at[k] = mid
            mouths[k] = [mid]
            print("   corner %s x %s: rounded at r=%.1f m, the two roads now meet straight through"
                  % (a_nm, b_nm, radius))

    per_chain = {}
    for k, pl in enumerate(places):
        if k in joint_at:
            continue                             # filleted above; the chains already meet
        for nm in pl["roads"]:
            per_chain.setdefault(nm, []).append((k, pl["at"]))
    for nm, items in per_chain.items():
        st = chains[nm]
        for key, p in sorted(items, key=lambda it: -_nearest_segment(chains[nm], it[1])):
            if len(st) < 2:
                continue
            sb = solved.get(key) or setback
            seg = _nearest_segment(st, p)
            a, b = st[seg], st[seg + 1]
            d = math.dist(a, b) or 1.0
            ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
            m0 = (p[0] - ux * sb, p[1] - uy * sb)
            m1 = (p[0] + ux * sb, p[1] + uy * sb)
            # A CROSSING AT A CHAIN'S END IS A T, NOT AN X, and this road contributes ONE arm.
            #
            # The plan's arterials terminate on each other by design ("never in mid-air"), so four
            # of the island's eight crossings sit on somebody's last station. Splicing a mouth
            # either side of one of those puts the far mouth PAST the end of the road, which is an
            # arm pointing at nothing: the chuo x nogyo pad came out with 5 arms, two of them
            # collinear, and `Auto Setback` — solving a clique that cannot be solved — pushed a
            # mouth out until the ring folded 135 m past its own centroid. One mouth, and the
            # stations beyond it go away with it.
            #
            # Order matters and is already guaranteed: the descending-`seg` walk means an END
            # crossing (high `seg`) is trimmed before a START crossing (`seg` 0) re-heads the
            # chain, so neither invalidates the other's index.
            #
            # A MOUTH REPLACES A STATION IT LANDS ON, it does not sit beside it. Stations are
            # `spacing` apart and a crossing falls wherever it falls, so a mouth `setback` from the
            # crossing is regularly within centimetres of the next station along — and inserting it
            # there left the road with two coincident points. That is what the chuo x nogyo pad's
            # 121 m fold and its never-built station were: an arm with no length, which the
            # junction solve then tried to face and set back like a real one. Everything inside the
            # setback window belongs to the junction, so it goes -- and the window is
            # `setback + MIN_SPAN`, not `setback`: a station 13.7 m from a crossing whose mouth
            # sits at 14 m survives a `setback`-wide prune and is then 30 cm from the mouth, which
            # is the same defect with a smaller number. A mouth needs clear road on the far side
            # of it, not merely to be alone.
            #
            # ...AND THE WINDOW HAS TO BE MEASURED WITH THE SETBACK THAT WILL ACTUALLY BE USED.
            # The rule above was right and the number it was asked with was not: `setback` was a
            # flat 14 m, `Auto Setback` solves 17.9-35.5 m on this island, and every metre of the
            # difference is a station that survived the prune and then had a mouth walked over the
            # top of it (0.19 m on Rinkai-dori at the Chuo crossing, and it passed the whole gate).
            # `sb` is `place_setbacks`' answer, from `point_solve.solved_setback` -- the same
            # function `Auto Setback` asks, so the two cannot disagree.
            near, keep = CROSSING_NEAR, sb + MIN_SPAN
            d_start, d_end = math.dist(p, st[0]), math.dist(p, st[-1])
            head = [q for q in st[:seg + 1] if math.dist(q, p) > keep]
            tail = [q for q in st[seg + 1:] if math.dist(q, p) > keep]
            if d_end < near:
                st[:] = head + [m0]
                mouths.setdefault(key, []).append(m0)
            elif d_start < near:
                st[:] = [m1] + tail
                mouths.setdefault(key, []).append(m1)
            else:
                st[:] = head + [m0, m1] + tail
                mouths.setdefault(key, []).extend([m0, m1])

    # Every mouth position, flat -- `simplify` must never drop a stop line.
    all_mouths = [q for pts in mouths.values() for q in pts]
    dropped_total = 0

    # A PAD MUST CLEAR THE GROUND UNDER ITSELF -- see `pad_lifts`. Computed here, once, from the
    # mouths' own draped heights, and handed to each road as a floor on those stations so the cone
    # grows the approach ramp rather than a step at the stop line.
    lifts = pad_lifts(places, mouths, joint_at, ground_fn)
    mouth_floor = [(q, lz) for k, lz in lifts.items() for q in mouths.get(k, ())]
    for k, lz in sorted(lifts.items()):
        print("   pad %s: raised %.2f m to clear the ground under it"
              % (" x ".join(places[k]["roads"]), lz))

    made = []
    for rid, plan_name in order:
        tier = tier_for(plan_name) if plan_name else T3
        kind = G.arterial_class(plan_name) if plan_name else "local"
        st = chains[rid]
        floors = {}
        for i, q in enumerate(st):
            for pos, lz in mouth_floor:
                if math.dist(q, pos) < 0.5:
                    g = ground_fn(q[0], q[1])
                    if g is not None:
                        floors[i] = g + lz
        zs = height_profile(st, ground_fn, kind, floors)
        grounds = [ground_under(x, y, ground_fn) for (x, y) in st]
        # DROP THE STATIONS THAT CARRY NO SHAPE. `resample` lays one every `spacing` metres so a
        # road has stations where it needs them; on the straight legs between two authored plan
        # vertices they are exactly collinear and cost geometry, build time and a lane control
        # point each for nothing. Measured against the ROAD and against the GROUND separately,
        # keeping the union -- see `GROUND_MERGE_TOL` for why a straight deck over a bay must not
        # collapse to its two shore stations.
        mouth_idx = [i for i, q in enumerate(st)
                     if any(math.dist(q, m) < 0.5 for m in all_mouths)]
        road3 = [(x, y, z) for (x, y), z in zip(st, zs)]
        gnd3 = [(x, y, g) for (x, y), g in zip(st, grounds)]
        keep_i = sorted(set(simplify(road3, STATION_MERGE_TOL, mouth_idx))
                        | set(simplify(gnd3, GROUND_MERGE_TOL, mouth_idx)))
        if len(keep_i) < len(st):
            dropped_total += len(st) - len(keep_i)
            st = [st[i] for i in keep_i]
            zs = [zs[i] for i in keep_i]
            grounds = [grounds[i] for i in keep_i]
            chains[rid] = st
        coll, _first = author_road(rid, st, zs, grounds, tier)
        made.append(coll.name)
        lifted = [i for i, (z, g) in enumerate(zip(zs, grounds)) if z - g > 1.0]
        print("   %-22s %2d stations   (%s)%s"
              % (coll.name, len(st), plan_name or "local street, T3",
                 "   %d elevated, up to %.1f m" % (len(lifted), max(z - g for z, g in zip(zs, grounds)))
                 if lifted else ""))

    if dropped_total:
        print("   %d station(s) dropped as carrying no shape (<= %.2f m road / %.2f m ground)"
              % (dropped_total, STATION_MERGE_TOL, GROUND_MERGE_TOL))

    # The mouths are the two stations either side of each crossing, on each road of the place.
    for key, pts in sorted(mouths.items()):
        names = places[key]["roads"]
        where = " x ".join(names)
        objs = []
        for nm in names:
            coll = pm._local(bpy.data.collections, nm)
            for o in pm.point_objects(coll):
                for q in pts:
                    if math.dist((o.matrix_world.translation.x,
                                  o.matrix_world.translation.y), q) < 0.5:
                        objs.append(o)
        for o in bpy.context.selected_objects:
            o.select_set(False)
        if len(objs) == 2:
            # TWO ROADS THAT BOTH END HERE ARE ONE CORRIDOR, not a junction. The plan's rule is
            # that an arterial terminates on another arterial, and where the other one terminates
            # too (Nishi-dori into Nogyo-michi) there is no crossing to pave -- there is a joint.
            # `make_intersection` needs three arms and this used to be reported and skipped, which
            # left the two roads ending 28 m apart with nothing between them.
            #
            # ...AND A JOINT ONLY WORKS WHILE THE TWO ROADS ARE STRAIGHT THROUGH. `W15`, CLOSED
            # 2026-09-04: a joint links two stations and nothing else, and a RUN never spans two
            # road collections, so each road sweeps its own carriageway to the shared point at its
            # OWN heading. Two sections cut on planes 98-124 deg apart agree nowhere -- the island's
            # three joints ended 24-28 m apart with a link across the hole.
            #
            # WHAT WAS TRIED AND REJECTED: paving the corner with a 2-arm pad (`check_junctions`
            # says in as many words that "a pad of 2 arms is a segment connection, not a junction",
            # `lane_movements` wires only the innermost lane across it, and every mouth comes out
            # 40-53 deg off its pad centre); and merging the two plan roads into ONE road, which
            # works and costs a street its name and every lane id with it.
            #
            # WHAT SHIPPED: the bend stays inside a road, as the ordinary stations the model is
            # built for, and only the JOINT moves. `fillet_corner` rounds the corner and splits the
            # arc between the two roads, so they meet TANGENTIALLY -- which is the straight-through
            # joint this branch already built correctly. Measured: gap 25.6 -> 0.00 m, deflection
            # 106 -> 7 deg (one arc step). `point_export.wire_joints` emits the lane hand-over, so
            # the graph says so too rather than leaving it to the runtime's proximity fallback.
            #
            # The bend is measured at the SHARED STATION, not from the place centroid: after the
            # fillet both chains end on the arc's midpoint and the old corner point is metres away
            # from either of them, so a vector from it to each says nothing (it read a confident
            # "180 deg" on three corners that are 7).
            bend = _chain_bend(objs)
            for o in objs:
                o.select_set(True)
            bpy.context.view_layer.objects.active = objs[0]
            bpy.ops.rka.connect_selected(type=pm.LINK_SEGMENT)
            print("   joint %s: %.0f deg through, linked as one corridor%s"
                  % (where, bend if bend is not None else 0.0,
                     "  <-- W15: still a corner, the lanes will not chain"
                     if bend is not None and bend > JOINT_BEND_DEG else ""))
            continue
        if len(objs) < 3:
            print("   crossing %s: only %d mouth(s) found -- skipped" % (where, len(objs)))
            continue
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        bpy.ops.rka.make_intersection()
        print("   intersection %s: %d mouths (%s)"
              % (where, len(objs), ", ".join(o.name for o in objs)))
    return made, len(xs)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--piece", default="",
                    help="a piece id from piece_registry -- its square is the region")
    ap.add_argument("--world", action="store_true",
                    help="the WHOLE island instead of one district (centre 0,0, half "
                         "island_v3_geom.ORIGIN) -- what build_island_base.py uses")
    ap.add_argument("--spacing", type=float, default=70.0,
                    help="station spacing in metres (default 70)")
    ap.add_argument("--only", default="", help="comma-separated plan road names")
    ap.add_argument("--links", type=int, default=0,
                    help="local cross-streets joining the plan arterials (default 0). The plan is "
                         "a TRUNK network -- two of its arterials can run through one district "
                         "without meeting, and a city district still needs the streets between "
                         "them. Each link crosses every arterial it reaches, so each one is "
                         "junctions, not just tarmac")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    if args.world:
        center, half = (0.0, 0.0), G.ORIGIN
        print("== the whole island: %.0f x %.0f m" % (G.WORLD, G.WORLD))
    else:
        if not args.piece:
            raise SystemExit("pass --piece <id> or --world")
        piece = pr.piece_by_id(args.piece)
        if piece is None:
            raise SystemExit("no such piece: %s" % args.piece)
        cx, cy, _cz = piece["position"]
        center, half = (cx, cy), HALF
        print("== %s at world (%.0f, %.0f), theme %s"
              % (args.piece, cx, cy, piece.get("theme")))

    made, n_cross = seed(center, half, spacing=args.spacing, only=args.only, links=args.links)
    if not made:
        raise SystemExit("no plan road crosses that region")
    print("   %d crossing(s) inside the region" % n_cross)
    print("== seeded %d road(s): %s" % (len(made), ", ".join(made)))
    if args.save:
        out = args.out or bpy.data.filepath
        bpy.ops.wm.save_as_mainfile(filepath=out)
        print("== saved %s" % out)


if __name__ == "__main__":
    main()

"""point_solve.py -- the resolver. Authored points in, the numbers geometry is built from out.

NOTHING HERE TOUCHES bpy, AND NOTHING DOWNSTREAM OF HERE COMPUTES A LATERAL OFFSET. Those two
rules are the whole point of the module. `point_build` sweeps what this returns; `point_edges`
reads the same band edges; the gate measures them. If a width is wrong it is wrong in one Python
function (ROAD_POINT_GRAPH.md 1.2, defect 1).

Three things are resolved here:

* **A road chain -> a carrier.** Arclength samples (`road_points.resample`), the interpolated
  cross-section at each one (`road_points.profile_at`), and one flat dict of per-sample numbers
  per the attribute registry below. Every band the GN stack sweeps reads its width and its lateral
  offset from that dict and from nothing else.
* **A clique -> a pad.** Each member point IS its arm's stop line (2.2), so the pad is
  `intersection_kit.build_junction_boundary` driven by `Arm.tail_pos` set to the AUTHORED mouth
  position -- not by a hidden setback solve. Plus the corner fillets, the star-shaped test the
  triangle fan depends on, and the turn paths.
* **delta = surface_z - ground_z -> the understructure.** One call into `road_support`, per
  sample, unconditionally (3.3 rule 1: `ground_z` is sampled by Build, never by a button).

WHY THE ATTRIBUTE REGISTRY IS A TABLE AND NOT A BAG OF STRING LITERALS. A Named Attribute node
pointing at a name the carrier does not carry reads 0 and builds a zero-width band -- silently. At
30-odd names that failure mode is indistinguishable from "my change had no effect". So every
attribute is declared once, with its unit and default, `carrier_values()` fills exactly that set,
and `point_build` asserts the mesh carries it (3.1).
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "lib"))

import intersection_kit as ik                                                # noqa: E402
import lane_movements as lm                                                  # noqa: E402
import lane_profile as lp                                                    # noqa: E402
import road_points as rp                                                     # noqa: E402
import road_support as rs                                                    # noqa: E402

try:
    from . import point_model as pm, point_profile as pp
except ImportError:
    import point_model as pm                                                 # noqa: E402
    import point_profile as pp                                               # noqa: E402


#: How far the structural deck's top face is sunk below the carriageway it carries. Big enough to
#: beat depth-buffer precision at world scale, small enough to be invisible.
DECK_Z_BIAS = -0.02

#: How far flush-with-the-road paint is lifted clear of the asphalt. Same reasoning, other sign.
PAINT_Z_BIAS = 0.01

#: A painted line's width, in metres. One number, because every marking in this model is the same
#: stripe in a different colour and pattern -- the type says WHICH line, never how wide.
MARK_WIDTH = 0.15

#: A `DOUBLE_Y` is two stripes this far apart, centre to centre.
MARK_DOUBLE_GAP = 0.30

#: Dash geometry, in metres: painted length and the gap after it. The road-marking standard is
#: roughly 1:2 at urban speeds.
MARK_DASH_ON = 3.0
MARK_DASH_OFF = 6.0

#: Kerb thickness as a fraction of its height -- a taller kerb reads as a heavier one.
KERB_THICKNESS = 0.5

#: How far a road with pedestrian access has to be above the ground before it grows a parapet.
#: An embankment this tall is a fall; below it, a kerb is the right answer and a wall would fence
#: off every slightly-raised street in the world.
BARRIER_MIN_DELTA = 2.0

#: Barrier thickness, in metres. Not authored: it is a constructional constant, and the artist has
#: no decision to make about it that the height does not already express.
BARRIER_THICKNESS = 0.32

#: Support-kind codes, as a float attribute (GN has no enum). Order is `road_support.KINDS`.
SUPPORT_CODE = {k: float(i) for i, k in enumerate(rs.KINDS)}


class Attr(object):
    """One per-sample carrier attribute: name, unit, default, and what reads it."""

    __slots__ = ("name", "unit", "default", "note")

    def __init__(self, name, unit, default, note=""):
        self.name, self.unit, self.default, self.note = name, unit, default, note

    def __repr__(self):
        return "Attr(%s %s)" % (self.name, self.unit)


CARRIER_ATTRS = (
    Attr("rka_halfw",   "m", 0.0, "paved half-width -- the carriageway band"),
    Attr("rka_shift",   "m", 0.0, "paved centre, signed lateral offset from the divide"),
    Attr("rka_med_h",   "m", 0.0, "median half-width"),
    Attr("rka_med_z",   "m", 0.0, "median top height (raised median = kerb height)"),
    Attr("rka_walk_cl", "m", 0.0, "left footway centre"),
    Attr("rka_walk_hl", "m", 0.0, "left footway half-width"),
    Attr("rka_walk_zl", "m", 0.0, "left footway level"),
    Attr("rka_walk_cr", "m", 0.0, "right footway centre"),
    Attr("rka_walk_hr", "m", 0.0, "right footway half-width"),
    Attr("rka_walk_zr", "m", 0.0, "right footway level"),
    Attr("rka_curb_ol", "m", 0.0, "left kerb line, signed lateral offset"),
    Attr("rka_curb_or", "m", 0.0, "right kerb line, signed lateral offset"),
    Attr("rka_curb_hl", "m", 0.0, "left kerb height (0 = no kerb here)"),
    Attr("rka_curb_hr", "m", 0.0, "right kerb height"),
    Attr("rka_curb_tl", "m", 0.0, "left kerb half-thickness"),
    Attr("rka_curb_tr", "m", 0.0, "right kerb half-thickness"),
    Attr("rka_deck_h",  "m", 0.0, "structural depth under the driving surface"),
    Attr("rka_deck_w",  "m", 0.0, "deck half-width -- the FULL outline, footways included"),
    Attr("rka_deck_c",  "m", 0.0, "deck centre, signed lateral offset"),
    Attr("rka_ground_z","m", 0.0, "sampled terrain height under this station"),
    Attr("rka_delta",   "m", 0.0, "surface_z - ground_z -- the one number the support derives from"),
    Attr("rka_support", "code", 0.0, "road_support kind, indexed into road_support.KINDS"),
    Attr("rka_fill_w",  "m", 0.0, "embankment TOE half-width (batter, not a prism)"),
    Attr("rka_pillar_h","m", 0.0, "column height, soffit down to ground"),
    Attr("rka_pillar_w","m", 1.4, "column side"),
    Attr("rka_pillar_param", "0/1", 0.0, "build a parametric column at this sample"),
    Attr("rka_sp_pillar","m", 30.0, "column spacing -- never 0, Resample Curve is unbounded at 0"),
    Attr("rka_sp_asset", "m", 5.0,  "asset row spacing"),
    # The barrier. HEIGHT is authored (`RoadData.barrier_height`); WHERE it stands is derived
    # here, from the same `delta` the supports come from -- so a viaduct grows a parapet and the
    # street it flies over does not, with nothing to remember. It rides the `__edges` carrier, so
    # `point_edges.open_runs` opens it at every gore and merge for free: that is the whole reason
    # a wall belongs on the outline and not on the centreline.
    Attr("rka_wall_h",  "m", 0.0, "barrier height above the footway (0 = no barrier here)"),
    Attr("rka_wall_c",  "m", 0.0, "barrier centre, signed lateral offset from the kerb line"),
    Attr("rka_wall_hw", "m", 0.0, "barrier half-thickness"),
    Attr("rka_wall_z",  "m", 0.0, "barrier TOP level above the carrier polyline"),
    # ...and its FOOT. The parametric barrier is a bar hung DOWN from its top, so the top is the
    # number it needs; a profile ASSET is drawn standing on its base, so the foot is. Two names
    # for one wall rather than one name meaning different ends to different layers.
    Attr("rka_wall_foot", "m", 0.0, "barrier FOOT level -- where a profile asset stands"),
    # THE PAINT. `lane_profile.marking_runs` has computed every painted boundary's type and its
    # per-station offset since the profile model landed, and nothing ever swept one -- so the road
    # kit built kerbs, footways, walls, pads and gores, and not one lane line. This is the width of
    # the stripe on a `__marks` carrier, whose polyline IS the line.
    Attr("rka_mark_w",  "m", 0.0, "lane marking half-width"),
)

ATTR_NAMES = tuple(a.name for a in CARRIER_ATTRS)
ATTR_DEFAULTS = {a.name: a.default for a in CARRIER_ATTRS}


# ------------------------------------------------------------------------------- small 2D maths

def _sub2(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _cross2(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _len2(a):
    return math.hypot(a[0], a[1])


def _norm2(a):
    n = _len2(a)
    return (a[0] / n, a[1] / n) if n > 1e-12 else (1.0, 0.0)


#: Samples along a turn connector's polyline. The connector's `curve` block is EXACT (its two
#: handles are emitted directly, nothing is fitted), so this only sets how finely the `points`
#: array approximates it -- and `points` is what a v1 consumer drives and what the preview draws
#: in `Lane polyline` mode. At n=9 a 90 deg turn's polyline sat 0.22 m inside its own curve, which
#: then read as the WORST "path off road" number on a whole district while nothing was wrong.
CONNECTOR_SAMPLES = 18


def bezier_through(p0, d0, p1, d1, n=CONNECTOR_SAMPLES):
    """A cubic from `p0` along `d0` to `p1` along `d1` -- sampled points plus its two control
    handles. Handle length is a third of the chord, the standard choice that keeps a 90 deg turn
    looking like a turn rather than a corner.

    THE ONE OWNER of the turn-connector shape: `point_export` emits these as `.lanekit` control
    points and `point_solve` draws them as the pad's movement preview, and the two must be the
    same curve or the cars drive somewhere the artist cannot see."""
    chord = math.dist(p0[:2], p1[:2]) or 1.0
    h = chord / 3.0
    a = [p0[k] + d0[k] * h for k in range(2)] + [p0[2] + (p1[2] - p0[2]) / 3.0]
    b = [p1[k] - d1[k] * h for k in range(2)] + [p0[2] + 2.0 * (p1[2] - p0[2]) / 3.0]
    out = []
    for i in range(n + 1):
        t = i / float(n)
        u = 1.0 - t
        out.append(tuple(u * u * u * p0[k] + 3 * u * u * t * a[k]
                         + 3 * u * t * t * b[k] + t * t * t * p1[k] for k in range(3)))
    return out, a, b


# ------------------------------------------------------------------------------- chain -> runs

#: `[[uid, ...], ...]` -- the road's chain split at every gap in its SEGMENT links. ONE OWNER, and
#: it is `point_model`: a run is a fact about the chain and its links, nothing is solved to find
#: one, and the ramp-direction rules in the model need it (a solve-layer owner could not give them
#: it without a circular import). `point_export` and `point_build` reach it through here, as they
#: always did.
road_runs = pm.road_runs


def _lerp_field(points, sample, name, is_loop=False):
    """One authored scalar, interpolated between the two bracketing stations.

    A station's OWN sample returns that station's value untouched, which is what makes "the taper
    length is the distance the author put between two points" true for a kerb height exactly as it
    already is for a lane count."""
    if sample.at_station is not None:
        return float(getattr(points[sample.at_station], name))
    i = sample.seg
    j = (i + 1) % len(points)
    a, b = float(getattr(points[i], name)), float(getattr(points[j], name))
    return a + (b - a) * sample.local


def _bool_field(points, sample, name):
    """A flag holds from the station that declares it until the next one -- a flag has no
    halfway."""
    i = sample.at_station if sample.at_station is not None else sample.seg
    return bool(getattr(points[i], name))


# ------------------------------------------------------------------------------- chain -> carrier

class RoadSolve(object):
    """One run of one road, fully resolved: the spine, the cross-section at every sample, and the
    per-sample numbers the GN stack sweeps."""

    __slots__ = ("road", "uids", "points", "stations", "samples", "profiles", "values",
                 "is_loop", "edges_left", "edges_right", "routes")

    def __init__(self, road, uids, points, stations, samples, profiles, values, is_loop,
                 edges_left, edges_right, routes):
        self.road, self.uids, self.points = road, uids, points
        self.stations, self.samples, self.profiles = stations, samples, profiles
        #: `values[i]` is the flat attribute dict for `samples[i]` -- exactly `ATTR_NAMES`.
        self.values = values
        self.is_loop = is_loop
        #: The PAVED band's two boundary polylines in world space -- what `point_edges` unions and
        #: what the kerb rides. Taken from the same numbers the asphalt is swept from, never
        #: re-derived, so the road's boundary and the road's surface cannot drift apart.
        self.edges_left, self.edges_right = edges_left, edges_right
        self.routes = routes

    def __len__(self):
        return len(self.samples)

    def length(self):
        return self.samples[-1].s if self.samples else 0.0

    def attr(self, name):
        return [v[name] for v in self.values]

    def __repr__(self):
        return "RoadSolve(%s %d samples %.1f m)" % (self.road.name, len(self.samples),
                                                    self.length())


def _lateral(pos, normal, off):
    return (pos[0] + normal[0] * off, pos[1] + normal[1] * off, pos[2] + normal[2] * off)


class MarkRun(object):
    """One painted boundary along one run: where it is, what it is, and how long it lives.

    A marking is a SLOT PROPERTY (`lane_profile.Slot.mark_left`, the line along that slot's low-s
    edge), so a line that appears when a lane opens, moves outward as a median widens, or stops
    when a ramp departs is the same mechanism as the lane itself -- no counting, no inference from
    `lanes_fwd`. `lane_profile.marking_runs` has computed all of this since the profile model
    landed; until now nothing swept the result."""

    __slots__ = ("slot_id", "mark", "points", "dashed", "yellow", "double")

    def __init__(self, slot_id, mark, points):
        self.slot_id, self.mark, self.points = slot_id, mark, points
        self.dashed = mark in (lp.MARK_DASH_W, lp.MARK_DASH_Y)
        self.yellow = mark in (lp.MARK_SOLID_Y, lp.MARK_DASH_Y, lp.MARK_DOUBLE_Y)
        self.double = mark == lp.MARK_DOUBLE_Y

    def __repr__(self):
        return "MarkRun(%s %s %d pts)" % (self.slot_id, self.mark, len(self.points))


def solve_marks(solve):
    """`[MarkRun]` for one run -- every painted lane boundary, as world polylines.

    ON THE CENTRELINE'S FRAME, NOT THE OUTLINE. This is the one piece of road furniture that
    genuinely belongs on the centreline: a lane marking is an INTERNAL boundary of a carriageway
    that is continuous by construction, so it needs none of `point_edges.open_runs`' machinery --
    and `marking_runs`' own presence rule ("something must exist on both sides, or it is the road's
    outer edge") already ends a line exactly where the lane it divides ends.

    A `DOUBLE_Y` comes back as TWO runs, `MARK_DOUBLE_GAP` apart. One wide yellow band is not a
    double line, and at any distance you can tell.

    A DASHED line comes back as one `MarkRun` PER DASH, cut here in Python. PYTHON OWNS THE CURVE
    AND GN ONLY SWEEPS -- the rule this whole addon is built on -- and a dash is a shorter curve,
    not a shading trick: this project has no image textures at all, so there is no alpha to cut it
    with, and geometry survives the glTF bake and reads back correctly. (Doing it in Geometry
    Nodes means resample-and-delete-alternate-spans, which is a node graph guessing at exactly the
    arithmetic that is three lines here.)"""
    if not getattr(solve.road, "markings", True):
        return []
    pset = lp.ProfileSet(solve.profiles)
    runs = lp.marking_runs(pset, len(solve.samples))
    out = []
    for r in runs:
        i0, i1 = r["i0"], r["i1"]
        if i1 <= i0:
            continue
        offsets = r["offsets"]
        shifts = ((-MARK_DOUBLE_GAP / 2.0, MARK_DOUBLE_GAP / 2.0)
                  if r["mark"] == lp.MARK_DOUBLE_Y else (0.0,))
        for shift in shifts:
            pts, svals = [], []
            for i in range(i0, i1 + 1):
                sm = solve.samples[i]
                pts.append(_lateral(sm.pos, sm.normal, offsets[i] + shift))
                # The CENTRELINE arclength at this sample -- the run's one clock, so every
                # boundary's dashes break at the same cross-sections. See `_dashes`.
                svals.append(sm.s)
            if len(pts) < 2:
                continue
            mark = r["mark"]
            if mark in (lp.MARK_DASH_W, lp.MARK_DASH_Y):
                out += [MarkRun(r["slot_id"], mark, d) for d in _dashes(pts, svals)]
            else:
                out.append(MarkRun(r["slot_id"], mark, pts))
    return out


def _dashes(pts, svals, on=None, off=None):
    """A boundary cut into painted dashes, laid out on the RUN'S OWN CLOCK.

    `svals[k]` is the centreline arclength at `pts[k]`, and the dash grid is cut in THAT domain --
    every boundary on a run therefore breaks at the same cross-sections, whatever sample it starts
    at and whatever its own length is.

    THE CLOCK IS SHARED OR THE PAINT IS WRONG. Walking each boundary from its own first point
    restarts the grid wherever that boundary happens to open, so a lane that appears mid-run is
    out of phase with the lines either side of it for its whole length: measured on the testbed,
    an auxiliary lane opening 4.00 m into a run came out 4.00 m off a 9.00 m period -- very nearly
    a half-period, which reads as the dashes on that lane being wrong rather than merely offset.
    Anchoring on the centreline `s` also keeps them aligned through a bend, where the outer line's
    own arclength runs ahead of the inner one's; the dashes then vary a few percent in length
    across the carriageway, which is exactly what setting out from stations does on a real road.

    Whole dashes only -- a stub of paint at the end of a lane reads as a mistake, so a final
    partial dash is dropped rather than drawn short."""
    on = MARK_DASH_ON if on is None else on
    off = MARK_DASH_OFF if off is None else off
    period = on + off
    if period <= 1e-6 or len(pts) < 2:
        return [pts]

    def at(target):
        """The point at centreline arclength `target`, interpolated between samples."""
        if target <= svals[0]:
            return pts[0]
        if target >= svals[-1]:
            return pts[-1]
        i = 0
        while i + 2 < len(svals) and svals[i + 1] < target:
            i += 1
        span = svals[i + 1] - svals[i]
        t = 0.0 if span <= 1e-9 else (target - svals[i]) / span
        a, b = pts[i], pts[i + 1]
        return tuple(a[k] + (b[k] - a[k]) * t for k in range(len(a)))

    out = []
    # Start at the first whole dash of the SHARED grid that this boundary actually covers.
    first = math.floor(svals[0] / period) * period
    s0 = first
    while s0 < svals[-1] - 1e-9:
        s1 = s0 + on
        if s0 >= svals[0] - 1e-9 and s1 <= svals[-1] + 1e-9:
            seg = [at(s0)]
            seg += [pts[k] for k in range(len(pts)) if s0 < svals[k] < s1]
            seg.append(at(s1))
            if len(seg) >= 2:
                out.append(seg)
        s0 += period
    return out


def solve_road(net, road, uids=None, ground_fn=None):
    """One run -> a `RoadSolve`. `uids` defaults to the road's whole chain (use `road_runs` to
    split it first -- see that function for why a junction gap is not carriageway).

    `ground_fn(x, y)` is the terrain sampler. Build passes the real raycast; everything else
    passes None, which falls back to each point's own authored/last-sampled `ground_z`. It is a
    PARAMETER and not a button: 3.3 rule 1 exists because `Cut Ground Under Road` being a manual
    panel step is the confirmed root cause of the mesh-hole reports."""
    if uids is None:
        uids = [u for u in road.points if u in net.points]
    if len(uids) < 2:
        return None
    points = [net.resolved(u) for u in uids]
    is_loop = bool(road.is_loop) and len(uids) == len(road.points)
    # A RUN'S END IS CUT ON THE CHAIN'S DIRECTION, not on the run's own last chord -- otherwise the
    # carriageway meets the pad on a different plane from the cap (`point_profile.run_end_axes`).
    stations = pp.stations(points, is_loop,
                           end_axes=(None if is_loop else pp.run_end_axes(net, points)))
    samples = rp.resample(stations, is_loop)
    if not samples:
        return None
    routes = rp.lane_taper_route(stations, samples, is_loop)

    values, left, right = [], [], []
    for sm in samples:
        prof = rp.profile_at(stations, sm, is_loop)
        v = dict(ATTR_DEFAULTS)

        # ---- the carriageway. `paved_extents` excludes the footway slots; `extents` includes
        # them. Both come back as POSITIVE distances, so the sign is applied here, once.
        p_neg, p_pos = lp.paved_extents(prof)
        f_neg, f_pos = lp.extents(prof)
        v["rka_halfw"] = (p_neg + p_pos) / 2.0
        v["rka_shift"] = (p_pos - p_neg) / 2.0
        v["rka_deck_w"] = (f_neg + f_pos) / 2.0
        v["rka_deck_c"] = (f_pos - f_neg) / 2.0

        # ---- the median, asked for by slot id and never computed
        mi = prof.index_of(pp.MED_ID)
        med_w = prof.slots[mi].width if mi is not None else 0.0

        # ---- the kerb lines sit exactly on the paved edges
        kh_l = _lerp_field(points, sm, "left_kerb_height", is_loop)
        kh_r = _lerp_field(points, sm, "right_kerb_height", is_loop)
        v["rka_curb_ol"] = p_pos
        v["rka_curb_or"] = -p_neg
        v["rka_curb_hl"], v["rka_curb_hr"] = kh_l, kh_r
        v["rka_curb_tl"] = kh_l * KERB_THICKNESS
        v["rka_curb_tr"] = kh_r * KERB_THICKNESS
        # WHAT THE DIVIDE IS, from `RoadData.median_style` -- authored once per road, because the
        # divide's material is one material for the whole run. `median_width` still varies per
        # station, so an island that widens, narrows and vanishes is authored exactly as before.
        #   NONE   -- no divide geometry at all, whatever the width says
        #   PAINT  -- double yellow: a flush stripe pair, no island (width is the PAIR's span)
        #   RAISED -- the island, its top at kerb height (what this always built)
        #   WALL   -- RAISED, and a barrier stands on it
        style = getattr(road, "median_style", pm.MED_RAISED)
        if style == pm.MED_NONE:
            v["rka_med_h"] = 0.0
            v["rka_med_z"] = 0.0
        elif style == pm.MED_PAINT:
            # Flush paint, and NARROW: the authored width is the gap between the two lines, not a
            # band of yellow. Half of it either side of the divide is the pair.
            v["rka_med_h"] = min(med_w, MARK_WIDTH * 2.0) / 2.0 if med_w > 0.0 else 0.0
            v["rka_med_z"] = 0.0
        else:
            v["rka_med_h"] = med_w / 2.0
            v["rka_med_z"] = kh_l if med_w > 0.0 else 0.0

        # ---- the footways, read off their own slots so a walk that tapers away tapers here too
        for sid, cw, hw, zk, sign in (("SW_L", "rka_walk_cl", "rka_walk_hl", "rka_walk_zl", 1),
                                      ("SW_R", "rka_walk_cr", "rka_walk_hr", "rka_walk_zr", -1)):
            si = prof.index_of(sid)
            if si is None:
                continue
            w = prof.slots[si].width
            if w <= 0.0:
                continue
            v[cw] = lp.slot_offset(prof, si)
            v[hw] = w / 2.0
            v[zk] = kh_l if sign > 0 else kh_r

        # ---- the understructure. ONE call, every sample, unconditionally.
        gz = ground_fn(sm.pos[0], sm.pos[1]) if ground_fn else None
        if gz is None:
            # A MISS is not zero. A road over water, or past the terrain's edge, keeps its
            # authored/last-sampled value -- dropping it to 0 would grow a 40 m column to nothing.
            gz = _lerp_field(points, sm, "ground_z", is_loop)
        deck_t = _lerp_field(points, sm, "deck_thickness", is_loop)
        sp = rs.support_profile(sm.pos[2], gz, v["rka_deck_w"], deck_t)
        v["rka_ground_z"] = gz
        v["rka_delta"] = sp["delta"]
        v["rka_support"] = SUPPORT_CODE[sp["kind"]]
        v["rka_fill_w"] = sp["toe_half_width"]
        v["rka_deck_h"] = sp["deck_thickness"] if sp["kind"] == rs.SUPPORT_PIER else 0.0
        v["rka_pillar_h"] = sp["pier_height"]
        spacing = _lerp_field(points, sm, "pillar_spacing", is_loop)
        v["rka_sp_pillar"] = max(spacing, 0.05)
        # A column is built where the support says PIER, the station has not vetoed it, and the
        # column would be tall enough to be a column. `pillar_skip` is the per-station escape
        # hatch 3.3 rule 4 asks for: PIER_SPACING with no override puts a bent inside a building.
        v["rka_pillar_param"] = 1.0 if (sp["kind"] == rs.SUPPORT_PIER
                                        and not _bool_field(points, sm, "pillar_skip")
                                        and sp["pier_height"] > 0.5) else 0.0

        # ---- the barrier. One rule, both cases: a road nobody may walk on is fenced along its
        # whole length, and a road they may walk on is fenced only where it is off the ground.
        v["rka_wall_h"] = (float(getattr(road, "barrier_height", 0.0))
                           if (not road.ped_access or sp["kind"] == rs.SUPPORT_PIER
                               or sp["delta"] >= BARRIER_MIN_DELTA)
                           else 0.0)
        # The foot is derived from the same two numbers and never authored separately, so the two
        # ends of one wall cannot disagree. `rka_wall_z` is filled in by `edge_run_values`, which
        # is where the wall's height above the footway is known; the foot follows it there.

        values.append(v)
        left.append(_lateral(sm.pos, sm.normal, p_pos))
        right.append(_lateral(sm.pos, sm.normal, -p_neg))

    return RoadSolve(road, uids, points, stations, samples,
                     [rp.profile_at(stations, s, is_loop) for s in samples],
                     values, is_loop, left, right, routes)


def solve_network(net, ground_fn=None):
    """Every run of every road. Returns `{road_name: [RoadSolve, ...]}` in chain order."""
    out = {}
    for road in net.roads.values():
        solves = []
        for uids in road_runs(net, road):
            s = solve_road(net, road, uids, ground_fn)
            if s is not None:
                solves.append(s)
        out[road.name] = solves
    return out


# ------------------------------------------------------------------------------- clique -> pad

class Mouth(object):
    """One junction member, as the pad sees it. The point IS the stop line (2.2) -- there is no
    hidden setback solve between the artist's Empty and this."""

    __slots__ = ("uid", "point", "road", "pos", "out_dir", "bearing", "lanes_in", "lanes_out",
                 "lane_width", "half_in", "half_out", "profile", "arm", "fwd_leaves", "normal",
                 "walk_in", "walk_out", "kerb_in", "kerb_out", "wall_h")

    def __init__(self, uid, point, road, pos, out_dir, lanes_in, lanes_out, lane_width,
                 half_in, half_out, profile, fwd_leaves=True, normal=(0.0, 1.0, 0.0),
                 walk_in=0.0, walk_out=0.0, kerb_in=0.0, kerb_out=0.0, wall_h=0.0):
        self.uid, self.point, self.road = uid, point, road
        self.pos = pos
        #: Unit XY direction pointing AWAY from the junction -- the way a car LEAVES along this arm,
        #: which is exactly what `intersection_kit.Arm.angle_deg` means.
        self.out_dir = out_dir
        self.bearing = math.degrees(math.atan2(out_dir[1], out_dir[0]))
        self.lanes_in, self.lanes_out = lanes_in, lanes_out
        self.lane_width = lane_width
        self.half_in, self.half_out = half_in, half_out
        self.profile = profile
        #: True when the road's FWD direction (increasing chain index) points AWAY from the pad.
        #: Decides which lane group arrives and which departs, and nothing else.
        self.fwd_leaves = fwd_leaves
        #: The +s lateral direction of the profile, in world space -- so a lane's authored offset
        #: becomes a world position without anything here computing an offset of its own.
        self.normal = normal
        #: The footway width, kerb height and barrier height on each of this mouth's two sides,
        #: named by which side of the PAD they face. A corner between two arms is bounded by one
        #: arm's OUT side and the next arm's IN side, so those are the numbers its kerb and
        #: footway are built from -- which is what makes the corner meet each street's own
        #: furniture instead of approximating it.
        self.walk_in, self.walk_out = walk_in, walk_out
        self.kerb_in, self.kerb_out = kerb_in, kerb_out
        self.wall_h = wall_h
        self.arm = None

    def dir_in(self):
        """The direction of travel of an ARRIVING vehicle: into the pad."""
        return (-self.out_dir[0], -self.out_dir[1])

    def lane_dir(self, arriving):
        return lp.REV if (arriving == self.fwd_leaves) else lp.FWD

    def lane_points(self, arriving):
        """`[(index_from_median, world_xyz)]` for this mouth's arriving or departing lanes.

        ASKS `lane_profile.travel_lanes` for the offsets; multiplies by the mouth's own lateral
        frame. No lateral arithmetic happens here (defect 1)."""
        want = self.lane_dir(arriving)
        out = []
        for slot, off, d, idx in lp.travel_lanes(self.profile):
            if d != want:
                continue
            out.append((idx, (self.pos[0] + self.normal[0] * off,
                              self.pos[1] + self.normal[1] * off,
                              self.pos[2] + self.normal[2] * off)))
        return sorted(out)

    def __repr__(self):
        return "Mouth(%s %.0fdeg %din/%dout)" % (self.uid[:6], self.bearing,
                                                 self.lanes_in, self.lanes_out)


class JunctionSolve(object):
    """One pad: its members, its boundary, the triangle fan and the turn paths."""

    __slots__ = ("uids", "centre", "mouths", "boundary", "fan", "fan_apex", "turns",
                 "kerb_radius", "star_ok", "star_worst", "corners")

    def __init__(self, uids, centre, mouths, boundary, fan, turns, kerb_radius,
                 star_ok, star_worst, fan_apex=None, corners=()):
        self.uids, self.centre, self.mouths = uids, centre, mouths
        #: World-space CCW pad ring.
        self.boundary = boundary
        #: `[(a, b, c)]` -- the pad's TRIANGLES, in world space. Always watertight: a fan from
        #: `fan_apex` when a kernel point exists, ear-clipped otherwise (`pad_triangles`).
        self.fan = fan
        #: The apex the fan radiates from -- the pad ring's KERNEL point, not necessarily the
        #: centroid. `centre` stays the mouths' centroid, which is what the export reports as the
        #: junction's position and what `point_edges` bounds the pad band with.
        self.fan_apex = fan_apex if fan_apex is not None else centre
        self.turns = turns
        self.kerb_radius = kerb_radius
        self.star_ok, self.star_worst = star_ok, star_worst
        #: The pad's own edge furniture -- see `Corner`.
        self.corners = list(corners)

    def __repr__(self):
        return "JunctionSolve(%d arms, %d ring pts%s)" % (
            len(self.mouths), len(self.boundary), "" if self.star_ok else " NOT STAR-SHAPED")


def is_star_shaped(poly, c, eps=1e-6):
    """`(ok, worst)` -- is every point of CCW `poly` visible from `c`?

    This is the triangle fan's PRECONDITION, not a nicety. A fan from the centroid tessellates a
    concave ring correctly if and only if the ring is star-shaped about that centroid, and a
    hand-dragged mouth pulled closer to the centre than a neighbouring fillet breaks it -- the pad
    then folds over itself and reads as a black crater. An n-gon instead of a fan is not the fix:
    n-gon tessellation of a concave non-planar pad left measured 0.38-0.49 m holes, which is why
    this is a fan with a checked precondition rather than a polygon with a hope.

    `worst` is the deepest violation in metres, so the gate can report a number the artist can act
    on rather than a boolean."""
    worst = 0.0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        e = _sub2(b, a)
        d = _cross2(e, _sub2(c, a))
        ln = _len2(e)
        if ln < 1e-9:
            continue
        signed = d / ln                       # positive = c is left of a->b = inside, for CCW
        if signed < -eps:
            worst = max(worst, -signed)
    return worst <= eps, worst


def fan_origin(poly, start, eps=1e-6, iterations=32):
    """`(origin, ok, worst)` -- a point of CCW `poly` that every vertex can see.

    THE FAN'S APEX DOES NOT HAVE TO BE THE CENTROID, and insisting that it was is what made a pad
    fragile. `is_star_shaped` is a property of the ring AND the apex together: a mouth dragged a
    few centimetres inside a neighbour's fillet leaves the ring perfectly fannable from a point
    slightly off-centre while failing from the centroid -- and the build then refused outright
    ("the pad ring folds 0.02 m past its own centroid"), with a suggested remedy (Auto Setback)
    that had nothing to move. Moving the apex is free and fixes the whole class.

    The search is the obvious one: while any edge has the apex on its outside, push the apex along
    those edges' inward normals by the amount it is out by. It converges in a handful of steps for
    anything shaped like a junction, and when it does not the caller ear-clips instead."""
    ok, worst = is_star_shaped(poly, start)
    if ok:
        return start, True, worst
    n = len(poly)
    cx, cy = start
    best_worst, best = worst, (cx, cy)
    for _ in range(iterations):
        px = py = 0.0
        hits = 0
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            e = _sub2(b, a)
            ln = _len2(e)
            if ln < 1e-9:
                continue
            signed = _cross2(e, _sub2((cx, cy), a)) / ln
            if signed < eps:
                # Interior is LEFT of a->b on a CCW ring, so the inward normal is the left normal.
                px += (-e[1] / ln) * (eps - signed)
                py += (e[0] / ln) * (eps - signed)
                hits += 1
        if not hits:
            break
        cx += px / hits * 1.05
        cy += py / hits * 1.05
        ok, worst = is_star_shaped(poly, (cx, cy))
        if worst < best_worst:
            best_worst, best = worst, (cx, cy)
        if ok:
            return (cx, cy), True, worst
    return best, False, best_worst


def _in_triangle(p, a, b, c, eps=1e-12):
    d1 = _cross2(_sub2(b, a), _sub2(p, a))
    d2 = _cross2(_sub2(c, b), _sub2(p, b))
    d3 = _cross2(_sub2(a, c), _sub2(p, c))
    return d1 >= -eps and d2 >= -eps and d3 >= -eps


def ear_clip(poly, eps=1e-12):
    """`[(i, j, k)]` -- indices into CCW `poly`. The fallback when no apex sees the whole ring.

    A pad ring is a few dozen vertices, so plain O(n^2) ear clipping is far below the noise floor
    of a rebuild. What matters is that it ALWAYS returns a watertight tessellation: a pad that
    cannot be fanned must still be a pad, not a hole in the world found by walking into it."""
    idx = list(range(len(poly)))
    if len(idx) < 3:
        return []
    out = []
    guard = 0
    while len(idx) > 3 and guard < len(poly) * len(poly) + 16:
        guard += 1
        clipped = False
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if _cross2(_sub2(b, a), _sub2(c, b)) <= eps:
                continue                                   # reflex or collinear: not an ear
            if any(_in_triangle(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            idx.pop(k)
            clipped = True
            break
        if not clipped:
            break                                          # degenerate ring; take what we have
    if len(idx) == 3:
        out.append(tuple(idx))
    return out


def pad_triangles(boundary, mouths, centroid):
    """`(apex, triangles, star_ok, worst)` -- the pad's surface, always watertight.

    ONE OWNER of how a pad is tessellated, so `point_build` sweeps exactly what the gate measured
    and the preview draws. `star_ok` is kept and reported, but it is ADVISORY now: it says the
    apex had to move, not that the build failed."""
    flat = [(p[0], p[1]) for p in boundary]
    origin, ok, worst = fan_origin(flat, (centroid[0], centroid[1]))
    apex = (origin[0], origin[1], _idw_z(mouths, origin))
    if ok:
        tris = [(apex, boundary[i], boundary[(i + 1) % len(boundary)])
                for i in range(len(boundary))]
    else:
        tris = [(boundary[i], boundary[j], boundary[k]) for i, j, k in ear_clip(flat)]
    return apex, tris, ok, worst


def _idw_z(mouths, xy, power=2.0):
    """Pad height at `xy` -- inverse-distance-weighted from the mouths.

    2.2 step 5: Z follows the mouths, so a junction on a grade TILTS instead of stepping. IDW
    rather than a fitted plane because a plane is underdetermined for a two-arm pad and overfits a
    four-arm one; IDW is exact AT each mouth (the weight diverges), which is the property that
    actually matters -- the pad must meet each approach at that approach's own elevation."""
    num = den = 0.0
    for m in mouths:
        d2 = (m.pos[0] - xy[0]) ** 2 + (m.pos[1] - xy[1]) ** 2
        if d2 < 1e-9:
            return m.pos[2]
        w = 1.0 / (d2 ** (power / 2.0))
        num += w * m.pos[2]
        den += w
    return num / den if den else 0.0


class _PadArm(ik.Arm):
    """An `intersection_kit.Arm` whose two curb-to-centreline distances are the AUTHORED paved
    half-widths, not `lane_width * lane_count`.

    Why a subclass and not a fudged `lane_width`: `Arm` derives its widths from a uniform lane
    count, and this model's cross-section has aux lanes, shoulders, parking bays and a median of
    arbitrary width -- widths whose only correct owner is `lane_profile.paved_extents`. Every
    other arm-geometry function in the kit (`curb_edges`, `_junction_corner_vertex`,
    `build_junction_boundary`) reads the widths through `in_width()`/`out_width()`, exactly as
    that module's own docstring promises, so overriding those two is the whole change and the
    fillet maths is reused untouched.

    2.2 step 1: the mouth cross-bar spans the point's PAVED width, kerb to kerb, and the MEDIAN IS
    IGNORED -- it is pad surface here, not an island -- so `median_width` stays 0."""

    def __init__(self, name, angle_deg, half_in, half_out, **kw):
        ik.Arm.__init__(self, name, angle_deg, **kw)
        self._half_in, self._half_out = half_in, half_out

    def in_width(self):
        return self._half_in

    def out_width(self):
        return self._half_out


def _round_ring(ring, segments=8):
    """Expand `[(x, y, radius)]` into a plain point ring, rounding every vertex with a radius.

    `build_junction_boundary` returns the fillet as a vertex PLUS a radius, because its own
    downstream (`kit_common._poly_curve_with_radius`) rounds it in Geometry Nodes. A triangle fan
    needs the arc as real points, so it is expanded here -- with the tangent length clamped to
    half of each adjoining edge, which is what stops a large `fillet_radius` on a short arm from
    eating past its neighbour's corner and inverting the ring."""
    n = len(ring)
    out = []
    for i in range(n):
        px, py, r = ring[i]
        v = (px, py)
        if r <= 1e-6 or n < 3:
            out.append(v)
            continue
        a = ring[(i - 1) % n][:2]
        b = ring[(i + 1) % n][:2]
        da, db = _sub2(a, v), _sub2(b, v)
        la, lb = _len2(da), _len2(db)
        if la < 1e-9 or lb < 1e-9:
            out.append(v)
            continue
        ua, ub = (da[0] / la, da[1] / la), (db[0] / lb, db[1] / lb)
        cosang = max(-1.0, min(1.0, ua[0] * ub[0] + ua[1] * ub[1]))
        half = math.acos(cosang) / 2.0
        if half < 1e-6 or abs(half - math.pi / 2.0) < 1e-6:
            out.append(v)
            continue
        t = min(r / math.tan(half), la * 0.5, lb * 0.5)
        ta = (v[0] + ua[0] * t, v[1] + ua[1] * t)
        tb = (v[0] + ub[0] * t, v[1] + ub[1] * t)
        eff = t * math.tan(half)
        # Arc centre lies along the angle bisector, `eff / sin(half)` from the vertex.
        bis = _norm2((ua[0] + ub[0], ua[1] + ub[1]))
        c = (v[0] + bis[0] * eff / math.sin(half), v[1] + bis[1] * eff / math.sin(half))
        a0 = math.atan2(ta[1] - c[1], ta[0] - c[0])
        a1 = math.atan2(tb[1] - c[1], tb[0] - c[0])
        d = a1 - a0
        while d > math.pi:
            d -= 2 * math.pi
        while d < -math.pi:
            d += 2 * math.pi
        for k in range(segments + 1):
            ang = a0 + d * k / float(segments)
            out.append((c[0] + eff * math.cos(ang), c[1] + eff * math.sin(ang)))
    return out


def clamp_corners(ring, mouths, cx, cy, kerb_radius):
    """Drop any corner vertex that escapes past the mouths that produced it.

    THE 15-DEGREE SKEW, AND WHY THIS IS NOT A FUDGE. `build_junction_boundary` rounds the corner
    between two angularly-adjacent arms at the point where their two outer kerb LINES intersect.
    For arms 15 degrees apart those lines are nearly parallel, so they meet 40-plus metres past
    anything the artist placed -- measured on a 15 degree crossing of a 2x2 arterial: a corner
    36.9 m outside the pad, which makes the ring non-star, folds the triangle fan, and reads
    in-game as a black crater. That is the SAME defect as the previous model's hidden setback
    solve asking a 15 degree crossing for a 136.7 m setback; it has simply moved from the setback
    to the fillet.

    The rule that kills it for good is the model's own: THE POINT IS THE STOP LINE. A corner that
    would need more room than the arms were given is not a corner -- the two carriageways just run
    into each other, exactly as an angularly-adjacent THROUGH pair already does (which is why
    dropping the vertex is the existing, tested shape of this answer and not a new case). The
    resulting straight edge between the two caps is the sharp gore a 15 degree X-crossing really
    has.

    Corner vertices are the ones `build_junction_boundary` gives a non-zero radius; the arms' own
    cap points carry radius 0 and are never dropped -- the pad can never shrink inside a mouth."""
    reach = 0.0
    for m in mouths:
        local = (m.pos[0] - cx, m.pos[1] - cy)
        half = max(m.half_in, m.half_out)
        reach = max(reach, math.hypot(_len2(local), half))
    limit = reach + kerb_radius
    return [v for v in ring if v[2] <= 1e-6 or _len2((v[0], v[1])) <= limit], limit


def mouth_axis(net, uid):
    """`(out_dir, fwd_leaves, seg_uid)` for a junction member.

    `out_dir` points AWAY from the pad, along this arm's own road -- the direction a car LEAVES
    on, which is what `Arm.angle_deg` means. WHICH SIDE is the carriageway comes from the chain
    neighbour this mouth is still joined to by a SEGMENT link (the other side is the pad); WHICH
    WAY that carriageway runs comes from `point_model.station_axis`, so a hand-rotated mouth turns
    its pad arm too. `fwd_leaves` says whether the road's FWD direction (increasing chain index)
    points away from the junction, which is what decides which lane group arrives and departs.

    Deriving the direction from the neighbour's POSITION alone -- which is what this did -- is why
    rotating an intersection mouth bent its street and left the pad untouched."""
    pt = net.points[uid]
    road = net.road_of(uid)
    if road is None:
        return (1.0, 0.0), True, None
    chain = [u for u in road.points if u in net.points]
    try:
        i = chain.index(uid)
    except ValueError:
        return (1.0, 0.0), True, None
    for j, fwd_leaves in ((i + 1, True), (i - 1, False)):
        if 0 <= j < len(chain) and pt.has_link(chain[j], pm.LINK_SEGMENT):
            # The AUTHORED frame first. `station_axis` returns the chain-FWD direction, so it is
            # the out-direction only when FWD leaves the pad.
            ax = pm.station_axis(net, uid)
            if ax is not None:
                return (ax if fwd_leaves else (-ax[0], -ax[1])), fwd_leaves, chain[j]
            d = _sub2(net.points[chain[j]].pos, pt.pos)
            if _len2(d) > 1e-9:
                return _norm2(d), fwd_leaves, chain[j]
    # A mouth with no carriageway neighbour at all is a gate error (`check_chains`), not something
    # to invent an axis for -- but the solve must still return something drawable meanwhile.
    return (1.0, 0.0), True, None


def build_mouth(net, uid, ground_fn=None):
    """One junction member -> a `Mouth`, with its arm."""
    pt = net.resolved(uid)
    road = net.road_of(uid)
    out_dir, fwd_leaves, _seg = mouth_axis(net, uid)
    prof = pp.build_profile(pt)
    p_neg, p_pos = lp.paved_extents(prof)
    n_fwd = int(pt.lanes_fwd) + int(pt.aux_fwd)
    n_bwd = int(pt.lanes_bwd) + int(pt.aux_bwd)
    if fwd_leaves:
        lanes_in, lanes_out = n_bwd, n_fwd
        half_in, half_out = p_neg, p_pos
    else:
        lanes_in, lanes_out = n_fwd, n_bwd
        half_in, half_out = p_pos, p_neg
    fwd_tan = out_dir if fwd_leaves else (-out_dir[0], -out_dir[1])
    normal = rp.left_normal((fwd_tan[0], fwd_tan[1], 0.0), 0.0)
    # LEFT IS +s (`solve_road` builds `edges_left` at `+p_pos`), and the arm's OUT side is +s
    # exactly when the road's FWD direction leaves the pad. So which of the point's two authored
    # walk/kerb values faces which side of the pad is decided by `fwd_leaves` and nothing else.
    walk_out = float(pt.left_walk_width if fwd_leaves else pt.right_walk_width) / 2.0
    walk_in = float(pt.right_walk_width if fwd_leaves else pt.left_walk_width) / 2.0
    kerb_out = float(pt.left_kerb_height if fwd_leaves else pt.right_kerb_height)
    kerb_in = float(pt.right_kerb_height if fwd_leaves else pt.left_kerb_height)
    # Same barrier rule as `solve_road`, read off the station's own sampled ground so a pad needs
    # no raycast of its own: a mouth of a road nobody may walk on, or one well off the ground,
    # carries the wall around its corners too.
    elevated = (float(pt.pos[2]) - float(pt.ground_z)) >= BARRIER_MIN_DELTA
    wall_h = (float(getattr(road, "barrier_height", 0.0))
              if road is not None and (not road.ped_access or elevated) else 0.0)
    return Mouth(uid, pt, road, tuple(float(c) for c in pt.pos), out_dir,
                 lanes_in, lanes_out, float(pt.lane_width), half_in, half_out, prof,
                 fwd_leaves, normal, walk_in, walk_out, kerb_in, kerb_out, wall_h)


class Corner(object):
    """One pad corner's edge furniture: the polyline the kerb rides, and the two arms it bridges.

    A pad used to be bare asphalt out to its own boundary, with each street's footway stopping
    dead at its mouth -- so every crossing in the world had four missing pavement corners, which
    is what a pedestrian notices first. This is the piece that closes them, and it is built from
    `intersection_kit.build_junction_curb_segments`: the SAME corner curve the pad's own boundary
    is rounded with, so the footway cannot bulge off the kerb it is supposed to sit behind."""

    __slots__ = ("a_uid", "b_uid", "points", "walk", "kerb", "wall")

    def __init__(self, a_uid, b_uid, points, walk, kerb, wall):
        self.a_uid, self.b_uid = a_uid, b_uid
        #: World-space, running CCW around the pad: from arm A's outer cap corner to arm B's.
        self.points = points
        #: Per-vertex footway half-width / kerb height / barrier height, blended along the corner
        #: from what arm A authors to what arm B authors -- so a wide street's footway narrows
        #: into a lane's rather than stepping.
        self.walk, self.kerb, self.wall = walk, kerb, wall

    def __repr__(self):
        return "Corner(%s->%s, %d pts)" % (self.a_uid[:6], self.b_uid[:6], len(self.points))


def _cap_points(m, tail_length=1.0):
    """`(p_in, p_out)` -- this arm's two cap corners, local to the pad centroid, derived exactly as
    `intersection_kit.build_junction_boundary` derives them so the corner segments and the pad ring
    share their endpoints instead of nearly sharing them."""
    c = m.arm.tail_center(tail_length)
    d = ik.arm_dir(m.arm.angle_deg)
    perp = ik.lane_perp(d, m.arm.traffic_side)
    return (ik.vadd(ik.vscale(perp, -m.arm.in_width()), c),
            ik.vadd(ik.vscale(perp, m.arm.out_width()), c))


def junction_corners(mouths, kerb_radius, cx, cy, segments=8):
    """`[Corner]` -- one per real corner of the pad. A through-pair contributes none, because the
    road runs straight on through and its own edge run already owns that stretch."""
    arms = [m.arm for m in mouths]
    segs = ik.build_junction_curb_segments(arms, kerb_radius, tail_length=1.0)
    caps = {m.uid: _cap_points(m) for m in mouths}
    out = []
    for seg in segs:
        # `build_junction_curb_segments` does not say WHICH pair each segment came from, and the
        # answer is needed to know whose footway width to build it with. Its two endpoints ARE two
        # arms' cap corners, so matching them is exact rather than a guess.
        a = min(mouths, key=lambda m: _len2(_sub2(caps[m.uid][1], seg[0][:2])))
        b = min(mouths, key=lambda m: _len2(_sub2(caps[m.uid][0], seg[-1][:2])))
        # `_round_ring` is a closed-ring expander, but only the MIDDLE vertex of a 3-point corner
        # carries a radius and its two neighbours are the endpoints either way -- so it expands an
        # open corner correctly with no second implementation of an arc.
        flat = _round_ring(list(seg), segments)
        if len(flat) < 2:
            continue
        pts = [(x + cx, y + cy, _idw_z(mouths, (x + cx, y + cy))) for (x, y) in flat]
        n = max(1, len(pts) - 1)
        walk, kerb, wall = [], [], []
        for i in range(len(pts)):
            t = i / float(n)
            walk.append(a.walk_out + (b.walk_in - a.walk_out) * t)
            kerb.append(a.kerb_out + (b.kerb_in - a.kerb_out) * t)
            wall.append(a.wall_h + (b.wall_h - a.wall_h) * t)
        out.append(Corner(a.uid, b.uid, pts, walk, kerb, wall))
    return out


def solve_junction(net, uids, segments=8, ground_fn=None):
    """A clique -> its pad. `uids` is one component from `NetworkData.junction_cliques()`."""
    mouths = [build_mouth(net, u, ground_fn) for u in uids if u in net.points]
    if len(mouths) < 2:
        return None
    cx = sum(m.pos[0] for m in mouths) / len(mouths)
    cy = sum(m.pos[1] for m in mouths) / len(mouths)
    cz = sum(m.pos[2] for m in mouths) / len(mouths)
    centre = (cx, cy, cz)

    # The SMALLEST authored radius wins: `build_junction_boundary` takes one scalar, and a corner
    # is shared by two arms -- taking the larger would round a corner further than the arm that
    # asked for the tight one can actually give.
    kerb_radius = min(float(m.point.fillet_radius) for m in mouths)
    for m in mouths:
        # `tail_pos` pins this arm's cap at the AUTHORED mouth, local to the centroid, while
        # `angle_deg` independently still orients the cap and drives both neighbours' corners --
        # exactly the split `Arm.tail_center` documents. That is what makes "the member point IS
        # the stop line" literally true instead of approximately true.
        local = (m.pos[0] - cx, m.pos[1] - cy)
        m.arm = _PadArm(m.uid, m.bearing, m.half_in, m.half_out,
                        lane_width=m.lane_width, lanes=max(m.lanes_in, 1),
                        lanes_out=max(m.lanes_out, 1), traffic_side='LEFT',
                        tail_length=_len2(local), tail_pos=local,
                        traffic_light=bool(m.point.traffic_light))
    arms = [m.arm for m in mouths]
    ring = ik.build_junction_boundary(arms, kerb_radius, tail_length=1.0)
    ring, _limit = clamp_corners(ring, mouths, cx, cy, kerb_radius)
    flat = _round_ring(ring, segments)
    boundary = [(x + cx, y + cy, _idw_z(mouths, (x + cx, y + cy))) for (x, y) in flat]

    apex, fan, star_ok, star_worst = pad_triangles(boundary, mouths, (cx, cy))
    turns = build_turns(mouths)
    corners = junction_corners(mouths, kerb_radius, cx, cy, segments)
    return JunctionSolve(list(uids), centre, mouths, boundary, fan, turns, kerb_radius,
                         star_ok, star_worst, fan_apex=apex, corners=corners)


def build_turns(mouths, segments=9):
    """Every legal movement through the pad, as a sampled cubic plus its verdict.

    Legality is `lane_movements` and ONLY `lane_movements` -- the same rule set the `.lanekit`
    emitter uses, so "why is there no turn here" has one answer rather than two that disagree.
    Illegal movements are returned too, with `ok = False` and a reason: that IS the explainer, and
    an artist asking why a right turn vanished should not have to read the source."""
    out = []
    for mi in mouths:
        if mi.lanes_in <= 0:
            continue
        ins = mi.lane_points(True)
        d_in = mi.dir_in()
        for mo in mouths:
            if mo.lanes_out <= 0:
                continue
            outs = mo.lane_points(False)
            d_out = mo.out_dir
            for idx_in, p_in in ins:
                for idx_out, p_out in outs:
                    v = lm.movement_verdict(
                        d_in, d_out, idx_in, len(ins), len(outs),
                        same_arm=(mi.uid == mo.uid),
                        allow_cross=bool(mi.point.allow_cross),
                        allow_uturn=bool(mi.point.allow_uturn))
                    if not v.ok:
                        out.append({"from": mi.uid, "to": mo.uid, "lane_in": idx_in,
                                    "lane_out": idx_out, "ok": False, "reason": v.reason,
                                    "turn": v.turn, "points": []})
                        continue
                    if v.to_lane is not None and v.to_lane != idx_out:
                        continue          # this movement belongs to a different exit lane
                    pts, _a, _b = bezier_through(p_in, d_in, p_out, d_out, segments)
                    out.append({"from": mi.uid, "to": mo.uid, "lane_in": idx_in,
                                "lane_out": idx_out, "ok": True, "reason": "",
                                "turn": v.turn, "points": pts})
    return out


def solve_junctions(net, segments=8, ground_fn=None):
    """Every clique in the network -> `[JunctionSolve]`."""
    out = []
    for uids in net.junction_cliques():
        j = solve_junction(net, uids, segments, ground_fn)
        if j is not None:
            out.append(j)
    return out


# ------------------------------------------------------------------------------- ramp + gore
#
# 2.4 says the ramp/mainline join is EDGE ALIGNMENT, not a pad, and that stays true. What was
# missing is everything either side of it: the ramp's inboard edge was anchored on the wrong edge
# of the aux slot (so the ramp was a lane BEYOND the exit lane rather than its continuation), the
# ramp's own FACING was left wherever the chain put it (so its cross-section was cut at the ramp's
# angle while the mainline's was cut at the mainline's -- two edges that touch at one point and
# open instantly), and the wedge downstream of that point was nobody's geometry. Between them
# those three are the user-reported "the ramp lane enter edge is not aligned with the main lane
# point normal, and the mesh pad is not formed -- more like just stuck on the side".

#: How wide the gap between the mainline's edge and the ramp's edge may grow before the paved gore
#: ends and the physical nose begins. Real practice puts the painted nose at roughly 2-5 m; wider
#: than that and the two roads genuinely are separate carriageways with kerb between them.
GORE_NOSE_WIDTH = 4.0

#: Hard cap on how far downstream the gore is paved, whatever the divergence angle. A ramp that
#: leaves at half a degree would otherwise pave a 400 m splinter.
GORE_MAX_LENGTH = 90.0

#: Sampling step along the gore, in metres.
GORE_STEP = 2.0

#: How far the ramp's heading at its mouth may differ from the mainline's before the gate says so.
#: A parallel-type exit diverges at 2-5 degrees; past this the "edge alignment" the model promises
#: is true at exactly one point and false a metre later.
GORE_MAX_DIVERGE_DEG = 8.0

#: How many vertices the gore's nose cap carries. A short straight run, but the furniture BLENDS
#: across it -- a mainline that declares a barrier meeting a ramp that declares a footway must not
#: step -- and a two-vertex run has nothing to blend over.
GORE_NOSE_SEGMENTS = 5


def ramp_side_of(net, main_uid, ramp_uid):
    """The carriageway THIS ramp is on (`lane_profile.FWD` / `REV`), from where its mouth sits.

    `ramp_carriageway` asked of the ramp's own authored position. One station may hand a ramp to
    EACH carriageway -- an ordinary half-interchange: eastbound traffic leaves, westbound traffic
    joins -- and then `aux_block`'s "most slots, ties to FWD" reading answers FWD for both (8l).
    Everything per-ramp asks this instead."""
    p = net.points.get(ramp_uid)
    if p is None:
        return lp.FWD
    return ramp_carriageway(net, main_uid, p.pos)


def aux_allocation(net, main_uid, direction=None):
    """`{ramp_uid: [slot_id, ...]}` -- how one station's aux BLOCK is divided among its ramps.

    A DIVERGE IS ORDINARY, AND THE MODEL ALREADY HELD IT (8k). `aux_fwd = 2` handed to two
    one-lane ramps is a two-lane exit that splits, and two one-lane ramps merging into one
    two-lane slot is how a two-lane entrance is usually fed -- neither needs a new field, only the
    answer to "which of the block's slots is THIS ramp's". Everything before this asked for the
    block's gore line and got the same one for every ramp at the station: both mouths were placed
    on top of each other, both hand-off edges were written to the same lane, and the second ramp
    was reachable by no car.

    ORDER IS DERIVED FROM WHERE THE ARTIST PUT THE MOUTHS -- nearest the through lanes takes the
    innermost slot. It cannot be click order (that is 8i.2's mistake) and it must not be uid order
    (invisible, and it would reshuffle a network on a rename). Measuring the authored position is
    stable under `Align Ramp To Aux`, because aligning preserves the order it read.

    A ramp takes as many slots as it declares lanes, clamped to what is left; a ramp that finds
    nothing left gets an empty list, which `point_validate.check_aux_slots` reports as an
    over-subscribed block rather than silently overlapping two ramps."""
    m = net.resolved(main_uid)
    if m is None:
        return {}
    # PER CARRIAGEWAY (8l): a station's forward block and its reverse block are two different
    # pieces of pavement, and a ramp on one has no claim on the other. Called with no direction,
    # every carriageway is allocated -- which is what the gate and the exporter want.
    dirs = [direction] if direction is not None else sorted(
        {s.dir for s in pp.build_profile(m).slots if s.kind == lp.AUX})
    if len(dirs) > 1:
        out = {}
        for d in dirs:
            out.update(aux_allocation(net, main_uid, d))
        return out
    d = dirs[0] if dirs else None
    slots = pp.aux_slot_gores(pp.build_profile(m), d)
    ramps = [u for u in net.points[main_uid].targets(pm.LINK_AUX) if u in net.points
             and (d is None or ramp_side_of(net, main_uid, u) == d)]
    if not slots or not ramps:
        return {}
    ax = pm.station_axis(net, main_uid) or (1.0, 0.0)
    nx, ny = -ax[1], ax[0]
    outward = 1.0 if slots[0][1] >= 0.0 else -1.0

    def rank(uid):
        p = net.points[uid]
        s = ((p.pos[0] - m.pos[0]) * nx + (p.pos[1] - m.pos[1]) * ny) * outward
        return (round(s, 3), uid)

    out, k = {}, 0
    for uid in sorted(ramps, key=rank):
        r = net.resolved(uid)
        want = max(1, int(max(r.lanes_fwd, r.lanes_bwd)) if r is not None else 1)
        out[uid] = [sid for sid, _e, _w in slots[k:k + want]]
        k += want
    return out


def ramp_carriageway(net, main_uid, pos):
    """`lane_profile.FWD` / `REV` -- which carriageway a ramp mouth at `pos` sits off.

    WHICH CARRIAGEWAY IS A FACT ABOUT WHERE THE RAMP IS, not a thing to type (8j, and 8i.2's
    sibling: `Make Ramp` had already stopped asking which point is the mainline). It wrote
    `aux_fwd` unconditionally, so a ramp leaving the westbound side of a highway got its slot
    opened on the eastbound one -- geometry on the wrong side of the road, and a lane graph that
    fed it from traffic going the other way.

    The mainline's forward lanes lie on +s (this is a keep-left world; `point_profile.build_profile`
    lays them out that way and `aux_edge_offset` reads them back), so the sign of the mouth's
    lateral offset IS the answer. It needs no aux slot to exist yet, which is what lets `Make Ramp`
    ask BEFORE it opens one."""
    m = net.resolved(main_uid)
    if m is None:
        return lp.FWD
    ax = pm.station_axis(net, main_uid) or (1.0, 0.0)
    nx, ny = -ax[1], ax[0]
    s = (pos[0] - m.pos[0]) * nx + (pos[1] - m.pos[1]) * ny
    return lp.FWD if s >= 0.0 else lp.REV


def ramp_frame_sign(net, main_uid, ramp_uid):
    """+1 when the ramp's station frame runs WITH the mainline's, -1 when it runs against it.

    ONE OWNER for the fact that a ramp's Empty may face back down the road it joins (8j).
    Everything that reads the ramp's OWN left/right needs it: which of its two paved edges faces
    the through lanes, which side of its own station its band lies on, and which way to face the
    Empty. Three places derived it independently and all three assumed +1.

    TWO SIGNS, AND BOTH ARE FACTS THE MODEL ALREADY HOLDS -- neither is authored:

    * WHICH CARRIAGEWAY the aux slot is on. `aux_fwd` is the forward lanes and `aux_bwd` the
      reverse ones, and traffic through a reverse slot runs AGAINST the mainline's station axis.
      A two-lane exit off a westbound carriageway is an ordinary thing to author and its mouth
      faces west.
    * WHICH WAY THE RAMP'S OWN LANES RUN. Local +Y is the chain direction and `lanes_fwd`/
      `lanes_bwd` say which way traffic runs along it, so a ramp declared `lanes_bwd` -- an
      entrance whose mouth is its run's head, or an exit whose mouth is its tail -- carries a
      frame pointing back along its own traffic.

    Their product is the answer, which is why neither can be left out: a reverse-carriageway ramp
    declared `lanes_bwd` faces the SAME way as an ordinary forward one."""
    m = net.resolved(main_uid)
    r = net.resolved(ramp_uid)
    travel = -1.0 if ramp_side_of(net, main_uid, ramp_uid) != lp.FWD else 1.0
    lanes = -1.0 if (r is not None and r.lanes_fwd <= 0 < r.lanes_bwd) else 1.0
    return travel * lanes


def aux_gore_offset(net, main_uid, ramp_uid, prof_m=None):
    """The gore line for ONE ramp: the inner edge of the first slot `aux_allocation` gave it.

    Falls back to the block's own gore line (`point_profile.aux_edge_offset`) whenever the station
    hands its block to a single ramp, which is every network authored before this -- so the number
    is unchanged wherever it was ever right."""
    m = net.resolved(main_uid)
    if m is None:
        return None
    prof_m = prof_m if prof_m is not None else pp.build_profile(m)
    d = ramp_side_of(net, main_uid, ramp_uid)
    mine = aux_allocation(net, main_uid).get(ramp_uid)
    if not mine:
        return pp.aux_edge_offset(prof_m, d) or pp.aux_edge_offset(prof_m)
    first = mine[0]
    for sid, edge, _w in pp.aux_slot_gores(prof_m, d):
        if sid == first:
            return edge
    return pp.aux_edge_offset(prof_m, d)


def ramp_target(net, main_uid, ramp_uid):
    """`(pos, axis, side)` -- where the ramp's first point BELONGS, and which way it must face.

    ONE OWNER, shared by `Align Ramp To Aux`, `Make Ramp`, the sample network, the gate's residual
    report and the gore mesh. Every one of those used to work it out for itself, which is how the
    demo could ship an alignment the gate then measured differently.

    * `pos` is fully determined -- lateral AND longitudinal AND vertical. The ramp station sits on
      the mainline station's own cross-section line, so the two cuts are the SAME cut: that is
      what "the edges align" has to mean for a swept band, and translating only sideways (which is
      what the old code did) left the ramp's cut plane at its own angle.
    * `axis` is the MAINLINE's travel direction. The ramp leaves parallel and bends away at its
      NEXT point, which is how a parallel-type exit is built and the only way a gore opens
      gradually instead of at the divergence angle from vertex one.
    * `side` is +1 or -1: which way the ramp's band extends from the gore line. Derived, not
      authored, so a kerb-side and an offside exit need no separate path.

    Returns None when the mainline station declares no aux slot for the ramp to take."""
    m, ramp = net.resolved(main_uid), net.resolved(ramp_uid)
    if m is None or ramp is None:
        return None
    prof_m = pp.build_profile(m)
    gore = aux_gore_offset(net, main_uid, ramp_uid, prof_m)
    if gore is None:
        return None
    ax = pm.station_axis(net, main_uid) or (1.0, 0.0)
    nx, ny = -ax[1], ax[0]
    std = [lp.slot_offset(prof_m, j) for j, s in enumerate(prof_m.slots)
           if s.is_drivable() and s.kind != lp.AUX]
    ref = (sum(std) / len(std)) if std else 0.0
    side = 1.0 if gore >= ref else -1.0
    r_neg, r_pos = lp.paved_extents(pp.build_profile(ramp))
    # ...AND WHICH SIDE OF ITS OWN STATION THE BAND LIES ON. `paved_extents` is measured in the
    # RAMP's frame, and `ramp_frame_sign` is -1 exactly when that frame is reversed relative to
    # the mainline -- so the two extents swap roles. Reading them unsigned put a reverse-lane
    # ramp's band one full carriageway width outboard of the aux slot it is supposed to BE (8j).
    if ramp_frame_sign(net, main_uid, ramp_uid) < 0:
        r_neg, r_pos = r_pos, r_neg
    # The ramp's band must START at the gore line and grow away from the through lanes, so which
    # of its own two edges lands on the line is decided by `side` -- never by "whichever is
    # nearer", which is a coin flip once the point has been dragged.
    off = gore + (r_neg if side > 0 else -r_pos)
    return ((m.pos[0] + nx * off, m.pos[1] + ny * off, m.pos[2]), ax, side)


def ramp_facing(net, main_uid, ramp_uid):
    """Which way the ramp mouth's local +Y must point -- the mainline axis, SIGNED BY THE RAMP.

    `ramp_target`'s `axis` is the MAINLINE's travel direction and stays that. This is the frame
    the mouth's Empty has to carry, and the two are not the same vector whenever the ramp's lanes
    run against its chain.

    THE MODEL'S OWN RULE MAKES THIS FORCED (8j): local +Y is the CHAIN direction, and
    `lanes_fwd`/`lanes_bwd` say which way traffic runs along it -- so a ramp declared `lanes_bwd`
    is one whose chain runs against its traffic. Facing such a mouth at `+axis` unconditionally
    (which is what "face it down the mainline" was taken to mean) points the curve's tangent
    downstream while the rest of the chain lies upstream: the spline leaves the mouth the wrong
    way and loops back through half the district to reach its own second station. A two-lane
    entrance authored that way came out as a 600 m hairpin whose gore was a 38 m wall down the
    middle of the ramp -- with a green gate, because the residual and the angle were both measured
    against the same wrong vector.

    All four combinations reduce to one line, and it is the one-way declaration alone that decides
    it: traffic at the mouth runs along the mainline either way, so +Y agrees with the mainline
    when the ramp declares forward lanes and opposes it when it declares reverse ones."""
    got = ramp_target(net, main_uid, ramp_uid)
    if got is None:
        return None
    _pos, ax, _side = got
    if ramp_frame_sign(net, main_uid, ramp_uid) < 0:
        return (-ax[0], -ax[1])
    return ax


def ramp_residual(net, main_uid, ramp_uid):
    """`(distance_m, angle_deg)` -- how far the ramp's mouth is from where it belongs, and how far
    its heading is from the mainline's. Both reported rather than hidden; hiding them is how a
    ramp ends up 2 m off the carriageway it is supposed to peel from."""
    got = ramp_target(net, main_uid, ramp_uid)
    if got is None:
        return None
    want, ax, _side = got
    p = net.points.get(ramp_uid)
    if p is None:
        return None
    d = math.hypot(p.pos[0] - want[0], p.pos[1] - want[1])
    d = math.hypot(d, p.pos[2] - want[2])
    rx = pm.station_axis(net, ramp_uid)
    if rx is None:
        return d, 0.0
    # Against the SIGNED facing, not the mainline axis: a reverse-lane ramp's frame legitimately
    # opposes the mainline, and measuring it against `ax` reports every one of them as 180 deg out.
    want = ramp_facing(net, main_uid, ramp_uid) or ax
    dot = max(-1.0, min(1.0, rx[0] * want[0] + rx[1] * want[1]))
    return d, math.degrees(math.acos(dot))


def ramp_divergence(net, main_uid, ramp_uid):
    """`(outboard_m, along_m)` for the ramp's NEXT station -- WHICH WAY the ramp actually bends.

    THE FACT NOBODY HAD AN EYE ON (8j). `ramp_residual` measures the mouth: where it stands and
    which way it faces. Both are set by `Align Ramp To Aux`, which faces the mouth down the
    mainline -- so a ramp that leaves correctly and then swings back ACROSS the carriageway it is
    leaving passes every ramp check there is. Its band overlaps the mainline's for its whole
    length, the two edges never part, `solve_gore` finds no wedge and returns None, and the artist
    is told nothing at all: no gore, no nose, no error. The sample network's own exit ramp had
    exactly this shape.

    `outboard_m` is the ramp's next station measured along the mainline's OUTBOARD normal at the
    mouth -- the direction `ramp_target`'s `side` points, which is where the aux slot is. Positive
    is away from the through lanes, which is the only way a ramp may leave. Negative means it is
    driving back through them. `along_m` is the same displacement projected on the mainline axis,
    reported so the finding can say whether the ramp is diverging at all.

    Returns None when there is no next station to measure (a one-point ramp) or no aux slot."""
    got = ramp_target(net, main_uid, ramp_uid)
    if got is None:
        return None
    _want, ax, side = got
    run = pm.run_of(net, ramp_uid)
    if ramp_uid not in run or len(run) < 2:
        return None
    i = run.index(ramp_uid)
    nxt = run[i + 1] if pm.ramp_mouth_at_chain_start(net, ramp_uid) else run[i - 1]
    a, b = net.points.get(ramp_uid), net.points.get(nxt)
    if a is None or b is None:
        return None
    dx, dy = b.pos[0] - a.pos[0], b.pos[1] - a.pos[1]
    nx, ny = -ax[1] * side, ax[0] * side
    return (dx * nx + dy * ny, dx * ax[0] + dy * ax[1])


class GoreSolve(object):
    """The paved wedge between a mainline and the ramp peeling off it.

    Not a pad and not a nose -- a strip. Its two boundaries are the two roads' OWN paved edges,
    read off the same `RoadSolve.edges_*` the asphalt is swept from, so the gore cannot drift away
    from either road it closes. It ends where the gap reaches `GORE_NOSE_WIDTH`, which is where a
    real gore's paint ends and its physical nose begins."""

    __slots__ = ("main_uid", "ramp_uid", "main_edge", "ramp_edge", "tris", "poly", "length",
                 "nose_gap", "nose", "nose_sgn", "ped_access")

    def __init__(self, main_uid, ramp_uid, main_edge, ramp_edge, tris, poly, length, nose_gap,
                 nose=None, nose_sgn=1.0, ped_access=False):
        self.main_uid, self.ramp_uid = main_uid, ramp_uid
        #: The two boundaries, from the gore point downstream, same length, paired by arclength.
        self.main_edge, self.ramp_edge = main_edge, ramp_edge
        self.tris = tris
        #: Closed XY ring -- what `point_edges` treats as a band so kerbs open across it.
        self.poly = poly
        self.length, self.nose_gap = length, nose_gap
        #: The gore's OWN edge run -- a `Corner` like a junction's, closing the open V at the wide
        #: end. `None` when neither flank declares any furniture to carry across it.
        self.nose = nose
        #: Which side of the cap the furniture stands on: +1 = the cap's left. Derived from where
        #: the paint is, never authored.
        self.nose_sgn = float(nose_sgn)
        #: May a pedestrian stand on this gore? Both flanks must say yes -- an island between an
        #: expressway and its ramp is not a refuge, and its proxy must not bake as walkable.
        self.ped_access = bool(ped_access)

    def __repr__(self):
        return "GoreSolve(%s -> %s, %.1f m, nose %.2f m)" % (
            self.main_uid[:6], self.ramp_uid[:6], self.length, self.nose_gap)


def _edge_walk(edge, samples, start, forward, step, max_len):
    """Resample one of a run's paved edges from index `start`, in arclength steps."""
    n = len(edge)
    if n < 2 or not (0 <= start < n):
        return []
    s0 = samples[start].s
    out, want, i = [edge[start]], step, start
    while 0 <= i < n:
        j = i + (1 if forward else -1)
        if not (0 <= j < n):
            break
        travelled = abs(samples[j].s - s0)
        while want <= travelled and want <= max_len:
            t = 1.0 if travelled <= 1e-9 else (
                (want - abs(samples[i].s - s0)) /
                max(1e-9, travelled - abs(samples[i].s - s0)))
            t = max(0.0, min(1.0, t))
            out.append(tuple(edge[i][k] + (edge[j][k] - edge[i][k]) * t for k in range(3)))
            want += step
        if want > max_len:
            break
        i = j
    return out


def _nearest_index(samples, pos):
    best, bi = None, 0
    for i, sm in enumerate(samples):
        d = (sm.pos[0] - pos[0]) ** 2 + (sm.pos[1] - pos[1]) ** 2
        if best is None or d < best:
            best, bi = d, i
    return bi


def _project_signed(edge, p, side, sense=1.0):
    """`(foot, gap)` -- the point of polyline `edge` nearest `p`, and how far OUTBOARD `p` stands.

    PAIRED BY PROJECTION, NEVER BY INDEX (8k). `solve_gore` walks both boundaries in equal
    arclength steps and used to compare `a[i]` against `b[i]`, which silently assumes the two
    advance together. A ramp faced down its mainline does; two SIBLING ramps peeling off one
    station do not -- the outer one is longer, so by 90 m its samples lag its neighbour's by six
    metres of arclength, and the perpendicular offset was then measured against a point that is
    not opposite at all. Two ramps with a real 5 m hole between them read as a gap of zero and no
    gore was paved between them, on a viaduct, over the drop.

    Projecting also gives the strip a proper ladder: `foot` is the point actually opposite `p`, so
    the triangles are not skewed by the same drift."""
    best = None
    for i in range(len(edge) - 1):
        ax, ay = edge[i][0], edge[i][1]
        dx, dy = edge[i + 1][0] - ax, edge[i + 1][1] - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
        foot = tuple(edge[i][k] + (edge[i + 1][k] - edge[i][k]) * t for k in range(3))
        d2 = (p[0] - foot[0]) ** 2 + (p[1] - foot[1]) ** 2
        if best is None or d2 < best[0]:
            best = (d2, foot, _signed_gap(edge[i], edge[i + 1], p, side, sense))
    if best is None:
        return None, 0.0
    return best[1], best[2]


def _signed_gap(a, a_next, b, side, sense=1.0):
    """How far the ramp edge `b` stands OUTBOARD of the mainline edge running `a -> a_next`.

    Signed, and that sign is the whole trick. At the mouth the ramp's band lies ON the aux slot --
    the exit lane IS the outermost mainline lane, which is the point -- so the two edges start
    OVERLAPPING and a plain distance reads 3.5 m of "gap" where there is no hole at all. Paving
    that stretch lays a second surface on top of the mainline and z-fights. The hole begins where
    the ramp's inboard edge crosses out past the mainline's, which is exactly where this changes
    sign, and that crossing is the theoretical gore.

    OUTBOARD IS A FACT ABOUT THE ROAD, NOT ABOUT THE WALK (8j). `side` is measured against the
    mainline's TRAVEL direction -- it comes from `ramp_target`, which reads the aux slot off the
    station's own cross-section -- so the normal has to be taken on the travel tangent too.
    `a -> a_next` is the SAMPLING direction, and `solve_gore` walks the mainline upstream for an
    entrance; taking the normal off that reversed chord flipped the sign of every reading. Nothing
    reported it, because the flipped reading is a perfectly plausible number: the "gap" simply came
    back positive where the two bands overlap. So the upstream walk always looked like the one that
    parted, the mainline's samples were paired against the ramp's running the OTHER way in world
    space, and the cap that closes the wedge was laid across the ramp mouth instead of across the
    nose -- a 22 m wall standing in the exit lane of the sample network's own merge.

    `sense` is +1 when `a -> a_next` runs WITH the mainline's travel and -1 when it runs against
    it, and it is the only thing that makes the two readings in `solve_gore`'s direction search
    comparable."""
    tx, ty = a_next[0] - a[0], a_next[1] - a[1]
    m = math.hypot(tx, ty)
    if m < 1e-9:
        return 0.0
    tx, ty = (tx / m) * sense, (ty / m) * sense
    nx, ny = -ty * side, tx * side
    return (b[0] - a[0]) * nx + (b[1] - a[1]) * ny


def _lerp3(a, b, t):
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))


#: True when this point is at the START of its road's chain -- so a walk away from it runs with
#: increasing index. The ramp's mouth is its first point on an exit and its last on an entrance,
#: which is what decides both walks with no role table. ONE OWNER: `point_model`, because the
#: EXPORT needs the same answer to point the lane-graph edge the right way, and a second copy here
#: is how the geometry and the traffic end up disagreeing about which way a ramp runs.
_chain_direction = pm.ramp_mouth_at_chain_start


def _flank_edge_furniture(solve, pos, is_left):
    """`(walk_half_width, kerb_height, barrier_height)` one road offers to the gore beside it.

    Read off that road's OWN solved values and never re-derived. `solve_road` is the single owner
    of the barrier rule -- "a road nobody may walk on is fenced along its whole length, one they
    may walk on only where it is off the ground" -- and a second copy of it here is exactly how a
    gore ends up walled where its mainline is not."""
    v = solve.values[_nearest_index(solve.samples, pos)]
    if is_left:
        return v["rka_walk_hl"], v["rka_curb_hl"], v["rka_wall_h"]
    return v["rka_walk_hr"], v["rka_curb_hr"], v["rka_wall_h"]


def _gore_nose(main_uid, ramp_uid, main_pt, ramp_pt, main_solve, ramp_solve, side, centre,
               segments=GORE_NOSE_SEGMENTS, ramp_side=None, main_side=None):
    """`(Corner, sgn)` -- the gore's OWN edge run: the cap that closes the V at its wide end.

    THE HOLE THIS CLOSES. A gore is bare paint -- `point_edges.Band.carries_edge` is False for one
    deliberately -- so BOTH flanking walls open across it. Along the join that is exactly right: a
    wall there would stand in the exit lane. At the wide end it is exactly wrong: the two roads
    have parted, their own walls restart `nose_gap` metres apart, and between them was nobody's
    geometry -- an open V at the tip of every exit in the world, on a viaduct, over the drop.

    It is an ORDINARY `Corner` swept with the ORDINARY `edge_spec()`, for the same reason a
    junction corner is (8g): a gore with its own idea of what a kerb or a wall looks like is how
    the two drift apart.

    WHAT IT CARRIES IS THE RAMP'S, ALONG THE WHOLE CAP -- not a blend of the two flanks (8i, a
    user report). Blending was right only while both roads declared the same KIND of furniture. A
    ramp leaving an ordinary street is the common case and they never do: the ramp is fenced
    (`ped_access` off, `barrier_height` 1 m) and the street is kerbed and paved for pedestrians, so
    the cap came out a wall of falling height standing in a footway of growing width -- a shape
    neither road has anywhere else, wedged in the one place both of them end. The gore is the
    ramp's divergence, so the gore's nose is the ramp's: one uniform section, the ramp's own
    solved values, and the mainline's kerb and footway simply run on past it unbroken (they never
    stopped -- `point_edges.open_runs` only ever opened them ACROSS the paint).

    The mainline's values are the fallback for the one case the ramp cannot answer: a ramp that
    declares no furniture at all, where taking its zeroes would leave the V open again.

    `sgn` is which side the furniture stands on, and it is DERIVED: the cap's own left normal
    against the direction of the paint. The gore lies upstream of the cap, so its centroid says
    which way that is with no ramp-side/kerb-side case analysis."""
    # `main_side` is the flank's OWN side, which is not `side` when the inboard neighbour is a
    # sibling ramp with a reversed frame (8k).
    m_w, m_k, m_wall = _flank_edge_furniture(
        main_solve, main_pt, (side if main_side is None else main_side) > 0)
    # In the RAMP's own frame -- see `ramp_frame_sign`. Which of its two edges the gore lies
    # against decides which of its two kerb/footway/wall values the cap must carry.
    r_w, r_k, r_wall = _flank_edge_furniture(ramp_solve, ramp_pt,
                                             (side if ramp_side is None else ramp_side) < 0)
    if not any(abs(v) > 1e-6 for v in (r_w, r_k, r_wall)):
        r_w, r_k, r_wall = m_w, m_k, m_wall
    n = max(2, int(segments))
    pts, walk, kerb, wall = [], [], [], []
    for i in range(n):
        t = i / float(n - 1)
        pts.append(_lerp3(main_pt, ramp_pt, t))
        walk.append(r_w)
        kerb.append(r_k)
        wall.append(r_wall)
    dx, dy = ramp_pt[0] - main_pt[0], ramp_pt[1] - main_pt[1]
    to_paint = (centre[0] - main_pt[0], centre[1] - main_pt[1])
    sgn = 1.0 if (-dy * to_paint[0] + dx * to_paint[1]) > 0.0 else -1.0
    return Corner(main_uid, ramp_uid, pts, walk, kerb, wall), sgn


def solve_gore(net, main_uid, ramp_uid, main_solve, ramp_solve,
               nose=GORE_NOSE_WIDTH, max_len=GORE_MAX_LENGTH, step=GORE_STEP, inboard=None):
    """One aux pair -> its `GoreSolve`, or None when the two bands never actually part.

    The strip runs from the THEORETICAL GORE (where the ramp's inboard edge crosses out past its
    inboard neighbour's) to the NOSE (where the gap reaches `GORE_NOSE_WIDTH` and a real gore's
    paint gives way to kerb). Neither end is authored: both are measured off the two roads' own
    paved edges, so moving either point moves the gore with it.

    A GORE IS AGAINST THE NEIGHBOUR ON THE INBOARD SIDE, WHICH IS NOT ALWAYS THE MAINLINE (8k).
    When one station hands its aux block to two ramps, the innermost ramp's inboard neighbour is
    the mainline -- its through-lane edge is exactly where that ramp's band starts -- but the
    outer ramp's is the INNER RAMP. Measured against the mainline instead, the outer ramp's wedge
    is struck across the inner ramp's asphalt: a second surface laid on top of a road, which is
    the overlap `_signed_gap` exists to avoid, one participant further out. `inboard` is
    `(solve, edge_points, station_pos)` and `solve_gores` is the one place that chooses it."""
    got = ramp_target(net, main_uid, ramp_uid)
    if got is None or main_solve is None or ramp_solve is None:
        return None
    _want, _ax, side = got
    m = net.resolved(main_uid)
    if m is None:
        return None
    # WHICH edge of each road faces the gore is `side` and nothing else. `edges_left` is the +s
    # boundary, `edges_right` the -s one (see `solve_road`), on both roads. The mainline offers
    # its OUTER edge on the aux side; the ramp offers its INBOARD edge, the one still pointing at
    # the road it is leaving.
    # THE RAMP'S OWN LEFT IS NOT THE MAINLINE'S. `edges_left` is the +s boundary in each road's
    # OWN frame, and a reverse-lane ramp's frame is reversed (`ramp_frame_sign`) -- so the edge
    # still pointing at the road it is leaving is the other one. Taking `side` alone handed the
    # gore that ramp's OUTBOARD edge: the strip was measured across the whole ramp and came back
    # as a 38 m nose wall laid down the middle of it (8j).
    r_side = side * ramp_frame_sign(net, main_uid, ramp_uid)
    ramp_edge = ramp_solve.edges_right if r_side > 0 else ramp_solve.edges_left
    if inboard is None:
        main_side = side
        main_edge = main_solve.edges_left if side > 0 else main_solve.edges_right
        main_pos = m.pos
    else:
        main_solve, main_edge, main_pos, main_side = inboard
    mi = _nearest_index(main_solve.samples, main_pos)
    ri = _nearest_index(ramp_solve.samples, net.points[ramp_uid].pos)
    r_fwd = _chain_direction(net, ramp_uid)
    b = _edge_walk(ramp_edge, ramp_solve.samples, ri, r_fwd, step, max_len)
    # WHICH WAY along the mainline the gore opens is not a role lookup: an exit's gore lies
    # downstream and an entrance's upstream, so the answer is simply "the way the two bands part".
    # Walk both and keep the one whose signed gap actually goes positive.
    best = None
    for m_fwd in (True, False):
        a = _edge_walk(main_edge, main_solve.samples, mi, m_fwd, step, max_len)
        if len(a) < 2 or len(b) < 3:
            continue
        # `sense` keeps OUTBOARD pinned to the mainline's travel direction while the walk runs
        # either way -- see `_signed_gap`. Without it the upstream reading is the downstream one
        # with its sign flipped, so the upstream walk won this search unconditionally.
        sense = 1.0 if m_fwd else -1.0
        feet = [_project_signed(a, q, side, sense) for q in b]
        opened = max(g for _f, g in feet[1:])
        if best is None or opened > best[0]:
            best = (opened, a, len(b), sense, feet)
    if best is None or best[0] <= 0.05:
        return None                    # the bands never part: nothing to pave
    _opened, a, n, sense, feet = best
    # `a` is now indexed BY `b`: entry i is the point of the inboard boundary opposite `b[i]`.
    a = [f for f, _g in feet]
    gaps = [g for _f, g in feet]

    # ---- from the theoretical gore ...
    start = None
    for i in range(n - 1):
        g0, g1 = gaps[i], gaps[i + 1]
        if g0 <= 0.0 < g1:
            t = -g0 / max(1e-9, g1 - g0)
            start = (i, t)
            break
        if i == 0 and g0 > 0.0:
            # ALREADY PARTED AT THE MOUTH -- legitimate for a hand-nudged mouth, and the reason
            # the strip does not simply refuse to start. But only up to the nose width: past that
            # the two carriageways are not diverging, they are separate roads that happen to have
            # an AUX link, and there is no wedge between them to pave OR to cap. Paving it anyway
            # is what put a 22 m nose wall across a merge whose mouth had been dragged 24 m off
            # the gore line (8j) -- geometry that only ever appears where the gate is already
            # reporting `ramp_edge_residual`, and which blocked the ramp outright.
            if g0 >= nose:
                return None
            start = (0, 0.0)
            break
    if start is None:
        return None
    i0, t0 = start
    pa = [_lerp3(a[i0], a[i0 + 1], t0)]
    pb = [_lerp3(b[i0], b[i0 + 1], t0)]
    gap = 0.0
    i_end = i0
    # ---- ... to the nose
    for i in range(i0 + 1, n):
        pa.append(a[i])
        pb.append(b[i])
        i_end = i
        gap = math.hypot(a[i][0] - b[i][0], a[i][1] - b[i][1])
        if gap >= nose:
            break
    if len(pa) < 2:
        return None
    tris = []
    for i in range(len(pa) - 1):
        tris.append((pa[i], pb[i], pb[i + 1]))
        tris.append((pa[i], pb[i + 1], pa[i + 1]))
    poly = [(p[0], p[1]) for p in pa] + [(p[0], p[1]) for p in reversed(pb)]
    length = sum(math.hypot(pa[i + 1][0] - pa[i][0], pa[i + 1][1] - pa[i][1])
                 for i in range(len(pa) - 1))
    # ---- and the cap across the wide end.
    # AT THE NOSE, flush with the paint: the cap's two ends ARE the last pair of the gore strip,
    # so there is no seam between the wedge and the wall that closes it. That only works because
    # `point_edges.open_runs` CLIPS a run's end onto the band it stops against instead of snapping
    # it to the sample grid -- the two flanking walls therefore resume on this same nose line and
    # the three meet. Placing the cap a step downstream to chase an unclipped grid was the earlier
    # answer, and it left a visible gap between the gore mesh and the wall.
    cap_main, cap_ramp = pa[-1], pb[-1]
    ring = pa + pb
    centre = (sum(p[0] for p in ring) / float(len(ring)),
              sum(p[1] for p in ring) / float(len(ring)))
    nose_run, nose_sgn = _gore_nose(main_uid, ramp_uid, cap_main, cap_ramp,
                                    main_solve, ramp_solve, side, centre, ramp_side=r_side,
                                    main_side=main_side)
    ped = bool(getattr(main_solve.road, "ped_access", False)
               and getattr(ramp_solve.road, "ped_access", False))
    return GoreSolve(main_uid, ramp_uid, pa, pb, tris, poly, length, gap,
                     nose_run, nose_sgn, ped)


def inboard_neighbour(net, main_uid, ramp_uid, by_uid):
    """`(solve, edge_points, station_pos)` for whatever lies just INBOARD of this ramp's band, or
    None when that is the mainline itself.

    The one place the diverge order is turned into geometry (8k). `aux_allocation` already says
    which of the station's ramps holds which slots, innermost first; the ramp before this one in
    that order is the road whose paved edge this ramp's gore opens away from. Its OUTBOARD edge is
    the one facing us -- the opposite of the inboard edge `solve_gore` takes from the ramp itself,
    and picked with that same ramp's own `ramp_frame_sign`, because a sibling may be reversed
    while we are not."""
    # SAME CARRIAGEWAY ONLY (8l). A ramp leaving the forward side and one joining the reverse
    # side share a station and nothing else -- they are not beside each other anywhere, so
    # neither is the other's inboard neighbour.
    d = ramp_side_of(net, main_uid, ramp_uid)
    alloc = {u: v for u, v in aux_allocation(net, main_uid).items()
             if ramp_side_of(net, main_uid, u) == d}
    order = [u for u in sorted(alloc, key=lambda u: _alloc_rank(net, main_uid, alloc, u))]
    if ramp_uid not in order or order.index(ramp_uid) == 0:
        return None
    sib = order[order.index(ramp_uid) - 1]
    sib_solve, sib_pt = by_uid.get(sib), net.points.get(sib)
    if sib_solve is None or sib_pt is None:
        return None
    got = ramp_target(net, main_uid, sib)
    if got is None:
        return None
    _w, _ax, side = got
    s_side = side * ramp_frame_sign(net, main_uid, sib)
    # ...its OUTBOARD edge: the mirror of the inboard one `solve_gore` reads off a ramp.
    edge = sib_solve.edges_left if s_side > 0 else sib_solve.edges_right
    return (sib_solve, edge, sib_pt.pos, s_side)


def _alloc_rank(net, main_uid, alloc, uid):
    """Position of `uid` in the innermost-first slot order `aux_allocation` handed out."""
    slots = [sid for sid, _e, _w in pp.aux_slot_gores(
        pp.build_profile(net.resolved(main_uid)), ramp_side_of(net, main_uid, uid))]
    mine = alloc.get(uid) or []
    return slots.index(mine[0]) if mine and mine[0] in slots else len(slots)


def solve_gores(net, solves, **kw):
    """Every authored aux pair -> its gore. `solves` is the flat `[RoadSolve]` a build already has,
    so nothing is re-solved here."""
    by_uid = {}
    for s in solves:
        for u in s.uids:
            by_uid[u] = s
    out = []
    for main_uid, ramp_uid in net.aux_pairs():
        g = solve_gore(net, main_uid, ramp_uid, by_uid.get(main_uid), by_uid.get(ramp_uid),
                       inboard=inboard_neighbour(net, main_uid, ramp_uid, by_uid), **kw)
        if g is not None:
            out.append(g)
    return out


# ------------------------------------------------------------------------------- auto setback

#: How shallow two arms may meet before the corner-clearance term is written off as degenerate,
#: in degrees. Below this the two carriageways are effectively the same road and the corner runs
#: away to infinity; the pad is not the thing to fix there, the crossing is.
MIN_CORNER_ANGLE_DEG = 12.0


def corner_clearance(half_a, half_b, angle_deg):
    """The shortest setback at which two adjacent arms' CAPS stay in ring order, in metres.

    THE CONSTRAINT `recommended_tail_length` DOES NOT SEE. That search grows the tail until every
    turn MOVEMENT fits inside the pad -- a statement about the paths cars drive. It says nothing
    about the arms' own caps, and at a shallow crossing that is what folds the pad: two 29 m
    arterials meeting at 56 deg have their cap corners cross while the turns are perfectly happy,
    so the ring runs backwards between them (`pad_not_star_shaped`, 21.65 m past the centroid on
    Chuo x Rinkai -- and `Auto Setback`, the remedy the finding names, answered "moved 0").

    The ring walks the caps by bearing, so it stays in order exactly while the left corner of one
    arm sits at a smaller bearing than the right corner of the next:

        atan(half_a / d) + atan(half_b / d)  <=  angle between them

    Both terms shrink as `d` grows, so the smallest legal `d` is found by bisection. With equal
    half-widths it is the familiar `h / tan(theta/2)` -- 14.5 m at a square crossing, 27.2 m at 56
    degrees, which is the whole story of why a shallow junction has to be a big one.

    NOT "where the outer EDGES cross" (`(h_b + h_a cos t)/sin t`), which is the other natural
    reading and over-demands by half: the fillet is allowed to bulge past that point, and asking
    for it grows every square junction from 14.5 m to 20.5 for a fold that is not there.
    """
    t = math.radians(abs(((angle_deg + 180.0) % 360.0) - 180.0))
    if math.degrees(t) < MIN_CORNER_ANGLE_DEG or math.degrees(t) > 180.0 - MIN_CORNER_ANGLE_DEG:
        return 0.0
    lo, hi = 1e-3, 1000.0

    def fits(d):
        return math.atan(half_a / d) + math.atan(half_b / d) <= t

    if fits(lo):
        return lo
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if fits(mid):
            hi = mid
        else:
            lo = mid
    return hi


def corner_setback(mouths):
    """The longest corner clearance over every ADJACENT pair of a clique, in metres.

    Adjacent by BEARING, because a corner is between two arms with nothing between them -- the
    opposite arm of a through road is not a corner, it is the other end of the same street, and
    including it would demand a setback for a corner that does not exist.

    Each side takes `max(half_in, half_out)`: which of a road's two half-widths faces a given
    neighbour depends on the sense of the crossing, and over-reaching by the asymmetry of one road
    costs a metre of pad where getting it backwards costs a folded one.

    THE HALF-WIDTH IS THE DECK'S, NOT THE CARRIAGEWAY'S -- a footway is pavement, and a corner has
    to have room for the one that turns through it. `corner_clearance`'s own docstring quotes
    "14.5 m at a square crossing", which is a T2 road's DECK half (4 lanes + median + two 4 m
    footways = 29.0 m); the code passed `half_in`/`half_out`, which are `lane_profile
    .paved_extents` -- the carriageway alone, 10.5 m. The two were indistinguishable for as long as
    the footway width never reached the solve (`WORLD_REBUILD_PLAN.md` `W3`), and the moment it
    did, the pads were revealed to be sized for a 21 m road that is 29 m wide.

    What that costs is a corner with no room to turn in. Measured at Yamate x Hama, a 95/85
    crossing set back 14 m: at the 85 deg corner the two arms' cap points are **3.31 m** apart, so
    the kerb turns 95 deg in 3.3 m (a ~2 m radius against an authored `fillet_radius` of 6) and a
    4 m footway swept round that radius folds through itself -- the user report was "2 angles seem
    correct, but 2 other angles seem more square... the sidewalk is not align edge but kind of
    overlap each sidewalk". With the deck half-width the same corner asks 15.9 m and the caps come
    out 8.6 m apart, which is a corner.
    """
    if len(mouths) < 2:
        return 0.0
    ring = sorted(mouths, key=lambda m: m.bearing)
    worst = 0.0
    for a, b in zip(ring, ring[1:] + ring[:1]):
        ha = max(a.half_in + 2.0 * a.walk_in, a.half_out + 2.0 * a.walk_out)
        hb = max(b.half_in + 2.0 * b.walk_in, b.half_out + 2.0 * b.walk_out)
        worst = max(worst, corner_clearance(ha, hb, b.bearing - a.bearing))
    return worst


def solved_setback(mouths, kerb_radius, start=0.0, margin=2.0):
    """The stop-line distance a pad of these arms needs, in metres. ONE OWNER.

    Two constraints, and the answer is whichever binds: `recommended_tail_length` grows the tail
    until every turn MOVEMENT fits inside the pad, and `corner_setback` asks whether the arms' own
    CAPS stay in ring order (the one that binds at a shallow crossing -- see its docstring).

    IT IS A FUNCTION OF THE ARMS ALONE, which is the whole reason it is out here rather than
    inline in `auto_setback`. Anything that has to know where a mouth will END UP before the mouth
    exists -- `seed_district_roads`, which must prune the stations a pad will swallow -- can ask
    it with planned arms and get the same number `Auto Setback` will solve later. Two owners of
    "how far back is the stop line" is exactly how the island ended up with an ordinary station
    0.19 m from a mouth: the seeder placed every mouth at a provisional 14 m, pruned the stations
    inside that window, and `Auto Setback` then moved the mouth out to 17.9-35.5 m, over the top
    of a station that had survived the prune by being outside a window computed from the wrong
    number.

    `mouths` needs only the attributes `Mouth` and any planned stand-in share: `uid`, `bearing`,
    `half_in`/`half_out`, `walk_in`/`walk_out`, `lane_width`, `lanes_in`/`lanes_out`."""
    if not mouths:
        return 0.0
    # THE SEARCH NEEDS ARMS WHOSE CAPS CAN MOVE. `solve_junction`'s arms pin `tail_pos` at the
    # authored mouth -- which is the whole point of the model, and exactly wrong here: with
    # `tail_pos` set, `Arm.tail_center` ignores `tail_length` entirely, so growing it moves
    # nothing and `recommended_tail_length` returns its start value unchanged (a silent no-op,
    # measured on the 15 degree case). So the solve runs on a parallel set of arms that sit on
    # their own angle rays, and the answer is then written back onto the authored points.
    probe = [_PadArm(m.uid, m.bearing, m.half_in, m.half_out,
                     lane_width=m.lane_width, lanes=max(m.lanes_in, 1),
                     lanes_out=max(m.lanes_out, 1), traffic_side='LEFT')
             for m in mouths]
    tail = ik.recommended_tail_length(probe, kerb_radius, start=start, margin=margin)
    # ...AND THE ARMS' OWN ASPHALT HAS TO CLEAR. The search above is about the paths cars drive;
    # this is about where the carriageways themselves cross, which is the binding constraint at a
    # shallow crossing and the one that folds the pad. `margin` applies to both for the same
    # reason -- a corner that lands exactly on the cap is a corner that rounds to the wrong side.
    return max(tail, corner_setback(mouths) + margin)


def auto_setback(net, uids, margin=2.0):
    """Move a clique's UNLOCKED mouths out to a solved stop-line distance. Whole-clique,
    idempotent, non-destructive.

    2.2: "place four mouths by hand" is not what any shipping tool does -- RoadRunner solves the
    extents and exposes them as draggable, CityEngine generates from street width, Cities:Skylines
    solves outright. The universal pattern is SOLVE THEN OVERRIDE, and at 150-200 mouths, re-placed
    every time an approach's lane count changes, hand placement is not a workflow.

    Whole-clique because the maths does not survive being applied one mouth at a time:
    `recommended_tail_length` searches a tail length against `worst_movement_overshoot`, which
    measures every turn against the WHOLE pad polygon. Drag one mouth in isolation and the
    neighbouring fillet silently stops being tangent.

    Returns `[(uid, old_distance, new_distance)]` for the mouths it moved. A LOCKED mouth is never
    touched -- and `setback_locked` is an explicit toggle, never inferred from "the artist dragged
    it", or one accidental nudge would opt a mouth out of every future solve invisibly."""
    j = solve_junction(net, uids)
    if j is None:
        return []
    cx, cy = j.centre[0], j.centre[1]
    # The distance itself is `solved_setback`'s -- shared with the seeder, which has to know it
    # before these mouths exist. `start` is the widest mouth we already have, so the search only
    # ever grows a pad.
    start = max(_len2((m.pos[0] - cx, m.pos[1] - cy)) for m in j.mouths)
    tail = solved_setback(j.mouths, j.kerb_radius, start=start, margin=margin)
    moved = []
    for m in j.mouths:
        old = _len2((m.pos[0] - cx, m.pos[1] - cy))
        pt = net.points[m.uid]
        pt.setback_solved = float(tail)
        if pt.setback_locked:
            continue
        # A MOUTH SLIDES ALONG ITS OWN ROAD. IT DOES NOT MOVE SIDEWAYS OFF IT.
        #
        # `centre + out_dir * tail` puts every mouth on a ray from the pad CENTROID, and the
        # centroid is not on any of the roads: on a 5-arm pad it sits 12 m off the crossing, so a
        # mouth set back from there lands metres to the SIDE of its own centreline. The next
        # station along is still where the road is, so the road's own first span comes out at a
        # bogus angle -- measured on the island's port junction, port_road's approach left the pad
        # on a bearing 40 deg from its own alignment, ending up 3.5 deg from Chuo-dori's arm: two
        # carriageways leaving on top of each other, with the pavement drawn as a 90 m spike. It
        # was invisible for as long as every pad was a symmetric X, where the centroid IS the
        # crossing.
        #
        # So the centre is projected onto this mouth's own axis first. The mouth then sits `tail`
        # along its road from the point of that road nearest the pad centre, which is the same
        # place `seed_district_roads` measures from, and it can never leave the centreline. The
        # distance to the centroid comes out `hypot(tail, offset)` -- never less than `tail`, so
        # every corner still clears.
        foot = (cx - m.pos[0]) * m.out_dir[0] + (cy - m.pos[1]) * m.out_dir[1]
        ax, ay = m.pos[0] + m.out_dir[0] * foot, m.pos[1] + m.out_dir[1] * foot
        want = (ax + m.out_dir[0] * tail, ay + m.out_dir[1] * tail, m.pos[2])
        # THE NO-MOVE TEST IS ON THE POSITION, not on a distance. `old` is measured to the
        # centroid and `tail` along the arm, and once those stopped being the same number a mouth
        # that was already exactly right still reported as moved -- which is how a settled solve
        # reads as a drifting one.
        if _len2((want[0] - m.pos[0], want[1] - m.pos[1])) < 1e-4:
            continue
        pt.pos = want
        moved.append((m.uid, old, tail))
    return moved


# ------------------------------------------------------------------------------- self-test

def self_test():
    try:
        from . import point_validate as pv
    except ImportError:
        import point_validate as pv
    ok = 0
    net, mp, cp, rr = pv.build_testbed()

    # ---- a shallow crossing needs a BIG pad, and the turn search does not know it -------------
    # Two 29 m arterials meeting at 56 deg: `recommended_tail_length` is happy at 18.2 m because
    # every turn movement fits, and the ring then runs backwards between the caps
    # (`pad_not_star_shaped`, 21.65 m past the centroid on the island's Chuo x Rinkai). The cap
    # order is the constraint that binds, and at equal half-widths it is `h / tan(theta/2)`.
    for ang, want in ((56.2, 27.16), (90.0, 14.5), (120.0, 8.37)):
        got = corner_clearance(14.5, 14.5, ang)
        assert abs(got - want) < 0.02, (ang, got, want)
    # a corner belongs to BOTH arms, so it cannot depend on which one is asked first
    assert abs(corner_clearance(14.5, 8.0, 56.2) - corner_clearance(8.0, 14.5, 56.2)) < 1e-9
    # ...and two arms that are effectively the same road have no corner to clear
    assert corner_clearance(14.5, 14.5, 4.0) == 0.0 and corner_clearance(14.5, 14.5, 178.0) == 0.0
    ok += 1

    # ---- runs: the chain IS broken at the junction gap ---------------------------------------
    runs = road_runs(net, net.roads["road_main"])
    assert len(runs) == 2, runs
    assert len(runs[0]) == 3 and len(runs[1]) == 3, [len(r) for r in runs]
    ok += 1

    # ---- the carrier ------------------------------------------------------------------------
    sol = solve_road(net, net.roads["road_main"], runs[0])
    assert sol is not None and len(sol) > 10
    for name in ATTR_NAMES:
        assert name in sol.values[0], name
    # 2 lanes each way at 3.5 plus a 1.0 median = 15.0 paved, symmetric about the divide.
    assert abs(sol.values[0]["rka_halfw"] - 7.5) < 1e-6, sol.values[0]["rka_halfw"]
    assert abs(sol.values[0]["rka_shift"]) < 1e-6
    # The footways are outboard of the kerb line and NOT part of the carriageway.
    assert abs(sol.values[0]["rka_curb_ol"] - 7.5) < 1e-6
    assert abs(sol.values[0]["rka_walk_hl"] - 1.5) < 1e-6
    assert abs(sol.values[0]["rka_deck_w"] - 10.5) < 1e-6, sol.values[0]["rka_deck_w"]
    ok += 1

    # ---- the band edges are the numbers the asphalt is swept from ----------------------------
    i = len(sol.samples) // 2
    w = _len2(_sub2(sol.edges_left[i], sol.edges_right[i]))
    assert abs(w - 2 * sol.values[i]["rka_halfw"]) < 1e-6, w
    ok += 1

    # ---- the understructure derives from ONE number, with no other edit ----------------------
    heights = {}
    for z in (0.0, 2.0, 12.0):
        for u in runs[0]:
            p = net.points[u]
            p.pos = (p.pos[0], p.pos[1], z)
        s = solve_road(net, net.roads["road_main"], runs[0])
        heights[z] = (s.values[0]["rka_support"], s.values[0]["rka_pillar_param"],
                      s.values[0]["rka_fill_w"])
    assert heights[0.0][0] == SUPPORT_CODE[rs.SUPPORT_NONE], heights
    assert heights[2.0][0] == SUPPORT_CODE[rs.SUPPORT_FILL], heights
    assert heights[12.0][0] == SUPPORT_CODE[rs.SUPPORT_PIER], heights
    assert heights[12.0][1] == 1.0 and heights[0.0][1] == 0.0, heights
    # FILL is a battered trapezoid, not a prism: the toe is wider than the deck.
    assert heights[2.0][2] > 10.5 + 1.0, heights[2.0]
    assert abs(heights[2.0][2] - (10.5 + 2.0 * rs.FILL_SLOPE)) < 1e-6, heights[2.0]
    for u in runs[0]:
        p = net.points[u]
        p.pos = (p.pos[0], p.pos[1], 0.0)
    ok += 1

    # ---- the pad ----------------------------------------------------------------------------
    cliques = net.junction_cliques()
    assert len(cliques) == 1, cliques
    j = solve_junction(net, cliques[0])
    assert len(j.mouths) == 4, j.mouths
    assert j.star_ok, j.star_worst
    assert len(j.boundary) >= 8
    # Every mouth is COVERED: its own cross-bar endpoints lie on or inside the ring.
    poly = [(p[0], p[1]) for p in j.boundary]
    for m in j.mouths:
        for side in (m.half_out, -m.half_in):
            p = (m.pos[0] + m.normal[0] * side, m.pos[1] + m.normal[1] * side)
            assert ik._point_outside_polygon_dist(p, poly) < 0.05, (m.uid, side)
    ok += 1

    # ---- the pad follows the grade -----------------------------------------------------------
    for u, dz in zip(cliques[0], (0.0, 0.0, 0.0, 6.0)):
        net.points[u].pos = tuple(net.points[u].pos[:2]) + (dz,)
    jg = solve_junction(net, cliques[0])
    zs = [p[2] for p in jg.boundary]
    assert max(zs) - min(zs) > 1.0, (min(zs), max(zs))
    for m in jg.mouths:
        assert abs(_idw_z(jg.mouths, (m.pos[0], m.pos[1])) - m.pos[2]) < 1e-6
    for u in cliques[0]:
        net.points[u].pos = tuple(net.points[u].pos[:2]) + (0.0,)
    ok += 1

    # ---- movements: straight-ahead EXISTS (same_arm is the same MOUTH, not the same road) ----
    j = solve_junction(net, cliques[0])
    legal = [t for t in j.turns if t["ok"]]
    kinds = sorted({t["turn"] for t in legal})
    assert lm.TURN_S in kinds and lm.TURN_L in kinds and lm.TURN_R in kinds, kinds
    assert lm.TURN_U not in kinds, kinds
    for m in j.mouths:
        assert any(t["from"] == m.uid for t in legal), ("no movement leaves", m.uid)
        assert any(t["to"] == m.uid for t in legal), ("no movement reaches", m.uid)
    # Every connector actually bridges the two lanes it claims to.
    for t in legal:
        assert len(t["points"]) >= 4
    ok += 1

    # ---- the star-shaped test really fails when a mouth is dragged inside its neighbour ------
    square = [(10.0, 0.0), (0.0, 10.0), (-10.0, 0.0), (0.0, -10.0)]
    assert is_star_shaped(square, (0.0, 0.0))[0]
    # A mouth dragged PAST the centroid -- the ring stays simple, but the fan folds over.
    dented = [(10.0, 0.0), (0.5, -0.5), (0.0, 10.0), (-10.0, 0.0), (0.0, -10.0)]
    ok_flag, worst = is_star_shaped(dented, (0.0, 0.0))
    assert not ok_flag and 0.4 < worst < 0.7, (ok_flag, worst)
    ok += 1

    # ---- auto setback is idempotent and respects the lock -------------------------------------
    lock_uid = cliques[0][0]
    net.points[lock_uid].setback_locked = True
    before = dict((u, net.points[u].pos) for u in cliques[0])
    moved = auto_setback(net, cliques[0])
    assert all(u != lock_uid for u, _o, _n in moved), moved
    assert net.points[lock_uid].pos == before[lock_uid]
    again = auto_setback(net, cliques[0])
    assert not again, again
    ok += 1

    # ---- a ring road is one run and wraps --------------------------------------------------
    ring = pm.NetworkData()
    r = ring.add_road(pm.RoadData("ring", pm.PointData(lanes_fwd=1, lanes_bwd=0,
                                                       lane_width=3.5), is_loop=True))
    n = 8
    pts = [ring.add_station(r, (60.0 * math.cos(2 * math.pi * i / n),
                                60.0 * math.sin(2 * math.pi * i / n), 0.0)) for i in range(n)]
    for a, b in zip(pts, pts[1:]):
        ring.link(a.uid, b.uid, pm.LINK_SEGMENT)
    ring.link(pts[-1].uid, pts[0].uid, pm.LINK_SEGMENT)
    rs_ = solve_road(ring, r)
    assert rs_.is_loop
    assert rs_.length() > 2 * math.pi * 55, rs_.length()
    # One-way: the ribbon is single width, not double (redesign defect 1).
    assert abs(rs_.values[0]["rka_halfw"] - 1.75) < 1e-6, rs_.values[0]["rka_halfw"]
    ok += 1

    # ---- the fan apex moves rather than the build refusing -----------------------------------
    # A ring that no longer sees its own centroid: the apex search must find a point that does,
    # and when it cannot, ear clipping must still return a watertight cap. Refusing was the old
    # behaviour and it turned a 2 cm hand-drag into a failed build with a dead remedy.
    ring2 = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (11.0, 9.0), (0.0, 20.0)]
    ok_star, worst = is_star_shaped(ring2, (10.2, 10.2))
    assert not ok_star and worst > 0.0, (ok_star, worst)
    origin, found, _w = fan_origin(ring2, (10.2, 10.2))
    assert found and is_star_shaped(ring2, origin)[0], (origin, found)
    tris = ear_clip([(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (11.0, 9.0), (0.0, 20.0)])
    assert len(tris) == 3, tris
    ok += 1

    # ---- the ramp: one owner of where the mouth goes, and the gore between the bands ----------
    net2, mp2, _cp2, rr2 = pv.build_testbed()
    main_uid, ramp_uid = net2.aux_pairs()[0]
    want, ax, side = ramp_target(net2, main_uid, ramp_uid)
    # The gore line is the aux slot's THROUGH-LANE edge (median 0.5 + 2 x 3.5), not its outboard
    # one -- so the aux slot IS the exit lane and the ramp continues it.
    assert abs(want[1] - 7.5) < 1e-6, want
    assert abs(ax[0] - 1.0) < 1e-6 and side > 0, (ax, side)
    resid, angle = ramp_residual(net2, main_uid, ramp_uid)
    assert resid < 1e-6 and angle < 1e-6, (resid, angle)
    solves2 = [s for road in net2.roads.values() for uids in road_runs(net2, road)
               for s in [solve_road(net2, road, uids)] if s is not None]
    gores = solve_gores(net2, solves2)
    assert len(gores) == 1, gores
    g = gores[0]
    assert g.tris and g.length > 1.0, g
    # It starts where the bands PART, not at the mouth -- paving the overlap would lay a second
    # surface on top of the mainline -- and stops at the nose.
    head = math.hypot(g.main_edge[0][0] - g.ramp_edge[0][0],
                      g.main_edge[0][1] - g.ramp_edge[0][1])
    assert head < 1.0, "the gore opens at the theoretical gore, %.2f m apart" % head
    assert g.nose_gap >= GORE_NOSE_WIDTH - 1e-6 or g.length >= GORE_MAX_LENGTH - 1e-6, g

    # ---- ...and the CAP that closes the open V at its wide end --------------------------------
    # Both flanking walls open across a gore (`point_edges.Band.carries_edge` is False for one),
    # which is right along the join and wrong at the tip: without this the two walls restarted
    # `nose_gap` metres apart with nothing between them, at every exit in the world.
    import point_edges as _pe
    assert g.nose is not None and len(g.nose.points) == GORE_NOSE_SEGMENTS, g.nose
    main_s = next(s for s in solves2 if main_uid in s.uids)
    ramp_s = next(s for s in solves2 if ramp_uid in s.uids)
    # It carries what THE RAMP declares, read off the ramp's own solved values -- one owner, which
    # is `solve_road`'s barrier rule -- UNIFORMLY along the cap (8i). The testbed's mainline is a
    # walkable arterial at grade and its ramp is fenced: exactly the mixed pair a blend turned into
    # a wall of falling height standing in a widening footway. The cap is the ramp's section, and
    # the mainline's own kerb and footway run on past it untouched.
    m_wall, r_wall = _flank_edge_furniture(next(
        s for s in solves2 if main_uid in s.uids), g.nose.points[0], side > 0)[2], \
        _flank_edge_furniture(next(
            s for s in solves2 if ramp_uid in s.uids), g.nose.points[-1], side < 0)[2]
    assert r_wall > 0.0 and m_wall != r_wall, (m_wall, r_wall)
    assert all(abs(w - r_wall) < 1e-6 for w in g.nose.wall), g.nose.wall
    r_walk, r_kerb = _flank_edge_furniture(ramp_s, g.nose.points[-1], side < 0)[:2]
    assert all(abs(w - r_walk) < 1e-6 for w in g.nose.walk), g.nose.walk
    assert all(abs(k - r_kerb) < 1e-6 for k in g.nose.kerb), g.nose.kerb
    assert not g.ped_access, "an island between an expressway and its ramp is not a refuge"
    # THE CAP IS FLUSH WITH THE PAINT: its two ends ARE the gore strip's last pair, so there is no
    # seam between the wedge and the wall closing it.
    assert g.nose.points[0] == tuple(g.main_edge[-1]), (g.nose.points[0], g.main_edge[-1])
    assert g.nose.points[-1] == tuple(g.ramp_edge[-1]), (g.nose.points[-1], g.ramp_edge[-1])
    # ...and BOTH flanking walls resume ON that same nose line rather than a sample either side of
    # it, because `open_runs` clips a run's end onto the band it stops against. Three pieces of
    # wall meeting at a point is only possible once none of them is snapped to a 4 m grid.
    bands2 = _pe.collect_bands(solves2, solve_junctions(net2), [g])
    for solve, key, edge, cap in ((main_s, "left" if side > 0 else "right",
                                   main_s.edges_left if side > 0 else main_s.edges_right,
                                   g.nose.points[0]),
                                  (ramp_s, "right" if side > 0 else "left",
                                   ramp_s.edges_right if side > 0 else ramp_s.edges_left,
                                   g.nose.points[-1])):
        ends = [p for r in _pe.kerb_runs(solve, bands2)[key]
                for p in (_pe.sub_polyline(edge, r)[0], _pe.sub_polyline(edge, r)[-1])]
        near = min(math.dist(p[:2], cap[:2]) for p in ends)
        assert near <= 0.5, ("%s's wall must meet the cap, %.2f m off" % (solve.road.name, near))
    # A pair of roads that declares NO furniture gets no cap -- the empty case, not a special one.
    for r in (main_s.road, ramp_s.road):
        r.barrier_height = 0.0
        for pt in [r.base] + [net2.points[u] for u in r.points if u in net2.points]:
            pt.left_kerb_height = pt.right_kerb_height = 0.0
            pt.left_walk_width = pt.right_walk_width = 0.0
    bare = [s for road in net2.roads.values() for uids in road_runs(net2, road)
            for s in [solve_road(net2, road, uids)] if s is not None]
    gb = solve_gores(net2, bare)[0]
    assert not any(abs(v) > 1e-6 for v in
                   list(gb.nose.wall) + list(gb.nose.kerb) + list(gb.nose.walk)), gb.nose.wall
    # Move the mouth off the line and both numbers report it -- the gate reads these, so the
    # operator, the gate and the geometry cannot disagree about what "aligned" means.
    net2.points[ramp_uid].pos = (480.0, 12.0, 0.0)
    resid2, _a2 = ramp_residual(net2, main_uid, ramp_uid)
    assert abs(resid2 - 4.5) < 1e-6, resid2
    ok += 1

    # ---- a two-lane exit: the ramp continues the WHOLE aux block ------------------------------
    net3, mp3, _cp3, rr3 = pv.build_testbed()
    main3, ramp3 = net3.aux_pairs()[0]
    net3.points[main3].aux_fwd = 2
    net3.points[ramp3].lanes_fwd = 2
    net3.points[ramp3].profile_mode = pm.OVERRIDE
    want2, ax2, side2 = ramp_target(net3, main3, ramp3)
    # Unchanged from the one-lane case: the gore line is the block's INNER edge, so widening the
    # exit widens it outward and never moves the join.
    assert abs(want2[1] - 7.5) < 1e-6, want2
    assert side2 > 0 and abs(ax2[0] - 1.0) < 1e-6
    net3.points[ramp3].pos = want2
    resid3, _a3 = ramp_residual(net3, main3, ramp3)
    assert resid3 < 1e-6, resid3
    ok += 1

    # ---- the pad grows its own corner kerb + footway ------------------------------------------
    net4, _mp4, _cp4, _rr4 = pv.build_testbed()
    j4 = solve_junctions(net4)[0]
    # Four arms, none of them a through-pair at 90 degrees, so four real corners.
    assert len(j4.corners) == 4, j4.corners
    poly4 = [(p[0], p[1]) for p in j4.boundary]
    for c in j4.corners:
        assert len(c.points) >= 3 and len(c.walk) == len(c.points) == len(c.kerb) == len(c.wall)
        # A corner rides the OUTSIDE of the pad: no vertex of it may be inside the ring.
        for p in c.points:
            assert ik._point_outside_polygon_dist((p[0], p[1]), poly4) >= -0.05, (c, p)
        # ...and its ends are two arms' own cap corners, which is what makes the street's footway
        # meet it instead of stopping short.
        assert c.a_uid in net4.points and c.b_uid in net4.points
        assert max(c.walk) > 0.0, "the testbed's arterial authors a 3 m footway"

    # ---- a ROTATED mouth: the corner must leave along that mouth's own kerb line ---------------
    # `intersection_kit.curb_edges` used to anchor each arm's kerb-edge ray on the ORIGIN, which
    # passes through the cap only because a plain arm's tail centre is a multiple of its direction.
    # A rotated mouth's cap is not, so the corner vertex landed on a line the kerb never touches
    # and the corner left the cap ~50 degrees out -- the footway then met the street in a notch.
    cross_mouth = _cp4[1].uid
    net4.points[cross_mouth].tangent_mode = pm.MANUAL
    net4.points[cross_mouth].tangent = (math.sin(math.radians(20.0)),
                                        math.cos(math.radians(20.0)), 0.0)
    j5 = solve_junctions(net4)[0]
    m5 = next(m for m in j5.mouths if m.uid == cross_mouth)
    for c in j5.corners:
        if c.b_uid == cross_mouth:
            d = _norm2(_sub2(c.points[-1], c.points[-2]))
        elif c.a_uid == cross_mouth:
            d = _norm2(_sub2(c.points[0], c.points[1]))
        else:
            continue
        # The corner's end runs ALONG the mouth's own axis -- the same axis the carriageway and
        # `side_normals` use, so the two footways meet flush instead of at an angle.
        along = abs(d[0] * m5.out_dir[0] + d[1] * m5.out_dir[1])
        assert along > 0.999, (c.a_uid[:6], c.b_uid[:6], along, m5.bearing)
    ok += 1

    # ---- an ENTRANCE gore opens UPSTREAM, and its cap is a cap -------------------------------
    # 8j, a user report. `_signed_gap` took its normal off the chord it was walking, so the
    # upstream reading was the downstream one with its sign flipped: the "which way do the bands
    # part" search picked upstream unconditionally, the mainline's samples were paired against the
    # ramp's running the OTHER way in world space, and the nose cap came out 22 m long, laid
    # across the merge instead of across the gore. The gate was green throughout -- residual zero,
    # angle zero -- because nothing measured the mesh.
    net6, mp6, _cp6, rr6 = pv.build_testbed()
    m6, r6 = net6.aux_pairs()[0]
    ramp6 = net6.roads["ramp_e"]
    # Turn the testbed's EXIT into an ENTRANCE by the two facts that decide it (8i.3): the mouth
    # becomes the run's TAIL, and the ramp is laid UPSTREAM of it so its lanes run into the
    # mainline rather than out of it. Nothing is declared -- there is no entry/exit flag to set.
    ramp6.points.reverse()
    want6, ax6, _s6 = ramp_target(net6, m6, r6)
    net6.points[r6].pos = want6
    net6.points[r6].tangent_mode, net6.points[r6].tangent = pm.MANUAL, (ax6[0], ax6[1], 0.0)
    net6.points[rr6[1].uid].pos = (want6[0] - 90.0, want6[1] + 25.0, 0.0)
    net6.points[rr6[2].uid].pos = (want6[0] - 200.0, want6[1] + 70.0, 0.0)
    assert pm.ramp_is_entrance(net6, r6), "the mouth is the run's tail: cars arrive at it"
    solves6 = [s for road in net6.roads.values() for uids in road_runs(net6, road)
               for s in [solve_road(net6, road, uids)] if s is not None]
    g6 = solve_gores(net6, solves6)[0]
    cap6 = math.hypot(g6.nose.points[-1][0] - g6.nose.points[0][0],
                      g6.nose.points[-1][1] - g6.nose.points[0][1])
    assert cap6 <= GORE_NOSE_WIDTH + 2.0, "a %.1f m cap is a wall across the ramp" % cap6
    assert g6.length > 1.0, g6
    # It lies UPSTREAM of the mouth, which is what an entrance's gore is -- the wedge narrows the
    # way the traffic runs.
    assert g6.main_edge[0][0] < net6.points[m6].pos[0], (g6.main_edge[0], net6.points[m6].pos)
    ok += 1

    # ---- a ramp on the REVERSE carriageway faces, and lies, the other way ---------------------
    # `ramp_frame_sign` is the product of two signs and the second one was missing: which
    # carriageway the slot is on. A westbound exit had its mouth faced east, so the curve left it
    # backwards and looped through half the district to reach its own second station.
    net7, mp7, _cp7, _rr7 = pv.build_testbed()
    m7, r7 = net7.aux_pairs()[0]
    net7.points[m7].aux_fwd, net7.points[m7].aux_bwd = 0, 1
    assert ramp_carriageway(net7, m7, (net7.points[m7].pos[0], -20.0)) == lp.REV
    # WHICH SIDE THE MOUTH IS ON IS THE ANSWER (8l): move the block and the mouth follows it,
    # because a station may declare a block on BOTH carriageways and hand one ramp to each.
    p7 = net7.points[r7]
    p7.pos = (p7.pos[0], -abs(p7.pos[1]), p7.pos[2])
    assert ramp_side_of(net7, m7, r7) == lp.REV
    assert ramp_frame_sign(net7, m7, r7) < 0, "a reverse-carriageway ramp's frame is reversed"
    want7, ax7, side7 = ramp_target(net7, m7, r7)
    assert side7 < 0 and want7[1] < 0.0, (side7, want7)
    face7 = ramp_facing(net7, m7, r7)
    assert face7[0] * ax7[0] + face7[1] * ax7[1] < 0, (face7, ax7)
    ok += 1

    # ---- the paint: ONE dash clock per run ----------------------------------------------------
    # Every boundary of a run must break at the same cross-sections, whatever sample it opens at.
    # Walking each from its own first point put an auxiliary lane that opens 4.00 m into the run
    # 4.00 m out of phase on a 9.00 m period -- half a period, which reads as the dashes on that
    # lane being wrong rather than merely offset.
    period = MARK_DASH_ON + MARK_DASH_OFF
    checked = 0
    for road in net.roads.values():
        for uids in road_runs(net, road):
            sol = solve_road(net, road, uids)
            if sol is None:
                continue
            grids = {}
            for m in solve_marks(sol):
                if not m.dashed:
                    continue
                # The dash's own start, in the run's clock: nearest sample is enough here because
                # the assertion is about the PHASE, and the grid is 9 m against a 4 m sampling.
                s0 = min(sol.samples, key=lambda sm: math.dist(sm.pos, m.points[0])).s
                grids.setdefault(m.slot_id, set()).add(round(s0 / period) * period)
            if len(grids) < 2:
                continue
            # Every boundary's dashes sit on the SAME grid -- the ones that open late simply start
            # further along it. So each grid is a subset of the longest, never an offset copy.
            widest = max(grids.values(), key=len)
            for sid, g in grids.items():
                assert g <= widest, "%s/%s is on its own dash grid: %s not in %s" % (
                    road.name, sid, sorted(g - widest)[:4], sorted(widest)[:4])
            checked += 1
    assert checked, "the testbed must have a run with two dashed boundaries"
    print("OK: one dash clock per run -- every boundary breaks on the same grid (%d run(s))"
          % checked)
    ok += 1

    print("point_solve.py: %d checks PASS" % ok)
    return True


if __name__ == "__main__":
    self_test()

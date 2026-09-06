"""`.lanekit.json` **v2** -- the Godot hand-off.

v1 was a flat list of lanes as dense polylines. Three things were wrong with it and all three are
fixed here by construction rather than by convention:

* **The curve was not a curve.** Every `Curve3D` control point got zero in/out handles, so
  `getBakedPoints()` handed the polyline straight back and cars corner-cut and micro-jittered
  through every bend. Blender ALREADY has the tangents -- the chain is a spline -- so v1 was
  throwing information away, not lacking it. v2 emits `{p, in, out}` per control point, at the
  STATIONS rather than every 4 m sample, which is roughly a 5x drop in control points.
* **"Can a car spawn here" was inferred from whether a turn letter was blank.** That is why all 351
  island through lanes shipped unspawnable. v2 emits an explicit `spawnable` boolean, which cannot
  fail that way.
* **There was no `junctions[]`.** The clique already knows its centre, its arms, their bearings and
  their signal state; emitting it costs nothing here and is exactly what roads-v2 Phase 2's
  `JunctionArbiter` needs -- so the world does not need re-baking when Phase 2 lands.

`points` is still emitted alongside `curve`, so a Godot build that has not taken the v2 reader yet
keeps working unchanged.

THE ONE STRUCTURAL RULE. A road's chain is BROKEN at every junction gap: two chain-adjacent
INTERSECTION points are joined by the pad, not by carriageway, so a lane must not be swept through
the middle of the pad. Each unbroken stretch is a `run`, and runs are joined by turn connectors.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "lib"))

import lane_movements as lm                                                  # noqa: E402
import lane_profile as lp                                                    # noqa: E402
import road_points as rp                                                     # noqa: E402

try:
    from . import point_model as pm, point_profile as pp, point_validate as pv
    from . import point_solve as ps
except ImportError:
    import point_model as pm                                                 # noqa: E402
    import point_profile as pp                                               # noqa: E402
    import point_validate as pv                                              # noqa: E402
    import point_solve as ps                                                 # noqa: E402

SCHEMA_VER = 2

#: Straight-biased, the distribution the previous pipeline shipped and tuned.
TURN_WEIGHTS = {lm.TURN_S: 0.6, lm.TURN_L: 0.2, lm.TURN_R: 0.2, lm.TURN_U: 0.05}
#: How often a car in an aux lane that ALSO has junction connectors takes the ramp instead. The
#: same weight as a turn, because that is what it is: leaving the road you are on. It only applies
#: when there is something to weigh it against -- a ramp edge on its own is the whole choice (1.0).
RAMP_WEIGHT = 0.2

DEFAULT_SPEED = {"ramp": 40.0, "street": 40.0, "arterial": 60.0, "expressway": 80.0}


def godot(p):
    """Blender `(x, y, z)` -> Godot `(x, z, -y)`. THE one conversion site, as it has always been:
    a second one is how a world ends up mirrored in a way nobody can find."""
    return [round(float(p[0]), 4), round(float(p[2]), 4), round(-float(p[1]), 4)]


def blender(p):
    """The exact inverse of `godot`. It lives HERE, next to it, for the same reason: the flow
    preview draws the EXPORTED document -- that is the whole point of it, showing what Godot will
    receive rather than what Blender happens to hold -- so it has to come back, and a second,
    separately-derived inverse somewhere else is how a preview ends up mirrored against the world
    it is previewing."""
    return (float(p[0]), -float(p[2]), float(p[1]))


# ------------------------------------------------------------------------------- runs

#: The chain split at every junction gap. ONE owner, in `point_solve` -- the exporter and the
#: geometry solve must agree on where the carriageway stops and the pad begins, and a second copy
#: of that rule is exactly how they would stop agreeing.
road_runs = ps.road_runs


def arm_name(road_name, run_index, n_runs):
    return road_name if n_runs == 1 else "%s_%d" % (road_name, run_index)


# ------------------------------------------------------------------------------- bezier

def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-12 else [0.0, 0.0, 0.0]


def _tangents_at(points, i):
    """`(in_tangent, out_tangent)` of the sampled polyline AT index `i`.

    ONE-SIDED ON PURPOSE. A central difference would smooth across a `SHARP` station, which is the
    one place the road is genuinely allowed to corner -- and the whole reason the handles are
    per-side in the first place."""
    n = len(points)
    fwd = _unit([points[min(i + 1, n - 1)][k] - points[i][k] for k in range(3)])
    bwd = _unit([points[i][k] - points[max(i - 1, 0)][k] for k in range(3)])
    if i <= 0:
        bwd = fwd
    if i >= n - 1:
        fwd = bwd
    return bwd, fwd


def _arc_handle(chord, t0, t1):
    """Handle length for a cubic spanning `chord` that leaves along `t0` and arrives along `t1`.

    EXACT FOR A CIRCULAR ARC: a cubic matching an arc of turn angle `theta` and chord `c` wants
    `h = (4/3) tan(theta/4) c / (2 sin(theta/2))`, degenerating to the familiar `c/3` as
    `theta -> 0` -- a straight span, where the cubic is a straight line and the fit is exact too.
    This is the STARTING POINT and the fallback; `_fit_span` refines it against the real samples."""
    dot = max(-1.0, min(1.0, sum(t0[k] * t1[k] for k in range(3))))
    theta = math.acos(dot)
    if theta < 1e-4 or theta > math.pi - 1e-4:
        return chord / 3.0
    return (4.0 / 3.0) * math.tan(theta / 4.0) * chord / (2.0 * math.sin(theta / 2.0))


#: How far the exported cubic may sit from the sampled lane it represents, in metres, before the
#: export puts another control point in. Well under a paint stripe, and nearly three orders of
#: magnitude under the 22.57 m the Catmull refit reached -- see `curve_points`.
CURVE_FIT_TOL = 0.05

#: Ceiling on control points per lane, as a multiple of its stations. A pathological shape must
#: cost a bigger file, not an unbounded one.
CURVE_FIT_MAX_FACTOR = 6

#: Cubic samples per span when measuring the fit. The error is taken at these, so it bounds what a
#: car driving the curve actually experiences, not what the control points promise.
FIT_SAMPLES = 24


def _cubic(p0, h0, h1, p1, n=FIT_SAMPLES):
    """Sampled cubic bezier from `p0` with ABSOLUTE control points `h0`, `h1` to `p1`."""
    out = []
    for j in range(n + 1):
        t = j / float(n); u = 1.0 - t
        out.append(tuple(u*u*u*p0[k] + 3*u*u*t*h0[k] + 3*u*t*t*h1[k] + t*t*t*p1[k]
                         for k in range(3)))
    return out


def _dist_to_polyline(p, poly):
    best = float("inf")
    for i in range(len(poly) - 1):
        a, b = poly[i], poly[i + 1]
        d = [b[k] - a[k] for k in range(3)]
        L = sum(x * x for x in d)
        t = 0.0 if L < 1e-12 else max(0.0, min(1.0, sum((p[k] - a[k]) * d[k] for k in range(3)) / L))
        best = min(best, math.dist(p, [a[k] + d[k] * t for k in range(3)]))
    return best


def _chord_params(sub):
    """Chord-length parameterisation of a sub-polyline, normalised to [0, 1]."""
    acc, out = 0.0, [0.0]
    for i in range(len(sub) - 1):
        acc += math.dist(sub[i], sub[i + 1])
        out.append(acc)
    return [u / acc for u in out] if acc > 1e-9 else [i / float(max(1, len(sub) - 1))
                                                     for i in range(len(sub))]


def _fit_span(sub, t0, t1):
    """Least-squares handle lengths for one cubic through `sub`, with the END TANGENTS FIXED.

    `t0` leaves `sub[0]`; `t1` arrives at `sub[-1]` (pointing forward, so the handle is
    subtracted). Returns `(alpha, beta)`, the two handle lengths in metres.

    WHY FIT AND NOT JUST COMPUTE. `_arc_handle` is exact for a circular arc, and a road span is
    only approximately one -- the Hermite `road_points.resample` builds from the authored tangents
    puts more of its turn near whichever end has the longer handle. Fitting the two lengths is a
    2-unknown linear least squares (Schneider's classic form: the tangent DIRECTIONS are already
    known and correct, so only how far to reach along them is in question), which is a closed form
    and costs nothing. Without it the fit is driven entirely by subdivision, and subdivision that
    is compensating for a wrong handle length slivers instead of converging."""
    n = len(sub)
    if n < 3:
        return None
    p0, p3 = sub[0], sub[-1]
    us = _chord_params(sub)
    c00 = c01 = c11 = x0 = x1 = 0.0
    for i in range(n):
        u = us[i]; v = 1.0 - u
        b0, b1 = v*v*v, 3.0*v*v*u
        b2, b3 = 3.0*v*u*u, u*u*u
        a0 = [t0[k] * b1 for k in range(3)]
        a1 = [-t1[k] * b2 for k in range(3)]
        tmp = [sub[i][k] - (p0[k]*(b0 + b1) + p3[k]*(b2 + b3)) for k in range(3)]
        c00 += sum(a0[k]*a0[k] for k in range(3))
        c01 += sum(a0[k]*a1[k] for k in range(3))
        c11 += sum(a1[k]*a1[k] for k in range(3))
        x0 += sum(a0[k]*tmp[k] for k in range(3))
        x1 += sum(a1[k]*tmp[k] for k in range(3))
    det = c00*c11 - c01*c01
    if abs(det) < 1e-12:
        return None
    alpha = (x0*c11 - x1*c01) / det
    beta = (c00*x1 - c01*x0) / det
    chord = math.dist(p0, p3)
    # A negative or wild handle is a fit that has run away (near-collinear data, a cusp in an
    # offset lane). Reject it and let the arc form stand -- never ship a curve that loops.
    if not (1e-6 < alpha < chord * 3.0) or not (1e-6 < beta < chord * 3.0):
        return None
    return alpha, beta


def _span_handles(points, i0, i1, t_out, t_in):
    """The two ABSOLUTE handle points for the cubic over `points[i0:i1]`."""
    sub = points[i0:i1 + 1]
    chord = math.dist(points[i0], points[i1])
    fit = _fit_span(sub, t_out, t_in)
    if fit is None:
        h = _arc_handle(chord, t_out, t_in)
        a = b = h
    else:
        a, b = fit
    return ([points[i0][k] + t_out[k] * a for k in range(3)],
            [points[i1][k] - t_in[k] * b for k in range(3)])


def _span_error(points, i0, i1, h_out, h_in):
    """`(worst deviation, index to split at)` for the cubic over `points[i0:i1]`.

    MEASURED CURVE-TO-LANE, NOT LANE-TO-CURVE, and the direction is the whole point. Those two are
    not the same number: a cubic that bulges wide and comes back still passes near every sample
    (so lane-to-curve reads small) while sitting metres off the road in between -- and it is the
    car, driving the curve, that ends up in the wrong place. Measured on the sample network the
    two disagreed by 1.4 m on `demo_ramp_b_F1`, which is a whole lane.

    The split BISECTS. Splitting at the worst sample is the classic curve-fit choice, but when the
    worst point sits near an end -- which is what a mis-set handle looks like -- it shaves off a
    one-sample sliver, the span stays worst, and the next iteration shaves another. Measured: 21 of
    36 control points on `demo_ramp_b_F1` went into three such runs while two 90 m spans were never
    split at all, and the budget ran out. Halving cannot do that."""
    if i1 - i0 < 2:
        return 0.0, -1
    sub = points[i0:i1 + 1]
    worst = max(_dist_to_polyline(q, sub) for q in _cubic(points[i0], h_out, h_in, points[i1]))
    return worst, (i0 + i1) // 2


def curve_points(points, indices):
    """`[{p, in, out}]` at the chosen sample indices -- the v2 lane geometry.

    Control points start at the STATIONS: that is where the author put the shape, so that is where
    a spline's control points belong. Extra ones are added ONLY where a cubic cannot follow the
    lane to `CURVE_FIT_TOL`.

    THE HANDLES COME FROM THE SAMPLED CURVE, NOT FROM THE CHORD THROUGH THE NEIGHBOURS. This was a
    Catmull-Rom refit, `+/- (P_{i+1} - P_{i-1}) / 6` -- a SECOND owner of the road's shape, and a
    worse-informed one. The centreline is a Hermite per span, built by `road_points.resample` from
    the station's AUTHORED facing (`station_axis` -- the R-key bend gesture) and its
    `handle_in`/`handle_out` lengths in metres; refitting from neighbour POSITIONS discards all of
    it, so the artist's bend reached the asphalt and never reached Godot. Measured on the addon's
    own sample network, the exported Path3D -- which is what `WorldBaker` turns into every
    `Curve3D` the ambient cars drive -- ran up to **22.57 m** off the lane it is supposed to BE
    (`demo_ramp_F0`), because at an open end the refit took a chord sitting **70.3 deg** off the
    true heading. Gate green, geometry perfect, and no existing check could see it.

    So each handle's DIRECTION is the polyline's own one-sided tangent at that station, its LENGTH
    is least-squares-fitted against the real samples, and the span subdivides if that still is not
    enough. A lane centreline is an OFFSET curve whose tangent genuinely differs from the road
    centreline's on any bend, so all of it is read from the LANE's own samples -- the same reason
    `GN_PointSpine` stores one shared `rka_lat` rather than letting each layer re-derive a frame."""
    idx = sorted(set(i for i in indices if 0 <= i < len(points)))
    if len(idx) < 2:
        idx = [0, len(points) - 1]
    budget = max(4, len(idx) * CURVE_FIT_MAX_FACTOR)
    handles = _all_handles(points, idx)
    while len(idx) < budget:
        worst_k, worst_err, worst_at = -1, CURVE_FIT_TOL, -1
        for k in range(len(idx) - 1):
            err, at = _span_error(points, idx[k], idx[k + 1], handles[k][1], handles[k + 1][0])
            if err > worst_err:
                worst_k, worst_err, worst_at = k, err, at
        if worst_k < 0 or worst_at <= idx[worst_k] or worst_at >= idx[worst_k + 1]:
            break
        idx.insert(worst_k + 1, worst_at)
        handles = _all_handles(points, idx)
    return [{"p": godot(points[i]),
             "in": godot([handles[k][0][j] - points[i][j] for j in range(3)]),
             "out": godot([handles[k][1][j] - points[i][j] for j in range(3)])}
            for k, i in enumerate(idx)]


def _all_handles(points, idx):
    """Per control point, `[in, out]` as ABSOLUTE positions. One owner of the arithmetic; each
    SPAN owns both of its own handles -- the `out` of the station it leaves and the `in` of the
    one it arrives at -- which is what lets two spans of different length meet at one station
    without either restating the other's curvature."""
    tans = [_tangents_at(points, i) for i in idx]
    hs = [[list(points[i]), list(points[i])] for i in idx]
    for k in range(len(idx) - 1):
        a, b = _span_handles(points, idx[k], idx[k + 1], tans[k][1], tans[k + 1][0])
        hs[k][1], hs[k + 1][0] = a, b
    return hs


#: A lane whose exported Path3D sits further than this from the lane itself is REPORTED -- by the
#: gate (`point_validate.path_deviation`), by `Preview > Flow Report`, and in the viewport. A third
#: of a car's width: far enough to be a real mistake, close enough to catch one before it is 22 m.
PATH_DEVIATION_WARN = 0.5

#: ...and past this it is an ERROR. A car a full lane off the road is not a tuning problem.
PATH_DEVIATION_ERROR = 2.0


def curve_polyline(lane, samples_per_span=FIT_SAMPLES):
    """The exported `curve` block evaluated the way Godot's `Curve3D` will, in GODOT space.

    THE ONE OWNER of "where will the car actually be". `point_preview` draws this, the gate
    measures with it, and `self_test` asserts on it -- so the picture, the finding and the test
    cannot disagree about what shipped, which is the whole reason the preview exists."""
    c = lane.get("curve") or []
    if len(c) < 2:
        return [tuple(p) for p in lane.get("points", [])]
    out = []
    for k in range(len(c) - 1):
        p0, p1 = tuple(c[k]["p"]), tuple(c[k + 1]["p"])
        h0 = [p0[j] + c[k]["out"][j] for j in range(3)]
        h1 = [p1[j] + c[k + 1]["in"][j] for j in range(3)]
        seg = _cubic(p0, h0, h1, p1, samples_per_span)
        out.extend(seg if not out else seg[1:])
    return out


def path_deviation(lane):
    """How far the exported Path3D wanders from the lane polyline it represents, in metres.

    Curve-to-lane, never the reverse -- see `_span_error`. Zero for a lane with no `curve` block
    (a v1 lane IS its polyline)."""
    pts = [tuple(p) for p in lane.get("points") or []]
    if len(pts) < 2 or len(lane.get("curve") or []) < 2:
        return 0.0
    return max(_dist_to_polyline(q, pts) for q in curve_polyline(lane))


def deviating_lanes(doc, threshold=PATH_DEVIATION_WARN):
    """`[(lane_id, deviation)]` over `threshold`, worst first."""
    out = [(l["id"], path_deviation(l)) for l in doc.get("lanes") or []]
    out = [(i, d) for i, d in out if d > threshold]
    out.sort(key=lambda r: -r[1])
    return out


def _station_indices(samples):
    # `is not None`, NOT a truth test: `Sample.at_station` holds the station INDEX, so station 0
    # is falsy and a plain `if s.at_station` silently drops the first control point of every road.
    return [i for i, s in enumerate(samples) if s.at_station is not None]


# ------------------------------------------------------------------------------- lanes

def _slot_index(slot_id, lane_count):
    """Distance from the median, as an index. An aux slot sits OUTBOARD of every standard lane, so
    it continues the numbering rather than restarting it."""
    digits = "".join(c for c in slot_id if c.isdigit())
    k = int(digits) if digits else 0
    return k + lane_count if slot_id.startswith(("AF", "AR")) else k


def _dir_xy(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    m = math.hypot(dx, dy)
    return (1.0, 0.0) if m < 1e-9 else (dx / m, dy / m)


def _aux_handoffs(net, uids, pts, samples, st_idx):
    """`{slot_id: mainline_uid, (slot_id, "index"): sample_index}` for every station in this run
    that hands an aux slot to a ramp.

    Which slots: EVERY slot of the aux BLOCK on the side that declares the ramp -- the same block
    `point_profile.aux_block` anchors the gore line on, so the lanes that end at the gore are the
    lanes whose edges the gore is measured from.

    THE BLOCK, NOT THE OUTERMOST SLOT (8j) -- 8g.1's correction, one level up and never applied
    here. `aux_edge_offset` was taught that an exit is a block of slots and the geometry followed;
    this kept handing over exactly one, so with `aux_fwd = 2` the ramp's second lane had no
    predecessor at all. Both lanes were paved, both were exported, the gate was green and the
    inner one was unreachable from anywhere in the world -- `Preview > Flow Report` reports it as
    a `ramp_orphan`, which is the only place it was ever going to show."""
    out = {}
    if not st_idx:
        return out
    ramp_of = {}
    for main_uid, ramp_uid in net.aux_pairs():
        ramp_of.setdefault(main_uid, []).append(ramp_uid)
    for pos, uid in enumerate(uids):
        if uid not in ramp_of or pos >= len(st_idx):
            continue
        res = pts[pos]
        alloc = ps.aux_allocation(net, uid)
        for ramp_uid in ramp_of[uid]:
            mine = alloc.get(ramp_uid)
            if mine is None:
                mine = pp.aux_slot_ids(pp.build_profile(res),
                                       ps.ramp_side_of(net, uid, ramp_uid))
            for sid in mine:
                out[sid] = uid
                out[(sid, "index")] = st_idx[pos]
                #: WHICH END OF THE LANE THE GORE IS (8l). An exit's aux lane ENDS at it and an
                #: entrance's BEGINS at it, and the cut is on opposite sides -- see `build_run`.
                out[(sid, "entrance")] = pm.ramp_is_entrance(net, ramp_uid)
    return out


class RunLanes(object):
    """One unbroken stretch of a road, and the lanes it exports. `entry_uid`/`exit_uid` are per
    lane and already account for direction -- a REV lane's travel ENDS where the run begins."""

    __slots__ = ("road", "arm", "uids", "lanes", "samples", "stations")

    def __init__(self, road, arm, uids, lanes, samples, stations):
        self.road, self.arm, self.uids = road, arm, uids
        self.lanes, self.samples, self.stations = lanes, samples, stations


def build_run(net, road, uids, arm, n_runs):
    """One run -> its lane dicts. Everything geometric comes from `road_points`; nothing here
    computes a lateral offset."""
    if len(uids) < 2:
        return RunLanes(road, arm, uids, [], [], [])
    pts = [net.resolved(u) for u in uids]
    is_loop = road.is_loop and len(road.points) == len(uids)
    # Same end-of-run rule the sweep uses, or the exported lane leaves the mouth on a different
    # heading from the tarmac under it (`point_profile.run_end_axes`).
    stations = pp.stations(pts, is_loop,
                          end_axes=(None if is_loop else pp.run_end_axes(net, pts)))
    samples = rp.resample(stations, is_loop)
    routes = rp.lane_taper_route(stations, samples, is_loop)
    st_idx = _station_indices(samples)
    profiles, _b = pp.chain_profiles(pts, is_loop)
    counts = {lp.FWD: max(int(p.lanes_fwd) + int(p.aux_fwd) for p in pts),
              lp.REV: max(int(p.lanes_bwd) + int(p.aux_bwd) for p in pts)}
    speed = road.base.design_speed or DEFAULT_SPEED.get(road.road_class, 50.0)

    total = samples[-1].s or 1.0
    grade = (samples[-1].pos[2] - samples[0].pos[2]) / total
    banking = sum(p.roll for p in pts) / len(pts)

    handoff = _aux_handoffs(net, uids, pts, samples, st_idx)

    out = []
    for rt in routes:
        if not rt.dir or rt.dir == lp.NONE:
            continue
        fwd = (rt.dir == lp.FWD)
        pts_world = rt.points if fwd else list(reversed(rt.points))
        idx = st_idx if fwd else sorted(len(samples) - 1 - i for i in st_idx)
        base_lanes = max(int(p.lanes_fwd if fwd else p.lanes_bwd) for p in pts)
        # AN EXIT LANE ENDS AT ITS GORE. The aux slot keeps tapering downstream as pavement
        # recovery, but as a LANE it has left with the ramp -- and an aux lane exported at full
        # run length ends hundreds of metres past the ramp it feeds, far outside `CHAIN_TOL`, so
        # the successor never chains and nothing ever drives onto the ramp. That was the whole of
        # the exported defect: `demo_ramp_F0` had no predecessor at all.
        #
        # AN ENTRANCE'S LANE BEGINS AT ITS GORE, AND THE CUT IS THE OTHER WAY (8l). This branch
        # was written for exits and applied to both, so the lane a merging ramp handed into was
        # the stretch of aux slot UPSTREAM of the merge: `demo_ramp_F0` ended at x = 860 and its
        # successor's head was at x = 264, six hundred metres back down the road. Nothing reported
        # it -- the ramp had a successor, so it was not `broken`, and the lane was reached, so it
        # was not `unreached`. It is the acceleration lane, and it starts where the ramp arrives.
        hand_uid = handoff.get(rt.slot_id)
        cut = None if hand_uid is None else handoff[(rt.slot_id, "index")]
        opened_by_cut = died_by_cut = False
        if cut is not None:
            k = cut if fwd else (len(samples) - 1 - cut)
            if 1 <= k < len(pts_world) - 1:
                if handoff.get((rt.slot_id, "entrance")):
                    pts_world = pts_world[k:]
                    idx = [i - k for i in idx if i >= k] or [0, len(pts_world) - 1]
                    opened_by_cut = True
                else:
                    pts_world = pts_world[:k + 1]
                    idx = [i for i in idx if i <= k] or [0, k]
                    died_by_cut = True
            else:
                hand_uid = None
        lane_id = "%s_%s" % (arm, rt.slot_id)
        # A lane that opens or dies inside the run is a TAPER. A car must not be spawned on one:
        # it may be a metre wide where the spawn lands. `spawnable` says so explicitly rather than
        # leaving it to be inferred from a blank turn letter, which is how v1 got it wrong.
        through = (rt.merge_into is None and rt.opens_from is None)
        out.append({
            "id": lane_id,
            "points": [godot(p) for p in pts_world],
            "curve": curve_points(pts_world, idx),
            "kind": "through",
            "from_arm": arm,
            "turn": "",
            "spawnable": bool(through),
            "loop": bool(is_loop),
            "zone_id": road.zone_id or road.name,
            "road_class": road.road_class,
            "road_name": road.name,
            "speed_limit": round(float(speed), 1),
            "lane_index": _slot_index(rt.slot_id, base_lanes),
            "lane_width": round(max(rt.widths) if rt.widths else 3.5, 3),
            "grade": round(grade if fwd else -grade, 4),
            "banking": round(banking, 4),
            "junction_id": "",
            "next": [], "next_weights": [], "next_kinds": [],
            "_slot": rt.slot_id,
            #: The mainline station whose AUX link hands this lane to a ramp, or "".
            "_aux_handoff": hand_uid or "",
            "_dir": rt.dir,
            "_entry_uid": uids[0] if fwd else uids[-1],
            "_exit_uid": uids[-1] if fwd else uids[0],
            "_merge_into": ("%s_%s" % (arm, rt.merge_into)) if rt.merge_into else "",
            #: Is this lane ZERO WIDTH at the run's own ends? A lane that opens inside the run is
            #: not there at the head; one that dies inside it is not there at the tail. A junction
            #: arm may only offer the lanes that exist AT THE STOP LINE -- see `_arm_lanes`.
            #:
            #: ASKED OF THE WIDTHS, NOT OF THE RECEIVER (8k). `merge_into`/`opens_from` name the
            #: lane a taper hands over TO, and `road_points.lane_taper_route` leaves BOTH None
            #: when it cannot resolve a receiver -- which is indistinguishable here from a lane
            #: that runs the full length. `i0`/`i1` are the first and last sample at which the
            #: slot is a usable lane, which is the question actually being asked, and they are
            #: right whether or not a receiver was found. With the proxy, an auxiliary lane whose
            #: receiver went unresolved was offered by the junction arm as its kerb-most through
            #: lane, `lane_movements.target_lane` shifted every movement one outboard, and the
            #: arm's median lane came out with no predecessor at all -- 8i.13's failure again,
            #: reached from the other side.
            #: ...and the hand-off CUT is one of the ways a lane stops reaching the run's end.
            "_opens_inside": bool((rt.i0 > 0) if fwd else (rt.i1 < len(samples) - 1))
                             or opened_by_cut,
            "_dies_inside": bool((rt.i1 < len(samples) - 1) if fwd else (rt.i0 > 0))
                            or died_by_cut,
            # Blender-space, stripped before writing. The connector maths must work in the
            # authored frame -- `points` is already Godot-converted, and there is exactly ONE
            # conversion site.
            "_world": pts_world,
        })
    _adjacency(out, profiles)
    return RunLanes(road, arm, uids, out, samples, stations)


def _adjacency(lanes, profiles):
    """`inner_lane` / `outer_lane` -- the lane-change edges.

    NOT optional. Redesign defect 10 was exactly this: "the gore geometry is fine, the exits are
    unusable, because an exit lane has no way in except a lane change and no lane-change edge
    exists". Here the aux lane is a slot in the same station's profile, so the adjacency is a
    profile fact -- `lane_neighbors` would give it too; this reads it off the widest station,
    which is where every slot that exists at all is present."""
    if not profiles:
        return
    widest = max(profiles, key=lambda p: len(p.slots))
    by_slot = {l["_slot"]: l for l in lanes}
    ids = [s.id for s in widest.slots if s.id in by_slot]
    for i, sid in enumerate(ids):
        lane = by_slot[sid]
        # INBOARD is toward the divide (s = 0). The slot list runs -s -> +s, so for a FWD lane
        # (on the +s side, F0 nearest the divide) the inboard neighbour is the LOWER entry, and
        # for a REV lane (on -s, listed outermost-first) it is the HIGHER one. Getting this
        # backwards would point every exit ramp's lane-change edge at the median.
        lo = ids[i - 1] if i > 0 else None
        hi = ids[i + 1] if i + 1 < len(ids) else None
        inner, outer = (hi, lo) if lane["_dir"] == lp.REV else (lo, hi)
        for a, b in (("inner_lane", inner), ("outer_lane", outer)):
            if b is not None and by_slot[b]["_dir"] == lane["_dir"]:
                lane[a] = by_slot[b]["id"]


# ------------------------------------------------------------------------------- junctions

#: The turn-connector shape, owned by `point_solve` so the exported curve and the pad's on-screen
#: movement preview are the same cubic. Returns `(sampled_points, handle_a, handle_b)`.
_bezier_through = ps.bezier_through


def _lane_dir(lane, at_end):
    pts = lane["_world"]
    return _dir_xy(pts[-2], pts[-1]) if at_end else _dir_xy(pts[0], pts[1])


def _arm_lanes(lanes_by_uid, uid, side):
    """The lanes of one junction arm that ACTUALLY EXIST AT THE STOP LINE.

    A lane that opens inside its run is zero width at the run's head, and one that dies inside it
    is zero width at the tail -- `spawnable` already says as much ("a car spawned on it may be a
    metre wide"). A junction arm was still offering them, and `target_lane` preserves distance
    from the KERB, so an exit arm carrying a tapering aux slot counted it as its outermost through
    lane: both approach lanes shifted one outboard, the straight-ahead movement was fed into a
    lane that is not there yet, and the exit's MEDIAN lane came out with no predecessor at all --
    a through lane on a main road that nothing can reach, at every junction whose exit arm has an
    auxiliary lane anywhere in the same run.

    `spawnable` is not the test, though it is the same fact seen from one side: a lane that runs
    full width from the stop line and tapers away 300 m later is a perfectly good thing to leave a
    junction in, and an unspawnable one. Which END is zero decides it, so it is asked per end."""
    key = "out" if side == "out" else "in"
    flag = "_opens_inside" if key == "out" else "_dies_inside"
    return [l for l in lanes_by_uid.get((uid, key), []) if not l.get(flag)]


def build_junctions(net, lanes_by_uid, all_lanes):
    """One pad -> its turn connectors and its `junctions[]` entry.

    Legality is `lib/lane_movements.py` and nothing else -- the SAME rule set the "why is there no
    turn here" explainer will use, so an artist can never be told one thing by the tool and shown
    another by the export."""
    junctions, connectors = [], []
    for comp in net.junction_cliques():
        jid = "j%s" % comp[0][2:]
        cx = sum(net.points[u].pos[0] for u in comp) / len(comp)
        cy = sum(net.points[u].pos[1] for u in comp) / len(comp)
        cz = sum(net.points[u].pos[2] for u in comp) / len(comp)
        arms = []
        for u in comp:
            res = net.resolved(u)
            inb = _arm_lanes(lanes_by_uid, u, "in")
            outb = _arm_lanes(lanes_by_uid, u, "out")
            road = net.road_of(u)
            arms.append({
                "point": u,
                "road": road.name if road else "",
                "bearing": round(math.degrees(math.atan2(net.points[u].pos[1] - cy,
                                                         net.points[u].pos[0] - cx)), 2),
                "in_lanes": [l["id"] for l in inb],
                "out_lanes": [l["id"] for l in outb],
                "traffic_light": bool(res.traffic_light),
            })

        for u in comp:
            res = net.resolved(u)
            approach = _arm_lanes(lanes_by_uid, u, "in")
            for lane in approach:
                d_in = _lane_dir(lane, True)
                n_in = len([x for x in approach if x["_dir"] == lane["_dir"]])
                for v in comp:
                    outs = _arm_lanes(lanes_by_uid, v, "out")
                    if not outs:
                        continue
                    d_out = _lane_dir(outs[0], False)
                    # `same_arm` is leaving by the MOUTH you entered by -- that, and only that, is
                    # a U-turn. It is NOT "the same road": a street continuing straight through its
                    # own crossing has the same road on both sides, and calling that a U-turn
                    # deletes every straight-ahead movement in the world.
                    verdict = lm.movement_verdict(
                        d_in, d_out, lane["lane_index"], n_in, len(outs),
                        same_arm=(u == v), allow_cross=bool(res.allow_cross),
                        allow_uturn=bool(res.allow_uturn))
                    if not verdict.ok:
                        continue
                    target = next((o for o in outs
                                   if o["lane_index"] == verdict.to_lane), None)
                    if target is None:
                        continue
                    p0 = lane["_world"][-1]
                    p1 = target["_world"][0]
                    pts, a, b = _bezier_through(p0, d_in, p1, d_out)
                    cid = "c%s_%s__%s" % (jid, lane["id"], target["id"])
                    connectors.append({
                        "id": cid,
                        "points": [godot(p) for p in pts],
                        # Three control points is a cubic exactly: start, its out handle, the end
                        # and its in handle. Nothing is approximated on the way out.
                        "curve": [{"p": godot(p0), "in": godot((0, 0, 0)),
                                   "out": godot([a[k] - p0[k] for k in range(3)])},
                                  {"p": godot(p1),
                                   "in": godot([b[k] - p1[k] for k in range(3)]),
                                   "out": godot((0, 0, 0))}],
                        "kind": "connector",
                        "from_arm": lane["from_arm"],
                        "turn": verdict.turn,
                        # A connector is NEVER a spawn point: a car dropped mid-pad is a car
                        # inside the intersection box.
                        "spawnable": False,
                        "loop": False,
                        "zone_id": lane["zone_id"],
                        "road_class": lane["road_class"],
                        "road_name": lane["road_name"],
                        "speed_limit": min(lane["speed_limit"], target["speed_limit"]),
                        "lane_index": lane["lane_index"],
                        "lane_width": lane["lane_width"],
                        "grade": 0.0, "banking": 0.0,
                        "junction_id": jid,
                        "next": [target["id"]], "next_weights": [1.0], "next_kinds": ["chain"],
                        "_from": lane["id"],
                    })
        junctions.append({"id": jid, "center": godot((cx, cy, cz)), "arms": arms,
                          "traffic_light": any(a["traffic_light"] for a in arms)})
    return junctions, connectors


# ------------------------------------------------------------------------------- ramps

def wire_ramps(net, lanes, by_uid):
    """`{predecessor_lane_id: successor_lane_id}` for every authored AUX link.

    An AUX link is a LANE-GRAPH FACT and used to export as nothing at all: the ramp's lanes had no
    predecessor and the mainline's aux lane merged back into the carriageway, so a car could not
    reach a ramp from anywhere in the world.

    Which way the edge runs is DERIVED (`point_model.ramp_is_entrance`), not read off a role the
    artist had to declare: a ramp is one-way, so its mouth is either the point its lanes leave from
    (an exit -- the aux lane becomes the ramp) or the point they arrive at (an entrance -- the ramp
    becomes the aux lane). That is the whole content of the old `RAMP_EXIT`/`RAMP_ENTRY` split, and
    holding it in two places is how a ramp ends up geometrically right with its traffic wired
    backwards -- which reads in game only as "no car ever uses that ramp"."""
    aux_by_station = {}
    for l in lanes:
        if l.get("_aux_handoff"):
            aux_by_station.setdefault(l["_aux_handoff"], []).append(l)
    out = {}
    for main_uid, ramp_uid in net.aux_pairs():
        p = net.points.get(ramp_uid)
        if p is None:
            continue
        # ONLY THIS RAMP'S SLOTS (8k). A station may hand its aux block to more than one ramp --
        # a two-lane exit that splits, a two-lane entrance fed by two ramps -- and every ramp at
        # the station used to be wired to the WHOLE block, so each wrote over the previous one's
        # edge and only the last was reachable. `point_solve.aux_allocation` is the one owner of
        # which slots belong to which ramp; an empty allocation means the block is over-subscribed
        # and `point_validate.check_aux_slots` is already reporting it.
        mine = ps.aux_allocation(net, main_uid).get(ramp_uid)
        auxes = [l for l in (aux_by_station.get(main_uid) or [])
                 if mine is None or l["_slot"] in mine]
        entrance = pm.ramp_is_entrance(net, ramp_uid)
        # An exit ramp STARTS at its mouth, an entrance ramp ENDS there -- so the mouth lane is
        # the one leaving that point, or the one arriving at it.
        mouth = by_uid.get((ramp_uid, "in" if entrance else "out")) or []
        if not auxes or not mouth:
            continue
        # Lane-for-lane, median-outward on both sides, so a two-lane ramp off a two-lane aux slot
        # pairs up without a table.
        auxes = sorted(auxes, key=lambda l: l["lane_index"])
        mouth = sorted(mouth, key=lambda l: l["lane_index"])
        for i, a in enumerate(auxes):
            b = mouth[min(i, len(mouth) - 1)]
            if entrance:
                out[b["id"]] = a["id"]
            else:
                out[a["id"]] = b["id"]
    return out


# ------------------------------------------------------------------------------- assembly

#: A joint's lanes must line up this closely to be wired end to end -- `LaneGraph.JUNCTION_RADIUS`,
#: the same distance the runtime would use if it fell back to proximity.
JOINT_TOL = 4.5


def wire_joints(net, lanes, by_uid):
    """`{lane_id: successor_id}` across a SEGMENT link that leaves its road -- a corner JOINT.

    A RUN NEVER SPANS TWO ROADS, so two arterials that meet end to end export two lane sets that
    stop at each other and nothing joins them. `W15`: with the corner rounded (`seed_district_roads
    .fillet_corner`) the two stations are coincident and the lanes physically meet, and the flow
    report's verdict moved from `open_end` (running off the edge of the network) to `broken` (a
    tail sitting on a head with no edge between them) -- the geometry fixed and the graph still
    silent. The runtime would recover it: `LaneGraph` derives a proximity edge inside
    `JUNCTION_RADIUS`. That is exactly the reason to emit it here instead. §8f.4's rule is that
    reachability is not geometry and needs its own eye; leaving the one road-to-road hand-over in
    the world to a distance test is leaving it where no check can see it, and it would go silently
    wrong the day a corner is authored a little wider than the radius.

    Matched by GEOMETRY, not by lane index: which of the two chains runs which way is an accident
    of how the plan was authored, and the pairing that is always true is that a lane's tail and its
    successor's head are the same physical point.
    """
    out = {}
    seen = set()
    for uid in sorted(net.points):
        road = net.road_of(uid)
        for other in net.points[uid].targets(pm.LINK_SEGMENT):
            if other not in net.points or (uid, other) in seen:
                continue
            seen.add((uid, other))
            seen.add((other, uid))
            if net.road_of(other) is road:
                continue                      # an ordinary span inside one road
            for a, b in ((uid, other), (other, uid)):
                for src in by_uid.get((a, "in"), ()):
                    tail = _end_xyz(src, -1)
                    best, bd = None, JOINT_TOL
                    for dst in by_uid.get((b, "out"), ()):
                        d = _dist3(tail, _end_xyz(dst, 0))
                        if d < bd:
                            best, bd = dst, d
                    if best is not None:
                        out[src["id"]] = best["id"]
    return out


def _end_xyz(lane, i):
    pts = lane.get("points") or []
    return tuple(pts[i]) if pts else (0.0, 0.0, 0.0)


def _dist3(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def export_network(net):
    """The whole `.lanekit.json` v2 document."""
    runs, lanes, arms = [], [], []
    for name in sorted(net.roads):
        road = net.roads[name]
        rr = road_runs(net, road)
        for i, uids in enumerate(rr):
            arm = arm_name(name, i, len(rr))
            run = build_run(net, road, uids, arm, len(rr))
            if not run.lanes:
                continue
            runs.append(run)
            lanes.extend(run.lanes)
            arms.append({"name": arm,
                         "lane_width": round(float(road.base.lane_width), 3),
                         "road": name})

    by_uid = {}
    for run in runs:
        for l in run.lanes:
            by_uid.setdefault((l["_entry_uid"], "out"), []).append(l)
            by_uid.setdefault((l["_exit_uid"], "in"), []).append(l)

    junctions, connectors = build_junctions(net, by_uid, lanes)
    ramp_links = wire_ramps(net, lanes, by_uid)
    joint_links = wire_joints(net, lanes, by_uid)

    # Wire each inbound lane to the connectors that leave it, straight-biased.
    out_of = {}
    for c in connectors:
        out_of.setdefault(c["_from"], []).append(c)
    for l in lanes:
        cs = out_of.get(l["id"], [])
        nxt = [c["id"] for c in cs]
        wts = [TURN_WEIGHTS.get(c["turn"], 0.2) for c in cs]
        kinds = ["chain"] * len(cs)
        if l["id"] in ramp_links:
            # THE RAMP EDGE. Authored (an AUX link), never inferred from proximity -- and it is a
            # `next`, not a lane change: at the gore the exit lane simply becomes the ramp, so a
            # car that is in it goes there. Without this edge the ramp is reachable by nothing and
            # ambient traffic never uses it, which is what the flow preview shows as an orphan.
            #
            # ADDED TO the junction connectors, not chosen INSTEAD of them. An aux lane that hands
            # off to a ramp and then runs on to a crossing has both successors, and taking the
            # connectors as proof the ramp edge was unwanted silently dropped every exit that sits
            # on a junction approach -- the orphan this edge exists to prevent, in the one
            # arrangement where the lane looked healthy enough not to check.
            nxt.append(ramp_links[l["id"]])
            wts.append(RAMP_WEIGHT if cs else 1.0)
            kinds.append("ramp")
        if l["id"] in joint_links and joint_links[l["id"]] not in nxt:
            # THE CORNER HAND-OVER. Like the ramp edge, added to whatever else this lane feeds --
            # a joint can sit on a junction approach the same way a gore can.
            nxt.append(joint_links[l["id"]])
            wts.append(1.0 if not cs else RAMP_WEIGHT)
            kinds.append("chain")
        if nxt:
            l["next"], l["next_weights"], l["next_kinds"] = nxt, wts, kinds
        elif l["_merge_into"]:
            # An explicit successor from the dying slot into the receiving one, so
            # `LaneGraph.explicitSuccessorsOf` closes the gap regardless of geometry (2.1a rule 4).
            l["next"] = [l["_merge_into"]]
            l["next_weights"] = [1.0]
            l["next_kinds"] = ["merge"]

    lanes = lanes + connectors
    for l in lanes:
        for k in [k for k in l if k.startswith("_")]:
            del l[k]

    roads = [{"name": n, "road_class": net.roads[n].road_class,
              "zone_id": net.roads[n].zone_id or n,
              "ped_access": bool(net.roads[n].ped_access),
              "design_speed": round(float(net.roads[n].base.design_speed), 1)}
             for n in sorted(net.roads)]
    return {"schema_ver": SCHEMA_VER, "roads": roads, "arms": arms,
            "junctions": junctions, "lanes": lanes}


def write(net, path):
    import json
    doc = export_network(net)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return doc


# ------------------------------------------------------------------------------- self-test

def self_test():
    ok = 0
    net, mp, cp, rr = pv.build_testbed()
    doc = export_network(net)
    lanes = {l["id"]: l for l in doc["lanes"]}
    through = [l for l in doc["lanes"] if l["kind"] == "through"]
    conns = [l for l in doc["lanes"] if l["kind"] == "connector"]

    assert doc["schema_ver"] == 2 and doc["roads"] and doc["arms"]
    assert len(lanes) == len(doc["lanes"]), "lane ids must be globally unique"
    print("OK: v2 document has schema_ver, roads[], arms[], junctions[] and unique lane ids")
    ok += 1

    # -- the chain is BROKEN at the pad ------------------------------------------------------------
    runs = road_runs(net, net.roads["road_main"])
    assert [len(r) for r in runs] == [3, 3], [len(r) for r in runs]
    for l in through:
        if l["road_name"] != "road_main":
            continue
        xs = [p[0] for p in l["points"]]
        assert not (min(xs) < 240.0 and max(xs) > 260.0), \
            "no lane may be swept through the middle of the pad (%s)" % l["id"]
    print("OK: a road's chain is broken at the junction gap -- no lane crosses the pad")
    ok += 1

    # -- spawnable is EXPLICIT ----------------------------------------------------------------------
    assert all(l["spawnable"] is False for l in conns), "a connector is never a spawn point"
    assert any(l["spawnable"] for l in through), "through lanes must be spawnable"
    aux = [l for l in through if l["id"].endswith("AF0")]
    assert aux and aux[0]["spawnable"] is False, \
        "a lane that opens inside its run is a taper -- a car spawned on it may be a metre wide"
    # This is the v1 defect made unrepresentable: `turn` is blank on a through lane AND the flag
    # is set, so nothing has to infer one from the other.
    assert all(l["turn"] == "" for l in through)
    print("OK: `spawnable` is explicit -- connectors false, through true, an opening aux false")
    ok += 1

    # -- real bezier handles ------------------------------------------------------------------------
    for l in doc["lanes"]:
        assert len(l["curve"]) >= 2, l["id"]
        assert all(set(c) == {"p", "in", "out"} for c in l["curve"])
    straight = next(l for l in through if l["road_name"] == "road_main")
    assert len(straight["curve"]) < len(straight["points"]), \
        "control points sit at the STATIONS -- v2 must be smaller than the v1 polyline"
    turner = next(l for l in conns if l["turn"] == lm.TURN_L)
    assert any(any(abs(v) > 1e-6 for v in c["out"]) for c in turner["curve"]), \
        "a turn connector's handles must be non-zero, or the curve is not a curve"
    print("OK: %d control points replace %d polyline points on a mainline lane; handles non-zero"
          % (len(straight["curve"]), len(straight["points"])))
    ok += 1

    # -- THE PATH3D IS THE LANE (8m regression) -----------------------------------------------------
    # This is the check that did not exist while the export ran 22.57 m off the road. It measures
    # the thing that ships -- the `curve` block, evaluated as `Curve3D` will evaluate it -- against
    # the lane it claims to be, and it is pure Python so it runs in `check_roads.sh --quick`.
    worst_id, worst = max(((l["id"], path_deviation(l)) for l in through), key=lambda r: r[1])
    assert worst <= CURVE_FIT_TOL * 2.0, \
        "exported Path3D is %.3f m off lane %s -- a car drives the CURVE, not the polyline" \
        % (worst, worst_id)
    # A bend must actually cost control points, or the fit is passing by being straight.
    bendy = max(through, key=lambda l: len(l["curve"]))
    assert len(bendy["curve"]) > 2, "the testbed's ramp must exercise the fit"
    # ...and a dead straight lane must NOT: two control points, because a cubic through two points
    # with collinear handles IS the straight line, and paying for more would bloat every district.
    flat = min((l for l in through if len(l["points"]) > 8), key=lambda l: len(l["curve"]))
    assert len(flat["curve"]) == 2, \
        "a straight lane wants 2 control points, got %d on %s" % (len(flat["curve"]), flat["id"])
    assert not deviating_lanes(doc), deviating_lanes(doc)
    print("OK: exported Path3D tracks its lane to %.3f m (worst: %s); a straight lane still costs "
          "2 control points" % (worst, worst_id))
    ok += 1

    # -- junction movements come from lane_movements and nowhere else --------------------------------
    assert len(doc["junctions"]) == 1
    j = doc["junctions"][0]
    assert len(j["arms"]) == 4 and all(a["road"] for a in j["arms"])
    turns = {c["turn"] for c in conns}
    assert turns <= {lm.TURN_L, lm.TURN_S, lm.TURN_R}, turns
    assert lm.TURN_S in turns and lm.TURN_L in turns and lm.TURN_R in turns
    assert not any(c["turn"] == lm.TURN_U for c in conns), "no U-turn unless the junction says so"
    print("OK: one pad, 4 arms, %d connectors covering L/S/R with no U-turn" % len(conns))
    ok += 1

    # -- every connector actually bridges the lanes it claims to -------------------------------------
    for c in conns:
        src = lanes[c["next"][0]]
        head = src["points"][0]
        tail = c["points"][-1]
        d = math.dist(head, tail)
        assert d < 1e-3, "connector %s ends %.2f m from its successor's head" % (c["id"], d)
    print("OK: every connector's tail is ON its successor's head (0.00 m, not 4.5 m of slack)")
    ok += 1

    # -- lane-change adjacency exists ----------------------------------------------------------------
    auxl = aux[0]
    assert auxl.get("inner_lane"), \
        "an aux exit lane is reachable ONLY by a lane change -- without inner_lane it is in the " \
        "graph but unusable (defect 10)"
    inner = lanes[auxl["inner_lane"]]
    assert inner["lane_index"] < auxl["lane_index"], "inner_lane must be toward the median"
    print("OK: the aux exit lane carries a lane-change edge inboard (%s -> %s)"
          % (auxl["id"], inner["id"]))
    ok += 1

    # -- a junction arm offers only the lanes that exist AT THE STOP LINE ------------------------------
    # The testbed's `road_main` opens an aux slot at p4, which is in the SAME run as the junction's
    # east mouth (p3). `target_lane` preserves distance from the KERB, so counting that slot as an
    # exit lane shifted both approach lanes one outboard: the straight-ahead movement was fed into
    # a lane that does not exist yet and the exit's MEDIAN lane came out with no predecessor at
    # all -- a through lane on a main road that nothing can reach.
    preds = {}
    for l in doc["lanes"]:
        for nid in l["next"]:
            preds.setdefault(nid, []).append(l["id"])
    east = [l for l in doc["lanes"] if l["id"].startswith("road_main_1_F")]
    assert east, [l["id"] for l in doc["lanes"]]
    for l in east:
        assert preds.get(l["id"]), "%s leaves the junction and nothing reaches it" % l["id"]
    # ...and the tapering aux slot is NOT one of them: a car cannot be in a lane of zero width.
    aux_ids = {l["id"] for l in doc["lanes"] if l["id"].endswith("_AF0")}
    for c in conns:
        assert c["next"][0] not in aux_ids, "%s feeds a lane that opens INSIDE the run" % c["id"]
    # The rule is per END, not `spawnable`: a lane that runs full width from the stop line and
    # tapers away later is a fine thing to leave a junction in, and an unspawnable one.
    assert all(l["lane_index"] == i for i, l in
               enumerate(sorted(east, key=lambda x: x["lane_index"]))), [l["id"] for l in east]
    print("OK: a junction arm offers only the lanes open at its stop line -- %d exit lane(s), all "
          "reachable" % len(east))
    ok += 1

    # -- the ramp edge, and which WAY it runs, derived from the chain ---------------------------------
    # ONE ramp role: an exit and an entrance are the same authored thing (a road joined to an aux
    # slot by an AUX link) walked in opposite directions, and the direction is a fact about the
    # ramp's own chain -- its mouth is the head on an exit and the tail on an entrance.
    def _ramp_edge(document):
        return [(l["id"], l["next"][0]) for l in document["lanes"]
                if "ramp" in l.get("next_kinds", [])]

    exits = _ramp_edge(doc)
    assert len(exits) == 1, exits
    assert exits[0][0].endswith("AF0") and "ramp_e" in exits[0][1], exits
    net3, _mp3, _cp3, rr3 = pv.build_testbed()
    assert not pm.ramp_is_entrance(net3, rr3[0].uid)
    net3.roads["ramp_e"].points.reverse()             # the mouth is now the chain's TAIL
    assert pm.ramp_is_entrance(net3, rr3[0].uid)
    ents = _ramp_edge(export_network(net3))
    assert len(ents) == 1, ents
    assert "ramp_e" in ents[0][0] and ents[0][1].endswith("AF0"), ents
    print("OK: the ramp edge is wired from the chain, not a role -- exit %s, entrance %s"
          % (exits[0][0].split("_")[-1], ents[0][1].split("_")[-1]))
    ok += 1

    # -- the merging lane gets an explicit successor --------------------------------------------------
    net2, mp2, cp2, rr2 = pv.build_testbed()
    net2.points[mp2[5].uid].lanes_fwd = 1
    doc2 = export_network(net2)
    merging = [l for l in doc2["lanes"] if "merge" in l["next_kinds"]]
    assert merging, "a dying lane must carry an explicit MERGE successor (2.1a rule 4)"
    print("OK: a dying lane emits an explicit merge successor, not a geometric guess")
    ok += 1

    print("\nALL SELF-TESTS PASSED (%d)" % ok)
    return True


if __name__ == "__main__":
    self_test()

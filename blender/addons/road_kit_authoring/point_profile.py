"""Station -> `lane_profile.Profile`: the slot-id vocabulary, and nothing else.

This module is the ONLY place a road point's scalar fields become a cross-section. It owns the id
vocabulary and it owns chain-wide id STABILITY; it does not own lateral position -- that is
`lane_profile.slot_offset()`, the single owner, and no other module may compute an offset
(redesign defect 1, the one rule that must never be relaxed).

    id      band
    ------  ---------------------------------------------------------------
    F0..Fn  forward travel lanes, counted OUTWARD from the divide
    R0..Rn  reverse travel lanes, counted OUTWARD from the divide
    AF/AR   auxiliary lanes -- ALWAYS outboard of the standard lanes, unless
            `aux_side = MEDIAN`, which is the offside-exit case
    MED     the median, present only when BOTH directions carry lanes
    SH/PK/SW  shoulder, parking, footway

Sides follow the existing convention (`lane_profile.profile_from_scalars`): the `+s` side carries
the FORWARD lanes and is the side called "L". Keeping that verbatim is what lets a profile built
here and one built from the legacy scalars be compared without a translation table.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "lib"))

import lane_profile as lp                                                    # noqa: E402
import road_points as rp                                                     # noqa: E402

try:
    from . import point_model as pm                                          # noqa: E402
except ImportError:
    import point_model as pm                                                 # noqa: E402


def fwd_id(i):
    return "F%d" % i


def rev_id(i):
    return "R%d" % i


def aux_fwd_id(i):
    return "AF%d" % i


def aux_rev_id(i):
    return "AR%d" % i


MED_ID = "MED"


def build_profile(pt, fwd_base=0, rev_base=0):
    """One station's cross-section, laid out median-outward with `anchor = DIVIDE` -- so `s = 0`
    is the centre divide and lanes expand either way from the point exactly as the model asks.

    `fwd_base` / `rev_base` shift the lane NUMBERING without moving anything: they are how a
    station that lost its median-side lane says so. See `chain_profiles`."""
    lw = float(pt.lane_width)
    nf, nr = int(pt.lanes_fwd), int(pt.lanes_bwd)
    af, ar = int(pt.aux_fwd), int(pt.aux_bwd)
    med = float(pt.median_width) if (nf > 0 and nr > 0) else 0.0
    aux_med = (pt.aux_side == pm.MEDIAN)
    slots = []

    def add(sid, kind, width, dir=lp.NONE, mark=lp.MARK_NONE):
        if width > 0.0:
            slots.append(lp.Slot(sid, kind, width, dir, mark))

    # ---- reverse side (-s), outermost first so the list stays ordered -s -> +s ----------------
    add("SW_R", lp.SIDEWALK, pt.right_walk_width)
    add("PK_R", lp.PARKING, pt.parking_right_width)
    add("SH_R", lp.SHOULDER, pt.shoulder_right_width)
    if not aux_med:
        for k in range(ar - 1, -1, -1):
            add(aux_rev_id(k), lp.AUX, lw, lp.REV, lp.MARK_NONE if k == ar - 1 else lp.MARK_DASH_W)
    for k in range(nr - 1, -1, -1):
        # `mark_left` is the line on a slot's LOW-s edge and this block runs outward-to-inward, so
        # the bare edge is the OUTERMOST lane -- unless an aux lane is outboard of it, in which
        # case the boundary between them is a real painted line.
        outermost = (k == nr - 1) and not (ar > 0 and not aux_med)
        add(rev_id(rev_base + k), lp.TRAVEL, lw, lp.REV,
            lp.MARK_NONE if outermost else lp.MARK_DASH_W)
    if aux_med:
        for k in range(ar):
            add(aux_rev_id(k), lp.AUX, lw, lp.REV, lp.MARK_DASH_W)

    # ---- the divide --------------------------------------------------------------------------
    # UNPAINTED: a median with real width IS the separator, and painting a solid yellow along its
    # low edge draws a line through the physical island (a real user report on the scalar path).
    add(MED_ID, lp.MEDIAN, med, lp.NONE, lp.MARK_NONE)

    # ---- forward side (+s) -------------------------------------------------------------------
    if aux_med:
        for k in range(af - 1, -1, -1):
            add(aux_fwd_id(k), lp.AUX, lw, lp.FWD, lp.MARK_DASH_W)
    for k in range(nf):
        if k == 0 and not aux_med:
            mark = lp.MARK_DOUBLE_Y if (nr > 0 and med <= 0.0) else lp.MARK_NONE
        else:
            mark = lp.MARK_DASH_W
        add(fwd_id(fwd_base + k), lp.TRAVEL, lw, lp.FWD, mark)
    if not aux_med:
        for k in range(af):
            add(aux_fwd_id(k), lp.AUX, lw, lp.FWD, lp.MARK_DASH_W)
    add("SH_L", lp.SHOULDER, pt.shoulder_left_width)
    add("PK_L", lp.PARKING, pt.parking_left_width)
    add("SW_L", lp.SIDEWALK, pt.left_walk_width)
    return lp.Profile(slots, lp.ANCHOR_DIVIDE)


def _bases(counts, drop_sides):
    """Lane-numbering base per station for ONE direction.

    An integer lane count cannot say WHICH lane a decrease removes, so each station carries
    `drop_side`. For the ordinary KERB drop the numbering is already right -- the dying lane is the
    outermost, its id simply stops appearing, and `lane_profile.interpolate` treats an absent slot
    as width 0. A MEDIAN drop is the case that needs work: the station with the LOWER count must
    number its surviving lanes `F1, F2` rather than `F0, F1`, or interpolation will match the wrong
    pairs and slide the whole carriageway sideways instead of dropping the offside lane.

    The station with FEWER lanes is the one that declares the side, in both directions of change:
    it is the narrow end, and the question `drop_side` answers is "at the narrow end, which lane is
    missing?" -- which is meaningless to ask at the wide end."""
    n = len(counts)
    base = [0] * n
    for i in range(1, n):
        a, b = counts[i - 1], counts[i]
        if a == b:
            base[i] = base[i - 1]
            continue
        side = drop_sides[i] if b < a else drop_sides[i - 1]
        if side != pm.MEDIAN:
            base[i] = base[i - 1]
        else:
            base[i] = base[i - 1] + (a - b)
    shift = -min(base)
    return [b + shift for b in base]


def chain_profiles(points, is_loop=False):
    """A road chain -> one `Profile` per station, with ids that are STABLE along the whole chain.

    Returns `(profiles, bases)`; `bases[i] = (fwd_base, rev_base)`. Ids being stable along the
    chain is the entire reason lane merge, lane opening, one-way and aux tapering need no
    special-case code: two adjacent stations differ, and that difference IS the taper."""
    if not points:
        return [], []
    fb = _bases([int(p.lanes_fwd) for p in points], [p.drop_side_fwd for p in points])
    rb = _bases([int(p.lanes_bwd) for p in points], [p.drop_side_bwd for p in points])
    bases = list(zip(fb, rb))
    return [build_profile(p, f, r) for p, (f, r) in zip(points, bases)], bases


def _wrap_delta(counts, sides, base):
    a, b = counts[-1], counts[0]
    if a == b:
        want = base[-1]
    else:
        side = sides[0] if b < a else sides[-1]
        want = base[-1] + ((a - b) if side == pm.MEDIAN else 0)
    return want - base[0]


def loop_base_mismatch(points):
    """For a closed loop the wrap link must agree with the numbering the chain already fixed.
    Returns `(fwd_delta, rev_delta)`; `(0, 0)` means the ring closes on itself. Non-zero means the
    artist authored a lane change that never comes back, so the ring's last station cannot hand its
    lanes to its first -- a real defect the gate reports, never something to silently renumber."""
    if len(points) < 2:
        return (0, 0)
    out = []
    for cf, sf in (("lanes_fwd", "drop_side_fwd"), ("lanes_bwd", "drop_side_bwd")):
        counts = [int(getattr(p, cf)) for p in points]
        sides = [getattr(p, sf) for p in points]
        out.append(_wrap_delta(counts, sides, _bases(counts, sides)))
    return tuple(out)


def profile_set(points, is_loop=False, fractions=None):
    """The chain as a `lane_profile.ProfileSet`, ready for `lane_runs` / `marking_runs`."""
    profiles, _bs = chain_profiles(points, is_loop)
    if fractions is None:
        n = max(1, len(profiles) - 1)
        fractions = [i / float(n) for i in range(len(profiles))]
    return lp.ProfileSet(profiles, fractions)


def aux_edge_offset(profile, direction=None):
    """The GORE LINE: the outermost aux slot's edge on the THROUGH-LANE side, as a signed lateral
    offset from the station.

    Single owner of "where does the ramp have to meet the mainline" -- 2.4's whole constraint is
    that the ramp's inboard band edge coincides with THIS value, so `Align Ramp To Aux`, the gate
    that reports the residual and the gore mesh must not each work it out for themselves.

    IT USED TO RETURN THE OUTBOARD EDGE, and that one sign is why a demo exit read as a ramp glued
    to the side of the road. On the outboard edge the ramp is a lane BEYOND the aux lane -- the
    carriageway is three lanes, then a fourth that stays, then a fifth that leaves -- so the aux
    lane tapers uselessly back into the mainline and the thing the artist is looking at genuinely
    is a separate road touching this one. Anchored on the through-lane side instead, the aux slot
    IS the exit lane and the ramp is its continuation: the outermost forward lane at the gore
    becomes the ramp, which is what a parallel-type exit is and what the artist expects to see.

    Case-free on purpose: the anchor is whichever of the slot's two edges is NEARER the standard
    travel lanes, so a kerb-side exit and an offside (`aux_side = MEDIAN`) exit both resolve with
    no side table to keep in step with `build_profile`. Returns None when the station declares no
    aux lane. Note it ASKS `slot_edges`; it does not compute a position."""
    got = aux_block(profile, direction)
    return None if got is None else got[0]


def aux_block(profile, direction=None):
    """`(gore_offset, far_offset, direction)` -- the WHOLE auxiliary block on the side a ramp takes,
    as signed lateral offsets: the edge against the through lanes, the edge away from them, and the
    travel direction of the slots.

    THE BLOCK, NOT ONE SLOT, and that is the correction. With `aux_fwd = 2` the exit is two lanes
    wide and the ramp continues BOTH of them, so its inboard edge belongs on the INNERMOST aux
    slot's inner edge. Anchoring on the outermost slot put a two-lane ramp half on the carriageway
    and half off the pavement -- user-reported, and invisible at `aux_fwd = 1` where the two
    answers coincide.

    Case-free about which side: the block is the aux slots on the side of the profile with the most
    of them (ties to FWD), and the gore edge is whichever of the block's two edges is nearer THAT
    DIRECTION's standard travel lanes -- which resolves a kerb-side and an offside
    (`aux_side = MEDIAN`) exit with no side table to keep in step with `build_profile`.

    `direction` NAMES THE CARRIAGEWAY WHEN THE STATION HAS TWO (8l). A station that hands a ramp
    to each carriageway -- one leaving on the forward side, one joining on the reverse side, which
    is the ordinary half-interchange -- declares `aux_fwd` AND `aux_bwd`, and the "most slots, ties
    to FWD" reading then answers FWD for both of them. The reverse ramp's mouth was placed on the
    forward carriageway's gore line, on the wrong side of the road. Callers that know which ramp
    they are asking about pass its side (`point_solve.ramp_carriageway`); callers that do not keep
    the old reading, which is right wherever only one carriageway declares a block."""
    groups = {}
    for i, s in enumerate(profile.slots):
        if s.kind == lp.AUX:
            groups.setdefault(s.dir, []).append(i)
    if not groups:
        return None
    if direction is not None:
        if direction not in groups:
            return None
        d = direction
    else:
        d = max(sorted(groups), key=lambda k: (len(groups[k]), k == lp.FWD))
    aux = groups[d]
    edges = [e for i in aux for e in lp.slot_edges(profile, i)]
    lo, hi = min(edges), max(edges)
    std = [lp.slot_offset(profile, j) for j, s in enumerate(profile.slots)
           if s.is_drivable() and s.kind != lp.AUX and s.dir == d]
    if not std:
        std = [lp.slot_offset(profile, j) for j, s in enumerate(profile.slots)
               if s.is_drivable() and s.kind != lp.AUX]
    ref = (sum(std) / len(std)) if std else 0.0
    return (lo, hi, d) if abs(lo - ref) <= abs(hi - ref) else (hi, lo, d)


def aux_slot_ids(profile, direction=None):
    """The slot ids of the whole aux block, innermost first -- the lanes a ramp takes with it.

    ONE OWNER for "which lanes leave", shared by the exporter's hand-off table and the gate's
    check that no two ramps on one run claim the same one. `aux_block` already answers WHICH side
    and how wide; this is the same answer expressed as the ids the lane graph is keyed on."""
    got = aux_block(profile, direction)
    if got is None:
        return []
    rev = got[2] != lp.FWD
    n = sum(1 for s in profile.slots if s.kind == lp.AUX and s.dir == got[2])
    return [(aux_rev_id(i) if rev else aux_fwd_id(i)) for i in range(n)]


def aux_slot_gores(profile, direction=None):
    """`[(slot_id, gore_edge, width)]` for the aux block, INNERMOST FIRST.

    `aux_edge_offset` is this list's first entry, and that is the relationship: the block's gore
    line is the innermost slot's inner edge, and every slot in it has one of its own. A station
    that hands its block to more than one ramp needs the per-slot answer -- the second ramp's
    inboard edge belongs on the second slot's inner edge, not on the block's.

    The edge is READ off `lane_profile.slot_edges` rather than accumulated from widths, so a block
    whose slots differ in width (or one taper-narrowed at this station) cannot drift."""
    got = aux_block(profile, direction)
    if got is None:
        return []
    lo, hi, _d = got
    outward = 1.0 if hi >= lo else -1.0
    out = []
    for sid in aux_slot_ids(profile, direction):
        k = profile.index_of(sid)
        if k is None:
            continue
        e = lp.slot_edges(profile, k)
        out.append((sid, min(e) if outward > 0 else max(e), profile.slots[k].width))
    return out


def aux_slot_span(profile):
    """`(gore_edge, far_edge, width)` of the whole aux block -- what the gore mesh needs to know how
    wide the departing lanes are where they leave."""
    got = aux_block(profile)
    if got is None:
        return None
    a, b, _d = got
    return a, b, abs(b - a)


def run_end_axes(net, points):
    """`{uid: (x, y)}` for a run's FIRST and LAST station -- the direction each of them faces
    according to the CHAIN, which is not what a run alone can see.

    A RUN IS NOT A ROAD, AND ITS END IS NOT AN END. `road_points.chain_tangents` falls back to the
    one available chord at an open chain's end ("extrapolating a phantom neighbour invents
    curvature the author did not author"), which is exactly right for a road that stops and
    exactly wrong for a JUNCTION MOUTH: the road does not stop there, it carries on across the pad,
    and the pad's cap is cut on `point_model.station_axis` -- the central difference over the
    CHAIN, which reaches the mouth on the other side. So the two are two owners of "which way does
    this station face", agreeing only while the road runs straight through the crossing.

    Measured on the island before this existed: at Rinkai x Kuko the cap was cut at 16.07 deg and
    the carriageway at 28.3 deg, leaving the road's own kerb line **2.23 m** off the pad corner it
    has to meet -- a visible notch, at every crossing a road bends through. It is 8f.1's rule
    ("direction has ONE owner: `station_axis`") applied to the last place that had not delegated.

    Only the two ends are answered: every interior station's central difference is the same
    calculation on the same neighbours, so `chain_tangents` already agrees there."""
    out = {}
    if net is None or len(points) < 2:
        return out
    for p in (points[0], points[-1]):
        if p.tangent_mode != pm.AUTO:
            continue                      # a MANUAL station already owns its direction
        a = pm.station_axis(net, p.uid)
        if a is not None:
            out[p.uid] = (float(a[0]), float(a[1]))
    return out


def stations(points, is_loop=False, end_axes=None):
    """PointData chain -> `road_points.Station` chain: the one bridge from the authored model into
    the pure geometry libs. Export, solve and the gate all cross here, so the profile ids they see
    are the same ids by construction.

    `end_axes` (`run_end_axes`) overrides the plan-view direction of the run's end stations, so a
    run that ends at a junction mouth is cut on the same plane the pad's cap is."""
    profiles, _bases = chain_profiles(points, is_loop)
    end_axes = end_axes or {}
    out = []
    for p, prof in zip(points, profiles):
        # THE BRIDGE. This line used to read `tangent = None`, unconditionally -- which made
        # `tangent_mode = MANUAL` and both handle lengths dead state: declared in the field table,
        # honoured by `road_points`, and never reachable, so rotating a point did nothing.
        # `PointData.tangent` carries the Empty's own +Y (see `point_model.facing_of`), and it is
        # None on any point the artist has not shaped -- which is exactly AUTO's input.
        mode, tan = p.tangent_mode, p.tangent
        ax = end_axes.get(p.uid)
        if ax is not None:
            # THE Z SLOPE IS THE RUN'S OWN. `station_axis` is a plan-view frame by design, and
            # forcing a flat tangent here would put a kink in the vertical profile of every graded
            # approach -- the bay bridge's, the touge's. So the XY comes from the chain and the
            # climb comes from the chord this run actually leaves on.
            dz = 0.0
            other = points[1] if p is points[0] else points[-2]
            d = (other.pos[0] - p.pos[0], other.pos[1] - p.pos[1], other.pos[2] - p.pos[2])
            flat = math.hypot(d[0], d[1])
            if flat > 1e-9:
                dz = (d[2] / flat) * (1.0 if p is points[0] else -1.0)
            mode, tan = pm.MANUAL, (ax[0], ax[1], dz)
        out.append(rp.Station(p.pos, prof, tangent_mode=mode, tangent=tan,
                              roll=float(p.roll), name=p.uid,
                              handle_in=float(p.handle_in), handle_out=float(p.handle_out)))
    return out


def chain_facings(net, resolve=True):
    """`{uid: (x, y, z)}` -- the direction each point's ARROW should show when the tool owns it.

    THE ONE OWNER of "which way does this station face". The stations are forced to `AUTO` before
    the tangents are taken, so "face the road" means the CHAIN's direction even for a point that is
    already MANUAL -- otherwise re-facing a hand-rotated point would just hand back its own current
    rotation and the re-straighten gesture could never work.

    Pure: no `bpy`. `point_ops.sync_facings` is the half that writes."""
    out = {}
    for road in net.roads.values():
        chain = [u for u in road.points if u in net.points]
        if len(chain) < 2:
            continue
        pts = [(net.resolved(u) if resolve else net.points[u]) for u in chain]
        sts = stations(pts, road.is_loop)
        for st in sts:
            st.tangent_mode, st.tangent = rp.AUTO, None
        tans = rp.chain_tangents(sts, road.is_loop)
        for uid, pair in zip(chain, tans):
            out[uid] = tuple(pair[1])
    return out


def centreline_runs(net, step=None):
    """`[(road_name, [pos, ...]), ...]` -- the RESOLVED centreline of every chain run.

    Cheap on purpose: `resample` only, no profile widths, no support, no ground raycast -- this
    feeds the viewport overlay, which redraws per region per frame while a point is being rotated.
    A run ends wherever two chain-adjacent points lack a SEGMENT link, exactly as
    `point_solve.road_runs` splits it (a pad bridges that gap; a ribbon must not)."""
    out = []
    for road in net.roads.values():
        uids = [u for u in road.points if u in net.points]
        if not uids:
            continue
        runs, cur = [], [uids[0]]
        for a, b in zip(uids, uids[1:]):
            if net.points[a].has_link(b, pm.LINK_SEGMENT):
                cur.append(b)
            else:
                runs.append(cur)
                cur = [b]
        runs.append(cur)
        for run in runs:
            if len(run) < 2:
                continue
            is_loop = bool(road.is_loop) and len(runs) == 1
            rpts = [net.resolved(u) for u in run]
            sts = stations(rpts, is_loop,
                           end_axes=(None if is_loop else run_end_axes(net, rpts)))
            kw = {} if step is None else {"step": step}
            out.append((road.name, [s.pos for s in rp.resample(sts, is_loop, **kw)]))
    return out


# ------------------------------------------------------------------------------- self-test

def _p(**kw):
    kw.setdefault("lane_width", 3.5)
    kw.setdefault("median_width", 1.0)
    return pm.PointData(**kw)


def _offsets(profile, dir=None):
    return {s.id: round(lp.slot_offset(profile, i), 3)
            for i, s in enumerate(profile.slots) if dir is None or s.dir == dir}


def self_test():
    ok = 0

    # -- the divide is s = 0, and lanes expand either way from the point ------------------------
    pr = build_profile(_p(lanes_fwd=2, lanes_bwd=2))
    off = _offsets(pr)
    assert off["MED"] == 0.0, "the median straddles the divide"
    assert off["F0"] == 2.25 and off["F1"] == 5.75
    assert off["R0"] == -2.25 and off["R1"] == -5.75
    ext = lp.extents(pr)
    assert abs(ext[0] - ext[1]) < 1e-9, "a symmetric road is symmetric about the point"
    print("OK: the point IS the divide -- lanes expand either way, s = 0 on the median")
    ok += 1

    # -- one-way ---------------------------------------------------------------------------------
    pr = build_profile(_p(lanes_fwd=3, lanes_bwd=0))
    assert lp.is_one_way(pr), "lanes_bwd = 0 is a one-way street"
    assert pr.slot(MED_ID) is None, "no median when only one direction carries lanes"
    assert abs(lp.total_width(pr) - 10.5) < 1e-9, "one-way sweeps SINGLE width, not double"
    print("OK: lanes_bwd = 0 gives a one-way road -- single width, no median (defect 1)")
    ok += 1

    # -- aux is ALWAYS outboard of the standard lanes --------------------------------------------
    pr = build_profile(_p(lanes_fwd=3, lanes_bwd=0, aux_fwd=1))
    off = _offsets(pr)
    assert off["AF0"] > off["F2"] > off["F1"] > off["F0"], "aux sits outboard of every standard lane"
    assert off["AF0"] == 12.25
    pr = build_profile(_p(lanes_fwd=3, lanes_bwd=0, aux_fwd=1, aux_side=pm.MEDIAN))
    off = _offsets(pr)
    assert off["AF0"] < off["F0"], "aux_side = MEDIAN is the offside exit -- aux inboard of F0"
    print("OK: aux lanes are outboard by default, inboard only for an offside exit")
    ok += 1

    # -- the gore line is the WHOLE aux BLOCK's inner edge, at any lane count ---------------------
    # `aux_fwd = 2` is a two-lane exit and the ramp continues BOTH, so its inboard edge belongs on
    # the innermost aux slot. Anchoring on the outermost put a two-lane ramp half on the
    # carriageway and half off the pavement -- and the two answers coincide at `aux_fwd = 1`,
    # which is why it shipped.
    for af in (1, 2, 3):
        pr = build_profile(_p(lanes_fwd=3, lanes_bwd=3, aux_fwd=af, median_width=2.0))
        assert abs(aux_edge_offset(pr) - 11.5) < 1e-9, (af, aux_edge_offset(pr))
        near, far, w = aux_slot_span(pr)
        assert abs(near - 11.5) < 1e-9 and abs(w - 3.5 * af) < 1e-9, (af, near, far, w)
    # An OFFSIDE exit resolves with the same code and no side table: the block sits between the
    # median and F0, so its gore edge is the one against F0, not the one against the median.
    pr = build_profile(_p(lanes_fwd=3, lanes_bwd=0, aux_fwd=2, aux_side=pm.MEDIAN))
    assert abs(aux_edge_offset(pr) - 7.0) < 1e-9, aux_edge_offset(pr)
    # ...and a REV block mirrors, sign and all.
    pr = build_profile(_p(lanes_fwd=3, lanes_bwd=3, aux_bwd=2, median_width=2.0))
    assert abs(aux_edge_offset(pr) + 11.5) < 1e-9, aux_edge_offset(pr)
    print("OK: the gore line is the aux BLOCK's through-lane edge -- 1, 2 or 3 lanes, either side")
    ok += 1

    # -- 3 -> 2 drop, both sides. These are the ROAD_POINT_GRAPH 2.1 measured numbers ------------
    a, b = _p(lanes_fwd=3, lanes_bwd=0), _p(lanes_fwd=2, lanes_bwd=0)
    prof, bases = chain_profiles([a, b])
    assert bases == [(0, 0), (0, 0)], "a KERB drop needs no renumbering"
    assert _offsets(prof[0]) == {"F0": 1.75, "F1": 5.25, "F2": 8.75}
    assert prof[1].slot("F2") is None, "the OUTERMOST lane is the one that dies on a KERB drop"

    b.drop_side_fwd = pm.MEDIAN
    prof, bases = chain_profiles([a, b])
    assert bases == [(0, 0), (1, 0)], "the narrow station renumbers so its median-side lane is gone"
    assert prof[1].slot("F0") is None and prof[1].slot("F1") is not None
    # The lanes that SURVIVE keep their ids and their positions relative to the divide; only the
    # median-side lane disappears. That is the offside exit, and it needs no lateral spine shift.
    assert _offsets(prof[1]) == {"F1": 1.75, "F2": 5.25}
    mid = lp.interpolate(prof[0], prof[1], 0.5)
    assert abs(mid.slot("F0").width - 1.75) < 1e-9, "F0 tapers away; F1/F2 hold their width"
    assert abs(mid.slot("F2").width - 3.5) < 1e-9
    print("OK: drop_side decides WHICH lane dies -- KERB drops F2, MEDIAN drops F0")
    ok += 1

    # -- an opening aux lane is three stations and no special case --------------------------------
    chain = [_p(lanes_fwd=3, lanes_bwd=0), _p(lanes_fwd=3, lanes_bwd=0, aux_fwd=1),
             _p(lanes_fwd=3, lanes_bwd=0, aux_fwd=1), _p(lanes_fwd=3, lanes_bwd=0)]
    ps = profile_set(chain)
    assert ps.stations == [0.0, 1 / 3.0, 2 / 3.0, 1.0]
    mid = ps.at(0.5)
    assert abs(mid.slot("AF0").width - 3.5) < 1e-9, "the aux is at full width across the buffer"
    quarter = ps.at(1 / 6.0)
    assert 0.0 < quarter.slot("AF0").width < 3.5, "aux 0 -> 1 IS the acceleration-lane taper"
    assert ps.at(1.0).slot("AF0") is None, "aux 1 -> 0 closes it again"
    print("OK: aux 0 -> 1 -> 1 -> 0 is the accel lane, its taper, its buffer and its close")
    ok += 1

    # -- markings survive a taper for free --------------------------------------------------------
    runs = lp.marking_runs(ps, 13)
    ids = {r["slot_id"] for r in runs}
    assert "AF0" in ids, "the lane LINE opens and closes with the lane it belongs to"
    print("OK: marking_runs paints the aux boundary only where the aux exists")
    ok += 1

    # -- a loop that does not close is reported, never silently renumbered -------------------------
    ring = [_p(lanes_fwd=2, lanes_bwd=2) for _ in range(4)]
    assert loop_base_mismatch(ring) == (0, 0), "a constant ring closes"
    ring[2].lanes_fwd = ring[3].lanes_fwd = 1
    ring[2].drop_side_fwd = pm.MEDIAN            # F0 dies; the ring now carries F1 only
    ring[3].drop_side_fwd = pm.MEDIAN            # ... and re-opens on the same side
    assert loop_base_mismatch(ring) == (0, 0), \
        "dropping offside and re-opening offside at the wrap is consistent -- the ring closes"
    ring[3].drop_side_fwd = pm.KERB              # ... but it re-opens on the KERB side instead
    assert loop_base_mismatch(ring) == (1, 0), \
        "a lane dropped offside and re-opened kerbside leaves the ring one lane out of register"
    print("OK: a loop whose numbering does not wrap is a reportable defect, not a rename")
    ok += 1

    print("\nALL SELF-TESTS PASSED (%d)" % ok)
    return True


if __name__ == "__main__":
    self_test()

"""The gate.

`ROAD_KIT_REDESIGN.md` 5a: **a build that fails the connectivity check is a failed build.** So the
gate is written FIRST and wired into Build, not left as an advisory console script that nobody runs.

Two rules shape every check in here:

* **Each failure names the OBJECT to fix**, because the artist fixes objects, not indices. A gate
  that reports "lane 47 of road 3" is a gate that gets ignored.
* **A gate that cannot pass is worse than no gate** (defect 11). Superelevation and sight distance
  are therefore WARN, not ERROR: this is a third-person shooter, ambient cars run a throttle
  governor and cannot perceive banking, and a check that fires on every hand-authored road and gets
  overridden every time is a dead check.
"""

import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "lib"))

import lane_profile as lp                                                    # noqa: E402
import road_points as rp                                                     # noqa: E402
import road_support as rs                                                    # noqa: E402

try:
    from . import point_model as pm, point_profile as pp                     # noqa: E402
except ImportError:
    import point_model as pm                                                 # noqa: E402
    import point_profile as pp                                               # noqa: E402

ERROR = 'ERROR'
WARN = 'WARN'

#: 2.1a. A merging lane's tail must land IN the receiving lane, not on the paint between them.
TAPER_ROUTE_TOL = 0.3
#: `LaneGraph.JUNCTION_RADIUS` -- a tail further than this from its successor's head never chains,
#: and `maintainTraffic` then reclaims the car as route-finished.
CHAIN_TOL = 4.5
#: A mouth whose axis disagrees with its road's tangent by more than this is a mis-placed arm.
MOUTH_ANGLE_TOL_DEG = 25.0


class Finding(object):
    __slots__ = ("code", "severity", "obj", "message")

    def __init__(self, code, severity, obj, message):
        self.code = code
        self.severity = severity
        self.obj = obj              # the uid / road name / collection the artist must go and fix
        self.message = message

    def __repr__(self):
        return "[%s] %s: %s -- %s" % (self.severity, self.code, self.obj, self.message)


#: The speed above which the merge-taper standard switches from its quadratic branch to its
#: linear one. 70 km/h, which is where the metric AASHTO/MUTCD form actually puts it -- it was 60
#: here, and that one number made every 60-70 km/h road demand a taper HALF AGAIN longer than the
#: standard asks (at 60 km/h and 3.5 m: 126 m against the correct 81 m). A gate that is stricter
#: than the book on a whole speed band is a gate people learn to override.
TAPER_LINEAR_ABOVE = 70.0

#: How far outboard the station AFTER a ramp's mouth must stand before the ramp counts as leaving.
#: Half a lane: less than that and the two bands have not parted anywhere the sweep can see.
MIN_DIVERGENCE = 1.75


def taper_min_length(width_change, design_speed, factor=1.0):
    """The taper length a lane drop of `width_change` metres wants at `design_speed` km/h.

    NOTHING about a taper is a length property -- the taper length IS the distance the artist put
    between two points. This is what the length is VALIDATED against, and it is derived from the
    design speed rather than being a flat constant, because a 3.5 m drop wants ~20 m in a car park
    and ~210 m on an expressway.

    The two branches are the metric standard: `L = W x S^2 / 155` up to 70 km/h, `L = 0.6 x W x S`
    above it. `factor` is the road's `taper_factor` -- see `ROAD_FIELDS`: the world is not 1:1, so
    a real-scale taper can eat a whole compressed district, and shortening it should be a visible
    authored decision rather than a constant quietly bent in here."""
    w, v = abs(float(width_change)), max(1.0, float(design_speed))
    base = 0.6 * w * v if v > TAPER_LINEAR_ABOVE else w * v * v / 155.0
    return base * max(0.0, float(factor))


def _dist_xy(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


# ------------------------------------------------------------------------------- identity + links

def check_identity(net, out):
    seen = {}
    for uid, p in sorted(net.points.items()):
        if not uid:
            out(Finding("uid_empty", ERROR, "<unnamed>", "a point carries no uid"))
            continue
        if uid in seen:
            out(Finding("uid_duplicate", ERROR, uid, "duplicate uid -- run Repair Links"))
        seen[uid] = p
        for l in p.links:
            if l.target not in net.points:
                # The artist cannot see this in the outliner, which is exactly why it is reported
                # by name and why `Repair Links` exists as an actionable fix rather than advice.
                out(Finding("link_dangling", ERROR, uid,
                            "%s link to a point that no longer exists (%s)" % (l.type, l.target)))
    for uid in sorted(net.points):
        if net.road_of(uid) is None:
            out(Finding("point_orphan", ERROR, uid, "point belongs to no road collection"))


def check_links(net, out):
    for uid in sorted(net.points):
        p = net.points[uid]
        for l in p.links:
            t = net.points.get(l.target)
            if t is None:
                continue
            if l.type == pm.LINK_JUNCTION:
                if not t.has_link(uid, pm.LINK_JUNCTION):
                    out(Finding("junction_asymmetric", ERROR, uid,
                                "JUNCTION link to %s is not returned -- one pad would be built as "
                                "two overlapping ones" % l.target))
                for q, who in ((p, uid), (t, l.target)):
                    if q.role != pm.INTERSECTION:
                        out(Finding("junction_role", ERROR, who,
                                    "takes a JUNCTION link but its role is %s, not INTERSECTION"
                                    % q.role))
            elif l.type == pm.LINK_AUX:
                if not pm.is_ramp_role(t.role):
                    out(Finding("aux_role", ERROR, l.target,
                                "is the target of an AUX link but its role is %s, not RAMP"
                                % t.role))
                if t.has_link(uid):
                    out(Finding("aux_backlink", ERROR, l.target,
                                "a ramp point connects ONLY to the aux slot -- it must not link "
                                "back to the mainline point"))
                res = net.resolved(uid)
                if res.aux_fwd <= 0 and res.aux_bwd <= 0:
                    out(Finding("aux_no_slot", ERROR, uid,
                                "carries an AUX link but declares no aux lane for the ramp to "
                                "align to"))
            elif l.type == pm.LINK_SEGMENT and not t.has_link(uid, pm.LINK_SEGMENT):
                out(Finding("segment_asymmetric", ERROR, uid,
                            "SEGMENT link to %s is not returned" % l.target))


def check_chains(net, out):
    """Adjacency in the ordered chain and adjacency in the link graph must agree. They are two
    statements of the same fact, and when they disagree the build follows the links while the
    artist is reading the order."""
    for name in sorted(net.roads):
        r = net.roads[name]
        uids = [u for u in r.points if u in net.points]
        if len(uids) < 2:
            if len(uids) < 1:
                out(Finding("road_empty", WARN, name, "road has no points"))
            continue
        # A through road contributes TWO mouths to a crossing -- one either side -- and what joins
        # them is the PAD, not a carriageway. So chain adjacency across a junction is expressed by
        # the JUNCTION link, and requiring a SEGMENT link here would have forced every crossing to
        # split its street (redesign defect 3 in a new hat). `point_model.road_corridors` is the
        # one owner of that rule; a break between two corridors is what this reports, and it is
        # the same break `Tidy Roads` files into separate collections.
        corridors = pm.road_corridors(net, r)
        for prev, nxt in zip(corridors, corridors[1:]):
            a, b = prev[-1], nxt[0]
            # WARN, NOT ERROR, and the downgrade has a rule behind it. `point_solve.road_runs`
            # already treats an unlinked pair as a RUN BOUNDARY and the build, the export and the
            # gore all handle it -- a collection holding two separate runs (a ramp authored inside
            # its mainline's road, the usual way this happens) builds correctly and is untidy, not
            # broken. What IS broken is a point joined to nothing at all, and that now arrives
            # under its own name below instead of being folded in here, where it read as an
            # ordering complaint about the wrong object.
            out(Finding("chain_unlinked", WARN, a,
                        "is chain-adjacent to %s in %s but carries no SEGMENT or JUNCTION link "
                        "to it, so %s is two roads sharing one collection -- if that was not "
                        "intended, link them; otherwise Author > Repair > Tidy Roads files the "
                        "second one into its own road" % (b, name, name)))
        # THE NAME ORDER AND THE LINK ORDER MUST BE THE SAME ORDER. The chain is read off the
        # object names and the road is built off the links, so when a hand edit puts them at odds
        # -- a rename, a duplicate, a point dragged between collections -- the build follows one
        # while the artist reads the other, and nothing says so. A WARN, because the build is not
        # wrong (the links are the truth and it follows them); what is wrong is that the file no
        # longer reads the way it behaves, and the remedy is one button.
        comps, tangled = pm.link_order(net, r)
        if tangled:
            out(Finding("chain_branched", WARN, tangled[0],
                        "is in a stretch of %s whose links BRANCH, so it has no chain order at "
                        "all -- three points meeting at one station is a junction, not a chain. "
                        "Renumber Roads leaves it alone; disconnect the odd link or Make "
                        "Intersection it" % name))
        flat = [u for c in comps for u in c]
        if flat and flat != [u for u in uids if u in set(flat)]:
            out(Finding("chain_out_of_order", WARN, flat[0],
                        "%s's points are named in a different order from the one its own links "
                        "put them in, so the chain you read is not the chain that gets built -- "
                        "Author > Repair > Renumber Roads renames them to match" % name))
        for u in uids:
            if not net.points[u].links:
                out(Finding("point_stranded", ERROR, u,
                            "is in %s with %d other point(s) and is joined to none of them -- a "
                            "hole in the chain, or a link that was cut by mistake"
                            % (name, len(uids) - 1)))
        if r.is_loop:
            if not net.points[uids[-1]].has_link(uids[0], pm.LINK_SEGMENT):
                out(Finding("loop_open", ERROR, uids[-1],
                            "%s is marked is_loop but the wrap SEGMENT link is missing" % name))
            df, dr = pp.loop_base_mismatch([net.resolved(u) for u in uids])
            if df or dr:
                out(Finding("loop_lane_register", ERROR, name,
                            "the ring's lane numbering does not wrap (fwd %+d, rev %+d) -- a lane "
                            "dropped on one side and re-opened on the other" % (df, dr)))


#: Two stations closer together than this are the same PLACE as far as a car is concerned: it is
#: `CHAIN_TOL`, the distance beyond which one lane never chains into the next.
MEET_TOL = CHAIN_TOL


def check_meetings(net, out):
    """A road that ENDS ON another road must be JOINED to it, and nothing else could see this.

    Every other check in this file reads the authored graph and asks whether it is consistent. A
    junction that was never authored at all is consistent with itself: two roads whose stations
    sit at the IDENTICAL position with no link between them pass `check_links` (no link to be
    asymmetric), `check_chains` (each chain is whole) and `check_junctions` (no clique to be
    incomplete), and the only symptom is that no car can get from one to the other -- which is
    invisible until you drive it.

    That is not hypothetical. `WORLD_REBUILD_PLAN.md` `W14`: a float-comparison bug in
    `seed_district_roads.seg_intersect` silently dropped four of the island's sixteen crossings,
    and the island shipped with the airport link lying on Hama-dori's carriageway unattached and
    the harbour apron as an unreachable component whose first station is exactly on Port road's
    last one -- gate 0 errors, 0 warnings. This is the eye that was missing.

    ONLY OPEN ENDS, and in 3D. A road that merely passes near another one is not a defect, and a
    street that runs UNDER an expressway is separated in Z by the deck -- so the subject is a
    point where a road STOPS (one SEGMENT link or none, and no junction or ramp business of its
    own), and the test is a real distance, not a plan one. A WARN, like `chain_unlinked`, and for
    the same reason: what is wrong is that the file no longer reads the way it behaves, and the
    two remedies are both one button."""
    ramp_heads = {t for _m, t in net.aux_pairs()}
    owner = {}
    for name in sorted(net.roads):
        for u in net.roads[name].points:
            owner[u] = name
    ends = []
    for uid in sorted(owner):
        p = net.points.get(uid)
        if p is None or uid in ramp_heads:
            continue
        if p.targets(pm.LINK_JUNCTION) or p.targets(pm.LINK_AUX):
            continue
        if len(p.targets(pm.LINK_SEGMENT)) <= 1:
            ends.append(uid)
    for uid in ends:
        p = net.points[uid]
        for other in sorted(owner):
            if owner[other] == owner[uid] or other == uid:
                continue
            q = net.points.get(other)
            if q is None or p.has_link(other) or q.has_link(uid):
                continue
            if math.dist(tuple(p.pos), tuple(q.pos)) > MEET_TOL:
                continue
            out(Finding("road_end_unjoined", WARN, uid,
                        "is where %s stops, and it stands %.2f m from %s in %s with no link to "
                        "it -- the two roads touch and no car can cross between them. Connect "
                        "Selected (SEGMENT) if they are one corridor, or Make Intersection if "
                        "this is a corner or a third arm meets here"
                        % (owner[uid], math.dist(tuple(p.pos), tuple(q.pos)), other,
                           owner[other])))
            break


# ------------------------------------------------------------------------------- taper

def check_tapers(net, out):
    """THE TAPER IS PER CARRIAGEWAY, AND ONLY WITHIN ONE RUN.

    Two things this used to get wrong, both of them the same mistake -- measuring a taper across a
    boundary a driver never crosses:

    * **Both sides summed.** A lane opening on the left and a lane already open on the right were
      added together, so declaring `aux_fwd` on a road that already had `aux_bwd` doubled the
      demanded length. Nobody drives both carriageways: a merge is a lateral move made by ONE
      driver on ONE side of the divide, so what the standard is about is the change on THAT side.
      The demand is therefore the WIDER of the two sides' changes, never their sum.
    * **Across a run break.** The chain is walked pairwise, but two chain-adjacent points with no
      SEGMENT link between them are not joined by carriageway at all -- a junction pad bridges
      them, or they are two separate runs sharing a collection. Measuring a taper over that gap
      demanded a merge length for a stretch of road that does not exist. Runs come from
      `point_solve.road_runs`, the same owner the build and the export use."""
    try:
        from . import point_solve as psolve
    except ImportError:
        import point_solve as psolve                                         # noqa: E402
    for name in sorted(net.roads):
        r = net.roads[name]
        pts = [net.resolved(u) for u in r.points if u in net.points]
        if len(pts) < 2:
            continue
        profiles, _b = pp.chain_profiles(pts, r.is_loop)
        at = {p.uid: k for k, p in enumerate(pts)}
        pairs = []
        for run in psolve.road_runs(net, r):
            idx = [at[u] for u in run if u in at]
            pairs.extend(zip(idx, idx[1:]))
        if r.is_loop and len(pts) > 2:
            pairs.append((len(pts) - 1, 0))
        for i, j in pairs:
            a, b = pts[i], pts[j]
            span = _dist_xy(a.pos, b.pos)
            if span <= 1e-6:
                out(Finding("station_coincident", ERROR, a.uid,
                            "is at the same position as %s -- a zero-length taper" % b.uid))
                continue
            # Per SIDE of the divide, and the demand is the wider of the two -- see the docstring.
            wa, wb = lp.paved_extents(profiles[i]), lp.paved_extents(profiles[j])
            dw = max(abs(wa[0] - wb[0]), abs(wa[1] - wb[1]))
            if dw <= 1e-6:
                continue
            want = taper_min_length(dw, min(a.design_speed, b.design_speed), r.taper_factor)
            if span >= want:
                continue
            # A LANE THAT DEPARTS IS NOT A LANE THAT MERGES, and this rule only knows about the
            # second one. `taper_min_length` is the merge taper: the length a driver needs to move
            # sideways into moving traffic. At an exit gore nobody moves sideways -- the aux lane
            # leaves on the ramp and the mainline's edge simply returns to the through-lane edge,
            # at gore rates, over a few tens of metres. Applying the merge rule there forces the
            # aux slot to taper for hundreds of metres PAST the ramp, which is both wrong on the
            # ground (a fourth lane that goes nowhere) and the thing that makes the ramp's band
            # overlap the mainline's for the whole of it. So a width change at a station that
            # hands its aux slot to a ramp is exempt -- and it is exempt only there, only for the
            # station that owns the AUX link.
            if a.targets(pm.LINK_AUX):
                continue
            # The factor that WOULD pass, spelled out. "lower taper_factor" on its own leaves the
            # artist guessing at a number the gate already knows, and guessing low is how a road
            # ends up with a taper an order of magnitude short of what it could have had.
            fits = r.taper_factor * span / want if want > 1e-9 else 0.0
            out(Finding("taper_too_short", ERROR, a.uid,
                        "%.2f m of paved width changes over %.1f m; at %.0f km/h it wants %.0f m "
                        "-- move %s further away, or set the road's taper_factor to %.2f or less "
                        "(now %.2f)"
                        % (dw, span, min(a.design_speed, b.design_speed), want, b.uid,
                           fits, r.taper_factor)))


def check_taper_routes(net, out):
    """2.1a, with the numbers. Lane WIDTHS interpolate for free; lane ROUTES do not, and this is
    the check that keeps that fix honest -- a merging lane whose centreline ends on the lane line
    finishes the merge straddling the paint, and one whose tail is beyond `CHAIN_TOL` from its
    successor's head never chains at all."""
    for name in sorted(net.roads):
        r = net.roads[name]
        pts = [net.resolved(u) for u in r.points if u in net.points]
        if len(pts) < 2:
            continue
        st = pp.stations(pts, r.is_loop)
        samples = rp.resample(st, r.is_loop)
        routes = rp.lane_taper_route(st, samples, r.is_loop)
        by_id = {rt.slot_id: rt for rt in routes}
        n = len(samples)
        for rt in routes:
            if rt.merge_into:
                recv = by_id.get(rt.merge_into)
                if recv is None:
                    out(Finding("merge_target_missing", ERROR, name,
                                "lane %s merges into %s, which has no route"
                                % (rt.slot_id, rt.merge_into)))
                    continue
                # Both routes span the WHOLE chain (that is 2.1a rule 3), so they are indexed by
                # sample and the comparison is at the far end -- where the merge completes and
                # where `LaneGraph` will look for the successor's head.
                d = _dist_xy(rt.points[-1], recv.points[-1])
                if True:
                    if d > TAPER_ROUTE_TOL:
                        out(Finding("taper_route_offset", ERROR, name,
                                    "lane %s ends %.2f m from the centreline of %s it merges into "
                                    "(tolerance %.2f m)"
                                    % (rt.slot_id, d, rt.merge_into, TAPER_ROUTE_TOL)))
                    if d > CHAIN_TOL:
                        out(Finding("taper_route_unchained", ERROR, name,
                                    "lane %s tail is %.2f m from its successor's head -- beyond "
                                    "LaneGraph.JUNCTION_RADIUS, so it never chains"
                                    % (rt.slot_id, d)))
            # The run must span the FULL station-to-station distance; LANE_MIN_WIDTH truncating it
            # ~20 % short is defect three of 2.1a.
            # 2.1a rule 3: the route is emitted over the FULL station-to-station span, never
            # truncated by LANE_MIN_WIDTH -- a polyline that stops a fifth of the taper short is
            # well outside LaneGraph's 4.5 m radius and simply never chains.
            if len(rt.points) != n:
                out(Finding("lane_route_truncated", ERROR, name,
                            "lane %s has %d route points for %d samples -- the run was truncated"
                            % (rt.slot_id, len(rt.points), n)))


# ------------------------------------------------------------------------------- junction + ramp

#: THE STATION'S TRAVEL DIRECTION -- delegated, never re-derived. The gate measured the mouth
#: angle and the ramp residual against a chord while the pad and the carriageway used the authored
#: rotation, so a rotated mouth could be green here and visibly wrong in the viewport. One owner:
#: `point_model.station_axis`.
_axis = pm.station_axis


def check_junctions(net, out):
    for comp in net.junction_cliques():
        jid = comp[0]
        if len(comp) < 3:
            out(Finding("junction_too_small", WARN, jid,
                        "a pad of %d arms is a segment connection, not a junction" % len(comp)))
        # A COMPONENT is not yet a CLIQUE. A missing mutual link must arrive here as a reportable
        # defect rather than silently splitting one pad into two that then overlap.
        for a in comp:
            missing = [b for b in comp
                       if b != a and not net.points[a].has_link(b, pm.LINK_JUNCTION)]
            if missing:
                out(Finding("junction_incomplete", ERROR, a,
                            "is in a pad with %s but not linked to %s -- Make Intersection writes "
                            "the full clique" % (", ".join(comp), ", ".join(missing))))
        cx = sum(net.points[u].pos[0] for u in comp) / len(comp)
        cy = sum(net.points[u].pos[1] for u in comp) / len(comp)
        for u in comp:
            p, res = net.points[u], net.resolved(u)
            if _dist_xy(p.pos, (cx, cy)) < 1e-6:
                out(Finding("junction_degenerate", ERROR, u,
                            "sits on the pad centroid -- the mouth has no bearing"))
            ax = _axis(net, u)
            if ax is not None:
                to_c = (cx - p.pos[0], cy - p.pos[1])
                m = math.hypot(*to_c)
                if m > 1e-6:
                    cosang = abs((ax[0] * to_c[0] + ax[1] * to_c[1]) / m)
                    ang = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
                    if ang > MOUTH_ANGLE_TOL_DEG:
                        out(Finding("mouth_angle", WARN, u,
                                    "the mouth axis is %.0f deg off the line to the pad centre; "
                                    "the approach will kink" % ang))
            if res.lanes_fwd <= 0 and res.lanes_bwd <= 0:
                out(Finding("junction_no_lanes", ERROR, u, "a junction arm with no lanes"))


#: How much clear carriageway a junction mouth needs beyond it, in metres, before the next
#: station along its own run. It is `seed_district_roads.MIN_SPAN`, restated on this side of the
#: fence: "anything closer is not a span, it is a duplicate point wearing a station's name".
MIN_MOUTH_CLEAR = 10.0


def check_mouth_clearance(net, out):
    """A MOUTH NEEDS CLEAR ROAD BEYOND IT, and until now nothing could see when it did not.

    The stop line is where the pad hands over to the street, so the first span of that street is
    where the cross-section is interpolated from the junction's numbers to the road's. Put an
    ordinary station a few centimetres past a mouth and that interpolation happens over nothing:
    the two profiles are the same (an `INHERIT` station takes the road's base, and so does the
    mouth), so `check_tapers` sees `dw == 0` and returns before it ever measures the span, and
    `station_coincident` only fires at 1e-6. A 0.19 m span therefore passed the whole gate.

    That is not hypothetical -- it is what the island shipped. `seed_district_roads` places every
    mouth at a provisional 14 m and prunes the stations inside that window; `Auto Setback` then
    solves the real distance (17.9-35.5 m on the island's ten pads) and moves the mouth out over
    the top of stations that had survived a window computed from the wrong number. Rinkai-dori's
    mouth at the Chuo crossing ended up 0.19 m from the next station along. The seeder now asks
    `point_solve.solved_setback` for the same number, so it prunes the right window; this check is
    what keeps that honest, and it also catches the hand-authored version (drag a mouth outward
    and it walks over the station beyond it just the same).

    Measured along the RUN, not the whole chain: a mouth's other neighbour is across the pad, and
    the pad is exactly where a short gap is meant to be."""
    try:
        from . import point_solve as psolve
    except ImportError:
        import point_solve as psolve                                         # noqa: E402
    junction = set()
    for comp in net.junction_cliques():
        junction.update(comp)
    for name in sorted(net.roads):
        r = net.roads[name]
        for run in psolve.road_runs(net, r):
            for a, b in zip(run, run[1:]):
                if (a in junction) == (b in junction):
                    continue        # pad-to-pad, or street-to-street: not a stop line
                mouth, station = (a, b) if a in junction else (b, a)
                if mouth not in net.points or station not in net.points:
                    continue
                span = _dist_xy(net.points[mouth].pos, net.points[station].pos)
                if span >= MIN_MOUTH_CLEAR:
                    continue
                sev = ERROR if span < CHAIN_TOL else WARN
                out(Finding("station_crowds_mouth", sev, station,
                            "stands %.2f m beyond the junction mouth %s, which wants %.0f m of "
                            "clear road -- the mouth was set back over the top of it. Delete this "
                            "station, or lock the mouth (setback_locked) and place it by hand"
                            % (span, mouth, MIN_MOUTH_CLEAR)))


def check_ramps(net, out):
    """The constraint is EDGE ALIGNMENT, not a pad (2.4) -- but alignment of a swept band is a fact
    about the whole CUT, not one point on it, so two numbers are measured and both are reported:

    * the station residual -- how far the ramp's mouth is from the station `point_solve.ramp_target`
      says it belongs at (lateral, longitudinal and vertical at once);
    * the divergence angle -- how far the ramp's heading is from the mainline's THERE. A ramp
      snapped to the right point while still facing 30 degrees away has its cross-section cut on a
      different plane, so the two bands touch at exactly one vertex and open from the next one.
      That is the "ramp stuck on the side" case, and it used to be invisible to this gate.

    Both come from `point_solve`, which is also what `Align Ramp To Aux` moves the point with and
    what the gore mesh is built from -- so the operator, the gate and the geometry cannot disagree."""
    try:
        from . import point_solve as psolve
    except ImportError:
        import point_solve as psolve                                         # noqa: E402
    for main_uid, ramp_uid in net.aux_pairs():
        if ramp_uid not in net.points:
            continue
        got = psolve.ramp_residual(net, main_uid, ramp_uid)
        if got is None:
            continue
        residual, angle = got
        if residual > 0.5:
            out(Finding("ramp_edge_residual", ERROR, ramp_uid,
                        "the ramp mouth is %.2f m from the gore line of %s -- run Align "
                        "Ramp To Aux" % (residual, main_uid)))
        if angle > psolve.GORE_MAX_DIVERGE_DEG:
            out(Finding("ramp_diverge_angle", WARN, ramp_uid,
                        "leaves %s at %.0f deg -- a parallel-type exit diverges 2-5 deg, so this "
                        "gore opens at the mouth instead of gradually. Run Align Ramp To Aux "
                        "(it faces the point down the mainline) and bend at the NEXT point"
                        % (main_uid, angle)))
        div = psolve.ramp_divergence(net, main_uid, ramp_uid)
        if div is None:
            continue
        outboard, along = div
        if outboard < -MIN_DIVERGENCE:
            # THE MOUTH CAN BE PERFECT AND THE RAMP STILL WRONG (8j). Both checks above measure
            # the mouth alone, and `Align Ramp To Aux` sets both of them -- so a ramp that leaves
            # correctly and then bends back THROUGH the carriageway passed the whole gate. The
            # two bands then overlap for the ramp's whole length, `solve_gore` finds no wedge, and
            # the artist sees no gore, no nose and no message.
            out(Finding("ramp_wrong_side", ERROR, ramp_uid,
                        "bends %.1f m back ACROSS %s instead of away from it -- the aux slot is "
                        "on the other side, so the ramp is driving through the lanes it is "
                        "leaving and no gore can be paved. Bend the station after the mouth "
                        "outboard, or move the aux slot to the other carriageway"
                        % (-outboard, main_uid)))
        elif outboard < MIN_DIVERGENCE and abs(along) > MIN_DIVERGENCE:
            out(Finding("ramp_parallel", WARN, ramp_uid,
                        "runs parallel to %s for %.0f m after the mouth -- it never parts, so "
                        "there is no gore to pave. Bend the station after the mouth outboard"
                        % (main_uid, abs(along))))


def check_aux_slots(net, out):
    """No two ramps on ONE RUN may take the same auxiliary slot.

    A REACHABILITY RULE WITH NO GEOMETRY (8j), and the second of its kind after 8f.4. A run
    exports ONE lane per slot -- `road_points.lane_taper_route` blends a slot's route across the
    whole run and `point_export.build_run` cuts it at the gore it is handed to. Two ramps on the
    same run therefore claim the same lane, the second hand-off overwrites the first, and exactly
    one of the two ramps ends up with a predecessor. The other is paved, exported, gate-green and
    unreachable from anywhere in the world.

    Two ramps on one road is not exotic -- an expressway with an exit and an entrance a few
    hundred metres apart is the ordinary case -- so this is reported by name, with the two ways
    out: put the second ramp on the OTHER carriageway (`aux_bwd` against `aux_fwd`, different slot
    ids), or break the run between them, which a junction already does.

    `point_profile.aux_slot_ids` is the one owner of which slots a ramp takes; the exporter's
    hand-off table asks the same function, so the gate and the export cannot disagree about what
    collides."""
    try:
        from . import point_solve as psolve
    except ImportError:
        import point_solve as psolve                                         # noqa: E402
    ramps_of = {}
    for m, r in net.aux_pairs():
        ramps_of.setdefault(m, []).append(r)
    for name in sorted(net.roads):
        for run in psolve.road_runs(net, net.roads[name]):
            claimed = {}
            # PER RAMP, not per station. One station MAY hand its block to several ramps -- a
            # two-lane exit that splits, a two-lane entrance fed by two ramps -- and
            # `point_solve.aux_allocation` divides the slots between them, so the collision to
            # look for is two ramps landing on the SAME slot, whatever station they hang off.
            for uid in run:
                res = net.resolved(uid)
                if res is None:
                    continue
                alloc = psolve.aux_allocation(net, uid)
                for ramp_uid in ramps_of.get(uid, ()):
                    side = psolve.ramp_side_of(net, uid, ramp_uid)
                    # PER CARRIAGEWAY (8l): a ramp on the forward side has no claim on the
                    # reverse side's block, and `AF*` / `AR*` ids keep the two apart by
                    # construction -- so a station handing one ramp to each is not a collision.
                    block = pp.aux_slot_ids(pp.build_profile(res), side)
                    mine = alloc.get(ramp_uid) or []
                    if not mine:
                        want = 0
                        r = net.resolved(ramp_uid)
                        if r is not None:
                            want = max(1, int(max(r.lanes_fwd, r.lanes_bwd)))
                        peers = [u for u in ramps_of[uid]
                                 if psolve.ramp_side_of(net, uid, u) == side]
                        out(Finding("aux_block_oversubscribed", ERROR, uid,
                                    "hands its %d-slot %s auxiliary block to %d ramp(s) and %s "
                                    "wants %d more lane(s) than are left -- widen the block "
                                    "(aux_fwd / aux_bwd) so every ramp has its own slots, or move "
                                    "this ramp to another station"
                                    % (len(block), "forward" if side == lp.FWD else "reverse",
                                       len(peers), ramp_uid, want)))
                        continue
                    for sid in mine:
                        if sid in claimed:
                            who, other = claimed[sid]
                            out(Finding("aux_slot_shared", ERROR, uid,
                                        "hands auxiliary slot %s to %s, but %s already hands it "
                                        "to %s on the same run of %s -- a run exports ONE lane "
                                        "per slot, so only one of the two ramps would be "
                                        "reachable by any car. Move this one to the other "
                                        "carriageway (aux_bwd against aux_fwd), widen the block "
                                        "so they take a slot each, or break the run between them"
                                        % (sid, ramp_uid, who, other, name)))
                        else:
                            claimed[sid] = (uid, ramp_uid)


def check_support(net, out):
    for uid in sorted(net.points):
        p = net.points[uid]
        if not p.has_ground_z:
            out(Finding("ground_unsampled", WARN, uid,
                        "no sampled ground_z -- Build samples it unconditionally, so this point "
                        "has never been built"))
            continue
        # A PIER with its columns switched off holds a deck on nothing. `pillar_skip` is the
        # per-station escape hatch for a bent that would land inside a building (3.3 rule 4), and
        # it is meant to be used with a neighbour picking the span up -- so this is a WARN naming
        # the point, not a refusal.
        kind = rs.support_kind(p.pos[2], p.ground_z)
        if kind == rs.SUPPORT_PIER and p.pillar_skip:
            out(Finding("pier_skipped", WARN, uid,
                        "is %.1f m above ground and has pillar_skip set -- the deck spans this "
                        "station on its neighbours' columns" % (p.pos[2] - p.ground_z)))


def check_pads(net, out):
    """THE TRIANGLE FAN'S PRECONDITION (2.2 step 4).

    A fan from the centroid tessellates a concave ring correctly IF AND ONLY IF the ring is
    star-shaped about that centroid. A mouth dragged closer to the centre than a neighbouring
    fillet breaks it and the pad folds over itself -- which reads in-game as a black crater, found
    by walking into it rather than by building. So the precondition is checked, in metres, and the
    finding names the pad.

    This check could not exist before the geometry solve did: it is a fact about the RESOLVED pad,
    not about the authored links, which is why it arrives with step 4 rather than step 1."""
    try:
        from . import point_solve as psolve
    except ImportError:
        import point_solve as psolve                                         # noqa: E402
    for comp in net.junction_cliques():
        try:
            j = psolve.solve_junction(net, comp)
        except Exception as exc:
            out(Finding("pad_unsolvable", ERROR, comp[0],
                        "the pad could not be solved: %r" % (exc,)))
            continue
        if j is None:
            continue
        if not j.star_ok:
            # WARN, NOT ERROR, and that downgrade is the point. The pad is now tessellated by
            # `pad_triangles`, which moves the fan's apex to a kernel point and ear-clips when
            # even that fails -- so a ring that folds still builds watertight. Refusing the whole
            # build over a 2 cm fold, and naming as the remedy an Auto Setback that then reports
            # "moved 0 mouth(es)", is exactly the dead gate rule 5 warns about. It still says so,
            # in metres, because a fold this deep usually does mean a mouth wants pulling out.
            out(Finding("pad_not_star_shaped", WARN, comp[0],
                        "the pad ring folds %.2f m past its own centroid -- built by ear-clipping "
                        "instead of a clean fan. Pull the mouth nearest the centre outward, or "
                        "run Auto Setback, for a tidier pad" % j.star_worst))
        area = 0.0
        n = len(j.boundary)
        for i in range(n):
            a, b = j.boundary[i], j.boundary[(i + 1) % n]
            area += a[0] * b[1] - b[0] * a[1]
        if abs(area) / 2.0 < 1.0:
            out(Finding("pad_degenerate", ERROR, comp[0],
                        "the pad encloses no area -- the mouths are coincident or collinear"))
        reached = {t["to"] for t in j.turns if t["ok"]} | {t["from"] for t in j.turns if t["ok"]}
        for m in j.mouths:
            if m.uid not in reached:
                out(Finding("mouth_unreachable", WARN, m.uid,
                            "no legal movement reaches or leaves this arm -- check its lane "
                            "counts, allow_cross and allow_uturn"))


# ------------------------------------------------------------------------------- the gate

def check_style(net, out):
    """Does every style slot name something this file actually has?

    A NAME THAT RESOLVES TO NOTHING IS SILENT. `point_style.resolve` falls back to the layer's
    default rather than building a black road or no kerb at all -- which is right, but means a
    typo, a renamed material, or a `road_kit.blend` that failed to link produces a road that looks
    merely wrong rather than broken. Warnings, not errors: falling back IS a working build, and a
    district should not refuse to bake because someone renamed a material."""
    try:
        from . import point_style as _pstyle
    except ImportError:
        import point_style as _pstyle                                        # noqa: E402
    for name, road in net.roads.items():
        for slot, kind, missing in _pstyle.resolve(road).missing():
            out(Finding("style_missing", WARN, name,
                        "%s %s '%s' is not in this file -- the %s layer falls back to its default"
                        % (slot, kind, missing, slot)))


#: How far a profile asset's own width may differ from the width the solve reserves for it before
#: it is reported, in metres. Tight: this shows up as a visible gap or an overhang, not as a
#: tolerance question.
ASSET_WIDTH_TOL = 0.05


def check_asset_width(net, out):
    """Does each profile asset's own width match the width the road reserves for it?

    THE SECTION IS SWEPT AT ITS OWN SIZE, BUT THE SOLVE STILL PLACES THE NEXT LAYER FROM THE
    AUTHORED ONE. So a 2.00 m footway section on a road whose `left_walk_width` reserves 3.00 m
    leaves a real 1.00 m of slack between the pavement's edge and the barrier beyond it, and a
    0.32 m kerb section overhangs a parametric 0.15 m kerb into where the footway begins. Measured
    on the shipped kit.

    This is the previous addon's round-2 defect one level up, and the reason it was worth an eye
    rather than a silent fallback: nothing about the built geometry LOOKS broken -- each piece is
    the right shape, correctly swept, in the right place for what it was told -- so the only
    symptom is a gap the artist finds by flying up to it.

    A WARNING, and the remedy is in the message: author the width to match the section you chose.
    Only the FOOTWAY is checked -- see the table below for why the kerb cannot be.
    (The structural fix -- the asset's measured width replacing the authored one inside
    `solve_road`, at the ONE place lateral offsets are computed -- is not built; see
    `point_style`'s module docstring.)"""
    try:
        from . import point_solve as _ps, point_style as _pstyle
    except ImportError:
        import point_solve as _ps                                            # noqa: E402
        import point_style as _pstyle                                        # noqa: E402
    for name, road in net.roads.items():
        base = getattr(road, "base", None)
        if base is None:
            continue
        style = _pstyle.resolve(road)
        #: `slot -> (what the road reserves for it, the field the artist would edit)`.
        #: THE FOOTWAY ONLY, and the omission is deliberate rather than an oversight. A kerb's
        #: reserved width is DERIVED (`kerb_height * KERB_THICKNESS`), so there is no field whose
        #: value would make a 0.32 m section fit -- "set kerb_height to 0.32" would be advice for a
        #: 64 cm kerb, and a finding that names a remedy which makes things worse is worse than no
        #: finding. The kerb case needs the structural fix (the section's width reaching
        #: `solve_road`), not a warning; see `point_style`'s module docstring.
        reserved = {
            "footway": (max(float(getattr(base, "left_walk_width", 0.0)),
                            float(getattr(base, "right_walk_width", 0.0))), "walk_width"),
        }
        for slot, (want, field) in reserved.items():
            asset = style.asset(slot)
            if asset is None or want <= 0.0:
                continue
            got = _pstyle.asset_width(asset)
            if got <= 0.0 or abs(got - want) <= ASSET_WIDTH_TOL:
                continue
            out(Finding("asset_width_mismatch", WARN, name,
                        "%s section '%s' is %.2f m wide but the road reserves %.2f m -- %.2f m of "
                        "%s. Set %s to %.2f, or pick a section that fits."
                        % (slot, asset.name, got, want, abs(got - want),
                           "overhang" if got > want else "slack", field, got)))


def check_path_fidelity(net, out):
    """Does the exported Path3D actually follow the lane it represents?

    THE ONE CHECK ABOUT WHAT SHIPS RATHER THAN WHAT IS AUTHORED, and it is here because the gate is
    the only thing an artist is guaranteed to run. `WorldBaker` builds every `Curve3D` an ambient
    car drives from the `curve` block of `.lanekit.json`, and that block is a FIT to the lane, not
    the lane -- so it can be wrong while every point, link, taper and pad is right. It has been:
    the Catmull refit this replaced ran 22.57 m off `demo_ramp_F0` on the addon's own sample
    network, with the gate green and the geometry perfect.

    Measured by `point_export.path_deviation`, the same function `Preview > Traffic Flow` draws
    with and the export's own self-test asserts on -- so the picture, the finding and the test
    cannot disagree about what shipped.

    A late check, and a forgiving one: the fit subdivides until it is inside 0.05 m, so anything
    reported here is a shape the fit could not follow at all, not a tuning matter."""
    try:
        from . import point_export as _pe
    except ImportError:
        import point_export as _pe                                           # noqa: E402
    try:
        doc = _pe.export_network(net)
    except Exception as exc:                    # noqa: BLE001 -- reported, never raised
        out(Finding("path_export_failed", WARN, "<network>",
                    "the lane graph could not be exported to measure: %s" % (exc,)))
        return
    for lane_id, dev in _pe.deviating_lanes(doc):
        sev = ERROR if dev > _pe.PATH_DEVIATION_ERROR else WARN
        out(Finding("path_deviation", sev, lane_id,
                    "the exported Path3D is %.2f m from the lane it represents -- cars will drive "
                    "there, not on the road. Add a station where the bend actually is." % dev))


CHECKS = (check_identity, check_links, check_chains, check_meetings, check_tapers,
          check_taper_routes, check_junctions, check_mouth_clearance, check_pads, check_ramps,
          check_aux_slots, check_support, check_path_fidelity, check_style, check_asset_width)


def validate(net, checks=CHECKS):
    findings = []
    for fn in checks:
        try:
            fn(net, findings.append)
        except Exception as exc:                       # a crashing check must not hide the others
            findings.append(Finding("check_crashed", ERROR, fn.__name__,
                                    "%s: %s" % (type(exc).__name__, exc)))
    return findings


def errors(findings):
    return [f for f in findings if f.severity == ERROR]


#: A uid as it appears inside a message body. `new_uid` builds them as `p_` + 8 hex, and nothing
#: else in a finding looks like that, so substituting them is unambiguous.
_UID_RE = re.compile(r"\bp_[0-9a-f]{8}\b")


def describe(finding, labels=None):
    """One finding as a line an artist can act on, with EVERY uid in it resolved to an object name.

    The subject was already translated at the two reporting sites; the message BODY was not -- and
    that is where most of the uids are ("is chain-adjacent to p_862c8815", "move p_5dd247b1
    further away", "the gore line of p_a12588f3"). So a finding named an object you could find in
    the outliner and then told you to go and fix one you could not, which is the same as not
    naming it: `p_862c8815` appears nowhere in Blender's UI.

    ONE OWNER for that substitution, here, because there are three reporting sites (Validate,
    Build, Export) and they had three different amounts of it. `labels` is `{uid: name}` --
    `point_model.point_labels()` builds it from the scene; pure-Python callers pass None and get
    the uids, which is right for a test."""
    labels = labels or {}
    subject = labels.get(finding.obj, finding.obj)
    body = _UID_RE.sub(lambda m: labels.get(m.group(0), m.group(0)), finding.message)
    return "%s: %s -- %s" % (finding.code, subject, body)


def report(findings, stream=None, labels=None):
    stream = stream or sys.stdout
    for f in findings:
        stream.write("[%s] %s\n" % (f.severity, describe(f, labels)))
    n = len(errors(findings))
    stream.write("%d error(s), %d warning(s)\n" % (n, len(findings) - n))
    return n == 0


# ------------------------------------------------------------------------------- self-test

def build_testbed():
    """The step-1 scene: a 6-point road, a 4-arm junction and a ramp, built PURELY through the
    data model -- no bpy, no operators, no geometry. If this cannot be expressed here, the model
    is wrong, and that is much cheaper to find out now than after the GN stack exists.

        road_main  p0 --- p1 --- [p2 | JCT | p3] --- p4 --- p5
                                    |         |        \\ AUX
        road_cross              c0 -+         +- c1     ramp_e  r0 --- r1

    Note what a crossing does NOT do: it does not split `road_main` into two roads. p2 and p3 are
    ordinary interior members of one 6-point chain that happen to carry JUNCTION links."""
    net = pm.NetworkData()

    def pt(road, x, y, **kw):
        # `add_station`, not `add_point`: an INHERIT station must take its lane counts from the
        # road base, or the PointData schema defaults (2/2) quietly override it.
        return net.add_station(road, (x, y, 0.0), has_ground_z=True, **kw)

    main = net.add_road(pm.RoadData(
        "road_main", pm.PointData(lane_width=3.5, median_width=1.0, lanes_fwd=2, lanes_bwd=2,
                                  left_walk_width=3.0, right_walk_width=3.0),
        road_class="arterial", zone_id="Testbed"))
    xs = [0.0, 120.0, 236.0, 264.0, 480.0, 600.0]
    mp = [pt(main, x, 0.0, role=(pm.INTERSECTION if i in (2, 3) else pm.SEGMENT))
          for i, x in enumerate(xs)]
    for a, b in zip(mp, mp[1:]):
        if not (a.role == pm.INTERSECTION and b.role == pm.INTERSECTION):
            net.link(a.uid, b.uid, pm.LINK_SEGMENT)

    cross = net.add_road(pm.RoadData(
        "road_cross", pm.PointData(lane_width=3.5, median_width=0.0, lanes_fwd=1, lanes_bwd=1),
        road_class="street", zone_id="Testbed"))
    cp = [pt(cross, 250.0, y, role=(pm.INTERSECTION if i in (1, 2) else pm.SEGMENT))
          for i, y in enumerate((-150.0, -14.0, 14.0, 150.0))]
    for a, b in zip(cp, cp[1:]):
        if not (a.role == pm.INTERSECTION and b.role == pm.INTERSECTION):
            net.link(a.uid, b.uid, pm.LINK_SEGMENT)

    arms = [mp[2].uid, mp[3].uid, cp[1].uid, cp[2].uid]
    for i, a in enumerate(arms):                      # the full clique, not just a component
        for b in arms[i + 1:]:
            net.link(a, b, pm.LINK_JUNCTION)

    # The ramp. The mainline opens an aux lane at p4 and hands it to the ramp; the ramp point
    # connects to the aux slot and to NOTHING else.
    mp[4].aux_fwd = 1
    ramp = net.add_road(pm.RoadData(
        "ramp_e", pm.PointData(lane_width=3.5, median_width=0.0, lanes_fwd=1, lanes_bwd=0),
        road_class="ramp", zone_id="Testbed", ped_access=False))
    # The mouth sits ON the gore line -- the aux slot's through-lane edge -- and FACES DOWN THE
    # MAINLINE. The divergence happens at the next point, which is what a parallel-type exit is:
    # the two bands leave the same cut plane together and open gradually, so the gore between them
    # is a strip with a nose rather than a wedge that gapes from vertex one.
    r0 = pt(ramp, 480.0, 7.5, role=pm.RAMP_EXIT, lanes_fwd=1, lanes_bwd=0,
            tangent_mode=pm.MANUAL)
    r0.tangent = (1.0, 0.0, 0.0)
    r1 = pt(ramp, 560.0, 12.0, lanes_fwd=1, lanes_bwd=0)
    r2 = pt(ramp, 620.0, 40.0, lanes_fwd=1, lanes_bwd=0)
    net.link(r0.uid, r1.uid, pm.LINK_SEGMENT)
    net.link(r1.uid, r2.uid, pm.LINK_SEGMENT)
    net.link(mp[4].uid, r0.uid, pm.LINK_AUX)
    return net, mp, cp, [r0, r1, r2]


def self_test():
    ok = 0

    net, mp, cp, rr = build_testbed()
    f = validate(net)
    if errors(f):
        report(f)
    assert not errors(f), "the testbed must be GREEN -- a gate that cannot pass is worse than none"
    assert len(net.roads["road_main"].points) == 6, "a crossing does NOT split the street"
    assert len(net.junction_cliques()) == 1 and len(net.junction_cliques()[0]) == 4
    print("OK: a 6-point road + 4-arm junction + a ramp is expressible in the model alone, green")
    ok += 1

    # -- the deliberately broken link ------------------------------------------------------------
    net, mp, cp, rr = build_testbed()
    net.points[mp[2].uid].unlink(cp[1].uid)          # one half of one JUNCTION link
    codes = {x.code for x in errors(validate(net))}
    assert "junction_asymmetric" in codes and "junction_incomplete" in codes, codes
    print("OK: a half-written JUNCTION link is caught -- one pad never becomes two overlapping")
    ok += 1

    net, mp, cp, rr = build_testbed()
    net.remove_point(mp[1].uid)
    codes = {x.code for x in errors(validate(net))}
    # A point left joined to NOTHING is the real defect, and it is named as one. The bare
    # "chain-adjacent but unlinked" observation is only a WARN: a road whose chain splits into two
    # runs still builds, and authoring a ramp inside its mainline's collection is how that happens.
    assert "point_stranded" in codes, codes
    assert "chain_unlinked" in {x.code for x in validate(net)}, "still reported, as a warning"
    assert "chain_unlinked" not in codes, codes
    assert "link_dangling" not in codes, "Delete Point strips inbound links; nothing dangles"
    print("OK: a hole in a chain is caught, and deletion leaves no dangling reference")
    ok += 1

    net, mp, cp, rr = build_testbed()
    net.points[mp[0].uid].link_to("p_ghost", pm.LINK_SEGMENT)
    assert "link_dangling" in {x.code for x in errors(validate(net))}
    print("OK: a link to a point that no longer exists is reported by name, not a traceback")
    ok += 1

    # -- taper rate is derived from design speed, never a constant --------------------------------
    net, mp, cp, rr = build_testbed()
    net.points[mp[1].uid].lanes_fwd = 1
    assert not errors(validate(net)), "3.5 m over 120 m at 50 km/h is a legal taper"
    # Design speed is a ROAD-level fact, so it is authored on the base profile -- setting it on
    # an INHERIT station would be silently ignored, which is worth having the test prove.
    net.roads["road_main"].base.design_speed = 100.0
    errs = [x for x in errors(validate(net)) if x.code == "taper_too_short"]
    assert errs and errs[0].obj == mp[0].uid, "the same taper at 100 km/h wants 210 m"
    assert abs(taper_min_length(3.5, 100.0) - 210.0) < 1e-9
    assert taper_min_length(3.5, 30.0) < taper_min_length(3.5, 100.0)
    # The branch switches at 70 km/h, where the metric standard puts it -- not 60, which demanded
    # half again the standard length across the whole 60-70 band.
    assert abs(taper_min_length(3.5, 60.0) - 3.5 * 3600 / 155.0) < 1e-9
    assert abs(taper_min_length(3.5, 80.0) - 0.6 * 3.5 * 80.0) < 1e-9
    # ...and `taper_factor` scales it, so a compressed world can say so out loud.
    assert abs(taper_min_length(3.5, 100.0, 0.5) - 105.0) < 1e-9
    net.roads["road_main"].taper_factor = 0.4
    assert not [x for x in errors(validate(net)) if x.code == "taper_too_short"], \
        "a taper_factor the artist set must actually relax the gate"
    net.roads["road_main"].taper_factor = 1.0
    print("OK: taper rate is validated against design speed x taper_factor, and names the point")
    ok += 1

    # ...and a lane that DEPARTS onto a ramp is exempt from the MERGE taper rule. Without this the
    # aux slot has to taper for hundreds of metres past the gore, so a fourth lane runs on to
    # nowhere and the ramp's band overlaps the mainline's for the whole of it.
    net, mp, cp, rr = build_testbed()
    net.roads["road_main"].base.design_speed = 120.0
    errs = [x for x in errors(validate(net)) if x.code == "taper_too_short"]
    # p4 owns the AUX link, so handing its aux slot to the ramp needs no MERGE taper...
    assert not [x for x in errs if x.obj == mp[4].uid], errs
    # ...and the exemption is local: p3, which OPENS that same slot over 216 m at 120 km/h, is
    # an ordinary merge and still an error.
    assert [x for x in errs if x.obj == mp[3].uid], errs
    print("OK: a lane departing onto a ramp is not a lane merging -- exempt, and only there")
    ok += 1

    # -- the taper is PER CARRIAGEWAY, and only WITHIN a run --------------------------------------
    # Opening an aux lane on one side of the divide must not cost more because the other side
    # already has one: nobody drives both carriageways, so the two changes are two tapers for two
    # drivers, not one twice as wide (8i, a user report).
    net, mp, cp, rr = build_testbed()
    net.roads["road_main"].base.design_speed = 70.0
    # p0 -> p1 is 120 m, and at 70 km/h one 3.5 m lane wants 111 m of it -- legal. TWO of them,
    # one per carriageway, want 221 m if you add the sides together, which is what this used to do.
    assert taper_min_length(3.5, 70.0) < 120.0 < taper_min_length(7.0, 70.0)
    for p in (mp[1], mp[2]):
        p.aux_bwd = 1                                # a reverse aux slot opens across p0 -> p1
    one = [x for x in errors(validate(net)) if x.code == "taper_too_short"]
    for p in (mp[1], mp[2]):
        p.aux_fwd = 1                                # ...and now a forward one, over the SAME span
    both = [x for x in errors(validate(net)) if x.code == "taper_too_short"]
    assert not one and not both, (one, both)
    # A run BREAK is not a taper. p2 and p3 are the crossing's two stop lines: the pad joins them,
    # not carriageway, so a lane count that differs across it is normal and costs no merge length.
    net, mp, cp, rr = build_testbed()
    net.roads["road_main"].base.design_speed = 120.0
    net.points[mp[2].uid].lanes_fwd = 1
    net.points[mp[1].uid].lanes_fwd = 1
    net.points[mp[0].uid].lanes_fwd = 1
    assert not [x for x in errors(validate(net)) if x.code == "taper_too_short"
                and x.obj == mp[2].uid], "a junction gap is not a taper"
    print("OK: the taper is measured per carriageway, and never across a run break")
    ok += 1

    # -- ramp edge alignment ----------------------------------------------------------------------
    net, mp, cp, rr = build_testbed()
    assert not [x for x in errors(validate(net)) if x.code.startswith("ramp_")], \
        "the testbed ramp is aligned to the aux slot edge"
    net.points[rr[0].uid].pos = (480.0, 16.0, 0.0)
    errs = [x for x in errors(validate(net)) if x.code == "ramp_edge_residual"]
    assert errs, errs
    print("OK: the gore-line residual is reported in metres, never hidden")
    ok += 1

    net, mp, cp, rr = build_testbed()
    net.points[mp[4].uid].aux_fwd = 0
    assert "aux_no_slot" in {x.code for x in errors(validate(net))}
    net, mp, cp, rr = build_testbed()
    # One-sided on purpose: `net.link` would retype the existing AUX link rather than add a
    # second one, and what this reproduces is the artist wiring the ramp back to the mainline.
    net.points[rr[0].uid].link_to(mp[4].uid, pm.LINK_SEGMENT)
    assert "aux_backlink" in {x.code for x in errors(validate(net))}
    print("OK: a ramp point with no aux slot, or one that links back, is caught")
    ok += 1

    # -- a ramp that bends back ACROSS the road it leaves ---------------------------------------
    # 8j. Both ramp checks above measure the MOUTH, and `Align Ramp To Aux` sets both of them, so
    # a ramp that leaves correctly and then drives back through the carriageway passed the whole
    # gate: the two bands overlapped for the ramp's length, `point_solve.solve_gore` found no
    # wedge and returned None, and the artist was told nothing at all. The sample network's own
    # exit ramp was authored exactly that way.
    net, mp, cp, rr = build_testbed()
    assert "ramp_wrong_side" not in {x.code for x in validate(net)}
    p1 = net.points[rr[1].uid]
    net.points[rr[1].uid].pos = (p1.pos[0], -30.0, p1.pos[2])      # bend it back over the road
    net.points[rr[2].uid].pos = (net.points[rr[2].uid].pos[0], -60.0, 0.0)
    assert "ramp_wrong_side" in {x.code for x in errors(validate(net))}
    print("OK: a ramp that bends back across the carriageway it leaves is caught, not silently "
          "left with no gore")
    ok += 1

    # -- two ramps on one run may not claim the same auxiliary slot ------------------------------
    # A reachability rule with no geometry: a run exports ONE lane per slot, so the second
    # hand-off overwrites the first and one of the two ramps is paved, exported, gate-green and
    # unreachable from anywhere in the world.
    net, mp, cp, rr = build_testbed()
    assert "aux_slot_shared" not in {x.code for x in validate(net)}
    second = net.add_road(pm.RoadData("ramp_f", pm.PointData(lane_width=3.5, median_width=0.0,
                                                             lanes_fwd=1, lanes_bwd=0),
                                      road_class="ramp"))
    s0 = net.add_station(second, (600.0, 7.5, 0.0), role=pm.RAMP, lanes_fwd=1, lanes_bwd=0)
    s1 = net.add_station(second, (680.0, 40.0, 0.0), lanes_fwd=1, lanes_bwd=0)
    net.link(s0.uid, s1.uid, pm.LINK_SEGMENT)
    net.points[mp[5].uid].aux_fwd = 1          # same RUN as mp[4], so the same AF0
    net.link(mp[5].uid, s0.uid, pm.LINK_AUX)
    assert "aux_slot_shared" in {x.code for x in errors(validate(net))}
    # ...and the other carriageway is the way out, so the rule is actionable rather than a wall.
    net.points[mp[5].uid].aux_fwd = 0
    net.points[mp[5].uid].aux_bwd = 1
    net.points[s0.uid].pos = (600.0, -7.5, 0.0)
    net.points[s1.uid].pos = (520.0, -40.0, 0.0)
    assert "aux_slot_shared" not in {x.code for x in errors(validate(net))}
    # ...and ONE station wired to two ramps is legal when its block is WIDE ENOUGH -- a two-lane
    # exit that splits into two one-lane ramps -- and over-subscribed when it is not. That is the
    # shape a duplicated ramp collection lands in, because the copy keeps the original's mainline.
    try:
        from . import point_solve as psolve
    except ImportError:
        import point_solve as psolve                                         # noqa: E402
    # ...and a station may hand a ramp to EACH CARRIAGEWAY (8l): the reverse block is a different
    # piece of pavement with different slot ids, so an exit on one side and an entrance on the
    # other is not a collision at all.
    net.points[mp[4].uid].aux_bwd = 1
    assert "aux_slot_shared" not in {x.code for x in errors(validate(net))}
    net.points[mp[4].uid].aux_bwd = 0

    # Back onto the FORWARD side, both ramps on ONE carriageway's block: that IS a collision when
    # the block has one slot, and a clean split when it has two.
    net.points[mp[5].uid].unlink(s0.uid)
    net.points[s0.uid].pos = (600.0, 7.5, 0.0)
    net.points[s1.uid].pos = (680.0, 40.0, 0.0)
    net.points[mp[5].uid].aux_bwd = 0
    net.points[mp[4].uid].aux_fwd = 1
    net.link(mp[4].uid, s0.uid, pm.LINK_AUX)
    codes = {x.code for x in errors(validate(net))}
    assert "aux_block_oversubscribed" in codes, codes   # one slot, two ramps
    net.points[mp[4].uid].aux_fwd = 2                   # widen it: a slot each
    alloc = {u: v for u, v in psolve.aux_allocation(net, mp[4].uid).items()}
    assert sorted(len(v) for v in alloc.values()) == [1, 1], alloc
    assert set().union(*alloc.values()) == {"AF0", "AF1"}, alloc
    codes = {x.code for x in errors(validate(net))}
    assert "aux_block_oversubscribed" not in codes and "aux_slot_shared" not in codes, codes
    print("OK: one station may hand its aux block to two ramps -- a slot each, and an "
          "over-subscribed block is named")
    ok += 1

    # -- the 2.1a route check actually runs on a real merge ---------------------------------------
    net, mp, cp, rr = build_testbed()
    net.points[mp[5].uid].lanes_fwd = 1
    f = validate(net)
    assert not [x for x in f if x.code in ("taper_route_offset", "taper_route_unchained")], \
        "lane_taper_route already lands the merging lane inside the receiving lane"
    print("OK: the 2.1a route tolerances hold on a real 2 -> 1 merge (0.3 m / 4.5 m)")
    ok += 1

    # ---- the pad gate: a mouth dragged past the centroid folds the fan ------------------------
    try:
        from . import point_solve as psolve
    except ImportError:
        import point_solve as psolve                                         # noqa: E402
    net, mp, cp, rr = build_testbed()
    comp = net.junction_cliques()[0]
    clean = [f for f in validate(net, checks=(check_pads,))]
    assert not errors(clean), clean
    # Pull one mouth across the centre. The pad ring then folds, and a centroid fan would invert.
    # A NAMED victim, not `comp[0]`: uids are random per run and `junction_cliques` sorts by uid,
    # so `comp[0]` is a different arm every time -- and how deeply the ring folds depends on WHICH
    # arm moved, which made this assertion flaky rather than wrong.
    victim = cp[1].uid
    net.points[victim].pos = (250.4, 0.4, 0.0)
    folded = validate(net, checks=(check_pads,))
    codes = {f.code for f in folded}
    assert "pad_not_star_shaped" in codes or "pad_degenerate" in codes, folded
    assert any(f.obj in comp for f in folded), folded
    # ...and it is a WARNING, because the pad still tessellates. A hand-dragged mouth must never
    # be able to refuse the whole build.
    assert not [f for f in errors(folded) if f.code == "pad_not_star_shaped"], folded
    tris = psolve.solve_junction(net, comp).fan
    assert tris, "a folded ring still yields triangles -- ear-clipped, never empty"
    print("OK: a pad whose ring folds past its centroid still builds, and WARNS, named by pad")
    ok += 1

    # -- a road that stops ON another road, with nothing joining them --------------------------
    net, mp, cp, rr = build_testbed()
    # `road_cross` is a through street; move its free south end onto `road_main`'s free west end.
    # Nothing else changes: both chains are whole, no link is asymmetric, no clique is incomplete
    # -- the network is perfectly consistent with itself and no car can get from one to the other.
    net.points[cp[0].uid].pos = tuple(mp[0].pos)
    codes = {x.code for x in validate(net)}
    assert "road_end_unjoined" in codes, codes
    assert not [x for x in errors(validate(net)) if x.code == "road_end_unjoined"], "a WARN"
    net.link(cp[0].uid, mp[0].uid, pm.LINK_SEGMENT)
    assert "road_end_unjoined" not in {x.code for x in validate(net)}, "linking clears it"
    # ...and Z separates an underpass from a meeting: the same two points, one deck apart.
    net.unlink(cp[0].uid, mp[0].uid)
    net.points[cp[0].uid].pos = (mp[0].pos[0], mp[0].pos[1], mp[0].pos[2] + MEET_TOL + 1.0)
    assert "road_end_unjoined" not in {x.code for x in validate(net)}, "a street under a deck"
    print("OK: a road that ends ON another road with no link is named -- and an underpass is not")
    ok += 1

    print("\nALL SELF-TESTS PASSED (%d)" % ok)
    return True


if __name__ == "__main__":
    self_test()

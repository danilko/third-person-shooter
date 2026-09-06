#!/usr/bin/env python3
"""
island_v3_to_roadkit.py — rebuild the island's ENTIRE road network as real road_kit_authoring
pieces, instead of the flat preview ribbons `build_island_v3.py` draws.

WHY THIS EXISTS: `build_island_v3.py` emits `kit_common.flat_ribbon` meshes. Those are a MAP —
they have no lanes, no curbs, no sidewalks, no `.lanekit.json`, and nothing downstream can drive
on them. The authoring addon builds the real thing, and it already accepts an arbitrary path:
`rka.build_segment_from_curve` samples a Blender Curve into a self-contained multi-point spine
you can then keep editing (add points, reshape, raise/lower for a genuine multi-point slope) with
the pavement updating live. So the missing link was never curve SUPPORT — it was that nothing
handed the addon curves. This does.

THE THREE THINGS IT DOES, in order:

1. SMOOTH — the authored polylines are coarse by design (the RING is 28 points across 4,987 m,
   ~180 m apart; an arterial is 7-10 points). Feeding those straight in would build a road out of
   long chords with visible corners. `smooth()` fits a Catmull-Rom spline THROUGH every authored
   point and resamples it at `--spacing` metres, so the curve gains as many intermediate points as
   the geometry needs while still passing exactly through the points you authored. This is the
   "add additional points to form curve as need" step.

2. SPLIT — a road running unbroken through six crossings cannot become intersections later. Every
   pair of roads is tested for XY crossings at compatible heights (a flyover is NOT a crossing —
   see `Z_CROSS_TOL`), and each road is cut into chunks between them. Each crossing also emits an
   `xing_*` empty carrying the roads it joins and their angle, which is the worklist for
   `rka.build_intersection`.

3. BUILD — one `rka.build_segment_from_curve` per chunk, with per-tier lane counts, widths, curbs
   and sidewalks from `TIERS`.

Curvature is CHECKED, never silently accepted: each tier declares a minimum radius, and a chunk
that violates it is reported with its tightest radius so the authored polyline can be eased.

RUN:
  blender --background --python blender/tools/island_v3_to_roadkit.py -- --curves-only
  blender --background --python blender/tools/island_v3_to_roadkit.py -- --build
  blender --background --python blender/tools/island_v3_to_roadkit.py -- --build --only RING,Chuo-dori
"""
import bpy, os, sys, math

BLENDER_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # blender/
REPO        = os.path.dirname(BLENDER_SRC)
ROOT        = os.path.join(REPO, "assets", "world_source")
sys.path.insert(0, os.path.join(BLENDER_SRC, "lib"))
sys.path.insert(0, os.path.join(BLENDER_SRC, "addons"))
sys.path.insert(0, os.path.join(REPO, "tools"))

import kit_common as kc
import assemble as asm
import island_v3_geom as G
import island_v3_plan as P
import road_geometry as rgeom
import road_kit_authoring as rka


# --------------------------------------------------------------------------- road tiers
# lane_width/lanes are the REAL Japanese figures behind v3 §5's tier widths:
#   T2 arterial 27 m = 2+2 x 3.25 m + 3 m median + 2 x 4 m sidewalk
#   T3 local    14 m = 1+1 x 3.25 m + 2 x 3.5 m sidewalk
#   T1 deck     22 m = 2+2 x 3.5 m, no sidewalk, parapet instead of curb
# min_radius is a DESIGN-SPEED figure, not a preference: 6% superelevation at the tier's speed.
#
# IT NOW ACTUALLY IS ONE (2026-08-15). The figures below were hand-written and every one of them was
# more permissive than the speed beside it claimed: T3 declared 25 m at 40 km/h where the equation
# gives 43 m, RAMP declared 30 m at 40 km/h, T2 60 m at 50 km/h where it needs 79 m, T1 140 m at
# 80 km/h where it needs 252 m. So the generator's own `TIGHT:` check was passing roads that a car
# cannot hold, and `tight=0` on a full run meant nothing. `min_radius` is now DERIVED from `speed`
# by `road_geometry.min_radius` (one equation, see that module) and cannot drift from it again.
# A tier may still override it deliberately -- `TOUGE` does, because an 11 m hairpin IS the design
# and calling it a violation would be the tool misunderstanding the road.
def _derived_min_radius(speed_kmh):
    return rgeom.min_radius(speed_kmh, rgeom.SUPERELEVATION_MAX)


TIERS = {
    "T1":   dict(lane_width=3.50, lanes=2, lanes_backward=2, median_width=1.2,
                 sidewalk=0.0, curb='NONE', min_radius=None, speed=80),
    # The expressway deck as it is actually built: TWO one-way carriageways, not one two-way
    # road. The ring is emitted as `LOOP_A`/`LOOP_B` in this tier (see `collect_roads`) so that an
    # interchange trunk is a CHUNK OF THE RING rather than a parallel construction beside it --
    # without that the ring exists twice, overlapping, and the interchange's lanes never meet the
    # ring's own (measured: 12-42 m apart along the ring, leaving 4 of 8 interchanges as islands
    # in the lane graph; see `tools/check_road_network.py`).
    "T1C":  dict(lane_width=3.50, lanes=2, lanes_backward=0, median_width=0.0,
                 sidewalk=0.0, curb='NONE', min_radius=None, speed=80),
    "T2":   dict(lane_width=3.25, lanes=2, lanes_backward=2, median_width=3.0,
                 sidewalk=4.0, curb='NONE', min_radius=None, speed=50),
    "T3":   dict(lane_width=3.25, lanes=1, lanes_backward=1, median_width=0.0,
                 sidewalk=3.5, curb='NONE', min_radius=None, speed=40),
    # 45 km/h ramps (2026-08-15, user-requested "to allow 45 or high speed ramp exit").
    # At 6% superelevation that is a 59 m minimum radius -- nearly double the 30 m this tier used
    # to claim, so the fillet pass now opens every ramp corner to it.
    "RAMP": dict(lane_width=4.50, lanes=1, lanes_backward=0, median_width=0.0,
                 sidewalk=0.0, curb='NONE', min_radius=None, speed=45),
    # A motorway-to-motorway link: two lanes one way, and driven faster than a surface-street
    # ramp because both of its ends are expressway. See `island_v3_to_graph.TIER_ATTRS["RAMP2"]`.
    "RAMP2": dict(lane_width=3.75, lanes=2, lanes_backward=0, median_width=0.0,
                  sidewalk=0.0, curb='NONE', min_radius=None, speed=60),
    # The touge is its own tier, not a ramp. v3 §5 specifies an 11 m minimum HAIRPIN radius on
    # purpose — a mountain pass whose corners open up to a ramp's 30 m is no longer a touge.
    # Tiering it correctly turns five "violations" back into the design they always were.
    "TOUGE": dict(lane_width=2.75, lanes=1, lanes_backward=1, median_width=0.0,
                  sidewalk=0.0, curb='NONE', min_radius=11.0, speed=30),
}

# Fill in every tier that did not deliberately override its own minimum radius.
for _t in TIERS.values():
    if _t.get("min_radius") is None:
        _t["min_radius"] = _derived_min_radius(_t["speed"])

Z_CROSS_TOL = 4.0     # roads whose heights differ by more than this at a meeting point are a
                      # FLYOVER, not a crossing — no intersection, just a clearance check.
MIN_CHUNK   = 24.0    # a chunk shorter than this is merged away rather than built as a stub


# ------------------------------------------------------------------------------ geometry
def smooth(pts, spacing=12.0, closed=False):
    """Catmull-Rom through every authored point, resampled at `spacing` metres.

    THE POINT: it INTERPOLATES (passes through the control points) rather than approximating
    them, so the designed alignment is preserved exactly while the corners between authored
    points become real curves. A bezier/B-spline fit would pull the road off the points you
    placed, which is the wrong trade for a layout that was measured."""
    if len(pts) < 3:
        return list(pts)
    src = list(pts)
    if closed:
        ext = [src[-1]] + src + [src[0], src[1]]
    else:
        ext = [src[0]] + src + [src[-1]]
    out = []
    for i in range(len(ext) - 3):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        seg = math.dist(p1[:2], p2[:2])
        n = max(2, int(math.ceil(seg / spacing)))
        for k in range(n):
            t = k / n
            t2, t3 = t * t, t * t * t
            pt = []
            for d in range(3):
                a = p0[d] if len(p0) > d else 0.0
                b = p1[d] if len(p1) > d else 0.0
                c = p2[d] if len(p2) > d else 0.0
                e = p3[d] if len(p3) > d else 0.0
                pt.append(0.5 * ((2 * b) + (-a + c) * t +
                                 (2 * a - 5 * b + 4 * c - e) * t2 +
                                 (-a + 3 * b - 3 * c + e) * t3))
            out.append(tuple(pt))
    if not closed:
        out.append(tuple(src[-1]) if len(src[-1]) == 3 else tuple(list(src[-1]) + [0.0]))
    return out


def resample_uniform(pts, step, closed=False):
    """Re-space a polyline at a constant arc-length `step`.

    THIS IS NOT COSMETIC. Every downstream measurement here — the Menger radius check, the
    crossing parameterisation, the addon's own spine sampling — assumes points are roughly
    evenly spaced. `fillet()` deliberately produces the opposite (dense arc points next to long
    straight runs), and feeding THAT to a Catmull-Rom pass made the network read as 47 radius
    violations where the geometry was in fact fine: the spline overshot between the tightly
    packed arc points, and the radius estimator divided by near-zero areas on the near-duplicate
    ones. Uniform spacing removes both failure modes at once, which is why smoothing is no longer
    applied after a fillet — the fillet already put a controlled, known radius in the corner, and
    a spline on top can only degrade it."""
    src = list(pts) + ([tuple(pts[0])] if closed else [])
    if len(src) < 2:
        return list(pts)
    out = [tuple(src[0])]
    carry = 0.0
    for a, b in zip(src, src[1:]):
        seg = math.dist(a[:2], b[:2])
        if seg < 1e-9:
            continue
        d = step - carry
        while d <= seg:
            t = d / seg
            out.append((a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t, a[2] + (b[2]-a[2])*t))
            d += step
        carry = (carry + seg) % step
    if math.dist(out[-1][:2], src[-1][:2]) > step * 0.35:
        out.append(tuple(src[-1]))
    return out


def fillet(pts, radius, closed=False):
    """Round every corner to at least `radius` before smoothing.

    WHY THIS COMES FIRST: Catmull-Rom interpolates THROUGH its control points, so a 90-degree
    authored corner stays a 90-degree corner no matter how finely it is resampled — smoothing
    cannot invent a radius that the control polygon does not permit. `chamfer()`-built corners on
    the expressway LOOP measured 17.6 m against the 140 m an 80 km/h deck needs. Filleting
    replaces each corner with a real tangent arc, and the arc is clamped to half the shorter leg
    so a fillet can never eat its neighbour."""
    n = len(pts)
    if n < 3 or radius <= 0.0:
        return list(pts)
    idx = range(n) if closed else range(1, n - 1)
    out = [] if closed else [tuple(pts[0])]
    for i in idx:
        p = pts[i]
        a = pts[(i - 1) % n]
        b = pts[(i + 1) % n]
        v1 = (a[0] - p[0], a[1] - p[1])
        v2 = (b[0] - p[0], b[1] - p[1])
        l1 = math.hypot(*v1) or 1.0
        l2 = math.hypot(*v2) or 1.0
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        theta = math.acos(dot)                      # interior angle at p
        if theta > math.radians(178.0) or theta < 1e-4:
            out.append(tuple(p))                    # already straight (or a spike) — leave it
            continue
        tan_len = radius / math.tan(theta / 2.0)
        tan_len = min(tan_len, l1 * 0.5, l2 * 0.5)
        t1 = (p[0] + u1[0] * tan_len, p[1] + u1[1] * tan_len, p[2])
        t2 = (p[0] + u2[0] * tan_len, p[1] + u2[1] * tan_len, p[2])
        # A TRUE CIRCULAR ARC, not a quadratic bezier through the corner. A quad bezier is
        # tangent to both legs, which looks right, but its radius at the apex is
        # L*sin^2(t/2)/cos(t/2) — for a 135-degree chamfer vertex that is 129 m when 140 m was
        # requested, a silent 7.6% shortfall that showed up as the expressway LOOP failing its
        # own minimum radius. The arc below achieves the requested radius exactly.
        r_eff = tan_len * math.tan(theta / 2.0)
        bx, by = u1[0] + u2[0], u1[1] + u2[1]
        bl = math.hypot(bx, by) or 1.0
        bx, by = bx / bl, by / bl                       # inward angle bisector
        cdist = r_eff / math.sin(theta / 2.0)
        cx, cy = p[0] + bx * cdist, p[1] + by * cdist   # arc centre
        a1 = math.atan2(t1[1] - cy, t1[0] - cx)
        a2 = math.atan2(t2[1] - cy, t2[0] - cx)
        sweep = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        # Tessellate to a SAGITTA TOLERANCE, not a fixed angle. A fixed 8 deg step puts 19.5 m
        # chords on a 140 m arc (0.34 m sagitta); the later uniform resample then lands its
        # points on those chords, INSIDE the true circle, and the radius reads ~7% low. Holding
        # the sagitta under ARC_TOL makes the polyline hug the arc closely enough that the
        # measured radius is the designed one.
        ARC_TOL = 0.03
        dmax = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - ARC_TOL / max(r_eff, 1e-6))))
        steps = max(2, min(256, int(math.ceil(abs(sweep) / max(dmax, 1e-4)))))
        for k in range(steps + 1):
            a = a1 + sweep * (k / steps)
            out.append((cx + r_eff * math.cos(a), cy + r_eff * math.sin(a), p[2]))
    if not closed:
        out.append(tuple(pts[-1]))
    return out


def seg_cross(a0, a1, b0, b1):
    """XY intersection of two finite segments -> (x, y, ta, tb) or None."""
    d1x, d1y = a1[0] - a0[0], a1[1] - a0[1]
    d2x, d2y = b1[0] - b0[0], b1[1] - b0[1]
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-9:
        return None
    ex, ey = b0[0] - a0[0], b0[1] - a0[1]
    ta = (ex * d2y - ey * d2x) / den
    tb = (ex * d1y - ey * d1x) / den
    if not (1e-6 < ta < 1 - 1e-6 and 1e-6 < tb < 1 - 1e-6):
        return None
    return (a0[0] + d1x * ta, a0[1] + d1y * ta, ta, tb)


def crossings(roads):
    """Every place two roads meet at compatible heights. Returns
    {road_name: [t_along_polyline, ...]} plus a list of crossing records."""
    cuts = {name: [] for name in roads}
    recs = []
    names = list(roads)
    for i, na in enumerate(names):
        pa = roads[na]["pts"]
        for nb in names[i + 1:]:
            pb = roads[nb]["pts"]
            for ia in range(len(pa) - 1):
                for ib in range(len(pb) - 1):
                    hit = seg_cross(pa[ia], pa[ia + 1], pb[ib], pb[ib + 1])
                    if not hit:
                        continue
                    x, y, ta, tb = hit
                    za = pa[ia][2] + (pa[ia + 1][2] - pa[ia][2]) * ta
                    zb = pb[ib][2] + (pb[ib + 1][2] - pb[ib][2]) * tb
                    dz = abs(za - zb)
                    ang = _angle_between(pa[ia], pa[ia + 1], pb[ib], pb[ib + 1])
                    if dz > Z_CROSS_TOL:
                        recs.append(dict(kind="FLYOVER", a=na, b=nb, x=x, y=y,
                                         za=za, zb=zb, dz=dz, angle=ang))
                        continue
                    cuts[na].append(ia + ta)
                    cuts[nb].append(ib + tb)
                    recs.append(dict(kind="MERGE" if ang < 30.0 else "INTERSECTION",
                                     a=na, b=nb, x=x, y=y, za=za, zb=zb, dz=dz, angle=ang))
    return cuts, recs


def _angle_between(a0, a1, b0, b1):
    va = (a1[0] - a0[0], a1[1] - a0[1])
    vb = (b1[0] - b0[0], b1[1] - b0[1])
    la = math.hypot(*va) or 1.0
    lb = math.hypot(*vb) or 1.0
    c = max(-1.0, min(1.0, (va[0]*vb[0] + va[1]*vb[1]) / (la * lb)))
    a = math.degrees(math.acos(c))
    return min(a, 180.0 - a)


def split_at(pts, ts, closed=False):
    """Cut a polyline at fractional-index positions, returning chunks. Chunks shorter than
    MIN_CHUNK are folded into their neighbour rather than built as unusable stubs."""
    ts = sorted(set(round(t, 6) for t in ts if 0.0 < t < len(pts) - 1))
    if not ts:
        return [list(pts)]
    def at(t):
        i = min(int(t), len(pts) - 2)
        f = t - i
        return tuple(pts[i][d] + (pts[i + 1][d] - pts[i][d]) * f for d in range(3))
    bounds = [0.0] + ts + [float(len(pts) - 1)]
    chunks = []
    for t0, t1 in zip(bounds, bounds[1:]):
        run = [at(t0)]
        for i in range(int(math.ceil(t0)), int(math.floor(t1)) + 1):
            if t0 < i < t1:
                run.append(tuple(pts[i]))
        run.append(at(t1))
        if len(run) >= 2 and _plen(run) >= MIN_CHUNK:
            chunks.append(run)
        elif chunks:
            chunks[-1].extend(run[1:])
    return chunks or [list(pts)]


def _plen(pts):
    return sum(math.dist(a[:2], b[:2]) for a, b in zip(pts, pts[1:]))


def min_radius(pts, window=25.0):
    """Delegates to the canonical windowed estimator in island_v3_plan — see its
    docstring for why adjacent-point Menger radius is the wrong tool here."""
    return P.min_radius_windowed(pts, window)


#: How close a ramp's gore must already be to an expressway vertex to reuse it rather than insert
#: a new one. Below the resampling spacing by a wide margin, so this never merges two real gores.
GORE_PIN_TOL = 0.75


def pin_gores(loop_pts, gores, closed=True, tol=GORE_PIN_TOL):
    """Give the expressway a vertex exactly at every ramp gore; return `(pts, pinned)`.

    AN INTERCHANGE GORE IS A FEATURE OF THE EXPRESSWAY, so the expressway's own geometry has to
    contain it. It did not: the deck is filleted and then resampled at a fixed spacing, and a gore
    landed wherever it fell between two of those samples. The graph builder then welded the ramp's
    first point to the NEAREST deck vertex -- up to a merge tolerance away -- and welding a point
    sideways ROTATES the segment leaving it.

    Measured on the island, that is the whole difference between a working interchange and a
    broken one. IC_CHUO and IC_RINKAI_E happen to have gores that land on a deck vertex exactly
    (distance 0.00 m), and their ramps leave at precisely the authored angle, classify as GOREs,
    and get their auxiliary lane. IC_YAMATE's gore sat 6.53 m from the nearest vertex: the weld
    swung its ramp from the authored -40.6 deg to -20.1 deg, a 20 deg error, which put it 41 deg
    off the mainline -- outside the gore tolerance. The solver then called it an INTERSECTION, so
    `auto_aux_lanes` skipped it (no aux lane: the exit is taken from the middle of the
    carriageway) and intersection rules let the far carriageway turn across the median into it.

    Both symptoms, one cause, and it is not fixable by widening a tolerance -- the fix is for the
    two roads to share the vertex they are supposed to share.

    Points are inserted at the PROJECTION of the gore onto the deck, so the deck's own alignment
    is unchanged; the caller then starts the ramp at that same coordinate."""
    pts = [tuple(p) for p in loop_pts]
    n = len(pts)
    if n < 2:
        return pts, {}
    spans = list(range(n)) if closed else list(range(n - 1))
    inserts, pinned = {}, {}
    for key, g in gores:
        best = None
        for i in spans:
            a, b = pts[i], pts[(i + 1) % n]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            if L2 <= 1e-12:
                continue
            t = max(0.0, min(1.0, ((g[0] - a[0]) * dx + (g[1] - a[1]) * dy) / L2))
            q = (a[0] + dx * t, a[1] + dy * t, a[2] + (b[2] - a[2]) * t)
            d = math.dist((g[0], g[1]), (q[0], q[1]))
            if best is None or d < best[0]:
                best = (d, i, t, q)
        if best is None:
            continue
        _d, i, t, q = best
        # Already a vertex here? Reuse it -- inserting a second one a few centimetres away is a
        # zero-length edge, which has no tangent and degenerates the sweep frame.
        near = min(((math.dist((q[0], q[1]), (p[0], p[1])), p) for p in pts), key=lambda z: z[0])
        if near[0] <= tol:
            pinned[key] = near[1]
            continue
        inserts.setdefault(i, []).append((t, q))
        pinned[key] = q
    for i in sorted(inserts, reverse=True):
        for _t, q in sorted(inserts[i], reverse=True):
            pts.insert(i + 1, q)
    return pts, pinned


# ------------------------------------------------------------------------------ sources


def collect_roads(spacing):
    """Every road on the island as {name: {tier, pts (smoothed, 3D), closed}}."""
    ground = _ground()
    roads = {}

    def add(name, tier, pts2_or_3, closed=False, z=None):
        pts = []
        for p in pts2_or_3:
            if len(p) == 3:
                pts.append((p[0], p[1], p[2]))
            else:
                pts.append((p[0], p[1], ground(p[0], p[1]) + 0.25 if z is None else z))
        # A closed ring must NOT also carry a repeated first==last point: the pair makes a
        # zero-length segment at the seam, whose unit tangent is undefined, and the fillet then
        # degenerates exactly there. `loop_deck()` legitimately returns the closing point for
        # drawing, so strip it here rather than making every producer remember the rule.
        if closed and len(pts) > 2 and math.dist(pts[0][:2], pts[-1][:2]) < 1e-6:
            pts = pts[:-1]
        # Fillet puts a controlled radius in every corner; uniform resampling then gives the
        # even point spacing every downstream measurement assumes. See resample_uniform().
        pts = fillet(pts, TIERS[tier]["min_radius"], closed)
        roads[name] = dict(tier=tier, pts=resample_uniform(pts, spacing, closed), closed=closed)

    add("RING", "T2", P.RING, closed=True)
    for nm, pts in G.ARTERIALS:
        add(nm, "T2", pts)
    add("LOOP", "T1", P.loop_deck(), closed=True)
    # ...then replace it with its two carriageways. Derived from the ALREADY filleted+resampled
    # deck (not the raw 8-vertex ring), so both inherit the corner radii and even point spacing
    # every downstream measurement assumes. `carriageways` offsets to each direction's median
    # edge, which is the datum a one-way piece anchors its lanes on.
    # ONE two-direction piece, not two one-way carriageways -- see ROAD_KIT_REDESIGN.md 2.3 and
    # `ops_split.two_way_carriageway_profile`. The split existed because a piece could carry a
    # single lane COUNT; a ProfileSet removes that, and the split cost correctness (every exit
    # landed on one carriageway and every entry on the other, so the reverse direction was a dead
    # end -- defect 13, caught by the connectivity gate).
    # FIT THE RAMPS AGAINST THE DECK THAT IS ACTUALLY BUILT. The plan's own `G.LOOP` is the raw
    # eight-corner ring; the deck above is that ring filleted and resampled, and a filleted corner
    # sits well inside the raw line. Answering "where is the gore on the LOOP?" from the raw ring
    # therefore describes a road that does not exist -- and the ramp gets fitted to it.
    P.use_loop_polyline([(p[0], p[1]) for p in roads["LOOP"]["pts"]])
    # PIN EVERY GORE INTO THE DECK BEFORE THE RAMPS ARE ADDED, and start each ramp on the vertex
    # that was pinned for it, so the two roads share that point exactly instead of being welded
    # together by proximity. See `pin_gores` for what welding-by-proximity costs.
    built = list(P.ramps())
    roads["LOOP"]["pts"], pinned = pin_gores(
        roads["LOOP"]["pts"], [(r[0], r[1][0]) for r in built], closed=True)
    for rid, p3, par, grade, ok, kind in built:
        q = pinned.get(rid)
        if q is not None:
            p3 = [(q[0], q[1], p3[0][2])] + list(p3[1:])
        add(rid, "RAMP2" if kind == "jct" else "RAMP", p3)
    add("SPIRAL_AIRPORT", "RAMP", P.spiral_ramp((905.0, -720.0))[0])
    add("TOUGE", "TOUGE", G.TOUGE)
    for nm, pts in (("WESTRAD", G.WESTRAD), ("PORTSPUR", G.PORTSPUR),
                    ("AIRPORT_ROAD", G.AIRPORT_ROAD)):
        add(nm, "RAMP", pts)
    return roads


def _ground():
    def ground(x, y):
        if G.inside(G.AIRPORT, x, y):
            return P.ISLAND_Z
        if G.inside(G.HARBOUR, x, y):
            return 2.0
        return 0.0
    return ground


# ------------------------------------------------------------------------------ emitters
def emit_curve(name, pts, coll):
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts) - 1)
    for i, p in enumerate(pts):
        sp.points[i].co = (p[0], p[1], p[2], 1.0)
    obj = bpy.data.objects.new(name, cu)
    coll.objects.link(obj)
    return obj


def arms_at(roads, rec, reach=18.0):
    """Approach bearings for an intersection, DERIVED from the curves that meet there.

    This is the replacement for authoring an intersection and then hand-nudging each arm's
    angle: the roads already know where they run, so the arm angles are a measurement, not a
    setting. For every road through the crossing, walk `reach` metres out along the polyline in
    both directions and take the bearing of the chord. A road that ENDS at the crossing contributes
    one arm; a road that passes through contributes two.

    `reach` IS THE TAIL LENGTH, and that is the whole point (2026-08-15). It used to be 30 m, on
    the reasoning that a long chord reports "the direction traffic actually arrives from" better
    than one resample step. But the arm's cap is built at the TAIL distance (~18 m) and the segment
    bolted to it leaves along the road's tangent THERE -- so on a curving approach the two
    disagreed by exactly the curvature between them. Measured on `Intersection_NWAY_013`, the
    file's own reference junction: all four arm tips landed on their segment's first spine point to
    0.0000 m, and `SegmentCurve_004` still left at 247.32 deg against an arm facing 256.22 deg --
    an **8.9 deg heading break** across a joint whose positions were flawless, worth 0.5 m of edge
    gap on a 3.25 m lane and invisible unless measured.

    Matching `reach` to the tail is the fix at source. It was tried as a post-pass first and both
    routes failed: writing `rka_arm_angles` directly is undone by the rebuild (which re-derives the
    angle from the marker), and driving `rka.aim_arm_at` -- the operator built for exactly this --
    **core-dumps in `--background`** (an unengaged `PointerRNA`; it needs UI context). Interactively
    that operator remains the right tool for a one-off manual fit.

    Returns (angles_deg_sorted, lanes_by_arm) — exactly the `arm_angles` string and per-arm lane
    counts `rka.build_intersection`'s NWAY preset takes."""
    px, py = rec["x"], rec["y"]
    arms = []
    for nm in (rec["a"], rec["b"]):
        pts = roads[nm]["pts"]
        tier = roads[nm]["tier"]
        i, best = 0, float("inf")
        for k, p in enumerate(pts):
            d = math.hypot(p[0] - px, p[1] - py)
            if d < best:
                best, i = d, k
        for direction in (-1, 1):
            run, j = 0.0, i
            while 0 <= j + direction < len(pts) and run < reach:
                run += math.dist(pts[j][:2], pts[j + direction][:2])
                j += direction
            if run < reach * 0.5:
                continue                       # the road ends here — no arm this way
            ang = math.degrees(math.atan2(pts[j][1] - py, pts[j][0] - px)) % 360.0
            arms.append((ang, TIERS[tier]["lanes"], tier))
    arms.sort(key=lambda a: a[0])
    merged = []
    for a in arms:                              # fold arms within 12 deg — one approach, not two
        if merged and min((a[0] - merged[-1][0]) % 360.0,
                          (merged[-1][0] - a[0]) % 360.0) < 12.0:
            continue
        merged.append(a)
    return [m[0] for m in merged], [m[1] for m in merged]


def build_intersection_auto(roads, rec, context, kerb_radius=8.0, tail=14.0):
    """Build one intersection with every arm angle and lane count measured off the incident
    curves. Nothing here is hand-set except the kerb radius and the approach tail length."""
    angles, lanes = arms_at(roads, rec)
    if len(angles) < 3:
        return None, "only %d arm(s)" % len(angles)
    angles = angles[:4]
    lanes = lanes[:4]
    tier = "T2" if any(roads[n]["tier"] == "T2" for n in (rec["a"], rec["b"])) else "T3"
    t = TIERS[tier]
    context.scene.cursor.location = (rec["x"], rec["y"], max(rec["za"], rec["zb"]))
    kw = dict(preset='NWAY', arm_angles=",".join("%.2f" % a for a in angles),
              lane_width=t["lane_width"], lanes=max(lanes), kerb_radius=kerb_radius,
              tail_length=tail, curb_style=t["curb"], traffic_side='LEFT')
    for k, v in zip(("lanes_arm1", "lanes_arm2", "lanes_arm3", "lanes_arm4"), lanes):
        kw[k] = v
    try:
        ret = bpy.ops.rka.build_intersection('EXEC_DEFAULT', **kw)
    except Exception as exc:
        return None, str(exc)
    if ret != {'FINISHED'}:
        return None, str(ret)

    # GIVE EVERY ARM THE ROAD'S OWN MEDIAN. Without this the arms come out flush while the roads
    # meeting them carry a 1.2-3.0 m median (see TIERS), so every lane centreline at the seam is
    # offset by half the median -- the ports coincide to the millimetre and not one LANE lines up.
    # That is precisely the "touching is not connecting" case, and it was the whole of the first
    # regeneration's 80 UNJOINED joints. `Arm.median_half` ignores the value on a one-way arm, so
    # setting it unconditionally is safe.
    coll = _newest_intersection_collection()
    if coll is not None and t["median_width"] > 0.0:
        for o in coll.objects:
            if "rka_arm_name" in o.keys():
                o["rka_arm_median_width"] = t["median_width"]
        # Called directly, not via `rka.rebuild_from_handles` -- that operator's poll needs an
        # active object in the right context, which a headless batch build does not have.
        from road_kit_authoring import ops_intersection as opint
        opint.rebuild_intersection_in_place(context, coll)
    return ret, "%d arms @ %s" % (len(angles), "/".join("%.0f" % a for a in angles))


def _newest_intersection_collection():
    """The intersection collection built most recently -- `bpy.ops` hands back only a status set,
    and this addon names junctions `Intersection_<preset>_%03d`, so the highest suffix is it."""
    colls = [c for c in bpy.data.collections
             if c.library is None and "rka_arm_names" in c.keys()]
    return max(colls, key=lambda c: c.name) if colls else None


def offset_polyline(pts, d, closed=False):
    """Lateral offset of a polyline by `d` metres — positive is LEFT of travel."""
    n = len(pts)
    out = []
    for i, p in enumerate(pts):
        a = pts[(i - 1) % n] if closed else pts[max(0, i - 1)]
        b = pts[(i + 1) % n] if closed else pts[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        L = math.hypot(tx, ty) or 1.0
        out.append((p[0] - ty / L * d, p[1] + tx / L * d, p[2]))
    return out


def carriageways(pts, lanes, lane_width, median, closed=True):
    """Split a two-way centreline into the two ONE-WAY carriageways it actually is.

    A line split needs a one-way trunk — a gore on a two-way centreline is meaningless, because
    the branch would have to cross opposing traffic. A real expressway deck is not one road with
    four lanes anyway; it is two carriageways either side of a median, which is exactly what the
    22 m T1 section describes. So this is not a workaround for the split primitive, it is the
    deck being modelled the way it is built.

    Keep-left: traffic travelling along the polyline's own direction runs on the LEFT half, so
    carriageway A is offset left; carriageway B is the right half, reversed so it too runs
    forward along its own points.

    THE OFFSET IS THE MEDIAN EDGE, NOT THE LANE-BLOCK CENTRE. A one-way piece anchors its lanes on
    the INNER EDGE of its lane block (`lane_profile`'s DIVIDE anchor with no reverse lanes: lanes
    sit at +0.5w, +1.5w ... from the spine), exactly as `intersection_kit.build_segment_from_spine`
    lays them out. Returning the block's CENTRE instead (the old `median/2 + lanes*w/2`) pushed the
    whole carriageway half a block outboard, so the interchange's lanes landed one full lane away
    from the ring's own -- measured on the T1 deck: interchange lanes at 5.85 m / 9.35 m from the
    centreline against the ring segment's 2.35 m / 5.85 m. They overlapped by one lane and matched
    on none, so endpoint proximity (4.5 m) could not join the interchange to the road either side
    of it and four of the eight interchanges were islands in the lane graph
    (`tools/check_road_network.py`).

    This is the same centred-versus-edge-anchored mistake that put a split's gore ~3.25 m off in
    `ops_split.branch_offsets`, in a second place. There is now one rule: a one-way datum is the
    edge its lanes start from."""
    half = median / 2.0
    a = offset_polyline(pts, +half, closed)
    b = list(reversed(offset_polyline(pts, -half, closed)))
    return a, b


def index_at_station(pts, s, closed=True):
    """Fractional POINT INDEX at arc-length `s` -- `split_at` cuts by index, while every
    interchange position is known as a distance along the ring, so one of the two has to be
    converted and this is it."""
    st = [0.0]
    ring = list(pts) + ([pts[0]] if closed else [])
    for a, b in zip(ring, ring[1:]):
        st.append(st[-1] + math.dist(a[:2], b[:2]))
    total = st[-1]
    if total <= 0.0:
        return 0.0
    s = (s % total) if closed else max(0.0, min(total, s))
    for i in range(len(st) - 1):
        if st[i] <= s <= st[i + 1]:
            seg = st[i + 1] - st[i]
            return i + ((s - st[i]) / seg if seg > 0 else 0.0)
    return float(len(pts) - 1)


def interchange_reservations(roads):
    """`{road_name: [(s0, s1), ...]}` -- the roads that `build_carriageways` builds ITSELF, so the
    ordinary chunk builder must not also build road there.

    WHAT THIS USED TO BE, and why it is now three lines. It used to space the interchanges apart:
    each one reserved a window around its gore, clamped by a `SPLIT_LEAD` so two neighbours could
    not claim the same stretch, and the leftovers were handed to the ordinary chunk builder. Both
    halves of that job have moved:

      * `ops_split.carriageway_chunk_pieces` now cuts the carriageway into chunks itself, and
        merges two interchanges whose windows overlap into ONE chunk carrying both auxiliary
        lanes -- which is a better answer than spacing them apart, because on this island they
        genuinely do overlap (`JCT_AIRPORT` begins 24 m before its neighbour's approach ends).
      * the whole carriageway is reserved regardless, because every metre of it is built here.

    So the `leads`/`chain` figures this returned were already dead by the time they were read.
    What is left is the statement of ownership, which is still needed:

      * THE WHOLE CARRIAGEWAY -- `build_carriageways` emits every chunk of it.
      * THE RAMPS, entirely. `carriageway_chunk_pieces` rebuilds the whole ramp polyline (only its
        first point moves, onto the gore seed), so leaving the ramp road to the ordinary chunk
        builder as well produced the SAME ramp twice: measured, `rc_IC_YAMATE_00` (209.8 m) and
        `spine_IC_YAMATE_split_ramp_001` (244.7 m) running to a shared touchdown at (700, 208, 0),
        each with its own pavement and its own line of columns."""
    res = {}
    if "LOOP" not in roads:
        return res
    for rid, gore, touch, kind, note in P.INTERCHANGES:
        # Both halves of a pair: the exit ramp AND its separate entry ramp are each their own road
        # here, and each is rebuilt by `build_carriageways` as its own piece.
        for rmp in ([rid, rid + P.ENTRY_SUFFIX] if kind == "pair" else [rid]):
            if rmp not in roads:
                continue
            # Just SHORT of the full length, never at or past it: `in_reservation` takes both
            # endpoints modulo the road length so a ring interval can wrap the seam, and on an
            # open road `total % total` folds back to 0 -- an interval of `(0, total)` reserves a
            # single point instead of the whole road, and `(0, total + 1)` reserves the first
            # metre. Both look like the reservation silently doing nothing.
            res[rmp] = [(0.0, max(0.0, _ring_total(roads[rmp]["pts"], False) - 1e-3))]
    res["LOOP"] = [(0.0, max(0.0, _ring_total(roads["LOOP"]["pts"],
                                              roads["LOOP"]["closed"]) - 1e-3))]
    return res


def _ring_total(pts, closed=True):
    ring = list(pts) + ([pts[0]] if closed else [])
    return sum(math.dist(a[:2], b[:2]) for a, b in zip(ring, ring[1:]))


def in_reservation(s, intervals, total):
    """Is arc-length `s` inside any reserved interval, wrapping the ring seam?"""
    for s0, s1 in intervals:
        a, b = s0 % total, s1 % total
        if a <= b:
            if a <= s <= b:
                return True
        elif s >= a or s <= b:      # the interval crosses the seam
            return True
    return False


def loop_window(pts, s_center, back, fwd, closed=True):
    """An OPEN sub-polyline of a closed ring, centred on an arc-length and wrapping the seam.

    A split needs `taper + auxiliary` metres of trunk BEHIND the gore, and on a ring that run
    frequently crosses the point where the polyline happens to start. Slicing naively there
    yields a stub and the split is refused for a reason that is an artefact of where the author
    began drawing."""
    st = [0.0]
    for p, q in zip(pts, pts[1:]):
        st.append(st[-1] + math.dist(p[:2], q[:2]))
    total = st[-1]
    if closed:
        total += math.dist(pts[-1][:2], pts[0][:2])

    def at(s):
        s = s % total if closed else max(0.0, min(total, s))
        ring = pts + [pts[0]] if closed else pts
        acc = 0.0
        for a, b in zip(ring, ring[1:]):
            seg = math.dist(a[:2], b[:2])
            if acc + seg >= s:
                t = (s - acc) / (seg or 1.0)
                return tuple(a[d] + (b[d] - a[d]) * t for d in range(3))
            acc += seg
        return tuple(ring[-1])

    step = max(6.0, (back + fwd) / 60.0)
    out, s = [], s_center - back
    while s <= s_center + fwd + 1e-6:
        out.append(at(s))
        s += step
    return out


def station_of(pts, probe, closed=True):
    """Arc-length of the point on the polyline CLOSEST TO `probe` — projected onto the segments,
    not snapped to the nearest vertex.

    The vertex-only version of this was a real bug with a very misleading signature. The
    expressway LOOP is an 8-vertex chamfered rectangle, so a gore sitting mid-edge — which every
    interchange on the east and north edges does — is up to **239 m** from the nearest vertex
    even though it is 0 m from the line. The split was then built a quarter of a kilometre from
    its ramp, and the segment builder, asked to run pavement between two points that far apart in
    the wrong direction, produced a deck that dived to Z -2.4 and climbed to +15.5 against a
    12 m deck. A wrong station does not fail loudly; it produces geometry that merely looks
    strange, which is why this is worth a comment."""
    acc, best, s_best = 0.0, float("inf"), 0.0
    ring = list(pts) + ([pts[0]] if closed else [])
    for a, b in zip(ring, ring[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        seg = math.sqrt(L2) if L2 > 0 else 0.0
        if seg > 0.0:
            t = max(0.0, min(1.0, ((probe[0] - a[0]) * dx + (probe[1] - a[1]) * dy) / L2))
            d = math.hypot(probe[0] - (a[0] + dx * t), probe[1] - (a[1] + dy * t))
            if d < best:
                best, s_best = d, acc + seg * t
        acc += seg
    return s_best


def _half_width(tier):
    """HALF the paved width of a tier -- which is what `GN_RoadSupport`'s `Half Width` means.

    It was being handed `lane_width * (lanes + lanes_backward)`, i.e. the FULL width, so every
    embankment came out twice as wide as designed. `GN_RoadSupport` builds the fill as
    `2 * (Half Width + delta * Fill Slope)`, so the error is doubled again in the output: the T1C
    carriageway is 7.0 m of pavement and was growing a 15.2 m embankment where it meets the ground
    and a 26 m one at the fill/pier changeover, against 8.2 m and 19 m from the correct figure.
    That is most of the "the pillar expands horizontally to an unreasonable size near ground
    level" -- the toe term vanishes as delta goes to zero, but the doubled base width does not."""
    return tier["lane_width"] * (tier["lanes"] + tier["lanes_backward"]) / 2.0



def report_tool_signals():
    """Ask the ADDON what it thinks of what we just built, and print it.

    THE PLACEMENT DOES NOT GET ITS OWN OPINION OF GEOMETRY. Everything above measures the AUTHORED
    polylines -- the `TIGHT` lines come from `island_v3_plan`, before a single piece exists -- and
    that is the wrong thing to trust, because what ships is the BUILT piece: the profile decides
    where a lane's centreline runs, the seed re-anchors a ramp, the landing moves a touchdown, and
    every one of those has silently changed a road's geometry at some point in this file's history
    without the plan noticing. So the build finishes by asking the same two tools an author would
    click, on the same scene:

        `ops_geometry_check.check_scene_geometry`  -- is each road drivable (GRADE/KINK/RADIUS/
                                                      CORNER), per exported lane
        `ops_joint_check.check_scene_joints`       -- do the connections meet, edge AND angle

    One implementation, three consumers: the panel buttons, this batch build, and
    `tools/check_road_network.py` on the exported sidecar. A batch run that disagreed with the
    button would be worse than no check at all.

    Reports and does not raise: which findings are acceptable is a judgement (four of the island's
    interchanges are knowingly under-radius and named in `island_v3_plan`'s `NEEDS_AUTHORING`), and
    a builder that refused to save would just get run with the check turned off."""
    try:
        import bpy as _bpy
        from road_kit_authoring import ops_geometry_check as ogc
        from road_kit_authoring import ops_joint_check as ojc
    except Exception as exc:                          # noqa: BLE001
        print("  tool signals unavailable (%s)" % exc)
        return
    ctx = _bpy.context
    try:
        geo = ogc.check_scene_geometry(ctx)
    except Exception as exc:                          # noqa: BLE001
        print("  geometry signal failed: %s" % exc)
        geo = []
    counts = {}
    for f in geo:
        counts[f[2]] = counts.get(f[2], 0) + 1
    print("  TOOL geometry: %s" % (", ".join("%s=%d" % kv for kv in sorted(counts.items()))
                                    or "clean"))
    for f in sorted(geo, key=lambda x: x[2])[:6]:
        print("    %s: %s -- %s" % (f[1], f[2], f[3]))
    try:
        problems, n_links, n_lanes = ojc.check_scene_joints(ctx)
    except Exception as exc:                          # noqa: BLE001
        print("  joint signal failed: %s" % exc)
        return
    real = [p for p in problems if p["status"] != "UNMEASURABLE"]
    kinds = {}
    for p in real:
        kinds[p["status"]] = kinds.get(p["status"], 0) + 1
    print("  TOOL joints: %d link(s) over %d lane(s); %s"
          % (n_links, n_lanes,
             ", ".join("%s=%d" % kv for kv in sorted(kinds.items())) or "all aligned"))
    import lane_joints as _lj
    # FLIPPED first: a seam whose two lanes point at each other is not a bad seam, it is a road
    # pointing the wrong way, and it makes every distance measured across it meaningless.
    for p in sorted(real, key=lambda q: (q["status"] != "FLIPPED",
                                          -(q.get("gap_left") or 0.0)))[:6]:
        print("    %s" % _lj.describe(p))


def build_road_support(roads, terrain, coll):
    """ONE support run per ROAD, over its whole centreline -- not one per segment.

    WHAT GOES WRONG PER-SEGMENT. A highway is built as many pieces: an arterial is cut into chunks
    between crossings, and an expressway carriageway is additionally cut around every interchange.
    Support attached per piece gives each piece its OWN pier line, and `pier_stations` starts
    counting from that piece's origin -- so the 30 m bent spacing RESETS at every seam, putting two
    bents a couple of metres apart at a chunk join and leaving an odd gap on the other side. Worse,
    two pieces that cover the same ground (the interchange ramp is authored as a road AND rebuilt
    as the split's ramp piece) each raise a full set of columns through the same air.

    Measured before this change: 94 objects carrying `RoadSupport` over 26,227 m of centreline for
    a 23,902 m network -- ~2,300 m of it doubled, with `rc_IC_YAMATE_00` (209.8 m) and
    `spine_IC_YAMATE_split_ramp_001` (244.7 m) being the same ramp down to a shared touchdown at
    (700, 208, 0).

    A road's understructure is a property of the ROAD, not of how many pieces we happened to cut
    its surface into, so it is derived once from the road's own centreline. The support object is
    independent of every road piece (it must be: `GN_RoadSupport` does not pass its input geometry
    through -- its Join Geometry takes only the piers and the embankment -- so stacking it on a
    spine REPLACES that spine's pavement with the columns, which is a viaduct of bare pillars
    holding up nothing)."""
    if terrain is None:
        return 0
    n = 0
    for name, road in sorted(roads.items()):
        pts = list(road["pts"])
        if road.get("closed") and len(pts) > 2:
            pts = pts + [pts[0]]
        if len(pts) < 2:
            continue
        t = TIERS[road["tier"]]
        obj = emit_curve("support_%s" % name.replace(" ", "_"), pts, coll)
        kc.road_support(obj, terrain, half_width=_half_width(t))
        n += 1
    return n


def _kerb_lane_offset(tier):
    """Lateral distance from an arterial's centreline to the CENTRE of its kerbside travel lane.

    Read off `lane_profile`, never typed in: the arterial's own profile already knows where its
    lanes are, and hand-computing it here would be a second formula to disagree with the road --
    the exact class of defect `ROAD_KIT_REDESIGN.md` 1.1 records. For the T2 arterial (2+2 lanes
    of 3.25 m about a 3 m median) this is 1.5 + 1.5 x 3.25 = 6.375 m."""
    import lane_profile as lp
    prof = lp.profile_from_scalars(tier["lanes"], tier["lanes_backward"], tier["lane_width"],
                                   median_width=tier["median_width"])
    fwd = [s for s in prof.slots if s.is_drivable() and s.dir == lp.FWD]
    if not fwd:
        return 0.0
    return abs(lp.slot_offset(prof, prof.index_of(fwd[-1].id)))


#: How far a ramp's touchdown may be from an arterial and still be considered to be JOINING it.
#: Beyond this the ramp meets something else (another expressway, through a junction) and must be
#: left where it was authored -- see the measurements in `land_ramp_on_kerb`.
MAX_LANDING_SNAP = 20.0


def land_ramp_on_kerb(pts, roads, at_end=True):
    """Move a ramp's ARTERIAL end from the arterial's centreline onto its kerbside lane.

    A touchdown in `island_v3_plan.INTERCHANGES` is a point on the arterial's CENTRELINE, and the
    ramp was run to exactly that point -- so every ramp arrived in the middle of the road, on the
    median, rather than merging into the nearside lane. Two things go wrong with that. Visually it
    is simply not how a ramp meets a street. Functionally it is worse: the ramp's last point is
    then ~6 m from any arterial LANE, outside the 4.5 m junction radius the runtime joins lanes
    with, so the expressway never attaches to the street network at all -- which is exactly what
    the connectivity gate reports as "isolated from the wider network".

    The target arterial is found by proximity (excluding the expressway and other ramps), the
    offset comes from that arterial's own profile via `_kerb_lane_offset`, and the side is
    whichever side the ramp already approaches from -- so a ramp is never swung across the road it
    is joining. The shift is a smoothstep confined to the last `BLEND` metres, the same bounded
    shape `ops_split.seed_ramp` uses to release its gore seed, so the two transforms compose in
    the run-out and neither of them touches the governing curve. (Both were once whole-length
    blends; both halved the radius the plan had just proved. See `seed_ramp` for the numbers.)"""
    if len(pts) < 2:
        return list(pts)
    idx = -1 if at_end else 0
    tip = pts[idx]
    best = None
    for name, road in roads.items():
        if name in ("LOOP",) or road["tier"] in ("RAMP", "T1", "T1C"):
            continue
        rp = road["pts"]
        for a, b in zip(rp, rp[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            if L2 <= 0.0:
                continue
            t = max(0.0, min(1.0, ((tip[0] - a[0]) * dx + (tip[1] - a[1]) * dy) / L2))
            px, py = a[0] + dx * t, a[1] + dy * t
            d = math.hypot(tip[0] - px, tip[1] - py)
            if best is None or d < best[0]:
                L = math.sqrt(L2)
                best = (d, (px, py), (dx / L, dy / L), road["tier"])
    if best is None:
        return list(pts)
    _d, (px, py), (ux, uy), tier = best
    if _d > MAX_LANDING_SNAP:
        # NOT A LANDING -- AN INVENTION. This function corrects a touchdown that is already ON an
        # arterial (authored on its centreline) onto that arterial's kerbside lane: a ~6 m move.
        # When the nearest arterial is far away the ramp was not authored to meet it at all, and
        # dragging it there both fabricates a junction and wrecks the alignment. Measured across
        # the eight ramps, the authored touchdowns sit 0.00-0.12 m off their arterial (5.7-16.7 m
        # for the two entries, which are authored from the loop end); `JCT_AIRPORT` sits 32.8 m
        # away because it is an expressway-to-expressway ramp with no arterial to land on, and
        # being dragged 26.4 m onto the nearest street took it from a 120.9 m radius to 62.7 m.
        # Leaving it alone is correct: a jct ramp's connection is made by the split/merge
        # machinery, not by kerb landing.
        print("  NOTE: ramp end is %.1f m from the nearest arterial (max %.0f m) -- left on its "
              "authored alignment, not landed" % (_d, MAX_LANDING_SNAP))
        return list(pts)
    off = _kerb_lane_offset(TIERS[tier])
    if off <= 0.0:
        return list(pts)
    # Keep the ramp on the side it already approaches from.
    nx, ny = -uy, ux
    side = 1.0 if ((tip[0] - px) * nx + (tip[1] - py) * ny) >= 0.0 else -1.0
    target = (px + nx * off * side, py + ny * off * side, tip[2])

    st = [0.0]
    for a, b in zip(pts, pts[1:]):
        st.append(st[-1] + math.dist(a[:3], b[:3]))
    total = st[-1] or 1.0
    dx, dy, dz = target[0] - tip[0], target[1] - tip[1], target[2] - tip[2]

    # CONFINED TO THE APPROACH, AND SMOOTH. This used to spread the shift linearly over the
    # WHOLE ramp (`w = s / total`), which is a shear in a FIXED direction -- and a fixed-direction
    # displacement growing with arc length bends a curve badly wherever it turns relative to that
    # direction. Measured: it roughly HALVED every ramp's radius between the plan and the built
    # spine, with the point count unchanged -- `IC_RINKAI_E` 61.7 m -> 29.3 m, `IC_RINKAI_W`
    # 74.2 m -> 41.6 m -- silently undoing the geometry `fit_ramp` had just searched for.
    #
    # A touchdown correction is a LOCAL move: the ramp needs to arrive on the kerbside lane
    # instead of the centreline, which is ~6 m over the last stretch, not a reshaping of the whole
    # alignment. Smoothstep over `BLEND` metres spreads that 6 m across ~120 m (about 3 degrees)
    # so it adds no kink of its own, and leaves everything upstream exactly as designed.
    BLEND = 120.0
    out = []
    for p, s in zip(pts, st):
        d = (total - s) if at_end else s          # distance from the end being moved
        t = 1.0 - min(1.0, d / BLEND)
        w = t * t * (3.0 - 2.0 * t)               # smoothstep: zero slope at both ends
        out.append((p[0] + dx * w, p[1] + dy * w, p[2] + dz * w))
    out[idx] = target
    return out


def ramp_touchdown_cuts(roads):
    """`{road_name: [fractional_index, ...]}` -- where each interchange ramp meets an arterial.

    A TOUCHDOWN IS A JUNCTION, and has to cut the road it lands on for the same reason an
    intersection does. The runtime joins lanes tail-to-head within `JUNCTION_RADIUS`, so a ramp
    that arrives part-way along an arterial lane has nothing to attach to however close it is:
    measured on `IC_CHUO`, the ramp tip sits 3.24 m from the nearest POINT of an arterial lane --
    comfortably inside the 4.5 m radius -- but 14.19 m from the nearest lane HEAD, because the
    arterial's own chunk boundaries are wherever its intersections happen to be. That is the whole
    of "LOOP_A isolated from the wider network": the expressway was laterally correct and
    topologically detached.

    Cutting here gives the arterial a lane boundary at the touchdown, so the ordinary joining rule
    applies and no special case is needed anywhere downstream."""
    from collections import defaultdict as _dd
    out = _dd(list)
    for rid, p3, _par, _grade, _ok, kind in P.ramps():
        if len(p3) < 2:
            continue
        # Authored deck-end-first, so the arterial end is the last point (both exits and the
        # entries, which are only reversed later when they are handed to a carriageway).
        tip = p3[-1]
        best = None
        for name, road in roads.items():
            if name in ("LOOP",) or road["tier"] in ("RAMP", "T1", "T1C"):
                continue
            rp = road["pts"]
            for i, (a, b) in enumerate(zip(rp, rp[1:])):
                dx, dy = b[0] - a[0], b[1] - a[1]
                L2 = dx * dx + dy * dy
                if L2 <= 0.0:
                    continue
                t = max(0.0, min(1.0, ((tip[0] - a[0]) * dx + (tip[1] - a[1]) * dy) / L2))
                d = math.hypot(tip[0] - (a[0] + dx * t), tip[1] - (a[1] + dy * t))
                if best is None or d < best[0]:
                    best = (d, name, i + t)
        if best is not None:
            out[best[1]].append(best[2])
    return out


def build_carriageways(roads, context, opts, terrain=None):
    """Build the expressway as a CHAIN OF ORDINARY SEGMENTS -- plain deck chunks, plus one chunk
    per interchange carrying an extra lane on the side that needs it -- plus one piece per ramp.

    WHY IT IS NO LONGER ONE PIECE. It was, and the reasoning was sound as far as it went: nothing
    about the carriageway divides it (a 3,278 m ring with zero crossing cuts), and a `ProfileSet`
    can carry every interchange as a station, so the twelve pieces the even older code produced
    were an artefact of a piece being able to hold one lane COUNT. What that argument missed is
    that the expressway then became the ONLY road on the island with its own shape. Every other
    road here is a chain of segments meeting at authored joints; the deck was one 3.3 km carrier
    with an interchange machinery of its own. Two shapes cost two of everything -- two ways support
    attaches, two ways a joint comes to exist, two answers to "what do I select to edit this
    stretch" -- and a control point you cannot drag without moving a road four interchanges away.

    So the deck is cut into the same primitives the rest of the map is made of, by
    `ops_split.carriageway_chunk_pieces`. AN INTERCHANGE IS NOT A NEW KIND OF THING: it is a
    segment whose ProfileSet opens one AUX slot, which is what an arterial's turn-lane widening
    already is. Because each chunk begins and ends on the plain cross-section, the seams between
    them are ordinary segment<->segment joints -- authored by `weld_chunk_ports` and measured
    edge-to-edge by `lane_export.emit_joint_links`, with no expressway special case anywhere.

    SUPPORT MOVED OFF THE PIECES with the same change. A pier line shared the one-piece deck's own
    spine datablock, which is exactly what `build_road_support` warns against once the road is
    several pieces: `pier_stations` counts from each piece's own origin, so the 30 m bent spacing
    would reset at every chunk seam. The deck now takes one continuous support run per ROAD, like
    every other road on the island. Ramps still carry theirs on their own spine -- a ramp IS one
    piece, and its built spine is the seeded/landed one, not the authored polyline."""
    from road_kit_authoring.ops_split import carriageway_chunk_pieces
    from road_kit_authoring.ops_segment import _build_segment_from_points
    from road_kit_authoring.ops_intersection import RkaBuildError

    t1 = TIERS["T1"]
    coll = kc.get_coll("RK_SPLITS")
    ramps = {rid: p3 for rid, p3, _par, _grade, _ok, _kind in P.ramps()}

    # Which interchanges ride which carriageway. Every interchange EXITS carriageway A; only a
    # "pair" also ENTERS carriageway B -- a Shuto-style "half" is deliberately one-way, so
    # building an entry there would invent a movement the design says does not exist.
    ics = []
    for rid, gore, touch, kind, note in P.INTERCHANGES:
        ramp = ramps.get(rid)
        if ramp is None:
            continue
        side = P.interchange_side(rid)
        ics.append((rid, land_ramp_on_kerb(list(ramp), roads, at_end=True), 'split', side))
        if kind == "pair":
            entry = ramps.get(rid + P.ENTRY_SUFFIX)
            if entry is None:
                print("  NOTE: %s is a pair but has no entry ramp -- skipping its on-ramp" % rid)
                continue
            ics.append((rid + P.ENTRY_SUFFIX,
                        land_ramp_on_kerb(list(reversed(entry)), roads, at_end=False),
                        'merge', side))
    plan = {"LOOP": ics}

    n_deck = n_ic = n_ramp = n_sup = 0
    for cw, ics in plan.items():
        if cw not in roads or not ics:
            continue
        try:
            out = carriageway_chunk_pieces(
                list(roads[cw]["pts"]), ics, lanes=t1["lanes"], lane_width=t1["lane_width"],
                median=t1["median_width"], min_chunk=MIN_CHUNK,
                closed=bool(roads[cw].get("closed")))
        except RkaBuildError as exc:
            print("  SKIP carriageway %-8s %s" % (cw, exc))
            continue

        # --- the deck, in ring order ----------------------------------------------------------
        # Named after the interchange it carries where it carries one, so the piece you select in
        # the outliner says which exit it is. `_build_segment_from_points` appends `_%03d`.
        for ch in out["chunks"]:
            ic_names = ch["interchanges"]
            base_name = ("%s_%s" % (cw, ic_names[0])) if ic_names else ("%s_deck" % cw)
            built = _build_segment_from_points(
                context, coll, ch["pts"], t1["lane_width"], ch["lanes"], ch["lanes_backward"],
                'NONE', 'NONE', 0.15, 0.25, False, "", "",
                base_name=base_name, traffic_side='LEFT', align='right',
                profile_set=ch["profile_set"],
                # Every deck chunk is `role='mainline'` in one group, and that is not a name
                # collision waiting to happen: `lane_kit.resolve_links` keys on
                # (group, role, SLOT), and an interchange's auxiliary slot (`<rid>_A0`) exists on
                # exactly one chunk. Nothing addresses a plain travel lane symbolically -- those
                # links come from the authored joints instead.
                link_group=cw, link_role="mainline")
            c = built.get("coll")
            if c is None:
                continue
            # The deck IS the expressway (80 km/h); a ramp is a ramp (45 km/h). Tagging them the
            # same would either excuse the deck's curves or condemn the ramps'.
            c["rka_design_speed"] = float(t1["speed"])
            # Which ramp relations this chunk carries, for `lane_export._carriageway_links`.
            # Flat comma-joined strings, not a Dictionary export -- see CLAUDE.md's note on the
            # registration scanner choking on nested generic Dictionary properties.
            if ch["exits"]:
                c["rka_link_exits"] = ",".join(ch["exits"])
            if ch["entries"]:
                c["rka_link_entries"] = ",".join(ch["entries"])
            if ic_names:
                c["rka_interchange"] = ",".join(ic_names)
                n_ic += 1
            else:
                n_deck += 1

        # --- one piece per ramp ---------------------------------------------------------------
        kinds = {rid: k for rid, _p, k, _s in ics}
        for rid, spec in sorted(out["ramps"].items()):
            built = _build_segment_from_points(
                context, coll, spec["pts"], t1["lane_width"], spec["lanes"], 0,
                'NONE', 'NONE', 0.15, 0.25, False, "", "",
                base_name="%s_ramp" % rid, traffic_side='LEFT',
                lanes_end=spec["lanes_end"], align=spec["align"],
                profile_set=spec.get("profile_set"), link_group=cw, link_role=rid)
            c = built.get("coll")
            if c is None:
                continue
            c["rka_design_speed"] = float(TIERS["RAMP"]["speed"])
            c["rka_link_kind"] = "EXIT" if kinds.get(rid) == 'split' else "ENTRY"
            n_ramp += 1
            if opts.support and terrain is not None:
                # Sharing the spine's DATABLOCK (not just its points) is what makes road and
                # columns move together when a control point is dragged. It still has to be a
                # separate OBJECT: `GN_RoadSupport` does not pass its input geometry through, so
                # stacking it on the spine would replace the deck with the columns.
                spine = built["spine_obj"]
                sup = bpy.data.objects.new("support_%s" % c.name, spine.data)
                sup.matrix_world = spine.matrix_world.copy()
                c.objects.link(sup)
                kc.road_support(sup, terrain, half_width=_half_width(TIERS["RAMP"]))
                n_sup += 1

    print("  expressway built as segments: %d plain deck chunk(s) + %d interchange chunk(s) "
          "(one extra lane each) + %d ramp(s); ramp support runs sharing a spine: %d"
          % (n_deck, n_ic, n_ramp, n_sup))
    return n_deck + n_ic, n_ramp



# -------------------------------------------------------------- joints: trim back, then AUTHOR
#
# THE DEFECT THIS FIXES. A road was cut AT each crossing and an intersection was then built
# CENTRED on that same crossing, with arms reaching `tail_length` (14-31 m, auto-grown for wide
# arms) outward. So every chunk ran through the whole junction pad and out the far side of its own
# arm: the road was authored twice over the junction, and no chunk end ever landed on an arm tip.
# Measured on the shipped file: of 204 segment ports, ZERO were within 5 m of any of the 83 arm
# tips (76 within 20 m, 128 further). That is why the island exported 0 authored joints -- there
# was no seam to author, only an overlap.
#
# The fix is ordering plus trimming: build intersections FIRST (they decide their own
# `tail_length`), then cut each chunk back to the arm tip it runs into, then record the joint.
#
# ON PROXIMITY. This uses distance, and that is not the thing the codebase refuses to do. The rule
# is that connectivity is AUTHORED data rather than re-derived at runtime from whatever happens to
# be nearby. Here the generator IS the author: it laid both pieces out, so it knows they meet, and
# it writes that down ONCE into `rka_linked_to` where a human can see it, edit it or delete it.
# What it does not do is decide the lanes -- `lane_export.emit_joint_links` MEASURES those
# edge-to-edge, and `check_road_network.py` 2b/2c then reports any joint written here that the
# geometry does not actually support. So a wrong guess surfaces as a failure, never as a silent
# connection.

ARM_TRIM_REACH = 45.0   # > the largest auto-grown tail_length (30.8 m on the widest island arm)
ARM_ON_ROAD_TOL = 6.0   # an arm this far off the polyline belongs to a different road
PORT_WELD_TOL = 0.05    # consecutive chunks of one road already meet this exactly


def piece_collection_names():
    """Every LOCAL road-piece collection name right now -- diffed either side of a build operator
    to learn which piece it just made (`bpy.ops` returns a status set, not the result)."""
    return {c.name for c in bpy.data.collections
            if c.library is None and ("rka_curve_object" in c.keys()
                                      or "rka_arm_names" in c.keys()
                                      or "rka_lanes_a" in c.keys())}


def collect_arm_markers():
    """`[(coll_name, arm_obj, tip_xyz, origin_xy), ...]` for every intersection arm currently built.

    The junction ORIGIN comes along because an arm tip alone cannot say which side of the junction
    it is on, and that is the question `trim_chunk_to_arms` has to answer."""
    out = []
    for coll in bpy.data.collections:
        if coll.library is not None or "rka_arm_names" not in coll.keys():
            continue
        origin = list(coll.get("rka_origin", (0.0, 0.0, 0.0)))
        for o in coll.objects:
            if "rka_arm_name" in o.keys():
                p = o.matrix_world.translation
                out.append((coll.name, o, (p.x, p.y, p.z), (origin[0], origin[1])))
    return out


def _nearest_vertex(pts, p):
    best_i, best_d = 0, float("inf")
    for i, q in enumerate(pts):
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        if d < best_d:
            best_i, best_d = i, d
    return best_i, best_d


def _leaves_by_this_arm(pts, i, ap, origin):
    """Does the chunk actually depart the junction along THIS arm?

    A road that runs straight THROUGH a junction passes within a few metres of the arm on the far
    side as well as its own, and both are inside any sane distance tolerance -- so distance alone
    matched chunks to the opposite arm. The symptom was unmistakable once measured: heading breaks
    of exactly 180.00 deg, and one arm claimed by three different segments at once.

    The question distance cannot answer is WHICH SIDE. An arm tip sits outward from the junction
    origin; the chunk that belongs to it must extend outward the same way. Comparing those two
    directions settles it, and 180 deg wrong stops being representable."""
    ox, oy = origin
    ax, ay = ap[0] - ox, ap[1] - oy
    la = math.hypot(ax, ay)
    if la < 1e-6:
        return False
    # Where the chunk goes from here: the end AWAY from this arm. If the arm sits near the tail of
    # the polyline the road runs back toward its head, and vice versa. (Getting this backwards
    # inverted the whole test and rejected most legitimate matches -- 77 arm joints fell to 30.)
    far = pts[0] if i > len(pts) / 2.0 else pts[-1]
    bx, by = far[0] - ox, far[1] - oy
    lb = math.hypot(bx, by)
    if lb < 1e-6:
        return False
    return (ax * bx + ay * by) / (la * lb) > 0.7071      # within 45 degrees


def trim_chunk_to_arms(pts, arms, claimed=None):
    """Cut `pts` back so each end that runs into an intersection STARTS/ENDS exactly on that
    intersection's arm tip. Returns `(pts, arm_at_start, arm_at_end)`.

    Ending exactly ON the tip is the point: the arm tip is where the junction's own lane movements
    begin (`intersection_kit.build_ports` and a movement's `entry_far` are the same point), so a
    chunk that stops there has its lane ribbons meeting the junction's edge-to-edge, which is what
    Step 4's check measures. Stopping short leaves a gap; running past re-paves the pad.

    `claimed` -- a set of arm names already taken. An arm serves ONE road; without this, several
    chunks converging on a junction all bind to the same arm and the last one to be aimed wins,
    leaving the others visibly skewed."""
    claimed = claimed if claimed is not None else set()
    hit_start = hit_end = None
    for coll_name, arm_obj, ap, origin in arms:
        if arm_obj.name in claimed:
            continue
        i, d_line = _nearest_vertex(pts, ap)
        if d_line > ARM_ON_ROAD_TOL:
            continue                      # this arm belongs to some other road passing nearby
        if not _leaves_by_this_arm(pts, i, ap, origin):
            continue                      # right distance, wrong side of the junction
        d0 = math.hypot(pts[0][0] - ap[0], pts[0][1] - ap[1])
        d1 = math.hypot(pts[-1][0] - ap[0], pts[-1][1] - ap[1])
        if d0 <= d1 and d0 < ARM_TRIM_REACH:
            if hit_start is None or d0 < hit_start[0]:
                hit_start = (d0, i, arm_obj, ap)
        elif d1 < ARM_TRIM_REACH:
            if hit_end is None or d1 < hit_end[0]:
                hit_end = (d1, i, arm_obj, ap)
    out = list(pts)
    arm_start = arm_end = None
    if hit_end is not None:
        _d, i, arm_obj, ap = hit_end
        keep = out[:i]
        if len(keep) >= 1:
            out = keep + [(ap[0], ap[1], ap[2])]
            arm_end = arm_obj
    if hit_start is not None:
        _d, i, arm_obj, ap = hit_start
        keep = out[i + 1:]
        if len(keep) >= 1:
            out = [(ap[0], ap[1], ap[2])] + keep
            arm_start = arm_obj
    return (out, arm_start, arm_end) if len(out) >= 2 else (list(pts), None, None)


def report_arm_heading_breaks():
    """MEASURE, do not fix: how far each junction arm's facing differs from the road bolted to it.
    Returns `(n_over_tolerance, worst_deg)`.

    This reports rather than corrects on purpose. Both ways of correcting it after the fact are
    dead ends -- see `arms_at`, which is where the angle is now got right in the first place -- so
    what is left worth having is the number, printed every build, so a regression in the source fix
    shows up immediately instead of silently costing edge alignment at every junction."""
    from road_kit_authoring import ops_segment as opseg, spine_io
    n, worst = 0, 0.0
    for coll in bpy.data.collections:
        if coll.library is not None or "rka_curve_object" not in coll.keys():
            continue
        spine = bpy.data.objects.get(coll["rka_curve_object"])
        if not spine_io.is_spine(spine):
            continue
        pts = opseg._spine_control_points(spine)
        if len(pts) < 2:
            continue
        for o in coll.objects:
            target_name = o.get("rka_linked_to")
            if o.get("rka_port") not in ("A", "B") or not target_name:
                continue
            arm = bpy.data.objects.get(target_name)
            if arm is None or "rka_arm_name" not in arm.keys() or not arm.users_collection:
                continue
            jc = arm.users_collection[0]
            if "rka_arm_names" not in jc.keys():
                continue
            # Outward tangent: the direction the road LEAVES the junction by, taken from the two
            # points at this end -- the same "last two distinct points" rule the alignment check
            # itself uses, so the two can never disagree about what this road's heading is.
            at_a = (o["rka_port"] == "A")
            p0, p1 = (pts[0], pts[1]) if at_a else (pts[-1], pts[-2])
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            if math.hypot(dx, dy) < 1e-6:
                continue
            ang = math.degrees(math.atan2(dy, dx)) % 360.0
            names = list(jc["rka_arm_names"])
            idx = names.index(arm["rka_arm_name"])
            delta = abs((ang - list(jc["rka_arm_angles"])[idx] + 180.0) % 360.0 - 180.0)
            if delta < 0.05:
                continue
            n += 1
            worst = max(worst, delta)
    return n, worst


def stamp_joint(coll, port_tag, target_marker):
    """Record that `coll`'s `port_tag` end connects to `target_marker` -- the same `rka_linked_to`
    key the interactive `Extend From Arm`/`Extend From Port` operators write, so a generated joint
    and a hand-authored one are the same data and `live_edit`'s propagation moves both.

    STAMPED ON THE PORT, NOT THE PIECE. `ops_segment._stamp_link` writes this key onto the piece's
    ORIGIN marker, which is right for what it does -- a piece extended FROM somewhere has exactly
    one parent. A chunk of a road network has TWO ends, each meeting a different junction, and one
    key per piece cannot hold both: stamping the origin marker twice silently kept only the second
    (measured: 153 joints stamped, 69 survived). `port_A`/`port_B` are explicitly supported link
    dependents (`ops_intersection._is_link_dependent_marker`, the dual-end linking fix), and
    `port_A`/`port_B` are the spine's first/last point respectively -- so this is the shape the
    addon already provides, not a new convention."""
    if coll is None or target_marker is None:
        return False
    from road_kit_authoring import live_edit
    dep = next((o for o in coll.objects if o.get("rka_port") == port_tag), None)
    if dep is None or dep is target_marker:
        return False
    dep[live_edit.RKA_LINKED_TO_KEY] = target_marker.name
    return True


def weld_chunk_ports():
    """Author the segment-to-segment joints: consecutive chunks of one road already END and START
    on the same point (149 of 204 ports on the shipped file were within 5 cm), so the seam exists
    -- it was simply never written down. One `rka_linked_to` per coincident pair."""
    from road_kit_authoring import live_edit, ops_segment, spine_io
    ports = []
    for coll in bpy.data.collections:
        if coll.library is not None or "rka_curve_object" not in coll.keys():
            continue
        spine = bpy.data.objects.get(coll["rka_curve_object"])
        if not spine_io.is_spine(spine):
            continue
        # POSITION COMES FROM THE SPINE, not from the port Empty's own transform. `port_A`/`port_B`
        # ARE the spine's first/last control point by definition, but the Empty is a derived
        # display marker and is not necessarily placed yet at this point in a batch build. Reading
        # the Empty instead welded 15 interchange ramp ports to each other while they all still sat
        # unplaced -- links up to 1340 m long, which then exported as authored joints no lane could
        # possibly cross. The spine is the source of truth and is always correct.
        pts = ops_segment._spine_control_points(spine)
        if len(pts) < 2:
            continue
        ends = {"A": tuple(pts[0][:3]), "B": tuple(pts[-1][:3])}
        for o in coll.objects:
            tag = o.get("rka_port")
            if tag in ("A", "B"):
                ports.append((coll, o, ends[tag]))
    n = 0
    linked = set()
    for i, (coll_a, obj_a, pa) in enumerate(ports):
        for coll_b, obj_b, pb in ports[i + 1:]:
            if coll_a is coll_b:
                continue
            key = tuple(sorted((coll_a.name, coll_b.name)))
            if key in linked or math.dist(pa, pb) > PORT_WELD_TOL:
                continue
            # Only one side records the link: it means "this end FOLLOWS that one", and both
            # sides following each other is a cycle for `live_edit`'s propagation to chase.
            # One record is enough for the joint too -- `authored_joints` pairs the collections.
            if obj_a.get(live_edit.RKA_LINKED_TO_KEY):
                continue        # this end already meets a junction arm; that link wins
            obj_a[live_edit.RKA_LINKED_TO_KEY] = obj_b.name
            linked.add(key)
            n += 1
    return n


def build_from_curve(curve_obj, tier, context):
    t = TIERS[tier]
    for o in context.selected_objects:
        o.select_set(False)
    curve_obj.select_set(True)
    context.view_layer.objects.active = curve_obj
    return bpy.ops.rka.build_segment_from_curve(
        'EXEC_DEFAULT', curve_object=curve_obj.name,
        lane_width=t["lane_width"], lanes=t["lanes"], lanes_backward=t["lanes_backward"],
        median_width=t["median_width"], curb_l_style=t["curb"], curb_r_style=t["curb"],
        sidewalk_l_width=t["sidewalk"], sidewalk_r_width=t["sidewalk"],
        traffic_side='LEFT')


# ---------------------------------------------------------------------------------- main
def parse_args():
    import argparse
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="island_v3_to_roadkit.py")
    ap.add_argument("--spacing", type=float, default=12.0,
                    help="resample distance for the smoothed curves, m (default 12)")
    ap.add_argument("--curves-only", action="store_true",
                    help="emit curves + the crossing worklist, do not build road pieces")
    ap.add_argument("--build", action="store_true", help="run build_segment_from_curve per chunk")
    ap.add_argument("--support", action="store_true",
                    help="attach the live GN_RoadSupport modifier to every emitted curve — piers/embankment derived from deck height over terrain, re-deriving as you drag a point")
    ap.add_argument("--splits", action="store_true",
                    help="build every expressway interchange as a real line SPLIT (and MERGE "
                         "where it serves both directions), instead of leaving the ramp as an "
                         "unconnected chunk")
    ap.add_argument("--intersections", action="store_true",
                    help="also build every detected INTERSECTION with auto-measured arm angles")
    ap.add_argument("--keep-seed-curves", action="store_true",
                    help="keep the rc_* seed polylines after building. They are sampled ONCE to "
                         "seed each piece's own spine and are dead weight afterwards, so they are "
                         "deleted by default; keep them to debug the smoothing/splitting passes")
    ap.add_argument("--only", default="", help="comma-separated road names to process")
    ap.add_argument("--out", default="island_v3_roads.blend")
    return ap.parse_args(argv)


def main():
    opts = parse_args()
    bpy.ops.wm.read_homefile(use_empty=True)
    kc.setup_units()
    if not hasattr(bpy.types.Scene, "rka"):
        rka.register()
    context = bpy.context

    cu_coll = kc.get_coll("RK_CURVES")
    xi_coll = kc.get_coll("RK_CROSSINGS")

    roads = collect_roads(opts.spacing)
    if opts.only:
        keep = {s.strip() for s in opts.only.split(",")}
        roads = {k: v for k, v in roads.items() if k in keep}

    cuts, recs = crossings(roads)
    # A ramp touchdown cuts the arterial it lands on -- see `ramp_touchdown_cuts`.
    if opts.splits:
        for rname, fracs in ramp_touchdown_cuts(roads).items():
            cuts.setdefault(rname, [])
            cuts[rname] = list(cuts[rname]) + list(fracs)

    # --- crossing worklist: this is what rka.build_intersection consumes next -------------
    kinds = {}
    for r in recs:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        e = bpy.data.objects.new(
            "xing_%s_%s_%s" % (r["kind"][:3].lower(), r["a"][:10], r["b"][:10]), None)
        e.empty_display_type = 'SPHERE' if r["kind"] == "INTERSECTION" else 'PLAIN_AXES'
        e.empty_display_size = 10.0
        e.location = (r["x"], r["y"], max(r["za"], r["zb"]))
        for k in ("kind", "a", "b", "angle", "dz"):
            e[k] = r[k]
        xi_coll.objects.link(e)
        if r["kind"] == "FLYOVER" and r["dz"] < 4.5:
            print("  WARNING: %s over %s has only %.2f m clearance (4.5 m needed)"
                  % (r["a"], r["b"], r["dz"]))

    # --- curves, one per chunk between crossings ------------------------------------------
    n_curve = n_built = n_tight = n_support = 0
    # A ground body for the support raycast. The layout blend owns the real
    # terrain; with none present the rule simply finds no hit and emits nothing,
    # which is the correct degenerate answer rather than an error.
    terrain = None
    if opts.support:
        terrain = kc.box("SupportGround", -G.ORIGIN, G.ORIGIN, -G.ORIGIN,
                          G.ORIGIN, -1.0, 0.0, kc.get_coll("RK_GROUND"), "dirt")
    # The expressway and its ramps are built by `build_carriageways`, so the ordinary chunk
    # builder must skip them entirely. Without that the ring is authored twice over the same
    # ground: once as ordinary chunks and once as deck chunks, overlapping and never meeting
    # (`tools/check_road_network.py` is the check for it).
    reservations = interchange_reservations(roads) if opts.splits else {}
    n_reserved = 0
    for rname, intervals in reservations.items():
        if rname not in roads:
            continue
        pts = roads[rname]["pts"]
        extra = []
        for s0, s1 in intervals:
            extra += [index_at_station(pts, s0, roads[rname]["closed"]),
                      index_at_station(pts, s1, roads[rname]["closed"])]
        cuts.setdefault(rname, [])
        cuts[rname] = list(cuts[rname]) + extra

    # --- intersections FIRST, so each chunk can be trimmed back to a real arm tip ------------
    # Order matters and used to be the other way round. An intersection decides its own
    # `tail_length` at build time (it auto-grows for wide arms -- 14 m to 30.8 m on this island),
    # so where its arms END is not knowable until it exists. Building chunks first therefore had
    # to guess, and did not: every chunk ran straight through the pad. See `trim_chunk_to_arms`.
    n_xing = 0
    if opts.intersections:
        for r in recs:
            if r["kind"] != "INTERSECTION":
                continue
            ret, note = build_intersection_auto(roads, r, context)
            if ret:
                n_xing += 1
            else:
                print("  SKIP intersection %s x %s: %s" % (r["a"], r["b"], note))
        print("  intersections built with auto-measured arms: %d" % n_xing)
    arm_markers = collect_arm_markers()
    claimed_arms = set()          # an arm serves ONE road -- see `trim_chunk_to_arms`
    n_trimmed = n_arm_joint = 0

    for name, road in sorted(roads.items()):
        tier = road["tier"]
        chunks = split_at(road["pts"], cuts.get(name, []), road["closed"])
        res_here = reservations.get(name, [])
        total = _ring_total(road["pts"], road["closed"]) if res_here else 0.0
        for i, chunk in enumerate(chunks):
            if res_here:
                mid = chunk[len(chunk) // 2]
                if in_reservation(station_of(road["pts"], mid, road["closed"]), res_here, total):
                    n_reserved += 1
                    continue
            cname = "rc_%s_%02d" % (name.replace(" ", "_"), i)
            chunk, arm_a, arm_b = trim_chunk_to_arms(chunk, arm_markers, claimed_arms)
            for _a in (arm_a, arm_b):
                if _a is not None:
                    claimed_arms.add(_a.name)
            if arm_a is not None or arm_b is not None:
                n_trimmed += 1
            obj = emit_curve(cname, chunk, cu_coll)
            obj["tier"] = tier
            obj["road"] = name
            n_curve += 1
            r = min_radius(chunk)
            if r < TIERS[tier]["min_radius"] * 0.99:   # 1% = polyline measurement noise
                n_tight += 1
                print("  TIGHT: %-26s r=%7.1f m < %5.1f m min for %s (%d km/h) — ease the "
                      "authored polyline here" % (cname, r, TIERS[tier]["min_radius"], tier,
                                                  TIERS[tier]["speed"]))
            if opts.build:
                try:
                    before = piece_collection_names()
                    ret = build_from_curve(obj, tier, context)
                    if ret == {'FINISHED'}:
                        n_built += 1
                        # AUTHOR the joint(s) this chunk was just trimmed to fit. One link per
                        # end; a chunk between two junctions records both. The operator returns
                        # only a status set, so the new piece is identified by diffing the piece
                        # collections either side of the call.
                        new = piece_collection_names() - before
                        new_coll = bpy.data.collections.get(sorted(new)[0]) if new else None
                        if new_coll is not None:
                            # The road class this was built as, carried onto every lane by
                            # `lane_export.collect_pieces` so `check_road_network` check 6 can ask
                            # whether the geometry actually supports it.
                            new_coll["rka_design_speed"] = float(TIERS[tier]["speed"])
                        # `port_A` is the spine's first point, `port_B` its last -- the same two
                        # ends `trim_chunk_to_arms` cut back.
                        for tag, arm in (("A", arm_a), ("B", arm_b)):
                            if stamp_joint(new_coll, tag, arm):
                                n_arm_joint += 1
                    else:
                        print("  FAILED: %s -> %s" % (cname, ret))
                except Exception as exc:
                    print("  ERROR : %s -> %s" % (cname, exc))
            # NB support is NOT attached here. It is derived once per ROAD, after every piece is
            # built, by `build_road_support` -- see its docstring for why per-chunk support
            # double-builds columns and resets the bent spacing at every seam.

    n_split = n_merge = 0
    if opts.splits:
        n_split, n_merge = build_carriageways(roads, context, opts, terrain=terrain)

    # Support for every road built as SEVERAL pieces -- which now includes the expressway deck,
    # since it is a chain of chunks like everything else. One continuous run per road, so the 30 m
    # bent spacing does not reset at each seam (see `build_road_support`).
    #
    # Only the RAMPS are excluded: each is genuinely one piece, and it gets its support inside
    # `build_carriageways` off its BUILT spine -- the seeded, landed alignment, which is not the
    # authored polyline this pass would use. Entry ramps (`<rid>_EN`) have to be excluded by name
    # too; leaving them in gave every one of them two full sets of columns through the same air.
    if opts.support:
        ramp_roads = set()
        for rid, _g, _t, kd, _n in P.INTERCHANGES:
            ramp_roads.add(rid)
            if kd == "pair":
                ramp_roads.add(rid + P.ENTRY_SUFFIX)
        rest = {k: v for k, v in roads.items() if k not in ramp_roads}
        n_support = build_road_support(rest, terrain, kc.get_coll("RK_SUPPORT"))

    # --- the remaining joints: chunk-to-chunk along one road -------------------------------
    # `or opts.splits`: the expressway deck is a chain of chunks now, and its seams are authored
    # here like any other road's. A `--splits` run without `--build` would otherwise leave the
    # whole deck unjointed.
    n_weld = weld_chunk_ports() if (opts.build or opts.splits) else 0

    # --- how well does each arm FACE the road bolted to it? ----------------------------------
    n_skew, worst_aim = (report_arm_heading_breaks() if opts.build and opts.intersections
                         else (0, 0.0))
    print("  arm/road heading breaks over 0.05 deg: %d (worst %.2f deg)" % (n_skew, worst_aim))

    # --- drop the seed curves ---------------------------------------------------------------
    # `rc_<road>_<nn>` is the polyline handed to `build_segment_from_curve`, which SAMPLES it once
    # to seed a self-contained spine and never refers to it again (see that operator's docstring).
    # Keeping them leaves a second, dead copy of every road in the file that looks like road and is
    # not: it exports nothing, edits nothing, and is the obvious thing to grab by mistake in the
    # viewport. `--keep-seed-curves` retains them for debugging the smoothing/splitting passes.
    n_seed = 0
    if opts.build and not opts.keep_seed_curves:
        for o in [x for x in cu_coll.objects if x.name.startswith("rc_")]:
            data = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.curves.remove(data)
            n_seed += 1
        if not cu_coll.objects and not cu_coll.children:
            bpy.data.collections.remove(cu_coll)
        print("  seed curves dropped after build: %d" % n_seed)

    if n_reserved:
        print("  chunks left to the interchanges (not built as ordinary road): %d" % n_reserved)
    print("  joints AUTHORED: %d chunk<->arm (from %d trimmed chunk ends), %d chunk<->chunk"
          % (n_arm_joint, n_trimmed, n_weld))
    report_tool_signals()
    print("ROADKIT: roads=%d  chunks/curves=%d  built=%d  intersections=%d  splits=%d  "
          "merges=%d  tight=%d"
          % (len(roads), n_curve, n_built, n_xing, n_split, n_merge, n_tight))
    print("  crossings: " + ("  ".join("%s=%d" % kv for kv in sorted(kinds.items()))
                             or "none"))
    print("  total centreline: %.0f m  support runs: %d (one per road)"
          % (sum(_plen(r["pts"]) for r in roads.values()), n_support))
    if bpy.app.background:
        kc.save_blend(ROOT, opts.out)


if __name__ == "__main__":
    main()

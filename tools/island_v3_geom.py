#!/usr/bin/env python3
"""Tokyo-Bay Island v3 — geometry, in GAME METRES (X east, Y north, centre origin).

Plan A's fictional island, condensed to the original ~2 km proposal and re-planned as a
clean north->south transect:

    MOUNTAIN -> FARMLAND -> RESIDENTIAL -> NEON (three centres) -> HARBOUR / BAY

Frame: 4032 x 4032 m = 8 x 8 districts of 504 m. The grid maths is GRID_N-parametric, so this
is a constant change, not a pipeline change.

Consumed by tools/island_v3_plates.py — no drawing lives here.
"""

from __future__ import annotations

import math
import random

# --------------------------------------------------------------------------- scale
#: WORLD SCALE. Every coordinate in this file is authored at the PLAN's own scale — the island was
#: designed as a 2016 m frame and the numbers below still read as the metres someone drew — and is
#: then multiplied by `SCALE` on the way out. 2.0 gives a 4032 m world, 8 x 8 districts of 504 m.
#:
#: WHY 4 km AND NOT MORE. The budget is +-3800 m from the origin on each axis; at `SCALE` 2.0 the
#: frame reaches +-2016 m and the drawn coastline +-1330 m, comfortably inside it. Float precision
#: is not what sets that budget (a float32 resolves ~0.24 mm at 2 km) -- it is the project's own
#: working limit, and this is the largest clean multiple of the authored plan that fits: `WORLD` is
#: `DISTRICT * GRID_N`, so 8 x 504 m is what "4 km" means here.
#:
#: WHAT SCALES AND WHAT DOES NOT. This is a similarity transform on the LAYOUT: the coast, the
#: water, the terrain contours (their radii AND their heights), the centrelines, the zone
#: envelopes, the landmarks. It is NOT applied to anything real-world-sized, all of which lives in
#: `island_v3_plan.py`: a lane is still 3.25 m, a block is still 168 m, an expressway deck is still
#: 12 m up, and `MAX_GRADE` is still 4%. That asymmetry is the point of a game map and this repo
#: already names it — see `taper_factor` in `ROAD_POINT_GRAPH.md` 8g: "the world is not 1:1".
#:
#: Scaling the contour HEIGHTS with the plan is what keeps the ground gate green for free: a
#: uniform scale leaves every dz/dx exactly where it was, so the arterials routed around the relief
#: at 4%/8% stay routed around it, every ramp keeps its grade, and the harbour's landward taper
#: keeps the number it was cut to. It also makes the vertical read as part of the same world: at
#: 2.0 the massif is a 760 m coastal peak over a 4 km island (Hakone's pass is 874 m), the
#: expressway deck flies at 24 m, and the reclaimed harbour and airport sit at +4 and +8 m.
#:
#: The ramps get their room back, too: `RAMP_MIN_RADIUS` is fixed at 59.1 m because a car's
#: cornering does not scale, so a bigger layout is strictly easier for the interchange fitter --
#: which is the same lever that made the 0.75 experiment harder, read from the other end.
SCALE = 2.0

def _s(v):
    return v * SCALE


def _p(pt):
    return tuple(v * SCALE for v in pt)


def _ps(pts):
    return [(x * SCALE, y * SCALE) for (x, y) in pts]


def _hill(cx, cy, bands, rot=0.0):
    """A contour set, scaled. Bands are `(rx, ry, z, tag)` — an elevation is a NUMBER, not a
    substring of a label: the old `"+320 snow"` form had to be re-parsed by everyone who wanted
    the height and could not survive a scale at all."""
    return dict(cx=_s(cx), cy=_s(cy), rot=rot,
                bands=[(_s(rx), _s(ry), _s(z), tag) for (rx, ry, z, tag) in bands])


# --------------------------------------------------------------------------- grid
DISTRICT = 504.0
GRID_N = 8
WORLD = DISTRICT * GRID_N      # 4032
ORIGIN = WORLD / 2.0           # 2016

# --------------------------------------------------------------------------- land
# Organic north/west coast; straight south edges where land is reclaimed.
MAIN_BASE = _ps([
    (-40, 962), (200, 940), (420, 880), (610, 780), (742, 640), (824, 470),
    (872, 275), (886, 80), (862, -110), (800, -250), (700, -350), (560, -430),
    (430, -470), (300, -500), (150, -520), (-40, -545), (-230, -570), (-420, -560),
    (-580, -510), (-712, -420), (-812, -290), (-872, -110), (-890, 110), (-858, 340),
    (-790, 560), (-672, 748), (-500, 878), (-280, 945),
])
# Reclaimed port peninsula — fused to the mainland by a wide land neck (drive straight in).
HARBOUR = _ps([(-380, -520), (140, -500), (140, -880), (-120, -930), (-380, -845)])
# Offshore reclaimed airport island, south-east.
AIRPORT = _ps([(380, -990), (960, -990), (960, -700), (560, -700), (380, -780)])

def fractalize(poly, iters=3, amp=_s(30.0), decay=0.55, seed=5, chord=_s(110.0)):
    """Midpoint displacement along each edge — multi-scale headlands and coves.

    The SMOOTH polygon stays the design skeleton (the ring road offsets from it, zones are
    authored against it); only the drawn/collided coastline is fractal, so detail can be
    retuned without moving a single road.
    """
    rng = random.Random(seed)
    pts = list(poly)
    for it in range(iters):
        a = amp * (decay ** it)
        out = []
        for i in range(len(pts)):
            p, q = pts[i], pts[(i + 1) % len(pts)]
            out.append(p)
            dx, dy = q[0] - p[0], q[1] - p[1]
            L = math.hypot(dx, dy) or 1.0
            d = rng.uniform(-a, a) * min(1.0, L / chord)
            out.append(((p[0] + q[0]) / 2 - dy / L * d, (p[1] + q[1]) / 2 + dx / L * d))
        pts = out
    return pts


MAIN = fractalize(MAIN_BASE)

# Offshore rocks — scenery and boat targets only; deliberately NOT in LAND, so they carry
# no roads, no buildings and no streaming cost.
ISLETS = [_ps(p) for p in (
    [(-980, 690), (-930, 716), (-902, 686), (-936, 652)],
    [(966, 512), (1000, 536), (982, 574), (944, 552)],
    [(408, -1078), (452, -1064), (446, -1030), (404, -1042)],
)]

LAND = [MAIN, HARBOUR, AIRPORT]

# THE BAY — a drowned river mouth (ria) cutting ~850 m north into the city. It is the
# river's own outlet, so one water feature does everything: snowmelt spine, city divider,
# harbour, and the reason the main arterial needs a 300 m bridge.
BAY = _ps([(176, -148), (298, -142), (352, -262), (368, -436), (352, -628), (330, -1010),
           (86, -1010), (112, -628), (130, -436), (148, -262)])
WATER = [BAY]            # LAGOON appended below, once defined

# --------------------------------------------------------------------- point tests
def inside(poly, x, y):
    n, ins = len(poly), False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ins = not ins
        j = i
    return ins


def on_land(x, y):
    if any(inside(p, x, y) for p in WATER):
        return False
    return any(inside(p, x, y) for p in LAND)


def ellipse(cx, cy, rx, ry, n=44, rot=0.0):
    c, s = math.cos(rot), math.sin(rot)
    out = []
    for k in range(n):
        a = 2 * math.pi * k / n
        ex, ey = rx * math.cos(a), ry * math.sin(a)
        out.append((cx + ex * c - ey * s, cy + ex * s + ey * c))
    return out


def pull_ashore(cx, cy, pts):
    """Shrink each point toward the centre until it lands — clips a band to the coast."""
    out = []
    for (x, y) in pts:
        for k in range(44):
            t = 1.0 - k * 0.023
            qx, qy = cx + (x - cx) * t, cy + (y - cy) * t
            if on_land(qx, qy):
                out.append((qx, qy))
                break
        else:
            out.append((cx, cy))
    return out


def plen(pts, close=False):
    q = pts + [pts[0]] if close else pts
    return sum(math.dist(a, b) for a, b in zip(q, q[1:]))


# ------------------------------------------------------------------------- terrain
# Main massif (north) + a SPUR ridge running south down the west side, ending in a hill
# directly behind the western neon centre — the "mountain on the neon side".
MASSIF = _hill(-300, 790, [(470, 250, 120, ""), (340, 178, 240, ""),
                           (215, 112, 320, "snow"), (120, 62, 380, "peak")])
SPUR = _hill(-640, 330, [(255, 330, 80, ""), (150, 205, 140, "hill")],
             rot=math.radians(-14))

PASS = _p((-452, 690))      # touge summit-of-drivable, +220
PEAK = _p((-300, 800))      # +380, scenery only
SPUR_TOP = _p((-648, 258))  # +140, city shrine

# ---------------------------------------------------------------------- green void
# Castle park — the Imperial Palace shrunk to a 260 x 210 m castle (v2's 520 x 440 would
# be a quarter of this map). Still does the same job: every road bends around it.
CASTLE = _ps([(-215, 190), (-60, 200), (-15, 150), (-10, -5), (-70, -55), (-205, -48),
              (-248, 10), (-250, 130)])
MOAT = _ps([(-248, 218), (-48, 230), (12, 172), (18, -20), (-58, -84), (-218, -76),
            (-282, -4), (-284, 142)])
SHIBA_PK = _ps([(268, -258), (410, -250), (416, -140), (272, -148)])   # temple + tower park
SHRINE_PK = _ps([(-742, 330), (-596, 348), (-566, 452), (-700, 470)])  # forest shrine, spur foot
UENO_PK = []
PARKS = (MOAT, SHIBA_PK, SHRINE_PK)

# ---------------------------------------------------------------------------- river
# One spine: snowmelt -> paddies -> residential -> becomes the harbour inlet at the sea.
RIVER = _ps([(-320, 770), (-190, 668), (-30, 610), (140, 572), (300, 520), (392, 430),
             (402, 300), (360, 158), (300, 18), (262, -120), (238, -146)])

# Shinano-style: the river is the city's organising line, not scenery at the edge.
# Neon A (old town, west bank) and Neon B (new centre, east bank) face each other across
# it; one multi-arch stone bridge is the hero crossing and the natural chokepoint.
ARCH_BRIDGE = (_p((236, -46)), _p((330, -52)))  # Bandai-bashi analogue, upstream

# Coastal pine windbreak (海岸松林) inside the ocean-facing dune line — the single
# cheapest "Sea of Japan coast" signal there is, and a continuous occluder wall.
PINE = _ps([(872, 275), (824, 470), (742, 640), (610, 780), (556, 740), (672, 618),
            (750, 452), (800, 268)])

# Remnant lagoon (潟) stranded among the paddies — what the drained back-swamp left.
LAGOON = _ps([(298, 646), (432, 664), (474, 592), (398, 538), (296, 562)])
WATER.append(LAGOON)

# ----------------------------------------------------------------------------- T1
def chamfer(x0, y0, x1, y1, c):
    return [(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1),
            (x0 + c, y1), (x0, y1 - c), (x0, y0 + c)]


LOOP = _ps(chamfer(-560, -450, 500, 290, 115))   # flagship circuit, closed
# Airport access is a RAMP off the loop beside S3 — not a second route. It leaves the
# south-east chamfer, hugs the coast, and turns straight onto the bridge head.
AIRPORT_RAMP = _ps([(500, -335), (620, -306), (722, -266), (788, -214), (800, -180)])
AIRPORT_ROAD = _ps([(905, -720), (820, -800), (700, -848)])   # continues on the island
PORTSPUR = _ps([(-150, -450), (-190, -570), (-215, -700), (-230, -810)])
WESTRAD = _ps([(-560, 60), (-660, 130), (-742, 236), (-790, 380), (-800, 520)])
TUNNEL = _p((-800, 520))
TOUGE = _ps([(-780, 592), (-620, 626), (-724, 668), (-566, 700), (-668, 740), (-520, 762),
             (-452, 690)])

# ------------------------------------------------------------------- T2 arterials
# The white lanes are AUTHORED, not a clipped lattice. Rule: every arterial is
# end-to-end and terminates on the RING or on another arterial — never in mid-air.
# RING is computed as an inward offset of the coastline (see plates.offset_inward).
RING_INSET = _s(62.0)

ARTERIALS = [
    # N-S trunk: port -> Neon A -> castle -> residential -> farmland -> the mountain foot.
    # It ENDS at the foot (see ARTERIAL_CLASS below): north of (52, 430) the massif's apron
    # starts, and a trunk road does not climb a 92% flank.
    # ITS HEAD IS THE PORT VERTEX, the same `(-96, -560)` Port road starts from and Nishihama-dori
    # ends on -- see the note under `Port road`.
    ("Chuo-dori", _ps([(-96, -560), (-70, -430), (-58, -300), (-30, -120), (26, 62),
                   (20, 250), (52, 430)])),
    # E-W trunk: west coast -> Neon C -> Neon A -> ARCH BRIDGE -> Neon B -> east coast.
    # The one low corridor across the western upland — it threads the spur's southern foot.
    ("Rinkai-dori", _ps([(-806, -128), (-620, -150), (-430, -166), (-160, -140), (40, -80),
                     (236, -46), (330, -52), (500, -96), (680, -104), (818, -76)])),
    # residential cross-street, from the spur's eastern foot (on Nishi-dori) to the coast
    ("Yamate-dori", _ps([(-215, 250), (-40, 240), (140, 228), (460, 218), (700, 208),
                     (812, 196)])),
    # farmland cross-street, along the massif's southern foot and south of the lagoon
    ("Nogyo-michi", _ps([(-185, 345), (-40, 385), (52, 430), (300, 455), (500, 470),
                     (650, 450), (778, 412)])),
    # east N-S: coastal station -> Neon B -> BAY BRIDGE -> port
    ("Hama-dori", _ps([(804, 336), (762, 130), (706, -60), (620, -206), (500, -330),
                   (420, -400), (366, -440), (124, -424), (20, -486), (-96, -560)])),
    # west N-S: Rinkai-dori -> Neon C -> the shrine at the foot -> Nogyo-michi. It runs at the
    # spur's eastern FOOT; the climb to the shrine, the tunnel and the pass is WESTRAD/TOUGE's.
    ("Nishi-dori", _ps([(-430, -166), (-380, -60), (-300, 60), (-250, 160), (-215, 250),
                    (-185, 345)])),
    # Port distributor on the reclaimed peninsula. `(-96, -560)` IS THE PORT VERTEX -- the one
    # place four arterials meet, written once here and read four times.
    #
    # It used to be three places 76 m apart: Chuo-dori's head at `(-96, -598)`, Hama-dori's tail at
    # `(-96, -566)`, and this pair. The seeder clusters crossings within `CROSSING_NEAR` (21 m), so
    # Chuo-dori did not join the cluster at all -- it ran 76 m PAST the junction to a dead end, and
    # because it passed through rather than stopped, it contributed TWO arms. The result was a
    # 5-arm pad with a stub hanging off it (JCT_0004, hand-corrected to 4 arms in `Island_base
    # .blend` 2026-09-04). Four roads ending on one vertex is what the plan's own rule already
    # says -- "terminates on another arterial, never in mid-air" -- and it gives one arm each.
    ("Port road", _ps([(-96, -560), (-180, -640), (-250, -740), (-300, -860)])),

    # ---- 2026-08-31, WORLD_REBUILD_PLAN.md `W2`. Four roads into land that had none.
    #
    # These are not the old pre-terrain alignments put back. Step 1 rerouted four arterials off the
    # relief and the reach that cost was measured, for the first time, by `island_v3_reach.py`:
    # 52.5% of the island's land within 250 m of a road, with 4.71 km^2 unserved in five patches.
    # Each of these serves one of them, and each is authored where the ground already allows it --
    # measured flat and hole-free before it was written down, not drawn and then checked.
    #
    # north-east coastal plain (0.39 km^2 of flat land, no road within 250 m of any of it):
    # from Nogyo-michi's east end, inside the coastline, round to the massif's northern foot.
    ("Kitahama-dori", _ps([(778, 412), (750, 500), (690, 600), (640, 700),
                           (520, 800), (415, 850), (315, 900)])),
    # south-west shore (0.57 km^2): Rinkai-dori's west end down to the port, closing the ring.
    ("Nishihama-dori", _ps([(-806, -128), (-780, -280), (-700, -410), (-560, -490),
                            (-410, -525), (-250, -540), (-96, -560)])),
    # the harbour's own eastern apron (0.23 km^2) -- the Port road stopped at the quay.
    ("Futo-dori", _ps([(-300, -860), (-150, -900), (0, -890), (70, -800)])),
    # the airport (0.64 km^2, the whole offshore island). It runs Hama-dori -> AIRPORT_BRIDGE ->
    # AIRPORT_ROAD, so `island_v3_plan.bridge_at` carries its 14 over-water stations and
    # `seed_district_roads.height_profile` ramps it onto the +24 m deck. `local` class, not
    # mainline: 8% is what gets it up to a T1 deck height in the ~300 m of land it has.
    #
    # IT STARTS AT THE RINKAI x HAMA CROSSING, and the head vertex below is a placeholder that
    # `_terminate_at`/`_crossing_of` move there -- see `KUKO_FOOT`. It was authored from
    # (706, -60), a vertex of Hama-dori, 53 m short of that crossing; then from where its own
    # first leg met Rinkai-dori, 104 m PAST it. Both were two pads too close together on an 8%
    # airport link, for a stretch of road Rinkai-dori already provides. Three roads meeting at one
    # place is one junction.
    ("Kuko-dori", _ps([(706, -60), (800, -180), (905, -720), (820, -800), (700, -848)])),
]


def _meet(a0, a1, b0, b1):
    """Where the LINE a0->a1 meets the LINE b0->b1, in plan metres."""
    d1 = (a1[0] - a0[0], a1[1] - a0[1])
    d2 = (b1[0] - b0[0], b1[1] - b0[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        raise ValueError("_meet: the two lines are parallel")
    t = ((b0[0] - a0[0]) * d2[1] - (b0[1] - a0[1]) * d2[0]) / den
    return (a0[0] + d1[0] * t, a0[1] + d1[1] * t)




def _crossing_of(a, b):
    """Where arterials `a` and `b` actually cross, from their own vertices. Plan metres.

    ONE OWNER FOR WHERE TWO ROADS MEET. Every other arterial here terminates on another road's own
    VERTEX, so the shared coordinate is written once and read twice and there is nothing to keep in
    sync. Kuko-dori is the one that starts in the middle of a span, and writing that point as a
    literal would make this file the second owner of Rinkai-dori's alignment -- a road that
    silently stops 40 m short of the junction the day somebody moves the other one. So it is
    computed, from both reference roads' own vertices, at import.

    Segment-to-segment, not line-to-line: two arterials that bend past each other have plenty of
    places where their infinite lines meet and exactly one where the roads do."""
    A = next(p for n, p in ARTERIALS if n == a)
    B = next(p for n, p in ARTERIALS if n == b)
    for a0, a1 in zip(A, A[1:]):
        for b0, b1 in zip(B, B[1:]):
            try:
                at = _meet(a0, a1, b0, b1)
            except ValueError:
                continue
            # ON both segments, within the same 5 cm the seeder's own `seg_intersect` allows --
            # `W14`'s rule: a bound like this belongs in metres, never in parameter space.
            if (math.dist(a0, at) + math.dist(at, a1) > (math.dist(a0, a1) or 1.0) + 0.05
                    or math.dist(b0, at) + math.dist(at, b1) > (math.dist(b0, b1) or 1.0) + 0.05):
                continue
            return at
    raise ValueError("_crossing_of: %s never crosses %s" % (a, b))


def _terminate_at(name, at):
    """Move `name`'s FIRST vertex to `at`. The value is always derived -- see `_terminate_on`."""
    ai = next(i for i, (n, _pts) in enumerate(ARTERIALS) if n == name)
    pts = list(ARTERIALS[ai][1])
    pts[0] = at
    ARTERIALS[ai] = (name, pts)
    return at


#: The airport link begins AT THE RINKAI x HAMA CROSSING -- one pad, not two.
#:
#: Walk-tested 2026-09-04. `_terminate_on("Kuko-dori", "Rinkai-dori")` put the head where Kuko's
#: own first leg met Rinkai-dori, which is 104 m east of where Rinkai already crosses Hama-dori:
#: two junction pads with 48 m of carriageway between their stop lines, so ~150 m of Rinkai-dori
#: read as one continuous unmarked lozenge with no street in it. That is the SAME defect the
#: previous fix was for ("two pads that close together on an 8% airport link") -- moving the head
#: off Hama-dori's vertex onto Rinkai-dori halved the distance and did not close it.
#:
#: A crossing is a place, and three roads meeting at one place is one junction. Kuko-dori now
#: leaves the crossing itself: a 5-arm pad whose tightest corner is 43.9 deg (Kuko against
#: Rinkai-dori east), which `point_solve.solved_setback` sizes on its own. Still derived from the
#: two reference roads' own vertices, so nothing here is a second owner of either alignment.
KUKO_FOOT = _terminate_at("Kuko-dori", _crossing_of("Rinkai-dori", "Hama-dori"))

#: HILL ROADS -- `(name, foot, target elevation)`, with the SHAPE derived, not authored.
#:
#: The alignment is `island_v3_terrain.hill_road`: a switchback walk of the height field at the
#: road's own grade limit. It is derived because it cannot be drawn -- `WESTRAD` and `TOUGE` above
#: are the hand-drawn attempt, authored against the old contour PRISMS, and measured against the
#: real heightfield they run at **48% and 105%**. A line on a map cannot know a hill; a walk of the
#: field cannot help but know it.
#:
#: Only the spur is served. The massif is deliberately wilderness: 51% of the unserved northwest is
#: steeper than 60%, its only flat ground is the 760 m summit, and 697 m of climb at 8.1% is 8.6 km
#: of road -- more tarmac than the entire rest of the island -- for scenery. One `hill_road` call
#: adds it the day that changes.
#: The APPROACH is authored (it is ordinary flat road, and it is what ties the hill road to the
#: network at a real junction); the CLIMB from its last point is derived. Two owners would be one
#: too many, and this is the honest split: a person decides where the road leaves the network, the
#: terrain decides how it gets up.
HILL_ROADS = [
    # the shrine plateau at +280 (`SPUR_TOP`), leaving the Nishi-dori x Nogyo-michi junction and
    # climbing the spur's eastern flank.
    ("Shrine touge", _ps([(-185, 345), (-245, 345)]), _s(140.0)),
]

# WHAT STANDARD EACH ARTERIAL IS DESIGNED TO — a key of `island_v3_plan.MAX_GRADE`.
#
# "Is this ground drivable?" has no answer until the road says what it is. The three named trunk
# routes are mainlines (4%); the cross-streets and the port distributor are local roads (8%), and
# the extra 4% is what lets them run close to a hill's foot instead of a safe distance from it.
# `check_island_ground.py` holds each road to its own number; `--limit` overrides the lot.
#
# NONE OF THEM CLIMBS, and that is the answer to WORLD_REBUILD_PLAN.md step 1's "route around the
# relief or climb it" — measured, not assumed. The plan's own contour spacing is 92–167% inside
# the massif and 48–57% inside the spur, so the gentlest ground either hill has is far steeper
# than a touge (8.1%): there is no alignment at any grade class that gets a road up them. The
# hills are served by the T1 continuations that were always meant to — `WESTRAD` climbs to the
# tunnel portal and `TOUGE` to the pass — and those are not arterials.
ARTERIAL_CLASS = {
    "Yamate-dori": "local",     # residential cross-street
    "Nogyo-michi": "local",     # farm cross-street
    "Nishi-dori":  "local",     # west distributor, along the spur's foot
    "Port road":   "local",     # port distributor
    "Kitahama-dori":  "local",  # north-east coastal
    "Nishihama-dori": "local",  # south-west shore
    "Futo-dori":      "local",  # harbour apron
    "Kuko-dori":      "local",  # airport link (8% is what reaches the bridge deck)
    "Shrine touge":   "touge",  # the derived hill road -- 8.1%, the only class steeper than local
}


def arterial_class(name):
    return ARTERIAL_CLASS.get(name, "mainline")

# ---------------------------------------------------------------------------- rail
RAIL_MAIN = _ps([(838, 300), (800, 120), (720, -30), (600, -140), (430, -205),
                 (240, -240), (60, -230), (-120, -190), (-300, -140), (-470, -60),
                 (-600, 90), (-660, 300), (-680, 470), (-760, 560)])
RAIL_BRANCH = _ps([(-120, -190), (-90, 60), (-40, 260), (60, 430), (200, 540)])
RAIL_AIRPORT = _ps([(800, -180), (905, -720), (820, -840)])

# ------------------------------------------------------------------------- bridges
# 283 m, the main crossing. The span is the WATER plus a deliberate abutment: the bay's edge is
# at x = 131 / x = 367 on this line, so a deck ending on Hama-dori's own vertices landed 7 m and
# 0 m from the water. Each end now reaches ~20 m onto firm ground.
BAY_BRIDGE = (_p((104, -423)), _p((386, -441)))
AIRPORT_BRIDGE = (_p((800, -180)), _p((905, -720)))          # double-deck
RUNWAY = (_p((430, -930)), _p((930, -778)))

# ------------------------------------------------------------------ urban envelopes
# name, rects, frontage, depth, perimeter retention, interior infill, colour
def _rects(rects):
    return [tuple(v * SCALE for v in r) for r in rects]


ZONES = [
    # three separated neon centres — not one blob
    ("neonA", _rects([(-260, -430, 150, 30)]),   6, 12, 0.92, 0.34, "#8b8577"),   # main core
    ("neonB", _rects([(376, -330, 700, 60)]),    6, 12, 0.86, 0.26, "#8b8577"),   # electric town
    ("neonC", _rects([(-720, -300, -380, 90)]),  7, 13, 0.80, 0.20, "#8b8577"),   # hillside strip
    ("resid", _rects([(-830, 40, 620, 440), (600, 60, 850, 320)]),
                                                 8,  9, 0.80, 0.16, "#b0ab99"),
    ("farm",  _rects([(-260, 420, 620, 760),                # valley paddies
                      (600, 60, 880, 520),                  # stretches to the ocean
                      (-620, 500, -240, 730)]),             # terraces on the flank
                                                14, 11, 0.30, 0.00, "#a9af92"),
    ("port",  _rects([(-380, -930, 140, -500)]), 30, 40, 0.85, 0.10, "#9d9686"),
    ("air",   _rects([(380, -990, 960, -700)]),  26, 34, 0.45, 0.00, "#9d9686"),
]

DANCHI_BEARING = 18.0      # locked sun angle — every slab shares it
# NOT SCALED: a city block and a street width are real-world sizes, like everything in
# `island_v3_plan.BLOCKS`. See the SCALE note at the top of this file.
BLOCK = 168.0
STREET = 14.0

# --------------------------------------------------------------------- district map
# gy3 (north) -> gy0 (south); verified against measured land fraction, see plates output.
# THE THEME MAP IS AUTHORED AT ITS OWN RESOLUTION, not at `GRID_N`'s.
#
# It used to be indexed by the district grid directly, so changing `GRID_N` meant re-authoring the
# whole map -- and 8 x 8 is 64 hand-written cells describing a transect that has four bands in it.
# A theme is a fact about WHERE YOU ARE ON THE ISLAND; the grid is a streaming decision. `theme_at`
# samples this 4 x 4 map at a cell's own centre, so the two are free to move independently.
MATRIX_N = 4
MATRIX = [
    ["void",   "mtn",    "rural",  "rural"],    # north
    ["mtn",    "resid",  "resid",  "rural"],
    ["city",   "CITY",   "city",   "rural"],
    ["void",   "harbor", "harbor", "harbor"],   # south
]


def theme_at(gx, gy, n=None):
    """The theme of district `(gx, gy)` of an `n x n` grid — `gy` 0 = SOUTH, as everywhere else."""
    n = GRID_N if n is None else n
    fx = (gx + 0.5) / float(n)
    fy = (gy + 0.5) / float(n)
    mx = min(MATRIX_N - 1, int(fx * MATRIX_N))
    row = min(MATRIX_N - 1, int((1.0 - fy) * MATRIX_N))     # MATRIX row 0 is the NORTH edge
    return MATRIX[row][mx]

THEME = {
    "mtn":    ("#9DB47F", "Mountain / touge"),
    "rural":  ("#C6D5A6", "Farmland"),
    "resid":  ("#DCCFA8", "Residential"),
    "city":   ("#C99C86", "Neon (3 centres)"),
    "harbor": ("#8FA6A0", "Harbour / port / airport"),
    "void":   (None, "Void"),
}

# ------------------------------------------------------------------------ landmarks
# label, x, y, kind
LANDMARKS = [
    ("Tower — 333 m",            _s(330), _s(-196), "asset"),
    ("Temple + park",            _s(300), _s(-238), "kit"),
    ("Castle + moat",           _s(-120), _s(78), "kit"),
    ("Central station",          _s(-60), _s(-222), "kit"),
    ("Electric town",            _s(470), _s(-140), "kit"),
    ("Hillside strip",          _s(-560), _s(-120), "kit"),
    ("Shrine at the foot",      _s(-654), _s(398), "kit"),
    ("Summit shrine (okumiya)", _s(-648), _s(258), "kit"),
    ("Pass shrine",             _s(-452), _s(690), "kit"),
    ("Arch bridge — 91 m",       _s(250), _s(-182), "asset"),
    ("Lagoon",                   _s(386), _s(600), ""),
    ("Bay bridge — 166 m",       _s(223), _s(-406), "asset"),
    ("Airport bridge — 550 m",   _s(852), _s(-450), "asset"),
    ("Airport terminal",         _s(700), _s(-880), "asset"),
    ("Container port",          _s(-230), _s(-740), ""),
    ("Coastal station",          _s(800), _s(330), "kit"),
    ("Tunnel portal",           _s(-800), _s(520), "asset"),
]

SECTORS = [("S1", _s(-30), _s(290), "moat straight — start/finish"),
           ("S2", _s(500), _s(0), "electric-town esses"),
           ("S3", _s(330), _s(-450), "port hairpin — braking point"),
           ("S4", _s(-230), _s(-450), "bayshore sweep, tower on the left"),
           ("S5", _s(-560), _s(-180), "hillside climb — blind crest"),
           ("S6", _s(-560), _s(180), "spur descent into the moat")]

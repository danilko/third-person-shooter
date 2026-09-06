#!/usr/bin/env python3
"""Tokyo-Bay Island v3 — CITY PLANNING rules, in GAME METRES (X east, Y north, centre origin).

`island_v3_geom.py` is the SHAPE of the island (coast, terrain, water, the authored arterial
and rail centrelines). This module is the PLANNING LAYER on top of it — the rules from
`tokyo-bay-island-v3-cityplanning.md` made computable:

    §0  two density fields (castle distance, station distance)
    §1  block size as a rule (per-quarter spec + jitter + dogleg), not a constant
    §2  the castle gradient — which quarter a point belongs to
    §3  expressway deck Z, interchange ramps, the bridge spiral
    §4  farmland parcel grains
    §5  rail Z profile
    §6  SUPPORT DERIVATION — the one function that decides what goes UNDER a surface

PURE PYTHON, no bpy — same rule as `blender/lib/world_grid.py`. Both the SVG plates and the
Blender builder import it, so "the plates and the blend agree" is shared COMPUTATION, not
shared convention. `python3 tools/island_v3_plan.py` runs the self-tests.
"""

from __future__ import annotations

import importlib.util
import math
import os
import random
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location("island_v3_geom",
                                            os.path.join(_here, "island_v3_geom.py"))
G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(G)


# =============================================================================== §6
# SUPPORT DERIVATION — the uniform rule.
#
# There is no separate "highway system" and "ground road system". There is ONE surface
# system, and what appears UNDERNEATH a surface is DERIVED from a single number: how far
# the finished surface sits above the terrain below it.
#
#     delta = surface_z - ground_z   ->   support kind
#
# That is the whole idea. A road drawn at ground level gets nothing under it; lift the same
# road 2 m and it grows an embankment; lift it to 12 m and the embankment becomes a pier
# line. Nothing about the road piece itself changed — only its height did. Because the rule
# is a pure function of delta, it re-evaluates live while a height is dragged, which is why
# it belongs in Geometry Nodes (see build_island_v3.py) rather than in a one-time bake.
# THE RULES LIVE IN `blender/lib/road_support.py`, not here. This module is the island PLANNER;
# the road BUILDER needs the identical rules, and two copies of "what goes under a surface" is
# defect 1 one level up -- the planner would decide a ramp needs a pier line while the builder drew
# an embankment, with nothing to report the disagreement. Re-exported so this module's own callers
# (`build_island_v3.py`, `island_v3_to_graph.py`, `kit_common.py`) keep importing them from here.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "blender", "lib"))
from road_support import (                                                   # noqa: E402,F401
    SUPPORT_NONE, SUPPORT_FILL, SUPPORT_PIER, SUPPORT_CUT, SUPPORT_TUNNEL,
    AT_GRADE_TOL, FILL_MAX, CUT_MAX, FILL_SLOPE, CUT_SLOPE, PIER_SPACING, PIER_SECTION,
    DECK_THICK, support_kind, fill_footprint, pier_stations, _frange)


# =============================================================================== height
# A control point does not carry a bare Z. It carries a Z and a RULE for how that Z is
# obtained, which is what lets one road run from ground onto a deck without splitting into
# two objects. `resolve_z` is the only place these are interpreted.
Z_DRAPE  = "DRAPE"    # follow terrain (+ offset) — ordinary ground road
Z_FIX    = "FIX"      # absolute world Z, LOCKED — a deck, a bridge, an apron
Z_OFFSET = "OFFSET"   # terrain + k — a road that stays k above whatever it crosses
Z_GRADE  = "GRADE"    # interpolate between the nearest FIX neighbours, grade-limited


def resolve_z(mode, value, ground_z):
    if mode == Z_FIX:
        return value
    if mode == Z_OFFSET:
        return ground_z + value
    return ground_z              # DRAPE; GRADE is resolved by grade_profile() below


MAX_GRADE = {"mainline": 0.04, "ramp": 0.06, "local": 0.08, "touge": 0.081, "rail": 0.025}

# How far a road surface sits above the terrain it is laid on.
#
# 3 CENTIMETRES, not half a metre. Z-fighting is a DEPTH-BUFFER PRECISION problem, and Godot 4
# uses a reversed-Z float depth buffer, so at this world scale a couple of centimetres already
# separates the two surfaces by far more than the buffer can confuse. Lifting a road any further
# does not buy more robustness — it just builds a step into the world:
#
#   * the addon's curb is 0.15 m tall (`curb_height` default). A road lifted 0.5 m would sit
#     0.35 m ABOVE the top of its own curb — the cross-section inverts and the curb disappears
#     under the pavement it is supposed to contain. Same for the 0.15 m sidewalk.
#   * every road edge becomes a 0.5 m ledge that characters climb and vehicles drop off, at every
#     kerb line on the map.
#   * the embankment/pier rule (§6) reads `delta = surface_z - ground_z`, and 0.5 m of blanket
#     lift is already past `AT_GRADE_TOL` — every flat road would start generating embankment.
#
# The REAL fix for pavement fighting ground is not lift at all: cut the ground out from under the
# road (`rka.cut_ground_under_road`), so there are not two surfaces competing in the first place.
# The lift is only insurance for wherever that cut has not been run.
ROAD_LIFT = 0.03


def grade_profile(pts, z0, z1, kind="ramp"):
    """Distribute a z0->z1 change along `pts` at a constant grade, and report whether the
    run is long enough to do it legally. Returns (points_with_z, grade, ok)."""
    segs = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    total = sum(segs) or 1.0
    grade = abs(z1 - z0) / total
    out, run = [], 0.0
    for i, p in enumerate(pts):
        if i:
            run += segs[i - 1]
        out.append((p[0], p[1], z0 + (z1 - z0) * (run / total)))
    return out, grade, grade <= MAX_GRADE.get(kind, 0.06) + 1e-9


def run_needed(dz, kind="ramp"):
    """Minimum horizontal run to change height by `dz` — the number that decides whether a
    ramp fits before it is drawn. A +12 m deck at 6% needs 200 m, plus taper."""
    return abs(dz) / MAX_GRADE.get(kind, 0.06)


# =============================================================================== §0/§2
# The two density fields. Everything about block size, height and infill is a function of
# these, which is why the city reads as grown rather than placed.
# POSITIONS ARE LAYOUT, so they carry `G.SCALE` like everything in `island_v3_geom`; the WALK
# does not, because ten minutes of walking is ten minutes of walking whatever size the map is.
# That asymmetry is deliberate and is what compressing a world means — see the SCALE note in
# `island_v3_geom.py`.
CASTLE_C = G._p((-120.0, 78.0))                # moat centre, from geom.CASTLE/MOAT
STATIONS = [("central",  G._p((-60.0, -222.0))),   # Neon A — the main station
            ("electric", G._p((470.0, -140.0))),   # Neon B
            ("coastal",  G._p((800.0,  330.0)))]   # farmland/ocean arm
STATION_WALK = 500.0                           # density falls off over a 10-minute walk


def castle_dist(x, y):
    return math.dist((x, y), CASTLE_C)


def station_dist(x, y):
    return min(math.dist((x, y), p) for _n, p in STATIONS)


def station_falloff(x, y):
    """1.0 at a station, 0.0 beyond a 10-minute walk — the height/infill multiplier."""
    return max(0.0, 1.0 - station_dist(x, y) / STATION_WALK)


# --- §1/§2 block specs, keyed by QUARTER (not by theme) -----------------------------
# block  : target block depth, m
# jitter : +/- fraction applied per block, so no two measure the same
# dogleg : offset a cross-street sideways every N blocks (0 = never)
# offset : how far sideways, m
# front  : lot frontage range, m      depth : lot depth range, m
# floors : storey range
BLOCKS = {
    "neon_core": dict(alley=45, block= 84, jitter=0.12, dogleg=3, offset=14, front=(4, 7),
                      depth=(12, 15), floors=(3, 8), setback=0.0, retain=0.92, infill=0.34),
    "neon_edge": dict(alley=52, block=109, jitter=0.15, dogleg=2, offset=14, front=(5, 8),
                      depth=(12, 18), floors=(2, 5), setback=0.0, retain=0.86, infill=0.26),
    "samurai":   dict(alley=0, block=160, jitter=0.22, dogleg=2, offset=20, front=(18, 34),
                      depth=(24, 40), floors=(1, 2), setback=3.0, retain=0.55, infill=0.05),
    "teramachi": dict(alley=0, block=120, jitter=0.10, dogleg=0, offset= 0, front=(30, 60),
                      depth=(40, 70), floors=(1, 2), setback=6.0, retain=0.35, infill=0.00),
    "resid":     dict(alley=60, block=168, jitter=0.08, dogleg=4, offset=10, front=(8, 10),
                      depth=(14, 20), floors=(2, 2), setback=0.8, retain=0.80, infill=0.16),
    "port":      dict(alley=0, block=252, jitter=0.05, dogleg=0, offset= 0, front=(30, 60),
                      depth=(40, 70), floors=(1, 1), setback=8.0, retain=0.85, infill=0.10),
    "air":       dict(alley=0, block=252, jitter=0.00, dogleg=0, offset= 0, front=(26, 40),
                      depth=(34, 60), floors=(1, 2), setback=12.0, retain=0.45, infill=0.00),
    "farm":      dict(alley=0, block=336, jitter=0.00, dogleg=0, offset= 0, front=(14, 22),
                      depth=(11, 16), floors=(1, 2), setback=4.0, retain=0.30, infill=0.00),
}

# Concentric castle-town rings, measured from the moat centre. Outside the last ring the
# zone's own quarter applies — the gradient is a CENTRE, not a whole-map scheme.
CASTLE_RINGS = [(G._s(150.0), None),          # inside the moat — no blocks at all
                (G._s(300.0), "samurai"),     # 武家地
                (G._s(430.0), "neon_edge"),   # 町人地 outer
                (G._s(520.0), "teramachi")]   # 寺町 — the old town's defensive perimeter

# Which quarter each authored ZONE falls back to when it is outside the castle rings.
ZONE_QUARTER = {"neonA": "neon_core", "neonB": "neon_core", "neonC": "neon_edge",
                "resid": "resid", "farm": "farm", "port": "port", "air": "air"}


def quarter_at(x, y, zone=None):
    """Which planning quarter governs a point — castle rings win inside their radius."""
    d = castle_dist(x, y)
    for r, q in CASTLE_RINGS:
        if d <= r:
            return q
    return ZONE_QUARTER.get(zone or "", "resid")


def block_spec_at(x, y, zone=None):
    q = quarter_at(x, y, zone)
    return (q, BLOCKS[q]) if q else (None, None)


# =============================================================================== §1
def _axis_lines(lo, hi, spec, rng):
    """Street positions along one axis: first line half a block in, then stepped by the block
    depth with `jitter` applied per block, and every `dogleg`-th line flagged for a sideways
    offset. Shared by street_grid() and block_cells() — called in the SAME order with the SAME
    seeded rng — so streets and blocks are two views of one computation and can never disagree
    (the mistake being avoided here is generating blocks independently and having them drift a
    few metres off the streets that are supposed to bound them)."""
    blk, jit = spec["block"], spec["jitter"]
    dog, off = spec["dogleg"], spec["offset"]
    out, v, n = [], lo + blk * 0.5, 0
    while v < hi:
        out.append((v, off if (dog and n % dog == dog - 1) else 0.0))
        v += blk * (1.0 + rng.uniform(-jit, jit))
        n += 1
    return out


# Corridor half-width a building must stay clear of, per road tier: pavement + sidewalk.
CORRIDOR = {"T1": 11.0, "T2": 17.5, "T3": 10.5, "RAMP": 6.0, "TOUGE": 4.0}
_CORRIDORS = None


def road_corridors():
    """Every T1/T2/ramp centreline with its tier — the roads that BLOCKS MUST YIELD TO.

    v3 §7's build order is "roads before blocks; blocks are the leftover polygons". The block
    generator lays its own T3 grid inside a zone rect and knows nothing about the arterials or
    the expressway, so without this mask the two layers are generated independently and simply
    overlap — a fit check found 33% of all buildings standing in a road. This is the subtraction
    that makes the rule true."""
    global _CORRIDORS
    if _CORRIDORS is not None:
        return _CORRIDORS
    out = [("RING", "T2", [(x, y) for (x, y) in RING])]
    for nm, pts in G.ARTERIALS:
        out.append((nm, "T2", [(p[0], p[1]) for p in pts]))
    out.append(("LOOP", "T1", [(p[0], p[1]) for p in loop_deck()]))
    for rid, p3, _par, _grade, _ok, _kind in ramps():
        out.append((rid, "RAMP", [(p[0], p[1]) for p in p3]))
    out.append(("TOUGE", "TOUGE", [(p[0], p[1]) for p in G.TOUGE]))
    for nm, pts in (("WESTRAD", G.WESTRAD), ("PORTSPUR", G.PORTSPUR),
                    ("AIRPORT_ROAD", G.AIRPORT_ROAD)):
        out.append((nm, "RAMP", [(p[0], p[1]) for p in pts]))
    _CORRIDORS = out
    return out


def _seg_dist(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.hypot(px - a[0], py - a[1])
    t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
    return math.hypot(px - (a[0] + dx * t), py - (a[1] + dy * t))


def nearest_road(px, py):
    """(distance, road name, tier) to the closest road centreline."""
    best, who, tier = float("inf"), None, None
    for nm, tr, pts in road_corridors():
        for a, b in zip(pts, pts[1:]):
            d = _seg_dist(px, py, a, b)
            if d < best:
                best, who, tier = d, nm, tr
    return best, who, tier


def in_road_corridor(px, py, footprint=0.0):
    """True if a building of this footprint at (px,py) would stand in a road."""
    dist, _who, tier = nearest_road(px, py)
    return dist < CORRIDOR.get(tier, 12.0) + footprint / 2.0


def block_cells(rect, spec, seed=0, street_w=14.0):
    """The BLOCKS — the leftover polygons between the T3 streets, inset by half a street width.

    v3 §7's build order is "roads before blocks; blocks are the leftover polygons". This is that
    step. Returns [(x0, y0, x1, y1), ...], only for cells that are actually on land."""
    x0, y0, x1, y1 = rect
    rng = random.Random(seed ^ int(x0) ^ (int(y0) << 8))
    xs = [p[0] for p in _axis_lines(x0, x1, spec, rng)]
    ys = [p[0] for p in _axis_lines(y0, y1, spec, rng)]
    h = street_w / 2.0
    bx = [x0] + xs + [x1]
    by = [y0] + ys + [y1]
    out = []
    for a, b in zip(bx, bx[1:]):
        for c, d in zip(by, by[1:]):
            cx0, cy0, cx1, cy1 = a + h, c + h, b - h, d - h
            if cx1 - cx0 < 12.0 or cy1 - cy0 < 12.0:
                continue
            mx, my = (cx0 + cx1) / 2, (cy0 + cy1) / 2
            if G.on_land(mx, my):
                out.append((cx0, cy0, cx1, cy1))
    return out


def subdivide(cell, alley, alley_w=4.5):
    """Split a block into SUB-BLOCKS along T4 *roji*.

    A T3 block at 168 m is a street spacing, not a building unit — lotting only its perimeter
    leaves a 154 m dead middle, which is what a first pass produced and it read as empty. Real
    Japanese blocks are cut further by 4-4.5 m alleys into 45-60 m sub-blocks, and THAT is the
    unit buildings front onto. 4 m is also the legal minimum road frontage under the Building
    Standards Act, which is precisely why *roji* are the width they are.

    `alley = 0` means the quarter genuinely has no alleys (port, airport, temple belt) and the
    block is returned unchanged."""
    x0, y0, x1, y1 = cell
    if alley <= 0:
        return [cell]
    h = alley_w / 2.0
    def cuts(lo, hi):
        n = max(1, int(round((hi - lo) / alley)))
        step = (hi - lo) / n
        return [lo + step * i for i in range(n + 1)]
    xs, ys = cuts(x0, x1), cuts(y0, y1)
    out = []
    for a, b in zip(xs, xs[1:]):
        for c, d in zip(ys, ys[1:]):
            sx0, sy0 = a + (h if a > x0 else 0.0), c + (h if c > y0 else 0.0)
            sx1, sy1 = b - (h if b < x1 else 0.0), d - (h if d < y1 else 0.0)
            if sx1 - sx0 >= 10.0 and sy1 - sy0 >= 10.0:
                out.append((sx0, sy0, sx1, sy1))
    return out


def lots(cell, spec, seed=0):
    """Perimeter lots for one block, as (x, y, bearing_deg, frontage, depth).

    Walk the block's frontage and split it into lots at the quarter's frontage width. Density in
    a Japanese city comes from NARROW FRONTAGE AND NO GAPS, never from big buildings — a zakkyo
    at 6 x 12 m and a suburban house at 7 x 9 m have nearly the same footprint, so this one
    function serves both and only the numbers differ."""
    x0, y0, x1, y1 = cell
    rng = random.Random(seed)
    fmin, fmax = spec["front"]
    dmin, dmax = spec["depth"]
    set_b = spec["setback"]
    out = []
    sides = [((x0, y0), (x1, y0), 90.0), ((x1, y0), (x1, y1), 180.0),
             ((x1, y1), (x0, y1), 270.0), ((x0, y1), (x0, y0), 0.0)]
    for (ax, ay), (bx, by), inward in sides:
        L = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / (L or 1.0), (by - ay) / (L or 1.0)
        s = 0.0
        while s < L - fmin:
            f = min(rng.uniform(fmin, fmax), L - s)
            if f < fmin * 0.8:
                break
            d = rng.uniform(dmin, dmax)
            cx = ax + ux * (s + f / 2.0)
            cy = ay + uy * (s + f / 2.0)
            ir = math.radians(inward)
            px = cx + math.cos(ir) * (d / 2.0 + set_b)
            py = cy + math.sin(ir) * (d / 2.0 + set_b)
            if G.on_land(px, py):
                out.append((px, py, inward, f, d))
            s += f
    return out


def street_grid(rect, spec, seed=0, clip=True):
    """T3 local streets for one zone rect, WITH jitter and doglegs — this is what replaces
    the uniform lattice. Returns [polyline, ...] in metres.

    A dogleg is the whole anti-lattice device: every `dogleg` blocks, the cross-street steps
    sideways by `offset` instead of running through, so nothing sightlines more than three
    blocks. Set `dogleg=0` for the industrial/airport quarters, which really are lattices.
    """
    x0, y0, x1, y1 = rect
    rng = random.Random(seed ^ int(x0) ^ (int(y0) << 8))
    out = []
    for axis in (0, 1):
        lo, hi = (x0, x1) if axis == 0 else (y0, y1)
        alo, ahi = (y0, y1) if axis == 0 else (x0, x1)
        mid = (alo + ahi) * 0.5
        for (v, shift) in _axis_lines(lo, hi, spec, rng):
            if shift:
                line = [(v, alo), (v, mid), (v + shift, mid), (v + shift, ahi)]
            else:
                line = [(v, alo), (v, ahi)]
            out.append([(p[0], p[1]) if axis == 0 else (p[1], p[0]) for p in line])
    return [r for line in out for r in (clipped(line) if clip else [line])]


def clipped(pts, step=9.0):
    """Split a polyline into the runs that are actually on land — the same helper the SVG
    plates use, lifted here so the blend and the plate clip identically."""
    runs, cur = [], []
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.dist(a, b) / step))
        for i in range(n + 1):
            t = i / n
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if G.on_land(*p):
                cur.append(p)
            elif cur:
                runs.append(cur); cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) > 1]


def offset_inward(poly, d):
    """Angle-bisector inward offset — how RING is derived from the coast skeleton."""
    n = len(poly)
    cx = sum(p[0] for p in poly) / n
    cy = sum(p[1] for p in poly) / n
    out = []
    for i in range(n):
        px, py = poly[i]
        ax, ay = poly[i - 1]
        bx, by = poly[(i + 1) % n]
        vx = (py - ay) + (by - py)
        vy = (ax - px) + (px - bx)
        L = math.hypot(vx, vy) or 1.0
        vx, vy = vx / L, vy / L
        if (cx - px) * vx + (cy - py) * vy < 0:
            vx, vy = -vx, -vy
        out.append((px + vx * d, py + vy * d))
    return out


RING = offset_inward(G.MAIN_BASE, G.RING_INSET)


# =============================================================================== §3
# THE LAYOUT SCALE IS 3D, NOT 2D — and this is the line where that was learned.
#
# These carry `G.SCALE` like every XY coordinate, because a ramp's LENGTH is derived from them:
# `run_needed(12 m, 6%)` is 200 m of horizontal run whatever size the island is. Compress the
# layout to 0.75 and leave the deck at 12 m and the ramps have 25% less room for a run that did
# not shrink — measured, that took `IC_CHUO_EN` from a 219 m radius to 19 m. Scaling the deck with
# the plan makes the whole elevated network scale-invariant: every ramp comes out exactly as it did.
#
# What stays 1:1 is the ENGINEERING: `MAX_GRADE`, `RAMP_MIN_RADIUS`, lane widths, pier sections,
# `road_support`'s fill/cut thresholds. A car's cornering does not care how big the map is.
DECK_Z    = G._s(12.0)   # T1 expressway deck — v3 §5
RAIL_Z    = G._s(8.0)    # rail viaduct deck, deliberately BELOW the road deck so they cross
ISLAND_Z  = G._s(4.0)    # airport island grade

#: Half-width of each road tier's drawn surface. A PLANNING fact (it is what decides whether two
#: alignments overlap, and how wide a bridge deck has to be to carry the road on it), so it lives
#: here rather than in the builder that draws it. `CORRIDOR` above is the different, larger
#: number: the clearance a road claims from BUILDINGS.
#: Half the total paved width of each tier, metres. T2/T3 follow `seed_district_roads.LANE_WIDTH`
#: (4.5 m since 2026-08-31 -- an arcade lane, see the note there): T2 is 4 lanes + a 3 m median +
#: two 4 m footways = 29.0 m, T3 is 2 lanes + two 3.5 m footways = 16.0 m. The others are the plan
#: diagram's own ribbons and are not seeded through the road kit, so they are unchanged.
ROAD_HALF = {"T1": 11.0, "T2": 14.5, "T3": 8.0, "T4": 2.25, "RAIL": 5.0, "RAMP": 4.5}

#: THE BRIDGES, as one list: name, span, deck height, half-width.
#:
#: One owner, because three things now ask the same question. `build_island_v3.build_bridges`
#: draws them; `island_v3_terrain.report` asks whether an arterial's off-land stations are carried;
#: and `check_island_ground.py` asks the same of the BUILT deck. A span authored in `island_v3_geom`
#: with a width invented separately in the builder could not be checked by either.
BRIDGES = [
    ("Arch_bridge",    G.ARCH_BRIDGE,    G._s(6.0),  ROAD_HALF["T2"]),
    ("Bay_bridge",     G.BAY_BRIDGE,     G._s(9.0),  ROAD_HALF["T2"]),
    ("Airport_bridge", G.AIRPORT_BRIDGE, DECK_Z,     ROAD_HALF["T1"]),
]


def bridge_at(x, y):
    """The bridge whose deck covers this point, or None. A deck is a straight ribbon, so this is
    a point-to-segment distance against its half-width — the same shape `kc.flat_ribbon` draws."""
    for (name, (a, b), z, half) in BRIDGES:
        if _seg_dist(x, y, a, b) <= half:
            return (name, z)
    return None

# Interchanges. Each is (id, gore point on the LOOP, touchdown on an arterial, kind).
# kind: "pair" = on+off ramp both directions, "half" = one direction only (Shuto's usual
# answer where there is no room), "jct" = expressway-to-expressway.
# Shuto C1 runs one ramp pair per ~740 m; five on a 3,331 m loop is that density.
INTERCHANGES = [
    # Touchdowns sit FAR along their arterial, not at the nearest point on it. A ramp descending
    # 12 m needs 200 m of run at 6%, and it also has to turn off the deck's tangent — squeezing
    # both into the ~130 m to the closest arterial point forced 17-25 m radii and, for
    # IC_RINKAI_W, threw 9 of its 20 points out over open water. Moving each touchdown one or two
    # nodes further along the SAME arterial fixes radius, grade and the coastline together:
    # IC_CHUO 18.7 -> 50.3 m, IC_PORT 17.0 -> 47.9 m, IC_RINKAI_W 20.2 -> 38.1 m and back on land.
    # IC_CHUO's touchdown MOVED when the island got a real ground surface (WORLD_REBUILD_PLAN.md
    # step 1). (74, 600) was a Chuo-dori vertex; Chuo-dori now ends at the massif's foot at
    # (52, 430), so that point is 100 m up a 92% flank with no road on it. It moves to the nearest
    # arterial that still runs there — Nogyo-michi, the farm cross-street Chuo-dori now terminates
    # ON — and the position along it is where the pair's WORSE radius peaks: exit 69.8 m / entry
    # 226.6 m, against 33.9 / 92.1 before. Both ramps clear the 59.1 m tier minimum for the first
    # time (the exit is still 0.2 m under the 70 m fit TARGET, hence the surviving warning).
    # (205, 445.4) was the optimum at the uncompressed 2016 m island. `G.SCALE` shrank every
    # radius by 0.75 while `RAMP_MIN_RADIUS` stayed 59.1 m — a car's cornering does not compress —
    # so the pair was re-swept at the real scale and (170, 441.9) is where its WORSE radius peaks.
    ("IC_CHUO",     G._p(( 30.0,  290.0)), G._p(( 170.0,  441.9)), "pair",
     "Chuo-dori / Nogyo-michi"),
    ("IC_RINKAI_E", G._p((500.0,  -96.0)), G._p(( 680.0, -104.0)), "pair", "Rinkai-dori"),
    ("IC_YAMATE",   G._p((500.0,  218.0)), G._p(( 700.0,  208.0)), "half", "Yamate-dori"),
    ("IC_RINKAI_W", G._p((-560.0, -155.0)), G._p((-806.0, -128.0)), "half", "Rinkai-dori west"),
    ("IC_PORT",     G._p((-150.0, -450.0)), G._p((-215.0, -700.0)), "half",
     "PORTSPUR / container port"),
    ("JCT_AIRPORT", G._p((500.0, -335.0)), G._p(( 800.0, -180.0)), "jct",  "airport bridge"),
]


# A "pair" interchange's ENTRY is a separate ramp at a separate place, not the exit ramp driven
# backwards. Both OSM and Shuto practice are unambiguous about this: an entrance and an exit are
# two one-way `motorway_link` ways ("create 2 different ways and tag them separately ...
# oneway=yes"), and on the Shuto they are physically distinct alignments -- Harumi and Toyosu are
# northbound-exit / southbound-entrance only, and where an interchange does serve both directions
# the two ramps meet the surface street at their own junctions.
#
# Generating both from ONE authored corridor produced two one-way roads on identical ground,
# differing only in their last segment as each reached its own carriageway's gore 21 m apart --
# which is exactly the kink at the deck end. The entry therefore gets its own gore, advanced along
# the loop, and its own touchdown offset beside the exit's.
ENTRY_GORE_ADVANCE  = 320.0   # PREFERRED separation; the real one is searched, see below
ENTRY_TOUCHDOWN_SEP = 22.0    # lateral gap where the two ramps meet the arterial
#: The entry gore is SEARCHED along the ring, not stepped a fixed distance. A fixed advance asks
#: the ring to be the right shape at exactly one distance in exactly one direction, and where it
#: is not, the fitter still returns something: at +320 m `IC_CHUO_EN` came out at a 29 m radius
#: (the tier minimum is 59.1 m) and `IC_RINKAI_E_EN` came out running down the middle of the
#: expressway, never leaving it, and crossing to the far side 4.5 m past its own gore. Neither is
#: an authoring mistake anyone made -- the position was simply never a choice. Sweeping it makes
#: "where can this entry actually leave the deck?" a question with an answer.
ENTRY_GORE_ADVANCE_RANGE = (200.0, 720.0)
ENTRY_GORE_ADVANCE_STEP  = 40.0
#: How close an entry gore may come to another interchange's gore. Measured, a fixed +320 m put
#: `IC_RINKAI_E`'s entry 36 m from `IC_YAMATE`'s gore -- two interchanges on one stretch of deck.
ENTRY_GORE_MIN_CLEAR = 120.0
#: Suffix marking the ENTRY half of a pair interchange, e.g. `IC_CHUO_EN`. One string, so a
#: consumer can pair an entry back to its interchange without a second table.
ENTRY_SUFFIX = "_EN"


def loop_point_at(pt, advance):
    """The point `advance` metres along `G.LOOP` from the loop position nearest `pt`.

    Walks the real ring rather than offsetting along the local tangent, so an entry gore placed a
    few hundred metres away still lands ON the carriageway even when the ring turns in between --
    which it does at every one of these interchanges."""
    poly = loop_poly()
    edges = list(zip(poly, poly[1:] + poly[:1]))
    # locate the nearest edge and the arc-length to it
    acc, best = 0.0, None
    for a, b in edges:
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L2))
        d = math.dist(pt, (a[0] + dx * t, a[1] + dy * t))
        seg = math.sqrt(L2)
        if best is None or d < best[0]:
            best = (d, acc + seg * t)
        acc += seg
    total = acc
    s = (best[1] + advance) % total
    acc = 0.0
    for a, b in edges:
        seg = math.dist(a, b)
        if acc + seg >= s:
            t = (s - acc) / (seg or 1.0)
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        acc += seg
    return tuple(poly[0])


def entry_endpoints(gore, touchdown, avoid=(), side="FWD", serves=None):
    """`(entry_gore, entry_touchdown)` for a pair interchange's ENTRY ramp.

    The gore moves along the loop so the two ramps genuinely leave the expressway at different
    places; the touchdown steps `ENTRY_TOUCHDOWN_SEP` sideways (square to the exit ramp's own run)
    so they arrive at the arterial as two adjacent connections rather than one. Together those
    give the diamond an entrance and an exit that never share ground.

    THE POSITION IS SEARCHED, NOT STEPPED. It began as a fixed +320 m, then as a choice of
    direction at 320 m -- because a fixed direction dropped `IC_RINKAI_E`'s entry 36 m from
    `IC_YAMATE`'s gore. Choosing only the direction has the same flaw one level down: it asks the
    ring to be the right shape at exactly one distance, and where it is not, `fit_ramp` still
    returns its least-bad candidate rather than nothing. Both of the island's pair interchanges
    were living on that: `IC_CHUO_EN` at a 29 m radius against a 59.1 m tier minimum, and
    `IC_RINKAI_E_EN` fitted straight down the middle of the expressway -- never leaving the
    carriageway, then crossing to its far side 4.5 m past its own gore, which is what welded a
    junction a car-length from the gore and pinched that interchange into a bow-tie.

    So the whole placement is swept, and the candidates are judged by what actually matters about
    a ramp, in order: it must LEAVE the expressway (`leaves_mainline` -- a hard requirement, not a
    preference, because a ramp that overlaps its own mainline is a same-grade crossing), it must
    stand clear of the neighbouring interchanges, it should satisfy radius and grade, and only
    then should it sit near the authored `ENTRY_GORE_ADVANCE`. Preference for the authored
    distance comes LAST on purpose: it is the one term that is a wish rather than a constraint."""
    # SEPARATE THE TWO TOUCHDOWNS ALONG THE ARTERIAL, not square to the exit ramp. Square to the
    # ramp moves the entry OFF the street it is supposed to meet, so the endpoint snap pulls it
    # straight back onto the same junction as the exit -- measured, IC_RINKAI_E's two ramps landed
    # in one 9-arm node at (680.6, -103.1), with 13 m scraps of arterial between the merged
    # junctions and lane tangents kinking ~105 deg through them. A diamond's entrance and exit
    # meet the surface street at two points ALONG it, which is what this offset should have meant
    # all along.
    ax, ay = arterial_tangent(touchdown)
    et = (touchdown[0] + ax * ENTRY_TOUCHDOWN_SEP, touchdown[1] + ay * ENTRY_TOUCHDOWN_SEP)

    lo, hi = ENTRY_GORE_ADVANCE_RANGE
    keep_clear = list(avoid) + [tuple(gore)]          # never land back on its own exit gore
    best, fallback = None, None
    for sign in (1.0, -1.0):
        adv = lo
        while adv <= hi:
            eg = loop_point_at(gore, adv * sign)
            clear = min([math.dist(eg, o) for o in keep_clear], default=float("inf"))
            adv += ENTRY_GORE_ADVANCE_STEP
            # Kept regardless, so a layout where NOTHING satisfies the gates still produces the
            # same answer this used to give rather than no interchange at all -- the report then
            # says the ramp does not fit, which is the honest outcome.
            if fallback is None or clear > fallback[0]:
                fallback = (clear, eg)
            if clear < ENTRY_GORE_MIN_CLEAR:
                continue
            pts, _par, _grade, ok = fit_ramp(eg, et, DECK_Z, "ramp", side=side)
            # TWO DIFFERENT DIRECTIONS, and they are genuinely different for an entry. `side` is
            # the direction the polyline is GENERATED in (deck-end-first, so the opposite of the
            # traffic on it); `serves` is the carriageway the merge feeds. The nearside test is
            # about the traffic, so it must use `serves` -- measured against the generation
            # direction it accepted gores whose ramp merged from the offside of the very stream
            # it joins, which is the one thing no auxiliary lane can serve.
            if not leaves_mainline(pts) or not leaves_on_the_nearside(pts, serves or side):
                continue
            score = (1 if ok else 0,
                     min(ramp_radius(pts), RAMP_FIT_TARGET),
                     -abs(adv - ENTRY_GORE_ADVANCE_STEP - ENTRY_GORE_ADVANCE))
            if best is None or score > best[0]:
                best = (score, eg)
    return (best[1] if best is not None else fallback[1]), et


# WHICH DIRECTION each interchange serves. Kept beside INTERCHANGES rather than as a sixth tuple
# field so the existing 5-way unpackings stay valid.
#
# A direction with entries and no exits is a dead end -- traffic drives onto it and can never
# leave. That is precisely what the connectivity gate caught when every exit was put on one
# carriageway and every entry on the other (ROAD_KIT_REDESIGN.md defect 13), so the assignment
# here deliberately gives BOTH directions exits: the two "pair" interchanges each serve one
# direction with an exit AND an entry, and the "half" ones alternate.
INTERCHANGE_SIDE = {
    "IC_CHUO":     "REV",
    "IC_RINKAI_E": "REV",
    "IC_YAMATE":   "REV",
    "IC_RINKAI_W": "REV",
    "IC_PORT":     "REV",
    "JCT_AIRPORT": "REV",
}


def interchange_side(rid):
    """`'FWD'`/`'REV'` for an interchange id, accepting the `_EN` entry suffix."""
    base = rid[:-len(ENTRY_SUFFIX)] if rid.endswith(ENTRY_SUFFIX) else rid
    return INTERCHANGE_SIDE.get(base, "FWD")


#: How far an authored gore may sit off the LOOP before `gore_on_loop` says so out loud. A gore is
#: where a ramp LEAVES the mainline, so it is on the mainline by definition; a few metres is
#: authoring slack, tens of metres is a typo.
GORE_SNAP_NOTE_M = 5.0


def gore_on_loop(gore):
    """An authored gore projected onto the LOOP polyline — the point the ramp actually departs
    from.

    A gore that is not on the expressway is not a gore. `IC_YAMATE`'s authored coordinate sat
    30.4 m off the ring (every other one is exactly on it), and everything downstream inherited
    that: the ramp was authored departing from open air, so `ops_split.seed_ramp` then had to
    slide it 46.8 m onto the auxiliary-lane slot — against 10.6 m for every well-placed
    interchange — and absorbing that on a 207 m ramp cost it a third of its radius (66.6 m
    planned, 47.4 m built, against a 59.1 m minimum).

    Projecting is the minimal correction: it does not move the interchange along the road, it puts
    it ON the road. The distance is printed when it is more than authoring slack, so a typo is
    reported rather than quietly absorbed."""
    poly = loop_poly()
    best, bd = tuple(gore), float("inf")
    for a, b in zip(poly, poly[1:] + poly[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 <= 0.0:
            continue
        t = max(0.0, min(1.0, ((gore[0] - a[0]) * dx + (gore[1] - a[1]) * dy) / L2))
        p = (a[0] + dx * t, a[1] + dy * t)
        d = math.dist(gore, p)
        if d < bd:
            bd, best = d, p
    if bd > GORE_SNAP_NOTE_M:
        print("  NOTE: authored gore %s is %.1f m off the LOOP — snapped onto the mainline at "
              "(%.1f, %.1f)" % (tuple(round(c, 1) for c in gore), bd, best[0], best[1]))
    return best


def loop_tangent(pt):
    """Unit direction of the LOOP edge nearest `pt` — the mainline heading at a gore."""
    poly = loop_poly()
    best, bd = (1.0, 0.0), float("inf")
    for a, b in zip(poly, poly[1:] + poly[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((pt[0]-a[0])*dx + (pt[1]-a[1])*dy) / L2))
        d = math.dist(pt, (a[0] + dx*t, a[1] + dy*t))
        if d < bd:
            bd, L = d, math.sqrt(L2)
            best = (dx / L, dy / L)
    return best


#: A 45 km/h ramp needs a 59.1 m radius at 6% superelevation -- `blender/lib/road_geometry.py`
#: `min_radius(45, 0.06)`, and `island_v3_to_roadkit.TIERS["RAMP"]` derives the same number from the
#: same function. This used to default to 30 m, which is not a design speed at all: it passed ramps
#: no car can hold (`IC_RINKAI_E` was built at R=20.6 m and needs 42% bank at 45 km/h).
RAMP_MIN_RADIUS = 59.1

#: What `fit_ramp` actually SEARCHES for, as opposed to what the standard requires.
#:
#: A planned ramp is not the ramp that gets built. Downstream, `island_v3_to_roadkit
#: .land_ramp_on_kerb` slides the touchdown ~6.3 m sideways (centreline -> kerbside lane) over a
#: 120 m smoothstep, and a smoothstep of amplitude A over length L adds curvature of roughly
#: `6A/L^2` -- here a 381 m radius, which composes with the planned one:
#:
#:      1/R_built  =  1/R_plan + 1/381        ->  R_plan = 70 m lands R_built = 59.1 m
#:
#: Planning to exactly the 59.1 m standard therefore guarantees a ramp that FAILS it once built,
#: by about 6 m -- measured, IC_RINKAI_E planned 61.7 m and built 55.7 m. `fit_ramp` stops at the
#: first parallel run that satisfies its target, so raising the target genuinely makes it search
#: harder rather than merely re-labelling the result. The standard itself is unchanged: TIGHT is
#: still reported against `RAMP_MIN_RADIUS`, because that is what a driver experiences.
#:
#: (The better fix is to remove the correction rather than budget for it -- author the touchdown
#: on the kerbside lane so there is nothing to land. That needs the plan to know the target
#: arterial's cross-section, which today lives in the builder's `TIERS`.)
RAMP_FIT_TARGET = 70.0

#: Bezier handle scales swept by `fit_ramp`, as a fraction of the start-to-touchdown distance.
#: 0.42 was the only value before both ends had a real tangent.
K_SCALES = (0.30, 0.36, 0.42, 0.50, 0.58, 0.66, 0.75, 0.85, 0.95)


def arterial_tangent(pt):
    """Unit heading of the ARTERIAL nearest `pt` — the road a ramp actually touches down on.

    Deliberately scans only the fixed road geometry (`RING`, `G.ARTERIALS`, the named spurs) and
    NOT `road_corridors()`, which calls `ramps()`, which calls `fit_ramp` — the caller of this.
    Going through the shared helper would recurse."""
    best, bd = (1.0, 0.0), float("inf")
    lines = [[(x, y) for (x, y) in RING]]
    lines += [[(p[0], p[1]) for p in pts] for _nm, pts in G.ARTERIALS]
    lines += [[(p[0], p[1]) for p in pts]
              for pts in (G.WESTRAD, G.PORTSPUR, G.AIRPORT_ROAD)]
    for pts in lines:
        for a, b in zip(pts, pts[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            if L2 < 1e-12:
                continue
            t = max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L2))
            d = math.dist(pt[:2], (a[0] + dx * t, a[1] + dy * t))
            if d < bd:
                L = math.sqrt(L2)
                bd, best = d, (dx / L, dy / L)
    return best


def ramp_polyline(gore, touchdown, tangent, parallel=0.0, samples=18, arrive=None,
                  k_scale=0.42):
    """Ramp centreline. A ramp does NOT leave the mainline at a corner — it diverges, so it
    first runs `parallel` metres alongside the deck on the departure tangent, then curves
    away to the touchdown. That parallel run is also how a ramp BUYS LENGTH: the direct
    distance to an arterial is usually well under the 200 m a +12 m deck needs at 6 %, and
    running alongside is exactly how a real Shuto half-interchange solves it."""
    gx, gy = gore
    ux, uy = tangent
    start = (gx + ux * parallel, gy + uy * parallel)
    tx, ty = touchdown
    D = math.dist(start, touchdown) or 1.0
    # CUBIC, with a tangent handle at BOTH ends. A quadratic has one control point, so the only
    # direction it can honour is the departure tangent — and it then has to hook back onto the
    # touchdown, which gets TIGHTER as the parallel run grows. That made lengthening the run
    # (the fix for grade) actively break the radius. A cubic leaves along the mainline and
    # ARRIVES along the approach direction, so growing the run now improves both together.
    ax, ay = (tx - start[0]) / D, (ty - start[1]) / D
    if arrive is not None:
        # ARRIVE ALONG THE ARTERIAL, not along the chord. The chord is the straight line from the
        # end of the parallel run to the touchdown, which is a direction no road actually runs in;
        # using it as the arrival handle is why ramps met the mainline at a poor angle and why
        # IC_CHUO hooked into a teardrop back under the deck (§5). Take the heading of the road
        # being joined instead, so the ramp's last metres are collinear with it -- which is also
        # what makes the joint edge-alignable at all.
        #
        # ORIENTED to the approach, never reversed onto it. An arterial's stored direction is
        # arbitrary; if it points back the way the ramp came, honouring it forces the curve into a
        # U-turn to arrive -- the exact "direction reversal" half of the defect. Flipping it when
        # it disagrees with the chord costs nothing and makes that unrepresentable.
        if arrive[0] * ax + arrive[1] * ay < 0.0:
            arrive = (-arrive[0], -arrive[1])
        ax, ay = arrive
    # HANDLE LENGTH, searched rather than fixed (`fit_ramp`). With only the departure tangent
    # meaningful there was nothing to trade off and 0.42 was as good as anything. Now that BOTH
    # ends have a real direction, how far the handles reach decides where the curvature piles up:
    # short handles turn hard at the ends, long ones bulge in the middle. The best value is a
    # property of each gore/touchdown pair, so it is a parameter and the caller sweeps it.
    k = k_scale * D
    p1 = (start[0] + ux * k, start[1] + uy * k)
    p2 = (tx - ax * k, ty - ay * k)
    pts = [gore]
    for i in range(samples + 1):
        t = i / samples
        m = 1 - t
        a = m ** 3, 3 * m * m * t, 3 * m * t * t, t ** 3
        pts.append((a[0]*start[0] + a[1]*p1[0] + a[2]*p2[0] + a[3]*tx,
                    a[0]*start[1] + a[1]*p1[1] + a[2]*p2[1] + a[3]*ty))
    # UNIFORM SPACING, or nothing downstream can measure this curve.
    #
    # As built above the polyline is `[gore] + samples+1 bezier points`: the FIRST span is the
    # whole parallel run -- up to 260 m -- and the rest are ~20 m apart. Every arc-length-windowed
    # measurement then depends on which side of that 260 m span its window lands, and the answer
    # stops being a property of the road: `IC_RINKAI_E` measured **159.1 m at an 18 m window and
    # 19.6 m at 25 m**, from the same points. A number that swings 8x on the measuring window is
    # not a radius, and it is what made this module's self-test report ramps as OK that the export
    # gate then failed. It also handed the builder a spine with one enormous span in it.
    return _resample_uniform(dedupe(pts), RAMP_SAMPLE_STEP)


#: Window the ramp radius is measured over. MUST match `blender/lib/road_geometry.py`'s
#: `CURVATURE_WINDOW_M`, because that is what the export gate judges the built lanes with -- when
#: the two differed (18 here, 25 there) this module happily reported ramps as OK that the gate then
#: failed, which is the worst possible split: a check that disagrees with the check downstream.
RAMP_RADIUS_WINDOW = 25.0

#: A SECOND, short window, checked alongside the 25 m one. A long-handled cubic can fold into a
#: local cusp mid-curve that a 25 m window averages straight over: `IC_CHUO` measured 76.4 m at
#: 25 m while carrying an adjacent-triple radius of 7.5 m at one point -- invisible until the lane
#: offset amplified it to 4.1 m and the export gate failed the built lane at R=25 m. Since
#: `RAMP_SAMPLE_STEP` makes the spacing uniform, a window near that step measures real local
#: curvature rather than sampling noise, so the two together catch both failure shapes.
RAMP_KINK_WINDOW = 12.0

#: Arc length between ramp centreline points. 8 m is well under the tightest radius any ramp is
#: allowed (59 m), so the polyline represents the curve rather than chording across it.
RAMP_SAMPLE_STEP = 8.0


def _resample_uniform(pts, step):
    """Re-space a polyline at `step` metres along its own arc length, keeping both ends."""
    if len(pts) < 2:
        return list(pts)
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.dist(a[:2], b[:2]))
    total = cum[-1]
    if total <= step:
        return list(pts)
    n = max(2, int(round(total / step)) + 1)
    out, j = [], 0
    for i in range(n):
        s = total * i / (n - 1)
        while j < len(cum) - 2 and cum[j + 1] < s:
            j += 1
        span = cum[j + 1] - cum[j]
        t = 0.0 if span <= 1e-12 else (s - cum[j]) / span
        a, b = pts[j], pts[j + 1]
        out.append(tuple(a[k] + (b[k] - a[k]) * t for k in range(len(a))))
    return out


def dedupe(pts, tol=1e-6):
    """Drop consecutive duplicate points.

    A zero-length segment has NO DEFINED TANGENT, and everything downstream derives a frame from
    the tangent — the curve's normal, the swept cross-section, the lane offsets. Feed one in and
    the road twists where the frame flips, usually at an end where it is most visible. With
    `parallel == 0` this function's caller emitted the gore twice (once as the lead-in point,
    once as the bezier's own start), which is exactly the twist seen on IC_PORT's ramp. Same
    failure the expressway LOOP hit with its repeated closing point — worth de-duplicating at
    every producer rather than remembering the rule at every consumer."""
    out = [tuple(pts[0])]
    for p in pts[1:]:
        if math.dist(p[:2], out[-1][:2]) > tol:
            out.append(tuple(p))
    return out


#: Degrees of NET turn past which a connector has stopped connecting and started coming back.
#: An S-curve swings one way and then the other, so its cumulative turn returns toward zero and it
#: passes; a U-turn accumulates in one direction and does not.
MAX_RAMP_TURN_DEG = 135.0


def turns_back(pts):
    """True when a polyline reverses on itself — a hairpin, not a ramp.

    THIS IS NOT A RADIUS TEST, AND IT CANNOT BE. Every windowed radius measure here and in
    `blender/lib/road_geometry.py` samples two points a fixed ARC-LENGTH either side of each
    point, which is exactly what makes it immune to sampling density (see `min_radius_windowed`).
    At a hairpin that same property turns into a blind spot: 25 m of arc back and 25 m of arc
    forward land on the two LEGS of the U, a few metres apart in space, and the circle through
    three nearly-collinear points is enormous. Measured on the built `IC_YAMATE` ramp: a spine
    that visibly doubles back — (151.2, 499.7) -> (145.3, 505.2) -> (148.5, 502.2) — scored
    70.2 m, comfortably inside a 45 km/h ramp's 59.1 m minimum. The lane offset off that spine
    scored 19 m, purely because the offset made the two legs' arc lengths differ enough for the
    window to land inside the fold.

    So `fit_ramp` was not choosing a bad radius; it was choosing a shape no radius test it had
    could describe. A ramp doubling back is a topology error, and the honest test is on TURNING:
    accumulate the signed heading change and take the largest excursion from zero."""
    head, cum, worst = None, 0.0, 0.0
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if math.hypot(dx, dy) < 1e-9:
            continue
        h = math.degrees(math.atan2(dy, dx))
        if head is not None:
            cum += (h - head + 180.0) % 360.0 - 180.0
            worst = max(worst, abs(cum))
        head = h
    return worst > MAX_RAMP_TURN_DEG


def _joint_heading_tol():
    """The joint checker's own tolerance, read from the TOOL rather than restated here -- one
    number, so the planner cannot approve a departure the checker will then refuse. Falls back to
    the same value if `blender/lib` is not importable (this module is pure-Python and stays so)."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(_here), "blender", "lib"))
        import lane_joints as _lj
        return _lj.HEADING_TOL_DEG
    except Exception:                                  # noqa: BLE001
        return 8.0


def _opposite(side):
    """The other carriageway direction -- see the ENTRY case in `ramps()`."""
    return "REV" if side == "FWD" else "FWD"


#: How far along a ramp, from its gore, the departure is judged. Measured on the island every
#: healthy ramp is 45-64 m clear of the LOOP centreline by here, so this is generous rather than
#: tight -- it exists to catch a ramp that never leaves at all, not to police the peel rate.
GORE_CLEAR_RUN = 90.0

#: How far off the LOOP centreline counts as "off the mainline". This is the LOOP's own corridor
#: half-width, so it means exactly "clear of the expressway's pavement" rather than an invented
#: distance.
GORE_CLEAR_OFFSET = CORRIDOR["T1"]


def _loop_offset(pt):
    """Signed lateral offset of `pt` from the LOOP, positive to the LEFT of its stored direction.

    Signed, not absolute, because the whole question is WHICH SIDE the ramp is on -- an absolute
    distance cannot tell "peeled off to the left" from "crossed to the right"."""
    poly = loop_poly()
    best, bd = 0.0, float("inf")
    for a, b in zip(poly, poly[1:] + poly[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 <= 0.0:
            continue
        t = max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L2))
        d = math.dist((pt[0], pt[1]), (a[0] + dx * t, a[1] + dy * t))
        if d < bd:
            L = math.sqrt(L2)
            bd, best = d, (dx / L) * (pt[1] - a[1]) - (dy / L) * (pt[0] - a[0])
    return best


def leaves_mainline(pts, watch=GORE_CLEAR_RUN, clear=GORE_CLEAR_OFFSET):
    """True when the ramp peels off its mainline and STAYS off it.

    THE THIRD SHAPE GATE, and the one that was missing. `turns_back` rejects a fold and
    `departs_tangentially` rejects a right-angle turn-off, but neither says anything about which
    SIDE of the expressway the ramp ends up on -- so a candidate that simply runs down the middle
    of the deck passes both, and scores beautifully on radius and grade precisely because running
    straight along a motorway is the gentlest curve there is. `IC_RINKAI_E_EN` was fitted that way:
    it crawled to 2.8 m off the LOOP centreline over 80 m (the pavement is 11 m half-width, so it
    never left the carriageway at all) and then crossed to the other side.

    The cost of that is not cosmetic. A ramp overlapping its own mainline at the same height is a
    same-grade crossing with no shared vertex, so `graph_solve.weld_crossings` repairs it by
    welding a junction in -- 4.5 m from the gore, which leaves the gore and that junction with less
    than a car-length of road between them. Downstream that pair merges, and the interchange comes
    out as a pinched bow-tie of asphalt with the ramp appearing to drive into the middle of the
    expressway. Both halves are worth stating: a ramp must never cross its mainline, and it must
    be clear of its pavement within a bounded run."""
    if len(pts) < 2:
        return True
    run, side = 0.0, 0.0
    reached = 0.0
    for i, p in enumerate(pts):
        if i:
            run += math.dist(pts[i - 1][:2], p[:2])
        if run > watch:
            break
        off = _loop_offset(p)
        reached = max(reached, abs(off))
        # The gore itself sits ON the centreline, so the first few metres carry no side at all --
        # only a decisive offset gets to fix which side this ramp is leaving on.
        if abs(off) <= 0.5:
            continue
        if side == 0.0:
            side = off
        elif off * side < 0.0:
            return False                       # crossed its own mainline
    return reached >= clear


def leaves_on_the_nearside(pts, side, watch=GORE_CLEAR_RUN):
    """True when the ramp peels off on the KERB side of the carriageway it serves.

    `leaves_mainline` already refuses a ramp that never leaves the expressway; this refuses one
    that leaves on the WRONG SIDE of it. Under the single convention the pipeline now obeys the
    auxiliary lane always opens at the kerb, so a ramp on the median side of its own stream has no
    lane that can serve it -- `auto_aux_lanes` reports exactly that, and the only real fix is to
    move the ramp. Where the position is already being searched (an entry gore) it is cheaper to
    never choose such a spot in the first place.

    Measured over the ramp's real polyline for the same reason every other side test here is: the
    departure tangent of a tangential gore says almost nothing about where the ramp ends up."""
    if len(pts) < 2:
        return True
    g = (pts[0][0], pts[0][1])
    t = loop_tangent(g)
    sgn = 1.0 if side == 'FWD' else -1.0
    d = (t[0] * sgn, t[1] * sgn)
    run, worst = 0.0, 0.0
    for i in range(1, len(pts)):
        run += math.dist(pts[i - 1][:2], pts[i][:2])
        if run > watch:
            break
        cross = d[0] * (pts[i][1] - g[1]) - d[1] * (pts[i][0] - g[0])
        if abs(cross) > abs(worst):
            worst = cross
    return worst >= 0.0


#: How far off an arterial's centreline a ramp is aimed, so it arrives on the KERB side of the
#: carriageway it merges into rather than down the middle of the road. About one lane plus the
#: median half on this island's arterials -- far enough to put the ramp unambiguously on the near
#: half, close enough that the graph's snap still welds it to the same junction.
TOUCHDOWN_KERB_OFFSET = 6.0


def arrives_on_the_nearside(pts, watch=80.0):
    """True when a ramp reaches its TOUCHDOWN on the kerb side of the traffic it will merge into.

    A MEASUREMENT, not a constraint -- nothing in `ramps()` enforces it, deliberately. Aiming the
    touchdown at the near kerb, and running the ramp straight alongside the road before it, were
    both tried: they improve THIS number (8 of 9 ramps offside down to 3) and make the built
    network worse, because a ramp that approaches from the far side has to cross the arterial to
    reach the near kerb, which changes the arm the solver reads as its host and leaves auxiliary
    lanes unfed (the movement audit went from 0 problems to 2, then to 4). Landing these ramps
    nearside needs them re-routed to approach from the other side while still elevated, which is a
    design decision per interchange, not an offset.

    The companion to `leaves_on_the_nearside`, at the other end of the ramp. Keep-left puts every
    stream on the LEFT half of its road, so its kerb -- where an auxiliary lane opens and where a
    merging ramp has to arrive -- is on the left of its direction of travel. A ramp arriving on
    the other side is beside the OPPOSING carriageway: its traffic would have to cross that
    carriageway to reach the lane opened for it, which no lane placement can fix (`graph_solve.
    ramp_candidates` refuses to serve it, and the merge degrades to a turn).

    The ramp's own last stretch gives both terms: its heading IS the direction of the stream it
    joins (it is one-way and drawn in the direction it is driven), and its offset from the
    arterial at the touchdown gives the side. So this needs nothing but the ramp itself."""
    if len(pts) < 3:
        return True
    tip = (pts[-1][0], pts[-1][1])
    road = arterial_tangent(tip)
    # Travel direction at arrival, measured over the last `watch` metres so a single short
    # sample cannot decide it.
    run, back = 0.0, pts[-1]
    for i in range(len(pts) - 1, 0, -1):
        run += math.dist(pts[i][:2], pts[i - 1][:2])
        back = pts[i - 1]
        if run >= watch:
            break
    d = (tip[0] - back[0], tip[1] - back[1])
    n = math.hypot(*d) or 1.0
    d = (d[0] / n, d[1] / n)
    # The arterial's tangent runs either way; take the one the ramp is travelling with.
    if road[0] * d[0] + road[1] * d[1] < 0.0:
        road = (-road[0], -road[1])
    # Left of travel is +cross. The ramp approaches from `back`, so its offset from the road at
    # the touchdown is what side it came in on.
    cross = road[0] * (back[1] - tip[1]) - road[1] * (back[0] - tip[0])
    return cross >= 0.0


def departs_tangentially(pts, tangent, tol_deg=None):
    """True when the ramp LEAVES along the mainline rather than turning off it.

    A gore is a divergence, not a corner: the exit lane runs alongside the traffic it is leaving
    and peels away. Nothing in the search enforced that, and once folded candidates were rejected
    every parallel run collapsed to 0 m -- at which point the ramp heads straight for its
    touchdown, which for these interchanges means leaving a motorway at 65-86 degrees. The joint
    checker measures exactly that angle (`lane_joints.joint_alignment`'s `heading_deg`), and the
    tolerance is read from it, so the two cannot disagree.

    A ramp that fails this is not merely uncomfortable -- it cannot be JOINED. The mainline lane
    and the ramp lane have to hand over edge-to-edge, and two ribbons meeting at 80 degrees never
    do, whatever their radii say."""
    if tol_deg is None:
        tol_deg = _joint_heading_tol()
    if len(pts) < 2:
        return True
    dx, dy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
    if math.hypot(dx, dy) < 1e-9:
        return True
    h = math.degrees(math.atan2(dy, dx))
    ht = math.degrees(math.atan2(tangent[1], tangent[0]))
    return abs((ht - h + 180.0) % 360.0 - 180.0) <= tol_deg


def ramp_radius(pts):
    """The radius a ramp is JUDGED by -- the worse of the sustained bend and any local cusp.

    One number, because `fit_ramp` searches against it, and a search that optimises the sustained
    radius alone will happily buy it by folding a cusp into the middle of the curve -- which is
    exactly what a 0.95 handle scale does."""
    return min(min_radius_windowed(pts, window=RAMP_RADIUS_WINDOW),
               min_radius_windowed(pts, window=RAMP_KINK_WINDOW))


def fit_ramp(gore, touchdown, dz, kind="ramp", max_parallel=420.0,
             min_radius=RAMP_FIT_TARGET, side=None):
    """Grow the parallel run until the ramp satisfies BOTH constraints — long enough to make
    `dz` at the tier's grade, AND gentle enough to hold `min_radius` — trying both directions
    along the mainline and keeping the shorter solution.

    Radius matters as much as grade and was the missed half: a ramp short enough to be steep is
    also short enough to be a hairpin, and lengthening the parallel run fixes both at once
    because it moves the bezier's control point further from the touchdown. Returns
    (points, parallel_used, grade, fits) and reports rather than silently accepting a violation
    when even `max_parallel` cannot buy the run."""
    need = run_needed(dz, kind)
    tan = loop_tangent(gore)
    arrive = arterial_tangent(touchdown)
    # WHICH WAY THE RAMP LEAVES IS NOT THE SEARCH'S TO CHOOSE. `side` is the carriageway this
    # interchange serves, and the carriageway's direction of travel fixes the departure: an exit
    # from the FORWARD carriageway leaves along `+loop_tangent`, one from the REVERSE carriageway
    # along `-loop_tangent`. Letting the search try both and keep whichever solved more easily is
    # exactly how three of the island's exits ended up departing INTO the traffic they exit from
    # -- `lane_joints` measured IC_CHUO and IC_PORT at 179 deg and IC_YAMATE at 167 deg against
    # their own mainline lane, a seam that can be geometrically perfect and still undrivable.
    # (This is why the joint checker measures angle at all, and not only edge alignment: an edge
    # test catches a flip only as a side effect, as a gap of one lane width.)
    signs = (1.0, -1.0) if side is None else ((1.0,) if side == "FWD" else (-1.0,))
    best = None        # satisfies BOTH constraints
    effort = None      # best radius among those that at least satisfy grade
    shape = None       # best radius among those that at least LEAVE THE MAINLINE PROPERLY
    for sign in signs:
        t = (tan[0] * sign, tan[1] * sign)
        par = 0.0
        while par <= max_parallel:
            for ks in K_SCALES:
                pts = ramp_polyline(gore, touchdown, t, par, arrive=arrive, k_scale=ks)
                L = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
                r = ramp_radius(pts)
                # SHAPE FIRST, GRADE SECOND. A candidate that folds or turns off the motorway at
                # 80 degrees is not a ramp at all, so those are rejected before anything else is
                # judged; a candidate that is the right SHAPE but too short to make the descent is
                # remembered, because emitting it and reporting the grade beats emitting a
                # right-angle stub that no joint can ever connect (which is what the last-resort
                # fallback used to do -- measured at 65-86 deg on four interchanges).
                # NOT gated on `leaves_on_the_nearside` here, deliberately. As a fit gate it is
                # counterproductive: when no candidate satisfies it `fit_ramp` falls through to
                # its least-bad fallback, which is worse than the near-miss it rejected, and it
                # also hides the failure from the entry-gore SEARCH -- which uses the same test to
                # choose a better position and can only do that if it sees the real fit. Measured,
                # gating here took the island from one offside ramp back to two.
                if turns_back(pts) or not departs_tangentially(pts, t) \
                        or not leaves_mainline(pts):
                    continue
                if shape is None or r > shape[3]:
                    shape = (pts, par, sign, r)
                if L < need:
                    continue
                if effort is None or r > effort[3]:
                    effort = (pts, par, sign, r)
                if r >= min_radius and (best is None or L < best[4]):
                    best = (pts, par, sign, r, L)
            if best is not None:
                break
            par += 20.0
    if best is not None:
        pts, par = best[0], best[1]
        p3, grade, ok = grade_profile(pts, dz, 0.0, kind)
        return p3, par, grade, ok
    # Nothing satisfies both. Emit the LEAST-BAD candidate (the widest radius that still makes
    # the grade), not the longest one — a ramp forced out to max_parallel had the WORST radius
    # of all, because a gore whose tangent is square to its touchdown gets tighter the further
    # it runs, not gentler. When this fires, the geometry needs an authoring decision (a loop
    # ramp, or a touchdown moved further from the gore), which is exactly the kind of call this
    # tool should surface rather than fake.
    fallback = effort or shape
    if fallback is not None:
        pts, par = fallback[0], fallback[1]
    else:
        pts, par = ramp_polyline(gore, touchdown, tan, 0.0, arrive=arrive), 0.0
    p3, grade, _ = grade_profile(pts, dz, 0.0, kind)
    return p3, par, grade, False


def ramps():
    """Every interchange ramp as (id, points_with_z, parallel_run, grade, fits, kind). A
    ramp that does not fit is REPORTED, never silently steepened — the run is the thing to
    lengthen, and that is an authoring decision."""
    out = []
    for rid, gore, touch, kind, _note in INTERCHANGES:
        # A ramp departs FROM the mainline, so the gore is projected onto it before anything is
        # derived from it -- see `gore_on_loop` for what an unprojected one costs downstream.
        gore = gore_on_loop(gore)
        aim = touch
        if kind == "jct":
            # Expressway-to-expressway: both ends are already at deck height, so there is no
            # descent -- but that makes the GRADE constraint trivial, not the radius one, and this
            # branch used to skip the fitter entirely and take the bare bezier at parallel 0. On
            # the one ramp with no descent to trade against, that produced an 11.4 m radius
            # against a 59.1 m tier minimum: a corner no car can take, and the only entry left in
            # the self-test's bad list that nothing explained. Fit it exactly like every other
            # ramp with a zero descent -- the search then spends the freed length on radius --
            # and re-stamp the deck height afterwards.
            pts, par, _grade, ok = fit_ramp(gore, aim, 0.0, "ramp",
                                            side=interchange_side(rid))
            p3 = [(p[0], p[1], DECK_Z) for p in pts]
            out.append((rid, p3, par, 0.0, ok, kind))
            # AND ITS ENTRY. A junction between two expressways is a road you can drive BOTH
            # ways: with only the exit, the bridge is somewhere traffic can go and never come
            # back from -- the same dead end the connectivity gate caught when every exit sat on
            # one carriageway and every entry on the other (ROAD_KIT_REDESIGN.md defect 13). Built
            # exactly like a "pair" interchange's entry (its own gore, found by searching along
            # the mainline, and its own alignment), differing only in having no descent to make:
            # both of its ends are already at deck height.
            others = [g for r, g, _t, _k, _n in INTERCHANGES if r != rid]
            # THE SEARCH IS TOLD THE SERVING CARRIAGEWAY, the same one the fit below is told. An
            # entry SERVES the carriageway it merges into -- the one departing its gore -- and
            # must therefore lie on THAT stream's nearside, exactly as an exit lies on the
            # nearside of the stream feeding it. Handing the search the opposite direction made
            # it grade every candidate against the wrong stream and choose gores whose ramp was
            # offside of the one it actually feeds; `graph_solve.ramp_services` then reported
            # them (three of the island's entries, once entries became real merges rather than
            # second exits).
            eg, et = entry_endpoints(gore, touch, avoid=others,
                                     side=_opposite(interchange_side(rid)),
                                     serves=interchange_side(rid))
            epts, epar, _eg, eok = fit_ramp(eg, et, 0.0, "ramp",
                                            side=_opposite(interchange_side(rid)))
            out.append((rid + ENTRY_SUFFIX, [(p[0], p[1], DECK_Z) for p in epts],
                        epar, 0.0, eok, kind))
            continue
        p3, par, grade, ok = fit_ramp(gore, aim, DECK_Z, "ramp",
                                       side=interchange_side(rid))
        # grade_profile ran top->0; re-stamp so the deck end is the deck end.
        out.append((rid, p3, par, grade, ok, kind))
        if kind == "pair":
            # ...and its ENTRY, on its own alignment. Authored deck-end-first exactly like the
            # exit, so a consumer that needs it running INTO the mainline simply reverses it --
            # the same convention every other ramp here follows.
            others = [g for r, g, _t, _k, _n in INTERCHANGES if r != rid]
            # THE SEARCH IS TOLD THE SERVING CARRIAGEWAY, the same one the fit below is told. An
            # entry SERVES the carriageway it merges into -- the one departing its gore -- and
            # must therefore lie on THAT stream's nearside, exactly as an exit lies on the
            # nearside of the stream feeding it. Handing the search the opposite direction made
            # it grade every candidate against the wrong stream and choose gores whose ramp was
            # offside of the one it actually feeds; `graph_solve.ramp_services` then reported
            # them (three of the island's entries, once entries became real merges rather than
            # second exits).
            eg, et = entry_endpoints(gore, touch, avoid=others,
                                     side=_opposite(interchange_side(rid)),
                                     serves=interchange_side(rid))
            # AN ENTRY TAKES THE OPPOSITE SIGN, because it is authored deck-end-first and its
            # consumer REVERSES it (`island_v3_to_roadkit` passes `reversed(entry)` as a
            # 'merge'). After that reversal the ramp arrives at the gore heading the negative of
            # the direction it was generated in -- so generating it along the carriageway makes
            # it arrive against the carriageway. Measured: with the same sign as the exit,
            # `lane_joints` reported IC_CHUO_EN meeting its own mainline lane at 180 deg.
            e3, epar, egrade, eok = fit_ramp(eg, et, DECK_Z, "ramp",
                                              side=_opposite(interchange_side(rid)))
            out.append((rid + ENTRY_SUFFIX, e3, epar, egrade, eok, kind))
    return out


def spiral_ramp(center, r=G._s(40.0), z_top=DECK_Z, z_bot=ISLAND_Z, turns=1.0,
                start_deg=90.0, samples=48):
    """The Odaiba-end descent: a full loop ramp winding from the bridge deck down to island
    grade. This is the real Rainbow Bridge's own solution and the reason the airport island
    reads as arrived-at rather than driven-to.

    `r` CARRIES `G.SCALE` like every other layout dimension. It was a bare 40 m while `z_top` and
    `z_bot` scaled, so the loop's height moved with the world and its radius did not: at
    `SCALE` 2.0 the descent came out at 6.37% against a 6% ramp limit, purely because the helix
    stayed the size it was drawn at.
    """
    cx, cy = center
    pts = []
    for i in range(samples + 1):
        t = i / samples
        a = math.radians(start_deg) + 2 * math.pi * turns * t
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a),
                    z_top + (z_bot - z_top) * t))
    run = sum(math.dist(a[:2], b[:2]) for a, b in zip(pts, pts[1:]))
    return pts, abs(z_top - z_bot) / (run or 1.0)


#: The LOOP polyline every gore/tangent question is answered against. `G.LOOP` is the RAW ring --
#: eight corner points -- while the deck that actually gets built is filleted and resampled from
#: it, so a corner is replaced by an arc that can sit tens of metres inside the raw line. Asking
#: the raw ring where a gore lands therefore answers about a road that does not exist: IC_YAMATE's
#: gore projected onto the raw ring at (478.5, 196.5), which is 6.5 m from the nearest point of the
#: built deck, and the ramp fitted from there left at the wrong angle once the two were joined.
#: `island_v3_to_roadkit.collect_roads` calls `use_loop_polyline` with the real deck before it
#: fits any ramp, so the plan and the geometry agree. Unset, everything behaves exactly as before.
_LOOP_OVERRIDE = None


def use_loop_polyline(pts):
    """Answer every LOOP question against `pts` (the deck as actually built), or `None` to reset."""
    global _LOOP_OVERRIDE
    _LOOP_OVERRIDE = None if pts is None else [(float(p[0]), float(p[1])) for p in pts]


def loop_poly():
    """The LOOP as a list of 2D points -- the override if one is installed, else the raw ring."""
    return _LOOP_OVERRIDE if _LOOP_OVERRIDE is not None else list(G.LOOP)


def loop_deck():
    """The T1 flagship loop at deck height, closed."""
    return [(x, y, DECK_Z) for (x, y) in G.LOOP] + [(G.LOOP[0][0], G.LOOP[0][1], DECK_Z)]


# =============================================================================== §4
FARM_GRAINS = {
    # (parcel long, parcel short, bearing deg, farm-road spacing) — 圃場整備 standard is
    # 30 x 100 m (30a) on ONE bearing; the terraces are pre-consolidation and irregular.
    "consolidated": (100.0, 30.0, 0.0, 100.0),
    "terraced":     ( 60.0, 18.0, None, 0.0),     # bearing None = follow the contour
    "dune":         (120.0, 26.0, 74.0, 0.0),     # parallel to the coast (Niigata grain)
}
# geom.ZONES["farm"] carries three rects; this is which grain each one gets.
FARM_RECT_GRAIN = ["consolidated", "dune", "terraced"]


def parcels(rect, grain, seed=0):
    """Field parcels for one farm rect. The consolidated grain is deliberately DEAD regular
    — that regularity is the contrast that makes the terraces read as older land."""
    x0, y0, x1, y1 = rect
    lng, shr, bearing, _road = FARM_GRAINS[grain]
    rng = random.Random(seed)
    b = math.radians(bearing if bearing is not None else rng.uniform(-25, 25))
    c, s = math.cos(b), math.sin(b)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    span = math.hypot(x1 - x0, y1 - y0) / 2 + lng
    out = []
    u = -span
    while u < span:
        v = -span
        while v < span:
            jl = lng if bearing is not None else lng * rng.uniform(0.6, 1.0)
            quad = [(u, v), (u + jl, v), (u + jl, v + shr), (u, v + shr)]
            poly = [(cx + px * c - py * s, cy + px * s + py * c) for px, py in quad]
            if all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in poly) and \
               all(G.on_land(*p) for p in poly):
                out.append(poly)
            v += shr
        u += lng
    return out


# =============================================================================== §5
# Rail Z profile — viaduct through the dense core, at grade in the open, cutting at the
# ridge. Given as (distance-along-line fraction, mode, value) breakpoints per line.
RAIL_PROFILE = {
    "RAIL_MAIN":    [(0.00, Z_DRAPE, 0.0), (0.18, Z_DRAPE, 0.0), (0.30, Z_FIX, RAIL_Z),
                     (0.62, Z_FIX, RAIL_Z), (0.74, Z_DRAPE, 0.0), (1.00, Z_DRAPE, 0.0)],
    "RAIL_BRANCH":  [(0.00, Z_FIX, RAIL_Z), (0.22, Z_FIX, RAIL_Z), (0.40, Z_DRAPE, 0.0),
                     (1.00, Z_DRAPE, 0.0)],
    "RAIL_AIRPORT": [(0.00, Z_FIX, RAIL_Z), (1.00, Z_FIX, RAIL_Z)],   # bridge lower deck
}
RAIL_MIN_RADIUS = 400.0      # mainline; 300 m on older/local lines
RAIL_MAX_GRADE  = MAX_GRADE["rail"]


def rail_z_at(line_name, frac, ground_z=0.0):
    prof = RAIL_PROFILE.get(line_name)
    if not prof:
        return ground_z
    for (f0, m0, v0), (f1, m1, v1) in zip(prof, prof[1:]):
        if f0 <= frac <= f1:
            t = 0.0 if f1 == f0 else (frac - f0) / (f1 - f0)
            z0 = resolve_z(m0, v0, ground_z)
            z1 = resolve_z(m1, v1, ground_z)
            return z0 + (z1 - z0) * t
    return resolve_z(prof[-1][1], prof[-1][2], ground_z)


def min_radius_windowed(pts, window=25.0):
    """Tightest radius along a polyline, measured over a FIXED ARC-LENGTH WINDOW.

    THE CANONICAL CURVATURE CHECK — use this, not `curvature_radii`, whenever the answer is
    compared against a design minimum. Menger radius through three ADJACENT points measures the
    discretisation sagitta rather than the curve, so it gets WORSE the finer the sampling: one
    140 m arc reported 140.9 at a 20 m resample, 77.1 at 12 m and 38.8 at 6 m. Sampling the
    outer two points a fixed distance away makes the result a property of the geometry instead
    of the resample step. `curvature_radii` is kept for per-vertex inspection only."""
    clean = [pts[0]]
    for p in pts[1:]:
        if math.dist(p[:2], clean[-1][:2]) > 0.5:
            clean.append(p)
    n = len(clean)
    if n < 3:
        return float("inf")
    cum = [0.0]
    for a, b in zip(clean, clean[1:]):
        cum.append(cum[-1] + math.dist(a[:2], b[:2]))
    if cum[-1] < 2.0 * window:
        window = max(4.0, cum[-1] / 2.5)
    best = float("inf")
    for i in range(n):
        lo = hi = i
        while lo > 0 and cum[i] - cum[lo] < window:
            lo -= 1
        while hi < n - 1 and cum[hi] - cum[i] < window:
            hi += 1
        if cum[i] - cum[lo] < window * 0.6 or cum[hi] - cum[i] < window * 0.6:
            continue
        a, b, c = clean[lo], clean[i], clean[hi]
        ab, bc, ca = math.dist(a[:2], b[:2]), math.dist(b[:2], c[:2]), math.dist(c[:2], a[:2])
        area = abs((b[0]-a[0]) * (c[1]-a[1]) - (c[0]-a[0]) * (b[1]-a[1])) / 2.0
        if area < 1e-6:
            continue
        best = min(best, (ab * bc * ca) / (4.0 * area))
    return best


def curvature_radii(pts):
    """Menger radius at each interior vertex — how a rail line is checked against
    RAIL_MIN_RADIUS before it is drawn, rather than after it looks wrong."""
    out = []
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
        area = abs((b[0]-a[0])*(c[1]-a[1]) - (c[0]-a[0])*(b[1]-a[1])) / 2.0
        out.append(float("inf") if area < 1e-6 else (ab * bc * ca) / (4.0 * area))
    return out


# =============================================================================== tests
def _selftest():
    assert support_kind(0.0, 0.0) == SUPPORT_NONE
    assert support_kind(2.0, 0.0) == SUPPORT_FILL
    assert support_kind(12.0, 0.0) == SUPPORT_PIER
    assert support_kind(-1.5, 0.0) == SUPPORT_CUT
    assert support_kind(-8.0, 0.0) == SUPPORT_TUNNEL
    assert abs(fill_footprint(3.0, 0.0, 13.5) - (13.5 + 4.5)) < 1e-9
    assert fill_footprint(12.0, 0.0, 13.5) == 13.5            # pier: no toe
    assert abs(run_needed(12.0, "ramp") - 200.0) < 1e-9        # the §3 number
    assert len(pier_stations(120.0)) == 4

    q = quarter_at(*CASTLE_C)
    assert q is None or q == CASTLE_RINGS[0][1]
    assert quarter_at(CASTLE_C[0] + G._s(250), CASTLE_C[1]) == "samurai"
    assert quarter_at(*G._p((-260, -430)), zone="neonA") == "neon_core"

    bad_ramps = []
    for rid, p3, par, grade, ok, kind in ramps():
        # Report WHICH constraint failed. A single "TOO STEEP" label lied about IC_RINKAI_E,
        # which sits at 5.84% against a 6% limit and is actually limited by its radius — and a
        # wrong reason sends the author to fix the wrong thing.
        r = ramp_radius(p3)
        steep = grade > MAX_GRADE.get("ramp", 0.06) + 1e-9
        # Against the DESIGN SPEED's radius, not a hardcoded 30 m. The old literal is why
        # `IC_PORT` at r=50 m and `IC_RINKAI_W` at r=38 m both printed "OK" while needing 42% and
        # 21% superelevation respectively -- the self-test agreed with itself and with nothing else.
        tight = r < RAMP_MIN_RADIUS
        why = "OK" if not (steep or tight) else \
            ("TOO STEEP" if steep and not tight else
             "TIGHT r=%.0fm" % r if tight and not steep else "STEEP+TIGHT")
        print(f"  ramp {rid:<12} {len(p3):3d} pts  parallel {par:5.0f} m  "
              f"grade {grade*100:5.2f}%  r {r:6.1f} m  {why}  ({kind})")
        if steep or tight:
            bad_ramps.append("%s (%s)" % (rid, why))
    # ASSERTED, not merely printed (2026-08-15). This table has printed TIGHT/TOO STEEP lines for
    # as long as it has existed and nothing ever failed on them, so every ramp regression since has
    # been invisible unless someone read the output. Every ramp now satisfies both constraints --
    # see `ramp_polyline`'s arrival tangent and `fit_ramp`'s handle sweep -- so this can hold the
    # line from here.
    # NAMED, not silenced. These four cannot reach the 59 m a 45 km/h ramp needs from their
    # authored gore/touchdown pair -- the search tries every parallel run and handle scale and the
    # best curve that neither cusps nor doubles back is still short. That is the authoring
    # decision `fit_ramp` was written to surface: move the touchdown further along its arterial,
    # add a loop ramp, or sign these at ~30-35 km/h. Listing them means a FIFTH ramp regressing
    # still fails.
    #
    # `IC_RINKAI_E` and `IC_RINKAI_W` JOINED THIS LIST ON 2026-08-15, and the reason is worth
    # keeping. They used to report 61.7 m and 74.2 m on parallel runs of 300 m and 320 m -- both
    # of which were U-TURNS. Every windowed radius measure available here is blind to a hairpin
    # (see `turns_back`), so the search happily bought its "radius" by folding the ramp back on
    # itself, and the number it reported was real arithmetic on a shape no car can drive. With
    # folds rejected, every parallel run collapses to 0 m, because the honest geometry is simply
    # this: a gore and a touchdown ~180 m apart cannot absorb a 12 m descent (200 m at 6%) AND a
    # turn. The distance between the two points is the constraint, and no search can fix that.
    #
    # ALL SIX JOINED ONCE THE DEPARTURE DIRECTION WAS FIXED (2026-08-15). `fit_ramp` used to try
    # both signs of the mainline tangent and keep whichever solved more easily -- so three exits
    # were authored leaving INTO the traffic they exit from (`lane_joints` measured IC_CHUO and
    # IC_PORT at 179 deg against their own mainline lane, IC_YAMATE at 167). Forcing the sign from
    # `INTERCHANGE_SIDE` makes those exits leave the right way, and their real geometry appears:
    # IC_YAMATE 66.4 -> 11.6 m, IC_CHUO 46.6 -> 30.2 m, IC_PORT 47.4 -> 22.4 m. Nothing got worse;
    # the earlier numbers were measured on ramps pointing backwards. (The ENTRY ramps take the
    # OPPOSITE sign -- see `ramps()` -- and once given it, IC_CHUO_EN went 29.0 -> 76.2 m and left
    # this list.)
    #
    # The shape of the authoring decision is now specific: a real exit lands DOWNSTREAM of its
    # gore -- you leave the motorway and arrive further along. These touchdowns sit beside or
    # behind their gores, so a correctly-directed ramp has to turn most of the way round to reach
    # them. Move each touchdown further along its arterial in the direction of mainline travel, or
    # give the interchange a loop ramp, which is the real answer when a surface street is behind
    # the gore.
    # THE LIST IS THE MEASUREMENT, and it moves with `G.SCALE` — because `RAMP_MIN_RADIUS` is
    # 59.1 m whatever size the world is (a car's cornering does not scale), a bigger layout is
    # strictly more room for the fitter and a smaller one is strictly less. Measured on the same
    # nine ramps:
    #
    #     SCALE 0.75 (1512 m):  6 tight  — IC_CHUO 37, IC_CHUO_EN 38, IC_RINKAI_E 21,
    #                                      IC_YAMATE 55, IC_RINKAI_W 28, IC_PORT 36
    #     SCALE 2.00 (4032 m):  3 tight  — IC_RINKAI_E 47, IC_RINKAI_W 58, JCT_AIRPORT_EN 48
    #
    # `JCT_AIRPORT_EN` is the one that got WORSE (155 -> 48 m), and that is not a contradiction:
    # the fitter's own search is not scale-invariant even though the geometry is — `max_parallel`,
    # the 20 m parallel step and `need` are all absolute metres, so a bigger layout changes WHICH
    # candidate wins, not just how good it is.
    #
    # The shape of the authoring decision is unchanged: a real exit lands DOWNSTREAM of its gore --
    # you leave the motorway and arrive further along. These touchdowns sit beside or behind their
    # gores, so a correctly-directed ramp has to turn most of the way round to reach them. Move each
    # touchdown further along its arterial in the direction of mainline travel, or give the
    # interchange a loop ramp, which is the real answer when a surface street is behind the gore.
    NEEDS_AUTHORING = {"IC_RINKAI_E", "IC_RINKAI_W", "JCT_AIRPORT_EN"}
    unexpected = [b for b in bad_ramps if b.split()[0] not in NEEDS_AUTHORING]
    assert not unexpected, ("ramps violating grade or minimum radius: %s" % ", ".join(unexpected))
    if bad_ramps:
        print("  (%d ramp(s) need an authoring decision -- see NEEDS_AUTHORING: %s)"
              % (len(bad_ramps), ", ".join(bad_ramps)))
    sp, sg = spiral_ramp(G.AIRPORT_BRIDGE[1])
    print(f"  spiral loop ramp   {len(sp):3d} pts  grade {sg*100:5.2f}%")

    assert len(RING) == len(G.MAIN_BASE)
    r = curvature_radii(G.RAIL_MAIN)
    tight = [x for x in r if x < RAIL_MIN_RADIUS]
    print(f"  RAIL_MAIN min radius {min(r):7.1f} m  ({len(tight)} vertices under "
          f"{RAIL_MIN_RADIUS:.0f} m)")

    n_st = sum(len(street_grid(rect, BLOCKS[ZONE_QUARTER[z[0]]], seed=i))
               for i, z in enumerate(G.ZONES) for rect in z[1] if z[0] != "farm")
    print(f"  T3 street runs (jittered + doglegged): {n_st}")
    n_par = sum(len(parcels(rect, FARM_RECT_GRAIN[i], seed=i))
                for z in G.ZONES if z[0] == "farm" for i, rect in enumerate(z[1]))
    print(f"  farm parcels across 3 grains: {n_par}")
    print("island_v3_plan self-test OK")


if __name__ == "__main__":
    _selftest()

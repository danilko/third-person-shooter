#!/usr/bin/env python3
"""
world_grid.py — shared grid/theme/elevation/naming math for the master world layout AND
every per-district builder (PURE PYTHON, no bpy — same convention as road_network.py, so
this can be imported by a Blender-free checker script too).

Single source of truth so a district piece, built as its own isolated Python invocation,
can compute the SAME seam coordinate, elevation, and cross-district traffic route name
that tools/build_world.py's master layout expects at that grid cell — turning "alignment
by convention" (both files happen to use the same numbers) into "alignment by shared
computation" (both files import the same numbers from here).

Imported by tools/build_world.py, tools/check_seams.py, and every district-aware Blender tool
(session_common.py, open_world_session.py, etc.).
"""
import os

import road_network as rn

# ---- grid geometry (all on the 168/56/7 m grid) -------------------------------
CELL       = rn.CELL                  # 7 m road cell
DISTRICT   = 504.0                    # 3 regions = 9 zones = 72 road cells
DCELLS     = int(DISTRICT / CELL)     # 72 cells per district
GRID_N     = 6                        # 6 x 6 districts (was 8x8/4032m — resized for tighter
                                       # traversal + float-precision headroom, see AUTHORING_GUIDE.md)
WORLD      = DISTRICT * GRID_N        # 3024 m (~3 km)
ORIGIN     = WORLD / 2.0              # centre-origin shift: the world spans [-ORIGIN,+ORIGIN], not
                                       # [0,WORLD] — halves the worst-case distance from (0,0,0) at
                                       # the far corner (single-precision float headroom) for free,
                                       # independent of and stacked with the GRID_N reduction above.
LANE_OFF   = rn.LANE_OFF              # keep-left lane offset (Japan) — matches VehicleRoute default
LANE_STRIDE = 36                      # sample a backbone route every 36 cells (252 m)

# ---- Tokyo Bay + Haneda airport island + the connecting bridge (GRID-SPACE coords, m —
# to_world() applied at each use site). Shared here (not build_world.py-local) so the harbor
# blockout, the slot_ landmark anchors, AND the overlay generators (overlays/build_*_overlay.py,
# e.g. the Rainbow Bridge overlay) all compute the same seat from one source. Island moved out
# to sea (was Y0/Y1 = -450/-187.5) to make room for the real Rainbow Bridge span (~750 m raw
# extraction); the gap is now ~800 m. Placeholder-quality sizing — hand-tune positions later.
BAY_Y0, BAY_Y1 = -1108.0, 0.0                # sea band south of the map (Tokyo Bay)
ISL_X0, ISL_X1 = 1134.0, 2268.0              # airport island footprint (SE, near harbor cells)
ISL_Y0, ISL_Y1 = -1058.0, -795.5
BR_X = 1701.0                                # bridge centreline x (~ island centre)
BR_DECK_Z = 11.0                             # LAYER_EXPS — road deck top (engine Z, size-independent)
BR_RAIL_Z = 8.0                              # LAYER_RAIL — rail deck top (engine Z, size-independent)


def to_world(local):
    """Convert a grid-space coordinate (0..WORLD, corner-origin — what k*DISTRICT / gx*DISTRICT
    naturally produce) to the actual centre-origin world coordinate every placed object uses."""
    return local - ORIGIN


def from_world(world):
    """Inverse of to_world — recovers the 0..WORLD corner-origin coordinate from a world position."""
    return world + ORIGIN


def grid_cell_of(world_x, world_y):
    """Signed grid cell (gx, gy) containing a world position — the same cell-boundary convention
    district_center()/theme_at() use (via from_world()), just WITHOUT their 0..GRID_N-1 clamp, so
    it extends in any direction: a piece placed west of the current map gets a negative gx
    automatically, with zero renumbering of anything already built (confirmed against every real
    district: this recovers each one's own existing (gx,gy) exactly, since their positions were
    always computed from this same to_world()/from_world() pair in the first place — the clamp
    was only ever in theme_at/elev_at's grid-DESIGN lookup, never in the position math itself).
    Mirrors flank_z()'s existing from_world()-then-divide pattern; floor (not round) since this
    answers "which cell contains this point", not "which seam line is nearest"."""
    return int(from_world(world_x) // DISTRICT), int(from_world(world_y) // DISTRICT)


def piece_id_for_cell(gx, gy):
    """Canonical piece id for a signed grid cell — no theme, no legacy `District_` prefix; a
    piece's theme lives in pieces.json/its own collection properties now, not its filename."""
    return f"Piece_{gx}_{gy}"


def suggest_piece_id(world_x, world_y):
    """Auto-suggested id for a NEW piece anchored at a world position — what "Add Piece" pre-fills
    (still editable, so a hero piece can keep a memorable custom name instead)."""
    return piece_id_for_cell(*grid_cell_of(world_x, world_y))

# ---- themes (Japan gradient: harbor -> city -> residential -> rural -> mtn -> snow) ----
THEMES = {
    "harbor":   dict(name="Harbor",      elev=0.0,  col="metal",    ai=0.6, veh=0.7, lod=0.9, light=6500.0, fog=0.010),
    "city":     dict(name="NeonCity",    elev=2.0,  col="accent",   ai=1.4, veh=1.5, lod=1.2, light=4200.0, fog=0.004),
    "resid":    dict(name="Residential", elev=4.0,  col="roof",     ai=1.0, veh=1.0, lod=1.0, light=5200.0, fog=0.006),
    "rural":    dict(name="Rural",       elev=10.0, col="leaf",     ai=0.5, veh=0.4, lod=0.8, light=5800.0, fog=0.008),
    "mtn":      dict(name="Mountain",    elev=40.0, col="wood",     ai=0.3, veh=0.2, lod=0.7, light=6000.0, fog=0.014),
    "snow":     dict(name="Snow",        elev=90.0, col="line_w",   ai=0.2, veh=0.1, lod=0.6, light=7000.0, fog=0.020),
    # Coastal industrial/warehouse zone — sparse pedestrian AI, heavy truck traffic, warm
    # sodium-vapor-ish lighting + a bit more haze (smog) than a plain harbor cell.
    "industry": dict(name="Industrial",  elev=0.0,  col="concrete", ai=0.4, veh=1.3, lod=0.9, light=4500.0, fog=0.012),
}

# 6 rows NORTH (top) -> SOUTH (bottom); columns WEST (left) -> EAST (right).
# Harbor on the south coast (sea level), neon downtown core inland (Forza-Horizon-light town
# presence in the outer rings, not GTA-dense — see AUTHORING_GUIDE.md), rural ring, mountains/snow
# rising NW. Compressed from the original 8x8 gradient, same overall shape.
# Two generic filler cells (5,1)/(4,2) — both "recycled" auto-fill, no named/PLATEAU CONFIG
# entry — reassigned harbor/resid -> industry: a coastal industrial cluster next to the SE
# harbor cells, matching real Tokyo Bay's Keihin-style industrial belt hugging the waterfront.
MAP = [
    "snow   mtn    mtn    rural  rural  rural",     # gy=5 (north)
    "mtn    mtn    rural  resid  resid  rural",     # gy=4
    "rural  resid  city   city   resid  rural",     # gy=3
    "rural  resid  city   city   industry harbor",  # gy=2
    "resid  city   city   city   resid  industry",  # gy=1
    "harbor harbor harbor harbor harbor harbor",    # gy=0 (south coast)
]

# A cell can be marked VOID instead of a real theme keyword — "deliberately no district here,
# permanently" (an open-water gap carved for an organic coastline/harbor shape, NOT "not built
# yet", which every other cell already tolerates via build_world.py's placeholder Plate_*). No
# cell in MAP uses this today (every cell above is a real, already-built district). This is a
# pure MAP-source-text decision (hand-edit MAP above) — there is deliberately no runtime
# toggle/override mechanism for it anymore: removing a district from the world for real now goes
# through the piece registry (the addon's "Remove Piece" deletes its .blend + pieces.json entry
# and rebuilds the master), which is also the ONLY thing that makes sense for a freestanding
# piece (it has no grid cell to mark void in the first place). A grid cell with no registered
# piece already renders as the ordinary "not yet built" placeholder plate either way, so VOID is
# only for a cell that should never even show that placeholder.
VOID = "void"


def is_void(gx, gy):
    """True if (gx,gy) is an IN-GRID cell deliberately marked VOID in MAP. Distinct from an
    off-grid (gx,gy) — theme_at/elev_at already clamp those to the nearest real cell, a
    pre-existing map-edge behaviour this does NOT change. Every caller that walks neighbours
    (which can be off-grid OR void) must check grid bounds itself first, then this — mirrors the
    existing `if not (0 <= nx < GRID_N and 0 <= ny < GRID_N)` guard pattern already used
    everywhere rather than replacing it."""
    if not (0 <= gx < GRID_N and 0 <= gy < GRID_N):
        return False
    return MAP[GRID_N - 1 - gy].split()[gx] == VOID

# Tokyo hero districts: (slot name, district gx, gy, footprint cells). Pieces are grid-address-
# named like every other piece (Piece_<gx>_<gy>, see piece_id_for_cell) — the hero identity lives
# in the slot name / this table / build_district.py's plateau_json, not the filename. (Used to
# also carry a redundant hardcoded `.tscn` filename per entry — dropped since it's fully derivable
# from (gx,gy) via piece_id_for_cell(), the one call site that needs it now computes it inline.)
LANDMARKS = [
    ("shibuya",       1, 1, 5),   # scramble core, SW of centre
    ("tokyostation",  2, 2, 6),   # rail hub, central
    ("akihabara",     3, 3, 4),   # electric town, NE
    ("imperialpalace",2, 3, 6),   # real PLATEAU data, low-rise palace grounds
    ("tokyotower",    3, 2, 6),  # real surrounding blocks; Tokyo Tower itself
                                  # NOT in the PLATEAU extraction (lattice tower,
                                  # no solid footprint) -- needs hand-modeling as
                                  # a building-tier asset using the real anchor
                                  # point (139.7454E, 35.6586N) as reference.
    ("dotonbori",     3, 0, 6),  # Ebisu Bridge/Dotonbori -- Osaka, not Tokyo
                                  # (real anchor 135.501361E, 34.669056N, EPSG:6674
                                  # not 6677); part of the "greatest hits" collage,
                                  # not literal Tokyo geography. harbor theme.
    # Haneda is offshore (reclaimed island) — placed by build_world.build_harbor(), not on the grid.
]

# Every zone is wired (up front) to a PREDICTABLE piece path so a district piece authored/baked
# later goes live with no master re-bake (WorldZone.geometryPath, resolved lazily at stream time).
PIECE_DIR  = "res://src/main/resources/com/openworld/world/pieces/"


def piece_path(gx, gy):
    return PIECE_DIR + piece_id_for_cell(gx, gy) + ".tscn"


def lod_low_piece_path(gx, gy):
    """Predictable path for a district's low-detail placeholder tier (lib/lod_low.py),
    resolved the same lazy way as piece_path()'s full-detail geometry_path — WorldZoneMarker
    just skips it (ResourceLoader.exists() check) if that piece hasn't built one (PLATEAU
    precincts don't, see build_district.py's build() vs build_plateau() branches)."""
    p = piece_path(gx, gy)
    return p[:-len(".tscn")] + "_LOD_LOW.tscn"


def theme_at(gx, gy):
    gx = max(0, min(GRID_N - 1, gx)); gy = max(0, min(GRID_N - 1, gy))
    return MAP[GRID_N - 1 - gy].split()[gx]


def elev_at(gx, gy):
    return THEMES[theme_at(gx, gy)]["elev"]


def district_center(gx, gy):
    """Centre-origin world position of district (gx,gy)'s middle — where its WorldZoneMarker sits
    and where a district piece's own local origin (post-recenter) lands when streamed in."""
    return (to_world(gx * DISTRICT + DISTRICT / 2.0), to_world(gy * DISTRICT + DISTRICT / 2.0))


def flank_z(x, y):
    """Elevation of an arterial point sitting on a district seam = avg of flanking districts.
    `x`,`y` are centre-origin WORLD coordinates — converted back to 0..WORLD grid-space via
    from_world() before recovering the grid cell they sit on/near."""
    gsx, gsy = from_world(x), from_world(y)
    gx = int(round(gsx / DISTRICT)); gy = int(round(gsy / DISTRICT))
    if abs(gsx - gx * DISTRICT) < abs(gsy - gy * DISTRICT):   # on a vertical seam (x on a line)
        return (elev_at(gx - 1, int(gsy // DISTRICT)) + elev_at(gx, int(gsy // DISTRICT))) / 2.0
    return (elev_at(int(gsx // DISTRICT), gy - 1) + elev_at(int(gsx // DISTRICT), gy)) / 2.0


def sampled(lo, hi, stride):
    pts = list(range(lo, hi + 1, stride))
    if pts[-1] != hi:
        pts.append(hi)
    return pts


def seam_route_name(gxA, gyA, gxB, gyB, pos, kind):
    """Canonical cross-district VehicleRoute name for one of the 4 route pieces that make up
    a bidirectional seam crossing between two adjacent districts (gxA,gyA)/(gxB,gyB).

    Each district bakes independently (separate WorldBaker invocations, separate .tscn
    files) — VehicleRoute.nextRoutes resolves purely by NODE NAME searched across the
    whole live scene tree at runtime (see VehicleRoute.pickNextRoute), so a single shared
    name per direction would collide once both pieces stream in together. Instead there
    are 4 distinct roles, one owned by each side per direction of travel:
      'exit_lo'  — inside the lower-coordinate district, heading toward the higher one,
                   ending at the shared edge. next_routes -> 'entry_hi'.
      'entry_hi' — inside the higher-coordinate district, starting at the shared edge,
                   heading into that district's interior. (adopted via 'exit_lo'.next_routes)
      'exit_hi'  — inside the higher district, heading toward the lower one, ending at
                   the edge. next_routes -> 'entry_lo'.
      'entry_lo' — inside the lower district, starting at the edge, heading inward.
    "lower"/"higher" = Python tuple comparison of (gx,gy), so both neighbours agree on
    which role is theirs without coordinating — whichever one is being built just checks
    `(my_gx,my_gy) <= (neighbour_gx,neighbour_gy)`.

    `pos` — integer index of the crossing point along the shared edge (0 for the single
    arterial crossing each district builds today; room for multiple local-road crossings
    later without changing the naming scheme).
    """
    assert kind in ("exit_lo", "entry_lo", "exit_hi", "entry_hi")
    a, b = (gxA, gyA), (gxB, gyB)
    lo, hi = (a, b) if a <= b else (b, a)
    return f"seam_{lo[0]}_{lo[1]}_{hi[0]}_{hi[1]}_{pos}_{kind}"

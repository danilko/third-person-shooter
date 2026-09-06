#!/usr/bin/env python3
"""
assemble.py — grid -> scene helpers shared by every towns/ assembler.

Turns a road_network.TownGrid into instanced geometry: classified road tiles (with
per-cell rotation), a ground tile under every non-road cell (no void), sidewalk
strips on road boundaries (where props are allowed), VehicleRoute lane markers, and
a camera/sun. Buildings & props are placed by the town script via lib/buildings.py.
"""
import bpy, math, os
import kit_common as kc
import road_network as rn

CELL = rn.CELL          # 7.0 — world metres per grid cell (overlay/arterial helpers)


def wipe_scene():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for me in list(bpy.data.meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)


def _local_coll_get(name):
    """Local (non-library) collection lookup — same-named collections can also arrive via
    library links (tools/link_neighbors.py links neighbour districts' STREET collections)."""
    return next((c for c in bpy.data.collections
                 if c.name == name and c.library is None), None)


def _clear_coll(name):
    c = _local_coll_get(name)
    if c:
        for o in list(c.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(c)


def _named_coll(name):
    c = _local_coll_get(name)
    if c is None:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
    return c


# ── Lane-route emission (single funnel for every lane_ empty) ──────────────────────────
#
# ROUTE_PREFIX namespaces every district-local route name (lane_<piece>__<route>_<n>) so
# recycled districts that emit identical local names (h12_0_E …) stay unique once several
# stream in together — VehicleRoute.findRoute/pickNextRoute search the LIVE scene by name
# and would otherwise resolve into the wrong district. Seam routes bypass the prefix (their
# names are the cross-district contract and already carry the grid coords). The master
# build leaves the prefix unset.
#
# The `_0` empty of every route ALWAYS carries explicit route metas (WorldBaker reads only
# the first empty). `lane_offset=0.0` is load-bearing: emitters bake the keep-left offset
# into marker POSITIONS, but WorldBaker's default is 1.75 — without the explicit 0.0 the
# runtime shifted every lane a second time, collapsing both directions back onto the road
# centreline (the head-on-traffic bug).

ROUTE_PREFIX = ""


def set_route_prefix(stem):
    """Set (or clear, stem=None/"") the per-piece route namespace, e.g. the district piece
    stem. Call once at the top of a district build, after wipe_scene."""
    global ROUTE_PREFIX
    ROUTE_PREFIX = f"{stem}__" if stem else ""


def route_name(route):
    """The namespaced route name (what VehicleRoute nodes will be called after the bake)."""
    return f"{ROUTE_PREFIX}{route}"


def lane_empty(mk, route, n, loc, size=0.5, **props):
    """Create one lane_<prefixed route>_<n> empty. On the _0 empty, stamp the explicit
    route metas (defaults: positions-are-truth offset 0, one-way, despawn at end) merged
    with any overrides in **props (end_behavior, next_routes, next_weights, turn, …)."""
    name = f"lane_{ROUTE_PREFIX}{route}_{n}"
    # Blender truncates object names at 63 chars and .001-renames collisions — either would
    # silently corrupt WorldBaker's name-based route grouping, so fail the build instead.
    if len(name) > 63:
        raise ValueError(f"lane marker name exceeds Blender's 63-char cap: {name}")
    if name in bpy.data.objects:
        raise ValueError(f"duplicate lane marker name (route emitted twice?): {name}")
    e = bpy.data.objects.new(name, None)
    e.empty_display_size = size
    e.location = loc
    if n == 0:
        meta = {"lane_offset": 0.0, "loop": False, "end_behavior": "DESPAWN"}
        meta.update(props)
        for k, v in meta.items():
            e[k] = v
    mk.objects.link(e)
    return e


def setup(here, reopen=None):
    """Prepare the scene with all kit sources appended & hidden + GN group ready.

    If `reopen` names an EXISTING output .blend, open it and clear only the procedural
    layers (STREET / MARKERS) — the MANUAL collection and any hand-added content are
    PRESERVED, so you can regenerate the town around your manual edits. Otherwise a
    fresh scene is built."""
    kc.setup_units()
    if reopen and os.path.exists(os.path.join(here, reopen)):
        bpy.ops.wm.open_mainfile(filepath=os.path.join(here, reopen))
        # clear procedural layers + stale kit sources (re-appended fresh below);
        # everything else — notably the MANUAL collection — is preserved.
        for cn in ("STREET", "MARKERS", "ROADS", "WALLS", "PROPS", "EXTRAS"):
            _clear_coll(cn)
        for me in list(bpy.data.meshes):
            if me.users == 0:
                bpy.data.meshes.remove(me)
    else:
        wipe_scene()
    colls = kc.load_kits(here)        # idempotent — skips kits already in the file
    kc.hide_sources(colls)
    kc.make_gn_group()


def lay_roads(grid, coll, skip=None):
    """Road cells EXCEPT arterials (laid wide by lay_arterials) and cells claimed by
    lay_intersections (`skip`). STRAIGHT local/one-way runs are COMPOSED PER-LANE — 2*lanes_of
    Road_Lane_3p5 strips injected at ±LANE/2, ±3*LANE/2, … around the centreline (so a road
    widens by just raising its lane count) plus a yellow centre line; corners/tees/crosses/ends
    (and alleys) still stamp the baked auto-tiler tile. One-way cells get a Deco_Oneway arrow."""
    skip = skip or set()
    groups = {}                                       # baked tiles (corner/tee/cross/end/alley)
    lane_pts, lane_rots = [], []                      # injected straight lane strips
    cen_pts, cen_rots = [], []                        # centre lines (two-way straights only)
    arrow_pts, arrow_rots = [], []
    # a wide cell that drops lanes toward a narrower neighbour lays only the NARROW count here (its
    # outer lanes must not overrun the narrow cell) — lay_lane_tapers paves the funnel over it.
    drops = {(cx, cy): lo for (cx, cy, _ax, _hi, lo, _s) in grid.lane_transitions()}
    for (cx, cy) in sorted(grid.roads):
        if (cx, cy) in skip:
            continue                                  # laid by lay_intersections
        cls = grid.class_of((cx, cy))
        if cls == 'arterial':
            continue                                  # laid by lay_arterials
        opens = grid.open_sides(cx, cy)
        tile, rot = rn.tile_for(opens)
        wx, wy = cx * CELL, cy * CELL
        if tile == 'Road_Straight_7' and cls in ('local', 'oneway'):
            ew = bool({'E', 'W'} & opens)             # EW street runs along X; NS along Y
            axis = 'EW' if ew else 'NS'
            lrot = (0, 0, math.radians(90 if ew else 0))
            total = 2 * drops.get((cx, cy), grid.lanes_of((cx, cy)))  # full carriageway both ways
            for i in range(total):
                off = (i - (total - 1) / 2.0) * rn.LANE
                lane_pts.append((*_lateral(axis, cx, cy, off), 0.0)); lane_rots.append(lrot)
            if cls != 'oneway':                        # yellow centre divides opposing traffic
                cen_pts.append((wx, wy, 0.0)); cen_rots.append(lrot)
        else:
            groups.setdefault(tile, []).append(((wx, wy, 0.0), (0, 0, math.radians(rot))))
        if cls == 'oneway':                           # arrow points +X if EW, else +Y
            ar = -90 if (({'E', 'W'} & opens) and not ({'N', 'S'} & opens)) else 0
            arrow_pts.append((wx, wy, 0.0)); arrow_rots.append((0, 0, math.radians(ar)))
    for tile, items in groups.items():
        kc.instancer("Road_" + tile.split('_')[1], [p for p, _ in items], tile, coll,
                     rots=[r for _, r in items])
    if lane_pts:
        kc.instancer("Road_Lanes", lane_pts, "Road_Lane_3p5", coll, rots=lane_rots)
    if cen_pts:
        kc.instancer("Road_CenterLines", cen_pts, "Deco_Line_Center", coll, rots=cen_rots)
    if arrow_pts:
        kc.instancer("OnewayArrows", arrow_pts, "Deco_Oneway", coll, rots=arrow_rots)


def _arterial_routes(coll, nm, cells, axis):
    """Keep-left lane centrelines, one polyline per direction, down the middle of each
    direction's lane pair (lane_<nm><dir>_<n> for the WorldBaker)."""
    mk = _named_coll("MARKERS")
    dirs = (("E", 3.95), ("W", -3.95)) if axis == 'EW' else (("N", 3.95), ("S", -3.95))
    for side, off in dirs:
        seq = cells if side in ('E', 'N') else list(reversed(cells))
        for n, (cx, cy) in enumerate(seq):
            wx, wy = _lateral(axis, cx, cy, off)
            lane_empty(mk, f"{nm}{side}", n, (wx, wy, 0.1))


ARTERIAL_HALF = 8.5      # m from the arterial centreline to its kerb/sidewalk (=sw_off)
LOCAL_HALF = 3.5         # m from a local/oneway centreline to its kerb


def lay_arterials(grid, coll, skip=None):
    """Lay every arterial run as a WIDE DIVIDED avenue: two 3.5 m lanes each way around a
    raised central median, flanking sidewalks, and keep-left lane routes both directions.
    Lighting (median twin-arm lamps) is added by place_road_lights; signals by
    add_traffic_lights. Arterial cells are skipped by lay_roads. `skip` = cells claimed by a JP
    intersection piece (lay_intersections) — the avenue stops at the junction block."""
    skip = skip or set()
    lane_offs = (-5.7, -2.2, 2.2, 5.7)        # 2 lanes each side of the 0.9 m median
    sw_off = ARTERIAL_HALF
    laid = 0
    for axis, cells in grid.arterial_runs():
        rotq = (0, 0, math.radians(90 if axis == 'EW' else 0))
        cross = {'N', 'S'} if axis == 'EW' else {'E', 'W'}
        med_pts, lane_pts, sw_pts = [], [], []
        for (cx, cy) in cells:
            if (cx, cy) in skip:
                continue                              # the JP junction piece paves this block
            is_junction = bool(cross & grid.open_sides(cx, cy))
            for off in lane_offs:                     # asphalt always paves the carriageway
                lane_pts.append((*_lateral(axis, cx, cy, off), 0.0))
            if not is_junction:                       # median + walks break at a crossing
                med_pts.append((*_lateral(axis, cx, cy, 0.0), 0.0))
                for sgn in (-1, 1):
                    sw_pts.append((*_lateral(axis, cx, cy, sgn * sw_off), 0.0))
        nm = f"Art_{axis}_{cells[0][0]}_{cells[0][1]}"
        kc.instancer(f"{nm}_lanes", lane_pts, "Road_Lane_3p5", coll, rots=[rotq]*len(lane_pts))
        kc.instancer(f"{nm}_median", med_pts, "SM_Road_Median_7", coll, rots=[rotq]*len(med_pts))
        kc.instancer(f"{nm}_sw", sw_pts, "Road_Sidewalk_2", coll, rots=[rotq]*len(sw_pts))
        _arterial_routes(coll, nm, cells, axis)
        laid += 1
    return laid


def lay_intersections(grid, coll):
    """Stamp the JP intersection PIECES (road_network.intersection_pieces): multi-cell arterial
    turn-lane junctions (2 through + left + right per approach, islands, crosswalks) + 1-cell
    local / one-way junctions. Returns the set of CLAIMED cells — pass it as `skip=` to lay_roads
    and lay_arterials so they don't double-pave the junction. Call this BEFORE them."""
    placements, claimed = grid.intersection_pieces()
    groups = {}
    for (cx, cy, piece, rot, _foot) in placements:
        g = groups.setdefault(piece, ([], []))
        g[0].append((cx * CELL, cy * CELL, 0.0))
        g[1].append((0, 0, math.radians(rot)))
    for piece, (pts, rots) in groups.items():
        kc.instancer(f"Junction_{piece}", pts, piece, coll, rots=rots)
    return claimed


def _rotcw(vx, vy):
    """Rotate a unit vector 90° clockwise (a driver's RIGHT, given travel = (vx,vy))."""
    return (vy, -vx)


def add_traffic_lights(grid, coll):
    """Japanese horizontal signals at every ARTERIAL intersection (arterial × any road).
    For each approach (open side d) traffic travels toward the centre (t = -d); the signal
    pole stands at the sidewalk corner on the driver's LEFT, the arm (+X) cantilevers to the
    driver's RIGHT across the lanes, and the head (-Y lenses) faces the oncoming driver.
    Pole is always at the kerb, never in a lane. Local×local junctions stay unsignalled."""
    pts, rots = [], []
    for (cx, cy, opens) in grid.arterial_intersections():
        bx, by = cx * CELL, cy * CELL
        for d in opens:
            dx, dy = rn.DVEC[d]
            tx, ty = -dx, -dy                         # travel direction into the junction
            rx, ry = _rotcw(tx, ty)                   # driver's right (arm points here)
            lx, ly = -rx, -ry                         # driver's left  (pole sits here)
            approach_art = grid.class_of((cx + dx, cy + dy)) == 'arterial'
            sideW = ARTERIAL_HALF if approach_art else LOCAL_HALF   # kerb of the approach road
            backW = LOCAL_HALF if approach_art else ARTERIAL_HALF   # past the crossing road
            wx = bx + dx * backW + lx * sideW
            wy = by + dy * backW + ly * sideW
            pts.append((wx, wy, 0.0))
            rots.append((0, 0, math.atan2(ry, rx)))   # +X (arm) -> driver's right
    if pts:
        kc.instancer("TrafficLights", pts, "SM_Env_TrafficLight", coll, rots=rots)
    return len(pts)


def place_road_lights(grid, coll, every=3):
    """All ground street/road lighting in one pass:
      * local / oneway straight runs  -> SM_Env_Light_Cobra cantilever from ONE sidewalk
        side every `every` cells, the arm arching OVER the carriageway (pole on the walk);
      * arterial runs                 -> SM_Env_LampMedian twin-arm lamp down the median,
        every `every` non-junction cells.
    Returns (n_street_lights, n_median_lamps)."""
    cob_pts, cob_rots = [], []
    for (cx, cy) in sorted(grid.roads):
        if grid.class_of((cx, cy)) not in ('local', 'oneway'):
            continue
        opens = grid.open_sides(cx, cy)
        if {'N', 'S'} <= opens and not ({'E', 'W'} & opens):
            axis, along = 'NS', cy; perp = ('E', 'W')
        elif {'E', 'W'} <= opens and not ({'N', 'S'} & opens):
            axis, along = 'EW', cx; perp = ('N', 'S')
        else:
            continue                                  # skip junctions/corners
        if along % every:
            continue
        side = next((d for d in perp if (cx + rn.DVEC[d][0], cy + rn.DVEC[d][1]) not in grid.roads),
                    None)
        if side is None:
            continue
        dx, dy = rn.DVEC[side]
        px = cx * CELL + dx * (CELL/2 + 0.6)          # pole on the walk near the kerb
        py = cy * CELL + dy * (CELL/2 + 0.6)
        cob_pts.append((px, py, 0.0))
        cob_rots.append((0, 0, math.atan2(-dy, -dx)))  # arm (+X) points across the road
    if cob_pts:
        kc.instancer("StreetLights", cob_pts, "SM_Env_Light_Cobra", coll, rots=cob_rots)

    med_pts, med_rots = [], []
    for axis, cells in grid.arterial_runs():
        rotq = (0, 0, math.radians(90 if axis == 'EW' else 0))   # twin arm spans the road
        cross = {'N', 'S'} if axis == 'EW' else {'E', 'W'}
        for i, (cx, cy) in enumerate(cells):
            if i % every or (cross & grid.open_sides(cx, cy)):
                continue
            med_pts.append((cx * CELL, cy * CELL, 0.0)); med_rots.append(rotq)
    if med_pts:
        kc.instancer("MedianLamps", med_pts, "SM_Env_LampMedian", coll, rots=med_rots)
    return len(cob_pts), len(med_pts)


def place_corridor_lamps(corridor, coll, every=3):
    """SM_Env_LampMedian down an elevated CORRIDOR deck centreline (e.g. the expressway),
    at deck height — twin arms light both carriageways."""
    rotq = (0, 0, math.radians(90 if corridor.axis == 'EW' else 0))
    pts = [(cx * CELL, cy * CELL, corridor.z)
           for i, (cx, cy) in enumerate(corridor.cells) if i % every == 0]
    if pts:
        kc.instancer("COR_lamps", pts, "SM_Env_LampMedian", coll, rots=[rotq]*len(pts))
    return len(pts)


def place_corridor_barriers(corridor, coll, half=None, base_lanes=4, opens=None):
    """Noise/sound barriers along BOTH edges of an elevated corridor deck. The default lateral
    offset is rn.barrier_offset(base_lanes) — OUTSIDE the outermost lane plus a shoulder + shy gap
    (the old fixed half=6.9 sat 0.1 m INSIDE a 4-lane deck edge, i.e. in the travel lane). ONE call
    lays both sides; `opens`={'L': {(cx,cy)...}, 'R': {...}} OPENS a gap in that edge (so an on-ramp
    can merge in / an off-ramp peel off) and CAPS each gap end with a sloped SM_Exps_Barrier_End, so
    the ramp never sticks through the wall and the wall never blocks the merge. Returns the count."""
    half = rn.barrier_offset(base_lanes) if half is None else half
    rotq = (0, 0, math.radians(90 if corridor.axis == 'EW' else 0))
    opens = opens or {}
    cells = list(corridor.cells)
    idx = {c: i for i, c in enumerate(cells)}
    n = 0
    for side, off in (("L", -half), ("R", half)):
        gap = opens.get(side) or set()
        scells = [c for c in cells if c not in gap]
        pts = [(*_lateral(corridor.axis, cx, cy, off), corridor.z) for (cx, cy) in scells]
        if pts:
            kc.instancer(f"COR_barrier_{side}", pts, "SM_Exps_NoiseBarrier", coll,
                         rots=[rotq]*len(pts))
            n += len(pts)
        if gap:                       # cap both ends of the opening with a sloped terminal
            ordered = sorted(gap, key=lambda c: idx.get(c, 0))
            for end, c, flip in ((0, ordered[0], False), (1, ordered[-1], True)):
                i = idx.get(c)
                if i is None:
                    continue
                nb = cells[i-1] if (end == 0 and i) else (cells[i+1] if i+1 < len(cells) else None)
                if nb is None:
                    continue
                cap = (0, 0, math.radians((90 if corridor.axis == 'EW' else 0) + (180 if flip else 0)))
                p = (*_lateral(corridor.axis, nb[0], nb[1], off), corridor.z)
                kc.instancer(f"COR_barrier_cap_{side}{end}", [p], "SM_Exps_Barrier_End",
                             coll, rots=[cap])
                n += 1
    return n


def place_corridor_signs(corridor, coll, every=6):
    """Overhead direction-sign trusses spanning the deck every `every` cells (skips ramp
    cells). Uses the demoted SM_Env_LampGantry (now a sign truss)."""
    rotq = (0, 0, math.radians(90 if corridor.axis == 'EW' else 0))
    pts = [(cx * CELL, cy * CELL, corridor.z)
           for i, (cx, cy) in enumerate(corridor.cells) if i % every == 0]
    if pts:
        kc.instancer("COR_signs", pts, "SM_Env_LampGantry", coll, rots=[rotq]*len(pts))
    return len(pts)


# ======================================================= CURVILINEAR RAMP ENGINE
# Every on/off connection is a road_network.RampCurve swept by the GN curve->road engine.
# This single path replaces the old place_ramp / _swept_deck / place_helix / place_merge_tail
# (and the wedge-tile ramp_end): the deck width tapers, the path banks + climbs gently, the
# edge barriers are SEPARATE instanced pieces that OPEN at the merge, and the lane markers
# follow the curve so the network connects as a graph.

def _route_polyline_markers(coll, route, poly, z_off=0.2, **props):
    """Emit ordered lane_<route>_<n> empties along a 3D polyline (the WorldBaker route).
    **props are stamped on the _0 empty (end_behavior, next_routes, …)."""
    mk = _named_coll("MARKERS")
    for n, (wx, wy, wz) in enumerate(poly):
        lane_empty(mk, route, n, (wx, wy, wz + z_off), **props)


def _edge_polyline(pts, sgn, extra=0.0):
    """Edge polyline at centreline ± (half_w + extra) (XY perpendicular of the local tangent),
    with the fraction t per point. sgn +1 = left edge, -1 = right edge. `extra` pushes the line
    OUTSIDE the lane edge (a shy gap) so an edge wall never sits in the travel lane. `pts` is the
    DENSIFIED spine [(x,y,z,bank,half_w), ...] (rc.densify) — NOT the sparse rc.pts — so the edge
    tracks the smooth pavement exactly (the gap/facet fix). -> [(x,y,z,t)]."""
    n = len(pts)
    out = []
    for i, (x, y, z, bank, hw) in enumerate(pts):
        a = pts[max(0, i - 1)]; b = pts[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        L = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / L, tx / L                      # left normal
        off = hw + extra
        out.append((x + sgn * off * nx, y + sgn * off * ny, z, i / (n - 1)))
    return out


def place_curve_barriers(rc, coll, dp, gaps=None, wall_h=1.1, wall_t=0.18, collide=True):
    """CONTINUOUS upright walls down BOTH edges of a ramp curve — one GN swept barrier
    (kc.barrier_from_curve, GN_BarrierProfile) per run, following the climb with NO gaps, set
    rn.SHY OUTSIDE the lane edge so the wall never stands in the travel lane. Fed the SAME densified
    spine `dp` (rc.densify) as the road, so pavement edge and wall never diverge (the reported
    'large gap/unstable' bug). `gaps`=[(t0,t1), ...] fraction ranges OPEN the wall there (vehicle
    entry at the foot / the merge crossing), splitting each edge into runs. `collide` also lays a
    thin `-colonly` proxy per run so the wall is solid in-engine."""
    gaps = gaps or []
    in_gap = lambda t: any(g0 <= t <= g1 for (g0, g1) in gaps)
    cnt = 0
    def emit(side, run):
        nonlocal cnt
        if len(run) >= 2:
            kc.barrier_from_curve(f"{rc.tag}_wall_{side}{cnt}", run, coll, h=wall_h, thickness=wall_t)
            if collide:
                kc.colonly_swept(f"{rc.tag}_wall_{side}{cnt}", run, wall_t / 2.0, coll, z0=0.0, z1=wall_h)
            cnt += 1
    for side, sgn in (("L", 1.0), ("R", -1.0)):
        run = []
        for (x, y, z, t) in _edge_polyline(dp, sgn, extra=rn.SHY):
            if in_gap(t):
                emit(side, run); run = []
            else:
                run.append((x, y, z))
        emit(side, run)
    return cnt


def lay_curve_road(rc, coll, thickness=0.4, pier="SM_Exps_RampPier", pier_step=10.0,
                   pier_drop=1.4, walls=True, wall_gaps=None, grid=None,
                   straddle_cells=None, hpier="SM_Exps_HPier", seg_len=None, collide=True):
    """Lay ONE curvilinear ramp/connector (a RampCurve): sweep the smooth GN curve->road
    surface, drop TAPERED piers to grade along it, run CONTINUOUS edge walls (with `wall_gaps`
    openings), and emit the lane_<route> waypoints. The single entry point for every loop /
    trumpet / merge tail.

    PIER placement is lower-structure aware: a sample whose column would land on a lower
    carriageway — a street cell of `grid.roads` OR any cell in `straddle_cells` (register a lower
    deck/ramp's cells there) — gets an **H-PIER straddling bent** (`hpier`, legs on either side of
    the crossed road, yawed to the ramp so the gap spans travel) instead of a single column planted
    in the lane. That keeps the elevated deck supported everywhere while never sticking a pillar in
    the middle of the road/ramp below (the reported bug). Clear-ground samples get the normal
    tapered column.

    SINGLE SOURCE OF TRUTH: the spine is resampled ONCE via rc.densify(seg_len) into `dp`, and the
    road surface, both edge walls, piers, lane nodes AND the `-colonly` collision are ALL derived
    from `dp` — so none of them diverge (the old bug: road swept a smooth NURBS while walls/piers
    chorded the sparse rc.pts -> gap/facet). `seg_len` (default rc.seg_len = 7 m) sets the discrete
    granularity; the swept surface stays smooth regardless.

    A WALLED ramp sweeps the COMBINED cross-section (deck + BOTH parapets, kc.ramp_section_sweep) as
    ONE welded mesh — the wall sits ON the deck edge so road and wall can never gap (the reported
    'major gap' — separate road+barrier sweeps left the deck ending a shy-line short of the wall,
    which reads as open air on an elevated ramp). A wall-LESS curve (flat collector/taper) keeps the
    plain GN deck sweep + a colonly slab."""
    dp = rc.densify(seg_len)                                # the ONE resampled spine
    poly = [(x, y, z) for (x, y, z, _b, _hw) in dp]         # densified centreline
    if walls and rc.walls:                                 # deck + parapets = ONE mesh (no gap)
        road = kc.ramp_section_sweep(rc.tag, dp, coll, deck_t=thickness, gaps=wall_gaps or [],
                                     grip=rc.grip, collide=collide)
    else:
        road = kc.road_from_curve(rc.tag, dp, coll, matkey=rc.grip, thickness=thickness)
        if collide:                                        # drivable floor the swept GN deck lacks
            kc.colonly_swept(rc.tag, dp, [p[4] for p in dp], coll, z0=-thickness, z1=0.0)
    straddle = set(grid.roads) if grid is not None else set()
    if straddle_cells:
        straddle |= set(straddle_cells)
    ppts, prots, pscls = [], [], []              # normal tapered columns (clear ground)
    hpts, hrots, hscls = [], [], []              # H-piers straddling a lower road/deck
    for (pos, hd) in kc.sample_polyline(poly, pier_step):
        h = pos[2] - 0.45
        if h <= pier_drop:
            continue                              # deck ~at grade here — no column needed
        cell = (round(pos[0] / CELL), round(pos[1] / CELL))
        if cell in straddle:
            hpts.append((pos[0], pos[1], 0.0)); hrots.append((0.0, 0.0, hd))
            hscls.append((1.0, 1.0, h))
        else:
            ppts.append((pos[0], pos[1], 0.0)); prots.append((0.0, 0.0, hd))
            pscls.append((1.0, 1.0, h))
    if pier and ppts:
        kc.instancer_scaled(f"{rc.tag}_pier", ppts, pier, coll, prots, pscls)
    if hpier and hpts:
        kc.instancer_scaled(f"{rc.tag}_hpier", hpts, hpier, coll, hrots, hscls)
    # (walls are part of the combined section mesh above — see kc.ramp_section_sweep)
    if rc.route:
        _route_polyline_markers(coll, rc.route, poly)
    return road


def place_gore(coll, anchor, tag="MERGE"):
    """Drop a SM_Exps_Gore nose at a merge `anchor`=(x, y, z, heading): the painted gore where
    a ramp joins/leaves the carriageway, oriented so the nose narrows along the lane. Decorative
    — the merge itself is now ONE continuous swept surface (ramp_between + corridor_curve), so no
    bolted-on tail is needed."""
    ang = anchor[3] if len(anchor) > 3 else 0.0
    return kc.instancer(f"{tag}_gore", [(anchor[0], anchor[1], anchor[2])], "SM_Exps_Gore",
                        coll, rots=[(0.0, 0.0, ang - math.pi / 2)])


def lay_lane_tapers(grid, coll, z=0.03):
    """Pave a smooth swept CONVERGING asphalt surface over every surface lane-count DROP
    (grid.lane_transitions), so a wide local road funnels down to the narrower road/junction
    instead of its outer lanes overrunning it (the reported '2-lane -> 1-lane' error). Uses the
    same GN curve->road sweep as the ramps (kc.road_from_curve) at surface height with NO median —
    the expressway SM_Exps_Deck_Taper carries a jersey median and is for elevated decks only.
    Pair with lay_roads (which lays only the narrow lane count on the wide transition cell).
    Returns the count laid."""
    laid = 0
    for (cx, cy, axis, hi, lo, sign) in grid.lane_transitions():
        hw_hi, hw_lo = hi * rn.LANE, lo * rn.LANE          # half-widths funnel hi -> lo
        bx, by = cx * CELL, cy * CELL
        if axis == 'EW':                                   # centreline runs along X toward the nb
            p0, p1 = (bx - sign * CELL / 2, by), (bx + sign * CELL / 2, by)
        else:                                              # runs along Y
            p0, p1 = (bx, by - sign * CELL / 2), (bx, by + sign * CELL / 2)
        pts = [(p0[0], p0[1], z, 0.0, hw_hi), (p1[0], p1[1], z, 0.0, hw_lo)]
        kc.road_from_curve(f"Road_Taper_{cx}_{cy}", pts, coll, matkey="asphalt", thickness=0.1)
        laid += 1
    return laid


def lay_lane_taper(coll, world_xy, z, axis, add=False, tag="Taper"):
    """Drop a SM_Exps_Deck_Taper tile at (world_xy, z): a one-lane DROP along the corridor
    axis (4->3, chain two for 4->2). `add=True` rotates 180 to ADD a lane (the 2->3 case).
    The taper tile narrows the carriageway so lanes visibly merge rather than overlap."""
    rot = (90 if axis == 'EW' else 0) + (180 if add else 0)
    return kc.instancer(f"{tag}_taper", [(world_xy[0], world_xy[1], z)],
                        "SM_Exps_Deck_Taper", coll, rots=[(0, 0, math.radians(rot))])


def add_ramp_links(grid):
    """Emit a baker marker at every lane split/merge node (ramp <-> street lane graph):
    link_split_<route> / link_merge_<route> arrow empties pointing along the street-lane
    heading, so the WorldBaker knows where a through lane branches to / absorbs a ramp."""
    mk = _named_coll("MARKERS")
    for kind, route, x, y, hd in grid.ramp_links_world():
        e = bpy.data.objects.new(f"link_{kind}_{route}", None)
        e.empty_display_type = 'SINGLE_ARROW'; e.empty_display_size = 3.0
        e.location = (x, y, 0.2); e.rotation_euler = (0, 0, hd)
        e["route"] = route; e["kind"] = kind
        mk.objects.link(e)
    return mk


def check_junction(label, ramp_poly, host_poly, coll, tol=2.5):
    """Connectivity TEST CASE for the lane/waypoint graph: does the `ramp_poly` actually JOIN
    the `host_poly` (street/ramp/mainline lane) — i.e. is one of the ramp's endpoints within
    `tol` m of SOME point on the host lane? Drops a visible empty `joint_<label>_OK` (or
    `_GAP{d}`) at the join and prints the gap, so a disconnect is obvious from the top view and
    the console. Returns the gap distance (m)."""
    mk = _named_coll("MARKERS")
    best = (1e9, host_poly[0] if host_poly else (0, 0, 0))
    for ep in (ramp_poly[0], ramp_poly[-1]):
        for hp in host_poly:
            d = math.dist(ep, hp)
            if d < best[0]:
                best = (d, hp)
    d, at = best
    ok = d <= tol
    e = bpy.data.objects.new(f"joint_{label}_{'OK' if ok else 'GAP%.1f' % d}", None)
    e.empty_display_type = 'SPHERE' if ok else 'CUBE'
    e.empty_display_size = 1.2 if ok else 2.0
    e.location = (at[0], at[1], at[2] + 0.3)
    mk.objects.link(e)
    print("  JUNCTION %-22s gap=%5.2f m  %s" % (label, d, "OK" if ok else "*** DISCONNECTED ***"))
    return d


def lay_ground(grid, coll):
    pts = [(wx, wy, 0.0) for (wx, wy) in grid.ground_tiles()]
    kc.instancer("Ground", pts, "Road_Ground_7", coll)


def lay_sidewalks(grid, coll):
    items = [((wx, wy, 0.0), (0, 0, math.radians(rot))) for (wx, wy, rot) in grid.sidewalk_edges()]
    if items:
        kc.instancer("Sidewalks", [p for p, _ in items], "Road_Sidewalk_2", coll,
                     rots=[r for _, r in items])


def lay_lane_markers(grid, coll):
    """One empty per lane sample, named lane_<route>_<n> for the Java WorldBaker.
    Positions already carry the keep-left offset; lane_empty stamps lane_offset=0."""
    mk = _named_coll("MARKERS")
    for route, pts in grid.lane_routes().items():
        for n, (wx, wy) in enumerate(pts):
            lane_empty(mk, route, n, (wx, wy, 0.1))


def add_zone_markers(grid, coll=None):
    """zone_<id> empty at every 56 m chunk centre (size meta -> WorldZone.size). Chunks
    tile the whole map and abut on grid lines, so geometry/navmesh stitch at the seam."""
    mk = _named_coll("MARKERS")
    for zid, (wx, wy) in grid.zone_chunks().items():
        e = bpy.data.objects.new(f"zone_{zid}", None)
        e.empty_display_type = 'CUBE'
        e.empty_display_size = kc.ZONE / 2.0          # draws the 56 m chunk box
        e.location = (wx, wy, 0.0)
        e["size"] = [kc.ZONE, 10.0, kc.ZONE]
        mk.objects.link(e)
    return mk


def _lateral(axis, cx, cy, offset):
    """World (x,y) of a centreline cell shifted by `offset` m perpendicular to the line.
    EW lines run along X (lateral = Y); NS lines run along Y (lateral = X)."""
    if axis == 'EW':
        return (cx * CELL, cy * CELL + offset)
    return (cx * CELL + offset, cy * CELL)


def _route_markers(coll, route, cells, axis, offset, z, reverse):
    """Emit ordered marker empties (rail_/lane_ for the baker) at height + offset; the
    order (hence travel direction) is reversed for an opposing line."""
    mk = _named_coll("MARKERS")
    seq = list(reversed(cells)) if reverse else list(cells)
    for n, (cx, cy) in enumerate(seq):
        wx, wy = _lateral(axis, cx, cy, offset)
        e = bpy.data.objects.new(f"{route}_{n}", None)
        e.empty_display_size = 0.5
        e.location = (wx, wy, z + 0.2)
        mk.objects.link(e)


def _piers(coll, tag, pier, cells, pier_every, z, axis, grid, skip=None):
    """Drop a column every `pier_every` cells, SKIPPING any cell that is a road in
    `grid` (span cross-streets) or in `skip` (e.g. ramp cells) so columns land only in
    the clear reserved band at full deck height."""
    skip = skip or set()
    pts = []
    for i, (cx, cy) in enumerate(cells):
        if (cx, cy) in skip:
            continue
        if i % max(1, pier_every):
            continue
        if grid is not None and (cx, cy) in grid.roads:
            continue                         # don't drop a pier onto a crossing road
        pts.append((cx * CELL, cy * CELL, 0.0))
    if pts:
        kc.instancer(f"{tag}_pier", pts, pier, coll)


def lay_overlay(line, coll, grid=None):
    """Lay a single elevated run ABOVE the street: deck tiles at line.z (rotated to the
    direction, shifted by line.offset), auto-dropped piers (road-aware via `grid`), an
    optional track on the deck, and route markers (reversed for line.reverse)."""
    rot = 90 if line.axis == 'EW' else 0
    rotq = (0, 0, math.radians(rot))
    deck_pts = [(*_lateral(line.axis, cx, cy, line.offset), line.z) for (cx, cy) in line.cells]
    tag = "OL_" + line.deck.replace("SM_", "")
    kc.instancer(f"{tag}_deck", deck_pts, line.deck, coll, rots=[rotq]*len(deck_pts))
    if line.track:
        kc.instancer(f"{tag}_track", deck_pts, line.track, coll, rots=[rotq]*len(deck_pts))
    _piers(coll, tag, line.pier, line.cells, line.pier_every, line.z, line.axis, grid)
    if line.route:
        _route_markers(coll, line.route, line.cells, line.axis, line.offset, line.z, line.reverse)


def lay_corridor(corridor, coll, grid=None, consists=None, swept=False,
                 widenings=None, drops=None, base_lanes=4, grip="asphalt", thickness=0.5):
    """Lay a multi-track CORRIDOR: ONE wide deck + ONE pier line along the centreline
    (road-aware), then each parallel line's track + route markers at its lateral offset
    and direction. `consists` optional {route: [car pieces front->back]} to drop sample
    trains/vehicles on each line (reverse-aware).

    `swept=True` builds the carriageway as ONE GN curve->road surface (road_network.corridor_curve
    + kc.road_from_curve) instead of instanced deck tiles, so ramps merge into it as one
    continuous surface and it can BULGE +1 lane over a merge (`widenings`=[(side,c0,c1)]) or DROP
    a lane (`drops`=[(c0,c1)]) — see corridor_curve. Piers/tracks/markers are unchanged. Use the
    swept path for the road expressway; leave it off (tiled) for the rail viaduct."""
    rot = 90 if corridor.axis == 'EW' else 0
    rotq = (0, 0, math.radians(rot))
    tag = "COR_" + corridor.deck.replace("SM_", "")
    if swept:
        # the carriageway is ONE swept road-spline (highway = a road at a higher elevation)
        pts = rn.corridor_curve(corridor, base_lanes=base_lanes, widenings=widenings, drops=drops)
        kc.road_from_curve(f"{tag}_deck", pts, coll, matkey=grip, thickness=thickness)
    else:
        # the mainline is LEVEL — every cell at corridor.z, instanced deck tiles
        deck_pts = [(cx * CELL, cy * CELL, corridor.z) for (cx, cy) in corridor.cells]
        kc.instancer(f"{tag}_deck", deck_pts, corridor.deck, coll, rots=[rotq]*len(deck_pts))
    _piers(coll, tag, corridor.pier, corridor.cells, corridor.pier_every,
           corridor.z, corridor.axis, grid)
    for (off, route, reverse, track) in corridor.lines:
        if track:
            tpts = [(*_lateral(corridor.axis, cx, cy, off), corridor.z) for (cx, cy) in corridor.cells]
            kc.instancer(f"{tag}_{route}_track", tpts, track, coll, rots=[rotq]*len(tpts))
        if route:
            _route_markers(coll, route, corridor.cells, corridor.axis, off, corridor.z, reverse)
        if consists and route in consists:
            _consist(corridor.cells, consists[route], coll, corridor.axis, off,
                     corridor.z, reverse, route)


def place_overpass(coll, cells, clear=None, route=None, grid=None,
                   grade_piece="SM_Road_Grade_7", level_piece="SM_Exps_Deck_2L",
                   pier="SM_Exps_RampPier", tag="Overpass"):
    """A simple grade-separated OVERPASS built from grid tiles — 'a simpler intersection with the
    road raised up', NOT a corkscrew: GRADE tiles (SM_Road_Grade_7, each climbing CELL*MAX_GRADE)
    ascend to a LEVEL span over the crossing road, then descend. `cells` = the ordered centreline
    run of the road that goes over (axis inferred). Piers drop under the level span, skipping the
    crossing road. The approach length is set by the travelability budget (rn.overpass_cells).
    Returns (n_grade, n_level, n_pier)."""
    if len(cells) < 3:
        return (0, 0, 0)
    clear = rn.CLEAR_V if clear is None else clear
    axis = 'EW' if cells[1][0] != cells[0][0] else 'NS'
    per = CELL * rn.MAX_GRADE                          # rise per 7 m grade tile (0.56 m)
    deck_z = clear + rn.DECK_T
    m = len(cells)
    n = rn.overpass_cells(deck_z)                      # grade tiles each side
    if 2 * n + 1 > m:
        n = max(1, (m - 1) // 2)                       # shrink to fit (may not reach full clear)
    zlev = n * per
    # SM_Road_Grade_7 rises +Y locally; orient so it rises ALONG travel on the way up and AGAINST
    # it on the way down (high end always toward the level span).
    asc_rot  = -90 if axis == 'EW' else 0
    desc_rot =  90 if axis == 'EW' else 180
    lev_rot  =  90 if axis == 'EW' else 0
    g_up, gr_up, g_dn, gr_dn, lev, prs = [], [], [], [], [], []
    for i, (cx, cy) in enumerate(cells):
        wx, wy = cx * CELL, cy * CELL
        if i < n:                                      # ascending approach
            g_up.append((wx, wy, i * per)); gr_up.append((0, 0, math.radians(asc_rot)))
        elif i >= m - n:                               # descending approach
            g_dn.append((wx, wy, (m - 1 - i) * per)); gr_dn.append((0, 0, math.radians(desc_rot)))
        else:                                          # level span over the crossing
            lev.append((wx, wy, zlev))
            if grid is None or (cx, cy) not in grid.roads:
                prs.append((wx, wy, 0.0))
    if g_up:
        kc.instancer(f"{tag}_up", g_up, grade_piece, coll, rots=gr_up)
    if g_dn:
        kc.instancer(f"{tag}_dn", g_dn, grade_piece, coll, rots=gr_dn)
    if lev:
        kc.instancer(f"{tag}_deck", lev, level_piece, coll,
                     rots=[(0, 0, math.radians(lev_rot))] * len(lev))
    if pier and prs:                                   # unit pier scaled to the deck height
        kc.instancer_scaled(f"{tag}_pier", prs, pier, coll,
                            [(0, 0, 0)] * len(prs), [(1.0, 1.0, zlev)] * len(prs))
    if route:
        poly = [(p[0], p[1], p[2] + 0.3) for grp in (g_up, lev, g_dn) for p in grp]
        _route_polyline_markers(coll, route, poly)
    return (len(g_up) + len(g_dn), len(lev), len(prs))


# ======================================================= MODULAR WALLED RAMP (arrayed segments)
# The user's "built from one lane segment" ramp: array the one-lane-with-walls kit atoms
# (SM_Ramp_Grade_Wall_7 sloped + SM_Ramp_Lane_Wall_7 flat) down from an elevated deck edge to the
# street. v1 = a STRAIGHT inclined ramp (ref: expressway->ramp->regular-road schematic); the
# circular LOOP ramp (place_ramp_loop, P2) arrays the same atoms along a ramp_between polyline.

def place_ramp_straight(coll, start_xy, start_z, heading, end_z=0.0, grade=None, max_tiles=None,
                        grade_piece="SM_Ramp_Grade_Wall_7", flat_piece="SM_Ramp_Lane_Wall_7",
                        pier="SM_Exps_RampPier", pier_drop=1.4, tag="RampStr"):
    """A SIMPLE straight inclined walled RAMP built by ARRAYING the one-lane walled atom: descend
    from an elevated deck edge (`start_xy` at `start_z`) along `heading` toward the street (`end_z`)
    at <= MAX_GRADE, laying SM_Ramp_Grade_Wall_7 tiles (each drops CELL*grade), then a flat
    SM_Ramp_Lane_Wall_7 foot. Tapered SM_Exps_RampPier columns drop under the elevated part.
    `heading` = the DOWNHILL travel direction (deck -> street). The grade tile rises toward its local
    +Y (uphill), so it is rotated heading+90 deg. `max_tiles` caps the run to fit a scene (the foot
    then lands at whatever z the grade reached — a full deck-height drop honestly needs ~20 tiles,
    which is why the compact interchange uses the LOOP ramp instead). Returns the foot anchor
    (x, y, foot_z, heading) so the caller ties in a street lane + opens the deck barrier at the mouth."""
    grade = rn.MAX_GRADE if grade is None else grade
    per = CELL * grade
    n = max(1, math.ceil((start_z - end_z) / per))
    if max_tiles is not None:
        n = min(n, max_tiles)
    dx, dy = math.cos(heading), math.sin(heading)
    trot = (0.0, 0.0, heading + math.pi / 2)          # tile local +Y (high end) faces uphill
    gp, gr, ppts, prots, pscls = [], [], [], [], []
    for i in range(n):
        cx = start_xy[0] + (i + 0.5) * CELL * dx
        cy = start_xy[1] + (i + 0.5) * CELL * dy
        oz = start_z - (i + 1) * per                  # object z: high end sits at start_z - i*per
        gp.append((cx, cy, oz)); gr.append(trot)
        if oz > pier_drop:                            # column only where the deck is well off grade
            ppts.append((cx, cy, 0.0)); prots.append((0.0, 0.0, heading))
            pscls.append((1.0, 1.0, oz))
    kc.instancer(f"{tag}_grade", gp, grade_piece, coll, rots=gr)
    if pier and ppts:
        kc.instancer_scaled(f"{tag}_pier", ppts, pier, coll, prots, pscls)
    foot_z = max(end_z, start_z - n * per)            # z the grade actually reached at the foot
    fx = start_xy[0] + (n + 0.5) * CELL * dx
    fy = start_xy[1] + (n + 0.5) * CELL * dy
    kc.instancer(f"{tag}_foot", [(fx, fy, foot_z)], flat_piece, coll,
                 rots=[(0.0, 0.0, heading - math.pi / 2)])
    return (start_xy[0] + (n + 1) * CELL * dx, start_xy[1] + (n + 1) * CELL * dy, foot_z, heading)


def place_ramp_loop(coll, start, end, z0, z1, radius=25.0, side='L', turns=1.0,
                    seg_piece="SM_Ramp_Lane_Wall_7", pier="SM_Exps_RampPier", pier_drop=1.4,
                    pier_step=10.0, route=None, tag="RampLoop", grid=None):
    """DEPRECATED for curved loops — use `lay_curve_road(rn.ramp_between(...))` instead: arraying
    STRAIGHT 7 m tiles along a &lt;=30 m arc facets/gaps the deck and twists the walls (the reported
    "ramp not smooth / disconnected"). The swept GN curve->road surface is smooth by construction.
    Kept only for reference / the walled-atom kit piece; `place_ramp_straight` remains the way to
    build a straight grade ramp.

    A gentle CIRCULAR LOOP ramp built by ARRAYING the one-lane walled atom (refs: newjec / Shuto
    loops). Reuses rn.ramp_between ONLY to compute the descending loop PATH (tangent lead-in ->
    constant-radius controlling curve -> straight landing, grade <= MAX_GRADE, exit auto-aligned to
    `end`); `turns>=1` gives a full helical corkscrew. Then places SM_Ramp_Lane_Wall_7 tiles along
    the path, each YAWED to the curve and PITCHED to the local slope (rot euler = (pitch, 0, yaw),
    applied X-then-Z so pitch tilts the tile's length about its own width axis) — the walled channel
    follows the spiral with no vertical stair-steps. Tapered SM_Exps_RampPier columns drop under the
    elevated part (skipping road cells). Returns the RampCurve so the caller runs the lane-graph /
    connectivity test."""
    rc = rn.ramp_between(start, end, z0=z0, z1=z1, radius=radius, side=side, turns=turns,
                         route=route, grip="asphalt", tag=tag)
    poly = rc.lane_polyline()
    samples = kc.sample_polyline(poly, CELL)
    pts, rots = [], []
    ppts, prots, pscls = [], [], []
    accum = pier_step                                 # drop a pier at the first eligible segment
    for (a, ha), (b, hb) in zip(samples, samples[1:]):
        mx, my, mz = (a[0]+b[0])/2, (a[1]+b[1])/2, (a[2]+b[2])/2
        dxy = math.hypot(b[0]-a[0], b[1]-a[1]) or 1e-6
        pitch = math.atan2(b[2]-a[2], dxy)            # local slope -> tilt the tile's length
        yaw = ha - math.pi/2                          # align tile local +Y with the travel heading
        pts.append((mx, my, mz)); rots.append((pitch, 0.0, yaw))
        accum += dxy
        h = mz - 0.45                                 # deck underside height above grade
        if pier and h > pier_drop and accum >= pier_step:
            cell = (round(mx/CELL), round(my/CELL))
            if grid is None or cell not in grid.roads:
                ppts.append((mx, my, 0.0)); prots.append((0.0, 0.0, yaw)); pscls.append((1.0, 1.0, h))
                accum = 0.0
    kc.instancer(f"{tag}_seg", pts, seg_piece, coll, rots=rots)
    if pier and ppts:
        kc.instancer_scaled(f"{tag}_pier", ppts, pier, coll, prots, pscls)
    if rc.route:
        _route_polyline_markers(coll, rc.route, poly)
    return rc


def _consist(cells, cars, coll, axis, offset, z, reverse, label, z_off=0.3):
    """Place cars (front->back) along `cells` at the lateral offset; reverse flips the
    travel direction (cars start at the far end and face 180° the other way)."""
    rot = (90 if axis == 'EW' else 0) + (180 if reverse else 0)
    rotq = (0, 0, math.radians(rot))
    seq = list(reversed(cells)) if reverse else list(cells)
    for i, piece in enumerate(cars):
        if i >= len(seq):
            break
        cx, cy = seq[i]
        wx, wy = _lateral(axis, cx, cy, offset)
        kc.instancer(f"Consist_{label}_{i}_{piece.replace('SM_','')}",
                     [(wx, wy, z + z_off)], piece, coll, rots=[rotq])


def lay_consist(line, cars, coll, offset=0.0, reverse=False, z_off=0.3):
    """Place a train consist (front->back) along an OverlayLine, on top of the deck.
    `offset`/`reverse` default to the line's own (pass to override). Static sample; the
    game animates via the route."""
    _consist(line.cells, cars, coll, line.axis,
             line.offset if offset == 0.0 else offset,
             line.z, line.reverse or reverse, line.route or "L", z_off)


def lay_arterial(coll, axis, cross, start, end, lanes=4, name="Arterial"):
    """Compose a wide multi-lane avenue from granular Road_Lane_3p5 strips + flanking
    sidewalks — a driving-feel city avenue wider than the 7 m grid tile. `axis` 'EW'
    runs along X at y=`cross`; 'NS' runs along Y at x=`cross`. `lanes` 3.5 m lanes are
    centred on the line, tiled every CELL along the run. Emits lane_<name>L/R_<n>
    centrelines (keep-left, one per direction half) for the WorldBaker."""
    LANE = kc.LANE
    half = (lanes - 1) / 2.0
    steps = list(range(int(start // CELL), int(end // CELL) + 1))
    rot = 90 if axis == 'EW' else 0
    def world(along, off):
        return (along, cross + off) if axis == 'EW' else (cross + off, along)
    lane_pts, lane_rot = [], []
    for li in range(lanes):
        off = (li - half) * LANE
        for s in steps:
            wx, wy = world(s * CELL, off)
            lane_pts.append((wx, wy, 0.0)); lane_rot.append((0, 0, math.radians(rot)))
    kc.instancer(f"{name}_lanes", lane_pts, "Road_Lane_3p5", coll, rots=lane_rot)
    # flanking raised sidewalks just outside the outermost lanes
    sw_off = half * LANE + 1.0
    sw_pts, sw_rot = [], []
    for sgn in (-1, 1):
        for s in steps:
            wx, wy = world(s * CELL, sgn * sw_off)
            sw_pts.append((wx, wy, 0.0)); sw_rot.append((0, 0, math.radians(rot)))
    kc.instancer(f"{name}_sw", sw_pts, "Road_Sidewalk_2", coll, rots=sw_rot)
    # lane centrelines per direction (keep-left: left half goes one way, right the other)
    mk = _named_coll("MARKERS")
    for side, off in (("L", -LANE), ("R", LANE)):
        for n, s in enumerate(steps):
            wx, wy = world(s * CELL, off)
            lane_empty(mk, f"{name}{side}", n, (wx, wy, 0.1))
    return lane_pts


def add_region_markers(grid):
    """region_<id> anchor empty per REGION_CELLS chunk (bounds meta) — the tier above
    zones; separate region .blends abut on these boundaries."""
    mk = _named_coll("MARKERS")
    for rid, (wx, wy) in grid.region_chunks().items():
        e = bpy.data.objects.new(f"region_{rid}", None)
        e.empty_display_type = 'CUBE'
        e.empty_display_size = kc.CELL * 12        # ~168 m region box
        e.location = (wx, wy, 0.0)
        e["bounds"] = [kc.CELL * 24, 40.0, kc.CELL * 24]
        mk.objects.link(e)
    return mk


def add_region_portals(grid, rail_lines=()):
    """portal_road_<a>__<b>_<n> where a road crosses a REGION edge, and
    portal_rail_<a>__<b>_<n> at the ends of elevated rail/expressway runs that exit the
    region — so neighbouring region .blends interconnect for drive + train."""
    mk = _named_coll("MARKERS")
    import road_network as rn
    for n, (wx, wy, ra, rb, axis) in enumerate(grid.region_road_portals()):
        e = bpy.data.objects.new(f"portal_road_{ra}__{rb}_{n}", None)
        e.empty_display_type = 'SINGLE_ARROW'; e.empty_display_size = 3.0
        e.location = (wx, wy, 0.1); e["axis"] = axis
        mk.objects.link(e)
    for li, line in enumerate(rail_lines):
        for end, (cx, cy) in (("a", line.cells[0]), ("b", line.cells[-1])):
            rr = (rn.region_index(cx), rn.region_index(cy))
            e = bpy.data.objects.new(f"portal_rail_{rr[0]}_{rr[1]}_{li}{end}", None)
            e.empty_display_type = 'SINGLE_ARROW'; e.empty_display_size = 3.0
            e.location = (cx * CELL, cy * CELL, line.z + 0.2)
            mk.objects.link(e)
    return mk


def add_seam_sockets(grid):
    """portal_<zoneA>__<zoneB>_<n> empty wherever a road crosses a zone boundary, on the
    shared grid line -> marks an enter/exit connection so the lane continues into the
    next chunk (the seam contract)."""
    mk = _named_coll("MARKERS")
    for n, (wx, wy, za, zb, axis) in enumerate(grid.seam_sockets()):
        e = bpy.data.objects.new(f"portal_{za}__{zb}_{n}", None)
        e.empty_display_type = 'SINGLE_ARROW'
        e.empty_display_size = 2.0
        e.location = (wx, wy, 0.1)
        e["connects"] = f"{za}|{zb}"
        e["axis"] = axis
        mk.objects.link(e)
    return mk


def place_manual_slots(grid):
    """Drop-in anchors for hand-authored content. Each manual cell gets a `slot_<n>`
    arrows-empty in a MANUAL collection, at the cell centre, rotated to face the street.
    Hang your own building under it (or set its `asset_path` / rename it
    `instance_<AssetId>` so the WorldBaker swaps your asset in). MANUAL is preserved
    across regen (see setup(reopen=...))."""
    import buildings as bd
    man = _named_coll("MANUAL")
    for (cx, cy, wx, wy, rdir) in grid.manual_slots():
        name = f"slot_{cx}_{cy}"
        if bpy.data.objects.get(name):        # idempotent: keep an existing anchor
            continue                          # (and anything you parented to it) on regen
        rot = bd.ROT_FOR_DIR.get(rdir, 0)
        e = bpy.data.objects.new(name, None)
        e.empty_display_type = 'ARROWS'
        e.empty_display_size = 3.0
        e.location = (wx, wy, 0.0)
        e.rotation_euler = (0, 0, math.radians(rot))
        e["road_dir"] = rdir
        e["face_deg"] = rot
        e["asset_path"] = ""        # set to res://.../YourBuilding.tscn for the baker
        man.objects.link(e)
    return man


def add_camera_sun(coll, target, cam_loc, lens=24):
    tgt = bpy.data.objects.new("AIM", None); coll.objects.link(tgt); tgt.location = target
    cam_d = bpy.data.cameras.new("Cam"); cam = bpy.data.objects.new("Cam", cam_d)
    coll.objects.link(cam); cam_d.lens = lens; cam_d.clip_end = kc.VIEW_CLIP_END
    # A DEPTH BUFFER'S PRECISION IS THE RATIO clip_end/clip_start, NOT clip_end.
    #
    # 100 km far against Blender's default 10 cm near is a 10^6 range, and at that ratio two
    # surfaces 0.6 m apart 4 km from the camera fall inside one depth quantum. On the 4032 m island
    # that drew the sea straight THROUGH the ground in even horizontal bands — which reads exactly
    # like rows of missing terrain, and was chased as one until an exact per-object raycast found
    # 0 holes in 17 263 on-land samples. A preview camera never stands closer than a few metres to
    # anything, so 1/10000 of the far plane costs nothing and buys back four orders of magnitude.
    cam_d.clip_start = max(0.1, kc.VIEW_CLIP_END / 10000.0)
    cam.location = cam_loc
    con = cam.constraints.new("TRACK_TO"); con.target = tgt
    con.track_axis = "TRACK_NEGATIVE_Z"; con.up_axis = "UP_Y"
    bpy.context.scene.camera = cam
    sd = bpy.data.lights.new("Sun", "SUN"); sd.energy = 3.5
    sun = bpy.data.objects.new("Sun", sd); coll.objects.link(sun)
    sun.rotation_euler = (math.radians(52), math.radians(10), math.radians(40))


def finalize(here, fname):
    for o in bpy.data.objects:
        o.select_set(False)
    return kc.save_blend(here, fname)

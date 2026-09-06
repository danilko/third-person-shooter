"""point_preview.py -- the TRAFFIC FLOW preview: what Godot will actually receive, in the viewport.

WHY THIS EXISTS. Everything else in this addon shows the artist the AUTHORED graph -- points,
links, lane counts, the swept ribbon. None of that is what ships. What ships is
`.lanekit.json`: a directed lane graph with explicit successors and weights, and the two are not
the same object. A road can be perfectly built, perfectly gate-green, and still export a lane that
nothing can reach -- which is exactly what the demo network did for its whole life. Its exit ramp
had no predecessor at all, so no ambient car ever drove onto it, and there was no way to see that
from inside Blender short of exporting the file and reading it. The only symptom in-game is "the
ramp is always empty", which nobody attributes to authoring.

So this draws THE EXPORT, not the authoring: it runs `point_export.export_network` and renders the
document that comes back.

    lanes         every exported lane centreline, as a directed ribbon with chevrons, coloured by
                  what it IS -- carriageway, junction connector, ramp hand-off, merge
    successors    the `next` edges, drawn tail-to-head, so a chain that does not close is visible
                  as a gap rather than inferred from a JSON diff
    defects       a lane with no successor whose tail is sitting on another lane's head is drawn
                  RED (it should have chained and did not); a lane nothing can reach is drawn
                  MAGENTA; a RAMP nothing can reach is called out by name in the panel
    cars          optional agents that actually walk the graph, choosing successors by the exported
                  `next_weights`. A ramp no car ever enters is the fastest possible read of a
                  missing edge, and a junction where every car turns the same way is the fastest
                  read of a weight that is wrong.

The cars are a SIMULATION OF THE EXPORTED GRAPH and nothing more -- no collision, no following, no
signals. They answer "can traffic get here, and how often", which is the question the authoring
tool can answer; how it drives once it is there is Godot's.

DRAW HANDLERS NEVER WRITE. The agents advance in an `app.timers` tick and the handler only reads
them, the same split `point_live` documents: a draw handler is safe mid-modal precisely because it
touches no `bpy.data`, and that property is what lets the preview keep running while the artist
drags a point.
"""

import math
import random

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

try:
    from . import point_export as pe, point_model as pm, point_overlay as ov
except ImportError:                                        # headless smoketests import flat
    import point_export as pe                                                # noqa: E402
    import point_model as pm                                                 # noqa: E402
    import point_overlay as ov                                               # noqa: E402


# ------------------------------------------------------------------------------- look

COL_THROUGH = (0.30, 0.62, 1.00, 0.85)
COL_CONNECTOR = (1.00, 0.75, 0.20, 0.85)
COL_RAMP = (0.35, 1.00, 0.45, 0.95)
COL_MERGE = (0.75, 0.45, 1.00, 0.90)
COL_LINK = (1.00, 1.00, 1.00, 0.45)
COL_BROKEN = (1.00, 0.18, 0.12, 1.00)
COL_UNREACHED = (1.00, 0.25, 0.90, 1.00)
COL_CAR = (1.00, 0.95, 0.80, 1.00)
#: The Path3D drawn OVER its lane in `BOTH` mode -- deliberately colourless, so the lane keeps its
#: own meaning (ramp/merge/connector) underneath and the white line reads as "and here is what
#: actually ships".
COL_PATH = (1.00, 1.00, 1.00, 0.95)
#: The gap between the two, and any lane whose Path3D wanders further than `PATH_DEVIATION_WARN`.
COL_DEVIATION = (1.00, 0.55, 0.05, 1.00)

#: What `Preview > Geometry` draws. The default is the EXPORT, because that is the object this
#: whole panel exists to show -- drawing the polyline by default is how a 22.57 m error survived.
GEO_POLY, GEO_PATH, GEO_BOTH = 'POLY', 'PATH', 'BOTH'


def _geometry(scene):
    return getattr(scene, "rka_preview_geometry", GEO_PATH) if scene is not None else GEO_PATH

#: How far above the asphalt the flow ribbon floats. Enough to clear a kerb, small enough that it
#: still reads as belonging to the lane under it.
LIFT = 0.25

#: Rungs drawn between the lane and its Path3D in `BOTH` mode. Fixed per lane rather than per
#: metre: the reading is "do these two part, and where", which a short ramp needs as much as a
#: kilometre of arterial.
DEVIATION_RUNGS = 24

#: A rung is only drawn where the two geometries part by more than this. NOT the export's own fit
#: tolerance: since the fit converges to 0.05 m everywhere, keying on that draws a rung on every
#: lane in the world and the picture says nothing. Half the warn threshold is "this one is heading
#: for a finding", which is what a rung should mean.
DEVIATION_VISIBLE = 0.25

#: Cubic samples per span when DRAWING. The fit measures at `point_export.FIT_SAMPLES` because it
#: is bounding an error; the draw only has to look smooth, and 24 per span put 697 points on one
#: ramp lane -- five times the polyline it is drawn beside.
DRAW_SAMPLES = 8

#: Chevron spacing along a lane, in metres.
CHEVRON_EVERY = 22.0

#: A dead end whose tail is within this of ANOTHER lane's head is a BROKEN LINK, not the edge of
#: the world -- the two lanes are touching and simply were never wired. Deliberately wider than
#: `LaneGraph`'s 4.5 m chain radius: the point is to catch the near-misses too.
JOIN_TOL = 8.0

#: Ceiling on simulated agents, whatever the density asks for. The island exports thousands of
#: lanes and this is a draw handler.
MAX_CARS = 400


# ------------------------------------------------------------------------------- the document

#: The exported doc, its derived per-lane geometry, and the revision it was built at.
_cache = {"stamp": -2, "doc": None, "lanes": {}, "alt": {}, "dev": {},
          "report": None}

#: Bumped by `refresh()`; also compared against `point_overlay`'s revision so an edit invalidates
#: the preview exactly when it invalidates the overlay.
_rev = [0]


def invalidate():
    _rev[0] += 1
    _cache["stamp"] = -2


def _stamp():
    # The geometry mode is part of the stamp: switching it changes what every `LaneGeo` IS, so it
    # has to invalidate the cache exactly the way an edit does.
    return (ov._rev[0], _rev[0], _geometry(bpy.context.scene if bpy.context else None))


class LaneGeo(object):
    """One exported lane, ready to draw: Blender-space points, cumulative arclength, colour."""

    __slots__ = ("id", "pts", "cum", "length", "colour", "kind", "lane")

    def __init__(self, id, pts, cum, colour, kind, lane):
        self.id, self.pts, self.cum = id, pts, cum
        self.length = cum[-1] if cum else 0.0
        self.colour, self.kind, self.lane = colour, kind, lane

    def at(self, s):
        """`(position, tangent)` at arclength `s`, clamped to the lane."""
        if len(self.pts) < 2:
            return (self.pts[0] if self.pts else Vector()), Vector((1.0, 0.0, 0.0))
        s = max(0.0, min(self.length, s))
        lo, hi = 0, len(self.cum) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        span = max(1e-6, self.cum[lo + 1] - self.cum[lo])
        t = (s - self.cum[lo]) / span
        a, b = self.pts[lo], self.pts[lo + 1]
        d = b - a
        return a + d * t, (d.normalized() if d.length > 1e-9 else Vector((1.0, 0.0, 0.0)))


def _lane_colour(lane, doc_roads):
    if lane["kind"] == "connector":
        return COL_CONNECTOR, "connector"
    if doc_roads.get(lane["road_name"]) == "ramp":
        return COL_RAMP, "ramp"
    if "ramp" in (lane.get("next_kinds") or ()):
        return COL_RAMP, "ramp"
    if "merge" in (lane.get("next_kinds") or ()):
        return COL_MERGE, "merge"
    return COL_THROUGH, "through"


#: How closely two lanes must agree in heading before "your tail is on my head" means they were
#: meant to chain. Without it, every road in the world reports itself broken: a lane's tail at the
#: edge of the network sits exactly on the head of its OWN opposite-direction twin, which is not a
#: missing link, it is the other carriageway.
JOIN_MAX_TURN_DEG = 75.0


def _end_dir(pts, at_end):
    d = (pts[-1] - pts[-2]) if at_end else (pts[1] - pts[0])
    d.z = 0.0
    return d.normalized() if d.length > 1e-9 else Vector((1.0, 0.0, 0.0))


def _compatible(a, b):
    dot = max(-1.0, min(1.0, a.x * b.x + a.y * b.y))
    return math.degrees(math.acos(dot)) <= JOIN_MAX_TURN_DEG


#: How far a successor's head may sit from its predecessor's tail before the edge is nonsense.
#: Generous on purpose -- a connector's ends are solved, not snapped, and `LaneGraph`'s own
#: junction radius is 4.5 m. This is for edges that are WRONG, not edges that are loose.
MISJOIN_TOL = 12.0


def flow_report(doc):
    """THE DIAGNOSIS -- the whole reason a flow preview beats reading the JSON.

    Four facts, all of them about REACHABILITY rather than geometry, because geometry already has
    a gate and reachability had nothing:

        broken       a lane with no successor whose tail is sitting on the head of a lane going
                     the SAME WAY. The two are touching; the edge was simply never written. This
                     is the one that matters -- a car reaching it is reclaimed as route-finished
                     and vanishes.
        open_end     a lane with no successor that genuinely runs off the edge of the network.
                     Expected, and separated out so it cannot drown the previous line.
        unreached    a lane no successor points at, whose head is on the tail of a lane going the
                     same way -- the mirror of `broken`, and equally a missing edge. A lane whose
                     head touches nothing is a road entering the world and is not listed.
        misjoined    a lane whose declared successor's HEAD is not where this lane's TAIL is.
                     The edge exists and points somewhere else entirely -- which every other line
                     here reads as healthy, because they only ever ask whether an edge exists.
        ramp_orphans every lane on a road classed `ramp` that nothing leads to, listed whether or
                     not it touches anything, because "the ramp is always empty" is the in-game
                     symptom of exactly this and nobody ever attributes it to authoring.

    The direction gate is what makes the first three usable. Reachability is a property of a
    DIRECTED graph, and a report that cannot tell a carriageway's far end from its opposite
    carriageway names every road in the world -- which is a report nobody reads."""
    lanes = {l["id"]: l for l in doc.get("lanes", ())}
    road_class = {r["name"]: r.get("road_class", "") for r in doc.get("roads", ())}
    geo = {}
    for l in doc.get("lanes", ()):
        pts = [Vector(pe.blender(p)) for p in l["points"]]
        if len(pts) >= 2:
            geo[l["id"]] = (pts[0], _end_dir(pts, False), pts[-1], _end_dir(pts, True))
    reached = set()
    for l in doc.get("lanes", ()):
        for n in l.get("next") or ():
            reached.add(n)
    broken, open_end, unreached, ramp_orphans, misjoined = [], [], [], [], []
    for lid, l in lanes.items():
        g = geo.get(lid)
        if g is None:
            continue
        head, head_d, tail, tail_d = g
        # A SUCCESSOR THAT IS NOWHERE NEAR (8l). Every check below asks whether an edge EXISTS;
        # none of them asked whether the edge it found goes anywhere. An entrance ramp handed
        # into the stretch of aux slot UPSTREAM of its merge -- 600 m back down the road -- and
        # it was invisible to all four: the ramp had a successor, so not `broken`; the lane was
        # reached, so not `unreached`. In game that is a car reaching the end of a ramp and
        # teleporting, or being reclaimed as route-finished.
        kinds = l.get("next_kinds") or []
        for i, n in enumerate(l.get("next") or ()):
            # A `merge` edge is a lateral hand-over -- "this lane tapers into that one" -- and its
            # target legitimately spans the whole run, so its head is nowhere near this tail. Only
            # edges a car FOLLOWS end to end are chains.
            if (kinds[i] if i < len(kinds) else "chain") == "merge":
                continue
            o = geo.get(n)
            if o is not None and (o[0] - tail).length > MISJOIN_TOL:
                misjoined.append((lid, n, round((o[0] - tail).length, 1)))
        if not l.get("next"):
            near = [i for i, o in geo.items()
                    if i != lid and (o[0] - tail).length <= JOIN_TOL
                    and _compatible(tail_d, o[1])]
            (broken if near else open_end).append((lid, near[:3]))
        if lid not in reached:
            if road_class.get(l["road_name"]) == "ramp":
                ramp_orphans.append(lid)
                continue
            # A LANE THAT OPENS INSIDE ITS RUN IS SUPPOSED TO HAVE NO PREDECESSOR (8l): a
            # deceleration lane that appears after a junction is entered by a LANE CHANGE, and a
            # lane-change edge is `inner_lane`/`outer_lane`, not `next`. `spawnable` is exactly
            # "full width at both ends of its run", which is the question -- and it stays false
            # for a lane that dies inside too, which is equally not a missing predecessor.
            if not l.get("spawnable"):
                continue
            back = [i for i, o in geo.items()
                    if i != lid and (o[2] - head).length <= JOIN_TOL
                    and _compatible(head_d, o[3])]
            if back:
                unreached.append(lid)
    return {"lanes": len(lanes), "junctions": len(doc.get("junctions", ())),
            "broken": broken, "open_end": open_end, "misjoined": misjoined,
            "unreached": unreached, "ramp_orphans": ramp_orphans,
            # THE PATH IS NOT THE LANE. Every other line here is about reachability, which is a
            # property of the graph; this one is about the geometry that graph is drawn on, and it
            # is the only line that can catch a car driving somewhere the road is not.
            "path_off_road": pe.deviating_lanes(doc),
            "spawnable": sum(1 for l in doc.get("lanes", ()) if l.get("spawnable"))}


def document(scene=None, force=False):
    """The exported document, re-run at most once per edit. Returns None if the export raises --
    a preview must never be the thing that takes the session down."""
    stamp = _stamp()
    if not force and _cache["stamp"] == stamp and _cache["doc"] is not None:
        return _cache["doc"]
    try:
        net = pm.read_network(scene)
        doc = pe.export_network(net)
    except Exception as exc:                    # noqa: BLE001 -- a preview never raises
        print("[point_preview] export failed: %r" % (exc,))
        _cache["stamp"] = stamp
        _cache["doc"] = None
        return None
    road_class = {r["name"]: r.get("road_class", "") for r in doc.get("roads", ())}
    mode = _geometry(scene if scene is not None else (bpy.context.scene if bpy.context else None))
    geo, alt, dev = {}, {}, {}
    for l in doc["lanes"]:
        # THE PATH3D IS THE DEFAULT, and the cars walk it. Every other panel here shows the
        # authoring; this one shows what Godot receives -- and `WorldBaker` builds each `Curve3D`
        # from the `curve` block, not from `points`. Drawing the polyline while shipping the curve
        # is how a lane 22.57 m off the road previewed as perfect.
        drive = _lane_points(l, mode != GEO_POLY)
        if len(drive) < 2:
            continue
        cum, run = [0.0], 0.0
        for a, b in zip(drive, drive[1:]):
            run += (b - a).length
            cum.append(run)
        colour, kind = _lane_colour(l, road_class)
        d = pe.path_deviation(l)
        dev[l["id"]] = d
        if d > pe.PATH_DEVIATION_WARN:
            colour = COL_DEVIATION
        geo[l["id"]] = LaneGeo(l["id"], drive, cum, colour, kind, l)
        if mode == GEO_BOTH:
            alt[l["id"]] = _lane_points(l, False)
    _cache.update(stamp=stamp, doc=doc, lanes=geo, alt=alt, dev=dev, report=flow_report(doc))
    _reseed()
    return doc


def _nearest_on(poly, p):
    """The closest point to `p` anywhere on a polyline."""
    best, bd = poly[0], float("inf")
    for a, b in zip(poly, poly[1:]):
        d = b - a
        L = d.length_squared
        t = 0.0 if L < 1e-12 else max(0.0, min(1.0, (p - a).dot(d) / L))
        q = a + d * t
        if (q - p).length_squared < bd:
            best, bd = q, (q - p).length_squared
    return best


def _lane_points(lane, as_path):
    """The lane as Blender-space points -- either the exported Path3D or the sampled polyline.

    ONE conversion site, and it goes through `point_export.curve_polyline` for the Path3D so the
    preview, the gate and the export's own self-test all evaluate the shipped curve with the same
    function. A picture drawn by a second implementation is a picture that can be wrong on its
    own."""
    src = pe.curve_polyline(lane, DRAW_SAMPLES) if as_path else (lane.get("points") or ())
    return [Vector(pe.blender(p)) + Vector((0.0, 0.0, LIFT)) for p in src]


def report(scene=None):
    document(scene)
    return _cache["report"]


# ------------------------------------------------------------------------------- the agents

class Car(object):
    __slots__ = ("lane", "s", "speed", "colour")

    def __init__(self, lane, s, speed, colour):
        self.lane, self.s, self.speed, self.colour = lane, s, speed, colour


_cars = []


def _weighted(ids, weights):
    """Successor choice by the EXPORTED weights -- the same distribution Godot will roll, so a
    junction whose weights are wrong looks wrong here. Malformed weights degrade to uniform rather
    than raising, matching `util/WeightedPick` on the runtime side."""
    if not ids:
        return None
    try:
        w = [max(0.0, float(x)) for x in (weights or ())]
    except (TypeError, ValueError):
        w = []
    if len(w) != len(ids) or sum(w) <= 0.0:
        return random.choice(ids)
    r = random.uniform(0.0, sum(w))
    for i, x in enumerate(w):
        r -= x
        if r <= 0.0:
            return ids[i]
    return ids[-1]


def _spawn_lanes():
    return [g for g in _cache["lanes"].values() if g.lane.get("spawnable") and g.length > 8.0]


def _reseed(scene=None):
    """Re-seed the agent set for the current document and density."""
    del _cars[:]
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None or not getattr(scene, "rka_preview_cars", False):
        return
    pool = _spawn_lanes()
    if not pool:
        return
    per_km = max(0, int(getattr(scene, "rka_preview_density", 8)))
    total = sum(g.length for g in pool) / 1000.0
    want = min(MAX_CARS, int(total * per_km) + (1 if pool else 0))
    for _ in range(want):
        g = random.choice(pool)
        _cars.append(Car(g.id, random.uniform(0.0, g.length),
                         max(4.0, float(g.lane.get("speed_limit", 50.0)) / 3.6), g.colour))


def step(dt):
    """Advance every agent. Pure state; no drawing, no `bpy.data` writes."""
    geo = _cache["lanes"]
    if not geo:
        return
    pool = None
    for car in _cars:
        g = geo.get(car.lane)
        if g is None:
            if pool is None:
                pool = _spawn_lanes()
            if not pool:
                continue
            g = random.choice(pool)
            car.lane, car.s, car.colour = g.id, 0.0, g.colour
            continue
        car.s += car.speed * dt
        if car.s < g.length:
            continue
        over = car.s - g.length
        nxt = _weighted(g.lane.get("next") or [], g.lane.get("next_weights"))
        n = geo.get(nxt) if nxt else None
        if n is None:
            # ROUTE-FINISHED. This is precisely what the Godot runtime does with a car that runs
            # out of successors -- it reclaims it -- so a preview that teleported the car onward
            # would hide the very defect it exists to show. Respawn somewhere legal instead.
            if pool is None:
                pool = _spawn_lanes()
            if not pool:
                continue
            n = random.choice(pool)
            over = 0.0
        car.lane, car.s, car.colour = n.id, min(over, n.length), n.colour
        car.speed = max(4.0, float(n.lane.get("speed_limit", 50.0)) / 3.6)


# ------------------------------------------------------------------------------- drawing

def _shader():
    return gpu.shader.from_builtin('UNIFORM_COLOR')


def _lines(coords, colour, width=1.6):
    if not coords:
        return
    sh = _shader()
    gpu.state.line_width_set(width)
    gpu.state.blend_set('ALPHA')
    batch = batch_for_shader(sh, 'LINES', {"pos": coords})
    sh.bind()
    sh.uniform_float("color", colour)
    batch.draw(sh)
    gpu.state.line_width_set(1.0)


def _tris(coords, colour):
    if not coords:
        return
    sh = _shader()
    gpu.state.blend_set('ALPHA')
    batch = batch_for_shader(sh, 'TRIS', {"pos": coords})
    sh.bind()
    sh.uniform_float("color", colour)
    batch.draw(sh)


def _chevrons(g, out):
    """Direction marks along a lane. A ribbon with no arrows is a road; a ribbon with arrows is a
    ONE-WAY lane, and telling those apart at a glance is most of what this preview is for."""
    s = CHEVRON_EVERY * 0.5
    while s < g.length:
        p, t = g.at(s)
        n = Vector((-t.y, t.x, 0.0))
        out += [p, p - t * 2.2 + n * 1.1, p, p - t * 2.2 - n * 1.1]
        s += CHEVRON_EVERY


def flow_batches(scene=None):
    """`{colour: [vertices]}` for every line batch the flow overlay draws, plus `"_cars"` as a
    triangle list. NO GPU CALLS -- which is the point: this is all the arithmetic, so a headless
    smoketest can assert the preview draws the right thing without a drawing context, exactly the
    way the rest of this plugin keeps its geometry out of bpy."""
    doc = document(scene)
    if doc is None:
        return {}
    geo = _cache["lanes"]
    rep = _cache["report"] or {}
    broken = {i for i, _n in rep.get("broken", ())}
    unreached = set(rep.get("unreached", ())) | set(rep.get("ramp_orphans", ()))

    out = {c: [] for c in (COL_THROUGH, COL_CONNECTOR, COL_RAMP, COL_MERGE,
                           COL_LINK, COL_PATH, COL_DEVIATION, COL_BROKEN, COL_UNREACHED)}
    # `BOTH`: the lane polyline underneath in its own colour, the shipped Path3D in white over it,
    # and a rung between them wherever they part by more than the export's own fit tolerance. The
    # rungs are the reading -- two lines a metre apart look like one line from any distance a whole
    # junction fits into.
    for lid, poly in (_cache.get("alt") or {}).items():
        g = geo.get(lid)
        if g is None or len(poly) < 2:
            continue
        col = g.colour if g.colour in out else COL_THROUGH
        for a, b in zip(poly, poly[1:]):
            out[col] += [a, b]
        for k in range(DEVIATION_RUNGS + 1):
            # Take the rung's foot at a fraction of the PATH's own arclength and drop it onto the
            # NEAREST point of the lane -- never the polyline's sample at the same index. The two
            # are sampled differently (4 m on the lane, per-span on the curve), so equal-index
            # pairs are metres apart along the road even where the two curves coincide exactly:
            # measured 926 rungs on a network whose worst real gap was 0.0988 m.
            b, _t = g.at(g.length * k / float(DEVIATION_RUNGS))
            a = _nearest_on(poly, b)
            if (a - b).length > DEVIATION_VISIBLE:
                out[COL_DEVIATION] += [a, b]
    both = bool(_cache.get("alt"))
    for g in geo.values():
        col = g.colour if g.colour in out else COL_THROUGH
        # In `BOTH` the lane already drew in its own colour, so the Path3D is the white one.
        line = COL_PATH if both and g.colour is not COL_DEVIATION else col
        for a, b in zip(g.pts, g.pts[1:]):
            out[line] += [a, b]
        _chevrons(g, out[line])
        tail = g.pts[-1]
        for nxt in g.lane.get("next") or ():
            n = geo.get(nxt)
            if n is not None:
                out[COL_LINK] += [tail, n.pts[0]]
        if g.id in broken:
            # An X on the tail, at the place the chain stopped.
            for dx, dy in ((1, 1), (-1, 1)):
                out[COL_BROKEN] += [tail + Vector((dx * 2.5, dy * 2.5, 0.0)),
                                    tail - Vector((dx * 2.5, dy * 2.5, 0.0))]
        if g.id in unreached:
            head, prev = g.pts[0], None
            for k in range(9):
                a = k / 8.0 * math.tau
                cur = head + Vector((math.cos(a) * 3.0, math.sin(a) * 3.0, 0.0))
                if prev is not None:
                    out[COL_UNREACHED] += [prev, cur]
                prev = cur

    tris = []
    if scene is not None and getattr(scene, "rka_preview_cars", False):
        for car in _cars:
            g = geo.get(car.lane)
            if g is None:
                continue
            p, t = g.at(car.s)
            n = Vector((-t.y, t.x, 0.0))
            up = Vector((0.0, 0.0, 0.4))
            tris += [p + t * 2.0 + up, p - t * 1.6 + n * 1.0 + up, p - t * 1.6 - n * 1.0 + up]
    out["_cars"] = tris
    return out


#: Line width per batch. Widest LAST, so a defect is the thing you see.
_WIDTH = {COL_THROUGH: 2.0, COL_CONNECTOR: 2.0, COL_RAMP: 2.4, COL_MERGE: 2.0,
          COL_LINK: 1.2, COL_PATH: 1.8, COL_DEVIATION: 3.0,
          COL_UNREACHED: 2.4, COL_BROKEN: 3.0}
_ORDER = (COL_THROUGH, COL_CONNECTOR, COL_MERGE, COL_RAMP, COL_LINK, COL_PATH,
          COL_UNREACHED, COL_BROKEN, COL_DEVIATION)


def _draw_3d():
    scene = bpy.context.scene
    if not getattr(scene, "rka_preview_flow", False):
        return
    batches = flow_batches(scene)
    if not batches:
        return
    for col in _ORDER:
        _lines(batches.get(col), col, _WIDTH[col])
    _tris(batches.get("_cars"), COL_CAR)


def _draw_2d():
    context = bpy.context
    scene = context.scene
    if not getattr(scene, "rka_preview_flow", False):
        return
    if not getattr(scene, "rka_preview_labels", False):
        return
    doc = _cache["doc"]
    if doc is None:
        return
    region, rv3d = context.region, context.region_data
    if rv3d is None:
        return
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    blf.size(0, 11)
    for g in _cache["lanes"].values():
        p, _t = g.at(g.length * 0.5)
        co = location_3d_to_region_2d(region, rv3d, p)
        if co is None:
            continue
        blf.position(0, co.x + 5, co.y + 5, 0)
        blf.color(0, g.colour[0], g.colour[1], g.colour[2], 0.95)
        blf.draw(0, g.id)


# ------------------------------------------------------------------------------- the tick

_timer_on = [False]
FPS = 30.0


def _tick():
    scene = getattr(bpy.context, "scene", None)
    if scene is None or not getattr(scene, "rka_preview_flow", False) \
            or not getattr(scene, "rka_preview_cars", False):
        _timer_on[0] = False
        return None
    if not _cache["lanes"]:
        document(scene)
    if not _cars:
        _reseed(scene)
    step((1.0 / FPS) * max(0.0, float(getattr(scene, "rka_preview_speed", 1.0))))
    _redraw()
    return 1.0 / FPS


def _redraw():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for w in wm.windows:
        screen = getattr(w, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def arm_timer():
    if _timer_on[0]:
        return
    try:
        bpy.app.timers.register(_tick, first_interval=1.0 / FPS)
        _timer_on[0] = True
    except Exception:                            # noqa: BLE001 -- headless has no timers to lose
        _timer_on[0] = False


def _on_toggle(self, _context):
    """Property update: INVALIDATE and arm, never export.

    `document()` runs `read_network`, which runs `view_layer.update()`, and a property update
    callback is not a place to issue a depsgraph update from -- it re-enters. The first draw or
    the first timer tick after this builds the document instead, off the cache miss."""
    invalidate()
    if getattr(self, "rka_preview_flow", False) and getattr(self, "rka_preview_cars", False):
        arm_timer()


# ------------------------------------------------------------------------------- operators

class RKA_OT_preview_refresh(bpy.types.Operator):
    """Re-export the lane graph and re-seed the preview traffic"""
    bl_idname = "rka.preview_refresh"
    bl_label = "Refresh Flow"
    bl_options = {'REGISTER'}

    def execute(self, context):
        invalidate()
        doc = document(context.scene, force=True)
        if doc is None:
            self.report({'ERROR'}, "the lane graph could not be exported -- run Validate")
            return {'CANCELLED'}
        rep = _cache["report"]
        _reseed(context.scene)
        arm_timer()
        self.report({'INFO'}, "%d lane(s), %d junction(s), %d broken link(s), %d unreached"
                    % (rep["lanes"], rep["junctions"], len(rep["broken"]),
                       len(rep["unreached"])))
        return {'FINISHED'}


class RKA_OT_preview_report(bpy.types.Operator):
    """List every lane traffic cannot reach, and every chain that does not close"""
    bl_idname = "rka.preview_report"
    bl_label = "Flow Report"
    bl_options = {'REGISTER'}

    def execute(self, context):
        doc = document(context.scene, force=True)
        if doc is None:
            self.report({'ERROR'}, "the lane graph could not be exported -- run Validate")
            return {'CANCELLED'}
        rep = _cache["report"]
        print("\n=== road kit -- traffic flow ===")
        print("%d lanes, %d junctions, %d spawnable"
              % (rep["lanes"], rep["junctions"], rep["spawnable"]))
        for lane, near in rep["broken"]:
            print("  BROKEN     %s ends on the head of %s but has no successor"
                  % (lane, ", ".join(near)))
        for lane, nxt, d in rep["misjoined"]:
            print("  MISJOINED  %s -> %s, whose head is %.1f m from this lane's tail"
                  % (lane, nxt, d))
        for lane in rep["ramp_orphans"]:
            print("  RAMP       %s is on a ramp and nothing leads to it -- no car will ever "
                  "use it" % lane)
        for lane in rep["unreached"]:
            if lane not in rep["ramp_orphans"]:
                print("  UNREACHED  %s has no predecessor" % lane)
        for lane, _n in rep["open_end"]:
            print("  open end   %s (runs off the edge of the network)" % lane)
        # NOT a reachability defect -- the one geometry line in a reachability report, because the
        # graph can be perfect while the curve it is drawn on is not the road.
        for lane, dev in rep.get("path_off_road") or ():
            print("  PATH       %s: the exported Path3D is %.2f m off the lane it represents"
                  % (lane, dev))
        # The severities are ordered so the ERROR line is the last thing in the status bar.
        for lane in rep["ramp_orphans"][:3]:
            self.report({'WARNING'}, "%s: a ramp lane nothing leads to -- check the AUX link and "
                                     "the mainline's aux slot" % lane)
        for lane, near in rep["broken"][:3]:
            self.report({'WARNING'}, "%s ends on %s with no successor" % (lane, near[0]))
        for lane, nxt, d in rep["misjoined"][:3]:
            self.report({'WARNING'}, "%s -> %s: the successor's head is %.0f m away" % (
                lane, nxt, d))
        for lane, dev in (rep.get("path_off_road") or ())[:3]:
            self.report({'ERROR' if dev > pe.PATH_DEVIATION_ERROR else 'WARNING'},
                        "%s: the exported Path3D is %.2f m off the lane" % (lane, dev))
        self.report({'INFO'}, "%d lane(s): %d broken, %d misjoined, %d unreached, %d open end -- "
                              "full list in the console"
                    % (rep["lanes"], len(rep["broken"]), len(rep["misjoined"]),
                       len(rep["unreached"]), len(rep["open_end"])))
        return {'FINISHED'}


CLASSES = (RKA_OT_preview_refresh, RKA_OT_preview_report)

_handle_3d = None
_handle_2d = None


def register():
    global _handle_3d, _handle_2d
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.rka_preview_flow = bpy.props.BoolProperty(
        name="Traffic Flow", default=False, update=_on_toggle,
        description="Draw the EXPORTED lane graph -- directed lanes, successor links, and every "
                    "lane traffic cannot reach")
    bpy.types.Scene.rka_preview_cars = bpy.props.BoolProperty(
        name="Cars", default=False, update=_on_toggle,
        description="Run agents along the exported graph, choosing successors by the exported "
                    "weights. A ramp no car enters is a missing edge")
    bpy.types.Scene.rka_preview_geometry = bpy.props.EnumProperty(
        name="Geometry", default=GEO_PATH, update=_on_toggle,
        items=[(GEO_PATH, "Path3D (export)", "The bezier Godot will build each Curve3D from -- "
                                             "what the cars actually drive", 0),
               (GEO_POLY, "Lane polyline", "The sampled lane centreline -- where the asphalt is",
                1),
               (GEO_BOTH, "Both", "Lane polyline in its own colour, the exported Path3D in white "
                                  "over it, and the gap between them in orange", 2)],
        description="Which geometry the flow preview draws. These are NOT the same object")
    bpy.types.Scene.rka_preview_labels = bpy.props.BoolProperty(
        name="Lane Ids", default=False,
        description="Label each lane with the id Godot will see")
    bpy.types.Scene.rka_preview_density = bpy.props.IntProperty(
        name="Cars / km", default=8, min=0, soft_max=40)
    bpy.types.Scene.rka_preview_speed = bpy.props.FloatProperty(
        name="Playback", default=1.0, min=0.0, soft_max=8.0,
        description="Time multiplier for the preview traffic only")
    if _handle_3d is None:
        _handle_3d = bpy.types.SpaceView3D.draw_handler_add(_draw_3d, (), 'WINDOW', 'POST_VIEW')
    if _handle_2d is None:
        _handle_2d = bpy.types.SpaceView3D.draw_handler_add(_draw_2d, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _handle_3d, _handle_2d
    if _handle_3d is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle_3d, 'WINDOW')
        _handle_3d = None
    if _handle_2d is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle_2d, 'WINDOW')
        _handle_2d = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _timer_on[0] = False
    del _cars[:]
    _cache.update(stamp=-2, doc=None, lanes={}, alt={}, dev={}, report=None)
    for n in ("rka_preview_flow", "rka_preview_cars", "rka_preview_labels",
              "rka_preview_density", "rka_preview_speed", "rka_preview_geometry"):
        if hasattr(bpy.types.Scene, n):
            delattr(bpy.types.Scene, n)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

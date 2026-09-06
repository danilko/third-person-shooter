"""Road point/port graph -- the authored data model.

The one rule that shapes this file: **the authored record is a git-diffable `.roads.json`, and the
Blender Empties are a VIEW of it** (ROAD_POINT_GRAPH.md 1.2c). Both previous road models stored the
authored state exclusively in `.blend` IDProperties, so when the model changed there was nothing to
migrate -- only a binary file to regenerate or abandon. So the schema lives here as plain Python
(`PointData` / `RoadData` / `NetworkData`), the JSON is its serialisation, and the bpy
PropertyGroups at the bottom are a projection that is read on load and written on build.

That split is also why this module imports bpy defensively: everything above the `bpy` guard runs
under plain `python3`, which is what lets `point_profile` and `point_validate` -- the profile
builder and the gate -- be tested with no Blender at all.
"""

import json
import math
import os
import uuid

# ------------------------------------------------------------------------------- vocabulary

SCHEMA_VER = 1

# Roles. A point's role says what KIND of connection it may take part in; the gate enforces it.
SEGMENT = 'SEGMENT'
INTERSECTION = 'INTERSECTION'
#: ONE ramp role. `RAMP_ENTRY`/`RAMP_EXIT` are kept so every `.roads.json` and `.blend` written
#: before this still loads, but nothing derives behaviour from the distinction any more: WHICH WAY
#: traffic runs through an AUX link is a fact about the ramp's own lane graph -- the mouth is where
#: its lanes START on an exit and where they END on an entrance -- so `ramp_is_entrance` reads it
#: off the lanes instead of asking the artist to declare it twice and keep the two agreeing.
RAMP = 'RAMP'
RAMP_ENTRY = 'RAMP_ENTRY'
RAMP_EXIT = 'RAMP_EXIT'
TERMINUS = 'TERMINUS'
#: APPEND ONLY. Blender stores an `EnumProperty` in the .blend by its ORDINAL, not its identifier,
#: so inserting a value into the middle of one of these tuples silently re-reads every saved file:
#: adding `RAMP` between INTERSECTION and RAMP_ENTRY turned every stored `RAMP_EXIT` into a
#: `RAMP_ENTRY` on load, with nothing to see in any diff. `_enum_items` pins the numbers explicitly
#: so the mapping is at least visible in the source, but the ordering rule is what protects it.
ROLES = (SEGMENT, INTERSECTION, RAMP_ENTRY, RAMP_EXIT, TERMINUS, RAMP)
RAMP_ROLES = (RAMP, RAMP_ENTRY, RAMP_EXIT)


def is_ramp_role(role):
    """True for the canonical `RAMP` and for both legacy directional spellings."""
    return role in RAMP_ROLES

# Link types.
LINK_SEGMENT = 'SEGMENT'
LINK_JUNCTION = 'JUNCTION'
LINK_AUX = 'AUX'
LINK_TYPES = (LINK_SEGMENT, LINK_JUNCTION, LINK_AUX)

# Which lane a count DECREASE removes. An integer `lanes_fwd` cannot say which lane dies, and the
# answer is not derivable -- a lane may drop into the kerb (the ordinary case) or be swallowed by
# the median (an offside exit). Verified against `lane_profile`: both resolve with no lateral spine
# shift, because ANCHOR_DIVIDE holds s = 0 on the divide either way.
KERB = 'KERB'
MEDIAN = 'MEDIAN'
DROP_SIDES = (KERB, MEDIAN)

INHERIT = 'INHERIT'
OVERRIDE = 'OVERRIDE'
PROFILE_MODES = (INHERIT, OVERRIDE)

AUTO = 'AUTO'
SHARP = 'SHARP'
MANUAL = 'MANUAL'
TANGENT_MODES = (AUTO, SHARP, MANUAL)

#: What the divide between the two carriageways IS. `median_width` says how wide; this says what.
#:
#: APPEND-ONLY, like every enum in this module -- a Blender `EnumProperty` is stored by ORDINAL,
#: so inserting a value in the middle silently re-reads every saved road in every `.blend` as its
#: neighbour, with nothing to see in any diff (8i.6).
MED_NONE = 'NONE'
MED_PAINT = 'PAINT_DOUBLE_Y'
MED_RAISED = 'RAISED'
MED_WALL = 'WALL'
MEDIAN_STYLES = (MED_NONE, MED_PAINT, MED_RAISED, MED_WALL)

#: The four fields a station may change while still INHERITing the road's base profile -- "what
#: actually varies along a road" (1.2a). Everything else is whole-profile INHERIT or OVERRIDE,
#: deliberately one bit rather than a 30-field mask nobody can hold in their head.
DELTA_FIELDS = ("lanes_fwd", "lanes_bwd", "aux_fwd", "aux_bwd")


# ------------------------------------------------------------------------------- the field table
#
# ONE declaration of every authored field: (name, kind, default). It drives the defaults, the JSON
# round-trip and the bpy PropertyGroup cross-check, so those three cannot drift apart -- the same
# discipline 3.1 applies to the GN attribute registry, one level up. `kind` is 'i' int, 'f' float,
# 'b' bool, 's' string, or a tuple of legal enum values.

POINT_FIELDS = (
    # identity ------------------------------------------------------------------------------
    ("uid",            's',  ""),
    ("role",           ROLES, SEGMENT),
    ("schema_ver",     'i',  SCHEMA_VER),
    ("profile_mode",   PROFILE_MODES, INHERIT),

    # cross-section, measured OUTWARD from this point; the point's position is the divide -------
    ("lanes_fwd",      'i',  2),
    ("lanes_bwd",      'i',  2),
    # 4.5 m, not the 3.5 m book value: this world drives arcade-wide on purpose -- see
    # `seed_district_roads.LANE_WIDTH` for the measurement (a 2.4 m car in a 3.25 m lane).
    # A hand-authored road should come out the same width as a seeded one.
    ("lane_width",     'f',  4.5),
    ("drop_side_fwd",  DROP_SIDES, KERB),
    ("drop_side_bwd",  DROP_SIDES, KERB),
    ("aux_fwd",        'i',  0),
    ("aux_bwd",        'i',  0),
    ("aux_side",       DROP_SIDES, KERB),
    ("shoulder_left_width",  'f', 0.0),
    ("shoulder_right_width", 'f', 0.0),
    ("parking_left_width",   'f', 0.0),
    ("parking_right_width",  'f', 0.0),
    ("median_width",   'f',  0.0),
    ("left_kerb_height",  'f', 0.15),
    ("left_walk_width",   'f', 0.0),
    ("right_kerb_height", 'f', 0.15),
    ("right_walk_width",  'f', 0.0),
    ("design_speed",   'f',  50.0),

    # structure. `ground_z` is SAMPLED by Build, never authored by a button (3.3 rule 1) --------
    ("deck_thickness", 'f',  0.8),
    ("pillar_spacing", 'f',  30.0),
    ("pillar_skip",    'b',  False),
    ("pillar_offset",  'f',  0.0),
    ("ground_z",       'f',  0.0),
    ("has_ground_z",   'b',  False),

    # shape. The Empty's transform is the road frame; only the handle lengths need storing ------
    ("tangent_mode",   TANGENT_MODES, AUTO),
    ("handle_in",      'f',  0.0),
    ("handle_out",     'f',  0.0),
    ("roll",           'f',  0.0),

    # junction only ---------------------------------------------------------------------------
    ("fillet_radius",  'f',  6.0),
    ("allow_cross",    'b',  True),
    ("allow_uturn",    'b',  False),
    ("traffic_light",  'b',  False),
    ("setback_solved", 'f',  0.0),
    # An EXPLICIT toggle with an overlay glyph -- never inferred from "the artist dragged it".
    # An accidental nudge would otherwise lock a mouth forever and every future Auto Setback would
    # silently skip it: exactly the invisible state this rewrite exists to kill (2.2).
    ("setback_locked", 'b',  False),
)

ROAD_FIELDS = (
    ("name",         's', ""),
    ("road_class",   's', "street"),
    ("zone_id",      's', ""),
    ("is_loop",      'b', False),
    ("ped_access",   'b', True),
    #: How tall a barrier stands along this road's open edges, in metres. 0 = never build one.
    #: WHERE it is built is derived (`point_solve.solve_road`): everywhere on a road with no
    #: pedestrian access, and on the elevated stretches of one that has. That split is the same
    #: "you author the value, Build decides where" rule the supports already follow.
    ("barrier_height", 'f', 1.0),
    #: Multiplies the merge-taper length the gate demands. THE WORLD IS NOT 1:1 -- this map
    #: compresses Tokyo into ~6 km, so a taper computed from real design speeds eats a whole
    #: district. Dialling it down is a legitimate, and now VISIBLE, authoring decision; the
    #: default is 1.0 so the real standard is what you get unless you say otherwise.
    ("taper_factor", 'f', 1.0),

    # ---- STYLE: what the road is MADE OF ---------------------------------------------------------
    #
    # PER ROAD, NOT PER POINT, deliberately. A cross-section change along a road is "two stations
    # that differ" and that is the whole model; what the road is made of is not -- a stretch of the
    # same road paved differently is a different road, and a per-point material would multiply the
    # layer stack by the station count for a distinction nobody authors.
    #
    # Each slot holds a DATABLOCK NAME, blank meaning "the layer's default". Names and not
    # pointers because `<stem>.roads.json` is the source of truth and must round-trip (rule 3) --
    # the same reason every other authored reference here is a name. `point_style` is the one
    # place they are resolved.
    #
    # `median_style` LIVES HERE and not on the point, though it was declared there (unused) from
    # the start: it decides which geometry the divide builds, and the divide's material is one
    # material for the whole run. `median_width` still varies per station, so an island that
    # widens, narrows and vanishes is authored exactly as before.
    ("median_style", MEDIAN_STYLES, MED_RAISED),
    ("surface_mat",  's', ""),
    ("median_mat",   's', ""),
    ("deck_mat",     's', ""),
    ("kerb_mat",     's', ""),
    ("footway_mat",  's', ""),
    ("barrier_mat",  's', ""),
    ("mark_w_mat",   's', ""),
    ("mark_y_mat",   's', ""),
    # A PROFILE ASSET replaces the parametric band for its layer: the artist models the section
    # once -- a real kerb with a chamfer, a jersey barrier, a gutter -- and the road sweeps it,
    # continuously, so it survives any curvature. (A TILED asset, which is what the previous model
    # used, cannot: rigid pieces round a 9 m corner sit 12.7 deg apart and open a real 7.8 cm gap
    # at every joint. Tiling is for lamp posts and signs, and is `GN_PointAssets`' job.)
    ("surface_asset", 's', ""),
    ("median_asset",  's', ""),
    ("kerb_asset",    's', ""),
    ("footway_asset", 's', ""),
    ("barrier_asset", 's', ""),
    #: Paint the lane markings at all. Off for a road whose surface is a placeholder, and off for
    #: every LOD-low bake -- stripes at 300 m are a triangle budget with nothing to show for it.
    ("markings", 'b', True),
)

_POINT_DEFAULTS = {n: d for n, _k, d in POINT_FIELDS}
_POINT_KINDS = {n: k for n, k, _d in POINT_FIELDS}
_ROAD_DEFAULTS = {n: d for n, _k, d in ROAD_FIELDS}


def _coerce(kind, value, default):
    if isinstance(kind, tuple):
        return value if value in kind else default
    try:
        if kind == 'i':
            return int(value)
        if kind == 'f':
            return float(value)
        if kind == 'b':
            return bool(value)
        return "" if value is None else str(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------------------- the data classes

class Link(object):
    """An AUTHORED connection. Connectivity is data, never inferred from distance -- inference is
    what `ramp_candidates` did (277 lines guessing which carriageway serves a ramp) and it is
    redesign defect 10."""

    __slots__ = ("target", "type")

    def __init__(self, target, type=LINK_SEGMENT):
        self.target = target            # the TARGET POINT'S UID, not an object
        self.type = type if type in LINK_TYPES else LINK_SEGMENT

    def to_dict(self):
        return {"target": self.target, "type": self.type}

    @staticmethod
    def from_dict(d):
        return Link(d.get("target", ""), d.get("type", LINK_SEGMENT))

    def __repr__(self):
        return "Link(%s -> %s)" % (self.type, self.target)


class PointData(object):
    """One road point: a station (cross-section) AND a port (its typed links), which is the whole
    idea. Attributes are exactly `POINT_FIELDS` plus `pos` and `links`."""

    def __init__(self, uid="", pos=(0.0, 0.0, 0.0), tangent=None, **kw):
        for n, k, d in POINT_FIELDS:
            setattr(self, n, _coerce(k, kw.get(n, d), d))
        if uid:
            self.uid = uid
        self.pos = (float(pos[0]), float(pos[1]), float(pos[2]) if len(pos) > 2 else 0.0)
        #: The authored travel direction in world space -- the Empty's own local +Y axis, read off
        #: its rotation. It is a TRANSFORM channel, not a property, which is why it lives beside
        #: `pos` and not in POINT_FIELDS: the artist authors it by ROTATING the point. Carried only
        #: when `tangent_mode == MANUAL`, so a road nobody has shaped by hand keeps a clean diff.
        self.tangent = None if tangent is None else (
            float(tangent[0]), float(tangent[1]),
            float(tangent[2]) if len(tangent) > 2 else 0.0)
        self.links = list(kw.get("links", ()))

    # -- links ---------------------------------------------------------------------------------
    def link_to(self, uid, type=LINK_SEGMENT):
        for l in self.links:
            if l.target == uid:
                l.type = type
                return l
        l = Link(uid, type)
        self.links.append(l)
        return l

    def unlink(self, uid):
        n = len(self.links)
        self.links = [l for l in self.links if l.target != uid]
        return n - len(self.links)

    def targets(self, type=None):
        return [l.target for l in self.links if type is None or l.type == type]

    def has_link(self, uid, type=None):
        return any(l.target == uid and (type is None or l.type == type) for l in self.links)

    # -- serialisation -------------------------------------------------------------------------
    def to_dict(self):
        d = {"pos": [round(c, 6) for c in self.pos],
             "links": [l.to_dict() for l in self.links]}
        if self.tangent is not None:
            d["tangent"] = [round(c, 6) for c in self.tangent]
        for n, _k, dv in POINT_FIELDS:
            v = getattr(self, n)
            if v != dv or n == "uid":
                d[n] = round(v, 6) if isinstance(v, float) else v
        return d

    @staticmethod
    def from_dict(d):
        kw = {n: d[n] for n, _k, _dv in POINT_FIELDS if n in d}
        kw["links"] = [Link.from_dict(x) for x in d.get("links", ())]
        return PointData(pos=d.get("pos", (0.0, 0.0, 0.0)), tangent=d.get("tangent"), **kw)

    def copy(self):
        return PointData.from_dict(self.to_dict())

    def __repr__(self):
        return "PointData(%s, %s, F%d/R%d)" % (self.uid, self.role, self.lanes_fwd, self.lanes_bwd)


class RoadData(object):
    """An ORDERED corridor: `points` is the chain, FWD is increasing index (1.3). That one
    convention removes the per-edge left/right ambiguity the mesh-graph model has, where flipping
    an edge swapped sidewalk_left and sidewalk_right.

    A road is divided only by something real -- a junction, or an authored break. NEVER by a lane
    count changing: that is redesign defect 3, which built a 3278 m ring with zero crossings as 12
    separate pieces. An INTERSECTION point is an ordinary interior member of its chain that
    additionally carries JUNCTION links to other roads' points."""

    def __init__(self, name, base=None, points=(), **kw):
        for n, k, d in ROAD_FIELDS:
            setattr(self, n, _coerce(k, kw.get(n, d), d))
        self.name = name
        #: The road's base cross-section. A station in INHERIT mode takes this and applies only
        #: its own DELTA_FIELDS -- so changing the lane width of a 20-station road is one edit.
        self.base = base if base is not None else PointData(uid="")
        self.points = list(points)          # uids, in order

    def to_dict(self):
        d = {"points": list(self.points), "base": self.base.to_dict()}
        for n, _k, dv in ROAD_FIELDS:
            v = getattr(self, n)
            if v != dv or n == "name":
                d[n] = v
        return d

    @staticmethod
    def from_dict(d):
        kw = {n: d[n] for n, _k, _dv in ROAD_FIELDS if n in d}
        kw.pop("name", None)
        return RoadData(d.get("name", ""), PointData.from_dict(d.get("base", {})),
                        d.get("points", ()), **kw)

    def __repr__(self):
        return "RoadData(%s, %d pts%s)" % (self.name, len(self.points),
                                           ", loop" if self.is_loop else "")


def resolve_point(point, road):
    """The station's EFFECTIVE cross-section: INHERIT takes the road's base and applies only the
    four genuine deltas; OVERRIDE is the point's own values verbatim (1.2a).

    Returns a NEW PointData -- callers must never write through it back onto the authored point."""
    if road is None or point.profile_mode == OVERRIDE:
        return point.copy()
    out = road.base.copy()
    out.uid = point.uid
    out.role = point.role
    out.pos = point.pos
    out.tangent = point.tangent
    out.links = list(point.links)
    out.profile_mode = point.profile_mode
    for n in DELTA_FIELDS:
        setattr(out, n, getattr(point, n))
    # Shape, structure sampling and junction state are per-station facts, not profile: a base
    # profile has no business overwriting where a corner is or what the terrain height was.
    for n in ("tangent_mode", "handle_in", "handle_out", "roll",
              "ground_z", "has_ground_z", "pillar_skip", "pillar_offset",
              "fillet_radius", "allow_cross", "allow_uturn", "traffic_light",
              "setback_solved", "setback_locked"):
        setattr(out, n, getattr(point, n))
    return out


# ------------------------------------------------------------- which way does a station face

def station_axis(net, uid):
    """The station's TRAVEL DIRECTION (chain-FWD) as a unit XY pair, or None.

    ONE OWNER, and that is the point of the function. The Empty's rotation is the road frame
    (rule 6), so a hand-rotated point must turn everything that is a fact about direction at that
    station -- the swept carriageway, the pad arm's cap, the mouth bearing, the ramp's gore. It
    used to turn only the carriageway, because the carrier asked `road_points` (which honours
    `tangent`) while `point_solve.mouth_axis` and `point_validate._axis` each re-derived the
    direction from the NEIGHBOUR'S POSITION and so could not see the rotation at all. Rotating an
    intersection mouth therefore bent its street and left its pad exactly where it was -- the
    user-reported "only the road changes, the intersection does not align to the new normal".

    MANUAL wins; otherwise the central-difference chord, which is what AUTO would align the arrow
    to anyway. Z is dropped: every consumer here is a plan-view frame."""
    p = net.points.get(uid)
    if p is None:
        return None
    if p.tangent_mode == MANUAL and p.tangent is not None:
        m = math.hypot(p.tangent[0], p.tangent[1])
        if m > 1e-9:
            return (p.tangent[0] / m, p.tangent[1] / m)
    road = net.road_of(uid)
    if road is None:
        return None
    chain = [u for u in road.points if u in net.points]
    try:
        i = chain.index(uid)
    except ValueError:
        return None
    a = net.points[chain[i - 1]].pos if i > 0 else p.pos
    b = net.points[chain[i + 1]].pos if i + 1 < len(chain) else p.pos
    dx, dy = b[0] - a[0], b[1] - a[1]
    m = math.hypot(dx, dy)
    return None if m < 1e-9 else (dx / m, dy / m)


def station_normal(net, uid):
    """The +s lateral direction at a station -- the left normal of `station_axis`."""
    ax = station_axis(net, uid)
    return None if ax is None else (-ax[1], ax[0])


# ---------------------------------------------- which way does a ramp run through its AUX link

def road_runs(net, road):
    """`[[uid, ...], ...]` -- the road's chain split at every gap in its SEGMENT links.

    THE ONE STRUCTURAL RULE, and the single most visible way to get this model wrong. Two
    chain-adjacent INTERSECTION points are joined by the PAD, not by carriageway. Sweeping a lane
    (or a kerb, or a deck) straight through that gap drives the road across the middle of the
    intersection. So a run ends wherever two chain-adjacent points lack a SEGMENT link, and the
    pad bridges it. The same split separates two unrelated stretches that happen to share a
    collection -- a ramp grown with `Extend Road` off its mainline, the usual way that happens.

    A run of ONE point is kept: it carries no length and builds no ribbon, but it is still a
    junction arm and the connectors either side of it are what carry traffic through.
    `point_solve.solve_road` drops it; `point_export`'s arm naming needs it.

    It lives HERE, not in `point_solve`, because it is a fact about the chain and its links and
    nothing else -- and because the ramp-direction rules below need it, which a solve-layer owner
    could not give them without a circular import. `point_solve.road_runs` is an alias."""
    if isinstance(road, str):
        road = net.roads[road]
    uids = [u for u in road.points if u in net.points]
    if not uids:
        return []
    runs, cur = [], [uids[0]]
    for a, b in zip(uids, uids[1:]):
        if net.points[a].has_link(b, LINK_SEGMENT):
            cur.append(b)
        else:
            runs.append(cur)
            cur = [b]
    runs.append(cur)
    return [r for r in runs if len(r) >= 1]


def run_of(net, uid):
    """The run `uid` belongs to -- NOT the whole collection's chain.

    A ramp authored inside its mainline's road collection sits at the head of its own RUN and in
    the middle of the collection's names, and every ramp rule below is about the run."""
    road = net.road_of(uid)
    if road is None:
        return [uid]
    for run in road_runs(net, road):
        if uid in run:
            return run
    return [uid]


def ramp_mouth_at_chain_start(net, uid):
    """True when this point is at the START of its RUN, so a walk away from it runs with
    increasing index. Used by every consumer that has to walk a ramp away from its mouth."""
    run = run_of(net, uid)
    if uid not in run or len(run) < 2:
        return True
    return run.index(uid) <= (len(run) - 1) / 2.0


def ramp_is_entrance(net, uid):
    """Does traffic run ramp -> mainline (an entrance) or mainline -> ramp (an exit)?

    DERIVED, not authored -- this is why there is ONE ramp role and not two. Two facts the model
    already holds decide it, and neither of them can be got wrong by hand:

    * WHERE the mouth sits in the ramp's own run: its head, or its tail;
    * WHICH WAY the ramp's lanes run: FWD is increasing index, REV is decreasing.

    Traffic LEAVES the mouth -- an exit -- when the mouth is the run's head and the ramp declares
    forward lanes, or when it is the run's tail and the ramp declares reverse ones. Anything else
    is traffic ARRIVING at the mouth, which is an entrance. Both readings are needed: a ramp drawn
    the other way round (`lanes_bwd = 1`, `lanes_fwd = 0`) is a perfectly ordinary thing to author
    and its head is where cars come OUT.

    Asking the artist to declare this as well gives two facts that can disagree -- and when they
    did, the export wired the lane graph backwards while the geometry was right, which reads in
    game only as "no car ever uses that ramp".

    The legacy `RAMP_ENTRY`/`RAMP_EXIT` roles are the tiebreak for the one case that has no
    answer: a ramp run of a single point, which has neither head nor tail."""
    run = run_of(net, uid)
    p = net.points.get(uid)
    if p is None:
        return False
    if len(run) < 2 or uid not in run:
        return p.role == RAMP_ENTRY
    res = resolve_point(p, net.road_of(uid))
    at_head = ramp_mouth_at_chain_start(net, uid)
    leaves = (int(res.lanes_fwd) + int(res.aux_fwd) > 0 if at_head
              else int(res.lanes_bwd) + int(res.aux_bwd) > 0)
    return not leaves


# ------------------------------------------------------------------------------- the network

def new_uid():
    """Short, readable, collision-free. Readable matters: every gate failure names an object, and
    `p_3f9a21c8` is findable in the outliner where a full GUID is not."""
    return "p_" + uuid.uuid4().hex[:8]


class NetworkData(object):
    """Every road and every point in one scene, keyed by uid. This IS the `.roads.json`."""

    def __init__(self):
        self.points = {}        # uid -> PointData
        self.roads = {}         # name -> RoadData
        self.schema_ver = SCHEMA_VER
        #: `{uid: "<road>/<object>"}` for the read this network came from, so a finding can name
        #: an object (8i.10). Filled by `read_network`; empty for a network built in plain Python.
        self.labels = {}
        #: `[(old_uid, new_uid, point)]` -- see `dedupe_uids`.
        self.uid_repairs = []

    # -- building ------------------------------------------------------------------------------
    def add_road(self, road):
        self.roads[road.name] = road
        return road

    def add_point(self, point, road=None):
        if not point.uid:
            point.uid = new_uid()
        self.points[point.uid] = point
        if road is not None:
            if isinstance(road, str):
                road = self.roads[road]
            if point.uid not in road.points:
                road.points.append(point.uid)
        return point

    def add_station(self, road, pos, **kw):
        """A point that genuinely INHERITS its road's base profile.

        The four `DELTA_FIELDS` are seeded from the base unless the caller names them, because a
        bare `PointData()` carries the SCHEMA defaults (2 lanes each way) and those would silently
        override a base that says otherwise. A station whose whole claim is "I am the same as my
        road" must not be the one place the lane count changes -- and every headless authoring
        path (the island generator, a test) builds points this way."""
        if isinstance(road, str):
            road = self.roads[road]
        for n in DELTA_FIELDS:
            kw.setdefault(n, getattr(road.base, n))
        kw.setdefault("lane_width", road.base.lane_width)
        return self.add_point(PointData(pos=pos, **kw), road)

    def road_of(self, uid):
        for r in self.roads.values():
            if uid in r.points:
                return r
        return None

    def resolved(self, uid):
        p = self.points.get(uid)
        return None if p is None else resolve_point(p, self.road_of(uid))

    # -- links ---------------------------------------------------------------------------------
    def link(self, a_uid, b_uid, type=LINK_SEGMENT, symmetric=None):
        """SEGMENT and JUNCTION links are symmetric; AUX is directed (mainline -> ramp), because
        the aux slot belongs to the mainline station and the ramp point connects to nothing else."""
        if symmetric is None:
            symmetric = (type != LINK_AUX)
        self.points[a_uid].link_to(b_uid, type)
        if symmetric:
            self.points[b_uid].link_to(a_uid, type)

    def unlink(self, a_uid, b_uid):
        n = 0
        if a_uid in self.points:
            n += self.points[a_uid].unlink(b_uid)
        if b_uid in self.points:
            n += self.points[b_uid].unlink(a_uid)
        return n

    def remove_point(self, uid):
        """Deleting a point needs its own path: INBOUND links are stripped FIRST. A PointerProperty
        is a real user-counted ID reference, so a point that is merely unlinked from its collection
        lives on as a zero-collection zombie held by its referrers -- invisible in the outliner and
        surviving Purge Orphans (1.2b)."""
        for p in self.points.values():
            p.unlink(uid)
        for r in self.roads.values():
            if uid in r.points:
                r.points.remove(uid)
        return self.points.pop(uid, None)

    # -- walks ---------------------------------------------------------------------------------
    def chain(self, road):
        """The ordered PointData list. A loop road is NOT closed here -- `is_loop` is carried
        alongside, because every arclength measure and profile interpolation needs the wrap as an
        explicit case rather than a duplicated last point."""
        if isinstance(road, str):
            road = self.roads[road]
        return [self.points[u] for u in road.points if u in self.points]

    def junction_cliques(self):
        """Connected components over JUNCTION links, each one pad. Returns `[sorted_uid_list]`.

        Components, not cliques, deliberately: a MISSING mutual link must reach the gate as a
        reportable defect rather than silently splitting one pad into two that then overlap."""
        seen, out = set(), []
        for uid in sorted(self.points):
            if uid in seen or not self.points[uid].targets(LINK_JUNCTION):
                continue
            stack, comp = [uid], []
            seen.add(uid)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for t in self.points[cur].targets(LINK_JUNCTION):
                    if t in self.points and t not in seen:
                        seen.add(t)
                        stack.append(t)
            out.append(sorted(comp))
        return out

    def aux_pairs(self):
        """`[(mainline_uid, ramp_uid)]` -- authored, never guessed from distance."""
        return [(u, t) for u in sorted(self.points)
                for t in self.points[u].targets(LINK_AUX)]


def dedupe_uids(entries):
    """`entries` = `[(order_key, PointData)]`, oldest first. Returns `[(old_uid, new_uid, point)]`.

    Blender has no `duplicate_post` handler and `Object.copy()` deep-copies IDProperties verbatim.
    Duplicating a WHOLE road is fine -- Blender's ID-remap pass rewrites pointers within the
    duplicated set. Duplicating ONE point gives a clone carrying the SAME uid whose links point at
    the ORIGINAL's neighbours, while those neighbours still point at the original: a half-connected
    graph with a uid collision that nothing reports (1.2b).

    So the rule is fixed here rather than left to the reader: the NEWER object loses. It is
    reallocated a fresh uid and its links are dropped here, because a uid-resolved link describes
    the ORIGINAL's connectivity and adopting it would silently double every one of the original's
    edges.

    DROPPED HERE, AND PUT BACK BY `read_network` WHERE THE OBJECTS SAY SO (8j). "Duplicating a
    whole road is fine" was true of the pointers and false of everything read off them: a copied
    collection's link rows DO point at the copies, but its uids do not, so this pass re-allocated
    every one of them and cleared every internal link -- the whole duplicated road arrived
    disconnected. `read_network` re-resolves links through OBJECT identity afterwards and keeps
    the ones that stay inside the re-allocated set, which is exactly the difference between
    Shift+D on one Empty (its links point at objects that kept their uids: inherited, drop them)
    and duplicating a road (every internal target was re-allocated too: internal, keep them)."""
    seen, fixed = set(), []
    for _key, p in sorted(entries, key=lambda e: e[0]):
        if not p.uid or p.uid in seen:
            old = p.uid
            p.uid = new_uid()
            p.links = []
            fixed.append((old, p.uid, p))
        seen.add(p.uid)
    return fixed


# ------------------------------------------------------------------------------- .roads.json

def network_to_dict(net):
    return {"schema_ver": net.schema_ver,
            "roads": [net.roads[n].to_dict() for n in sorted(net.roads)],
            "points": [net.points[u].to_dict() for u in sorted(net.points)]}


def network_from_dict(d):
    net = NetworkData()
    net.schema_ver = int(d.get("schema_ver", SCHEMA_VER))
    for pd in d.get("points", ()):
        p = PointData.from_dict(pd)
        net.points[p.uid] = p
    for rd in d.get("roads", ()):
        r = RoadData.from_dict(rd)
        net.roads[r.name] = r
    return net


def save_network(net, path):
    """Sorted keys and one road per line: this file is meant to be READ IN A DIFF. A lane count
    change should be one diff line, not an opaque `.blend` delta."""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(network_to_dict(net), fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def load_network(path):
    with open(path) as fh:
        return network_from_dict(json.load(fh))


# =========================================================================== the Blender projection
#
# Everything above this line runs under plain `python3`. Everything below is the VIEW: Empties that
# are read into a NetworkData and written back from one. Nothing below owns a fact.

try:
    import bpy
    from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                           FloatVectorProperty, IntProperty, PointerProperty, StringProperty)
    from bpy.types import Object, PropertyGroup
    from mathutils import Vector
except ImportError:          # plain python3 -- the self-tests, and the headless authoring path
    bpy = None
    PropertyGroup = object

ROAD_MANAGER = "ROAD_MANAGER"
ROAD_MANAGER_GEN = "ROAD_MANAGER_GEN"
JUNCTIONS = "JUNCTIONS"
#: Generated space only -- the paved wedge between a mainline and each ramp peeling off it.
GORES = "GORES"

_OVR = {'LIBRARY_OVERRIDABLE'}


def _enum_items(values):
    """The 5-tuple form, so the stored NUMBER is written down rather than implied by position.

    Blender persists an enum by its ordinal. With 3-tuples that ordinal is the list index and
    nothing in the source says so, which is how inserting one value into `ROLES` re-read every
    saved role in every .blend. The numbers are still positional -- they have to be, to match what
    is already on disk -- but they are now visible, and the tuples they come from are append-only."""
    return [(v, v.replace("_", " ").title(), "", 'NONE', i) for i, v in enumerate(values)]


if bpy is not None:

    class RKA_Link(PropertyGroup):
        # A real Object pointer, not a name: a name goes stale on rename, and rename is the one
        # thing a uid is required to survive.
        target: PointerProperty(type=Object, override=_OVR)
        type: EnumProperty(items=_enum_items(LINK_TYPES), default=LINK_SEGMENT, override=_OVR)

    _annotations = {
        "is_point": BoolProperty(default=False, override=_OVR),
        # `use_insertion` or a library-linked neighbour road (tools/link_neighbors.py seam editing)
        # is read-only AND invisible to the gate.
        "links": CollectionProperty(type=RKA_Link, override={'LIBRARY_OVERRIDABLE',
                                                             'USE_INSERTION'}),
    }
    for _n, _k, _d in POINT_FIELDS:
        if isinstance(_k, tuple):
            _annotations[_n] = EnumProperty(items=_enum_items(_k), default=_d, override=_OVR)
        elif _k == 'i':
            _annotations[_n] = IntProperty(default=_d, min=0, soft_max=8, override=_OVR)
        elif _k == 'f':
            _annotations[_n] = FloatProperty(default=_d, override=_OVR)
        elif _k == 'b':
            _annotations[_n] = BoolProperty(default=_d, override=_OVR)
        else:
            _annotations[_n] = StringProperty(default=_d, override=_OVR)

    # NOT a POINT_FIELD and deliberately NOT in the `.roads.json` record: this is the tool's own
    # bookkeeping -- the facing it last gave this point -- and the only thing that can tell a hand
    # rotation apart from an arrow that has not been re-faced since a drag. It is derived state, so
    # a diff must not carry it.
    _annotations["auto_tangent"] = FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0),
                                                       override=_OVR)

    def _on_tangent_mode(self, _context):
        """Going back to AUTO must not instantly re-promote to MANUAL.

        The facing is still the rotated one at that moment, so a stale baseline would read as "the
        artist rotated this" on the very next read. Re-stamping makes the current facing the new
        zero; `sync_facings` then straightens the arrow to the chain on the next build."""
        if self.tangent_mode != AUTO:
            return
        # `is` would be wrong here: `obj.rka_pt` builds a fresh RNA wrapper on every access, so
        # the identity test never held and the re-stamp never ran. The owner of a road point's
        # group is always an Object; the road's `base` profile hangs off a Collection, which has
        # no facing to stamp.
        obj = self.id_data
        if isinstance(obj, bpy.types.Object) and getattr(obj, "rka_pt", None) is not None:
            stamp_baseline(obj)

    _annotations["tangent_mode"] = EnumProperty(items=_enum_items(TANGENT_MODES), default=AUTO,
                                                override=_OVR, update=_on_tangent_mode)

    RKA_Point = type("RKA_Point", (PropertyGroup,), {"__annotations__": _annotations})

    # Road-level state, held on the road's COLLECTION. `base` is the base cross-section (1.2a)
    # and reuses RKA_Point, so there is exactly ONE cross-section schema in the file.
    _road_annotations = {
        "is_road": BoolProperty(default=False, override=_OVR),
        "base": PointerProperty(type=RKA_Point, override=_OVR),
    }
    for _n, _k, _d in ROAD_FIELDS:
        if isinstance(_k, tuple):
            _road_annotations[_n] = EnumProperty(items=_enum_items(_k), default=_d, override=_OVR)
        elif _k == 'b':
            _road_annotations[_n] = BoolProperty(default=_d, override=_OVR)
        elif _k == 'f':
            _road_annotations[_n] = FloatProperty(default=_d, min=0.0, override=_OVR)
        elif _k == 'i':
            _road_annotations[_n] = IntProperty(default=_d, min=0, override=_OVR)
        else:
            _road_annotations[_n] = StringProperty(default=_d, override=_OVR)

    RKA_Road = type("RKA_Road", (PropertyGroup,), {"__annotations__": _road_annotations})

    _CLASSES = (RKA_Link, RKA_Point, RKA_Road)


#: The Empty's local axis that means "travel direction" (1.2: the transform IS the road frame).
#: +Y, not +Z, because +Z is up and `left_normal` derives the lateral frame from a horizontal
#: tangent. Points are drawn with `empty_display_type = 'ARROWS'` precisely so this axis is
#: VISIBLE -- a single-arrow Empty draws along +Z, which would show the artist the wrong axis.
FORWARD_AXIS = 1


def facing_of(obj):
    """The Empty's local +Y in WORLD space -- its authored travel direction.

    Column, not row: `matrix_world.col[1]` is the image of the local +Y basis vector, which is
    what "the direction this point faces" means once the object is parented to a JCT_*."""
    v = obj.matrix_world.col[FORWARD_AXIS].to_3d()
    return None if v.length <= 1e-9 else tuple(v.normalized())


def face_matrix(obj, direction):
    """Rotate `obj` so its local +Y points along `direction`, keeping its world position."""
    d = Vector(direction)
    if d.length <= 1e-9:
        return
    loc = obj.matrix_world.translation.copy()
    obj.matrix_world = d.normalized().to_track_quat('Y', 'Z').to_matrix().to_4x4()
    obj.matrix_world.translation = loc


#: How far the Empty may drift from the facing the TOOL last gave it before that rotation counts
#: as AUTHORED. Well under any deliberate turn, well over the float noise of a matrix round-trip.
ROTATED_TOL_DEG = 0.5


def facing_local(obj):
    """The Empty's local +Y in its PARENT's frame -- the same direction `facing_of` returns, just
    expressed where the baseline lives. Identical to `facing_of` for an unparented point, since
    `matrix_local` IS `matrix_world` then."""
    v = obj.matrix_local.col[FORWARD_AXIS].to_3d()
    return None if v.length <= 1e-9 else tuple(v.normalized())


def to_parent_frame(obj, world_direction):
    """A WORLD direction expressed in `obj`'s parent frame. The identity for an unparented one."""
    d = Vector(world_direction)
    if obj.parent is None:
        return d.normalized() if d.length > 1e-9 else d
    d = obj.parent.matrix_world.to_3x3().inverted() @ d
    return d.normalized() if d.length > 1e-9 else d


def baseline_of(obj):
    """The facing `point_ops.sync_facings` last stamped on this point, IN ITS PARENT'S FRAME, or
    None if it never has."""
    v = Vector(obj.rka_pt.auto_tangent)
    return None if v.length <= 1e-9 else v.normalized()


def stamp_baseline(obj, direction=None):
    """Record the facing the TOOL is giving this point, so a later hand rotation is detectable.

    STORED IN THE POINT'S PARENT FRAME, and compared there -- see `was_rotated`. `direction` is a
    WORLD vector like everything else in this module; the conversion happens here, once."""
    d = Vector(direction) if direction is not None else Vector(facing_of(obj) or (0.0, 1.0, 0.0))
    d = to_parent_frame(obj, d)
    obj.rka_pt.auto_tangent = tuple(d) if d.length > 1e-9 else (0.0, 0.0, 0.0)


def was_rotated(obj):
    """Has the ARTIST turned this Empty away from the facing the tool gave it?

    THE GESTURE IS THE ROTATION -- not a mode switch the artist has to know about first. A point
    born by `Extend Road` was AUTO with identity rotation, so turning it 75 degrees about Z did
    NOTHING: the chain still took its Catmull-Rom tangent from the neighbours' positions and the
    arriving segment kept the old angle. That is the bug this answers, and the only way to answer
    it is to be able to tell a hand rotation apart from an arrow the tool has not re-faced yet --
    which is what the stamped baseline is for. Recomputing the chain tangent and comparing would
    promote every point the artist merely DRAGGED, because a translate changes the chain tangent
    while leaving the rotation alone.

    ASKED IN THE POINT'S PARENT FRAME, because that is the frame the baseline is stored in and a
    comparison across two frames is not a comparison. It matters for exactly one kind of point: a
    junction mouth, which is parented to its `JCT_*`. Turning the whole crossing rotates every
    mouth's WORLD facing, and against a world-space baseline that read as four hand rotations at
    once -- silently baking four MANUAL facings `Follow Road (Auto)` could then never straighten,
    and which rotating back would not undo. It is the pad's frame that moved, not the artist
    turning that one arm; the same distinction this function already draws between a rotation and
    a drag. For every unparented point the two frames are the same matrix, so this changes nothing
    at all -- including for every `.blend` written before it, whose junction parents all carry the
    identity rotation the old lock enforced."""
    base = baseline_of(obj)
    now = facing_local(obj)
    if base is None or now is None:
        return False
    dot = max(-1.0, min(1.0, base.dot(Vector(now))))
    return math.degrees(math.acos(dot)) > ROTATED_TOL_DEG


def read_point(obj):
    """Empty -> PointData. Reads `matrix_world.translation`, NEVER `location`: junction members are
    parented to their JCT_* Empty, so `location` is the offset within the pad, not the station.

    The ROTATION is read the same way, and only in MANUAL mode. This bridge was missing for the
    whole of step 4: `tangent_mode = MANUAL` was declared in the field table, `road_points`
    honoured it, and `point_profile.stations()` passed `tangent = None` unconditionally -- so
    rotating a point did nothing at all and the mode was dead state."""
    p = PointData(pos=tuple(obj.matrix_world.translation))
    src = obj.rka_pt
    for n, _k, _d in POINT_FIELDS:
        setattr(p, n, getattr(src, n))
    if p.tangent_mode == MANUAL:
        p.tangent = facing_of(obj)
    elif p.tangent_mode == AUTO and was_rotated(obj):
        # PROMOTION IS DERIVED AT READ TIME, never only by a handler. The overlay, the gate, the
        # build and the headless export all cross this one function, so a rotation takes effect
        # everywhere the instant it happens -- with no write, so it is safe mid-modal and safe
        # inside a draw handler. The enum on the object is flipped separately, by `sync_facings`,
        # purely so the panel tells the truth.
        p.tangent_mode, p.tangent = MANUAL, facing_of(obj)
    for l in src.links:
        if l.target is not None and getattr(l.target, "rka_pt", None) is not None:
            p.link_to(l.target.rka_pt.uid, l.type)
    return p


def write_point(obj, p, move=True):
    """PointData -> Empty. `move=False` leaves the transform alone, which is what a rebuild of the
    authored view after an edit wants: the artist's drag is the authority on position."""
    dst = obj.rka_pt
    dst.is_point = True
    for n, _k, _d in POINT_FIELDS:
        setattr(dst, n, getattr(p, n))
    if move:
        # Facing first: `face_matrix` rewrites the whole matrix, so setting the translation after
        # it is the only order that survives. A record with no tangent leaves the rotation alone.
        if p.tangent is not None:
            face_matrix(obj, p.tangent)
        obj.matrix_world.translation = p.pos
    return obj


def _local(collections, name):
    """Local-only lookup. A linked library carries same-named collections, so an unqualified
    `bpy.data.collections[name]` can hand back a neighbour district's ROAD_MANAGER -- and a build
    would then wipe geometry it does not own. Every collection lookup in this pipeline is
    local-only; keep new ones that way."""
    for c in collections:
        if c.name == name and c.library is None:
            return c
    return None


def road_collections(scene=None):
    """The per-road collections under ROAD_MANAGER, excluding JUNCTIONS."""
    root = _local(bpy.data.collections, ROAD_MANAGER)
    if root is None:
        return []
    return [c for c in root.children if c.library is None and c.name != JUNCTIONS]


def point_objects(coll):
    return [o for o in coll.objects if getattr(o, "rka_pt", None) is not None and o.rka_pt.is_point]


def link_order(net, road):
    """`(components, tangled)` -- the road's points as ITS OWN LINKS order them.

    THE CHAIN ORDER IS THE OBJECT-NAME ORDER (`read_network` sorts by name, `point_ops.point_name`
    and `_renumber` maintain it), and that is a fact about a list of strings. The LINKS are a fact
    about the road. They are two statements of the same thing and a hand edit can put them at odds
    -- rename a point, duplicate one, drag one between collections -- after which the build follows
    the links while the artist reads the order, silently. `point_validate.check_chains` reports the
    disagreement and `Author > Repair > Renumber Roads` fixes it, both from here.

    A component is a maximal set joined by SEGMENT or JUNCTION links (`road_corridors`' rule: a
    crossing does not split a street). Within one it is ordered by walking from an end. A component
    whose links BRANCH has no linear order at all -- three points on one station is not a chain --
    so it is returned in `tangled` and left alone; guessing an order for it would be inventing one.

    IT IS IDEMPOTENT ON A CORRECT ROAD, which is the property that makes it safe to run on
    everything: each walk starts at whichever end already sorts first, and the components come back
    in the order their earliest member currently sits, so a road that is already right is returned
    unchanged and the repair renames nothing.
    """
    uids = [u for u in road.points if u in net.points]
    rank = {u: i for i, u in enumerate(uids)}
    members = set(uids)

    def nbrs(u):
        p = net.points[u]
        return sorted((l.target for l in p.links
                       if l.target in members
                       and l.type in (LINK_SEGMENT, LINK_JUNCTION)), key=lambda t: rank[t])

    seen, comps, tangled = set(), [], []
    for u in uids:
        if u in seen:
            continue
        stack, comp = [u], []
        seen.add(u)
        while stack:
            v = stack.pop()
            comp.append(v)
            for w in nbrs(v):
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        comp.sort(key=lambda t: rank[t])
        deg = {v: len(nbrs(v)) for v in comp}
        if any(d > 2 for d in deg.values()):
            tangled.extend(comp)
            continue
        if len(comp) == 1:
            comps.append(list(comp))
            continue
        ends = [v for v in comp if deg[v] == 1]
        start = ends[0] if ends else comp[0]      # no end = a closed loop; start where it sorts
        order, prev, cur = [start], None, start
        while True:
            nxt = [w for w in nbrs(cur) if w != prev]
            if not nxt or nxt[0] in order:
                break
            prev, cur = cur, nxt[0]
            order.append(cur)
        if len(order) != len(comp):               # unreachable for deg<=2, but never emit a partial
            tangled.extend(comp)
            continue
        comps.append(order)
    comps.sort(key=lambda c: min(rank[u] for u in c))
    return comps, tangled


def road_corridors(net, road):
    """One road collection -> the groups of points that are actually ONE road.

    A corridor breaks where two chain-adjacent points carry NEITHER a SEGMENT nor a JUNCTION link.
    A junction gap is still one street -- the pad joins the two mouths, and a crossing does not
    split either road -- while a stretch joined by nothing at all is a SECOND road that happens to
    share a collection.

    That is a different split from `road_runs`, and both are needed. A **run** is what gets SWEPT
    (a lane, a kerb or a deck must not be drawn across a pad). A **corridor** is what gets FILED:
    one road collection should hold exactly one. `point_validate.check_chains` reports the breaks
    and `Tidy Roads` acts on them, from this one definition."""
    if isinstance(road, str):
        road = net.roads[road]
    uids = [u for u in road.points if u in net.points]
    if not uids:
        return []
    out, cur = [], [uids[0]]
    for a, b in zip(uids, uids[1:]):
        p = net.points[a]
        if p.has_link(b, LINK_SEGMENT) or p.has_link(b, LINK_JUNCTION):
            cur.append(b)
        else:
            out.append(cur)
            cur = [b]
    out.append(cur)
    return out


def point_labels(scene=None):
    """`{uid: "<road>/<object>"}` for every authored point in the scene.

    The map the gate needs to speak the artist's language: findings identify points by uid (they
    have to -- a uid is what survives a rename, which is the whole reason identity is not a name),
    and an artist has never seen one. `point_validate.describe` substitutes with this.

    IT READS THE NETWORK RATHER THAN THE OBJECTS (8j), because the uid a finding carries is the
    uid `read_network` RESOLVED, and the uid stored on a duplicated Empty is the one `dedupe_uids`
    replaced. Building the map from `rka_pt.uid` therefore missed exactly the points a duplicate
    generates findings about -- so every one of them came back naming a raw `p_1234abcd`, which is
    the thing 8i.10 exists to stop. Callers that already hold a network should use `net.labels`."""
    return read_network(scene).labels


def relink_from_objects(owners, reassigned):
    """Rebuild every point's links from its Empty's link rows, resolving targets by OBJECT.

    `owners` is `[(road, obj, point)]` for every authored point in the scene; `reassigned` is the
    set of points `dedupe_uids` just handed a fresh uid.

    WHY THIS CANNOT BE DONE IN `read_point` (8j). A link row holds an Object pointer, and
    `read_point` turns it into a uid by reading `target.rka_pt.uid` -- the uid STORED on the
    target, which after a duplicate is somebody else's. Duplicate a road collection and every
    internal link in the copy resolves to the ORIGINAL's points: the copy is wired into the road it
    was copied from, and the original's own membership is then rewritten out from under it. Only a
    pass that can see all the objects at once can tell those apart, so the resolution happens here,
    after the dedupe, from the object map -- which is the ground truth a pointer already is.

    A link whose target is in no road collection is DROPPED. That is not authored data: a point
    with no collection is invisible to the gate, to Build and to the export, and a link to one
    used to resolve -- by uid -- onto whichever real point happened to share its uid, silently
    re-wiring a live road to a deleted one."""
    by_obj = {}
    for _road, o, p in owners:
        by_obj[o.name] = p
    for _road, o, p in owners:
        p.links = []
        seen = set()
        for l in o.rka_pt.links:
            t = l.target
            if t is None or getattr(t, "rka_pt", None) is None:
                continue
            tp = by_obj.get(t.name)
            if tp is None or tp is p or tp.uid in seen:
                continue
            # A CLONE'S INHERITED LINK. The source was re-allocated and the target was not, so
            # this row was copied off an object whose neighbours still point at the ORIGINAL --
            # `dedupe_uids`' half-connected graph. A row inside the re-allocated set is the
            # duplicated road's own wiring and is kept.
            if p in reassigned and tp not in reassigned:
                continue
            seen.add(tp.uid)
            p.link_to(tp.uid, l.type)


def read_network(scene=None):
    """Scene -> NetworkData, with uid integrity enforced on the way through.

    The dedupe runs on EVERY read rather than at duplicate time, because there is no duplicate-time
    hook to run it in. `session_uid` orders the entries so `dedupe_uids` can tell which object is
    the newer one; Blender's own `.001` suffixing is the only ordering signal available and it is
    good enough -- a Shift+D clone is always the suffixed name.

    MEMBERSHIP IS BUILT FROM THE OBJECTS, NEVER REMAPPED BY UID (8j). It used to be collected as
    the points were read and then patched with `{old_uid: new_uid}` -- which is only a function
    when uids are unique, and the one moment it runs is the moment they are not. Duplicating a road
    collection put the SAME old uid in two roads, so the remap rewrote BOTH: the copy took the new
    uid and the ORIGINAL's road forgot its own points, which arrived at the gate as
    `point_orphan: <the copy> -- point belongs to no road collection` on five points that were
    plainly sitting in a road collection. Which collection an Empty is in is a fact about the
    Empty, so it is read off the Empty."""
    # A junction member's `matrix_world` is stale until the depsgraph settles, and its world
    # position IS the stop line -- reading a stale one silently misplaces every mouth.
    bpy.context.view_layer.update()
    net = NetworkData()
    entries, owners = [], []
    for coll in road_collections(scene):
        road = RoadData(coll.name)
        src = getattr(coll, "rka_road", None)
        if src is not None:
            for n, _k, _d in ROAD_FIELDS:
                setattr(road, n, getattr(src, n))
            road.name = coll.name
            for n, _k, _d in POINT_FIELDS:
                setattr(road.base, n, getattr(src.base, n))
        objs = sorted(point_objects(coll), key=lambda o: o.name)
        for i, o in enumerate(objs):
            p = read_point(o)
            entries.append(((o.name, i), p))
            owners.append((road, o, p))
        net.add_road(road)
    fixed = dedupe_uids(entries)
    relink_from_objects(owners, {p for _o, _n, p in fixed})
    for road, o, p in owners:
        net.points[p.uid] = p
        road.points.append(p.uid)
        net.labels[p.uid] = "%s/%s" % (road.name, o.name)
    net.uid_repairs = fixed
    return net


def register():
    if bpy is None:
        return
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.rka_pt = PointerProperty(type=RKA_Point)
    bpy.types.Collection.rka_road = PointerProperty(type=RKA_Road)


def unregister():
    if bpy is None:
        return
    del bpy.types.Collection.rka_road
    del bpy.types.Object.rka_pt
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)


# ------------------------------------------------------------------------------- self-test

def _road(net, name, n=3, spacing=50.0, **kw):
    r = net.add_road(RoadData(name, PointData(uid="", lane_width=3.5, median_width=1.0), **kw))
    prev = None
    for i in range(n):
        p = net.add_point(PointData(pos=(i * spacing, 0.0, 0.0)), r)
        if prev is not None:
            net.link(prev.uid, p.uid, LINK_SEGMENT)
        prev = p
    return r


def self_test():
    ok = 0

    # -- INHERIT / OVERRIDE ---------------------------------------------------------------------
    net = NetworkData()
    r = _road(net, "road_a", 3)
    r.base.lane_width = 3.25
    mid = net.points[r.points[1]]
    mid.lanes_fwd, mid.aux_fwd = 3, 1
    res = net.resolved(mid.uid)
    assert res.lane_width == 3.25, "INHERIT must take the road's base width"
    assert (res.lanes_fwd, res.aux_fwd) == (3, 1), "the four deltas survive INHERIT"
    mid.profile_mode = OVERRIDE
    mid.lane_width = 4.0
    assert net.resolved(mid.uid).lane_width == 4.0, "OVERRIDE is the point's own values"
    # A base profile has no business owning where a corner is.
    mid.profile_mode, mid.tangent_mode = INHERIT, SHARP
    assert net.resolved(mid.uid).tangent_mode == SHARP, "shape is per-station, never inherited"
    print("OK: INHERIT takes the road base + the four deltas; OVERRIDE and shape are per-station")
    ok += 1

    # -- link symmetry --------------------------------------------------------------------------
    net = NetworkData()
    r = _road(net, "road_a", 3)
    a, b, c = [net.points[u] for u in r.points]
    assert a.has_link(b.uid, LINK_SEGMENT) and b.has_link(a.uid, LINK_SEGMENT)
    ramp = net.add_road(RoadData("ramp_x"))
    rp = net.add_point(PointData(pos=(120.0, -8.0, 0.0), role=RAMP_EXIT,
                                 lanes_fwd=1, lanes_bwd=0), ramp)
    net.link(b.uid, rp.uid, LINK_AUX)
    assert b.has_link(rp.uid, LINK_AUX), "the mainline point owns the AUX link"
    assert not rp.has_link(b.uid), "AUX is DIRECTED -- the ramp point connects to nothing else"
    assert net.aux_pairs() == [(b.uid, rp.uid)]
    print("OK: SEGMENT/JUNCTION links are symmetric, AUX is directed mainline -> ramp")

    # -- the authored facing is a transform channel, and it round-trips -------------------------
    net = NetworkData()
    r = _road(net, "road_a", 3)
    mid = net.points[r.points[1]]
    assert mid.tangent is None, "a point nobody shaped carries no tangent -- keeps the diff clean"
    mid.tangent_mode = MANUAL
    mid.tangent = (0.0, 1.0, 0.0)
    back = PointData.from_dict(mid.to_dict())
    assert back.tangent == (0.0, 1.0, 0.0), back.tangent
    # ...and it survives INHERIT, because a road's base profile has no business owning a corner.
    assert net.resolved(mid.uid).tangent == (0.0, 1.0, 0.0)
    assert "tangent" not in net.points[r.points[0]].to_dict(), "unshaped points stay out of JSON"
    print("OK: the authored facing round-trips and is per-station, never inherited")
    ok += 1
    ok += 1

    # -- junction clique ------------------------------------------------------------------------
    net = NetworkData()
    ns, ew = _road(net, "road_ns", 3), _road(net, "road_ew", 3)
    m1, m2 = net.points[ns.points[1]], net.points[ew.points[1]]
    m1.role = m2.role = INTERSECTION
    net.link(m1.uid, m2.uid, LINK_JUNCTION)
    cl = net.junction_cliques()
    assert len(cl) == 1 and cl[0] == sorted([m1.uid, m2.uid])
    # The crossing must NOT have split either chain -- redesign defect 3.
    assert len(ns.points) == 3 and len(ew.points) == 3, "a junction sits INSIDE a chain"
    assert m1.uid in ns.points, "an INTERSECTION point stays an ordinary member of its road"
    print("OK: a junction is a clique over JUNCTION links and does NOT split either road")
    ok += 1

    # -- deletion strips inbound links (the zombie case) -----------------------------------------
    net = NetworkData()
    r = _road(net, "road_a", 3)
    a, b, c = list(r.points)
    net.remove_point(b)
    assert b not in net.points and b not in r.points
    assert not net.points[a].has_link(b) and not net.points[c].has_link(b), \
        "inbound links must be stripped FIRST or the point survives as a zero-collection zombie"
    print("OK: Delete Point strips inbound links first -- no dangling reference, no zombie")
    ok += 1

    # -- Shift+D uid collision ------------------------------------------------------------------
    orig = PointData(uid="p_dead", pos=(0.0, 0.0, 0.0))
    orig.link_to("p_nbr", LINK_SEGMENT)
    clone = orig.copy()                      # exactly what Object.copy() does to IDProperties
    assert clone.uid == orig.uid and clone.targets() == ["p_nbr"]
    fixed = dedupe_uids([(("p000", 0), orig), (("p000.001", 1), clone)])
    assert len(fixed) == 1 and fixed[0][0] == "p_dead"
    assert orig.uid == "p_dead", "the ORIGINAL keeps its uid"
    assert clone.uid != "p_dead" and clone.uid.startswith("p_"), "the NEWER object is reallocated"
    assert clone.targets() == [], \
        "the clone's links describe the ORIGINAL's connectivity -- adopting them doubles every edge"
    print("OK: Shift+D on one point -> the clone gets a NEW uid and no inherited links")
    ok += 1

    # -- .roads.json round-trip ------------------------------------------------------------------
    net = NetworkData()
    r = _road(net, "road_a", 4, is_loop=True, road_class="arterial", zone_id="IslandRoads")
    net.points[r.points[2]].profile_mode = OVERRIDE
    net.points[r.points[2]].lanes_fwd = 3
    net.link(r.points[3], r.points[0], LINK_SEGMENT)
    back = network_from_dict(network_to_dict(net))
    assert network_to_dict(back) == network_to_dict(net), "the record must round-trip byte-stable"
    assert back.roads["road_a"].is_loop and back.roads["road_a"].road_class == "arterial"
    assert back.points[r.points[2]].lanes_fwd == 3
    assert back.roads["road_a"].base.median_width == 1.0, "the base profile round-trips too"
    print("OK: .roads.json round-trips byte-stable, including the road base profile and the loop")
    ok += 1

    print("\nALL SELF-TESTS PASSED (%d)" % ok)
    return True


if __name__ == "__main__":
    self_test()

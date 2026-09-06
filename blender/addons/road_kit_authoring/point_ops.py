"""Operators -- the authoring gestures of ROAD_POINT_GRAPH.md 4.1.

Every operator in here is a thin shell: it edits the AUTHORED Empties and their `rka_pt` data and
then stops. It never builds geometry, never computes a lateral offset, and never touches
`ROAD_MANAGER_GEN`. That separation is what makes step 2 testable at all -- the whole scene below
is buildable, and gate-checkable, before a single Geometry Nodes socket exists.

Headless discipline, paid for once already: `--background` NEVER calls an operator's `invoke()`,
and `INVOKE_DEFAULT` degrades silently to `EXEC_DEFAULT` with property defaults. So no operator in
this file may compute anything essential in `invoke()`; everything an operator needs is an operator
PROPERTY, and the panel is what fills those in interactively.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator
from mathutils import Vector

from . import point_model as pm
from . import point_profile as pp
from . import point_solve as psolve
from . import point_validate as pv

import lane_profile as lp


# ------------------------------------------------------------------------------- scene plumbing

def _ensure_collection(name, parent=None):
    """Local-only lookup, then create. A linked library carries same-named collections, so an
    unqualified `bpy.data.collections[name]` can hand back a NEIGHBOUR district's ROAD_MANAGER --
    and a build would then wipe geometry it does not own."""
    c = pm._local(bpy.data.collections, name)
    if c is None:
        c = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(c)
    elif parent is not None and c.name not in {x.name for x in parent.children}:
        try:
            bpy.context.scene.collection.children.unlink(c)
        except Exception:
            pass
        parent.children.link(c)
    return c


def ensure_roots():
    root = _ensure_collection(pm.ROAD_MANAGER)
    _ensure_collection(pm.JUNCTIONS, root)
    _ensure_collection(pm.ROAD_MANAGER_GEN)
    return root


def road_collection(name):
    return _ensure_collection(name, ensure_roots())


def point_name(coll, i):
    """`<road>_p000`. The road prefix is NOT decoration: Blender object names are GLOBAL, so an
    unprefixed `p000` in a second road becomes `p000.001` and a third `p000.005` -- and since the
    chain order IS the name order, a road's points then sort into an order nobody authored. The
    prefix also makes the outliner legible, which is half of why this rewrite exists."""
    return "%s_p%03d" % (coll.name, i)


def _next_point_name(coll):
    used = {o.name for o in bpy.data.objects}
    i = 0
    while point_name(coll, i) in used:
        i += 1
    return point_name(coll, i)


def new_point(coll, pos, facing=None, **fields):
    """One authored road point. The transform IS the road frame at that station, so it needs no
    extra properties (1.2): position is the station, local +Y is travel direction, roll is banking.

    `ARROWS`, not `SINGLE_ARROW`: a single-arrow Empty draws along +Z, so it would show the artist
    an axis the model never reads while hiding the one it does. Rotating a point only makes sense
    if you can see which way +Y points."""
    obj = bpy.data.objects.new(_next_point_name(coll), None)
    obj.empty_display_type = 'ARROWS'
    obj.empty_display_size = 4.0
    obj.location = Vector(pos)
    coll.objects.link(obj)
    obj.rka_pt.is_point = True
    obj.rka_pt.uid = pm.new_uid()
    for k, v in fields.items():
        setattr(obj.rka_pt, k, v)
    # BORN FACING THE ROAD. A fresh Empty has identity rotation, so its +Y is world +Y -- on any
    # road that does not happen to run north the arrow the artist is looking at was a LIE, and
    # switching that point to MANUAL would snap the road to face north. Stamping the baseline in
    # the same breath is what makes a later hand rotation detectable (`point_model.was_rotated`).
    #
    # NOT `pm.face_matrix` and NOT `pm.facing_of`: both read `matrix_world`, which is STALE until
    # the next depsgraph evaluation -- on an object created microseconds ago it is still identity,
    # so face_matrix would write the station back to the world origin. Rotation and baseline are
    # both set from the vector we already hold.
    d = Vector(facing) if facing is not None else Vector((0.0, 1.0, 0.0))
    d = d.normalized() if d.length > 1e-9 else Vector((0.0, 1.0, 0.0))
    obj.rotation_euler = d.to_track_quat('Y', 'Z').to_euler()
    pm.stamp_baseline(obj, d)
    return obj


def points_in(coll):
    return sorted(pm.point_objects(coll), key=lambda o: o.name)


def collection_of(obj):
    for c in pm.road_collections():
        if obj.name in c.objects:
            return c
    return None


def link_objects(a, b, type=pm.LINK_SEGMENT, symmetric=None):
    """Writes the link on the OBJECTS. Two points carry at most ONE link between them, so this
    RETYPES an existing one rather than adding a second -- an AUX link and a SEGMENT link between
    the same pair would be contradictory. Retyping silently is the sharp edge; `Connect Selected`
    is where the artist is told, which is why this helper stays blunt and the operator does not."""
    if symmetric is None:
        symmetric = (type != pm.LINK_AUX)

    def one(src, dst):
        for l in src.rka_pt.links:
            if l.target is dst:
                l.type = type
                return
        l = src.rka_pt.links.add()
        l.target = dst
        l.type = type

    one(a, b)
    if symmetric:
        one(b, a)


def unlink_one(src, dst):
    """Remove only `src -> dst`, leaving the other direction alone. `unlink_objects` cuts both,
    which is right for `Disconnect` and wrong for repairing a DIRECTED link -- an AUX pair whose
    ramp has linked back needs the ramp's row gone and the mainline's kept."""
    n = 0
    for i in range(len(src.rka_pt.links) - 1, -1, -1):
        if src.rka_pt.links[i].target is dst:
            src.rka_pt.links.remove(i)
            n += 1
    return n


def unlink_objects(a, b):
    n = 0
    for src, dst in ((a, b), (b, a)):
        for i in range(len(src.rka_pt.links) - 1, -1, -1):
            if src.rka_pt.links[i].target is dst:
                src.rka_pt.links.remove(i)
                n += 1
    return n


def _local_road(name):
    return pm._local(bpy.data.collections, name)


def _select(context, *objs, active=None):
    """Set the selection an operator will read. `Add Sample Network` drives the real gestures, and
    a gesture's input IS the selection -- so the sample has to make one the same way a hand does."""
    for o in context.selected_objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    context.view_layer.objects.active = active or (objs[0] if objs else None)


def selected_points(context):
    return [o for o in context.selected_objects
            if getattr(o, "rka_pt", None) is not None and o.rka_pt.is_point]


def is_point(obj):
    return getattr(obj, "rka_pt", None) is not None and obj.rka_pt.is_point


def resolve_pair(context, target_name=""):
    """`(a, b)` for a two-point gesture, where **`a` is always the ACTIVE point**.

    The order is not cosmetic. `AUX` is a DIRECTED link -- mainline -> ramp -- and this used to be
    `a, b = selected_points(context)`, i.e. `context.selected_objects` order, which is arbitrary.
    So the Aux button was a coin flip that disagreed with the panel's own hint ("active =
    mainline") half the time. Anchoring on the active object is also what makes the gesture
    describable in one sentence: whatever you clicked LAST is `a`.

    `target_name` is the panel's "Connect To" field: name one point and the selection stops
    mattering at all, which is the answer to "it is hard to select two points"."""
    act = context.active_object
    act = act if (act is not None and is_point(act)) else None
    if target_name:
        b = bpy.data.objects.get(target_name)
        if act is None or b is None or not is_point(b):
            return None, None
        return act, b
    sel = selected_points(context)
    if len(sel) != 2:
        return None, None
    if act is not None and act in sel:
        other = sel[0] if sel[1] is act else sel[1]
        return act, other
    return sel[0], sel[1]


def declares_aux(obj):
    return obj.rka_pt.aux_fwd > 0 or obj.rka_pt.aux_bwd > 0


def resolve_aux_pair(a, b):
    """`(mainline, ramp)` for an AUX gesture, or `(None, None)` when neither reading works.

    AUX IS DIRECTED, BUT WHICH POINT IS THE MAINLINE IS A FACT ABOUT THE TWO POINTS -- not about
    which one you happened to click last. It used to be the second: `a` (the active point) had to
    be the mainline, so `Aux` on a ramp point with the mainline named in `Connect To` simply
    refused, and the only way to wire a ramp that MERGES into a road was to select it from the
    other end. Since an entrance ramp reads naturally in exactly that order (ramp joins road),
    half the ramps in a network could not be authored with the gesture the panel offers.

    So the two readings are scored and the better one wins. What makes a point the mainline is
    that it declares an aux slot for the ramp to land in; what makes one the ramp is that it is a
    one-way road and/or already carries a ramp role. The active point only breaks a genuine tie,
    which keeps the documented `active = mainline` behaviour true wherever it was ever true."""
    def score(main, ramp):
        s = 0
        if declares_aux(main):
            s += 3
        if pm.is_ramp_role(ramp.rka_pt.role):
            s += 2
        if pm.is_ramp_role(main.rka_pt.role):
            s -= 2
        if declares_aux(ramp):
            s -= 1
        # A ramp is one-way BY CONSTRUCTION (2.1) -- `Make Ramp` writes `lanes_bwd = 0`.
        if ramp.rka_pt.lanes_bwd == 0 or ramp.rka_pt.lanes_fwd == 0:
            s += 1
        if main.rka_pt.lanes_bwd == 0 or main.rka_pt.lanes_fwd == 0:
            s -= 1
        return s
    sa, sb = score(a, b), score(b, a)
    if sa <= 0 and sb <= 0:
        return None, None
    return (a, b) if sa >= sb else (b, a)


def sync_facings(scene=None, net=None):
    """Keep every AUTO arrow pointing along its road, and turn a hand ROTATION into authored shape.

    `(promoted, refaced)` -- both lists of object names.

    TWO HALVES, AND THE ORDER BETWEEN THEM IS THE WHOLE THING. Promotion is tested FIRST, or the
    re-face would overwrite the very rotation it is meant to notice and the gesture would be
    unusable. After that, every point the tool still owns is re-faced to the chain tangent and its
    baseline re-stamped, so the arrow the artist sees is the direction the solver actually uses --
    and so the NEXT rotation is measurable against a known zero.

    A point in SHARP or MANUAL is never touched: those modes mean the artist owns the facing."""
    scene = scene or bpy.context.scene
    net = net if net is not None else pm.read_network(scene)
    want = pp.chain_facings(net)
    promoted, refaced = [], []
    for coll in pm.road_collections(scene):
        for o in points_in(coll):
            pt = o.rka_pt
            if pt.tangent_mode != pm.AUTO:
                continue
            if pm.was_rotated(o):
                pt.tangent_mode = pm.MANUAL          # the rotation IS the gesture
                promoted.append(o.name)
                continue
            d = want.get(pt.uid)
            if d is None:
                continue
            pm.face_matrix(o, d)
            pm.stamp_baseline(o, d)
            refaced.append(o.name)
    return promoted, refaced


# ------------------------------------------------------------------------------- corridor

class RKA_OT_new_road(Operator):
    """Create a road collection and its first point"""
    bl_idname = "rka.new_road"
    bl_label = "New Road"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(default="road_new")
    x: FloatProperty(default=0.0)
    y: FloatProperty(default=0.0)
    z: FloatProperty(default=0.0)
    lanes_fwd: bpy.props.IntProperty(default=2, min=0)
    lanes_bwd: bpy.props.IntProperty(default=2, min=0)
    lane_width: FloatProperty(default=3.5)
    median_width: FloatProperty(default=1.0)
    road_class: StringProperty(default="street")
    design_speed: FloatProperty(default=50.0)

    def execute(self, context):
        coll = road_collection(self.name)
        coll.rka_road.is_road = True
        coll.rka_road.name = coll.name
        coll.rka_road.road_class = self.road_class
        # The ROAD's base profile (1.2a). A station in INHERIT mode takes this and applies only
        # the four genuine deltas, so changing a 20-station road's lane width is ONE edit.
        b = coll.rka_road.base
        b.lanes_fwd, b.lanes_bwd = self.lanes_fwd, self.lanes_bwd
        b.lane_width, b.median_width = self.lane_width, self.median_width
        b.design_speed = self.design_speed
        p = new_point(coll, (self.x, self.y, self.z),
                      lanes_fwd=self.lanes_fwd, lanes_bwd=self.lanes_bwd)
        context.view_layer.objects.active = p
        self.report({'INFO'}, "%s created" % coll.name)
        return {'FINISHED'}


class RKA_OT_extend_road(Operator):
    """Add a point beyond the road's end, linked SEGMENT -- the E-key loop.

    EITHER END. The chain order IS the object-name order and FWD is increasing index, so a new
    point can only ever be born at one end of the names -- and this used to be the LAST one
    unconditionally, whichever point was active. Extending from `..._p000` therefore produced a
    point that was:

    * placed in the FORWARD direction, i.e. back down the road it was supposed to grow away from
      (with only the head's own arrow to go on, `prev` was None and the +Y facing is the way the
      road already runs); and
    * named `..._p00N`, i.e. sorted to the far end of a road it sits at the START of, so the chain
      order disagreed with the geometry and the link, and Build reported `chain_unlinked` on a
      pair of points nobody had touched.

    Both halves are the same fix: grow AWAY from the chain, and renumber so the name order still
    matches the road. Extending the head prepends; extending the tail appends, exactly as before.
    An interior point is refused by name rather than guessed at -- "extend" has no meaning in the
    middle of a chain, and picking an end for the artist is how a road silently grows the wrong
    way."""
    bl_idname = "rka.extend_road"
    bl_label = "Extend Road"
    bl_options = {'REGISTER', 'UNDO'}

    distance: FloatProperty(default=100.0)
    dx: FloatProperty(default=0.0)
    dy: FloatProperty(default=0.0)
    dz: FloatProperty(default=0.0)
    use_delta: BoolProperty(default=False,
                            description="Use dx/dy/dz verbatim instead of the chain tangent")

    def execute(self, context):
        obj = context.active_object
        coll = collection_of(obj) if obj else None
        if coll is None:
            self.report({'ERROR'}, "no active road point")
            return {'CANCELLED'}
        pts = points_in(coll)
        i = pts.index(obj)
        # Which END is this, in NAME order? That is the only order the chain has.
        at_head, at_tail = (i == 0), (i == len(pts) - 1)
        if not (at_head or at_tail):
            self.report({'ERROR'},
                        "%s is in the middle of %s -- extend from an end (%s or %s), or "
                        "Insert Point to add a station between two, or Branch Ramp Here to "
                        "start a new road leaving this one"
                        % (obj.name, coll.name, pts[0].name, pts[-1].name))
            return {'CANCELLED'}
        # The neighbour we are growing AWAY from. At the tail that is the point before; at the
        # head it is the point after, and the sign flip is the whole of the head fix.
        nb = pts[i - 1] if at_tail and i > 0 else (pts[1] if at_head and len(pts) > 1 else None)
        if self.use_delta:
            off = Vector((self.dx, self.dy, self.dz))
        else:
            # Along the chain tangent, so extending a curving road keeps curving. With one point
            # there is no chord yet, so the Empty's own +Y is the direction -- which is exactly
            # what the arrow in the viewport is showing the artist.
            if nb is not None:
                d = (obj.matrix_world.translation - nb.matrix_world.translation)
            else:
                d = obj.matrix_world.to_quaternion() @ Vector((0.0, 1.0, 0.0))
            d = d.normalized() if d.length > 1e-9 else Vector((0.0, 1.0, 0.0))
            off = d * self.distance
        # +Y IS TRAVEL, and travel runs with increasing index. A point prepended at the head faces
        # back INTO the road (toward the old p000); one appended at the tail faces on out.
        facing = off if at_tail else -off
        p = new_point(coll, obj.matrix_world.translation + off, facing=facing)
        for n in pm.DELTA_FIELDS:
            setattr(p.rka_pt, n, getattr(obj.rka_pt, n))
        link_objects(obj, p, pm.LINK_SEGMENT)
        if at_head and len(pts) > 1:
            # `_next_point_name` can only ever hand out the NEXT free index, so a head extension is
            # always born misfiled. Renaming is safe by construction: identity is the uid.
            _renumber(coll, inserted=p, at=0)
        # ...and the point we grew FROM. Extending a one-point road turns the start station into a
        # station with a chain direction, and until this ran its arrow still pointed at world +Y.
        sync_facings(context.scene)
        for o in context.selected_objects:
            o.select_set(False)
        p.select_set(True)
        context.view_layer.objects.active = p
        return {'FINISHED'}


class RKA_OT_insert_point(Operator):
    """Split a SEGMENT link; the new point's profile is INTERPOLATED, so inserting changes nothing"""
    bl_idname = "rka.insert_point"
    bl_label = "Insert Point"
    bl_options = {'REGISTER', 'UNDO'}

    t: FloatProperty(default=0.5, min=0.0, max=1.0)

    def execute(self, context):
        sel = selected_points(context)
        if len(sel) == 1:
            # ONE POINT IS ENOUGH, and it is the gesture an artist actually makes: "put another
            # station after this one". The downstream neighbour is not a guess -- it is the point
            # this one carries a SEGMENT link to that comes after it in the chain, which is exactly
            # the span an insert splits. At the tail there is no such span, and `Extend Road` is
            # the operator for growing the road instead of subdividing it.
            a = sel[0]
            b = _next_in_chain(a)
            if b is None:
                self.report({'ERROR'}, "no span after this point -- use Extend Road to grow the "
                                       "road, or select both points of the span to split")
                return {'CANCELLED'}
            sel = [a, b]
        if len(sel) != 2:
            self.report({'ERROR'}, "select 1 point (splits the span after it) or 2 linked points")
            return {'CANCELLED'}
        a, b = sel
        coll = collection_of(a)
        if coll is None or collection_of(b) is not coll:
            self.report({'ERROR'}, "both points must be in the same road")
            return {'CANCELLED'}
        pos = a.matrix_world.translation.lerp(b.matrix_world.translation, self.t)
        p = new_point(coll, pos, facing=b.matrix_world.translation - a.matrix_world.translation)
        # INHERIT, and the four deltas taken from the upstream station: an inserted point must
        # change NOTHING visually, or "add a bend here" silently re-declares the cross-section.
        for n in pm.DELTA_FIELDS:
            setattr(p.rka_pt, n, getattr(a.rka_pt, n))
        unlink_objects(a, b)
        link_objects(a, p, pm.LINK_SEGMENT)
        link_objects(p, b, pm.LINK_SEGMENT)
        # Name it so it SORTS between its neighbours -- the chain order is the object-name order,
        # and an inserted `p006` between `p002` and `p003` would reorder the whole road.
        _renumber(coll, after=a, inserted=p, before=b)
        sync_facings(context.scene)
        context.view_layer.objects.active = p
        return {'FINISHED'}


def _next_in_chain(obj):
    """The point after `obj` in its own road: its SEGMENT-linked neighbour with the higher chain
    position. Derived from the links and the order together, because either alone is ambiguous --
    the link says which points are joined and the order says which of the two is downstream."""
    coll = collection_of(obj)
    if coll is None:
        return None
    chain = points_in(coll)
    if obj not in chain:
        return None
    i = chain.index(obj)
    linked = {l.target for l in obj.rka_pt.links
              if l.target is not None and l.type == pm.LINK_SEGMENT}
    for o in chain[i + 1:]:
        if o in linked:
            return o
    return None


def _renumber(coll, after=None, inserted=None, before=None, at=None):
    """Rename every point so the chain order IS the name order. Renaming is safe: a uid survives
    a rename by construction, which is precisely why identity is a uid and not a name.

    `after` puts `inserted` straight after that point (Insert Point); `at` puts it at an absolute
    index (Extend Road prepending to the head, `at=0`).

    CALLER RULE, because object names are GLOBAL and this cannot see past its own collection: when
    points MOVE between collections, renumber the DESTINATION first. The two passes below only
    protect against collisions WITHIN `pts`; a point that has left this collection but not yet been
    renamed still holds a `<coll>_pNNN` name, and pass two then gets `<coll>_pNNN.001` back from
    Blender -- a point whose name sorts outside the chain it belongs to, which is the one thing the
    name order is supposed to guarantee."""
    pts = points_in(coll)
    if inserted is not None and (after is not None or at is not None):
        pts.remove(inserted)
        pts.insert(pts.index(after) + 1 if after is not None
                   else max(0, min(int(at), len(pts))), inserted)
    # Two passes: renaming straight into the target names would collide with the points still
    # holding them and Blender would suffix them instead. Renaming is safe -- a uid survives a
    # rename by construction, which is precisely why identity is a uid and not a name.
    for i, o in enumerate(pts):
        o.name = "__tmp_%s_%03d" % (coll.name, i)
    for i, o in enumerate(pts):
        o.name = point_name(coll, i)
    return pts


#: The pad handle's object-name prefix. One owner: `_junction_name` allocates them and
#: `complete_junction_cliques` recognises them.
JCT_PREFIX = "JCT_"


def complete_junction_cliques(seed, context=None):
    """Make every junction COMPONENT containing one of `seed` a proper pad again. Returns
    `(links_written, roles_fixed, parented)`.

    A PAD IS A CLIQUE, A MOUTH IS AN `INTERSECTION`, AND BOTH ARMS OF IT HANG OFF THE `JCT_*`.
    `make_pad` writes all three together and is the only place that ever did -- so any OTHER
    gesture that hands a point a JUNCTION link leaves a pad that is a component and not a clique,
    with a mouth still typed `SEGMENT` and unparented. `point_solve.solve_junction` then builds one
    pad as two overlapping ones and `Auto Setback` solves a clique that is not one.

    `Merge Points` was that other gesture. It carries every link that left the collapsed run onto
    the survivor -- correctly -- and stopped there: merging two stations of a road that meets a
    4-arm crossing left the island's port junction a 4-node component with **3** edges, two mouths
    typed `SEGMENT`, and 8 gate errors. Repairing it belongs here rather than in the operator so
    `Repair Links` and any future gesture share one answer.
    """
    net = pm.read_network()
    want = {o.rka_pt.uid for o in seed if is_point(o)}
    by_uid = {}
    for c in pm.road_collections():
        for o in points_in(c):
            by_uid[o.rka_pt.uid] = o
    wrote = roles = parented = 0
    for comp in net.junction_cliques():
        if not (want & set(comp)):
            continue
        objs = [by_uid[u] for u in comp if u in by_uid]
        for i, a in enumerate(objs):
            for b in objs[i + 1:]:
                if not any(x.target is b for x in a.rka_pt.links):
                    link_objects(a, b, pm.LINK_JUNCTION)
                    wrote += 1
        # The pad's handle, if the crossing already has one. A mouth that is not parented to it
        # does not turn with the crossing and is not counted by `junction_centre`, so G and R on
        # the `JCT_*` would silently leave it behind.
        jct = next((o.parent for o in objs if o.parent is not None
                    and o.parent.name.startswith(JCT_PREFIX)), None)
        for o in objs:
            if o.rka_pt.role != pm.INTERSECTION:
                o.rka_pt.role = pm.INTERSECTION
                roles += 1
            if jct is not None and o.parent is not jct:
                world = o.matrix_world.copy()
                o.parent = jct
                o.matrix_parent_inverse = jct.matrix_world.inverted()
                o.matrix_world = world
                parented += 1
        if jct is not None and context is not None:
            recentre_junction(jct, context)
    return wrote, roles, parented


class RKA_OT_merge_points(Operator):
    """Collapse the selected run of points into ONE, keeping every link that left the run"""
    bl_idname = "rka.merge_points"
    bl_label = "Merge Points"
    bl_options = {'REGISTER', 'UNDO'}

    at_active: BoolProperty(
        name="At Active",
        description="Put the survivor on the active point instead of the centroid",
        default=False)

    def execute(self, context):
        sel = selected_points(context)
        if len(sel) < 2:
            self.report({'ERROR'}, "select 2 or more points of one road")
            return {'CANCELLED'}
        coll = collection_of(sel[0])
        if coll is None or any(collection_of(o) is not coll for o in sel):
            # ONE ROAD, and this is a rule about the model rather than a limitation. Two points in
            # DIFFERENT roads that ought to be the same place are not a merge -- a crossing is a
            # clique of JUNCTION links between points that each stay in their own road, and
            # collapsing them into one would delete the junction. `Connect Selected` and
            # `Make Intersection` are that gesture.
            self.report({'ERROR'}, "all selected points must be in the same road -- two points in "
                                   "different roads are joined with Connect / Make Intersection")
            return {'CANCELLED'}

        chain = points_in(coll)
        idx = sorted(chain.index(o) for o in sel)
        if idx != list(range(idx[0], idx[0] + len(idx))):
            # A CONTIGUOUS RUN, or the road would be spliced end to end. Merging p002 with p009
            # is not "make these one station", it is "delete everything between them" wearing a
            # friendlier name, and Delete Point already says that out loud.
            self.report({'ERROR'}, "select points that are next to each other in the chain")
            return {'CANCELLED'}
        doomed = [chain[i] for i in idx]

        # THE SURVIVOR IS THE FIRST OF THE RUN, so the merged station keeps a station's identity
        # (its uid, its cross-section, its facing) rather than being a new point wearing an
        # average of several. Only its POSITION moves, and the default is the centroid because
        # "merge these into one" means one in the middle of them; `at_active` is for the case
        # where one of them is the right place and the others are the mistake.
        keep = doomed[0]
        act = context.view_layer.objects.active
        if self.at_active and is_point(act) and act in doomed:
            keep = act
        gone = [o for o in doomed if o is not keep]
        if self.at_active:
            pos = keep.matrix_world.translation.copy()
        else:
            pos = doomed[0].matrix_world.translation.copy()
            for o in doomed[1:]:
                pos = pos + o.matrix_world.translation
            pos = pos / float(len(doomed))

        # EVERY LINK THAT LEFT THE RUN COMES WITH IT. A merged station stands where several did, so
        # whatever they joined -- the road either side, a junction clique, a ramp -- must still be
        # joined to something, and the survivor is the only thing left to join it to. Links INSIDE
        # the run are what is being collapsed and go.
        moved = 0
        for o in gone:
            for l in list(o.rka_pt.links):
                other, ltype = l.target, l.type
                if other is None or other in doomed:
                    continue
                unlink_objects(o, other)
                if not any(k.target is other for k in keep.rka_pt.links):
                    link_objects(keep, other, ltype)
                    moved += 1
        for o in doomed:
            for q in doomed:
                if o is not q:
                    unlink_objects(o, q)
        # Strip inbound links the same way Delete Point does -- a merely unlinked point survives as
        # a zero-collection zombie held by its referrers (1.2b).
        dead = set(gone)
        for c in pm.road_collections():
            for o in points_in(c):
                if o in dead:
                    continue
                for i in range(len(o.rka_pt.links) - 1, -1, -1):
                    if o.rka_pt.links[i].target in dead:
                        o.rka_pt.links.remove(i)
        for o in gone:
            bpy.data.objects.remove(o, do_unlink=True)

        keep.matrix_world.translation = pos
        # A MERGED STATION THAT INHERITED A JUNCTION LINK IS A MOUTH. Carrying the link over is
        # only half of it: a pad is a clique, its members are `INTERSECTION`, and they hang off the
        # `JCT_*` handle -- see `complete_junction_cliques` for what leaving that out cost.
        # `matrix_world` is read in there, so the depsgraph has to catch up with the line above.
        context.view_layer.update()
        wrote, roles, parented = complete_junction_cliques([keep], context)
        _renumber(coll)
        sync_facings(context.scene)
        context.view_layer.objects.active = keep
        keep.select_set(True)
        self.report({'INFO'}, "merged %d point(s) into %s (%d link(s) carried over)"
                    % (len(doomed), keep.name, moved))
        return {'FINISHED'}


class RKA_OT_renumber_roads(Operator):
    """Rename every road's points so the chain order is the order its own LINKS put them in"""
    bl_idname = "rka.renumber_roads"
    bl_label = "Renumber Roads"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # THE ANSWER TO "is it okay to let points connect randomly after a manual edit". It is not:
        # `point_model.read_network` sorts a road's points BY NAME and that order IS the chain, so
        # a hand edit that breaks the naming (a rename, a Shift+D, a point dragged into another
        # collection) silently reorders the road -- the links still say what is joined to what, and
        # the build follows them, but the file no longer reads the way it behaves. Every gesture in
        # this addon renumbers for you; this is the repair for the ones that are not gestures.
        #
        # It reads the LINKS and renames to match, never the other way round: links are authored
        # and a name is derived, so when they disagree the name is the one that is wrong.
        net = pm.read_network()
        renamed, tangled_roads = 0, []
        for name in sorted(net.roads):
            coll = pm._local(bpy.data.collections, name)
            if coll is None:
                continue
            comps, tangled = pm.link_order(net, net.roads[name])
            if tangled:
                tangled_roads.append(name)
            order = [u for c in comps for u in c] + tangled
            by_uid = {o.rka_pt.uid: o for o in points_in(coll)}
            want = [by_uid[u] for u in order if u in by_uid]
            if want == points_in(coll):
                continue
            # Two passes for the same reason `_renumber` uses them: renaming straight into a name
            # another point still holds gets a `.001` suffix back from Blender, and a suffixed name
            # sorts outside its own chain -- the one thing the order has to guarantee.
            for i, o in enumerate(want):
                o.name = "__tmp_%s_%03d" % (coll.name, i)
            for i, o in enumerate(want):
                o.name = point_name(coll, i)
            renamed += len(want)
        msg = "renumbered %d point(s)" % renamed
        if tangled_roads:
            msg += "; left %s alone (links branch)" % ", ".join(sorted(tangled_roads))
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class RKA_OT_split_road(Operator):
    """Move the selected points into a road of their own, keeping every link

    THE FIX FOR "I EXTENDED INTO THE WRONG ROAD". A road's chain is its object-name order, so a
    stretch that is not joined to the rest of the collection still sorts into the middle of it:
    the build splits it out as a separate run (`point_solve.road_runs`) and gets the geometry
    right, but the gate has to report the seam and every panel reads one road where there are two.
    That is exactly what happens when a ramp is grown with `Extend Road` off its mainline instead
    of `New Road` -- the usual way, because it is the gesture already under your hand.

    Links are OBJECT POINTERS, so nothing has to be rewired: the AUX link from the mainline into
    the moved points survives the move, which is the whole reason a ramp may live in its own road
    in the first place. Both collections are renumbered, so both chains stay in name order."""
    bl_idname = "rka.split_road"
    bl_label = "Split To New Road"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(default="", description="Blank = <source>_split")

    def execute(self, context):
        sel = selected_points(context)
        if not sel:
            self.report({'ERROR'}, "select the points to move out")
            return {'CANCELLED'}
        src = collection_of(sel[0])
        if src is None or any(collection_of(o) is not src for o in sel):
            self.report({'ERROR'}, "every selected point must be in the SAME road")
            return {'CANCELLED'}
        if len(sel) >= len(points_in(src)):
            self.report({'ERROR'}, "that is the whole of %s -- rename the collection instead"
                        % src.name)
            return {'CANCELLED'}
        dst = road_collection(self.name or (src.name + "_split"))
        dst.rka_road.is_road = True
        dst.rka_road.name = dst.name
        # The new road INHERITS the source's base profile and road-level settings. A split is a
        # re-filing, not a re-authoring: a ramp moved out of its highway must keep the highway's
        # `ped_access` and `barrier_height` or its walls silently change.
        for n, _k, _d in pm.ROAD_FIELDS:
            if n != "name":
                setattr(dst.rka_road, n, getattr(src.rka_road, n))
        for n, _k, _d in pm.POINT_FIELDS:
            setattr(dst.rka_road.base, n, getattr(src.rka_road.base, n))
        moved = sorted(sel, key=lambda o: o.name)
        for o in moved:
            src.objects.unlink(o)
            dst.objects.link(o)
        # DESTINATION FIRST. Object names are GLOBAL, and until the moved points are renamed into
        # the destination's namespace they are still holding `<src>_pNNN` -- so renumbering the
        # source first tries to hand out a name a moved point still owns and Blender suffixes it
        # (`main_p000.001`), leaving a point whose name sorts outside its own chain. See
        # `_renumber`.
        _renumber(dst)
        _renumber(src)
        context.view_layer.objects.active = moved[0]
        self.report({'INFO'}, "%d point(s) -> %s" % (len(moved), dst.name))
        return {'FINISHED'}


# ------------------------------------------------------------------------------- connections

class RKA_OT_connect_selected(Operator):
    """Link two selected points, validating the roles BEFORE writing"""
    bl_idname = "rka.connect_selected"
    bl_label = "Connect Selected"
    bl_options = {'REGISTER', 'UNDO'}

    type: EnumProperty(items=[(t, t.title(), "") for t in pm.LINK_TYPES], default=pm.LINK_SEGMENT)
    #: Optional: connect the ACTIVE point to this named one instead of to the other selected one.
    #: Selecting exactly two points in a dense network is fiddly; naming one is not.
    target: StringProperty(default="")

    def execute(self, context):
        a, b = resolve_pair(context, self.target)
        if a is None:
            self.report({'ERROR'}, "select exactly 2 points, or pick a target in the panel")
            return {'CANCELLED'}
        if a is b:
            return {'CANCELLED'}
        if self.type == pm.LINK_JUNCTION:
            for o in (a, b):
                o.rka_pt.role = pm.INTERSECTION
        elif self.type == pm.LINK_AUX:
            # EITHER ORDER. `resolve_aux_pair` decides which of the two is the mainline from what
            # they declare, so wiring a ramp that merges INTO a road (select the ramp, name the
            # road) works exactly as well as one that leaves it. The link itself stays directed.
            main, ramp = resolve_aux_pair(a, b)
            if main is None:
                self.report({'ERROR'}, "neither %s nor %s declares an aux lane -- set aux_fwd (or "
                                       "aux_bwd) on the MAINLINE point first, then connect"
                                       % (a.name, b.name))
                return {'CANCELLED'}
            if not declares_aux(main):
                self.report({'ERROR'}, "%s declares no aux lane for the ramp to align to"
                            % main.name)
                return {'CANCELLED'}
            # ONE ramp role. The direction (exit or entrance) is derived at export time from the
            # ramp's own chain, so there is nothing here for the artist to get the wrong way round.
            if not pm.is_ramp_role(ramp.rka_pt.role):
                ramp.rka_pt.role = pm.RAMP
            a, b = main, ramp
        # Validated BEFORE writing, and AFTER the AUX orientation is resolved, or the warning would
        # name the pair the artist typed rather than the one being written. The model's `link_to`
        # retypes an existing link silently, which is right for the data layer and wrong as a
        # gesture -- this is where the artist is told.
        existing = next((l for l in a.rka_pt.links if l.target is b), None)
        if existing is not None and existing.type != self.type:
            self.report({'WARNING'}, "retyping the existing %s link to %s"
                        % (existing.type, self.type))
        link_objects(a, b, self.type)
        self.report({'INFO'}, "%s: %s -> %s" % (self.type, a.name, b.name)
                    if self.type == pm.LINK_AUX
                    else "%s: %s <-> %s" % (self.type, a.name, b.name))
        return {'FINISHED'}


class RKA_OT_disconnect_selected(Operator):
    """Remove the link between two selected points"""
    bl_idname = "rka.disconnect_selected"
    bl_label = "Disconnect Selected"
    bl_options = {'REGISTER', 'UNDO'}

    target: StringProperty(default="")

    def execute(self, context):
        a, b = resolve_pair(context, self.target)
        if a is None:
            self.report({'ERROR'}, "select exactly 2 points, or use the X on a connection row")
            return {'CANCELLED'}
        n = unlink_objects(a, b)
        self.report({'INFO'}, "removed %d link(s)" % n)
        return {'FINISHED'} if n else {'CANCELLED'}


class RKA_OT_repair_links(Operator):
    """Drop links that cannot be honoured, restore the halves that went missing

    THE GATE HAS BEEN ADVISING THIS SINCE STEP 1 AND IT DID NOT EXIST. `uid_duplicate` said "run
    Repair Links" and `link_dangling`'s comment called it "an actionable fix rather than advice";
    it was deferred out of step 2 and never came back. So the one class of defect an artist
    genuinely CANNOT see -- a link row pointing at a deleted object, a half-written junction, a
    clone carrying somebody else's uid -- was reported with a remedy nobody could run.

    What it does is exactly the set of repairs that have ONE right answer:

    * **Drop** a row that is structurally impossible: a `None` target (the object was deleted), a
      self-link, a target that is not a point or lives in no road, and a duplicate row to a target
      already linked. A pair of points carries at most ONE link -- `link_objects`' invariant.
    * **Drop** the ramp's half of an `AUX` pair. `AUX` is directed and a ramp point connects ONLY
      to the aux slot (`aux_backlink`).
    * **Restore** the missing half of a `SEGMENT` or `JUNCTION` link, and complete a junction
      component into the full clique. These are "the gesture half-wrote it" cases, and completing
      them is what the gesture would have done.
    * **Write back** a repaired uid. `read_network` already re-allocates one for a Shift+D clone
      (`dedupe_uids`), but nothing persisted it, so every read re-did the same repair and the
      warning never went away.

    Where two rows of one pair DISAGREE about the type, the more explicit gesture wins:
    `AUX` over `JUNCTION` over `SEGMENT`. A junction and a ramp are both things you went and did;
    `SEGMENT` is what `Extend Road` writes by default."""
    bl_idname = "rka.repair_links"
    bl_label = "Repair Links"
    bl_options = {'REGISTER', 'UNDO'}

    #: Precedence when the two directions of one pair disagree -- see the docstring.
    RANK = {pm.LINK_SEGMENT: 0, pm.LINK_JUNCTION: 1, pm.LINK_AUX: 2}

    def execute(self, context):
        # THE UID WRITE-BACK FIRST. Everything below identifies points by object, but the finding
        # the artist saw was about a uid, and leaving the collision in place means the next read
        # re-allocates a different one -- so the repair must land on the objects before anything
        # else reads them.
        net = pm.read_network()
        uids = 0
        if net.uid_repairs:
            by_old = {}
            for coll in pm.road_collections():
                for o in points_in(coll):
                    by_old.setdefault(o.rka_pt.uid, []).append(o)
            victims = []
            for old, new, p in net.uid_repairs:
                # `dedupe_uids` keeps the OLDEST holder and re-allocates the newer one, ordering
                # by name -- Blender's `.001` suffix. Same order here, same object.
                same = sorted(by_old.get(old, []), key=lambda o: o.name)
                if len(same) > 1:
                    victim = same[-1]
                    victim.rka_pt.uid = p.uid
                    victims.append(victim)
                    same.pop()
                    uids += 1
            # A CLONE'S INHERITED LINK, NOT EVERY LINK IT HAS (8j). Clearing them all was right
            # for Shift+D on one Empty and destroyed a duplicated ROAD: its rows already point at
            # its own copies, and wiping them left the artist's new road as five loose points.
            # `point_model.relink_from_objects` draws the line in the read; this is the same line,
            # written back. A row that leaves the re-allocated set describes the original's
            # connectivity and goes; one that stays inside it is the copy's own wiring and stays.
            fresh = set(victims)
            for victim in victims:
                for i in range(len(victim.rka_pt.links) - 1, -1, -1):
                    if victim.rka_pt.links[i].target not in fresh:
                        victim.rka_pt.links.remove(i)

        points = [o for coll in pm.road_collections() for o in points_in(coll)]
        known = set(points)
        dropped = 0
        for o in points:
            seen = set()
            for i in range(len(o.rka_pt.links) - 1, -1, -1):
                l = o.rka_pt.links[i]
                t = l.target
                if t is None or t is o or t not in known or t in seen:
                    o.rka_pt.links.remove(i)
                    dropped += 1
                    continue
                seen.add(t)

        # -- one link per pair, one type ----------------------------------------------------------
        retyped, restored = 0, 0
        for o in points:
            for l in list(o.rka_pt.links):
                back = next((x for x in l.target.rka_pt.links if x.target is o), None)
                if back is None:
                    continue
                if back.type != l.type:
                    win = l.type if self.RANK[l.type] >= self.RANK[back.type] else back.type
                    l.type = back.type = win
                    retyped += 1
        for o in points:
            for l in list(o.rka_pt.links):
                if l.type == pm.LINK_AUX:
                    # Directed: the ramp must not link back. Drop the ramp's row, keep ours.
                    if unlink_one(l.target, o):
                        dropped += 1
                    continue
                if not any(x.target is o for x in l.target.rka_pt.links):
                    link_objects(l.target, o, l.type)
                    restored += 1

        # -- a junction COMPONENT becomes the CLIQUE `Make Intersection` would have written --------
        cliques = 0
        for comp in pm.read_network().junction_cliques():
            objs = [o for o in points if o.rka_pt.uid in set(comp)]
            for i, a in enumerate(objs):
                for b in objs[i + 1:]:
                    if not any(x.target is b for x in a.rka_pt.links):
                        link_objects(a, b, pm.LINK_JUNCTION)
                        restored += 1
            for o in objs:
                o.rka_pt.role = pm.INTERSECTION
            cliques += 1

        parts = []
        for n, what in ((dropped, "link(s) dropped"), (restored, "half-link(s) restored"),
                        (retyped, "type conflict(s) resolved"), (uids, "uid(s) reallocated"),
                        (cliques, "junction clique(s) checked")):
            if n:
                parts.append("%d %s" % (n, what))
        self.report({'INFO'}, "; ".join(parts) if parts else "nothing to repair")
        return {'FINISHED'}


class RKA_OT_tidy_roads(Operator):
    """File every point into the road collection its connections say it belongs to

    TWO MOVES, and both answer "the point is in the wrong collection" without asking the artist
    which one is right -- the links already say.

    * **Relocate a mis-filed point.** A point with no `SEGMENT` link inside its own collection
      whose `SEGMENT` links all land in ONE other collection belongs in that one. It is placed
      next to the neighbour it joins, not appended, because the chain order is the name order.
      Only `SEGMENT` counts: a ramp is `AUX`-linked to a mainline in another road and belongs in
      neither that one nor nowhere.
    * **Split a collection holding more than one corridor.** `point_model.road_corridors` is the
      rule -- a junction gap is still one street, a stretch joined by nothing is a second road.
      The corridor holding the lowest-named point keeps the collection; the rest move out. A
      corridor something `AUX`-links INTO is named `<road>_ramp`, because that is what it is and
      it is the way this happens: a ramp grown with `Extend Road` off its mainline instead of
      `New Road`, that being the gesture already under your hand.

    Nothing is deleted and no link is touched -- links are object pointers, so they survive a
    move. This is filing, not authoring."""
    bl_idname = "rka.tidy_roads"
    bl_label = "Tidy Roads"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        moved, split = 0, 0

        # -- 1. mis-filed points ------------------------------------------------------------------
        for o in [p for c in pm.road_collections() for p in points_in(c)]:
            here = collection_of(o)
            if here is None:
                continue
            segs = [l.target for l in o.rka_pt.links
                    if l.type == pm.LINK_SEGMENT and l.target is not None]
            if not segs or any(collection_of(t) is here for t in segs):
                continue
            homes = {collection_of(t) for t in segs}
            homes.discard(None)
            if len(homes) != 1:
                continue                     # joins two roads: the artist has to say which
            dst = homes.pop()
            anchor = min((t for t in segs if collection_of(t) is dst), key=lambda t: t.name)
            here.objects.unlink(o)
            dst.objects.link(o)
            _renumber(dst, after=anchor, inserted=o)
            _renumber(here)
            moved += 1

        # -- 2. collections holding more than one corridor -----------------------------------------
        net = pm.read_network()
        by_uid = {p.rka_pt.uid: p for c in pm.road_collections() for p in points_in(c)}
        for coll in list(pm.road_collections()):
            road = net.roads.get(coll.name)
            if road is None:
                continue
            corridors = pm.road_corridors(net, road)
            if len(corridors) < 2:
                continue
            aux_targets = {t for _m, t in net.aux_pairs()}
            for k, corridor in enumerate(corridors[1:], start=1):
                objs = [by_uid[u] for u in corridor if u in by_uid]
                if not objs:
                    continue
                stem = "%s_ramp" % coll.name if any(u in aux_targets for u in corridor) \
                    else "%s_%d" % (coll.name, k + 1)
                name, n = stem, 1
                while pm._local(bpy.data.collections, name) is not None:
                    n += 1
                    name = "%s_%d" % (stem, n)
                # NOT `bpy.ops.rka.split_road`: that operator reads the SELECTION, and driving
                # it from here would mean stomping on the artist's selection once per corridor.
                # Both write the same thing; only the source of the point list differs.
                dst = road_collection(name)
                dst.rka_road.is_road = True
                dst.rka_road.name = dst.name
                for fname, _k, _d in pm.ROAD_FIELDS:
                    if fname != "name":
                        setattr(dst.rka_road, fname, getattr(coll.rka_road, fname))
                for fname, _k, _d in pm.POINT_FIELDS:
                    setattr(dst.rka_road.base, fname, getattr(coll.rka_road.base, fname))
                for o in objs:
                    coll.objects.unlink(o)
                    dst.objects.link(o)
                _renumber(dst)
                split += 1
            _renumber(coll)

        # -- 3. the leavings ------------------------------------------------------------------------
        # An empty road collection and a childless `JCT_*` still own their NAMES, and names are
        # global -- so the next road or crossing with that name is created as `<name>.001` and
        # every lookup by name finds the dead one instead. Exactly the defect that made a second
        # `Add Sample Network` fail. It is a whole-scene tidy with no selection, which is what this
        # operator is for.
        empty_colls, dead_jcts = clear_debris()

        if not (moved or split or empty_colls or dead_jcts):
            self.report({'INFO'}, "every point is already in the right road")
            return {'CANCELLED'}
        bits = ["%d point(s) re-filed" % moved, "%d corridor(s) split into their own road" % split]
        if empty_colls:
            bits.append("%d empty road collection(s) removed" % empty_colls)
        if dead_jcts:
            bits.append("%d junction parent(s) with no arms removed" % dead_jcts)
        self.report({'INFO'}, ", ".join(bits))
        return {'FINISHED'}


class RKA_OT_jump_to_point(Operator):
    """Select and activate a point by name -- the Connections list's way of walking the graph"""
    bl_idname = "rka.jump_to_point"
    bl_label = "Go To Point"
    bl_options = {'REGISTER', 'UNDO'}

    target: StringProperty(default="")

    def execute(self, context):
        obj = bpy.data.objects.get(self.target)
        if obj is None or not is_point(obj):
            self.report({'ERROR'}, "no such road point: %s" % self.target)
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {'FINISHED'}


class RKA_OT_align_tangent(Operator):
    """Rotate the selected points so local +Y follows the road, and switch them to MANUAL

    The anti-footgun for MANUAL mode. A fresh Empty has identity rotation, so its +Y is world +Y;
    flipping a point on an east-west road to MANUAL without this would snap the road 90 degrees
    the instant you did it. Running this first makes the switch a NO-OP -- the road does not move
    -- and every subsequent rotation is a deliberate bend."""
    bl_idname = "rka.align_tangent"
    bl_label = "Face Road (Manual)"
    bl_options = {'REGISTER', 'UNDO'}

    #: Off = align the facing but leave the mode alone (useful to re-straighten a MANUAL point).
    set_manual: BoolProperty(default=True)

    def execute(self, context):
        sel = selected_points(context)
        if not sel:
            self.report({'ERROR'}, "select at least one road point")
            return {'CANCELLED'}
        net = pm.read_network(context.scene)
        # ONE owner of "which way does this station face" -- `point_profile.chain_facings`, which
        # forces AUTO before taking the tangents so re-running this on an already-MANUAL point
        # re-straightens it instead of handing back its own current rotation.
        want = pp.chain_facings(net)
        n = 0
        for o in sel:
            d = want.get(o.rka_pt.uid)
            if d is None:
                continue
            pm.face_matrix(o, d)
            # Re-stamp, ALWAYS. The baseline is what tells a hand rotation apart from a stale
            # arrow, so leaving the old one here would make the very next read see the road's own
            # direction as "the artist rotated this".
            pm.stamp_baseline(o, d)
            if self.set_manual:
                o.rka_pt.tangent_mode = pm.MANUAL
            n += 1
        self.report({'INFO'}, "%d point(s) now face the road" % n)
        return {'FINISHED'} if n else {'CANCELLED'}


def _junction_name():
    used = {c.name for c in bpy.data.objects}
    i = 1
    while ((JCT_PREFIX + "%04d") % i) in used:
        i += 1
    return (JCT_PREFIX + "%04d") % i


def make_junction(context, points, fillet_radius=6.0):
    """N points -> one pad: the JCT_* parent at the centroid, the roles, and the FULL clique.

    Extracted so `Make Intersection` and the sample-network builder cannot disagree about what a
    junction is. Everything subtle about it lives here once: the parent's locked transform, the
    stale-`matrix_world` ordering, `matrix_parent_inverse`, and writing the complete clique."""
    centre = Vector((0.0, 0.0, 0.0))
    for o in points:
        centre += o.matrix_world.translation
    centre /= len(points)

    jct = bpy.data.objects.new(_junction_name(), None)
    jct.empty_display_type = 'SPHERE'
    jct.empty_display_size = 3.0
    jct.location = centre
    # SCALE IS LOCKED, and so is rotation OUT OF PLANE -- but Z is not.
    #
    # Both used to be locked, under one justification: "a mouth's width is its lane count -- not
    # something a transform may quietly restate." That is exactly right about SCALE and does not
    # transfer to rotation. A mouth's DIRECTION is already a transform: the authored facing
    # (`point_model.station_axis`), which 8f made the single owner of an arm's direction precisely
    # so that rotating a mouth turns its cap, its fillets and its turn paths. Turning the whole
    # crossing is that same gesture one level up, and the model already supports it -- `facing_of`
    # reads `matrix_world`, so all N arms turn together and every derived thing follows with no new
    # code. X/Y stay locked because the pad is solved in plan: tilting it has no meaning.
    jct.lock_rotation = (True, True, False)
    jct.lock_scale = (True, True, True)
    _ensure_collection(pm.JUNCTIONS, ensure_roots()).objects.link(jct)
    # `matrix_world` is STALE until the depsgraph updates, and the next lines read the parent's.
    # Never read a world position in the same pass that created or moved its parent.
    context.view_layer.update()

    for o in points:
        o.rka_pt.role = pm.INTERSECTION
        o.rka_pt.fillet_radius = fillet_radius
        world = o.matrix_world.copy()
        o.parent = jct
        # `obj.parent = x` does NOT set this, and `parent_set(keep_transform=True)` needs a
        # context override headless. Without it every mouth jumps by the parent's offset.
        o.matrix_parent_inverse = jct.matrix_world.inverted()
        o.matrix_world = world
    # The FULL clique, written here once. A component that is not a clique builds one pad as two
    # overlapping ones, and the gate reports it -- so the gesture must never leave one out.
    for i, a in enumerate(points):
        for b in points[i + 1:]:
            link_objects(a, b, pm.LINK_JUNCTION)
    # A no-op right here -- the Empty was just placed at that centroid. It runs anyway so that ONE
    # function is the answer to "where does the handle sit", rather than this line being a second
    # copy of it that the two can drift apart from. Which is exactly what happened.
    recentre_junction(jct, context)
    return jct


def junction_centre(jct):
    """The live centroid of a `JCT_*`'s mouths, in world space, or None if it has none."""
    kids = [c for c in jct.children if is_point(c)]
    if not kids:
        return None
    acc = Vector((0.0, 0.0, 0.0))
    for c in kids:
        acc += c.matrix_world.translation
    return acc / len(kids)


def junction_drift(jct):
    """How far the handle currently sits from the centre it should be on, in metres."""
    centre = junction_centre(jct)
    return 0.0 if centre is None else (jct.matrix_world.translation - centre).length


def recentre_junction(jct, context=None):
    """Move the `JCT_*` Empty's origin onto the LIVE centre of its mouths, moving no mouth.

    THE HANDLE MUST SIT WHERE THE THING IT HANDLES IS. `make_junction` set this origin to the
    mouths' centroid once, at creation, and nothing ever re-derived it -- while
    `point_solve.JunctionSolve.centre` recomputes that centroid every solve and is what the pad,
    the fillets, the turn paths and the export all use. Two owners of "where is this junction", one
    of them frozen. Measured on the sample network: dragging a single mouth 42 m to widen its
    approach left the Empty **10.44 m** from the real centre, so G and R pivoted around a point
    with nothing there -- the artist's report, exactly. `Auto Setback` makes it worse by design,
    since it moves every unlocked mouth, so the first thing the recommended workflow does after
    `Make Intersection` slides the centre out from under the handle.

    No geometry moves: the solve never reads this Empty's position. Only the grip changes.

    Moving a parent moves its children, so each mouth's world transform is captured BEFORE and
    restored after -- the same dance `make_junction` performs when it parents, and with the same
    trap: `matrix_world` is stale until the depsgraph updates, so the parent inverse must be taken
    after `view_layer.update()` and never in the same breath as the move.

    Returns the distance the handle travelled."""
    kids = [c for c in jct.children if is_point(c)]
    centre = junction_centre(jct)
    if centre is None:
        return 0.0
    moved = (jct.matrix_world.translation - centre).length
    if moved <= 1e-6:
        return 0.0
    worlds = [c.matrix_world.copy() for c in kids]
    jct.matrix_world.translation = centre
    view = getattr(context or bpy.context, "view_layer", None)
    if view is not None:
        view.update()
    for c, w in zip(kids, worlds):
        c.matrix_parent_inverse = jct.matrix_world.inverted()
        c.matrix_world = w
    return moved


def recentre_all_junctions(context=None):
    """Every `JCT_*` in the file. `(count moved, worst drift)`."""
    n, worst = 0, 0.0
    for o in list(bpy.data.objects):
        if o.parent is None and o.name.startswith("JCT_") and o.type == 'EMPTY':
            d = recentre_junction(o, context)
            if d > 1e-6:
                n += 1
                worst = max(worst, d)
    return n, worst


class RKA_OT_sync_facings(Operator):
    """Point every AUTO arrow along its road, and adopt any point you have hand-rotated

    Runs by itself as part of Build; this is the button for doing it without building."""
    bl_idname = "rka.sync_facings"
    bl_label = "Follow Road (Auto)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        promoted, refaced = sync_facings(context.scene)
        if promoted:
            self.report({'INFO'}, "%d rotated point(s) adopted as MANUAL: %s"
                        % (len(promoted), ", ".join(promoted[:4])))
        self.report({'INFO'}, "%d arrow(s) re-faced, %d adopted" % (len(refaced), len(promoted)))
        return {'FINISHED'}


class RKA_OT_make_intersection(Operator):
    """N selected points -> one pad: the full JUNCTION clique plus a JCT_* parent at the centroid"""
    bl_idname = "rka.make_intersection"
    bl_label = "Make Intersection"
    bl_options = {'REGISTER', 'UNDO'}

    fillet_radius: FloatProperty(default=6.0)

    def execute(self, context):
        sel = selected_points(context)
        if len(sel) < 2:
            self.report({'ERROR'}, "select at least 2 points")
            return {'CANCELLED'}
        jct = make_junction(context, sel, self.fillet_radius)
        context.view_layer.objects.active = jct
        self.report({'INFO'}, "%s: %d arms" % (jct.name, len(sel)))
        return {'FINISHED'}


# ------------------------------------------------------------------------------- ramps

def align_ramp_point(net, main_uid, ramp_uid, obj):
    """Put one ramp mouth where `point_solve.ramp_target` says it belongs, FACING the mainline.

    Two moves, not one, and the second is the one that was missing. Translating the point onto the
    gore line makes the two bands touch; facing it down the mainline makes them share a CUT PLANE,
    which is what an aligned edge means for a swept cross-section. Without it the ramp's section is
    cut at the ramp's own heading, the two bands meet at a single vertex, and the join reads as a
    road stuck on the side of another road -- which is exactly what it is.

    The facing is pinned MANUAL and its baseline re-stamped, so `sync_facings` will not sweep it
    back to the chain direction on the next Build, and the arrow the artist sees is the frame the
    solver uses. The divergence is then authored at the NEXT point, by rotating it.

    "Down the mainline" is SIGNED -- `point_solve.ramp_facing` owns the sign, because a ramp whose
    lanes run against its chain has to face the other way or the curve leaves the mouth backwards
    (8j)."""
    got = psolve.ramp_target(net, main_uid, ramp_uid)
    if got is None:
        return False
    want, ax, _side = got
    ax = psolve.ramp_facing(net, main_uid, ramp_uid) or ax
    d = Vector((ax[0], ax[1], 0.0))
    pm.face_matrix(obj, d)
    obj.rka_pt.tangent_mode = pm.MANUAL
    pm.stamp_baseline(obj, d)
    obj.matrix_world.translation = Vector(want)
    return True


class RKA_OT_align_ramp_to_aux(Operator):
    """Put the ramp mouth on the mainline's gore line and face it down the mainline -- no pad"""
    bl_idname = "rka.align_ramp_to_aux"
    bl_label = "Align Ramp To Aux"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        net = pm.read_network()
        by_uid = {}
        for coll in pm.road_collections():
            for o in points_in(coll):
                by_uid[o.rka_pt.uid] = o
        moved = 0
        sel = {o.rka_pt.uid for o in selected_points(context)}
        for main_uid, ramp_uid in net.aux_pairs():
            if sel and not (sel & {main_uid, ramp_uid}):
                continue
            obj = by_uid.get(ramp_uid)
            if obj is not None and align_ramp_point(net, main_uid, ramp_uid, obj):
                moved += 1
        if not moved:
            self.report({'WARNING'}, "no aux pair to align -- Make Ramp writes the AUX link, and "
                                     "the mainline point needs aux_fwd (or aux_bwd) >= 1")
            return {'CANCELLED'}
        self.report({'INFO'}, "aligned %d ramp point(s)" % moved)
        return {'FINISHED'}


class RKA_OT_make_ramp(Operator):
    """Mainline point + ramp point -> aux slot, AUX link, and an align in one gesture"""
    bl_idname = "rka.make_ramp"
    bl_label = "Make Ramp"
    bl_options = {'REGISTER', 'UNDO'}

    #: ONE ramp role (`pm.RAMP`). The two legacy spellings are still offered so an old macro or
    #: smoketest that names one keeps working, but nothing reads the difference any more --
    #: exit-or-entrance is derived from the ramp's own chain (`point_model.ramp_is_entrance`).
    role: EnumProperty(items=[(pm.RAMP, "Ramp (auto)", "Direction is derived from the ramp chain"),
                              (pm.RAMP_EXIT, "Exit (legacy)", ""),
                              (pm.RAMP_ENTRY, "Entry (legacy)", "")],
                       default=pm.RAMP)
    aux_lanes: bpy.props.IntProperty(default=1, min=1)
    align: BoolProperty(default=True)
    carriageway: EnumProperty(items=[('AUTO', "Auto", "Read off which side the mouth is on"),
                                     ('FWD', "Forward", "Open the slot on the forward lanes"),
                                     ('BWD', "Reverse", "Open the slot on the reverse lanes")],
                              default='AUTO')

    mainline: StringProperty(default="", description="Object name; blank = whichever of the two "
                                                     "points declares the aux slot")

    def execute(self, context):
        sel = selected_points(context)
        if len(sel) != 2:
            self.report({'ERROR'}, "select the mainline point and the ramp's mouth")
            return {'CANCELLED'}
        named = bpy.data.objects.get(self.mainline) if self.mainline else None
        if named is not None and named not in sel:
            self.report({'ERROR'}, "%s is not one of the two selected points" % named.name)
            return {'CANCELLED'}
        if named is not None:
            main, ramp = named, (sel[0] if sel[1] is named else sel[1])
        else:
            # EITHER ORDER, same rule as the Aux button. The active point breaks a tie and nothing
            # more: an entrance ramp reads "ramp joins road", and insisting the mainline be active
            # made that gesture impossible to express.
            act = context.active_object
            a = act if (act in sel) else sel[0]
            b = sel[0] if sel[1] is a else sel[1]
            main, ramp = resolve_aux_pair(a, b)
            if main is None:
                main, ramp = a, b

        ramp.rka_pt.role = self.role
        # A ramp is one-way by construction (2.1): ONE of the two counts is zero and that IS the
        # declaration. Which one is not assumed -- a ramp already authored `lanes_bwd` (an
        # entrance whose mouth is its run's head, 8i.3) must keep running the way it runs.
        if not ramp.rka_pt.lanes_fwd and not ramp.rka_pt.lanes_bwd:
            ramp.rka_pt.lanes_fwd = self.aux_lanes
        elif ramp.rka_pt.lanes_fwd and ramp.rka_pt.lanes_bwd:
            ramp.rka_pt.lanes_bwd = 0
        ramp.rka_pt.profile_mode = pm.OVERRIDE
        # WHICH CARRIAGEWAY, DERIVED FROM WHERE THE MOUTH IS (`point_solve.ramp_carriageway`).
        # This used to write `aux_fwd` unconditionally, which is right for a ramp off the forward
        # side and silently wrong for one off the reverse side -- the slot opened on the opposite
        # carriageway and the ramp was fed by traffic going the other way.
        net = pm.read_network()
        way = self.carriageway
        if way == 'AUTO':
            way = ('FWD' if psolve.ramp_carriageway(
                net, main.rka_pt.uid, tuple(ramp.matrix_world.translation)) == lp.FWD else 'BWD')
        field = _aux_field(way)
        setattr(main.rka_pt, field, max(getattr(main.rka_pt, field), self.aux_lanes))
        unlink_objects(main, ramp)
        link_objects(main, ramp, pm.LINK_AUX)
        if self.align:
            bpy.ops.rka.align_ramp_to_aux()
        self.report({'INFO'}, "%s: %s -> %s" % (self.role, main.name, ramp.name))
        return {'FINISHED'}


# ------------------------------------------------------------------------------- edit + select

def _aux_field(carriageway):
    return "aux_fwd" if carriageway == 'FWD' else "aux_bwd"


def open_aux_slot(net, main, lanes, carriageway, entrance):
    """Open an aux slot on `main` AND on the stations the taper standard needs it on.

    `(stations, metres)`.

    A ONE-CLICK GESTURE MUST NOT LEAVE THE GATE RED (8j). Writing `aux_fwd = 1` on the mouth alone
    is the whole aux slot as far as the data model is concerned, and it is wrong on the ground:
    the slot then appears out of nothing over whatever gap happens to precede the mouth, and
    `check_tapers` says so -- correctly, because a lane that opens in 20 m at 80 km/h is not a
    deceleration lane. (The pair AT the mouth is exempt -- a departing lane is not a merging one,
    8f.4 -- but the pair before it is not, and that is the one that reports.)

    So the slot is opened back along the run to the first station whose OWN next span is long
    enough to hold the taper `point_validate.taper_min_length` asks for. An aux count is an
    integer, so the slot goes from zero to full width across exactly ONE span -- the one between
    the last station that carries it and the first that does not -- and it is that span, not the
    total distance walked, that the gate measures. Opening the slot on more stations does not
    lengthen the taper; it only moves which span the change lands on. The length itself is derived
    from the design speed and the road's own `taper_factor`, the same number `check_tapers`
    measures against, so the gesture and the gate cannot disagree.

    Which way "back" is depends on both the carriageway (FWD travels with increasing index, BWD
    against it) and the job: an exit needs its slot UPSTREAM of the mouth, an entrance downstream.

    `(stations, span, want)`. `span` is short only when the run runs out before a long enough one
    is found -- reported rather than hidden, because the gate is about to say so."""
    coll = collection_of(main)
    field = _aux_field(carriageway)
    res = net.resolved(main.rka_pt.uid)
    width = lanes * (res.lane_width if res else main.rka_pt.lane_width)
    speed = res.design_speed if res else main.rka_pt.design_speed
    factor = getattr(coll.rka_road, "taper_factor", 1.0) if coll else 1.0
    want = pv.taper_min_length(width, speed, factor)

    pts = points_in(coll) if coll else [main]
    run = next((r for r in _object_runs(net, coll, pts) if main in r), [main])
    step = -1 if (carriageway == 'FWD') != bool(entrance) else 1
    chain, j, span = [main], run.index(main), 0.0
    while True:
        setattr(chain[-1].rka_pt, field, max(getattr(chain[-1].rka_pt, field), lanes))
        k = j + step
        if not (0 <= k < len(run)):
            span = 0.0                 # the run ENDS here: the slot is open at its very end
            break
        span = (run[k].matrix_world.translation - run[j].matrix_world.translation).length
        if span >= want:
            break                      # this span holds the taper -- stop, do not open across it
        chain.append(run[k])
        j = k
    return len(chain), span, want


def _object_runs(net, coll, pts):
    """`point_model.road_runs` as OBJECTS -- the run is what a taper may be measured along, and a
    junction gap is not part of it."""
    if coll is None or coll.name not in net.roads:
        return [pts]
    by_uid = {o.rka_pt.uid: o for o in pts}
    out = []
    for run in pm.road_runs(net, net.roads[coll.name]):
        objs = [by_uid[u] for u in run if u in by_uid]
        if objs:
            out.append(objs)
    return out or [pts]


class RKA_OT_branch_ramp(Operator):
    """Start a NEW road leaving (or joining) this point -- the gesture for a mid-corridor ramp

    THE ANSWER TO "I CANNOT EXTEND FROM THE MIDDLE OF A HIGHWAY" (8j). `Extend Road` refuses an
    interior point and is right to: "extend" has no meaning in the middle of a chain, and picking
    an end for the artist is how a road silently grows the wrong way. But the thing the artist was
    actually trying to do -- start a ramp HERE, two thirds of the way along the highway -- had no
    gesture at all. It took `New Road` at a guessed position, a lane count, a role, a one-way
    declaration, an aux slot, `Aux`, `Align Ramp To Aux`, and then a station bent outboard by hand,
    with a red gate at every step in between.

    This is that sequence, in the order it has to happen, with every number that CAN be derived
    derived:

    * the aux slot is opened over the length `check_tapers` asks for (`open_aux_slot`) -- not just
      on the mouth, which is the shape that makes the gate red the moment the button is released;
    * the mouth is placed and faced by `Align Ramp To Aux`, the one owner of where a mouth belongs;
    * the second station is bent OUTBOARD, which is the direction `point_solve.ramp_target` says
      the aux slot is on. A ramp that leaves and then crosses back over the carriageway builds no
      gore at all (`ramp_wrong_side`), and outboard-versus-inboard is not a thing to leave to a
      hand-typed delta.

    It works from ANY point of the corridor, interior included -- that is the whole point -- and
    leaves the ramp's far end selected so `Extend Road` carries straight on from it."""
    bl_idname = "rka.branch_ramp"
    bl_label = "Branch Ramp Here"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(default="", description="New road name; blank = <road>_ramp")
    aux_lanes: bpy.props.IntProperty(default=1, min=1, max=4,
                                     description="Lanes that leave with the ramp")
    carriageway: EnumProperty(items=[('FWD', "Forward", "The aux slot is on the forward lanes"),
                                     ('BWD', "Reverse", "The aux slot is on the reverse lanes")],
                              default='FWD')
    entrance: BoolProperty(default=False, name="Entrance",
                           description="The ramp JOINS this road here instead of leaving it")
    length: FloatProperty(default=80.0, min=1.0,
                          description="How far along the road the ramp's second station sits")
    spread: FloatProperty(default=25.0, min=0.0,
                          description="How far OUTBOARD that station sits -- the divergence")
    drop: FloatProperty(default=0.0, description="Height change over that span")

    def execute(self, context):
        main = context.active_object
        coll = collection_of(main) if main else None
        if coll is None or not getattr(main.rka_pt, "is_point", False):
            self.report({'ERROR'}, "no active road point")
            return {'CANCELLED'}
        net = pm.read_network()
        stations, span, want = open_aux_slot(net, main, self.aux_lanes, self.carriageway,
                                             self.entrance)

        base = self.name or "%s_ramp" % coll.name
        name = base
        n = 1
        while pm._local(bpy.data.collections, name) is not None:
            n += 1
            name = "%s%d" % (base, n)
        # THE LANE DIRECTION IS DECIDED BEFORE THE ALIGN, NOT AFTER IT. `ramp_target` reads the
        # ramp's own paved extents to work out which of its two edges lands on the gore line, and
        # a one-way road's extents are entirely on one side of its divide -- so flipping FWD to
        # BWD afterwards moves the band a full carriageway width and leaves the mouth exactly that
        # far off the line it was just snapped to (7 m, and `ramp_edge_residual` said so).
        #
        # A ramp is one-way BY CONSTRUCTION (2.1): the zero side IS the one-way declaration. An
        # ENTRANCE runs INTO the mainline, so its lanes run toward the mouth -- the mouth is its
        # run's head and the lanes are REVERSE, which is exactly the pair of facts
        # `point_model.ramp_is_entrance` reads (8i.3). Nothing declares the direction twice.
        fwd, bwd = (0, self.aux_lanes) if self.entrance else (self.aux_lanes, 0)
        bpy.ops.rka.new_road(name=name,
                             x=main.matrix_world.translation.x,
                             y=main.matrix_world.translation.y,
                             z=main.matrix_world.translation.z,
                             lanes_fwd=fwd, lanes_bwd=bwd,
                             lane_width=net.resolved(main.rka_pt.uid).lane_width,
                             median_width=0.0, road_class="ramp",
                             design_speed=max(30.0, net.resolved(main.rka_pt.uid).design_speed
                                              - 20.0))
        ramp_coll = pm._local(bpy.data.collections, name)
        ramp_coll.rka_road.ped_access = getattr(coll.rka_road, "ped_access", False)
        mouth = points_in(ramp_coll)[0]
        mouth.rka_pt.role = pm.RAMP
        mouth.rka_pt.lanes_fwd, mouth.rka_pt.lanes_bwd = fwd, bwd
        mouth.rka_pt.profile_mode = pm.OVERRIDE
        link_objects(main, mouth, pm.LINK_AUX)

        _select(context, mouth)
        bpy.ops.rka.align_ramp_to_aux()

        # OUTBOARD IS DERIVED. `ramp_target`'s `side` is which way the aux slot lies off the
        # mainline's own cross-section, and it is the only thing that separates a ramp that leaves
        # from one that drives back through the lanes it is leaving (`ramp_wrong_side`).
        net = pm.read_network()
        got = psolve.ramp_target(net, main.rka_pt.uid, mouth.rka_pt.uid)
        if got is None:
            self.report({'ERROR'}, "%s declares no aux slot to branch from" % main.name)
            return {'CANCELLED'}
        _want, ax, side = got
        along = Vector((ax[0], ax[1], 0.0))
        if (self.carriageway == 'BWD') != bool(self.entrance):
            along = -along
        outward = Vector((-ax[1] * side, ax[0] * side, 0.0))
        off = along * self.length + outward * self.spread + Vector((0.0, 0.0, self.drop))

        _select(context, mouth)
        bpy.ops.rka.extend_road(use_delta=True, dx=off.x, dy=off.y, dz=off.z)
        far = context.active_object
        sync_facings(context.scene)
        _select(context, far)
        short = span < want - 1e-6
        self.report({'WARNING'} if short else {'INFO'},
                    "%s: %d-lane %s off %s; aux slot on %d station(s), opens over %.0f m%s -- "
                    "Extend Road carries on from %s"
                    % (name, self.aux_lanes, "entrance" if self.entrance else "exit", main.name,
                       stations, span, (" (wants %.0f)" % want) if short else "", far.name))
        return {'FINISHED'}


class RKA_OT_delete_point(Operator):
    """Delete selected points, stripping INBOUND links first"""
    bl_idname = "rka.delete_point"
    bl_label = "Delete Point"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        sel = selected_points(context)
        if not sel:
            return {'CANCELLED'}
        doomed = set(sel)
        # Strip inbound links FIRST. `bpy.data.objects.remove()` would null the pointers, but a
        # point that is merely UNLINKED from its collection lives on as a zero-collection zombie
        # held by its referrers -- invisible in the outliner and surviving Purge Orphans (1.2b).
        for coll in pm.road_collections():
            for o in points_in(coll):
                if o in doomed:
                    continue
                for i in range(len(o.rka_pt.links) - 1, -1, -1):
                    if o.rka_pt.links[i].target in doomed:
                        o.rka_pt.links.remove(i)
        colls = {collection_of(o) for o in sel if collection_of(o) is not None}
        for o in sel:
            bpy.data.objects.remove(o, do_unlink=True)
        for c in colls:
            _renumber(c)
        self.report({'INFO'}, "deleted %d point(s)" % len(sel))
        return {'FINISHED'}


class RKA_OT_select_road(Operator):
    """Select every point of the active point's road"""
    bl_idname = "rka.select_road"
    bl_label = "Select Road"
    bl_options = {'REGISTER', 'UNDO'}

    include_junction_members: BoolProperty(
        default=False,
        description="Junction members are positioned by their JCT_* parent; dragging them with "
                    "the road tears the pad apart")

    def execute(self, context):
        coll = collection_of(context.active_object) if context.active_object else None
        if coll is None:
            return {'CANCELLED'}
        n = 0
        for o in points_in(coll):
            if o.rka_pt.role == pm.INTERSECTION and not self.include_junction_members:
                continue
            o.select_set(True)
            n += 1
        self.report({'INFO'}, "%d point(s) of %s" % (n, coll.name))
        return {'FINISHED'}


class RKA_OT_select_junction(Operator):
    """Select every arm of the active junction"""
    bl_idname = "rka.select_junction"
    bl_label = "Select Junction"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        act = context.active_object
        if act is None:
            return {'CANCELLED'}
        # Either the JCT_* parent itself, or any one of its arms.
        jct = act.parent if (act.rka_pt.is_point and act.parent is not None) else act
        n = 0
        for o in bpy.data.objects:
            if getattr(o, "rka_pt", None) is not None and o.rka_pt.is_point and o.parent is jct:
                o.select_set(True)
                n += 1
        return {'FINISHED'} if n else {'CANCELLED'}


#: The mask defaults to NOTHING and the panel prints the field list before you press it. The old
#: brush's failure mode was a scene-level stamp with eight toggles ticked by default that silently
#: rewrote the median while you meant to change a lane count (4.2).
_MASK_GROUPS = {
    'LANES': ("lanes_fwd", "lanes_bwd", "aux_fwd", "aux_bwd", "aux_side",
              "drop_side_fwd", "drop_side_bwd"),
    'WIDTH': ("lane_width", "shoulder_left_width", "shoulder_right_width",
              "parking_left_width", "parking_right_width"),
    'MEDIAN': ("median_width", "median_style"),
    'SIDES': ("left_kerb_height", "left_walk_width", "right_kerb_height", "right_walk_width"),
    'STRUCTURE': ("deck_thickness", "pillar_spacing", "pillar_skip", "pillar_offset"),
    'JUNCTION': ("fillet_radius", "allow_cross", "allow_uturn", "traffic_light"),
}


class RKA_OT_apply_cross_section(Operator):
    """Copy chosen cross-section groups from the ACTIVE point to every other selected point"""
    bl_idname = "rka.apply_cross_section"
    bl_label = "Apply Cross-Section To Selection"
    bl_options = {'REGISTER', 'UNDO'}

    groups: EnumProperty(items=[(k, k.title(), "") for k in sorted(_MASK_GROUPS)],
                         options={'ENUM_FLAG'}, default=set())

    def execute(self, context):
        src = context.active_object
        sel = [o for o in selected_points(context) if o is not src]
        if src is None or not sel:
            self.report({'ERROR'}, "need an active point and at least one other selected")
            return {'CANCELLED'}
        if not self.groups:
            self.report({'ERROR'}, "no field group chosen -- the mask defaults to nothing on "
                                   "purpose; tick what you mean to change")
            return {'CANCELLED'}
        fields = [f for g in self.groups for f in _MASK_GROUPS[g]]
        for o in sel:
            for f in fields:
                setattr(o.rka_pt, f, getattr(src.rka_pt, f))
            o.rka_pt.profile_mode = pm.OVERRIDE
        self.report({'INFO'}, "%d field(s) -> %d point(s)" % (len(fields), len(sel)))
        return {'FINISHED'}


# ------------------------------------------------------------------------------- the record

def default_record_path():
    """`<stem>.roads.json` beside the .blend -- the same sibling-sidecar convention
    `.lanekit.json` and `.seam.json` already use."""
    import os
    blend = bpy.data.filepath
    if not blend:
        return ""
    return os.path.splitext(blend)[0] + ".roads.json"


def apply_network(net):
    """NetworkData -> Empties. The record is the source of truth and this is the projection, so it
    REPLACES what is in ROAD_MANAGER rather than merging into it -- a merge would silently keep an
    object the record no longer mentions, which is the exact drift 1.2c exists to prevent."""
    for coll in pm.road_collections():
        for o in points_in(coll):
            bpy.data.objects.remove(o, do_unlink=True)
    made = {}
    for name in sorted(net.roads):
        r = net.roads[name]
        coll = road_collection(name)
        coll.rka_road.is_road = True
        for n, _k, _d in pm.ROAD_FIELDS:
            setattr(coll.rka_road, n, getattr(r, n))
        coll.rka_road.name = coll.name
        for n, _k, _d in pm.POINT_FIELDS:
            setattr(coll.rka_road.base, n, getattr(r.base, n))
        for uid in r.points:
            p = net.points.get(uid)
            if p is None:
                continue
            obj = new_point(coll, p.pos)
            pm.write_point(obj, p, move=True)
            made[uid] = obj
    for uid, obj in made.items():
        for l in net.points[uid].links:
            if l.target in made:
                link_objects(obj, made[l.target], l.type, symmetric=False)
    return made


class RKA_OT_save_record(Operator):
    """Write the authored roads to a git-diffable <stem>.roads.json"""
    bl_idname = "rka.save_record"
    bl_label = "Save Road Record"
    bl_options = {'REGISTER'}

    filepath: StringProperty(default="", subtype='FILE_PATH')

    def execute(self, context):
        path = self.filepath or default_record_path()
        if not path:
            self.report({'ERROR'}, "save the .blend first, or pass filepath")
            return {'CANCELLED'}
        net = pm.read_network()
        pm.save_network(net, path)
        self.report({'INFO'}, "%d road(s), %d point(s) -> %s"
                    % (len(net.roads), len(net.points), path))
        return {'FINISHED'}


class RKA_OT_load_record(Operator):
    """Rebuild the Empties from <stem>.roads.json -- the record is the source of truth"""
    bl_idname = "rka.load_record"
    bl_label = "Load Road Record"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(default="", subtype='FILE_PATH')

    def execute(self, context):
        path = self.filepath or default_record_path()
        if not path:
            self.report({'ERROR'}, "save the .blend first, or pass filepath")
            return {'CANCELLED'}
        net = pm.load_network(path)
        made = apply_network(net)
        self.report({'INFO'}, "%d point(s) from %s" % (len(made), path))
        return {'FINISHED'}


class RKA_OT_validate(Operator):
    """Run the gate. A build that fails the connectivity check is a failed build"""
    bl_idname = "rka.validate"
    bl_label = "Validate Roads"
    bl_options = {'REGISTER'}

    def execute(self, context):
        net = pm.read_network()
        findings = pv.validate(net)
        # uid -> object, so a report the artist can act on names the OBJECT everywhere it names
        # one -- in the subject AND inside the message, which is where most of them are.
        label = net.labels
        for f in findings:
            print("[%s] %s" % (f.severity, pv.describe(f, label)))
        errs = pv.errors(findings)
        for f in errs[:5]:
            self.report({'ERROR'}, pv.describe(f, label))
        if net.uid_repairs:
            self.report({'WARNING'}, "%d uid collision(s) repaired on read (Shift+D on a single "
                                     "point)" % len(net.uid_repairs))
        if not errs:
            self.report({'INFO'}, "gate GREEN -- %d road(s), %d point(s), %d warning(s)"
                        % (len(net.roads), len(net.points), len(findings)))
        return {'FINISHED'}


class RKA_OT_export_lanekit(Operator):
    """Write the Godot `.lanekit.json` v2 sidecar. Refuses if the gate is not green"""
    bl_idname = "rka.export_lanekit"
    bl_label = "Export .lanekit (v2)"
    bl_options = {'REGISTER'}

    filepath: StringProperty(default="", subtype='FILE_PATH')
    force: BoolProperty(default=False, description="Export even with gate errors")

    def execute(self, context):
        import os
        from . import point_export as pe
        path = self.filepath
        if not path:
            blend = bpy.data.filepath
            if not blend:
                self.report({'ERROR'}, "save the .blend first, or pass filepath")
                return {'CANCELLED'}
            path = os.path.splitext(blend)[0] + ".lanekit.json"
        net = pm.read_network()
        errs = pv.errors(pv.validate(net))
        if errs and not self.force:
            # A build that fails the connectivity check is a FAILED build (5). Exporting anyway
            # ships a network whose defects only show up as cars falling through the world.
            for f in errs[:5]:
                self.report({'ERROR'}, pv.describe(f, net.labels))
            self.report({'ERROR'}, "%d gate error(s) -- not exporting" % len(errs))
            return {'CANCELLED'}
        doc = pe.write(net, path)
        self.report({'INFO'}, "%d lane(s), %d junction(s) -> %s"
                    % (len(doc["lanes"]), len(doc["junctions"]), path))
        return {'FINISHED'}


def clear_debris():
    """Remove empty road collections and childless `JCT_*` parents. `(collections, junctions)`.

    THE COMPANION TO "a road point with no collection is debris". These are the leavings of
    deleting points: an empty collection still owns its NAME, and names are global, so the next
    road called `demo_spur` is created as `demo_spur.001` and every lookup by name finds the stale
    one. A childless junction parent is the same kind of leaving -- it still holds `JCT_0001`, so
    the next crossing is `JCT_0002` and the outliner accumulates one dead Empty per press.

    Safe to call at any time: it only ever removes something that holds nothing."""
    colls = 0
    for c in list(pm.road_collections()):
        if pm.point_objects(c):
            continue
        bpy.data.collections.remove(c)
        colls += 1
    jcts = 0
    for o in list(bpy.data.objects):
        if (o.type == 'EMPTY' and o.name.startswith("JCT_") and o.parent is None
                and not [c for c in o.children if is_point(c)]):
            bpy.data.objects.remove(o, do_unlink=True)
            jcts += 1
    return colls, jcts


class RKA_OT_demo_network(Operator):
    """Build a worked example THROUGH THE GESTURES: two streets crossing, an elevated highway
    with a weaving section, a ramp that leaves it and merges into the arterial, and a two-lane
    exit and two-lane entrance sharing one auxiliary lane"""
    bl_idname = "rka.demo_network"
    bl_label = "Add Sample Network"
    bl_options = {'REGISTER', 'UNDO'}

    replace: BoolProperty(default=True, name="Replace",
                          description="Delete existing road points first")

    #: THE ROAD, AS AUTHORED. One table so the sample is a thing you EDIT rather than a function
    #: you read: change a number here and press the button again. `is_loop` on the Road panel is
    #: the one shape this table cannot express -- a ring needs the last point linked back to the
    #: first, which is a `Connect` away once you have the chain.
    def execute(self, context):
        if self.replace:
            doomed = [o for coll in pm.road_collections() for o in points_in(coll)]
            # ...AND every point that is in no collection at all. Such a point is invisible to
            # `read_network`, to the gate and to this sweep, but it is still in the .blend, still
            # in the outliner, and still numbered -- which is why a second press of this button
            # produced `demo_hwy_p005..p009` beside an unreachable `p000..p004` in a user's file.
            # A road point with no collection is not authored data; it is debris.
            doomed += [o for o in bpy.data.objects
                       if getattr(o, "rka_pt", None) is not None and o.rka_pt.is_point
                       and not o.users_collection]
            for o in doomed:
                bpy.data.objects.remove(o, do_unlink=True)
            # ...AND THE COLLECTIONS AND JUNCTION PARENTS THEY LEFT BEHIND, which is what made a
            # second press fail (user-reported).
            #
            # Removing the points empties every road collection but does not remove one, so
            # `demo_spur` was still there on the next press -- and object and collection NAMES ARE
            # GLOBAL, so the freshly branched spur became `demo_spur.001` while `_local_road
            # ("demo_spur")` kept handing back the stale empty one. The sample then tried to
            # extend a road with no points in it (`IndexError`), left the network half-built, and
            # the Build after it failed the gate on three unaligned ramp mouths -- with the FIRST
            # iteration's geometry still standing in the viewport, so it read as "the roads built
            # but the markings did not".
            #
            # Same rule as the line above, one level up: a road collection with no points is not
            # authored data, it is debris. So is a `JCT_*` parent with no arms left.
            clear_debris()

        # EVERY ROAD BELOW IS BUILT BY PRESSING THE BUTTONS AN ARTIST PRESSES. It used to write
        # the scene with the internal helpers, which made it a fixture for the DATA MODEL: the
        # sample could be perfect while `Extend Road` grew a road backwards and `Aux` refused half
        # the ramps in the world. Driving the operators means the button that teaches the gestures
        # is also the button that exercises them, and the smoketest that presses it covers both.
        def road(name, x, y, z, base=None, **kw):
            bpy.ops.rka.new_road(name=name, x=x, y=y, z=z,
                                 lanes_fwd=kw.pop("lanes_fwd", 2),
                                 lanes_bwd=kw.pop("lanes_bwd", 2),
                                 lane_width=kw.pop("lane_width", 3.5),
                                 median_width=kw.pop("median_width", 1.0),
                                 road_class=kw.pop("road_class", "street"),
                                 design_speed=kw.pop("design_speed", 50.0))
            coll = _local_road(name)
            for k, v in kw.items():
                setattr(coll.rka_road, k, v)
            for k, v in (base or {}).items():
                setattr(coll.rka_road.base, k, v)
            return coll

        def extend(name, *deltas):
            for d in deltas:
                _select(context, points_in(_local_road(name))[-1])
                bpy.ops.rka.extend_road(use_delta=True, dx=d[0], dy=d[1], dz=d[2])
            return points_in(_local_road(name))

        # ---- two streets, crossing ------------------------------------------------------------
        main = road("demo_main", 0.0, 0.0, 0.0, lanes_fwd=2, lanes_bwd=2, median_width=1.0,
                    road_class="arterial", design_speed=50.0, zone_id="Demo", ped_access=True,
                    base=dict(left_walk_width=3.0, right_walk_width=3.0))
        extend("demo_main", (120, 0, 0), (116, 0, 0), (28, 0, 0), (216, 0, 0),
               (220, 0, 0), (160, 0, 0), (120, 0, 0), (140, 0, 0))
        # ...AND FROM THE HEAD. The one gesture the old sample could not have caught being broken:
        # `Extend Road` appended whatever point was active, so extending `_p000` placed the new
        # station forward, back down the road, and named it to sort at the far end (8i.1).
        _select(context, points_in(main)[0])
        bpy.ops.rka.extend_road(use_delta=True, dx=-120.0, dy=0.0, dz=0.0)
        mp = points_in(main)        # x = -120 0 120 236 264 480 700 860 980 1120

        cross = road("demo_cross", 250.0, -150.0, 0.0, lanes_fwd=1, lanes_bwd=1, median_width=0.0,
                     road_class="street", design_speed=40.0, zone_id="Demo", ped_access=True,
                     barrier_height=0.0, base=dict(left_walk_width=2.5, right_walk_width=2.5))
        cp = extend("demo_cross", (0, 136, 0), (0, 28, 0), (0, 136, 0))

        # A crossing does NOT split either street: the two mainline mouths stay adjacent members
        # of ONE chain, joined by the pad rather than by carriageway -- so the SEGMENT link
        # between them comes out before the JUNCTION clique goes in.
        for pair in ((mp[3], mp[4]), (cp[1], cp[2])):
            _select(context, *pair)
            bpy.ops.rka.disconnect_selected()
        _select(context, mp[3], mp[4], cp[1], cp[2])
        bpy.ops.rka.make_intersection(fillet_radius=6.0)

        # ---- an elevated highway with a weaving section ----------------------------------------
        #
        # THREE FORWARD LANES PLUS A FOURTH THAT LEAVES. `lanes_fwd = 3` with `aux_fwd = 1` is a
        # four-lane carriageway whose OUTERMOST lane is the exit lane -- that is the shape this
        # sample exists to teach. The slot opens two stations early (the deceleration length),
        # holds full width to the gore, and CLOSES AT THE GORE because the lane has left with the
        # ramp: a parallel-type exit, not a fifth lane beyond a third.
        hwy = road("demo_hwy", 0.0, 320.0, 14.0, lanes_fwd=3, lanes_bwd=3, median_width=2.0,
                   road_class="expressway", design_speed=80.0, zone_id="Demo", ped_access=False,
                   base=dict(deck_thickness=1.6, pillar_spacing=30.0))
        # The western approach is 400 m long deliberately: at 80 km/h a TWO-lane slot wants 336 m
        # of taper, and a sample that teaches a multi-lane ramp on a 60 m span teaches a red gate.
        hp = extend("demo_hwy", (400, 0, 0), (160, 0, 0), (60, 0, 0), (180, 0, 0),
                    (400, 0, 0), (400, 0, 0), (400, 0, 0))
        # ONE STATION, ONE RAMP OUT AND ONE RAMP IN -- the ordinary half-interchange, and the
        # thing a single `aux_fwd` integer cannot say on its own. `hp[3]` declares a slot on
        # BOTH carriageways: the forward one is the deceleration lane for the ramp that LEAVES
        # (so it opens upstream, at `hp[2]`), the reverse one is the acceleration lane for the
        # ramp that JOINS (so it opens downstream, which on the reverse carriageway is also
        # `hp[2]`). The two are different pavement with different slot ids (`AF0` / `AR0`), and
        # which one a ramp is on is read off which side its mouth sits (8l).
        #
        # This is also 8i.4's case: an auxiliary lane on EACH carriageway over one span. Adding
        # the two sides together demanded 336 m and refused this road; the standard asks 168 m of
        # the widest SINGLE change, because nobody drives both carriageways.
        for p in hp[1:3]:
            p.rka_pt.aux_fwd = 1            # one lane leaves eastbound...
            p.rka_pt.aux_bwd = 2            # ...and TWO join westbound

        # ---- one ramp doing both jobs: it LEAVES the highway and MERGES into the street ---------
        #
        # The same road is an exit at one end and an entrance at the other, and nothing declares
        # which: `point_model.ramp_is_entrance` reads the mouth's place in the ramp's own run and
        # the way its lanes run (8i.3). It is also the mismatched-flank gore (8i.5) -- fenced ramp,
        # kerbed-and-paved arterial -- so the nose at the street end carries the RAMP's wall while
        # the arterial's footway runs on past it.
        # IT LEAVES OUTBOARD AND LOOPS BACK UNDER THE VIADUCT. It used to dive straight across
        # the carriageway it was leaving -- the aux slot is on the +y side and the second station
        # sat at -47 y -- so the ramp's band overlapped the highway's for its whole length, the
        # two edges never parted, and `solve_gore` returned None with nothing to report it. That
        # is the shape `check_ramps`' `ramp_wrong_side` now catches (8j), and the sample was its
        # first hit: a fixture cannot be evidence while it is authored the way the bug is.
        P, Q = hp[2], None                  # the shared stations, named once
        ramp = road("demo_ramp", 560.0, 337.0, 14.0, lanes_fwd=1, lanes_bwd=0, median_width=0.0,
                    road_class="ramp", design_speed=40.0, zone_id="Demo", ped_access=False)
        rp = extend("demo_ramp", (90, 30, -2), (90, -40, -6), (70, -160, -4), (50, -158, -2))

        # ---- ...AND ONE COMING BACK THE OTHER WAY, THROUGH THE SAME TWO STATIONS ---------------
        #
        # ONE RAMP OUT AND ONE RAMP IN, AT EACH OF THEM (8l). Eastbound traffic leaves the
        # expressway here and joins the arterial there; westbound traffic does the reverse. Both
        # ramps are one-way and both hang off `demo_hwy_p003` and `demo_main_p007` -- so each of
        # those stations accepts an EXIT and an ENTRANCE at once.
        #
        # It runs on the WESTBOUND side of both roads, and that is what makes it a straight run
        # rather than a loop: two one-way ramps between the same two points, both travelling the
        # same way, would have to double back. Nothing declares which carriageway it is on either
        # -- `point_solve.ramp_side_of` reads it off which side of the road its mouths sit.
        # ...and it is TWO LANES, which is the other thing this pair carries: `aux_bwd = 2` is
        # 8g.1's case, where a ramp anchored on the outermost slot instead of the whole BLOCK
        # lands half on the carriageway.
        road("demo_ramp_b", 860.0, -9.0, 0.0, lanes_fwd=2, lanes_bwd=0, median_width=0.0,
             road_class="ramp", design_speed=40.0, zone_id="Demo", ped_access=False)
        rb = extend("demo_ramp_b", (-80, -31, 4), (-80, 70, 3), (-60, 150, 3),
                    (20, 110, 3.5), (-100, 18, 0.5))

        # An ENTRANCE's slot opens DOWNSTREAM of its mouth and an EXIT's upstream -- and on
        # opposite carriageways "downstream" is opposite ends of the chain. That is the whole
        # reason all four numbers are stated here rather than derived from one.
        mp[7].rka_pt.aux_fwd = 1            # the eastbound acceleration lane for the merge...
        mp[8].rka_pt.aux_fwd = 1            # ...closing one station later
        mp[7].rka_pt.aux_bwd = 2            # the westbound deceleration lanes for the exit...
        mp[8].rka_pt.aux_bwd = 2            # ...opening one station earlier, going west

        # BOTH ENDS OF BOTH RAMPS ARE WIRED WITH THE RAMP POINT ACTIVE, which is the natural way
        # to read "this ramp joins that road" and which `Make Ramp` refused outright until 8i.2.
        # `align=False`, then one align at the end: where a station has several ramps, where each
        # mouth belongs depends on the others, so they are placed once the whole set is wired.
        for mouth, mainline, n in ((rp[0], P, 1), (rp[-1], mp[7], 1),
                                   (rb[0], mp[7], 2), (rb[-1], P, 2)):
            _select(context, mouth, mainline, active=mouth)
            bpy.ops.rka.make_ramp(aux_lanes=n, align=False)

        # ---- and one more, from the MIDDLE of the highway ---------------------------------------
        #
        # `Author > Ramp > Branch Ramp Here` -- the gesture that did not exist, and the reason it
        # had to (8j): `Extend Road` refuses an interior station, so starting a ramp two thirds of
        # the way along a highway meant `New Road` at a guessed position and seven more steps.
        # It hangs off the arterial's WESTERN run, which carries no other ramp. Both of the
        # highway's carriageways are spoken for at `demo_hwy_p002` -- a run exports ONE lane per
        # slot, so a third ramp anywhere on that run would collide, and `check_aux_slots` says so
        # by name. The crossing is what splits the arterial into two runs, which is the other way
        # out the finding names.
        _select(context, mp[1])
        bpy.ops.rka.branch_ramp(name="demo_spur", aux_lanes=1, carriageway='FWD',
                                entrance=False, length=140.0, spread=45.0, drop=0.0)
        extend("demo_spur", (150, 120, 0), (140, 60, 0))

        # ONE ALIGN AT THE END, for every ramp in the scene: a mouth's place depends on which
        # slot of its station's block it was allocated, and that depends on every ramp there.
        for o in context.selected_objects:
            o.select_set(False)
        bpy.ops.rka.align_ramp_to_aux()

        _select(context, mp[0])
        self.report({'INFO'}, "sample: 6 roads, 1 crossing, a ramp OUT and a ramp IN at each of "
                              "two shared stations, and a spur branched mid-highway -- press "
                              "Build Roads")
        return {'FINISHED'}


class RKA_OT_auto_setback(Operator):
    """Solve the whole clique's stop-line distances and move every UNLOCKED mouth there"""
    bl_idname = "rka.auto_setback"
    bl_label = "Auto Setback"
    bl_options = {'REGISTER', 'UNDO'}

    margin: FloatProperty(name="Margin", default=2.0, min=0.0,
                          description="How far a turn may reach outside the pad before the "
                                      "solve grows the setback")

    def execute(self, context):
        net = pm.read_network()
        by_uid = {}
        for coll in pm.road_collections():
            for o in points_in(coll):
                by_uid[o.rka_pt.uid] = o
        sel = {o.rka_pt.uid for o in selected_points(context)}
        moved = seen = 0
        for uids in net.junction_cliques():
            # WHOLE-CLIQUE, always. The solver's couplings do not survive being applied one mouth
            # at a time -- it takes the max over both corners at a node and clamps per chain
            # jointly. Drag one mouth in isolation and the neighbouring fillet silently stops
            # being tangent. A selection PICKS a clique; it never narrows one.
            if sel and not (sel & set(uids)):
                continue
            for uid, _old, _new in psolve.auto_setback(net, uids, self.margin):
                obj = by_uid.get(uid)
                if obj is None:
                    continue
                pos = net.points[uid].pos
                obj.matrix_world.translation = Vector(pos)
                moved += 1
            for uid in uids:
                if uid in by_uid:
                    by_uid[uid].rka_pt.setback_solved = net.points[uid].setback_solved
            seen += 1
        if not seen:
            self.report({'WARNING'}, "no junction selected -- select a mouth, or nothing, to "
                                     "solve every pad")
            return {'CANCELLED'}
        # THIS operator is the biggest single source of handle drift -- it moves every unlocked
        # mouth, which is exactly what moves the centre out from under the `JCT_*` Empty. It has to
        # put the handle back on the centre it just created, or the recommended workflow
        # (Make Intersection, then Auto Setback) ships a wrong grip by default.
        recentre_all_junctions(context)
        # FINISHED even at zero, deliberately. This operator is what the pad findings tell the
        # artist to run, and a remedy that answers "moved 0 mouth(es)" AND reports CANCELLED reads
        # as "it did not work" when it means "they are already where I would put them".
        self.report({'INFO'}, "%d pad(s): moved %d mouth(es)%s"
                    % (seen, moved, "" if moved else " -- already at the solved setback"))
        return {'FINISHED'}


class RKA_OT_recentre_junctions(Operator):
    """Put every intersection's handle back on the centre of its own mouths, moving no mouth

    The `JCT_*` Empty is the grip for moving and turning a whole crossing, and its origin is what
    G and R pivot around. It was written once, at Make Intersection, and never re-derived -- so
    every mouth dragged since has left it behind. Build and Auto Setback now keep it honest; this
    is the button for a file authored before they did"""
    bl_idname = "rka.recentre_junctions"
    bl_label = "Recentre Handles"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n, worst = recentre_all_junctions(context)
        if not n:
            self.report({'INFO'}, "every junction handle is already on its centre")
        else:
            self.report({'INFO'}, "%d junction handle(s) recentred -- worst was %.2f m off"
                        % (n, worst))
        return {'FINISHED'}


class RKA_OT_link_road_kit(Operator):
    """Library-link the profile asset kit into this file, so roads can name a section

    LINKED and not appended, deliberately: every district points at the one `road_kit.blend`, so
    editing a kerb section there restyles every road in the world that names it. Appending would
    make each district's copy drift"""
    bl_idname = "rka.link_road_kit"
    bl_label = "Link Road Kit"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import os
        from . import paths, point_style as pstyle
        if pstyle.kit_collection() is not None:
            self.report({'INFO'}, "%s is already in this file" % pstyle.KIT_COLLECTION)
            return {'FINISHED'}
        path = paths.ROAD_KIT_BLEND
        if not os.path.exists(path):
            self.report({'ERROR'}, "%s does not exist -- run blender/tools/build_road_kit.py"
                        % path)
            return {'CANCELLED'}
        with bpy.data.libraries.load(path, link=True) as (src, dst):
            if pstyle.KIT_COLLECTION not in src.collections:
                dst.collections = []
            else:
                dst.collections = [pstyle.KIT_COLLECTION]
        coll = pstyle.kit_collection()
        if coll is None:
            self.report({'ERROR'}, "%s holds no %s collection" % (path, pstyle.KIT_COLLECTION))
            return {'CANCELLED'}
        # Linked but NOT instanced into the scene: these are sections to sweep, not props to
        # place, and an Empty full of cross-sections standing at the origin is one more thing in
        # the viewport that looks like a mistake.
        self.report({'INFO'}, "%d profile section(s) linked from %s"
                    % (len(pstyle.kit_assets()), os.path.basename(path)))
        return {'FINISHED'}


CLASSES = (RKA_OT_new_road, RKA_OT_extend_road, RKA_OT_insert_point,
           RKA_OT_merge_points, RKA_OT_renumber_roads, RKA_OT_split_road,
           RKA_OT_repair_links, RKA_OT_tidy_roads, RKA_OT_connect_selected,
           RKA_OT_disconnect_selected, RKA_OT_make_intersection, RKA_OT_align_ramp_to_aux,
           RKA_OT_make_ramp, RKA_OT_branch_ramp, RKA_OT_delete_point, RKA_OT_select_road,
           RKA_OT_select_junction,
           RKA_OT_apply_cross_section, RKA_OT_auto_setback, RKA_OT_demo_network,
           RKA_OT_save_record, RKA_OT_load_record, RKA_OT_validate,
           RKA_OT_export_lanekit, RKA_OT_jump_to_point, RKA_OT_align_tangent,
           RKA_OT_sync_facings, RKA_OT_recentre_junctions,
           RKA_OT_link_road_kit)


def register():
    pm.register()
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    pm.unregister()

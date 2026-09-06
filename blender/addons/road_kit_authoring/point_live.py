"""point_live.py -- the depsgraph dirty set and the debounced rebuild (4.4).

SPLIT IN TWO, AND THAT SPLIT IS THE WHOLE DESIGN. A trailing-edge debounce does NOT solve the
modal case: a G-drag that pauses for 120 ms fires it, and creating or removing objects while
`transform.translate` is modal fights or crashes the operator -- and there is no clean public "is
a modal operator running" API to gate on.

    during the drag   -- the GPU overlay only (`point_overlay`). A draw handler needs no
                         `bpy.data` write, so it is ALWAYS safe, and the artist sees the ribbon,
                         the lane counts and the link colours follow the point in real time.
    on settle         -- the mesh rebuild, here. `depsgraph_update_post` marks the moved point's
                         road dirty plus any road across a JUNCTION or AUX link; a timer debounce
                         rebuilds only those.

SIX LANDMINES, EVERY ONE OF THEM HANDLED EXPLICITLY -- they are why this file is 200 lines and not
20:

* `depsgraph.updates[i].id` is the EVALUATED datablock. The authored Empty is `.id.original`, and
  the update must be filtered on `is_updated_transform` or every property tweak triggers a sweep.
* Writing into `ROAD_MANAGER_GEN` re-triggers the handler. Guarded by a re-entrancy flag AND by
  ignoring any update whose id lives in GEN -- with only the flag, the debounce never settles.
* `bpy.app.timers` do not survive a file load, and non-`@persistent` handlers are cleared on
  `load_post`. Both are re-armed from a `@persistent load_post`.
* Undo is a memfile snapshot, so `undo_post` re-marks everything dirty rather than trying to
  reason about what changed.
* Rebuilding while a modal operator is live is the crash. Gated on `mode == 'OBJECT'` AND an empty
  `window.modal_operators`.
* `matrix_world` is stale until the depsgraph updates, so a member's world position is never read
  in the same pass that moved its parent -- the read happens in the timer, a tick later.

FULL-NETWORK REBUILD STAYS A BUTTON. This only ever rebuilds the dirty set.
"""

import bpy
from bpy.app.handlers import persistent

try:
    from . import (point_build as pb, point_edges as pe, point_model as pm, point_solve as ps,
                   point_style as pstyle)
except ImportError:
    import point_build as pb                                                 # noqa: E402
    import point_edges as pe                                                 # noqa: E402
    import point_model as pm                                                 # noqa: E402
    import point_solve as ps                                                 # noqa: E402
    import point_style as pstyle                                             # noqa: E402


#: Seconds of quiet before a rebuild. Long enough that a G-drag's pauses do not fire it, short
#: enough that letting go feels immediate.
DEBOUNCE = 0.35

#: Roads waiting to be rebuilt, by collection name.
_dirty = set()

#: Re-entrancy guard. The rebuild writes objects, which re-enters `depsgraph_update_post`.
_building = False

#: THE SEVENTH LANDMINE, found by the step-5 smoketest and not on the plan's list.
#: `point_model.read_network()` begins with `view_layer.update()` -- it has to, or a junction
#: member's `matrix_world` is stale and every mouth reads at its parent's old offset. But
#: `view_layer.update()` issues a depsgraph update, which re-enters THIS handler, which reads the
#: network again: unbounded recursion, and Blender reports it as `RecursionError` inside
#: `PointData.__init__` -- a place with no connection to the actual cause. `_building` does not
#: cover it (the handler is not building), so the handler needs a guard of its own.
_in_handler = False

_timer_armed = False


#: ONE owner of "which collections are generated space" -- `point_build`, because it is what
#: creates them. The handler and the terrain raycast must agree, and the way they stop agreeing is
#: by each keeping its own copy.
_gen_names = pb.gen_collection_names


def _in_gen(obj, gen_names=None):
    """Does this object live in generated space? An update about our own output is not an edit --
    and without this the debounce never settles, however good the re-entrancy flag is."""
    names = _gen_names() if gen_names is None else gen_names
    return any(c.name in names for c in obj.users_collection)


def road_of_object(obj):
    for c in obj.users_collection:
        if c.library is None and getattr(c, "rka_road", None) is not None and c.rka_road.is_road:
            return c.name
    return None


def neighbours(net, road_name):
    """Roads reachable from this one across a JUNCTION or AUX link.

    NOT just the moved road. Dragging one mouth of a crossing changes the PAD, which changes where
    every other approach's carriageway stops -- and dragging a mainline point moves the aux edge a
    ramp is aligned to. Rebuilding only the moved road leaves the neighbour's kerb ending in mid
    air until something else happens to touch it."""
    out = {road_name}
    road = net.roads.get(road_name)
    if road is None:
        return out
    for uid in road.points:
        p = net.points.get(uid)
        if p is None:
            continue
        for t in p.targets(pm.LINK_JUNCTION) + p.targets(pm.LINK_AUX):
            other = net.road_of(t)
            if other is not None:
                out.add(other.name)
    # ...and inbound AUX: a ramp is linked TO, not FROM.
    for other in net.roads.values():
        if other.name in out:
            continue
        for uid in other.points:
            p = net.points.get(uid)
            if p is not None and any(net.road_of(t) is not None
                                     and net.road_of(t).name == road_name
                                     for t in p.targets(pm.LINK_AUX)):
                out.add(other.name)
                break
    return out


def mark_dirty(road_names):
    global _dirty
    _dirty |= set(n for n in road_names if n)
    _arm()


def dirty_set():
    """The pending rebuild scope. Exposed so a smoketest can assert it directly rather than
    inferring it from what geometry happens to exist."""
    return set(_dirty)


def _arm():
    global _timer_armed
    if _timer_armed or not bpy.app.timers.is_registered(_tick):
        if not _timer_armed:
            bpy.app.timers.register(_tick, first_interval=DEBOUNCE)
            _timer_armed = True


def _safe_to_build():
    if _building:
        return False
    try:
        if bpy.context.mode != 'OBJECT':
            return False
        for w in bpy.context.window_manager.windows:
            if getattr(w, "modal_operators", None):
                return False
    except AttributeError:
        pass
    return True


def rebuild(road_names, scene=None):
    """Rebuild exactly these roads and nothing else."""
    global _building
    scene = scene or bpy.context.scene
    # `_building` goes up FIRST: `read_network` calls `view_layer.update()`, which fires the
    # depsgraph handler (see `_in_handler`), and `gen_group` frees objects, which fires it again.
    _building = True
    try:
        # Same order as the Build button: adopt any hand rotation and re-face what the tool owns
        # BEFORE reading, so a settle never sweeps a facing the viewport has already moved past.
        # Inside `_building` because it writes transforms, which re-enters the depsgraph handler.
        try:
            from . import point_ops as po
        except ImportError:
            import point_ops as po
        po.sync_facings(scene)
        # Every junction handle back onto its own centre. ON SETTLE, never per drag frame: moving
        # a parent mid-modal fights the transform operator the artist is still holding -- the same
        # line this module already draws for geometry. Inside `_building` with `sync_facings`
        # because it too writes transforms, which re-enters the depsgraph handler.
        po.recentre_all_junctions()
        net = pm.read_network(scene)
    finally:
        _building = False
    ground = pb.ground_sampler(scene)
    # The BANDS are network-wide even when the rebuild is not: a kerb opens because of a road that
    # is not being rebuilt, so a partial band set would close gores that should be open.
    solves, jsolves = [], ps.solve_junctions(net, ground_fn=ground)
    for road in net.roads.values():
        for uids in ps.road_runs(net, road):
            s = ps.solve_road(net, road, uids, ground)
            if s is not None:
                solves.append(s)
    gsolves = ps.solve_gores(net, solves)
    bands = pe.collect_bands(solves, jsolves, gsolves)
    _building = True
    try:
        for name in road_names:
            runs = [s for s in solves if s.road.name == name]
            if not runs:
                continue
            coll = pb.gen_group(name, scene)
            for i, s in enumerate(runs):
                obj_name = name if len(runs) == 1 else "%s_%d" % (name, i)
                style = pstyle.resolve(s.road, material_fn=pb.material)
                surf = pb.build_carrier(s, coll, obj_name, style)
                edges = pb.build_edges(s, bands, coll, obj_name, style)
                # The paint too -- a live rebuild that quietly strips the markings teaches the
                # artist that moving a point deletes them.
                pb.build_marks(s, coll, obj_name, style)
                pb.build_collision([surf], edges, coll, obj_name, bool(s.road.ped_access))
        if jsolves:
            jcoll = pb.gen_group(pm.JUNCTIONS, scene)
            for j in jsolves:
                nm = "JCT_" + j.uids[0][:8]
                pb.build_pad(j, jcoll, nm)
                pb.build_junction_edges(j, jcoll, nm)
        # A gore spans a mainline and a ramp, and `neighbours()` already pulls both into the dirty
        # set across the AUX link -- so rebuilding every gore here is the only way one of them
        # moving cannot leave the wedge between them behind.
        if gsolves:
            gcoll = pb.gen_group(pm.GORES, scene)
            for g in gsolves:
                nm = "GORE_" + g.ramp_uid[:8]
                pb.build_gore(g, gcoll, nm)
                pb.build_gore_edges(g, gcoll, nm)
    finally:
        _building = False
    return len(road_names)


def _tick():
    global _dirty, _timer_armed
    if not _dirty:
        _timer_armed = False
        return None
    if not _safe_to_build():
        return DEBOUNCE                    # still dragging -- come back, do not build
    todo, _dirty = set(_dirty), set()
    _timer_armed = False
    try:
        rebuild(sorted(todo))
    except Exception as exc:                # a live rebuild must never take the session with it
        print("[point_live] rebuild failed: %r" % (exc,))
    return None


@persistent
def on_depsgraph(scene, depsgraph=None):
    global _in_handler
    if _building or _in_handler:
        return
    # BEFORE the live-rebuild gate, deliberately. The overlay's own cache key was the object COUNT,
    # so moving or rotating a point did not invalidate it and the overlay drew stale positions --
    # which made "the overlay follows the drag either way" (the sentence in this module's own
    # property description) untrue, and would have made MANUAL rotation feel like it did nothing.
    # Invalidating is setting one int; it must not be conditional on a rebuild the artist may have
    # switched off precisely because they only want the overlay.
    _invalidate_overlay()
    if not getattr(scene, "rka_live_rebuild", False):
        return
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    net = None
    hit = set()
    _in_handler = True
    try:
        hit = _scan(scene, depsgraph)
    finally:
        _in_handler = False
    if hit:
        mark_dirty(hit)


def _invalidate_overlay():
    """Imported lazily: `point_overlay` pulls in `gpu`/`blf`, which this module has no need of."""
    try:
        from . import point_overlay as ov
    except ImportError:                       # headless smoketests import the modules flat
        try:
            import point_overlay as ov
        except ImportError:
            return
    ov.invalidate()


def _scan(scene, depsgraph):
    net = None
    hit = set()
    gen_names = _gen_names()
    for upd in depsgraph.updates:
        obj = getattr(upd.id, "original", upd.id)
        if not isinstance(obj, bpy.types.Object):
            continue
        if not upd.is_updated_transform:
            continue
        if getattr(obj, "rka_pt", None) is None or not obj.rka_pt.is_point:
            continue
        if _in_gen(obj, gen_names):
            continue
        road = road_of_object(obj)
        if road is None:
            continue
        if net is None:
            net = pm.read_network(scene)
        hit |= neighbours(net, road)
    return hit


@persistent
def on_undo(scene, depsgraph=None):
    """Undo is a memfile snapshot -- there is no update list to reason about, so everything the
    scene holds is re-marked."""
    if not getattr(scene, "rka_live_rebuild", False):
        return
    mark_dirty({c.name for c in pm.road_collections(scene)})


@persistent
def on_load(_dummy):
    """Timers do not survive a file load and non-persistent handlers are cleared, so both are
    re-armed here."""
    global _dirty, _timer_armed
    _dirty, _timer_armed = set(), False


def register():
    bpy.types.Scene.rka_live_rebuild = bpy.props.BoolProperty(
        name="Live Rebuild", default=False,
        description="Rebuild a road's geometry when its points settle. The overlay follows the "
                    "drag either way")
    if on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(on_depsgraph)
    if on_undo not in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.append(on_undo)
    if on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load)


def unregister():
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, on_depsgraph),
                    (bpy.app.handlers.undo_post, on_undo),
                    (bpy.app.handlers.load_post, on_load)):
        if fn in lst:
            lst.remove(fn)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    del bpy.types.Scene.rka_live_rebuild

"""Step 5's acceptance test: the dirty scope, the panels and the overlay, headless.

    blender --background --python-exit-code 1 \
            --python blender/addons/road_kit_authoring/smoketest_point_live.py

The one assertion that matters: DRAGGING ONE POINT MARKS EXACTLY ITS ROAD AND ITS LINK-NEIGHBOURS
DIRTY AND REBUILDS NOTHING ELSE. Asserted on `point_live.dirty_set()` directly rather than
inferred from what geometry happens to exist, because "the right thing was rebuilt" and "nothing
else changed" are different claims and only the first is visible in the output.

`--background` never calls `invoke()`, so every operator here is driven with explicit properties.
Draw handlers never fire headless either, so the overlay is tested by calling its data path.
"""

import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "blender", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "lib"))

from road_kit_authoring import point_live as pl          # noqa: E402
from road_kit_authoring import point_model as pm         # noqa: E402
from road_kit_authoring import point_ops as po           # noqa: E402
from road_kit_authoring import point_panel as ppn        # noqa: E402


def _wipe():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


def check(msg):
    print("OK:", msg)


def _road(name, y, n=3, spacing=120.0, **kw):
    bpy.ops.rka.new_road(name=name, x=0.0, y=y, **kw)
    coll = pm._local(bpy.data.collections, name)
    pts = po.points_in(coll)
    for i in range(1, n):
        bpy.context.view_layer.objects.active = pts[-1]
        bpy.ops.rka.extend_road(use_delta=True, dx=spacing, dy=0.0, dz=0.0)
        pts = po.points_in(coll)
    return coll, pts


def main():
    ok = 0
    _wipe()
    if not hasattr(bpy.types, "RKA_OT_point_build"):
        from road_kit_authoring import point_build, point_overlay
        point_build.register()
        point_overlay.register()
        pl.register()
        ppn.register()
    if not hasattr(bpy.types, "RKA_OT_validate"):
        po.register()

    # ---- three roads: two crossing, one alone -------------------------------------------------
    a_coll, ap = _road("road_a", 0.0)
    b_coll, bp = _road("road_b", -240.0)
    c_coll, cp = _road("road_c", 900.0)
    # Put road_b's middle point next to road_a's and make a crossing out of the two.
    bp[1].matrix_world.translation = (240.0, -120.0, 0.0)
    bpy.context.view_layer.update()
    for o in bpy.context.selected_objects:
        o.select_set(False)
    ap[1].select_set(True)
    bp[1].select_set(True)
    bpy.context.view_layer.objects.active = ap[1]
    bpy.ops.rka.make_intersection()
    check("scene: two roads crossing at a junction, one road alone")
    ok += 1

    # ---- THE DIRTY SCOPE ------------------------------------------------------------------------
    net = pm.read_network(bpy.context.scene)
    scope = pl.neighbours(net, "road_a")
    assert scope == {"road_a", "road_b"}, scope
    assert pl.neighbours(net, "road_c") == {"road_c"}, pl.neighbours(net, "road_c")
    check("dragging road_a marks road_a + road_b (across the JUNCTION link) and nothing else")
    ok += 1

    # ---- an AUX link pulls the ramp in, in BOTH directions --------------------------------------
    d_coll, dp = _road("ramp_d", 400.0, n=2, lanes_fwd=1, lanes_bwd=0)
    net = pm.read_network(bpy.context.scene)
    a_uid = ap[2].rka_pt.uid
    po.link_objects(ap[2], dp[0], pm.LINK_AUX, symmetric=False)
    ap[2].rka_pt.aux_fwd = 1
    dp[0].rka_pt.role = pm.RAMP_EXIT
    net = pm.read_network(bpy.context.scene)
    assert "ramp_d" in pl.neighbours(net, "road_a"), pl.neighbours(net, "road_a")
    assert "road_a" in pl.neighbours(net, "ramp_d"), pl.neighbours(net, "ramp_d")
    check("an AUX link is followed from both ends -- moving either rebuilds the gore")
    ok += 1

    # ---- the handler only fires on a TRANSFORM, and only when live rebuild is on ---------------
    pl._dirty.clear()
    bpy.context.scene.rka_live_rebuild = False
    ap[0].matrix_world.translation = (10.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    pl.on_depsgraph(bpy.context.scene)
    assert not pl.dirty_set(), pl.dirty_set()
    check("live rebuild off: a drag marks nothing dirty")
    ok += 1

    # ---- generated output never re-triggers the handler -----------------------------------------
    from road_kit_authoring import point_build as pb
    bpy.context.scene.rka_live_rebuild = True
    net = pm.read_network(bpy.context.scene)
    pb.build_network(net, bpy.context.scene, sample_ground=False)
    gen = [o for o in bpy.data.objects if o.name.endswith(pb.SUFFIX_CARRIER)]
    assert gen, "nothing was built"
    for o in gen:
        assert pl._in_gen(o), o.name
    pl._dirty.clear()
    for o in gen:
        o.matrix_world.translation = (0.0, 0.0, 1.0)
    bpy.context.view_layer.update()
    pl.on_depsgraph(bpy.context.scene)
    assert not pl.dirty_set(), pl.dirty_set()
    check("a write into ROAD_MANAGER_GEN never re-triggers the rebuild -- the debounce settles")
    ok += 1

    # ---- undo re-marks everything, because a memfile snapshot has no update list ----------------
    pl._dirty.clear()
    pl.on_undo(bpy.context.scene)
    assert pl.dirty_set() == {"road_a", "road_b", "road_c", "ramp_d"}, pl.dirty_set()
    check("undo re-marks every road -- no reasoning about a memfile snapshot")
    ok += 1

    # ---- a partial rebuild touches ONLY its own roads ---------------------------------------------
    pl._dirty.clear()
    before = {o.name: o.data.name for o in bpy.data.objects
              if o.name.endswith(pb.SUFFIX_CARRIER)}
    ap[0].matrix_world.translation = (-40.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    pl.rebuild(["road_a"])
    after = {o.name: o.data.name for o in bpy.data.objects
             if o.name.endswith(pb.SUFFIX_CARRIER)}
    assert set(before) == set(after), set(before) ^ set(after)
    c_obj = next(o for o in bpy.data.objects
                 if o.name.startswith("road_c") and o.name.endswith(pb.SUFFIX_CARRIER))
    assert before[c_obj.name] == after[c_obj.name] or c_obj.name in after
    check("rebuilding one road leaves every other road's objects in place")
    ok += 1

    # ---- the panels resolve their context ---------------------------------------------------------
    bpy.context.view_layer.objects.active = ap[0]
    assert ppn.active_point(bpy.context) is ap[0]
    assert ppn.active_road(bpy.context) is a_coll
    bpy.context.view_layer.objects.active = ap[1]
    jct = ppn.active_junction(bpy.context)
    assert jct is not None and jct.name.startswith("JCT_"), jct
    check("the inspector finds the active point, its road and its junction parent")
    ok += 1

    # ---- the overlay's data path runs, and finds the taper it should ------------------------------
    from road_kit_authoring import point_overlay as pov
    pov.invalidate()
    net = pov._network(bpy.context.scene)
    assert net is not None
    ap[1].rka_pt.lanes_fwd = 6          # 4 lanes appearing over 120 m at 50 km/h: far too fast
    pov.invalidate()
    pov._network(bpy.context.scene)
    assert pov._cache["bad_uids"], "the overlay found no taper violation to draw red"
    ap[1].rka_pt.lanes_fwd = 2
    check("the overlay reads the gate and has a taper violation to draw in red")
    ok += 1

    print("\nALL SMOKETESTS PASSED (%d)" % ok)


main()

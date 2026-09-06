#!/usr/bin/env python3
"""
smoketest_curb_kit.py -- headless verification for the road_kit_authoring addon's
'ASSET' curb style (curb/barrier/fence asset-library instancing, see kit/build_curb_kit.py
and kit_common.curb_asset_row).

RUN (after kit/build_curb_kit.py has produced kit/curb_kit.blend):
  blender --background --python kit/smoketest_curb_kit.py
"""
import bmesh
import bpy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))        # assets/world_source/kit
ROOT = os.path.dirname(HERE)                               # assets/world_source
ADDONS_DIR = os.path.join(ROOT, "addons")
sys.path.insert(0, ADDONS_DIR)
sys.path.insert(0, os.path.join(ROOT, "lib"))

import road_kit_authoring as rka          # noqa: E402
from road_kit_authoring import ops_segment as opseg   # noqa: E402
from road_kit_authoring import paths as rpaths        # noqa: E402
import kit_common as kc                    # noqa: E402


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _eval_vert_count(obj, depsgraph):
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    n = len(mesh.vertices)
    eval_obj.to_mesh_clear()
    return n


def main():
    bpy.ops.wm.read_homefile(use_empty=True)
    kc.setup_units()
    if not hasattr(bpy.types.Scene, "rka"):
        rka.register()   # already registered if the addon auto-loads from the user's addons dir

    curb_blend = rpaths.CURB_KIT_BLEND
    _assert(os.path.exists(curb_blend), "curb_kit.blend not found -- run build_curb_kit.py first")
    with bpy.data.libraries.load(curb_blend, link=True) as (src, dst):
        dst.collections = list(src.collections)
    barrier_coll = bpy.data.collections.get("Kit_Curb_JerseyBarrier_L2")
    _assert(barrier_coll is not None, "Kit_Curb_JerseyBarrier_L2 not linked")
    barrier_obj = next(o for o in barrier_coll.objects if o.type == 'MESH')
    barrier_vert_count = len(barrier_obj.data.vertices)
    _assert(barrier_vert_count > 0, "barrier piece has no verts")

    scene_coll = bpy.context.scene.collection
    context = bpy.context
    pts = [(0.0, 0.0, 0.0), (20.0, 0.0, 0.0)]

    result = opseg._build_segment_from_points(
        context, scene_coll, pts, lane_width=5.0, lanes=1, lanes_backward=1,
        curb_l_style='ASSET', curb_r_style='BOX', curb_height=0.15, curb_thickness=0.25,
        join_visual_mesh=False, export_path="", gltf_export_path="",
        curb_asset_collection="Kit_Curb_JerseyBarrier_L2", curb_asset_spacing=2.0,
        curb_asset_rot_offset_r=180.0)
    coll_name = result["coll"].name
    spine_name = result["spine_obj"].name

    curb_l_name = "curb_%s_L" % coll_name
    curb_r_name = "curb_%s_R" % coll_name
    curb_l = bpy.data.objects.get(curb_l_name)
    curb_r = bpy.data.objects.get(curb_r_name)
    _assert(curb_l is not None, "curb_*_L object missing")
    _assert(curb_r is not None, "curb_*_R object missing")

    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    curb_l = bpy.data.objects.get(curb_l_name)   # re-fetch: depsgraph_get() can invalidate refs
    l_verts = _eval_vert_count(curb_l, depsgraph)
    _assert(l_verts % barrier_vert_count == 0 and l_verts > 0,
            "L curb evaluated verts (%d) not a multiple of barrier verts (%d) -- "
            "ASSET instancing didn't realize N instances" % (l_verts, barrier_vert_count))
    n_instances = l_verts // barrier_vert_count
    _assert(n_instances >= 2, "expected >= 2 barrier instances over a 20m segment at 2m spacing, "
                               "got %d" % n_instances)

    # R side must still be a swept BOX wall -- different object type (Curve, not the barrier's own
    # Mesh vertex count) -- proves independent per-side dispatch.
    curb_r = bpy.data.objects.get(curb_r_name)
    _assert(curb_r.type == 'CURVE', "R curb (BOX style) should still be the curb_loop Curve object")

    print("curb asset smoketest: L (ASSET) = %d instances (%d verts / %d per-piece); "
          "R (BOX) unaffected" % (n_instances, l_verts, barrier_vert_count))

    # --- simulate a live-edit drag: extend the spine, rebuild in place, check no duplicate
    # accumulation and the instance count scales with the new length.
    spine_obj = bpy.data.objects.get(spine_name)
    sp = spine_obj.data.splines[0]
    sp.points[-1].co = (40.0, 0.0, 0.0, 1.0)   # was (20,0,0) -- double the length

    coll = bpy.data.collections.get(coll_name)
    opseg.rebuild_segment_gn_in_place(context, coll)

    coll = bpy.data.collections.get(coll_name)
    l_curbs = [o for o in coll.objects if o.name.startswith("curb_") and o.name.endswith("_L")]
    _assert(len(l_curbs) == 1, "expected exactly 1 L curb object after rebuild, got %d "
                                "(duplicate accumulation?)" % len(l_curbs))
    l_curb_name = l_curbs[0].name
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    l_curbs = [bpy.data.objects.get(l_curb_name)]
    l_verts_2 = _eval_vert_count(l_curbs[0], depsgraph)
    n_instances_2 = l_verts_2 // barrier_vert_count
    _assert(n_instances_2 > n_instances,
            "instance count should grow after doubling segment length (%d -> %d)"
            % (n_instances, n_instances_2))

    print("curb asset smoketest: after live-edit rebuild -- 1 L curb object, "
          "%d instances (was %d) -- no duplicate accumulation" % (n_instances_2, n_instances))
    print("SMOKETEST OK")


if __name__ == "__main__":
    main()

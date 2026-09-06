"""build_road_kit.py -- the PROFILE ASSET kit for road_kit_authoring.

    blender --background --python-exit-code 1 --python blender/tools/build_road_kit.py

Writes `assets/world_source/kit/road_kit.blend`, which is TWO things and deliberately one file:

  * one `ROAD_KIT` collection of profile CURVES. A road names one of these in a style slot
    (`RoadData.kerb_asset`, ...) and `GN_PointProfile` sweeps it along the road's own edge, at
    the section's own authored size;
  * the repo's MATERIAL LIBRARY. Every `kit_common.MATS` / `TILED_MATS` entry is written here
    with a fake user, and `kit_common.mat()` LINKS from this file rather than creating its own
    copy in whichever `.blend` is building (see that function's own comment). So the world's look
    is authored in a `.blend` an artist can open, not in a Python table, and a kerb section's
    concrete is the SAME datablock as the deck's beside it.

One file, one link, one resolver: the addon already links this kit for its sections
(`RKA_OT_link_road_kit`), and a second library plus a link chain would buy nothing.

WHY THESE ARE CURVES AND NOT KIT PIECES. The previous model instanced rigid meshes along an edge.
Round a 9 m corner a 2 m piece sits ~12.7 deg from its neighbour and opens a real ~7.8 cm gap at
every joint -- an inherent limit of tiling rigid geometry on a curve (the same reason real
guardrail systems show visible joints on bends), not a phase bug, and eventually the reason that
whole style was retired. A swept section has no joints to open, at any curvature.

HOW TO AUTHOR A NEW ONE. Draw the cross-section in the XY plane: **+X is outward from the road,
+Y is up**, origin at the point the road hands it (the kerb line for a kerb, the wall's foot for a
barrier). That is the natural way round -- `GN_PointProfile` applies the measured
profile-to-world correction, so what you draw is what you get. Give it a material; the addon reads
it off the asset, because `Curve to Mesh` drops it. Name it `RKA_PROFILE_<slot>_<variant>`.

The file is LIBRARY-LINKED into a district (`Author > Style > Link Road Kit`), so editing one
section here restyles every road in the world that names it.
"""

import os
import sys

import bpy

HERE = os.path.dirname(os.path.realpath(__file__))
BLENDER_SRC = os.path.dirname(HERE)
REPO = os.path.dirname(BLENDER_SRC)
sys.path.insert(0, os.path.join(BLENDER_SRC, "lib"))

import kit_common as kc                                                      # noqa: E402

# THE TOOL THAT AUTHORS THE LIBRARY MUST NOT CONSULT IT. `kc.mat()` links from
# `road_kit.blend`; this script IS `road_kit.blend`, so it takes the bootstrap path and builds
# every material from the tables. Without this the second run would link the first run's output
# into itself.
kc.USE_MATERIAL_LIBRARY = False

OUT = os.path.join(REPO, "assets", "world_source", "kit", "road_kit.blend")
COLLECTION = "ROAD_KIT"
PREFIX = "RKA_PROFILE_"


def _wipe():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    for cu in list(bpy.data.curves):
        bpy.data.curves.remove(cu)
    # The startup file's default `Material` would otherwise ship in the library and be offered
    # as a road material — a datablock nobody authored, named after nothing.
    for m in list(bpy.data.materials):
        if not m.name.startswith("M_"):
            bpy.data.materials.remove(m)


def build_materials():
    """Every material the repo declares, written into the kit with a fake user.

    A FAKE USER IS WHAT MAKES A MATERIAL LIBRARY POSSIBLE: a `.blend` drops any datablock with
    zero users on save, and most of these are used by no object in this file — the kit holds six
    cross-sections, not a scene. Without the flag the library would ship exactly the handful of
    materials the sections happen to carry, which is how it behaved until now."""
    made = []
    for key in list(kc.MATS) + list(kc.TILED_MATS):
        m = kc.mat(key)
        m.use_fake_user = True
        made.append(m)
    return made


def profile(name, points, matkey, coll):
    """One section, as a POLY curve in the XY plane. `points` are `(x_outward, y_up)` metres."""
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '2D'
    sp = cu.splines.new('POLY')
    sp.points.add(len(points) - 1)
    for i, (x, y) in enumerate(points):
        sp.points[i].co = (float(x), float(y), 0.0, 1.0)
    cu.materials.append(kc.mat(matkey))
    o = bpy.data.objects.new(name, cu)
    coll.objects.link(o)
    return o


#: `(name, matkey, [(x_outward, y_up)])`. Every section starts at the origin -- the line the road
#: hands it -- so two of them stacked (a kerb and the footway above it) meet by construction.
SECTIONS = (
    # -- kerbs ------------------------------------------------------------------------------------
    ("kerb_std", "concrete",
     [(0.0, 0.0), (0.0, 0.15), (0.30, 0.15), (0.30, 0.0)]),
    # A real kerb is chamfered at the top edge -- it is the single detail that reads as "kerb"
    # rather than "step" at driving speed.
    ("kerb_granite", "concrete",
     [(0.0, 0.0), (0.0, 0.12), (0.03, 0.16), (0.14, 0.17), (0.32, 0.17), (0.32, 0.0)]),
    # A dished gutter, INBOARD of the kerb line: negative X, because the channel belongs on the
    # road side of the line the kerb stands on.
    ("gutter_dish", "concrete",
     [(-0.45, 0.02), (-0.30, -0.03), (-0.10, -0.02), (0.0, 0.0), (0.0, 0.14), (0.26, 0.14),
      (0.26, 0.0)]),
    # -- barriers ---------------------------------------------------------------------------------
    ("wall_parapet", "barrier",
     [(0.0, 0.0), (0.0, 1.0), (0.22, 1.0), (0.22, 0.0)]),
    # The New Jersey section: a near-vertical face, the 55-degree slope, and the toe.
    ("wall_jersey", "barrier",
     [(0.0, 0.0), (0.0, 0.81), (0.09, 0.81), (0.16, 0.33), (0.32, 0.08), (0.32, 0.0)]),
    # -- footways ---------------------------------------------------------------------------------
    # A slab with a fall toward the road, which is what makes a wet street read as drained.
    ("footway_slab", "concrete_tile",
     [(0.0, 0.0), (0.0, 0.02), (2.0, 0.06), (2.0, 0.0)]),
)


def build():
    _wipe()
    mats = build_materials()
    coll = bpy.data.collections.new(COLLECTION)
    bpy.context.scene.collection.children.link(coll)
    made = []
    for name, matkey, pts in SECTIONS:
        made.append(profile(PREFIX + name, pts, matkey, coll))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=OUT)
    print("build_road_kit: %d profile(s), %d material(s) -> %s" % (len(made), len(mats), OUT))
    for o in made:
        xs = [v[0] for v in o.bound_box]
        ys = [v[1] for v in o.bound_box]
        print("   %-28s %.2f m wide, %.2f m tall" % (o.name, max(xs) - min(xs), max(ys) - min(ys)))
    return made


if __name__ == "__main__":
    build()

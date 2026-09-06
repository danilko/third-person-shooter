#!/usr/bin/env python3
"""
kit_common.py — shared Blender helpers for the modular town kit.

Used by every kit/ builder and towns/ assembler. Holds: unit setup, collection
helpers, the material set, low-poly mesh primitives (box/cyl/gable/combine/cut),
the `-colonly` collision-proxy helper, glTF export, and the Geometry-Nodes
instancing core (GN_Instance group + instancer/place_side).

CONVENTIONS (locked — see README / BLENDER_CONVENTIONS.md):
  * 1 Blender unit = 1 m. Blender Z up; Godot importer flips to Y up (don't pre-rotate).
  * Forward face authored on +Y (imports to Godot +Z = forward).
  * PIVOTS by asset class:
      - walls / fences / facade modules .. ENDPOINT: origin at base START corner
        (x=0 end), front face +Y, length runs +X, base z=0.   (easy abutting)
      - floor / ceiling slab ............. TOP corner (top face on storey datum).
      - road / grid tiles ................ footprint CENTRE at ground (z=0)
        (90deg rotation snaps; tiles abut on grid lines).
      - standalone props ................. bottom CENTRE of footprint, base z=0.
  * Collision: each leaf gets a sibling box proxy `<Name>-colonly` (visual removed
    on import -> Godot CollisionShape3D). Authored by colonly().
  * Road grid = 7 m cell, 3.5 m lane. Zone cell = 56 m.
"""
import bpy
import bmesh
import math
import os
from mathutils import Matrix

# ---------------------------------------------------------------- dimensions
T      = 0.20      # wall thickness
R_BAY  = 2.0       # residential bay width
R_H    = 3.0       # residential floor-to-floor
K_H    = 3.6       # konbini wall height
SLAB   = 0.30      # floor/ceiling slab thickness
CELL   = 7.0       # road grid cell (matches game Road2LaneStraight 7 m)
LANE   = 3.5       # single lane width
ZONE   = 56.0      # streaming zone cell (= 8 road tiles = 14 wall runs)


# ----------------------------------------------------------------- scene/units
def reset_scene(coll_names):
    """Wipe objects in the given collections, then purge orphan meshes."""
    for cn in coll_names:
        coll = bpy.data.collections.get(cn)
        if coll:
            for obj in list(coll.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.collections.remove(coll)
    for me in list(bpy.data.meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)


# Default far clip baked into every generated .blend (viewports + script-made cameras): the
# 3 km world vanishes past Blender's 1 km viewport default the moment you zoom out to frame it.
VIEW_CLIP_END = 100000.0


def setup_view_clip(end=VIEW_CLIP_END):
    """Set the far clip on every 3D viewport of every screen/workspace stored in the file, so
    the saved .blend opens showing the whole world. Works headless too — bpy.data.screens is
    the file's saved UI data, no window needed.

    THE NEAR CLIP MOVES WITH IT. Depth precision is governed by the RATIO far/near, not by the
    far plane alone, and Blender's own defaults (0.01 m / 1000 m) sit at 1e5. Pushing the far
    plane out two orders of magnitude while leaving the near plane at 0.01 takes that ratio to
    1e7, which z-fights surfaces a few centimetres apart -- exactly the separation a kerb, a
    painted median and a deck slab sit at. So the near plane is raised to hold the same ratio the
    default view has, which is invisible at world scale (you cannot get within 10 cm of anything
    while framing a 3 km island) and keeps close surfaces readable."""
    near = max(0.01, end / 1.0e5)
    for scr in bpy.data.screens:
        for area in scr.areas:
            if area.type == 'VIEW_3D':
                for sp in area.spaces:
                    if sp.type == 'VIEW_3D':
                        sp.clip_end = end
                        sp.clip_start = near


def setup_units():
    sc = bpy.context.scene
    sc.unit_settings.system = 'METRIC'
    sc.unit_settings.scale_length = 1.0
    sc.unit_settings.length_unit = 'METERS'
    setup_view_clip()


def get_coll(name):
    # local-only lookup: with neighbour districts library-linked in (tools/link_neighbors.py),
    # several libraries can each contribute a same-named collection (e.g. STREET) — a bare
    # bpy.data.collections.get() may return a read-only linked one instead of ours.
    coll = next((c for c in bpy.data.collections
                 if c.name == name and c.library is None), None)
    if coll is None:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll


def link_collections(abspath, names):
    """TRUE library-link (link=True) the named collections from `abspath` in ONE library load;
    returns those found. Linked data is a live, read-only reference to the source .blend —
    edit + save the source, reload the linking file, the edit is there; nothing is copied and
    the linking file can never corrupt the source. Shared mechanism of tools/link_world.py,
    tools/link_neighbors.py and the master's linked-district layer (tools/build_world.py)."""
    with bpy.data.libraries.load(abspath, link=True) as (src, dst):
        dst.collections = [c for c in src.collections if c in names]
    return [c for c in dst.collections if c is not None]


def instance_collection(dest, name, coll, loc):
    """Collection-Instance empty placing (linked) collection `coll` at world `loc`."""
    inst = bpy.data.objects.new(name, None)
    inst.instance_type = 'COLLECTION'
    inst.instance_collection = coll
    inst.location = loc
    dest.objects.link(inst)
    return inst


# ----------------------------------------------------------------- materials
MATS = {
    "concrete": ("M_Concrete", (0.82, 0.80, 0.76, 1)),
    "trim":     ("M_Trim",     (0.74, 0.69, 0.58, 1)),
    "glass":    ("M_Glass",    (0.55, 0.78, 0.85, 1)),
    "metal":    ("M_Metal",    (0.55, 0.60, 0.64, 1)),
    "accent":   ("M_Accent",   (0.18, 0.42, 0.87, 1)),
    "asphalt":  ("M_Asphalt",  (0.28, 0.30, 0.33, 1)),
    "roof":     ("M_RoofTile", (0.32, 0.36, 0.42, 1)),
    "wood":     ("M_Wood",     (0.45, 0.30, 0.18, 1)),
    "leaf":     ("M_Leaf",     (0.28, 0.42, 0.22, 1)),
    "red":      ("M_Red",      (0.72, 0.16, 0.13, 1)),
    "line_y":   ("M_LineY",    (0.86, 0.74, 0.20, 1)),   # yellow lane line
    "line_w":   ("M_LineW",    (0.90, 0.90, 0.90, 1)),   # white lane line
    "dirt":     ("M_Dirt",     (0.40, 0.34, 0.26, 1)),   # ground fill
    # SEE-THROUGH ON PURPOSE. The sea plate has a seabed under it now (`build_island_v3
    # .build_seabed`), and an opaque plate would turn the water into a lid: walk off the shore and
    # you are standing on real ground in the dark, under a ceiling. The alpha is what makes the
    # floor readable from above and the shallows readable from in them.
    "water":    ("M_Water",    (0.09, 0.30, 0.42, 0.45)),  # sea / bay surface, translucent
    "rail":     ("M_Rail",     (0.40, 0.40, 0.44, 1)),   # rail steel
    "col":      ("M_Collision", (0.90, 0.20, 0.55, 0.25)),  # debug proxy tint
    # --- Tokyo urban set ---
    "neon":        ("M_Neon",        (0.95, 0.25, 0.55, 1)),   # emissive pink neon
    "screen":      ("M_Screen",      (0.45, 0.70, 1.00, 1)),   # emissive media facade
    "brick":       ("M_Brick",       (0.50, 0.22, 0.16, 1)),   # red-brick viaduct
    "glasscurtain":("M_GlassCurtain", (0.42, 0.58, 0.70, 1)),  # tinted curtain wall
    "steel":       ("M_Steel",       (0.58, 0.60, 0.64, 1)),   # train / structure steel
    "shink":       ("M_Shink",       (0.92, 0.93, 0.96, 1)),   # shinkansen white body
    # --- road kit (road_kit_authoring). Here rather than in `point_build` so the roads share ONE
    # material registry with every other builder in the repo: a road's asphalt is the same
    # datablock as a car park's, and `line_w`/`line_y` above -- which existed for lane lines from
    # the beginning and had no user until the road kit started painting them -- are now theirs.
    "median":      ("M_Median",      (0.20, 0.30, 0.16, 1)),   # planted median island
    "barrier":     ("M_Barrier",     (0.68, 0.67, 0.64, 1)),   # parapet / jersey barrier
}

# emissive keys -> emission strength
EMISSIVE = {"M_Neon": 5.0, "M_Screen": 3.5}


def get_mat(name, rgba):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = rgba
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = rgba
            if rgba[3] < 1.0:
                # ALPHA IS NOT PART OF BASE COLOR. Principled reads it from its own `Alpha` input;
                # writing an RGBA into Base Color sets a colour and nothing else, which is how a
                # "translucent" material stays solid. The render method is set by whichever name
                # this Blender uses (4.2+ renamed `blend_method`), so the viewport agrees with the
                # export instead of only the export being right.
                bsdf.inputs["Alpha"].default_value = rgba[3]
                if hasattr(mat, "surface_render_method"):
                    mat.surface_render_method = 'BLENDED'
                elif hasattr(mat, "blend_method"):
                    mat.blend_method = 'BLEND'
            if name == "M_Glass":
                bsdf.inputs["Roughness"].default_value = 0.05
                if "Transmission Weight" in bsdf.inputs:
                    bsdf.inputs["Transmission Weight"].default_value = 0.9
            elif name == "M_GlassCurtain":
                bsdf.inputs["Roughness"].default_value = 0.08
                if "Metallic" in bsdf.inputs:
                    bsdf.inputs["Metallic"].default_value = 0.5
            elif name in EMISSIVE:
                if "Emission Color" in bsdf.inputs:
                    bsdf.inputs["Emission Color"].default_value = rgba
                if "Emission Strength" in bsdf.inputs:
                    bsdf.inputs["Emission Strength"].default_value = EMISSIVE[name]
    return mat


# PROCEDURAL, WORLD-POSITION-based tile/paving materials -- keyed separately from MATS/get_mat
# (a single flat Base Color) since these need a small shader graph instead. 2026-08, user-asked:
# "how does most industry work around this" for a tiled-paving LOOK on a sidewalk/curb that must
# also smoothly follow a curved corner (the geometric-limit problem discrete rigid ASSET tiles
# have -- see kit_common.curb_asset_row's own docstring). The industry answer or a real texture:
# UV the mesh from a FIXED reference frame (a "box"/planar projection aligned to a stable axis),
# NOT the curve's own tangent frame (which stretches/pinches near a corner apex, since the inner
# and outer rails of a bend cover different arc lengths for the same angular sweep). This project
# has no image textures at all yet (every MATS entry is a flat color), so the equivalent fix here
# is a PURELY PROCEDURAL checker pattern read directly from the mesh's own Position -- Blender's
# 'Checker Texture' shader node takes a Vector input and is evaluated per-shading-point, with NO
# UV/parametrization involved at all, so it is by construction immune to the pinching problem: a
# curved corner's checker cells read as perfectly square, cut straight across the bend, because
# the pattern was never derived from the curve's own geometry in the first place. WORLD position
# (not a per-piece road-axis) is used deliberately: every piece built through the SAME material
# then shares the exact same grid automatically, with zero per-piece alignment/rotation logic
# needed at every joint -- a road-axis-aligned scheme would need to re-derive and match that axis
# at every seam, reintroducing a smaller version of the same problem being solved.
TILED_MATS = {
    # key: (material name, tile color 1, tile color 2, tile size in meters)
    "concrete_tile": ("M_ConcreteTile", (0.85, 0.83, 0.79, 1), (0.78, 0.76, 0.72, 1), 0.6),
}


def get_tiled_mat(name, rgba1, rgba2, tile_size):
    """Build (or fetch the cached) procedural checker-pattern material described by
    `TILED_MATS`'s own module comment -- Geometry (Position, world-space for every GN-built
    object in this addon, which all sit at an identity transform with positions already baked
    into local space) -> Checker Texture (Scale = 1/tile_size) -> Base Color."""
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    m = bpy.data.materials.new(name)
    m.diffuse_color = rgba1
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (400, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.location = (150, 0)
    checker = nt.nodes.new("ShaderNodeTexChecker"); checker.location = (-150, 0)
    geo = nt.nodes.new("ShaderNodeNewGeometry"); geo.location = (-400, 0)
    checker.inputs["Scale"].default_value = 1.0 / max(tile_size, 1e-3)
    checker.inputs["Color1"].default_value = rgba1
    checker.inputs["Color2"].default_value = rgba2
    L = nt.links.new
    L(geo.outputs["Position"], checker.inputs["Vector"])
    L(checker.outputs["Color"], bsdf.inputs["Base Color"])
    L(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


# ------------------------------------------------- the material library lives in `assets/`
# A MATERIAL COMES FROM THE ASSET KIT, NOT FROM THIS FILE'S CONSTANTS (2026-09-05, user-asked:
# "default materials assignment to assets from assets rather than assign different ones").
#
# WHAT WAS WRONG. `mat()` get-or-CREATED every material from the tables above, in whichever
# `.blend` happened to be building — so the look of the world was defined in Python, in a builder,
# and an artist who wanted a different asphalt had to edit code. Worse, it was not even ONE
# datablock: `road_kit.blend`'s profile sections carry their own `M_Concrete` (they were built by
# this same function, in that file), so a district that links a kerb section ends up with the
# kit's `M_Concrete` on the kerb and a locally-created `M_Concrete` on the deck beside it —
# identical, and two materials in the exported scene. Two owners of one fact, the shape this
# codebase keeps paying for.
#
# THE RULE NOW, and it is ONE lookup with a bootstrap, not a second registry:
#
#   1. a datablock of that name already in the file wins — a hand-edited local material, or the
#      library material already linked by an earlier call, both survive a rebuild exactly as
#      before (`bpy.data.materials.get` prefers a LOCAL datablock over a linked one, measured);
#   2. otherwise LINK it from the asset kit — so editing `road_kit.blend` restyles every road,
#      wall and building in the world, which is what an asset library is for;
#   3. otherwise build it from the tables above. That is the BOOTSTRAP — it is how
#      `build_road_kit.py` authors the library in the first place, and it is what keeps every
#      builder working in a checkout where the kit has not been built yet.
#
# Linked and not appended, for the same reason the profile sections are (`RKA_OT_link_road_kit`):
# an appended copy drifts, a linked one does not. A linked material IS writable from Python in a
# background export process (measured), so `export_world.py`'s base-colour flattening is
# unaffected.
_KIT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))), "assets", "world_source", "kit")

#: The one asset file every material is linked from. It also holds the road kit's profile
#: sections (`ROAD_KIT`) — one kit file, one link, one resolver.
MATERIAL_LIBRARY = os.path.join(_KIT_DIR, "road_kit.blend")

#: `build_road_kit.py` clears this: the tool that AUTHORS the library must not consult it.
USE_MATERIAL_LIBRARY = True

_library_tried = False


def material_name(key):
    """The datablock name a material key resolves to — one owner of the key -> name mapping."""
    if key in TILED_MATS:
        return TILED_MATS[key][0]
    return MATS[key][0]


def link_material_library(path=None):
    """Link every material the asset kit ships, once per session. Returns True if it linked."""
    global _library_tried
    if path is None:
        if _library_tried:
            return False
        _library_tried = True
        path = MATERIAL_LIBRARY
    if not os.path.exists(path):
        return False
    have = {m.name for m in bpy.data.materials}
    with bpy.data.libraries.load(path, link=True) as (src, dst):
        # Only what this file does not already have, and only the kit's own `M_*` set — a stray
        # `Material` left over from a default startup file is not a material anyone asked for.
        dst.materials = [n for n in src.materials if n.startswith("M_") and n not in have]
    return True


def mat(key):
    """The material for `key` — from the asset kit if it ships one, else built from the tables.

    See "the material library lives in `assets/`" above for why the kit wins."""
    name = material_name(key)
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    if USE_MATERIAL_LIBRARY and link_material_library():
        m = bpy.data.materials.get(name)
        if m is not None:
            return m
    if key in TILED_MATS:
        _n, c1, c2, tile_size = TILED_MATS[key]
        return get_tiled_mat(name, c1, c2, tile_size)
    return get_mat(name, MATS[key][1])


def set_mod_input(mod, socket_id, value):
    """Set a Geometry Nodes modifier's input by interface-socket identifier (e.g. `mat_sock.
    identifier`) -- the ONE place this happens, since the mechanism for it has already changed
    out from under this codebase once. Old Blender versions exposed GN modifier inputs as plain
    ID-properties (`mod[socket_id] = value` -- what every call site here used to do); this
    Blender build's `NodesModifier` no longer supports IDProperties at all (`mod.keys()` itself
    raises "this type doesn't support IDProperties") and instead exposes a structured
    `mod.properties.inputs` object whose per-socket attributes (named after the identifier, e.g.
    `.Socket_1`) are read-only POINTERs to a small per-socket struct carrying the actual mutable
    `.value` (confirmed empirically -- `setattr(mod.properties.inputs, socket_id, value)` raises
    "attribute ... is read-only"; `getattr(mod.properties.inputs, socket_id).value = value` is
    the one path that actually works). Centralizing this means a future API change again only
    needs fixing here, not at every one of the ~20 call sites this replaced."""
    getattr(mod.properties.inputs, socket_id).value = value


def get_mod_input(mod, socket_id):
    """Read a Geometry Nodes modifier's input by interface-socket identifier -- the read-side
    counterpart to set_mod_input(), see its docstring for why this indirection exists."""
    return getattr(mod.properties.inputs, socket_id).value


def _new_obj(name, me, coll, matkey):
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    if matkey:
        obj.data.materials.append(mat(matkey))
    return obj


# ----------------------------------------------------------------- primitives
def recalc_normals(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()


def box(name, x0, x1, y0, y1, z0, z1, coll, matkey=None):
    """Axis-aligned box. Object origin stays at world (0,0,0); the LOCAL pivot is
    wherever (0,0,0) falls relative to the verts you pass."""
    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    recalc_normals(me)
    return _new_obj(name, me, coll, matkey)


def cyl(name, radius, z0, z1, coll, matkey=None, seg=16):
    """Vertical cylinder, base centre at local (0,0,z0)."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=seg,
                          radius1=radius, radius2=radius, depth=(z1 - z0))
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, (z0 + z1) / 2.0))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    recalc_normals(me)
    return _new_obj(name, me, coll, matkey)


def gable(name, w, d, peak, coll, matkey="roof", eave=0.3):
    """Pitched gable roof prism. Ridge runs along X; eaves overhang by `eave`."""
    x0, x1 = -eave, w + eave
    y0, y1 = -eave, d + eave
    ym = d / 2.0
    verts = [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0),
             (x0, ym, peak), (x1, ym, peak)]
    faces = [(0, 1, 5, 4), (4, 5, 2, 3), (0, 4, 3), (1, 2, 5)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    recalc_normals(me)
    return _new_obj(name, me, coll, matkey)


def combine(name, parts, coll):
    """One mesh from several boxes, each with its own material.
       parts = [((x0,x1,y0,y1,z0,z1), matkey), ...]"""
    bm = bmesh.new()
    mats, midx = [], {}
    for (x0, x1, y0, y1, z0, z1), mk in parts:
        if mk not in midx:
            midx[mk] = len(mats); mats.append(mk)
        ret = bmesh.ops.create_cube(bm, size=1.0)
        vs = ret["verts"]
        bmesh.ops.scale(bm, vec=(x1-x0, y1-y0, z1-z0), verts=vs)
        bmesh.ops.translate(bm, vec=((x0+x1)/2, (y0+y1)/2, (z0+z1)/2), verts=vs)
        for f in {f for v in vs for f in v.link_faces}:
            f.material_index = midx[mk]
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    for mk in mats:
        obj.data.materials.append(mat(mk))
    return obj


def wedge(name, parts, coll):
    """One mesh from sloped slabs (a ramp deck). Each part:
       ((x0,x1, y0,y1, z_y0, z_y1, thick), matkey)
    builds a slab of vertical thickness `thick` whose TOP runs from z_y0 (at y0) to
    z_y1 (at y1) — the rise is along +Y. Same box topology, just y-dependent z."""
    bm = bmesh.new(); mats, midx = [], {}
    for (x0, x1, y0, y1, za, zb, th), mk in parts:
        if mk not in midx:
            midx[mk] = len(mats); mats.append(mk)
        vs = [bm.verts.new(p) for p in [
            (x0, y0, za-th), (x1, y0, za-th), (x1, y1, zb-th), (x0, y1, zb-th),
            (x0, y0, za),    (x1, y0, za),    (x1, y1, zb),    (x0, y1, zb)]]
        for fi in [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1),
                   (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]:
            bm.faces.new([vs[i] for i in fi]).material_index = midx[mk]
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    for mk in mats:
        obj.data.materials.append(mat(mk))
    return obj


def cut(target, x0, x1, y0, y1, z0, z1):
    """Boolean-difference a box opening out of `target` (applies + cleans up)."""
    cutter = box("__cutter", x0, x1, y0, y1, z0, z1, get_coll("__tmp"))
    m = target.modifiers.new("cut", 'BOOLEAN')
    m.operation = 'DIFFERENCE'
    m.object = cutter
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cutter, do_unlink=True)
    tmp = bpy.data.collections.get("__tmp")
    if tmp and not tmp.objects:
        bpy.data.collections.remove(tmp)


def prism(name, poly_pts_xy, z0, z1, coll, matkey=None):
    """A solid vertical prism over an arbitrary (not necessarily convex) closed 2D polygon
    `poly_pts_xy = [(x, y), ...]` (world/local XY, NOT closed -- no repeated first==last point),
    from world Z `z0` to `z1`. Same raw vert/face `from_pydata` style as `box()` (an N=4
    special-case of this), generalized to N points: N bottom verts + N top verts, one bottom N-gon,
    one top N-gon, N side quads. Winding order doesn't matter -- `recalc_normals` fixes it, same
    as every other mesh builder here."""
    n = len(poly_pts_xy)
    verts = [(x, y, z0) for (x, y) in poly_pts_xy] + [(x, y, z1) for (x, y) in poly_pts_xy]
    faces = [tuple(range(n)), tuple(range(2 * n - 1, n - 1, -1))]
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    recalc_normals(me)
    return _new_obj(name, me, coll, matkey)


def cut_polygon(target, poly_pts_xy, z0, z1):
    """Boolean-difference an arbitrary-footprint opening (`prism`, not a box) out of `target`
    (applies + cleans up) -- the same pattern as `cut()`, generalized from an axis-aligned box to
    a road's own curved footprint (a segment's curb/sidewalk offset lines, or a junction's
    `build_junction_boundary` boundary), so a cut follows the road's actual shape instead of
    over-cutting a bounding box around a bent/curved piece. `z0`/`z1` are ABSOLUTE world Z (not
    relative to `target`, since a cutter's own local transform is irrelevant to a boolean -- only
    world-space mesh geometry matters)."""
    cutter = prism("__cutter_poly", poly_pts_xy, z0, z1, get_coll("__tmp"))
    m = target.modifiers.new("cut", 'BOOLEAN')
    m.operation = 'DIFFERENCE'
    m.object = cutter
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cutter, do_unlink=True)
    tmp = bpy.data.collections.get("__tmp")
    if tmp and not tmp.objects:
        bpy.data.collections.remove(tmp)


# ----------------------------------------------------- collision proxy / export
def colonly(visual, coll=None, inset=0.0):
    """Author a `<Name>-colonly` box proxy spanning the visual's local bounds.
    On Godot import the visual is dropped and the box becomes a CollisionShape3D."""
    bb = [v[:] for v in visual.bound_box]      # 8 local corners
    xs = [p[0] for p in bb]; ys = [p[1] for p in bb]; zs = [p[2] for p in bb]
    x0, x1 = min(xs)+inset, max(xs)-inset
    y0, y1 = min(ys)+inset, max(ys)-inset
    z0, z1 = min(zs), max(zs)
    c = coll or (visual.users_collection[0] if visual.users_collection else get_coll("ENV"))
    p = box(visual.name + "-colonly", x0, x1, y0, y1, z0, z1, c, "col")
    p["proxy_for"] = visual.name
    return p


def colonly_mesh(visual, coll=None):
    """Author a `<Name>-colonly` proxy that COPIES the visual's own mesh geometry instead of its
    bounding box. Same Godot import contract as colonly() (the `-colonly` suffix drops the visual
    half on import and builds a CollisionShape3D from this mesh) -- but for an irregular real-world
    footprint (a PLATEAU building/bridge) a box proxy blocks empty space the visual never occupied
    (an L-shaped or angled building reads as a solid rectangle to collision), which shows up as an
    invisible wall in an area that looks walkable. Use this instead of colonly() wherever the
    visual mesh is already low-poly enough to serve directly as its own (concave) collider -- real
    PLATEAU buildings/bridges/landmarks, not modular kit pieces (those keep the box proxy).
    Deliberately NOT a convex-hull option: a bridge or archway's real open span under/through it
    would become solid under a convex hull (found on Rainbow Bridge during walk-testing), which is
    worse than the box proxy this replaces -- concave is the only shape that keeps real holes."""
    c = coll or (visual.users_collection[0] if visual.users_collection else get_coll("ENV"))
    me = visual.data.copy()
    p = bpy.data.objects.new(visual.name + "-colonly", me)
    c.objects.link(p)
    p.data.materials.clear()
    p.data.materials.append(mat("col"))
    p["proxy_for"] = visual.name
    return p


def colonly_mesh_evaluated(visual, coll=None, name=None):
    """Like `colonly_mesh()`, but for a GN-modifier-backed object (a Curve with a live Nodes
    modifier -- `junction_pad`/`road_spine`/`curb_loop`, all of the road_kit_authoring visual
    pieces) whose OWN `.data` is unevaluated source data (raw curve control points), not the real
    swept/filled/filleted mesh a viewer actually sees -- `colonly_mesh()`'s plain `visual.data.
    copy()` would copy the WRONG (pre-modifier) geometry for these. Evaluates the object through
    the depsgraph first (`bpy.data.meshes.new_from_object`, the same bake glTF export itself
    performs) so the collision proxy is an EXACT copy of the real, on-screen shape -- corner
    fillets, tapers, bends, all of it -- with zero hand-rolled approximation math to keep in sync.

    2026-07-27, user-reported/screenshotted (twice): road_kit_authoring's hand-rolled collision
    helpers (`colonly_polygon`'s corner-squared-off boundary ignoring fillet radius,
    `colonly_swept`'s per-vertex sweep) visibly diverged from the real curved/filleted visual mesh
    at corners -- a real, structural coarseness problem, not a one-off bug, and NOT what
    `colonly_mesh()` already does correctly for real-world (PLATEAU building/landmark) geometry.
    This closes that gap the same way: don't approximate, copy the truth. Replaces `colonly_polygon`
    (pad) and `colonly_swept`/`colonly_swept_between` (curb walls, pavement) as the road-piece
    collision source; `colonly_swept`'s own point-list-driven signature stays available for the few
    truly synthetic uses that were never GN-Curve objects to begin with (none as of this writing --
    kept for API stability, not because anything still calls it for road pieces).

    `name` overrides the proxy's own base name (default `visual.name`) -- needed for the pavement
    case specifically: the pavement collision proxy is built from `spine_<piece>` (the spine object
    itself is never deleted/recreated by any rebuild, per `rebuild_segment_gn_in_place`'s own
    docstring), but `clear_generated_mesh_objects`'s cleanup sweep matches a `pave_` prefix, not
    `spine_` -- passing `name="pave_<piece>"` keeps that convention (and the existing cleanup code)
    working unchanged; without it, a fresh `pave_`-less orphan would pile up on every rebuild.

    UPDATE-IN-PLACE (2026-08, the crash-surface fix -- the same reasoning as
    `_poly_curve_with_radius`): if an object named `base + "-colonly"` already exists in `c`, its
    MESH DATA is swapped for a freshly-evaluated one (`obj.data = new_mesh`, then free the old
    mesh block) instead of deleting/recreating the OBJECT -- reassigning `.data` on a live object
    is an ordinary, safe Blender operation (unlike `bpy.data.objects.remove`, which is what's
    actually unsafe to do to an object a modal Transform operator is still holding). This keeps
    the collision proxy's OWN object identity stable across a rebuild, same as the visual boundary
    it copies."""
    c = coll or (visual.users_collection[0] if visual.users_collection else get_coll("ENV"))
    base = name or visual.name
    deps = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(visual.evaluated_get(deps))
    existing = c.objects.get(base + "-colonly")
    if existing is not None and existing.type == 'MESH':
        old_data = existing.data
        existing.data = me
        existing.matrix_world = visual.matrix_world
        if old_data is not None and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
        me.materials.clear()
        me.materials.append(mat("col"))
        existing["proxy_for"] = base
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    p = bpy.data.objects.new(base + "-colonly", me)
    p.matrix_world = visual.matrix_world
    c.objects.link(p)
    p.data.materials.clear()
    p.data.materials.append(mat("col"))
    p["proxy_for"] = base
    p["_rka_touched"] = True
    return p


def _is_stack_carrier(obj):
    """A `road_stack` MESH carrier: the whole road as one object's modifier stack.

    Structural test (a `GN_SpineCurve` modifier), not a name guess -- the same question
    `road_kit_authoring.spine_io.is_stack_carrier` answers, restated here because `lib/` must not
    import the addon. Keep the two in sync; there are only these two."""
    return (obj.type == 'MESH' and obj.name.startswith("spine_")
            and any(m.type == 'NODES' and m.node_group
                    and m.node_group.name == "GN_SpineCurve" for m in obj.modifiers))


def bake_colonly_proxies(objects, target_coll):
    """Generate a `-colonly` collision proxy for every `pad_*`/`curb_*`/`spine_*` GN-modified
    Curve object in `objects` (typically a collection's own `.objects`, or a whole scene's),
    linking each into `target_coll`. 2026-08: this is the EXPORT-TIME replacement for what used
    to be baked live in Blender during authoring/rebuild (`ops_intersection._populate_
    intersection_mesh`/`ops_segment._populate_segment_mesh_gn`/`_populate_transition_visuals`
    each called `colonly_mesh_evaluated` directly) -- moved here because a `-colonly` proxy has
    ZERO authoring-time value (it's invisible, existing purely so Godot's importer builds a
    `CollisionShape3D`) while being the single most expensive AND most crash-prone live rebuild
    operation (a `to_mesh()` depsgraph bake, confirmed to intermittently segfault even in
    unmodified code in this Blender build). Moving it to export time removes that entire cost/
    risk from live editing with no change to Godot's output -- same bake, just deferred to when
    it's actually needed. `tools/export_world.py` calls this once over the whole loaded scene
    right before glTF export.

    Identifies candidates precisely (a modifier check, not a name-prefix guess) so it can never
    accidentally catch an ASSET-style curb instancer (no "Pad"/"Curb"/"Road" modifier) or
    anything unrelated: `junction_pad`/`curb_loop`-built objects (the "Pad"/"Curb" modifier
    covers pad, L/R curb, median, AND sidewalk objects uniformly -- they all go through
    `curb_loop`) and `road_spine`-built pavement (the "Road" modifier), the latter renamed
    `pave_<piece>` to match `colonly_mesh_evaluated`'s existing `name=` convention (the spine
    object itself is `spine_<piece>`, never `pave_<piece>` -- see that function's own docstring
    for why the override exists).

    `join_visual_mesh=True` special case: `join_meshes` bakes pad_/curb_/spine_'s GN modifiers
    away and combines them into ONE plain `mesh_<piece>` object BEFORE export ever runs, so by
    bake time there's no separate Pad/Curb/Road-modified Curve left for any of the branches above
    to find. A `mesh_*` object (plain MESH, no live modifier -- already fully realized by the
    join) gets ONE colonly proxy covering its whole combined footprint via the simpler
    `colonly_mesh` (a plain data copy -- no depsgraph evaluation needed, there's no live
    modifier left to evaluate through), rather than the fragmented per-piece colonlies a
    non-joined build would get. This is a deliberate, arguably better outcome, not a compromise:
    `join_visual_mesh` exists specifically to reduce object count, so collapsing collision the
    same way the visual already did is consistent with that intent.

    Returns the list of created/updated proxy objects (idempotent via `colonly_mesh_evaluated`'s
    own update-in-place logic for the GN cases; `colonly_mesh`'s plain-copy case is delete/
    recreate, which is fine -- this only ever runs at export time, never mid-drag)."""
    out = []
    for o in list(objects):
        if o.type == 'CURVE':
            if o.modifiers.get("Pad") is not None or o.modifiers.get("Curb") is not None:
                out.append(colonly_mesh_evaluated(o, target_coll))
            elif o.modifiers.get("Road") is not None and o.name.startswith("spine_"):
                out.append(colonly_mesh_evaluated(o, target_coll,
                                                    name="pave_" + o.name[len("spine_"):]))
        elif _is_stack_carrier(o):
            # A MODIFIER-STACK piece is ONE object carrying its entire road -- pavement, curbs,
            # sidewalks, median, the lot -- so it gets ONE proxy covering all of it, for the same
            # reason `join_visual_mesh` does: there is nothing else to make a separate proxy from.
            # Named `pave_<piece>` to match the Curve spine's own convention above, so the
            # existing cleanup sweeps and `proxy_for` tagging keep working unchanged.
            out.append(colonly_mesh_evaluated(o, target_coll,
                                                name="pave_" + o.name[len("spine_"):]))
        elif o.type == 'MESH' and o.name.startswith("mesh_"):
            existing = target_coll.objects.get(o.name + "-colonly")
            if existing is not None:
                bpy.data.objects.remove(existing, do_unlink=True)
            out.append(colonly_mesh(o, target_coll))
    return [p for p in out if p is not None]


# ------------------------------------------------------------- lane centerlines
def centerlines_from_vertex_group(obj, group_name="lanedata"):
    """Extract ordered, directional lane polylines from `group_name` on mesh `obj`. Reused by the
    interactive `RKA_OT_centerline_from_vertex_group` operator (road_kit_authoring addon) so kit
    pieces get their lane data from hand-tagged mesh topology instead of a guessed bbox midline.

    Each edge-connected component of tagged vertices is treated as ONE lane -- this is what lets
    a single 'lanedata' group carry several distinct lane paths in one mesh (e.g. every turn
    movement through an intersection piece), disambiguated purely by mesh connectivity, no extra
    grouping convention needed. A component must be a simple path (both ends degree <=1) or a
    closed loop (every vertex degree 2); anything branchier is a tagging mistake, not a lane.

    Direction: a non-loop lane's point order (index 0 = tail, index -1 = head) follows the tagged
    verts' GROUP WEIGHT when the two path endpoints differ (lower weight = tail) -- tag weight 0.0
    at a lane's start and 1.0 at its end (Blender's Vertex Weights panel) to make direction
    explicit. This is what lets ONE lane-tile mesh serve both directions of a 2-way street: place
    it normally for one direction, rotate it 180 degrees for the other -- the weight travels with
    the vertex data, so which physical end is "head" survives the rotation even though the
    topological walk alone (arbitrary tie-break) would not. Equal/default weights (e.g. both ends
    at the vertex-group default of 1.0) fall back to the topological walk order -- no behavior
    change for data tagged before this convention existed.

    Returns (lanes, warnings): `lanes` is a list of {"points": [world-space Vector, ...],
    "loop": bool}; `warnings` is a list of human-readable strings (isolated tagged verts with no
    edge partner -- skipped, not an error, since a piece may be tagged incrementally)."""
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        return [], []
    gi = vg.index
    me = obj.data
    tagged = {}
    for v in me.vertices:
        for g in v.groups:
            if g.group == gi:
                tagged[v.index] = g.weight
    if not tagged:
        return [], []

    adjacency = {vi: set() for vi in tagged}
    for e in me.edges:
        a, b = e.vertices[0], e.vertices[1]
        if a in tagged and b in tagged:
            adjacency[a].add(b)
            adjacency[b].add(a)

    mw = obj.matrix_world
    lanes, warnings = [], []
    visited = set()
    for start in sorted(tagged):
        if start in visited:
            continue
        component, stack = set(), [start]
        while stack:
            v = stack.pop()
            if v in component:
                continue
            component.add(v)
            stack.extend(adjacency[v] - component)
        visited |= component

        if len(component) == 1:
            warnings.append("%s: isolated tagged vertex %d has no edge partner -- skipped"
                             % (obj.name, start))
            continue

        degrees = {v: len(adjacency[v]) for v in component}
        branchy = [v for v, d in degrees.items() if d > 2]
        if branchy:
            raise ValueError(
                "%s: vertex %d in '%s' has %d tagged neighbours -- a lane centerline must be a "
                "simple path or loop, not a branch" % (obj.name, branchy[0], group_name, degrees[branchy[0]]))

        endpoints = [v for v, d in degrees.items() if d <= 1]
        is_loop = not endpoints
        start_v = endpoints[0] if endpoints else next(iter(component))

        ordered = [start_v]
        prev, cur = None, start_v
        while True:
            nxt = [n for n in adjacency[cur] if n != prev]
            if not nxt or nxt[0] == ordered[0]:
                break
            cur, prev = nxt[0], cur
            ordered.append(cur)

        if not is_loop and tagged[ordered[0]] > tagged[ordered[-1]]:
            ordered.reverse()

        lanes.append({"points": [mw @ me.vertices[vi].co for vi in ordered], "loop": is_loop})
    return lanes, warnings


def lane_marking_strip(name, x_center, y0, y1, z, width, matkey, coll):
    """Thin flat lane-boundary marking, centered on local `x_center`, spanning `y0..y1`, sitting
    on the road surface at height `z`. `matkey` is 'line_w' (same-direction divider) or 'line_y'
    (opposite-direction divider) from `MATS`. Used by `RKA_OT_combine_lanes` to mark the seam
    between two adjacent lane tiles -- kept as its own small object (not joined into the lane
    mesh) so it can later be swapped for a dashed/textured decal without touching lane geometry."""
    return box(name, x_center - width / 2.0, x_center + width / 2.0, y0, y1, z, z + 0.01, coll, matkey)


def poly_curve(name, pts, coll, loop=False, lane_width=None, oneway=True, end_behavior='CHAIN'):
    """A plain POLY-spline Curve object through pts=[(x,y,z), ...] EXACTLY (no NURBS smoothing/
    approximation -- every point is a real control point on a straight-segment spline). Same
    `lanecl_*`-shape object `RKA_OT_centerline_from_vertex_group` builds by hand from tagged mesh
    topology (see ops_centerline.py) -- this is the computed-geometry counterpart, used by
    `RKA_OT_build_intersection` for corner/turn centerlines generated from `lib/intersection_kit.py`.
    Sets `curve.rka_curve` (lane_width/oneway/loop/end_behavior) when `lane_width` is given.

    UPDATE-IN-PLACE (2026-08, the crash-surface fix): same convention as
    `_poly_curve_with_radius` -- an existing object named `name` in `coll` with a matching point
    count is rewritten in place rather than deleted/recreated (this is `lanecl_*`'s own builder,
    the last remaining delete-recreate surface in the live-drag hot path)."""
    existing = coll.objects.get(name)
    if (existing is not None and existing.type == 'CURVE' and existing.data.splines
            and len(existing.data.splines[0].points) == len(pts)):
        # The shape is baked as ABSOLUTE world-space coordinates directly into the point data
        # (not via the object's own transform), so the object's transform must always stay at
        # identity -- but a reused object's transform is NOT implicitly reset just by rewriting
        # its point data (unlike the old delete/recreate path, where a fresh object always
        # started at identity for free). A real Grab/Rotate on a piece selection (`RKA_OT_
        # select_piece` selects these generated objects too, not just markers) can leave this
        # object's own location/rotation non-zero, which then double-transforms the already-
        # absolute point data on every subsequent rebuild -- confirmed root cause of "arm/pad
        # generation in a strange shape that's still wrong after releasing the drag" (2026-08).
        existing.location = (0.0, 0.0, 0.0)
        existing.rotation_euler = (0.0, 0.0, 0.0)
        existing.scale = (1.0, 1.0, 1.0)
        sp = existing.data.splines[0]
        for i, (x, y, z) in enumerate(pts):
            sp.points[i].co = (x, y, z, 1.0)
        sp.use_cyclic_u = loop
        if lane_width is not None:
            existing.data.rka_curve.lane_width = lane_width
            existing.data.rka_curve.oneway = oneway
            existing.data.rka_curve.loop = loop
            existing.data.rka_curve.end_behavior = end_behavior
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts) - 1)
    for i, (x, y, z) in enumerate(pts):
        sp.points[i].co = (x, y, z, 1.0)
    sp.use_cyclic_u = loop
    if lane_width is not None:
        cu.rka_curve.lane_width = lane_width
        cu.rka_curve.oneway = oneway
        cu.rka_curve.loop = loop
        cu.rka_curve.end_behavior = end_behavior
    obj = bpy.data.objects.new(name, cu)
    coll.objects.link(obj)
    obj["_rka_touched"] = True
    return obj


def flat_ribbon(name, pts, half_width, coll, matkey="asphalt"):
    """A flat, constant-`half_width` quad-strip mesh following the 3D polyline pts=[(x,y,z), ...]
    EXACTLY (same tangent-offset technique as `swept_wall`, just horizontal instead of vertical) --
    the visual driving surface under a computed lane centerline (see `poly_curve` /
    `lib/intersection_kit.py`), so a generated turn reads as an actual road, not a bare line.

    UPDATE-IN-PLACE (2026-08, the crash-surface fix -- same convention as `marking_ribbon`,
    which this mirrors exactly: a dash/gap-free ribbon's vertex count is stable, but swapping an
    existing object's MESH DATA is safe regardless of whether the new topology matches the old
    one, unlike deleting the OBJECT, which is what's actually unsafe mid-drag -- so every
    `ribbon_*` rebuild reuses its object by name unconditionally). This closes the last
    identity-crash surface in `ops_segment`'s legacy point-segment rebuild path
    (`rebuild_segment_in_place`), the one still-live-editable piece shape that never got the
    GN-modifier treatment `road_spine`/`GN_RoadProfile` gave the newer curve-backed segments."""
    n = len(pts)
    if n < 2:
        return None
    verts, faces = [], []
    for i, (x, y, z) in enumerate(pts):
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        L = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / L * half_width, tx / L * half_width
        verts += [(x - nx, y - ny, z), (x + nx, y + ny, z)]
    for i in range(n - 1):
        a, b = i * 2, (i + 1) * 2
        faces.append((a, a + 1, b + 1, b))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    recalc_normals(me)
    existing = coll.objects.get(name)
    if existing is not None and existing.type == 'MESH':
        existing.location = (0.0, 0.0, 0.0)
        existing.rotation_euler = (0.0, 0.0, 0.0)
        existing.scale = (1.0, 1.0, 1.0)
        old_data = existing.data
        existing.data = me
        if old_data is not None and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
        me.materials.clear()
        me.materials.append(mat(matkey))
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    obj.data.materials.append(mat(matkey))
    obj["_rka_touched"] = True
    return obj


def marking_ribbon(name, pts, half_width, coll, matkey, dash_len=0.0, gap_len=0.0,
                    exclude_ranges=None, z_lift=0.01):
    """Flat marking strip following `pts` EXACTLY (same tangent-offset quad-strip technique as
    `flat_ribbon`), lifted `z_lift` (default 0.01m) above the pavement it rides on -- otherwise
    the marking is exactly coplanar with the road surface (both sample the same spine Z) and
    z-fights with it in render (2026-07-28, user-requested). Optionally DASHED (both `dash_len`
    and `gap_len` > 0; either <= 0 means solid -- one continuous strip) and/or with
    `exclude_ranges` ([(t0, t1), ...], t = normalized cumulative arc length along `pts`, 0 at
    pts[0] / 1 at pts[-1]) fully OMITTED -- the
    'survive a live-edit rebuild' answer to clearing a marking across a driveway/merge zone:
    `exclude_ranges` is meant to be read back from the owning segment's `rka_marking_gaps`
    custom property on every rebuild, never from hand-deleting a generated object (which the
    addon's delete-and-rebuild-from-scratch cleanup would silently recreate on the next drag).

    Deliberately does NOT resample `pts` at a fixed spacing (an earlier version called
    `sample_polyline(pts, 0.25)` unconditionally, which put a vertex every 25 cm regardless of the
    road spine's own control-point density -- a 40 m straight two-point segment's SOLID yellow
    line got ~160 quads for what needs exactly one, since `road_spine`/`_spine_control_points`
    already establish that the pavement itself is straight between consecutive control points, no
    resolution subdivision -- see their docstrings). Instead this walks `pts` at ITS OWN
    resolution and inserts extra vertices ONLY exactly where something actually changes: a dash
    on/off transition, or an exclude-range boundary -- so a solid, ungapped marking on a 2-point
    straight spine is exactly 2 vertices/1 quad, matching the pavement's own resolution, while a
    dashed one only gets the handful of extra vertices its dash cycle actually needs, placed at
    the exact transition arc-length (not snapped to a sampling grid). Returns None if every
    sub-run ends up skipped (e.g. a gap spanning the whole line)."""
    exclude_ranges = exclude_ranges or []
    n = len(pts)
    if n < 2:
        return None
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total_len = cum[-1]
    if total_len < 1e-6:
        return None
    dashed = dash_len > 0.0 and gap_len > 0.0
    cycle = dash_len + gap_len

    # Breakpoints: every ORIGINAL point's own arc length (the spine's own resolution) plus every
    # dash on/off transition and exclude-range boundary that actually falls on this line --
    # inserted exactly where needed instead of marching a fixed step across the whole length.
    breaks = set(cum)
    if dashed:
        k = 0
        while k * cycle < total_len:
            on_end = k * cycle + dash_len
            if 0.0 < on_end < total_len:
                breaks.add(on_end)
            off_end = (k + 1) * cycle
            if 0.0 < off_end < total_len:
                breaks.add(off_end)
            k += 1
    for t0, t1 in exclude_ranges:
        a, b = t0 * total_len, t1 * total_len
        if 0.0 < a < total_len:
            breaks.add(a)
        if 0.0 < b < total_len:
            breaks.add(b)
    positions = sorted(breaks)

    def point_at(s):
        """(x, y, z), heading_rad at arc length `s` along the ORIGINAL `pts` (linear
        interpolation within whichever original segment contains it -- never off the spine's own
        straight chords)."""
        s = max(0.0, min(total_len, s))
        i = 1
        while i < n - 1 and cum[i] < s:
            i += 1
        a, b = pts[i - 1], pts[i]
        seg_len = cum[i] - cum[i - 1]
        t = 0.0 if seg_len < 1e-9 else (s - cum[i - 1]) / seg_len
        pos = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
        return pos, math.atan2(b[1] - a[1], b[0] - a[0])

    def excluded(t):
        return any(t0 <= t <= t1 for (t0, t1) in exclude_ranges)

    verts, faces = [], []
    run = []   # arc-length positions of a contiguous "on" run, flushed into a quad-strip

    def flush():
        if len(run) < 2:
            run.clear()
            return
        base = len(verts)
        for s in run:
            (x, y, z), hd = point_at(s)
            nx, ny = -math.sin(hd) * half_width, math.cos(hd) * half_width
            z += z_lift
            verts.extend([(x - nx, y - ny, z), (x + nx, y + ny, z)])
        for i in range(len(run) - 1):
            a, b = base + i * 2, base + (i + 1) * 2
            faces.append((a, a + 1, b + 1, b))
        run.clear()

    for j in range(len(positions) - 1):
        s0, s1 = positions[j], positions[j + 1]
        if s1 - s0 < 1e-9:
            continue
        mid = (s0 + s1) / 2.0
        on = (not excluded(mid / total_len)) and (not dashed or (mid % cycle) < dash_len)
        if on:
            if not run:
                run.append(s0)
            run.append(s1)
        else:
            flush()
    flush()
    if not verts:
        return None
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    recalc_normals(me)
    # UPDATE-IN-PLACE (2026-08, the crash-surface fix): a dash pattern's vertex/face count varies
    # with segment length/dash settings, so this can't reuse `_poly_curve_with_radius`'s "same
    # point count" check -- but swapping an existing object's MESH DATA is safe regardless of
    # whether the new topology matches the old (unlike deleting the OBJECT, which is what's
    # actually unsafe mid-drag), so every mark_* rebuild reuses its object by name unconditionally.
    existing = coll.objects.get(name)
    if existing is not None and existing.type == 'MESH':
        # Same identity-transform requirement as `_poly_curve_with_radius` -- `verts` above are
        # absolute world-space coordinates, so a reused object's transform must be reset to
        # identity, not left however a prior Grab/Rotate on the piece selection may have set it.
        existing.location = (0.0, 0.0, 0.0)
        existing.rotation_euler = (0.0, 0.0, 0.0)
        existing.scale = (1.0, 1.0, 1.0)
        old_data = existing.data
        existing.data = me
        if old_data is not None and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
        me.materials.clear()
        me.materials.append(mat(matkey))
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    obj.data.materials.append(mat(matkey))
    obj["_rka_touched"] = True
    return obj


def swept_profile(name, pts, profile_2d, coll, matkey="concrete"):
    """A solid mesh sweeping an arbitrary 2D cross-section (`profile_2d`, an ordered list of
    `(lateral_offset, height)` pairs) along the 3D polyline `pts=[(x,y,z), ...]` EXACTLY -- the
    same tangent/right-normal-offset technique as `swept_wall`/`flat_ribbon`, generalized from a
    fixed rectangle/ribbon to any cross-section (e.g. a curb-and-gutter L-shape, see
    `gutter_curb_profile`). `lateral_offset` is signed distance from the path (right-hand normal,
    same sign convention as `swept_wall`'s `thickness`); `height` is added to each point's Z.
    Produces the walls between consecutive profile points only (no end caps or a top/bottom
    closing face) -- fine for a curb/gutter that abuts other geometry on its unseen sides; add
    caps yourself if the profile is meant to be a free-standing closed solid.

    UPDATE-IN-PLACE (2026-08, the crash-surface fix) -- same convention as `swept_wall`/
    `flat_ribbon`, closing the legacy GUTTER-style curb's identity-crash surface."""
    n = len(pts)
    m = len(profile_2d)
    if n < 2 or m < 2:
        return None
    verts, faces = [], []
    for i, (x, y, z) in enumerate(pts):
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        L = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / L, tx / L   # unit right-normal
        for off, h in profile_2d:
            verts.append((x + nx * off, y + ny * off, z + h))
    for i in range(n - 1):
        for j in range(m - 1):
            a, b = i * m + j, (i + 1) * m + j
            faces.append((a, a + 1, b + 1, b))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    recalc_normals(me)
    existing = coll.objects.get(name)
    if existing is not None and existing.type == 'MESH':
        existing.location = (0.0, 0.0, 0.0)
        existing.rotation_euler = (0.0, 0.0, 0.0)
        existing.scale = (1.0, 1.0, 1.0)
        old_data = existing.data
        existing.data = me
        if old_data is not None and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
        me.materials.clear()
        me.materials.append(mat(matkey))
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    obj.data.materials.append(mat(matkey))
    obj["_rka_touched"] = True
    return obj


def gutter_curb_profile(width, height):
    """A simple 'city gutter' curb cross-section -- flush road-facing apron stepping up to a
    flat-topped curb face -- matching the overall SILHOUETTE of the hand-modeled
    `kit_side_straight_city_gutter_curb_w0p6m_l5m` piece (`kit/lane_kit.blend`, inspected
    read-only, never modified) reduced to just its width/height (per the author's own
    instruction -- not a literal geometry extraction, which that piece's actual topology doesn't
    trivially reduce to for an arbitrary-length swept curve). Points ordered by increasing
    lateral offset: road edge (flush, height 0) -> apron edge (flush) -> curb base -> curb top."""
    return [(0.0, 0.0), (width * 0.4, 0.0), (width * 0.4, height), (width, height)]


def export_gltf(objs, filepath):
    """Export the given objects (+ their data) to a .glb at filepath."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    for o in bpy.data.objects:
        o.select_set(False)
    for o in objs:
        o.hide_set(False)
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.export_scene.gltf(filepath=filepath, use_selection=True,
                              export_format='GLB', export_apply=True)
    print("exported", filepath)


def save_blend(here, fname):
    out = os.path.join(here, fname)
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print("Saved", out)
    return out


# -------------------------------------------------------- Geometry-Nodes core
def make_gn_group():
    """GN_Instance: Object Info (As Instance) -> Instance on Points, with an
    optional per-point FLOAT_VECTOR `rot` attribute driving instance Rotation.
    Returns (node_group, object_socket_identifier)."""
    ng = bpy.data.node_groups.get("GN_Instance")
    if ng:
        return ng, ng["obj_id"]
    ng = bpy.data.node_groups.new("GN_Instance", "GeometryNodeTree")
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    obj_sock = ifc.new_socket("Object", in_out="INPUT", socket_type="NodeSocketObject")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-500, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (400, 0)
    oi = ng.nodes.new("GeometryNodeObjectInfo"); oi.location = (-250, -180)
    oi.transform_space = "ORIGINAL"
    if "As Instance" in oi.inputs:
        oi.inputs["As Instance"].default_value = True
    attr = ng.nodes.new("GeometryNodeInputNamedAttribute"); attr.location = (-250, 180)
    attr.data_type = "FLOAT_VECTOR"
    attr.inputs["Name"].default_value = "rot"
    iop = ng.nodes.new("GeometryNodeInstanceOnPoints"); iop.location = (100, 0)
    # Realize the instances into real mesh at the group OUTPUT. Without this the modifier emits bare
    # 'Instance on Points' instances, which render fine in Blender but the glTF exporter DROPS (they
    # collapse to the source at origin) — so every instanced layer (streetwall, road tiles, props)
    # piled up at center in the exported district pieces. Realizing bakes the placement into geometry
    # the exporter keeps. (Cost is Blender-side render/export memory only; the game uses the baked
    # .tscn, and the .blend still stores the compact GN setup, not realized geometry.)
    real = ng.nodes.new("GeometryNodeRealizeInstances"); real.location = (250, 0)
    L = ng.links.new
    L(nin.outputs["Geometry"], iop.inputs["Points"])
    L(nin.outputs["Object"], oi.inputs["Object"])
    L(oi.outputs["Geometry"], iop.inputs["Instance"])
    L(attr.outputs["Attribute"], iop.inputs["Rotation"])
    L(iop.outputs["Instances"], real.inputs["Geometry"])
    L(real.outputs["Geometry"], nout.inputs["Geometry"])
    ng["obj_id"] = obj_sock.identifier
    return ng, obj_sock.identifier


def src(name):
    o = bpy.data.objects.get(name)
    if o is None:
        raise RuntimeError("missing source object: " + name)
    return o


# ---- scene-instance MARKER mode (for the district-piece pipeline) --------------------------------
# When ON, world-space (parent=None) placements emit an `instance_<piece>` EMPTY carrying an
# `asset_path` meta = MARKER_KIT_DIR/<piece>.glb, instead of GN-instancing. The Java WorldBaker then
# swaps each into a real scene `instance=` reference to the kit leaf .glb — so a district piece is a
# tiny list of references (not baked mega-geometry) AND inherits the leaf's -colonly collision for
# free (BLENDER_CONVENTIONS "nested instancing"). Parented placements (tower modules via place_side)
# keep GN — they are few and stay real geometry. glTF cannot carry res:// instances, hence the marker.
MARKER_MODE = False
MARKER_KIT_DIR = "res://src/main/resources/com/openworld/world/kit/"


def _emit_markers(name, coords, piece_name, coll, loc, rot_z, rots):
    """Emit one instance_ marker per point (world space): pos = Rz(rot_z)·point + loc, and the
    per-marker Z rotation folds in rot_z + the point's own `rots` z. asset_path -> the kit .glb."""
    asset = MARKER_KIT_DIR + piece_name + ".glb"
    lx, ly, lz = loc
    a = math.radians(rot_z); ca, sa = math.cos(a), math.sin(a)
    for i, c in enumerate(coords):
        px, py, pz = c
        # mmesh_ -> WorldBaker folds all same-asset markers into ONE MultiMeshInstance3D (GPU
        # instancing: one node + one draw call, and NO collision body). Collision comes from
        # separate coarse proxies, so a dense district stays under the physics-body cap.
        e = bpy.data.objects.new("mmesh_" + piece_name, None)
        e.empty_display_size = 0.4
        e.location = (ca * px - sa * py + lx, sa * px + ca * py + ly, pz + lz)
        rz = rot_z + (math.degrees(rots[i][2]) if rots is not None else 0.0)
        e.rotation_euler = (0, 0, math.radians(rz))
        e["asset_path"] = asset
        coll.objects.link(e)
    return None


_PROXY_MESH_CACHE = {}   # asset_path -> imported visual Mesh datablock (imported once, reused)


def _res_to_abspath(res_path):
    """res://... -> absolute filesystem path (repo root is 3 dirnames up from blender/lib/kit_common.py:
    lib -> blender -> repo root)."""
    if not res_path.startswith("res://"):
        return res_path
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo_root, res_path[len("res://"):])


def _attach_proxy(marker, asset_path, coll):
    """Import (once per unique asset_path — cached in _PROXY_MESH_CACHE) the kit .glb's VISUAL
    mesh only (skips any -colonly/-convcolonly collision-only object) and parent a linked
    duplicate (shared Mesh datablock, no geometry copy — same pattern as Blender's own Alt+D) under
    `marker` at local identity, so it moves/rotates with the marker but costs no extra mesh data
    per placement. Purely a viewport aid: WorldBaker's freeEmpty() discards the whole marker
    (proxy included) once it resolves asset_path, so this never reaches the baked output — verified
    producing byte-identical `instance=` results with or without it (see AUTHORING_GUIDE.md)."""
    mesh_data = _PROXY_MESH_CACHE.get(asset_path)
    if asset_path not in _PROXY_MESH_CACHE:
        abspath = _res_to_abspath(asset_path)
        if not abspath or not os.path.exists(abspath):
            mesh_data = None   # can't preview (e.g. asset not staged yet) — marker still bakes fine
        else:
            before = set(bpy.data.objects)
            bpy.ops.import_scene.gltf(filepath=abspath)
            imported = [o for o in bpy.data.objects if o not in before]
            visual = next((o for o in imported if o.type == 'MESH'
                           and not o.name.endswith(("-colonly", "-convcolonly"))), None)
            mesh_data = visual.data if visual else None
            for o in imported:   # drop the temporary import objects — only the Mesh datablock is kept
                bpy.data.objects.remove(o, do_unlink=True)
        _PROXY_MESH_CACHE[asset_path] = mesh_data
    if mesh_data is None:
        return
    proxy = bpy.data.objects.new(marker.name + "_proxy", mesh_data)
    coll.objects.link(proxy)
    proxy.parent = marker
    proxy.matrix_parent_inverse = Matrix.Identity(4)
    proxy.hide_select = True   # visual aid only — don't let it get accidentally moved/selected


def instance_marker(name, asset_path, loc, rot_z, coll, show_proxy=True):
    """Place an `instance_`/`asset_path` marker — the hand-authoring contract
    `WorldBaker.buildInstance` resolves at bake time (see BLENDER_CONVENTIONS.md "Nested
    instancing") — at `loc`/`rot_z`, optionally with a REAL visual proxy (see `_attach_proxy`) so
    you see the actual kit piece while hand-placing it, not a bare axis gizmo. Use this (not a
    bare `bpy.data.objects.new` + manual `asset_path` meta) for any hand-crafted building/district
    content going forward — it's the same marker contract either way, just with the viewport aid
    built in. Distinct from `_emit_markers` (the `mmesh_` bulk-content path) — that one stays
    proxy-free on purpose (thousands of repeats would make the proxy overhead real; a hand-placed
    building is a few dozen pieces at most)."""
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = 'PLAIN_AXES'
    e.empty_display_size = 0.3
    e.location = loc
    e.rotation_euler = (0, 0, math.radians(rot_z))
    e["asset_path"] = asset_path
    coll.objects.link(e)
    if show_proxy:
        _attach_proxy(e, asset_path, coll)
    return e


def instancer(name, coords, piece, coll, loc=(0, 0, 0), rot_z=0.0, parent=None, rots=None):
    """Instance `piece` (object or source-name) at each point in `coords`.
    rots: optional list of (rx,ry,rz) radians per point -> per-instance rotation.

    UPDATE-IN-PLACE (2026-08, the crash-surface fix -- same reasoning as
    `_poly_curve_with_radius`/`colonly_mesh_evaluated`): if an object named `name` already exists
    in `coll` with a "GN" Nodes modifier, its point-cloud MESH DATA is swapped in place instead of
    deleting/recreating the object -- this is what makes ASSET-style curb (`curb_asset_row`, this
    addon's only live-edit-hot-path caller of `instancer`) safe to leave alive across a rebuild,
    same as the swept-profile curb styles already are."""
    if not coords:
        return None
    piece_name = piece if isinstance(piece, str) else piece.name
    if MARKER_MODE and parent is None:            # world-space layer -> scene-instance markers
        return _emit_markers(name, coords, piece_name, coll, loc, rot_z, rots)
    ng, obj_id = make_gn_group()
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(c) for c in coords], [], [])
    me.update()
    if rots is not None:
        a = me.attributes.new("rot", 'FLOAT_VECTOR', 'POINT')
        for i, rv in enumerate(rots):
            a.data[i].vector = rv
    existing = coll.objects.get(name)
    if existing is not None and existing.type == 'MESH' and existing.modifiers.get("GN") is not None:
        old_data = existing.data
        existing.data = me
        existing.location = loc
        existing.rotation_euler = (0, 0, math.radians(rot_z))
        if old_data is not None and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
        set_mod_input(existing.modifiers["GN"], obj_id,
                      piece if isinstance(piece, bpy.types.Object) else src(piece))
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    if existing is not None:
        # A same-named object exists but isn't a reusable instancer (e.g. a curb style switched
        # from BOX/GUTTER -- a Curve with a "Curb" modifier -- to ASSET) -- delete it explicitly
        # so the fresh object below claims the clean `name` instead of Blender auto-suffixing a
        # ".001" onto it (which would silently break every exact-name lookup elsewhere, e.g.
        # `coll.objects["curb_<piece>_L"]`), matching `_poly_curve_with_radius`'s own fallback.
        bpy.data.objects.remove(existing, do_unlink=True)
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    obj.location = loc
    obj.rotation_euler = (0, 0, math.radians(rot_z))
    if parent is not None:
        obj.parent = parent
        obj.matrix_parent_inverse = Matrix.Identity(4)
    mod = obj.modifiers.new("GN", "NODES")
    mod.node_group = ng
    set_mod_input(mod, obj_id, piece if isinstance(piece, bpy.types.Object) else src(piece))
    obj["_rka_touched"] = True
    return obj


def make_gn_group_scaled():
    """Like GN_Instance but also reads a per-point FLOAT_VECTOR `scl` -> instance Scale
    (e.g. tapered ramp piers: one unit pillar scaled to each cell's height; a curb/sidewalk/prop
    row instance MIRRORED via a negative axis instead of rotated -- see `curb_asset_row`'s R-side
    docstring).

    2026-08: added the same 'Realize Instances' node `make_gn_group` already has and explains
    ("the glTF exporter DROPS [bare instances]... collapse to the source at origin") -- this group
    never had one, so anything built through `instancer_scaled` (tapered piers, and now a mirrored
    curb/sidewalk/prop row) silently exported empty/at-origin. Confirmed directly: `to_mesh()` on
    an un-realized instance object returns ZERO vertices, not just "wrong at export time" -- any
    Python code (verification, collision baking) reading the evaluated mesh was equally broken."""
    ng = bpy.data.node_groups.get("GN_InstanceScaled")
    if ng:
        return ng, ng["obj_id"]
    ng = bpy.data.node_groups.new("GN_InstanceScaled", "GeometryNodeTree")
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    obj_sock = ifc.new_socket("Object", in_out="INPUT", socket_type="NodeSocketObject")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-500, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (400, 0)
    oi = ng.nodes.new("GeometryNodeObjectInfo"); oi.location = (-250, -180)
    oi.transform_space = "ORIGINAL"
    if "As Instance" in oi.inputs:
        oi.inputs["As Instance"].default_value = True
    rot = ng.nodes.new("GeometryNodeInputNamedAttribute"); rot.location = (-250, 200)
    rot.data_type = "FLOAT_VECTOR"; rot.inputs["Name"].default_value = "rot"
    scl = ng.nodes.new("GeometryNodeInputNamedAttribute"); scl.location = (-250, 60)
    scl.data_type = "FLOAT_VECTOR"; scl.inputs["Name"].default_value = "scl"
    iop = ng.nodes.new("GeometryNodeInstanceOnPoints"); iop.location = (100, 0)
    real = ng.nodes.new("GeometryNodeRealizeInstances"); real.location = (250, 0)
    L = ng.links.new
    L(nin.outputs["Geometry"], iop.inputs["Points"])
    L(nin.outputs["Object"], oi.inputs["Object"])
    L(oi.outputs["Geometry"], iop.inputs["Instance"])
    L(rot.outputs["Attribute"], iop.inputs["Rotation"])
    L(scl.outputs["Attribute"], iop.inputs["Scale"])
    L(iop.outputs["Instances"], real.inputs["Geometry"])
    L(real.outputs["Geometry"], nout.inputs["Geometry"])
    ng["obj_id"] = obj_sock.identifier
    return ng, obj_sock.identifier


def instancer_scaled(name, coords, piece, coll, rots, scls):
    """Instance `piece` at each point with per-point rot AND scl (both FLOAT_VECTOR,
    same length as coords). Used for tapered piers (a unit pillar scaled per point) and a
    mirrored curb/sidewalk/prop row (`curb_asset_row`'s R-side fix, Scale=(1,-1,1) instead of a
    180-degree rotation -- see that function's own docstring for why).

    UPDATE-IN-PLACE (2026-08, matching `instancer`'s own crash-surface-safety reasoning exactly --
    this group runs in the SAME live-edit hot path via `curb_asset_row` now, so it needs the same
    safety): reuses an existing same-named GN-modifier object's mesh/attributes in place instead
    of deleting/recreating it."""
    if not coords:
        return None
    ng, obj_id = make_gn_group_scaled()
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(c) for c in coords], [], [])
    me.update()
    ar = me.attributes.new("rot", 'FLOAT_VECTOR', 'POINT')
    asc = me.attributes.new("scl", 'FLOAT_VECTOR', 'POINT')
    for i, (rv, sv) in enumerate(zip(rots, scls)):
        ar.data[i].vector = rv
        asc.data[i].vector = sv
    existing = coll.objects.get(name)
    if existing is not None and existing.type == 'MESH' and existing.modifiers.get("GN") is not None:
        old_data = existing.data
        existing.data = me
        existing.location = (0.0, 0.0, 0.0)
        existing.rotation_euler = (0.0, 0.0, 0.0)
        existing.scale = (1.0, 1.0, 1.0)
        if old_data is not None and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
        set_mod_input(existing.modifiers["GN"], obj_id,
                      piece if isinstance(piece, bpy.types.Object) else src(piece))
        existing["_rka_touched"] = True
        return existing
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    mod = obj.modifiers.new("GN", "NODES")
    mod.node_group = ng
    set_mod_input(mod, obj_id, piece if isinstance(piece, bpy.types.Object) else src(piece))
    obj["_rka_touched"] = True
    return obj


def make_curb_asset_row_group():
    """GN_CurbAssetRow: a LIVE Geometry Nodes graph that tiles an Object along a Curve boundary --
    the genuine Curve+GN architecture BOX-style curb/pad (`GN_CurbLoop`/`GN_JunctionPad`) already
    use, applied to ASSET-style rows too (2026-08, user-requested repeatedly: "please use GN and
    curve for them as well"). Unlike the earlier Python-computed-then-baked-to-point-cloud
    approach this replaces for the common case (see `curb_asset_row`'s own docstring for exactly
    when that Python path still applies), the resample here is genuinely LIVE: editing the
    boundary curve's own control points re-triggers this modifier automatically, with zero
    Python re-invocation needed for the tiling math itself.

    Graph: Curve Length -> Count = max(1, round(Length/Spacing)) computed AS NODES (a Math chain,
    not Python) so it re-resolves on every curve edit -> Curve To Points (mode=COUNT, Count+1 --
    see below) -> per-point heading (Z-only, `atan2(Tangent.y, Tangent.x)` + RotOffset, ignoring
    any Z tilt so props/curbs never lean with a sloped road, matching every other heading
    convention in this addon) stored as a 'rowrot' point attribute (survives the delete step
    below, same pattern `GN_Instance`'s own 'rot' attribute uses) -> delete the ONE point at index
    == Count (Curve To Points' own COUNT mode always places a point AT the far end INCLUSIVE of
    both boundary ends, so requesting Count+1 points and dropping the very last one leaves exactly
    Count points spanning [0, Length) with NONE overshooting past the boundary's true end --
    verified directly: Curve To Points with Count=5 on a 10m line places points at 0/2.5/5/7.5/10,
    not the 0/2/4/6/8 a fixed-step sampler would use; using that raw output un-trimmed would
    reproduce the exact "redundant overlapping tail instance" defect the original Python
    `sample_polyline`-based approach had) -> Object Info (As Instance) -> Instance on Points
    (Scale=(1,ScaleY,1) -- ScaleY=-1 mirrors the outward-facing local Y axis for an R-side row
    WITHOUT reversing the length axis, see `curb_asset_row`'s own docstring for why a plain
    180-degree rotation is wrong for an asymmetric piece) -> Realize Instances (required for glTF
    export, see `make_gn_group`'s own docstring -- bare un-realized instances export empty/at
    origin). Returns `(node_group, socket_id_dict)`."""
    ng = bpy.data.node_groups.get("GN_CurbAssetRow")
    if ng:
        return ng, {"Object": ng["obj_id"], "Spacing": ng["sp_id"],
                     "RotOffset": ng["rot_id"], "ScaleY": ng["scl_id"]}
    ng = bpy.data.node_groups.new("GN_CurbAssetRow", "GeometryNodeTree")
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    obj_sock = ifc.new_socket("Object", in_out="INPUT", socket_type="NodeSocketObject")
    sp_sock = ifc.new_socket("Spacing", in_out="INPUT", socket_type="NodeSocketFloat")
    sp_sock.default_value = 2.0
    rot_sock = ifc.new_socket("RotOffset", in_out="INPUT", socket_type="NodeSocketFloat")
    scl_sock = ifc.new_socket("ScaleY", in_out="INPUT", socket_type="NodeSocketFloat")
    scl_sock.default_value = 1.0
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-900, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (1300, 0)
    L = ng.links.new

    curve_len = ng.nodes.new("GeometryNodeCurveLength"); curve_len.location = (-700, 300)
    div = ng.nodes.new("ShaderNodeMath"); div.operation = 'DIVIDE'; div.location = (-550, 300)
    rnd = ng.nodes.new("ShaderNodeMath"); rnd.operation = 'ROUND'; rnd.location = (-400, 300)
    maxn = ng.nodes.new("ShaderNodeMath"); maxn.operation = 'MAXIMUM'; maxn.location = (-250, 300)
    maxn.inputs[1].default_value = 1.0
    plus1 = ng.nodes.new("ShaderNodeMath"); plus1.operation = 'ADD'; plus1.location = (-100, 300)
    plus1.inputs[1].default_value = 1.0
    L(nin.outputs["Geometry"], curve_len.inputs["Curve"])
    L(curve_len.outputs["Length"], div.inputs[0])
    L(nin.outputs["Spacing"], div.inputs[1])
    L(div.outputs["Value"], rnd.inputs[0])
    L(rnd.outputs["Value"], maxn.inputs[0])
    L(maxn.outputs["Value"], plus1.inputs[0])

    c2p = ng.nodes.new("GeometryNodeCurveToPoints"); c2p.mode = 'COUNT'; c2p.location = (-100, 0)
    L(nin.outputs["Geometry"], c2p.inputs["Curve"])
    L(plus1.outputs["Value"], c2p.inputs["Count"])

    # Heading (Z-only) from tangent, stored as an attribute BEFORE deletion (survives it).
    sepxyz = ng.nodes.new("ShaderNodeSeparateXYZ"); sepxyz.location = (-100, -150)
    atan2 = ng.nodes.new("ShaderNodeMath"); atan2.operation = 'ARCTAN2'; atan2.location = (60, -150)
    addrot = ng.nodes.new("ShaderNodeMath"); addrot.operation = 'ADD'; addrot.location = (200, -150)
    combxyz = ng.nodes.new("ShaderNodeCombineXYZ"); combxyz.location = (340, -150)
    L(c2p.outputs["Tangent"], sepxyz.inputs["Vector"])
    L(sepxyz.outputs["Y"], atan2.inputs[0])
    L(sepxyz.outputs["X"], atan2.inputs[1])
    L(atan2.outputs["Value"], addrot.inputs[0])
    L(nin.outputs["RotOffset"], addrot.inputs[1])
    L(addrot.outputs["Value"], combxyz.inputs["Z"])

    store_rot = ng.nodes.new("GeometryNodeStoreNamedAttribute"); store_rot.location = (500, 0)
    store_rot.data_type = 'FLOAT_VECTOR'; store_rot.domain = 'POINT'
    store_rot.inputs["Name"].default_value = "rowrot"
    L(c2p.outputs["Points"], store_rot.inputs["Geometry"])
    L(combxyz.outputs["Vector"], store_rot.inputs["Value"])

    # Mark + delete the one OVERSHOOT point (index == Count, the far-end-inclusive extra point
    # Curve To Points' own COUNT mode always adds -- see this function's own docstring).
    idx = ng.nodes.new("GeometryNodeInputIndex"); idx.location = (60, -350)
    idx_f = ng.nodes.new("ShaderNodeMath"); idx_f.operation = 'ADD'; idx_f.location = (200, -350)
    idx_f.inputs[1].default_value = 0.0
    cmp = ng.nodes.new("FunctionNodeCompare"); cmp.data_type = 'FLOAT'; cmp.operation = 'EQUAL'
    cmp.location = (340, -350)
    L(idx.outputs["Index"], idx_f.inputs[0])
    L(idx_f.outputs["Value"], cmp.inputs[0])
    L(maxn.outputs["Value"], cmp.inputs[1])

    delp = ng.nodes.new("GeometryNodeDeleteGeometry"); delp.domain = 'POINT'; delp.location = (700, 0)
    L(store_rot.outputs["Geometry"], delp.inputs["Geometry"])
    L(cmp.outputs["Result"], delp.inputs["Selection"])

    read_rot = ng.nodes.new("GeometryNodeInputNamedAttribute"); read_rot.location = (700, -200)
    read_rot.data_type = 'FLOAT_VECTOR'
    read_rot.inputs["Name"].default_value = "rowrot"

    oi = ng.nodes.new("GeometryNodeObjectInfo"); oi.location = (500, -400)
    oi.transform_space = "ORIGINAL"
    if "As Instance" in oi.inputs:
        oi.inputs["As Instance"].default_value = True
    L(nin.outputs["Object"], oi.inputs["Object"])

    scl_comb = ng.nodes.new("ShaderNodeCombineXYZ"); scl_comb.location = (500, -550)
    scl_comb.inputs["X"].default_value = 1.0
    scl_comb.inputs["Z"].default_value = 1.0
    L(nin.outputs["ScaleY"], scl_comb.inputs["Y"])

    iop = ng.nodes.new("GeometryNodeInstanceOnPoints"); iop.location = (900, -200)
    L(delp.outputs["Geometry"], iop.inputs["Points"])
    L(oi.outputs["Geometry"], iop.inputs["Instance"])
    L(read_rot.outputs["Attribute"], iop.inputs["Rotation"])
    L(scl_comb.outputs["Vector"], iop.inputs["Scale"])

    real = ng.nodes.new("GeometryNodeRealizeInstances"); real.location = (1100, -200)
    L(iop.outputs["Instances"], real.inputs["Geometry"])
    L(real.outputs["Geometry"], nout.inputs["Geometry"])

    ng["obj_id"] = obj_sock.identifier
    ng["sp_id"] = sp_sock.identifier
    ng["rot_id"] = rot_sock.identifier
    ng["scl_id"] = scl_sock.identifier
    socket_ids = {"Object": obj_sock.identifier, "Spacing": sp_sock.identifier,
                  "RotOffset": rot_sock.identifier, "ScaleY": scl_sock.identifier}
    return ng, socket_ids


def make_road_profile_group():
    """GN_RoadProfile: sweep a flat road ribbon along a curve via Curve to Mesh and assign a
    Material (group input) -- one filled pavement surface, shade-smooth, no thickness/volume.
    The curve's per-point RADIUS scales the profile -> a VARIABLE-WIDTH carriageway (true
    3->2->1 lane taper); its per-point TILT banks the deck. Mirrors `make_junction_pad_group()`'s
    shape exactly (Fillet/Fill-Curve there, Curve-to-Mesh here, otherwise the same "one flat
    filled mesh, materialed, shade-smooth" idiom) -- deliberately, see below. Returns
    `(node_group, (mat_id, thick_id))`; `thick_id`/"Thickness" is kept as an accepted-but-unused
    input purely so `road_spine()`/`road_from_curve()`/`assemble.py`'s existing
    `set_mod_input(mod, thick_id, thickness)` calls keep working unchanged -- it no longer drives
    any geometry.

    **Simplified from a solid extruded slab to a flat plane (2026-07-28, user's own question after
    the bug below was found and fixed: "why is the road segment a box being pushed down, why not
    just a plane, like the intersection [pad]?").** That question was correct and is the real fix.
    The previous version swept the ribbon, then Extrude-Mesh'd it downward for a `Thickness`-deep
    solid deck, which needed a whole apparatus (endpoint tagging, cap-face deletion, a
    flatten-then-selective-reshade shading pass, and eventually a Join Geometry to restore a
    missing top face -- see git history / road_blender_godot.md for that chain of fixes) just to
    behave like a normal road surface. NONE of that is needed: nothing in the codebase reads or
    depends on the pavement having actual volume (collision, rendering, and every gameplay system
    only ever care about the top drivable surface), and `GN_JunctionPad` already proves a flat,
    zero-thickness swept/filled mesh collides and renders correctly for exactly the same kind of
    surface. A flat ribbon has no side walls, no end caps, and no top/bottom distinction to get
    wrong -- this eliminates the entire bug class the extrude approach kept reintroducing, for
    segments AND transitions alike (both go through this same group via `road_spine()`)."""
    ng = bpy.data.node_groups.get("GN_RoadProfile")
    if ng:
        return ng, (ng["mat_id"], ng["thick_id"], ng["negf_id"], ng["posf_id"])
    ng = bpy.data.node_groups.new("GN_RoadProfile", "GeometryNodeTree")
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    mat_sock = ifc.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    thick_sock = ifc.new_socket("Thickness", in_out="INPUT", socket_type="NodeSocketFloat")
    thick_sock.default_value = 0.4   # accepted, unused -- see docstring
    # ASYMMETRIC CARRIAGEWAY (2026-08, the "one-way roads are built double-width" defect -- see
    # `intersection_kit.carriageway_extents`). The profile line used to be hardcoded symmetric,
    # (-1..+1) scaled by the spine's per-point Radius, so the pavement was ALWAYS mirrored about
    # the spine -- while `build_segment_from_spine` places forward lanes on the positive side and
    # backward lanes on the negative one. A one-way road therefore swept a whole empty mirror
    # carriageway (measured: 21.00 m of asphalt for 10.50 m of lanes).
    # These two fractions move each end of the profile line independently. They are FRACTIONS OF
    # RADIUS, not metres, so the existing per-point Radius still drives every width taper
    # untouched: with `radius = (neg + pos) / 2`, setting `Neg Frac = neg / radius` and
    # `Pos Frac = pos / radius` sweeps exactly `[-neg, +pos]` (the two always sum to 2).
    # Defaults 1.0/1.0 reproduce the old symmetric sweep byte-for-byte, so every already-built
    # piece and any caller that never sets them is unaffected.
    negf_sock = ifc.new_socket("Neg Frac", in_out="INPUT", socket_type="NodeSocketFloat")
    negf_sock.default_value = 1.0
    posf_sock = ifc.new_socket("Pos Frac", in_out="INPUT", socket_type="NodeSocketFloat")
    posf_sock.default_value = 1.0
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-700, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (500, 0)
    line = ng.nodes.new("GeometryNodeCurvePrimitiveLine"); line.location = (-500, -220)
    line.inputs["Start"].default_value = (-1.0, 0.0, 0.0)   # profile spans the curve normal
    line.inputs["End"].default_value = (1.0, 0.0, 0.0)
    # Start.x = -Neg Frac, End.x = +Pos Frac (y/z stay 0 -- the profile is a flat lateral line).
    negx = ng.nodes.new("ShaderNodeMath"); negx.location = (-700, -300)
    negx.operation = 'MULTIPLY'; negx.inputs[1].default_value = -1.0
    startv = ng.nodes.new("ShaderNodeCombineXYZ"); startv.location = (-680, -220)
    endv = ng.nodes.new("ShaderNodeCombineXYZ"); endv.location = (-680, -380)
    c2m = ng.nodes.new("GeometryNodeCurveToMesh"); c2m.location = (-250, 0)
    # Blender 5.x Curve to Mesh has an explicit per-point "Scale" field (radius no longer
    # auto-scales) — drive it from the spine's Radius so half_w controls carriageway width.
    rad = ng.nodes.new("GeometryNodeInputRadius"); rad.location = (-500, -60)
    setm = ng.nodes.new("GeometryNodeSetMaterial"); setm.location = (60, 0)
    ss = ng.nodes.new("GeometryNodeSetShadeSmooth"); ss.location = (280, 0)
    L = ng.links.new
    L(nin.outputs["Neg Frac"], negx.inputs[0])
    L(negx.outputs["Value"], startv.inputs["X"])
    L(nin.outputs["Pos Frac"], endv.inputs["X"])
    L(startv.outputs["Vector"], line.inputs["Start"])
    L(endv.outputs["Vector"], line.inputs["End"])
    L(nin.outputs["Geometry"], c2m.inputs["Curve"])
    L(line.outputs["Curve"], c2m.inputs["Profile Curve"])
    L(rad.outputs["Radius"], c2m.inputs["Scale"])
    L(c2m.outputs["Mesh"], setm.inputs["Geometry"])
    L(nin.outputs["Material"], setm.inputs["Material"])
    L(setm.outputs["Geometry"], ss.inputs["Geometry"])
    L(ss.outputs["Geometry"], nout.inputs["Geometry"])
    ng["mat_id"] = mat_sock.identifier
    ng["thick_id"] = thick_sock.identifier
    ng["negf_id"] = negf_sock.identifier
    ng["posf_id"] = posf_sock.identifier
    return ng, (mat_sock.identifier, thick_sock.identifier,
                negf_sock.identifier, posf_sock.identifier)


def swept_wall(name, pts, h, coll, matkey="concrete", thickness=0.18, z0=0.0):
    """A CONTINUOUS vertical barrier following the 3D polyline pts=[(x,y,z), ...]: a thin
    solid wall (box section) from z+z0 up by `h`, welded end-to-end with NO gaps — the fix
    for instanced straight panels that gap/overlap on a tight curve. Stays world-vertical
    (Curve-to-Mesh can't keep a wall profile upright on a banked/climbing spine).

    UPDATE-IN-PLACE (2026-08, the crash-surface fix) -- same convention as `flat_ribbon`/
    `marking_ribbon`: reuses an existing same-named object's mesh data unconditionally instead of
    deleting/recreating the object, closing the legacy BOX-style curb's identity-crash surface in
    `ops_segment.rebuild_segment_in_place`."""
    n = len(pts)
    if n < 2:
        return None
    verts, faces = [], []
    for i, (x, y, z) in enumerate(pts):
        a = pts[max(0, i - 1)]; b = pts[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        L = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / L * thickness / 2.0, tx / L * thickness / 2.0   # half-thickness normal
        base = z + z0
        verts += [(x - nx, y - ny, base), (x + nx, y + ny, base),
                  (x + nx, y + ny, base + h), (x - nx, y - ny, base + h)]
    for i in range(n - 1):
        a, b = i * 4, (i + 1) * 4
        faces += [(a + 3, a + 2, b + 2, b + 3),       # top
                  (a + 1, a + 2, b + 2, b + 1),       # +normal face
                  (a, a + 3, b + 3, b)]               # -normal face
    faces += [(0, 1, 2, 3), ((n-1)*4, (n-1)*4 + 3, (n-1)*4 + 2, (n-1)*4 + 1)]   # end caps
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces); me.update(); recalc_normals(me)
    existing = coll.objects.get(name)
    if existing is not None and existing.type == 'MESH':
        existing.location = (0.0, 0.0, 0.0)
        existing.rotation_euler = (0.0, 0.0, 0.0)
        existing.scale = (1.0, 1.0, 1.0)
        old_data = existing.data
        existing.data = me
        if old_data is not None and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
        me.materials.clear()
        me.materials.append(mat(matkey))
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)
    obj = bpy.data.objects.new(name, me); coll.objects.link(obj)
    obj.data.materials.append(mat(matkey))
    obj["_rka_touched"] = True
    return obj


def road_from_curve(name, pts, coll, matkey="asphalt", thickness=0.4, z_lift=0.0,
                    resolution=24, neg_frac=1.0, pos_frac=1.0):
    """Build a NURBS spine from pts=[(x,y,z,tilt,half_w), ...] (radius=half_w, tilt=bank)
    and apply GN_RoadProfile -> one swept, variable-width, banked, climbing road surface.
    Used for every ramp/connector/merge tail. Returns the road object (modifier live; glTF
    export bakes it).

    `neg_frac`/`pos_frac` -- see `make_road_profile_group`; defaults sweep symmetrically about
    the spine exactly as before."""
    cu = bpy.data.curves.new(name + "_curve", 'CURVE')
    cu.dimensions = '3D'
    cu.resolution_u = resolution
    sp = cu.splines.new('NURBS')
    sp.points.add(len(pts) - 1)
    for i, (x, y, z, tl, hw) in enumerate(pts):
        sp.points[i].co = (x, y, z + z_lift, 1.0)
        sp.points[i].radius = max(hw, 1e-3)
        sp.points[i].tilt = tl
    sp.order_u = min(4, len(pts))
    sp.use_endpoint_u = True
    obj = bpy.data.objects.new(name, cu)
    coll.objects.link(obj)
    ng, (mat_id, thick_id, negf_id, posf_id) = make_road_profile_group()
    mod = obj.modifiers.new("Road", "NODES")
    mod.node_group = ng
    set_mod_input(mod, mat_id, mat(matkey))
    set_mod_input(mod, thick_id, thickness)
    set_mod_input(mod, negf_id, neg_frac)
    set_mod_input(mod, posf_id, pos_frac)
    return obj


def road_spine(name, pts, coll, radius, matkey="asphalt", thickness=0.4,
                neg_frac=1.0, pos_frac=1.0):
    """A live-editable POLY-spline Curve object through pts=[(x,y,z), ...] with `GN_RoadProfile`
    attached DIRECTLY to it -- unlike `road_from_curve` (a fresh throwaway NURBS curve rebuilt
    from scratch every call), this object IS the persistent, user-editable spine: entering Edit
    Mode and adding/dragging a control point reshapes the pavement immediately via Blender's own
    dependency graph, no Python rebuild step for the pavement itself (only separately-offset L/R
    curb walls and lane-centerline data curves need re-sampling afterward -- see
    `road_kit_authoring/ops_segment.py`). `radius` is either one scalar (every point, a
    constant-width road) or a list matching `pts` (e.g. a linear lane-count-transition taper --
    `GN_RoadProfile`'s per-point Radius already does variable-width sweeps natively, no extra GN
    work needed for a taper). Returns the object (modifier live; glTF export bakes it).

    `neg_frac`/`pos_frac` -- see `make_road_profile_group`. Together with `radius` they express an
    ASYMMETRIC carriageway (a one-way road, or a two-way road carrying different lane counts each
    way) without moving the authored spine: pass
    `radius, shift = intersection_kit.sweep_radius_and_shift(*carriageway_extents(...))` and the
    matching `neg/radius`, `pos/radius` fractions. Defaults 1.0/1.0 are the old symmetric sweep."""
    cu = bpy.data.curves.new(name + "_spine", 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts) - 1)
    radii = radius if isinstance(radius, (list, tuple)) else [radius] * len(pts)
    for i, p in enumerate(pts):
        sp.points[i].co = (p[0], p[1], p[2], 1.0)
        sp.points[i].radius = max(radii[min(i, len(radii) - 1)], 1e-3)
    obj = bpy.data.objects.new(name, cu)
    coll.objects.link(obj)
    ng, (mat_id, thick_id, negf_id, posf_id) = make_road_profile_group()
    mod = obj.modifiers.new("Road", "NODES")
    mod.node_group = ng
    set_mod_input(mod, mat_id, mat(matkey))
    set_mod_input(mod, thick_id, thickness)
    set_mod_input(mod, negf_id, neg_frac)
    set_mod_input(mod, posf_id, pos_frac)
    return obj


def set_road_spine_material(spine_obj, matkey):
    """Update an EXISTING `road_spine()` object's pavement material in place, without touching its
    shape/thickness -- for changing a segment/transition's pavement material after the fact
    (2026-07-28, user-reported: material was a build-time-only hardcoded literal, no way to change
    it afterward at all). `rebuild_segment_gn_in_place`/`rebuild_lane_transition_in_place`
    deliberately never delete/recreate the spine object itself (its own control points ARE the
    live-edited shape state), so a material change can't go through the normal "clear + rebuild"
    path curb/lane data uses -- this updates the live "Road" GN modifier's Material input directly,
    the same input `road_spine()` itself sets at creation time. No-op (returns False) if `spine_obj`
    doesn't have a "Road" modifier (not actually a road_spine() object)."""
    mod = spine_obj.modifiers.get("Road")
    if mod is None:
        return False
    _ng, (mat_id, _thick_id, _negf_id, _posf_id) = make_road_profile_group()
    set_mod_input(mod, mat_id, mat(matkey))
    return True


def set_road_spine_profile_fracs(spine_obj, neg_frac, pos_frac):
    """Update an EXISTING `road_spine()` object's asymmetric-carriageway fractions in place --
    the same "reach into the live Road modifier" path `set_road_spine_material` uses, and for the
    same reason: the spine object is never deleted/recreated by a rebuild (its control points ARE
    the live-edited shape state), so these can't ride along on a clear-and-rebuild the way curb and
    lane data do.

    Called from `ops_segment._refresh_pavement_radius`, which every lane-count/median adjust
    operator funnels through. Without it a one-way piece would silently revert to the symmetric
    double-width sweep (see `make_road_profile_group`) the moment any of those controls was
    touched, since only the per-point radius was being refreshed. Returns False if `spine_obj`
    has no "Road" modifier."""
    mod = spine_obj.modifiers.get("Road")
    if mod is None:
        return False
    _ng, (_mat_id, _thick_id, negf_id, posf_id) = make_road_profile_group()
    set_mod_input(mod, negf_id, neg_frac)
    set_mod_input(mod, posf_id, pos_frac)
    return True


def make_road_support_group():
    """GN_RoadSupport: derive what goes UNDERNEATH a road from how high it sits over the terrain.

    THE RULE, and it is the whole point: there is no separate "viaduct builder" and "ground road
    builder". A road is a road. Sample the terrain under the spine, take
    `delta = deck_z - ground_z`, and switch on it:

        delta >  At-Grade   -> DECK   : a swept slab under the full road width
        delta >  Fill Max   -> + PIER : columns from the ground up to that slab
        otherwise           -> nothing

    ONE STRUCTURE, THICKENING WITH HEIGHT. There used to be a third case -- an earth EMBANKMENT
    prism between at-grade and `Fill Max` -- and it was removed (2026-08-15, user's call) once the
    deck existed: the slab under the road already IS what a road standing a little proud of the
    ground looks like, so a separate near-ground primitive only added a second thing to keep in
    agreement with the first. A road now grows exactly one understructure, and height decides
    whether it needs legs. `island_v3_plan.support_kind` still CLASSIFIES `FILL` and
    `fill_footprint` still reports the true embankment toe, because that is an authoring-clearance
    question (what ground a raised road eats, so a block is not laid into it) and is unaffected by
    how the support is drawn.

    It is a MODIFIER, not a bake, and that is deliberate. The support has to re-derive while a
    deck height is being dragged, or the piers silently stop matching the road they hold up
    between rebuilds. `tools/island_v3_plan.py: support_kind()` is the same rule in pure Python
    and is the SPECIFICATION this must agree with; that one is testable headless, this one is
    live. Keep them in step.

    CUT/TUNNEL are deliberately NOT emitted here. A trench or a bore is a hole in the terrain,
    not an object added under a road — it belongs to the ground-cutting pass
    (`rka.cut_ground_under_road`), and pretending otherwise would put a box where a void is
    wanted. The Python reference still CLASSIFIES them so the authoring report can say a stretch
    needs cutting.

    Returns `(node_group, ids)` where ids maps input name -> socket identifier, the same
    accessor shape the other builders here return for `set_mod_input`."""
    ng = bpy.data.node_groups.get("GN_RoadSupport")
    if ng:
        return ng, ng["sock_ids"].to_dict() if hasattr(ng["sock_ids"], "to_dict") \
            else dict(ng["sock_ids"])
    ng = bpy.data.node_groups.new("GN_RoadSupport", "GeometryNodeTree")
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    socks = {}
    def fin(name, kind, default=None, minv=None):
        s = ifc.new_socket(name, in_out="INPUT", socket_type=kind)
        if default is not None:
            s.default_value = default
        if minv is not None:
            s.min_value = minv
        socks[name] = s.identifier
        return s
    fin("Terrain", "NodeSocketObject")
    fin("Half Width", "NodeSocketFloat", 11.0, 0.0)
    fin("Pier Spacing", "NodeSocketFloat", 30.0, 1.0)
    fin("Deck Thickness", "NodeSocketFloat", 1.6, 0.0)
    fin("Pier Section", "NodeSocketFloat", 2.2, 0.1)
    fin("At-Grade Tol", "NodeSocketFloat", 0.4, 0.0)
    fin("Fill Max", "NodeSocketFloat", 4.0, 0.0)
    fin("Fill Slope", "NodeSocketFloat", 1.5, 0.0)
    mat = ifc.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    socks["Material"] = mat.identifier
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    N = ng.nodes.new
    L = ng.links.new
    nin = N("NodeGroupInput"); nin.location = (-1400, 0)
    nout = N("NodeGroupOutput"); nout.location = (1500, 0)

    # --- sample the terrain under every station along the spine -----------------------
    res = N("GeometryNodeResampleCurve"); res.location = (-1150, 120)
    # Blender 5.x moved Resample Curve's mode from a node PROPERTY to a menu INPUT SOCKET
    # (`res.mode` no longer exists), so set it through the socket and tolerate either form.
    if "Mode" in res.inputs:
        res.inputs["Mode"].default_value = 'Length'
    else:
        res.mode = 'LENGTH'
    L(nin.outputs["Geometry"], res.inputs["Curve"])
    L(nin.outputs["Pier Spacing"], res.inputs["Length"])

    terr = N("GeometryNodeObjectInfo"); terr.location = (-1150, -220)
    terr.transform_space = 'RELATIVE'
    L(nin.outputs["Terrain"], terr.inputs["Object"])

    pos = N("GeometryNodeInputPosition"); pos.location = (-1150, -60)
    down = N("ShaderNodeCombineXYZ"); down.location = (-1150, -400)
    down.inputs["X"].default_value = 0.0
    down.inputs["Y"].default_value = 0.0
    down.inputs["Z"].default_value = -1.0

    ray = N("GeometryNodeRaycast"); ray.location = (-900, -160)
    ray.inputs["Ray Length"].default_value = 10000.0
    L(terr.outputs["Geometry"], ray.inputs["Target Geometry"])
    L(pos.outputs["Position"], ray.inputs["Source Position"])
    L(down.outputs["Vector"], ray.inputs["Ray Direction"])

    deck_z = N("ShaderNodeSeparateXYZ"); deck_z.location = (-900, 60)
    L(pos.outputs["Position"], deck_z.inputs["Vector"])
    gnd_z = N("ShaderNodeSeparateXYZ"); gnd_z.location = (-660, -260)
    L(ray.outputs["Hit Position"], gnd_z.inputs["Vector"])

    delta = N("ShaderNodeMath"); delta.location = (-450, -60); delta.operation = 'SUBTRACT'
    L(deck_z.outputs["Z"], delta.inputs[0])
    L(gnd_z.outputs["Z"], delta.inputs[1])

    def cmp(op, a_out, b_out, loc):
        m = N("ShaderNodeMath"); m.location = loc; m.operation = op
        L(a_out, m.inputs[0]); L(b_out, m.inputs[1])
        return m
    def band(a_out, b_out, loc):
        b = N("FunctionNodeBooleanMath"); b.location = loc; b.operation = 'AND'
        L(a_out, b.inputs[0]); L(b_out, b.inputs[1])
        return b

    is_pier = cmp('GREATER_THAN', delta.outputs[0], nin.outputs["Fill Max"], (-230, 140))
    pier_sel = band(is_pier.outputs[0], ray.outputs["Is Hit"], (-30, 140))

    cube = N("GeometryNodeMeshCube"); cube.location = (-30, 400)
    cube.inputs["Size"].default_value = (1.0, 1.0, 1.0)

    tangent = N("GeometryNodeInputTangent"); tangent.location = (-30, 560)
    align = N("FunctionNodeAlignRotationToVector"); align.location = (180, 560)
    align.axis = 'Y'
    L(tangent.outputs["Tangent"], align.inputs["Vector"])

    # --- PIER: column from the ground up to the deck soffit ---------------------------
    ph = N("ShaderNodeMath"); ph.location = (180, 240); ph.operation = 'SUBTRACT'
    L(delta.outputs[0], ph.inputs[0])
    L(nin.outputs["Deck Thickness"], ph.inputs[1])
    p_scale = N("ShaderNodeCombineXYZ"); p_scale.location = (400, 240)
    L(nin.outputs["Pier Section"], p_scale.inputs["X"])
    L(nin.outputs["Pier Section"], p_scale.inputs["Y"])
    L(ph.outputs[0], p_scale.inputs["Z"])

    p_iop = N("GeometryNodeInstanceOnPoints"); p_iop.location = (620, 340)
    L(res.outputs["Curve"], p_iop.inputs["Points"])
    L(pier_sel.outputs[0], p_iop.inputs["Selection"])
    L(cube.outputs["Mesh"], p_iop.inputs["Instance"])
    L(align.outputs[0], p_iop.inputs["Rotation"])
    p_si = N("GeometryNodeScaleInstances"); p_si.location = (840, 340)
    L(p_iop.outputs["Instances"], p_si.inputs["Instances"])
    L(p_scale.outputs["Vector"], p_si.inputs["Scale"])
    # column centre sits half its height below the soffit
    p_half = N("ShaderNodeMath"); p_half.location = (620, 140); p_half.operation = 'MULTIPLY'
    L(ph.outputs[0], p_half.inputs[0]); p_half.inputs[1].default_value = 0.5
    p_off = N("ShaderNodeMath"); p_off.location = (840, 140); p_off.operation = 'ADD'
    L(p_half.outputs[0], p_off.inputs[0])
    L(nin.outputs["Deck Thickness"], p_off.inputs[1])
    p_neg = N("ShaderNodeMath"); p_neg.location = (1020, 140); p_neg.operation = 'MULTIPLY'
    L(p_off.outputs[0], p_neg.inputs[0]); p_neg.inputs[1].default_value = -1.0
    p_vec = N("ShaderNodeCombineXYZ"); p_vec.location = (1180, 140)
    L(p_neg.outputs[0], p_vec.inputs["Z"])
    p_tr = N("GeometryNodeTranslateInstances"); p_tr.location = (1180, 340)
    p_tr.inputs["Local Space"].default_value = False
    L(p_si.outputs["Instances"], p_tr.inputs["Instances"])
    L(p_vec.outputs["Vector"], p_tr.inputs["Translation"])

    # --- SOFFIT: the deck's UNDERSIDE, spanning the full road width -------------------
    #
    # The piers were hanging from a surface that did not exist. `Deck Thickness` was already used
    # twice -- to shorten each column and to drop it -- so the whole node group was built around a
    # soffit it never drew: from underneath, a viaduct was a line of columns holding up open air,
    # and every ramp read as "one-off structure" rather than as a road with a deck.
    #
    # SWEPT, NOT INSTANCED, and that is the difference between this and the FILL block above. A
    # box per station tiles into a straight slab but scallops on a bend -- a 30 m box on a 100 m
    # radius leaves a 1.1 m sagitta, in and out, along the one surface you actually look at from
    # below. Curve to Mesh follows the alignment exactly, which is also how every curb, sidewalk
    # and median in the stack is built (`road_stack.make_profile_sweep_group`), so a deck is now
    # the same kind of object as the rest of the cross-section instead of its own special case.
    #
    # It is cut to the VIADUCT STRETCHES by deleting the points that are not on piers, which
    # splits the curve into sub-splines -- so one road that runs at grade, climbs onto a viaduct
    # and comes back down grows exactly one slab, in the right place, with no stretch-finding
    # logic anywhere. Same rule, same `delta`, as the columns underneath it.
    sres = N("GeometryNodeResampleCurve"); sres.location = (-1150, 320)
    if "Mode" in sres.inputs:
        sres.inputs["Mode"].default_value = 'Length'
    else:
        sres.mode = 'LENGTH'
    L(nin.outputs["Geometry"], sres.inputs["Curve"])
    # A fraction of the bent spacing: fine enough that the swept slab is smooth on any curve a
    # road can legally hold, without making the raycast below a per-metre cost.
    s_step = N("ShaderNodeMath"); s_step.location = (-1370, 320); s_step.operation = 'DIVIDE'
    L(nin.outputs["Pier Spacing"], s_step.inputs[0]); s_step.inputs[1].default_value = 6.0
    L(s_step.outputs[0], sres.inputs["Length"])

    s_pos = N("GeometryNodeInputPosition"); s_pos.location = (-950, 480)
    s_ray = N("GeometryNodeRaycast"); s_ray.location = (-720, 420)
    s_ray.inputs["Ray Length"].default_value = 10000.0
    L(terr.outputs["Geometry"], s_ray.inputs["Target Geometry"])
    L(s_pos.outputs["Position"], s_ray.inputs["Source Position"])
    L(down.outputs["Vector"], s_ray.inputs["Ray Direction"])
    s_deck = N("ShaderNodeSeparateXYZ"); s_deck.location = (-720, 600)
    L(s_pos.outputs["Position"], s_deck.inputs["Vector"])
    s_gnd = N("ShaderNodeSeparateXYZ"); s_gnd.location = (-500, 540)
    L(s_ray.outputs["Hit Position"], s_gnd.inputs["Vector"])
    s_delta = N("ShaderNodeMath"); s_delta.location = (-300, 600); s_delta.operation = 'SUBTRACT'
    L(s_deck.outputs["Z"], s_delta.inputs[0])
    L(s_gnd.outputs["Z"], s_delta.inputs[1])
    s_is = cmp('GREATER_THAN', s_delta.outputs[0], nin.outputs["At-Grade Tol"], (-100, 600))
    s_sel = band(s_is.outputs[0], s_ray.outputs["Is Hit"], (100, 600))
    s_not = N("FunctionNodeBooleanMath"); s_not.location = (280, 600)
    s_not.operation = 'NOT'
    L(s_sel.outputs[0], s_not.inputs[0])

    s_del = N("GeometryNodeDeleteGeometry"); s_del.location = (440, 640)
    s_del.domain = 'POINT'
    s_del.mode = 'ALL'
    L(sres.outputs["Curve"], s_del.inputs["Geometry"])
    L(s_not.outputs[0], s_del.inputs["Selection"])

    s_norm = N("GeometryNodeSetCurveNormal"); s_norm.location = (620, 640)
    # Z Up for the same reason `make_profile_sweep_group` pins it: the default Minimum Twist frame
    # derives "up" from the curve's own bending, so a deck would roll with the road.
    if "Mode" in s_norm.inputs:
        s_norm.inputs["Mode"].default_value = 'Z Up'
    else:
        s_norm.mode = 'Z_UP'
    L(s_del.outputs["Geometry"], s_norm.inputs["Curve"])

    s_quad = N("GeometryNodeCurvePrimitiveQuadrilateral"); s_quad.location = (620, 820)
    s_w = N("ShaderNodeMath"); s_w.location = (440, 880); s_w.operation = 'MULTIPLY'
    L(nin.outputs["Half Width"], s_w.inputs[0]); s_w.inputs[1].default_value = 2.0
    L(s_w.outputs[0], s_quad.inputs["Width"])
    L(nin.outputs["Deck Thickness"], s_quad.inputs["Height"])

    s_c2m = N("GeometryNodeCurveToMesh"); s_c2m.location = (860, 640)
    # Capped: a viaduct that starts and stops has real ends, unlike a curb that continues into
    # the next piece.
    s_c2m.inputs["Fill Caps"].default_value = True
    L(s_norm.outputs["Curve"], s_c2m.inputs["Curve"])
    L(s_quad.outputs["Curve"], s_c2m.inputs["Profile Curve"])

    # Drop it so the slab's TOP face sits on the driving surface, not its centre.
    s_drop = N("ShaderNodeMath"); s_drop.location = (860, 460); s_drop.operation = 'MULTIPLY'
    L(nin.outputs["Deck Thickness"], s_drop.inputs[0]); s_drop.inputs[1].default_value = -0.5
    s_dvec = N("ShaderNodeCombineXYZ"); s_dvec.location = (1040, 460)
    L(s_drop.outputs[0], s_dvec.inputs["Z"])
    s_tr = N("GeometryNodeTransform"); s_tr.location = (1180, 640)
    L(s_c2m.outputs["Mesh"], s_tr.inputs["Geometry"])
    L(s_dvec.outputs["Vector"], s_tr.inputs["Translation"])

    join = N("GeometryNodeJoinGeometry"); join.location = (1320, 60)
    L(p_tr.outputs["Instances"], join.inputs["Geometry"])
    L(s_tr.outputs["Geometry"], join.inputs["Geometry"])
    # REALIZE before materialing. Unrealized GN instances are invisible to
    # `bpy.data.meshes.new_from_object()`, which is what every headless check here and the
    # export path both use -- the supports evaluated to zero vertices until this was added,
    # while looking perfectly correct in the viewport. Set Material also only applies to real
    # geometry. Pier/embankment counts are in the hundreds, not the millions, so the lost
    # instancing is not worth being invisible to the toolchain.
    real = N("GeometryNodeRealizeInstances"); real.location = (1380, 60)
    L(join.outputs["Geometry"], real.inputs["Geometry"])
    setm = N("GeometryNodeSetMaterial"); setm.location = (1420, 60)
    L(real.outputs["Geometry"], setm.inputs["Geometry"])
    L(nin.outputs["Material"], setm.inputs["Material"])
    L(setm.outputs["Geometry"], nout.inputs["Geometry"])

    ng["sock_ids"] = socks
    return ng, socks


def road_support(spine_obj, terrain_obj, half_width=11.0, matkey="concrete", **over):
    """Attach GN_RoadSupport to an existing road spine curve. LIVE: drag a spine point up and
    the piers re-derive, because the rule is a modifier and not a bake."""
    ng, ids = make_road_support_group()
    mod = spine_obj.modifiers.get("RoadSupport") or \
        spine_obj.modifiers.new("RoadSupport", 'NODES')
    mod.node_group = ng
    vals = dict(Terrain=terrain_obj, **{"Half Width": half_width, "Material": mat(matkey)})
    vals.update(over)
    for name, value in vals.items():
        if name in ids:
            set_mod_input(mod, ids[name], value)
    return mod


def make_barrier_profile_group():
    """GN_BarrierProfile: sweep a CONSTANT thin vertical rectangle (thickness x height, both
    group inputs, NOT scaled by the spine radius) along an EDGE curve via Curve to Mesh -> one
    continuous upright barrier following the ramp exactly, gap-free, at UNIFORM height while the
    deck lane width varies. The wall counterpart of GN_RoadProfile; both sweep the SAME densified
    spine so pavement edge and wall never diverge (the old bug: road swept a smooth curve while the
    wall chorded sparse control points). Profile-X maps to the curve normal (lateral) and profile-Y
    to world up (same frame the road relies on), so the rectangle is Width=Thickness (X) by
    Height (Y), lifted +Height/2 so the wall base sits on the deck edge. Returns
    (ng, (mat_id, h_id, t_id))."""
    ng = bpy.data.node_groups.get("GN_BarrierProfile")
    if ng:
        return ng, (ng["mat_id"], ng["h_id"], ng["t_id"])
    ng = bpy.data.node_groups.new("GN_BarrierProfile", "GeometryNodeTree")
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    mat_sock = ifc.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    h_sock = ifc.new_socket("Height", in_out="INPUT", socket_type="NodeSocketFloat")
    h_sock.default_value = 1.1
    t_sock = ifc.new_socket("Thickness", in_out="INPUT", socket_type="NodeSocketFloat")
    t_sock.default_value = 0.18
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-800, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (700, 0)
    quad = ng.nodes.new("GeometryNodeCurvePrimitiveQuadrilateral"); quad.location = (-560, -220)
    quad.mode = 'RECTANGLE'                              # Width -> X (lateral), Height -> Y (up)
    half = ng.nodes.new("ShaderNodeMath"); half.location = (-560, -400)
    half.operation = 'MULTIPLY'; half.inputs[1].default_value = -0.5   # profile-Y -> world -Z here,
    # so a -Height/2 lift puts the wall base ON the deck edge (0..H upward), not hanging below it
    comb = ng.nodes.new("ShaderNodeCombineXYZ"); comb.location = (-380, -400)
    sp = ng.nodes.new("GeometryNodeSetPosition"); sp.location = (-200, -220)  # lift base to 0..H
    c2m = ng.nodes.new("GeometryNodeCurveToMesh"); c2m.location = (40, 0)
    if "Fill Caps" in c2m.inputs:
        c2m.inputs["Fill Caps"].default_value = True
    setm = ng.nodes.new("GeometryNodeSetMaterial"); setm.location = (320, 0)
    ss = ng.nodes.new("GeometryNodeSetShadeSmooth"); ss.location = (500, 0)
    L = ng.links.new
    L(nin.outputs["Thickness"], quad.inputs["Width"])
    L(nin.outputs["Height"], quad.inputs["Height"])
    L(nin.outputs["Height"], half.inputs[0])
    L(half.outputs["Value"], comb.inputs["Y"])          # translate +Height/2 in profile-up (Y)
    L(quad.outputs["Curve"], sp.inputs["Geometry"])
    L(comb.outputs["Vector"], sp.inputs["Offset"])
    L(nin.outputs["Geometry"], c2m.inputs["Curve"])
    L(sp.outputs["Geometry"], c2m.inputs["Profile Curve"])
    L(c2m.outputs["Mesh"], setm.inputs["Geometry"])
    L(nin.outputs["Material"], setm.inputs["Material"])
    L(setm.outputs["Geometry"], ss.inputs["Geometry"])
    L(ss.outputs["Geometry"], nout.inputs["Geometry"])
    ng["mat_id"] = mat_sock.identifier
    ng["h_id"] = h_sock.identifier
    ng["t_id"] = t_sock.identifier
    return ng, (mat_sock.identifier, h_sock.identifier, t_sock.identifier)


def barrier_from_curve(name, edge_pts, coll, h=1.1, thickness=0.18, matkey="concrete",
                       resolution=24):
    """Build a NURBS edge spine from edge_pts=[(x,y,z), ...] and apply GN_BarrierProfile -> one
    continuous upright wall of constant height/thickness that follows the ramp edge. The GN
    swept-barrier replacement for the hand-welded swept_wall: fed the SAME densified edge as the
    road (same curve type/resolution), so wall and pavement never gap. Returns the wall object
    (modifier live; glTF export bakes it)."""
    if len(edge_pts) < 2:
        return None
    cu = bpy.data.curves.new(name + "_curve", 'CURVE')
    cu.dimensions = '3D'
    cu.resolution_u = resolution
    sp = cu.splines.new('NURBS')
    sp.points.add(len(edge_pts) - 1)
    for i, p in enumerate(edge_pts):
        sp.points[i].co = (p[0], p[1], p[2], 1.0)
    sp.order_u = min(4, len(edge_pts))
    sp.use_endpoint_u = True
    obj = bpy.data.objects.new(name, cu)
    coll.objects.link(obj)
    ng, (mat_id, h_id, t_id) = make_barrier_profile_group()
    mod = obj.modifiers.new("Barrier", "NODES")
    mod.node_group = ng
    set_mod_input(mod, mat_id, mat(matkey))
    set_mod_input(mod, h_id, h)
    set_mod_input(mod, t_id, thickness)
    return obj


# ---------------------------------------------- intersection pad/curb (road_kit_authoring, GN)
def _poly_curve_with_radius(name, pts_radius, coll, closed=True):
    """A POLY-spline Curve object through pts_radius=[(x,y,z,radius), ...] -- `radius` sets each
    point's built-in `.radius` (read by `GeometryNodeInputRadius` inside `GN_JunctionPad`/
    `GN_CurbLoop`, exactly the per-point-radius convention `GN_RoadProfile` already uses for lane
    width, reused here for per-corner FILLET radius: near-zero on straight/tail points, the
    corner's own (already clamped by `intersection_kit.build_curb_corners`) fillet radius on
    actual corner vertices; a `curb_loop(closed=False)` open path -- a segment/transition's own L/R
    curb line, no corners -- just passes 0 everywhere). Shared by `junction_pad` (always closed)
    and `curb_loop` (closed for an intersection loop, open for a segment's curb line) -- each
    builds its OWN boundary object from the same `pts_radius` (two small curve datablocks, not two
    GN outputs off one object) since a pad (Fill Curve) and a curb (Curve to Mesh) are genuinely
    different meshes that both need to coexist as separate exportable objects, same as this
    addon's other pieces.

    UPDATE-IN-PLACE (2026-08, the crash-surface fix): if an object named `name` already exists in
    `coll` with a POLY spline of the SAME point count, its point data is rewritten in place
    (whole-tuple `co` writes, matching `road_kit_authoring.live_edit._translate_spine`'s own safe
    convention) and that SAME object is returned -- no `bpy.data.objects.remove`/`.new` at all.
    This is what actually removes the "delete the object a modal Transform operator is still
    holding" crash class for `junction_pad`/`curb_loop`, which previously reconstructed a fresh
    object (and, worse, a fresh GN modifier -- see `junction_pad`/`curb_loop`'s own update-in-place
    handling of that) on every single drag tick. Point COUNT changing (e.g. an arm added/removed,
    or a curb style toggled) is a genuinely different topology -- that case still deletes and
    rebuilds fresh, same as before, but only ever happens from a deliberate button click, never a
    live drag, so the reentrancy hazard this fix targets doesn't apply there anyway."""
    existing = coll.objects.get(name)
    if (existing is not None and existing.type == 'CURVE' and existing.data.splines
            and len(existing.data.splines[0].points) == len(pts_radius)):
        # `pts_radius` are absolute world-space coordinates -- the object's own transform must
        # stay at identity (see the update-in-place note above), but reusing an object does NOT
        # implicitly reset a transform a prior Grab/Rotate on the piece selection may have left
        # non-zero (`RKA_OT_select_piece` selects pad_/curb_ objects too, not just markers).
        # Confirmed root cause of "arm/pad generation in a strange shape, still wrong after
        # releasing the drag" (2026-08) -- reset explicitly on every reuse.
        existing.location = (0.0, 0.0, 0.0)
        existing.rotation_euler = (0.0, 0.0, 0.0)
        existing.scale = (1.0, 1.0, 1.0)
        sp = existing.data.splines[0]
        for i, (x, y, z, r) in enumerate(pts_radius):
            sp.points[i].co = (x, y, z, 1.0)
            sp.points[i].radius = max(r, 1e-4)   # exactly 0 confuses Fillet Curve's Poly mode
        sp.use_cyclic_u = closed
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)
    cu = bpy.data.curves.new(name + "_bound", 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts_radius) - 1)
    for i, (x, y, z, r) in enumerate(pts_radius):
        sp.points[i].co = (x, y, z, 1.0)
        sp.points[i].radius = max(r, 1e-4)   # exactly 0 confuses Fillet Curve's Poly mode
    sp.use_cyclic_u = closed
    obj = bpy.data.objects.new(name + "_bound", cu)
    coll.objects.link(obj)
    obj.name = name   # match the update-in-place identity check above on the NEXT call
    obj["_rka_touched"] = True
    return obj


def make_junction_pad_group():
    """GN_JunctionPad: a closed boundary curve (per-point Radius, see `_poly_curve_with_radius`) ->
    Fillet Curve (Poly mode, radius from the curve's own per-point Radius, `Limit Radius` on as a
    second, GN-native safety net alongside `intersection_kit`'s own tangent-length clamp) -> Fill
    Curve (N-gons) -> one filled pavement mesh, materialed, shade-smooth.

    This is the direct fix for the "widen one arm -> curb moves but pavement doesn't" bug: the pad
    is generated PURELY from the boundary polygon's own geometry, never from the union of
    per-lane-movement ribbons (which was capped at `min(a.lanes, b.lanes)` between arm pairs and
    could leave bare gaps) -- so it can never have a coverage gap regardless of how lane counts
    differ across arms. Returns `(node_group, (mat_id, seg_id, z_id))`.

    **Z-restore step (2026-07-27, user-reported "collision far from real mesh"/vehicles floating
    above the visual pad):** `Fill Curve` (N-gons mode) empirically FLATTENS every output vertex
    to world Z=0 regardless of the input curve's actual height -- confirmed directly: a plain flat
    closed curve with every point at Z=0.15 fed through Fill Curve alone (no fillet involved)
    still evaluates to Z=0.0 everywhere. This silently sank every intersection's visual pavement to
    Z=0 while `colonly_polygon`'s collision proxy (built independently straight from the same
    boundary points, never routed through Fill Curve) correctly sat at the real height
    (`lane_surface_z`, ~0.15m by default) -- a ~15-20cm vertical gap between the visible road
    surface and where vehicles/characters actually rest, exactly the reported symptom. Since every
    junction pad is flat by construction (`_populate_intersection_mesh.to3r` uses one single `z`
    for the whole boundary, never per-point height), the fix doesn't need to preserve arbitrary
    per-point Z through the fill -- a `Separate XYZ` -> `Combine XYZ` (X/Y passthrough, Z replaced
    by a new `Pad Z` input) -> `Set Position` (Position, not Offset -- REPLACES the wrong Z instead
    of stacking onto it) restores the correct flat height unconditionally, regardless of whatever
    height Fill Curve's internals happen to compute."""
    ng = bpy.data.node_groups.get("GN_JunctionPad")
    if ng:
        return ng, (ng["mat_id"], ng["seg_id"], ng["z_id"])
    ng = bpy.data.node_groups.new("GN_JunctionPad", "GeometryNodeTree")
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    mat_sock = ifc.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    seg_sock = ifc.new_socket("Segments", in_out="INPUT", socket_type="NodeSocketInt")
    seg_sock.default_value = 8
    z_sock = ifc.new_socket("Pad Z", in_out="INPUT", socket_type="NodeSocketFloat")
    z_sock.default_value = 0.0
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-700, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (900, 0)
    fillet = ng.nodes.new("GeometryNodeFilletCurve"); fillet.location = (-450, 0)
    fillet.inputs["Mode"].default_value = "Poly"
    fillet.inputs["Limit Radius"].default_value = True
    rad = ng.nodes.new("GeometryNodeInputRadius"); rad.location = (-650, -220)
    fill = ng.nodes.new("GeometryNodeFillCurve"); fill.location = (-180, 0)
    fill.inputs["Mode"].default_value = "N-gons"
    pos = ng.nodes.new("GeometryNodeInputPosition"); pos.location = (-180, -240)
    sepxyz = ng.nodes.new("ShaderNodeSeparateXYZ"); sepxyz.location = (20, -240)
    combxyz = ng.nodes.new("ShaderNodeCombineXYZ"); combxyz.location = (220, -240)
    setpos = ng.nodes.new("GeometryNodeSetPosition"); setpos.location = (280, 0)  # restore real Z
    setm = ng.nodes.new("GeometryNodeSetMaterial"); setm.location = (460, 0)
    ss = ng.nodes.new("GeometryNodeSetShadeSmooth"); ss.location = (680, 0)
    L = ng.links.new
    L(nin.outputs["Geometry"], fillet.inputs["Curve"])
    L(rad.outputs["Radius"], fillet.inputs["Radius"])
    L(nin.outputs["Segments"], fillet.inputs["Count"])
    L(fillet.outputs["Curve"], fill.inputs["Curve"])
    L(pos.outputs["Position"], sepxyz.inputs["Vector"])
    L(sepxyz.outputs["X"], combxyz.inputs["X"])
    L(sepxyz.outputs["Y"], combxyz.inputs["Y"])
    L(nin.outputs["Pad Z"], combxyz.inputs["Z"])
    L(fill.outputs["Mesh"], setpos.inputs["Geometry"])
    L(combxyz.outputs["Vector"], setpos.inputs["Position"])
    L(setpos.outputs["Geometry"], setm.inputs["Geometry"])
    L(nin.outputs["Material"], setm.inputs["Material"])
    L(setm.outputs["Geometry"], ss.inputs["Geometry"])
    L(ss.outputs["Geometry"], nout.inputs["Geometry"])
    ng["mat_id"] = mat_sock.identifier
    ng["seg_id"] = seg_sock.identifier
    ng["z_id"] = z_sock.identifier
    return ng, (mat_sock.identifier, seg_sock.identifier, z_sock.identifier)


def junction_pad(name, boundary_pts_radius, coll, matkey="asphalt", segments=8):
    """A filled intersection pavement pad from a closed boundary polygon (see
    `_poly_curve_with_radius`) via `GN_JunctionPad`. Returns the boundary/pad object (modifier
    live; glTF export bakes it, same convention as `road_from_curve`/`barrier_from_curve`).

    `Pad Z` is read off `boundary_pts_radius[0][2]` -- every point in a junction boundary shares
    the same Z by construction (a junction pad is always flat), so the first point's Z is the
    correct flat height for the whole pad; see `make_junction_pad_group`'s docstring for why this
    needs restoring at all (Fill Curve silently flattens to 0 otherwise)."""
    bound = _poly_curve_with_radius(name, boundary_pts_radius, coll, closed=True)
    ng, (mat_id, seg_id, z_id) = make_junction_pad_group()
    mod = bound.modifiers.get("Pad")   # reuse the existing modifier when `bound` was updated
    if mod is None:                     # in place -- a second `.modifiers.new("Pad", ...)` would
        mod = bound.modifiers.new("Pad", "NODES")   # stack a redundant "Pad.001" instead
        mod.node_group = ng
    set_mod_input(mod, mat_id, mat(matkey))
    set_mod_input(mod, seg_id, segments)
    set_mod_input(mod, z_id, boundary_pts_radius[0][2] if boundary_pts_radius else 0.0)
    bound.name = name
    return bound


def make_curb_loop_group():
    """GN_CurbLoop: the same filleted boundary curve as `GN_JunctionPad`, swept with a
    caller-supplied cross-section PROFILE object via Curve to Mesh -> one continuous,
    correctly-mitered curb loop (native Curve to Mesh handles corner miters for free; the old
    per-corner `swept_wall` code did not). `Profile` is a `NodeSocketObject` (NOT Geometry --
    assigning an Object directly to a Geometry-typed modifier input silently no-ops in this
    Blender version; routing it through an Object socket into an internal `Object Info` node is
    what actually works, verified against Blender 5.1) wired through this group's own `Object
    Info` node, so `curb_loop` can hand it a small cached BOX/GUTTER cross-section object (see
    `_curb_profile_object`) without duplicating profile geometry per curb. Returns
    `(node_group, (mat_id, seg_id, prof_id))`."""
    ng = bpy.data.node_groups.get("GN_CurbLoop")
    if ng:
        return ng, (ng["mat_id"], ng["seg_id"], ng["prof_id"])
    ng = bpy.data.node_groups.new("GN_CurbLoop", "GeometryNodeTree")
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    mat_sock = ifc.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    seg_sock = ifc.new_socket("Segments", in_out="INPUT", socket_type="NodeSocketInt")
    seg_sock.default_value = 8
    prof_sock = ifc.new_socket("Profile", in_out="INPUT", socket_type="NodeSocketObject")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-700, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (700, 0)
    fillet = ng.nodes.new("GeometryNodeFilletCurve"); fillet.location = (-450, 100)
    fillet.inputs["Mode"].default_value = "Poly"
    fillet.inputs["Limit Radius"].default_value = True
    rad = ng.nodes.new("GeometryNodeInputRadius"); rad.location = (-650, -140)
    oi = ng.nodes.new("GeometryNodeObjectInfo"); oi.location = (-450, -260)
    c2m = ng.nodes.new("GeometryNodeCurveToMesh"); c2m.location = (-180, 0)
    # Fill Caps was unconditionally True (2026-07-28, user-reported: EVERY segment's curb shows a
    # solid box-shaped end wall exactly where it meets an intersection/another segment -- "align is
    # at top of the curb, not at road level" -- confirmed directly: an exported curb's end ring at
    # the connection point has all 4 cross-section corners present, a fully closed box face, not an
    # open end). Root cause: this curb loop is built from an OPEN (non-cyclic) boundary curve for a
    # segment's own L/R curb (`curb_loop(..., closed=False)`) -- Curve to Mesh's Fill Caps then caps
    # BOTH ends of that boundary with the profile's own shape, i.e. a solid curb-height block right
    # at the connection, the exact same class of bug `GN_RoadProfile`'s pavement had (fixed
    # 2026-07-28 earlier the same day) but never applied here. Fix is simpler here: Curve to Mesh
    # has a direct boolean for this (no manual endpoint-tag/delete-geometry plumbing needed like the
    # pavement fix required). False is safe unconditionally: a CLOSED intersection curb loop
    # (`closed=True`) is a cyclic boundary curve with no ends at all, so Fill Caps was already a
    # no-op there -- this only ever changed behavior for open (segment/transition) curbs, which is
    # exactly the case that needed it.
    c2m.inputs["Fill Caps"].default_value = False
    setm = ng.nodes.new("GeometryNodeSetMaterial"); setm.location = (60, 0)
    ss = ng.nodes.new("GeometryNodeSetShadeSmooth"); ss.location = (280, 0)
    L = ng.links.new
    L(nin.outputs["Geometry"], fillet.inputs["Curve"])
    L(rad.outputs["Radius"], fillet.inputs["Radius"])
    L(nin.outputs["Segments"], fillet.inputs["Count"])
    L(nin.outputs["Profile"], oi.inputs["Object"])
    L(fillet.outputs["Curve"], c2m.inputs["Curve"])
    L(oi.outputs["Geometry"], c2m.inputs["Profile Curve"])
    L(c2m.outputs["Mesh"], setm.inputs["Geometry"])
    L(nin.outputs["Material"], setm.inputs["Material"])
    L(setm.outputs["Geometry"], ss.inputs["Geometry"])
    L(ss.outputs["Geometry"], nout.inputs["Geometry"])
    ng["mat_id"] = mat_sock.identifier
    ng["seg_id"] = seg_sock.identifier
    ng["prof_id"] = prof_sock.identifier
    return ng, (mat_sock.identifier, seg_sock.identifier, prof_sock.identifier)


def curb_outer_clearance(curb_style, curb_thickness, asset_obj=None):
    """How far a curb wall of `curb_style` genuinely extends past the boundary LINE it was swept
    from (a segment's `half_w`/`half_w_end`, or an intersection arm's `out_width()`/`in_width()`)
    -- 2026-08, user-reported: a sidewalk's own offset previously started exactly AT that boundary
    line, but a BOX curb's profile (`_curb_profile_object`) straddles it symmetrically
    (`+-curb_thickness/2`), so its OUTER half (e.g. 0.125m at the 0.25m default) visibly overlapped
    the sidewalk. GUTTER's profile is one-sided (road edge at the line -> curb top at
    `+curb_thickness`, see `gutter_curb_profile`), so its full thickness extends past the line, not
    half. NONE has no wall at all (0 clearance -- a sidewalk can start right at the lane edge).
    Shared by `ops_segment._populate_segment_mesh_gn` and `ops_intersection._populate_
    intersection_mesh` so a segment's and an intersection's sidewalk offset can never disagree
    about what "flush against the curb" means.

    ASSET (2026-08 follow-up, user-reported against real content: "the sideway seem not align
    with asset curb, and only align with box curb... there will be a gap for asset between curb
    and sideway"): measured directly off the RESOLVED asset object's own local bounding box (its
    max local Y) -- per `tools/build_curb_kit.py`'s own documented pivot convention (origin at the
    boundary line, front/outward face on local +Y), the piece's own outermost point IS its real
    outward clearance, whether it's centered on the line (e.g. `Kit_Curb_JerseyBarrier_L2`,
    straddling +-0.35m) or purely one-sided (e.g. `Kit_Curb_FencePost_L1`, 0 to +0.1m) -- no new
    authoring convention needed, every existing kit piece already carries this. Falls back to 0.0
    when the piece hasn't resolved yet (blank/unlinked -- same 'nothing to measure' case ASSET
    curbs already have for `bake_colonly_proxies`'s boundary)."""
    if curb_style == 'GUTTER':
        return curb_thickness
    if curb_style == 'ASSET':
        if asset_obj is None:
            return 0.0
        return max(0.0, max(corner[1] for corner in asset_obj.bound_box))
    if curb_style == 'PROFILE':
        # Same "measure the resolved piece's own real geometry" approach as ASSET, just off the
        # extracted profile CURVE's own lateral (local X) extent instead of a raw mesh bound_box
        # -- see `asset_profile_object`'s own docstring for why this is the right measurement for
        # a continuously-swept (not tiled) curb.
        prof_obj = asset_profile_object(asset_obj)
        if prof_obj is None:
            return 0.0
        xs = [pt.co.x for pt in prof_obj.data.splines[0].points]
        return max(0.0, max(xs)) if xs else 0.0
    if curb_style == 'NONE':
        return 0.0
    return curb_thickness / 2.0   # BOX (default/fallback)


def asset_row_width(asset_obj):
    """The real physical width (local Y bounding-box extent) of a `curb_asset_row` piece -- e.g.
    a sidewalk kit tile's actual paved width, independent of whatever `sidewalk_*_width` value the
    caller has separately configured as the design width. `curb_asset_row` centers each instance
    ON the boundary line it's given (matching `curb_loop`'s own symmetric-profile convention, same
    as `curb_outer_clearance`'s docstring), so a caller offsetting that boundary line to place a
    sidewalk/curb/median row must use THIS measured width, not the configured design width, or the
    placed row lands at the wrong distance from the curb whenever the two disagree.

    2026-08, user-reported: an ASSET-style sidewalk read as broken/misaligned ("previously when
    using plane seem to be better"). Root cause: `sidewalk_l_width`/`sidewalk_r_width` (design
    width, e.g. the panel's own `DEFAULT_SIDEWALK_WIDTH = 3.5`) was being used DIRECTLY as the
    swept width in the offset-line formula (`half_w + curb_clearance + sidewalk_width/2`) -- exact
    for the procedural `curb_loop(curb_thickness=sidewalk_width)` sweep, since its swept thickness
    genuinely equals that value, but wrong for an ASSET tile whose OWN physical width is fixed by
    the chosen kit mesh (e.g. `Kit_Curb_SidewalkTile_L2` is a fixed 3.0m regardless of the dial).
    A 3.5m-configured sidewalk with a 3.0m-wide tile placed the tile's centerline 0.25m too far
    out, opening a real gap between the curb's outer edge and the tile's own inner edge (and an
    equal-sized unexplained overhang past the tile's outer edge) -- confirmed directly against
    `world_session.blend`'s own authored intersection sidewalk. Callers should measure this BEFORE
    computing the offset line whenever an asset object is resolved, and fall back to the
    configured design width otherwise (see `ops_segment._populate_segment_mesh_gn`/
    `ops_intersection._populate_intersection_sidewalks`). Returns 0.0 for `None` (nothing to
    measure, caller keeps the design width)."""
    if asset_obj is None:
        return 0.0
    ys = [corner[1] for corner in asset_obj.bound_box]
    return max(ys) - min(ys) if ys else 0.0


_CURB_PROFILE_CACHE = {}


def _curb_profile_object(style, height, thickness):
    """A cached, un-transformed helper Curve object at the origin representing ONE curb
    cross-section (local X = lateral offset from the spine, local Y = up -- the same profile-plane
    convention `GN_BarrierProfile`/`GN_RoadProfile` already use), fed into `GN_CurbLoop`'s Profile
    input. Cached by `(style, height, thickness)` so repeated builds/rebuilds (live-edit!) reuse
    one object instead of leaking a new datablock per rebuild; deliberately NOT linked into any
    scene collection (referenced only by GN modifiers via Object Info, never rendered/exported
    itself -- `export_gltf` only ever selects the objects it's explicitly given).

    The cache is a plain Python module global, so it survives a File > New / File > Open in the
    SAME Blender session (Python globals aren't reset by loading a different .blend) -- the
    PREVIOUS file's cached object is by then a freed RNA struct, and accessing ANY attribute on it
    (including `.name`, for the staleness check itself) raises `ReferenceError`, not a clean
    "not found." Guard with `GD`-style `try/except` instead of a bare attribute read so a stale
    cross-file reference is treated as a cache miss (rebuilt) rather than crashing the next build."""
    key = (style, round(height, 4), round(thickness, 4))
    obj = _CURB_PROFILE_CACHE.get(key)
    try:
        obj_valid = obj is not None and obj.name in bpy.data.objects
    except ReferenceError:
        obj_valid = False
    if obj_valid:
        return obj
    if style == 'GUTTER':
        pts2d = gutter_curb_profile(thickness, height)   # open profile: road edge -> curb top
        cyclic = False
    else:   # BOX (default/fallback for any unrecognized style)
        half_t = thickness / 2.0
        pts2d = [(-half_t, 0.0), (half_t, 0.0), (half_t, height), (-half_t, height)]
        cyclic = True
    cu = bpy.data.curves.new("RKA_CurbProfile_%s" % style, 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts2d) - 1)
    # `GN_CurbLoop`'s Curve to Mesh sweep maps this profile's local +Y to world -Z, the exact
    # same quirk `GN_BarrierProfile` already documents/compensates for ("profile-Y -> world -Z
    # here") -- `_curb_profile_object` never got the matching negation, so every curb hung DOWN
    # from the road surface instead of rising above it (2026-07-28, user-reported: "intersection
    # generated mesh is on upper of curb rather than bottom of curb" -- confirmed directly, a
    # flat curb line at Z=5.0 evaluated to [4.85, 5.0] instead of [5.0, 5.15]; same bug on both
    # segments and intersections, since both go through this one shared helper). Negate the
    # height component here, NOT inside `gutter_curb_profile()` itself, which is also used by
    # `ops_intersection.build_curb`'s GUTTER branch through `swept_profile` -- a different,
    # hand-rolled sweep with its own (already-correct, un-inverted) Z convention.
    for i, (lat, h) in enumerate(pts2d):
        sp.points[i].co = (lat, -h, 0.0, 1.0)
    sp.use_cyclic_u = cyclic
    obj = bpy.data.objects.new("RKA_CurbProfile_%s" % style, cu)
    _CURB_PROFILE_CACHE[key] = obj
    return obj


def extract_cross_section_profile(asset_obj, x_frac=0.5):
    """Slice `asset_obj`'s own mesh at a fixed local X (`x_frac`, default 0.5 = the piece's own
    local-X MIDPOINT, avoiding any end-cap geometry that wouldn't represent the piece's REPEATING
    cross-section) and return its cross-section as an ORDERED list of `(lateral, height)` 2D
    points -- `lateral` = the asset's own local Y (its authored lateral/'outward' axis, see
    `tools/build_curb_kit.py`'s worked-example pivot convention: "Length runs along local +X...
    Front/outward face on local +Y"), `height` = the asset's own local Z, UN-negated (matches the
    plain `pts2d` shape `_curb_profile_object` already expects before ITS OWN `-h` write
    convention -- see that function's docstring for the profile-plane quirk this shares).

    2026-08, user-requested: "let GN use the outline of a mesh 2d to form more complex shape
    rather than box... to ensure no gap" -- the CONTINUOUS-sweep answer to the "geometric limit of
    rigid pieces on a curve" finding (tiling a REPEATED discrete mesh around a corner always
    shows a joint/gap on the outer edge; sweeping ONE profile continuously, the way BOX-style curb
    already works, has no joint at all, by construction, regardless of how tight the corner is).

    Uses `bmesh.ops.bisect_plane` (a single planar cut) then walks the resulting cut EDGES into an
    ordered loop by vertex adjacency (each vertex has exactly 2 cut edges for a simple closed
    manifold cross-section, the case every current kit piece's own PRIMARY sub-object satisfies --
    see `_resolve_curb_asset`'s own docstring for what 'primary' means for a multi-part piece,
    e.g. `Kit_Curb_JerseyBarrier_L2`'s resolved cross-section is its `base` box only, not the
    separate `_Cap` sub-object, matching the same 'the primary object represents the piece'
    convention `asset_row_width`/`asset_row_length` already use). Returns `None` if the mesh has
    no geometry at that X, or the cut isn't a single simple loop (a multi-island or open
    cross-section -- not a shape this profile-plane sweep can represent; caller falls back the
    same way an unresolved ASSET piece already does)."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(asset_obj.data)
    xs = [v.co.x for v in bm.verts]
    if not xs:
        bm.free()
        return None
    x_cut = min(xs) + (max(xs) - min(xs)) * x_frac
    geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
    result = bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(x_cut, 0.0, 0.0),
                                     plane_no=(1.0, 0.0, 0.0), clear_inner=False, clear_outer=False)
    cut_edges = [e for e in result['geom_cut'] if isinstance(e, bmesh.types.BMEdge)]
    if not cut_edges:
        bm.free()
        return None
    adjacency = {}
    for e in cut_edges:
        v0, v1 = e.verts
        adjacency.setdefault(v0, []).append(v1)
        adjacency.setdefault(v1, []).append(v0)
    start = cut_edges[0].verts[0]
    loop = [start]
    prev, cur = None, start
    guard = len(cut_edges) + 2   # a simple closed loop visits len(cut_edges) verts at most
    while len(loop) <= guard:
        nexts = [v for v in adjacency.get(cur, []) if v is not prev]
        if not nexts:
            break
        nxt = nexts[0]
        if nxt is start:
            break
        loop.append(nxt)
        prev, cur = cur, nxt
    pts2d = [(v.co.y, v.co.z) for v in loop]
    bm.free()
    return pts2d if len(pts2d) >= 3 else None


_ASSET_PROFILE_CACHE = {}


def asset_profile_object(asset_obj):
    """Cached profile Curve object built from `asset_obj`'s own cross-section
    (`extract_cross_section_profile`) -- the asset-derived counterpart to `_curb_profile_object`,
    same profile-plane convention (including its `-h` write quirk), same module-global cache
    idiom (survives a File > New/Open in one session; a stale cross-file reference is treated as
    a cache miss via the same `ReferenceError` guard). Feeds `GN_CurbLoop`'s Profile input via
    `curb_loop(curb_style='PROFILE', asset_obj=...)`, so a curb/median/sidewalk can follow the
    resolved kit piece's own real silhouette while sweeping CONTINUOUSLY along any curve -- no
    discrete tiling, no per-joint corner seam, by construction. Returns `None` when `asset_obj`
    doesn't resolve or its cross-section can't be extracted, matching the same 'no piece = no
    geometry' convention every ASSET-style caller already has."""
    if asset_obj is None:
        return None
    key = asset_obj.name
    cached = _ASSET_PROFILE_CACHE.get(key)
    try:
        cached_valid = cached is not None and cached.name in bpy.data.objects
    except ReferenceError:
        cached_valid = False
    if cached_valid:
        return cached
    pts2d = extract_cross_section_profile(asset_obj)
    if not pts2d:
        return None
    cu = bpy.data.curves.new("RKA_AssetProfile_%s" % key, 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts2d) - 1)
    for i, (lat, h) in enumerate(pts2d):
        sp.points[i].co = (lat, -h, 0.0, 1.0)   # same '-h' quirk as _curb_profile_object
    sp.use_cyclic_u = True
    obj = bpy.data.objects.new("RKA_AssetProfile_%s" % key, cu)
    _ASSET_PROFILE_CACHE[key] = obj
    return obj


def curb_loop(name, boundary_pts_radius, coll, curb_style='BOX', curb_height=0.15,
              curb_thickness=0.25, matkey="concrete", segments=8, closed=True, asset_obj=None):
    """One continuous, correctly-mitered curb from a boundary/edge polygon via `GN_CurbLoop`.
    `closed=True` (default) is an intersection's full loop (same boundary `junction_pad` uses);
    `closed=False` is an OPEN edge line -- a straight/transition segment's own L or R curb, no
    corners to fillet (pass 0 radius for every point; Fillet Curve is then a no-op). Returns
    `None` for `curb_style == 'NONE'` (curb toggled off -- the caller skips linking/using the
    result, no wasted empty object is created at all) OR for `curb_style == 'PROFILE'` when
    `asset_obj` doesn't resolve/its cross-section can't be extracted (same 'no piece = no
    geometry' convention ASSET style already has).

    `curb_style == 'PROFILE'` (2026-08, user-requested: "let GN use the outline of a mesh 2d to
    form more complex shape rather than box... to ensure no gap") sweeps `asset_obj`'s OWN
    cross-section (`asset_profile_object`) instead of the built-in flat BOX/GUTTER profile --
    the resolved kit piece's real silhouette (a taper, a lip, anything more complex than a flat
    rectangle), swept CONTINUOUSLY along any curve exactly like BOX already is, so it has no
    discrete tiling and therefore no per-joint corner seam at all, unlike ASSET style's repeated
    instances (see that style's own docstring for the geometric limit this replaces)."""
    if curb_style == 'NONE':
        # A rebuild that switches an EXISTING curb to NONE (RKA_OT_set_curb_style) must still
        # clean up the now-stale object -- `_poly_curve_with_radius`'s update-in-place path is
        # never reached in this branch (nothing to update it FROM), so this is the one place
        # left that still needs an explicit delete for the "topology changed" case.
        stale = coll.objects.get(name)
        if stale is not None:
            bpy.data.objects.remove(stale, do_unlink=True)
        return None
    if curb_style == 'PROFILE':
        prof_obj = asset_profile_object(asset_obj)
        if prof_obj is None:
            stale = coll.objects.get(name)
            if stale is not None:
                bpy.data.objects.remove(stale, do_unlink=True)
            return None
    else:
        prof_obj = _curb_profile_object(curb_style, curb_height, curb_thickness)
    bound = _poly_curve_with_radius(name, boundary_pts_radius, coll, closed=closed)
    ng, (mat_id, seg_id, prof_id) = make_curb_loop_group()
    mod = bound.modifiers.get("Curb")   # reuse in place -- see junction_pad's identical reasoning
    if mod is None or mod.node_group is not ng:
        # 2026-08: a same-named object may have survived from ASSET style instead (`curb_asset_row`
        # now ALSO builds a Curve object sharing this exact name convention, with its own "GN"
        # modifier, not "Curb") -- `_poly_curve_with_radius`'s reuse check is point-count-only, not
        # modifier-identity-aware, so clear anything stale before attaching this style's own
        # modifier (mirrors `curb_asset_row`'s identical fix, same reasoning, opposite direction).
        for m in list(bound.modifiers):
            bound.modifiers.remove(m)
        mod = bound.modifiers.new("Curb", "NODES")
        mod.node_group = ng
    set_mod_input(mod, mat_id, mat(matkey))
    set_mod_input(mod, seg_id, segments)
    set_mod_input(mod, prof_id, prof_obj)
    bound.name = name
    return bound


# Named cross-section silhouettes for `swept_profile_between` -- each a list of `(t, h)` pairs
# walking the FULL outline in order (both vertical risers and horizontal top segments), `t` in
# [0,1] = normalized lateral position from the left rail (0) to the right rail (1), `h` in [0,1] =
# fraction of the caller's own `height` argument raised at that point. 2026-08, user-requested: a
# median should always be ONE mesh + a gap distance -- what varies is only this silhouette ("two
# curb" / "one curb" / "one wall" / painted line(s) / no mesh at all), not the mechanism.
MEDIAN_PROFILES = {
    # Solid box spanning the WHOLE gap -- "one wall" / a wide "two-lane separator" island
    # (tune width via the existing Median Width, not a separate parameter).
    'WALL': [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)],
    # Raised lip at BOTH edges, flat/low in the middle -- reads like the OLD two-separate-curb
    # look (a raised edge on each side of the gap) but is genuinely ONE continuous mesh, no seam.
    'DOUBLE_LIP': [(0.0, 0.0), (0.0, 1.0), (0.12, 1.0), (0.12, 0.0),
                   (0.88, 0.0), (0.88, 1.0), (1.0, 1.0), (1.0, 0.0)],
    # Raised lip on the LEFT edge only (mirror the two rails to flip sides) -- "one curb".
    'SINGLE_LIP': [(0.0, 0.0), (0.0, 1.0), (0.2, 1.0), (0.2, 0.0), (1.0, 0.0)],
    # Flush (h always 0) painted line(s) -- geometry-flat, distinguished visually by matkey
    # ("line_y") at the call site, not by height. One line centered in the gap...
    'PAINT_1': [(0.0, 0.0), (0.45, 0.0), (0.45, 0.0), (0.55, 0.0), (0.55, 0.0), (1.0, 0.0)],
    # ...or two lines, one near each edge (each spans a FRACTION of however wide the gap currently
    # is, so it narrows/widens with the median the same as everything else here -- an intentional
    # simplification vs. a fixed absolute paint width, which would need a per-point-varying
    # profile; fine for a lane-marking-scale stripe).
    'PAINT_2': [(0.05, 0.0), (0.15, 0.0), (0.15, 0.0), (0.05, 0.0),
                (0.85, 0.0), (0.95, 0.0), (0.95, 0.0), (0.85, 0.0)],
}


def swept_profile_between(name, left_pts, right_pts, coll, profile=None, height=0.0,
                           matkey="concrete"):
    """Sweep an arbitrary 2D cross-section `profile` (see `MEDIAN_PROFILES`; `None` default = a
    plain flat fill, `[(0,0),(1,0)]`) between two parallel-ish rails (e.g. a segment's own tapered
    `median_edges`) -- ONE continuous mesh whose silhouette can be a flush fill, a solid wall,
    curbed lips at one/both edges, or painted line(s), all through the SAME mechanism (2026-08,
    user-requested: a median should always be a single mesh + a gap distance, only the silhouette
    should vary). Plain Python vertex/face construction (no GN needed, same low-level technique
    `flat_ribbon`/`marking_ribbon` already use) -- unlike `curb_loop` (a FIXED-width cross-section
    swept via Curve to Mesh, can't taper along its own length), this follows each rail's OWN
    already-tapered points directly, so both the gap WIDTH and every profile point's lateral
    position taper correctly along the segment. No kit-library asset to link (the bug this
    replaces: a kit-asset-based single median silently built NOTHING when its target collection
    wasn't linked/resolved -- this is pure procedural geometry, always available)."""
    profile = profile or [(0.0, 0.0), (1.0, 0.0)]
    n = min(len(left_pts), len(right_pts))
    k = len(profile)
    if n < 2 or k < 2:
        return None
    verts = []
    for i in range(n):
        lx, ly, lz = left_pts[i][0], left_pts[i][1], left_pts[i][2]
        rx, ry, rz = right_pts[i][0], right_pts[i][1], right_pts[i][2]
        for (t, h) in profile:
            verts.append((lx + (rx - lx) * t, ly + (ry - ly) * t, lz + (rz - lz) * t + h * height))
    faces = []
    for i in range(n - 1):
        base0, base1 = i * k, (i + 1) * k
        for j in range(k - 1):
            faces.append((base0 + j, base0 + j + 1, base1 + j + 1, base1 + j))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    recalc_normals(me)
    # UPDATE-IN-PLACE (same crash-surface-safe reasoning as marking_ribbon/flat_ribbon -- topology
    # can change (point count) between rebuilds, so this reuses the OBJECT unconditionally rather
    # than checking point count like _poly_curve_with_radius does).
    existing = coll.objects.get(name)
    if existing is not None and existing.type == 'MESH':
        # A same-named object might be a STALE `instancer()`-built GN object (a piece that used to
        # be `ASSET_SINGLE`/`curb_asset_row`, now switched to this plain-mesh `SINGLE` style, same
        # name convention -- `ops_segment.py`'s median block). Its "GN" modifier would otherwise
        # keep instancing the old asset onto this function's much smaller vertex set (wrong/broken
        # geometry) -- explicit clear, same "delete the mismatched reuse candidate's stale bits"
        # reasoning `instancer()` itself uses when the reverse switch happens.
        for mod in list(existing.modifiers):
            existing.modifiers.remove(mod)
        existing.location = (0.0, 0.0, 0.0)
        existing.rotation_euler = (0.0, 0.0, 0.0)
        existing.scale = (1.0, 1.0, 1.0)
        old_data = existing.data
        existing.data = me
        if old_data is not None and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
        me.materials.clear()
        me.materials.append(mat(matkey))
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    obj.data.materials.append(mat(matkey))
    obj["_rka_touched"] = True
    return obj


def asset_row_length(asset_obj):
    """The real physical LENGTH (local X bounding-box extent) of a `curb_asset_row` piece -- the
    axis it tiles along (`tools/build_curb_kit.py`'s pivot convention: "Length runs along local
    +X"). Counterpart to `asset_row_width` (the lateral/Y extent); used to auto-correct the
    caller's requested tiling `spacing` so it always evenly divides the piece's own real length
    (see `curb_asset_row`'s docstring) instead of relying on the caller/user to have measured it
    by hand. Returns 0.0 for `None`."""
    if asset_obj is None:
        return 0.0
    xs = [corner[0] for corner in asset_obj.bound_box]
    return max(xs) - min(xs) if xs else 0.0


def resample_polyline_even(pts, spacing, include_endpoint=False, phase_offset=0.0):
    """Resample 3D polyline `pts=[(x,y,z), ...]` into N EVENLY-SPACED anchor points
    `[(pos, heading_rad), ...]`, where `N = max(1, round(total_length / spacing))` -- so N
    instances of ~`spacing` length tile the WHOLE polyline with ZERO leftover gap or overlap by
    construction (`real_spacing = total_length / N` always divides the length exactly, however far
    `spacing` itself was from a clean divisor). This is the resample step `curb_asset_row` needs
    and is mathematically identical to what a Geometry Nodes 'Resample Curve' (Count mode) ->
    'Curve to Points' would produce for this same POLY (straight-segment) boundary -- there's no
    spline curvature for GN's own curve evaluator to add that this doesn't already capture, so
    computing it directly here (rather than round-tripping through a live GN modifier) needs no
    scene-linked helper object and cannot disagree with a downstream Python reader of the result.
    Each anchor's heading is the LOCAL polyline segment's own tangent (`atan2`), same convention
    `sample_polyline` already uses.

    Differs from `sample_polyline` (still used by this module's OTHER callers, e.g. `assemble.py`'s
    pier placement, which want a plain fixed-step walk with an anchor forced at the true endpoint)
    -- 2026-08, user-reported: an ASSET curb/sidewalk/median row "break[s]... left [a] major
    gap... instead of smooth line" where BOX (one continuous swept mesh) reads as fine.
    `sample_polyline`'s fixed-step walk always over/undershoots the boundary's true end by up to a
    full `spacing`, then unconditionally appends one more anchor exactly at that end regardless of
    phase -- for a TILED row (not a one-off endpoint anchor) that's either a real leftover gap
    right before the forced final piece, or a mostly-overlapping double-thickness clump when the
    two land close together. This function's even-count redistribution instead guarantees every
    tile boundary lands exactly back-to-back, always.

    `include_endpoint=True` additionally appends the polyline's own exact final point as one MORE
    anchor past the last regular (evenly-tiled) one -- for a caller that needs an anchor EXACTLY
    at a boundary's far end regardless of tiling phase (e.g. `median_merge.py`'s cross-piece chain
    continuity, which asserts a merged row's last point lands byte-exact on the next piece's own
    port -- see `curb_asset_row`'s own `include_endpoint` passthrough). Defaults off for ordinary
    visual tiling, which would otherwise reintroduce exactly the double-thickness tail artifact
    this function exists to remove.

    `phase_offset` (meters, default 0.0) shifts every regular anchor's own distance-along-boundary
    by this much before placing it -- 2026-08, user-requested streetlight arrays: "Stagger
    streetlight positions on alternating sides of the street" (a real-world spacing convention, so
    poles don't line up directly across from each other). An anchor whose shifted distance would
    land at/past the boundary's true end is simply DROPPED (not wrapped back to the start, which
    would place it somewhere visually unrelated on a non-cyclic boundary) -- so a nonzero
    `phase_offset` yields one fewer anchor than the unshifted case, not a repositioned same count.
    This is a cosmetic spacing tool (streetlights, unlike curb/sidewalk/median tiles, are never
    meant to butt edge-to-edge), so losing exact 'divides the whole length' coverage right at the
    shifted end is an acceptable, intended tradeoff -- not reused by any edge-to-edge tiling
    caller."""
    if len(pts) < 2:
        return []
    segs = []
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-9:
            continue
        segs.append((a, b, L))
        total += L
    if not segs:
        return []
    n = max(1, round(total / spacing)) if spacing > 1e-9 else 1
    real_spacing = total / n
    out = []
    seg_idx = 0
    seg_start_d = 0.0
    a, b, L = segs[0]
    for i in range(n):
        d = i * real_spacing + phase_offset
        if d < 0.0 or d >= total:
            continue
        while d > seg_start_d + L + 1e-9 and seg_idx < len(segs) - 1:
            seg_start_d += L
            seg_idx += 1
            a, b, L = segs[seg_idx]
        t = 0.0 if L < 1e-9 else max(0.0, min(1.0, (d - seg_start_d) / L))
        pos = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
        hd = math.atan2(b[1] - a[1], b[0] - a[0])
        out.append((pos, hd))
    if include_endpoint:
        last_a, last_b, _ = segs[-1]
        out.append((tuple(last_b), math.atan2(last_b[1] - last_a[1], last_b[0] - last_a[0])))
    return out


def curb_asset_row(name, boundary_pts_radius, coll, asset_obj, spacing, rot_offset_deg=0.0,
                    include_endpoint=False, phase_offset=0.0, exclude_positions=None,
                    exclude_radius=0.0):
    """Repeat `asset_obj` (a mesh Object, e.g. from a linked kit/curb_kit.blend collection) along
    the edge polyline `boundary_pts_radius` (same 4-tuple-or-3-tuple shape `curb_loop()` accepts
    -- any 4th 'radius' element is ignored here, since ASSET-style curbs never fillet through GN:
    every boundary this addon ever builds is already an explicit polyline, arcs included as
    point density, so there is no live corner radius left to resolve at this style). Combines two
    existing generic building blocks: `resample_polyline_even` (evenly-tiled position+heading, see
    its own docstring for why this is the GN-'Resample Curve'-equivalent technique, computed
    directly since there's no spline curvature involved) feeds `instancer` (GN Instance-on-Points,
    per-point Z-rotation from heading). `rot_offset_deg` (typically 0 or 180) additionally spins
    every instance around its own Z -- the R-side curb of a two-way segment/corner typically needs
    180 relative to the L side so an ASYMMETRIC piece's authored 'front' face (local +Y, see
    tools/build_curb_kit.py's worked example) keeps facing AWAY from the road on both sides, since
    both boundaries are sampled in the same spine direction. Returns the single GN-backed
    instancer Object (None if the boundary has fewer than 2 points after sampling), so it slots
    into the same 'one curb object per side/corner' convention every other curb style returns.

    A `rot_offset_deg` near 180 is handled as a local Y-axis MIRROR (Scale=(1,-1,1) via
    `instancer_scaled`/`GN_InstanceScaled`), NOT a plain extra Z-rotation -- 2026-08, user-reported:
    "right side is push back further than segment while left side is push forward than the road
    segment." Root cause: a full 180-degree Z-rotation flips BOTH local axes together (the
    outward-facing +Y, the intent, AND the length/tiling +X, an unintended side effect), so the
    R-side row's own footprint ran BACKWARD from each anchor instead of forward -- confirmed
    directly, a 40m straight segment's R-side curb evaluated to X in [-2, 38] against the L side's
    correct [0, 40], a full piece-length regression at both ends. A local Scale is applied in the
    instance's OWN unrotated frame BEFORE the heading rotation (verified directly against the
    evaluated mesh), so Scale=(1,-1,1) mirrors ONLY the outward-facing Y axis while the SAME
    heading-only rotation as the L side keeps the length axis pointing forward on both sides. Any
    OTHER `rot_offset_deg` value (unused by every real caller in this codebase, which only ever
    passes 0 or 180) keeps the original plain-rotation behavior, unchanged.

    `spacing` is auto-corrected to the nearest clean multiple of the resolved piece's own REAL
    length (`asset_row_length`, measured off its local-X bound_box) whenever one resolves -- the
    "read it back and suggest a matching spacing instead of the user having to measure it by hand"
    behaviour `tools/build_curb_kit.py`'s worked-example docs always intended but never actually
    implemented (confirmed via grep: `rka_curb_asset_length` was written at kit-build time but
    never read back anywhere). This is what makes an arbitrary spacing dial value safe to leave
    alone -- e.g. the shipped `Kit_Median_YellowSeparator`/`_Island` (2.0m) against the historical
    `median_asset_spacing` default (3.0m, a genuine out-of-the-box mismatch, separately fixed at
    its call sites too) now self-corrects to 2.0m even if some other caller still passes 3.0.

    `include_endpoint` -- see `resample_polyline_even`'s own docstring; passed straight through
    (default False, matching every EXISTING caller's expectations unchanged) for
    `median_merge.py`'s exact-endpoint chain-continuity need.

    `phase_offset` -- see `resample_polyline_even`'s own docstring; passed straight through
    (default 0.0, unchanged for every existing caller) -- 2026-08, user-requested streetlight
    'Stagger... on alternating sides' rule: `ops_segment._populate_segment_mesh_gn` passes half
    the prop spacing on the R side only.

    `exclude_positions` (world-space `(x,y,z)` points, default None = no filtering) + `exclude_
    radius` (meters) drop any resampled anchor within `exclude_radius` of ANY of those points
    before instancing -- 2026-08, user-requested streetlight 'Intersection Exclusion Zone' rule
    (keep a segment's own streetlight row clear of a nearby intersection's signal poles/gantries).
    Checked in WORLD space (the anchors computed here already ARE world-space, same as every
    other `curb_asset_row` caller's boundary), so the caller doesn't need to pre-transform
    anything.

    IMPLEMENTATION (2026-08, user-requested repeatedly: "please use GN and curve for them as
    well"): the common case (`include_endpoint=False`, `phase_offset==0.0`, no
    `exclude_positions` -- i.e. every curb/median/sidewalk row, and a plain prop row with no
    nearby signal to avoid) now builds a LIVE Curve object + `GN_CurbAssetRow` modifier
    (`make_curb_asset_row_group`'s own docstring has the full graph writeup) -- the genuine
    Curve+GN architecture `curb_loop`/BOX style already uses, reused-by-identity across rebuilds
    exactly like `curb_loop`'s own boundary curve (`_poly_curve_with_radius`). The three NEWER,
    narrower features above (`median_merge.py`'s exact cross-piece endpoint match, streetlight
    stagger, streetlight exclusion zone) still go through `_curb_asset_row_python` -- porting
    those into the live graph would need a Trim Curve (stagger) and a Geometry Proximity +
    Delete Geometry pass against an externally-supplied point cloud (exclusion), both real GN
    capabilities but not yet built/verified here; the Python path already correctly implements
    them and stays available as a deliberate, documented fallback for exactly these three cases,
    not a general escape hatch."""
    if include_endpoint or phase_offset != 0.0 or exclude_positions:
        return _curb_asset_row_python(name, boundary_pts_radius, coll, asset_obj, spacing,
                                       rot_offset_deg, include_endpoint, phase_offset,
                                       exclude_positions, exclude_radius)
    if len(boundary_pts_radius) < 2:
        return None
    pts4 = [(p[0], p[1], p[2], 0.0) for p in boundary_pts_radius]
    piece_len = asset_row_length(asset_obj)
    if piece_len > 1e-4:
        spacing = piece_len * max(1, round(spacing / piece_len))
    curve_obj = _poly_curve_with_radius(name, pts4, coll, closed=False)
    ng, sock = make_curb_asset_row_group()
    mod = curve_obj.modifiers.get("GN")
    if mod is None or mod.node_group is not ng:
        # A same-named object may have survived from a DIFFERENT curb style sharing this exact
        # name (`curb_loop`'s own "Curb" modifier for BOX, or a stale modifier from before this
        # architecture change) -- `_poly_curve_with_radius`'s own reuse check is point-count-only,
        # not modifier-identity-aware, so this call must own fixing up the modifier stack whenever
        # it doesn't already have exactly the right one.
        for m in list(curve_obj.modifiers):
            curve_obj.modifiers.remove(m)
        mod = curve_obj.modifiers.new("GN", "NODES")
        mod.node_group = ng
    is_mirror = abs((rot_offset_deg % 360.0) - 180.0) < 1e-3
    set_mod_input(mod, sock["Object"], asset_obj)
    set_mod_input(mod, sock["Spacing"], spacing)
    set_mod_input(mod, sock["RotOffset"], 0.0 if is_mirror else math.radians(rot_offset_deg))
    set_mod_input(mod, sock["ScaleY"], -1.0 if is_mirror else 1.0)
    curve_obj["_rka_touched"] = True
    return curve_obj


def _curb_asset_row_python(name, boundary_pts_radius, coll, asset_obj, spacing, rot_offset_deg,
                            include_endpoint, phase_offset, exclude_positions, exclude_radius):
    """The pre-2026-08 Python-computed-then-baked-to-point-cloud implementation, kept as
    `curb_asset_row`'s fallback for `include_endpoint`/`phase_offset`/`exclude_positions` -- see
    that function's own docstring for exactly why these three still use it instead of
    `GN_CurbAssetRow`. Builds a MESH point-cloud + `GN_Instance`/`GN_InstanceScaled` (the same
    primitive `instancer`/`instancer_scaled` always used), NOT a Curve object."""
    pts3 = [(p[0], p[1], p[2]) for p in boundary_pts_radius]
    piece_len = asset_row_length(asset_obj)
    if piece_len > 1e-4:
        spacing = piece_len * max(1, round(spacing / piece_len))
    samples = resample_polyline_even(pts3, spacing, include_endpoint=include_endpoint,
                                      phase_offset=phase_offset)
    if exclude_positions and exclude_radius > 0.0:
        samples = [(pos, hd) for pos, hd in samples
                   if all(math.dist(pos, ep) >= exclude_radius for ep in exclude_positions)]
    if not samples:
        return None
    coords = [pos for pos, _hd in samples]
    is_mirror = abs((rot_offset_deg % 360.0) - 180.0) < 1e-3
    if is_mirror:
        rots = [(0.0, 0.0, hd) for _pos, hd in samples]
        scls = [(1.0, -1.0, 1.0)] * len(samples)
        return instancer_scaled(name, coords, asset_obj, coll, rots, scls)
    rots = [(0.0, 0.0, hd + math.radians(rot_offset_deg)) for _pos, hd in samples]
    return instancer(name, coords, asset_obj, coll, rots=rots)


def colonly_swept(name, cpts, half_w, coll, z0=-0.4, z1=0.0, miter_limit=4.0):
    """A `<name>-colonly` swept solid slab following cpts=[(x,y,z[,...]), ...] with lateral
    half-width `half_w` (scalar OR per-point list), from z+z0 to z+z1. This is the drivable/solid
    COLLISION proxy a swept GN road/wall otherwise LACKS (importer drops the visual and builds a
    CollisionShape3D from the -colonly mesh). Low-poly by construction (one ring per densified
    point) so seg_len keeps the collider cheap while every segment still spans a vehicle.

    Per-vertex lateral direction is a proper MITER JOIN (2026-07-27, user-reported/screenshotted:
    a wedge of collision fanning out well past the curb at a sharp corner, well above the visual
    road) -- bisects the INCOMING edge's own left-normal and the OUTGOING edge's own left-normal,
    then scales the half-width by `1/cos(half the turn angle)` so both edges' offset lines still
    meet exactly at the corner (standard 2D polyline-offset miter join). The previous version used
    a single central-difference chord (`cpts[i+1] - cpts[i-1]`) for the normal at every point --
    fine for a gentle curve, but at a SHARP corner that chord doesn't match either adjacent edge's
    true perpendicular, so the swept quad connecting two corner rings could skew/twist into
    exactly this kind of overshooting wedge, while the visual mesh (Blender's native Curve-to-Mesh/
    Fillet Curve, which DOES miter corners correctly) stayed clean -- explaining why only the
    COLLISION looked wrong, never the road surface itself. `miter_limit` (default 4, i.e. never
    more than 4x half_w) caps the scale for a near-reversal turn (where the two edges point almost
    opposite ways and a true miter would run to infinity) -- falls back to the incoming edge's own
    plain perpendicular past that limit, a small square-ish notch instead of a spike, matching
    ordinary vector-graphics miter-limit behavior."""
    n = len(cpts)
    if n < 2:
        return None
    hw = list(half_w) if isinstance(half_w, (list, tuple)) else [half_w] * n
    verts, faces = [], []
    for i, p in enumerate(cpts):
        x, y, z = p[0], p[1], p[2]
        ein = eout = None
        if i > 0:
            ax, ay = p[0] - cpts[i - 1][0], p[1] - cpts[i - 1][1]
            La = math.hypot(ax, ay) or 1.0
            ein = (ax / La, ay / La)
        if i < n - 1:
            bx, by = cpts[i + 1][0] - p[0], cpts[i + 1][1] - p[1]
            Lb = math.hypot(bx, by) or 1.0
            eout = (bx / Lb, by / Lb)
        ein = ein or eout
        eout = eout or ein
        n1x, n1y = -ein[1], ein[0]      # incoming edge's own left normal
        n2x, n2y = -eout[1], eout[0]    # outgoing edge's own left normal
        mx, my = n1x + n2x, n1y + n2y
        mL = math.hypot(mx, my)
        if mL < 1e-6:
            nx, ny, scale = n1x, n1y, 1.0     # near-180 reversal -- no well-defined bisector
        else:
            nx, ny = mx / mL, my / mL
            cos_half = max(nx * n1x + ny * n1y, 1.0 / miter_limit)
            scale = 1.0 / cos_half
        w = hw[i] * scale
        verts += [(x - nx*w, y - ny*w, z+z0), (x + nx*w, y + ny*w, z+z0),
                  (x + nx*w, y + ny*w, z+z1), (x - nx*w, y - ny*w, z+z1)]
    for i in range(n - 1):
        a, b = i * 4, (i + 1) * 4
        faces += [(a+3, a+2, b+2, b+3),                 # top (drivable surface)
                  (a, b, b+1, a+1),                     # bottom
                  (a+1, b+1, b+2, a+2),                 # +normal side
                  (a, a+3, b+3, b)]                     # -normal side
    faces += [(0, 1, 2, 3), ((n-1)*4, (n-1)*4+3, (n-1)*4+2, (n-1)*4+1)]   # end caps
    me = bpy.data.meshes.new(name + "-colonly")
    me.from_pydata(verts, [], faces); me.update(); recalc_normals(me)
    # UPDATE-IN-PLACE (2026-08, the crash-surface fix): closes the legacy point-segment path's
    # last identity-crash surface (`ops_segment._populate_segment_mesh`'s own live pavement
    # collision bake, still called on every `rebuild_segment_in_place` tick). `proxy_for` already
    # matches `ops_intersection.clear_generated_mesh_objects`'s existing "-colonly" + proxy_for
    # sparing condition, so no change needed there -- reusing this object is enough.
    existing = coll.objects.get(name + "-colonly")
    if existing is not None and existing.type == 'MESH':
        existing.location = (0.0, 0.0, 0.0)
        existing.rotation_euler = (0.0, 0.0, 0.0)
        existing.scale = (1.0, 1.0, 1.0)
        old_data = existing.data
        existing.data = me
        if old_data is not None and old_data.users == 0:
            bpy.data.meshes.remove(old_data)
        existing["proxy_for"] = name
        existing["_rka_touched"] = True   # see ops_intersection.sweep_untouched_boundaries
        return existing
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)
    obj = bpy.data.objects.new(name + "-colonly", me)
    coll.objects.link(obj)
    obj.data.materials.append(mat("col"))
    obj["proxy_for"] = name
    obj["_rka_touched"] = True
    return obj


SHOULDER_MARGIN = 0.4   # m, extra collision-only half-width beyond the curb line -- see docstring
SEAM_OVERLAP = 0.5      # m, extra collision-only LENGTH past each end -- see docstring
# Both shrunk 2026-07-27 (from 2.0/1.5) once a much bigger contributor was found and fixed
# (VehicleAIController.cruiseSpeed 11->7 m/s cut departures from dozens starting at t=2.5s to two
# starting at t=41.5s on its own) -- the wide margin was then mostly just extra invisible collision
# floating past the visible curb with little safety benefit left to justify it (and was itself
# reported as visible: character/vehicle collision sitting perceptibly off the curb line). Kept
# small and nonzero rather than 0 -- still a bit of forgiveness for ordinary wheel/foot clipping,
# just not enough to read as a mismatch against the visual road.


def colonly_swept_between(name, left_pts, right_pts, coll, z0=-0.4, z1=0.0,
                           margin=SHOULDER_MARGIN, end_overlap=SEAM_OVERLAP):
    """A `<name>-colonly` swept solid slab for the PAVEMENT between two parallel offset lines
    (e.g. a segment/transition's own `curbs` field -- the exact `[left_pts, right_pts]` its visual
    ribbon/GN sweep already uses), built as `colonly_swept` from the pointwise midpoint centerline
    with pointwise half-width = half the left/right separation -- so it naturally follows a
    tapering width (a lane-count transition) with no extra per-caller math.

    This is the drivable-surface collision a segment/transition otherwise LACKS: only the curb
    EDGES got `colonly_swept` collision (a thin strip at each side), leaving the open lanes between
    them collision-free -- a vehicle driving the middle of the road fell straight through to
    whatever's below (ground/terrain, or nothing), landing well beneath the visual pavement while
    still correctly following its `PathLaneRoute` (a pure geometric path, unaffected by missing
    collision).

    `margin` (2026-07-27, user-reported "vehicle still on ground even at the beginning" -- traced
    via a per-vehicle raycast diagnostic to widespread, near-immediate collision departures, not a
    rare edge case): curb walls are only `curb_height` (~0.15m) tall -- trivially easy for a moving
    vehicle to hop over. `margin` extends the COLLISION ONLY (never the visual pavement/curb, which
    are built from the unmodified `left_pts`/`right_pts` elsewhere) a couple meters past each curb
    line, an invisible shoulder catching ordinary lateral steering drift. **Confirmed (via a
    per-tick position/velocity trace) NOT the dominant cause on its own** -- widening this alone
    left departures just as frequent, because most observed falls were a vehicle moving STEADILY
    FORWARD (accelerating out of a stop, not turning) that cleanly lost support and free-fell
    (velocity.y matching -9.8 m/s^2 exactly) while still advancing -- a LENGTHWISE gap, not a
    lateral one.

    `end_overlap` is the fix for that: each piece's collision slab starts/ends EXACTLY at its own
    p0/p1, meeting the NEXT piece's slab (built independently, from that piece's own p0/p1) at the
    same point -- geometrically touching, but two separate static colliders meeting at an EXACT
    shared boundary is exactly the classic "gap between tiles" scenario a fast-moving physics body
    can tunnel through in a single substep (Jolt, like most engines, doesn't guarantee catching a
    boundary-straddling contact between two disjoint meshes at speed). `end_overlap` extends the
    first/last centerline point backward/forward along its own local tangent by that many meters,
    so consecutive pieces' collision volumes OVERLAP instead of exactly touching, eliminating the
    seam a vehicle could otherwise slip through mid-crossing. Purely a safety net -- it does not
    change where a vehicle is intended to drive, only what happens if it strays slightly or crosses
    a seam at speed."""
    n = min(len(left_pts), len(right_pts))
    if n < 2:
        return None
    cpts, half_w = [], []
    for i in range(n):
        lx, ly, lz = left_pts[i][0], left_pts[i][1], left_pts[i][2]
        rx, ry, rz = right_pts[i][0], right_pts[i][1], right_pts[i][2]
        cpts.append(((lx + rx) / 2.0, (ly + ry) / 2.0, (lz + rz) / 2.0))
        half_w.append(math.hypot(rx - lx, ry - ly) / 2.0 + margin)
    if end_overlap > 0.0:
        def extend(p_from, p_to, dist):
            dx, dy, dz = p_to[0] - p_from[0], p_to[1] - p_from[1], p_to[2] - p_from[2]
            L = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            return (p_to[0] + dx / L * dist, p_to[1] + dy / L * dist, p_to[2] + dz / L * dist)
        # Compute both extensions from the ORIGINAL (pre-mutation) points -- for a 2-point
        # polyline cpts[-2] IS cpts[0], so mutating cpts[0] before computing the far end would
        # feed the far end's tangent an already-extended (wrong) reference point.
        new_first = extend(cpts[1], cpts[0], end_overlap)
        new_last = extend(cpts[-2], cpts[-1], end_overlap)
        cpts[0] = new_first
        cpts[-1] = new_last
    return colonly_swept(name, cpts, half_w, coll, z0=z0, z1=z1)


def colonly_polygon(name, pts_radius, coll, z0=-0.4, z1=0.0, margin=SHOULDER_MARGIN):
    """A `<name>-colonly` flat filled-polygon collision slab for an intersection PAD footprint
    (`junction_pad`'s own `boundary_pts_radius`, the same `[(x,y,z,radius), ...]` shape
    `_poly_curve_with_radius` takes) -- fan-triangulated from the centroid (safe: every boundary
    `intersection_kit.build_junction_boundary` produces is star-shaped from the junction's own
    center by construction, since it's built from arm angles radiating outward) and extruded from
    z+z0 to z+z1, mirroring `colonly_swept`'s low-poly-by-construction philosophy.

    Deliberately built from the RAW control points, ignoring each point's fillet `radius` (a
    slightly-squared-off coarse footprint instead of the exact filleted arc) -- `junction_pad`'s
    own object is a GN-modifier-backed Curve with no real mesh data until glTF export bakes the
    modifier, so there is nothing to read back at author time the way `colonly_mesh()` reads a
    real visual mesh; a coarse polygon is more than adequate for a collision proxy, same standard
    every other `-colonly` helper in this module already accepts (`colonly()`'s own box proxy is
    coarser still). Godot import contract matches every other `-colonly` proxy: visual dropped,
    `CollisionShape3D` built from this mesh.

    `margin` -- same collision-only shoulder as `colonly_swept_between` (see its docstring for the
    full rationale: 15cm curb walls are trivially hoppable, and a vehicle that clears one over an
    elevated road otherwise free-falls). Pushes each boundary point directly away from the pad's
    own centroid by `margin` meters before triangulating -- valid for this star-shaped boundary
    (every point already radiates outward from the center by construction), and naturally extends
    thin arm-tail stubs further along their own already-outward direction rather than sideways.

    `z1` default changed 0.05 -> 0.0 (2026-07-27, user-reported: characters/vehicles visibly
    floating above the road instead of standing on it) -- the collision top no longer sits above
    the actual pad height at all; nothing about the coarse-polygon-vs-fillet approximation needs
    a positive margin here specifically (that concern is about the fillet corners' XY shape, not
    Z), so there was no real tradeoff in removing it."""
    n = len(pts_radius)
    if n < 3:
        return None
    cx = sum(p[0] for p in pts_radius) / n
    cy = sum(p[1] for p in pts_radius) / n
    z_ref = pts_radius[0][2]

    def push(p):
        dx, dy = p[0] - cx, p[1] - cy
        d = math.hypot(dx, dy) or 1.0
        return (p[0] + dx / d * margin, p[1] + dy / d * margin, p[2])

    pts_radius = [push(p) for p in pts_radius]
    bot_c = 0
    bot = [1 + i for i in range(n)]                # bottom boundary verts
    top_c = 1 + n
    top = [2 + n + i for i in range(n)]             # top boundary verts
    verts = [(cx, cy, z_ref + z0)] + [(p[0], p[1], p[2] + z0) for p in pts_radius] \
          + [(cx, cy, z_ref + z1)] + [(p[0], p[1], p[2] + z1) for p in pts_radius]
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.append((bot_c, bot[j], bot[i]))                  # bottom fan, normal down
        faces.append((top_c, top[i], top[j]))                  # top fan, normal up
        faces.append((bot[i], bot[j], top[j], top[i]))         # outward-facing side quad
    me = bpy.data.meshes.new(name + "-colonly")
    me.from_pydata(verts, [], faces); me.update(); recalc_normals(me)
    obj = bpy.data.objects.new(name + "-colonly", me)
    coll.objects.link(obj)
    obj.data.materials.append(mat("col"))
    obj["proxy_for"] = name
    return obj


def ramp_section_sweep(name, cpts, coll, deck_t=0.4, wall_h=1.1, wall_t=0.22, gaps=None,
                       grip="asphalt", wall_mat="concrete", collide=True):
    """Sweep the COMBINED ramp cross-section — deck slab + BOTH parapet walls — along the densified
    centreline cpts=[(x,y,z,bank,half_w), ...] as ONE welded mesh. Road and wall are the SAME object
    so they can never gap: the deck spans ±(half_w+wall_t), the wall sits directly ON that deck edge,
    and the drivable lane is ±half_w at v=0. This is `SM_Ramp_Grade_Wall_7`'s profile made CONTINUOUS
    (swept, not arrayed as tiles that facet a tight loop) — the fix for both the tile-faceting AND the
    shy-line gap of separate road+barrier sweeps. `gaps`=[(t0,t1)] OPEN the parapets (merge landing /
    ramp foot) while the deck continues. Emits a matching `<name>-colonly` (deck+walls) when `collide`.
    Returns the visual object."""
    n = len(cpts)
    if n < 2:
        return None
    gaps = gaps or []
    in_gap = lambda t: any(g0 <= t <= g1 for (g0, g1) in gaps)
    ring = []
    for i, p in enumerate(cpts):
        a = cpts[max(0, i - 1)]; b = cpts[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        L = math.hypot(tx, ty) or 1.0
        ring.append((p[0], p[1], p[2], -ty / L, tx / L, p[4]))    # x, y, z, nx, ny, half_w
    verts, faces, fmat = [], [], []
    def tube(idx, uL, uR, vB, vT, mi):
        """Swept rectangular tube over the ring indices `idx` (contiguous run); uL/uR are lateral
        offset functions of the point's half_w, vB/vT the vertical span. Records material index mi."""
        m = len(idx)
        if m < 2:
            return
        base = len(verts)
        for i in idx:
            x, y, z, nx, ny, hw = ring[i]
            for (u, v) in ((uL(hw), vB), (uR(hw), vB), (uR(hw), vT), (uL(hw), vT)):
                verts.append((x + u*nx, y + u*ny, z + v))
        for k in range(m - 1):
            a, b = base + k*4, base + (k+1)*4
            for f in ((a+3, a+2, b+2, b+3), (a, b, b+1, a+1),
                      (a+1, b+1, b+2, a+2), (a, a+3, b+3, b)):
                faces.append(f); fmat.append(mi)
        e = base + (m-1)*4
        faces.append((base, base+1, base+2, base+3)); fmat.append(mi)
        faces.append((e, e+3, e+2, e+1)); fmat.append(mi)
    WW = wall_t
    tube(list(range(n)), lambda hw: -(hw+WW), lambda hw: hw+WW, -deck_t, 0.0, 0)   # deck (mat 0)
    run = []                                                                        # walls (mat 1)
    for i in range(n):
        if in_gap(i / (n - 1)):
            if len(run) >= 2:
                tube(run, lambda hw: -(hw+WW), lambda hw: -hw, 0.0, wall_h, 1)      # left parapet
                tube(run, lambda hw: hw, lambda hw: hw+WW, 0.0, wall_h, 1)          # right parapet
            run = []
        else:
            run.append(i)
    if len(run) >= 2:
        tube(run, lambda hw: -(hw+WW), lambda hw: -hw, 0.0, wall_h, 1)
        tube(run, lambda hw: hw, lambda hw: hw+WW, 0.0, wall_h, 1)
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces]); me.update()
    me.materials.append(mat(grip)); me.materials.append(mat(wall_mat))
    for f, mi in zip(me.polygons, fmat):
        f.material_index = mi
    recalc_normals(me)
    obj = bpy.data.objects.new(name, me)
    coll.objects.link(obj)
    if collide:
        cme = bpy.data.meshes.new(name + "-colonly")
        cme.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces]); cme.update()
        cme.materials.append(mat("col")); recalc_normals(cme)
        cobj = bpy.data.objects.new(name + "-colonly", cme)
        coll.objects.link(cobj)
        cobj["proxy_for"] = name
    return obj


def sample_polyline(pts, spacing):
    """Resample a 3D polyline pts=[(x,y,z), ...] at ~`spacing` m -> [(pos, heading_rad)].
    For dropping discrete edge pieces (barriers, lights, piers) along a curve at grade."""
    if len(pts) < 2:
        return []
    out, carry = [], 0.0
    for (a, b) in zip(pts, pts[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if seg < 1e-6:
            continue
        hd = math.atan2(b[1] - a[1], b[0] - a[0])
        d = carry
        while d < seg:
            t = d / seg
            out.append(((a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t, a[2] + (b[2]-a[2])*t), hd))
            d += spacing
        carry = d - seg
    out.append(((pts[-1][0], pts[-1][1], pts[-1][2]),
                math.atan2(pts[-1][1]-pts[-2][1], pts[-1][0]-pts[-2][0])))
    return out


def new_root(name, coll, loc, rot_z):
    root = bpy.data.objects.new(name, None)
    coll.objects.link(root)
    root.location = loc
    root.rotation_euler = (0, 0, math.radians(rot_z))
    root.empty_display_size = 0.4
    return root


def place_side(root, coll, tag, edge_loc, rot_z, nbays, floors, h, piece_for, z0=0.0,
               batch=None, base_loc=(0, 0, 0), base_rot=0.0):
    """Lay a wall grid (nbays x floors) along an edge, grouped by piece for fewer
    instancers. piece_for(floor, bay, nbays) -> piece name or None.
    If `batch` (a Batch) is given, accumulate world-space instances into it instead of
    creating per-call instancer objects — base_loc/base_rot are the building root's
    world loc/Z-rotation (deg), edge_loc/rot_z this side's own loc/rot (deg)."""
    groups = {}
    for j in range(floors):
        for i in range(nbays):
            pn = piece_for(j, i, nbays)
            if pn:
                groups.setdefault(pn, []).append((i * R_BAY, 0, z0 + j * h))
    if batch is not None:
        for pn, pts in groups.items():
            batch.add(pn, base_loc, base_rot, edge_loc, rot_z, pts)
        return
    for pn, pts in groups.items():
        instancer(f"{tag}_{pn.split('_')[-1]}", pts, pn, coll,
                  loc=edge_loc, rot_z=rot_z, parent=root)


def _rotz2(x, y, deg):
    """Rotate (x, y) about Z by `deg` degrees."""
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return (c * x - s * y, s * x + c * y)


class Batch:
    """Consolidates many buildings' GN instances into ONE point cloud per piece — the
    lay_roads pattern applied to buildings. Each instance is baked into world space with
    a per-point Z rotation, so a whole streetwall flushes to a few dozen instancer
    objects instead of thousands of per-building point clouds + modifier stacks.

    The world transform reproduces the old object hierarchy exactly:
        world = T(base_loc) . Rz(base_rot) . T(edge_loc) . Rz(edge_rot) . p
    and the instance's Z rotation = base_rot + edge_rot (both rotations are about Z)."""

    def __init__(self):
        self.groups = {}                  # piece_name -> ([points], [rots])

    def add(self, piece, base_loc, base_rot, edge_loc, edge_rot, local_pts):
        rz = (0.0, 0.0, math.radians(base_rot + edge_rot))
        pts, rots = self.groups.setdefault(piece, ([], []))
        bx, by, bz = base_loc
        ex, ey, ez = edge_loc
        for (px, py, pz) in local_pts:
            qx, qy = _rotz2(px, py, edge_rot)         # edge frame
            wx, wy = _rotz2(qx + ex, qy + ey, base_rot)  # root frame
            pts.append((wx + bx, wy + by, pz + ez + bz))
            rots.append(rz)

    def flush(self, coll, tag="Front"):
        """Emit one instancer per accumulated piece, then clear. Returns the objects."""
        objs = [instancer(f"{tag}_{piece}", pts, piece, coll, rots=rots)
                for piece, (pts, rots) in self.groups.items()]
        self.groups = {}
        return objs


def append_kit(here, blendname, coll_name):
    """Append a collection (with its objects) from <here>/<blendname> into the scene.
    Idempotent: if the kit collection is already present (e.g. regen reopened the
    file), do nothing rather than create a .001 duplicate."""
    if bpy.data.collections.get(coll_name):
        return
    path = os.path.join(here, blendname)
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        if coll_name in src.collections:
            dst.collections = [coll_name]
    for col in dst.collections:
        if col is not None and col.name not in {c.name for c in
                                                bpy.context.scene.collection.children}:
            bpy.context.scene.collection.children.link(col)


def place_landmark(coll, blend_path, collection_name, loc):
    """Append a hand-modeled building-tier asset (its own top-level collection, e.g.
    PLATEAU_TokyoTower.blend's "TokyoTower" collection) and place its objects at `loc` (world/
    local coordinates in the CALLER's own frame -- a district piece's post-recenter local origin,
    or build_world.py's grid-space-then-to_world() frame; this helper only translates by `loc`,
    it doesn't know or care which frame that is). Distinct from `append_kit` (which links a shared
    kit-source collection once and leaves it hidden for GN instancing) -- a landmark is placed
    directly as real geometry, once, no reuse. Shared by build_district.py (precinct landmarks)
    and build_world.py (harbor landmarks) -- the one place this append-and-place pattern lives."""
    with bpy.data.libraries.load(blend_path, link=False) as (src, dst):
        if collection_name in src.collections:
            dst.collections = [collection_name]
    for landmark_coll in dst.collections:
        if landmark_coll is None:
            continue
        for obj in list(landmark_coll.objects):
            coll.objects.link(obj)
            if obj.parent is None:
                obj.location.x += loc[0]
                obj.location.y += loc[1]
                obj.location.z += loc[2]
    # the appended collection itself (now empty of useful content at the top level) isn't needed
    for c in list(dst.collections):
        if c is not None and c.name in bpy.data.collections and not c.objects:
            bpy.data.collections.remove(c)


def load_kits(here, kits=(("roads_kit.blend", "ROADS"), ("walls_kit.blend", "WALLS"),
                          ("props_kit.blend", "PROPS"), ("townextra_kit.blend", "EXTRAS"))):
    """Append every kit collection so all SOURCE pieces are available by name."""
    for blendname, coll in kits:
        append_kit(here, blendname, coll)
    return [coll for _, coll in kits]


def hide_sources(coll_names):
    """Hide kit SOURCE objects so they stay evaluated (for GN) but out of render."""
    for cn in coll_names:
        c = bpy.data.collections.get(cn)
        if not c:
            continue
        for o in c.objects:
            o.hide_render = True
            try:
                o.hide_set(True)
            except Exception:
                pass

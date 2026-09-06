#!/usr/bin/env python3
"""
export_world.py — export the master world-layout .blend into res:// as glTF for the baker.

Unlike export_kit.py (per-leaf .glb library), this exports the WHOLE open scene — every
district plate, arterial ribbon, harbor blockout, and (crucially) every named marker empty
with its Custom Properties as glTF `extras` — so the Java WorldBaker can turn region_/lane_/
intersection_/water_/slot_ nodes into gameplay nodes (BLENDER_CONVENTIONS.md I6a).

The one flag that matters vs. the kit export: `export_extras=True` (marker params ride along)
and empties are kept as nodes. Cameras/lights are dropped (preview-only).

RUN (with the master .blend open):
  blender --background world_master.blend --python tools/export_world.py
Then bake it: point BakeWorldMaster.tscn (or BakeWorld.tscn) at the emitted glTF and run it
(headless CLI / editor F6 / DebugHarness F5) — see BLENDER_CONVENTIONS "Three ways to trigger".

Also reused by tools/build_piece.sh for a per-district PIECE export (not just the master) —
optionally scoped to ONE top-level content collection via `--only <CollName>`, e.g.
`--only STREET_LOD_LOW` exports JUST the low-detail placeholder tier (see lib/lod_low.py) as
its own standalone glTF/`.tscn`, dropping STREET/MARKERS/MANUAL entirely so the two LOD tiers
bake to two independent scenes a runtime LOD switch can pick between
(WorldZoneMarker.instantiateLodLow/removeLodLow) instead of one merged scene:
  blender --background District_X.blend --python tools/export_world.py -- --only STREET_LOD_LOW out.gltf
"""
import bpy, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # blender
sys.path.insert(0, os.path.join(ROOT, "lib"))       # kit_common.bake_colonly_proxies
import kit_common as kc
# res:// world dir the baker reads from.
PROJECT = os.path.dirname(ROOT)                    # repo root (…/third-person-shooter)
OUT_DIR = os.path.join(PROJECT, "src", "main", "resources", "com", "openworld", "world")
OUT = os.path.join(OUT_DIR, "master", "World_master.gltf")

def _local_coll(name):
    """Local (non-library) collection by name — a district .blend with neighbours linked in
    (tools/link_neighbors.py) holds several same-named linked STREET collections, and a bare
    bpy.data.collections.get() may return one of those instead of the piece's own."""
    return next((c for c in bpy.data.collections
                 if c.name == name and c.library is None), None)


argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
only_coll = None
if argv and argv[0] == "--only":
    only_coll = argv[1]
    argv = argv[2:]
if argv:
    OUT = argv[0]
os.makedirs(os.path.dirname(OUT), exist_ok=True)

# Scoped export: keep ONLY `only_coll`'s objects among the piece's content collections (kit
# SOURCE collections are untouched here — the existing drop-sources pass below removes them
# regardless of scope). Runs on a throwaway load, same as the rest of this script.
if only_coll:
    _dropped_scope = 0
    for cname in ("STREET", "STREET_LOD_LOW", "MARKERS", "MANUAL"):
        if cname == only_coll:
            continue
        c = _local_coll(cname)
        if not c:
            continue
        for o in list(c.objects):
            bpy.data.objects.remove(o, do_unlink=True)
            _dropped_scope += 1
    print("--only %s: dropped %d objects from other content collections" % (only_coll, _dropped_scope))
    kept = _local_coll(only_coll)
    if not kept or not kept.objects:
        print("--only %s: collection missing or empty -- nothing to export" % only_coll)
        sys.exit(3)

# Realize Geometry-Nodes instances into real mesh BEFORE export. Blender's glTF exporter does NOT
# export bare GN 'Instance on Points' instances — they collapse to the source at origin — so every
# kc.instancer / kc.Batch layer (fill_frontage streetwall, road/sidewalk tiling, trees, poles) would
# pile up at center. Converting each GN-modified object to mesh bakes the placement into real,
# positioned geometry. (Runs on a throwaway load — the .blend keeps its GN; only the export is baked.)
_gn = [o for o in bpy.context.scene.objects
       if o.type == 'MESH' and any(m.type == 'NODES' for m in o.modifiers)]
if _gn:
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in _gn:
        o.hide_set(False)
        o.hide_viewport = False
        o.select_set(True)
    bpy.context.view_layer.objects.active = _gn[0]
    bpy.ops.object.convert(target='MESH')     # applies GN + realizes instances into real mesh
    print("realized %d GN-instanced layers into mesh for export" % len(_gn))

# Bake road_kit_authoring's collision proxies (-colonly) for every pad_/curb_/spine_ GN boundary
# object. 2026-08: this used to be baked LIVE in Blender during authoring/rebuild -- moved here
# because a -colonly proxy is invisible/has zero authoring-time value while being the single
# most expensive+crash-prone live rebuild operation (a to_mesh() depsgraph bake); same exact
# bake (kit_common.colonly_mesh_evaluated, unchanged), just deferred to when it's actually
# needed. Runs BEFORE export, same throwaway-load convention as the GN-realize step above --
# these proxies never touch the source .blend, they only exist for this export.
_colonly = kc.bake_colonly_proxies(bpy.context.scene.objects, bpy.context.scene.collection)
if _colonly:
    print("baked %d road_kit_authoring -colonly collision proxies for export" % len(_colonly))

# Drop kit SOURCE objects before export. load_kits appends every kit piece (SM_*, Road_*, Deco_*, +
# their -colonly proxies) at ORIGIN; hide_sources only hides them from RENDER, but the exporter takes
# the whole scene regardless — so they baked as a redundant geometry+collision PILE at the district
# centre that blocks movement. They're not needed in the export: mmesh visuals load from the res://
# kit glbs and towers are already realized above. Remove them from this throwaway load (the .blend is
# untouched). NOTE: this runs AFTER the GN realize so tower geometry (which referenced these sources)
# is already baked to mesh. LANDMARK_PREVIEW (tools/link_landmark_preview.py) is dropped for the same
# reason — a real district's STREET content linked in purely so opening world_master.blend shows it
# beside the harbor/ring; it must never reach the baked master (that district already streams in on
# its own via the normal region_ zone mechanism — this would double it up). LAYOUT (build_world.py's
# linked-district Piece_* Collection-Instances — every built piece library-linked at its world
# position — plus fallback Plate_* boxes for unbuilt districts) and HARBOR (its harbor/Haneda/bridge
# blockout boxes) are the master's own preview layer: each district already streams in on its own at
# runtime, so exporting the linked instances would double every piece (the glTF exporter expands
# Collection-Instances into real nodes) — every gameplay marker built alongside (region_/lane_/
# intersection_/slot_) already lands in MARKERS/LANDMARKS instead, so dropping these two collections
# loses no gameplay data. Left in, they baked hundreds of raw MeshInstance3D/StaticBody3D/
# ConcavePolygonShape3D nodes straight into World_master.tscn (never collapsed to MultiMesh — that
# only applies to `mmesh_`-tagged markers) — real geometry with no runtime purpose, heavy enough to
# make the Godot editor slow to open/render the baked master scene.
_dropped = 0
# NEIGHBOR_REF = tools/link_neighbors.py's linked neighbour-district/master references (the
# in-context seam-editing aid) — read-only Blender-side context that must never reach the game
# (each neighbour already streams in on its own; exporting it would double the geometry), same
# rationale as LANDMARK_PREVIEW on the master.
for cname in ("ROADS", "WALLS", "PROPS", "EXTRAS", "HIGHRISE", "INFRA", "LANDMARK_PREVIEW",
              "LAYOUT", "HARBOR", "ROADS_SRC", "NEIGHBOR_REF"):
    c = _local_coll(cname)
    if c:
        for o in list(c.objects):
            bpy.data.objects.remove(o, do_unlink=True)
            _dropped += 1
if _dropped:
    print("dropped %d kit source objects (kept out of the export)" % _dropped)

# ------------------------------------------------------------------ the ground cutters
# THERE ARE NONE ANY MORE, and this note is what is left of them.
#
# `point_build.cut_ground` used to build one solid per road/pad and hand it to the terrain as a
# BOOLEAN target. They were scene objects in `ROAD_MANAGER_GEN/CUTTERS`, and the glTF exporter
# takes the whole scene -- so all 32 baked into the game as raw white boxes straddling every road
# ("one white ground cover the main pave segment, like an open box over the road"). Unseen for as
# long as `bmesh.ops.solidify` was silently producing zero-thickness sheets at z = -40 (`W22`);
# giving them real volume made them real geometry in Godot. Removing the objects was NOT the fix,
# because the boolean would then lose its target and the terrain would export uncut, so they were
# UNLINKED from every collection instead -- the modifier's own reference keeping them alive while
# the exporter, which walks the scene rather than `bpy.data`, no longer saw them.
#
# The boolean cut itself was removed on 2026-09-06 (the road now deforms the heightfield instead --
# `island_v3_terrain.Carve`), so there is no cutter to hide and this workaround went with it. Left
# as a comment because "a modifier input is not content" is a rule the exporter will meet again the
# next time anything hands the scene a helper object.

# view-layer objects only: bpy.data.objects also holds library-linked datablocks (neighbour
# refs), and select_set raises on an object that is not in the view layer. Snapshot the list and
# skip None entries: the removals above leave stale None slots in view_layer.objects until the
# next depsgraph update (Blender 5.x), and select_set on that None crashed the whole export.
for o in list(bpy.context.view_layer.objects):
    if o is not None:
        o.select_set(False)

# ------------------------------------------------------------------ material flattening
# A PROCEDURAL BASE COLOUR CANNOT CROSS glTF, AND IT LEAVES NO TRACE WHEN IT FAILS.
#
# glTF carries a base colour as either a CONSTANT factor or an IMAGE texture. Blender's exporter
# reads the Principled BSDF's Base Color: unlinked, it writes the constant; linked to an image, it
# writes the texture; linked to anything else -- a Checker, a Noise, a Mix -- it writes **nothing**,
# and Godot renders the surface pure white with no warning on either side.
#
# `kit_common.get_tiled_mat` builds exactly that shape, and for a good reason (a world-position
# checker survives a curved corner without the UV pinch a tangent-frame pattern gets, which is what
# a footway wrapping a junction fillet needs). So `M_ConcreteTile` -- the PAVEMENT, the most visible
# surface in the world after the asphalt -- has been white in-game since it was introduced, while
# looking right in every Blender render.
#
# The authoring intent belongs in Blender and the constraint belongs to the seam, so the fix lives
# here: just before export, any Base Color driven by a non-image node is temporarily replaced by a
# representative constant, and restored afterwards. A Checker's constant is the mean of its two
# colours; anything else falls back to the material's own viewport `diffuse_color`, which is what a
# human already picked as "what this material looks like".
_flattened = []


def _flatten_base_colours():
    for m in bpy.data.materials:
        if not m.use_nodes or not m.node_tree:
            continue
        for n in m.node_tree.nodes:
            if n.type != 'BSDF_PRINCIPLED':
                continue
            sock = n.inputs.get("Base Color")
            if sock is None or not sock.is_linked:
                continue
            src = sock.links[0].from_node
            if src.type in ('TEX_IMAGE',):
                continue                      # an image DOES cross; leave it alone
            if src.type == 'TEX_CHECKER':
                c1 = tuple(src.inputs["Color1"].default_value)
                c2 = tuple(src.inputs["Color2"].default_value)
                col = tuple((a + b) * 0.5 for a, b in zip(c1, c2))
            else:
                col = tuple(m.diffuse_color)
            _flattened.append((m, [l for l in sock.links], tuple(sock.default_value)))
            for l in list(sock.links):
                m.node_tree.links.remove(l)
            sock.default_value = col


def _restore_base_colours():
    for m, links, prev in _flattened:
        for n in m.node_tree.nodes:
            if n.type != 'BSDF_PRINCIPLED':
                continue
            sock = n.inputs.get("Base Color")
            if sock is None:
                continue
            sock.default_value = prev
            # The link objects themselves are gone; re-make them from the checker that is still
            # in the tree. Export runs in a throwaway process, so this is belt-and-braces.
            for src in m.node_tree.nodes:
                if src.type == 'TEX_CHECKER':
                    m.node_tree.links.new(src.outputs["Color"], sock)
                    break
    _flattened.clear()


_flatten_base_colours()
if _flattened:
    print("flattened %d procedural base colour(s) for export: %s"
          % (len(_flattened), ", ".join(sorted({m.name for m, _l, _p in _flattened}))))

bpy.ops.export_scene.gltf(
    filepath=OUT,
    export_format='GLTF_SEPARATE',   # text .gltf + .bin (inspectable; matches WorldExample.gltf)
    use_selection=False,             # whole scene
    export_apply=True,
    export_extras=True,              # Custom Properties -> glTF extras -> node metadata (the marker params)
    export_cameras=False,
    export_lights=False,
    export_yup=True,                 # Blender Z-up -> glTF Y-up (Godot importer expects this)
)
_restore_base_colours()
print("EXPORTED world master ->", OUT)

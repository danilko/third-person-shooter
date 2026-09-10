"""Export the character rig + animations to the .glb the game actually loads.

    blender -b assets/merged_animation.blend --python blender/tools/export_character.py

`assets/merged_animation.tscn` instances `assets/merged_animation.glb`, NOT the .blend — so a
.blend edit is invisible to the game until this runs. Settings below are the ones the shipped
.glb was produced with; changing them changes every clip at once, so change them deliberately.

Two naming rules the pipeline depends on (see CLAUDE.md "Animation"):
  * the glTF animation name is the ACTION name, and Godot's importer strips a trailing `-loop`
    (that suffix is what sets loop mode) — action `crouch_idle-loop` -> clip `crouch_idle`;
  * an action that is both the object's ACTIVE action and stashed exports twice, merged into one
    animation with doubled channels. Keep one named NLA track per clip and no active action;
    `check_character_anim.py` fails the build if that regresses.
"""
import bpy
import os
import sys

OUT = os.path.join(os.path.dirname(bpy.data.filepath), "merged_animation.glb")

arm = bpy.data.objects.get("Godot_Chan_Stealth")
if arm is not None and arm.animation_data is not None and arm.animation_data.action is not None:
    print(f"[export_character] clearing active action {arm.animation_data.action.name!r} "
          f"(it would export a second time)")
    arm.animation_data.action = None

bpy.ops.export_scene.gltf(
    filepath=OUT,
    export_format='GLB',
    use_visible=True,          # keeps the scratch `Icosphere` out of the export
    export_yup=True,
    export_apply=True,
    export_texcoords=True,
    export_normals=True,
    export_tangents=True,
    export_vertex_color='NONE',
    export_materials='EXPORT',
    export_extras=True,
    export_cameras=True,
    export_lights=True,
    export_skins=True,
    export_all_influences=True,
    export_def_bones=False,
    export_animations=True,
    export_animation_mode='ACTIONS',
    export_force_sampling=True,
    export_bake_animation=True,
    export_optimize_animation_size=False,
    export_anim_slide_to_zero=False,
    export_negative_frame='SLIDE',
)
print(f"[export_character] wrote {OUT} ({os.path.getsize(OUT)} bytes)")
print("[export_character] now run BOTH, in this order:")
print("  1. godot --headless --path . --import      <-- NOT OPTIONAL after a rename")
print("  2. python3 blender/tools/check_character_anim.py")
print("[export_character] Godot does NOT rescan imports on a `--headless --script` run, so without")
print("[export_character] step 1 every headless gate measures the PREVIOUS export: the AnimationTree")
print("[export_character] asks for the new clip names, the cached library still holds the old ones,")
print("[export_character] nothing resolves and the skeleton falls back to its REST pose. That reads")
print("[export_character] as a plausible failure (crawl's chest 129 deg -> 44) rather than as a stale")
print("[export_character] cache, and check_character_anim.py cannot see it -- it reads the .glb.")

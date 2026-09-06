#!/usr/bin/env bash
# build_intersection_piece.sh [lanekit-json] — bake the intersection-prototype demo INTO Godot so
# it can be opened/walk-tested in the editor (SoloIntersection.tscn), mirroring build_piece.sh's
# stem-form ("bake an existing .blend as-is, don't regenerate it") for the intersection prototype.
#
# Deliberately does NOT invoke Blender or re-run tools/build_intersection_prototype.py -- that
# script rebuilds intersection_prototype.blend from a factory-empty scene and would WIPE any
# manual edits (F9 redo-panel tweaks, hand-authored Lane Map Override, etc.) you made in the
# Blender GUI. Instead this assumes you already exported fresh geometry + data straight from
# Blender: open kit/intersection_prototype.blend, use the "Intersection (prototype)" panel's
# "Build Intersection" (or F9 redo on the last run) with BOTH "Export .glb" and "Export
# .lanekit.json" pointed at the same pair this script expects by default:
#   Export .glb          -> src/main/resources/com/openworld/world/pieces/District_intersectiondemo.glb
#   Export .lanekit.json -> assets/world_source/kit/intersection_prototype.4way.lanekit.json
# (matching what tools/build_intersection_prototype.py's first-time run already writes) -- then
# run this script to get it into Godot.
#
#   tools/build_intersection_piece.sh                                   # use the defaults above
#   tools/build_intersection_piece.sh kit/intersection_prototype.3way_t.lanekit.json
#
# then: <godot-jvm> --path <repo> res://src/main/resources/com/openworld/world/hosts/SoloIntersection.tscn
set -euo pipefail

BP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"           # blender
REPO="$(cd "$BP/.." && pwd)"                                    # repo root
WORLD_SOURCE="$REPO/assets/world_source"                        # data root
source "$BP/tools/env.sh"

RES_DIR="src/main/resources/com/openworld/world/pieces"      # relative to res://
GLTF_REL="$RES_DIR/District_intersectiondemo.glb"
TSCN_REL="$RES_DIR/District_intersectiondemo.tscn"
GLTF_ABS="$REPO/$GLTF_REL"
[ -f "$GLTF_ABS" ] || { echo "ERROR: $GLTF_ABS not found -- export it first (see header comment)"; exit 1; }

LANEKIT_ABS="${1:-$WORLD_SOURCE/kit/intersection_prototype.4way.lanekit.json}"
[[ "$LANEKIT_ABS" = /* ]] || LANEKIT_ABS="$REPO/$LANEKIT_ABS"
[ -f "$LANEKIT_ABS" ] || { echo "ERROR: $LANEKIT_ABS not found -- export it first (see header comment)"; exit 1; }

CLEANUP_FILES=()
trap 'rm -f "${CLEANUP_FILES[@]}" 2>/dev/null || true' EXIT

echo "── 1/2 import + bake -> res://$TSCN_REL"
"$GODOT" --headless --path "$REPO" --import >/dev/null 2>&1 || true

BAKE_TSCN="$REPO/$RES_DIR/_bake_intersection_$$.tscn"
CLEANUP_FILES+=("$BAKE_TSCN" "$BAKE_TSCN.import")
cat > "$BAKE_TSCN" <<EOF
[gd_scene format=3 uid="uid://bintersectionbake${RANDOM}"]
[ext_resource type="Script" path="res://src/main/java/com/openworld/world/WorldBaker.java" id="1"]
[node name="BakeIntersection" type="Node" unique_id=900002${RANDOM}]
script = ExtResource("1")
source_scene_path = "res://$GLTF_REL"
output_scene_path = "res://$TSCN_REL"
lanekit_path = "$LANEKIT_ABS"
bake_on_ready = true
quit_when_done = true
EOF
# --headless is fine here (unlike build_piece.sh's bake_one): this prototype has no MultiMesh
# content, so there's no RenderingServer transform-buffer dependency to work around.
"$GODOT" --headless --path "$REPO" "res://$RES_DIR/$(basename "$BAKE_TSCN")" 2>&1 \
    | grep -iE "WorldBaker: baked|WorldBaker: lanekit" | grep -viE "OCIO" || true

echo "── 2/2 point SoloIntersection.tscn at $TSCN_REL"
SOLO="$REPO/src/main/resources/com/openworld/world/hosts/SoloIntersection.tscn"
if [ -f "$SOLO" ]; then
  python3 - "$SOLO" "res://$TSCN_REL" <<'PY'
import re, sys
p, path = sys.argv[1], sys.argv[2]
s = open(p).read()
s = re.sub(r'(\[ext_resource type="PackedScene" path=")[^"]*(" id="piece"\])',
           lambda m: m.group(1) + path + m.group(2), s)
open(p, 'w').write(s)
PY
fi

echo
echo "DONE. Walk-test it:"
echo "  $GODOT --path \"$REPO\" res://src/main/resources/com/openworld/world/hosts/SoloIntersection.tscn"

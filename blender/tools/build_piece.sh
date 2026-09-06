#!/usr/bin/env bash
# build_piece.sh <stem> — the ONE-COMMAND district-piece iteration loop (see BLENDER_CONVENTIONS
# "district piece"). BAKE-ONLY: exports the district's EXISTING .blend to res:// as glTF, bakes
# it to a native .tscn via WorldBaker, and points SoloPiece.tscn at it so you can immediately
# walk-test it. Every district is hand-authored/hand-edited directly in Blender now (no
# generator to (re)run) — this script never creates or regenerates a district's content, only
# exports+bakes whatever is currently saved in its .blend.
#
#   tools/build_piece.sh Piece_1_1
#   then: <godot-jvm> --path <repo> res://src/main/resources/com/openworld/world/hosts/SoloPiece.tscn
#
# The master never needs re-baking: its zones' geometry_path already point at these predictable
# res://…/world/pieces/District_<Name>.tscn files (resolved lazily at stream time).
#
# A district built with the (now-removed) procedural filler generator may still carry a
# STREET_LOD_LOW collection from when it was created — if so this ALSO bakes a second,
# independent District_<Name>_LOD_LOW.tscn, wired at the predictable path
# world_grid.lod_low_piece_path() computes (WorldZoneManager streams it in whenever the
# full-detail piece is unloaded). Detected by whether STREET_LOD_LOW actually has content at
# export time (export_world.py's own "nothing to export" skip) — nothing to regenerate it, this
# is just baking whatever LOD_LOW content already exists in the .blend, same as everything else.
set -euo pipefail

# Every throwaway bake-host .tscn/.import (and export log) bake_one() creates gets registered
# here, so an interrupted or failed run (Ctrl-C, a Blender/Godot crash) still cleans up on exit
# instead of leaving a stray _bake_*.tscn behind in districts/ — the per-call `rm -f` below only
# covers the success path.
CLEANUP_FILES=()
trap 'rm -f "${CLEANUP_FILES[@]}" 2>/dev/null || true' EXIT

NAME="${1:?usage: build_piece.sh <piece-id>   (any assets/world_source/pieces/<piece-id>.blend -- bakes the existing .blend, nothing regenerates it)}"
BP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"           # blender
REPO="$(cd "$BP/.." && pwd)"                                    # repo root
WORLD_SOURCE="$REPO/assets/world_source"                        # data root
source "$BP/tools/env.sh"
# NOT --headless for bake/convert runs: MultiMesh data routes through the RenderingServer, and
# the headless dummy RS drops the transform buffers (instances collapse to origin).
RUN=("$GODOT")
command -v xvfb-run >/dev/null 2>&1 && RUN=(xvfb-run -a "$GODOT")
RES_DIR="src/main/resources/com/openworld/world/pieces"     # relative to res://
ABS_DIR="$REPO/$RES_DIR"
mkdir -p "$ABS_DIR"

STEM="$NAME"
BLEND="$WORLD_SOURCE/pieces/$STEM.blend"
[ -f "$BLEND" ] || { echo "ERROR: $BLEND does not exist"; exit 1; }
echo "── 1/4 bake-only — using existing $BLEND"
# attempt the LOD_LOW bake and let export_world.py's "nothing to export" skip it cleanly for
# any district that has no STREET_LOD_LOW content.
HAS_LOD_LOW=true

# bake_one <gltf-export-args...> <gltf-relpath> <tscn-relpath> — export (with the given extra
# export_world.py args) then bake via a throwaway WorldBaker host scene. Reads the outer-scope
# LANEKIT_PATH (set by the caller, only for the full-detail bake — see below) as an absolute
# filesystem path to a road_kit_authoring combined sidecar (tools/save_lane_kit.py,
# road_blender_godot.md P6.6); empty/unset means "no lanekit_path property at all" — byte-identical
# throwaway host scene to before this existed for every piece that has no sidecar yet.
bake_one() {
  local gltf_rel="$1" tscn_rel="$2"; shift 2
  echo "   export -> res://$gltf_rel"
  local export_log="/tmp/export_$$.log"
  CLEANUP_FILES+=("$export_log")
  # --python-exit-code 1: without it Blender exits 0 even when the export script dies on an
  # unhandled exception, and the bake below silently rebakes a STALE .gltf from the previous run.
  if ! $BLENDER --background "$BLEND" --python-exit-code 1 --python "$BP/tools/export_world.py" -- "$@" "$ABS_DIR/$(basename "$gltf_rel")" >"$export_log" 2>&1; then
    if grep -q "nothing to export" "$export_log"; then
      echo "   (skipped — collection missing/empty)"; return 1
    fi
    cat "$export_log"; return 1
  fi
  $GODOT --headless --path "$REPO" --import >/dev/null 2>&1 || true
  local bake_tscn="$REPO/$RES_DIR/_bake_$$_${RANDOM}.tscn"
  CLEANUP_FILES+=("$bake_tscn" "$bake_tscn.import")
  [[ -n "${LANEKIT_PATH:-}" ]] && echo "   lanekit -> $LANEKIT_PATH"
  {
    cat <<EOF
[gd_scene format=3 uid="uid://bpiecebake${RANDOM}"]
[ext_resource type="Script" path="res://src/main/java/com/openworld/world/WorldBaker.java" id="1"]
[node name="BakePiece" type="Node" unique_id=900000${RANDOM}]
script = ExtResource("1")
source_scene_path = "res://$gltf_rel"
output_scene_path = "res://$tscn_rel"
EOF
    if [[ -n "${LANEKIT_PATH:-}" ]]; then
      printf 'lanekit_path = "%s"\n' "$LANEKIT_PATH"
    fi
    cat <<EOF
bake_on_ready = true
quit_when_done = true
EOF
  } > "$bake_tscn"
  "${RUN[@]}" --path "$REPO" "res://$RES_DIR/$(basename "$bake_tscn")" 2>&1 \
      | grep -iE "WorldBaker: baked" | grep -viE "OCIO" || true
  rm -f "$bake_tscn" "$bake_tscn.import" 2>/dev/null || true
}

# bake_nav <tscn-relpath> — bake a NavigationRegion3D into an already-baked district .tscn (from
# ITS OWN collision geometry) via a throwaway NavBaker host scene, same shape as bake_one() above.
# STATIC_COLLIDERS parsing only reads PhysicsServer3D data (see NavBaker.java), so unlike
# bake_one()'s MultiMesh step this runs --headless — no xvfb dependency, no RenderingServer needed.
# NAV_HALF (env, optional): half-extent of the navmesh clip box in metres. Unset = NavBaker's own
# 252 m district default. The island rebuild's BASE piece is the whole 1512 m world in one object,
# and clipped to a district it got a navmesh over its middle 503 m and nothing else:
#   NAV_HALF=756 blender/tools/build_piece.sh Island_base
bake_nav() {
  local tscn_rel="$1"
  echo "   navmesh -> res://$tscn_rel${NAV_HALF:+  (clip half-extent ${NAV_HALF} m)}"
  local nav_tscn="$REPO/$RES_DIR/_navbake_$$_${RANDOM}.tscn"
  CLEANUP_FILES+=("$nav_tscn" "$nav_tscn.import")
  {
    cat <<EOF
[gd_scene format=3 uid="uid://bnavbake${RANDOM}"]
[ext_resource type="Script" path="res://src/main/java/com/openworld/world/NavBaker.java" id="1"]
[node name="BakeNav" type="Node" unique_id=900001${RANDOM}]
script = ExtResource("1")
scene_path = "res://$tscn_rel"
EOF
    # WRITTEN AS A FLOAT, always. `clip_half_extent = 756` parses as an INT and the JVM binding
    # then leaves the float property at its default -- the bake reported the district clip back
    # with no error at all, which is the worst way for a number not to arrive.
    [[ -n "${NAV_HALF:-}" ]] && printf 'clip_half_extent = %.1f\n' "$NAV_HALF"
    cat <<EOF
bake_on_ready = true
quit_when_done = true
EOF
  } > "$nav_tscn"
  $GODOT --headless --path "$REPO" "res://$RES_DIR/$(basename "$nav_tscn")" 2>&1 \
      | grep -iE "NavBaker: baked" || true
  rm -f "$nav_tscn" "$nav_tscn.import" 2>/dev/null || true
}

# road_kit_authoring combined traffic sidecar (tools/save_lane_kit.py) — only wired into the
# FULL-DETAIL bake (LOD_LOW is a distant visual-only placeholder, no traffic lanes needed there).
LANEKIT_PATH=""
if [[ -f "$WORLD_SOURCE/pieces/$STEM.lanekit.json" ]]; then
  LANEKIT_PATH="$WORLD_SOURCE/pieces/$STEM.lanekit.json"
fi

echo "── 2-3/5 export + bake full detail -> $STEM.tscn"
bake_one "$RES_DIR/$STEM.gltf" "$RES_DIR/$STEM.tscn"

echo "── 4/5 bake navmesh into $STEM.tscn"
bake_nav "$RES_DIR/$STEM.tscn"

if $HAS_LOD_LOW; then
  echo "── (LOD_LOW) export + bake -> ${STEM}_LOD_LOW.tscn"
  LANEKIT_PATH="" bake_one "$RES_DIR/${STEM}_LOD_LOW.gltf" "$RES_DIR/${STEM}_LOD_LOW.tscn" --only STREET_LOD_LOW || \
      echo "   (no STREET_LOD_LOW content — leaving ${STEM}_LOD_LOW.tscn unbaked)"
else
  echo "── (no STREET_LOD_LOW collection for this piece — PLATEAU precinct, skipping LOD_LOW bake)"
fi

echo "── 5/6 refresh binary district scenes (.tscn -> .scn)"
# The runtime prefers a sibling .scn over the .tscn (WorldZoneManager.resolveGeometryPath) — a
# stale .scn from a previous bake would silently shadow the scene just baked. ConvertPieces
# mtime-skips unchanged districts, so this only reconverts what this run touched. Same
# non-headless/xvfb pattern as bake_one(): MultiMesh data doesn't survive the headless dummy RS.
"${RUN[@]}" --path "$REPO" res://src/main/resources/com/openworld/world/hosts/ConvertPieces.tscn 2>&1 \
    | grep -iE "PieceBinaryConverter: done" || true

echo "── 6/6 point SoloPiece.tscn at $STEM.tscn"
SOLO="$REPO/src/main/resources/com/openworld/world/hosts/SoloPiece.tscn"
python3 - "$SOLO" "res://$RES_DIR/$STEM.tscn" <<'PY'
import re, sys
p, path = sys.argv[1], sys.argv[2]
s = open(p).read()
ref = '[ext_resource type="PackedScene" path="%s" id="piece"]' % path
node = '[node name="Piece" parent="." instance=ExtResource("piece")]'
if 'id="piece"' in s:
    s = re.sub(r'(\[ext_resource type="PackedScene" path=")[^"]*(" id="piece"\])',
               lambda m: m.group(1) + path + m.group(2), s)
else:
    # SELF-HEALING. The host ships with NO piece wired -- there is no piece to wire it to until
    # one is baked -- so a plain substitution would silently do nothing and leave the artist with
    # a host that opens on an empty world. Insert the resource before the first other
    # `ext_resource`, and the node that instances it before `WorldSystems` (which must stay last:
    # its AutoLoads expect the world already in the tree).
    s = re.sub(r'(\[ext_resource )', ref + "\n\\1", s, count=1)
    if node not in s:
        s = re.sub(r'(\[node name="WorldSystems")', node + "\n\n\\1", s, count=1)
if 'id="piece"' not in s or node not in s:
    sys.exit("build_piece.sh: could not wire %s into %s" % (path, p))
open(p, 'w').write(s)
PY

echo
echo "DONE. Walk-test it:"
echo "  $GODOT --path \"$REPO\" res://src/main/resources/com/openworld/world/hosts/SoloPiece.tscn"

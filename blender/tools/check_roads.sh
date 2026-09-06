#!/usr/bin/env bash
# check_roads.sh -- ONE COMMAND for the whole road-authoring gate (ROAD_POINT_GRAPH.md 9).
#
# The repo has no CI and the verification story has always been "run these by hand" -- which is the
# same story the previous addon had, with ~3.5 kLOC of smoketests, and it did not prevent this
# rewrite. Hand-run discipline is exactly what decays once a project gets boring, so the five steps
# are wrapped here for a git hook or a GitHub Action to run as a unit.
#
# USAGE:
#     blender/tools/check_roads.sh          # everything
#     blender/tools/check_roads.sh --quick  # pure-Python only (no Blender, ~2 s)
#
# Exits non-zero if anything fails.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BP="$(dirname "$HERE")"
ROOT="$(dirname "$BP")"
ADDON="$BP/addons/road_kit_authoring"
BLENDER="${BLENDER:-blender}"
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

pass=0
declare -a failed=()

run() {  # run <label> <cmd...>
  local label="$1"; shift
  local log; log="$(mktemp)"
  if "$@" >"$log" 2>&1; then
    pass=$((pass + 1)); printf 'PASS  %s\n' "$label"
  else
    failed+=("$label"); printf 'FAIL  %s\n' "$label"
    grep -A25 'Traceback (most recent call last)' "$log" | head -30 | sed 's/^/        /'
    tail -5 "$log" | sed 's/^/        /'
  fi
  rm -f "$log"
}

echo "== 1. pure-Python self-tests (no Blender) =="
for f in "$BP"/lib/road_points.py "$BP"/lib/lane_movements.py "$BP"/lib/lane_profile.py \
         "$BP"/lib/road_support.py \
         "$ADDON"/point_model.py "$ADDON"/point_profile.py "$ADDON"/point_solve.py \
         "$ADDON"/point_edges.py "$ADDON"/point_validate.py "$ADDON"/point_export.py \
         "$ADDON"/point_style.py; do
  [ -f "$f" ] && run "$(basename "$f")" python3 "$f"
done

echo
echo "== 2. the standing lanekit gate, on a freshly exported testbed =="
TMP="$(mktemp -d)"
run "export testbed -> .lanekit v2" python3 -c "
import sys; sys.path.insert(0, '$ADDON')
import point_validate as pv, point_export as pe
net, _mp, _cp, _rr = pv.build_testbed()
pe.write(net, '$TMP/testbed.lanekit.json')
"
[ -f "$TMP/testbed.lanekit.json" ] && \
  run "check_lanekit_graph.py" python3 "$BP/tools/check_lanekit_graph.py" \
      "$TMP/testbed.lanekit.json"
rm -rf "$TMP"

if [ "$QUICK" -eq 0 ]; then
  echo
  echo "== 3. headless Blender smoketests =="
  for f in "$ADDON"/smoketest_point_*.py; do
    # --python-exit-code BEFORE --python, or a crashing test exits 0. See run_smoketests.sh.
    run "$(basename "$f" .py)" "$BLENDER" --background --python-exit-code 1 --python "$f"
  done
fi

echo
printf 'PASS=%d FAIL=%d\n' "$pass" "${#failed[@]}"
if [ "${#failed[@]}" -gt 0 ]; then
  printf 'failed: %s\n' "${failed[*]}"
  exit 1
fi

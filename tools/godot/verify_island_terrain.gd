extends SceneTree
##
## Reads the imported Terrain3D back and compares it against the sample points
## ``tools/island_to_terrain3d.py --verify`` prints from the Python field.
##
##     Godot_v4.7.2-stable_linux.x86_64 --headless --script tools/godot/verify_island_terrain.gd
##
## This is the control for the one error in the bridge that produces a perfectly plausible
## island: a north-south mirror, from the Blender ``(x, y, z) -> (x, z, -y)`` mapping being
## applied the wrong way round. The landmarks are asymmetric, so a mirrored import reads the
## seabed where the massif should be and fails loudly instead of looking fine.
##
## Expected values come from the manifest's sibling ``island_probe.json``, written by the Python
## side, so neither end carries a hardcoded copy of the other's numbers.
##

const PROBE := "res://assets/terrain3d/_source/island_probe.json"
const DATA_DIR := "res://assets/terrain3d/island"
const TOLERANCE := 0.05  # metres; the r16 lattice quantises at 12 mm

var _terrain: Variant
var _probe: Dictionary


func _initialize() -> void:
	var text := FileAccess.get_file_as_string(PROBE)
	if text.is_empty():
		push_error("Missing %s — run tools/island_to_terrain3d.py first." % PROBE)
		quit(1)
		return
	_probe = JSON.parse_string(text)
	_terrain = ClassDB.instantiate("Terrain3D")
	root.add_child(_terrain)


func _process(_delta: float) -> bool:
	quit(_run())
	return true


func _run() -> int:
	var data: Variant = _terrain.get("data")
	_terrain.set("region_size", int(_probe.region_size))
	_terrain.set("vertex_spacing", float(_probe.vertex_spacing))
	_terrain.set("data_directory", DATA_DIR)
	data = _terrain.get("data")

	var regions: Array = data.get("region_locations")
	print("Terrain3D verify — %d regions from %s" % [regions.size(), DATA_DIR])
	if regions.is_empty():
		push_error("No regions loaded")
		return 1

	var worst := 0.0
	var failures := 0
	print("  %-12s %-22s %10s %10s %8s" % ["landmark", "godot (x, _, z)", "python", "terrain", "delta"])
	for row in _probe.points:
		var gx := float(row.godot[0])
		var gz := float(row.godot[1])
		var want := float(row.y)
		var got: float = data.get_height(Vector3(gx, 0.0, gz))
		var delta: float = absf(got - want)
		worst = maxf(worst, delta)
		var mark := "" if delta <= TOLERANCE else "   <-- MISMATCH"
		if delta > TOLERANCE:
			failures += 1
		print("  %-12s (%8.1f, %8.1f) %10.2f %10.2f %8.3f%s" % [
			row.name, gx, gz, want, got, delta, mark])

	print("  worst delta %.3f m over %d points, tolerance %.3f m" % [
		worst, _probe.points.size(), TOLERANCE])

	# A control that cannot fail is not a control. Sample each landmark's north-south mirror and
	# require it to read something else: if the import WERE mirrored, the loop above would still
	# pass (it would be comparing the mirrored field against itself at mirrored coordinates), and
	# only the asymmetry proves the check has teeth.
	var mirror_min := INF
	for row in _probe.points:
		var gx := float(row.godot[0])
		var gz := float(row.godot[1])
		if absf(gz) < 1.0:
			continue  # on the axis; its own mirror, nothing to prove
		var here: float = data.get_height(Vector3(gx, 0.0, gz))
		var there: float = data.get_height(Vector3(gx, 0.0, -gz))
		mirror_min = minf(mirror_min, absf(here - there))
	print("  mirror separation %.2f m (smallest over the asymmetric landmarks)" % mirror_min)
	if mirror_min < 1.0:
		push_error("Landmarks are near-symmetric — the mirror control proves nothing here")
		return 1

	if failures > 0:
		push_error("%d of %d probe points disagree with the Python field" % [
			failures, _probe.points.size()])
		return 1
	print("  OK")
	return 0

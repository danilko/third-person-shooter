extends SceneTree
## Bakes a RoadTerrain3DConnector's flattening into the Terrain3D region files on disk.
##
##     Godot_v4.7.2-stable_linux.x86_64 --headless --script tools/godot/bake_road_terrain.gd \
##         -- <scene.tscn> [connector_node_name]
##
## Why this exists: the connector's `auto_refresh` wires itself up through
## `configure_road_update_signal`, which returns early when `Engine.is_editor_hint()` is false.
## So terrain flattening is an AUTHORING step, not a runtime one — correct, because it writes
## into the Terrain3D height map, which lives in `data_directory` on disk. In the editor you press
## the connector's Refresh button; this is the same operation for a headless/CI run.
##
## `do_full_refresh()` only QUEUES the segments — the work happens in the connector's
## `_physics_process` (raycasts must run on the physics thread). So this waits frames rather than
## reading straight back, and reports the worst station error so a silent no-op cannot pass.

const SETTLE_FRAMES := 6      # let Terrain3D load its regions and the roads build
const WORK_FRAMES := 60       # let the connector drain its queue

var scene_path: String
var conn_name: String
var world: Node
var terrain: Node
var connector: Node
var stations: Array[Vector3] = []
var frames := 0
var refreshed := false

func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.is_empty():
		printerr("usage: --script tools/godot/bake_road_terrain.gd -- <scene.tscn> [connector_name]")
		quit(2); return
	scene_path = args[0]
	conn_name = args[1] if args.size() > 1 else "RoadTerrain3DConnector"
	var ps: PackedScene = load(scene_path)
	if ps == null:
		printerr("FAIL  cannot load %s" % scene_path); quit(2); return
	world = ps.instantiate()
	root.add_child(world)
	terrain = world.find_child("Terrain3D", true, false)
	connector = world.find_child(conn_name, true, false)
	if terrain == null or connector == null:
		printerr("FAIL  terrain=%s connector=%s in %s" % [terrain, connector, scene_path])
		quit(2); return

func _process(_delta: float) -> bool:
	frames += 1
	if frames < SETTLE_FRAMES:
		return false
	if not refreshed:
		if not connector.call("is_configured"):
			printerr("FAIL  connector is not configured (terrain/road_manager unset?)")
			printerr("      an exported Node reference needs node_paths=PackedStringArray(...) in")
			printerr("      the .tscn node header, or it silently stays null")
			quit(1); return true
		stations = _road_stations()
		print("baking %d road stations from %s" % [stations.size(), scene_path])
		connector.call("do_full_refresh")
		refreshed = true
		return false
	if frames < SETTLE_FRAMES + WORK_FRAMES:
		return false

	var offset: float = connector.get("offset")
	var worst := 0.0
	var worst_at := Vector3.ZERO
	for s in stations:
		var h: float = terrain.data.get_height(Vector3(s.x, 0.0, s.z))
		if is_nan(h):
			continue
		var err: float = abs(h - (s.y + offset))
		if err > worst:
			worst = err; worst_at = s
	print("worst station error %.3f m at %s (offset %.2f)" % [worst, worst_at, offset])

	var dir: String = terrain.data_directory
	terrain.data.save_directory(dir)
	print("saved terrain data to %s" % dir)
	# A bake that changed nothing is the failure worth naming: it looks exactly like a working one.
	quit(0 if worst < 0.10 else 1)
	return true

func _road_stations() -> Array[Vector3]:
	var out: Array[Vector3] = []
	var mgr: Node = connector.get("road_manager")
	for c in mgr.call("get_containers"):
		for pt in c.get_children():
			if pt.get_script() != null and "lane_width" in pt:
				out.append(pt.global_position)
	return out

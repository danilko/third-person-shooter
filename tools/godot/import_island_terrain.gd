extends SceneTree
##
## Imports the island heightmap produced by ``tools/island_to_terrain3d.py`` into a Terrain3D
## data directory, headless.
##
##     Godot_v4.7.2-stable_linux.x86_64 --headless --script tools/godot/import_island_terrain.gd
##
## Everything it needs is in ``assets/terrain3d/_source/island_import.json`` — the lattice, the
## height range the r16 was quantised over, and the import position. Nothing is re-derived here
## and no number is typed twice: the manifest is the single carrier across the Python/Godot seam,
## because the two ends failing to agree about the height range or the origin is the one error
## that produces a terrain which loads fine and is silently wrong.
##
## Sea level is Y = 0 by construction (the Python side already shifted the field), so this script
## has no sea constant of its own.
##
## **Timing is load-bearing.** ``Terrain3D.data`` does not exist until the node's ``_ready`` has
## run, which is a frame after ``add_child`` — and ``region_size`` silently keeps its default
## (256) when set before then, which would slice the heightmap on a lattice it was not sampled
## on. So the node is created in ``_initialize`` and everything else happens on the first
## ``_process`` frame, with the lattice read back and checked rather than assumed.
##

const DEFAULT_MANIFEST := "res://assets/terrain3d/_source/island_import.json"

## Which manifest to import. Pass another after `--` to build a different world from the same
## field (the DebugWorld window uses `debug_import.json`).
static func manifest_path() -> String:
	var a := OS.get_cmdline_user_args()
	return a[0] if a.size() > 0 else DEFAULT_MANIFEST

var _manifest: Dictionary
var _terrain: Variant
var _code := 0


func _initialize() -> void:
	_manifest = _read_manifest()
	if _manifest.is_empty():
		_code = 1
		quit(1)
		return
	_terrain = ClassDB.instantiate("Terrain3D")
	root.add_child(_terrain)


func _process(_delta: float) -> bool:
	_code = _run()
	quit(_code)
	return true


func _run() -> int:
	var size := Vector2i(int(_manifest.size[0]), int(_manifest.size[1]))
	var height_range := Vector2(float(_manifest.range[0]), float(_manifest.range[1]))
	var pos := Vector3(float(_manifest.import_position[0]), 0.0, float(_manifest.import_position[1]))
	var data_dir: String = _manifest.data_directory
	var region_size := int(_manifest.region_size)
	var vertex_spacing := float(_manifest.vertex_spacing)

	print("Terrain3D import")
	print("  source     %s" % _manifest.height_file)
	print("  lattice    %d x %d @ %.2f m, region %d" % [size.x, size.y, vertex_spacing, region_size])
	print("  height     %.2f .. %.2f m, sea level Y %.2f" % [
		height_range.x, height_range.y, float(_manifest.sea_level_y)])
	print("  position   (%.1f, %.1f)" % [pos.x, pos.z])

	var data: Variant = _terrain.get("data")
	if data == null:
		push_error("Terrain3D.data is still null after _ready")
		return 1

	_terrain.set("region_size", region_size)
	_terrain.set("vertex_spacing", vertex_spacing)
	var got_region: int = _terrain.get("region_size")
	var got_spacing: float = _terrain.get("vertex_spacing")
	if got_region != region_size or not is_equal_approx(got_spacing, vertex_spacing):
		push_error("Lattice not applied: region_size %d (wanted %d), vertex_spacing %f (wanted %f)"
			% [got_region, region_size, got_spacing, vertex_spacing])
		return 1

	# The regions stop at the sea floor's own edge; FLAT carries the horizon past them for free,
	# and the world has a logic wall well inside them either way, so nothing walks out there.
	var material: Variant = _terrain.get("material")
	if material != null:
		material.set("world_background", 1)  # NONE 0 / FLAT 1 / NOISE 2
		material.set("show_checkered", false)

	var img: Image = Terrain3DUtil.load_image(
		_manifest.height_file, ResourceLoader.CACHE_MODE_IGNORE, height_range, size)
	if img == null:
		push_error("Failed to load %s" % _manifest.height_file)
		return 1
	var min_max: Vector2 = Terrain3DUtil.get_min_max(img)
	print("  loaded     %d x %d, actual %.2f .. %.2f m" % [
		img.get_width(), img.get_height(), min_max.x, min_max.y])

	var images: Array[Image] = []
	images.resize(3)  # Terrain3DRegion.TYPE_HEIGHT / TYPE_CONTROL / TYPE_COLOR
	images[0] = img
	data.import_images(images, pos, 0.0, 1.0)

	var regions: Array = data.get("region_locations")
	print("  imported   %d regions" % regions.size())
	if regions.is_empty():
		push_error("import_images produced no regions")
		return 1

	var expected: int = int(_manifest.region_locations[1]) - int(_manifest.region_locations[0]) + 1
	if regions.size() != expected * expected:
		push_warning("Expected %d regions, got %d" % [expected * expected, regions.size()])

	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(data_dir))
	_terrain.set("data_directory", data_dir)
	data.save_directory(data_dir)
	print("  saved      %s" % data_dir)
	return 0


func _read_manifest() -> Dictionary:
	var mp := manifest_path()
	var text := FileAccess.get_file_as_string(mp)
	if text.is_empty():
		push_error("Missing or empty manifest %s — run tools/island_to_terrain3d.py first." % mp)
		return {}
	var parsed: Variant = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		push_error("Manifest %s is not a JSON object" % mp)
		return {}
	return parsed

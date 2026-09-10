extends SceneTree
##
## Paint a Terrain3D control map by ELEVATION and SLOPE, so the world reads correctly without
## anyone hand-painting a 5 km island.
##
##     Godot_v4.7.2-stable_linux.x86_64 --headless --script tools/godot/paint_terrain.gd \
##         -- res://assets/terrain3d/island
##
## Terrain3D packs base index, overlay index and a blend weight into one uint32 per texel, carried
## in an RF image — `Terrain3DUtil.enc_*` builds it and `as_float`/`as_uint` move between the two
## views. Painting the pair plus a blend (rather than a single index) is what makes the bands meet
## in a gradient instead of a hard line.
##
## The bands, bottom to top:
##
##     deep water          seabed rock, flat
##     approaching shore   seabed -> sand, blended by depth
##     the beach itself    sand
##     rising off the sand sand -> grass, blended by height
##     land                grass -> cliff, blended by SLOPE, so crags show rock and fields do not
##
## Auto-shader is switched OFF per texel: it picks by slope alone, which cannot know that the same
## 30% grade is a sea cliff in one place and a mountain flank in another.
##

const GRASS := 0
const CLIFF := 1
const SAND := 2
const SEABED := 3

const DEEP := -12.0        # below this it is all seabed
const SHORE := -1.0        # ...blending to sand by here
const SAND_TOP := 5.0      # bare sand up to here
const GRASS_TOP := 14.0    # ...fully grass by here
const SLOPE_ROCK_MIN := 0.30   # 30% grade starts showing rock
const SLOPE_ROCK_MAX := 0.85   # ...and it is bare rock by 85%

var _terrain: Variant
var _dir: String


func _initialize() -> void:
	var a := OS.get_cmdline_user_args()
	_dir = a[0] if a.size() > 0 else "res://assets/terrain3d/island"
	_terrain = ClassDB.instantiate("Terrain3D")
	root.add_child(_terrain)


func _process(_delta: float) -> bool:
	quit(_run())
	return true


func _ctl(base: int, overlay: int, blend: float) -> float:
	var v: int = Terrain3DUtil.enc_base(base) \
		| Terrain3DUtil.enc_overlay(overlay) \
		| Terrain3DUtil.enc_blend(int(clampf(blend, 0.0, 1.0) * 255.0))
	return Terrain3DUtil.as_float(v)


func _run() -> int:
	_terrain.set("vertex_spacing", 2.0)
	_terrain.set("data_directory", _dir)
	var data: Variant = _terrain.get("data")
	if data == null:
		push_error("no Terrain3D data at %s" % _dir)
		return 1

	var rs: int = _terrain.get("region_size")
	var vs: float = _terrain.get("vertex_spacing")
	var counts := {GRASS: 0, CLIFF: 0, SAND: 0, SEABED: 0}
	var painted := 0

	for loc in data.get("region_locations"):
		var region: Variant = data.call("get_region", loc)
		if region == null:
			continue
		var hmap: Image = region.call("get_height_map")
		var cmap: Image = region.call("get_control_map")
		if hmap == null or cmap == null:
			continue
		for y in rs:
			for x in rs:
				var h: float = hmap.get_pixel(x, y).r
				var hx: float = hmap.get_pixel(mini(x + 1, rs - 1), y).r
				var hy: float = hmap.get_pixel(x, mini(y + 1, rs - 1)).r
				var slope: float = maxf(absf(hx - h), absf(hy - h)) / vs

				var base := GRASS
				var overlay := GRASS
				var blend := 0.0
				if h < DEEP:
					base = SEABED; overlay = SEABED
				elif h < SHORE:
					base = SEABED; overlay = SAND
					blend = (h - DEEP) / (SHORE - DEEP)
				elif h < SAND_TOP:
					base = SAND; overlay = SAND
				elif h < GRASS_TOP:
					base = SAND; overlay = GRASS
					blend = (h - SAND_TOP) / (GRASS_TOP - SAND_TOP)
				else:
					base = GRASS; overlay = CLIFF
					blend = clampf((slope - SLOPE_ROCK_MIN) / (SLOPE_ROCK_MAX - SLOPE_ROCK_MIN), 0.0, 1.0)

				cmap.set_pixel(x, y, Color(_ctl(base, overlay, blend), 0.0, 0.0))
				counts[base] = counts[base] + 1
				painted += 1
		region.call("set_control_map", cmap)
		if region.has_method("set_modified"):
			region.call("set_modified", true)

	data.call("update_maps", 1, true, false)
	data.save_directory(_dir)
	print("painted %d texels" % painted)
	for k in [GRASS, SAND, CLIFF, SEABED]:
		print("  base %-6s %8d  (%.1f%%)" % [
			["grass", "cliff", "sand", "seabed"][k], counts[k], 100.0 * counts[k] / maxi(painted, 1)])
	print("saved   %s" % _dir)
	return 0

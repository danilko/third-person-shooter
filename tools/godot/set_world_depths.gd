extends SceneTree
##
## Enforce the vertical envelope of the world, and report it.
##
##     Godot_v4.7.2-stable_linux.x86_64 --headless --script tools/godot/set_world_depths.gd
##
## One invariant, and everything else is derived from it:
##
##     water bottom  <  terrain floor  <  ...ground...  <  water top (Y = 0)
##
## A body can only ever be inside the water box or standing on terrain. If the terrain dipped
## BELOW the water box, the sea floor there would be outside the swim volume — you would walk off
## the bottom of the ocean and stop being in water, which is the "strange condition" this closes.
##
## `WorldBounds.floorY` is the kill-Z that teleports a body back to PlayerSpawn, so it must sit
## below the water box, not above the sea floor. It was never set in the scene and so ran on the
## Java default of -64 m while the sea floor is at -138.2 m — every deep-water position in the
## world was already past the kill line.
##
## The terrain clamp is a GUARD, not a reshape: it raises anything below TERRAIN_FLOOR and leaves
## every other texel alone, so sculpting deeper than the envelope can no longer break the rule
## silently.
##

const DATA_DIR := "res://assets/terrain3d/island"
const TERRAIN_FLOOR := -190.0    # deepest the ground may go
const WATER_BOTTOM := -200.0     # water box bottom — 10 m below the terrain floor

var _terrain: Variant


func _initialize() -> void:
	_terrain = ClassDB.instantiate("Terrain3D")
	root.add_child(_terrain)


func _process(_delta: float) -> bool:
	quit(_run())
	return true


func _run() -> int:
	_terrain.set("vertex_spacing", 2.0)
	_terrain.set("data_directory", DATA_DIR)
	var data: Variant = _terrain.get("data")
	if data == null:
		push_error("no Terrain3D data at %s" % DATA_DIR)
		return 1

	var before: Vector2 = data.get_height_range()
	print("terrain before   %.2f .. %.2f m" % [before.x, before.y])

	var locs: Array = data.get("region_locations")
	var touched := 0
	var pixels := 0
	for loc in locs:
		var region: Variant = data.call("get_region", loc)
		if region == null:
			continue
		var rr: Vector2 = region.get("height_range")
		if rr.x >= TERRAIN_FLOOR:
			continue                       # nothing in this region is below the floor
		var img: Image = region.call("get_height_map")
		if img == null:
			continue
		var w := img.get_width()
		var h := img.get_height()
		var n := 0
		for y in h:
			for x in w:
				var v: float = img.get_pixel(x, y).r
				if v < TERRAIN_FLOOR:
					img.set_pixel(x, y, Color(TERRAIN_FLOOR, 0.0, 0.0))
					n += 1
		if n > 0:
			region.call("set_height_map", img)
			if region.has_method("set_modified"):
				region.call("set_modified", true)
			touched += 1
			pixels += n

	if touched > 0:
		data.call("calc_height_range", true)
		data.call("update_maps", 0, true, false)
		data.save_directory(DATA_DIR)
		print("clamped          %d texels in %d regions to %.1f m, saved" % [pixels, touched, TERRAIN_FLOOR])
	else:
		print("clamped          nothing — no texel is below %.1f m" % TERRAIN_FLOOR)

	var after: Vector2 = data.get_height_range()
	print("terrain after    %.2f .. %.2f m" % [after.x, after.y])
	print("water box        %.2f .. 0.00 m" % WATER_BOTTOM)
	print("clearance        %.2f m of water below the deepest ground" % (after.x - WATER_BOTTOM))
	if after.x <= WATER_BOTTOM:
		push_error("INVARIANT BROKEN: terrain (%.2f) reaches the water bottom (%.2f)" % [after.x, WATER_BOTTOM])
		return 1
	return 0

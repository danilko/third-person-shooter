extends SceneTree
##
## Two fixes to the island's Terrain3D data, in place, preserving every sculpted edit.
##
##     Godot_v4.7.2-stable_linux.x86_64 --headless --script tools/godot/fix_island_extent.gd
##
## 1. region_size 64 -> 256. Terrain3D's region map is a fixed REGION_MAP_SIZE (32) square, so
##    region_size caps the WORLD: 64 texels x 2 m spacing = 128 m per region x 32 = 4096 m, i.e.
##    +/-2048 — and the WorldBounds wall stands at +/-2160, OUTSIDE it. Measured before this ran:
##    get_height() returns NaN past +/-2048, so the last 112 m before the wall had no floor at all.
##    At 256 the cap is 16384 m, which is headroom rather than a limit.
##    change_region_size() re-tiles the existing maps, so nothing sculpted is lost.
##
## 2. An ocean ring. Everything past the wall is unreachable, and unreachable ground that simply
##    STOPS reads as the world running out. One region of sea floor beyond the wall, deepening
##    outward, closes the horizon with water instead of an edge. It is written only OUTSIDE the
##    existing extent — the interior is never covered by an import, so the sculpt is untouched.
##

const DATA_DIR := "res://assets/terrain3d/island"
const NEW_REGION_SIZE := 256
const RING_REGIONS := 1        # how many regions of open sea to add beyond the current extent
const DEPTH_DROP := -50.0      # how much deeper the outer edge of the ring sits than the coast
                               # shelf it starts from. The INNER depth is read off the existing
                               # data, not typed here, so the ring starts flush with the sculpt
                               # instead of stepping off it.

var _terrain: Variant
var _stage := 0


func _initialize() -> void:
	_terrain = ClassDB.instantiate("Terrain3D")
	root.add_child(_terrain)


func _process(_delta: float) -> bool:
	quit(_run())
	return true


func _extent(data: Variant, span: float) -> Array:
	var locs: Array = data.get("region_locations")
	var lo := Vector2i(9999, 9999)
	var hi := Vector2i(-9999, -9999)
	for l in locs:
		lo.x = mini(lo.x, l.x); lo.y = mini(lo.y, l.y)
		hi.x = maxi(hi.x, l.x); hi.y = maxi(hi.y, l.y)
	return [lo, hi, locs.size(), lo.x * span, (hi.x + 1) * span]


## Height on the sculpt's boundary, robustly.
##
## get_height() interpolates, and near the lattice edge that interpolation reaches a texel that is
## not there: (2046, -2040.0) reads -76.31 while (2046, -2039.984) — 16 mm away — is NaN. So probe
## ON the texel lattice, and if that still fails walk inward a few texels rather than falling back
## to a constant, which is what put a 58 m step in the sea floor.
func _edge_height(data: Variant, side: int, u: float, inner: float, vs: float) -> float:
	var uu: float = snappedf(clampf(u, -inner + vs * 2.0, inner - vs * 2.0), vs)
	for step in range(1, 10):
		var inset: float = inner - vs * float(step)
		var p: Vector3
		match side:
			0: p = Vector3(snappedf(inset, vs), 0.0, uu)
			1: p = Vector3(snappedf(-inset, vs), 0.0, uu)
			2: p = Vector3(uu, 0.0, snappedf(inset, vs))
			_: p = Vector3(uu, 0.0, snappedf(-inset, vs))
		var h: float = data.get_height(p)
		if not is_nan(h):
			return minf(h, 0.0)
	return NAN


func _run() -> int:
	_terrain.set("vertex_spacing", 2.0)
	_terrain.set("data_directory", DATA_DIR)
	var data: Variant = _terrain.get("data")
	if data == null:
		push_error("no Terrain3D data at %s" % DATA_DIR); return 1

	var rs: int = _terrain.get("region_size")
	var vs: float = _terrain.get("vertex_spacing")
	var e := _extent(data, float(rs) * vs)
	print("before   region_size %d (%.0f m/region), %d regions, extent %.0f .. %.0f m, height %s"
		% [rs, rs * vs, e[2], e[3], e[4], data.get_height_range()])

	# ---- 1. re-tile ----
	if rs != NEW_REGION_SIZE:
		_terrain.call("change_region_size", NEW_REGION_SIZE)
		rs = _terrain.get("region_size")
		if rs != NEW_REGION_SIZE:
			push_error("change_region_size did not take (still %d)" % rs); return 1
	var span := float(rs) * vs
	e = _extent(data, span)
	print("re-tiled region_size %d (%.0f m/region), %d regions, extent %.0f .. %.0f m, height %s"
		% [rs, span, e[2], e[3], e[4], data.get_height_range()])

	# ---- 2. ocean ring ----
	var lo: Vector2i = e[0]
	var hi: Vector2i = e[1]
	var ring_lo := lo - Vector2i(RING_REGIONS, RING_REGIONS)
	var ring_hi := hi + Vector2i(RING_REGIONS, RING_REGIONS)
	var half: int = int(data.REGION_MAP_SIZE / 2)
	if ring_lo.x < -half or ring_hi.x >= half:
		push_error("ring would exceed the %d x %d region map" % [data.REGION_MAP_SIZE, data.REGION_MAP_SIZE])
		return 1

	var added := 0
	var inner: float = maxf(absf(lo.x), absf(hi.x + 1)) * span      # where the sculpt ends
	var outer: float = maxf(absf(ring_lo.x), absf(ring_hi.x + 1)) * span
	# The ring must START at whatever the sculpted sea floor actually is where it stops, and that
	# varies all the way round — a single mean left a 66 m step at the shallowest corner. So the
	# boundary is sampled once into a profile (the region grid is a SQUARE, so the perimeter is
	# parameterised by side + offset) and every ring texel starts from its own nearest edge value.
	var nan_probes := 0
	var prof_n := 512
	var profile: Array[float] = []
	profile.resize(prof_n * 4)
	for side in 4:
		for i in prof_n:
			var u: float = -inner + 2.0 * inner * i / float(prof_n - 1)
			var h: float = _edge_height(data, side, u, inner, vs)
			if is_nan(h):
				nan_probes += 1
				h = -60.0
			profile[side * prof_n + i] = h
	print("ring     edge profile: %d points, %d NaN" % [profile.size(), nan_probes])
	for sdbg in 4:
		var line := "     side %d:" % sdbg
		for k in [0, prof_n / 4, prof_n / 2, prof_n * 3 / 4, prof_n - 1]:
			line += " %8.2f" % profile[sdbg * prof_n + k]
		print(line)
	if nan_probes > profile.size() / 20:
		push_error("edge profile is %d/%d NaN — the ring would be a flat shelf" % [nan_probes, profile.size()])
		return 1
	for ry in range(ring_lo.y, ring_hi.y + 1):
		for rx in range(ring_lo.x, ring_hi.x + 1):
			if rx >= lo.x and rx <= hi.x and ry >= lo.y and ry <= hi.y:
				continue    # existing region — never import over the sculpt
			var img := Image.create(rs, rs, false, Image.FORMAT_RF)
			for y in rs:
				for x in rs:
					# depth by distance from the world centre, so the ring deepens outward
					var wx := (rx * rs + x) * vs
					var wz := (ry * rs + y) * vs
					# 0 at the sculpt's edge, 1 at the outer rim — so there is no step to fall off
					var r: float = maxf(absf(wx), absf(wz))
					var dd: float = clampf((r - inner) / maxf(outer - inner, 1.0), 0.0, 1.0)
					# nearest point on the boundary square, and its own depth
					var side := 0
					var u := 0.0
					if absf(wx) >= absf(wz):
						side = 0 if wx >= 0.0 else 1
						u = wz
					else:
						side = 2 if wz >= 0.0 else 3
						u = wx
					var pi: int = clampi(int(round((clampf(u, -inner, inner) + inner)
						/ (2.0 * inner) * float(prof_n - 1))), 0, prof_n - 1)
					var start: float = profile[side * prof_n + pi]
					img.set_pixel(x, y, Color(start + DEPTH_DROP * dd, 0, 0))
			var images: Array[Image] = []
			images.resize(3)
			images[0] = img
			data.import_images(images, Vector3(rx * span, 0.0, ry * span), 0.0, 1.0)
			added += 1

	e = _extent(data, span)
	print("ring     +%d regions -> %d total, extent %.0f .. %.0f m, height %s"
		% [added, e[2], e[3], e[4], data.get_height_range()])

	data.save_directory(DATA_DIR)
	print("saved    %s" % DATA_DIR)
	return 0

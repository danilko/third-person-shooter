extends SceneTree
##
## Add landform character to an already-sculpted Terrain3D world, in place.
##
##     Godot_v4.7.2-stable_linux.x86_64 --headless --script tools/godot/shape_terrain.gd \
##         -- res://assets/terrain3d/island
##
## Everything here is ADDITIVE against the heights already in the data, masked so it only touches
## what it is meant to. The sculpt is the base; this is detail laid over it. Nothing regenerates,
## so hand edits survive.
##
## **Ridges, because a mountain is not a dome.** The island's massif came out of a contour field
## as a smooth cone, which reads as a hill however tall you make it. Real ranges — the Japanese
## ones especially — are sharp crests with V-cut valleys between them, and that is exactly what
## RIDGED fractal noise is: `abs()` of each octave, inverted, so the zero crossings become creases
## instead of smooth minima. Ordinary FBM would only make the dome lumpy.
##
## The ridge amplitude is masked by BOTH height and slope. Height alone would corrugate the
## airport apron and the harbour aprons the moment they sit high enough; slope alone would chew
## into a cliff at the waterline. A place has to be high AND already sloping to earn ridges.
##
## **A harbour needs deep water at the quay.** A naturally shelving coast gives a metre of water
## fifty metres out, which is right for a beach and useless for a port. Inside the harbour radii
## the underwater profile is multiplied so the depth arrives close in, clamped so it does not
## punch through the sea floor.
##

const RIDGE_START := 40.0        # below this the terrain is left alone entirely
const RIDGE_FULL := 220.0        # ...and the ridges reach full amplitude here
const RIDGE_AMP := 38.0          # metres, the coarse crest-to-trough detail
const RIDGE_FREQ := 0.0035       # ~285 m between crests
const DETAIL_AMP := 9.0          # finer second layer
const DETAIL_FREQ := 0.016       # ~60 m
const SLOPE_FULL := 0.25         # 25% grade earns the full ridge amplitude
const SEA_GUARD := 6.0           # never touch anything below this: coast, beach, quay

## Harbour mouths, in GODOT world coordinates, where the water must get deep fast.
const HARBOURS := [
	{"pos": Vector2(-240.0, 1470.0), "radius": 420.0},   # HARBOUR
	{"pos": Vector2(-392.0, 1265.0), "radius": 300.0},   # PORTSPUR
]
const HARBOUR_DEEPEN := 3.4      # multiplier on existing depth inside the radius
const HARBOUR_MAX_DEPTH := 26.0  # ...clamped, so it stays above the sea floor

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


func _run() -> int:
	_terrain.set("vertex_spacing", 2.0)
	_terrain.set("data_directory", _dir)
	var data: Variant = _terrain.get("data")
	if data == null:
		push_error("no Terrain3D data at %s" % _dir)
		return 1

	var rs: int = _terrain.get("region_size")
	var vs: float = _terrain.get("vertex_spacing")
	var before: Vector2 = data.get_height_range()
	print("before   %d regions of %d @ %.1f m, height %.2f .. %.2f" % [
		data.get("region_locations").size(), rs, vs, before.x, before.y])

	var ridge := FastNoiseLite.new()
	ridge.noise_type = FastNoiseLite.TYPE_SIMPLEX
	ridge.fractal_type = FastNoiseLite.FRACTAL_RIDGED
	ridge.frequency = RIDGE_FREQ
	ridge.fractal_octaves = 5
	ridge.fractal_lacunarity = 2.1
	ridge.fractal_gain = 0.55
	ridge.seed = 20260907

	var detail := FastNoiseLite.new()
	detail.noise_type = FastNoiseLite.TYPE_SIMPLEX
	detail.fractal_type = FastNoiseLite.FRACTAL_RIDGED
	detail.frequency = DETAIL_FREQ
	detail.fractal_octaves = 3
	detail.seed = 991

	var raised := 0
	var deepened := 0
	var max_ridge := 0.0
	var max_deep := 0.0

	for loc in data.get("region_locations"):
		var region: Variant = data.call("get_region", loc)
		if region == null:
			continue
		var img: Image = region.call("get_height_map")
		if img == null:
			continue
		var changed := false
		var ox: float = float(loc.x) * float(rs) * vs
		var oz: float = float(loc.y) * float(rs) * vs

		for y in rs:
			for x in rs:
				var h: float = img.get_pixel(x, y).r
				var wx: float = ox + float(x) * vs
				var wz: float = oz + float(y) * vs

				# ---- harbour: deep water close in ----
				if h < 0.0:
					for hb in HARBOURS:
						if Vector2(wx, wz).distance_to(hb.pos) < hb.radius:
							var deep: float = maxf(h * HARBOUR_DEEPEN, -HARBOUR_MAX_DEPTH)
							if deep < h - 0.01:
								max_deep = maxf(max_deep, h - deep)
								h = deep
								changed = true
								deepened += 1
							break
					img.set_pixel(x, y, Color(h, 0.0, 0.0))
					continue

				if h < SEA_GUARD:
					continue

				# ---- mountains: ridges where it is both high and already steep ----
				var hx: float = img.get_pixel(mini(x + 1, rs - 1), y).r
				var hy: float = img.get_pixel(x, mini(y + 1, rs - 1)).r
				var slope: float = maxf(absf(hx - h), absf(hy - h)) / vs
				var by_height: float = clampf((h - RIDGE_START) / (RIDGE_FULL - RIDGE_START), 0.0, 1.0)
				var by_slope: float = clampf(slope / SLOPE_FULL, 0.0, 1.0)
				var mask: float = by_height * by_slope
				if mask <= 0.001:
					continue
				var d: float = ridge.get_noise_2d(wx, wz) * RIDGE_AMP \
					+ detail.get_noise_2d(wx, wz) * DETAIL_AMP * by_height
				d *= mask
				if absf(d) > 0.01:
					max_ridge = maxf(max_ridge, absf(d))
					img.set_pixel(x, y, Color(h + d, 0.0, 0.0))
					changed = true
					raised += 1

		if changed:
			region.call("set_height_map", img)
			if region.has_method("set_modified"):
				region.call("set_modified", true)

	data.call("calc_height_range", true)
	data.call("update_maps", 0, true, false)
	data.save_directory(_dir)
	var after: Vector2 = data.get_height_range()
	print("ridges   %d texels, largest displacement %.2f m" % [raised, max_ridge])
	print("harbour  %d texels deepened, largest %.2f m" % [deepened, max_deep])
	print("after    height %.2f .. %.2f m   (was %.2f .. %.2f)" % [
		after.x, after.y, before.x, before.y])
	print("saved    %s" % _dir)
	return 0

extends SceneTree
## The THREE driving views, driven end to end through the real input path.
##
##   godot --headless --path . --script tools/godot/probe_vehicle_views.gd
##
## A real Player walks into a real Vehicle's EntranceArea and presses `use_carrier` through the
## Input singleton, so what runs is the shipped PlayerController -> Player.applyInput ->
## requestEnter -> Vehicle.tryEnter path, not a hand-built approximation of it. Then `view` is
## pressed to walk the cycle. Four things are asserted, and each was a real hole before this pass:
##
##   1. the on-foot FPS choice CARRIES into the seat (it used to be dropped: the carrier's own
##      cameraMode defaulted to TPS and remembered whatever that car was last left in),
##   2. the cockpit view renders from CockpitCameraMount -- the driver's eye, not the bonnet,
##   3. the driver's HEAD is hidden in the cockpit view and visible in the other two,
##   4. the view survives the exit, so climbing out puts the player back in the view they chose.
##
## Head visibility is read off the `head` mesh itself rather than any flag: it is the thing the
## player actually sees, and the whole class of bug here was a flag that disagreed with it.

const PLAYER  := "res://src/main/resources/com/openworld/character/Player.tscn"
const VEHICLE := "res://src/main/resources/com/openworld/vehicle/Vehicle.tscn"

var fails := 0

func _find(n: Node, nm: String) -> Node:
	if n.name == nm:
		return n
	for c in n.get_children():
		var r: Node = _find(c, nm)
		if r != null:
			return r
	return null

func _floor(parent: Node) -> void:
	var b := StaticBody3D.new()
	var cs := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(60, 1, 60)
	cs.shape = box
	b.add_child(cs)
	b.position = Vector3(0, -0.5, 0)
	parent.add_child(b)

func _tick(n: int) -> void:
	for i in range(n):
		await physics_frame

func _press(action: String) -> void:
	Input.action_press(action)
	await physics_frame
	Input.action_release(action)
	await _tick(12)

func _check(label: String, ok: bool, detail: String) -> void:
	if not ok:
		fails += 1
	print("  %s  %-34s %s" % ["PASS" if ok else "FAIL", label, detail])

func _initialize() -> void:
	var world := Node3D.new()
	root.add_child(world)
	_floor(world)
	await _tick(3)

	var car: Node3D = (load(VEHICLE) as PackedScene).instantiate() as Node3D
	world.add_child(car)
	# Parked and FROZEN, at its own measured resting height. The suspension is not what is under
	# test here, and in a bare probe it is actively in the way: with a Player anywhere in the tree
	# the car climbs at a steady ~50 m/s, taking its seats, mounts and EntranceArea with it. Freeze
	# leaves every one of those exactly where a parked car has them, which is all this needs.
	car.position = Vector3(0, 0.98, 0)
	car.set("freeze", true)
	await _tick(20)

	var p: Node3D = (load(PLAYER) as PackedScene).instantiate() as Node3D
	world.add_child(p)
	# Behind the car: inside the 4 x 2.5 x 7 EntranceArea, clear of the body's own hull.
	p.position = Vector3(0, 1.0, 2.9)
	await _tick(30)

	var head: Node3D      = _find(p, "head") as Node3D
	var cockpit: Node3D   = _find(car, "CockpitCameraMount") as Node3D
	var bonnet: Node3D    = _find(car, "FPSCameraMount") as Node3D
	var car_cam: Camera3D = _find(car, "ActiveCamera") as Camera3D
	if head == null or cockpit == null or bonnet == null or car_cam == null:
		print("FAIL: missing node — head=%s cockpit=%s bonnet=%s car_cam=%s"
			% [head != null, cockpit != null, bonnet != null, car_cam != null])
		quit(1)
		return

	# The player chooses FPS ON FOOT. Everything after this is about whether that choice survives.
	p.set("is_fps_mode", true)
	await _tick(10)
	print("")
	print("=== on foot ===")
	_check("FPS on foot hides the head", not head.visible,
		"head.visible=%s" % head.visible)

	await _press("use_carrier")
	await _tick(30)

	print("")
	print("=== seated: view 1 of 3 (carried in from foot) ===")
	_check("carrier camera is current", car_cam.current, "current=%s" % car_cam.current)
	var d_cockpit: float = car_cam.global_position.distance_to(cockpit.global_position)
	var d_bonnet: float  = car_cam.global_position.distance_to(bonnet.global_position)
	_check("camera is at the COCKPIT mount", d_cockpit < 0.02,
		"d(cockpit)=%.3f m  d(bonnet)=%.3f m" % [d_cockpit, d_bonnet])
	_check("cockpit view hides the head", not head.visible,
		"head.visible=%s" % head.visible)
	# WHERE the cockpit camera sits relative to the driver's OWN eye, decomposed in the car's frame.
	# The mount is authored as an offset from the SEAT, so it can only be as right as that offset:
	# get the sign of the fore/aft term backwards and the camera sits behind the driver's neck,
	# looking at the back of their own shoulders, with the head hidden so nothing explains it.
	var eye_marker: Node3D = _find(p, "MarkerFPSCamera") as Node3D
	if eye_marker != null:
		var b: Basis = car.global_transform.basis
		var d: Vector3 = car_cam.global_position - eye_marker.global_position
		print("   cockpit cam vs driver eye: fwd %+.3f  right %+.3f  up %+.3f m"
			% [d.dot(-b.z), d.dot(b.x), d.dot(b.y)])
		_check("cockpit camera is AT the driver's eye", d.length() < 0.10,
			"off by %.3f m" % d.length())

	await _press("view")
	print("")
	print("=== seated: view 2 of 3 (bonnet) ===")
	d_cockpit = car_cam.global_position.distance_to(cockpit.global_position)
	d_bonnet  = car_cam.global_position.distance_to(bonnet.global_position)
	_check("camera is at the BONNET mount", d_bonnet < 0.02,
		"d(cockpit)=%.3f m  d(bonnet)=%.3f m" % [d_cockpit, d_bonnet])
	_check("bonnet view shows the head", head.visible,
		"head.visible=%s" % head.visible)

	await _press("view")
	print("")
	print("=== seated: view 3 of 3 (TPS follow) ===")
	d_cockpit = car_cam.global_position.distance_to(cockpit.global_position)
	d_bonnet  = car_cam.global_position.distance_to(bonnet.global_position)
	_check("camera is at neither eye mount", d_cockpit > 1.0 and d_bonnet > 1.0,
		"d(cockpit)=%.3f m  d(bonnet)=%.3f m" % [d_cockpit, d_bonnet])
	_check("TPS view shows the head", head.visible,
		"head.visible=%s" % head.visible)

	# ...and round, back to where the cycle started.
	await _press("view")
	d_cockpit = car_cam.global_position.distance_to(cockpit.global_position)
	print("")
	print("=== seated: cycle wraps ===")
	_check("view 4 is view 1 again", d_cockpit < 0.02, "d(cockpit)=%.3f m" % d_cockpit)

	# Exit in a first-person view: the player should still be in first person on foot.
	await _press("use_carrier")
	await _tick(30)
	print("")
	print("=== back on foot ===")
	_check("still in first person", bool(p.get("is_fps_mode")),
		"is_fps_mode=%s" % p.get("is_fps_mode"))
	_check("head hidden again on foot", not head.visible,
		"head.visible=%s" % head.visible)

	print("")
	print("PASS (0 failures)" if fails == 0 else "FAIL (%d failures)" % fails)
	quit(1 if fails > 0 else 0)

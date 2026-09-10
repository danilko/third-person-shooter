extends SceneTree
## The drive-by firing sector, the rear posture, and the residual left for the bones.
##
##   godot --headless --path . --script tools/godot/probe_driveby_aim.gd
##
## A seated driver's aim is swept through a full half-turn and the shipped
## VehicleCameraController -> Vehicle.clampSeatAim -> Character.applySeatedAimTarget path is what
## runs. The sweep is produced by ROTATING THE CAR under a held `aim`, not by faking mouse motion:
## in FPS aim mode the camera keeps its own world yaw, so turning the car by theta puts the aim at
## heading -theta exactly, with no dependence on input plumbing (InputEventMouseMotion through
## Input.parse_input_event was tried and does not arrive reliably in a bare --script tree). The
## camera's own end of the chain is covered by probe_vehicle_views.gd. Three things are asserted,
## and they are the three the drive-by rework is about:
##
##   1. SECTOR      -- the aim point never leaves the seat's authored sweep, however far round the
##                     camera looks. Before this the camera's point went straight through to the
##                     bullet, so a driver could shoot through their own car.
##   2. POSTURE     -- past the rig's reach the occupant turns round in the seat instead of
##                     straining, and turns back when the aim comes forward again (hysteresis).
##   3. RESIDUAL    -- what is left for the spine and collarbones after the posture has taken its
##                     share stays inside the stance's reach. This is the number that says the rear
##                     sector is reachable by a body rather than only by a clamp.
##
## Gun-vs-bullet agreement is not asserted here because it is now true by CONSTRUCTION: the bones,
## the shot's sight leg and the reticle all read the one clamped point (Character
## .applySeatedAimTarget), so there is no second value left to disagree.

const PLAYER  := "res://src/main/resources/com/openworld/character/Player.tscn"
const VEHICLE := "res://src/main/resources/com/openworld/vehicle/Vehicle.tscn"

# Vehicle.tscn: yaw_sensitivity, and VehicleConfig's authored sector/posture.
const YAW_SENSITIVITY := 0.02
const SWEEP_MIN       := -180.0
const SWEEP_MAX       := 180.0
const RIG_REACH       := 100.0   # Character.tscn DriveCarrier aim_yaw_limit
const POSTURE_HOLD    := 80.0    # VehicleConfig.postureHoldDeg
const REAR_BODY_YAW   := 150.0
const REAR_ENTER      := 100.0
const REAR_EXIT       := 80.0
const TOL             := 6.0     # easing/lerp slack, degrees

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

func _wrap(d: float) -> float:
	while d > 180.0:
		d -= 360.0
	while d < -180.0:
		d += 360.0
	return d

## Signed heading of a world direction relative to the car's forward, + to the RIGHT.
##
## DERIVED FROM THE CAR'S OWN AXES, deliberately not written to match Vehicle.headingDegrees. The
## first version of this function mirrored that implementation line for line, so when the Java side
## had the sign backwards -- returning -90 for a direction pointing RIGHT -- the probe agreed with it
## and reported 4/4 on a car whose driver could shoot out of the passenger window and not his own.
## A check that reproduces the implementation's reasoning cannot test the reasoning. Column 0 IS
## right and -column 2 IS forward, by definition of the transform, so a dot product against them
## cannot express the mistake.
func _heading(car: Node3D, dir: Vector3) -> float:
	var b: Basis = car.global_transform.basis
	return rad_to_deg(atan2(dir.dot(b.x), dir.dot(-b.z)))

## Point the camera at `heading_deg` relative to the car (+ = right) by turning the CAR, and
## CONVERGE on the measured value rather than computing a car yaw and hoping.
##
## Under a held `aim` the carrier camera keeps its own yaw, so turning the car moves the aim's
## car-relative heading one-for-one -- but only if that yaw really is constant, and it is not
## reliably: the FPS/TPS-follow branches re-snap it to the vehicle heading the moment
## `isAimingOrFiring()` reads false for a frame. An open-loop version of this drifted 126 degrees by
## the end of the sweep and reported it as a posture failure. Measuring the camera and closing the
## loop makes the probe indifferent to that entirely.
func _aim_at_heading(car: Node3D, cam: Camera3D, heading_deg: float) -> void:
	for i in range(6):
		var err := _wrap(heading_deg - _heading(car, -cam.global_transform.basis.z))
		if abs(err) < 1.0:
			break
		car.rotation.y += deg_to_rad(err)
		await _tick(6)
	# Enough frames for the posture ease (postureTurnSpeed 360 deg/s over at most 150) to finish.
	await _tick(45)

func _check(label: String, ok: bool, detail: String) -> void:
	if not ok:
		fails += 1
	print("  %s  %-30s %s" % ["PASS" if ok else "FAIL", label, detail])

func _initialize() -> void:
	var world := Node3D.new()
	root.add_child(world)
	_floor(world)
	await _tick(3)

	var car: Node3D = (load(VEHICLE) as PackedScene).instantiate() as Node3D
	world.add_child(car)
	car.position = Vector3(0, 0.98, 0)
	car.set("freeze", true)          # see probe_vehicle_views.gd -- suspension, not this feature
	await _tick(20)

	var p: Node3D = (load(PLAYER) as PackedScene).instantiate() as Node3D
	world.add_child(p)
	p.position = Vector3(0, 1.0, 2.9)
	await _tick(30)

	await _press("use_carrier")
	await _tick(30)

	# THE FLOOR GOES ONCE THE DRIVER IS SEATED, and the reason is a real property of the system
	# rather than probe hygiene. The carrier camera rests pitched `followPitchDeg` (10) DOWN, so its
	# 200 m aim ray lands on a floor about 4 m away -- and at 4 m the occupant sits far enough off
	# the camera that the direction OCCUPANT -> aim point is tens of degrees from the camera's own.
	# The clamp and the posture both work from the occupant's heading (correctly: the gun is on the
	# occupant), so a near-field target genuinely demands a big body angle and made the residual
	# reading swing 65 degrees between samples. With no floor the ray reaches its full length, which
	# is what a real world outdoors does, and the parallax becomes negligible.
	for child in world.get_children():
		if child is StaticBody3D:
			child.queue_free()
	await _tick(10)

	var aim: Node3D = _find(p, "AimTarget") as Node3D
	if aim == null:
		print("FAIL: no AimTarget marker under the player")
		quit(1)
		return

	# FPS sub-mode: the only one that takes free mouse yaw while aiming, which is what lets this
	# probe point the camera anywhere without fighting the chase camera's recovery lerp.
	await _press("view")
	Input.action_press("aim")
	await _tick(10)

	print("")
	print("=== sweeping the aim round the car ===")
	print("  %-9s %-9s %-9s %-9s %-9s %-9s" % ["want-hdg", "cam-hdg", "aim-hdg", "body-yaw", "residual", "aim-dist"])
	var worst_residual := 0.0
	var sector_ok := true
	var car_cam: Camera3D = _find(car, "ActiveCamera") as Camera3D
	# BOTH sides, because a left-hand-drive seat covering only its own half is the defect this
	# round fixed: the far side was reachable by no posture, so the aim clamped at the sweep's near
	# bound and the body swung back to forward whenever the player crossed it.
	for want in [0.0, -80.0, -140.0, -179.0, 179.0, 140.0, 80.0, 40.0]:
		await _aim_at_heading(car, car_cam, want)
		var hdg := _heading(car, aim.global_position - p.global_position)
		var body := _wrap(rad_to_deg(p.global_rotation.y - car.global_rotation.y))
		# body is a GODOT yaw (+ left) and hdg is a HEADING (+ right), so the body's heading is
		# -body. Subtracting them directly -- which this did at first -- compares two different
		# spaces and reported 129.6 deg of residual on a rig that was covering 69.6.
		var residual: float = abs(_wrap(hdg + body))
		worst_residual = max(worst_residual, residual)
		if hdg < SWEEP_MIN - TOL or hdg > SWEEP_MAX + TOL:
			sector_ok = false
		# The camera's own heading, from its FORWARD vector -- never from euler.y, which this rig's
		# 180-degree Pivot flip and any pitch make useless.
		var camhdg := _heading(car, -car_cam.global_transform.basis.z)
		var dist: float = (aim.global_position - p.global_position).length()
		print("  %+9.1f %+9.1f %+9.1f %+9.1f %9.1f %9.1f" % [want, camhdg, hdg, body, residual, dist])

	print("")
	_check("aim stays in the sector", sector_ok,
		"sweep [%.0f, %.0f]" % [SWEEP_MIN, SWEEP_MAX])
	var rear_body := _wrap(rad_to_deg(p.global_rotation.y - car.global_rotation.y))
	_check("posture stands down near forward", abs(rear_body) < TOL,
		"body %+.1f deg at the sweep's last sample (+40, inside the reach)" % rear_body)
	_check("bones asked only the residual", worst_residual <= RIG_REACH + TOL,
		"worst residual %.1f deg (rig reach %.0f)" % [worst_residual, RIG_REACH])
	# The continuous rule holds the residual AT postureHoldDeg once the body starts turning, so the
	# load on the spine and collarbones is flat all the way round rather than sawtoothing between a
	# fixed body angle and the aim. A fixed-angle posture cannot produce this.
	_check("residual is flat past the hold", worst_residual <= POSTURE_HOLD + TOL,
		"worst %.1f deg vs hold %.0f" % [worst_residual, POSTURE_HOLD])

	# BOTH SIDES REACH, AND SYMMETRICALLY. A left-hand-drive seat (Seat0 is at x = -0.45) used to
	# be able to fire far out of its OWN window only -- first because the heading sign was inverted
	# (so it was the wrong window), then because the posture could only ever turn one way.
	print("")
	print("=== can a LEFT seat cover both sides? ===")
	await _aim_at_heading(car, car_cam, -150.0)
	var left_hdg := _heading(car, aim.global_position - p.global_position)
	var left_body := _wrap(rad_to_deg(p.global_rotation.y - car.global_rotation.y))
	_check("own window (LEFT) reaches", left_hdg < -140.0,
		"aim-hdg %+.1f, body %+.1f" % [left_hdg, left_body])
	await _aim_at_heading(car, car_cam, 150.0)
	var right_hdg := _heading(car, aim.global_position - p.global_position)
	var right_body := _wrap(rad_to_deg(p.global_rotation.y - car.global_rotation.y))
	_check("far side (RIGHT) reaches too", right_hdg > 140.0,
		"aim-hdg %+.1f, body %+.1f" % [right_hdg, right_body])
	# The postures must be MIRRORS: the body turns toward the target either way, so the two body
	# angles are equal and opposite. A one-sided posture reads as +150/+150 or +150/0 here.
	_check("posture mirrors, not one-sided", abs(left_body + right_body) < TOL * 2.0,
		"body %+.1f left vs %+.1f right (want equal and opposite)" % [left_body, right_body])

	# ...and forward again: the posture must come back, not latch.
	await _aim_at_heading(car, car_cam, 0.0)
	var fwd_body := _wrap(rad_to_deg(p.global_rotation.y - car.global_rotation.y))
	var fwd_hdg := _heading(car, aim.global_position - p.global_position)
	print("")
	print("=== aiming forward again ===")
	_check("posture returns to forward", abs(fwd_body) < TOL,
		"body %+.1f deg at aim-hdg %+.1f (exit %.0f)" % [fwd_body, fwd_hdg, REAR_EXIT])

	Input.action_release("aim")
	print("")
	print("PASS (0 failures)" if fails == 0 else "FAIL (%d failures)" % fails)
	quit(1 if fails > 0 else 0)

func _press(action: String) -> void:
	Input.action_press(action)
	await physics_frame
	Input.action_release(action)
	await _tick(12)

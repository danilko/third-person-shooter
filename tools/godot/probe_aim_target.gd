extends SceneTree
## Is the AimTarget where the CAMERA is looking, or where the BODY is facing?
##
##   godot --headless --script tools/godot/probe_aim_target.gd
##
## MovementController faces the mesh with aimYaw() = atan2(-dx,-dz) toward
## Character.getAimTargetPosition() while in combat, and with the same formula on the movement
## direction when out of combat. The user reports out-of-combat locomotion correct and in-combat
## facing 90 deg off, so the formula is fine and the AIM TARGET is the suspect: if the marker rides
## a node parented under the BODY rather than under the camera rig, then a body placed at a -90 deg
## yaw (which is exactly how both world scenes place the Player) puts the aim point 90 deg away from
## where the camera looks -- combat-only, constant, and invisible out of combat.
##
## Nothing here crosses the JVM boundary except reading node transforms.

const CHAR := "res://src/main/resources/com/openworld/character/Character.tscn"
const BODY_YAW_DEG := -90.0          # World.tscn:335 / DebugWorld.tscn:558 place the Player here

func _find(n: Node, nm: String) -> Node:
	if n.name == nm:
		return n
	for c in n.get_children():
		var r: Node = _find(c, nm)
		if r != null:
			return r
	return null

func _yaw(v: Vector3) -> float:
	var f := Vector3(v.x, 0.0, v.z)
	if f.length() < 0.0001:
		return NAN
	f = f.normalized()
	return rad_to_deg(atan2(f.x, f.z))

func _delta(a: float, b: float) -> float:
	var d: float = a - b
	while d > 180.0:
		d -= 360.0
	while d < -180.0:
		d += 360.0
	return d

func _path_from(root_node: Node, n: Node) -> String:
	return str(root_node.get_path_to(n)) if n != null else "<missing>"

func _initialize() -> void:
	var packed: PackedScene = load(CHAR) as PackedScene
	if packed == null:
		print("FAIL: cannot load ", CHAR)
		quit(1)
		return
	var body: Node3D = packed.instantiate()
	body.rotation.y = deg_to_rad(BODY_YAW_DEG)
	root.add_child(body)
	for i in 8:
		await physics_frame

	var cam_ctl: Node3D = _find(body, "TPSCameraController") as Node3D
	var active_cam: Node3D = _find(body, "ActiveCamera") as Node3D
	var aim_ray: RayCast3D = _find(body, "AimRay") as RayCast3D
	var aim_target: Node3D = _find(body, "AimTarget") as Node3D

	print("--- who is parented to whom ---")
	print("  TPSCameraController : ", _path_from(body, cam_ctl),
		  "   top_level=", (cam_ctl.top_level if cam_ctl != null else "n/a"))
	print("  ActiveCamera        : ", _path_from(body, active_cam))
	print("  AimRay              : ", _path_from(body, aim_ray))
	print("  AimTarget           : ", _path_from(body, aim_target))
	if aim_target == null or active_cam == null:
		print("FAIL: could not find the aim chain")
		quit(1)
		return

	print("")
	print("--- yaws (world, degrees) ---")
	var body_fwd: Vector3 = -body.global_transform.basis.z
	var cam_fwd: Vector3 = -active_cam.global_transform.basis.z
	var to_target: Vector3 = aim_target.global_position - body.global_position
	print("  body.rotation.y            = %8.1f" % rad_to_deg(body.rotation.y))
	print("  body forward (-Z)          = %8.1f" % _yaw(body_fwd))
	print("  ActiveCamera forward (-Z)  = %8.1f" % _yaw(cam_fwd))
	print("  body -> AimTarget          = %8.1f     <- this is what aimYaw() uses" % _yaw(to_target))
	print("  AimTarget global pos       = (%.2f, %.2f, %.2f)" % [aim_target.global_position.x, aim_target.global_position.y, aim_target.global_position.z])
	print("")
	var err: float = _delta(_yaw(to_target), _yaw(cam_fwd))
	print("  aim-target yaw  MINUS  camera yaw = %+.1f deg" % err)
	if absf(err) < 10.0:
		print("  -> the aim point tracks the camera. aimYaw() is not the source of the offset.")
	else:
		print("  -> the aim point does NOT track the camera; it is off by the amount above.")
		print("     Compare with body forward (%.1f): if it matches, the marker is riding the BODY." % _yaw(body_fwd))
	quit(0 if absf(err) < 10.0 else 1)

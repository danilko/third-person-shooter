extends SceneTree
## Where does the character's BODY actually face, for a known world-space movement direction?
##
##   godot --headless --script tools/godot/probe_mesh_facing.gd
##
## MovementController writes meshRoot's Y rotation every physics frame from either the aim yaw
## (combat) or the movement direction (out of combat), via atan2(-dx, -dz) -- the mapping for a
## -Z-FORWARD mesh. This measures the result end to end: hips yaw derived from a bone pair (never
## from one bone's own +Z, whose axes point down the bone), against the direction asked for.
##
## Only Vector3/float cross the JVM boundary here on purpose: MovementState/CombatState instances
## built in GDScript do not marshal, which is what stopped probe_locomotion_blend.gd.

const CHAR := "res://src/main/resources/com/openworld/character/Character.tscn"

func _find(n: Node, nm: String) -> Node:
	if n.name == nm:
		return n
	for c in n.get_children():
		var r: Node = _find(c, nm)
		if r != null:
			return r
	return null

func _find_type(n: Node, t) -> Node:
	if is_instance_of(n, t):
		return n
	for c in n.get_children():
		var r: Node = _find_type(c, t)
		if r != null:
			return r
	return null

func _yaw(v: Vector3) -> float:
	return rad_to_deg(atan2(v.x, v.z))

func _delta(a: float, b: float) -> float:
	var d: float = a - b
	while d > 180.0:
		d -= 360.0
	while d < -180.0:
		d += 360.0
	return d

func _initialize() -> void:
	var packed: PackedScene = load(CHAR) as PackedScene
	if packed == null:
		print("FAIL: cannot load ", CHAR)
		quit(1)
		return
	var body: Node3D = packed.instantiate()
	# Both world scenes place the Player at a -90 deg yaw, and MovementController captures
	# playerInitRotation in _ready -- so set it BEFORE entering the tree or the compensation
	# under test never sees the rotation it exists to cancel.
	body.rotation.y = deg_to_rad(-90.0)
	root.add_child(body)
	for i in 6:
		await physics_frame

	var mc: Node = _find(body, "MovementController")
	var skel: Skeleton3D = _find_type(body, Skeleton3D) as Skeleton3D
	if mc == null or skel == null:
		print("FAIL: mc=", mc, " skel=", skel)
		quit(1)
		return

	var hl := BoneAttachment3D.new(); hl.bone_name = "thigh_l"; skel.add_child(hl)
	var hr := BoneAttachment3D.new(); hr.bone_name = "thigh_r"; skel.add_child(hr)
	await physics_frame

	var mesh_root: Node3D = _find(body, "MeshRoot") as Node3D
	print("body.rotation.y      = %.1f deg" % rad_to_deg(body.rotation.y))
	print("MeshRoot found       = %s" % (mesh_root != null))
	print("")
	print("Out of combat, MovementController faces the mesh down the movement direction.")
	print("%-22s %10s %10s %10s   %s" % ["world dir asked for", "want yaw", "hips yaw", "meshRoot y", "error"])

	var dirs := [["+X  (east)", Vector3(1, 0, 0)], ["-X  (west)", Vector3(-1, 0, 0)],
				 ["+Z  (south)", Vector3(0, 0, 1)], ["-Z  (north)", Vector3(0, 0, -1)]]
	var worst: float = 0.0
	for d in dirs:
		var v: Vector3 = d[1]
		mc.call("on_set_movement_direction", v)
		for i in 40:
			await physics_frame
		var axis: Vector3 = hl.global_transform.origin - hr.global_transform.origin
		axis.y = 0.0
		var fwd: Vector3 = axis.normalized().cross(Vector3.UP).normalized()
		var want: float = _yaw(v)
		var got: float = _yaw(fwd)
		var mry: float = rad_to_deg(mesh_root.rotation.y) if mesh_root != null else NAN
		var err: float = _delta(got, want)
		if absf(err) > absf(worst):
			worst = err
		print("%-22s %10.1f %10.1f %10.1f   %+8.1f" % [d[0], want, got, mry, err])
	print("")
	print("worst facing error = %+.1f deg" % worst)
	quit(0 if absf(worst) < 15.0 else 1)

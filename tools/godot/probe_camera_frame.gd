extends SceneTree
## Where does the PLAYER camera rig actually point, and does PlayerController's movement frame
## agree with it?
##
##   godot --headless --script tools/godot/probe_camera_frame.gd
##
## The whole player facing chain is three numbers that must compose:
##   * the camera's world forward yaw  C   (what the human sees as "forward")
##   * PlayerController's movement frame  phi = TPSCameraController.get_current_yaw() + body.rotation.y
##     -- a WASD "forward" (local -Z) lands at yaw phi + 180, so a correct rig has C == phi + 180
##   * MovementController's mesh frame: it writes meshRoot.rotation.y = worldYaw - playerInitRotation,
##     where playerInitRotation is the body's yaw AS CAPTURED IN _ready(). The rendered facing is
##     body.rotation.y + meshRoot.rotation.y + 180, so the mesh is off by
##     (body.rotation.y - playerInitRotation) -- zero only while the body's yaw has not changed
##     since _ready().
##
## Both middle terms bake the body's rotation at a single instant, so both break the same way:
## rotate the body AFTER add_child() and the rig/mesh keep the pre-rotation frame. This probe
## runs the same body both ways and prints the error for each.

const PLAYER := "res://src/main/resources/com/openworld/character/Player.tscn"

func _find(n: Node, nm: String) -> Node:
	if n.name == nm:
		return n
	for c in n.get_children():
		var r: Node = _find(c, nm)
		if r != null:
			return r
	return null

func _yaw(v: Vector3) -> float:
	return rad_to_deg(atan2(v.x, v.z))

func _wrap(d: float) -> float:
	while d > 180.0:
		d -= 360.0
	while d < -180.0:
		d += 360.0
	return d

func _report(label: String, body: Node3D) -> void:
	var rig: Node3D = _find(body, "TPSCameraController") as Node3D
	var yaw_node: Node3D = rig.get_node("Yaw") as Node3D
	var cam: Node3D = _find(body, "ActiveCamera") as Node3D
	var mesh_root: Node3D = _find(body, "MeshRoot") as Node3D

	var body_yaw := rad_to_deg(body.rotation.y)
	var rig_yaw := rad_to_deg(rig.global_basis.get_euler().y)
	# The rig's world basis is identity (W3), so this LOCAL yaw is also the world view yaw.
	var cam_local_yaw := rad_to_deg(yaw_node.rotation.y)
	# The camera looks down its own -Z.
	var cam_fwd := -cam.global_basis.z
	cam_fwd.y = 0.0
	var c := _yaw(cam_fwd)

	# What PlayerController builds: it takes the frame off ActiveCamera's own world basis, so a
	# WASD "forward" goes straight down the camera's flattened forward. (It used to rebuild the
	# frame as cam_local_yaw + body.rotation.y, which is what case B below caught: that form is
	# only right while the body's yaw has not changed since TPSCameraController's _ready(). The
	# column that printed that old formula is gone with AIM_PLAN.md W3 -- the rig's world basis is
	# now stated outright every frame, so there is no frozen frame left for it to document.)
	var d_fwd := c

	# What MovementController renders for a given world target yaw: mesh_root holds
	# (target - playerInitRotation); rendered facing = body + mesh + 180.
	var mesh_yaw := rad_to_deg(mesh_root.rotation.y)
	var facing := _wrap(body_yaw + mesh_yaw + 180.0)
	# MovementController was asked for BACK (+Z, yaw 0) once in Character._ready.
	var facing_err := _wrap(facing - 0.0)

	print("--- ", label)
	print("   body.rotation.y      : %8.2f" % body_yaw)
	print("   rig root GLOBAL yaw  : %8.2f   (should be 0.00 -- W3 states it every frame)" % rig_yaw)
	print("   Yaw node yaw (world) : %8.2f" % cam_local_yaw)
	print("   camera forward   C   : %8.2f" % c)
	print("   move frame (current)  : %8.2f   <- WASD 'forward' goes here" % d_fwd)
	print("   MOVE ERROR (C-fwd)   : %8.2f   <<< 0 = W walks where the camera looks" % _wrap(c - d_fwd))
	print("   meshRoot.rotation.y  : %8.2f" % mesh_yaw)
	print("   rendered facing      : %8.2f   (asked for BACK = 0.00)" % facing)
	print("   FACE ERROR           : %8.2f   <<< 0 = the mesh faces what it was told to" % facing_err)

func _initialize() -> void:
	var packed: PackedScene = load(PLAYER) as PackedScene
	if packed == null:
		print("FAIL: cannot load ", PLAYER)
		quit(1)
		return

	var world := Node3D.new()
	root.add_child(world)
	# Let the tree start running: during _initialize() add_child does NOT propagate _ready
	# immediately, which hides the very ordering this probe is about.
	for i in range(3):
		await physics_frame

	# Case A: the world scenes -- the transform is part of the scene data, so it is already
	# applied when _ready() runs.
	var a := packed.instantiate() as Node3D
	a.rotation = Vector3(0, deg_to_rad(90.0), 0)
	a.position = Vector3(0, 1, 0)
	world.add_child(a)

	# Case B: AimDebugHost -- add_child first (which runs every _ready), rotate after.
	var b := packed.instantiate() as Node3D
	world.add_child(b)
	b.position = Vector3(20, 1, 0)
	b.rotation = Vector3(0, deg_to_rad(90.0), 0)

	for i in range(30):
		await physics_frame

	print("")
	print("=== camera / movement / mesh frame composition ===")
	_report("A  rotate BEFORE add_child  (World.tscn, DebugWorld.tscn)", a)
	_report("B  rotate AFTER  add_child  (AimDebugHost.spawnPlayer)", b)
	print("")
	quit(0)

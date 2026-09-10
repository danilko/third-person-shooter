extends SceneTree
## Is the FPS view registered, centred, steady, and does the head come back?
##
##   stdbuf -oL /data/danilko/bin/Godot_v4.7.2-stable_linux.x86_64 --headless --path . \
##       --script tools/godot/probe_fps_camera.gd
##
## Four questions the driven gate (AimDebugAuto) cannot ask, because it never switches view:
##
##  1. is `is_fps_mode` a REGISTERED property? It was a plain Java field, which reads like a
##     working one from Java and like nothing at all from here -- get() returned <null> and set()
##     did nothing, so a probe that switched to FPS silently measured the TPS camera.
##  2. is the eye point on the character's CENTRE LINE? The shoulder offset is the TPS boom's
##     framing device; a first-person camera sitting 0.5 m off to one side is the bug.
##  3. is it STEADY? The mount rides a BoneAttachment3D on neck_01 -- head bob, weapon one-shots
##     and the shoulder-aim modifier all move it. This compares the raw bone against what the
##     camera actually did, in MeshRoot space so running is not counted as shake.
##  4. does the HEAD come back? Head visibility used to be latched at the view toggle, so a
##     player who entered a vehicle in FPS drove a headless character.

const PLAYER := "res://src/main/resources/com/openworld/character/Player.tscn"
const SAMPLES := 90

func _find(n: Node, nm: String) -> Node:
	if n.name == nm:
		return n
	for c in n.get_children():
		var r: Node = _find(c, nm)
		if r != null:
			return r
	return null

func _floor(world: Node3D) -> void:
	var b := StaticBody3D.new()
	var cs := CollisionShape3D.new()
	var sh := BoxShape3D.new()
	sh.size = Vector3(200, 2, 200)
	cs.shape = sh
	b.add_child(cs)
	b.position = Vector3(0, -1, 0)
	world.add_child(b)

## Peak-to-peak span and mean per-frame jerk (|second difference|) of a sample series.
func _stats(pts: Array) -> Dictionary:
	var lo := Vector3(1e9, 1e9, 1e9)
	var hi := Vector3(-1e9, -1e9, -1e9)
	for p in pts:
		lo = lo.min(p)
		hi = hi.max(p)
	var jerk := 0.0
	for i in range(2, pts.size()):
		jerk += ((pts[i] - pts[i - 1]) - (pts[i - 1] - pts[i - 2])).length()
	return {
		"span": (hi - lo).length(),
		"jerk": jerk / max(1, pts.size() - 2),
	}

func _initialize() -> void:
	var world := Node3D.new()
	root.add_child(world)
	_floor(world)
	for i in range(3):
		await physics_frame

	var p := (load(PLAYER) as PackedScene).instantiate() as Node3D
	world.add_child(p)
	p.position = Vector3(0, 1.2, 0)
	for i in range(40):
		await physics_frame

	var fails := 0
	print("")
	print("=== FPS view ===")

	# 1. registration -----------------------------------------------------------------
	var reg = p.get("is_fps_mode")
	print("   is_fps_mode get()   : %s" % [reg])
	if reg == null:
		print("   FAIL: is_fps_mode is not a registered property")
		fails += 1
	else:
		print("   PASS: is_fps_mode is registered")

	var mesh_root: Node3D = _find(p, "MeshRoot") as Node3D
	var mount: Node3D = _find(p, "MarkerFPSCamera") as Node3D
	var cam: Node3D = _find(p, "ActiveCamera") as Node3D
	var head: Node3D = _find(p, "head") as Node3D
	if mesh_root == null or mount == null or cam == null:
		print("   FAIL: missing MeshRoot/MarkerFPSCamera/ActiveCamera")
		quit(1)
		return

	# 4a. head visible in TPS ---------------------------------------------------------
	print("   head visible, TPS   : %s   (expect true)" % [head.visible])
	if not head.visible:
		fails += 1

	# switch to FPS and walk ----------------------------------------------------------
	p.set("is_fps_mode", true)
	Input.action_press("aim")
	Input.action_press("forward")
	for i in range(20):
		await physics_frame

	var raw: Array = []
	var eye: Array = []
	# Swing the view back and forth while sampling. In combat neck_01 is one of
	# ShoulderAimModifier's driven bones, so turning the head is the LARGEST thing that moves the
	# mount -- larger than footstep bob -- and it is the motion a bone-parented FPS camera is
	# worst at. Sampling a body that only walks in a straight line would miss it entirely.
	for i in range(SAMPLES):
		var mm := InputEventMouseMotion.new()
		mm.relative = Vector2(90.0 if (i / 12) % 2 == 0 else -90.0, 0.0)
		Input.parse_input_event(mm)
		await physics_frame
		raw.append(mesh_root.to_local(mount.global_position))
		eye.append(mesh_root.to_local(cam.global_position))
	Input.action_release("forward")
	Input.action_release("aim")

	var rs := _stats(raw)
	var es := _stats(eye)

	# 2. centre line ------------------------------------------------------------------
	var lateral := 0.0
	for v in eye:
		lateral = max(lateral, abs(v.x))
	print("   eye lateral offset  : %6.3f m   (expect < 0.10 -- no shoulder in FPS)" % lateral)
	if lateral > 0.10:
		print("   FAIL: the FPS eye is off the centre line")
		fails += 1

	# 3. steadiness -------------------------------------------------------------------
	print("   bone  span %6.4f m  jerk %7.5f m/frame^2   (the raw neck mount)" % [rs.span, rs.jerk])
	print("   eye   span %6.4f m  jerk %7.5f m/frame^2   (what the camera did)" % [es.span, es.jerk])
	if rs.jerk > 1e-6:
		print("   jerk reduction      : %5.1fx" % (rs.jerk / max(es.jerk, 1e-9)))
	if es.jerk > rs.jerk:
		print("   FAIL: the filter made the camera SHAKIER than the bone")
		fails += 1
	elif es.span > rs.span:
		print("   FAIL: the eye travels further than the bone it follows")
		fails += 1
	else:
		print("   PASS: the eye is steadier than the bone")

	# 4b. head hidden in FPS, and back in a vehicle -----------------------------------
	print("   head visible, FPS   : %s   (expect false)" % [head.visible])
	if head.visible:
		fails += 1
	# A PASSENGER keeps their own camera when they sit down -- nothing switches it -- so in FPS
	# they are still behind their own eyes and the head stays off. `current_vehicle_node` alone no
	# longer decides anything: the question is whose camera is on screen, and only the DRIVER's is
	# the carrier's. That is why this sets the vehicle node WITHOUT `vehicle_driver`, which is
	# exactly what riding shotgun looks like.
	#
	# The driver's three views (cockpit hides the head, bonnet and TPS put it back) are asserted
	# against a real Vehicle in tools/godot/probe_vehicle_views.gd -- a fake carrier cannot answer
	# "which of the carrier's views is up", and that is the whole question now.
	p.set("current_vehicle_node", p)
	p.call("refresh_head_visibility")
	print("   head visible, riding: %s   (expect false -- a passenger keeps their own camera)" % [head.visible])
	if head.visible:
		print("   FAIL: a passenger in FPS should still be behind their own eyes")
		fails += 1
	p.set("current_vehicle_node", null)

	print("")
	print("=== %s (%d failures) ===" % ["PASS" if fails == 0 else "FAIL", fails])
	quit(0 if fails == 0 else 1)

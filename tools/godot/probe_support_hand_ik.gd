extends SceneTree
## Does the support hand actually land on the weapon's foregrip?
##
##   godot --headless --path . --script tools/godot/probe_support_hand_ik.gd
##
## `SupportHandIKModifier` places `hand_l` on the held weapon's `SupportPoint`. Every way it can
## fail is SILENT -- no weapon controller resolved, no held weapon, no marker on the weapon, a bone
## name that does not exist, a `weight` of 0 -- and each of them returns early leaving the arm
## exactly as the clip authored it. So the measurement has to be "the hand MOVED to the marker", and
## a before/after is the only way to tell that from "the clip already had it about there".
##
## `hand_l` is read through a BoneAttachment3D, never `get_bone_global_pose`: a skeleton modifier
## writes into the FINAL pose only, so the accessor would report the pre-IK arm and a perfectly
## working solve would measure as inert (CLAUDE.md, "How to measure this rig without fooling
## yourself" -- this is precisely the trap that cost a wrong conclusion there).

const PLAYER := "res://src/main/resources/com/openworld/character/Player.tscn"
## The solve clamps to the arm's own length, so a foregrip inside reach should be hit closely.
const HIT_TOLERANCE := 0.05

var fails := 0

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
		var r = _find_type(c, t)
		if r != null:
			return r
	return null

func _floor(parent: Node) -> void:
	var b := StaticBody3D.new()
	var cs := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(40, 1, 40)
	cs.shape = box
	b.add_child(cs)
	b.position = Vector3(0, -0.5, 0)
	parent.add_child(b)

func _tick(n: int) -> void:
	for i in range(n):
		await physics_frame

func _check(label: String, ok: bool, detail: String) -> void:
	if not ok:
		fails += 1
	print("  %s  %-34s %s" % ["PASS" if ok else "FAIL", label, detail])

func _initialize() -> void:
	var world := Node3D.new()
	root.add_child(world)
	_floor(world)
	await _tick(3)

	var p: Node3D = (load(PLAYER) as PackedScene).instantiate() as Node3D
	world.add_child(p)
	p.position = Vector3(0, 1.2, 0)
	await _tick(40)

	var sk: Skeleton3D = _find_type(p, Skeleton3D) as Skeleton3D
	var ik: Node = _find(p, "SupportHandIKModifier")
	if sk == null or ik == null:
		print("FAIL: skeleton=%s modifier=%s" % [sk != null, ik != null])
		quit(1)
		return

	# Read the FINAL pose: a BoneAttachment3D follows the skinned result, the pose accessors do not.
	var probe := BoneAttachment3D.new()
	sk.add_child(probe)
	probe.bone_name = "hand_l"
	await _tick(5)

	# The modifier resolves its target through WeaponController.getCurrentWeaponItem(), so the test
	# has to go through a HELD weapon rather than a loose one -- that lookup is half of what can
	# silently fail. The character spawns holding `Fist`, so the support point is given to that:
	# it exercises controller resolution, held-weapon lookup and marker lookup, and leaves the
	# grip-alignment question to probe_weapon_sockets.gd where it belongs.
	var held: Node3D = _find(p, "Fist") as Node3D
	if held == null:
		print("FAIL: nothing held (expected the default Fist)")
		quit(1)
		return

	# BEFORE: no SupportPoint anywhere, so the modifier returns early and the arm is the clip's.
	await _tick(20)
	var before: Vector3 = probe.global_position

	# A point 10 cm off the held hand: unambiguously within the other arm's reach, so a failure to
	# arrive is the solve failing rather than the target being unreachable.
	var grip := Marker3D.new()
	grip.name = "SupportPoint"
	held.add_child(grip)
	grip.position = Vector3(0, 0, -0.10)
	await _tick(30)

	var after: Vector3 = probe.global_position
	var before_err: float = before.distance_to(grip.global_position)
	var after_err: float = after.distance_to(grip.global_position)

	print("")
	print("=== support hand vs the held weapon's support point ===")
	print("  clip alone : %.3f m from the SupportPoint" % before_err)
	print("  with IK    : %.3f m" % after_err)
	_check("hand reaches the support point", after_err < HIT_TOLERANCE, "off by %.3f m" % after_err)
	_check("the IK actually moved the arm", before.distance_to(after) > 0.01,
		"hand moved %.3f m" % before.distance_to(after))

	print("")
	print("PASS (0 failures)" if fails == 0 else "FAIL (%d failures)" % fails)
	quit(1 if fails > 0 else 0)

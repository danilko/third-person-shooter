extends SceneTree
## Does a weapon's own GripPoint actually place it, and does a weapon without one still behave?
##
##   godot --headless --path . --script tools/godot/probe_weapon_sockets.gd
##
## `WeaponController.reparentWeapon` asks the weapon how it sits (`WeaponItem.alignmentFor`) instead
## of zeroing its transform. The failure mode is SILENCE: if the grip marker cannot be resolved --
## renamed, missing, or the export left blank -- `alignmentFor` returns identity, the weapon lands
## exactly where it used to, and nothing anywhere says the feature did not run. So both halves are
## measured: a weapon WITH a grip point must move its grip onto the socket, and one WITHOUT must
## still put its ORIGIN there.
##
## The socket is a bare Node3D with a deliberately awkward transform. That matters: the alignment is
## applied in SOCKET space, so a socket that is only translated would let a wrong-but-plausible
## implementation (e.g. one that negates in world space) pass.
##
## It also asserts the thing that made this probe hard to write in the first place: a weapon under a
## socket HOLDS the transform it is given. It used to be a RigidBody3D, so the physics server owned
## that transform and gravity had moved it 5.4 mm by the next frame -- which reads exactly like a
## small alignment error rather than like free fall, and had to be worked around with
## `set("freeze", true)`. A held weapon is a plain Node3D now (see item/PickupBody.java), so there
## is nothing to freeze and the error is 0.

const ATL4 := "res://src/main/resources/com/openworld/weapon/ATL4.tscn"   # has a GripPoint
const AR4  := "res://src/main/resources/com/openworld/weapon/AR4.tscn"    # has none

var fails := 0

func _check(label: String, ok: bool, detail: String) -> void:
	if not ok:
		fails += 1
	print("  %s  %-38s %s" % ["PASS" if ok else "FAIL", label, detail])

func _initialize() -> void:
	var world := Node3D.new()
	root.add_child(world)

	# An off-axis, rotated socket -- a hand is never axis-aligned.
	var socket := Node3D.new()
	world.add_child(socket)
	socket.global_transform = Transform3D(Basis.from_euler(Vector3(0.4, -1.1, 0.7)),
										  Vector3(1.5, 1.2, -0.8))
	await physics_frame

	print("")
	print("=== weapon-owned grip alignment ===")

	# 1. A weapon that declares a GripPoint must land that POINT on the socket.
	var launcher: Node3D = (load(ATL4) as PackedScene).instantiate() as Node3D
	socket.add_child(launcher)
	# What WeaponController does before it reparents onto a socket. Without it the item is loose in
	# the world as far as it can tell, and correctly wraps itself in a PickupBody -- see
	# `Pickup.bornHeld`. Stating the transition is what the game does; it is not probe scaffolding.
	launcher.call("on_picked_up")
	launcher.show()
	_check("a weapon is not a physics body", not (launcher is PhysicsBody3D),
		"root class is %s" % launcher.get_class())
	await physics_frame
	var grip: Node3D = launcher.get_node_or_null("GripPoint") as Node3D
	if grip == null:
		print("FAIL: ATL4 has no GripPoint child")
		quit(1)
		return
	launcher.transform = launcher.call("alignment_for", false)
	await physics_frame
	var grip_err: float = grip.global_position.distance_to(socket.global_position)
	var origin_moved: float = launcher.global_position.distance_to(socket.global_position)
	_check("grip point lands ON the socket", grip_err < 0.001, "off by %.4f m" % grip_err)
	# ...and the weapon's ORIGIN must therefore NOT be on the socket, or the alignment did nothing.
	_check("origin is offset by the grip", origin_moved > 0.05,
		"origin %.3f m from socket (the grip offset's length)" % origin_moved)

	# 2. A weapon that declares none keeps the historical behaviour exactly: origin on the socket.
	var rifle: Node3D = (load(AR4) as PackedScene).instantiate() as Node3D
	socket.add_child(rifle)
	rifle.call("on_picked_up")
	rifle.show()
	await physics_frame
	rifle.transform = rifle.call("alignment_for", false)
	await physics_frame
	var rifle_err: float = rifle.global_position.distance_to(socket.global_position)
	_check("no grip point -> origin on the socket", rifle_err < 0.001,
		"off by %.4f m (unchanged behaviour)" % rifle_err)

	# 3. Nothing moves a socketed weapon. This is the free-fall symptom, measured directly: hold it
	#    for a second of physics and the error must be EXACTLY zero, not merely small.
	var held := launcher.global_transform
	for i in range(60):
		await physics_frame
	var drift: float = launcher.global_position.distance_to(held.origin)
	_check("a socketed weapon does not drift", drift == 0.0, "moved %.6f m over 60 physics frames" % drift)

	print("")
	print("PASS (0 failures)" if fails == 0 else "FAIL (%d failures)" % fails)
	quit(1 if fails > 0 else 0)

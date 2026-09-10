extends SceneTree
## Where is a SEATED driver's eye, relative to the seat anchor the vehicle pins them to?
##
##   godot --headless --script tools/godot/probe_cockpit_eye.gd
##
## `VehicleCameraController`'s cockpit view renders from `Seats/Seat0/CockpitCameraMount`, a
## Marker3D authored in Vehicle.tscn. Its height is the one number in that feature that cannot be
## derived -- a seated head's offset above the seat anchor is a fact about the character rig and
## its DriveCarrier clip -- so it is measured here rather than guessed, and the result is what the
## scene ships.
##
## The vehicle is not instantiated at all: `Vehicle.tryEnter` is not a registered method, so
## GDScript cannot seat anyone. It does not need to. The vehicle pins the occupant's ORIGIN to the
## seat marker (Vehicle._physicsProcess), so the offset being measured is exactly
## "head marker minus body origin, in the DriveCarrier stance" -- driven straight through the
## AnimationTree, which is engine-side and needs no JVM registration.

const PLAYER := "res://src/main/resources/com/openworld/character/Player.tscn"
const STANCES := ["Upright", "Crouch", "DriveCarrier"]

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
	box.size = Vector3(40, 1, 40)
	cs.shape = box
	b.add_child(cs)
	b.position = Vector3(0, -0.5, 0)
	parent.add_child(b)

func _initialize() -> void:
	var world := Node3D.new()
	root.add_child(world)
	_floor(world)
	# During _initialize() add_child does NOT propagate _ready immediately, and the character's
	# visuals (AnimationTree included) are instanced by Character._ready. Give the tree frames.
	for i in range(3):
		await physics_frame

	var body: Node3D = (load(PLAYER) as PackedScene).instantiate() as Node3D
	world.add_child(body)
	body.position = Vector3(0, 1.2, 0)
	for i in range(40):
		await physics_frame

	var tree: AnimationTree = _find(body, "AnimationTree") as AnimationTree
	if tree == null:
		print("FAIL: no AnimationTree under Player.tscn")
		quit(1)
		return

	var eye: Node3D = _find(body, "MarkerFPSCamera") as Node3D
	var neck: Node3D = _find(body, "NeckAttachment") as Node3D
	if eye == null or neck == null:
		print("FAIL: no MarkerFPSCamera / NeckAttachment under the character")
		quit(1)
		return

	for stance in STANCES:
		tree.set("parameters/StanceTransition/transition_request", stance)
		# The transition is a blend, not a snap: give it real frames to land on.
		for i in range(90):
			await physics_frame
		# AVERAGED over a full second of the clip, not read off one frame. The seated pose is a
		# 57-frame loop and the head moves within it, so a single sample is worth about +/-1 cm and
		# two runs disagree by more than the number is precise to -- which is exactly how a 0.809
		# measurement got "corrected" to 0.791 and back. The spread is printed so the noise is
		# visible rather than implied.
		var n := 60
		var sum := Vector3.ZERO
		var lo := INF
		var hi := -INF
		for i in range(n):
			var d: Vector3 = eye.global_position - body.global_position
			sum += d
			lo = min(lo, d.y)
			hi = max(hi, d.y)
			await physics_frame
		var d_eye: Vector3 = sum / float(n)
		var d_neck: Vector3 = neck.global_position - body.global_position
		print("%-13s eye  y=%+.3f (%.3f..%.3f)  fwd(-z)=%+.3f  x=%+.3f   |  neck y=%+.3f"
			% [stance, d_eye.y, lo, hi, -d_eye.z, d_eye.x, d_neck.y])

	print("")
	print("CockpitCameraMount (child of Seats/Seat0) takes the DriveCarrier row directly:")
	print("  origin = (x, eye.y, -fwd)   -- the seat's basis is the vehicle's, and both the")
	print("  character and the vehicle are -Z forward, so no conversion is involved.")
	quit(0)

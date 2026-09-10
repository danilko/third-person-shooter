extends SceneTree
## Measures where the character's body parts actually POINT, per stance, by driving the SHIPPED
## CharacterVisuals AnimationTree exactly as AnimationController does.
##
##   godot --headless --script tools/godot/probe_character_aim.gd
##
## Two rules this tool exists to honour:
##  * Read the FINAL bone transform via BoneAttachment3D, never get_bone_global_pose(): skeleton
##    modifiers (SpineAimModifier) write into the final pose only, so the accessor returns the
##    PRE-modifier value and a working modifier reads as inert.
##  * Derive "forward" from a PAIR of bones (the hip axis, the shoulder axis, heel->toe), never
##    from one bone's own +Z. A thigh's local axes point down the bone, so comparing raw bone
##    bases across the body compares nothing and hides a real hips-vs-shoulders yaw split.

const VISUALS := "res://src/main/resources/com/openworld/character/CharacterVisuals_GodotChan.tscn"

var skel: Skeleton3D
var tree: AnimationTree
var att := {}

func _find(n: Node, t) -> Node:
	if is_instance_of(n, t):
		return n
	for c in n.get_children():
		var r: Node = _find(c, t)
		if r != null:
			return r
	return null

func _attach(bone: String) -> void:
	var a := BoneAttachment3D.new()
	a.bone_name = bone
	skel.add_child(a)
	att[bone] = a

func _pos(bone: String) -> Vector3:
	return (att[bone] as BoneAttachment3D).global_transform.origin

## Forward implied by a left/right pair: perpendicular to the pair's axis, in the ground plane.
func _fwd_from_pair(left: String, right: String) -> Vector3:
	var axis: Vector3 = _pos(left) - _pos(right)
	axis.y = 0.0
	if axis.length() < 0.0001:
		return Vector3.ZERO
	return axis.normalized().cross(Vector3.UP).normalized()

func _yaw(v: Vector3) -> float:
	if v.length() < 0.0001:
		return NAN
	return rad_to_deg(atan2(v.x, v.z))

func _delta(a: float, b: float) -> float:
	var d: float = a - b
	while d > 180.0:
		d -= 360.0
	while d < -180.0:
		d += 360.0
	return d

func _initialize() -> void:
	var ps: PackedScene = load(VISUALS)
	var vis: Node3D = ps.instantiate()
	root.add_child(vis)
	skel = _find(vis, Skeleton3D) as Skeleton3D
	tree = _find(vis, AnimationTree) as AnimationTree
	var modifier: LookAtModifier3D = _find(skel, LookAtModifier3D) as LookAtModifier3D

	for b in ["thigh_l", "thigh_r", "calf_l", "calf_r", "foot_l", "foot_r", "ball_l", "ball_r",
			  "clavicle_l", "clavicle_r", "pelvis", "spine_01", "spine_03", "hand_r"]:
		_attach(b)

	var target := Node3D.new()
	root.add_child(target)
	# The mesh is -Z FORWARD (Godot convention; MovementController.aimYaw's atan2(-dx,-dz) is the
	# same fact). A target at +Z is BEHIND the character and makes the spine modifier twist the
	# chest ~180 deg to look backwards -- which is a real thing to test, but not the resting case.
	target.global_position = Vector3(0.0, 1.2, -10.0)
	if modifier != null:
		modifier.target_node = modifier.get_path_to(target)
	await process_frame

	tree.active = true
	tree.set("parameters/WeaponBlend/blend_amount", 1.0)
	tree.set("parameters/OnFloorBlend/blend_amount", 1.0)
	tree.set("parameters/CombatTransition/transition_request", "Combat")
	tree.set("parameters/NeckFront/blend_amount", 1.0)
	tree.set("parameters/WeaponAim/blend_position", 0.0)
	# NOTE: there is no per-stance aim branch. WeaponAim is ONE blendspace (aim_pistol/aim_rifle)
	# and WeaponBlend's filter takes only clavicles/arms/hands from it, so the aim pose is the same
	# in every stance by construction. This used to also set WeaponAimCrouch, WeaponAimCrawl and
	# AimStanceTransition, none of which exist -- AnimationTree.set() on an unknown parameter is
	# silently ignored, so the probe read as if it were switching something. (The blend does carry
	# aim_pistol_crouch-loop and three siblings, but over the filtered bones they are bit-identical
	# to the upright clip, so nothing is lost; check_character_anim's orphan_clips names them.)

	print("Mesh forward is -Z (yaw 180). Columns are world yaw in degrees; the last two are the")
	print("splits that matter: shoulders-vs-hips and feet-vs-hips. Both should be near 0.")
	print("%-8s %-7s %8s %8s %8s %8s %8s   %s" % ["stance", "move", "hips", "shldrs", "footR", "footL", "chest", "splits"])
	var moves := [["idle", Vector2(0, 0)], ["fwd", Vector2(0, 1)], ["back", Vector2(0, -1)],
				  ["left", Vector2(-1, 0)], ["right", Vector2(1, 0)],
				  ["fwd-L", Vector2(-1, 1)], ["fwd-R", Vector2(1, 1)]]
	for stance in ["Upright", "Crouch", "Crawl"]:
		tree.set("parameters/StanceTransition/transition_request", stance)
		# Every stance aims now (AIM_PLAN.md W1): Stance.spineAimMaxAngle is a real limit and only
		# a stance that must not aim at all sets it to 0. This used to hardcode "crawl does not".
		if modifier != null:
			modifier.active = true
		for mv in moves:
			tree.set("parameters/%sMovementBlend/blend_position" % stance, mv[1])
			for i in 14:
				await process_frame
			var hips: float = _yaw(_fwd_from_pair("thigh_l", "thigh_r"))
			var shld: float = _yaw(_fwd_from_pair("clavicle_l", "clavicle_r"))
			var fr: float = _yaw(_pos("ball_r") - _pos("foot_r"))
			var chest: float = _yaw((att["spine_03"] as BoneAttachment3D).global_transform.basis.z)
			print("%-8s %-6s hips=%7.1f shldrs=%7.1f chest=%7.1f footR=%7.1f | shldr-hips=%+7.1f  chest-hips=%+7.1f"
				% [stance, mv[0], hips, shld, chest, fr, _delta(shld, hips), _delta(chest, hips)])
	quit()

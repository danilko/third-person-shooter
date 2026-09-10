extends SceneTree
## A weapon has TWO states, and only one of them is a physics body.
##
##   godot --headless --path . --script tools/godot/probe_weapon_world_body.gd
##
## `WeaponItem extends Pickup extends RigidBody3D` made a weapon a body even in the hand. Every cost
## of that was in the codebase as its own workaround: equips deferred out of physics callbacks
## because reparenting a CollisionObject3D there is forbidden; socketless weapons left lying in the
## world scene because moving a frozen body leaves Jolt's body position stale and it later clips
## through the ground; `Pickup.pause()` freezing a body at all; and a probe measuring gravity's
## answer 5.4 mm later instead of the transform it had just set.
##
## The split is now `item.Pickup` (a Node3D -- meshes, markers, logic, identity, replication) riding
## inside `item.PickupBody` (a RigidBody3D -- shape, gravity, impulses) while it is in the world, and
## hanging off a bone socket with no physics at all while it is held.
##
## Every failure mode here is SILENT, which is why both directions are asserted rather than one:
## a body that is never built leaves a weapon hanging in mid-air where it was dropped, and a body
## that is never taken away leaves a rigid body riding a character's hand -- and neither says so.
##
## Section 4 is the one regression this refactor introduced and had to fix: `ImpactManager
## .resolveHitContext` walks UP from the collider, and the item is now the collider's CHILD, so
## shooting a dropped grenade stopped finding its Detonatable. That check is structural rather than
## a real bullet -- see its own comment for why a space query is misleading here.
##
## T1 is the weapon under test because it is the one that auto-collects (every other weapon sets
## `require_interact`, which needs a key press), and it is also the socketless case: it has no
## hold_socket, so it exercises the stow path that used to leave it in the world scene.

const PLAYER := "res://src/main/resources/com/openworld/character/Player.tscn"
const T1     := "res://src/main/resources/com/openworld/weapon/T1.tscn"
const THROWABLE_SLOT := 5

var fails := 0

func _check(label: String, ok: bool, detail: String) -> void:
	if not ok:
		fails += 1
	print("  %s  %-40s %s" % ["PASS" if ok else "FAIL", label, detail])

func _tick(n: int) -> void:
	for i in range(n):
		await physics_frame

func _find(n: Node, nm: String) -> Node:
	if n.name == nm:
		return n
	for c in n.get_children():
		var r: Node = _find(c, nm)
		if r != null:
			return r
	return null

## Every live PickupBody in the tree. Identified by its script, not its class or its name: the name
## is derived from the item and the class is the ordinary RigidBody3D every vehicle also is.
func _bodies(n: Node, out: Array = []) -> Array:
	var scr: Script = n.get_script() as Script
	if scr != null and scr.resource_path.ends_with("PickupBody.java"):
		out.append(n)
	for c in n.get_children():
		_bodies(c, out)
	return out

func _direct_shapes(n: Node) -> int:
	var c := 0
	for child in n.get_children():
		if child is CollisionShape3D:
			c += 1
	return c

## Is there a physics body anywhere between `n` and `stop`? That is the question a held weapon has
## to answer "no" to -- the socket chain is BoneAttachment3D/Marker3D/Node3D all the way up.
func _physics_between(n: Node, stop: Node) -> Node:
	var p: Node = n.get_parent()
	while p != null and p != stop:
		if p is PhysicsBody3D:
			return p
		p = p.get_parent()
	return null

func _is_under(n: Node, ancestor: Node) -> bool:
	var p: Node = n.get_parent()
	while p != null:
		if p == ancestor:
			return true
		p = p.get_parent()
	return false

func _floor(parent: Node) -> void:
	var b := StaticBody3D.new()
	var cs := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(60, 1, 60)
	cs.shape = box
	b.add_child(cs)
	b.position = Vector3(0, -0.5, 0)
	parent.add_child(b)

func _initialize() -> void:
	var world := Node3D.new()
	root.add_child(world)
	_floor(world)
	await _tick(3)

	var player: Node3D = (load(PLAYER) as PackedScene).instantiate() as Node3D
	world.add_child(player)
	player.position = Vector3(0, 1.2, 0)
	await _tick(40)

	var wc: Node = player.get_node_or_null("WeaponController")
	if wc == null:
		print("FAIL: Player has no WeaponController")
		quit(1)
		return

	# ── 1. In the world: the item rides a body, and the body is what falls ───────────────────
	print("")
	print("=== 1. world state -- the item rides a PickupBody ===")
	var item: Node3D = (load(T1) as PackedScene).instantiate() as Node3D
	world.add_child(item)
	item.global_position = Vector3(8, 4, 0)
	await _tick(4)

	var body: Node3D = item.get_parent() as Node3D
	_check("the item's parent is its world body", _bodies(world).size() == 1 and body == _bodies(world)[0],
		"parent is %s (%s)" % [body.name, body.get_class()])
	_check("the item itself is not a physics body", not (item is PhysicsBody3D),
		"item root class is %s" % item.get_class())
	_check("the shape moved onto the body", _direct_shapes(item) == 0 and _direct_shapes(body) == 1,
		"item holds %d shape(s), body holds %d" % [_direct_shapes(item), _direct_shapes(body)])

	var y0: float = body.global_position.y
	await _tick(10)
	var fell: float = y0 - body.global_position.y
	_check("the body falls", fell > 0.05, "dropped %.3f m in 10 frames" % fell)
	_check("the item rides the body", item.global_position.distance_to(body.global_position) < 0.0001,
		"item is %.6f m from the body" % item.global_position.distance_to(body.global_position))
	await _tick(60)
	_check("it comes to rest on the ground", abs(body.linear_velocity.y) < 0.2,
		"resting at y=%.3f, vy=%.3f" % [body.global_position.y, body.linear_velocity.y])

	# ── 2. Collected: no physics anywhere ───────────────────────────────────────────────────
	print("")
	print("=== 2. held state -- no physics body at all ===")
	body.linear_velocity = Vector3.ZERO
	body.global_position = player.global_position
	await _tick(30)

	_check("the item is carried by the player", _is_under(item, player),
		"parent chain reaches the player: %s" % _is_under(item, player))
	var stray: Node = _physics_between(item, player)
	_check("no physics body between item and character", stray == null,
		"found %s" % ("none" if stray == null else "%s (%s)" % [stray.name, stray.get_class()]))
	_check("its world body is gone", _bodies(world).size() == 0,
		"%d PickupBody left in the tree" % _bodies(world).size())
	# The socketless case: it used to be left lying in the world scene, hidden, at wherever it was
	# collected. It is stowed on the character now, so it moves with them.
	_check("a socketless weapon is stowed, not abandoned", not _is_under(item, world) or _is_under(item, player),
		"parent is %s" % item.get_parent().name)

	# ── 3. Dropped: the body is rebuilt and thrown ──────────────────────────────────────────
	print("")
	print("=== 3. dropped -- the body comes back ===")
	wc.call("on_set_weapon", THROWABLE_SLOT)
	await _tick(60)                      # the holster->draw transition (~0.45 s)
	wc.call("drop_current_weapon")
	await _tick(6)

	var dropped: Array = _bodies(world)
	_check("a world body was rebuilt", dropped.size() == 1, "%d PickupBody in the tree" % dropped.size())
	if dropped.size() == 1:
		var b2: Node3D = dropped[0] as Node3D
		_check("the item is back inside it", item.get_parent() == b2, "parent is %s" % item.get_parent().name)
		_check("the shape moved back onto it", _direct_shapes(item) == 0 and _direct_shapes(b2) == 1,
			"item holds %d shape(s), body holds %d" % [_direct_shapes(item), _direct_shapes(b2)])
		_check("it was thrown", b2.linear_velocity.length() > 0.5,
			"speed %.2f m/s" % b2.linear_velocity.length())

	# ── 4. A hit must reach the ITEM, which is now the collider's CHILD ─────────────────────
	#
	# `ImpactManager.resolveHitContext` walks UP from whatever the weapon's ray returned. What it
	# returns for a world item is the PickupBody, and the item hangs BELOW it -- so an un-redirected
	# walk sails straight past the ThrowableItem and a grenade shot in the world does nothing at
	# all, silently, because a bullet that finds no Detonatable just leaves a decal. The Character
	# AimRay's mask is 29, which includes the PICKUP layer, so this is a live path, not a dead one.
	#
	# This is a STRUCTURAL assertion, not an end-to-end shot: driving a real bullet at a dropped
	# grenade in a bare probe tree means fighting the camera boom's resting orientation (with no
	# input driving it the aim ray sits at identity, so "in front of the player" is over a metre off
	# the ray) and the melee cone's own range and surface-normal filters, neither of which is under
	# test. What is asserted instead is exactly the redirect's input and output -- plus the second
	# check, which is the one that matters: it proves walking UP alone finds nothing, so the
	# redirect is load-bearing rather than decorative.
	print("")
	print("=== 4. a hit reaches the item inside the body ===")
	var b3: Node3D = _bodies(world)[0] as Node3D if _bodies(world).size() == 1 else null
	if b3 != null:
		# What a weapon's ray returns is the CollisionObject3D that owns the shape it hit, and
		# section 3 has already established that the shape is on the body and not on the item -- so
		# the collider handed to resolveHitContext is b3. No space query is needed to say that, and
		# one is actively misleading here: every segment through a grenade resting at a character's
		# feet also passes through that character's ragdoll bones, which are on the HITBOX layer the
		# same mask includes.
		_check("the item's shape belongs to the body, so the body is what a ray returns",
			_direct_shapes(item) == 0 and _direct_shapes(b3) == 1,
			"item %d shape(s), body %d" % [_direct_shapes(item), _direct_shapes(b3)])
		# The regression itself: walking UP from that collider -- which is all resolveHitContext did
		# before the redirect -- never reaches the item, so a grenade shot in the world found no
		# Detonatable and did nothing at all, silently.
		var found_upward := false
		var up: Node = b3
		while up != null:
			if up == item:
				found_upward = true
			up = up.get_parent()
		_check("walking UP from it does NOT find the item", not found_upward,
			"item reachable upward: %s (this is why the redirect exists)" % found_upward)
		var carried: Node = b3.call("carried_item") as Node
		_check("the body hands over the item it carries", carried == item,
			"carried_item() -> %s" % ("null" if carried == null else carried.name))

	# ── 5. ...and its body must not be left standing ────────────────────────────────────────
	print("")
	print("=== 5. no orphan bodies ===")
	if is_instance_valid(item):
		item.queue_free()
	await _tick(6)
	_check("the item's body goes with it", _bodies(world).size() == 0,
		"%d PickupBody left standing" % _bodies(world).size())

	print("")
	print("PASS (0 failures)" if fails == 0 else "FAIL (%d failures)" % fails)
	quit(1 if fails > 0 else 0)

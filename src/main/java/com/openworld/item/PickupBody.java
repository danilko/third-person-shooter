package com.openworld.item;

import com.openworld.util.CollisionLayers;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node;
import godot.api.RigidBody3D;
import godot.core.MethodCallable;
import godot.core.StringName;

/**
 * The WORLD representation of a {@link Pickup} — and the only physics in the weapon system.
 *
 * <p>A weapon has two states, not one object in two moods. Lying in the world it is a rigid body
 * that falls, rests on ground, is shoved by explosions and can be thrown; held, it is a plain
 * {@code Node3D} hanging off a bone socket with no physics presence at all. {@code WeaponItem}
 * used to be BOTH — {@code extends Pickup extends RigidBody3D} — so a weapon in a character's hand
 * was still a body in the physics space, and every consequence of that was in the codebase as a
 * separate workaround rather than as one cause:
 *
 * <ul>
 *   <li>reparenting a {@code CollisionObject3D} is forbidden inside a physics callback, so every
 *       equip had to be queued and drained in {@code _process};</li>
 *   <li>a frozen body that is moved leaves Jolt's body position stale and the item later clips
 *       through the ground, so weapons with no holster socket were left lying in the world scene
 *       (hidden) instead of being stowed on the character;</li>
 *   <li>{@code Pickup.pause()} had to freeze a body at all;</li>
 *   <li>the physics server owned a held weapon's transform, so a probe that set it measured
 *       gravity's answer a frame later — 5.4 mm, which reads exactly like a small alignment error
 *       rather than like free fall.</li>
 * </ul>
 *
 * <p>The item rides INSIDE this body (its only {@code Pickup} child, at identity), and the item's
 * authored {@code CollisionShape3D} children are handed up to it while it exists — a shape only
 * registers with the {@code CollisionObject3D} it is a DIRECT child of, so the shape has to travel.
 * The weapon scene stays the one place a weapon's shape is authored; this node just borrows it.
 *
 * <p>Created and freed by {@link Pickup#placeInWorld} / {@link Pickup#detachWorldBody}. It also
 * frees ITSELF once no item is left inside it, which is what covers the paths that free an item
 * directly ({@code queueFree} on a fully-absorbed throwable stack, a reconcile discard): those
 * would otherwise leave an empty body standing in the world.
 */
@Script(className = "PickupBody")
public class PickupBody extends RigidBody3D {

  @Register
  @Override
  public void _ready() {
    setCollisionLayer(CollisionLayers.PICKUP);
    setCollisionMask(CollisionLayers.WORLD);
    connect(new StringName("child_exiting_tree"),
        MethodCallable.createUnsafe(this, "on_child_exiting"));
  }

  /**
   * The item has left (picked up, or freed outright). Deferred because this fires DURING the
   * child's removal — freeing the body here would take the departing item with it.
   */
  @Register
  public void onChildExiting(Node child) {
    callDeferred(new StringName("free_if_empty"));
  }

  /**
   * The item this body carries, or null. A body STANDS IN for its item: the item is the body's
   * child, so anything that resolves a collider by walking UP the parent chain (a bullet's
   * {@code ImpactManager.resolveHitContext}) would walk straight past it and find nothing.
   *
   * <p>Registered so the redirect can be asserted from a probe, and so a scene can ask.
   */
  @Register
  public Pickup carriedItem() {
    for (Node child : getChildren()) {
      if (child instanceof Pickup p) return p;
    }
    return null;
  }

  /** Frees this body once it holds no item. A no-op while it is already being freed. */
  @Register
  public void freeIfEmpty() {
    if (isQueuedForDeletion()) return;
    if (carriedItem() != null) return;
    queueFree();
  }
}

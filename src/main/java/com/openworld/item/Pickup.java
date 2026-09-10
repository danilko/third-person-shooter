package com.openworld.item;

import com.openworld.control.Controllable;
import com.openworld.character.Health;
import com.openworld.game.EventBus;
import com.openworld.net.NetworkManager;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Area3D;
import godot.api.CollisionShape3D;
import godot.api.Input;
import godot.api.Node;
import godot.api.Node3D;
import godot.core.NodePath;
import godot.core.StringName;
import godot.core.Transform3D;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;
import java.util.List;
import com.openworld.character.CharacterInfo;
import com.openworld.game.GameManager;
import com.openworld.net.NetworkController;
import com.openworld.weapon.WeaponController;
import com.openworld.weapon.WeaponItem;

/**
 * A collectable ITEM. Not a physics body — see {@link PickupBody}, which is.
 *
 * An item has two states and they are different SHAPES, not one object with physics turned
 * down. Lying in the world it rides inside a {@code PickupBody} (a RigidBody3D that owns the
 * collision shape, gravity and impulses); held, it is a plain Node3D under a bone socket with no
 * physics presence at all. This class owns that transition — {@link #placeInWorld} builds the body,
 * {@link #detachWorldBody} takes it away — so no caller has to reason about a body it cannot see.
 *
 * Scene setup:
 *   Pickup (Node3D + subclass script)      ← the item: meshes, markers, logic
 *     CollisionShape3D        ← the WORLD body's shape; lent to PickupBody while in the world
 *     PickupArea (Area3D)     ← character detection    (layer 0, mask layer 2)
 *       CollisionShape3D      ← detection volume
 *   Connect: PickupArea.body_entered → on_body_entered
 *   Connect: PickupArea.body_exited  → on_body_exited
 *
 * A scene-placed item wraps ITSELF in a body on the first idle frame ({@link #attachWorldBody});
 * one authored inside a character (the Fist under a weapon socket) does not — see
 * {@link #bornHeld()}.
 *
 * Post-pickup behaviour:
 *   removeOnPickup = true   → queue_free()  (one-shot consumable: health, key)
 *   pauseOnPickup  = true   → drop the body + hide + disable area; resume via resumeFromPause()
 *   neither                 → stays active  (permanent station)
 *
 * Interaction modes:
 *   requireInteract = false → auto-pickup on body_entered  (default)
 *   requireInteract = true  → player must press "interact" action while in range;
 *                             EventBus.pickupInteractChanged fires to show/hide HUD prompt
 *
 * Subclasses override onCharacterEntered(Node) to apply the pickup effect and
 * getInteractLabel() to supply the HUD prompt text.
 * Re-declare @Register onBodyEntered calling super so the method appears
 * in the subclass .gdj for scene signal connections.
 */
@Script(className = "Pickup")
public class Pickup extends Node3D {

  protected static final NodePath WEAPON_CONTROLLER_PATH = new NodePath("WeaponController");
  private static final String PICKUP_AREA = "PickupArea";

  @Export public boolean removeOnPickup  = false;
  @Export public boolean pauseOnPickup   = false;
  @Export public boolean requireInteract = false;

  /**
   * Seconds the pickup ignores all body_entered events after being returned to the
   * world (e.g. after a character drops it). Prevents the dropping character from
   * immediately re-acquiring the item. Industry standard: 0.3–0.5 s.
   */
  @Export public float pickupCooldownAfterDrop = 0.5f;

  /**
   * Stable replication identity, mirroring CharacterInfo.characterId. Scene-placed
   * pickups derive it from their scene path in _ready() — identical on every peer
   * because all peers load the same World.tscn. Runtime-spawned pickups (drops) get a
   * UUID assigned by the originating peer's drop event before any peer references it.
   */
  @Export public String pickupId = "";

  /**
   * Continuous collision detection for this item's world body. A small, light item thrown hard —
   * a grenade — can tunnel through a thin floor in one step without it; a rifle dropped at the
   * player's feet does not need the cost. Authored on the ITEM because it is a fact about the item,
   * and applied to the {@link PickupBody} each time one is built.
   */
  @Export public boolean worldBodyContinuousCd = false;

  /** Group every pickup joins in _ready() — replication handlers resolve pickupId through it. */
  public static final String PICKUPS_GROUP = "pickups";

  private final List<Node3D> overlappingBodies = new ArrayList<>();
  /** True while held in a character's inventory; blocks body_entered re-triggering. */
  protected boolean equipped = false;
  private float pickupCooldown = 0f;
  private EventBus eventBus;
  /**
   * Networked host only: a collector queued in a body_entered signal, resolved in _process
   * (a safe, non-signal context) so the equip is SYNCHRONOUS and the host broadcasts the
   * resolved outcome rather than a pre-equip guess. See {@link #collectBy}.
   */
  private Node pendingCollector;
  /**
   * The RigidBody3D this item rides while it is lying in the world; null while held. The item is
   * this body's child at identity, and the item's authored CollisionShape3D children are lent to
   * it for as long as it exists (a shape only registers with the CollisionObject3D it is a DIRECT
   * child of). Never read it raw — {@link #worldBody()} nulls a freed one.
   */
  private PickupBody worldBody;

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  /** Subclass overrides MUST call super._ready() or the pickup never registers for replication. */
  @Register
  @Override
  public void _ready() {
    addToGroup(new StringName(PICKUPS_GROUP));
    // NodePath.toString() returns "NodePath(<subnames>)" — empty for plain paths, so every
    // pickup would share the literal id "NodePath()". getPath() (the path property) is the
    // actual path string, identical on every peer for world-scene nodes.
    if (pickupId.isEmpty()) pickupId = getPath().getPath();
    // A scene-placed world item gets its body on the next idle frame. Deferred because _ready runs
    // while the scene is still being built (reparenting into a body we just created is not a thing
    // to do mid-construction), and because the id above must be read from the AUTHORED path —
    // wrapping first would change it, and every peer has to derive the same string.
    if (!bornHeld()) callDeferred(new StringName("attach_world_body"));
  }

  // ── World body: the item's physics, which exists only while it is in the world ──────────

  /** This item's world body, or null when it is held (or when a previous one has been freed). */
  public PickupBody worldBody() {
    if (worldBody != null && !GD.isInstanceValid(worldBody)) worldBody = null;
    return worldBody;
  }

  /**
   * True when this item was authored INSIDE a character (the Fist, a vehicle's mounted weapon) and
   * so is held from birth — it must never wrap itself in a body. The test is the one that actually
   * decides it: a WeaponController somewhere up the parent chain. Owner/scene-root tests were
   * considered and are wrong for a runtime-instantiated weapon (no owner) and for a weapon placed
   * in a sub-scene.
   */
  protected final boolean bornHeld() {
    for (Node n = getParent(); n != null; n = n.getParent()) {
      if (n.hasNode(WEAPON_CONTROLLER_PATH)) return true;
    }
    return false;
  }

  /**
   * Claim this item for an inventory before the equip actually resolves. Two things want this and
   * both are the same thing: an item that is ON ITS WAY into a slot is not lying in the world, so
   * it must not sprout a world body in the meantime.
   *
   * <p>Measured on DebugWorld: without it, every weapon spawned straight into an inventory
   * (ZoneManager arming a streamed-in AI, DebugHarness) built a PickupBody on the deferred frame
   * and had it freed again by the equip one frame later — 3 of the 13 bodies built in a boot.
   * The pickup path had this already: {@code WeaponItem.onCharacterEntered} sets the same flag
   * before it defers, to block a re-trigger.
   *
   * <p>Deliberately does NOT take the body away — that reparents CollisionShape3D nodes out of a
   * RigidBody3D, and this is reached from a body_entered physics signal where the physics server
   * will not accept it. {@link #onPickedUp} does the detach, at idle.
   */
  public void claimForInventory() {
    equipped = true;
  }

  /**
   * Wrap this item in a {@link PickupBody} in place, keeping its world transform. Idempotent, and a
   * no-op while the item is equipped — which is what makes the deferred call from {@link #_ready}
   * safe whichever side of the first equip it lands on.
   */
  @Register
  public void attachWorldBody() {
    attachWorldBodyUnder(null);
  }

  /** As {@link #attachWorldBody}, placing (or moving) the body under {@code host}. */
  private void attachWorldBodyUnder(Node host) {
    if (equipped || !isInsideTree()) return;
    PickupBody body = worldBody();
    if (body != null) {
      if (host != null && !host.equals(body.getParent())) body.reparent(host, true);
      return;
    }
    Node parent = host != null ? host : getParent();
    if (parent == null || parent instanceof PickupBody) return;

    Transform3D at = getGlobalTransform();
    body = new PickupBody();
    body.setName(new StringName(getName().toString() + "Body"));
    parent.addChild(body);
    // Orthonormalised: a scaled rigid body is a physics hazard, and any scale the item carries
    // stays on the item (reparent below keeps its global transform, so it absorbs the residual).
    body.setGlobalTransform(new Transform3D(at.getBasis().orthonormalized(), at.getOrigin()));
    if (worldBodyContinuousCd) body.setUseContinuousCollisionDetection(true);
    reparent(body, true);
    lendShapesTo(body);
    worldBody = body;
  }

  /**
   * Take the body away: shapes come back, the item is reparented to where the body stood (keeping
   * its world transform) and the body is freed. Idempotent — most callers cannot know whether the
   * item currently has one.
   */
  public void detachWorldBody() {
    PickupBody body = worldBody();
    worldBody = null;
    if (body == null) return;
    reclaimShapesFrom(body);
    Node host = body.getParent();
    if (host != null && isInsideTree()) reparent(host, true);
    body.queueFree();
  }

  /**
   * Put this item in the world at {@code position} and throw it with {@code impulse} — the one
   * entry point for a drop. {@code parent} null keeps it where it already is (the replicated-drop
   * convergence path, which only needs to move an item that is already lying about).
   */
  public void placeInWorld(Node parent, Vector3 position, Vector3 impulse) {
    attachWorldBodyUnder(parent);
    PickupBody body = worldBody();
    if (body == null) {           // equipped, or not in the tree — nothing to throw
      setGlobalPosition(position);
      return;
    }
    body.setGlobalPosition(position);
    // Clear the velocity a freshly built body cannot have but a re-placed one can: an item
    // converged by a replicated drop keeps whatever it was doing, and adding an impulse on top of
    // that can tunnel it through a thin floor.
    body.setLinearVelocity(Vector3.Companion.getZERO());
    body.setAngularVelocity(Vector3.Companion.getZERO());
    body.applyCentralImpulse(impulse);
  }

  /** Hand every direct CollisionShape3D to {@code body} — a shape only registers with its DIRECT parent. */
  private void lendShapesTo(PickupBody body) {
    for (Node child : new ArrayList<>(collectShapes(this))) child.reparent(body, true);
  }

  /** The other half of {@link #lendShapesTo}: take them back before the body goes. */
  private void reclaimShapesFrom(PickupBody body) {
    for (Node child : new ArrayList<>(collectShapes(body))) child.reparent(this, true);
  }

  private List<CollisionShape3D> collectShapes(Node parent) {
    List<CollisionShape3D> shapes = new ArrayList<>();
    for (Node child : parent.getChildren()) {
      if (child instanceof CollisionShape3D shape) shapes.add(shape);
    }
    return shapes;
  }

  // ── Tick ─────────────────────────────────────────────────────────────────

  @Register
  @Override
  public void _process(double delta) {
    if (pickupCooldown > 0f) pickupCooldown -= (float) delta;

    // Networked-host collect, resolved out of the body_entered signal (see collectBy).
    if (pendingCollector != null) {
      Node collector = pendingCollector;
      pendingCollector = null;
      resolveHostCollect(collector);
    }

    if (requireInteract && !overlappingBodies.isEmpty()
        && Input.INSTANCE.isActionJustPressed("interact", false)) {
      triggerInteract();
    }
  }

  // ── Override points ───────────────────────────────────────────────────────

  protected void onCharacterEntered(Node character) {}

  /** Text shown in the HUD interact prompt. Override in subclasses. */
  protected String getInteractLabel() { return "Item"; }

  // ── Body detection (connected from PickupArea signals) ────────────────────

  @Register
  public void onBodyEntered(Node3D body) {
    if (equipped || pendingCollector != null || pickupCooldown > 0f) return;
    Node character = resolveCharacter(body);
    if (character == null || !isAlive(character) || !isLocallyOwned(character)) return;

    if (shouldAutoPickup(character)) {
      collectBy(character);
    } else {
      overlappingBodies.add(body);
      emitInteractPrompt(true);
    }
  }

  /**
   * Returns true when this pickup should be collected immediately on body enter,
   * bypassing the interact prompt. Default: auto-pickup when requireInteract is false.
   * Subclasses can override for context-sensitive behaviour (e.g. WeaponItem checks
   * whether the target slot is free).
   */
  protected boolean shouldAutoPickup(Node character) {
    return !requireInteract;
  }

  @Register
  public void onBodyExited(Node3D body) {
    overlappingBodies.remove(body);
    if (overlappingBodies.isEmpty()) emitInteractPrompt(false);
  }

  // ── Pause / resume ────────────────────────────────────────────────────────

  public void pause() {
    overlappingBodies.clear();
    emitInteractPrompt(false);
    setVisible(false);
    detachWorldBody();
    setAreaMonitoring(false);
  }

  @Register
  public void resumeFromPause() {
    setVisible(true);
    attachWorldBody();
    setAreaMonitoring(true);
    onResumed();
  }

  /**
   * Toggle the PickupArea. Deferred: a physics server will not accept a monitoring change while it
   * is flushing queries, and this is reached from body_entered.
   */
  private void setAreaMonitoring(boolean on) {
    Node areaNode = getNodeOrNull(PICKUP_AREA);
    if (areaNode instanceof Area3D area) area.setDeferred(new StringName("monitoring"), on);
  }

  protected void onResumed() {}

  // ── Equip / return lifecycle (used by WeaponItem) ─────────────────────────

  /** Collected: the item loses its world body entirely and becomes an ordinary Node3D the caller
   *  can hang off a bone socket. Hides immediately so the world pickup vanishes on collection;
   *  WeaponController.moveWeaponToHand calls show() afterwards for weapons with a hold socket, so
   *  the hide here is intentionally overridden for visually-held weapons.
   *
   *  <p>Must run BEFORE the caller reparents the item onto a socket — reparenting the item while it
   *  still sits inside its body would carry the body's shapes nowhere and strand the body itself.
   *
   *  <p>Registered because it is half of the item's world/held transition, and the only way anything
   *  outside {@code WeaponController} (a scene, a probe) can say "this item is carried now". */
  @Register
  public void onPickedUp() {
    equipped = true;
    overlappingBodies.clear();
    emitInteractPrompt(false);
    hide();
    detachWorldBody();
    setAreaMonitoring(false);
  }

  /** Back in the world: physical presence and detection return. Note this only clears the equipped
   *  flag and rebuilds the body IN PLACE — a drop that also has a position and a throw goes through
   *  {@link #placeInWorld}. Safe to call from _process (not a signal). */
  @Register
  public void onReturnedToWorld() {
    equipped = false;
    pickupCooldown = pickupCooldownAfterDrop;
    attachWorldBody();
    setAreaMonitoring(true);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  protected final Node resolveCharacter(Node3D body) {
    if (body.hasNode(WEAPON_CONTROLLER_PATH)) return body;
    Node owner = body.getOwner();
    if (owner != null && owner.hasNode(WEAPON_CONTROLLER_PATH)) return owner;
    return null;
  }

  private void triggerInteract() {
    for (Node3D body : new ArrayList<>(overlappingBodies)) {
      Node character = resolveCharacter(body);
      if (character != null && isAlive(character) && isLocallyOwned(character)) {
        overlappingBodies.clear();
        emitInteractPrompt(false);
        collectBy(character);
        return;
      }
    }
  }

  /**
   * Single collection entry point — routes by network role (host-arbitrated pickups):
   *   single-player / host-owned body → collect locally, then (if hosting) broadcast
   *     MSG_PICKUP_TAKEN so every client mirrors the collect on its own copy;
   *   client-owned body → send MSG_PICKUP_REQUEST only; the host validates and the
   *     TAKEN echo performs the actual collect here (see applyReplicatedPickup).
   * The owner-gate in onBodyEntered/triggerInteract guarantees the character passed
   * here is locally owned, so "am I the host" fully determines the branch.
   */
  private void collectBy(Node character) {
    Node netNode = getNodeOrNull("/root/NetworkManager");
    if (netNode instanceof NetworkManager net && net.isNetworked()) {
      String characterId = resolveCharacterId(character);
      if (characterId.isEmpty()) return;   // unreplicatable collector — never collect silently in MP
      if (!net.isServer()) {
        net.requestPickup(pickupId, characterId);
        return;
      }
      // Networked host: defer to _process so the equip resolves SYNCHRONOUSLY (out of this
      // body_entered physics signal, where reparent is forbidden) and the broadcast carries
      // the post-merge OUTCOME. Broadcasting before the equip could announce a pickup the
      // slot-displacement guard then bounced back to the world — every client would delete an
      // item the host still has. pendingCollector blocks re-trigger until _process resolves it.
      pendingCollector = character;
      return;
    }
    // Single-player: resolve inline (no broadcast, no cross-peer convergence concern).
    // Claim the pickup BEFORE onCharacterEntered: a throwable's free-slot equip is deferred to
    // _process, and during that window a second body_entered for the SAME pickup (the player's held
    // weapon body follows the capsule into the area, or split-frame timing) could collect it again —
    // the "picked up 6 instead of 3" double-count. Setting equipped here makes the onBodyEntered guard
    // reject any re-trigger immediately. Safe: collectBy is only reached when the pickup will be
    // consumed (shouldAutoPickup), and the equip/merge paths set equipped anyway.
    equipped = true;
    onCharacterEntered(character);
    applyPostPickup();
  }

  /**
   * Networked host: resolve a queued collect synchronously, then broadcast MSG_PICKUP_TAKEN
   * only for what was actually consumed (equipped or merged). Mirrors GameManager's grant path
   * (applyReplicatedPickup → broadcast-if-taken) for the host's OWN collects, so a cluster of
   * same-type pickups merges deterministically and clients are never told an item is taken that
   * the host bounced back to the world. Runs in _process — safe context for reparent.
   */
  private void resolveHostCollect(Node character) {
    if (character == null || !character.isInsideTree()) return;
    // Capture state BEFORE collecting — a throwable merge consumes this node's magazine.
    int magazine = getReplicatedMagazine();
    int reserve = getReplicatedReserve();
    applyReplicatedPickup(character, magazine, reserve);   // synchronous equip + applyPostPickup
    Node netNode = getNodeOrNull("/root/NetworkManager");
    if (isTaken() && netNode instanceof NetworkManager net && net.isNetworked() && net.isServer()) {
      String characterId = resolveCharacterId(character);
      if (!characterId.isEmpty()) net.broadcastPickupTaken(pickupId, characterId, magazine, reserve);
    }
  }

  /**
   * Executes a host-confirmed MSG_PICKUP_TAKEN on this peer — the same collect path a
   * local pickup takes, with the item state stamped from the event first so every peer's
   * copy of the item is identical. Idempotent: a duplicate event on an already-taken
   * item is a no-op (equipped is set by onCharacterEntered/WeaponItem before any defer).
   */
  public void applyReplicatedPickup(Node character, int magazine, int reserve) {
    if (equipped) return;
    stampReplicatedAmmo(magazine, reserve);
    // Resolve the equip SYNCHRONOUSLY (this runs in NetworkManager's _process packet drain,
    // not a physics signal, so reparent is legal). Each granted/echoed pickup then fully
    // lands before the next is processed, so a cluster of same-type items merges
    // deterministically — identical on host and every client. See WeaponController.synchronousEquip.
    Node wcNode = character.getNodeOrNull(WEAPON_CONTROLLER_PATH);
    com.openworld.weapon.WeaponController wc =
        wcNode instanceof com.openworld.weapon.WeaponController w ? w : null;
    if (wc != null) wc.setSynchronousEquip(true);
    try {
      onCharacterEntered(character);
    } finally {
      if (wc != null) {
        wc.setSynchronousEquip(false);
        // Refresh the owner's HUD to the post-collect active-weapon count — a throwable
        // merge that landed on the active slot must update the displayed count immediately,
        // not stay stale until the next manual weapon switch.
        wc.refreshActiveAmmoDisplay();
      }
    }
    applyPostPickup();
  }

  /** True once collected into an inventory — the host's ALREADY_TAKEN arbitration check. */
  public boolean isTaken() { return equipped; }

  // Replication hooks — WeaponItem overrides to carry magazine/reserve; base pickups have no
  // ammo. Getters are public so the host's grant path (GameManager.processPickupRequest) can
  // sample the item state it puts on the wire.
  public int getReplicatedMagazine() { return 0; }
  public int getReplicatedReserve()  { return 0; }
  protected void stampReplicatedAmmo(int magazine, int reserve) { }

  /** The stable CharacterInfo.characterId of a collector, or "" when it has none. */
  private String resolveCharacterId(Node character) {
    if (character instanceof Controllable c && c.getCharacterInfo() != null) {
      return c.getCharacterInfo().characterId;
    }
    return "";
  }

  private void applyPostPickup() {
    if (removeOnPickup) queueFree();
    else if (pauseOnPickup) pause();
  }

  private boolean isAlive(Node character) {
    Node healthNode = character.getNodeOrNull("Health");
    if (healthNode instanceof Health h) return !h.isDead();
    return true;
  }

  /**
   * Only the peer that OWNS a character may process its pickup intent. Without this,
   * a NetworkController puppet walking over a pickup would collect it into its local
   * inventory — diverging inventories between peers (each peer's copy of the same
   * character collecting different items). Single-player / non-networked: always true.
   * Networked: resolves CharacterInfo.ownerPeerId against this peer's id; a body whose
   * ownership can't be established is conservatively NOT collectable.
   */
  protected final boolean isLocallyOwned(Node character) {
    Node netNode = getNodeOrNull("/root/NetworkManager");
    if (!(netNode instanceof NetworkManager net) || !net.isNetworked()) return true;
    if (!(character instanceof Controllable c) || c.getCharacterInfo() == null) return false;
    return net.isAuthorityFor(c.getCharacterInfo());
  }

  private EventBus getEventBus() {
    if (eventBus == null) {
      Node n = getNodeOrNull("/root/EventBus");
      if (n instanceof EventBus eb) eventBus = eb;
    }
    return eventBus;
  }

  private void emitInteractPrompt(boolean inRange) {
    EventBus bus = getEventBus();
    if (bus != null) bus.pickupInteractChanged.emit(inRange, inRange ? "Pick up: " + getInteractLabel() : "");
  }
}

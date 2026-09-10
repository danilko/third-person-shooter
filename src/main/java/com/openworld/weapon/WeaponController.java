package com.openworld.weapon;

import com.openworld.game.EventBus;
import com.openworld.net.NetworkManager;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.*;
import godot.core.Callable;
import godot.core.MethodCallable;
import godot.core.NodePath;
import godot.core.Signal1;
import godot.core.Signal2;
import godot.core.StringName;
import godot.core.StringNames;
import godot.core.VariantArray;
import godot.core.Vector3;
import godot.global.GD;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.character.AnimationController;
import com.openworld.character.Character;
import com.openworld.character.CharacterInfo;
import com.openworld.character.CharacterVisuals;
import com.openworld.character.MeshConfig;
import com.openworld.debug.DebugHarness;
import com.openworld.game.GameManager;
import com.openworld.item.Pickup;
import com.openworld.movement.character.Stance;
import com.openworld.net.NetMessageCodec;
import com.openworld.net.NetStats;
import com.openworld.net.NetworkController;

@Script(className = "WeaponController")
public class WeaponController extends Node {

  @Export public AnimationController animationController;

  @Export
  public NodePath aimRayPath = new NodePath("ActiveCamera/AimRay");

  @Export
  public NodePath weaponAttachmentPath = new NodePath("MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/WeaponAttachment");

  /**
   * All weapon socket Marker3D nodes for this character, listed as NodePaths from the
   * scene root. Each Marker3D node name becomes a key in the socket registry.
   *
   * WeaponItem.holdSocket / WeaponItem.holsterSockets reference these names.
   * Example: a rifle with holdSocket="MarkerRifle" attaches to the Marker3D named
   * "MarkerRifle" registered here. A shovel with holsterSockets=["MarkerBack"] parks at
   * the Marker3D named "MarkerBack". No code changes needed for new weapons or poses.
   */
  @Export
  public VariantArray<NodePath> socketPaths = new VariantArray<>(NodePath.class);

  public final Signal1<Float> weaponFired = new Signal1<>(this, new StringName("weapon_fired"));

  public final Signal2<Integer, Integer> ammoChanged = new Signal2<>(this, new StringName("ammo_changed"));

  @Export public AudioStreamPlayer3D weaponAudio;

  /**
   * Defines the type of each slot by index.
   * Slot 0 is always FIST — permanent, non-droppable, auto-populated in _ready().
   * Slots 1–6 are the standard weapon inventory.
   */
  protected WeaponSlotType[] slotTypes = {
      WeaponSlotType.FIST,        // slot 0 — permanent fist (key: key 0)
      WeaponSlotType.PRIMARY,     // slot 1 — first long weapon  (key 1)
      WeaponSlotType.PRIMARY,     // slot 2 — second long weapon (key 2)
      WeaponSlotType.SECONDARY,   // slot 3 — sidearm            (key 3)
      WeaponSlotType.MELEE,       // slot 4 — melee              (key 4)
      WeaponSlotType.THROWABLE,   // slot 5 — throwable          (key 5)
      WeaponSlotType.CONSUMABLE,  // slot 6 — consumable         (key 6)
  };

  private WeaponItem[] weapons;
  private int activeSlotIndex  = 0;
  private int pendingSlotIndex = 0;
  // Rolling shot counter (u8) replicated in each snapshot — remote peers play the fire cue when it
  // changes (fire-as-state). On a puppet this is set from the wire via setReplicatedFireSeq.
  private int fireSeq = 0;
  // Rolling reload counter (u8), same pattern as fireSeq — remote peers play the reload animation
  // when it changes, so a reloading character is visibly reloading on every screen (a tactical tell).
  private int reloadSeq = 0;

  // Weapons queued for equip/drop; drained in _process (idle). A held weapon is no longer a
  // physics body, so this is no longer the CollisionObject-reparent restriction it was built for —
  // what it still buys is that an equip resolves OUT of the body_entered signal that requested it,
  // which is what the same-frame merge/displacement guards below are written against.
  private final List<WeaponItem> pendingEquips = new ArrayList<>();
  // Items already equipped during the current _process pass — see the race-guard
  // comment in equipWeapon(). Cleared at the start of each pass that has work to do.
  private final Set<WeaponItem> equippedThisPass = new HashSet<>();
  // Countdown (seconds) for a deferred throwable slot-clear; < 0 = idle. See clearActiveSlot
  // for why the clear is held off (last-throw cue replication across a snapshot send).
  private double pendingSlotClearCountdown = -1.0;
  /** Hold the emptied throwable active this long so its throw fireSeq replicates (≥2 snapshot intervals at ~33 ms). */
  private static final double SLOT_CLEAR_DELAY_SECONDS = 0.1;
  /**
   * Draw-settle fire lockout after a weapon switch completes (s). A small fixed value just long enough
   * to stop a held fire button launching on the very first mid-draw frame (a projectile spawned from
   * the transient muzzle could fire into the ground/self). Previously this was a full
   * {@code 1/switchSpeed} — doubling the perceived switch time; the deploy time alone is the switch
   * cost (CS/PUBG-style), so the settle is now a brief constant.
   */
  private static final double DRAW_SETTLE_SECONDS = 0.08;
  // When true, requestEquip resolves the equip IMMEDIATELY instead of deferring to _process.
  // Set by the network collect path (Pickup.applyReplicatedPickup), which runs in _process
  // (NetworkManager's packet drain) — a safe, non-signal context where reparent is legal.
  // Synchronous resolution is what makes a cluster of same-type pickups (e.g. 3 grenades)
  // converge: each granted/echoed pickup fully equips before the next is processed, so the
  // second sees the first in the slot and MERGES — identical outcome on host and every
  // client, instead of the timing-dependent merge-vs-displace race that diverged inventories.
  // The local body_entered collect path keeps deferring (it runs in a physics signal).
  private boolean synchronousEquip = false;
  // The slot is captured at queue time — it is nulled before _process drains the queue,
  // and the replicated drop event (Phase E) is keyed by slot on the receiving peers.
  private record PendingDrop(WeaponItem item, int slot) { }
  private final List<PendingDrop> pendingDrops  = new ArrayList<>();
  // True when pendingDrops was populated by dropAllWeapons() (death); false for a
  // manual single-weapon drop. Controls which physics parameters are used.
  private boolean isDeathDrop = false;

  // Populated in _ready() from socketPaths: node name → Marker3D node.
  private final Map<String, Node> socketMap = new HashMap<>();

  /**
   * Where a weapon with no hold/holster socket is parked while carried — the weapon attachment
   * node, so it rides the character hidden instead of being left lying in the world scene.
   *
   * <p>That used to be the workaround for a real hazard: the weapon WAS a RigidBody3D, and moving a
   * frozen body confused Jolt into clipping it through the ground when it came back. A held weapon
   * has no body now (see {@code item.PickupBody}), so there is nothing left to confuse and the item
   * can simply go where it belongs.
   */
  private Node stowNode;

  private RayCast3D aimRay;
  private RayCast3D originalAimRay;
  private EventBus eventBus;

  private Timer transitionTimer;
  private Timer fireTimer;
  private Timer reloadTimer;

  // ── Accessors ─────────────────────────────────────────────────────────────

  public int getWeapon() { return activeSlotIndex; }

  /**
   * The slot to replicate (G4-1): the switch TARGET while a transition is in flight, otherwise the
   * active slot. Replicating the target the instant the owner commits to a switch — not the
   * post-animation outcome — lets puppets begin their draw promptly (and keeps the per-tick snapshot
   * agreeing with the reliable MSG_WEAPON_SWITCH event so the two never fight). Owner-only state: a
   * puppet never transitions, so this just returns its snapped activeSlotIndex.
   */
  public int getReplicatedActiveSlot() { return isWeaponTransitioning() ? pendingSlotIndex : activeSlotIndex; }

  /** True when a real weapon (slot > 0) is active. False when fist is active. */
  public boolean isArmed() { return activeSlotIndex > 0; }

  public WeaponItem getCurrentWeaponItem() {
    return weapons[activeSlotIndex];
  }

  /** Active weapon's magazine (0 when fist/empty) — replicated per snapshot so puppets track ammo consumption. */
  public int getActiveMagazine() {
    WeaponItem w = getCurrentWeaponItem();
    return w != null ? w.getMagazine() : 0;
  }

  /**
   * Sets the active weapon's magazine from a replicated snapshot (puppet side only). Clears the
   * slot if a consumable (throwable) emptied — mirrors the owner's onMagazineEmpty so a thrown-out
   * grenade stack disappears on the puppet instead of lingering, and the host's manifest (built
   * from this copy) stops listing phantom ammo. Never called on the owner's own body.
   */
  public void applyReplicatedActiveMagazine(int magazine) {
    WeaponItem w = getCurrentWeaponItem();
    if (w == null || w.isInfiniteAmmo || w.getMagazine() == magazine) return;
    w.setMagazine(magazine);
    notifyAmmoChange(w);
    if (magazine == 0) w.onMagazineEmpty();
  }

  public int getWeaponCount() {
    int n = 0;
    for (WeaponItem w : weapons) if (w != null) n++;
    return n;
  }

  public WeaponItem getWeaponItem(int slotIndex) {
    // weapons is allocated in _ready(); a reader can hit this before then (e.g. a sibling
    // Nameplate whose _ready runs first) — treat "not yet built" as empty.
    if (weapons == null || slotIndex < 0 || slotIndex >= weapons.length) return null;
    return weapons[slotIndex];
  }

  public float getCurrentSpreadDeg() {
    WeaponItem w = getCurrentWeaponItem();
    return w != null ? w.getCurrentSpreadDeg() : 0f;
  }

  /** Current weapon's crosshair openness (0..1, weapon-normalized) for the HUD reticle. 0 when unarmed. */
  public float getCrosshairSpreadFraction() {
    WeaponItem w = getCurrentWeaponItem();
    return w != null ? w.getCrosshairFraction() : 0f;
  }

  /** The active AimRay for all character-owned weapons. May be the vehicle ray when overridden. */
  public RayCast3D getAimRay() { return aimRay; }

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  @Register
  @Override
  public void _process(double delta) {
    // Process equips first (deferred from Area3D body_entered signals)
    if (!pendingEquips.isEmpty()) {
      equippedThisPass.clear();
      for (WeaponItem item : new ArrayList<>(pendingEquips)) equipWeapon(item);
      pendingEquips.clear();
      equippedThisPass.clear();
    }
    // Deferred throwable slot-clear (held ~100 ms after the last throw — see clearActiveSlot).
    if (pendingSlotClearCountdown >= 0.0) {
      pendingSlotClearCountdown -= delta;
      if (pendingSlotClearCountdown < 0.0) performActiveSlotClear();
    }
    // Then process any pending drops (single manual drop or full death-drop batch)
    if (!pendingDrops.isEmpty()) {
      List<PendingDrop> batch = new ArrayList<>(pendingDrops);
      boolean death = isDeathDrop;
      pendingDrops.clear();
      isDeathDrop = false;
      for (PendingDrop drop : batch) {
        if (death) returnWeaponToWorldOnDeath(drop.item(), drop.slot());
        else       returnWeaponToWorld(drop.item(), drop.slot());
      }
    }
  }

  /**
   * Stop any in-flight weapon SFX before this controller leaves the tree. A 3D playback still
   * running when its node is freed mid-session leaks the playback and its stream at exit
   * ("Leaked instance: AudioStreamPlaybackWAV … Resource still in use: Rifle_reload.wav"). The
   * networked symptom: the client frees its pre-placed Player on connect ({@code
   * NetworkManager.removeLocalPrePlacedPlayer}) while the spawn-time equip SFX is still playing —
   * single-player never frees a body mid-session, so the cue always finishes naturally. Stopping
   * here releases the playback on every teardown path (despawn, disconnect, engine shutdown).
   */
  @Register
  @Override
  public void _exitTree() {
    silenceAudio();
  }

  /**
   * Stop any in-progress weapon SFX. Call this <b>before</b> the body is removed/freed (while it is
   * still in the tree) so the AudioServer releases the AudioStreamPlaybackWAV — relying on
   * {@code _exitTree} alone is unreliable when the whole body subtree is freed at once, because the
   * sibling {@code WeaponAudio} node can exit the tree before this {@code _exitTree} runs, leaking the
   * playback (see CLAUDE.md audio-leak quirk). E1 zone-unload now frees armed AI mid-session, so the
   * remover (ZoneManager) calls this first.
   */
  public void silenceAudio() {
    if (weaponAudio != null && GD.isInstanceValid(weaponAudio)) weaponAudio.stop();
  }

  @Register
  @Override
  public void _ready() {
    weapons = new WeaponItem[slotTypes.length];

    transitionTimer = (Timer) getNode("TransitionTimer");
    fireTimer       = (Timer) getNode("FireTimer");
    reloadTimer     = (Timer) getNode("ReloadTimer");

    if (getOwner().hasNode(aimRayPath)) {
      aimRay = (RayCast3D) getOwner().getNode(aimRayPath);
    }

    // Build socket registry from owner-relative socket paths (no CharacterVisuals).
    // Characters using CharacterVisuals skip this — postInitFromVisuals() rebuilds from visuals root.
    for (java.lang.Object obj : socketPaths) {
      NodePath p = (NodePath) obj;
      if (p == null || p.toString().isEmpty()) continue;
      Node socket = getOwner().getNodeOrNull(p);
      if (socket != null) socketMap.put(socket.getName().toString(), socket);
    }

    // Discover weapons pre-placed in the weapon attachment (e.g. vehicle weapon mounts).
    // For CharacterVisuals characters the attachment lives inside the visuals sub-scene, so
    // getNodeOrNull returns null here — postInitFromVisuals() re-discovers correctly later.
    discoverPrePlacedWeapons(getOwner(), weaponAttachmentPath);
    showWeapon(activeSlotIndex);

    emitInitialAmmoState();

    // Self-relay: re-broadcast every ammoChanged emission to EventBus.characterAmmoChanged
    // so multi-character HUD/game-state code (C2) can track any character's ammo, not
    // just the local player's. Avoids touching every existing ammoChanged.emit() call site.
    ammoChanged.connectUnsafe(
        MethodCallable.createUnsafe(this, "relayAmmoToEventBus"),
        godot.api.Object.ConnectFlags.DEFAULT);

    // Have WeaponAudio stop ITSELF the instant it leaves the tree. _exitTree here is too late when a
    // whole body subtree is freed at once (incl. app exit): the sibling WeaponAudio can exit first,
    // orphaning its in-flight AudioStreamPlaybackWAV before our stop() runs (CLAUDE.md audio-leak
    // quirk). tree_exiting fires while the node is still valid, so a self-stop reliably releases it.
    if (weaponAudio != null && GD.isInstanceValid(weaponAudio)) {
      weaponAudio.connect(new StringName("tree_exiting"),
          MethodCallable.createUnsafe(weaponAudio, "stop"));
    }
  }

  /** Receives our own ammoChanged signal and re-broadcasts it on EventBus, keyed by owner CharacterInfo. */
  @Register
  public void relayAmmoToEventBus(int magazine, int reserve) {
    if (!(getOwner() instanceof Character c) || c.characterInfo == null) return;
    Node busNode = getNodeOrNull("/root/EventBus");
    if (busNode instanceof EventBus bus) bus.characterAmmoChanged.emit(c.characterInfo, magazine, reserve);
  }

  /**
   * Wires all mesh-dependent references from a newly instantiated CharacterVisuals scene.
   * Called by Character._ready() after addChild(visualsInstance) and after setting
   * Safe to call with a null config (no-op).
   */
  public void postInitFromVisuals(Node visualsRoot, MeshConfig config) {
    if (visualsRoot == null || config == null) return;

    // Rebuild socket map from visuals-relative paths in meshConfig.
    socketMap.clear();
    for (java.lang.Object obj : config.socketPaths) {
      NodePath p = (NodePath) obj;
      if (p == null || p.toString().isEmpty()) continue;
      Node socket = visualsRoot.getNodeOrNull(p);
      if (socket != null) socketMap.put(socket.getName().toString(), socket);
    }

    // Discover weapons pre-placed in the weapon attachment node.
    discoverPrePlacedWeapons(visualsRoot, config.weaponAttachmentPath);
    showWeapon(activeSlotIndex);

    // Sync animation tree to the initial weapon pose (no transition fires on first discovery).
    WeaponItem initial = weapons[activeSlotIndex];
    if (initial != null && animationController != null) {
      animationController.onWeaponEquip(initial.weaponPoseIndex);
    }

    emitInitialAmmoState();
  }

  private void discoverPrePlacedWeapons(Node root, NodePath attachPath) {
    if (attachPath == null || attachPath.isEmpty()) return;
    Node attachment = root.getNodeOrNull(attachPath);
    if (attachment == null) return;
    stowNode = attachment;
    for (Node child : attachment.getChildren()) {
      if (child.getChildCount() > 0 && child.getChild(0) instanceof WeaponItem w) {
        int slot = findFreeSlot(w.resolveSlotType());
        if (slot < 0) slot = findFirstSlot(w.resolveSlotType());
        if (slot < 0) continue;
        weapons[slot] = w;
        w.onPickedUp();
        injectCharacterRefs(w);
        w.hide();
      }
    }
  }

  // ── Runtime equip / drop ─────────────────────────────────────────────────

  /**
   * Queues {@code item} to be equipped in the next idle frame (_process).
   * Called from WeaponItem.onCharacterEntered which runs inside an Area3D
   * body_entered signal (physics context) where reparent() is forbidden.
   */
  public void requestEquip(WeaponItem item) {
    // Claim it now, whichever way the equip resolves: an item heading for a slot must not build
    // itself a world body in the meantime (see Pickup.claimForInventory). The bounce paths in
    // equipWeapon hand it back with onReturnedToWorld, which rebuilds one.
    item.claimForInventory();
    if (synchronousEquip) {
      // Isolated single-item pass: equippedThisPass is cleared either side so the
      // cross-item displacement guard in equipWeapon never fires here — sequential
      // network grants resolve via merge (the second pickup sees the first), which is
      // exactly the convergence we want.
      equippedThisPass.clear();
      equipWeapon(item);
      equippedThisPass.clear();
      return;
    }
    if (!pendingEquips.contains(item)) pendingEquips.add(item);
  }

  /** See {@link #synchronousEquip} — toggled by the network collect path around its equip. */
  public void setSynchronousEquip(boolean on) { synchronousEquip = on; }

  /**
   * Equips {@code item} into the first free slot whose type matches the weapon's
   * {@link WeaponItem#resolveSlotType()}. Falls back to the first slot of that type
   * (displacing the current occupant) if no free slot exists.
   * FIST slot (0) is structurally protected — no other weapon type maps to it.
   */
  public void equipWeapon(WeaponItem item) {
    WeaponSlotType type = item.resolveSlotType();
    int targetSlot = findFreeSlot(type);
    if (targetSlot < 0) {
      // No free slot of this type — replace the weapon currently HELD if it's this type
      // (drop what you're holding for the new one), otherwise fall back to the first slot
      // of this type. Previously this always displaced the first slot, so picking up a
      // primary while holding the second primary wrongly threw away the first one.
      targetSlot = (activeSlotIndex >= 0 && activeSlotIndex < slotTypes.length
                    && slotTypes[activeSlotIndex] == type)
          ? activeSlotIndex
          : findFirstSlot(type);
    }
    if (targetSlot < 0) return;

    WeaponItem displaced = weapons[targetSlot];

    // Two nearby pickups of the same slot type can both pass shouldAutoPickup() in the
    // same physics frame — the matching slot still reads as "free" to both because the
    // actual equip is deferred to _process. Without this guard the second one would
    // immediately displace-and-throw the first back into the world the instant it lands
    // (e.g. "two throwables close together — one gets picked up and flung away again").
    // Hand it back to the world instead: same outcome as walking up to a full slot
    // (interact-prompt / re-trigger once settled), just consistent and non-jarring.
    if (displaced != null && equippedThisPass.contains(displaced)) {
      // Same-type throwable stacking: when two or more grenade pickups are grabbed in the SAME
      // frame they all pass the (deferred-slot) free/merge check against a still-empty slot and
      // queue a free-slot equip; only the first lands and the rest reach this guard. Merge their
      // carry counts into the stack already equipped this pass instead of bouncing the extras back
      // to the world — otherwise the stack under-counts (only the first unit lands) and re-collecting
      // the bounced pickups one-by-one produces inconsistent totals (the over/under-count bug).
      if (item instanceof ThrowableItem incoming && displaced instanceof ThrowableItem stack
          && !incoming.weaponId.isEmpty() && incoming.weaponId.equals(stack.weaponId)) {
        int room = stack.magazineSize - stack.magazine;
        if (room > 0) {
          int moved = Math.min(room, incoming.magazine);
          stack.magazine += moved;
          incoming.magazine -= moved;
          // Unconditional re-emit (see ThrowableItem merge): refresh the slot UI / nameplate even
          // when the merged stack is not the active weapon — notifyAmmoChange would no-op there.
          refreshActiveAmmoDisplay();
        }
        // Anything that didn't fit (stack hit magazineSize) goes back to the world; a fully
        // absorbed pickup is consumed.
        if (incoming.magazine > 0) incoming.onReturnedToWorld();
        else incoming.queueFree();
        return;
      }
      item.onReturnedToWorld();
      return;
    }

    boolean willBeActive = targetSlot == activeSlotIndex || weapons[activeSlotIndex] == null;

    item.onPickedUp();
    injectCharacterRefs(item);
    weapons[targetSlot] = item;
    equippedThisPass.add(item);

    // Items that opt into autoEquipOnPickup (e.g. throwables) jump straight to the
    // active slot when the character is unarmed (fist active), so they're immediately
    // usable without a manual slot switch. Generalizes the old throwable-only special
    // case so any weapon archetype can opt in via the exported flag — see
    // WeaponItem.autoEquipOnPickup.
    if (!willBeActive && item.autoEquipOnPickup && activeSlotIndex == 0) {
      willBeActive = true;
      activeSlotIndex = targetSlot;
    }

    if (willBeActive) {
      boolean wasArmed = isArmed();
      activeSlotIndex = targetSlot;
      moveWeaponToHand(item);
      // Block accidental fire on pickup: the fire button may already be held from the
      // previous weapon. Use the switch-speed delay (same as a manual slot switch) so the
      // player must release and re-press fire before the newly equipped weapon can fire.
      if (fireTimer.getTimeLeft() <= 0) {
        fireTimer.setWaitTime(1.0 / item.getSwitchSpeed());
        fireTimer.start();
      }
      if (item.getReloadAudio() != null) {
        weaponAudio.setStream(item.getReloadAudio());
        weaponAudio.play();
      }
      if (animationController != null) animationController.onWeaponEquip(item.weaponPoseIndex);
      ammoChanged.emit(item.getMagazine(), item.getReserve());
      if (wasArmed != isArmed()) emitArmedStateChanged(isArmed());
    } else {
      // Weapon goes into an inactive slot — mount at its holster socket.
      moveWeaponToHolster(item);
      WeaponItem active = getCurrentWeaponItem();
      ammoChanged.emit(active != null ? active.getMagazine() : 0,
                       active != null ? active.getReserve()  : 0);
    }

    EventBus bus = getEventBus();
    if (bus != null) {
      String characterId = (getOwner() instanceof Character c && c.characterInfo != null)
          ? c.characterInfo.characterId : "";
      bus.weaponPickedUp.emit(characterId, item.getDisplayName(), item.weaponIcon);
    }

    if (displaced != null && displaced.shouldDropToWorld()) returnWeaponToWorld(displaced, targetSlot);
    else if (displaced != null) displaced.setup(null, null, null);
  }

  /**
   * Removes the currently active weapon from the inventory and returns it to the
   * world at the character's feet with a throw impulse.
   * No-op if the active weapon is not droppable (e.g. fist at slot 0).
   */
  @Register
  public void dropCurrentWeapon() {
    WeaponItem current = weapons[activeSlotIndex];
    if (current == null || !current.isDroppable) return;
    int slot = activeSlotIndex;
    weapons[slot] = null;
    current.hide();
    // ThrowableItem with 0 carry count: clear refs but don't spawn a world pickup.
    if (current.shouldDropToWorld()) {
      pendingDrops.add(new PendingDrop(current, slot));
    } else {
      current.setup(null, null, null);
    }
    activateFirstAvailableSlot();
  }

  /**
   * Releases every droppable weapon back into the world. Called on character death.
   * Safe to invoke from a physics callback — actual reparenting is deferred to _process().
   * Each weapon spawns at hip height + 0.5 m extra and is thrown in a random radial
   * direction so weapons fan out rather than pile on one spot.
   * Non-droppable weapons (fist) are skipped.
   */
  public void dropAllWeapons() {
    for (int i = 0; i < weapons.length; i++) {
      WeaponItem item = weapons[i];
      if (item == null || !item.isDroppable) continue;
      weapons[i] = null;
      item.hide();
      pendingDrops.add(new PendingDrop(item, i));
    }
    isDeathDrop = true;
  }

  // ── Signal handlers ───────────────────────────────────────────────────────

  @Register
  public void onWeaponFire() {
    if (fireTimer.getTimeLeft() > 0 || reloadTimer.getTimeLeft() > 0 || isWeaponTransitioning()) return;
    WeaponItem w = getCurrentWeaponItem();
    if (w == null) return;
    if (!w.isInfiniteAmmo && w.getMagazine() == 0) { onWeaponReload(); return; }
    if (!w.canUse()) return;
    // The weapon's OWN moving parts (pump, bolt, cylinder) -- see WeaponItem.weaponAnimatorPath.
    // Placed after every gate, so a shot that was suppressed moves nothing.
    w.playMotion(w.fireAnimation);

    fireTimer.setWaitTime(1.0 / w.getFireRate());
    fireTimer.start();

    w.useWeapon();
    weaponFired.emit(w.getFireRate() * 0.2f);
    ammoChanged.emit(w.getMagazine(), w.getReserve());
    // Fire is replicated as STATE: bump the rolling shot counter that rides the snapshot stream.
    // Remote peers play the muzzle/tracer cue when they see this change (NetworkController), so no
    // separate (droppable) fire message is needed. u8 on the wire — only change-detection matters.
    fireSeq = (fireSeq + 1) & 0xFF;
    // After the last throw, let the weapon clear its own slot (ThrowableItem auto-empties)
    if (!w.isInfiniteAmmo && w.getMagazine() == 0) w.onMagazineEmpty();
  }

  /** Rolling shot counter sampled into each snapshot (fire-as-state). */
  public int getFireSeq() { return fireSeq; }

  /** Mirrors the replicated counter onto a puppet so the host re-broadcasts the correct value. */
  public void setReplicatedFireSeq(int seq) { fireSeq = seq & 0xFF; }

  /** Rolling reload counter sampled into each snapshot (reload-as-state). */
  public int getReloadSeq() { return reloadSeq; }

  /** Mirrors the replicated reload counter onto a puppet so the host re-broadcasts the correct value. */
  public void setReplicatedReloadSeq(int seq) { reloadSeq = seq & 0xFF; }

  /**
   * Network replay hook — plays the reload cosmetics (animation + audio) without running the reload
   * timer or refilling the magazine (ammo arrives via the replicated activeMagazine). Lets every
   * peer see a character is reloading. No-op when there's no active weapon (e.g. fist/diverged slot).
   */
  public void playRemoteReloadCue() {
    WeaponItem w = getCurrentWeaponItem();
    if (w == null || !isArmed()) return;
    w.playMotion(w.reloadAnimation);
    if (w.getReloadAudio() != null) {
      weaponAudio.setStream(w.getReloadAudio());
      weaponAudio.play();
    }
    if (animationController != null) animationController.onWeaponReload();
  }

  // One-shot guard for the cue-divergence diagnostic below — the cue fires per shot (up to
  // ~10/s under sustained fire), so an unguarded print would flood the console.
  private boolean loggedCueNoFirearm = false;

  /** Network replay hook — plays the firing cosmetics (flash/audio + tracer) without consuming ammo or running hitscan. */
  @Register
  public void playRemoteFireCue() {
    // G4-2 (puppet fire-gate): never render a shot before the weapon is up. Mirror the owner's own
    // onWeaponFire gate — suppress while the holster→draw transition runs OR through the draw-settle
    // fireTimer that onWeaponTransitionComplete starts. A cue inside that window is the
    // fire-precedes-draw race (rare once G4-1 aligns the switch). The owner self-gates its real fire
    // on the same condition, so nothing authoritative is lost.
    if (isWeaponTransitioning() || fireTimer.getTimeLeft() > 0) {
      NetStats.increment("fire_cue_predraw_suppressed");
      return;
    }
    WeaponItem w = getCurrentWeaponItem();
    if (w != null && isArmed()) {
      w.playMotion(w.fireAnimation);   // the weapon's own moving parts, on the remote peer too
      // Polymorphic cosmetic replay: firearms draw muzzle/tracer, throwable/projectile
      // weapons spawn a non-damaging projectile so the grenade/rocket arc + explosion is
      // seen on every peer (damage stays authority-side). Default no-op for the fist.
      w.playRemoteFireCue();
      return;
    }
    // The authority fired (its fireSeq advanced) but this puppet has no real active weapon to
    // render it — the puppet's inventory/slot diverged from the owner's (the "host does not do
    // any fire" symptom). Round 11 N1: count + log once so the divergence is visible; the
    // MSG_INVENTORY sweep is what actually heals it.
    com.openworld.net.NetStats.increment("cue_no_weapon");
    if (!loggedCueNoFirearm) {
      loggedCueNoFirearm = true;
      GD.print("WeaponController: remote fire cue on '" + getOwner().getName()
          + "' landed on empty/fist slot " + activeSlotIndex
          + " — puppet inventory diverged (MSG_INVENTORY will reconcile)");
    }
  }

  @Register
  public void onWeaponNotFire() {
    WeaponItem w = getCurrentWeaponItem();
    if (w != null) w.stopUseWeapon();
  }

  @Register
  public void onWeaponReload() {
    WeaponItem w = getCurrentWeaponItem();
    if (w == null || w.isInfiniteAmmo || w.getReserve() == 0 || isWeaponReloading()) return;
    reloadTimer.setWaitTime(1.0 / w.getReloadSpeed());
    w.playMotion(w.reloadAnimation);
    if (w.getReloadAudio() != null) {
      weaponAudio.setStream(w.getReloadAudio());
      weaponAudio.play();
    }
    reloadTimer.start();
    if (animationController != null) animationController.onWeaponReload();
    // Reload-as-state: bump the rolling counter that rides the snapshot stream so every remote peer
    // replays the reload animation (a visible "reloading, can't fire yet" tell). u8 — only change
    // detection matters, and the new value persists across snapshots so a dropped frame is harmless.
    reloadSeq = (reloadSeq + 1) & 0xFF;
  }

  @Register
  public void onWeaponReloadComplete() {
    WeaponItem w = getCurrentWeaponItem();
    if (w == null) return;
    w.onReloadComplete();
    ammoChanged.emit(w.getMagazine(), w.getReserve());
  }

  @Register
  public void onSetWeapon(int slotIndex) {
    if (!beginWeaponTransition(slotIndex)) return;
    // G4-1: announce the equip START now (reliable, ordered), so puppets begin the SAME transition
    // at the same logical moment. The per-tick snapshot (getReplicatedActiveSlot, the target during
    // this transition) is the drop-heal backstop.
    announceWeaponSwitch(pendingSlotIndex);
  }

  /**
   * Starts the holster→draw transition toward {@code slotIndex} — the shared timing used by both the
   * owner's input ({@link #onSetWeapon}) and a puppet's replicated switch
   * ({@link #applyReplicatedWeaponSlot}). The new weapon is only raised at
   * {@link #onWeaponTransitionComplete} (after {@code transitionTimer}), so every peer draws the new
   * weapon at the same offset from switch-start — owner and puppet stay timing-identical. Returns
   * true when a transition actually started (false on a no-op: mid-transition, invalid/empty slot, or
   * re-selecting the active slot).
   */
  private boolean beginWeaponTransition(int slotIndex) {
    if (isWeaponTransitioning()) return false;
    if (slotIndex < 0 || slotIndex >= weapons.length) return false;
    if (weapons[slotIndex] == null) return false;
    if (slotIndex == activeSlotIndex) { showWeapon(activeSlotIndex); return false; }

    pendingSlotIndex = slotIndex;
    showWeapon(activeSlotIndex);   // keep the OLD weapon up through the holster phase
    transitionTimer.setWaitTime(1.0 / weapons[pendingSlotIndex].getSwitchSpeed());
    transitionTimer.start();
    return true;
  }

  /**
   * Owner/host → network: announce the start of a weapon switch (G4-1). Gated on local authority
   * for this body (mirrors announceWeaponDropped) — a puppet's own cosmetic switch never re-announces.
   */
  private void announceWeaponSwitch(int targetSlot) {
    if (!(getOwner() instanceof Character c) || c.characterInfo == null) return;
    Node netNode = getNodeOrNull("/root/NetworkManager");
    if (!(netNode instanceof NetworkManager net) || !net.isNetworked()) return;
    if (!net.isAuthorityFor(c.characterInfo)) return;
    net.sendWeaponSwitch(c.characterInfo.characterId, targetSlot);
  }

  @Register
  public void onWeaponTransitionComplete() {
    boolean wasArmed = isArmed();
    activeSlotIndex = pendingSlotIndex;
    showWeapon(activeSlotIndex);
    WeaponItem next = weapons[activeSlotIndex];
    if (next != null) {
      // Lock firing through the draw-settle (mirrors the pickup-equip lockout in
      // equipWeapon): the new weapon only becomes active here, and its equip animation
      // raises it into the hand pose over the next frames. A held fire button could
      // otherwise launch on the very first frame, while the muzzle is still mid-draw —
      // a projectile/rocket spawned from that transient muzzle position can fire into the
      // ground or the shooter's own body and detonate on self. Fixed brief settle (not a full
      // 1/switchSpeed) so the switch feels snappy — the deploy (transitionTimer) is the switch cost.
      fireTimer.setWaitTime(DRAW_SETTLE_SECONDS);
      fireTimer.start();
      animationController.onWeaponEquip(next.weaponPoseIndex);
      ammoChanged.emit(next.getMagazine(), next.getReserve());
    }
    if (wasArmed != isArmed()) emitArmedStateChanged(isArmed());
  }

  /**
   * Puppet-side weapon switch (non-authority peers, driven by the replicated switch — the ordered
   * MSG_WEAPON_SWITCH event, with the per-tick {@code getReplicatedActiveSlot} slot as the drop-heal
   * backstop).
   *
   * <p>Runs the SAME holster→draw {@code transitionTimer} the owner runs (via
   * {@link #beginWeaponTransition}), so the new weapon is raised at the same offset from switch-start
   * on every peer — owner and puppet stay timing-identical. This relies on the switch being delivered
   * at switch-<i>start</i> (the G4-1 event), not the post-animation slot: an earlier version snapped
   * the weapon up instantly to compensate for late (post-transition) delivery, which — once delivery
   * became prompt — made the puppet draw a full {@code transitionTime} <i>before</i> the owner. The
   * shared {@link #onWeaponTransitionComplete} starts {@code fireTimer} (the draw-settle), so a puppet
   * never fires before its weapon is up (the {@link #playRemoteFireCue} gate keys on
   * {@code isWeaponTransitioning() || fireTimer}, mirroring the owner's own {@code onWeaponFire} gate).
   *
   * <p>Foundation for scale: remote weapon visuals derive from replicated <i>logical</i> state (the
   * switch event + slot), not the transient animation pose — the invariant that lets future LOD /
   * interest-management coarsen or drop puppet animation without breaking shot consistency. No-op when
   * the slot is unchanged, empty, or a transition is already in flight (handled in beginWeaponTransition).
   */
  public void applyReplicatedWeaponSlot(int slotIndex) {
    beginWeaponTransition(slotIndex);
  }

  @Register
  public void onSetStance(Stance stance) {
    for (WeaponItem w : weapons) if (w != null) w.onSetStance(stance);
  }

  // ── Ammo refill ───────────────────────────────────────────────────────────

  public void fillWeaponAmmo() {
    for (WeaponItem w : weapons) if (w != null) w.fillAmmo();
    WeaponItem w = getCurrentWeaponItem();
    if (w != null) ammoChanged.emit(w.getMagazine(), w.getReserve());
  }

  // ── State queries ─────────────────────────────────────────────────────────

  public int     getSlotCount()             { return slotTypes.length; }
  public boolean isSlotFreeFor(WeaponSlotType type) { return findFreeSlot(type) >= 0; }

  /**
   * Returns the first weapon in the given slot type whose weaponId matches.
   * Used by ThrowableItem to find an existing stack for carry-count merging.
   */
  public WeaponItem findWeaponByIdAndType(String weaponId, WeaponSlotType type) {
    for (int i = 0; i < slotTypes.length; i++) {
      if (slotTypes[i] == type && weapons[i] != null
          && weapons[i].weaponId.equals(weaponId)) return weapons[i];
    }
    return null;
  }

  /**
   * Emits ammoChanged for the given weapon if it is currently the active weapon.
   * Called by ThrowableItem after a carry-count merge so the HUD updates immediately.
   */
  public void notifyAmmoChange(WeaponItem forItem) {
    if (forItem != null && forItem == getCurrentWeaponItem()) {
      ammoChanged.emit(forItem.getMagazine(), forItem.getReserve());
    }
  }

  /**
   * Re-emits the active weapon's ammo so the owner's HUD reflects a networked pickup/merge
   * that changed it. The throwable merge already calls notifyAmmoChange, but a replicated
   * collect can update the active count through other paths (a fresh equip then a same-tick
   * merge, or the slot becoming active mid-collect); this unconditional refresh after the
   * collect resolves guarantees the HUD is never left on the pre-pickup count until the next
   * manual weapon switch. No-op effect when the count didn't change (idempotent emit).
   */
  public void refreshActiveAmmoDisplay() {
    WeaponItem w = getCurrentWeaponItem();
    ammoChanged.emit(w != null ? w.getMagazine() : 0, w != null ? w.getReserve() : 0);
  }

  /**
   * Starts the fire timer using the given item's switch speed if it isn't already running.
   * Called by ThrowableItem after a merge pickup so rapid sequential pickups can't chain
   * into an accidental throw when the player holds the fire button.
   */
  public void resetFireTimerForEquip(WeaponItem item) {
    if (fireTimer.getTimeLeft() <= 0) {
      fireTimer.setWaitTime(1.0 / item.getSwitchSpeed());
      fireTimer.start();
    }
  }

  /**
   * Removes the active weapon from its slot without creating a world pickup, clears its
   * character refs, and switches to the next available weapon.
   * Called by ThrowableItem.onMagazineEmpty() after the last grenade is thrown so
   * the THROWABLE slot becomes free for any other throwable type.
   * The active slot is guaranteed to hold the item being emptied — no search needed.
   */
  public void clearActiveSlot() {
    // Hold the just-emptied throwable in the active slot briefly (resolved in _process) before
    // freeing it and switching to fist. The throw's fireSeq is sampled into the snapshot AFTER
    // this tick (gatherInput/sendOwnedState runs before applyInput/onWeaponFire in a tick, so
    // the bumped counter first ships on the next throttled send ~33 ms later); if the slot
    // cleared first, puppets would switch to fist before rendering the throw cue and the LAST
    // grenade's cosmetic projectile would be lost (a single grenade would never show). The
    // ~100 ms hold spans ≥2 snapshot intervals and is imperceptible — canUse() is already false
    // at magazine 0, so it blocks no input. Re-arming via this same call refreshes the delay.
    pendingSlotClearCountdown = SLOT_CLEAR_DELAY_SECONDS;
  }

  /** Performs the deferred {@link #clearActiveSlot} — frees the emptied item and activates the next slot. */
  private void performActiveSlotClear() {
    WeaponItem item = weapons[activeSlotIndex];
    if (item == null) return;
    // Cancel the clear if a pickup merged more units into this stack during the ~100 ms hold window
    // (clearActiveSlot is only requested when the magazine hits 0 on the last throw; a same-tick
    // merge can refill it). Freeing it here regardless would silently drop the just-collected
    // grenades. Re-emit so the HUD reflects the refilled count.
    if (!item.isInfiniteAmmo && item.getMagazine() > 0) {
      ammoChanged.emit(item.getMagazine(), item.getReserve());
      return;
    }
    weapons[activeSlotIndex] = null;
    item.setup(null, null, null);
    item.hide();
    item.queueFree();
    activateFirstAvailableSlot();
  }
  public boolean isWeaponReloading()        { return reloadTimer.getTimeLeft() > 0; }
  public boolean isWeaponTransitioning()    { return transitionTimer.getTimeLeft() > 0; }

  /** 0..1 progress of an in-flight weapon switch (deploy phase), or -1 when not switching. For the HUD progress ring. */
  public double getSwitchProgress()  { return timerProgress(transitionTimer); }
  /** 0..1 progress of an in-flight reload, or -1 when not reloading. For the HUD progress ring. */
  public double getReloadProgress()  { return timerProgress(reloadTimer); }

  private static double timerProgress(Timer timer) {
    if (timer == null) return -1.0;
    double left = timer.getTimeLeft();
    if (left <= 0.0) return -1.0;
    double total = timer.getWaitTime();
    return total > 0.0 ? 1.0 - (left / total) : -1.0;
  }

  public boolean hasAmmoForWeapon(int slotIndex) {
    WeaponItem w = getWeaponItem(slotIndex);
    if (w == null) return false;
    if (w.isInfiniteAmmo) return true;
    return w.getMagazine() > 0 || w.getReserve() > 0;
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  /** First slot index with the given type that has no weapon. -1 if none. */
  private int findFreeSlot(WeaponSlotType type) {
    for (int i = 0; i < slotTypes.length; i++) {
      if (slotTypes[i] == type && weapons[i] == null) return i;
    }
    return -1;
  }

  /** First slot index with the given type, occupied or not. -1 if none configured. */
  private int findFirstSlot(WeaponSlotType type) {
    for (int i = 0; i < slotTypes.length; i++) {
      if (slotTypes[i] == type) return i;
    }
    return -1;
  }

  private EventBus getEventBus() {
    if (eventBus == null) {
      Node n = getNodeOrNull("/root/EventBus");
      if (n instanceof EventBus eb) eventBus = eb;
    }
    return eventBus;
  }

  private void emitArmedStateChanged(boolean armed) {
    EventBus bus = getEventBus();
    if (bus != null) bus.armedStateChanged.emit(getOwner(), armed);
  }

  /**
   * Replaces the AimRay used by the currently equipped weapon with {@code vehicleRay}.
   * Saves the original ray so {@link #restoreAimRay()} can undo the swap.
   * Called by Vehicle when PASSENGER_WEAPON occupant enters so the weapon fires along
   * the vehicle camera's forward direction rather than the character's camera ray.
   */
  public void overrideAimRay(RayCast3D vehicleRay) {
    originalAimRay = aimRay;
    aimRay = vehicleRay;
  }

  /** Restores the character's original AimRay after exiting PASSENGER_WEAPON mode. */
  public void restoreAimRay() {
    if (originalAimRay == null) return;
    aimRay = originalAimRay;
    originalAimRay = null;
  }

  private void injectCharacterRefs(WeaponItem item) {
    CharacterBody3D character = getOwner() instanceof CharacterBody3D c ? c : null;
    item.setup(this, character, weaponAudio);
  }

  private void showWeapon(int slotIndex) {
    for (int i = 0; i < weapons.length; i++) {
      if (weapons[i] == null) continue;
      if (i == slotIndex) moveWeaponToHand(weapons[i]);
      else moveWeaponToHolster(weapons[i]);
    }
  }

  /** Returns the Marker3D registered under {@code socketName}, or null if not found. */
  private Node resolveSocket(String socketName) {
    return (socketName == null || socketName.isEmpty()) ? null : socketMap.get(socketName);
  }

  /** Reparents {@code item} to its holdSocket Marker3D and shows it. An item with no holdSocket
   *  (a throwable) is stowed on the character, hidden — see {@link #stowWeapon}. */
  private void moveWeaponToHand(WeaponItem item) {
    Node target = resolveSocket(item.holdSocket);
    if (target != null) {
      reparentWeapon(item, target, false);
      item.show();
    } else {
      stowWeapon(item);
    }
  }

  /** Reparents {@code item} to the first free socket in its holsterSockets list and shows it.
   *  A socket is considered free when it has no children or already holds this weapon.
   *  An item with no free holster socket is stowed on the character, hidden. */
  private void moveWeaponToHolster(WeaponItem item) {
    for (String socketName : item.holsterSockets) {
      Node target = resolveSocket(socketName);
      if (target == null) continue;
      if (target.getChildCount() > 0 && !target.getChild(0).equals(item)) continue;
      reparentWeapon(item, target, true);
      item.show();
      return;
    }
    stowWeapon(item);
  }

  /**
   * Park a carried weapon that has no socket to show it at: hidden, on the character's weapon
   * attachment. It must GO somewhere — leaving it in the world scene is what the old frozen-body
   * workaround did, and a throwable carried that way sat invisible at wherever it was collected,
   * with a stale transform that nothing dared read.
   */
  private void stowWeapon(WeaponItem item) {
    item.hide();
    if (stowNode != null && GD.isInstanceValid(stowNode) && !stowNode.equals(item.getParent())) {
      item.reparent(stowNode, false);
      item.setTransform(new godot.core.Transform3D());
    }
  }

  /**
   * Reparents {@code item} to {@code target} and aligns it. Skips the reparent if {@code item} is
   * already a child of {@code target}, to avoid re-triggering {@code _ready}.
   *
   * <p>The alignment is the WEAPON's to state ({@code WeaponItem.alignmentFor}): a weapon that
   * declares a {@code GripPoint} puts THAT point on the socket, and one that declares none keeps
   * the historical zeroed transform, which puts its ORIGIN there. Zeroing unconditionally is what
   * forced a per-weapon marker onto the character rig for every weapon in the game — see
   * {@code WeaponItem.gripPoint} for why that is the wrong way round.
   */
  private void reparentWeapon(WeaponItem item, Node target, boolean holstered) {
    Node current = item.getParent();
    if (current != null && current.equals(target)) return;
    item.reparent(target, false);
    item.setTransform(item.alignmentFor(holstered));
  }

  // Manual drop: throw forward at chest height (1.3 m gives clearance when crouching/crawling).
  private void returnWeaponToWorld(WeaponItem item, int slot) {
    CharacterBody3D character = (CharacterBody3D) getOwner();
    Vector3 spawnPos = character.getGlobalPosition().plus(new Vector3(0, 1.3f, 0));
    Vector3 forward  = character.getGlobalTransform().getBasis().getZ().times(-1f);
    Vector3 impulse  = forward.times(3.0f)
        .plus(new Vector3(0, 4.0f, 0))
        .plus(character.getVelocity().times(0.3f));
    returnWeaponToWorld(item, spawnPos, impulse);
    announceWeaponDropped(item, slot, spawnPos, impulse, false);
  }

  // Death drop: spawn higher (1.5 m) and scatter each weapon in a random direction
  // so multiple weapons fan out instead of piling on the same spot.
  private void returnWeaponToWorldOnDeath(WeaponItem item, int slot) {
    CharacterBody3D character = (CharacterBody3D) getOwner();
    Vector3 spawnPos = character.getGlobalPosition().plus(new Vector3(0, 1.5f, 0));
    float   angle    = GD.randf() * (float) (Math.PI * 2.0);
    Vector3 scatter  = new Vector3((float) Math.cos(angle), 0f, (float) Math.sin(angle)).times(2.5f);
    Vector3 impulse  = scatter
        .plus(new Vector3(0, (float) GD.randfRange(3.0f, 6.0f), 0))
        .plus(character.getVelocity().times(0.4f));
    returnWeaponToWorld(item, spawnPos, impulse);
    announceWeaponDropped(item, slot, spawnPos, impulse, true);
  }

  /**
   * Replicates a locally-originated drop (Phase E). Only two peers may ever announce:
   * the character's OWNER for manual/displacement drops (the only peer whose input can
   * trigger them), and the HOST for death drops (Character.onDied runs only where health
   * is authoritative — including on the host's puppet of a dead client body, which the
   * owning client itself never executes). A host displacing a weapon while applying a
   * client's replicated equip announces nothing — the owner's own announcement converges
   * everyone via the oldPickupId fallback (GameManager.applyReplicatedDrop).
   *
   * The originator rolls a fresh UUID and adopts it before sending, so after the event
   * lands every peer agrees on the item's identity even if its previous path-derived id
   * differed per peer (AI scene loadouts).
   */
  private void announceWeaponDropped(WeaponItem item, int slot, Vector3 spawnPos, Vector3 impulse, boolean deathDrop) {
    if (!(getOwner() instanceof Character c) || c.characterInfo == null) return;
    Node netNode = getNodeOrNull("/root/NetworkManager");
    if (!(netNode instanceof NetworkManager net) || !net.isNetworked()) return;
    boolean mayAnnounce = deathDrop ? net.isServer() : net.isAuthorityFor(c.characterInfo);
    if (!mayAnnounce) return;
    String oldPickupId = item.pickupId;
    item.pickupId = java.util.UUID.randomUUID().toString();
    net.sendWeaponDropped(c.characterInfo.characterId, slot, oldPickupId, item.pickupId,
        spawnPos, impulse, item.getMagazine(), item.getReserve());
  }

  /**
   * Executes a replicated MSG_WEAPON_DROPPED on this peer's copy of the character: removes
   * the weapon from {@code slot} and returns it to the world at the originator's
   * position/impulse (never re-rolled — death scatter is randomized at the source), adopting
   * {@code newPickupId} and the carried ammo so the world item is identical everywhere.
   * Returns false when the slot is already empty (the displacement race) so the caller can
   * fall back to converging the already-dropped world item by its old id.
   */
  public boolean applyReplicatedDrop(int slot, String newPickupId, Vector3 spawnPos, Vector3 impulse,
      int magazine, int reserve) {
    WeaponItem item = getWeaponItem(slot);
    if (item == null) return false;
    boolean wasActive = slot == activeSlotIndex;
    weapons[slot] = null;
    item.hide();
    returnWeaponToWorld(item, spawnPos, impulse);
    item.pickupId = newPickupId;
    item.setMagazine(magazine);
    item.setReserve(reserve);
    if (wasActive) activateFirstAvailableSlot();
    return true;
  }

  // ── Inventory state reconciliation (Round 11 N2 — MSG_INVENTORY) ──────────
  //
  // The event-replicated inventory (MSG_PICKUP_TAKEN / MSG_WEAPON_DROPPED) can diverge
  // permanently from a single missed/raced event, and some inventory was never
  // event-replicated at all (AI rifles equipped at runtime via requestEquip). The host
  // periodically broadcasts each character's authoritative slot manifest; this pair
  // builds it (host side) and reconciles toward it (receiver side).

  /** Maximum pickupId length the wire accepts (NetworkManager.MAX_STRING_LENGTH) — oversized path-derived loadout ids are sent as "" (receiver keeps its local id). */
  private static final int MAX_WIRE_ID_LENGTH = 64;

  /** Host side: snapshot of every occupied slot (slot 0/fist excluded — permanent scene furniture). */
  public List<com.openworld.net.NetMessageCodec.InventorySlotEntry> buildInventoryEntries() {
    List<com.openworld.net.NetMessageCodec.InventorySlotEntry> entries = new ArrayList<>();
    for (int slot = 1; slot < weapons.length; slot++) {
      WeaponItem w = weapons[slot];
      if (w == null) continue;
      String scenePath = w.getSceneFilePath();
      String pickupId = (w.pickupId != null && !w.pickupId.isEmpty() && w.pickupId.length() <= MAX_WIRE_ID_LENGTH)
          ? w.pickupId : "";
      entries.add(new com.openworld.net.NetMessageCodec.InventorySlotEntry(slot,
          w.weaponId != null ? w.weaponId : "",
          scenePath != null ? scenePath : "",
          pickupId, w.getMagazine(), w.getReserve()));
    }
    return entries;
  }

  /**
   * Receiver side: reconcile this character's slots toward the host's manifest.
   *
   * <p>{@code addOnly} is true for the body this peer OWNS: its inventory is driven by its
   * own input plus the reliable event echoes, and overwriting it from a (lag-stale) manifest
   * would re-create the Round 10.2 echo feedback loop — so for owned bodies we only converge
   * pickupId on a matching slot, never remove items, touch ammo, or resurrect a slot the owner
   * emptied (the throwable-restock fix — see the loop body). Non-owned puppets reconcile fully:
   * match per slot by
   * weaponId, converge ammo + pickupId on match, equip from the manifest on mismatch
   * (preferring the matching local world pickup over instantiating a duplicate), and discard
   * local extras WITHOUT dropping them to the world (a reconcile drop would spawn orphan,
   * unsynced pickups).
   *
   * <p>Skipped entirely while local equips/drops are still queued — the manifest was built
   * before them and would fight their outcome; the next sweep (~300 ms) reconciles cleanly.
   */
  public void applyReplicatedInventory(List<com.openworld.net.NetMessageCodec.InventorySlotEntry> entries, boolean addOnly) {
    if (!pendingEquips.isEmpty() || !pendingDrops.isEmpty()) {
      com.openworld.net.NetStats.increment("inventory_apply_deferred");
      return;
    }
    Map<Integer, com.openworld.net.NetMessageCodec.InventorySlotEntry> bySlot = new HashMap<>();
    for (com.openworld.net.NetMessageCodec.InventorySlotEntry e : entries) bySlot.put(e.slot(), e);

    for (int slot = 1; slot < weapons.length; slot++) {
      com.openworld.net.NetMessageCodec.InventorySlotEntry entry = bySlot.get(slot);
      WeaponItem local = weapons[slot];

      if (entry == null) {
        if (local != null && !addOnly) {
          com.openworld.net.NetStats.increment("inventory_reconciled_remove");
          discardSlotItem(slot);
        }
        continue;
      }

      if (local != null) {
        if (manifestMatches(local, entry)) {
          if (!addOnly) {
            local.setMagazine(entry.magazine());
            local.setReserve(entry.reserve());
            notifyAmmoChange(local);
          }
          if (!entry.pickupId().isEmpty()) local.pickupId = entry.pickupId();
          continue;
        }
        if (addOnly) continue;   // owned body: never displace what the owner is holding
        com.openworld.net.NetStats.increment("inventory_reconciled_replace");
        discardSlotItem(slot);
      }

      // Reached only when the slot is locally empty but the manifest lists an item.
      // For an OWNED body, do NOT resurrect it: an owned body's slot presence is driven by
      // its own input and the RELIABLE, ordered grant/drop events — never by a lag-stale
      // manifest. This was the throwable-restock bug: a client throws its last grenade and
      // clears the slot, but the host's copy hasn't caught the throw (consumption rides no
      // reliable event, and the active-magazine snapshot can't carry the same-frame
      // slot-clear), so its manifest still lists the stack and the next sweep re-instantiated
      // it. The owner's inventory is restored on (re)join by baseline spawns + pickups, not by
      // this path; AI inventory is non-owned (full reconcile), so its runtime rifle still heals.
      if (addOnly) {
        com.openworld.net.NetStats.increment("inventory_owned_no_resurrect");
        continue;
      }
      equipReconciled(slot, entry);
    }
  }

  /** Same item identity? weaponId is the designed key; scenePath is the fallback for items with no id set. */
  private boolean manifestMatches(WeaponItem local, com.openworld.net.NetMessageCodec.InventorySlotEntry entry) {
    String localId = local.weaponId != null ? local.weaponId : "";
    if (!localId.isEmpty() || !entry.weaponId().isEmpty()) return localId.equals(entry.weaponId());
    String localScene = local.getSceneFilePath();
    return localScene != null && localScene.equals(entry.scenePath());
  }

  /**
   * Removes a slot's item during reconciliation — clears refs and frees it, never returns
   * it to the world (the manifest says the authority doesn't have it; a world drop here
   * would create an orphan pickup no other peer knows about).
   */
  private void discardSlotItem(int slot) {
    WeaponItem item = weapons[slot];
    if (item == null) return;
    boolean wasActive = slot == activeSlotIndex;
    weapons[slot] = null;
    item.setup(null, null, null);
    item.hide();
    item.queueFree();
    if (wasActive) activateFirstAvailableSlot();
  }

  /**
   * Materialises a manifest entry into {@code slot}: prefer adopting the matching local
   * world pickup by the manifest's pickupId (kills the ghost-pickup case when healing a
   * lost grant echo), else instantiate the validated weapon scene — the same
   * add-to-tree-then-equip shape DebugHarness.equipDebugRifle uses. Runs at idle time
   * (NetworkManager._process), where the item's world body can be taken away and rebuilt.
   */
  private void equipReconciled(int slot, com.openworld.net.NetMessageCodec.InventorySlotEntry entry) {
    WeaponItem item = findWorldPickupById(entry.pickupId());
    if (item == null) item = instantiateWeaponScene(entry.scenePath());
    if (item == null) {
      com.openworld.net.NetStats.increment("inventory_equip_failed");
      GD.print("WeaponController: inventory reconcile could not materialise '" + entry.weaponId()
          + "' (scene '" + entry.scenePath() + "') for slot " + slot + " on '" + getOwner().getName() + "'");
      return;
    }
    if (!entry.pickupId().isEmpty()) item.pickupId = entry.pickupId();
    item.setMagazine(entry.magazine());
    item.setReserve(entry.reserve());
    item.onPickedUp();
    injectCharacterRefs(item);
    weapons[slot] = item;
    if (slot == activeSlotIndex) {
      moveWeaponToHand(item);
      if (animationController != null) animationController.onWeaponEquip(item.weaponPoseIndex);
      ammoChanged.emit(item.getMagazine(), item.getReserve());
    } else {
      moveWeaponToHolster(item);
    }
    com.openworld.net.NetStats.increment("inventory_reconciled_equip");
  }

  /** Resolves a manifest pickupId to an un-taken WeaponItem in the world "pickups" group, or null. */
  private WeaponItem findWorldPickupById(String pickupId) {
    if (pickupId == null || pickupId.isEmpty() || getTree() == null) return null;
    for (Node node : getTree().getNodesInGroup(new StringName(com.openworld.item.Pickup.PICKUPS_GROUP))) {
      if (node instanceof WeaponItem w && pickupId.equals(w.pickupId) && !w.isTaken()) return w;
    }
    return null;
  }

  /** Loads + instantiates a manifest weapon scene into the current scene tree (must be in-tree before socket reparenting). Path already validated by NetworkManager.isValidInventory. */
  private WeaponItem instantiateWeaponScene(String scenePath) {
    if (scenePath == null || scenePath.isEmpty()) return null;
    java.lang.Object loaded = GD.load(scenePath);
    if (!(loaded instanceof PackedScene scene)) return null;
    Node instance = scene.instantiate();
    if (!(instance instanceof WeaponItem item)) {
      instance.queueFree();
      return null;
    }
    getTree().getCurrentScene().addChild(item);
    return item;
  }

  /**
   * Shared mechanics for both drop variants: clears character refs, un-equips the item, and hands
   * it to {@code Pickup.placeInWorld} — which builds the item's world body (a {@code PickupBody}
   * RigidBody3D that takes the item's authored collision shapes), puts it at {@code spawnPos} and
   * throws it.
   *
   * <p>Where the weapon currently hangs — a hand socket, a holster socket, the stow node, or
   * nowhere — does not matter here: it has no body to reparent, and building one is what puts it
   * back in the world. That is the whole reason this used to need a case analysis and a comment
   * about Jolt not propagating setGlobalPosition on a frozen body.
   */
  private void returnWeaponToWorld(WeaponItem item, Vector3 spawnPos, Vector3 impulse) {
    item.setup(null, null, null);
    item.show();
    item.onReturnedToWorld();
    item.placeInWorld(getTree().getCurrentScene(), spawnPos, impulse);
  }

  // After a drop, fall back to fist (slot 0) which is always available.
  private void activateFirstAvailableSlot() {
    for (int i = 0; i < weapons.length; i++) {
      if (weapons[i] != null) { onSetWeapon(i); return; }
    }
    ammoChanged.emit(0, 0);
  }

  private void emitInitialAmmoState() {
    WeaponItem w = getCurrentWeaponItem();
    if (w != null) ammoChanged.emit(w.getMagazine(), w.getReserve());
    else ammoChanged.emit(0, 0);
  }
}

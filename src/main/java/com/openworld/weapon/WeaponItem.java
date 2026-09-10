package com.openworld.weapon;

import com.openworld.world.manager.ImpactManager;
import com.openworld.item.Pickup;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.AnimationPlayer;
import godot.api.AudioStreamPlayer3D;
import godot.api.AudioStreamWAV;
import godot.api.CharacterBody3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.Texture2D;
import godot.core.NodePath;
import godot.core.PackedStringArray;
import godot.core.Transform3D;
import godot.core.Vector3;

import static godot.global.GD.min;
import com.openworld.ai.AIBehaviorConfig;
import com.openworld.character.AICharacter;
import com.openworld.character.Character;
import com.openworld.movement.character.Stance;

@Script(className = "WeaponItem")
public class WeaponItem extends Pickup implements WeaponAction {

  // Internal identifier used for event bus payloads and save keys. No spaces.
  @Export public String weaponId = "";

  // Human-readable display name: HUD, kill feed, inventory, interact prompt.
  @Export public String weaponName = "";

  // WeaponSlotType ordinal: 0=PRIMARY 1=SECONDARY 2=MELEE 3=THROWABLE 4=CONSUMABLE 5=FIST
  @Export public int slotType = 0;

  // When false the weapon cannot be dropped (e.g. FistItem). Guards dropCurrentWeapon/dropAllWeapons.
  @Export public boolean isDroppable = true;

  // When true magazine/reserve checks are bypassed — weapon has unlimited uses (e.g. FistItem).
  @Export public boolean isInfiniteAmmo = false;

  // When true, picking this up while the character is unarmed (fist active) makes it
  // the active weapon immediately instead of stowing it in a holster slot — for
  // consumables like throwables where instant access matters more than a deliberate
  // weapon-switch choice. Default false: rifles/pistols/melee always require an
  // explicit slot switch, matching how players expect ranged/melee pickups to behave.
  @Export public boolean autoEquipOnPickup = false;

  // Index into the AnimationTree weapon blend nodes (WeaponAim, WeaponHold, WeaponChangeAnimation).
  // Decoupled from slot so the same animation pose is used regardless of which slot holds the weapon.
  @Export public int weaponPoseIndex = 0;

  // Icon shown in the kill feed and radial menu. Set in the inspector per weapon scene.
  @Export public Texture2D weaponIcon = null;

  // Name of the Marker3D socket to attach to when this weapon is the active (held) weapon.
  // Must match a node name registered in WeaponController.socketPaths.
  // ── How this weapon SITS in a socket, and how its own parts MOVE ──────────
  //
  // Both of these are facts about the WEAPON, and both used to have nowhere to live.

  /**
   * Child {@code Marker3D} naming the point this weapon is HELD by — where the hand's socket
   * should land on it. Empty (or missing) keeps the historical behaviour: the weapon's local
   * transform is zeroed, so its ORIGIN lands on the socket.
   *
   * <p><b>This inverts who owns the fit.</b> Zeroing meant the character's socket carried the whole
   * placement, so every weapon needed its own marker on the character rig
   * ({@code MarkerAR4}, {@code MarkerATL4}, ...) — adding a weapon meant editing the CHARACTER, the
   * fit could not travel with the weapon to another rig, and a weapon's own anatomy (where its grip
   * is) was written down nowhere. With a grip point the character needs one socket per GRIP
   * ARCHETYPE and each weapon aligns itself to it, which is the same split
   * {@code weaponPoseIndex} already makes for the pose.
   *
   * <p>A launcher is the case that shows why it matters: it rides on the SHOULDER, so its tube sits
   * high and back relative to the hand compared to a rifle. That is a translation of this marker,
   * not a new animation — though the ARM pose really is authored art (`weaponPoseIndex` 2).
   */
  @Export public String gripPoint = "GripPoint";

  /** As {@link #gripPoint}, for the pose this weapon takes when HOLSTERED — a rifle hangs on a back
   *  socket by a different point than the one the hand grips. Empty falls back to the grip point. */
  @Export public String holsterPoint = "";

  /**
   * Optional {@code AnimationPlayer} inside this weapon's own scene, for MOVING PARTS — a shotgun
   * pump, a bolt, a revolver cylinder, a folding stock.
   *
   * <p>It is the weapon's own player on purpose. Putting those parts in the character's skeleton
   * would tie every weapon to one rig and one clip set, and the part has to keep moving while the
   * character is doing something else entirely. What the character and the weapon share is the
   * EVENT, not the animation: {@code WeaponController} plays both from the same fire/reload moment,
   * on the authority AND on a puppet, so a remote peer sees the pump cycle too.
   */
  @Export public NodePath weaponAnimatorPath = new NodePath("WeaponAnimator");

  /** Clip on {@link #weaponAnimatorPath} to play per shot. Empty = this weapon has no moving parts. */
  @Export public String fireAnimation = "";

  /** Clip on {@link #weaponAnimatorPath} to play on reload. Empty = none. */
  @Export public String reloadAnimation = "";

  private AnimationPlayer weaponAnimator;
  private boolean weaponAnimatorResolved = false;

  /**
   * The local transform this weapon takes when parented to {@code socketName}'s marker, so that its
   * grip (or holster) point coincides with the socket. Identity when it declares none.
   */
  @Register
  public Transform3D alignmentFor(boolean holstered) {
    String name = holstered && !holsterPoint.isEmpty() ? holsterPoint : gripPoint;
    if (name == null || name.isEmpty()) return new Transform3D();
    Node n = getNodeOrNull(new NodePath(name));
    if (!(n instanceof Node3D marker)) return new Transform3D();
    return marker.getTransform().affineInverse();
  }

  /**
   * Play one of this weapon's own motion clips, if it has an animator and the clip exists.
   *
   * <p>Silent about a missing clip by design — a weapon with no moving parts names none, and every
   * call site fires for every weapon.
   */
  public void playMotion(String clip) {
    if (clip == null || clip.isEmpty()) return;
    if (!weaponAnimatorResolved) {
      weaponAnimatorResolved = true;
      Node n = (weaponAnimatorPath == null || weaponAnimatorPath.isEmpty())
              ? null : getNodeOrNull(weaponAnimatorPath);
      if (n instanceof AnimationPlayer ap) weaponAnimator = ap;
    }
    if (weaponAnimator == null || !weaponAnimator.hasAnimation(clip)) return;
    weaponAnimator.stop();
    weaponAnimator.play(clip, -1.0, 1.0f, false);
  }

  @Export public String holdSocket = "";

  // Names of Marker3D sockets to try (in order) when parking this weapon in inventory.
  // Each name must match a node registered in WeaponController.socketPaths.
  // The first socket with no other weapon in it is used. Empty array = hide when inactive.
  @Export public PackedStringArray holsterSockets = new PackedStringArray();

  @Export public float spread = 0.0f;
  // Inaccuracy added per shot; decays at bloomDecaySpeed when not firing.
  // Set bloomDecaySpeed lower than (bloomPerShot × fireRate) for bloom to
  // accumulate during full-auto. Set it higher for semi-auto tap-fire weapons
  // where each shot clears before the next.
  @Export public float bloomPerShot    = 0.0f;
  @Export public float bloomDecaySpeed = 1.0f;
  @Export public float bloomMax        = 0.25f;
  @Export public float reloadSpeed = 0.8f;
  // switchSpeed is a rate: deploy time = 1/switchSpeed. 2.2 ⇒ ~0.45 s deploy (CS/PUBG-snappy); the
  // post-deploy fire lockout is a small fixed constant (WeaponController.DRAW_SETTLE_SECONDS), not a
  // second full 1/switchSpeed, so total switch ≈ deploy time.
  @Export public float switchSpeed = 2.2f;
  @Export public float fireRate = 8.0f;
  @Export public boolean auto = true;
  @Export public int magazine = 40;
  @Export public int magazineSize = 40;
  @Export public int reserve = 40;
  @Export public int reserveMax = 40;
  @Export public float recoil = 0.8f;
  @Export public float damage = 25.0f;

  // Effective engagement distance in metres. AI uses this (via AICharacter.getEffectiveAttackRange)
  // to cap how far it will try to fight with this weapon — e.g. a melee AI closes to arm's
  // reach instead of standing at AIBehaviorConfig.attackRange and swinging at empty air.
  // MeleeItem overrides getEffectiveRange() to return meleeRange so the two stay in sync.
  @Export public float weaponRange = 50.0f;
  @Export public AudioStreamWAV fireAudio;
  @Export public AudioStreamWAV reloadAudio;

  // ── Injected references (shared by all weapon subtypes) ─────────────────────
  // Populated by WeaponController.injectCharacterRefs() after discovery/pickup.
  // Cleared (set to null) when the weapon is returned to the world.
  protected WeaponController       weaponController;
  protected CharacterBody3D        owningCharacter;
  protected AudioStreamPlayer3D    weaponAudio;
  private   ImpactManager          impactManager;

  /** Called by WeaponController after discovery or pickup. Pass nulls to clear on world return. */
  public void setup(WeaponController controller, CharacterBody3D character, AudioStreamPlayer3D audio) {
    this.weaponController = controller;
    this.owningCharacter  = character;
    this.weaponAudio      = audio;
    this.impactManager    = null;
  }

  /** Lazily resolves and caches the world ImpactManager (single group lookup per weapon). */
  protected ImpactManager getImpactManager() {
    if (impactManager != null) return impactManager;
    Node found = getTree().getFirstNodeInGroup("impact_manager");
    if (found instanceof ImpactManager im) impactManager = im;
    return impactManager;
  }

  /** Attacker display name for kill-feed and event-bus payloads. */
  protected String resolveAttackerName() {
    if (owningCharacter instanceof Character c && c.characterInfo != null) return c.characterInfo.displayName;
    return owningCharacter != null ? owningCharacter.getName().toString() : "";
  }

  /** Attacker faction string for faction/friendly-fire checks. */
  protected String resolveAttackerFaction() {
    if (owningCharacter instanceof Character c && c.characterInfo != null) return c.characterInfo.faction;
    return "";
  }

  /** World position of the shooter, for the HUD damage-direction indicator. Null when unknown. */
  protected godot.core.Vector3 resolveAttackerPosition() {
    return owningCharacter != null ? owningCharacter.getGlobalPosition() : null;
  }

  /**
   * The exported {@code slotType} ordinal as its enum. Not {@code getSlotType} — that shape
   * would bind the int field to an enum-typed accessor property (see AICharacter).
   */
  public WeaponSlotType resolveSlotType() {
    WeaponSlotType[] types = WeaponSlotType.values();
    if (slotType >= 0 && slotType < types.length) return types[slotType];
    return WeaponSlotType.PRIMARY;
  }

  /** Returns weaponName if set, otherwise the node name. Use everywhere a display name is needed. */
  public String getDisplayName() {
    return weaponName.isEmpty() ? getName().toString() : weaponName;
  }

  // ── Pickup callbacks ──────────────────────────────────────────────────────

  /**
   * Registers this weapon's kill-feed icon under its weaponName so the networked
   * kill feed can resolve it locally (textures never cross the wire — see
   * {@link IconRegistry}). MUST call super._ready() or the Pickup base never
   * registers for replication.
   */
  @Register
  @Override
  public void _ready() {
    super._ready();
    IconRegistry.register(weaponName, weaponIcon);
  }

  /**
   * Auto-pickup when the character's matching slot is free; otherwise show the
   * interact prompt so the player consciously chooses to swap their current weapon.
   */
  @Override
  protected boolean shouldAutoPickup(Node character) {
    Node wcNode = character.getNodeOrNull(WEAPON_CONTROLLER_PATH);
    if (wcNode instanceof WeaponController wc) return wc.isSlotFreeFor(resolveSlotType());
    return false;
  }

  @Override
  protected void onCharacterEntered(Node character) {
    Node wcNode = character.getNodeOrNull(WEAPON_CONTROLLER_PATH);
    if (wcNode instanceof WeaponController wc) {
      // Set equipped immediately to prevent re-triggering during the deferred frame,
      // then queue the actual equip so reparent() runs in _process (idle), not
      // inside the Area3D body_entered signal (physics callback).
      equipped = true;
      wc.requestEquip(this);
    }
  }

  @Override
  protected String getInteractLabel() {
    return getDisplayName();
  }

  // ── Replication hooks (Pickup base) ───────────────────────────────────────
  // MSG_PICKUP_TAKEN carries the item's magazine/reserve at take time so every peer's
  // copy of the weapon ends up byte-identical regardless of prior local drift.

  @Override public int getReplicatedMagazine() { return magazine; }
  @Override public int getReplicatedReserve()  { return reserve; }

  @Override
  protected void stampReplicatedAmmo(int replicatedMagazine, int replicatedReserve) {
    magazine = replicatedMagazine;
    reserve  = replicatedReserve;
  }

  /** Current holder (set by WeaponController.setup), or null while in the world — used for the late-join pickup baseline. */
  public CharacterBody3D getOwningCharacter() { return owningCharacter; }

  // ── WeaponAction defaults — concrete subclasses override what they need ───
  // Semi-auto lock: set true in useWeapon() after a shot, cleared on stopUseWeapon()
  // (i.e. on trigger release). Subclasses gate canUse() on isSemiAutoReady() so that a
  // non-auto weapon produces exactly one use per trigger pull regardless of fireRate.
  protected boolean isWeaponFired = false;

  /**
   * True when the trigger may produce another use: full-auto weapons (auto) always,
   * semi-auto weapons only after the trigger was released since the last shot.
   */
  protected boolean isSemiAutoReady() { return !isWeaponFired || auto; }

  @Override public void useWeapon() {}
  // Trigger released — clear the semi-auto lock so the next pull can fire.
  @Override public void stopUseWeapon() { isWeaponFired = false; }
  @Override public void onReloadComplete() {}
  @Override public boolean canUse() { return false; }
  @Override public WeaponType getWeaponType() { return WeaponType.RANGED; }
  @Override public float getCurrentSpreadDeg() { return 0f; }

  /**
   * Crosshair openness for the CURRENT accuracy state, as a 0..1 fraction of this weapon's own
   * worst-case spread envelope — a purely cosmetic, weapon-normalized signal for the HUD reticle
   * (the real bullet cone is {@link #getCurrentSpreadDeg()}). Normalizing per weapon means the
   * crosshair uses the same fixed pixel range for every gun (no per-weapon crosshair tuning, never
   * runs off-screen), while still reflecting movement/bloom/stance changes within that range.
   * Default 0 (weapons with no spread keep a tight reticle).
   */
  public float getCrosshairFraction() { return 0f; }

  @Override public void onSetStance(Stance stance) {}

  /**
   * Whether dropping this weapon should create a world pickup.
   * Default: matches isDroppable. ThrowableItem overrides to block empty drops.
   */
  public boolean shouldDropToWorld() { return isDroppable; }

  /**
   * Called by WeaponController immediately after useWeapon() empties the magazine.
   * Default is a no-op. ThrowableItem overrides to auto-clear the slot so any
   * other throwable type can be picked up without needing an interact-to-swap.
   */
  public void onMagazineEmpty() {}

  /**
   * Cosmetic remote replay on non-authority peers (puppets), invoked by
   * WeaponController.playRemoteFireCue when the snapshot's fireSeq counter advances.
   * Default no-op; FirearmItem replays muzzle/tracer, throwable/projectile weapons spawn a
   * non-damaging projectile so every peer sees the shot/throw + explosion. Never consumes
   * ammo or applies damage — those stay authority-side.
   */
  public void playRemoteFireCue() {}

  public void decrementMagazine() {
    if (magazine > 0) magazine--;
  }

  public void fillMagazine() {
    int emptySpace = magazineSize - magazine;
    magazine += min(emptySpace, reserve);
    reserve -= min(emptySpace, reserve);
  }

  public void fillAmmo() {
    reserve = reserveMax;
    magazine = magazineSize;
  }

  public float getSpread() { return spread; }
  public void setSpread(float spread) { this.spread = spread; }

  public float getBloomPerShot()    { return bloomPerShot; }
  public void  setBloomPerShot(float v)    { bloomPerShot    = v; }
  public float getBloomDecaySpeed() { return bloomDecaySpeed; }
  public void  setBloomDecaySpeed(float v) { bloomDecaySpeed = v; }
  public float getBloomMax()        { return bloomMax; }
  public void  setBloomMax(float v)        { bloomMax        = v; }

  public float getReloadSpeed() { return reloadSpeed; }
  public void setReloadSpeed(float reloadSpeed) { this.reloadSpeed = reloadSpeed; }

  public float getSwitchSpeed() { return switchSpeed; }
  public void setSwitchSpeed(float switchSpeed) { this.switchSpeed = switchSpeed; }

  public float getFireRate() { return fireRate; }
  public void setFireRate(float fireRate) { this.fireRate = fireRate; }

  public boolean isAuto() { return auto; }
  public void setAuto(boolean auto) { this.auto = auto; }

  public int getMagazine() { return magazine; }
  public void setMagazine(int magazine) { this.magazine = magazine; }

  public int getMagazineSize() { return magazineSize; }
  public void setMagazineSize(int magazineSize) { this.magazineSize = magazineSize; }

  public int getReserve() { return reserve; }
  public void setReserve(int reserve) { this.reserve = reserve; }

  public int getReserveMax() { return reserveMax; }
  public void setReserveMax(int reserveMax) { this.reserveMax = reserveMax; }

  public float getRecoil() { return recoil; }
  public void setRecoil(float recoil) { this.recoil = recoil; }

  /** Effective engagement distance in metres — overridden by MeleeItem to mirror meleeRange. */
  public float getEffectiveRange() { return weaponRange; }

  public AudioStreamWAV getFireAudio() { return fireAudio; }
  public void setFireAudio(AudioStreamWAV fireAudio) { this.fireAudio = fireAudio; }

  public AudioStreamWAV getReloadAudio() { return reloadAudio; }
  public void setReloadAudio(AudioStreamWAV reloadAudio) { this.reloadAudio = reloadAudio; }
}

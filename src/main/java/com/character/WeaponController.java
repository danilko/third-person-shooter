package com.character;
import com.util.ObjectPool;
import godot.annotation.*;
import godot.api.*;
import godot.api.CharacterBody3D;
import godot.api.Object;
import godot.core.NodePath;
import godot.core.Signal1;
import godot.core.Signal2;
import godot.core.StringName;
import godot.core.VariantArray;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;

@RegisterClass(className = "WeaponController")
public class WeaponController extends Node {

  @RegisterProperty
  @Export
  public AnimationController animationController;

  @RegisterProperty
  @Export
  public NodePath aimRayPath = new NodePath("CameraRoot/Yaw/Pitch/Pivot/SpringArm/Camera/AimRay");

  @RegisterProperty
  @Export
  public NodePath splattersPath = new NodePath("Splatters");

  @RegisterProperty
  @Export
  public NodePath cameraControllerPath = new NodePath("CameraRoot");


  @RegisterProperty
  @Export
  public NodePath weaponAttachmentPath = new NodePath("MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/WeaponAttachment");


  @RegisterSignal
  public final Signal1<Float> weaponFired = new Signal1<>(this, new StringName("weapon_fired"));

  /** Emitted whenever magazine or backup ammo count changes (mag, ammoBackup). */
  @RegisterSignal
  public final Signal2<Integer, Integer> ammoChanged = new Signal2<>(this, new StringName("ammo_changed"));


  @RegisterProperty
  @Export
  public AudioStreamPlayer3D weaponAudio;

  @RegisterProperty
  @Export
  public BoneAttachment3D neckBoneAttachement;

  private AnimationPlayer muzzleFlashAnimationPlayer;

  private ArrayList<WeaponStats> weapons = new ArrayList<>();

  private Timer transitionTimer;
  private Timer fireTimer;
  private Timer reloadTimer;

  private RayCast3D aimRay3D;

  // Round-robin VFX pool: acquire → position → emit → release immediately.
  // Particles continue emitting independently; the pool just tracks order.
  private ObjectPool<GPUParticles3D> splatterPool;

  public int getWeapon() {
    return weapon;
  }

  public WeaponStats getCurrentWeaponStats() {
    return weapons.get(weapon);
  }

  public int getWeaponCount() {
    return weapons.size();
  }

  private int weapon = 0;
  private int pendingWeapon = 0;
  private boolean isWeaponFired = false;

  // Bloom: accumulated per shot, decays toward 0 while not firing.
  // Rates and cap are per-weapon (stored in WeaponStats).
  private float currentBloom = 0.0f;

  // Spread increase per m/s of character velocity (applied before stance multiplier).
  private static final float MOVEMENT_SPREAD_PER_MPS = 0.12f;

  // Stance spread multipliers: proportion of (base + movement + bloom) that lands.
  private static final float CROUCH_SPREAD_MULT = 0.7f;
  private static final float CRAWL_SPREAD_MULT  = 0.5f;
  private static final float JUMP_SPREAD_MULT   = 2.0f;

  private StanceName currentStance = StanceName.UPRIGHT;

  @RegisterFunction
  @Override
  public void _physicsProcess(double delta) {
    if (!weapons.isEmpty()) {
      currentBloom = Math.max(0f, currentBloom - getCurrentWeaponStats().getBloomDecaySpeed() * (float) delta);
    }
  }

  /**
   * Current total spread in degrees: (base + movement + bloom) × stance multiplier.
   * Used by WeaponController for ballistics and by MovementController for the crosshair.
   */
  public float getCurrentSpreadDeg() {
    if (weapons.isEmpty()) return 0f;
    WeaponStats stats = getCurrentWeaponStats();
    float speed = (float) ((CharacterBody3D) getOwner()).getVelocity().length();
    float raw  = stats.getSpread() + speed * MOVEMENT_SPREAD_PER_MPS + currentBloom;
    return Math.max(0f, raw * stanceMultiplier());
  }

  private float stanceMultiplier() {
    if (!((CharacterBody3D) getOwner()).isOnFloor()) return JUMP_SPREAD_MULT;
    return switch (currentStance) {
      case CROUCH -> CROUCH_SPREAD_MULT;
      case CRAWL  -> CRAWL_SPREAD_MULT;
      default     -> 1.0f;
    };
  }

  @RegisterFunction
  public void onSetStance(Stance stance) {
    currentStance = StanceName.fromKey(String.valueOf(stance.getName()));
  }

  @RegisterFunction
  @Override
  public void _ready(){
    transitionTimer = (Timer) getNode("TransitionTimer");
    fireTimer = (Timer) getNode("FireTimer");
    reloadTimer = (Timer) getNode("ReloadTimer");

    // Discover all WeaponStats children dynamically — add more weapon nodes to
    // the scene without touching this class.
    for (Node child : getOwner().getNode(weaponAttachmentPath).getChildren()) {
      if (child instanceof WeaponStats) {
        weapons.add((WeaponStats) child);
      }
    }

    muzzleFlashAnimationPlayer = ((AnimationPlayer)neckBoneAttachement.getNode("AnimationPlayer"));
    // Hide the muzzle flash by playing once as a workaround
    muzzleFlashAnimationPlayer.play("MuzzleFlash");

    if (getOwner().hasNode(aimRayPath)) {
      aimRay3D = (RayCast3D) getOwner().getNode(aimRayPath);
    }

    ArrayList<GPUParticles3D> splatterNodes = new ArrayList<>();
    for (Node splatterNode : getOwner().getNode(splattersPath).getChildren()) {
      splatterNodes.add((GPUParticles3D) splatterNode);
    }
    int poolSize = splatterNodes.size();
    int[] idx = {0};
    // Factory cycles through the pre-existing scene nodes; reset is a no-op
    // because particles continue emitting fire-and-forget after release.
    splatterPool = new ObjectPool<>(poolSize,
        () -> splatterNodes.get(idx[0]++),
        p -> {});  // no reset — particle keeps playing after release
    emitInitialAmmoState();
  }

  @RegisterFunction
  public void onWeaponFire() {
    if(fireTimer.getTimeLeft() > 0 || reloadTimer.getTimeLeft() > 0) {
      return;
    }

    // If not auto, require release before allow to fire again
    if(isWeaponFired && !weapons.get(weapon).isAuto()) {
      return;
    }

    if(weapons.get(weapon).mag == 0) {
      onWeaponReload();
      return;
    }

    fireTimer.setWaitTime(1 / weapons.get(weapon).getFireRate());
    weaponAudio.stop();
    weaponAudio.setStream(weapons.get(weapon).getFireAudio());

    weapons.get(weapon).decrementMag();
    ammoChanged.emit(weapons.get(weapon).getMag(), weapons.get(weapon).getAmmoBackup());

    fireTimer.start();


    isWeaponFired = true;

    weaponFired.emit((weapons.get(weapon).getFireRate() * 0.2f));

    ((GPUParticles3D)neckBoneAttachement.getNode("MuzzleFlash")).setSpeedScale(getCurrentWeaponStats().getFireRate());
    ((GPUParticles3D)neckBoneAttachement.getNode("Streaks")).getProcessMaterial().set("directional_velocity_max", 8000.0f/ getCurrentWeaponStats().getFireRate());
    muzzleFlashAnimationPlayer.setSpeedScale(GD.clamp(getCurrentWeaponStats().getFireRate(), 5, 10));


    muzzleFlashAnimationPlayer.play("MuzzleFlash");

    weaponAudio.play();


    // Route recoil through CameraController so it accumulates and recovers properly
    if (getOwner().hasNode(cameraControllerPath)) {
      Node camNode = getOwner().getNode(cameraControllerPath);
      if (camNode instanceof CameraController cam) {
        float recoil = getCurrentWeaponStats().getRecoil();
        float horizRecoil = (float) GD.randfRange(-recoil * 0.3f, recoil * 0.3f);
        cam.applyRecoil(recoil, horizRecoil);
      }
    }

    // Accumulate per-shot bloom (per-weapon rate and cap)
    WeaponStats stats = getCurrentWeaponStats();
    currentBloom = Math.min(currentBloom + stats.getBloomPerShot(), stats.getBloomMax());

    if (aimRay3D != null) {
      // Player: apply angular spread + force update. Enemy: snapAimRay already
      // positioned the ray (with scatter baked in) and called forceRaycastUpdate —
      // rotating it again would override that carefully placed aim.
      boolean applySpread = getOwner() instanceof Character
          && ((Character) getOwner()).useWeaponSpread;

      Vector3 savedRot = null;
      if (applySpread) {
        savedRot = aimRay3D.getRotationDegrees();
        float halfSpread = getCurrentSpreadDeg() * 0.5f;
        float pitchOff = (float) GD.randfRange(-halfSpread, halfSpread);
        float yawOff   = (float) GD.randfRange(-halfSpread, halfSpread);
        aimRay3D.setRotationDegrees(new Vector3(savedRot.getX() + pitchOff, savedRot.getY() + yawOff, 0f));
        aimRay3D.forceRaycastUpdate();
      }

      if (aimRay3D.isColliding() &&
          (aimRay3D.getCollisionPoint().minus(aimRay3D.getGlobalTransform().getOrigin())).length() > 0.1) {
        Object collider = aimRay3D.getCollider();
        if (collider instanceof godot.api.Node hitNode) {
          if (hitNode.getOwner().hasNode(new NodePath("Health"))) {
            Health health = (Health) hitNode.getOwner().getNode(new NodePath("Health"));
            String weaponName = getCurrentWeaponStats().getName().toString();
            String attackerName = getOwner().getName().toString();
            health.takeDamage(hitNode, getCurrentWeaponStats().damage, weaponName, attackerName);
          }
        }
        GPUParticles3D splatter = splatterPool.acquire();
        splatter.setGlobalPosition(aimRay3D.getCollisionPoint());
        splatter.setEmitting(true);
        splatterPool.release(splatter);
      }

      if (savedRot != null) {
        aimRay3D.setRotationDegrees(savedRot);
      }
    }
  }

  public void fillWeaponAmmo() {
    for(int index = 0; index < weapons.size(); index++) {
      weapons.get(index).fillAmmo();;
    }

    ammoChanged.emit(weapons.get(weapon).getMag(), weapons.get(weapon).getAmmoBackup());
  }

  @RegisterFunction
  public void onWeaponReload() {
    if(weapons.get(weapon).ammoBackup == 0 || isWeaponReloading()) {
      return;
    }
    reloadTimer.setWaitTime(1 / weapons.get(weapon).getReloadSpeed());
    weaponAudio.setStream(weapons.get(weapon).getReloadAudio());

    reloadTimer.start();

    animationController.onWeaponReload();
    weaponAudio.play();
  }

  @RegisterFunction
  public void onWeaponReloadComplete() {
    weapons.get(weapon).fillMag();
    ammoChanged.emit(weapons.get(weapon).getMag(), weapons.get(weapon).getAmmoBackup());
  }

 public boolean isWeaponReloading(){
    return reloadTimer.getTimeLeft() > 0;
  }

  /** True while a weapon-switch animation is in flight (transitionTimer hasn't fired yet). */
  public boolean isWeaponTransitioning() {
    return transitionTimer.getTimeLeft() > 0;
  }

  public boolean hasAmmoForWeapon(int index) {
    if (index < 0 || index >= weapons.size()) return false;
    WeaponStats stats = weapons.get(index);
    return stats.getMag() > 0 || stats.getAmmoBackup() > 0;
  }

  public WeaponStats getWeaponStats(int index) {
    if (index < 0 || index >= weapons.size()) return null;
    return weapons.get(index);
  }

  @RegisterFunction
  public void onWeaponNotFire() {
    isWeaponFired = false;
  }

  @RegisterFunction
  public void onSetWeapon(int weapon) {
    showWeapon(this.weapon);

    // No need to switch weapon
    if (weapon == this.weapon) {
      return;
    }

    pendingWeapon = weapon;

    // force show current weapon
    showWeapon(this.weapon);

    // unequip current weapon
    animationController.onWeaponTransition(this.weapon, false);
    transitionTimer.start();
  }

  @RegisterFunction
  public void onWeaponTransitionComplete() {
    // assign weapon to pending weapon
    weapon = pendingWeapon;
    // force show current weapon
    showWeapon(weapon);
    // equip current weapon
    animationController.onWeaponTransition(weapon, true);
    // notify HUD of the newly equipped weapon's ammo
    ammoChanged.emit(weapons.get(weapon).getMag(), weapons.get(weapon).getAmmoBackup());
  }


  /** Emit initial ammo state so HUD listeners can populate on first frame. */
  private void emitInitialAmmoState() {
    if (!weapons.isEmpty()) {
      ammoChanged.emit(weapons.get(weapon).getMag(), weapons.get(weapon).getAmmoBackup());
    }
  }


  // workaround for animation controller for now
  private void showWeapon(int weapon){
    for (int i = 0; i < weapons.size(); i++) {
      if(i != weapon) {
        weapons.get(i).hide();
      }
    }

    weapons.get(weapon).show();
  }
}

package com.character;
import com.util.ObjectPool;
import godot.annotation.*;
import godot.api.*;
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
  public NodePath recoilPitchPath = new NodePath("CameraRoot/Yaw/Pitch");


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


    if (getOwner().hasNode(recoilPitchPath)) {
      ((Node3D) getOwner().getNode(recoilPitchPath)).rotateX((float) GD.degToRad(getCurrentWeaponStats().getRecoil()));
    }

    // Apply spread and check for collision
    if(aimRay3D != null) {
      Vector3 currentRotationDegree = aimRay3D.getRotationDegrees();
      float spread = getCurrentWeaponStats().getSpread();
      aimRay3D.setRotationDegrees(new Vector3(currentRotationDegree.getX(), 0.5 * GD.randfRange(-spread, spread), 0.5 * GD.randfRange(-spread, spread)));
    }

    // final check for collision point
    if(aimRay3D != null && aimRay3D.isColliding() &&  (aimRay3D.getCollisionPoint().minus(aimRay3D.getGlobalTransform().getOrigin())).length() > 0.1) {
      // Apply damage if the hit body has a Health node
      Object collider = aimRay3D.getCollider();
      if (collider instanceof godot.api.Node) {
        godot.api.Node hitNode = (godot.api.Node) collider;
        if (hitNode.getOwner().hasNode(new NodePath("Health"))) {
          ((Health) hitNode.getOwner().getNode(new NodePath("Health"))).takeDamage(hitNode, getCurrentWeaponStats().damage);
        }
      }

      GPUParticles3D splatter = splatterPool.acquire();
      splatter.setGlobalPosition(aimRay3D.getCollisionPoint());
      splatter.setEmitting(true);
      splatterPool.release(splatter); // immediately back; particle keeps emitting
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

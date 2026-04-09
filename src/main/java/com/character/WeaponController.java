package com.character;
import godot.annotation.*;
import godot.api.*;
import godot.api.Object;
import godot.core.NodePath;
import godot.core.Signal1;
import godot.core.VariantArray;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;

@RegisterClass(className = "WeaponController")
public class WeaponController extends Node {

  @RegisterProperty
  @Export
  public WeaponStats pistol;
  @RegisterProperty
  @Export
  public WeaponStats rifle;

  @RegisterProperty
  @Export
  public AnimationController animationController;

  @RegisterProperty
  @Export
  public NodePath magLabelPath = new NodePath("UI/Mag/ColorRect/Mag");

  @RegisterProperty
  @Export
  public NodePath ammoBackupLabelPath = new NodePath("UI/Mag/ColorRect/AmmoBackup");

  @RegisterProperty
  @Export
  public NodePath rayCastPath = new NodePath("CameraRoot/Yaw/Pitch/Pivot/SpringArm/Camera/RayCast3D");

  @RegisterProperty
  @Export
  public NodePath spineIKTargetPath = new NodePath("CameraRoot/Yaw/Pitch/Pivot/SpringArm/Camera/SpineIKTarget");

  @RegisterProperty
  @Export
  public NodePath splattersPath = new NodePath("Splatters");

  @RegisterProperty
  @Export
  public NodePath recoilPitchPath = new NodePath("CameraRoot/Yaw/Pitch");

  @RegisterSignal
  public final Signal1<Float> weaponFired = Signal1.create(this, "weaponFired");

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

  private Label magLabel;
  private Label ammoBackupLabel;
  private RayCast3D rayCast3D;
  private Marker3D marker3D;

  private int currentSplatersIndex = 0;
  private int maxSplatterSize = 0;


  private ArrayList<GPUParticles3D> splatters = new ArrayList<>();

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

    magLabel = (Label) getOwner().getNode(magLabelPath);
    ammoBackupLabel = (Label) getOwner().getNode(ammoBackupLabelPath);

    weapons.add(pistol);
    weapons.add(rifle);

    muzzleFlashAnimationPlayer = ((AnimationPlayer)neckBoneAttachement.getNode("AnimationPlayer"));
    // Hide the muzzle flash by play once as a workaround
    muzzleFlashAnimationPlayer.play("MuzzleFlash");

    rayCast3D = (RayCast3D) getOwner().getNode(rayCastPath);

    marker3D = (Marker3D) getOwner().getNode(spineIKTargetPath);

    for (Node splatterNode : getOwner().getNode(splattersPath).getChildren()) {
        splatters.add((GPUParticles3D) splatterNode);
    }

    maxSplatterSize = splatters.size();

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

    fireTimer.start();


    isWeaponFired = true;

    weaponFired.emit((weapons.get(weapon).getFireRate() * 0.2f));

    ((GPUParticles3D)neckBoneAttachement.getNode("MuzzleFlash")).setSpeedScale(getCurrentWeaponStats().getFireRate());
    ((GPUParticles3D)neckBoneAttachement.getNode("Streaks")).getProcessMaterial().set("directional_velocity_max", 8000.0f/ getCurrentWeaponStats().getFireRate());
    muzzleFlashAnimationPlayer.setSpeedScale(GD.clamp(getCurrentWeaponStats().getFireRate(), 5, 10));


    muzzleFlashAnimationPlayer.play("MuzzleFlash");

    weaponAudio.play();


    Vector3 currentRotationDegree = rayCast3D.getRotationDegrees();
    float spread = getCurrentWeaponStats().getSpread();
    rayCast3D.setRotationDegrees(new Vector3( currentRotationDegree.getX(), 0.5 * GD.randfRange(-spread, spread), 0.5 * GD.randfRange(-spread, spread)));

    ((Node3D) getOwner().getNode(recoilPitchPath)).rotateX((float) GD.degToRad(getCurrentWeaponStats().getRecoil()));

    // final check for collision point
    if(rayCast3D.isColliding() &&  (rayCast3D.getCollisionPoint().minus(rayCast3D.getGlobalTransform().getOrigin())).length() > 0.1) {
      // Apply damage if the hit body has a Health node
      Object collider = rayCast3D.getCollider();
      if (collider instanceof godot.api.Node) {
        godot.api.Node hitNode = (godot.api.Node) collider;
        if (hitNode.hasNode(new NodePath("Health"))) {
          ((Health) hitNode.getNode(new NodePath("Health"))).takeDamage(getCurrentWeaponStats().damage);
        }
      }

      splatters.get(currentSplatersIndex).setGlobalPosition(rayCast3D.getCollisionPoint());
      splatters.get(currentSplatersIndex).setEmitting(true);
      currentSplatersIndex++;

      if(currentSplatersIndex >= maxSplatterSize) {
        currentSplatersIndex = 0;
      }
    }
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
  }

 public boolean isWeaponReloading(){
    return reloadTimer.getTimeLeft() > 0;
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
  }


  @RegisterFunction
  @Override
  public void _process(double delta) {
    magLabel.setText(String.valueOf(weapons.get(weapon).getMag()));
    ammoBackupLabel.setText(String.valueOf(weapons.get(weapon).getAmmoBackup()));
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

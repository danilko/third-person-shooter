package com.character;

import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterProperty;
import godot.api.AudioStreamWAV;
import godot.api.Node3D;

import static godot.global.GD.min;

@RegisterClass(className = "WeaponStats")
public class WeaponStats extends Node3D {
  @RegisterProperty
  @Export
  public float spread = 18.0f;

  @RegisterProperty
  @Export
  public float movementSpread = 5.5f;

  @RegisterProperty
  @Export
  public float aimSpread = -7.0f;

  @RegisterProperty
  @Export
  public float crouchSpread = -6.0f;

  @RegisterProperty
  @Export
  public float jumpSpread = 12.0f;

  @RegisterProperty
  @Export
  public float reloadSpeed = 0.8f;

  @RegisterProperty
  @Export
  public float switchSpeed = 1.2f;

  @RegisterProperty
  @Export
  public float fireRate = 8.0f;

  @RegisterProperty
  @Export
  public boolean auto = true;

  @RegisterProperty
  @Export
  public int mag = 40;

  @RegisterProperty
  @Export
  public int magSize = 40;

  @RegisterProperty
  @Export
  public int ammoBackup = 40;

  @RegisterProperty
  @Export
  public int ammoBackupMax = 40;

  @RegisterProperty
  @Export
  public float recoil = 0.8f;

  @RegisterProperty
  @Export
  public AudioStreamWAV fireAudio;

  @RegisterProperty
  @Export
  public AudioStreamWAV reloadAudio;

  public void decrementMag(){
    if (mag > 0) {
      mag--;
    }
  }

  public void fillMag(){
    int emptySpace = magSize - mag;
    mag += min(emptySpace, ammoBackup);
    ammoBackup -= min(emptySpace, ammoBackup);
  }

  public void fillAmmo(){
    ammoBackup = ammoBackupMax;
    mag = magSize;
  }

  public float getSpread() {
    return spread;
  }

  public void setSpread(float spread) {
    this.spread = spread;
  }

  public float getMovementSpread() {
    return movementSpread;
  }

  public void setMovementSpread(float movementSpread) {
    this.movementSpread = movementSpread;
  }

  public float getAimSpread() {
    return aimSpread;
  }

  public void setAimSpread(float aimSpread) {
    this.aimSpread = aimSpread;
  }

  public float getCrouchSpread() {
    return crouchSpread;
  }

  public void setCrouchSpread(float crouchSpread) {
    this.crouchSpread = crouchSpread;
  }

  public float getJumpSpread() {
    return jumpSpread;
  }

  public void setJumpSpread(float jumpSpread) {
    this.jumpSpread = jumpSpread;
  }

  public float getReloadSpeed() {
    return reloadSpeed;
  }

  public void setReloadSpeed(float reloadSpeed) {
    this.reloadSpeed = reloadSpeed;
  }

  public float getSwitchSpeed() {
    return switchSpeed;
  }

  public void setSwitchSpeed(float switchSpeed) {
    this.switchSpeed = switchSpeed;
  }

  public float getFireRate() {
    return fireRate;
  }

  public void setFireRate(float fireRate) {
    this.fireRate = fireRate;
  }

  public boolean isAuto() {
    return auto;
  }

  public void setAuto(boolean auto) {
    this.auto = auto;
  }

  public int getMag() {
    return mag;
  }

  public void setMag(int mag) {
    this.mag = mag;
  }

  public int getMagSize() {
    return magSize;
  }

  public void setMagSize(int magSize) {
    this.magSize = magSize;
  }

  public int getAmmoBackup() {
    return ammoBackup;
  }

  public void setAmmoBackup(int ammoBackup) {
    this.ammoBackup = ammoBackup;
  }

  public int getAmmoBackupMax() {
    return ammoBackupMax;
  }

  public void setAmmoBackupMax(int ammoBackupMax) {
    this.ammoBackupMax = ammoBackupMax;
  }

  public float getRecoil() {
    return recoil;
  }

  public void setRecoil(float recoil) {
    this.recoil = recoil;
  }

  public AudioStreamWAV getFireAudio() {
    return fireAudio;
  }

  public void setFireAudio(AudioStreamWAV fireAudio) {
    this.fireAudio = fireAudio;
  }

  public AudioStreamWAV getReloadAudio() {
    return reloadAudio;
  }

  public void setReloadAudio(AudioStreamWAV reloadAudio) {
    this.reloadAudio = reloadAudio;
  }
}
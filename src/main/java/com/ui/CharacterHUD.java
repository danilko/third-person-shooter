package com.ui;

import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.Control;
import godot.api.Label;
import godot.core.NodePath;

/**
 * Owns all in-game HUD labels (health, ammo).
 *
 * Listens to signals instead of polling nodes directly:
 *   - WeaponController.ammoChanged(int mag, int ammoBackup)
 *   - Health.damaged(float amount)  →  caller passes currentHealth via onHealthChanged
 *
 * Wire these signal connections in the scene (or in the owning character's _ready).
 */
@RegisterClass(className = "CharacterHUD")
public class CharacterHUD extends Control {

  @RegisterProperty
  @Export
  public NodePath healthLabelPath = new NodePath("Health/ColorRect/Health");

  @RegisterProperty
  @Export
  public NodePath magLabelPath = new NodePath("Mag/ColorRect/Mag");

  @RegisterProperty
  @Export
  public NodePath ammoBackupLabelPath = new NodePath("Mag/ColorRect/AmmoBackup");

  private Label healthLabel;
  private Label magLabel;
  private Label ammoBackupLabel;

  @RegisterFunction
  @Override
  public void _ready() {
    if (hasNode(healthLabelPath)) {
      healthLabel = (Label) getNode(healthLabelPath);
    }
    if (hasNode(magLabelPath)) {
      magLabel = (Label) getNode(magLabelPath);
    }
    if (hasNode(ammoBackupLabelPath)) {
      ammoBackupLabel = (Label) getNode(ammoBackupLabelPath);
    }
  }

  /** Receive WeaponController.ammoChanged signal. */
  @RegisterFunction
  public void onAmmoChanged(int mag, int ammoBackup) {
    if (magLabel != null) {
      magLabel.setText(String.valueOf(mag));
    }
    if (ammoBackupLabel != null) {
      ammoBackupLabel.setText(String.valueOf(ammoBackup));
    }
  }

  /** Receive Health.damaged signal (pass currentHealth from the character). */
  @RegisterFunction
  public void onHealthChanged(float currentHealth) {
    if (healthLabel != null) {
      healthLabel.setText(String.valueOf((int) currentHealth));
    }
  }
}

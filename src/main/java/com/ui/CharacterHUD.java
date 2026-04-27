package com.ui;

import com.game.EventBus;
import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.Control;
import godot.api.Label;
import godot.api.Node;
import godot.core.Callable;
import godot.core.NodePath;
import godot.core.StringNames;

/**
 * Owns all in-game HUD labels (health, ammo, kill notifications).
 *
 * Kill notifications arrive via EventBus.characterEliminated — no direct
 * signal wiring to WeaponController or Health is needed.
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
  private Label eliminatedNotificationLabel;
  private double killNotificationTimer = 0.0;
  private static final double KILL_NOTIFICATION_DURATION = 3.0;

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
    eliminatedNotificationLabel = (Label) getNode("Notification/EliminatedNotification");

    Node busNode = getNodeOrNull("/root/EventBus");
    if (busNode instanceof EventBus bus) {
      bus.characterEliminated.connectUnsafe(
          Callable.createUnsafe(this, StringNames.toGodotName("onCharacterEliminated")),
          godot.api.Object.ConnectFlags.DEFAULT);
    }
  }

  @RegisterFunction
  @Override
  public void _process(double delta) {
    if (killNotificationTimer > 0) {
      killNotificationTimer -= delta;
      if (killNotificationTimer <= 0 && eliminatedNotificationLabel != null) {
        eliminatedNotificationLabel.setVisible(false);
      }
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

  /** Receive EventBus.characterEliminated — any character killed by any source. */
  @RegisterFunction
  public void onCharacterEliminated(String attackerName, String victimName, String weaponName, boolean headshot) {
    StringBuilder sb = new StringBuilder(victimName).append(" Eliminated");
    if (!weaponName.isEmpty()) sb.append(" [").append(weaponName).append("]");
    sb.append(headshot ? " - Headshot" : " - Eliminated");
    showKillNotification(sb.toString());
  }

  private void showKillNotification(String text) {
    if (eliminatedNotificationLabel == null) return;
    eliminatedNotificationLabel.setText(text);
    eliminatedNotificationLabel.setVisible(true);
    killNotificationTimer = KILL_NOTIFICATION_DURATION;
  }
}

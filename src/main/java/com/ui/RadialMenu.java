package com.ui;

import com.character.Player;
import com.character.WeaponController;
import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.*;
import godot.core.Vector3;

@RegisterClass(className = "RadialMenu")
public class RadialMenu extends Control {

  @Export
  @RegisterProperty
  public Player player;
  @Export
  @RegisterProperty
  public Node camera;

  @Export
  @RegisterProperty
  public WeaponController weaponController;

  private AnimationPlayer animationPlayer;

  @RegisterFunction
  @Override
  public void _ready() {
    animationPlayer = (AnimationPlayer) getNode("AnimationPlayer");
    hide();
  }

  @RegisterFunction
  @Override
  public void _input(InputEvent event) {
    if(event.isActionPressed("radialmenu") && !weaponController.isWeaponReloading()) {
      showRadialMenu();
    }
    else if(event.isActionReleased("radialmenu")) {
      hideRadialMenu();
    }
  }

  public void showRadialMenu() {
    Input.setMouseMode(Input.MouseMode.VISIBLE);
    player.setProcessInput(false);
    // Stop the player movement to prevent infinite move
    player.setMovementDirection(Vector3.Companion.getZERO());
    player.setMovementState("Idle");
    camera.setProcessInput(false);
    show();
    animationPlayer.play("Zoom");
  }

  public void hideRadialMenu() {
    Input.setMouseMode(Input.MouseMode.CAPTURED);
    // Restore to Idle and let the player's _input re-derive the correct movement state
    // from current key presses on the next frame, avoiding stale cached state.
    player.setMovementState("Idle");
    player.setProcessInput(true);
    camera.setProcessInput(true);
    hide();
  }

  public Player getPlayer() {
    return player;
  }

  public int getWeaponCount() {
    return weaponController.getWeaponCount();
  }

}

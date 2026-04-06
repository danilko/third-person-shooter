package com.ui;

import com.character.Player;
import com.character.WeaponController;
import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.*;
import godot.core.GodotEnum;
import godot.core.Vector3;

@RegisterClass(className = "RadialMenu")
public class RadialMenu extends Control {

  @Export
  @RegisterProperty
  public Player player;
  @Export
  @RegisterProperty
  public Node camera;

  private AnimationPlayer animationPlayer;
  private String previousMovementState;
  private WeaponController weaponController;

  @RegisterFunction
  @Override
  public void _ready() {
    animationPlayer = (AnimationPlayer) getNode("AnimationPlayer");
    weaponController = (WeaponController) getOwner().getNode("WeaponController");
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
    // Store the previous state so can restore
    previousMovementState = player.getCurrentMovementStateName();
    player.setMovementState("Idle");
    camera.setProcessInput(false);
    show();
    animationPlayer.play("Zoom");
  }

  public void hideRadialMenu() {
    Input.setMouseMode(Input.MouseMode.CAPTURED);
    player.setMovementState(previousMovementState);
    player.setProcessInput(true);
    camera.setProcessInput(true);
    hide();
  }

  public Player getPlayer() {
    return player;
  }

}

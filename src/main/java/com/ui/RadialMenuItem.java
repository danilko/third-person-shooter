package com.ui;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.api.*;
import godot.annotation.Export;
import godot.annotation.RegisterProperty;
import godot.core.NodePath;
import godot.core.Vector2;
import godot.core.Vector3;
import godot.global.GD;

@RegisterClass(className = "RadialMenuItem")
public class RadialMenuItem extends Control {

  @Export
  @RegisterProperty
  public int index = 0;

  private RadialMenu radialMenu;


  @RegisterFunction
  @Override
  public void _ready() {
    radialMenu = (RadialMenu) getOwner().getNode("RadialMenu");
  }

  @RegisterFunction
  public void onClicked(){
    // workaround for now until dynamic assign weapon/check weapon count
    int weapon = index;
    if (weapon < 0 || weapon > 1) {
      weapon = 0;
    }

    radialMenu.getPlayer().setWeapon(weapon);
    radialMenu.hideRadialMenu();
  }

  @RegisterFunction
  public void onHover(){
    // workaround for now until dynamic assign weapon/check weapon count
    int weapon = index;
    if (weapon < 0 || weapon > 1) {
      weapon = 0;
    }
    radialMenu.getPlayer().setWeapon(weapon);
  }
}

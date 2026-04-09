package com.ui;

import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.Control;

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
    int weapon = clampWeaponIndex(index);
    radialMenu.getPlayer().setWeapon(weapon);
    radialMenu.hideRadialMenu();
  }

  @RegisterFunction
  public void onHover(){
    int weapon = clampWeaponIndex(index);
    radialMenu.getPlayer().setWeapon(weapon);
  }

  private int clampWeaponIndex(int idx) {
    int maxIndex = radialMenu.getWeaponCount() - 1;
    if (idx < 0 || idx > maxIndex) {
      return 0;
    }
    return idx;
  }
}

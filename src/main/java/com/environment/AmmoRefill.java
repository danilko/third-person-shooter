package com.environment;

import com.character.Player;
import com.character.WeaponController;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.api.Area3D;
import godot.api.Node;

@RegisterClass(className = "AmmoRefil")
public class AmmoRefill extends Area3D {

  @RegisterFunction
  public void onBodyEntered(Node node) {
    if (!(node instanceof Player)) {
      return;
    }
    ((WeaponController) node.getNode("WeaponController")).getCurrentWeaponStats().fillAmmo();
  }

}

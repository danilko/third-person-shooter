package com.environment;

import com.character.Player;
import com.character.WeaponController;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.api.Area3D;
import godot.api.Node;
import godot.api.Node3D;

import java.awt.geom.Area;

@RegisterClass(className = "AmmoRefil")
public class AmmoRefill extends Area3D {


  @RegisterFunction
  public void onBodyEntered(Node node) {
    // Workaround for now
    if(node.getNode("WeaponController") != null) {
      ((WeaponController) node.getNode("WeaponController")).getCurrentWeaponStats().fillAmmo();
    }
  }

}

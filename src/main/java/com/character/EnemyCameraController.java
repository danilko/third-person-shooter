package com.character;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.core.Vector2;
import godot.global.GD;

@RegisterClass(className = "EnemyCameraController")
public class EnemyCameraController extends CameraController {

  @RegisterFunction
  @Override
  public void _ready() {
    super._ready();
    // Enemy camera is inactive until the player hops to this character.
    camera.clearCurrent(false);
  }

  /**
   * Derives yaw from the character's body facing direction so the camera
   * tracks the enemy's movement naturally.  Pitch stays level (0).
   */
  @Override
  protected Vector2 gatherLookInput(double delta) {
    double characterYawDeg = Math.toDegrees(player.getRotation().getY());
    double targetYaw = -characterYawDeg;

    // Compute the shortest-path delta so the yaw accumulator stays in sync.
    double deltaYaw = GD.wrapf(targetYaw - yaw, -180.0, 180.0);

    return new Vector2((float) deltaYaw, 0f);
  }
}

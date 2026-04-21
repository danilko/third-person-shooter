package com.character;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.api.Input;
import godot.core.Vector2;

@RegisterClass(className = "PlayerCameraController")
public class PlayerCameraController extends CameraController {

  @RegisterFunction
  @Override
  public void _ready() {
    super._ready();
    Input.setMouseMode(Input.MouseMode.CAPTURED);
    camera.makeCurrent();
  }

  @Override
  protected Vector2 gatherLookInput(double delta) {
    Vector2 mouseVelocity = Input.getLastMouseVelocity();
    double deltaYaw   = -mouseVelocity.getX() * yawSensitivity * delta;
    double deltaPitch =  mouseVelocity.getY() * pitchSensitivity * delta;

    if (Input.isActionJustPressed("shoulder", false)) {
      changeShoulderDirection();
    }

    return new Vector2((float) deltaYaw, (float) deltaPitch);
  }
}

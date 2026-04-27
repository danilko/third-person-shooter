package com.character;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.core.Vector2;
import godot.core.Vector3;
import godot.global.GD;

@RegisterClass(className = "EnemyCameraController")
public class EnemyCameraController extends CameraController {

  // World-space aim target; null = fall back to body-facing direction.
  private Vector3 aimTarget = null;

  @RegisterFunction
  @Override
  public void _ready() {
    super._ready();
    camera.clearCurrent(false);
  }

  public void setAimTarget(Vector3 worldTarget) { this.aimTarget = worldTarget; }
  public void clearAimTarget()                  { this.aimTarget = null; }

  /**
   * When an aim target is set, drives Yaw/Pitch toward that world position so the
   * AimRay converges on the target across frames.
   *
   * Camera forward = (cos(p)*sin(y), -sin(p), cos(p)*cos(y)).
   * Inverting: targetYaw = atan2(dx, dz), targetPitch = -atan2(dy, hDist).
   *
   * Without an aim target, falls back to tracking the enemy body's facing direction.
   */
  @Override
  protected Vector2 gatherLookInput(double delta) {
    if (aimTarget != null) {
      Vector3 myPos  = getGlobalPosition();
      double  dx     = aimTarget.getX() - myPos.getX();
      double  dy     = aimTarget.getY() - myPos.getY();
      double  dz     = aimTarget.getZ() - myPos.getZ();
      double  hDist  = Math.sqrt(dx * dx + dz * dz);

      double targetYawDeg   = Math.toDegrees(Math.atan2(dx, dz));
      double targetPitchDeg = (hDist > 0.01) ? -Math.toDegrees(Math.atan2(dy, hDist)) : 0.0;

      double deltaYaw   = GD.wrapf(targetYawDeg - yaw,  -180.0, 180.0);
      double deltaPitch = targetPitchDeg - pitch;
      return new Vector2((float) deltaYaw, (float) deltaPitch);
    }

    // Default: track character body facing, pitch stays level.
    double characterYawDeg = Math.toDegrees(player.getRotation().getY());
    double targetYaw       = -characterYawDeg;
    double deltaYaw        = GD.wrapf(targetYaw - yaw, -180.0, 180.0);
    return new Vector2((float) deltaYaw, 0f);
  }
}

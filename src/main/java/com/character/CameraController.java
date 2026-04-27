package com.character;

import godot.annotation.*;
import godot.api.*;
import godot.core.NodePath;
import godot.core.Signal1;
import godot.core.StringName;
import godot.core.Vector2;
import godot.core.Vector3;
import godot.global.GD;

@RegisterClass(className = "CameraController")
public class CameraController extends Node3D {

  @RegisterSignal
  public Signal1<Double> setCamRotation = new Signal1<>(this, new StringName("set_cam_rotation"));

  @Export
  @RegisterProperty
  public CharacterBody3D player;

  protected int shoulderDirection = 1;

  protected Node3D yawNode;
  protected Node3D pitchNode;
  protected Node3D pivotNode;
  protected SpringArm3D springArm;
  protected Camera3D camera;

  @Export
  @RegisterProperty
  public double yawSensitivity = 0.07;

  @Export
  @RegisterProperty
  public double pitchSensitivity = 0.07;

  @Export
  @RegisterProperty
  public double yawAcceleration = 15.0;

  @Export
  @RegisterProperty
  public double pitchAcceleration = 15.0;

  @Export
  @RegisterProperty
  public double pitchMax = 75.0;

  @Export
  @RegisterProperty
  public double pitchMin = -55.0;

  @Export
  @RegisterProperty
  public double shoulderOffsetLerpSpeed = 4.0;

  @Export
  @RegisterProperty
  public double followLerpSpeed = 18.0;

  @Export
  @RegisterProperty
  public double fovTweenDuration = 0.5;

  protected double yaw = 0.0;
  protected double pitch = 0.0;

  private Vector3 positionOffset = new Vector3(0, 0.8, 0);
  private Vector3 positionOffsetTarget = new Vector3(0, 0.8, 0);

  private float springArmLengthTarget = 3;

  private double movementFov = 0.0;
  private double cameraFov = 0.0;
  protected boolean combat = false;

  // Recoil offsets added per shot, decaying toward zero each frame.
  // Kept separate from pitch/yaw (mouse intent) so recovery never fights mouse aim.
  private double recoilPitch = 0.0;
  private double recoilYaw   = 0.0;

  @Export
  @RegisterProperty
  public double recoilRecoverySpeed = 8.0;

  private Tween tween;

  @RegisterFunction
  @Override
  public void _ready() {
    yawNode = (Node3D) getNode(new NodePath("Yaw"));
    pitchNode = (Node3D) getNode(new NodePath("Yaw/Pitch"));
    pivotNode = (Node3D) getNode(new NodePath("Yaw/Pitch/Pivot"));
    springArm = (SpringArm3D) getNode(new NodePath("Yaw/Pitch/Pivot/SpringArm"));
    camera = (Camera3D) getNode(new NodePath("Yaw/Pitch/Pivot/SpringArm/Camera"));

    if (player != null) {
      springArm.addExcludedObject(player.getRid());
    }

    setAsTopLevel(true);
  }

  /**
   * Returns the look-input delta for this frame as (deltaYawDeg, deltaPitchDeg).
   * Subclasses provide the input source: mouse for the player, AI facing for enemies.
   */
  protected Vector2 gatherLookInput(double delta) {
    return Vector2.Companion.getZERO();
  }

  public void changeShoulderDirection() {
    shoulderDirection = shoulderDirection * -1;
    positionOffsetTarget.setX(positionOffsetTarget.getX() * shoulderDirection);
    setCameraFov();
  }

  @RegisterFunction
  @Override
  public void _physicsProcess(double delta) {
    Vector2 lookDelta = gatherLookInput(delta);
    yaw   += lookDelta.getX();
    pitch += lookDelta.getY();

    // Position interpolation
    positionOffset = positionOffset.lerp(positionOffsetTarget, shoulderOffsetLerpSpeed * delta);

    // Apply shoulder offset along yaw's right vector (camera-relative), not world X.
    Vector3 playerBase = player.getGlobalPosition().plus(new Vector3(0, positionOffset.getY(), 0));
    Vector3 yawRight   = yawNode.getGlobalTransform().getBasis().getX();
    Vector3 targetPos  = playerBase.plus(yawRight.times(positionOffset.getX()));

    // Tighten follow speed in combat to prevent SpringArm casting from wrong position.
    float followSpeedWeight = combat ? 1 : (float) (followLerpSpeed * delta);
    setGlobalPosition(getGlobalPosition().lerp(targetPos, followSpeedWeight));

    springArm.setLength(GD.lerp(springArm.getLength(), springArmLengthTarget, followSpeedWeight));

    // Clamp clean mouse-intent pitch
    pitch = GD.clamp(pitch, pitchMin, pitchMax);

    // Decay recoil offsets toward zero each frame
    recoilPitch = GD.lerp(recoilPitch, 0.0, recoilRecoverySpeed * delta);
    recoilYaw   = GD.lerp(recoilYaw,   0.0, recoilRecoverySpeed * delta);

    // Display values: mouse aim + per-shot kick, pitch clamped
    double displayPitch = GD.clamp(pitch + recoilPitch, pitchMin, pitchMax);
    double displayYaw   = yaw + recoilYaw;

    // Rotation interpolation
    Vector3 yawRot = yawNode.getRotationDegrees();
    yawRot.setY(GD.lerp(yawRot.getY(), displayYaw, yawAcceleration * delta));
    yawNode.setRotationDegrees(yawRot);

    Vector3 pitchRot = pitchNode.getRotationDegrees();
    pitchRot.setX(GD.lerp(pitchRot.getX(), displayPitch, pitchAcceleration * delta));
    pitchNode.setRotationDegrees(pitchRot);

    setCamRotation.emit(yawNode.getRotation().getY());
  }

  /** Adds a per-shot kick (degrees) that decays back to zero at recoilRecoverySpeed. */
  public void applyRecoil(double pitchKick, double yawKick) {
    recoilPitch += pitchKick;
    recoilYaw   += yawKick;
  }

  @RegisterFunction
  public void onSetCombatState(CombatState combatState) {
    combat = combatState.isCombat();
    cameraFov = combatState.cameraFov;
    positionOffsetTarget.setX(combatState.cameraShoulderOffset * shoulderDirection);
    springArmLengthTarget = (float) combatState.cameraDistance;
    setCameraFov();
  }

  @RegisterFunction
  public void onSetMovementState(MovementState movementState) {
    movementFov = movementState.getCameraFov();
    setCameraFov();
  }

  private void setCameraFov() {
    if (tween != null && tween.isValid()) {
      tween.kill();
    }

    double targetFov = combat ? cameraFov : movementFov;

    tween = createTween();
    tween.tweenProperty(camera, "fov", targetFov, fovTweenDuration)
         .setTrans(Tween.TransitionType.SINE)
         .setEase(Tween.EaseType.OUT);
  }

  @RegisterFunction
  public void onSetStance(Stance stance) {
    positionOffsetTarget.setY(stance.getCameraHeight());
  }
}

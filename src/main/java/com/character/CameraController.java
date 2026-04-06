package com.character;

import godot.annotation.*;
import godot.api.*;
import godot.core.NodePath;
import godot.core.Signal1;
import godot.core.Vector3;
import godot.global.GD;

@RegisterClass(className = "CameraController")
public class CameraController extends Node3D {

  // Define the Signal
  @RegisterSignal
  public Signal1<Double> setCamRotation = Signal1.create(this, "setCamRotation");

  @Export
  @RegisterProperty
  public CharacterBody3D player;

  private int shoulderDirection = 1;

  private Node3D yawNode;
  private Node3D pitchNode;
  private Node3D pivotNode;
  private SpringArm3D springArm;
  private Camera3D camera;

  private double yaw = 0.0;
  private double pitch = 0.0;
  private double yawSensitivity = 0.07;
  private double pitchSensitivity = 0.07;
  private double yawAcceleration = 15.0;
  private double pitchAcceleration = 15.0;
  private double pitchMax = 75.0;
  private double pitchMin = -55.0;

  private Vector3 positionOffset = new Vector3(0, 0.8, 0);
  private Vector3 positionOffsetTarget = new Vector3(0, 0.8, 0);

  private float springArmLengthTarget = 3;

  private double movementFov = 0.0;
  private double cameraFov = 0.0;
  private boolean combat = false;

  private Tween tween;

  @RegisterFunction
  @Override
  public void _ready() {
    Input.setMouseMode(Input.MouseMode.CAPTURED);

    // Handling @onready assignments
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

  @RegisterFunction
  @Override
  public void _input(InputEvent event) {
    if (event instanceof InputEventMouseMotion) {
      InputEventMouseMotion mouseMotion = (InputEventMouseMotion) event;
      yaw += -mouseMotion.getRelative().getX() * yawSensitivity;
      pitch += mouseMotion.getRelative().getY() * pitchSensitivity;
    }

    if(event.isActionPressed("shoulder")) {
      changeShoulderDirection();
    }
  }

  public void changeShoulderDirection() {
    shoulderDirection = shoulderDirection * -1;

    positionOffsetTarget.setX(positionOffsetTarget.getX() * shoulderDirection);

    setCameraFov();
  }

  @RegisterFunction
  @Override
  public void _physicsProcess(double delta) {
    // Position interpolation
    positionOffset = positionOffset.lerp(positionOffsetTarget, 4 * delta);

    // Apply shoulder offset along yaw's right vector (camera-relative), not world X.
    // This ensures the shoulder offset stays on the correct side regardless of player facing direction.
    Vector3 playerBase = player.getGlobalPosition().plus(new Vector3(0, positionOffset.getY(), 0));
    Vector3 yawRight = yawNode.getGlobalTransform().getBasis().getX();
    Vector3 targetPos = playerBase.plus(yawRight.times(positionOffset.getX()));

    // In combat/aim mode, tighten follow speed so the SpringArm origin stays close to
    // the player. A slow-following pivot causes the SpringArm to cast from the wrong
    // position during lateral movement, making the camera flip to the wrong side.
    float followSpeedWeight = combat ? 1 : (float) (18.0 * delta);
    setGlobalPosition(getGlobalPosition().lerp(targetPos, followSpeedWeight));

    springArm.setLength(GD.lerp(springArm.getLength(), springArmLengthTarget, followSpeedWeight));

    // Clamp pitch
    pitch = GD.clamp(pitch, pitchMin, pitchMax);

    // Rotation interpolation
    Vector3 yawRot = yawNode.getRotationDegrees();
    yawRot.setY(GD.lerp(yawRot.getY(), yaw, yawAcceleration * delta));
    yawNode.setRotationDegrees(yawRot);

    Vector3 pitchRot = pitchNode.getRotationDegrees();
    pitchRot.setX(GD.lerp(pitchRot.getX(), pitch, pitchAcceleration * delta));
    pitchNode.setRotationDegrees(pitchRot);

    // Emit signal
    setCamRotation.emit(yawNode.getRotation().getY());
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

    double targetFov = movementFov;
    // move to combat
    if(combat) {
      targetFov = cameraFov;
    }

    tween = createTween();
    tween.tweenProperty(camera, "fov", targetFov, 0.5)
         .setTrans(Tween.TransitionType.SINE)
         .setEase(Tween.EaseType.OUT);
  }

  @RegisterFunction
  public void onSetStance(Stance stance) {
    double height = stance.getCameraHeight();
    positionOffsetTarget.setY(height);
  }
}
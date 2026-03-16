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

  private Node3D yawNode;
  private Node3D pitchNode;
  private Camera3D camera;

  private double yaw = 0.0;
  private double pitch = 0.0;
  private double yawSensitivity = 0.07;
  private double pitchSensitivity = 0.07;
  private double yawAcceleration = 15.0;
  private double pitchAcceleration = 15.0;
  private double pitchMax = 75.0;
  private double pitchMin = -55.0;

  private Tween tween;
  private Vector3 positionOffset = new Vector3(0, 0.8, 0);
  private Vector3 positionOffsetTarget = new Vector3(0, 0.8, 0);

  @RegisterFunction
  @Override
  public void _ready() {
    Input.setMouseMode(Input.MouseMode.CAPTURED);

    // Handling @onready assignments
    yawNode = (Node3D) getNode(new NodePath("CamYaw"));
    pitchNode = (Node3D) getNode(new NodePath("CamYaw/CamPitch"));
    SpringArm3D springArm = (SpringArm3D) getNode(new NodePath("CamYaw/CamPitch/SpringArm"));
    camera = (Camera3D) getNode(new NodePath("CamYaw/CamPitch/SpringArm/Camera"));

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
  }

  @RegisterFunction
  @Override
  public void _physicsProcess(double delta) {
    // Position interpolation
    positionOffset = positionOffset.lerp(positionOffsetTarget, 4 * delta);

    Vector3 targetPos = player.getGlobalPosition().plus(positionOffset);
    setGlobalPosition(getGlobalPosition().lerp(targetPos, 18 * delta));


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
  public void onSetMovementState(MovementState movementState) {
    if (tween != null && tween.isValid()) {
      tween.kill();
    }

    tween = createTween();
    double targetFov = movementState.getCameraFov();

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
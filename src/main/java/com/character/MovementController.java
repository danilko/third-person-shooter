package com.character;

import godot.api.CharacterBody3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.core.Vector3;
import godot.global.GD;
import static java.lang.Math.atan2;

@RegisterClass
public class MovementController extends Node {

  @Export
  @RegisterProperty
  public CharacterBody3D player = null;

  @Export
  @RegisterProperty
  public Node3D meshRoot = null;

  @Export
  @RegisterProperty
  public double rotationSpeed = 8.0;

  @Export
  @RegisterProperty
  public double fallGravity = 45.0;

  private double jumpGravity = fallGravity;
  private Vector3 direction = new Vector3();
  private Vector3 velocity = new Vector3();
  private double acceleration = 0.0;
  private double speed = 0.0;
  private double camRotation = 0.0;
  private double playerInitRotation = 0.0;
  private StrafingState.StrafingStateType strafingStateType = StrafingState.StrafingStateType.NOT_STRAFING;

  @RegisterFunction
  @Override
  public void _ready() {
    if (player != null) {
      playerInitRotation = player.getRotation().getY();
    }
  }

  @RegisterFunction
  @Override
  public void _physicsProcess(double delta) {
    if (player == null || meshRoot == null) return;

    // Calculate horizontal velocity
    Vector3 normDir = direction.normalized();
    velocity.setX(speed * normDir.getX());
    velocity.setZ(speed * normDir.getZ());

    // Handle Gravity
    if (!player.isOnFloor()) {
      if (velocity.getY() >= 0) {
        velocity.setY(velocity.getY() - (jumpGravity * delta));
      } else {
        velocity.setY(velocity.getY() - (fallGravity * delta));
      }
    }

    // Apply movement using lerp
    player.setVelocity( GD.lerp(player.getVelocity(), velocity, acceleration * delta));
    player.moveAndSlide();

    // Handle Mesh Rotation
    double targetRotation;
    if (strafingStateType ==  StrafingState.StrafingStateType.STRAFING) {
      // Face camera direction regardless of movement
      targetRotation = camRotation - playerInitRotation;
    } else {
      // Face movement direction (only when actually moving)
      if (direction.lengthSquared() > 0.001) {
        targetRotation = atan2(direction.getX(), direction.getZ()) - playerInitRotation;
      } else {
        targetRotation = meshRoot.getRotation().getY(); // hold current facing
      }
    }

    Vector3 currentRot = meshRoot.getRotation();
    double newY = GD.lerpAngle(currentRot.getY(), targetRotation, rotationSpeed * delta);

    // Update only the Y axis
    meshRoot.setRotation(new Vector3(currentRot.getX(), newY, currentRot.getZ()));
  }

  @RegisterFunction
  public void jump(JumpState jumpState) {
    velocity.setY(2.0 * jumpState.getJumpHeight() / jumpState.getApexDuration());
    jumpGravity = velocity.getY() / jumpState.getApexDuration();
  }

  @RegisterFunction
  public void onSetMovementState(MovementState movementState) {
    speed = movementState.getMovementSpeed();
    acceleration = movementState.getAcceleration();
  }

  @RegisterFunction
  public void onSetStrafingState(StrafingState strafingState) {
    this.strafingStateType = strafingState.getStrafingStateType();
  }

  @RegisterFunction
  public void onSetMovementDirection(Vector3 movementDirection) {
    direction = movementDirection.rotated(Vector3.Companion.getUP(), camRotation + playerInitRotation);
  }

  @RegisterFunction
  public void onSetCamRotation(double newCamRotation) {
    camRotation = newCamRotation;
  }
}
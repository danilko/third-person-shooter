package com.character;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.api.AnimationNodeOneShot;
import godot.api.AnimationTree;
import godot.api.CharacterBody3D;
import godot.api.Node;
import godot.api.Tween;
import godot.annotation.Export;
import godot.annotation.RegisterProperty;
import godot.core.NodePath;
import godot.core.Vector2;
import godot.core.Vector3;
import godot.global.GD;

@RegisterClass(className = "AnimationController")
public class AnimationController extends Node {

  @RegisterProperty
  @Export
  public AnimationTree animationTree;

  @RegisterProperty
  @Export
  public CharacterBody3D player;

  private double onFloorBlend = 1.0;
  private double onFloorBlendTarget = 1.0;
  private Tween tween;
  private String currentStanceName = "Upright";
  private StrafingState.StrafingStateType strafingStateType = StrafingState.StrafingStateType.NOT_STRAFING;
  private Vector2 movementDirection = new Vector2();
  private Vector2 animationDirection = new Vector2();
  private MovementState currentMovementState = null;

  @RegisterFunction
  @Override
  public void _physicsProcess(double delta) {
    if (player == null || animationTree == null) return;

    // Calculate floor blend target
    onFloorBlendTarget = player.isOnFloor() ? 1.0 : 0.0;

    // Smoothly interpolate the blend value
    onFloorBlend = GD.lerp(onFloorBlend, onFloorBlendTarget, 10.0 * delta);

    // Access AnimationTree parameters using set() with a NodePath
    animationTree.set("parameters/OnFloorBlend/blend_amount", onFloorBlend);
  }

  @RegisterFunction
  public void jump(JumpState jumpState) {
    if (animationTree == null) return;

    // Request a OneShot animation fire
    String path = "parameters/" + jumpState.getAnimationName() + "/request";
    animationTree.set(path, AnimationNodeOneShot.OneShotRequest.FIRE.getValue());
  }

  @RegisterFunction
  public void onSetMovementState(MovementState movementState) {
    currentMovementState = movementState;
    updateAnimationBlend(movementState);
  }

  @RegisterFunction
  public void onSetStance(Stance stance) {
    if (animationTree == null) return;

    // Update the transition and keep track of the current stance name
    animationTree.set("parameters/StanceTransition/transition_request", stance.getName().toString());
    this.currentStanceName = stance.getName().toString();
  }


  @RegisterFunction
  public void onSetStrafingState(StrafingState strafingState) {
    this.strafingStateType = strafingState.getStrafingStateType();
  }

  @RegisterFunction
  public void onSetMovementDirection(Vector3 movementDirection) {
    this.movementDirection.setX(movementDirection.getX() == 0 ? 0 : movementDirection.getX() > 0 ? 1 : -1);
    this.movementDirection.setY(movementDirection.getZ() == 0 ? 0 : movementDirection.getZ() > 0 ? 1 : -1);

    updateAnimationBlend(currentMovementState);
  }

  private void updateAnimationBlend(MovementState movementState) {
    if (animationTree == null || currentMovementState == null) return;

    if (tween != null && tween.isValid()) {
      tween.kill();
    }

    tween = createTween();

    if (strafingStateType == StrafingState.StrafingStateType.STRAFING) {
      int id = Math.min(movementState.getId(), 1);
      animationDirection.setX(id * movementDirection.getX());
      animationDirection.setY(id * movementDirection.getY());
    } else {
      animationDirection.setX(0.0f);
      animationDirection.setY(movementState.getId());
    }

    GD.print("starf:" + (strafingStateType == StrafingState.StrafingStateType.STRAFING) + "," + animationDirection);

    String blendPath = "parameters/" + currentStanceName + "MovementBlend/blend_position";
    tween.tweenProperty(animationTree, new NodePath(blendPath), animationDirection, 0.25);

    String speedPath = "parameters/MovementAnimSpeed/scale";
    tween.parallel().tweenProperty(animationTree, new NodePath(speedPath), movementState.animationSpeed, 0.7);
  }
}
package com.character;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.api.*;
import godot.annotation.Export;
import godot.annotation.RegisterProperty;
import godot.core.*;
import godot.global.GD;

@RegisterClass(className = "AnimationController")
public class AnimationController extends Node {

  @RegisterProperty
  @Export
  public AnimationTree animationTree;

  @RegisterProperty
  @Export
  public CharacterBody3D player;

  @RegisterProperty
  @Export
  public LookAtModifier3D aimLookAtModifier;

  @Export
  @RegisterProperty
  public double animationBlendDuration = 0.25;

  @Export
  @RegisterProperty
  public double animationSpeedDuration = 0.7;

  @Export
  @RegisterProperty
  public double floorBlendSpeed = 10.0;

  private int weapon = 0;
  private int pendingWeapon = -1;
  private static final double WEAPON_SWAP_DELAY = 0.3;

  private double onFloorBlend = 1.0;
  private double onFloorBlendTarget = 1.0;
  private Tween tween;
  private String currentStanceName = "Upright";
  private boolean combat = false;
  private Vector2 movementDirection = new Vector2();
  private Vector2 animationDirection = new Vector2();
  private MovementState currentMovementState = null;


  @RegisterFunction
  @Override
  public void _physicsProcess(double delta) {
    if (player == null || animationTree == null || aimLookAtModifier == null) return;

    // Calculate floor blend target
    onFloorBlendTarget = player.isOnFloor() ? 1.0 : 0.0;

    // Smoothly interpolate the blend value
    onFloorBlend = GD.lerp(onFloorBlend, onFloorBlendTarget, floorBlendSpeed * delta);

    // Access AnimationTree parameters using set() with a NodePath
    animationTree.set("parameters/OnFloorBlend/blend_amount", onFloorBlend);
  }

  @RegisterFunction
  public void jump(JumpState jumpState) {
    if (animationTree == null) return;
    // Do not kill the movement blend tween — the jump OneShot plays on top of the
    // movement blend, so we want the blend to continue smoothly while airborne.
    String path = "parameters/" + jumpState.getAnimationName() + "/request";
    animationTree.set(path, AnimationNodeOneShot.OneShotRequest.FIRE.getValue());
  }

  @RegisterFunction
  public void roll(RollState rollState) {
    if (animationTree == null) return;
    // Same reasoning as jump: the roll OneShot is independent of movement blending.
    String path = "parameters/" + rollState.getAnimationName() + "/request";
    animationTree.set(path, AnimationNodeOneShot.OneShotRequest.FIRE.getValue());
  }

  @RegisterFunction
  public void onSetMovementState(MovementState movementState) {
    currentMovementState = movementState;
    updateAnimationBlend(movementState);
  }

  public void onWeaponTransition(int animationWeaponIndex, boolean isEquipping) {
    // If unequipping, we stay on the current weapon's pose.
    if (!isEquipping) {
      animationTree.set("parameters/WeaponAim/blend_position", animationWeaponIndex);
      animationTree.set("parameters/WeaponHold/blend_position", animationWeaponIndex);
    }
    // 2. CONFIGURE: Tell the transition which weapon's specific animation to play
    animationTree.set("parameters/WeaponChangeAnimation/blend_position", animationWeaponIndex);
    // 3. DIRECTION: 1 for Equip (In), 0 or -1 for Unequip (Out)
    int state = isEquipping ? 1 : -1;
    animationTree.set("parameters/WeaponChangeScale/transition_request", state);

    // 4. EXECUTE: Fire the actual movement
    animationTree.set("parameters/WeaponChange/request", AnimationNodeOneShot.OneShotRequest.FIRE.getValue());

    // If equipping, change to correct weapon pose
    if (isEquipping) {
      animationTree.set("parameters/WeaponAim/blend_position", animationWeaponIndex);
      animationTree.set("parameters/WeaponHold/blend_position", animationWeaponIndex);
    }
  }

  @RegisterFunction
  public void onWeaponReload() {
    animationTree.set("parameters/Reload/request", AnimationNodeOneShot.OneShotRequest.FIRE.getValue());
  }

  @RegisterFunction
  public void onSetStance(Stance stance) {
    if (animationTree == null) return;

    // Update the transition and keep track of the current stance name
    animationTree.set("parameters/StanceTransition/transition_request", stance.getName().toString());
    this.currentStanceName = stance.getName().toString();
  }


  @RegisterFunction
  public void onSetCombatState(CombatState combatState) {
      combat = combatState.isCombat();

      animationTree.set("parameters/CombatTransition/transition_request", combat ? "Combat" : "NoCombat");
      // Update neck to T pose so crawl and other will not have issue
      animationTree.set("parameters/NeckFront/blend_amount", combat ? 1 : 0);

      aimLookAtModifier.setActive(combat);
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

    if (combat) {
      int id = Math.min(movementState.getId(), 1);
      // The animation is opposite of the direction calculation
      animationDirection.setX(id * movementDirection.getX() * -1);
      animationDirection.setY(id * movementDirection.getY());
    } else {
      animationDirection.setX(0.0f);
      animationDirection.setY(movementState.getId());
    }

    String blendPath = "parameters/" + currentStanceName + "MovementBlend/blend_position";
    tween.tweenProperty(animationTree, new NodePath(blendPath), animationDirection, animationBlendDuration);

    String speedPath = "parameters/MovementAnimSpeed/scale";
    tween.parallel().tweenProperty(animationTree, new NodePath(speedPath), movementState.animationSpeed, animationSpeedDuration);
  }
}
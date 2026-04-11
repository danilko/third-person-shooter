package com.character;

import godot.api.CollisionShape3D;
import godot.api.Node;
import godot.api.RayCast3D;
import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.core.VariantArray;
import godot.global.GD;

@RegisterClass
public class Stance extends Node {

  // --- Movement States ---
  @Export
  @RegisterProperty
  public MovementState idleState;

  @Export
  @RegisterProperty
  public MovementState walkState;

  @Export
  @RegisterProperty
  public MovementState sprintState;

  // --- Camera Data ---
  @Export
  @RegisterProperty
  public double cameraHeight = 1.3;

  // --- Collision ---
  @Export
  @RegisterProperty
  public CollisionShape3D collider;

  @Export
  @RegisterProperty
  public RayCast3D colRaycast;

  /**
   * Stances that require more vertical clearance than this one (i.e. taller stances).
   *
   * When {@code colRaycast} detects a ceiling obstruction, all stances listed here
   * are also considered blocked — preventing the character from standing up into a
   * surface even if they are not the direct target of the transition.
   *
   * Example: Crawl.higherStances = [Crouch, Upright].  When crawling under a low
   * ceiling, neither Crouch nor Upright can be entered until the ceiling clears.
   *
   * Inspector: assign the sibling Stance nodes that are blocked by this stance's ceiling raycast.
   */
  @Export
  @RegisterProperty
  public VariantArray<Stance> higherStances = new VariantArray<>(Stance.class);

  /**
   * Returns true if this stance's space is obstructed (ceiling too low).
   * Also returns true if any of the {@link #higherStances} are themselves blocked,
   * preventing an upward transition that would collide.
   */
  @RegisterFunction
  public boolean isBlocked() {
    if (colRaycast != null && colRaycast.isColliding()) return true;
    for (Stance taller : higherStances) {
      if (taller != null && taller.isBlocked()) return true;
    }
    return false;
  }

  public MovementState getIdleState() {
    return idleState;
  }

  public void setIdleState(MovementState idleState) {
    this.idleState = idleState;
  }

  public MovementState getWalkState() {
    return walkState;
  }

  public void setWalkState(MovementState walkState) {
    this.walkState = walkState;
  }


  public MovementState getSprintState() {
    return sprintState;
  }

  public void setSprintState(MovementState sprintState) {
    this.sprintState = sprintState;
  }

  public double getCameraHeight() {
    return cameraHeight;
  }

  public void setCameraHeight(double cameraHeight) {
    this.cameraHeight = cameraHeight;
  }

  public CollisionShape3D getCollider() {
    return collider;
  }

  public void setCollider(CollisionShape3D collider) {
    this.collider = collider;
  }

  public RayCast3D getColRaycast() {
    return colRaycast;
  }

  public void setColRaycast(RayCast3D colRaycast) {
    this.colRaycast = colRaycast;
  }

  public VariantArray<Stance> getHigherStances() {
    return higherStances;
  }

  public void setHigherStances(VariantArray<Stance> higherStances) {
    this.higherStances = higherStances;
  }

  public MovementState getMovementState(MovementType type) {
    switch (type) {
      case IDLE:   return idleState;
      case WALK:   return walkState;
      case SPRINT: return sprintState;
      default:
        return idleState;
    }
  }
}
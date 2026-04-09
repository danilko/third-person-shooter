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

  @Export
  @RegisterProperty
  public VariantArray<Stance> higherStances = new VariantArray<>(Stance.class);

  @RegisterFunction
  public boolean isBlocked() {
    return colRaycast != null && colRaycast.isColliding();
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

  @RegisterFunction
  public MovementState getMovementState(String stateName) {
    switch (stateName) {
      case "Idle":
        return idleState;
      case "Walk":
        return walkState;
      case "Sprint":
        return sprintState;
      default:
        return idleState;
    }
  }
}
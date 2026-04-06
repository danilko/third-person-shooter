package com.character;

import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterProperty;
import godot.api.Resource;

@RegisterClass(className = "CombatState")
public class CombatState extends Resource {

  @Export
  @RegisterProperty
  public boolean combat = false;

  @Export
  @RegisterProperty
  public double speedFactor = 1.0f;

  @Export
  @RegisterProperty
  public double accelerationFactor = 1.0f;

  @Export
  @RegisterProperty
  public double cameraDistance = 2.0f;

  @Export
  @RegisterProperty
  public double cameraShoulderOffset = 0.1f;

  @Export
  @RegisterProperty
  public double cameraFov = 70.0f;


  // Default constructor is required for Godot to instantiate the Resource
  public CombatState() {
    super();
  }

  public boolean isCombat() {
    return combat;
  }

  public double getSpeedFactor() {
    return speedFactor;
  }

  public double getAccelerationFactor() {
    return accelerationFactor;
  }
}
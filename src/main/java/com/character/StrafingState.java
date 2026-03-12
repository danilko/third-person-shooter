package com.character;

import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterProperty;
import godot.api.Resource;

@RegisterClass(className = "StrafingState")
public class StrafingState extends Resource {

  public enum StrafingStateType {
    STRAFING,
    NOT_STRAFING,
  }

  private StrafingStateType strafingStateType = StrafingStateType.NOT_STRAFING;

  public void setStrafingStateType(StrafingStateType strafingStateType) {
    this.strafingStateType = strafingStateType;
  }
  public StrafingStateType getStrafingStateType() {
    return strafingStateType;
  }

  // Default constructor is required for Godot to instantiate the Resource
  public StrafingState() {
    super();
  }
}
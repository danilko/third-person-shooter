package com.ui;

import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.*;
import godot.core.VariantArray;
import godot.core.Vector2;
import godot.global.GD;

@RegisterClass(className = "Crosshair")
public class Crosshair extends Control {

  /**
   * Lerp speed when arms move outward (bloom spike after a shot).
   * Default 60 ≈ instant snap at 60 fps once weight is clamped to [0,1],
   * giving the same crisp feedback the old fire animation had.
   */
  @Export
  @RegisterProperty
  public double crosshairExpandSpeed = 60.0;

  /** Lerp speed when arms drift inward (bloom recovery between shots). */
  @Export
  @RegisterProperty
  public double crosshairContractSpeed = 1.0;

  private VariantArray<Node> lines;
  // Default matches rest: 0.5° × 8 px/° = 4 px.
  private float positionX = 4.0f;

  public void setPositionX(float positionX) {
    this.positionX = positionX;
  }

  @RegisterFunction
  @Override
  public void _ready() {
    lines = getNode("Reticle/Lines").getChildren();
  }

  /**
   * Bloom expansion is already captured in positionX via getCurrentSpreadDeg(),
   * so no separate per-shot animation is needed here.
   */
  @RegisterFunction
  public void onWeaponFire(float speedScale) {
    // intentionally empty
  }

  @RegisterFunction
  @Override
  public void _process(double delta) {
    for (Node line : lines) {
      Node2D currentLine = (Node2D) line.getNode("LineBase");
      Vector2 currentPos  = currentLine.getPosition();
      double  speed  = currentPos.getX() < positionX ? crosshairExpandSpeed : crosshairContractSpeed;
      // Clamp to [0,1]: prevents overshoot on a large first-frame delta.
      double  weight = Math.min(1.0, speed * delta);
      float   newX   = (float) GD.lerp(currentPos.getX(), positionX, weight);
      currentLine.setPosition(new Vector2(newX, currentPos.getY()));
    }
  }
}

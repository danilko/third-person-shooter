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

  @Export
  @RegisterProperty
  public double crosshairLerpSpeed = 12.0;

  private VariantArray<Node> lines;

  public float getPositionX() {
    return positionX;
  }

  public void setPositionX(float positionX) {
    this.positionX = positionX;
  }

  private float positionX = 15.0f;

  @RegisterFunction
  @Override
  public void _ready() {
    lines = getNode("Reticle/Lines").getChildren();
  }

  @RegisterFunction
  public void onWeaponFire(float speedScale){
    for(Node line : lines){
      AnimationPlayer animationPlayer = (AnimationPlayer) line.getNode("AnimationPlayer");
      animationPlayer.setSpeedScale(speedScale);
      animationPlayer.stop();
      animationPlayer.play("Fire");
    }
  }

  @RegisterFunction
  @Override
  public void _process(double delta) {
    for(Node line : lines){
      Node2D currentLine = (Node2D) line.getNode("LineBase");

      // Get current position
      Vector2 currentPos = currentLine.getPosition();

      // Calculate the new X using Mathf.lerp
      float newX = (float) GD.lerp(currentPos.getX(), positionX, delta * crosshairLerpSpeed);

      // Apply the new position (creating a new Vector3 with the updated X)
      currentLine.setPosition(new Vector2(newX, currentPos.getY()));
    }
  }

}

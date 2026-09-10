package com.openworld.movement.character;

import godot.annotation.Export;
import godot.annotation.Script;
import godot.api.Resource;

@Script(className = "CombatState")
public class CombatState extends Resource {

  @Export
  public boolean combat = false;

  @Export
  public double speedFactor = 1.0f;

  @Export
  public double accelerationFactor = 1.0f;

  @Export
  public double cameraDistance = 2.0f;

  /**
   * Sideways camera offset in metres, along the CAMERA's own right — so positive is the right
   * shoulder and negative the left. The game ships LEFT (the authored states are negative).
   *
   * <p>The sign only started meaning anything with AIM_PLAN.md W3. Before it, the offset was
   * applied along the Yaw node's X under a rig carrying an uncancelled 180-degree flip, so it went
   * the opposite way from the camera's right and a positive number produced a left-shoulder camera.
   * Removing the flip made the axis honest, which mirrors the framing — so the authored values were
   * negated to keep the shipped side. Flipping them back is now a one-character change per state,
   * and it means what it says.
   */
  @Export
  public double cameraShoulderOffset = -0.1f;

  /**
   * Vertical camera adjustment applied ON TOP of the current stance's camera height while in
   * this combat state. An OFFSET (not an absolute height) so it composes with stances —
   * crouch-aim stays lower than stand-aim. Negative lowers the camera toward the gun line for
   * a tight over-the-shoulder aim (RE4/PUBG style); 0 leaves the stance height unchanged.
   */
  @Export
  public double cameraHeightOffset = 0.0f;

  @Export
  public double cameraFov = 70.0f;


  // Default constructor is required for Godot to instantiate the Resource
  public CombatState() {
    super();
  }

  public boolean isCombat() {
    return combat;
  }

  /** Setter half of the exported {@code combat} property. */
  public void setCombat(boolean value) {
    this.combat = value;
  }

  public double getSpeedFactor() {
    return speedFactor;
  }

  /** Setter half of the exported {@code speedFactor} property. */
  public void setSpeedFactor(double value) {
    this.speedFactor = value;
  }

  public double getAccelerationFactor() {
    return accelerationFactor;
  }

  /** Setter half of the exported {@code accelerationFactor} property. */
  public void setAccelerationFactor(double value) {
    this.accelerationFactor = value;
  }
}

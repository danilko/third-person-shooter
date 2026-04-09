package com.character;

import com.ui.Crosshair;
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

@RegisterClass(className = "MovementController")
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
  private boolean combat = false;
  private double combatSpeedFactor = 1.0;
  private double combatAccelerationFactor = 1.0;
  private boolean rolling = false;
  private double rollTimer = 0.0;
  private double rollSpeed = 0.0;
  private Stance stance;

  @RegisterProperty
  @Export
  public WeaponController weaponController;

  @RegisterProperty
  @Export
  public Crosshair crosshair;

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

    if (rolling) {
      velocity.setX(rollSpeed * normDir.getX());
      velocity.setZ(rollSpeed * normDir.getZ());
    } else {
      velocity.setX(speed * normDir.getX());
      velocity.setZ(speed * normDir.getZ());
    }

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
    if (rolling && direction.lengthSquared() > 0.001) {
      // During roll: always face movement direction, even in combat
      targetRotation = atan2(direction.getX(), direction.getZ()) - playerInitRotation;
    } else if (combat) {
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

    WeaponStats weaponStats = weaponController.getCurrentWeaponStats();
    float baseSpread = weaponStats.getSpread() + weaponStats.getMovementSpread() + (float) velocity.length();
    float jumpContrib = weaponStats.jumpSpread * (player.isOnFloor() ? 1 : 0);
    float aimContrib = combat ? weaponStats.getAimSpread() * 2 : 0;
    float crouchContrib = "Idle".equalsIgnoreCase(stance.getName().toString()) ? weaponStats.crouchSpread * 2 : 0;
    crosshair.setPositionX(baseSpread + jumpContrib + aimContrib + crouchContrib);
  }


  @RegisterFunction
  public void onSetStance(Stance stance) {
    this.stance = stance;
  }

  @RegisterFunction
  public void roll(RollState rollState) {
    rolling = true;
    rollTimer = rollState.getRollDuration();
    rollSpeed = rollState.getRollSpeed();
  }

  @RegisterFunction
  public void completedRoll() {
    rolling = false;
  }

  @RegisterFunction
  public void jump(JumpState jumpState) {
    velocity.setY(2.0 * jumpState.getJumpHeight() / jumpState.getApexDuration());
    jumpGravity = velocity.getY() / jumpState.getApexDuration();
  }

  @RegisterFunction
  public void onSetMovementState(MovementState movementState) {
    speed = movementState.getMovementSpeed() * combatSpeedFactor;
    acceleration = movementState.getAcceleration() * combatAccelerationFactor;
  }

  @RegisterFunction
  public void onSetCombatState(CombatState combatState) {
    combat = combatState.isCombat();
    combatSpeedFactor = combatState.getSpeedFactor();
    combatAccelerationFactor = combatState.getAccelerationFactor();
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
package com.character;

import com.ui.Crosshair;
import godot.api.CharacterBody3D;
import godot.api.Label;
import godot.annotation.*;
import godot.api.*;
import godot.core.*;
import godot.global.GD;

import java.lang.Math;

@RegisterClass
public class Player extends CharacterBody3D {

  // Signal Definitions
  @RegisterSignal
  public final Signal1<JumpState> pressedJump = Signal1.create(this, "pressedJump");

  @RegisterSignal
  public final Signal1<RollState> pressedRoll = Signal1.create(this, "pressedRoll");

  @RegisterSignal
  public final Signal1<Stance> changedStance = Signal1.create(this, "changedStance");

  @RegisterSignal
  public final Signal0 fireWeapon = Signal0.create(this, "fireWeapon");
  @RegisterSignal
  public final Signal0 notFireWeapon = Signal0.create(this, "notFireWeapon");

  @RegisterSignal
  public final Signal1<MovementState> changedMovementState = Signal1.create(this, "changedMovementState");

  @RegisterSignal
  public final Signal1<Vector3> changedMovementDirection = Signal1.create(this, "changedMovementDirection");

  @RegisterSignal
  public final Signal1<CombatState> changedCombatState = Signal1.create(this, "changedCombatState");

  @RegisterSignal
  public final Signal1<Integer> changedWeapon = Signal1.create(this, "changedWeapon");

  @RegisterSignal
  public final Signal0 reloadWeapon = Signal0.create(this, "reloadWeapon");

  // Exports
  @RegisterProperty
  public int maxAirJump = 1;

  @Export
  @RegisterProperty
  public Dictionary<String, JumpState> jumpStates = new Dictionary<>(String.class, JumpState.class);

  @Export
  @RegisterProperty
  public Dictionary<String, NodePath> stances = new Dictionary<>(String.class, NodePath.class);

  @Export
  @RegisterProperty
  public Dictionary<String, CombatState> combatStates = new Dictionary<>(String.class, CombatState.class);

  @Export
  @RegisterProperty
  public RollState rollState = null;

  // Internal State
  private int airJumpCounter = 0;
  private Vector3 movementDirection = new Vector3();
  private StanceName currentStanceName = StanceName.UPRIGHT;
  private MovementType currentMovementType = MovementType.IDLE;
  private boolean isRolling = false;
  private Timer stanceAntispamTimer;
  private Timer rollTimer;
  private Timer aimStayTimer;

  private boolean combat = false;
  private boolean wantCombat = false;

  private RayCast3D rayCast3D;
  private Marker3D marker3D;
  private Label healthLabel;
  private Health healthNode;

  @RegisterProperty
  @Export
  public WeaponController weaponController;

  @RegisterProperty
  @Export
  public NodePath rayCastNodePath = new NodePath("CameraRoot/Yaw/Pitch/Pivot/SpringArm/Camera/RayCast3D");

  @RegisterProperty
  @Export
  public NodePath spineIKTargetPath = new NodePath("CameraRoot/Yaw/Pitch/Pivot/SpringArm/Camera/SpineIKTarget");

  @RegisterProperty
  @Export
  public NodePath healthLabelPath = new NodePath("UI/Health/ColorRect/Health");

  @RegisterFunction
  @Override
  public void _ready() {
    SceneTree tree = getTree();
    stanceAntispamTimer = (Timer) getNode("StanceAntispamTimer");
    rollTimer = (Timer) getNode("RollTimer");
    rollTimer.setWaitTime(rollState.getRollDuration());

    aimStayTimer = (Timer) getNode("AimStayTimer");

    rayCast3D = (RayCast3D) getNode(rayCastNodePath);
    rayCast3D.addException(this);
    marker3D = (Marker3D) getNode(spineIKTargetPath);

    healthLabel = (Label) getNode(healthLabelPath);
    healthNode = (Health) getNode("Health");
    healthLabel.setText(String.valueOf((int) healthNode.getCurrentHealth()));

    changedMovementDirection.emit(Vector3.Companion.getBACK());
    setMovementState(MovementType.IDLE);
    setStance(currentStanceName);
    setCombatState();
    setWeapon(0);
  }

  @RegisterFunction
  @Override
  public void _input(InputEvent event) {
    if (event == null) return;

    Input input = Input.INSTANCE;


    if (event.isActionPressed("movement", false) || event.isActionReleased("movement", false)) {
      MovementType movementType = MovementType.IDLE;
      movementDirection.setX(input.getActionStrength("left") - input.getActionStrength("right"));
      movementDirection.setZ(input.getActionStrength("forward") - input.getActionStrength("back"));

      if (isMovementOngoing()) {
        movementType = input.isActionPressed("walk", false) ? MovementType.WALK : MovementType.SPRINT;
      }

      setMovementState(movementType);
    }

    if (input.isActionPressed("reload", false)) {
      reloadWeapon.emitSignal();
    }

    if (!isRolling) {
      // Update combat

      Vector3 currentRotationDegree = rayCast3D.getRotationDegrees();
      rayCast3D.setRotationDegrees(new Vector3(currentRotationDegree.getX(), 0.0f, 0.0f));

      wantCombat = input.isActionPressed("aim", false) || input.isActionPressed("fire", false);

      if (wantCombat) {
        // Cancel any pending exit — player re-engaged aim/fire
        aimStayTimer.stop();
        if (!combat) {
          combat = true;
          setCombatState();
        }
      } else if (combat && aimStayTimer.isStopped()) {
        // Released aim/fire and timer not yet running — begin the hold window
        aimStayTimer.start();
      }

      if (input.isActionPressed("fire", false)) {
        fireWeapon.emit();
      } else {
        notFireWeapon.emit();
      }


      if (input.isActionPressed("jump", false)) {
        if (airJumpCounter <= maxAirJump) {
          if (isStanceBlocked(StanceName.UPRIGHT)) return;

          if (currentStanceName != StanceName.UPRIGHT) {
            setStance(StanceName.UPRIGHT);
            return;
          }

          String jumpName = (airJumpCounter > 0) ? "AirJump" : "GroundJump";
          JumpState state = jumpStates.get(jumpName);
          if (state != null) {
            pressedJump.emit(state);
          }
          airJumpCounter++;
        }
      }

      if (input.isActionPressed("roll", false)) {
        if (isOnFloor() && isMovementOngoing() && !weaponController.isWeaponReloading()) {
          if (rollTimer == null || rollTimer.getTimeLeft() <= 0) {
            roll(true);
          }
        }
      }
    } else {
      notFireWeapon.emit();
    }

    if (isOnFloor() && (rollTimer == null || rollTimer.getTimeLeft() <= 0)) {
      for (String stanceKey : stances.keys()) {
        if (event.isActionPressed(stanceKey.toLowerCase(), false)) {
          setStance(StanceName.fromKey(stanceKey));
        }
      }
    }
  }

  private void setCombatState() {
    changedCombatState.emit(combatStates.get(combat ? "Combat" : "NoCombat"));
  }

  @RegisterFunction
  public void onPlayerDied() {
    setProcessInput(false);
    GD.print("Player died — game over");
    // TODO Phase 5: show game-over screen
  }

  @RegisterFunction
  public void onPlayerDamaged(float amount) {
    healthLabel.setText(String.valueOf((int) healthNode.getCurrentHealth()));
  }

  private void roll(boolean isRoll) {

    isRolling = isRoll;

    // If not crouch, swap to crouch collider during roll
    if (currentStanceName != StanceName.CROUCH) {

      StanceName disabledStanceName = isRoll ? currentStanceName : StanceName.CROUCH;
      StanceName enabledStanceName  = isRoll ? StanceName.CROUCH  : currentStanceName;

      NodePath disabledStancePath = stances.get(disabledStanceName.getKey());
      if (disabledStancePath != null) {
        Stance currentStanceNode = (Stance) getNode(disabledStancePath);
        if (currentStanceNode != null && currentStanceNode.getCollider() != null) {
          currentStanceNode.getCollider().setDisabled(true);
        }
      }

      NodePath enabledStancePath = stances.get(enabledStanceName.getKey());
      if (enabledStancePath != null) {
        Stance currentStanceNode = (Stance) getNode(enabledStancePath);
        if (currentStanceNode != null && currentStanceNode.getCollider() != null) {
          currentStanceNode.getCollider().setDisabled(false);
        }
      }
    }

    if (isRoll) {
      pressedRoll.emit(rollState);
    }
  }

  @RegisterFunction
  public void completedRoll() {
    roll(false);
  }

  @RegisterFunction
  @Override
  public void _physicsProcess(double delta) {
    if (isMovementOngoing()) {
      changedMovementDirection.emit(movementDirection);
    }

    if (isOnFloor()) {
      airJumpCounter = 0;
    } else if (airJumpCounter == 0) {
      airJumpCounter = 1;
    }

    if (combat) {
      // Exit combat once the aim-stay timer has finished and player is no longer engaging
      if (aimStayTimer.isStopped() && !wantCombat) {
        combat = false;
        setCombatState();
      }

      if (rayCast3D.isColliding() && (rayCast3D.getCollisionPoint().minus(rayCast3D.getGlobalTransform().getOrigin())).length() > 0.1) {
        marker3D.setGlobalPosition(rayCast3D.getCollisionPoint());
      } else {
        marker3D.setGlobalPosition(rayCast3D.toGlobal(rayCast3D.getTargetPosition()));
      }
    }
  }

  private boolean isMovementOngoing() {
    return Math.abs(movementDirection.getX()) > 0 || Math.abs(movementDirection.getZ()) > 0;
  }

  public void setMovementState(MovementType type) {
    NodePath path = stances.get(currentStanceName.getKey());
    if (path == null) return;

    Stance stanceNode = (Stance) getNode(path);
    if (stanceNode == null) return;
    currentMovementType = type;
    changedMovementState.emit(stanceNode.getMovementState(type));
  }

  private void setStance(StanceName stanceName) {
    if (stanceAntispamTimer.getTimeLeft() > 0) {
      return;
    }

    SceneTree tree = getTree();
    if (tree != null) {
      stanceAntispamTimer.start();
    }

    StanceName nextStanceName = (stanceName == currentStanceName) ? StanceName.UPRIGHT : stanceName;

    if (isStanceBlocked(nextStanceName)) return;

    // Disable current collider
    NodePath currentPath = stances.get(currentStanceName.getKey());
    if (currentPath != null) {
      Stance currentStanceNode = (Stance) getNode(currentPath);
      if (currentStanceNode != null && currentStanceNode.getCollider() != null) {
        currentStanceNode.getCollider().setDisabled(true);
      }
    }

    // Update name and enable new collider
    currentStanceName = nextStanceName;
    NodePath nextPath = stances.get(currentStanceName.getKey());
    if (nextPath != null) {
      Stance nextStanceNode = (Stance) getNode(nextPath);
      if (nextStanceNode != null) {
        if (nextStanceNode.getCollider() != null) {
          nextStanceNode.getCollider().setDisabled(false);
        }
        changedStance.emit(nextStanceNode);
      }
    }

    setMovementState(currentMovementType);
  }

  private boolean isStanceBlocked(StanceName stanceName) {
    NodePath path = stances.get(stanceName.getKey());
    if (path == null) return false;

    Stance stanceNode = (Stance) getNode(path);
    return (stanceNode != null) && stanceNode.isBlocked();
  }

  public void setMovementDirection(Vector3 movementDirection) {
    this.movementDirection = movementDirection;
  }


  public void setWeapon(int weapon) {
    changedWeapon.emit(weapon);
  }
}
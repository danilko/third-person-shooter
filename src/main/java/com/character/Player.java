package com.character;

import godot.api.CharacterBody3D;
import godot.annotation.*;
import godot.api.*;
import godot.core.*;
import godot.global.GD;

import java.lang.Math;
import java.lang.Object;

@RegisterClass
public class Player extends CharacterBody3D {

  // Signal Definitions
  @RegisterSignal
  public final Signal1<JumpState> pressedJump = Signal1.create(this, "pressedJump");

  @RegisterSignal
  public final Signal1<Stance> changedStance = Signal1.create(this, "changedStance");

  @RegisterSignal
  public final Signal1<MovementState> changedMovementState = Signal1.create(this, "changedMovementState");

  @RegisterSignal
  public final Signal1<Vector3> changedMovementDirection = Signal1.create(this, "changedMovementDirection");

  @RegisterSignal
  public final Signal1<StrafingState> changedStrafingState= Signal1.create(this, "changedStrafingState");
  // Exports
  @RegisterProperty
  public int maxAirJump = 1;

  @Export
  @RegisterProperty
  public Dictionary<String, JumpState> jumpStates = new Dictionary<>(String.class, JumpState.class);

  @Export
  @RegisterProperty
  public Dictionary<String, NodePath> stances = new Dictionary<>(String.class, NodePath.class);

  // Internal State
  private int airJumpCounter = 0;
  private Vector3 movementDirection = new Vector3();
  private String currentStanceName = "Upright";
  private String currentMovementStateName = "";
  private SceneTreeTimer stanceAntispamTimer;
  private StrafingState strafingState = new StrafingState();

  @RegisterFunction
  @Override
  public void _ready() {
    SceneTree tree = getTree();
    if (tree != null) {
      stanceAntispamTimer = tree.createTimer(0.25f);
    }

    changedMovementDirection.emit(Vector3.Companion.getBACK());
    setMovementState("Idle");
    setStance(currentStanceName);
    setStrafingState();
  }

  @RegisterFunction
  @Override
  public void _input(InputEvent event) {
    if (event == null) return;
    Input input = Input.INSTANCE;
    if (event.isActionPressed("movement", false) || event.isActionReleased("movement", false)) {



      movementDirection.setX(input.getActionStrength("left") - input.getActionStrength("right"));
      movementDirection.setZ(input.getActionStrength("forward") - input.getActionStrength("back"));
      String movementState = "Idle";
      if (isMovementOngoing()) {

        if (input.isActionPressed("walk", false)) {
          movementState = "Walk";
        }
        else {

          movementState = "Sprint";
        }
      }

      setMovementState(movementState);
    }

    // Update staring
    boolean isAiming = input.isActionPressed("aim", false) || input.isActionPressed("fire", false);
    StrafingState.StrafingStateType desired = isAiming
                                                ? StrafingState.StrafingStateType.STRAFING
                                                : StrafingState.StrafingStateType.NOT_STRAFING;

    if (strafingState.getStrafingStateType() != desired) {
      strafingState.setStrafingStateType(desired);

      setStrafingState();
    }

    if (input.isActionPressed("jump", false)) {
      if (airJumpCounter <= maxAirJump) {
        if (isStanceBlocked("Upright")) return;

        if (!currentStanceName.equals("Upright")) {
          setStance("Upright");
          return;
        }

        String jumpName = (airJumpCounter > 0) ? "AirJump" : "GroundJump";
        JumpState state = jumpStates.get(jumpName);
        if (state != null) {
          getPressedJump().emit(state);
        }
        airJumpCounter++;
      }
    }

    if (isOnFloor()) {
      for (String stanceKey : stances.keys()) {
        if (event.isActionPressed(stanceKey.toLowerCase(), false)) {
          setStance(stanceKey);
        }
      }
    }
  }

  private void setStrafingState() {
    changedStrafingState.emit(strafingState);
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
  }

  private boolean isMovementOngoing() {
    return Math.abs(movementDirection.getX()) > 0 || Math.abs(movementDirection.getZ()) > 0;
  }

  private void setMovementState(String state) {


    NodePath path = stances.get(currentStanceName);

    if (path == null) return;

    Stance stanceNode = (Stance) getNode(path);
    if (stanceNode == null) return;
    currentMovementStateName = state;
    changedMovementState.emit(stanceNode.getMovementState(state));
  }

  private void setStance(String stanceName) {
    if (stanceAntispamTimer != null && stanceAntispamTimer.getTimeLeft() > 0) {
      return;
    }

    SceneTree tree = getTree();
    if (tree != null) {
      stanceAntispamTimer = tree.createTimer(0.25);
    }

    String nextStanceName = (stanceName.equals(currentStanceName)) ? "Upright" : stanceName;

    if (isStanceBlocked(nextStanceName)) return;

    // Disable current collider
    NodePath currentPath = stances.get(currentStanceName);
    if (currentPath != null) {
      Stance currentStanceNode = (Stance) getNode(currentPath);
      if (currentStanceNode != null && currentStanceNode.getCollider() != null) {
        currentStanceNode.getCollider().setDisabled(true);
      }
    }

    // Update name and enable new collider
    currentStanceName = nextStanceName;
    NodePath nextPath = stances.get(currentStanceName);
    if (nextPath != null) {
      Stance nextStanceNode = (Stance) getNode(nextPath);
      if (nextStanceNode != null) {
        if (nextStanceNode.getCollider() != null) {
          nextStanceNode.getCollider().setDisabled(false);
        }
        changedStance.emit(nextStanceNode);
      }
    }

    setMovementState(currentMovementStateName);
  }

  private boolean isStanceBlocked(String stanceName) {
    NodePath path = stances.get(stanceName);
    if (path == null) return false;

    Stance stanceNode = (Stance) getNode(path);
    return (stanceNode != null) && stanceNode.isBlocked();
  }


  public Signal1<JumpState> getPressedJump() {
    return pressedJump;
  }


  public Signal1<Stance> getChangedStance() {
    return changedStance;
  }


  public Signal1<MovementState> getChangedMovementState() {
    return changedMovementState;
  }


  public Signal1<Vector3> getChangedMovementDirection() {
    return changedMovementDirection;
  }


  public int getMaxAirJump() {
    return maxAirJump;
  }

  public void setMaxAirJump(int maxAirJump) {
    this.maxAirJump = maxAirJump;
  }

  public Dictionary<String, JumpState> getJumpStates() {
    return jumpStates;
  }

  public void setJumpStates(Dictionary<String, JumpState> jumpStates) {
    this.jumpStates = jumpStates;
  }

  public Dictionary<String, NodePath> getStances() {
    return stances;
  }

  public void setStances(Dictionary<String, NodePath> stances) {
    this.stances = stances;
  }

  public int getAirJumpCounter() {
    return airJumpCounter;
  }

  public void setAirJumpCounter(int airJumpCounter) {
    this.airJumpCounter = airJumpCounter;
  }

  public Vector3 getMovementDirection() {
    return movementDirection;
  }

  public void setMovementDirection(Vector3 movementDirection) {
    this.movementDirection = movementDirection;
  }

  public String getCurrentStanceName() {
    return currentStanceName;
  }

  public void setCurrentStanceName(String currentStanceName) {
    this.currentStanceName = currentStanceName;
  }

  public String getCurrentMovementStateName() {
    return currentMovementStateName;
  }

  public void setCurrentMovementStateName(String currentMovementStateName) {
    this.currentMovementStateName = currentMovementStateName;
  }

  public SceneTreeTimer getStanceAntispamTimer() {
    return stanceAntispamTimer;
  }

  public void setStanceAntispamTimer(SceneTreeTimer stanceAntispamTimer) {
    this.stanceAntispamTimer = stanceAntispamTimer;
  }

  public StrafingState getStrafingState() {
    return strafingState;
  }
}
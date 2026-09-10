package com.openworld.camera;

import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Input;
import godot.api.InputEvent;
import godot.api.InputEventMouseMotion;
import godot.core.Vector2;
import com.openworld.character.Character;
import com.openworld.control.Controller;
import com.openworld.control.PlayerController;
import com.openworld.net.NetworkController;

@Script(className = "PlayerCameraController")
public class PlayerCameraController extends TPSCameraController {

  // Accumulated raw pixel deltas from all mouse-motion events since last physics step.
  // Using _input + getRelative() (the same pattern as VehicleCameraController) captures
  // every mouse event between physics frames; getLastMouseVelocity()*delta only reads
  // the last event velocity and drops all intermediate events.
  private double pendingYaw   = 0;
  private double pendingPitch = 0;

  @Register
  @Override
  public void _ready() {
    super._ready();
    // Mouse capture is NOT decided here: same lifecycle problem as camera activation
    // (see the comment below) — `player.getController()` is still null at this point,
    // since Character._ready() resolves it bottom-up *after* this node's _ready() runs.
    // isLocallyControlled() (called lazily from gatherLookInput, every physics frame)
    // owns that decision instead, mirroring Character.activateCameraIfOwned().
    //
    // Camera activation is NOT decided here either: at this point activeCamera is still
    // null (this _ready() runs bottom-up, before Character._ready() resolves it —
    // see TPSCameraController.setCameraFov's comment for the canonical explanation).
    // Character.activateCameraIfOwned() — deferred from Character._ready() once
    // activeCamera/characterInfo are populated — owns this decision instead.
  }

  /**
   * True only for the single body this machine's human is actually playing —
   * i.e. the body whose Controller child is a PlayerController (not
   * ServerProxyController, which drives a *remote* human's body on the server,
   * nor NetworkController, which drives a replica). Godot delivers every _input
   * event and global Input.isAction()/Input.MouseMode change to every node that
   * processes them, regardless of which body's camera is active — without this
   * gate, a listen-server host with a connected client ends up with two
   * PlayerCameraControllers (its own + the client's ServerProxyController-driven
   * proxy) both consuming the host's mouse, so the proxy body's mesh silently
   * tracks wherever the *host* is looking instead of the remote client's actual
   * aim — exactly the "wrong direction" replication symptom (round 5c report).
   */
  private boolean isLocallyControlled() {
    // `character`, not `player`: the runtime-resolved reference (TPSCameraController._ready).
    // A stale export wrapper's controller field is null, which would silently make every body
    // non-local and swallow all mouse look.
    return character != null && character.getController() instanceof PlayerController;
  }

  @Register
  @Override
  public void _input(InputEvent event) {
    if (!isLocallyControlled()) return;
    if (event instanceof InputEventMouseMotion mm) {
      pendingYaw   -= mm.getRelative().getX() * yawSensitivity;
      // Godot's mouse Y grows DOWNWARD and positive pitch is UP, so this subtracts. It added
      // while Pivot's 180° flip negated the pitch downstream (AIM_PLAN.md W3); the two changed
      // together and the resulting view is identical.
      pendingPitch -= mm.getRelative().getY() * pitchSensitivity;
    }
  }

  @Override
  protected Vector2 gatherLookInput(double delta) {
    if (!isLocallyControlled()) return Vector2.Companion.getZERO();

    // While on-foot input is blocked by a UI overlay that owns the mouse (the radial weapon menu,
    // pause menu — they set Character.inputBlocked AND mouse mode VISIBLE), do NOT re-capture the
    // mouse or apply look. This runs in _physicsProcess, which setProcessInput(false) does not stop,
    // so without this guard the per-frame CAPTURED re-grab below immediately hides/locks the cursor
    // the overlay just made visible — making the radial menu impossible to navigate with the mouse.
    if (character != null && character.inputBlocked) {
      pendingYaw   = 0;
      pendingPitch = 0;
      return Vector2.Companion.getZERO();
    }

    // Deferred from _ready() (see comment there) — idempotent, cheap to repeat.
    Input.setMouseMode(Input.MouseMode.CAPTURED);

    boolean isFps = character != null && character.isFpsMode;

    if (Input.isActionJustPressed("shoulder", false) && !isFps) {
      changeShoulderDirection();
    }

    if (Input.isActionJustPressed("view", false)) {
      if (character != null) character.setCameraMode(!character.isFpsMode);
    }

    double dy = pendingYaw;
    double dp = pendingPitch;
    pendingYaw   = 0;
    pendingPitch = 0;
    return new Vector2((float) dy, (float) dp);
  }
}

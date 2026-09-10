package com.openworld.debug;

import com.openworld.control.Controller;
import com.openworld.control.UserCommand;
import com.openworld.movement.character.MovementType;
import com.openworld.movement.character.StanceName;
import godot.annotation.Script;
import godot.core.Vector3;

/**
 * A {@link Controller} whose command is written from the outside, one field at a time — the third
 * command source the UserCommand loop was built for, standing in for a pair of hands.
 *
 * <p>{@link ScriptedDriveController} replays a fixed timeline and is right for a physics soak test;
 * this one is the opposite shape and is what a HANDLING test needs: the host drives it step by
 * step, asserting between steps, so a failure names the step it happened at. It is deliberately
 * body-agnostic — the same instance walks a {@link com.openworld.character.Character} and then
 * drives the {@link com.openworld.carrier.vehicle.Vehicle} it is hot-swapped onto, which is exactly
 * what happens to a player's controller on {@code tryEnter}.
 *
 * <p>{@link #pressEnterExit()} is a ONE-SHOT, and that is not a convenience: the real source is
 * {@code Input.isActionJustPressed}, so a held-true {@code enterExit} would be a key that is
 * pressed every physics frame — the car would be entered and left again on alternating ticks and
 * the test would pass or fail on parity.
 */
@Script(className = "ScriptedInputController")
public class ScriptedInputController extends Controller {

    /** World-space walk intent, straight through to {@code MovementController.direction}. */
    public Vector3 move = Vector3.Companion.getZERO();
    public MovementType movementType = MovementType.IDLE;
    /** Throttle when this controller is driving a vehicle (−1 reverse … 1 full). */
    public float motor = 0f;
    public float steering = 0f;
    public boolean brake = false;

    // ── Pose / aim intent, for a stand that poses a body instead of driving one ──────────────
    /** Combat pose: what turns the aim modifiers on (AnimationController.onSetCombatState). */
    public boolean wantCombat = false;
    /** null leaves the stance alone; otherwise requested every tick, so it also HOLDS a stance. */
    public StanceName desiredStance = null;
    /** Weapon slot to hold. Negative leaves the current one alone. */
    public int desiredWeapon = -1;
    /** World point to aim at; null leaves the AimTarget marker where it is. */
    public Vector3 aimTargetPosition = null;
    /** Trigger held. Goes through the same UserCommand field a human's trigger sets. */
    public boolean fire = false;
    public boolean reload = false;

    private boolean enterExitPending = false;
    private long tick = 0;

    /** Queue a single {@code use_carrier} press, consumed by the next {@link #gatherInput}. */
    public void pressEnterExit() { enterExitPending = true; }

    @Override
    public UserCommand gatherInput(double delta) {
        UserCommand cmd = new UserCommand();
        cmd.movementDirection = move;
        cmd.movementType = movementType;
        cmd.motor = motor;
        cmd.steering = steering;
        cmd.brake = brake;
        cmd.enterExit = enterExitPending;
        cmd.wantCombat = wantCombat;
        cmd.desiredStance = desiredStance;
        if (desiredWeapon >= 0) cmd.desiredWeapon = desiredWeapon;
        cmd.aimTargetPosition = aimTargetPosition;
        cmd.fire = fire;
        cmd.reload = reload;
        cmd.tick = ++tick;
        enterExitPending = false;
        return cmd;
    }
}

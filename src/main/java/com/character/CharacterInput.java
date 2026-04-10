package com.character;

import godot.core.Vector3;

/**
 * Declarative per-tick input snapshot for a Character.
 *
 * Produced by:
 *   - Player   : samples keyboard/mouse via Input singleton
 *   - Enemy    : AI FSM translates decisions into this struct
 *   - Network  : server/client injects a deserialized snapshot (future)
 *
 * Consumed by Character.applyInput(), which drives all state transitions.
 *
 * Network-sync design notes
 * ─────────────────────────
 * All fields are primitives or copied value types — the struct is cheap to
 * copy, diff, and serialize.  The `tick` field lets an authoritative server
 * reorder, deduplicate, and replay inputs for client-side prediction and
 * reconciliation.  Use copy() to build a prediction rollback buffer.
 */
public class CharacterInput {

    // ── Movement ──────────────────────────────────────────────────────────────

    /** Desired XZ movement direction. Need not be normalized. */
    public Vector3 movementDirection;

    /** Desired gait this tick. */
    public MovementType movementType;

    // ── Combat ────────────────────────────────────────────────────────────────

    /**
     * True while the character wants to maintain a combat/aim stance.
     * Character.applyInput() enters or exits combat when this diverges from
     * the current combat state.
     */
    public boolean wantCombat;

    // ── Weapon actions (one-shot per tick) ───────────────────────────────────

    /** Fire the current weapon this tick. */
    public boolean fire;

    /** Trigger a weapon reload this tick. */
    public boolean reload;

    // ── Body actions (one-shot per tick) ─────────────────────────────────────

    /** Attempt a jump this tick. */
    public boolean jump;

    /** Attempt to start a roll this tick. */
    public boolean roll;

    // ── State requests ────────────────────────────────────────────────────────

    /**
     * Request a stance change. null = no change this tick.
     * Character.setStance() applies toggle semantics: requesting the current
     * stance reverts to UPRIGHT.
     */
    public StanceName desiredStance;

    /**
     * Request a weapon switch. -1 = no change this tick.
     */
    public int desiredWeapon;

    // ── Aim ───────────────────────────────────────────────────────────────────

    /**
     * World-space position the character is aiming at this tick.
     * null = do not update AimTarget node.
     *
     * Player: derived from camera raycast collision point.
     * Enemy:  set to the tracked target's global position.
     * Network: received from server authoritative aim state.
     */
    public Vector3 aimTargetPosition;

    // ── Network sequencing ────────────────────────────────────────────────────

    /**
     * Monotonically increasing tick number.  Stamped by Character._physicsProcess
     * so inputs are totally ordered and can be replayed during reconciliation.
     */
    public long tick;

    // ── Construction ──────────────────────────────────────────────────────────

    public CharacterInput() {
        movementDirection = new Vector3();
        movementType      = MovementType.IDLE;
        wantCombat        = false;
        fire              = false;
        reload            = false;
        jump              = false;
        roll              = false;
        desiredStance     = null;
        desiredWeapon     = -1;
        aimTargetPosition = null;
        tick              = 0;
    }

    /**
     * Shallow copy — safe because primitives are copied by value and Vector3
     * fields are copied explicitly.  Use this to populate a prediction buffer.
     */
    public CharacterInput copy() {
        CharacterInput c    = new CharacterInput();
        c.movementDirection = new Vector3(
                movementDirection.getX(),
                movementDirection.getY(),
                movementDirection.getZ());
        c.movementType      = movementType;
        c.wantCombat        = wantCombat;
        c.fire              = fire;
        c.reload            = reload;
        c.jump              = jump;
        c.roll              = roll;
        c.desiredStance     = desiredStance;
        c.desiredWeapon     = desiredWeapon;
        c.aimTargetPosition = aimTargetPosition != null
                ? new Vector3(aimTargetPosition.getX(),
                              aimTargetPosition.getY(),
                              aimTargetPosition.getZ())
                : null;
        c.tick              = tick;
        return c;
    }
}

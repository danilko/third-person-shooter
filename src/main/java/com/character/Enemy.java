package com.character;

import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.NavigationAgent3D;
import godot.api.RayCast3D;
import godot.core.Vector3;
import godot.global.GD;

@RegisterClass(className = "Enemy")
public class Enemy extends Character {

    private enum State { PATROL, CHASE, ATTACK }

    // ── Inspector-tunable properties ──────────────────────────────────────────
    @Export
    @RegisterProperty
    public Character player;

    @Export
    @RegisterProperty
    public float detectionRange = 12.0f;

    @Export
    @RegisterProperty
    public float attackRange = 15.0f;

    @Export
    @RegisterProperty
    public float patrolRadius = 8.0f;

    // ── AI state ──────────────────────────────────────────────────────────────
    private NavigationAgent3D navAgent;
    private RayCast3D sightRay;

    private State state = State.PATROL;
    private Vector3 spawnPosition;
    private double attackTimer = 0.0;
    private double lostPlayerTimer = 0.0;
    private boolean isDead = false;

    private static final double LOST_PLAYER_TIMEOUT = 3.0;

    // ── Lifecycle ─────────────────────────────────────────────────────────────
    @RegisterFunction
    @Override
    public void _ready() {
        super._ready();
        navAgent      = (NavigationAgent3D) getNode("NavigationAgent3D");
        sightRay      = (RayCast3D)         getNode("SightRay");
        spawnPosition = new Vector3(getGlobalPosition());
        setNextPatrolTarget();
    }

    // ── Input gathering (AI FSM → CharacterInput) ─────────────────────────────
    /**
     * Runs the AI FSM for this tick and expresses its decisions as a
     * CharacterInput — the same struct the Player produces from keyboard input.
     *
     * This makes the Enemy a drop-in for any future network-authoritative
     * simulation: the server can run the same FSM, produce CharacterInputs,
     * and broadcast them to clients exactly as it would for a human player.
     */
    @Override
    protected CharacterInput gatherInput(double delta) {
        CharacterInput input = new CharacterInput();

        if (isDead) return input;   // empty input → applyInput is a no-op

        attackTimer = Math.max(0.0, attackTimer - delta);

        switch (state) {
            case PATROL: fillPatrolInput(input);        break;
            case CHASE:  fillChaseInput(input, delta);  break;
            case ATTACK: fillAttackInput(input);        break;
        }

        return input;
    }

    // ── FSM input translators ─────────────────────────────────────────────────

    private void fillPatrolInput(CharacterInput input) {
        if (canSeePlayer()) {
            state = State.CHASE;
            lostPlayerTimer = 0.0;
            // Transition — let CHASE fill the input on this same tick
            fillChaseInput(input, 0.0);
            return;
        }

        input.wantCombat   = false;
        input.movementType = MovementType.WALK;

        if (!navAgent.isNavigationFinished()) {
            Vector3 dir = navAgent.getNextPathPosition()
                                  .minus(getGlobalPosition())
                                  .normalized();
            input.movementDirection.setX(dir.getX());
            input.movementDirection.setZ(dir.getZ());
        } else {
            setNextPatrolTarget();
        }
    }

    private void fillChaseInput(CharacterInput input, double delta) {
        if (player == null) {
            state = State.PATROL;
            fillPatrolInput(input);
            return;
        }

        float dist = (float) getGlobalPosition().distanceTo(player.getGlobalPosition());
        if (dist <= attackRange) {
            state = State.ATTACK;
            fillAttackInput(input);
            return;
        }

        input.wantCombat   = true;
        input.movementType = MovementType.SPRINT;

        if (canSeePlayer()) {
            lostPlayerTimer = 0.0;
            navAgent.setTargetPosition(player.getGlobalPosition());
            input.aimTargetPosition = new Vector3(
                    player.getGlobalPosition().getX(),
                    player.getGlobalPosition().getY(),
                    player.getGlobalPosition().getZ());
        } else {
            lostPlayerTimer += delta;
            if (lostPlayerTimer >= LOST_PLAYER_TIMEOUT) {
                state = State.PATROL;
                setNextPatrolTarget();
                fillPatrolInput(input);
                return;
            }
        }

        Vector3 dir = navAgent.getNextPathPosition()
                              .minus(getGlobalPosition())
                              .normalized();
        input.movementDirection.setX(dir.getX());
        input.movementDirection.setZ(dir.getZ());
    }

    private void fillAttackInput(CharacterInput input) {
        if (player == null) {
            state = State.PATROL;
            fillPatrolInput(input);
            return;
        }

        float dist = (float) getGlobalPosition().distanceTo(player.getGlobalPosition());
        if (dist > attackRange) {
            state = State.CHASE;
            fillChaseInput(input, 0.0);
            return;
        }

        // Face and aim toward the player while standing still
        Vector3 toPlayer = player.getGlobalPosition()
                                 .minus(getGlobalPosition())
                                 .normalized();
        input.movementDirection.setX(toPlayer.getX());
        input.movementDirection.setZ(toPlayer.getZ());
        input.movementType      = MovementType.IDLE;
        input.wantCombat        = true;
        input.aimTargetPosition = new Vector3(
                player.getGlobalPosition().getX(),
                player.getGlobalPosition().getY(),
                player.getGlobalPosition().getZ());

        // Fire on cooldown
        if (attackTimer <= 0.0) {
            double fireRate = (weaponController != null
                    && weaponController.getCurrentWeaponStats() != null)
                    ? weaponController.getCurrentWeaponStats().getFireRate()
                    : 0.0;
            attackTimer = (fireRate > 0.0) ? 1.0 / fireRate : 1.5;
            input.fire  = true;
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────
    private boolean canSeePlayer() {
        if (player == null) return false;
        float dist = (float) getGlobalPosition().distanceTo(player.getGlobalPosition());
        if (dist > detectionRange) return false;

        sightRay.setTargetPosition(sightRay.toLocal(player.getGlobalPosition()));
        sightRay.forceRaycastUpdate();

        if (!sightRay.isColliding()) return false;
        return sightRay.getCollider() == player;
    }

    private void setNextPatrolTarget() {
        float angle = GD.randf() * (float) Math.PI * 2.0f;
        float dist  = GD.randf() * patrolRadius;
        navAgent.setTargetPosition(spawnPosition.plus(new Vector3(
                (float) Math.cos(angle) * dist, 0.0f, (float) Math.sin(angle) * dist)));
    }

    // ── Signal receiver ───────────────────────────────────────────────────────
    @RegisterFunction
    @Override
    public void onDied() {
        isDead = true;
        queueFree();
    }
}

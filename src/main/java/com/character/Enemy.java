package com.character;

import com.character.ai.EnemyAIState;
import com.character.ai.PatrolState;
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

    private EnemyAIState currentState;
    private Vector3 spawnPosition;
    private boolean isDead = false;

    // Timers exposed as package-private so state objects can read/write them
    // without reflection — states live in the same module (com.character.ai).
    double attackTimer    = 0.0;
    double lostPlayerTimer = 0.0;

    private static final double LOST_PLAYER_TIMEOUT = 3.0;

    // ── Lifecycle ─────────────────────────────────────────────────────────────
    @RegisterFunction
    @Override
    public void _ready() {
        super._ready();
        navAgent      = (NavigationAgent3D) getNode("NavigationAgent3D");
        sightRay      = (RayCast3D)         getNode("SightRay");
        spawnPosition = new Vector3(getGlobalPosition());

        transitionTo(PatrolState.INSTANCE);
    }

    // ── Input gathering (AI FSM → CharacterInput) ─────────────────────────────
    @Override
    protected CharacterInput gatherInput(double delta) {
        CharacterInput input = new CharacterInput();
        if (isDead) return input;

        EnemyAIState next = currentState.update(this, input, delta);
        if (next != currentState) {
            transitionTo(next);
        }

        return input;
    }

    // ── State machine helpers ─────────────────────────────────────────────────

    private void transitionTo(EnemyAIState next) {
        if (currentState != null) {
            currentState.exit(this);
        }
        currentState = next;
        currentState.enter(this);
    }

    // ── Methods used by state objects ─────────────────────────────────────────

    public Character getPlayer() {
        return player;
    }

    public NavigationAgent3D getNavAgent() {
        return navAgent;
    }

    /** Returns true when the player is within detectionRange AND visible via LoS raycast. */
    public boolean canSeePlayer() {
        if (player == null) return false;
        float dist = (float) getGlobalPosition().distanceTo(player.getGlobalPosition());
        if (dist > detectionRange) return false;

        sightRay.setTargetPosition(sightRay.toLocal(player.getGlobalPosition()));
        sightRay.forceRaycastUpdate();

        if (!sightRay.isColliding()) return false;
        return sightRay.getCollider() == player;
    }

    /** Pick a new random patrol destination within patrolRadius. */
    public void setNextPatrolTarget() {
        float angle = GD.randf() * (float) Math.PI * 2.0f;
        float dist  = GD.randf() * patrolRadius;
        navAgent.setTargetPosition(spawnPosition.plus(new Vector3(
                (float) Math.cos(angle) * dist, 0.0f, (float) Math.sin(angle) * dist)));
    }

    // ── Attack-timer helpers ──────────────────────────────────────────────────

    public void resetAttackTimer() {
        attackTimer = 0.0;
    }

    public void resetAttackTimer(double value) {
        attackTimer = value;
    }

    /** Advance by delta (pass negative delta to count down). */
    public void advanceAttackTimer(double delta) {
        attackTimer = Math.max(0.0, attackTimer + delta);
    }

    public boolean isAttackReady() {
        return attackTimer <= 0.0;
    }

    // ── Lost-player timer helpers ─────────────────────────────────────────────

    public void resetLostPlayerTimer() {
        lostPlayerTimer = 0.0;
    }

    public void advanceLostPlayerTimer(double delta) {
        lostPlayerTimer += delta;
    }

    public boolean isPlayerLost() {
        return lostPlayerTimer >= LOST_PLAYER_TIMEOUT;
    }

    // ── Signal receiver ───────────────────────────────────────────────────────
    @RegisterFunction
    @Override
    public void onDied() {
        isDead = true;
        queueFree();
    }
}

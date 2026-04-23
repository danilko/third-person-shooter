package com.character;

import com.character.ai.EnemyAIState;
import com.character.ai.PatrolState;
import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.Area3D;
import godot.api.NavigationAgent3D;
import godot.core.Vector3;
import godot.global.GD;

@RegisterClass(className = "Enemy")
public class Enemy extends Character {

    /** Matches the AimRay node's Y-offset in the scene. */
    public static final float EYE_HEIGHT = 1.4f;

    /**
     * Y-offset from the player's CharacterBody3D origin (feet) to the upper body.
     * Used for aim targeting and line-of-sight so the enemy shoots at the torso,
     * not the ground point returned by getGlobalPosition().
     */
    public static final float PLAYER_BODY_HEIGHT = 0.9f;

    // ── Inspector-tunable properties ──────────────────────────────────────────
    @Export
    @RegisterProperty
    public Character player;

    @Export
    @RegisterProperty
    public float detectionRange = 12.0f;

    /** Vertical aim limit (degrees downward) — matches CameraController.pitchMin. */
    @Export
    @RegisterProperty
    public float aimPitchMin = -55.0f;

    /** Vertical aim limit (degrees upward) — matches CameraController.pitchMax. */
    @Export
    @RegisterProperty
    public float aimPitchMax = 75.0f;

    @Export
    @RegisterProperty
    public float attackRange = 15.0f;

    @Export
    @RegisterProperty
    public float patrolRadius = 8.0f;

    @Export
    @RegisterProperty
    public Area3D ammoRefill;

    private static final float AMMO_REFILL_ARRIVAL_THRESHOLD = 1.5f;

    // ── AI state ──────────────────────────────────────────────────────────────
    private NavigationAgent3D navAgent;

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
        return hasLineOfSight();
    }

    /** Pure LoS raycast with no distance limit — use when already engaged with the player. */
    public boolean hasLineOfSight() {
        if (player == null) return false;
        // Aim at the player's upper body, not their feet — avoids false negatives
        // when low cover blocks the ankle but the torso is clearly visible.
        Vector3 playerBodyPos = player.getGlobalPosition()
                                      .plus(new Vector3(0, PLAYER_BODY_HEIGHT, 0));
        cameraRoot.lookAt(playerBodyPos);
        aimRay.setTargetPosition(aimRay.toLocal(playerBodyPos));
        aimRay.forceRaycastUpdate();
        if (!aimRay.isColliding()) return false;
        return aimRay.getCollider() == player;
    }

    /**
     * Returns the index of the weapon that has ammo and the highest damage stat.
     * Returns -1 if all weapons are dry.
     */
    public int selectBestWeapon() {
        if (weaponController == null) return -1;
        int count = weaponController.getWeaponCount();
        int bestIndex = -1;
        float bestDamage = -1f;
        for (int i = 0; i < count; i++) {
            if (!weaponController.hasAmmoForWeapon(i)) continue;
            WeaponStats stats = weaponController.getWeaponStats(i);
            if (stats != null && stats.damage > bestDamage) {
                bestDamage = stats.damage;
                bestIndex = i;
            }
        }
        return bestIndex;
    }

    public boolean hasAnyAmmo() {
        return selectBestWeapon() >= 0;
    }

    public boolean isAtAmmoRefill() {
        if (ammoRefill == null) return false;
        return (float) getGlobalPosition().distanceTo(ammoRefill.getGlobalPosition())
                <= AMMO_REFILL_ARRIVAL_THRESHOLD;
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

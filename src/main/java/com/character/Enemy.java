package com.character;

import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.CharacterBody3D;
import godot.api.NavigationAgent3D;
import godot.api.RayCast3D;
import godot.core.NodePath;
import godot.core.Vector3;
import godot.global.GD;

@RegisterClass(className = "Enemy")
public class Enemy extends CharacterBody3D {

    // --- Internal FSM state (enum is Java-only, not exposed to Godot) ---
    private enum State { PATROL, CHASE, ATTACK }

    // --- Inspector-tunable properties ---
    @Export
    @RegisterProperty
    public CharacterBody3D player;

    @Export
    @RegisterProperty
    public float detectionRange = 12.0f;

    @Export
    @RegisterProperty
    public float attackRange = 2.0f;

    @Export
    @RegisterProperty
    public float moveSpeed = 3.5f;

    @Export
    @RegisterProperty
    public float attackDamage = 15.0f;

    @Export
    @RegisterProperty
    public float attackCooldown = 1.5f;

    @Export
    @RegisterProperty
    public float patrolRadius = 8.0f;

    @Export
    @RegisterProperty
    public float gravity = 9.8f;

    // --- Internal state ---
    private NavigationAgent3D navAgent;
    private RayCast3D sightRay;
    private Health health;

    private State state = State.PATROL;
    private Vector3 spawnPosition;
    private double attackTimer = 0.0;
    private double lostPlayerTimer = 0.0;
    private boolean isDead = false;

    private static final double LOST_PLAYER_TIMEOUT = 3.0;

    @RegisterFunction
    @Override
    public void _ready() {
        navAgent = (NavigationAgent3D) getNode("NavigationAgent3D");
        sightRay = (RayCast3D) getNode("SightRay");
        health = (Health) getNode("Health");
        spawnPosition = new Vector3(getGlobalPosition());
        setNextPatrolTarget();
    }

    @RegisterFunction
    @Override
    public void _physicsProcess(double delta) {
        if (isDead) return;

        // Apply gravity when airborne
        if (!isOnFloor()) {
            Vector3 vel = getVelocity();
            vel.setY(vel.getY() - gravity * (float) delta);
            setVelocity(vel);
        }

        attackTimer = Math.max(0.0, attackTimer - delta);

        switch (state) {
            case PATROL: updatePatrol(delta); break;
            case CHASE:  updateChase(delta);  break;
            case ATTACK: updateAttack(delta); break;
        }

        moveAndSlide();
    }

    // --- State update methods ---

    private void updatePatrol(double delta) {
        if (canSeePlayer()) {
            state = State.CHASE;
            lostPlayerTimer = 0.0;
            return;
        }

        if (!navAgent.isNavigationFinished()) {
            Vector3 nextPos = navAgent.getNextPathPosition();
            Vector3 dir = nextPos.minus(getGlobalPosition()).normalized();
            setVelocity(new Vector3(dir.getX() * moveSpeed, getVelocity().getY(), dir.getZ() * moveSpeed));
            faceDirection(dir);
        } else {
            setNextPatrolTarget();
        }
    }

    private void updateChase(double delta) {
        if (player == null) {
            state = State.PATROL;
            return;
        }

        float dist = (float) getGlobalPosition().distanceTo(player.getGlobalPosition());

        if (dist <= attackRange) {
            state = State.ATTACK;
            return;
        }

        if (canSeePlayer()) {
            lostPlayerTimer = 0.0;
            navAgent.setTargetPosition(player.getGlobalPosition());
        } else {
            lostPlayerTimer += delta;
            if (lostPlayerTimer >= LOST_PLAYER_TIMEOUT) {
                state = State.PATROL;
                setNextPatrolTarget();
                return;
            }
        }

        Vector3 nextPos = navAgent.getNextPathPosition();
        Vector3 dir = nextPos.minus(getGlobalPosition()).normalized();
        setVelocity(new Vector3(dir.getX() * moveSpeed, getVelocity().getY(), dir.getZ() * moveSpeed));
        faceDirection(dir);
    }

    private void updateAttack(double delta) {
        if (player == null) {
            state = State.PATROL;
            return;
        }

        float dist = (float) getGlobalPosition().distanceTo(player.getGlobalPosition());
        if (dist > attackRange) {
            state = State.CHASE;
            return;
        }

        // Face the player and stand still
        Vector3 toPlayer = player.getGlobalPosition().minus(getGlobalPosition()).normalized();
        faceDirection(toPlayer);
        setVelocity(new Vector3(0.0f, getVelocity().getY(), 0.0f));

        if (attackTimer <= 0.0) {
            attackTimer = attackCooldown;
            if (player.hasNode(new NodePath("Health"))) {
                Health playerHealth = (Health) player.getNode(new NodePath("Health"));
                playerHealth.takeDamage(attackDamage);
            }
        }
    }

    // --- Helpers ---

    private boolean canSeePlayer() {
        if (player == null) return false;
        float dist = (float) getGlobalPosition().distanceTo(player.getGlobalPosition());
        if (dist > detectionRange) return false;

        // Cast sight ray toward player in the ray's local space
        Vector3 playerPosLocal = sightRay.toLocal(player.getGlobalPosition());
        sightRay.setTargetPosition(playerPosLocal);
        sightRay.forceRaycastUpdate();

        // Clear line-of-sight if ray hits nothing, or if it hits the player directly
        if (!sightRay.isColliding()) return true;
        return sightRay.getCollider() == player;
    }

    private void setNextPatrolTarget() {
        float angle = GD.randf() * (float) Math.PI * 2.0f;
        float dist  = GD.randf() * patrolRadius;
        Vector3 target = spawnPosition.plus(new Vector3(
            (float) Math.cos(angle) * dist,
            0.0f,
            (float) Math.sin(angle) * dist
        ));
        navAgent.setTargetPosition(target);
    }

    private void faceDirection(Vector3 dir) {
        if (Math.abs(dir.getX()) < 0.001f && Math.abs(dir.getZ()) < 0.001f) return;
        double targetY = Math.atan2(dir.getX(), dir.getZ());
        Vector3 rot = getRotation();
        setRotation(new Vector3(rot.getX(), (float) targetY, rot.getZ()));
    }

    // --- Signal receiver: connected in scene ---

    @RegisterFunction
    public void onDied() {
        isDead = true;
        queueFree();
    }
}

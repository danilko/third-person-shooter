package com.character;

import com.character.ai.EnemyAIState;
import com.character.ai.PatrolState;
import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.*;
import godot.core.Vector3;
import godot.global.GD;

@RegisterClass(className = "Enemy")
public class Enemy extends Character {

    /** Matches the SightRay and AimRay node Y-offset in the scene. */
    public static final float EYE_HEIGHT = 1.4f;

    /**
     * Y-offset from the player's CharacterBody3D origin (feet) to the upper body.
     * Used for aim targeting and LoS so the enemy shoots at the torso, not the ground.
     */
    public static final float PLAYER_BODY_HEIGHT = 0.9f;

    // ── Inspector-tunable properties ──────────────────────────────────────────
    @Export
    @RegisterProperty
    public Character player;

    @Export
    @RegisterProperty
    public float detectionRange = 120.0f;

    @Export
    @RegisterProperty
    public float aimPitchMin = -55.0f;

    @Export
    @RegisterProperty
    public float aimPitchMax = 75.0f;

    @Export
    @RegisterProperty
    public float attackRange = 150.0f;

    @Export
    @RegisterProperty
    public float patrolRadius = 80.0f;

    @Export
    @RegisterProperty
    public Area3D ammoRefill;

    // ── CS 1.6-style difficulty knobs ─────────────────────────────────────────
    /** Per-shot probability of actually hitting the player (0 = always miss, 1 = always hit). */
    @Export
    @RegisterProperty
    public float hitChance = 0.9f;

    /** Seconds from first LoS contact before the enemy starts firing. */
    @Export
    @RegisterProperty
    public float reactionTime = 0.1f;

    /**
     * Maximum aim scatter radius (world units) for a miss at 10 m.
     * Scales linearly with distance so close shots are harder to miss.
     */
    @Export
    @RegisterProperty
    public float aimScatterRadius = 1.5f;

    /** Seconds between lateral strafe direction changes while in attack stance. */
    @Export
    @RegisterProperty
    public float strafeChangeDuration = 1f;

    /**
     * Seconds the enemy keeps firing at the last known player position after losing
     * line of sight. After this window, the enemy transitions to SearchState.
     * Shorter = more conservative; longer = more aggressive suppression fire.
     */
    @Export
    @RegisterProperty
    public float suppressionDuration = 1.5f;

    // ── Constants ─────────────────────────────────────────────────────────────
    private static final float  AMMO_REFILL_ARRIVAL_THRESHOLD = 1.5f;
    private static final double LOST_PLAYER_TIMEOUT           = 3.0;
    private static final double UNDER_ATTACK_DURATION         = 2.5;

    // ── AI node refs ──────────────────────────────────────────────────────────
    private NavigationAgent3D navAgent;

    /** Dedicated LoS ray that is independent of the camera — no aim-bot coupling. */
    private RayCast3D sightRay;

    // ── AI FSM state ──────────────────────────────────────────────────────────
    private EnemyAIState currentState;
    private Vector3      spawnPosition;
    private boolean      isDead = false;

    // ── Timers / floats exposed package-privately so state singletons can access them ──
    double  attackTimer        = 0.0;
    double  lostPlayerTimer    = 0.0;
    double  reactionTimer      = 0.0;  // reset on AttackState enter; counts up until reactionTime
    double  underAttackTimer   = 0.0;  // set when hit; counts down; >0 means "recently hit"
    double  strafeTimer        = 0.0;  // counts down; repick strafe direction when <=0
    double  searchTimer        = 0.0;  // counts up in SearchState

    float   strafeX = 0f;
    float   strafeZ = 0f;

    /** World position where the player was last confirmed visible. Updated on every LoS success. */
    Vector3 lastKnownPlayerPosition = null;

    /**
     * Where the camera (and weapon AimRay) is currently trying to point.
     * Updated per-shot: exact player body on a hit, scattered position on a miss.
     */
    Vector3 currentAimTarget = null;

    // ── Lifecycle ─────────────────────────────────────────────────────────────
    @RegisterFunction
    @Override
    public void _ready() {
        useWeaponSpread = false; // accuracy managed by hitChance + aimScatterRadius
        super._ready();
        navAgent  = (NavigationAgent3D) getNode("NavigationAgent3D");
        sightRay  = (RayCast3D)         getNode("CameraRoot/Yaw/Pitch/Pivot/SpringArm/Camera/SightRay");

        for (int i = 0; i < physicalBoneSimulator.getChildCount(); i++) {
            Node child = physicalBoneSimulator.getChild(i);
            if (child instanceof PhysicalBone3D bone) {
                sightRay.addException(bone);
            }
        }
        spawnPosition = new Vector3(getGlobalPosition());
        transitionTo(PatrolState.INSTANCE);
    }

    // ── Input gathering (AI FSM → CharacterInput) ─────────────────────────────
    @Override
    protected CharacterInput gatherInput(double delta) {
        CharacterInput input = new CharacterInput();
        if (isDead) return input;

        // Passive timer: underAttackTimer counts down each frame automatically.
        if (underAttackTimer > 0) {
            underAttackTimer = Math.max(0.0, underAttackTimer - delta);
        }

        EnemyAIState next = currentState.update(this, input, delta);
        if (next != currentState) {
            transitionTo(next);
        }
        return input;
    }

    // ── State machine helpers ─────────────────────────────────────────────────

    private void transitionTo(EnemyAIState next) {
        if (currentState != null) currentState.exit(this);
        currentState = next;
        currentState.enter(this);
    }

    // ── Methods used by state objects ─────────────────────────────────────────

    public Character getPlayer()            { return player; }
    public NavigationAgent3D getNavAgent()  { return navAgent; }

    /** Returns true when the player is within detectionRange AND visible via SightRay. */
    public boolean canSeePlayer() {
        if (player == null) return false;
        float dist = (float) getGlobalPosition().distanceTo(player.getGlobalPosition());
        if (dist > detectionRange) return false;
        return hasLineOfSight();
    }

    /**
     * Pure LoS raycast using the dedicated SightRay — does NOT move the camera.
     * Decoupled from fire direction so accurate LoS never implies accurate aim.
     */
    public boolean hasLineOfSight() {
        if (player == null || sightRay == null) return false;
        Vector3 playerBodyPos = ((Node3D)player.getNode("MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/PhysicalBoneSimulator3D/Physical Bone neck_01")).getGlobalPosition();
        sightRay.setTargetPosition(sightRay.toLocal(playerBodyPos));
        sightRay.forceRaycastUpdate();
        if (!sightRay.isColliding()) return false;
      return sightRay.getCollider() instanceof Node3D && ((Node3D) sightRay.getCollider()).getOwner() == player;
    }

    /**
     * Tells the EnemyCameraController to smoothly drive Yaw/Pitch toward {@code target}
     * each frame.  CameraController's existing lerp provides the smooth tracking;
     * call {@link #snapAimRay} on the fire frame to guarantee the AimRay collision
     * is accurate regardless of how far the camera has converged.
     */
    public void aimAtPosition(Vector3 target, double delta) {
        if (!(cameraRoot instanceof EnemyCameraController cam) || target == null) return;
        cam.setAimTarget(target);
    }

    /** Clears the EnemyCameraController aim override so the camera reverts to body-facing. */
    public void clearCameraAimTarget() {
        if (cameraRoot instanceof EnemyCameraController cam) {
            cam.clearAimTarget();
        }
    }

    /**
     * Forces the AimRay to point at {@code worldTarget} and updates collision immediately.
     * Mirrors the pattern used by {@link #hasLineOfSight()} for the SightRay.
     * Call just before setting {@code input.fire = true} so WeaponController reads the
     * correct collision result on the same frame.
     */
    public void snapAimRay(Vector3 worldTarget) {
        if (aimRay == null || worldTarget == null) return;
        aimRay.setTargetPosition(aimRay.toLocal(worldTarget));
        aimRay.forceRaycastUpdate();
    }

    /**
     * Returns the world-space aim position for a single shot.
     * On a hit the aim lands on the player's upper body; on a miss it is offset by a
     * scatter radius that scales linearly with distance (close shots are harder to miss).
     */
    public Vector3 computeAimTarget(boolean isHit, float hDist) {
        Vector3 base = ((Node3D)player.getNode("MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/PhysicalBoneSimulator3D/Physical Bone neck_01")).getGlobalPosition();
        if (isHit) return base;
        float maxOffset = aimScatterRadius * (hDist / 10f);
        float offset    = GD.randf() * maxOffset;
        float angle     = GD.randf() * (float)(Math.PI * 2.0);
        return base.plus(new Vector3(
                offset * (float) Math.cos(angle),
                offset * (float) Math.sin(angle),
                0f));
    }

    /**
     * Picks a new lateral strafe direction (perpendicular to the approach toward
     * lastKnownPlayerPosition) and resets strafeTimer. Falls back to random if
     * lastKnownPlayerPosition is null.
     */
    public void refreshStrafe() {
        if (lastKnownPlayerPosition != null) {
            Vector3 toTarget = lastKnownPlayerPosition.minus(getGlobalPosition());
            double len = toTarget.length();
            if (len > 0.1) {
                float side = GD.randf() > 0.5f ? 1f : -1f;
                // 90° CCW rotation in XZ plane
                strafeX = side * (float)(toTarget.getZ() / len);
                strafeZ = side * (float)(-toTarget.getX() / len);
                strafeTimer = strafeChangeDuration;
                return;
            }
        }
        float angle = GD.randf() * (float)(Math.PI * 2.0);
        strafeX = (float) Math.cos(angle);
        strafeZ = (float) Math.sin(angle);
        strafeTimer = strafeChangeDuration;
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
                bestIndex  = i;
            }
        }
        return bestIndex;
    }

    public boolean hasAnyAmmo() { return selectBestWeapon() >= 0; }

    public boolean isAtAmmoRefill() {
        if (ammoRefill == null) return false;
        return (float) getGlobalPosition().distanceTo(ammoRefill.getGlobalPosition())
                <= AMMO_REFILL_ARRIVAL_THRESHOLD;
    }

    /** Pick a new random patrol destination within patrolRadius of spawn. */
    public void setNextPatrolTarget() {
        float angle = GD.randf() * (float) Math.PI * 2.0f;
        float dist  = GD.randf() * patrolRadius;
        navAgent.setTargetPosition(spawnPosition.plus(new Vector3(
                (float) Math.cos(angle) * dist, 0.0f, (float) Math.sin(angle) * dist)));
    }

    // ── Attack-timer helpers ──────────────────────────────────────────────────

    public void resetAttackTimer()               { attackTimer = 0.0; }
    public void resetAttackTimer(double value)   { attackTimer = value; }
    public void advanceAttackTimer(double delta) { attackTimer = Math.max(0.0, attackTimer + delta); }
    public boolean isAttackReady()               { return attackTimer <= 0.0; }

    // ── Lost-player / suppression timer helpers ──────────────────────────────

    public void resetLostPlayerTimer()               { lostPlayerTimer = 0.0; }
    public void advanceLostPlayerTimer(double delta) { lostPlayerTimer += delta; }
    public boolean isPlayerLost()                    { return lostPlayerTimer >= LOST_PLAYER_TIMEOUT; }
    /** True once the suppression-fire window has elapsed after losing LoS. */
    public boolean isSuppressExpired()               { return lostPlayerTimer >= suppressionDuration; }

    /**
     * Aim position for a suppression shot when the enemy has no line of sight.
     * Scatters around {@link #lastKnownPlayerPosition} with twice the normal radius
     * to model firing blind through/around cover.
     */
    public Vector3 computeSuppressTarget(float hDist) {
        if (lastKnownPlayerPosition == null) return null;
        float maxOffset = aimScatterRadius * 2f * (hDist / 10f);
        float offset    = GD.randf() * maxOffset;
        float angle     = GD.randf() * (float)(Math.PI * 2.0);
        return lastKnownPlayerPosition.plus(new Vector3(
                offset * (float) Math.cos(angle),
                offset * (float) Math.sin(angle),
                0f));
    }

    // ── Under-attack helpers ──────────────────────────────────────────────────

    /** True for UNDER_ATTACK_DURATION seconds after the enemy last took damage. */
    public boolean isUnderAttack() { return underAttackTimer > 0.0; }

    // ── Strafe helpers ────────────────────────────────────────────────────────

    /** True when strafeTimer has elapsed and a new lateral direction should be picked. */
    public boolean needsStrafeUpdate()               { return strafeTimer <= 0.0; }
    public void    tickStrafeTimer(double delta)     { if (strafeTimer > 0) strafeTimer -= delta; }
    public float   getStrafeX()                      { return strafeX; }
    public float   getStrafeZ()                      { return strafeZ; }

    // ── Reaction-timer helpers ────────────────────────────────────────────────

    public void    advanceReactionTimer(double delta){ reactionTimer += delta; }
    public boolean isReactionReady()                 { return reactionTimer >= reactionTime; }
    public void    resetReactionTimer()              { reactionTimer = 0.0; }

    // ── Search-timer helpers ──────────────────────────────────────────────────

    public void    resetSearchTimer()                        { searchTimer = 0.0; }
    public void    advanceSearchTimer(double delta)          { searchTimer += delta; }
    public boolean isSearchTimedOut(double timeout)          { return searchTimer >= timeout; }

    // ── Last-known-position helpers ───────────────────────────────────────────

    public Vector3 getLastKnownPlayerPosition()              { return lastKnownPlayerPosition; }
    public void    setLastKnownPlayerPosition(Vector3 pos)   { lastKnownPlayerPosition = pos; }
    public boolean hasLastKnownPosition()                    { return lastKnownPlayerPosition != null; }

    // ── Current aim-target helpers ────────────────────────────────────────────

    public Vector3 getCurrentAimTarget()                     { return currentAimTarget; }
    public void    setCurrentAimTarget(Vector3 target)       { currentAimTarget = target; }

    // ── Signal receivers ──────────────────────────────────────────────────────

    /** Called by the Health node's `damaged` signal (connected in Enemy.tscn). */
    @RegisterFunction
    public void onEnemyDamaged(float amount) {
        underAttackTimer = UNDER_ATTACK_DURATION;
        // Record where the player probably is so SearchState has a destination.
        if (player != null && lastKnownPlayerPosition == null) {
            lastKnownPlayerPosition = new Vector3(player.getGlobalPosition());
        }
    }

    @RegisterFunction
    @Override
    public void onDied() {
        isDead = true;
        super.onDied(); // stops physics processing and activates ragdoll
    }
}

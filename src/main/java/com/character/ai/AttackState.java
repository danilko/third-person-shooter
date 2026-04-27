package com.character.ai;

import com.character.CharacterInput;
import com.character.Enemy;
import com.character.MovementType;
import godot.api.Node3D;
import godot.core.Vector3;
import godot.global.GD;

/**
 * CS 1.6-style attack: enemy strafes laterally, waits for a reaction delay,
 * then fires with per-shot hit/miss accuracy controlled by {@link Enemy#hitChance}.
 *
 * When line of sight is lost the enemy does not immediately retreat — instead it
 * fires suppression shots at the last known player position for up to
 * {@link Enemy#suppressionDuration} seconds before transitioning to SearchState.
 * This makes enemies feel aggressive at close range (tight scatter) and naturally
 * more conservative at long range (wide scatter, hard to hit through cover).
 */
public class AttackState implements EnemyAIState {

    public static final AttackState INSTANCE = new AttackState();

    private AttackState() {}

    @Override
    public void enter(Enemy enemy) {
        enemy.resetAttackTimer();
        enemy.resetReactionTimer();
        enemy.resetLostPlayerTimer();
        enemy.setCurrentAimTarget(null);
    }

    @Override
    public void exit(Enemy enemy) {
        enemy.clearCameraAimTarget();
    }

    @Override
    public EnemyAIState update(Enemy enemy, CharacterInput input, double delta) {
        if (enemy.getPlayer() == null) return PatrolState.INSTANCE;

        Vector3 playerPos = enemy.getPlayer().getGlobalPosition();
        Vector3 enemyPos  = enemy.getGlobalPosition();
        float dx    = (float)(playerPos.getX() - enemyPos.getX());
        float dz    = (float)(playerPos.getZ() - enemyPos.getZ());
        float hDist = (float) Math.sqrt(dx * dx + dz * dz);
        float dist  = (float) enemyPos.distanceTo(playerPos);

        if (dist > enemy.attackRange) return ChaseState.INSTANCE;

        input.wantCombat = true;
        enemy.advanceReactionTimer(delta);

        // ── LoS tracking: clear shot resets timer; lost shot advances it ─────
        boolean hasLoS = enemy.hasLineOfSight();
        if (hasLoS) {
            enemy.setLastKnownPlayerPosition(new Vector3(playerPos));
            enemy.resetLostPlayerTimer();
        } else {
            enemy.advanceLostPlayerTimer(delta);
            if (!enemy.hasLastKnownPosition() || enemy.isSuppressExpired()) {
                return SearchState.INSTANCE;
            }
        }

        // ── Movement: retreat on extreme pitch, strafe otherwise ─────────────
        Vector3 eyePos = enemyPos.plus(new Vector3(0, Enemy.EYE_HEIGHT, 0));
        float targetY  = (float)playerPos.getY() + Enemy.PLAYER_BODY_HEIGHT;
        float dy       = targetY - (float)eyePos.getY();
        float pitchDeg = (hDist > 0.01f) ? (float) Math.toDegrees(Math.atan2(dy, hDist)) : 0f;
        boolean pitchOutOfRange = pitchDeg > enemy.aimPitchMax || pitchDeg < enemy.aimPitchMin;

        if (pitchOutOfRange && hDist > 0.01f) {
            input.movementDirection.setX(-dx / hDist);
            input.movementDirection.setZ(-dz / hDist);
            input.movementType = MovementType.WALK;
        } else {
            if (enemy.needsStrafeUpdate()) {
                enemy.refreshStrafe();
            }
            enemy.tickStrafeTimer(delta);
            input.movementDirection.setX(enemy.getStrafeX());
            input.movementDirection.setZ(enemy.getStrafeZ());
            input.movementType = MovementType.WALK;
        }

        // ── Aim camera toward current target each frame ───────────────────────
        if (enemy.getCurrentAimTarget() == null) {
            // Initialise aim toward the player (or last known position if no LoS yet)
            Vector3 initialTarget = hasLoS
                    ? ((Node3D) enemy.getPlayer().getNode("MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/PhysicalBoneSimulator3D/Physical Bone neck_01")).getGlobalPosition()
                    : enemy.getLastKnownPlayerPosition();
            enemy.setCurrentAimTarget(initialTarget);
        }
        enemy.aimAtPosition(enemy.getCurrentAimTarget(), delta);
        input.aimTargetPosition = enemy.getCurrentAimTarget();

        // ── Weapon management: always prefer highest-damage weapon with ammo ──
        int bestWeapon = enemy.selectBestWeapon();
        if (bestWeapon < 0) return RefillAmmoState.INSTANCE;
        if (enemy.weaponController != null && bestWeapon != enemy.weaponController.getWeapon()) {
            if (!enemy.weaponController.isWeaponTransitioning()) {
                input.desiredWeapon = bestWeapon;
            }
            return this;
        }

        // ── Reaction delay: don't fire immediately on first LoS contact ───────
        if (!enemy.isReactionReady()) {
            return this;
        }

        // ── Fire on cooldown ─────────────────────────────────────────────────
        enemy.advanceAttackTimer(-delta);
        if (enemy.isAttackReady()) {
            double fireRate = (enemy.weaponController != null
                    && enemy.weaponController.getCurrentWeaponStats() != null)
                    ? enemy.weaponController.getCurrentWeaponStats().getFireRate()
                    : 0.0;
            enemy.resetAttackTimer(fireRate > 0.0 ? 1.0 / fireRate : 1.5);

            Vector3 newTarget;
            if (hasLoS) {
                // Clear shot: normal hit/miss accuracy roll
                boolean isHit = GD.randf() < enemy.hitChance;
                newTarget = enemy.computeAimTarget(isHit, hDist);
            } else {
                // No LoS: suppression fire at last known position with extra scatter
                newTarget = enemy.computeSuppressTarget(hDist);
                if (newTarget == null) return SearchState.INSTANCE;
            }

            enemy.setCurrentAimTarget(newTarget);
            enemy.aimAtPosition(newTarget, delta);
            enemy.snapAimRay(newTarget);
            input.aimTargetPosition = newTarget;
            input.fire = true;
        }

        return this;
    }
}

package com.character.ai;

import com.character.CharacterInput;
import com.character.Enemy;
import com.character.MovementType;
import godot.core.Vector3;
import godot.global.GD;

/**
 * CS 1.6-style attack: enemy strafes laterally, waits for a reaction delay,
 * then fires with per-shot hit/miss accuracy controlled by {@link Enemy#hitChance}.
 * Falls back to {@link SearchState} on LoS loss, {@link ChaseState} if out of range.
 */
public class AttackState implements EnemyAIState {

    public static final AttackState INSTANCE = new AttackState();

    private AttackState() {}

    @Override
    public void enter(Enemy enemy) {
        enemy.resetAttackTimer();
        enemy.resetReactionTimer();
        enemy.setCurrentAimTarget(null);
    }

    @Override
    public void exit(Enemy enemy) {}

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

        // ── Movement: retreat on extreme pitch, strafe otherwise ─────────────
        Vector3 eyePos = enemyPos.plus(new Vector3(0, Enemy.EYE_HEIGHT, 0));
        float targetY  = (float)playerPos.getY() + Enemy.PLAYER_BODY_HEIGHT;
        float dy       = targetY - (float)eyePos.getY();
        float pitchDeg = (hDist > 0.01f) ? (float) Math.toDegrees(Math.atan2(dy, hDist)) : 0f;
        boolean pitchOutOfRange = pitchDeg > enemy.aimPitchMax || pitchDeg < enemy.aimPitchMin;

        if (pitchOutOfRange && hDist > 0.01f) {
            // Back away from the player to reach a shootable vertical angle
            input.movementDirection.setX(-dx / hDist);
            input.movementDirection.setZ(-dz / hDist);
            input.movementType = MovementType.WALK;
        } else {
            // CS 1.6-style: strafe left/right and change direction periodically
            if (enemy.needsStrafeUpdate()) {
                enemy.refreshStrafe();
            }
            enemy.tickStrafeTimer(delta);
            input.movementDirection.setX(enemy.getStrafeX());
            input.movementDirection.setZ(enemy.getStrafeZ());
            input.movementType = MovementType.WALK;
        }

        // ── Aim camera toward current target (smooth tracking) ───────────────
        if (enemy.getCurrentAimTarget() == null) {
            // Before first shot decision, track the exact player position
            enemy.setCurrentAimTarget(playerPos.plus(new Vector3(0, Enemy.PLAYER_BODY_HEIGHT, 0)));
        }
        enemy.aimAtPosition(enemy.getCurrentAimTarget(), delta);
        input.aimTargetPosition = enemy.getCurrentAimTarget();

        // ── LoS check: search aggressively if player ducks out of sight ──────
        if (!enemy.hasLineOfSight()) {
            return SearchState.INSTANCE;
        }
        enemy.setLastKnownPlayerPosition(new Vector3(playerPos));

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

        // ── Fire on cooldown with hit/miss accuracy ───────────────────────────
        enemy.advanceAttackTimer(-delta);
        if (enemy.isAttackReady()) {
            double fireRate = (enemy.weaponController != null
                    && enemy.weaponController.getCurrentWeaponStats() != null)
                    ? enemy.weaponController.getCurrentWeaponStats().getFireRate()
                    : 0.0;
            enemy.resetAttackTimer(fireRate > 0.0 ? 1.0 / fireRate : 1.5);

            boolean isHit = GD.randf() < enemy.hitChance;
            Vector3 newTarget = enemy.computeAimTarget(isHit, hDist);
            enemy.setCurrentAimTarget(newTarget);
            enemy.aimAtPosition(newTarget, delta);
            input.aimTargetPosition = newTarget;
            input.fire = true;
        }

        return this;
    }
}

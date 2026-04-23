package com.character.ai;

import com.character.CharacterInput;
import com.character.Enemy;
import com.character.MovementType;
import godot.core.Vector3;

/**
 * Enemy stands still, faces the player, and fires on cooldown.
 * Falls back to {@link ChaseState} if the player moves out of attack range.
 */
public class AttackState implements EnemyAIState {

    public static final AttackState INSTANCE = new AttackState();

    private AttackState() {}

    @Override
    public void enter(Enemy enemy) {
        enemy.resetAttackTimer();
    }

    @Override
    public void exit(Enemy enemy) {}

    @Override
    public EnemyAIState update(Enemy enemy, CharacterInput input, double delta) {
        if (enemy.getPlayer() == null) {
            return PatrolState.INSTANCE;
        }

        float dist = (float) enemy.getGlobalPosition()
                                  .distanceTo(enemy.getPlayer().getGlobalPosition());
        if (dist > enemy.attackRange) {
            return ChaseState.INSTANCE;
        }

        // Compute horizontal vector to player
        Vector3 playerPos = enemy.getPlayer().getGlobalPosition();
        Vector3 enemyPos  = enemy.getGlobalPosition();
        float dx = (float) (playerPos.getX() - enemyPos.getX());
        float dz = (float) (playerPos.getZ() - enemyPos.getZ());
        float hDist = (float) Math.sqrt(dx * dx + dz * dz);

        // Always face the player horizontally
        if (hDist > 0.01f) {
            input.movementDirection.setX(dx / hDist);
            input.movementDirection.setZ(dz / hDist);
        }

        // Compute vertical pitch from eye to player's upper body.
        // Using the feet position (getGlobalPosition().Y) makes the spine aim at
        // the ground and shots pass below the torso — offset to body center.
        Vector3 eyePos = enemyPos.plus(new Vector3(0, Enemy.EYE_HEIGHT, 0));
        float targetY   = (float) playerPos.getY() + Enemy.PLAYER_BODY_HEIGHT;
        float dy = targetY - (float) eyePos.getY();
        float pitchDeg = (hDist > 0.01f)
                ? (float) Math.toDegrees(Math.atan2(dy, hDist))
                : 0f;

        boolean pitchOutOfRange = pitchDeg > enemy.aimPitchMax || pitchDeg < enemy.aimPitchMin;
        float clampedPitch = Math.max(enemy.aimPitchMin, Math.min(enemy.aimPitchMax, pitchDeg));

        // Clamp aim target Y so the spine modifier stays within achievable angles
        float clampedY = (float) (eyePos.getY() + hDist * (float) Math.tan(Math.toRadians(clampedPitch)));
        input.aimTargetPosition = new Vector3(playerPos.getX(), clampedY, playerPos.getZ());
        input.wantCombat = true;

        if (pitchOutOfRange && hDist > 0.01f) {
            // Retreat away from the player to reduce the extreme vertical angle.
            // The spine LookAtModifier keeps the upper body aimed at the clamped target
            // while the body backs away to bring the player into the valid pitch range.
            input.movementDirection.setX(-dx / hDist);
            input.movementDirection.setZ(-dz / hDist);
            input.movementType = MovementType.WALK;
        } else {
            input.movementType = MovementType.IDLE;
        }

        // Require line-of-sight before firing; fall back to chase if occluded
        if (!enemy.hasLineOfSight()) {
            return ChaseState.INSTANCE;
        }

        // Weapon management: prefer weapon 2, fall back to weapon 1, seek ammo if all dry
        int bestWeapon = enemy.selectBestWeapon();
        if (bestWeapon < 0) {
            return RefillAmmoState.INSTANCE;
        }
        if (enemy.weaponController != null && bestWeapon != enemy.weaponController.getWeapon()) {
            // Only send the switch request once — resending every frame restarts
            // transitionTimer before it fires, locking the enemy in an infinite loop.
            if (!enemy.weaponController.isWeaponTransitioning()) {
                input.desiredWeapon = bestWeapon;
            }
            return this;
        }

        // Fire on cooldown (driven by attackTimer on Enemy)
        enemy.advanceAttackTimer(-delta);
        if (enemy.isAttackReady()) {
            double fireRate = (enemy.weaponController != null
                    && enemy.weaponController.getCurrentWeaponStats() != null)
                    ? enemy.weaponController.getCurrentWeaponStats().getFireRate()
                    : 0.0;
            enemy.resetAttackTimer(fireRate > 0.0 ? 1.0 / fireRate : 1.5);
            input.fire = true;
        }

        return this;
    }
}

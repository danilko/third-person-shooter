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

        // Face and aim at the player while standing still
        Vector3 toPlayer = enemy.getPlayer().getGlobalPosition()
                                .minus(enemy.getGlobalPosition())
                                .normalized();
        input.movementDirection.setX(toPlayer.getX());
        input.movementDirection.setZ(toPlayer.getZ());
        input.movementType = MovementType.IDLE;
        input.wantCombat   = true;
        Vector3 pp = enemy.getPlayer().getGlobalPosition();
        input.aimTargetPosition = new Vector3(pp.getX(), pp.getY(), pp.getZ());

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

package com.character.ai;

import com.character.CharacterInput;
import com.character.Enemy;
import com.character.MovementType;
import godot.core.Vector3;

/**
 * Enemy sprints toward the player and enters attack range.
 * Navigates toward {@link Enemy#lastKnownPlayerPosition} even when LoS is broken.
 * Falls back to {@link PatrolState} after losing the player for too long.
 */
public class ChaseState implements EnemyAIState {

    public static final ChaseState INSTANCE = new ChaseState();

    private ChaseState() {}

    @Override
    public void enter(Enemy enemy) {
        enemy.resetLostPlayerTimer();
    }

    @Override
    public void exit(Enemy enemy) {}

    @Override
    public EnemyAIState update(Enemy enemy, CharacterInput input, double delta) {
        if (enemy.getPlayer() == null) return PatrolState.INSTANCE;

        float dist = (float) enemy.getGlobalPosition()
                                  .distanceTo(enemy.getPlayer().getGlobalPosition());

        // Always in combat while chasing — set before any early return so the
        // combat animation and LookAtModifier activate on the same frame.
        input.wantCombat = true;

        if (dist <= enemy.attackRange && enemy.hasLineOfSight()) {
            if (!enemy.hasAnyAmmo()) return RefillAmmoState.INSTANCE;
            return AttackState.INSTANCE;
        }

        input.movementType = MovementType.SPRINT;

        if (enemy.hasLineOfSight()) {
            enemy.resetLostPlayerTimer();
            Vector3 playerPos = enemy.getPlayer().getGlobalPosition();
            enemy.setLastKnownPlayerPosition(new Vector3(playerPos));
            enemy.getNavAgent().setTargetPosition(playerPos);

            // Aim camera at player while chasing
            Vector3 aimTarget = new Vector3(playerPos.getX(),
                    playerPos.getY() + Enemy.PLAYER_BODY_HEIGHT,
                    playerPos.getZ());
            enemy.aimAtPosition(aimTarget, delta);
            input.aimTargetPosition = aimTarget;
        } else {
            enemy.advanceLostPlayerTimer(delta);
            if (enemy.isPlayerLost()) {
                return PatrolState.INSTANCE;
            }
            // LoS broken but not yet timed out — keep navigating toward last known position
            if (enemy.hasLastKnownPosition()) {
                enemy.getNavAgent().setTargetPosition(enemy.getLastKnownPlayerPosition());
            }
        }

        Vector3 dir = enemy.getNavAgent()
                           .getNextPathPosition()
                           .minus(enemy.getGlobalPosition())
                           .normalized();
        input.movementDirection.setX(dir.getX());
        input.movementDirection.setZ(dir.getZ());

        return this;
    }
}

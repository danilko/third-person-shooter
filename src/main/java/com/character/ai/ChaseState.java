package com.character.ai;

import com.character.CharacterInput;
import com.character.Enemy;
import com.character.MovementType;
import godot.core.Vector3;

/**
 * Enemy sprints toward the player and enters attack range.
 * Falls back to {@link PatrolState} if it loses sight for too long.
 * Advances to {@link AttackState} when close enough.
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
        if (enemy.getPlayer() == null) {
            return PatrolState.INSTANCE;
        }

        float dist = (float) enemy.getGlobalPosition()
                                  .distanceTo(enemy.getPlayer().getGlobalPosition());
        if (dist <= enemy.attackRange) {
            return AttackState.INSTANCE;
        }

        input.wantCombat   = true;
        input.movementType = MovementType.SPRINT;

        if (enemy.canSeePlayer()) {
            enemy.resetLostPlayerTimer();
            enemy.getNavAgent().setTargetPosition(enemy.getPlayer().getGlobalPosition());
            Vector3 pp = enemy.getPlayer().getGlobalPosition();
            input.aimTargetPosition = new Vector3(pp.getX(), pp.getY(), pp.getZ());
        } else {
            enemy.advanceLostPlayerTimer(delta);
            if (enemy.isPlayerLost()) {
                return PatrolState.INSTANCE;
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

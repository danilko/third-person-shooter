package com.character.ai;

import com.character.CharacterInput;
import com.character.Enemy;
import com.character.MovementType;
import godot.core.Vector3;

/**
 * Enemy wanders within its patrol radius.
 * Transitions to {@link ChaseState} when the player enters detection range and LoS.
 */
public class PatrolState implements EnemyAIState {

    public static final PatrolState INSTANCE = new PatrolState();

    private PatrolState() {}

    @Override
    public void enter(Enemy enemy) {
        enemy.setNextPatrolTarget();
    }

    @Override
    public void exit(Enemy enemy) {}

    @Override
    public EnemyAIState update(Enemy enemy, CharacterInput input, double delta) {
        if (enemy.canSeePlayer()) {
            return ChaseState.INSTANCE;
        }

        input.wantCombat   = false;
        input.movementType = MovementType.WALK;

        if (!enemy.getNavAgent().isNavigationFinished()) {
            Vector3 dir = enemy.getNavAgent()
                               .getNextPathPosition()
                               .minus(enemy.getGlobalPosition())
                               .normalized();
            input.movementDirection.setX(dir.getX());
            input.movementDirection.setZ(dir.getZ());
        } else {
            enemy.setNextPatrolTarget();
        }

        return this;
    }
}

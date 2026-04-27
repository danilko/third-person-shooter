package com.character.ai;

import com.character.CharacterInput;
import com.character.Enemy;
import com.character.MovementType;
import godot.core.Vector3;

/**
 * Enemy sprints to the player's last known position and searches the area.
 * Triggered when LoS is lost during combat or when the enemy is hit while patrolling.
 * Re-engages immediately if the player is spotted; gives up after {@link #SEARCH_TIMEOUT}.
 */
public class SearchState implements EnemyAIState {

    public static final SearchState INSTANCE = new SearchState();

    private SearchState() {}

    private static final double SEARCH_TIMEOUT = 5.0;

    @Override
    public void enter(Enemy enemy) {
        enemy.resetSearchTimer();
        if (enemy.hasLastKnownPosition() && enemy.getNavAgent() != null) {
            enemy.getNavAgent().setTargetPosition(enemy.getLastKnownPlayerPosition());
        }
    }

    @Override
    public void exit(Enemy enemy) {}

    @Override
    public EnemyAIState update(Enemy enemy, CharacterInput input, double delta) {
        // Re-engage the moment the player is visible again
        if (enemy.canSeePlayer()) {
            float dist = (float) enemy.getGlobalPosition()
                                      .distanceTo(enemy.getPlayer().getGlobalPosition());
            if (dist <= enemy.attackRange && enemy.hasAnyAmmo()) {
                return AttackState.INSTANCE;
            }
            return ChaseState.INSTANCE;
        }

        enemy.advanceSearchTimer(delta);
        if (enemy.isSearchTimedOut(SEARCH_TIMEOUT)) {
            return PatrolState.INSTANCE;
        }

        input.wantCombat = true;

        boolean arrivedAtLastKnown = !enemy.hasLastKnownPosition()
                || enemy.getNavAgent().isNavigationFinished();

        if (arrivedAtLastKnown) {
            // At last known position — strafe to peek around cover
            if (enemy.needsStrafeUpdate()) {
                enemy.refreshStrafe();
            }
            enemy.tickStrafeTimer(delta);
            input.movementDirection.setX(enemy.getStrafeX());
            input.movementDirection.setZ(enemy.getStrafeZ());
            input.movementType = MovementType.WALK;

            // Look toward where the player was last seen
            if (enemy.hasLastKnownPosition()) {
                Vector3 lookTarget = enemy.getLastKnownPlayerPosition()
                        .plus(new Vector3(0, Enemy.PLAYER_BODY_HEIGHT, 0));
                enemy.aimAtPosition(lookTarget, delta);
                input.aimTargetPosition = lookTarget;
            }
        } else {
            // Still navigating to last known position
            input.movementType = MovementType.SPRINT;
            Vector3 dir = enemy.getNavAgent()
                               .getNextPathPosition()
                               .minus(enemy.getGlobalPosition())
                               .normalized();
            input.movementDirection.setX(dir.getX());
            input.movementDirection.setZ(dir.getZ());
        }

        return this;
    }
}

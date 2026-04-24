package com.character.ai;

import com.character.CharacterInput;
import com.character.Enemy;
import com.character.MovementType;
import godot.core.Vector3;

/**
 * Enemy wanders within its patrol radius.
 * Transitions to {@link ChaseState} or {@link AttackState} when the player is spotted.
 * Transitions to {@link SearchState} immediately when hit (even without visual contact).
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
        // Keep best-damage weapon equipped while idle — guard against restarting
        // the transitionTimer every frame, which would prevent the switch from completing.
        int bestWeapon = enemy.selectBestWeapon();
        if (bestWeapon >= 0 && enemy.weaponController != null
                && bestWeapon != enemy.weaponController.getWeapon()
                && !enemy.weaponController.isWeaponTransitioning()) {
            input.desiredWeapon = bestWeapon;
        }

        if (enemy.canSeePlayer()) {
            // Prime combat mode this frame so animation and LookAtModifier start immediately.
            input.wantCombat = true;
            float dist = (float) enemy.getGlobalPosition()
                                      .distanceTo(enemy.getPlayer().getGlobalPosition());
            if (dist <= enemy.attackRange && enemy.hasAnyAmmo()) {
                return AttackState.INSTANCE;
            }
            return ChaseState.INSTANCE;
        }

        // React to being hit even without visual contact — search toward attacker
        if (enemy.isUnderAttack() && enemy.hasLastKnownPosition()) {
            return SearchState.INSTANCE;
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

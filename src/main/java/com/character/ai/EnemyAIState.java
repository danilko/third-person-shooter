package com.character.ai;

import com.character.CharacterInput;
import com.character.Enemy;

/**
 * One state in the enemy AI finite state machine.
 *
 * Each concrete state is responsible for:
 *  - Deciding whether to transition to another state
 *  - Filling a CharacterInput for this tick
 *
 * States are stateless value objects; all mutable data lives on Enemy.
 * Returning a different EnemyAIState from update() triggers a transition.
 */
public interface EnemyAIState {

    /**
     * Called once when entering this state.
     * Use to reset per-state timers stored on the enemy.
     */
    void enter(Enemy enemy);

    /**
     * Called once when leaving this state.
     */
    void exit(Enemy enemy);

    /**
     * Produce a CharacterInput for this tick.
     * Return {@code this} to stay in the current state, or a new state to transition.
     */
    EnemyAIState update(Enemy enemy, CharacterInput input, double delta);
}

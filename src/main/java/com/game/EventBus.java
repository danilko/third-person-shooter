package com.game;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterSignal;
import godot.api.Node;
import godot.core.Signal0;
import godot.core.Signal1;

/**
 * Global event bus — registered as an AutoLoad singleton named "EventBus".
 *
 * Any node in the game can reach it via:
 *   EventBus bus = (EventBus) getNode("/root/EventBus");
 *
 * Emit side:  bus.enemyKilled.emit();
 * Listen side: bus.enemyKilled.connect(this::onEnemyKilled);
 *
 * AutoLoad entry (add to project.godot after running ./gradlew build):
 *   [autoload]
 *   EventBus="*res://gdj/com/game/EventBus.gdj"
 */
@RegisterClass(className = "EventBus")
public class EventBus extends Node {

    /** Emitted by Player.onDied(). Payload: none — the player is a singleton. */
    @RegisterSignal
    public final Signal0 playerDied = Signal0.create(this, "playerDied");

    /** Emitted by Enemy.onDied(). Payload: the enemy's score value. */
    @RegisterSignal
    public final Signal1<Integer> enemyKilled = Signal1.create(this, "enemyKilled");

    /** Emitted by Health.takeDamage() for the player character. Payload: new currentHealth. */
    @RegisterSignal
    public final Signal1<Float> playerHealthChanged = Signal1.create(this, "playerHealthChanged");

    /** Emitted by AmmoRefill.onBodyEntered(). Payload: weapon index that was refilled. */
    @RegisterSignal
    public final Signal1<Integer> ammoPickedUp = Signal1.create(this, "ammoPickedUp");
}

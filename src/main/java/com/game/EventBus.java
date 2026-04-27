package com.game;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterSignal;
import godot.api.Node;
import godot.core.Signal0;
import godot.core.Signal1;
import godot.core.Signal4;
import godot.core.StringName;

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
    public final Signal0 playerDied = new Signal0(this, new StringName("player_died"));

    /** Emitted by Enemy.onDied(). Payload: the enemy's score value. */
    @RegisterSignal
    public final Signal1<Integer> enemyKilled = new Signal1<>(this, new StringName("enemy_killed"));

    /** Emitted by Health.takeDamage() for the player character. Payload: new currentHealth. */
    @RegisterSignal
    public final Signal1<Float> playerHealthChanged = new Signal1<>(this, new StringName("player_health_changed"));

    /** Emitted by AmmoRefill.onBodyEntered(). Payload: weapon index that was refilled. */
    @RegisterSignal
    public final Signal1<Integer> ammoPickedUp = new Signal1<>(this, new StringName("ammo_picked_up"));

    /**
     * Emitted by Health when any character is eliminated.
     * Payload: attackerName, victimName, weaponName, headshot.
     */
    @RegisterSignal
    public final Signal4<String, String, String, Boolean> characterEliminated =
            new Signal4<>(this, new StringName("character_eliminated"));
}

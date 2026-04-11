package com.game;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.api.Node;
import godot.core.Callable;
import godot.core.StringNames;
import godot.global.GD;

/**
 * Central game state machine — registered as an AutoLoad singleton named "GameManager".
 *
 * Responsibilities:
 *  - Track current GameState (PLAYING, PAUSED, GAME_OVER)
 *  - Respond to player death (show game-over screen, restart, quit)
 *  - Provide a single entry point for scene transitions
 *
 * AutoLoad entry (add to project.godot after running ./gradlew build):
 *   [autoload]
 *   GameManager="*res://gdj/com/game/GameManager.gdj"
 *
 * Wire EventBus.playerDied → GameManager.onPlayerDied() in the scene or in _ready().
 */
@RegisterClass(className = "GameManager")
public class GameManager extends Node {

    public enum GameState {
        PLAYING,
        PAUSED,
        GAME_OVER
    }

    private GameState currentState = GameState.PLAYING;

    @RegisterFunction
    @Override
    public void _ready() {
        // Connect to EventBus once it is available as a sibling AutoLoad.
        // AutoLoads are added in order, so EventBus must be listed first in project.godot.
        Node eventBusNode = getNodeOrNull("/root/EventBus");
        if (eventBusNode instanceof EventBus) {
            EventBus bus = (EventBus) eventBusNode;
            bus.playerDied.connect(Callable.create(this, StringNames.toGodotName("onPlayerDied")), 1);
        }
    }

    // ── State transitions ─────────────────────────────────────────────────────

    @RegisterFunction
    public void onPlayerDied() {
        if (currentState != GameState.PLAYING) return;
        transitionTo(GameState.GAME_OVER);
        // TODO: show game-over UI (get game-over screen node and call show())
        GD.print("GameManager: player died — game over");
    }

    public void pauseGame() {
        if (currentState != GameState.PLAYING) return;
        transitionTo(GameState.PAUSED);
        if (getTree() != null) getTree().setPause(true);
    }

    public void resumeGame() {
        if (currentState != GameState.PAUSED) return;
        transitionTo(GameState.PLAYING);
        if (getTree() != null) getTree().setPause(false);
    }

    public void restartLevel() {
        transitionTo(GameState.PLAYING);
        if (getTree() != null) getTree().reloadCurrentScene();
    }

    public void loadLevel(String scenePath) {
        transitionTo(GameState.PLAYING);
        if (getTree() != null) getTree().changeSceneToFile(scenePath);
    }

    // ── Getters ───────────────────────────────────────────────────────────────

    public GameState getCurrentState() {
        return currentState;
    }

    public boolean isPlaying() {
        return currentState == GameState.PLAYING;
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private void transitionTo(GameState next) {
        GD.print("GameManager: " + currentState + " → " + next);
        currentState = next;
    }
}

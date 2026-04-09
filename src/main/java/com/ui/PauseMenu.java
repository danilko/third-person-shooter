package com.ui;

import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.api.Control;
import godot.api.Input;
import godot.api.InputEvent;

@RegisterClass(className = "PauseMenu")
public class PauseMenu extends Control {

    private boolean paused = false;

    @RegisterFunction
    @Override
    public void _ready() {
        // PROCESS_MODE_ALWAYS (3) is set in the scene so this node keeps
        // responding to input even while the SceneTree is paused.
        hide();
    }

    @RegisterFunction
    @Override
    public void _input(InputEvent event) {
        if (event.isActionPressed("ui_cancel")) {
            if (paused) {
                resume();
            } else {
                pause();
            }
            getViewport().setInputAsHandled();
        }
    }

    @RegisterFunction
    public void onResumePressed() {
        resume();
    }

    @RegisterFunction
    public void onQuitPressed() {
        getTree().quit();
    }

    private void pause() {
        paused = true;
        getTree().setPause(true);
        Input.setMouseMode(Input.MouseMode.VISIBLE);
        show();
    }

    private void resume() {
        paused = false;
        getTree().setPause(false);
        Input.setMouseMode(Input.MouseMode.CAPTURED);
        hide();
    }
}

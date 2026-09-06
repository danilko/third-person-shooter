package com.openworld.debug;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node;
import godot.api.PackedScene;
import godot.api.Time;
import godot.global.GD;

/**
 * One-shot throwaway host (same pattern as {@code WorldBaker}/{@code NavBaker}): loads
 * {@code scenePath}, times a single {@code PackedScene.instantiate()} + {@code addChild()} — the
 * exact pair {@code ZoneManager.load()} calls on a real zone stream-in, including the
 * physics-server registration every {@code StaticBody3D}/{@code CollisionShape3D} does on
 * {@code _enter_tree} — then prints the elapsed ms and quits. Run once per process (not looped
 * in-process) so a queued {@code queueFree()} from a prior iteration can never contaminate the
 * next measurement; average across runs externally instead.
 */
@Script(className = "LoadBench")
public class LoadBench extends Node {

    @Export public String scenePath = "";
    @Export public boolean quitWhenDone = true;

    @Register
    @Override
    public void _ready() {
        Object loaded = GD.load(scenePath);
        if (!(loaded instanceof PackedScene ps)) {
            GD.printErr("LoadBench: not a PackedScene: " + scenePath);
            if (quitWhenDone && getTree() != null) getTree().quit();
            return;
        }

        long t0 = Time.INSTANCE.getTicksUsec();
        Node inst = ps.instantiate();
        addChild(inst);
        long t1 = Time.INSTANCE.getTicksUsec();

        GD.print("LoadBench: '" + scenePath + "' instantiate+addChild = "
                + String.format("%.2f", (t1 - t0) / 1000.0) + " ms");

        if (quitWhenDone && getTree() != null) getTree().quit();
    }
}

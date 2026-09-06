package com.openworld.world;

import godot.api.Node;
import godot.api.PackedScene;
import godot.global.GD;
import com.openworld.character.AICharacter;

import java.util.ArrayDeque;
import java.util.Deque;

/**
 * Recycles {@link AICharacter} bodies for {@link ZoneManager} (PLAN.md Part E / E1).
 *
 * <p>Plain (non-Godot-registered) helper — NOT {@code util.ObjectPool}, which is fixed-capacity,
 * throws on exhaustion, and is not tree-aware. Instantiating a full character scene is the
 * expensive, stutter-causing step; this pool avoids it by detaching healthy bodies on
 * {@link #release} ({@code removeChild}, NOT free) and handing them back on {@link #acquire}.
 *
 * <p><b>Only healthy bodies are pooled</b> — dead AI follow the normal death/ragdoll → free flow
 * and are never released here, so a recycled body never needs un-ragdolling. A detached body is
 * out of the scene tree, so it drops out of {@code getNodesInGroup("characters")} scans and stops
 * processing automatically. {@link AICharacter#activateForSpawn} re-initialises it on reuse (its
 * {@code _ready()} does not run again).
 *
 * <p>{@link #wasLastAcquireRecycled()} lets the manager skip re-equipping a recycled body that
 * still carries its previous weapon. Single-threaded use (the physics tick).
 */
public class SpawnPool {

    private final Deque<AICharacter> idle = new ArrayDeque<>();
    private final PackedScene scene;
    private final int capacity;
    private boolean lastRecycled = false;

    public SpawnPool(PackedScene scene, int capacity) {
        this.scene = scene;
        this.capacity = capacity;
    }

    /**
     * Return a detached (out-of-tree) AICharacter — a recycled one if available, else a fresh
     * instance. Returns null if the scene fails to instantiate an AICharacter. The caller adds it
     * to the tree and calls {@link AICharacter#activateForSpawn}.
     */
    public AICharacter acquire() {
        while (!idle.isEmpty()) {
            AICharacter ai = idle.poll();
            if (GD.isInstanceValid(ai)) { lastRecycled = true; return ai; }
        }
        lastRecycled = false;
        if (scene == null) return null;
        Node inst = scene.instantiate();
        if (inst instanceof AICharacter ai) return ai;
        if (inst != null) inst.queueFree();
        return null;
    }

    /** True if the most recent {@link #acquire} returned a recycled (already-armed) body. */
    public boolean wasLastAcquireRecycled() { return lastRecycled; }

    /** Number of detached bodies currently held for reuse (debug/logging aid). */
    public int idleCount() { return idle.size(); }

    /**
     * Detach a healthy body from the tree and keep it for reuse (up to capacity; beyond that it is
     * freed). The body's controller and equipped weapon ride along with the detached subtree.
     */
    public void release(AICharacter ai) {
        if (ai == null || !GD.isInstanceValid(ai)) return;
        Node parent = ai.getParent();
        if (parent != null) parent.removeChild(ai);
        if (idle.size() < capacity) idle.add(ai);
        else ai.queueFree();
    }

    /** Free every pooled body — called from ZoneManager._exitTree() (leak discipline). */
    public void clear() {
        for (AICharacter ai : idle) if (GD.isInstanceValid(ai)) ai.queueFree();
        idle.clear();
    }
}

package com.openworld.character;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;
import java.util.List;

/**
 * Shared group awareness for a band of AI (PLAN.md Part E / E3). A squad is a node — placed in the
 * editor (members point an {@code @Export squadPath} at it) or created per {@code SpawnConfig} by
 * {@link com.openworld.world.ZoneManager}. Members {@link #register} on spawn and
 * {@link #unregister} on free.
 *
 * <p><b>What it buys:</b> when one member confirms a target (LoS in {@code AttackState}, or being shot),
 * it {@link #broadcastSpotted}s — every squad-mate within {@link #alertBroadcastRadius} adopts that
 * target <i>immediately</i>, skipping its own ~0.4 s scan interval. So shooting one AI turns the whole
 * nearby squad toward the shooter within a frame instead of each waking on its next scan.
 * {@code AICharacter.discoverTarget()} consults {@link #getSharedTarget()} before its own scan, so a
 * mate keeps converging even before it personally sees the threat.
 *
 * <p>Squad members are one faction and the spotter already verified the target is hostile, so adopters
 * skip a redundant faction re-check. {@link #getSharedTarget()} self-clears a dead/freed target (the
 * "lose track" path), so explicit {@link #clearThreat()} is only needed to drop a still-alive one.
 */
@Script(className = "AISquad")
public class AISquad extends Node {

    /** A spotter's sighting reaches squad-mates within this distance of the spotter (m). */
    @Export public float alertBroadcastRadius = 60.0f;

    /** Seconds since any member last spotted the shared target after which the squad gives up on it
     *  ("loses track"). Refreshed on every {@link #broadcastSpotted}, so it only fires once no member
     *  has seen the target for this long — the still-alive-but-lost path (death auto-clears sooner). */
    @Export public float forgetDuration = 8.0f;

    private final List<AICharacter> members = new ArrayList<>();
    private Character sharedTarget;
    private Vector3 sharedLastKnownPosition;
    /** Monotonic seconds accumulated in _process (no engine-time dependency); the forget clock. */
    private double elapsed = 0.0;
    private double lastSpottedTime = 0.0;

    public void register(AICharacter m) {
        if (m != null && !members.contains(m)) members.add(m);
    }

    public void unregister(AICharacter m) {
        members.remove(m);
    }

    /** The squad's shared target, or null — auto-clears a target that has died or left the tree. */
    public Character getSharedTarget() {
        if (sharedTarget != null
                && (!GD.isInstanceValid(sharedTarget) || !sharedTarget.isInsideTree() || !sharedTarget.isAlive())) {
            sharedTarget = null;
            sharedLastKnownPosition = null;
        }
        return sharedTarget;
    }

    public Vector3 getSharedLastKnownPosition() { return sharedLastKnownPosition; }

    /**
     * A member spotted/was-hit-by {@code target}: record it as the squad target and push it to every
     * mate within {@link #alertBroadcastRadius} of the spotter so they engage this frame.
     */
    public void broadcastSpotted(AICharacter spotter, Character target, Vector3 pos) {
        if (target == null || spotter == null) return;
        sharedTarget = target;
        sharedLastKnownPosition = (pos != null) ? new Vector3(pos) : new Vector3(target.getGlobalPosition());
        lastSpottedTime = elapsed;
        Vector3 spotterPos = spotter.getGlobalPosition();
        for (AICharacter m : members) {
            if (m == spotter || !GD.isInstanceValid(m) || m.isDead()) continue;
            if (m.getGlobalPosition().distanceTo(spotterPos) <= alertBroadcastRadius) {
                m.adoptSquadTarget(target, sharedLastKnownPosition);
            }
        }
    }

    /** Drop a still-alive shared target (e.g. the squad gave up the search). */
    public void clearThreat() {
        sharedTarget = null;
        sharedLastKnownPosition = null;
    }

    @Register
    @Override
    public void _process(double delta) {
        elapsed += delta;
        // Lose track: no member has spotted the target for forgetDuration → the squad gives up, and
        // members fall back to their own scans on the next discoverTarget. (Death is handled sooner by
        // getSharedTarget's self-clear.)
        if (sharedTarget != null && elapsed - lastSpottedTime > forgetDuration) clearThreat();
    }

    @Register
    @Override
    public void _exitTree() {
        members.clear();
        sharedTarget = null;
        sharedLastKnownPosition = null;
    }
}

package com.openworld.world;

import com.openworld.character.Player;
import com.openworld.game.PlayerRegistry;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.CharacterBody3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.RigidBody3D;
import godot.core.StringName;
import godot.core.Vector3;
import godot.global.GD;

import java.util.List;

/**
 * THE LOGIC WALL — the edge of the world, enforced in script rather than with a collider.
 *
 * <p>Baked from a {@code bounds_<id>} marker ({@code WorldBaker}); {@code size} gives the playable
 * square and {@code floor} the kill-Z. The island's sea floor deliberately reaches
 * {@code island_v3_terrain.SEABED_MARGIN} (288 m) FURTHER than this boundary, so the wall always
 * stands on real ground with more ground behind it — a wall on the last triangle of the world is a
 * wall with a hole under it the moment anything overshoots by one frame's motion.
 *
 * <p><b>Why not an invisible collider.</b> A ring of static walls is the obvious build and it is
 * worse in three ways that all matter here: it collides with bullets, vehicles and ragdolls as well
 * as with the player; a `CharacterBody3D` pressed into it slides along it at full speed forever
 * (there is no "you are leaving the area" moment to hang anything on); and it cannot do the one
 * thing this must also do, which is recover a body that is somehow already outside — the collider
 * would simply keep it out there. A per-frame correction answers all three, and it is the same
 * shape {@code ZoneManager.maintainTraffic} already uses to reclaim a vehicle below
 * {@code Y = -30}.
 *
 * <p><b>What it feels like.</b> Inside {@link #softMargin} of the edge the body is pushed inward
 * with a force that ramps from nothing to {@link #pushSpeed} — you can still walk out into it, and
 * it gets harder, and then you stop. Past the edge the position is clamped back and the outward
 * part of the velocity is cancelled, so nothing can accumulate speed against it. Turning round and
 * swimming back to shore is unresisted: the push is one-directional by construction (it only ever
 * points inward).
 *
 * <p><b>Cost.</b> Players every frame, off {@link PlayerRegistry} (there are one or a handful, and
 * they are the ones who will actually go looking for the edge). Everything else in the
 * {@code "characters"} group — AI, vehicles — on a {@link #sweepInterval} tick, the same cadence
 * and the same reason as the traffic sweep: a car doing 30 m/s covers 15 m in that window and is
 * still turned round well before it reaches ground that does not exist.
 */
@Script(className = "WorldBounds")
public class WorldBounds extends Node3D {

    /** Half-extent of the playable square in X and Z, metres. The wall stands here. */
    @Export public float halfExtent = 2016f;

    public float getHalfExtent() { return halfExtent; }

    public void setHalfExtent(float v) { halfExtent = v; }

    /** How far inside the wall the push starts, metres. 0 makes it a hard stop with no warning. */
    @Export public float softMargin = 80f;

    public float getSoftMargin() { return softMargin; }

    public void setSoftMargin(float v) { softMargin = v; }

    /** Full-strength inward speed at the wall, m/s. Comfortably over a swim and under a sprint. */
    @Export public float pushSpeed = 4f;

    public float getPushSpeed() { return pushSpeed; }

    public void setPushSpeed(float v) { pushSpeed = v; }

    /**
     * Kill-Z. A body below this has left the world downward — through a hole, or off the seabed's
     * far edge — and is put back rather than fallen forever. It is far below the sea floor on
     * purpose: this is the backstop for a defect, not part of the design.
     */
    @Export public float floorY = -64f;

    public float getFloorY() { return floorY; }

    public void setFloorY(float v) { floorY = v; }

    /** Seconds between sweeps of everything that is not a player. */
    @Export public float sweepInterval = 0.5f;

    public float getSweepInterval() { return sweepInterval; }

    public void setSweepInterval(float v) { sweepInterval = v; }

    @Export public boolean debugLog = false;

    public boolean getDebugLog() { return debugLog; }

    public void setDebugLog(boolean v) { debugLog = v; }

    private static final StringName CHARACTERS = new StringName("characters");
    private static final StringName PLAYER_SPAWN = new StringName("PlayerSpawn");

    private double sweepTimer = 0.0;

    @Register
    @Override
    public void _physicsProcess(double delta) {
        List<Player> players = PlayerRegistry.getPlayers();
        for (Player p : players) {
            if (p != null && GD.isInstanceValid(p)) confine(p, delta);
        }
        sweepTimer -= delta;
        if (sweepTimer > 0.0) return;
        sweepTimer = sweepInterval;
        if (getTree() == null) return;
        for (Node n : getTree().getNodesInGroup(CHARACTERS)) {
            // The players were done above at full rate; doing them twice would double the push.
            if (n instanceof Node3D body && !(n instanceof Player p && players.contains(p))) {
                confine(body, sweepInterval);
            }
        }
    }

    /**
     * One body, one frame. Returns true if it had to be moved.
     *
     * <p>The correction is applied PER AXIS and only ever inward, which is what keeps it from
     * fighting ordinary movement: a body running along the wall is corrected in X and left alone in
     * Z, and a body heading back toward the island has no outward component to cancel.
     */
    private boolean confine(Node3D body, double delta) {
        Vector3 pos = body.getGlobalPosition();
        if (pos.getY() < floorY) {
            rescue(body);
            return true;
        }
        double px = pos.getX(), pz = pos.getZ();
        double dx = push(px, delta), dz = push(pz, delta);
        if (dx == 0.0 && dz == 0.0) return false;
        body.setGlobalPosition(new Vector3(px + dx, pos.getY(), pz + dz));
        cancelOutward(body, dx, dz);
        if (debugLog) {
            GD.print("WorldBounds: turned back " + body.getName() + " at ("
                    + Math.round(px) + ", " + Math.round(pz) + ")");
        }
        return true;
    }

    /**
     * Inward correction for one axis, metres this frame.
     *
     * <p>Past the wall it is the whole overshoot, so a body can never be outside for two frames
     * running. Inside {@link #softMargin} it is a ramped nudge — quadratic rather than linear so
     * the first metres of the band are almost free and the last are firm, which is what makes it
     * read as resistance rather than as a floor that has started sliding.
     */
    private double push(double v, double delta) {
        double over = Math.abs(v) - halfExtent;
        double sign = v >= 0.0 ? -1.0 : 1.0;
        if (over >= 0.0) return sign * (over + 0.01);
        if (softMargin <= 0.0) return 0.0;
        double into = over + softMargin;                 // 0 at the band's inner edge
        if (into <= 0.0) return 0.0;
        double t = into / softMargin;
        return sign * pushSpeed * t * t * delta;
    }

    /** Kill the outward part of the body's own velocity so it cannot build speed into the wall. */
    private void cancelOutward(Node3D body, double dx, double dz) {
        if (body instanceof CharacterBody3D cb) {
            Vector3 v = cb.getVelocity();
            cb.setVelocity(new Vector3(clampSign(v.getX(), dx), v.getY(), clampSign(v.getZ(), dz)));
        } else if (body instanceof RigidBody3D rb) {
            Vector3 v = rb.getLinearVelocity();
            rb.setLinearVelocity(new Vector3(clampSign(v.getX(), dx), v.getY(), clampSign(v.getZ(), dz)));
        }
    }

    /** {@code v}, with any component pointing the opposite way to the correction removed. */
    private static double clampSign(double v, double correction) {
        if (correction > 0.0 && v < 0.0) return 0.0;
        if (correction < 0.0 && v > 0.0) return 0.0;
        return v;
    }

    /**
     * Put a body that fell out of the world back on the map: the scene's {@code PlayerSpawn}
     * marker when there is one (the convention {@code GameManager.spawnPlayerBody} and
     * {@code DebugHarness} already share), else the world origin at ground level.
     */
    private void rescue(Node3D body) {
        Node scene = getTree() != null ? getTree().getCurrentScene() : null;
        Node marker = scene != null ? scene.getNodeOrNull(new godot.core.NodePath(PLAYER_SPAWN.toString())) : null;
        Vector3 to = marker instanceof Node3D anchor
                ? anchor.getGlobalPosition().plus(new Vector3(0f, 2f, 0f))
                : new Vector3(0f, 4f, 0f);
        body.setGlobalPosition(to);
        cancelAll(body);
        GD.print("WorldBounds: rescued " + body.getName() + " from below " + floorY + " m");
    }

    private static void cancelAll(Node3D body) {
        Vector3 zero = Vector3.Companion.getZERO();
        if (body instanceof CharacterBody3D cb) cb.setVelocity(zero);
        else if (body instanceof RigidBody3D rb) { rb.setLinearVelocity(zero); rb.setAngularVelocity(zero); }
    }
}

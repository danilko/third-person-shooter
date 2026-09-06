package com.openworld.debug;

import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.world.WorldZone;
import com.openworld.world.WorldZoneManager;
import com.openworld.world.WorldZoneMarker;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.PhysicsDirectSpaceState3D;
import godot.api.PhysicsRayQueryParameters3D;
import godot.api.World3D;
import godot.core.Dictionary;
import godot.core.RID;
import godot.core.StringName;
import godot.core.Vector3;
import godot.core.VariantArray;
import godot.global.GD;

/**
 * One-shot diagnostic (NOT a permanent regression test) verifying the "vehicles crash at a point
 * rather than follow the Path3D" report in District_industry_5_1, after removing the triplicated
 * Segment_007/008/009 duplicate lane geometry found at that junction. Streams the real district in
 * at its true master-world marker position, lets ambient traffic spawn and drive, and every 0.5s
 * scans every {@link Vehicle} in the "characters" group for a speed spike (a sudden physics
 * explosion from bad/self-intersecting collision would show as an implausible speed, not a smooth
 * curve) or a huge single-tick position jump (a teleport/warp between duplicate lanes).
 *
 * Run with:
 *   godot --headless res://src/main/resources/com/openworld/debug/TrafficCrashDiagnostic.tscn
 *
 * Grep for "TRAFFICDIAG".
 */
@Script(className = "TrafficCrashDiagnosticHost")
public class TrafficCrashDiagnosticHost extends Node3D {

    private static final String DISTRICT =
            "res://src/main/resources/com/openworld/world/districts/District_industry_5_1.tscn";
    private static final Vector3 MARKER_POS = new Vector3(1260f, 0f, 756f);
    private static final double RUN_SECONDS = 45.0;
    private static final float SPEED_ALARM = 60f;      // m/s, well above any sane traffic top speed
    private static final float JUMP_ALARM = 15f;        // m in one 0.5s sample = 30 m/s implied, still generous

    private WorldZoneMarker marker;
    private com.openworld.character.Player player;
    private double timer = 0.0;
    private double nextSample = 0.5;
    private final java.util.Map<Long, Vector3> lastPos = new java.util.HashMap<>();
    private final java.util.Map<Long, Boolean> onRoad = new java.util.HashMap<>();
    private int anomalies = 0;
    private boolean raycastDone = false;

    /** Raycast straight down from `pos` (well above it) and return the hit dictionary (null if
     * nothing hit), so "vehicle Y = X" can be checked against "what is actually under it" instead
     * of guessed from a single fixed reference height -- this district spans a real elevation
     * change (harbor/dock ramps), so a low Y is not automatically off-road. */
    private Dictionary<java.lang.Object, java.lang.Object> raycastDown(Vector3 pos) {
        World3D world = getWorld3d();
        PhysicsDirectSpaceState3D space = world != null ? world.getDirectSpaceState() : null;
        if (space == null) return null;
        Vector3 from = new Vector3(pos.getX(), pos.getY() + 50f, pos.getZ());
        Vector3 to = new Vector3(pos.getX(), pos.getY() - 50f, pos.getZ());
        VariantArray<RID> exclude = new VariantArray<>(RID.class);
        PhysicsRayQueryParameters3D q =
                PhysicsRayQueryParameters3D.Companion.create(from, to, 1L, exclude);
        Dictionary<java.lang.Object, java.lang.Object> hit = space.intersectRay(q);
        return (hit == null || hit.isEmpty()) ? null : hit;
    }

    private void raycastReport(String label, Vector3 pos) {
        Dictionary<java.lang.Object, java.lang.Object> hit = raycastDown(pos);
        if (hit == null) {
            GD.print("TRAFFICDIAG: " + label + " vehicleY=" + pos.getY()
                    + " RAYCAST FOUND NOTHING within 50m up/down of vehicle -- floating/falling free");
        } else {
            GD.print("TRAFFICDIAG: " + label + " vehicleY=" + pos.getY()
                    + " RAYCAST HIT collider=" + hit.get("collider") + " at position=" + hit.get("position"));
        }
    }

    /** True if `pos` is within 2m (vertically) of whatever the downward raycast from it hits --
     * "resting on a real surface right here," road or not. Used to detect the MOMENT a vehicle
     * leaves whatever surface was supporting it (road collision has a name prefix of pave_/pad_/
     * curb_/an anonymized @StaticBody3D@N proxy_for one of those -- but ANY surface within 2m
     * counts as "on something," so this only flags genuine free-fall/detachment, not just
     * "not exactly on a named road piece" (e.g. legitimately parked over a manhole cover mesh). */
    private boolean isSupported(Vector3 pos) {
        Dictionary<java.lang.Object, java.lang.Object> hit = raycastDown(pos);
        if (hit == null) return false;
        java.lang.Object posObj = hit.get("position");
        if (!(posObj instanceof Vector3 hitPos)) return false;
        return Math.abs(hitPos.getY() - pos.getY()) < 2.0f;
    }

    @Register
    @Override
    public void _ready() {
        Node characters = new Node();
        characters.setName(new StringName("Characters"));
        addChild(characters);

        WorldZone zone = new WorldZone();
        zone.zoneId = "District_industry_5_1";
        zone.geometryPath = DISTRICT;
        zone.size = new Vector3(504f, 40f, 504f);
        zone.loadRadius = 402f;
        zone.unloadRadius = 552f;

        com.openworld.world.VehicleSpawnConfig vsc = new com.openworld.world.VehicleSpawnConfig();
        vsc.routeName = "District_industry_5_1";
        vsc.count = 8;
        zone.vehicleSpawnConfigs.add(vsc);

        marker = new WorldZoneMarker();
        marker.setName(new StringName("ZoneMarker_District_industry_5_1"));
        marker.zone = zone;
        addChild(marker);
        marker.setGlobalPosition(MARKER_POS);

        java.lang.Object res = godot.api.ResourceLoader.INSTANCE.load(
                "res://src/main/resources/com/openworld/character/Player.tscn", "",
                godot.api.ResourceLoader.CacheMode.REUSE);
        if (res instanceof godot.api.PackedScene packed
                && packed.instantiate() instanceof com.openworld.character.Player p) {
            player = p;
            addChild(p);
            p.setGlobalPosition(MARKER_POS.plus(new Vector3(0f, 3f, 0f)));
        }
        GD.print("TRAFFICDIAG: zone marker placed at " + MARKER_POS + "; will run " + RUN_SECONDS + "s");
    }

    @Register
    @Override
    public void _physicsProcess(double delta) {
        timer += delta;

        // Per-tick trace of EVERY vehicle during the window most departures happen in (t=1-4s per
        // the 0.5s-sampled data), to see the actual trajectory shape (turning vs. straight, sudden
        // vs. gradual) instead of only 0.5s snapshots -- narrow window keeps volume manageable.
        if (timer >= 1.0 && timer <= 4.0) {
            for (Node n : getTree().getNodesInGroup(new StringName("characters"))) {
                if (!(n instanceof Vehicle v)) continue;
                Vector3 pos = v.getGlobalPosition();
                Vector3 vel = v.getLinearVelocity();
                GD.print(String.format("TRAFFICDIAG: TRACE t=%.3f v=%s pos=(%.3f,%.3f,%.3f) vel=(%.2f,%.2f,%.2f) supported=%s",
                        timer, v.getName(), pos.getX(), pos.getY(), pos.getZ(), vel.getX(), vel.getY(), vel.getZ(),
                        isSupported(pos)));
            }
        }

        if (timer >= nextSample) {
            nextSample += 0.5;
            int count = 0, moving = 0;
            for (Node n : getTree().getNodesInGroup(new StringName("characters"))) {
                if (!(n instanceof Vehicle v)) continue;
                count++;
                Vector3 pos = v.getGlobalPosition();
                float speed = (float) v.getLinearVelocity().length();
                if (speed > 0.5f) moving++;
                if (speed > SPEED_ALARM) {
                    anomalies++;
                    GD.printErr("TRAFFICDIAG: ANOMALY speed=" + speed + " m/s at pos=" + pos
                            + " vehicle=" + v.getName());
                }
                Vector3 prev = lastPos.get(v.getInstanceId());
                if (prev != null) {
                    float jump = (float) prev.distanceTo(pos);
                    if (jump > JUMP_ALARM) {
                        anomalies++;
                        GD.printErr("TRAFFICDIAG: ANOMALY jump=" + jump + " m in 0.5s from " + prev
                                + " to " + pos + " vehicle=" + v.getName());
                    }
                }
                lastPos.put(v.getInstanceId(), pos);

                boolean supported = isSupported(pos);
                Boolean was = onRoad.get(v.getInstanceId());
                if (was != null && was && !supported) {
                    GD.print(String.format(
                            "TRAFFICDIAG: DEPARTED SURFACE at t=%.1f vehicle=%s pos=%s (was "
                                    + "supported last sample, now isn't -- this is where/when it "
                                    + "left the road)",
                            timer, v.getName(), pos));
                } else if ((was == null || !was) && supported) {
                    GD.print(String.format(
                            "TRAFFICDIAG: (re)LANDED at t=%.1f vehicle=%s pos=%s",
                            timer, v.getName(), pos));
                }
                onRoad.put(v.getInstanceId(), supported);
            }
            GD.print(String.format("TRAFFICDIAG: t=%.1f vehicles=%d moving=%d anomalies=%d",
                    timer, count, moving, anomalies));

            if (!raycastDone) {
                raycastDone = true;
                int idx = 0;
                for (Node n : getTree().getNodesInGroup(new StringName("characters"))) {
                    if (!(n instanceof Vehicle v)) continue;
                    raycastReport("BEGINNING vehicle=" + v.getName() + " #" + (idx++),
                            v.getGlobalPosition());
                }
            }
        }

        if (timer >= RUN_SECONDS) {
            for (Node n : getTree().getNodesInGroup(new StringName("characters"))) {
                if (!(n instanceof Vehicle v)) continue;
                GD.print("TRAFFICDIAG: final vehicle=" + v.getName() + " pos=" + v.getGlobalPosition()
                        + " vel=" + v.getLinearVelocity()
                        + " sleeping=" + v.isSleeping());
                raycastReport("FINAL vehicle=" + v.getName(), v.getGlobalPosition());
            }
            GD.print("TRAFFICDIAG: done -- total anomalies=" + anomalies);
            if (getTree() != null) getTree().quit();
        }
    }
}

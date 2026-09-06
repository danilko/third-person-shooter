package com.openworld.debug;

import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.character.CharacterInfo;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.*;
import godot.core.Vector3;
import godot.global.GD;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Headless vehicle-physics test stand (the WorldBaker/ConvertPieces one-shot host idiom):
 * builds a large flat ground slab, spawns Vehicle.tscn driven by a
 * {@link ScriptedDriveController}, logs speed/roll telemetry every 0.5 s, prints a per-phase
 * summary, and quits. Run with:
 *
 *   godot --headless res://src/main/resources/com/openworld/world/hosts/DriveTest.tscn
 *
 * The grep-able health signal is the DRIVETEST SUMMARY block: per-phase top speed and max
 * body tilt (degrees off world-up). Tilt > 60° in any phase = the car flipped.
 */
@Script(className = "DriveTestHost")
public class DriveTestHost extends Node3D {

    private static final double LOG_INTERVAL = 0.5;
    private static final double FLIP_TILT_DEG = 60.0;

    private Vehicle vehicle;
    private ScriptedDriveController driver;
    private double logTimer = 0.0;
    private final Map<String, Double> maxTilt  = new HashMap<>();
    private final Map<String, Double> maxSpeed = new HashMap<>();
    private final Map<String, Double> maxLatG  = new HashMap<>();
    private final Map<String, Double> headingDeg = new HashMap<>();
    private Vector3 lastVel = Vector3.Companion.getZERO();
    private double  lastYaw = Double.NaN;
    private boolean done = false;

    @Register
    @Override
    public void _ready() {
        StaticBody3D ground = new StaticBody3D();
        CollisionShape3D cs = new CollisionShape3D();
        BoxShape3D box = new BoxShape3D();
        box.setSize(new Vector3(20000f, 1f, 20000f));   // 240 km/h launch alone covers ~1 km
        cs.setShape(box);
        ground.addChild(cs);
        ground.setPosition(new Vector3(0f, -0.5f, 0f));
        addChild(ground);

        Resource res = ResourceLoader.INSTANCE.load(
                "res://src/main/resources/com/openworld/vehicle/Vehicle.tscn", "",
                ResourceLoader.CacheMode.REUSE);
        if (!(res instanceof PackedScene packed)
                || !(packed.instantiate() instanceof Vehicle v)) {
            GD.printErr("DriveTestHost: cannot load Vehicle.tscn");
            return;
        }
        vehicle = v;
        CharacterInfo info = new CharacterInfo();
        info.characterId = UUID.randomUUID().toString();
        info.displayName = "DriveTest";
        v.characterInfo = info;

        driver = new ScriptedDriveController();
        v.addChild(driver);          // Vehicle._ready scans children for a Controller
        v.setPosition(new Vector3(0f, 1.0f, 0f));
        addChild(v);
        GD.print("DRIVETEST start");
    }

    @Register
    @Override
    public void _physicsProcess(double delta) {
        if (vehicle == null || driver == null || done) return;

        String phase = driver.phase();
        Vector3 vel  = vehicle.getLinearVelocity();
        double speed = vel.length();
        double upDot = GD.clamp(vehicle.getGlobalBasis().getY().dot(Vector3.Companion.getUP()), -1.0, 1.0);
        double tilt  = Math.toDegrees(Math.acos(upDot));
        // Horizontal Δv/Δt in g — in a steady turn this is the centripetal (lateral) accel,
        // the number the friction-circle cap (maxLateralG) must bound.
        Vector3 dv   = vel.minus(lastVel);
        double  latG = new Vector3(dv.getX(), 0.0, dv.getZ()).length() / Math.max(1e-4, delta) / 9.81;
        lastVel = vel;
        // Accumulated |Δyaw| per phase — HANDBRAKE's total is the drift-sharpness metric
        // (a good drift rotates 60–120° in its ~2.5 s window).
        double yaw = vehicle.getGlobalRotation().getY();
        if (!Double.isNaN(lastYaw)) {
            double dy = Math.atan2(Math.sin(yaw - lastYaw), Math.cos(yaw - lastYaw));
            headingDeg.merge(phase, Math.toDegrees(Math.abs(dy)), Double::sum);
        }
        lastYaw = yaw;
        maxTilt.merge(phase, tilt, Math::max);
        maxSpeed.merge(phase, speed, Math::max);
        maxLatG.merge(phase, latG, Math::max);

        logTimer -= delta;
        if (logTimer <= 0.0) {
            logTimer = LOG_INTERVAL;
            GD.print(String.format("DRIVETEST phase=%s kmh=%.1f tilt=%.1f g=%.2f pos=%s",
                    phase, speed * 3.6, tilt, latG, vehicle.getGlobalPosition()));
        }

        if (driver.finished()) {
            done = true;
            boolean flipped = false;
            for (String p : new String[]{"LAUNCH", "TURN", "SLALOM", "COAST", "HANDBRAKE", "BRAKE",
                                         "DONUT", "REVERSE"}) {
                double mt = maxTilt.getOrDefault(p, 0.0);
                double ms = maxSpeed.getOrDefault(p, 0.0);
                if (mt > FLIP_TILT_DEG) flipped = true;
                GD.print(String.format(
                        "DRIVETEST SUMMARY phase=%s maxKmh=%.1f maxTilt=%.1f maxG=%.2f turnedDeg=%.0f%s",
                        p, ms * 3.6, mt, maxLatG.getOrDefault(p, 0.0),
                        headingDeg.getOrDefault(p, 0.0),
                        mt > FLIP_TILT_DEG ? " FLIP" : ""));
            }
            GD.print("DRIVETEST verdict=" + (flipped ? "UNSTABLE" : "STABLE"));
            if (getTree() != null) getTree().quit();
        }
    }
}

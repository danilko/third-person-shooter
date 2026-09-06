package com.openworld.debug;

import com.openworld.ai.vehicle.VehicleAIController;
import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.character.CharacterInfo;
import com.openworld.character.Faction;
import com.openworld.character.Player;
import com.openworld.control.Controller;
import com.openworld.movement.character.MovementController;
import com.openworld.movement.character.MovementType;
import com.openworld.movement.character.StanceName;
import com.openworld.util.CollisionLayers;
import com.openworld.world.WaterVolume;
import com.openworld.world.WorldBounds;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.BoxShape3D;
import godot.api.CollisionShape3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.PackedScene;
import godot.api.Resource;
import godot.api.ResourceLoader;
import godot.api.StaticBody3D;
import godot.core.NodePath;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * Headless HANDLING test stand — the two walk-test fixes of 2026-08-30, driven instead of played
 * (WORLD_REBUILD_PLAN.md {@code W5}).
 *
 * <pre>
 *   godot --headless res://src/main/resources/com/openworld/world/hosts/HandlingTest.tscn
 * </pre>
 *
 * <p><b>Why this exists.</b> Both fixes — {@link MovementController#stepHeight}'s ledge step and
 * {@link Vehicle#isAiDriven()} + the {@code tryEnter} brain drop — compiled, and the scene loaded
 * with zero errors, and neither was worth anything as evidence: a headless run presses no keys, so
 * "it loads" and "it works" had never been the same claim. The UserCommand loop is built to take
 * its input from an interchangeable third source, so the honest fix is to BE that source
 * ({@link ScriptedInputController}) rather than to ask a person to go and press F.
 *
 * <p><b>Every case is run WITH ITS CONTROL.</b> A test that only shows the character climbing a kerb
 * cannot tell you whether the step code did it or whether the physics engine would have anyway, and
 * one that only shows a player driving away cannot tell you the brain was dropped rather than never
 * there. So the kerb is walked three times — once as shipped, once with {@code stepHeight = 0}
 * (must be blocked), once at a wall twice {@code stepHeight} (must be blocked) — and the car is
 * asserted AI-driven before the player takes it.
 *
 * <p>The grep-able signal is the {@code HANDLINGTEST SUMMARY} block and the process exit code:
 * every case prints {@code PASS} or {@code FAIL} with the number it decided on.
 */
@Script(className = "HandlingTestHost")
public class HandlingTestHost extends Node3D {

    // ── The world under test ──────────────────────────────────────────────────
    /** The height the road kit's carriageway proxy actually stands proud of the ground, measured
     *  across Yamate-dori on the built island. This is the kerb the fix is for, not a round number. */
    private static final float KERB_H = 0.160f;
    /** A ledge no step should ever take: comfortably over {@link MovementController#stepHeight}. */
    private static final float WALL_H = 0.60f;
    /** Where every ledge's near face stands. The walker starts {@link #WALK_START_X} short of it. */
    private static final float LEDGE_X = 0f;
    private static final float WALK_START_X = -3f;
    private static final float KERB_Z = 0f;
    private static final float WALL_Z = 30f;
    private static final float CAR_Z = 60f;
    private static final float WATER_Z = 90f;
    /** Water deep enough to swim: over `SwimState.swimEnterDepth` (1.4 m) above the slab. */
    private static final float WATER_TOP = 2.5f;
    /** A deliberately tiny world so the walker reaches its edge in a few seconds. */
    private static final float BOUNDS_HALF = 20f;
    private static final float BOUNDS_Z = 130f;

    /** Seconds of held forward walk per kerb case — 3 m of approach at walk pace, and then some. */
    private static final double WALK_SECONDS = 5.0;
    /** Seconds of held throttle in the drive case. */
    private static final double DRIVE_SECONDS = 4.0;
    /** Settling time after any teleport, so a case never measures a body still falling. */
    private static final double SETTLE_SECONDS = 1.0;
    /** How long to wait for the vehicle's EntranceArea to report the walker. */
    private static final double PROMPT_WAIT_SECONDS = 1.5;

    /** A climb counts when the body has risen this close to the ledge top. */
    private static final double CLIMB_MARGIN = 0.03;
    /** …and has actually got PAST the face, not just leaned on it. */
    private static final double PAST_LEDGE_X = 0.5;
    /** Driving counts when the car has covered this much ground under the player's own throttle. */
    private static final double DRIVE_MIN_METRES = 5.0;

    private enum Phase {
        SETTLE, KERB, KERB_RESET, KERB_CONTROL, WALL_RESET, WALL,
        CAR_RESET, CAR_PROMPT, CAR_ENTER, CAR_DRIVE, CAR_EXIT,
        WATER_RESET, WATER_WADE, WATER_SWIM, WATER_OUT,
        BOUNDS_RESET, BOUNDS_PUSH, BOUNDS_OUTSIDE, BOUNDS_FLOOR, DONE
    }

    private Player player;
    private MovementController movement;
    private ScriptedInputController input;
    private Vehicle vehicle;
    private WorldBounds bounds;

    private Phase phase = Phase.SETTLE;
    private double phaseTime = 0.0;
    private double baselineY = 0.0;
    private Vector3 driveStart = Vector3.Companion.getZERO();

    private final List<String> results = new ArrayList<>();
    private boolean failed = false;

    @Register
    @Override
    public void _ready() {
        // Ground, then the two ledges. A box whose top face is at `h` sits at `h - halfHeight`;
        // the half that ends up under the ground slab is simply never touched.
        addSlab("Ground", new Vector3(400f, 1f, 400f), new Vector3(0f, -0.5f, 0f));
        addSlab("Kerb", new Vector3(40f, 1f, 40f), new Vector3(20f + LEDGE_X, KERB_H - 0.5f, KERB_Z));
        addSlab("Wall", new Vector3(40f, 1f, 40f), new Vector3(20f + LEDGE_X, WALL_H - 0.5f, WALL_Z));

        Object res = ResourceLoader.INSTANCE.load(
                "res://src/main/resources/com/openworld/character/Player.tscn", "",
                ResourceLoader.CacheMode.REUSE);
        if (!(res instanceof PackedScene packed) || !(packed.instantiate() instanceof Player p)) {
            GD.printErr("HandlingTestHost: cannot load Player.tscn");
            finish();
            return;
        }
        player = p;
        addChild(player);
        player.setGlobalPosition(new Vector3(WALK_START_X, 1f, KERB_Z));

        Node mc = player.getNodeOrNull(new NodePath("MovementController"));
        if (!(mc instanceof MovementController m)) {
            GD.printErr("HandlingTestHost: Player has no MovementController node");
            finish();
            return;
        }
        movement = m;

        // THE PLAYER'S OWN CONTROLLER IS REPLACED, not added beside: `attachController` frees the
        // outgoing PlayerController (CLAUDE.md's swap-must-free rule), and a PlayerController left
        // in place would keep polling a keyboard nobody is at and fight this one for the command.
        input = new ScriptedInputController();
        player.attachController(input);

        GD.print("HANDLINGTEST start — kerb " + KERB_H + " m, wall " + WALL_H
                + " m, stepHeight " + movement.stepHeight + " m, stepReach " + movement.stepReach + " m");
    }

    @Register
    @Override
    public void _physicsProcess(double delta) {
        if (phase == Phase.DONE || player == null) return;
        phaseTime += delta;

        switch (phase) {
            case SETTLE -> {
                if (phaseTime >= SETTLE_SECONDS) {
                    baselineY = player.getGlobalPosition().getY();
                    GD.print("HANDLINGTEST baseline ground y = " + fmt(baselineY));
                    startWalk(Phase.KERB);
                }
            }
            case KERB -> {
                if (phaseTime >= WALK_SECONDS) {
                    judgeClimb("kerb 0.16 m, stepHeight " + fmt(movement.stepHeight), KERB_H, true);
                    stop();
                    // The control runs on the SAME kerb with the step switched off. Anything else
                    // (a different ledge, a different lane) would leave a second variable in it.
                    movement.stepHeight = 0.0;
                    teleport(KERB_Z);
                    phase = Phase.KERB_RESET;
                }
            }
            case KERB_RESET -> { if (phaseTime >= SETTLE_SECONDS) startWalk(Phase.KERB_CONTROL); }
            case KERB_CONTROL -> {
                if (phaseTime >= WALK_SECONDS) {
                    judgeClimb("kerb 0.16 m, stepHeight 0 (control)", KERB_H, false);
                    stop();
                    movement.stepHeight = 0.35;
                    teleport(WALL_Z);
                    phase = Phase.WALL_RESET;
                }
            }
            case WALL_RESET -> { if (phaseTime >= SETTLE_SECONDS) startWalk(Phase.WALL); }
            case WALL -> {
                if (phaseTime >= WALK_SECONDS) {
                    judgeClimb("wall 0.60 m, stepHeight " + fmt(movement.stepHeight), WALL_H, false);
                    stop();
                    setupVehicle();
                    phase = Phase.CAR_RESET;
                    phaseTime = 0.0;
                }
            }
            case CAR_RESET -> { if (phaseTime >= SETTLE_SECONDS) { phase = Phase.CAR_PROMPT; phaseTime = 0.0; } }
            case CAR_PROMPT -> {
                if (player.nearbyVehicle == vehicle) {
                    record("vehicle prompt — EntranceArea reported the walker", true, "nearbyVehicle set");
                    enterCar();
                } else if (phaseTime >= PROMPT_WAIT_SECONDS) {
                    // Not a failure of the fix under test, but it must be SAID: the rest of the
                    // case is then testing tryEnter without the area that normally arms it.
                    record("vehicle prompt — EntranceArea reported the walker", false,
                           "no body_entered within " + fmt(PROMPT_WAIT_SECONDS) + " s; wiring nearbyVehicle directly");
                    player.nearbyVehicle = vehicle;
                    enterCar();
                }
            }
            case CAR_ENTER -> {
                // One frame is enough: applyInput ran, requestEnter ran, tryEnter ran.
                if (phaseTime >= 0.2) {
                    boolean seated = vehicle.getOccupant() == player;
                    boolean brainGone = !vehicle.isAiDriven();
                    boolean atWheel = vehicle.getController() == input;
                    record("player takes the wheel of an AI-driven car",
                           seated && brainGone && atWheel,
                           "occupant=" + (seated ? "player" : String.valueOf(vehicle.getOccupant()))
                           + " isAiDriven=" + vehicle.isAiDriven()
                           + " vehicleController=" + name(vehicle.getController()));
                    driveStart = vehicle.getGlobalPosition();
                    input.motor = 1f;
                    phase = Phase.CAR_DRIVE;
                    phaseTime = 0.0;
                }
            }
            case CAR_DRIVE -> {
                if (phaseTime >= DRIVE_SECONDS) {
                    Vector3 now = vehicle.getGlobalPosition();
                    double dx = now.getX() - driveStart.getX();
                    double dz = now.getZ() - driveStart.getZ();
                    double moved = Math.sqrt(dx * dx + dz * dz);
                    record("the car drives on the player's own throttle", moved >= DRIVE_MIN_METRES,
                           "moved " + fmt(moved) + " m in " + fmt(DRIVE_SECONDS) + " s");
                    input.motor = 0f;
                    input.brake = true;
                    input.pressEnterExit();
                    phase = Phase.CAR_EXIT;
                    phaseTime = 0.0;
                }
            }
            case CAR_EXIT -> {
                if (phaseTime >= 0.5) {
                    boolean empty = vehicle.getOccupant() == null;
                    boolean gotBack = player.getController() == input;
                    record("the player gets back out", empty && gotBack,
                           "occupant=" + name(vehicle.getOccupant())
                           + " playerController=" + name(player.getController()));
                    setupWater();
                    teleportTo(-6f, WATER_Z);
                    phase = Phase.WATER_RESET;
                }
            }
            case WATER_RESET -> { if (phaseTime >= SETTLE_SECONDS) startWalk(Phase.WATER_WADE); }
            case WATER_WADE -> {
                // Half a second in: still on the slab short of the pool, so definitely not swimming.
                if (phaseTime >= 0.5) {
                    record("dry ground is not water", player.getStanceOrdinal() != SWIM_ORDINAL,
                           "stance=" + stance(player) + " at x = " + fmt(player.getGlobalPosition().getX()));
                    phase = Phase.WATER_SWIM;
                    phaseTime = 0.0;
                }
            }
            case WATER_SWIM -> {
                if (phaseTime >= WALK_SECONDS) {
                    record("walking into deep water starts a swim",
                           player.getStanceOrdinal() == SWIM_ORDINAL,
                           "stance=" + stance(player) + " at ("
                           + fmt(player.getGlobalPosition().getX()) + ", "
                           + fmt(player.getGlobalPosition().getY()) + ")");
                    // ...and back out the way we came in. A swim you cannot leave is the beach bug.
                    input.move = new Vector3(-1f, 0f, 0f);
                    phase = Phase.WATER_OUT;
                    phaseTime = 0.0;
                }
            }
            case WATER_OUT -> {
                if (phaseTime >= WALK_SECONDS) {
                    record("swimming back out of the water ends the swim",
                           player.getStanceOrdinal() != SWIM_ORDINAL,
                           "stance=" + stance(player) + " at x = " + fmt(player.getGlobalPosition().getX()));
                    stop();
                    setupBounds();
                    teleportTo(0f, BOUNDS_Z);
                    phase = Phase.BOUNDS_RESET;
                }
            }
            case BOUNDS_RESET -> { if (phaseTime >= SETTLE_SECONDS) startWalk(Phase.BOUNDS_PUSH); }
            case BOUNDS_PUSH -> {
                // Walk straight at the edge for long enough to be well past it if nothing stopped us.
                if (phaseTime >= WALK_SECONDS * 2) {
                    double x = player.getGlobalPosition().getX();
                    record("the logic wall turns a walker back at the edge", x <= BOUNDS_HALF + 0.5,
                           "walked " + fmt(WALK_SECONDS * 2) + " s outward, reached x = " + fmt(x)
                           + " against a wall at " + fmt(BOUNDS_HALF));
                    stop();
                    // Dropped OUTSIDE, which a collider could never recover from — the reason this
                    // is a per-frame correction and not an invisible wall.
                    player.setVelocity(Vector3.Companion.getZERO());
                    player.setGlobalPosition(new Vector3(BOUNDS_HALF + 60f, (float) baselineY + 0.5f, BOUNDS_Z));
                    phase = Phase.BOUNDS_OUTSIDE;
                    phaseTime = 0.0;
                }
            }
            case BOUNDS_OUTSIDE -> {
                if (phaseTime >= 0.5) {
                    double x = player.getGlobalPosition().getX();
                    record("a body already outside is pushed back in", x <= BOUNDS_HALF + 0.5,
                           "dropped at x = " + fmt(BOUNDS_HALF + 60f) + ", now x = " + fmt(x));
                    player.setVelocity(Vector3.Companion.getZERO());
                    player.setGlobalPosition(new Vector3(0f, bounds.floorY - 20f, BOUNDS_Z));
                    phase = Phase.BOUNDS_FLOOR;
                    phaseTime = 0.0;
                }
            }
            case BOUNDS_FLOOR -> {
                if (phaseTime >= 0.5) {
                    double y = player.getGlobalPosition().getY();
                    record("a body below the kill-Z is rescued, not fallen forever",
                           y > bounds.floorY,
                           "dropped at y = " + fmt(bounds.floorY - 20f) + ", now y = " + fmt(y));
                    finish();
                }
            }
            default -> { }
        }
    }

    // ── the cases ─────────────────────────────────────────────────────────────

    private void startWalk(Phase next) {
        input.move = new Vector3(1f, 0f, 0f);          // world-space: +X, straight at the ledge
        input.movementType = MovementType.WALK;
        phase = next;
        phaseTime = 0.0;
    }

    private void stop() {
        input.move = Vector3.Companion.getZERO();
        input.movementType = MovementType.IDLE;
    }

    /** The stance name behind {@code Character.getStanceOrdinal()} — the one public view of it. */
    private static final int SWIM_ORDINAL = StanceName.SWIM.ordinal();

    private static String stance(Player p) {
        int o = p.getStanceOrdinal();
        StanceName[] all = StanceName.values();
        return o >= 0 && o < all.length ? all[o].name() : String.valueOf(o);
    }

    /**
     * A pool the walker can wade into: a {@link WaterVolume} whose top is {@link #WATER_TOP} above
     * the slab, so the depth under the body clears {@code SwimState.swimEnterDepth}.
     *
     * <p>Its mask is {@code CollisionLayers.CHARACTER}, which is the half of this that the world
     * baker was missing — an area on the default mask never sees a character body at all, and the
     * swim system then looks exactly like a swim system with no water in the world.
     */
    private void setupWater() {
        WaterVolume water = new WaterVolume();
        water.setName(new godot.core.StringName("TestWater"));
        water.setCollisionLayer(0L);
        water.setCollisionMask(CollisionLayers.CHARACTER);
        CollisionShape3D cs = new CollisionShape3D();
        BoxShape3D box = new BoxShape3D();
        box.setSize(new Vector3(60f, 12f, 40f));
        cs.setShape(box);
        water.addChild(cs);
        addChild(water);
        water.setGlobalPosition(new Vector3(30f, WATER_TOP - 6f, WATER_Z));
    }

    private void setupBounds() {
        bounds = new WorldBounds();
        bounds.setName(new godot.core.StringName("TestBounds"));
        bounds.halfExtent = BOUNDS_HALF;
        bounds.softMargin = 6f;
        bounds.floorY = (float) baselineY - 40f;
        addChild(bounds);
    }

    private void teleportTo(float x, float z) {
        player.setVelocity(Vector3.Companion.getZERO());
        player.setGlobalPosition(new Vector3(x, (float) baselineY + 0.5f, z));
        phaseTime = 0.0;
    }

    private void teleport(float z) {
        player.setVelocity(Vector3.Companion.getZERO());
        player.setGlobalPosition(new Vector3(WALK_START_X, (float) baselineY + 0.5f, z));
        phaseTime = 0.0;
    }

    /**
     * Did the walker end up ON the ledge? Both halves are required, and the second is the one that
     * matters: a body wedged against the face with its capsule riding up the corner gains height
     * without ever getting anywhere.
     */
    private void judgeClimb(String label, float ledgeH, boolean shouldClimb) {
        Vector3 pos = player.getGlobalPosition();
        double rise = pos.getY() - baselineY;
        boolean climbed = rise >= ledgeH - CLIMB_MARGIN && pos.getX() >= LEDGE_X + PAST_LEDGE_X;
        record(label, climbed == shouldClimb,
               (shouldClimb ? "expected to step up; " : "expected to be blocked; ")
               + "rose " + fmt(rise) + " m, reached x = " + fmt(pos.getX()));
    }

    private void setupVehicle() {
        // Out of the ledges' way, and back on flat ground.
        player.setVelocity(Vector3.Companion.getZERO());
        player.setGlobalPosition(new Vector3(-2f, (float) baselineY + 0.5f, CAR_Z));

        Object res = ResourceLoader.INSTANCE.load(
                "res://src/main/resources/com/openworld/vehicle/Vehicle.tscn", "",
                ResourceLoader.CacheMode.REUSE);
        if (!(res instanceof PackedScene packed) || !(packed.instantiate() instanceof Vehicle v)) {
            GD.printErr("HandlingTestHost: cannot load Vehicle.tscn");
            finish();
            return;
        }
        vehicle = v;
        // Identity in CODE, never the scene's shared sub-resource (CLAUDE.md's shared-sub-resource
        // rule) — the same three lines DebugHarness F4 writes for a traffic car.
        CharacterInfo info = new CharacterInfo();
        info.characterId = UUID.randomUUID().toString();
        info.displayName = "HandlingTest car";
        info.faction = Faction.NEUTRAL;
        vehicle.characterInfo = info;
        addChild(vehicle);
        vehicle.setGlobalPosition(new Vector3(0f, (float) baselineY + 0.6f, CAR_Z));
        // THE WHOLE POINT OF THE CASE: an ambient car is AI-driven with an EMPTY SEAT. This is F4's
        // own wiring (Design B puts the lane-follow brain on the VEHICLE), which is what made
        // isAiOccupied() the wrong question and left the player driving nothing.
        vehicle.attachController(new VehicleAIController());
        record("the car is AI-driven with an empty seat (the case under test)",
               vehicle.isAiDriven() && vehicle.getOccupant() == null,
               "isAiDriven=" + vehicle.isAiDriven() + " isAiOccupied=" + vehicle.isAiOccupied());
    }

    private void enterCar() {
        input.pressEnterExit();
        phase = Phase.CAR_ENTER;
        phaseTime = 0.0;
    }

    // ── reporting ─────────────────────────────────────────────────────────────

    private void record(String label, boolean ok, String detail) {
        if (!ok) failed = true;
        results.add(String.format("  %-4s %-52s %s", ok ? "PASS" : "FAIL", label, detail));
        GD.print("HANDLINGTEST " + (ok ? "PASS" : "FAIL") + " — " + label + " — " + detail);
    }

    private void finish() {
        phase = Phase.DONE;
        GD.print("=== HANDLINGTEST SUMMARY ===");
        for (String r : results) GD.print(r);
        GD.print("=== HANDLINGTEST " + (failed ? "FAILED" : "OK") + " ===");
        if (getTree() != null) getTree().quit(failed ? 1 : 0);
    }

    private void addSlab(String name, Vector3 size, Vector3 at) {
        StaticBody3D body = new StaticBody3D();
        body.setName(new godot.core.StringName(name));
        CollisionShape3D cs = new CollisionShape3D();
        BoxShape3D box = new BoxShape3D();
        box.setSize(size);
        cs.setShape(box);
        body.addChild(cs);
        body.setPosition(at);
        addChild(body);
    }

    private static String name(Object o) {
        if (o == null) return "null";
        if (o instanceof Controller c) return c.getClass().getSimpleName();
        return o.getClass().getSimpleName();
    }

    private static String fmt(double v) { return String.format("%.3f", v); }
}

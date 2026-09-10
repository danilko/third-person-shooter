package com.openworld.debug;

import com.openworld.camera.AICameraController;
import com.openworld.character.Character;
import com.openworld.character.ShoulderAimModifier;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.BoneAttachment3D;
import godot.api.BoxMesh;
import godot.api.BoxShape3D;
import godot.api.CanvasLayer;
import godot.api.Camera3D;
import godot.api.CollisionShape3D;
import godot.api.Input;
import godot.api.InputEventMouseMotion;
import godot.api.DirectionalLight3D;
import godot.api.Label;
import godot.api.MeshInstance3D;
import godot.api.Node;
import godot.api.InputEvent;
import godot.api.InputEventKey;
import godot.api.RayCast3D;
import godot.api.SphereMesh;
import godot.core.Basis;
import godot.core.Key;
import com.openworld.movement.character.MovementType;
import com.openworld.movement.character.Stance;
import com.openworld.movement.character.StanceName;
import com.openworld.weapon.WeaponItem;
import godot.core.NodePath;
import godot.core.VariantArray;
import godot.api.AnimationTree;
import godot.api.BaseMaterial3D;
import com.openworld.util.CollisionLayers;
import com.openworld.world.WaterVolume;
import godot.api.Node3D;
import godot.api.PackedScene;
import godot.api.Skeleton3D;
import godot.api.StandardMaterial3D;
import godot.api.StaticBody3D;
import godot.core.Color;
import godot.core.StringName;
import godot.core.Vector2;
import godot.core.Transform3D;
import godot.core.Vector3;
import java.util.ArrayList;
import java.util.List;
import godot.global.GD;

/**
 * Walk-around debug stand for the "which way is the character actually facing" problem.
 *
 * <p>Run it as the main scene ({@code world/hosts/AimDebug.tscn}) and move/aim normally. It builds
 * a flat floor, four labelled compass pillars, and a Player placed at the SAME +90° yaw both world
 * scenes use ({@code World.tscn:334}, {@code DebugWorld.tscn:556}) — the offset only reproduces
 * when the body is rotated, so a stand that spawns at identity would show nothing.
 *
 * <p>With {@code autoDrive} on (or {@code --headless}, which turns it on by itself, since a
 * headless stand has no hands) it instead presses its own keys and moves its own mouse through a
 * fixed case list and prints PASS/FAIL per case — a stand you have to interpret by eye can only
 * tell you a number, not whether it is the right number. Every case drives the SHIPPED
 * PlayerController through the real {@code Input} singleton, so nothing under test is stubbed.
 *
 * <p>The overlay prints every yaw in the chain, in world degrees, plus the two differences that
 * matter. Read it like this:
 * <ul>
 *   <li><b>hips − camera</b> is the reported bug. It should be ~0 while aiming.</li>
 *   <li><b>aim − camera</b> isolates {@code MovementController.aimYaw()}'s input: if this is off,
 *       the AimTarget is not where the camera looks and the legs are faithfully following a bad
 *       number. If it is ~0 while hips is not, the fault is downstream of the aim point.</li>
 *   <li><b>shoulders − hips</b> is the upper/lower split, so you can tell "the whole character is
 *       turned" from "only the legs are turned" without eyeballing it.</li>
 * </ul>
 *
 * <p>Two measurement rules are baked in, both of which produced wrong conclusions when broken:
 * facings come from a PAIR of bones (a thigh's own axes run down the bone, so one bone's +Z means
 * nothing), and they are read through {@link BoneAttachment3D}, because
 * {@code Skeleton3D.get_bone_global_pose()} returns the pose BEFORE the skeleton modifiers run —
 * which makes a working {@code SpineAimModifier} look inert.
 */
@Script(className = "AimDebugHost")
public class AimDebugHost extends Node3D {

    @Export
    public String playerScenePath = "res://src/main/resources/com/openworld/character/Player.tscn";

    /**
     * Both world scenes place the Player at this yaw ({@code World.tscn:334},
     * {@code DebugWorld.tscn:556} — their basis is a +90 degree Y rotation, not the -90 this
     * stand used to claim). The stand mirrors it because a frame bug that cancels at identity
     * shows nothing at identity.
     */
    @Export
    public double bodyYawDegrees = 90.0;

    /** Also echo the readout to the console this often, so a headless run can verify it. */
    @Export
    public double printInterval = 1.0;

    /**
     * Press the stand's own keys and assert, instead of waiting for a human. Forced on when there
     * is no display, because a headless stand cannot be walked.
     */
    @Export
    public boolean autoDrive = false;

    /**
     * Interactive workbench: a poseable mannequin on the AI rig, a movable aim ball it tracks, and
     * a free-fly camera. Off by default so the plain stand and the driven self-test are unchanged.
     */
    @Export
    public boolean workbench = false;

    @Export
    public String aiScenePath = "res://src/main/resources/com/openworld/character/AICharacter.tscn";

    @Export
    public String vehicleScenePath = "res://src/main/resources/com/openworld/vehicle/Vehicle.tscn";

    /** Spawn a car and a pool so the DriveCarrier and Swim stances can be reached at all. */
    @Export
    public boolean spawnProps = true;

    /** Metres the aim ball moves per second while its keys are held. */
    @Export
    public double aimBallSpeed = 6.0;

    /** Metres per second for the free-fly camera (x4 while Shift is held). */
    @Export
    public double flySpeed = 8.0;

    private double printTimer;
    private Node3D player;
    private Label readout;
    private BoneAttachment3D thighL, thighR, clavL, clavR, spine3, head, neck;

    // ── Workbench ────────────────────────────────────────────────────────────────────────────
    private Node3D mannequin;                 // AICharacter body, posed by a ScriptedInputController
    private ScriptedInputController pose;     // ... this one
    private Node3D aimBall;                   // what the mannequin aims at
    private Camera3D flyCam;
    private boolean flying;
    private double flyYaw, flyPitch;
    private int mannequinWeapon = 1;
    private StanceName mannequinStance = StanceName.UPRIGHT;
    private AICameraController aiCamera;
    private Node3D vehicle;
    private WaterVolume water;
    private boolean playerHidden;       // "no character" mode: fly with nobody driving
    private boolean firing;

    @Register
    @Override
    public void _ready() {
        buildFloor();
        buildLight();
        // Unambiguous world directions: you can name what you are looking at instead of guessing.
        buildPillar(new Vector3(0, 0, -25), new Color(0.9, 0.2, 0.2, 1.0), "NORTH  -Z");
        buildPillar(new Vector3(0, 0, 25), new Color(0.2, 0.5, 0.9, 1.0), "SOUTH  +Z");
        buildPillar(new Vector3(25, 0, 0), new Color(0.2, 0.8, 0.3, 1.0), "EAST  +X");
        buildPillar(new Vector3(-25, 0, 0), new Color(0.9, 0.8, 0.2, 1.0), "WEST  -X");
        spawnPlayer();
        if (workbench) buildWorkbench();
        else if (autoDrive) buildAiSubject();
        buildOverlay();
    }

    private void buildFloor() {
        StaticBody3D floor = new StaticBody3D();
        floor.setName("Floor");
        CollisionShape3D cs = new CollisionShape3D();
        BoxShape3D shape = new BoxShape3D();
        shape.setSize(new Vector3(120, 1, 120));
        cs.setShape(shape);
        floor.addChild(cs);
        MeshInstance3D mi = new MeshInstance3D();
        BoxMesh mesh = new BoxMesh();
        mesh.setSize(new Vector3(120, 1, 120));
        mi.setMesh(mesh);
        mi.setMaterialOverride(material(new Color(0.25, 0.25, 0.28, 1.0)));
        floor.addChild(mi);
        addChild(floor);
        floor.setPosition(new Vector3(0, -0.5, 0));
    }

    private void buildLight() {
        DirectionalLight3D light = new DirectionalLight3D();
        light.setRotation(new Vector3(Math.toRadians(-50), Math.toRadians(35), 0));
        addChild(light);
    }

    private void buildPillar(Vector3 at, Color color, String label) {
        MeshInstance3D mi = new MeshInstance3D();
        BoxMesh mesh = new BoxMesh();
        mesh.setSize(new Vector3(2, 8, 2));
        mi.setMesh(mesh);
        mi.setMaterialOverride(material(color));
        mi.setName(label.replace(' ', '_'));
        addChild(mi);
        mi.setPosition(at.plus(new Vector3(0, 4, 0)));
    }

    private StandardMaterial3D material(Color c) {
        StandardMaterial3D m = new StandardMaterial3D();
        m.albedoColorProperty(c);
        return m;
    }

    private void spawnPlayer() {
        PackedScene scene = (PackedScene) GD.INSTANCE.load(playerScenePath);
        if (scene == null) {
            GD.INSTANCE.printErr("AimDebugHost: could not load " + playerScenePath);
            return;
        }
        Node inst = scene.instantiate();
        if (!(inst instanceof Node3D p)) {
            GD.INSTANCE.printErr("AimDebugHost: player scene root is not a Node3D");
            return;
        }
        player = p;
        // Place BEFORE addChild. `add_child` runs the whole subtree's `_ready()` synchronously,
        // and two of those bake the body's yaw at that instant: MovementController cached it as
        // `playerInitRotation`, and TPSCameraController's `setAsTopLevel(true)` freezes the rig's
        // world frame. Rotating afterwards left this stand measuring its own spawn order — 90
        // degrees of movement error and 88 of facing error, which is exactly the report it was
        // built to investigate. The world scenes carry the transform in the scene data, so it is
        // applied before `_ready()`; do the same here.
        p.setPosition(new Vector3(0, 1.0, 0));
        p.setRotation(new Vector3(0, Math.toRadians(bodyYawDegrees), 0));
        addChild(player);
    }

    private void buildOverlay() {
        CanvasLayer layer = new CanvasLayer();
        addChild(layer);
        readout = new Label();
        readout.setPosition(new godot.core.Vector2(16, 16));
        readout.addThemeFontSizeOverride(new godot.core.StringName("font_size"), 16);
        layer.addChild(readout);
    }

    /** Attaches once, lazily: the skeleton only exists after Character builds its visuals. */
    private boolean bindBones() {
        if (thighL != null) return true;
        if (player == null) return false;
        Skeleton3D skel = findSkeleton(player);
        if (skel == null) return false;
        thighL = attach(skel, "thigh_l");
        thighR = attach(skel, "thigh_r");
        clavL = attach(skel, "clavicle_l");
        clavR = attach(skel, "clavicle_r");
        armStand(skel);
        spine3 = attach(skel, "spine_03");
        head = attach(skel, "head_2");   // NOT "head" -- see attach()
        neck = attach(skel, "neck_01");
        return thighL != null;
    }

    /**
     * Puts a real firearm in the character's hand so {@link #gunAimError()} has a {@code Muzzle} to
     * measure.
     *
     * <p>CharacterVisuals_GodotChan instances only {@code Fist} under {@code WeaponAttachment} — a
     * MeleeItem, which has no muzzle — so without this the gun column reads "n/a" and the one
     * number that says whether the ARMS carry the weapon to the aim is missing. That matters most
     * for the shoulder-aim stances (crawl, drive, swim): their spine deliberately does not move, so
     * the weapon's direction comes entirely from the clavicle rotation
     * {@link com.openworld.character.ShoulderAimModifier} applies.
     *
     * <p>It is parented under the weapon's own marker and NOT equipped through WeaponController:
     * the muzzle's pose is a function of {@code MarkerAR4} and {@code hand_r} either way, and
     * equipping would drag in ammo, audio and network state this stand has no use for.
     */
    private void armStand(Skeleton3D skel) {
        armStandOn(player, skel);
    }

    private void armStandOn(Node body, Skeleton3D skel) {
        if (findByName(body, "Muzzle") != null) return;            // already armed
        Node marker = findByName(skel, "MarkerAR4");
        if (marker == null) {
            GD.INSTANCE.print("[AimDebug] armStand: no MarkerAR4 -- gun angle stays unmeasured");
            return;
        }
        Object res = GD.INSTANCE.load("res://src/main/resources/com/openworld/weapon/AR4.tscn");
        if (!(res instanceof PackedScene ps)) return;
        Node gun = ps.instantiate();
        if (gun != null) marker.addChild(gun);
    }

    // ── Workbench ────────────────────────────────────────────────────────────────────────────
    //
    // Three things an artist or designer needs that a driven self-test cannot give: a body that
    // HOLDS a pose while you look at it, an aim point you can put anywhere, and a camera that is
    // not bolted to either. The mannequin is a real AICharacter -- same Character,
    // MovementController, AnimationController and AICameraController the game runs -- with its
    // AIController swapped for a ScriptedInputController, so it exercises the AI RIG without the
    // AI BRAIN wandering off mid-inspection. The Player beside it is untouched and driven by hand,
    // which is the other half of "how does this look for both setups".

    private void buildWorkbench() {
        aimBall = buildAimBall();
        if (spawnProps) buildProps();
        spawnMannequin(new Vector3(3.0, 1.0, 0.0));
    }

    /** Where the driven test parks the AI subject: clear of the Player, which walks ~10 m per case. */
    private static final Vector3 AI_HOME = new Vector3(30.0, 1.0, 0.0);

    /**
     * The AI half of the driven test: a mannequin and its aim ball, and NO props.
     *
     * <p>The car and the pool are a workbench affordance; here they would be two physics bodies
     * sitting in the Player's walking area. The ball starts on the first case's offset so the AI
     * camera — which tracks at {@code aim_tracking_degrees_per_sec}, 90 deg/s on this rig — has the
     * whole player half of the run to settle before the first AI case is measured.
     */
    private void buildAiSubject() {
        aimBall = buildAimBall();
        aimBall.setPosition(AI_HOME.plus(AI_BALL_NORTH));
        spawnMannequin(AI_HOME);
    }

    private static final Vector3 AI_BALL_NORTH = new Vector3(0.0, 0.5, -30.0);
    private static final Vector3 AI_BALL_EAST = new Vector3(30.0, 0.5, 0.0);
    private static final Vector3 AI_BALL_SOUTH = new Vector3(0.0, 0.5, 30.0);
    private static final Vector3 AI_BALL_WEST = new Vector3(-30.0, 0.5, 0.0);

    /**
     * The AI subject: a real {@code AICharacter} with its {@code AIController} swapped for a
     * {@link ScriptedInputController}, so the AI RIG runs while the AI BRAIN does not wander off
     * mid-measurement.
     *
     * <p>Shared by the workbench and the driven self-test. The self-test needs it because the AI
     * and player camera rigs are DIFFERENT SHAPES — {@code AICharacter.tscn} overrides {@code Pivot}
     * back to identity while the player rig carries two cancelling 180-degree flips — so a change to
     * the camera convention (AIM_PLAN.md W3) moves the AI aim path and nothing measured it. It is
     * parked well away from the Player, which walks up to ~10 m from the origin during a case.
     */
    private void spawnMannequin(Vector3 at) {
        Object res = GD.INSTANCE.load(aiScenePath);
        if (!(res instanceof PackedScene ps)) {
            GD.INSTANCE.printErr("AimDebugHost: could not load " + aiScenePath);
            return;
        }
        Node inst = ps.instantiate();
        if (!(inst instanceof Node3D ai)) return;
        mannequin = ai;
        // Same rule as spawnPlayer: place BEFORE addChild, because add_child runs _ready() for the
        // whole subtree and the camera rig freezes its world frame there.
        ai.setPosition(at);
        ai.setRotation(new Vector3(0, Math.toRadians(bodyYawDegrees), 0));
        addChild(ai);

        if (ai instanceof Character c) {
            pose = new ScriptedInputController();
            pose.setName("PoseController");
            c.attachController(pose);          // frees the AIController it replaces
            pose.wantCombat = true;
            pose.desiredStance = mannequinStance;
            pose.desiredWeapon = mannequinWeapon;   // slot 0 is the fist; start on a real gun
        }
        // NOT armed here. armStandOn() early-returns when the body already has a Muzzle, and a
        // body's own WeaponController equips over the opening frames -- arming during _ready wins
        // that race and leaves a DANGLING AR4 in the marker's rest transform rather than a held
        // one. Measured: gun 91.6 deg off the ball that way, against 1.5-6.0 for a real equip.
        // So it is retried every frame from workbenchStep and only ever fires if nothing equips.
    }

    /**
     * A car and a pool, because two of the five stances cannot be reached without them.
     *
     * <p>DriveCarrier and Swim are the stances whose aim was most wrong and are the two the stand
     * could not measure -- "wired the same way and therefore probably fine" is exactly the kind of
     * claim this project keeps finding to be false. Walk the Player into the car (the game's own
     * enter key) or into the water and the readout covers them like any other stance. The car is
     * also the only place a YAW limit can be seen doing its job: a seated body cannot turn, so the
     * aim keeps asking for an offset the clamp has to refuse.
     */
    private void buildProps() {
        Object v = GD.INSTANCE.load(vehicleScenePath);
        if (v instanceof PackedScene vs && vs.instantiate() instanceof Node3D car) {
            vehicle = car;
            car.setPosition(new Vector3(-6.0, 0.6, 0.0));
            addChild(car);
        } else {
            GD.INSTANCE.print("[AimDebug] no vehicle at " + vehicleScenePath + " -- DriveCarrier uncovered");
        }

        // A pool: an Area3D on the CHARACTER mask (WaterVolume's own docs -- mask 1 never sees a
        // character body), plus a translucent box so you can see where it is.
        water = new WaterVolume();
        water.setName("Pool");
        CollisionShape3D cs = new CollisionShape3D();
        BoxShape3D box = new BoxShape3D();
        box.setSize(new Vector3(10, 4, 10));
        cs.setShape(box);
        water.addChild(cs);
        water.setCollisionMask(CollisionLayers.CHARACTER);
        MeshInstance3D mi = new MeshInstance3D();
        BoxMesh bm = new BoxMesh();
        bm.setSize(new Vector3(10, 4, 10));
        mi.setMesh(bm);
        StandardMaterial3D wm = new StandardMaterial3D();
        wm.albedoColorProperty(new Color(0.2, 0.5, 0.9, 0.35));
        wm.setTransparency(BaseMaterial3D.Transparency.ALPHA);
        mi.setMaterialOverride(wm);
        water.addChild(mi);
        addChild(water);
        water.setPosition(new Vector3(10.0, -1.0, 0.0));   // top face at y = 1
    }

    /** A visible, movable aim point. The mannequin tracks it; the readout measures against it. */
    private Node3D buildAimBall() {
        MeshInstance3D mi = new MeshInstance3D();
        SphereMesh mesh = new SphereMesh();
        mesh.setRadius(0.25f);
        mesh.setHeight(0.5f);
        mi.setMesh(mesh);
        mi.setMaterialOverride(material(new Color(1.0, 0.35, 0.85, 1.0)));
        mi.setName("AimBall");
        addChild(mi);
        mi.setPosition(new Vector3(3.0, 1.5, -8.0));
        return mi;
    }

    /** Keyboard state for the workbench, polled once per frame. */
    private void workbenchStep(double delta) {
        if (aimBall == null) return;

        // Aim ball: IJKL on the ground plane, U/O for height. Deliberately NOT the arrow keys --
        // those are bound to game actions and would move the Player at the same time.
        Vector3 d = Vector3.Companion.getZERO();
        if (Input.isPhysicalKeyPressed(Key.I)) d = d.plus(new Vector3(0, 0, -1));
        if (Input.isPhysicalKeyPressed(Key.K)) d = d.plus(new Vector3(0, 0, 1));
        if (Input.isPhysicalKeyPressed(Key.J)) d = d.plus(new Vector3(-1, 0, 0));
        if (Input.isPhysicalKeyPressed(Key.L)) d = d.plus(new Vector3(1, 0, 0));
        if (Input.isPhysicalKeyPressed(Key.U)) d = d.plus(new Vector3(0, 1, 0));
        if (Input.isPhysicalKeyPressed(Key.O)) d = d.plus(new Vector3(0, -1, 0));
        if (d.lengthSquared() > 0) {
            aimBall.setPosition(aimBall.getPosition()
                    .plus(d.normalized().times((float) (aimBallSpeed * delta))));
        }

        // The mannequin aims at the ball the way an AI does, which is TWO things, not one:
        // the command carries the world point (Character.applyInput moves the AimTarget marker
        // from it), and AICameraController.setAimTarget swings the camera rig -- which owns the
        // AimRay the marker hangs off, so driving only the command leaves the rig dragging the
        // marker back every frame. Measured: gun 91.6 deg off the ball with the command alone.
        driveMannequinAim();

        if (mannequin != null) armStandNode(mannequin);   // no-op once anything holds a Muzzle
        if (flying && flyCam != null) flyStep(delta);
    }

    /**
     * Point the mannequin at the aim ball.
     *
     * <p>TWO halves, not one: the command carries the world point ({@code Character.applyInput}
     * moves the {@code AimTarget} marker from it) AND {@code AICameraController.setAimTarget} swings
     * the camera rig — which owns the {@code AimRay} the marker hangs off, so driving only the
     * command leaves the rig dragging the marker back every frame. Measured: gun 91.6 deg off the
     * ball with the command alone.
     */
    private void driveMannequinAim() {
        if (aimBall == null) return;
        if (pose != null) pose.aimTargetPosition = aimBall.getGlobalPosition();
        if (aiCamera == null && mannequin != null) {
            Node n = findByName(mannequin, "TPSCameraController");
            if (n instanceof AICameraController ac) aiCamera = ac;
        }
        if (aiCamera != null) aiCamera.setAimTarget(aimBall.getGlobalPosition());
    }

    /** WASD + R/F, relative to where the fly camera is looking. Shift is a 4x sprint. */
    private void flyStep(double delta) {
        Basis b = flyCam.getGlobalTransform().getBasis();
        Vector3 move = Vector3.Companion.getZERO();
        if (Input.isPhysicalKeyPressed(Key.W)) move = move.minus(b.getZ());
        if (Input.isPhysicalKeyPressed(Key.S)) move = move.plus(b.getZ());
        if (Input.isPhysicalKeyPressed(Key.A)) move = move.minus(b.getX());
        if (Input.isPhysicalKeyPressed(Key.D)) move = move.plus(b.getX());
        if (Input.isPhysicalKeyPressed(Key.R)) move = move.plus(new Vector3(0, 1, 0));
        if (Input.isPhysicalKeyPressed(Key.F)) move = move.minus(new Vector3(0, 1, 0));
        if (move.lengthSquared() <= 0) return;
        double sp = flySpeed * (Input.isPhysicalKeyPressed(Key.SHIFT) ? 4.0 : 1.0);
        flyCam.setPosition(flyCam.getPosition().plus(move.normalized().times((float) (sp * delta))));
    }

    /**
     * Free-fly on/off.
     *
     * <p>It FREEZES the Player while active, and that is not laziness: the fly camera and the
     * Player both want WASD, so leaving the body live would walk it every time you moved the view.
     * Freezing also holds the pose still, which is the point of flying around it. The mannequin is
     * left running so its aim keeps tracking the ball while you circle it.
     */
    private void toggleFly() {
        if (flyCam == null) {
            flyCam = new Camera3D();
            flyCam.setName("FlyCam");
            addChild(flyCam);
            flyCam.setPosition(new Vector3(0, 2.5, 6));
        }
        flying = !flying;
        if (flying) {
            Vector3 e = flyCam.getRotation();
            flyYaw = e.getY();
            flyPitch = e.getX();
            flyCam.setCurrent(true);
        } else if (player instanceof Node3D) {
            Node3D pc = (Node3D) findByName(player, "ActiveCamera");
            if (pc != null) {
                Camera3D cam = (Camera3D) findByName(pc, "Camera3D");
                if (cam != null) cam.setCurrent(true);
            }
        }
        if (player != null) {
            player.setProcessMode(flying ? ProcessMode.DISABLED : ProcessMode.INHERIT);
        }
    }

    /**
     * Hold the mannequin's trigger.
     *
     * <p>It goes through {@code UserCommand.fire}, the same field a human's trigger sets, so the
     * whole shipped path runs: the semi-auto lock, the two-stage muzzle trace, recoil into
     * {@code CameraController.recoilPitch}, bloom, the tracer and the GUNSHOT stimulus. Firing is
     * what makes the aim numbers mean something -- a body can point a gun correctly and still put
     * the shot somewhere else, and only pulling the trigger tells them apart.
     */
    private void toggleFire() {
        firing = !firing;
        if (pose != null) pose.fire = firing;
    }

    /**
     * Take the Player out of the stand entirely, leaving the mannequin and the free camera.
     *
     * <p>Asked for as "free move across without any character". It is a real mode rather than just
     * hiding a mesh: the Player owns the current Camera3D and the mouse capture, so leaving it in
     * the tree means something is still steering the view. It is frozen and hidden, and the fly
     * camera takes over.
     */
    private void togglePlayer() {
        if (player == null) return;
        playerHidden = !playerHidden;
        player.setVisible(!playerHidden);
        player.setProcessMode(playerHidden ? ProcessMode.DISABLED : ProcessMode.INHERIT);
        if (playerHidden && !flying) toggleFly();
    }

    private void cycleWeapon(int dir) {
        mannequinWeapon += dir;
        if (mannequinWeapon < 0) mannequinWeapon = 0;
        if (pose != null) pose.desiredWeapon = mannequinWeapon;
    }

    private void setMannequinStance(StanceName st) {
        mannequinStance = st;
        if (pose != null) pose.desiredStance = st;
    }

    @Register
    @Override
    public void _input(InputEvent event) {
        if (!workbench) return;
        if (event instanceof InputEventMouseMotion mm && flying && flyCam != null) {
            flyYaw -= mm.getRelative().getX() * 0.003;
            flyPitch = GD.clamp(flyPitch - mm.getRelative().getY() * 0.003,
                                Math.toRadians(-89), Math.toRadians(89));
            flyCam.setRotation(new Vector3(flyPitch, flyYaw, 0));
            return;
        }
        if (!(event instanceof InputEventKey k) || !k.isPressed() || k.isEcho()) return;
        Key code = k.getPhysicalKeycode();
        if (code == Key.KEY_1) setMannequinStance(StanceName.UPRIGHT);
        else if (code == Key.KEY_2) setMannequinStance(StanceName.CROUCH);
        else if (code == Key.KEY_3) setMannequinStance(StanceName.CRAWL);
        else if (code == Key.KEY_4 && pose != null) pose.wantCombat = !pose.wantCombat;
        else if (code == Key.Q) cycleWeapon(-1);
        else if (code == Key.E) cycleWeapon(1);
        else if (code == Key.G) toggleFly();
        else if (code == Key.F) toggleFire();
        else if (code == Key.H) togglePlayer();
    }

    /** The key legend plus the mannequin's live aim numbers -- the thing you actually watch. */
    private String workbenchHeader() {
        String stance = mannequinStance.toString();
        String fly = flying ? "FLY (player frozen)" : "player camera";
        StringBuilder b = new StringBuilder();
        b.append("AIM WORKBENCH\n")
         .append("  1/2/3 mannequin stance   4 combat on/off   Q/E weapon slot\n")
         .append("  F hold fire   I/J/K/L move aim ball, U/O height\n")
         .append("  G free-fly camera (WASD + R/F, mouse, Shift fast)   H drop the player\n")
         .append("  PLAYER: WASD + mouse + right-mouse aim (your own hands)\n")
         .append("          walk into the CAR or the POOL for DriveCarrier / Swim\n")
         .append("\n")
         .append("mannequin (AI rig) : ").append(stance)
         .append(pose != null && pose.wantCombat ? " / combat" : " / relaxed")
         .append("   weapon slot ").append(mannequinWeapon).append("\n")
         .append("view               : ").append(fly).append("\n");
        b.append("firing             : ").append(firing ? "YES" : "no")
         .append("      player: ").append(playerHidden ? "REMOVED" : "in scene").append("\n");
        if (mannequin != null && aimBall != null) {
            b.append("mannequin gun-off-aim : ")
             .append(gunText(gunAimErrorOn(mannequin, aimBall)))
             .append("   chest-off-ball ").append(gunText(boneOffBall(mannequin, "spine_03")))
             .append("\n");
        }
        b.append(setupLine(player, "PLAYER   ")).append(setupLine(mannequin, "MANNEQUIN"));
        b.append("\n");
        return b.toString();
    }

    /**
     * Angle between a bone's own forward (+Z, this rig's chest frame -- see {@link #chestForward})
     * and the direction to the aim ball. This is what says whether the aim MODIFIERS are working
     * on a given body, separately from whether the body's mesh turned.
     */
    private double boneOffBall(Node body, String boneName) {
        Skeleton3D skel = findSkeleton(body);
        if (skel == null || aimBall == null) return Double.NaN;
        int idx = skel.findBone(boneName);
        if (idx < 0) return Double.NaN;
        Transform3D g = skel.getGlobalTransform().times(skel.getBoneGlobalPose(idx));
        Vector3 to = aimBall.getGlobalPosition().minus(g.getOrigin());
        Vector3 fwd = g.getBasis().getZ();
        if (to.lengthSquared() < 1e-6f || fwd.lengthSquared() < 1e-6f) return Double.NaN;
        double cos = to.normalized().dot(fwd.normalized());
        return Math.toDegrees(Math.acos(Math.max(-1.0, Math.min(1.0, cos))));
    }

    /**
     * One line describing how a body is set up -- which controller drives it, which aim mechanism
     * its stance selects and with what yaw cap, what it is holding, and where it is.
     *
     * <p>Asked for as "display the test character setup", and it earns its place: nearly every
     * defect in this area was a stance selecting the wrong mechanism, or a body whose controller
     * was not the one being reasoned about.
     */
    private String setupLine(Node3D body, String label) {
        if (!(body instanceof Character c)) return label + ": -\n";
        Node ctrl = c.getNodeOrNull(new NodePath("PoseController"));
        String driver = ctrl != null ? "ScriptedInputController"
                : (c.getNodeOrNull(new NodePath("PlayerController")) != null ? "PlayerController"
                : (c.getNodeOrNull(new NodePath("AIController")) != null ? "AIController"
                : (c.getNodeOrNull(new NodePath("NetworkController")) != null ? "NetworkController"
                : "?")));
        // Character keeps its stance in a private field and exposes only the replicated ORDINAL,
        // so map that back through StanceName -- the same enum the scene names its Stance nodes
        // after, one child of "Stances" each.
        StanceName sname = stanceForOrdinal(c.getStanceOrdinal());
        String stName = sname == null ? "?" : sname.toString();
        Stance st = (c.getNodeOrNull(new NodePath("Stances/" + stName)) instanceof Stance sn)
                ? sn : null;
        String aim = st == null ? "?"
                : st.isSpineAimEnabled() ? "spine"
                : st.isShoulderAimEnabled() ? "shoulder"
                : "NONE";
        String yaw = st == null ? "?" : String.format("%.0f", st.getAimYawLimit());
        return String.format("%s: %-22s stance %-12s aim %-8s yaw+-%s  holding %s%s\n",
                label, driver,
                stName, aim, yaw, heldName(c),
                c.currentVehicleNode != null ? "  [IN VEHICLE]" : "");
    }

    private static StanceName stanceForOrdinal(int i) {
        StanceName[] all = StanceName.values();
        return (i >= 0 && i < all.length) ? all[i] : null;
    }

    /** {@link #armStand} for an arbitrary body, so the mannequin gets a gun too. */
    private void armStandNode(Node body) {
        Skeleton3D skel = findSkeleton(body);
        if (skel != null) armStandOn(body, skel);
    }

    /** Elevation of a direction, degrees; + is up. */
    private double pitchOf(Vector3 v) {
        double len = v.length();
        if (len < 1e-5) return Double.NaN;
        return Math.toDegrees(Math.asin(v.getY() / len));
    }

    /**
     * A {@link BoneAttachment3D} given a bone name the skeleton does not have does NOT error -- it
     * simply sits at the skeleton's origin, so every measurement taken through it comes back
     * plausible and wrong. (This rig's head bone is {@code head_2}; asking for {@code head} put the
     * "chest" vector at the character's feet and read as a chest pitched 87 degrees down, in every
     * stance.) So the index is checked here, once, loudly.
     */
    private BoneAttachment3D attach(Skeleton3D skel, String bone) {
        BoneAttachment3D a = new BoneAttachment3D();
        a.setName("dbg_" + bone);
        skel.addChild(a);
        a.setBoneName(bone);
        if (a.getBoneIdx() < 0) {
            GD.INSTANCE.printErr("AimDebugHost: skeleton has no bone '" + bone
                    + "' — every measurement through it would be taken at the skeleton origin");
        }
        return a;
    }

    private Skeleton3D findSkeleton(Node n) {
        if (n instanceof Skeleton3D s) return s;
        for (Node c : n.getChildren()) {
            Skeleton3D r = findSkeleton(c);
            if (r != null) return r;
        }
        return null;
    }

    private Node findByName(Node n, String name) {
        if (name.equals(n.getName().toString())) return n;
        for (Node c : n.getChildren()) {
            Node r = findByName(c, name);
            if (r != null) return r;
        }
        return null;
    }

    /** World yaw of a horizontal direction, degrees; 0 = +Z, 90 = +X. */
    private double yaw(Vector3 v) {
        double x = v.getX(), z = v.getZ();
        if (x * x + z * z < 1e-8) return Double.NaN;
        return Math.toDegrees(Math.atan2(x, z));
    }

    private double wrap(double d) {
        while (d > 180) d -= 360;
        while (d < -180) d += 360;
        return d;
    }

    private String deg(double d) {
        return Double.isNaN(d) ? "   n/a" : String.format("%7.1f", d);
    }


    // ── Driven self-test (autoDrive) ─────────────────────────────────────────────────────────
    //
    // Every case presses real keys and moves a real mouse through the `Input` singleton, so the
    // shipped PlayerController -> Character -> MovementController path is what runs; nothing is
    // stubbed and no private state is poked. What each case asserts is a DIFFERENCE of two world
    // yaws, never an absolute one, so it is independent of where the stand happens to place the
    // body and of which way the camera happens to start.

    /** What a case measures the hips against. */
    private enum Against { CAMERA, AIM }

    /**
     * Which axis a case is about: the body's yaw frame, where the gun points in elevation, or
     * which corner of the locomotion blendspace the legs were sent to.
     */
    private enum Axis { YAW, PITCH, STRAFE, AI, LIMIT, WEIGHT }

    private static final class Case {
        final String name;
        final String stance;      // "" | "crouch" | "crawl"
        final String move;        // "" | forward | back | left | right
        final boolean combat;
        final double mouseDx;     // raw pixels injected once at case start, to move the view
        final double mouseDy;     // raw pixels of pitch; +Y looks DOWN, as a mouse does
        final Against against;
        final double expected;    // degrees of (measured - reference)
        final Axis axis;
        final Vector2 blend;      // STRAFE/AI: the blendspace corner the legs must be sent to, or null
        final Vector3 ball;       // AI only: where the aim ball sits, relative to the AI subject
        final Vector3 aiMove;     // AI only: world-space walk intent written into the command
        // LIMIT only: the band the CHEST must stay inside, or NaN where the chest does not carry
        // the aim (crawl pins it -- see ShoulderAimModifier).
        double chestMax = Double.NaN, chestMin = Double.NaN;
        Case(String name, String stance, String move, boolean combat, double mouseDx, double mouseDy,
             Against against, double expected, Axis axis, Vector2 blend, Vector3 ball, Vector3 aiMove) {
            this.name = name; this.stance = stance; this.move = move; this.combat = combat;
            this.mouseDx = mouseDx; this.mouseDy = mouseDy; this.against = against;
            this.expected = expected; this.axis = axis; this.blend = blend;
            this.ball = ball; this.aiMove = aiMove;
        }
        Case chest(double lo, double hi) { this.chestMin = lo; this.chestMax = hi; return this; }
        static Case yaw(String name, String stance, String move, boolean combat, double mouseDx,
                        Against against, double expected) {
            return new Case(name, stance, move, combat, mouseDx, 0.0, against, expected, Axis.YAW,
                    null, null, null);
        }
        static Case pitch(String name, String stance, double mouseDy) {
            return new Case(name, stance, "", true, 0.0, mouseDy, Against.AIM, 0.0, Axis.PITCH,
                    null, null, null);
        }
        /**
         * A stance's own elevation limit. Saturating on purpose: the mouse delta is far larger
         * than any range, so wherever the view was, one frame of it lands on the limit -- which
         * makes these cases absolute rather than relative and frees them from the
         * {@code appliedPitchPixels} bookkeeping the {@link #pitch} pairs need. That is also why
         * they run LAST: they leave the view saturated.
         */
        static Case limit(String name, String stance, double mouseDy, double expectedPitchDeg) {
            return new Case(name, stance, "", true, 0.0, mouseDy, Against.AIM, expectedPitchDeg,
                    Axis.LIMIT, null, null, null);
        }

        /**
         * A {@link ShoulderAimModifier#drivenWeights} setting, held at a saturated aim so the
         * split is measured where it is largest. Reports, never asserts: the shipped weight is a
         * measured trade between the shoulder's pose and the gun's direction (see measureWeight),
         * and a number picked from this sweep is what the assertion in the LIMIT cases guards.
         */
        static Case weight(String name, String stance, double mouseDy, double w) {
            return new Case(name, stance, "", true, 0.0, mouseDy, Against.AIM, w, Axis.WEIGHT,
                    null, null, null);
        }

        static Case strafe(String name, String stance, String move, double mouseDx, Vector2 blend) {
            return new Case(name, stance, move, true, mouseDx, 0.0, Against.AIM, 0.0, Axis.STRAFE,
                    blend, null, null);
        }
        static Case ai(String name, Vector3 ball, Vector3 aiMove, Vector2 blend) {
            return new Case(name, "", "", true, 0.0, 0.0, Against.AIM, 0.0, Axis.AI,
                    blend, ball, aiMove);
        }
    }

    /**
     * How long a case is held before it is measured. `MovementController` turns the mesh with a
     * `lerpAngle` at `rotationSpeed` (8/s), so a 180 degree case needs roughly a second; anything
     * shorter measures the turn rather than the result.
     */
    private static final double CASE_SECONDS = 1.6;

    /** Degrees of slack. Only the mesh lerp's asymptotic tail is in here now -- see the note at the assert. */
    private static final double TOLERANCE_DEG = 3.0;

    /**
     * Fraction of a view-elevation swing the chest must pick up. Deliberately far below 1.0: the
     * spine look-at is one bone in an animated chain and is not expected to track the view exactly
     * -- this asks only whether it tracks it AT ALL, which is the difference between the modifier
     * being on and being switched off.
     */
    private static final double PITCH_FOLLOW_MIN = 0.30;

    /**
     * How far the GUN may point from the aim, degrees.
     *
     * <p>Generous on purpose. This is a COSMETIC bound: the bullet is traced from the muzzle to the
     * sight point (FirearmItem's two-stage resolution), so the shot converges on the crosshair
     * whatever the barrel's visual angle. What it catches is a stance whose arms do not carry the
     * weapon to the aim at all. Measured baseline, identical across every stance: 1.5 deg aiming
     * up, 5.9-6.1 aiming down -- crawl, which moves no spine at all and gets its whole aim from
     * the clavicles, matches upright to 0.2 deg.
     */
    private static final double GUN_OFF_AIM_MAX = 12.0;

    /**
     * Slack on a blend corner. The blend is TWEENED over
     * {@code AnimationController.animationBlendDuration} (0.25 s) and a case is held for
     * {@link #CASE_SECONDS}, so it has long since arrived; this only covers the tween's tail.
     */
    private static final double BLEND_TOLERANCE = 0.05;



    private static final String[] ALL_ACTIONS = {"forward", "back", "left", "right", "aim", "crouch", "crawl", "weapon_slot_1"};

    private List<Case> cases;
    private int caseIndex = -1;
    private double caseTimer;
    private int failures;
    private int pitchFailures;
    private int limitFailures;
    private int weightFailures;
    private ShoulderAimModifier shoulderModCached;
    private int strafeFailures;
    private int aiFailures;
    /**
     * Degrees the AI's CAMERA rig may sit off the aim bearing it is tracking.
     *
     * <p>This was the W3 BASELINE — reported and not asserted, because the rig pointed
     * {@code 180 - bodyYaw} degrees away from its own aim target (measured 90.0 at this stand's
     * spawn yaw of 90, 179.3 at a spawn yaw of 0) and asserting it would have left the gate
     * permanently red. Both causes are gone: {@code TPSCameraController} states the rig's world
     * basis every frame instead of inheriting a frame frozen by {@code setAsTopLevel}, and the two
     * 180-degree Y flips are out of {@code Character.tscn}, so {@code AICharacter.tscn} no longer
     * needs the {@code Pivot} override that cancelled only one of them. **0.8 deg now**, and it is
     * an assertion.
     *
     * <p>Looser than {@link #TOLERANCE_DEG} because it is a different mechanism: the mesh is turned
     * by a lerp that has settled long before the case is read, while
     * {@code AICameraController.gatherLookInput} slews at a fixed
     * {@code aim_tracking_degrees_per_sec} (90 on this rig) and so approaches the bearing
     * asymptotically. The strafe cases also walk the subject, which moves the bearing under it.
     */
    private static final double AI_CAM_TOLERANCE_DEG = 6.0;

    /** Worst AI camera-vs-aim error seen this run, summarised at the end. */
    private double aiCamWorst = 0.0;
    private double appliedPitchPixels;
    private double pairViewPitch, pairChestPitch, pairHeadPitch;

    private void buildCases() {
        cases = new ArrayList<>();
        // Out of combat the mesh faces where it WALKS, so each key names a direction relative to
        // the view. These four are the report: "input fwd -> face back, move right".
        for (double dx : new double[] {0.0, -530.0}) {   // two view headings; the second is not a round angle
            String tag = dx == 0.0 ? "" : " (turned)";
            cases.add(Case.yaw("walk forward" + tag, "", "forward", false, dx, Against.CAMERA, 0.0));
            cases.add(Case.yaw("walk back" + tag, "", "back", false, dx, Against.CAMERA, 180.0));
            cases.add(Case.yaw("walk right" + tag, "", "right", false, dx, Against.CAMERA, -90.0));
            cases.add(Case.yaw("walk left" + tag, "", "left", false, dx, Against.CAMERA, 90.0));
        }
        // In combat the mesh faces the AIM POINT, not the camera -- the two differ by the
        // over-the-shoulder parallax, which is why the reference changes here rather than the
        // tolerance widening. Standing still on purpose: a moving combat case also picks a strafe
        // clip, and those clips carry up to 63 degrees of hip swing of their own (a content fact,
        // measured, see CLAUDE.md) which would drown the frame error this is looking for.
        cases.add(Case.yaw("aim standing, upright", "", "", true, 0.0, Against.AIM, 0.0));
        cases.add(Case.yaw("aim standing, upright (turned)", "", "", true, 420.0, Against.AIM, 0.0));
        cases.add(Case.yaw("aim standing, crouch", "crouch", "", true, 0.0, Against.AIM, 0.0));
        cases.add(Case.yaw("aim standing, crawl", "crawl", "", true, 0.0, Against.AIM, 0.0));

        // ELEVATION is a RESPONSE measurement, not an absolute one. There is no single "correct"
        // chest pitch to compare against -- the aim marker is 2 km out and rides a moving camera,
        // and the muzzle belongs to whichever weapon happens to be equipped. What is unambiguous
        // is whether the chest FOLLOWS the view: swing the view from up to down by a known amount
        // and see how much of that the chest picks up. All three of these stances track it now
        // (0.94/0.94/0.96); a stance declaring Stance.spineAimEnabled == false has the
        // SpineAimModifier switched off by AnimationController.updateAimModifiers and holds its
        // clip's authored elevation instead -- which is what crouch and crawl used to do, and was
        // the "gun points at the floor" report. DriveCarrier and Swim still opt out that way and
        // are not covered here. See AIM_PLAN.md W1.
        for (String stance : new String[] {"", "crouch", "crawl"}) {
            String label = stance.isEmpty() ? "upright" : stance;
            cases.add(Case.pitch("aim up, " + label, stance, -360.0));     // ~25 deg above level
            cases.add(Case.pitch("aim down, " + label, stance, 360.0));    // ~25 deg below level
        }

        // COMBAT STRAFE (AIM_PLAN.md W2). In combat the mesh faces the aim point and the legs
        // strafe under it, so which corner of the blendspace they are sent to is the whole
        // question -- and it is one the yaw cases above deliberately cannot ask, because they
        // stand still to keep the clips' own hip swing out of the measurement.
        //
        // Assert the blend INPUT, not the hips: the upright walk_left/walk_right clips are
        // turn-and-walk clips carrying up to 63 degrees of hip yaw of their own (a content fact,
        // measured -- see CLAUDE.md's per-corner table), so a hips reading cannot separate "the
        // wrong clip was chosen" from "the right clip is authored that way".
        //
        // Two view headings, and the second is not a round angle: the defect this covers is a
        // FRAME error, so it is invisible wherever the frames happen to coincide. Walking north
        // used to play the same corner whichever way the character faced.
        for (double dx : new double[] {0.0, -530.0}) {
            String tag = dx == 0.0 ? "" : " (turned)";
            cases.add(Case.strafe("strafe forward" + tag, "", "forward", dx, new Vector2(0, 1)));
            cases.add(Case.strafe("strafe back" + tag, "", "back", dx, new Vector2(0, -1)));
            cases.add(Case.strafe("strafe right" + tag, "", "right", dx, new Vector2(1, 0)));
            cases.add(Case.strafe("strafe left" + tag, "", "left", dx, new Vector2(-1, 0)));
        }
        // The AI writes its direction in world space and used to rotate it by the camera rig's
        // local yaw, so it needs its own coverage -- crouch shares the player path but a different
        // blendspace node, which is the cheapest way to catch a per-stance parameter-path typo.
        cases.add(Case.strafe("strafe forward, crouch", "crouch", "forward", 0.0, new Vector2(0, 1)));
        cases.add(Case.strafe("strafe right, crouch", "crouch", "right", 0.0, new Vector2(1, 0)));

        // ── THE AI SUBJECT (AIM_PLAN.md W3's prerequisite) ────────────────────────────────────
        // Everything above drives a Player, and the two camera rigs are DIFFERENT SHAPES:
        // AICharacter.tscn overrides Pivot back to identity while the player rig carries two
        // cancelling 180-degree flips. So the AI aim path is a separate thing that happens to work,
        // and W3 -- one world-space control rotation, no compensation terms -- moves it. This
        // records the baseline BEFORE anything moves, which is the step the previous (reverted)
        // aim session did not have.
        //
        // Two assertions per case, because they are different owners: the MESH must face the aim
        // point (MovementController.aimYaw) and the CAMERA rig must point at it
        // (AICameraController.gatherLookInput, which owns the AimRay the AimTarget marker hangs
        // off). An AI whose camera lags still faces correctly, and vice versa.
        if (aiSubjectPresent()) {
            cases.add(Case.ai("ai aim north", AI_BALL_NORTH, null, null));
            cases.add(Case.ai("ai aim east", AI_BALL_EAST, null, null));
            cases.add(Case.ai("ai aim south", AI_BALL_SOUTH, null, null));
            cases.add(Case.ai("ai aim west", AI_BALL_WEST, null, null));
            // The AI's own half of W2: it writes its walk direction in WORLD space and used to have
            // the blend rotate that by the camera rig's LOCAL yaw. Ball due north, so the mesh
            // faces world -Z and each world move maps onto a named corner. WALK, not sprint, so the
            // subject stays near the ball it is measured against.
            cases.add(Case.ai("ai strafe forward", AI_BALL_NORTH, new Vector3(0, 0, -1), new Vector2(0, 1)));
            cases.add(Case.ai("ai strafe right", AI_BALL_NORTH, new Vector3(1, 0, 0), new Vector2(1, 0)));
            cases.add(Case.ai("ai strafe back", AI_BALL_NORTH, new Vector3(0, 0, 1), new Vector2(0, -1)));
            cases.add(Case.ai("ai strafe left", AI_BALL_NORTH, new Vector3(-1, 0, 0), new Vector2(-1, 0)));
        }

        // ── PER-STANCE ELEVATION LIMIT ────────────────────────────────────────────────────────
        // The stance owns how far the view may pitch (Stance.viewPitchMax/viewPitchMin), and it
        // owns it BECAUSE the bones follow the view: the aim target is where the view ray lands,
        // so a limit anywhere else is a second owner of the same fact and reads as the gun
        // disagreeing with the crosshair. What that buys is the thing this asserts -- a prone
        // character cannot be made to fold its neck back 80 degrees by looking straight up.
        //
        // LAST on purpose: these saturate the view (see Case.limit), so any case after one of
        // them would measure a body still looking at the sky.
        // TWO assertions per case, because there are two limits and they are deliberately
        // different numbers: the VIEW stops at Stance.viewPitch* (a player must still be able to
        // look at a rooftop) and the CHEST stops at Stance.aimPitch* (a trunk arches about 45 deg,
        // not 80). Asserting only the view is what let the 1:1 chest through -- the chest bound is
        // the half that says the BODY held a pose a body can hold. NaN for crawl: it pins the
        // chest at -87 and spends the aim in the neck and clavicles, so a chest band is the wrong
        // question there (the pitch FOLLOW pair above is the one that covers it).
        cases.add(Case.limit("limit up, upright", "", -12000.0, 60.0).chest(-999, 45.0));
        cases.add(Case.limit("limit down, upright", "", 12000.0, -75.0).chest(-65.0, 999));
        cases.add(Case.limit("limit up, crouch", "crouch", -12000.0, 55.0).chest(-999, 40.0));
        cases.add(Case.limit("limit down, crouch", "crouch", 12000.0, -70.0).chest(-60.0, 999));
        cases.add(Case.limit("limit up, crawl", "crawl", -12000.0, 40.0));
        cases.add(Case.limit("limit down, crawl", "crawl", 12000.0, -35.0));

        // ── THE CLAVICLE SPLIT (report only) ──────────────────────────────────────────────────
        // Prone, the delta is measured from a chest that points at the FLOOR, so the rotation
        // handed to the collarbones is ~120 deg, not the ~30 deg of aim elevation -- which is what
        // reads as a dislocated shoulder. drivenWeights lets the neck take its full share while
        // the clavicles take less. There is no free lunch: the gun rides hand_r under clavicle_r,
        // so what comes off the shoulder comes back as gun-off-aim. This sweep is that curve,
        // measured at the saturated aim where it is worst.
        // Ascending, so the sweep ENDS on the shipped value and cannot leave the rig dialled down
        // for anything that runs after it. The 1.0 row is the one that asserts.
        for (double w : new double[] {0.0, 0.5, 1.0}) {
            cases.add(Case.weight(String.format("clav w=%.1f, crawl", w), "crawl", -12000.0, w));
        }
    }

    /** True when the driven test has an AI subject to measure -- see {@link #buildAiSubject}. */
    private boolean aiSubjectPresent() {
        return mannequin != null && pose != null && aimBall != null;
    }

    private void releaseAll() {
        for (String a : ALL_ACTIONS) Input.INSTANCE.actionRelease(new StringName(a));
    }

    private void enterCase(Case c) {
        releaseAll();
        // Put the body back on the spot. A case that walks holds its key for CASE_SECONDS at sprint
        // speed, and there are enough of them now to leave the 120 m floor -- at which point the
        // body falls and every later case measures a fall. Position only: the body's YAW is what
        // the cases are measured against, and it is deliberately never touched after spawn (see
        // spawnPlayer -- rotating a body after add_child is the defect this stand was built for).
        if (player != null) player.setPosition(new Vector3(0, 1.0, 0));
        if (!c.stance.isEmpty()) Input.INSTANCE.actionPress(new StringName(c.stance), 1.0f);
        if (!c.move.isEmpty()) Input.INSTANCE.actionPress(new StringName(c.move), 1.0f);
        if (c.combat) Input.INSTANCE.actionPress(new StringName("aim"), 1.0f);
        // Mouse motion is a DELTA and the view keeps whatever it accumulated, so a case asking for
        // an absolute elevation must inject only the difference from the case before it. (Yaw is
        // deliberately still relative -- those cases only need the view to be somewhere different.)
        boolean saturating = c.axis == Axis.LIMIT || c.axis == Axis.WEIGHT;
        double dy = saturating ? c.mouseDy : c.mouseDy - appliedPitchPixels;
        if (!saturating) appliedPitchPixels = c.mouseDy;
        if (c.mouseDx != 0.0 || dy != 0.0) {
            InputEventMouseMotion mm = new InputEventMouseMotion();
            mm.setRelative(new Vector2((float) c.mouseDx, (float) dy));
            Input.INSTANCE.parseInputEvent(mm);
        }
        // The aim pose needs a firearm in hand; slot 0 is the fist.
        if (c.axis == Axis.PITCH || c.axis == Axis.LIMIT || c.axis == Axis.WEIGHT) {
            Input.INSTANCE.actionPress(new StringName("weapon_slot_1"), 1.0f);
        }
        if (c.axis == Axis.WEIGHT) applyClavicleWeight(c.expected);
        if (c.axis == Axis.AI) enterAiCase(c);
        caseTimer = 0.0;
    }

    /**
     * Put the aim ball where the case wants it and write the walk intent into the AI subject's
     * command. The subject is re-homed for the same reason the Player is: a walking case would
     * otherwise leave the ball's bearing drifting case after case.
     */
    private void enterAiCase(Case c) {
        if (!aiSubjectPresent()) return;
        mannequin.setPosition(AI_HOME);
        aimBall.setPosition(AI_HOME.plus(c.ball));
        pose.wantCombat = true;
        pose.desiredStance = StanceName.UPRIGHT;
        pose.desiredWeapon = 1;                     // slot 0 is the fist -- no Muzzle to measure
        pose.move = c.aiMove == null ? Vector3.Companion.getZERO() : c.aiMove;
        // WALK, not SPRINT: 1.6 s of sprint carries the subject far enough that the ball's bearing
        // -- which every assertion here is measured against -- swings while the case runs.
        pose.movementType = c.aiMove == null ? MovementType.IDLE : MovementType.WALK;
    }

    /** Returns true while the driver is still running, so _process knows to skip the human readout. */
    private boolean driveStep(double delta, double camYaw, double aimYaw, double hipsYaw, double shldYaw,
                              double bodyYaw, double meshYaw) {
        if (cases == null) buildCases();
        if (caseIndex >= cases.size()) return false;

        if (caseIndex < 0) {
            // One settle pass before the first case: the visuals, the stance colliders and the
            // first weapon equip all land over the opening frames.
            caseTimer += delta;
            if (caseTimer < 1.0) return true;
            caseIndex = 0;
            GD.INSTANCE.print("[AimDebug] driven self-test: " + cases.size() + " cases, "
                    + CASE_SECONDS + "s each, tolerance " + TOLERANCE_DEG + " deg");
            enterCase(cases.get(0));
            return true;
        }

        // The subject is driven every frame, not just at case entry: the aim needs BOTH halves
        // (see driveMannequinAim) and the weapon equip has to be retried until it takes.
        if (aiSubjectPresent()) {
            driveMannequinAim();
            armStandNode(mannequin);
        }

        caseTimer += delta;
        if (caseTimer < CASE_SECONDS) return true;

        Case c = cases.get(caseIndex);
        if (c.axis == Axis.PITCH) {
            measurePitch(c);
            advance();
            return caseIndex < cases.size();
        }
        if (c.axis == Axis.STRAFE) {
            measureStrafe(c, camYaw, aimYaw, bodyYaw, meshYaw);
            advance();
            return caseIndex < cases.size();
        }
        if (c.axis == Axis.AI) {
            measureAi(c);
            advance();
            return caseIndex < cases.size();
        }
        if (c.axis == Axis.LIMIT) {
            measureLimit(c);
            advance();
            return caseIndex < cases.size();
        }
        if (c.axis == Axis.WEIGHT) {
            measureWeight(c);
            advance();
            return caseIndex < cases.size();
        }
        double reference = c.against == Against.AIM ? aimYaw : camYaw;
        // Assert on where the CODE points the character (meshRoot), and report where the SKELETON
        // ended up (hips) beside it. They are different owners: MovementController writes meshRoot,
        // and the locomotion clip then yaws the hips within it by whatever that clip was authored
        // with -- the crawl aim pose carries 8.2 degrees of its own, the upright walk_left/right
        // clips up to 63 (measured; see CLAUDE.md's per-corner table). Folding both into one
        // number means an authored pose can fail a frame check, or mask one.
        double meshFacingNow = wrap(bodyYaw + meshYaw + 180.0);
        double actual = wrap(meshFacingNow - reference);
        double error = Math.abs(wrap(actual - c.expected));
        boolean ok = error <= TOLERANCE_DEG;
        if (!ok) failures++;
        // meshRoot is where the CODE points the character; hips is where the SKELETON ended up.
        // Printing both separates "MovementController aimed the mesh wrong" from "the clip yaws
        // the whole skeleton off the mesh" -- two different owners, indistinguishable from the
        // hips number alone.
        GD.INSTANCE.print(String.format(
                "[AimDebug] %-4s %-32s mesh-%s = %7.1f  expected %6.1f  (err %4.1f)"
                        + "  | clip hips-mesh %6.1f  shldr-hips %6.1f",
                ok ? "PASS" : "FAIL", c.name, c.against == Against.AIM ? "aim" : "cam",
                actual, c.expected, error,
                wrap(hipsYaw - meshFacingNow), wrap(shldYaw - hipsYaw)));
        advance();
        return caseIndex < cases.size();
    }

    /**
     * Which corner of the locomotion blendspace the legs were sent to (AIM_PLAN.md W2).
     *
     * <p>Read straight off the AnimationTree parameter {@code AnimationController} writes, so this
     * asserts the CODE's decision. The alternative -- looking at the skeleton -- cannot work here:
     * the upright walk_left/walk_right clips are turn-and-walk clips carrying up to 63 degrees of
     * hip yaw of their own, so a hips reading confuses "the wrong clip was chosen" with "the right
     * clip is authored that way". Same separation the yaw cases make between meshRoot and hips.
     *
     * <p>Also prints the mesh's facing against the aim, because the two failures look identical
     * from the corner alone: a body facing the wrong way strafes into the correct corner of a
     * frame that is itself rotated.
     */
    private void measureStrafe(Case c, double camYaw, double aimYaw, double bodyYaw, double meshYaw) {
        Vector2 actual = blendPosition(c.stance);
        boolean ok = actual != null
                && Math.abs(actual.getX() - c.blend.getX()) <= BLEND_TOLERANCE
                && Math.abs(actual.getY() - c.blend.getY()) <= BLEND_TOLERANCE;
        if (!ok) strafeFailures++;
        double meshFacingNow = wrap(bodyYaw + meshYaw + 180.0);
        GD.INSTANCE.print(String.format(
                "[AimDebug] %-4s %-32s blend = %s  expected (%.0f, %.0f)  | mesh-aim %6.1f  mesh-cam %6.1f",
                ok ? "PASS" : "FAIL", c.name,
                actual == null ? "<no AnimationTree>"
                        : String.format("(%5.2f, %5.2f)", actual.getX(), actual.getY()),
                c.blend.getX(), c.blend.getY(),
                wrap(meshFacingNow - aimYaw), wrap(meshFacingNow - camYaw)));
    }

    private Vector2 blendPosition(String stance) {
        return blendPosition(player, stance);
    }

    /** The live blend_position of the stance's own MovementBlend node, or null if unreadable. */
    private Vector2 blendPosition(Node body, String stance) {
        Node at = findByName(body, "AnimationTree");
        if (!(at instanceof AnimationTree tree)) return null;
        String node = stance.isEmpty() ? "Upright"
                // java.lang.Character -- com.openworld.character.Character is imported here.
                : java.lang.Character.toUpperCase(stance.charAt(0)) + stance.substring(1);
        java.lang.Object v = tree.get("parameters/" + node + "MovementBlend/blend_position");
        return (v instanceof Vector2 v2) ? v2 : null;
    }

    /**
     * The AI subject, measured the way the Player is (AIM_PLAN.md W3's prerequisite).
     *
     * <p>Two owners, asserted separately. The MESH is turned by {@code MovementController.aimYaw()}
     * from the {@code AimTarget} marker; the CAMERA RIG is turned by
     * {@code AICameraController.gatherLookInput} and owns the {@code AimRay} that marker hangs off.
     * They can disagree — an AI whose camera lags still faces correctly — and W3 moves both, so
     * folding them into one number would hide half of any regression.
     *
     * <p>The camera gets its own, looser bound: this rig tracks at
     * {@code aim_tracking_degrees_per_sec} (90 on {@code AICharacter.tscn}), so it approaches the
     * bearing asymptotically rather than snapping to it, and the subject walks during the strafe
     * cases which moves the bearing under it.
     */
    private void measureAi(Case c) {
        if (!aiSubjectPresent()) return;
        Vector3 bodyPos = mannequin.getGlobalPosition();
        Vector3 ballPos = aimBall.getGlobalPosition();
        double ballYaw = yaw(ballPos.minus(bodyPos));

        Node3D mr = (Node3D) findByName(mannequin, "MeshRoot");
        double meshFacing = wrap(Math.toDegrees(mannequin.getRotation().getY())
                + (mr == null ? 0.0 : Math.toDegrees(mr.getRotation().getY())) + 180.0);
        double meshErr = Math.abs(wrap(meshFacing - ballYaw));

        Vector2 blend = c.blend == null ? null : blendPosition(mannequin, "");
        boolean blendOk = c.blend == null || (blend != null
                && Math.abs(blend.getX() - c.blend.getX()) <= BLEND_TOLERANCE
                && Math.abs(blend.getY() - c.blend.getY()) <= BLEND_TOLERANCE);

        // The CAMERA rig is a separate owner from the mesh and is asserted separately: the mesh is
        // turned by MovementController.aimYaw() off the AimTarget marker, the rig by
        // AICameraController.gatherLookInput. They can disagree -- an AI whose camera lags still
        // faces correctly -- and W3 moved both, so folding them into one number would hide half of
        // any regression.
        Vector3 camFwd = cameraForwardOf(mannequin);
        double camErr = camFwd.lengthSquared() < 1e-6f
                ? Double.NaN : Math.abs(wrap(yaw(camFwd) - ballYaw));

        boolean ok = meshErr <= TOLERANCE_DEG && blendOk
                && !Double.isNaN(camErr) && camErr <= AI_CAM_TOLERANCE_DEG;
        if (!ok) aiFailures++;
        // The marker the SKELETON aims at. Resolved through the Character, not by name: an
        // AICharacter carries a SECOND node called AimTarget (a debug mesh directly under
        // ActiveCamera), and a depth-first hunt finds whichever comes first -- the same trap
        // activeMuzzle() documents for Muzzle. Character.aimTargetPath is the one in use.
        double markerErr = Double.NaN;
        if (mannequin instanceof Character mc) {
            Vector3 marker = mc.getAimTargetPosition();
            markerErr = marker.minus(ballPos).length();
        }
        double gunErr = gunAimErrorOn(mannequin, aimBall);

        GD.INSTANCE.print(String.format(
                "[AimDebug] %-4s %-32s mesh-ball %6.1f%s"
                        + "  | cam-ball %s  marker-off-ball %s m  gun-off-aim %s",
                ok ? "PASS" : "FAIL", c.name, meshErr,
                c.blend == null ? ""
                        : String.format("  blend %s expected (%.0f, %.0f)",
                                blend == null ? "<none>"
                                        : String.format("(%5.2f, %5.2f)", blend.getX(), blend.getY()),
                                c.blend.getX(), c.blend.getY()),
                Double.isNaN(camErr) ? " n/a " : String.format("%5.1f", camErr),
                Double.isNaN(markerErr) ? " n/a " : String.format("%5.1f", markerErr),
                gunText(gunErr)));
        if (camErr > aiCamWorst) aiCamWorst = camErr;
    }

    private Vector3 cameraForwardOf(Node body) {
        Node3D cam = (Node3D) findByName(body, "ActiveCamera");
        if (cam == null) return Vector3.Companion.getZERO();
        return cam.getGlobalTransform().getBasis().getZ().times(-1.0f);   // a camera looks down -Z
    }

    /**
     * How much of a view-elevation swing the chest actually picks up.
     *
     * <p>Read through {@link BoneAttachment3D} because {@code get_bone_global_pose()} returns the
     * pose BEFORE the skeleton modifiers run, which makes a working SpineAimModifier read as inert.
     *
     * <p><b>Measure the chest's FORWARD, not the spine's UP.</b> This measured spine_03 -> head_2
     * first -- a bone PAIR, which is the right instinct for YAW and was carried over here without
     * re-deriving it. That vector runs UP the neck, and elevation is a useless coordinate for a
     * near-vertical vector: tipping the chest 25 degrees moves an up-axis that starts at 82
     * degrees by almost nothing, so a chest tracking the target PERFECTLY read as FOLLOW 0.15.
     * Measured on the shipped visuals (tools/godot/probe_spine_aim.gd): with the modifier
     * unlimited the up-axis metric reports 0.14 for the very same motion the forward axis reports
     * 1.00 for, in every stance. The forward direction is spine_03's own local +Z, and that is
     * justified by measurement rather than convention -- see {@link #chestForward()}. The up axis is still printed beside it, because
     * an authored pose that pitches the whole torso shows up there and not in the forward.
     */
    private void measurePitch(Case c) {
        double viewPitch = pitchOf(cameraForward());
        double chestPitch = pitchOf(chestForward());
        double spinePitch = pitchOf(spineUpAxis());
        double headPitch = pitchOf(headForward());
        double gunErr = gunAimError();
        if (c.mouseDy < 0) {                       // first half of the pair: just record it
            pairViewPitch = viewPitch;
            pairChestPitch = chestPitch;
            pairHeadPitch = headPitch;
            GD.INSTANCE.print(String.format(
                    "[AimDebug]      %-26s view %6.1f  chest %6.1f  head %6.1f  (spine up %6.1f)"
                            + "  gun-off-aim %s",
                    c.name, viewPitch, chestPitch, headPitch, spinePitch, gunText(gunErr)));
            return;
        }
        double dView = viewPitch - pairViewPitch;
        double dChest = chestPitch - pairChestPitch;
        double dHead = headPitch - pairHeadPitch;
        double follow = Math.abs(dView) < 1e-3 ? 0.0 : dChest / dView;
        double headFollow = Math.abs(dView) < 1e-3 ? 0.0 : dHead / dView;
        // A stance aims with its CHEST or with its SHOULDERS AND HEAD (Stance.spineAimEnabled /
        // shoulderAimEnabled). Either mechanism aiming is a pass -- what must never happen is
        // NEITHER, which is what crouch, crawl, drive and swim all did before AIM_PLAN.md W1.
        boolean aims = follow >= PITCH_FOLLOW_MIN || headFollow >= PITCH_FOLLOW_MIN;
        // A gun that cannot be measured must not silently pass -- an unarmed stand is a gap, and
        // NaN >= anything is false, so spell the two cases apart.
        boolean gunOk = Double.isNaN(gunErr) || gunErr <= GUN_OFF_AIM_MAX;
        boolean ok = aims && gunOk;
        if (!ok) pitchFailures++;
        GD.INSTANCE.print(String.format(
                "[AimDebug] %-4s %-26s view %6.1f  chest %6.1f  head %6.1f  (spine up %6.1f)"
                        + "  | swing: view %6.1f chest %6.1f head %6.1f"
                        + "  FOLLOW chest %5.2f head %5.2f (need >= %.2f)"
                        + "  | chest-view offset: up %+6.1f down %+6.1f  gun-off-aim %s",
                ok ? "PASS" : "FAIL", c.name, viewPitch, chestPitch, headPitch, spinePitch,
                dView, dChest, headPitch - pairHeadPitch,
                follow, headFollow, PITCH_FOLLOW_MIN,
                pairChestPitch - pairViewPitch, chestPitch - viewPitch, gunText(gunErr)));
    }

    /**
     * Does the view stop where the stance says it stops? Measured on the CAMERA, because that is
     * the owner -- the aim marker rides the AimRay, so a camera held at the limit is a bone chain
     * asked for no more than the limit. The chest/head elevation is printed beside it so a limit
     * that is respected but still folds the body is visible in the same line.
     */
    private void measureLimit(Case c) {
        double viewPitch = pitchOf(cameraForward());
        double chestPitch = pitchOf(chestForward());
        double headPitch = pitchOf(headForward());
        double err = viewPitch - c.expected;
        boolean viewOk = Math.abs(err) <= TOLERANCE_DEG;
        boolean chestOk = true;
        String chestNote = "";
        if (!Double.isNaN(c.chestMax)) {
            chestOk = chestPitch <= c.chestMax + TOLERANCE_DEG
                   && chestPitch >= c.chestMin - TOLERANCE_DEG;
            chestNote = String.format(" [chest band %.0f..%.0f]",
                    Math.max(c.chestMin, -90.0), Math.min(c.chestMax, 90.0));
        }
        boolean ok = viewOk && chestOk;
        if (!ok) limitFailures++;
        GD.INSTANCE.print(String.format(
                "[AimDebug] %-4s %-22s view %7.1f  expected %7.1f  err %6.2f | chest %7.1f head %7.1f%s%s",
                ok ? "PASS" : "FAIL", c.name, viewPitch, c.expected, err, chestPitch, headPitch,
                chestNote, chestOk ? "" : "  <<< BODY PAST ITS LIMIT"));
    }

    /** The shoulder-aim modifier on the Player, by node name; null once looked up and absent. */
    private ShoulderAimModifier shoulderMod() {
        if (shoulderModCached == null) {
            Node n = findByName(player, "ShoulderAimModifier");
            shoulderModCached = (n instanceof ShoulderAimModifier m) ? m : null;
        }
        return shoulderModCached;
    }

    private void applyClavicleWeight(double w) {
        ShoulderAimModifier m = shoulderMod();
        if (m == null) return;
        VariantArray<Float> a = new VariantArray<>(Float.class);
        a.add((float) w);          // clavicle_l
        a.add((float) w);          // clavicle_r
        a.add(1.0f);               // neck_01 -- the head always takes its full share
        m.setDrivenWeights(a);
    }

    /**
     * The clavicle-split trade, at a saturated prone aim.
     *
     * <p>Three numbers, and they are the two sides of the choice plus its cause. {@code delta} is
     * how big the rotation being split actually is (prone it is ~120 deg, because it is measured
     * from a chest pointing at the floor — that is WHY this dial exists). {@code shoulder} is the
     * angle between {@code clavicle_r} and its parent {@code spine_03}: reference-free, so it needs
     * no modifier-off baseline, and it rises with the distortion. {@code gun} is what it costs.
     */
    private void measureWeight(Case c) {
        double delta = Double.NaN, shoulder = Double.NaN;
        if (spine3 != null && clavR != null) {
            Vector3 chest = chestForward();
            // Through the Character, never by node name: a body can carry a SECOND node called
            // AimTarget and a depth-first hunt finds whichever comes first (the trap
            // activeMuzzle() and measureAi() both document).
            if (player instanceof Character pc && chest.lengthSquared() > 1e-8) {
                Vector3 toAim = pc.getAimTargetPosition().minus(spine3.getGlobalPosition());
                delta = angleBetween(chest, toAim);
            }
            shoulder = angleBetween(spine3.getGlobalTransform().getBasis().getZ(),
                                    clavR.getGlobalTransform().getBasis().getZ());
        }
        // Only the SHIPPED weight asserts. The other rows are the evidence for it: the trade is
        // worse than 1:1 and has no knee (1.0 -> 0.7 buys 39 deg of shoulder for 36 deg of gun),
        // so there is no setting where dialling the clavicles down is worth taking, and the number
        // that must not regress is the prone gun's.
        double gun = gunAimError();
        boolean shipped = c.expected >= 0.999;
        boolean ok = true;
        if (shipped) {
            ok = !Double.isNaN(gun) && gun <= PRONE_GUN_TOLERANCE_DEG;
            if (!ok) weightFailures++;
        }
        GD.INSTANCE.print(String.format(
                "[AimDebug] %-4s %-22s delta %6.1f  shoulder-vs-chest %6.1f  gun-off-aim %s  head %6.1f%s",
                shipped ? (ok ? "PASS" : "FAIL") : "    ", c.name, delta, shoulder,
                gunText(gun), pitchOf(headForward()),
                shipped ? String.format("  [shipped weight, gun must be <= %.0f]",
                                        PRONE_GUN_TOLERANCE_DEG) : ""));
    }

    /**
     * How far the prone gun may sit off the aim at the shipped clavicle weight. Generous next to
     * the 3 deg yaw tolerance because prone spends the whole aim in the arms and neck from a chest
     * that points at the floor — the measured value is 9.1, and what this guards is the tens of
     * degrees a wrong split costs.
     */
    private static final double PRONE_GUN_TOLERANCE_DEG = 15.0;

    private static double angleBetween(Vector3 a, Vector3 b) {
        if (a.lengthSquared() < 1e-8 || b.lengthSquared() < 1e-8) return Double.NaN;
        double cos = a.normalized().dot(b.normalized());
        return Math.toDegrees(Math.acos(Math.max(-1.0, Math.min(1.0, cos))));
    }

    private Vector3 cameraForward() {
        return cameraForwardOf(player);
    }

    /**
     * Where the chest POINTS: spine_03's own local <b>+Z</b>.
     *
     * <p>Note the sign, which is the opposite of {@link #cameraForward()}'s. A camera looks down
     * -Z, and copying that convention onto a bone reads a perfect track as FOLLOW -0.94. This rig
     * was measured: in the rest pose spine_03's +Z lands on world -Z (the mesh forward) and its +X
     * lands on the clavicle_l - clavicle_r axis, so +Z out of the sternum is the chest forward and
     * the SpineAimModifier's own forward_axis (BONE_AXIS_PLUS_Z, the default) agrees with it.
     */
    private Vector3 chestForward() {
        if (spine3 == null) return Vector3.Companion.getZERO();
        return spine3.getGlobalTransform().getBasis().getZ();
    }

    /**
     * Angle between where the GUN points and where the player is aiming, in degrees.
     *
     * <p>This is the question the chest measurements are a proxy for. The shot is traced from the
     * weapon's own {@code Muzzle} marker to the sight point (FirearmItem's two-stage resolution),
     * and the muzzle rides a BoneAttachment3D on {@code hand_r} -- so it is carried by the ARM
     * pose, which comes from the aim clip through WeaponBlend's filter, on top of a {@code
     * spine_03} the SpineAimModifier has already aimed. A stance whose chest tracks perfectly can
     * still hold the gun off-target if its arms are posed for a different stance, and only this
     * number says so. Reported, not asserted: what an acceptable value is has never been decided.
     *
     * <p><b>It reads {@code n/a} today because this stand is unarmed.</b>
     * CharacterVisuals_GodotChan instances only {@code Fist} under WeaponAttachment (a MeleeItem,
     * which has no {@code Muzzle}); the firearms are added elsewhere. Arming the stand -- instance
     * AR4 under {@code MarkerAR4} and equip it through WeaponController -- is what turns this into
     * a real assertion, and is the thing to do BEFORE authoring any per-stance aim clip, since the
     * gun angle is the only number that says whether such a clip is needed. See AIM_PLAN.md W4.
     */
    /**
     * The muzzle of the weapon the body is actually HOLDING.
     *
     * <p>Not the first {@code Muzzle} in the subtree: {@code WeaponAttachment} carries a marker per
     * weapon type and more than one can hold an instance, so a depth-first search returns whichever
     * happens to come first in the scene. That read 91.6 deg off the aim on a body whose chest was
     * measured 4.6 deg off it -- a stowed gun, not a mis-aimed one. Ask the WeaponController.
     */
    private Node3D activeMuzzle(Node body) {
        if (body instanceof Character c && c.weaponController != null) {
            WeaponItem held = c.weaponController.getCurrentWeaponItem();
            if (held != null && held.getNodeOrNull(new NodePath("Muzzle")) instanceof Node3D m) {
                return m;
            }
            // else: holding something with no muzzle (fist, knife) -- fall through
        }
        // Fallback: the rifle armStand() parks in MarkerAR4. That marker IS the authored hold
        // offset for an AR4 and hangs off the hand_r BoneAttachment3D, so a gun sitting in it is
        // exactly where a held one would be -- which is what makes it a fair thing to measure
        // while the body happens to be holding a fist (slot 0). Deterministic, unlike a
        // depth-first hunt for any "Muzzle" in the subtree.
        Node marker = findByName(body, "MarkerAR4");
        if (marker != null && findByName(marker, "Muzzle") instanceof Node3D m) return m;
        return null;
    }

    private String heldName(Node body) {
        if (body instanceof Character c && c.weaponController != null) {
            WeaponItem w = c.weaponController.getCurrentWeaponItem();
            return w == null ? "<none>" : w.getName().toString();
        }
        return "?";
    }

    /** "n/a (unarmed)" rather than NaN -- a missing muzzle is a fact about the stand, not a value. */
    private String gunText(double deg) {
        return Double.isNaN(deg) ? "n/a (unarmed stand)" : String.format("%5.1f", deg);
    }

    private double gunAimError() {
        return gunAimErrorOn(player, (Node3D) findByName(player, "AimTarget"));
    }

    private double gunAimErrorOn(Node body, Node3D aim) {
        Node3D muzzle = activeMuzzle(body);
        if (muzzle == null || aim == null) return Double.NaN;
        Vector3 toAim = aim.getGlobalPosition().minus(muzzle.getGlobalPosition());
        // The muzzle marker's own forward is its -Z, the same convention every Marker3D uses here.
        Vector3 fwd = muzzle.getGlobalTransform().getBasis().getZ().times(-1.0f);
        if (toAim.lengthSquared() < 1e-6f || fwd.lengthSquared() < 1e-6f) return Double.NaN;
        double cos = toAim.normalized().dot(fwd.normalized());
        return Math.toDegrees(Math.acos(Math.max(-1.0, Math.min(1.0, cos))));
    }

    /** Where the HEAD points: neck_01's own +Z, the same chest-frame convention as spine_03's. */
    private Vector3 headForward() {
        if (neck == null) return Vector3.Companion.getZERO();
        return neck.getGlobalTransform().getBasis().getZ();
    }

    /** spine_03 -> head_2, the spine's UP axis. Diagnostic only -- see {@link #measurePitch}. */
    private Vector3 spineUpAxis() {
        if (spine3 == null) return Vector3.Companion.getZERO();
        return head.getGlobalPosition().minus(spine3.getGlobalPosition());
    }

    private void advance() {
        caseIndex++;
        if (caseIndex >= cases.size()) {
            releaseAll();
            GD.INSTANCE.print("[AimDebug] yaw " + (yawCases() - failures) + "/" + yawCases()
                    + " passed, pitch " + (pitchCases() - pitchFailures) + "/" + pitchCases()
                    + " passed, strafe " + (strafeCases() - strafeFailures) + "/" + strafeCases()
                    + " passed, ai " + (aiCases() - aiFailures) + "/" + aiCases()
                    + " passed, limit " + (limitCases() - limitFailures) + "/" + limitCases()
                    + " passed, clav " + (1 - weightFailures) + "/1"
                    + " passed");
            if (aiCases() > 0) {
                GD.INSTANCE.print(String.format(
                        "[AimDebug] AI camera worst %.1f deg off its aim (limit %.1f) -- was"
                                + " 90.1 before AIM_PLAN.md W3",
                        aiCamWorst, AI_CAM_TOLERANCE_DEG));
            }
            if (pitchFailures > 0 || strafeFailures > 0 || aiFailures > 0 || limitFailures > 0
                    || weightFailures > 0) {
                GD.INSTANCE.print("[AimDebug] failures above are the OPEN defect -- see AIM_PLAN.md");
            }
            if (getTree() != null) {
                getTree().quit(failures + pitchFailures + strafeFailures + aiFailures
                        + limitFailures + weightFailures == 0 ? 0 : 1);
            }
            return;
        }
        enterCase(cases.get(caseIndex));
    }

    private int countCases(Axis axis) {
        int n = 0;
        for (Case c : cases) if (c.axis == axis) n++;
        return n;
    }

    private int yawCases() { return countCases(Axis.YAW); }

    /** Elevation cases come in up/down PAIRS and only the second half asserts, so a pair is one check. */
    private int pitchCases() { return countCases(Axis.PITCH) / 2; }

    private int strafeCases() { return countCases(Axis.STRAFE); }

    private int aiCases() { return countCases(Axis.AI); }

    private int limitCases() { return countCases(Axis.LIMIT); }

    @Register
    @Override
    public void _process(double delta) {
        if (workbench) workbenchStep(delta);
        if (readout == null || player == null) return;
        if (!bindBones()) {
            readout.setText("AimDebugHost: waiting for the character skeleton…");
            return;
        }

        Node3D activeCam = (Node3D) findByName(player, "ActiveCamera");
        Node3D aimTarget = (Node3D) findByName(player, "AimTarget");
        Node3D meshRoot = (Node3D) findByName(player, "MeshRoot");
        if (activeCam == null || aimTarget == null) {
            readout.setText("AimDebugHost: ActiveCamera/AimTarget not found");
            return;
        }

        Vector3 bodyPos = player.getGlobalPosition();
        Vector3 aimPos = aimTarget.getGlobalPosition();
        // Camera forward without touching a Basis: AimTarget sits down the camera's own -Z.
        double camYaw = yaw(aimPos.minus(activeCam.getGlobalPosition()));
        double aimYaw = yaw(aimPos.minus(bodyPos));

        Vector3 hipAxis = thighL.getGlobalPosition().minus(thighR.getGlobalPosition());
        Vector3 shoulderAxis = clavL.getGlobalPosition().minus(clavR.getGlobalPosition());
        double hipsYaw = yaw(new Vector3(hipAxis.getX(), 0, hipAxis.getZ())
                .normalized().cross(new Vector3(0, 1, 0)));
        double shldYaw = yaw(new Vector3(shoulderAxis.getX(), 0, shoulderAxis.getZ())
                .normalized().cross(new Vector3(0, 1, 0)));

        double bodyYawDeg = Math.toDegrees(player.getRotation().getY());
        double meshYawDeg = meshRoot != null ? Math.toDegrees(meshRoot.getRotation().getY()) : 0.0;
        if (autoDrive && driveStep(delta, camYaw, aimYaw, hipsYaw, shldYaw, bodyYawDeg, meshYawDeg)) {
            readout.setText("AimDebugHost: driven self-test running (see console)");
            return;
        }

        boolean combat = (player instanceof Character c) && c.isCombat();
        double bodyYaw = bodyYawDeg;
        double meshYaw = meshRoot != null ? meshYawDeg : Double.NaN;

        String s = (workbench ? workbenchHeader() : "AIM DEBUG   (WASD move, mouse look, right-mouse aim)\n")
                + "combat (aim/shoot) : " + (combat ? "YES" : "no") + "\n"
                + "\n"
                + "camera forward     : " + deg(camYaw) + "\n"
                + "body -> AimTarget  : " + deg(aimYaw) + "   <- MovementController.aimYaw()\n"
                + "hips               : " + deg(hipsYaw) + "\n"
                + "shoulders          : " + deg(shldYaw) + "\n"
                + "\n"
                + "body.rotation.y    : " + deg(bodyYaw) + "   (scene placement)\n"
                + "meshRoot.rotation.y: " + deg(meshYaw) + "   (what MovementController writes)\n"
                + "\n"
                + "hips  - camera     : " + deg(wrap(hipsYaw - camYaw)) + "   <- THE REPORTED BUG\n"
                + "hips  - aim        : " + deg(wrap(hipsYaw - aimYaw)) + "   (0 = legs face the aim point)\n"
                + "aim   - camera     : " + deg(wrap(aimYaw - camYaw)) + "   <- is the aim point bad?\n"
                + "shldr - hips       : " + deg(wrap(shldYaw - hipsYaw)) + "   <- upper/lower split\n";
        readout.setText(s);

        printTimer += delta;
        if (printInterval > 0 && printTimer >= printInterval) {
            printTimer = 0;
            GD.INSTANCE.print("[AimDebug] combat=" + (combat ? "Y" : "n")
                    + " cam=" + deg(camYaw) + " aim=" + deg(aimYaw)
                    + " hips=" + deg(hipsYaw) + " shldr=" + deg(shldYaw)
                    + " body=" + deg(bodyYaw) + " mesh=" + deg(meshYaw)
                    + " | hips-cam=" + deg(wrap(hipsYaw - camYaw))
                    + " hips-aim=" + deg(wrap(hipsYaw - aimYaw))
                    + " aim-cam=" + deg(wrap(aimYaw - camYaw))
                    + " shldr-hips=" + deg(wrap(shldYaw - hipsYaw)));
            if (workbench && mannequin != null && aimBall != null) {
                Node3D mm = (Node3D) findByName(mannequin, "MeshRoot");
                double toBall = yaw(aimBall.getGlobalPosition().minus(mannequin.getGlobalPosition()));
                // Same convention as driveStep: meshRoot's rotation is LOCAL to the body, and the
                // mesh is -Z FORWARD, so its facing is body + mesh + 180. Comparing a rotation to
                // a direction is how this read -180 while the character faced the ball exactly.
                double mmYaw = mm == null ? Double.NaN
                        : wrap(Math.toDegrees(mannequin.getRotation().getY())
                               + Math.toDegrees(mm.getRotation().getY()) + 180.0);
                GD.INSTANCE.print("[AimDebug] mannequin " + mannequinStance
                        + (pose != null && pose.wantCombat ? " combat" : " relaxed")
                        + " slot=" + mannequinWeapon
                        + " mesh=" + deg(mmYaw) + " ball=" + deg(toBall)
                        + " mesh-ball=" + deg(wrap(mmYaw - toBall))
                        + " gun-off-aim=" + gunText(gunAimErrorOn(mannequin, aimBall))
                        + " combat=" + ((mannequin instanceof Character mc) ? mc.isCombat() : "?")
                        + " holding=" + heldName(mannequin)
                        + " chest-off-ball=" + gunText(boneOffBall(mannequin, "spine_03"))
                        + " head-off-ball=" + gunText(boneOffBall(mannequin, "neck_01")));
            }
        }
    }
}

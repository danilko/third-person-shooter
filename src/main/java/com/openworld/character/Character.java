package com.openworld.character;

import com.openworld.util.CollisionLayers;
import com.openworld.carrier.vehicle.VehicleWeaponMode;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.annotation.Visible;
import godot.api.*;
import godot.core.*;
import godot.global.GD;

import java.util.ArrayList;
import java.util.EnumMap;
import java.util.Map;
import java.util.UUID;
import com.openworld.camera.ControlRotation;
import com.openworld.camera.FPSCameraController;
import com.openworld.camera.TPSCameraController;
import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.control.Controllable;
import com.openworld.control.Controller;
import com.openworld.control.ModalInput;
import com.openworld.control.PlayerController;
import com.openworld.control.UserCommand;
import com.openworld.game.EventBus;
import com.openworld.game.GameManager;
import com.openworld.movement.character.CombatState;
import com.openworld.movement.character.JumpState;
import com.openworld.movement.character.MovementController;
import com.openworld.movement.character.MovementState;
import com.openworld.movement.character.MovementType;
import com.openworld.movement.character.Stance;
import com.openworld.movement.character.StanceName;
import com.openworld.movement.character.SwimState;
import com.openworld.net.NetworkController;
import com.openworld.net.NetworkManager;
import com.openworld.ui.HUDManager;
import com.openworld.weapon.WeaponController;
import com.openworld.weapon.WeaponItem;
import com.openworld.weapon.WeaponType;
import com.openworld.world.SpatialEntityGrid;
import com.openworld.world.manager.ExplosionManager;
import com.openworld.world.manager.ImpactManager;

@Script
public class Character extends CharacterBody3D implements Controllable, NameplateTarget {

    // ── Signals ──────────────────────────────────────────────────────────────
    public final Signal1<JumpState> pressedJump = new Signal1<>(this, new StringName("pressed_jump"));

    public final Signal1<Stance> changedStance = new Signal1<>(this, new StringName("changed_stance"));

    public final Signal0 fireWeapon = new Signal0(this, new StringName("fire_weapon"));

    public final Signal0 notFireWeapon = new Signal0(this, new StringName("not_fire_weapon"));

    public final Signal1<MovementState> changedMovementState = new Signal1<>(this, new StringName("changed_movement_state"));

    public final Signal1<Vector3> changedMovementDirection = new Signal1<>(this, new StringName("changed_movement_direction"));

    public final Signal1<CombatState> changedCombatState = new Signal1<>(this, new StringName("changed_combat_state"));

    public final Signal1<Integer> changedWeapon = new Signal1<>(this, new StringName("changed_weapon"));

    public final Signal0 reloadWeapon = new Signal0(this, new StringName("reload_weapon"));

    public final Signal0 dropWeapon = new Signal0(this, new StringName("drop_weapon"));

    /**
     * Generic {@link NameplateTarget} refresh: emitted whenever name, faction colour, or active
     * weapon changes (faction swap, weapon switch). The nameplate re-reads the getters; health/ammo
     * refresh via the Health/WeaponController node signals.
     */
    public final Signal0 nameplateChanged = new Signal0(this, new StringName("nameplate_changed"));

    // ── Exports ───────────────────────────────────────────────────────────────
    @Export
    public Dictionary<String, JumpState> jumpStates = new Dictionary<>(String.class, JumpState.class);

    @Export
    public Dictionary<String, NodePath> stances = new Dictionary<>(String.class, NodePath.class);

    @Export
    public Dictionary<String, CombatState> combatStates = new Dictionary<>(String.class, CombatState.class);

    @Export
    public CharacterInfo characterInfo;

    /**
     * How long (seconds) the ragdoll simulates before all physics are frozen.
     * 0 or less skips the ragdoll entirely and freezes the mesh at the last
     * animation pose — cheapest option for large crowd scenes.
     */
    @Export
    public float ragdollDuration = 3.0f;

    @Export
    public WeaponController weaponController;

    @Export
    public NodePath cameraRootPath = new NodePath("TPSCameraController");

    @Export
    public NodePath fpsCameraRootPath = new NodePath("FPSCameraController");

    @Export
    public NodePath aimTargetPath = new NodePath("ActiveCamera/AimRay/AimTarget");

    @Export
    public NodePath aimRayPath = new NodePath("ActiveCamera/AimRay");

    @Export
    public NodePath activeCameraPath = new NodePath("ActiveCamera");

    @Export
    public NodePath physicalBoneSimulatorPath = new NodePath("MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/PhysicalBoneSimulator3D");

    /**
     * Packed scene whose root is a {@link CharacterVisuals} node containing the mesh,
     * AnimationTree, and PhysicalBoneSimulator3D.  Instantiated in {@code _ready()} and
     * attached to the {@code VisualsMount} Marker3D.  Swapping this one field swaps the
     * entire character appearance; the embedded {@link MeshConfig} wires all dependent
     * component references automatically.
     */
    @Export
    public PackedScene characterVisuals;

    // ── Protected state ───────────────────────────────────────────────────────
    protected Vector3 movementDirection = new Vector3();
    protected StanceName currentStanceName = StanceName.UPRIGHT;
    protected MovementType currentMovementType = MovementType.IDLE;

    /** True while overlapping a {@code WaterVolume} (PLAN.md I1) — gates the depth-based swim decision. */
    protected boolean inWater = false;
    /** World-space Y of the overlapped water surface; the swim decision compares feet depth against it. */
    protected double waterSurfaceY = 0.0;
    // Floor-probe throttle: the down-ray is re-cast at most every WATER_PROBE_INTERVAL while in water
    // (it never runs out of water) and the depth is cached between casts — swim transitions don't need
    // per-frame precision, so this cuts the raycast rate ~6× while wading. Reset to 0 on water exit so
    // re-entry (and dive-in) probes immediately.
    private static final double WATER_PROBE_INTERVAL = 0.1;
    private double cachedWaterDepth = 0.0;
    private double waterProbeCooldown = 0.0;
    /** Remaining lung air (s) — drains while fully submerged, recovers at the surface (PLAN.md I1). */
    private double currentOxygen = -1.0;   // <0 = uninitialised; lazily set to SwimState.maxOxygen
    /** Accumulates real time once oxygen is empty; deals a drowning tick every drowningInterval. */
    private double drownTimer = 0.0;
    /** Debug aid (PLAN.md I1): when set, the local player shows an on-screen swim/water-depth readout. */
    @Export
    public boolean debugSwim = false;
    private Label swimDebugLabel = null;

    /** Full-screen blue tint shown to the local player while the camera is submerged (PLAN.md I2 follow-up). */
    private CanvasLayer underwaterLayer = null;
    private ColorRect underwaterTint = null;

    // False for AI-controlled characters whose accuracy is managed by their own system.
    // public (not protected): read cross-package by weapon/movement/control collaborators.
    public boolean useWeaponSpread = true;

    // ── Vehicle / drive state ─────────────────────────────────────────────────
    // The outward-visible flags stay here (NetworkController/NetworkManager read them); the
    // transition logic + pre-drive snapshot live in CharacterDriveState (WS5 god-class split).
    public VehicleWeaponMode vehicleWeaponMode = VehicleWeaponMode.NONE;

    /** True while seated as the DRIVER (seat 0) of {@link #currentVehicleNode}; false as a passenger. */
    public boolean vehicleDriver = false;
    /**
     * The Vehicle RigidBody3D this character is currently riding, or null when on foot.
     *
     * <p>{@code @Visible} so "is this body in a seat" is answerable from a probe or the remote
     * debugger. It is the deciding half of {@link #refreshHeadVisibility()} -- a headless driver
     * is exactly this flag disagreeing with {@link #isFpsMode} -- and unobservable state is how
     * that shipped in the first place.
     */
    @Visible
    public Node currentVehicleNode = null;
    final CharacterDriveState driveState = new CharacterDriveState(this);

    // ── Combat/stance state (read by NetworkManager._physicsProcess for MSG_SNAPSHOT gather,
    // applied on remote peers via applyReplicatedCombatAndStance — see NetworkController) ──
    @Export
    public boolean combat = false;

    @Export
    public int stanceOrdinal = StanceName.UPRIGHT.ordinal();

    @Export
    public VariantArray<NodePath> headMeshPaths = new VariantArray<>(NodePath.class);

    protected ArrayList<Node3D> headMeshes = new ArrayList<>();

    /** Last value pushed to {@link #headMeshes}, so {@link #refreshHeadVisibility()} is a no-op when unchanged. */
    private boolean headVisibleApplied = true;

    public Health healthNode;
    protected Marker3D aimTarget;
    public RayCast3D aimRay;

    protected Node3D cameraRoot;
    protected FPSCameraController fpsCameraController;

    /**
     * Which of the on-foot views the shared {@link #activeCamera} is being written from — false =
     * over-the-shoulder TPS, true = first person. Toggled by {@code PlayerCameraController} on the
     * {@code view} action, and by {@link #setCameraMode(boolean)} in code.
     *
     * <p><b>{@code @Visible}, so it is a real registered property</b> ({@code is_fps_mode}). It was
     * a plain Java field, which reads exactly like a registered one from Java and like nothing at
     * all from GDScript: {@code body.get("is_fps_mode")} returned {@code <null>} and
     * {@code body.set(...)} silently did nothing, so a probe that switched to FPS and measured
     * measured the TPS camera and said so with a straight face (AIM_PLAN.md W3's two blind spots).
     * {@code @Visible} rather than {@code @Export} on purpose — it is live view state, not
     * something to author into a scene.
     *
     * <p>Writing it directly is safe: nothing is latched off the transition. Head visibility is
     * re-derived every frame by {@link #refreshHeadVisibility()}.
     */
    @Visible
    public boolean isFpsMode = false;
    public final ControlRotation controlRotation = new ControlRotation();
    public Camera3D activeCamera;
    protected PhysicalBoneSimulator3D physicalBoneSimulator;

    // ── Visuals scene (B2: mesh-swap foundation) ──────────────────────────────
    protected CharacterVisuals visualsInstance;
    protected MeshConfig meshConfig;

    // ── Ragdoll + death visuals (extracted to CharacterRagdoll — WS5 god-class split) ──
    // The settle-timer state lives in the collaborator; _process pumps it via tickFreeze.
    final CharacterRagdoll ragdoll = new CharacterRagdoll(this);
    // Guards the death visuals (anim-tree off + ragdoll) so they run exactly once, whether the
    // body died authoritatively (onDied) or via a replicated zero-health snapshot (applyReplicatedDeath).
    // Protected: Player.applyReplicatedDeath keys its once-only playerDied emission off it;
    // CharacterRagdoll (same package) reads/sets it from enableDeathVisuals.
    protected boolean deathVisualsApplied  = false;

    // ── Stance cache — populated once in _ready() to avoid repeated NodePath traversals ──
    // Package-private: CharacterRagdoll reads it to drop the active stance collider on death.
    final Map<StanceName, Stance> stanceCache = new EnumMap<>(StanceName.class);

    // ── Tick counter (stamped onto every UserCommand for network ordering) ─────
    protected long currentTick = 0;

    // ── Controller (generates UserCommand each tick) ──────────────────────────
    protected Controller controller;

    // ── Non-authority replication writes (extracted to CharacterReplication — WS5 god-class split) ──
    final CharacterReplication replication = new CharacterReplication(this);

    // ── UI input lock (set by radial menu / pause / any overlay that must own the mouse) ──
    public boolean inputBlocked = false;

    // ── Lifecycle ─────────────────────────────────────────────────────────────
    @Register
    @Override
    public void _ready() {
        healthNode = (Health) getNode("Health");
        if (characterInfo == null) characterInfo = new CharacterInfo();
        // Privatize a scene-embedded (shared) CharacterInfo before stamping our id — a .tscn
        // sub-resource is shared across every instantiation of that scene unless copied, so
        // stamping a per-instance UUID onto the shared object would rewrite every sibling's
        // identity. An empty characterId means "scene-supplied" (code-spawned bodies stamp a
        // UUID before addChild); copy it into a fresh instance first. See CharacterInfo.copyOf.
        else if (characterInfo.characterId.isEmpty())
            characterInfo = CharacterInfo.copyOf(characterInfo);
        if (characterInfo.characterId.isEmpty())
            characterInfo.characterId = UUID.randomUUID().toString();
        addToGroup(new StringName("characters"), false);
        // Register in the spatial grid so AI target discovery can query neighbours in O(k)
        // instead of scanning the whole "characters" group (PLAN.md Part D / D1). Stagger the
        // first re-bucket so bodies spawned together don't all update on the same frame.
        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) grid.register(this, getGlobalPosition());
        gridUpdateTimer = GD.randfRange(0f, (float) GRID_UPDATE_INTERVAL);
        if (hasNode(aimTargetPath)) {
            aimTarget = (Marker3D) getNode(aimTargetPath);
        }
        if (hasNode(aimRayPath)) {
            aimRay = (RayCast3D) getNode(aimRayPath);
            // Exclude our own body from the AimRay. The bone exceptions (addPhysicalBoneExceptions)
            // only cover the ragdoll PhysicalBone3D nodes, not the live stance capsule. Firearms
            // never hit self (spread < 0.5deg), but MeleeItem casts this ray through a +/-25deg cone
            // that can tilt onto the character's own capsule and deal self-damage. Excepting the body
            // here is rotation-independent, so it covers every cone ray (and hardens firearm hitscan).
            aimRay.addException(this);
        }
        if (hasNode(cameraRootPath)) {
            cameraRoot = (Node3D) getNode(cameraRootPath);
        }
        if (fpsCameraRootPath != null && !fpsCameraRootPath.isEmpty() && hasNode(fpsCameraRootPath)) {
            Node fpsNode = getNode(fpsCameraRootPath);
            if (fpsNode instanceof FPSCameraController fpsCtrl) {
                fpsCameraController = fpsCtrl;
            }
        }
        if (activeCameraPath != null && !activeCameraPath.isEmpty() && hasNode(activeCameraPath)) {
            activeCamera = (Camera3D) getNode(activeCameraPath);
            // Deferred: camera controllers' _ready() runs bottom-up — BEFORE this
            // method assigns activeCamera (see TPSCameraController.setCameraFov's
            // comment) — so any makeCurrent()/clearCurrent() ownership-gating done
            // there sees a null activeCamera and is a silent no-op. Decide and act
            // here instead, once activeCamera AND characterInfo are both resolved,
            // deferred so every sibling _ready() (NetworkManager spawn wiring, etc.)
            // has finished too — same pattern as emitCharacterSpawned below.
            callDeferred(StringNames.toGodotName("activateCameraIfOwned"));
        }

        // ── Visuals instantiation (B2) ─────────────────────────────────────
        if (characterVisuals != null && hasNode("VisualsMount")) {
            Node mount = getNode("VisualsMount");
            Node vis = characterVisuals.instantiate();
            mount.addChild(vis);
            if (vis instanceof CharacterVisuals cv) {
                visualsInstance = cv;
                meshConfig = cv.meshConfig;
            }
            wireFromMeshConfig();
        } else {
            // Legacy fallback: visuals embedded directly in Character scene.
            for (NodePath headMeshPath : headMeshPaths) {
                if (headMeshPath != null && !headMeshPath.isEmpty() && hasNode(headMeshPath)) {
                    headMeshes.add((Node3D) getNode(headMeshPath));
                }
            }
            if (physicalBoneSimulatorPath != null && !physicalBoneSimulatorPath.isEmpty()
                    && hasNode(physicalBoneSimulatorPath)) {
                physicalBoneSimulator = (PhysicalBoneSimulator3D) getNode(physicalBoneSimulatorPath);
                addPhysicalBoneExceptions(physicalBoneSimulator);
            }
        }

        for (Node child : getChildren()) {
            if (child instanceof Controller c) { controller = c; break; }
        }

        for (StanceName sn : StanceName.values()) {
            NodePath sp = stances.get(sn.getKey());
            if (sp == null) continue;
            Node sn2 = getNodeOrNull(sp);
            if (sn2 instanceof Stance s) stanceCache.put(sn, s);
        }

        changedMovementDirection.emit(Vector3.Companion.getBACK());
        setMovementState(MovementType.IDLE);
        // Init: force-emit the starting stance. setStance is now idempotent (no-ops on
        // same stance), so the initial wiring of colliders/animation goes through
        // forceSetStance which always emits changed_stance.
        forceSetStance(currentStanceName);
        setCombatState();
        setWeapon(0);

        // Deferred so all sibling _ready() calls (e.g. HUDManager, GameManager)
        // finish connecting to characterSpawned before this fires. Fires for every
        // character — player and AI — so multi-character systems (C2) can build
        // characterId-keyed registries instead of assuming a single local player.
        callDeferred(StringNames.toGodotName("emitCharacterSpawned"));

        // Decide our own-nameplate hide by ownership, not the camera — deferred so ownerPeerId is
        // resolved. No-ops for AI and other peers' bodies (they keep their visible nameplate).
        callDeferred(StringNames.toGodotName("applyNameplateVisibility"));
    }

    @Register
    public void emitCharacterSpawned() {
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof com.openworld.game.EventBus bus) bus.characterSpawned.emit(this, characterInfo);
    }

    /**
     * Resolves all mesh-dependent node references from the newly instantiated
     * {@link CharacterVisuals} scene and wires them into sibling components.
     * Called in _ready() immediately after addChild(visualsInstance).
     */
    private void wireFromMeshConfig() {
        if (meshConfig == null || visualsInstance == null) return;

        // ── Physical bone simulator ────────────────────────────────────────
        if (!meshConfig.physicalBoneSimulatorPath.isEmpty()) {
            Node n = visualsInstance.getNodeOrNull(meshConfig.physicalBoneSimulatorPath);
            if (n instanceof PhysicalBoneSimulator3D sim) {
                physicalBoneSimulator = sim;
                addPhysicalBoneExceptions(sim);
            }
        }

        // ── Head meshes (FPS mode visibility) ─────────────────────────────
        headMeshes.clear();
        for (NodePath p : meshConfig.headMeshPaths) {
            if (p == null || p.isEmpty()) continue;
            Node n = visualsInstance.getNodeOrNull(p);
            if (n instanceof Node3D n3d) headMeshes.add(n3d);
        }

        // ── AnimationController ────────────────────────────────────────────
        Node acNode = getNodeOrNull("AnimationController");
        if (acNode instanceof AnimationController ac) {
            if (!meshConfig.animationTreePath.isEmpty()) {
                Node atNode = visualsInstance.getNodeOrNull(meshConfig.animationTreePath);
                if (atNode instanceof AnimationTree at) ac.animationTree = at;
            }
            // The strafe blend's frame is the mesh's own facing -- the same node MovementController
            // yaws, and the same reason ShoulderAimModifier needs it below (AIM_PLAN.md W2).
            if (!meshConfig.meshRootPath.isEmpty()
                    && visualsInstance.getNodeOrNull(meshConfig.meshRootPath) instanceof Node3D acMr) {
                ac.meshRoot = acMr;
            }
            if (!meshConfig.aimSpineModifierPath.isEmpty()) {
                Node asmNode = visualsInstance.getNodeOrNull(meshConfig.aimSpineModifierPath);
                if (asmNode instanceof ShoulderAimModifier asm) {
                    ac.aimSpineModifier = asm;
                    if (aimTarget != null) asm.setTargetNode(asm.getPathTo(aimTarget));
                    // The chest aim needs the body frame too now: its yaw clamp is measured
                    // against it, and so is the elevation clamp's fallback bearing for a target
                    // straight overhead, which has none of its own.
                    if (!meshConfig.meshRootPath.isEmpty()
                            && visualsInstance.getNodeOrNull(meshConfig.meshRootPath)
                                    instanceof Node3D smr) {
                        asm.setBodyForwardNode(asm.getPathTo(smr));
                    }
                }
            }
            if (!meshConfig.shoulderAimModifierPath.isEmpty()) {
                Node samNode = visualsInstance.getNodeOrNull(meshConfig.shoulderAimModifierPath);
                if (samNode instanceof ShoulderAimModifier sam) {
                    ac.shoulderAimModifier = sam;
                    // Both aim mechanisms track the SAME marker, so a stance swap cannot move the
                    // aim point -- only which bones carry it.
                    if (aimTarget != null) sam.setTargetNode(sam.getPathTo(aimTarget));
                    // "Straight ahead" for the yaw clamp is the mesh's facing -- the node
                    // MovementController yaws. A prone chest points at the floor, so the bone
                    // itself cannot supply it (see ShoulderAimModifier.bodyForwardNode).
                    if (!meshConfig.meshRootPath.isEmpty()
                            && visualsInstance.getNodeOrNull(meshConfig.meshRootPath)
                                    instanceof Node3D mr) {
                        sam.setBodyForwardNode(sam.getPathTo(mr));
                    }
                }
            }
        }

        // ── WeaponController ──────────────────────────────────────────────
        if (weaponController != null) {
            weaponController.postInitFromVisuals(visualsInstance, meshConfig);
        }

        // ── FPSCameraController ────────────────────────────────────────────
        if (fpsCameraController != null) {
            if (!meshConfig.fpsCameraMarkerPath.isEmpty()) {
                Node fpsMountNode = visualsInstance.getNodeOrNull(meshConfig.fpsCameraMarkerPath);
                if (fpsMountNode instanceof Node3D fpsMark) fpsCameraController.fpsCameraMount = fpsMark;
            }
            // The frame the eye-point filter works in: MeshRoot, the node MovementController yaws.
            // Sprinting and turning are not motion in that frame, so the filter has only the head
            // bone's own bob and aim swing to deal with -- see FPSCameraController's class doc.
            if (!meshConfig.meshRootPath.isEmpty()
                    && visualsInstance.getNodeOrNull(meshConfig.meshRootPath) instanceof Node3D fmr) {
                fpsCameraController.frameNode = fmr;
            }
        }

        // ── MovementController ─────────────────────────────────────────────
        Node mcNode = getNodeOrNull("MovementController");
        if (mcNode instanceof MovementController mc && !meshConfig.meshRootPath.isEmpty()) {
            Node mr = visualsInstance.getNodeOrNull(meshConfig.meshRootPath);
            if (mr instanceof Node3D mrN) mc.meshRoot = mrN;
        }

        // ── Stance colliders ──────────────────────────────────────────────
        // CharacterVisuals owns the authoring shapes (so sizes can be edited in
        // mesh context). Character.tscn owns the live physics shapes (direct
        // children of CharacterBody3D, required until godot#77937 lands).
        // We copy the Shape3D resource + transform from the visual into the
        // existing character-level collider so both stay in sync.
        if (!meshConfig.stanceColliderPaths.isEmpty()) {
            for (StanceName sn : StanceName.values()) {
                String key = sn.getKey();
                NodePath stancePath = stances.get(key);
                if (stancePath == null || stancePath.isEmpty()) continue;
                Node stanceNode = getNodeOrNull(stancePath);
                if (!(stanceNode instanceof Stance stance)) continue;
                NodePath cp = (NodePath) meshConfig.stanceColliderPaths.get(key);
                if (cp != null && !cp.isEmpty()) {
                    Node cn = visualsInstance.getNodeOrNull(cp);
                    CollisionShape3D charShape = stance.getCollider();
                    if (cn instanceof CollisionShape3D visShape && charShape != null) {
                        if (visShape.getShape() != null) charShape.setShape(visShape.getShape());
                        charShape.setTransform(visShape.getTransform());
                    }
                }
                NodePath rp = (NodePath) meshConfig.stanceRaycastPaths.get(key);
                if (rp != null && !rp.isEmpty()) {
                    Node rn = visualsInstance.getNodeOrNull(rp);
                    RayCast3D charRay = stance.getColRaycast();
                    if (rn instanceof RayCast3D visRay && charRay != null) {
                        charRay.setTransform(visRay.getTransform());
                        charRay.setTargetPosition(visRay.getTargetPosition());
                    }
                }
            }
        }

        // ── Health ────────────────────────────────────────────────────────
        if (healthNode != null) healthNode.meshConfig = meshConfig;
    }

    private void addPhysicalBoneExceptions(PhysicalBoneSimulator3D sim) {
        if (aimRay == null) return;
        for (int i = 0; i < sim.getChildCount(); i++) {
            Node child = sim.getChild(i);
            if (child instanceof PhysicalBone3D bone) aimRay.addException(bone);
        }
    }

    /**
     * Make {@code ray} blind to this body — its collider AND every ragdoll hitbox bone.
     *
     * <p>The same set {@code _ready()} excepts from this character's own {@code aimRay}, offered to
     * anyone else whose ray must not see us. **A CARRIER'S AIM RAY IS EXACTLY THAT**, and it was
     * missing: `Vehicle.tscn`'s `ActiveCamera/AimRay` has `collision_mask = 25`, which includes the
     * HITBOX layer, so a seated driver's aim ray started at the cockpit camera — inside their own
     * head — and reported a hit on their own hitbox 0.3 m later. Measured on
     * `probe_driveby_aim.gd`: the aim point landed 0.8 m from the driver instead of 200 m out, its
     * heading read 64-118 degrees off where the camera was pointing, and since
     * {@code FirearmItem.resolveSightPoint} reads that same point for a seated shooter, the shot
     * was aimed into the shooter. It also read exactly like the reported symptom — the aim refusing
     * to go where you were looking — because the point kept snapping back onto the body.
     *
     * <p>One owner: this is what a body says must not be hit, so the carrier asks rather than
     * assembling its own list of our bones.
     */
    public void addAimExceptionsTo(RayCast3D ray) {
        if (ray == null) return;
        ray.addException(this);
        if (physicalBoneSimulator == null) return;
        for (int i = 0; i < physicalBoneSimulator.getChildCount(); i++) {
            if (physicalBoneSimulator.getChild(i) instanceof PhysicalBone3D bone) ray.addException(bone);
        }
    }

    /** Undo {@link #addAimExceptionsTo} — a carrier's ray must see the next body that stands there. */
    public void removeAimExceptionsFrom(RayCast3D ray) {
        if (ray == null) return;
        ray.removeException(this);
        if (physicalBoneSimulator == null) return;
        for (int i = 0; i < physicalBoneSimulator.getChildCount(); i++) {
            if (physicalBoneSimulator.getChild(i) instanceof PhysicalBone3D bone) ray.removeException(bone);
        }
    }

    public boolean isCombat() { return combat; }

    /** Setter half of the exported {@code combat} property. */
    public void setCombat(boolean value) {
        this.combat = value;
    }

    /** Current physics-tick counter — read by NetworkManager when gathering MSG_SNAPSHOT. */
    public long getCurrentTick() { return currentTick; }

    /** Current stance ordinal — read by NetworkManager when gathering MSG_SNAPSHOT (mirrors the exported `stanceOrdinal` field). */
    public int getStanceOrdinal() { return stanceOrdinal; }

    /** Setter half of the exported {@code stanceOrdinal} property. */
    public void setStanceOrdinal(int value) {
        this.stanceOrdinal = value;
    }

    /**
     * Visual facing — read by NetworkManager when gathering MSG_SNAPSHOT. Facing lives
     * on MovementController.meshRoot's Y rotation (radians), NOT on this body's own
     * transform: MovementController rotates the mesh independently of movement velocity
     * (e.g. strafing keeps the mesh facing the aim target while the body slides
     * sideways), so the body's rotation alone is never enough to reproduce "which way
     * this character is visually facing" on a remote peer (Round 5 "wrong direction" bug).
     */
    public float getFacingYaw() {
        Node mcNode = getNodeOrNull("MovementController");
        if (mcNode instanceof MovementController mc && mc.meshRoot != null) {
            return (float) (mc.meshRoot.getRotation().getY() + getRotation().getY());
        }
        return 0f;
    }

    /**
     * World-space spine-IK look point — read by NetworkManager when gathering MSG_SNAPSHOT so a
     * remote puppet can reproduce where this character is looking (drives its aimTarget node, which
     * the LookAtModifier3D spine tracks). Falls back to the body origin if no aimTarget marker exists.
     */
    public Vector3 getAimTargetPosition() {
        return aimTarget != null ? aimTarget.getGlobalPosition() : getGlobalPosition();
    }

    /** Current movement type ordinal (IDLE/WALK/SPRINT) — replicated in the snapshot flag bits so a puppet's locomotion blend matches exactly. */
    public int getMovementTypeOrdinal() {
        return currentMovementType != null ? currentMovementType.ordinal() : 0;
    }

    /** Current movement direction — read by AIController to build a PASSIVE-tier hold-heading command. */
    public Vector3 getMovementDirection() { return movementDirection; }

    /** Current movement type — read by AIController to build a PASSIVE-tier hold-heading command. */
    public MovementType getCurrentMovementType() { return currentMovementType; }

    // ── Spatial grid bookkeeping (PLAN.md Part D / D1) ───────────────────────
    private static final double GRID_UPDATE_INTERVAL = 0.25;
    private double gridUpdateTimer = 0.0;

    /**
     * Throttled spatial-grid re-bucket. Cheap (a map lookup) while the body stays in its cell;
     * only an actual cell crossing re-hashes. Called from {@link #_physicsProcess} BEFORE the
     * non-authority early return so puppet bodies (NetworkController, e.g. remote players on the
     * host) keep their grid cell current too — host AI must be able to find them.
     */
    protected void updateSpatialCell(double delta) {
        gridUpdateTimer -= delta;
        if (gridUpdateTimer > 0.0) return;
        gridUpdateTimer = GRID_UPDATE_INTERVAL;
        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) grid.move(this, getGlobalPosition());
    }

    // ── Physics loop: gather → apply ─────────────────────────────────────────
    @Register
    @Override
    public void _physicsProcess(double delta) {
        updateSpatialCell(delta);
        UserCommand cmd;
        if (controller != null) {
            if (!controller.isAuthority()) return; // non-authority: state arrives via MSG_SNAPSHOT — see NetworkController
            // inputBlocked is set by UI overlays (radial menu, pause) that own the mouse.
            // _physicsProcess still runs while blocked — we return an empty command so the
            // character stays still without interrupting physics (gravity, collision).
            cmd = inputBlocked ? new UserCommand() : controller.gatherInput(delta);
        } else {
            cmd = gatherInput(delta); // fallback: subclass override
        }
        cmd.tick = currentTick++;
        applyInput(cmd, delta);
    }

    /** Counts down the ragdoll-settle timer and freezes physics when it expires. */
    @Register
    @Override
    public void _process(double delta) {
        ragdoll.tickFreeze(delta);
    }

    /** Drop out of the spatial grid when this body leaves the tree (death/despawn). */
    @Register
    @Override
    public void _exitTree() {
        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) grid.unregister(this);
    }

    /** Fallback input path when no Controller child is present. Returns empty command. */
    protected UserCommand gatherInput(double delta) {
        return new UserCommand();
    }

    /**
     * Apply a CharacterInput snapshot to this character's state.
     *
     * All signal emissions and state transitions live here so that any
     * input source (local, AI, network) produces identical results.
     */
    protected void applyInput(UserCommand input, double delta) {

        // ── Seated passenger (multi-seat) ──────────────────────────────────
        // The body is pinned to its seat by the vehicle each tick; movement/jump/stance/swim
        // are meaningless, so the input reduces to weapon use + aim (GTA drive-by model).
        // Exit intent is handled by Player.applyInput before this runs.
        if (isSeatedPassenger()) {
            applySeatedPassengerInput(input);
            return;
        }

        // ── Movement direction ─────────────────────────────────────────────
        if (input.movementDirection.lengthSquared() > 0.001
                || movementDirection.lengthSquared() > 0.001) {
            movementDirection = input.movementDirection;
            changedMovementDirection.emit(movementDirection);
        }
        setMovementState(input.movementType);

        // ── Combat state ───────────────────────────────────────────────────
        // Pressing fire with a melee weapon automatically enters combat stance so
        // the character raises fists without requiring a separate aim button press.
        boolean effectiveCombat = input.wantCombat;
        if (input.fire && weaponController != null) {
            WeaponItem w = weaponController.getCurrentWeaponItem();
            if (w != null && w.getWeaponType() == WeaponType.MELEE) effectiveCombat = true;
        }
        if (effectiveCombat != combat) {
            combat = effectiveCombat;
            setCombatState();
        }

        // ── Aim target ─────────────────────────────────────────────────────
        // Set directly so the spine IK always matches the actual bullet direction.
        // Lerping caused the weapon to visually lag behind the crosshair when
        // strafing: the bullet hit the new point while the weapon still pointed
        // at the previous lerped target.
        if (input.aimTargetPosition != null && aimTarget != null) {
            aimTarget.setGlobalPosition(input.aimTargetPosition);
        }

        // ── Fire / not-fire ────────────────────────────────────────────────
        if (input.fire) {
            fireWeapon.emit();
        } else {
            notFireWeapon.emit();
        }

        // ── Reload ─────────────────────────────────────────────────────────
        if (input.reload) {
            reloadWeapon.emit();
        }

        // ── Drop ────────────────────────────────────────────────────────────
        if (input.drop) {
            dropWeapon.emit();
        }

        // ── Jump (single ground jump only) ─────────────────────────────────
        // First stand up if crouched/crawling, otherwise launch a ground jump.
        // No jumping while swimming.
        if (input.jump && isOnFloor() && currentStanceName != StanceName.SWIM) {
            if (!isStanceBlocked(StanceName.UPRIGHT)) {
                if (currentStanceName != StanceName.UPRIGHT) {
                    setStance(StanceName.UPRIGHT);
                } else {
                    JumpState js = jumpStates.get("GroundJump");
                    if (js != null) {
                        pressedJump.emit(js);
                    }
                }
            }
        }

        // ── Stance ─────────────────────────────────────────────────────────
        // Swimming is a continuous function of the TRUE water depth below the body (surface − floor,
        // from a downward physics probe), not of volume overlap or grounding (GTA/PUBG model). Using
        // the floor depth — rather than how submerged the body currently is — is what makes every case
        // work with one rule:
        //  • Dive/jump in from above → floor is deep → swim immediately (no sink-to-floor-then-rise).
        //  • Wade in on a slope → floor shallow → walk; deepens past chest → swim.
        //  • Puddle / shallow harbor shelf → floor shallow → never swims (walk).
        //  • Swim toward a rising shore → depth drops below exit → stand up automatically.
        //  • High dock wall over deep water → floor stays deep → keep floating at the surface (never
        //    falls out), since the decision no longer needs the body to touch the bottom.
        // enter > exit gives hysteresis (no SWIM⇄UPRIGHT flicker at the boundary).
        MovementController mc = movementController();
        SwimState sw = (mc != null) ? mc.swimState : null;
        boolean swimming = (currentStanceName == StanceName.SWIM);
        double waterDepth = 0.0;
        if (inWater && sw != null) {
            waterProbeCooldown -= delta;                   // throttled floor probe (see field doc)
            if (waterProbeCooldown <= 0.0) {
                cachedWaterDepth = waterDepthBelowBody(sw);
                waterProbeCooldown = WATER_PROBE_INTERVAL;
            }
            waterDepth = cachedWaterDepth;                 // surface − floor directly below
            if (!swimming) {
                if (waterDepth >= sw.getSwimEnterDepth()) swimming = true;
            } else {
                // Only stand up when the body can ACTUALLY rest on ground — a shallow depth reading
                // alone is not enough. Near a harbor the down-probe hits the dock's underwater wall
                // and reads shallow while the body floats over genuinely deep water beside it; exiting
                // SWIM there dropped the (ungrounded) body to the seabed under gravity, then buoyancy
                // refloated it — the airborne drop/refloat oscillation. Requiring isOnFloor() means we
                // revert only when buoyancy has actually settled the body onto a shallow bottom
                // (wading out), never beside a deep wall.
                if (waterDepth <= sw.getSwimExitDepth() && isOnFloor()) swimming = false;
            }
        } else {
            swimming = false;
            waterProbeCooldown = 0.0;                       // probe immediately on next water entry
        }

        if (swimming) {
            if (currentStanceName != StanceName.SWIM) setStance(StanceName.SWIM);
            if (mc != null) {
                mc.setSwimVertical(input.swimVertical);
                // Tap jump → breach hop toward a low ledge/harbor (a tall dock can't be cleared).
                if (input.jump) mc.swimJump();
            }
        } else if (currentStanceName == StanceName.SWIM) {
            setStance(StanceName.UPRIGHT);
        } else if (input.desiredStance != null && isOnFloor()) {
            setStance(input.desiredStance);
        }
        if (mc != null) mc.setSwimming(swimming, waterSurfaceY);
        updateOxygen(sw, swimming, delta);
        // Screen tint when the head (camera) drops below the water line — the water mesh is single-sided,
        // so once you're under the surface there is no blue in the world to signal it; the overlay does.
        updateUnderwaterOverlay(inWater && activeCamera != null
                && activeCamera.getGlobalPosition().getY() < waterSurfaceY);
        if (debugSwim) updateSwimDebug(sw, waterDepth, swimming);

        // ── Weapon switch / unequip ────────────────────────────────────────
        if (input.wantUnequip) {
            setWeapon(-1);
        } else if (input.desiredWeapon >= 0) {
            setWeapon(input.desiredWeapon);
        }
    }

    // ── Shared helpers ────────────────────────────────────────────────────────
    protected void setCombatState() {
        changedCombatState.emit(combatStates.get(resolveCombatKey(null)));
    }

    // ── Replication apply (bodies extracted to CharacterReplication — WS5 god-class split) ──
    //
    // Non-authority bodies never run gatherInput/applyInput/physics (see the early return in
    // _physicsProcess above), so these are the only writes their transform/combat/stance state
    // ever receive. NetworkController calls these each frame; the implementations live in
    // CharacterReplication. Position/velocity are written every frame by the interpolator;
    // combat/stance are discrete and applied once per snapshot.

    /** Direct transform write — safe outside _physicsProcess because non-authority bodies never run move_and_slide. */
    public void applyReplicatedTransform(Vector3 position, Vector3 velocity) {
        replication.applyTransform(position, velocity);
    }

    /** Direct mesh-facing write — counterpart to {@link #getFacingYaw()}. */
    public void applyReplicatedFacing(float yaw) {
        replication.applyFacing(yaw);
    }

    /** Direct spine-IK look write — counterpart to {@link #getAimTargetPosition()}. */
    public void applyReplicatedAim(Vector3 aimPosition) {
        replication.applyAim(aimPosition);
    }

    /**
     * Drives the locomotion blend on a non-authority body from replicated motion. Takes no yaw:
     * the strafe blend's frame is MeshRoot's own facing, which {@code applyReplicatedFacing} has
     * already written this frame from the same snapshot (see CharacterReplication.applyLocomotion).
     */
    public void applyReplicatedLocomotion(Vector3 velocity) {
        replication.applyLocomotion(velocity);
    }

    /** Applies a replicated movement type (IDLE/WALK/SPRINT) on a puppet. */
    public void applyReplicatedMovementType(int ordinal) {
        replication.applyMovementType(ordinal);
    }

    /** Replays the local applyInput combat/stance transition cascade from a replicated snapshot. */
    public void applyReplicatedCombatAndStance(boolean combat, int stanceOrdinal) {
        replication.applyCombatAndStance(combat, stanceOrdinal);
    }

    /**
     * Picks the combat-state dictionary key for the given weapon slot.
     * Pass null to use the currently equipped weapon (for state refreshes).
     * Pass a slot index to preview the camera state for a weapon being switched to.
     *
     * Returns "MeleeCombat" when the target weapon is MELEE type and that key exists,
     * otherwise falls back to "Combat" / "NoCombat" as before.
     */
    private String resolveCombatKey(Integer targetSlot) {
        if (!combat) return "NoCombat";
        WeaponItem w = null;
        if (weaponController != null) {
            w = (targetSlot != null) ? weaponController.getWeaponItem(targetSlot)
                                     : weaponController.getCurrentWeaponItem();
        }
        boolean isMelee = w != null && w.getWeaponType() == WeaponType.MELEE;
        return (isMelee && combatStates.get("MeleeCombat") != null) ? "MeleeCombat" : "Combat";
    }


    public void setMovementState(MovementType type) {
        Stance stanceNode = stanceCache.get(currentStanceName);
        if (stanceNode == null) return;
        currentMovementType = type;
        changedMovementState.emit(stanceNode.getMovementState(type));
    }

    /**
     * Pure idempotent stance setter — sets the character to exactly {@code stanceName}.
     * No toggle, no anti-spam timer: the hold-vs-toggle decision lives in the input
     * layer ({@link ModalInput} in {@link PlayerController}) and AI uses its own hold
     * timer. This is also the replication apply path, so it must be idempotent.
     */
    protected void setStance(StanceName stanceName) {
        if (stanceName == currentStanceName) return;
        if (isStanceBlocked(stanceName)) return;

        Stance current = stanceCache.get(currentStanceName);
        if (current != null && current.getCollider() != null) current.getCollider().setDisabled(true);

        currentStanceName = stanceName;
        stanceOrdinal = currentStanceName.ordinal();
        Stance nextStance = stanceCache.get(currentStanceName);
        if (nextStance != null) {
            if (nextStance.getCollider() != null) nextStance.getCollider().setDisabled(false);
            changedStance.emit(nextStance);
        }

        setMovementState(currentMovementType);
    }

    protected boolean isStanceBlocked(StanceName stanceName) {
        Stance s = stanceCache.get(stanceName);
        return (s != null) && s.isBlocked();
    }

    /**
     * Water enter/exit hook (PLAN.md I1) — called by {@code WaterVolume} on every peer. Toggles
     * the swim flag and the MovementController's swim physics; the actual SWIM ⇄ UPRIGHT stance
     * switch happens on the authority body in {@link #applyInput} (and replicates from there).
     */
    public void setInWater(boolean value, double surfaceY) {
        inWater = value;
        if (value) {
            waterSurfaceY = surfaceY;
        } else {
            // Left the volume entirely — stop swim physics. The per-frame decision in applyInput
            // owns the SWIM stance while inside (it may be UPRIGHT in shallow water).
            MovementController mc = movementController();
            if (mc != null) mc.setSwimming(false, 0.0);
        }
    }

    /**
     * True water depth (water surface − floor) directly below the body, from a downward physics
     * raycast. Drives the depth-based swim decision so it works whether the body is grounded, wading,
     * or airborne (diving in) — unlike {@code isOnFloor()}, which a floating swimmer never satisfies
     * over deep water. Returns a large value (the full probe length) when no floor is found, i.e.
     * very deep water. Bodies (layer-masked) are hit; the water {@code Area3D} is ignored by the ray.
     */
    private double waterDepthBelowBody(SwimState sw) {
        World3D world = getWorld3d();
        if (world == null) return 0.0;
        PhysicsDirectSpaceState3D space = world.getDirectSpaceState();
        if (space == null) return 0.0;
        Vector3 origin = getGlobalPosition();
        double probe = sw.getFloorProbeLength();
        Vector3 from = new Vector3(origin.getX(), origin.getY() + 0.2, origin.getZ());
        Vector3 to   = new Vector3(origin.getX(), origin.getY() - probe, origin.getZ());
        VariantArray<RID> exclude = new VariantArray<>(RID.class);
        exclude.add(getRid());
        PhysicsRayQueryParameters3D q =
            PhysicsRayQueryParameters3D.Companion.create(from, to, sw.getFloorProbeMask(), exclude);
        Dictionary<java.lang.Object, java.lang.Object> hit = space.intersectRay(q);
        if (hit.isEmpty()) return waterSurfaceY - to.getY();   // no floor within probe → deep
        java.lang.Object pos = hit.get("position");
        return (pos instanceof Vector3 p) ? (waterSurfaceY - p.getY()) : (waterSurfaceY - to.getY());
    }

    /**
     * Breath/oxygen tick (PLAN.md I1). Swimming at the surface is free; once the body is fully
     * submerged (head deeper than {@code submergeDepth} below the surface) the lungs drain, and when
     * empty the swimmer takes periodic drowning damage — so a dive has a time budget and the player
     * must surface (tactical play; also covers a future murky-water shader). Air recovers above water
     * and on land. Runs only on the authority body (applyInput), like the rest of the swim decision.
     */
    private void updateOxygen(SwimState sw, boolean swimming, double delta) {
        if (sw == null) return;
        double max = sw.getMaxOxygen();
        if (currentOxygen < 0.0) currentOxygen = max;   // lazy init to full

        double prevOxygen = currentOxygen;
        boolean submerged = swimming && (waterSurfaceY - getGlobalPosition().getY()) > sw.getSubmergeDepth();
        if (submerged) {
            currentOxygen = Math.max(0.0, currentOxygen - delta);   // 1 s of air per real second
            if (currentOxygen <= 0.0) {
                drownTimer += delta;
                double interval = Math.max(0.1, sw.getDrowningInterval());
                while (drownTimer >= interval) {
                    drownTimer -= interval;
                    if (healthNode != null) {
                        String attackerName    = (characterInfo != null) ? characterInfo.displayName : "";
                        String attackerFaction = (characterInfo != null) ? characterInfo.faction     : "";
                        healthNode.takeDamage(null, (float) sw.getDrowningDamage(), "Drowning",
                                              null, attackerName, attackerFaction);
                    }
                }
            } else {
                drownTimer = 0.0;
            }
        } else {
            currentOxygen = Math.min(max, currentOxygen + sw.getOxygenRecoverRate() * delta);
            drownTimer = 0.0;
        }

        // Emit on change, plus the frame it returns to full (so the HUD meter hides). Skip otherwise.
        boolean reachedFull = (currentOxygen >= max && prevOxygen < max);
        if (currentOxygen != prevOxygen || reachedFull) {
            Node busNode = getNodeOrNull("/root/EventBus");
            if (busNode instanceof EventBus bus && characterInfo != null) {
                bus.characterOxygenChanged.emit(characterInfo, (float) currentOxygen, (float) max);
            }
        }
    }

    /**
     * On-screen swim/water-depth readout for the local player (gated by {@link #debugSwim}). Lazily
     * builds a tiny CanvasLayer+Label as a child of the body, so it renders to screen and frees with
     * the body. Shows in-water, measured depth, enter/exit thresholds, grounded, and the swim decision
     * — enough to diagnose "why am I (not) swimming here" during a walk-test without the editor.
     */
    private void updateSwimDebug(SwimState sw, double waterDepth, boolean swimming) {
        if (!isLocallyOwnedPlayer()) return;
        if (swimDebugLabel == null) {
            CanvasLayer layer = new CanvasLayer();
            addChild(layer);
            swimDebugLabel = new Label();
            swimDebugLabel.setPosition(new Vector2(16f, 120f));
            layer.addChild(swimDebugLabel);
        }
        String enter = sw != null ? String.format("%.2f", sw.getSwimEnterDepth()) : "-";
        String exit  = sw != null ? String.format("%.2f", sw.getSwimExitDepth()) : "-";
        swimDebugLabel.setText(String.format(
            "SWIM DEBUG\ninWater: %s\nwaterDepth: %s\nenter/exit: %s / %s\nonFloor: %s\nstance: %s%s",
            inWater,
            inWater ? String.format("%.2f m", waterDepth) : "-",
            enter, exit,
            isOnFloor(),
            currentStanceName,
            swimming ? "  [SWIMMING]" : ""));
    }

    /**
     * Shows/hides a full-screen blue tint while the local player is submerged. Lazily builds a
     * CanvasLayer + full-rect ColorRect as a child of the body (same pattern as {@link #updateSwimDebug},
     * so it renders to screen and frees with the body). No-op for AI / remote bodies.
     */
    private void updateUnderwaterOverlay(boolean submerged) {
        if (!isLocallyOwnedPlayer()) return;
        if (underwaterTint == null) {
            if (!submerged) return; // don't build the overlay until it is first needed
            underwaterLayer = new CanvasLayer();
            addChild(underwaterLayer);
            underwaterTint = new ColorRect();
            underwaterTint.setColor(new Color(0.05, 0.32, 0.55, 0.45));
            underwaterTint.setMouseFilter(Control.MouseFilter.IGNORE);
            underwaterTint.setAnchorsPreset(Control.LayoutPreset.PRESET_FULL_RECT, false);
            underwaterLayer.addChild(underwaterTint);
        }
        underwaterTint.setVisible(submerged);
    }

    private MovementController movementControllerRef;

    /** Lazily-cached MovementController sibling (never freed/swapped during the body's life). */
    private MovementController movementController() {
        if (movementControllerRef == null) {
            Node n = getNodeOrNull("MovementController");
            if (n instanceof MovementController mc) movementControllerRef = mc;
        }
        return movementControllerRef;
    }

    /**
     * Immediately transitions to {@code next} stance, bypassing the anti-spam timer
     * and toggle logic.  Used for forced transitions (vehicle enter/exit) that must
     * happen the same frame regardless of how recently the last stance changed.
     */
    protected void forceSetStance(StanceName next) {
        Stance current = stanceCache.get(currentStanceName);
        if (current != null && current.getCollider() != null) current.getCollider().setDisabled(true);
        currentStanceName = next;
        stanceOrdinal     = next.ordinal();
        Stance nextStance = stanceCache.get(next);
        if (nextStance != null) {
            if (nextStance.getCollider() != null) nextStance.getCollider().setDisabled(false);
            changedStance.emit(nextStance);
        }
    }

    // ── Vehicle drive state (bodies extracted to CharacterDriveState — WS5 god-class split) ──

    /** True while riding a carrier as a non-driving passenger — input reduces to weapon use. */
    public boolean isSeatedPassenger() {
        return currentVehicleNode != null && !vehicleDriver;
    }

    /**
     * True while a carrier is the thing deciding what this character sees — the DRIVER's seat, whose
     * view is one of the carrier's three (TPS follow / cockpit / bonnet).
     *
     * <p>A PASSENGER is deliberately excluded: nothing switches their camera when they sit down, so
     * their own rig is still their view and must keep running. That asymmetry is the same one
     * {@link #refreshHeadVisibility} turns on.
     */
    public boolean carrierOwnsView() {
        return currentVehicleNode != null && vehicleDriver;
    }

    /**
     * How far this body can turn its aim off its own facing in the seated stance, degrees — the
     * RIG's reach, for a carrier to compare its authored firing sector against. Negative when the
     * stance is not loaded (nothing to say, so nothing is warned).
     */
    public double seatedAimReachDegrees() {
        Stance s = stanceCache.get(StanceName.DRIVE_CARRIER);
        return s != null ? s.getAimYawLimit() : -1.0;
    }

    /** Set while the carrier's firing sector is holding this occupant's aim short of where the camera looks. */
    private boolean seatAimClamped = false;

    /** True while the seat's firing sector is holding the aim back — the reticle dims to say so. */
    public boolean isSeatAimClamped() { return seatAimClamped; }

    /**
     * True while this occupant's reticle should be pinned to the WORLD point they are aiming at
     * rather than to the centre of the screen.
     *
     * <p>Seated only, and that is not an arbitrary restriction: on foot the aim point IS the camera
     * ray's hit, so the screen centre and the world point are the same pixel and anchoring would
     * buy nothing but jitter. In a seat they come apart, because the camera may look anywhere and
     * the weapon may not.
     */
    public boolean isSeatedAimAnchored() {
        return currentVehicleNode != null && vehicleWeaponMode == VehicleWeaponMode.PASSENGER_WEAPON;
    }

    /**
     * THE one place a seated occupant's aim target is written — for the driver (via
     * {@code CharacterDriveState.applyPassengerWeaponInput}, fed by the carrier camera) and for a
     * passenger (via {@link #applySeatedPassengerInput}, fed by their own). Both used to write the
     * marker directly, which is how the clamp came to have no owner at all.
     *
     * <p>Everything downstream reads this ONE point: the aim modifiers (so the gun points here),
     * {@code FirearmItem.resolveSightPoint} (so the bullet goes here), the snapshot (so remote peers
     * see the same), and the reticle (so the player is told where it is). That is the whole fix —
     * before it, the bones were clamped by {@code Stance.aimYawLimit} and the bullet was not clamped
     * by anything, so the visible gun and the shot disagreed by up to 135 degrees.
     */
    public void applySeatedAimTarget(Vector3 desired) {
        if (desired == null || aimTarget == null) return;
        Vector3 allowed = (currentVehicleNode instanceof Vehicle v)
                ? v.clampSeatAim(this, desired) : desired;
        seatAimClamped = allowed.minus(desired).lengthSquared() > 1e-4;
        aimTarget.setGlobalPosition(allowed);
    }

    /** Reduced input path while riding as a passenger — weapon use + aim only. */
    protected void applySeatedPassengerInput(UserCommand input) {
        applySeatedAimTarget(input.aimTargetPosition);
        if (vehicleWeaponMode != VehicleWeaponMode.PASSENGER_WEAPON) return;   // seat can't shoot
        if (input.fire) fireWeapon.emit();
        else            notFireWeapon.emit();
        if (input.reload) reloadWeapon.emit();
        if (input.desiredWeapon >= 0) setWeapon(input.desiredWeapon);
    }

    /** Puts the character into the DRIVER (seat 0) DRIVE_CARRIER state. Called by {@code Vehicle.tryEnter}. */
    public void enterDriveState(VehicleWeaponMode mode, Node vehicleNode) {
        driveState.enter(mode, vehicleNode, true);
    }

    /** Seat-aware drive state — passengers keep their own controller/input (see CharacterDriveState). */
    public void enterDriveState(VehicleWeaponMode mode, Node vehicleNode, boolean isDriver) {
        driveState.enter(mode, vehicleNode, isDriver);
    }

    /** Restores the character's pre-drive state. Called by {@code Vehicle.tryExit} before the controller is returned. */
    public void exitDriveState() {
        driveState.exit();
    }

    /**
     * Relays fire/reload commands from the vehicle controller to this character's weapon system
     * each physics frame. Only called when {@code vehicleWeaponMode == PASSENGER_WEAPON}.
     */
    public void applyPassengerWeaponInput(boolean fire, boolean reload, int desiredWeapon, Vector3 aimTargetPos) {
        driveState.applyPassengerWeaponInput(fire, reload, desiredWeapon, aimTargetPos);
    }

    public void setMovementDirection(Vector3 movementDirection) {
        this.movementDirection = movementDirection;
    }

    public void setWeapon(int weapon) {
        changedWeapon.emit(weapon);
        nameplateChanged.emit();
        // Preview camera state for the target weapon type immediately so the camera
        // starts lerping to melee/ranged position during the weapon-switch animation.
        if (combat && weapon >= 0) {
            CombatState state = combatStates.get(resolveCombatKey(weapon));
            if (state != null) changedCombatState.emit(state);
        }
    }

    // ── Controllable implementation ───────────────────────────────────────────

    @Override
    public void applyCommand(UserCommand cmd, double delta) {
        applyInput(cmd, delta);
    }

    @Override
    public CharacterInfo getCharacterInfo() {
        return characterInfo;
    }

    /** Setter half of the exported {@code characterInfo} property. */
    public void setCharacterInfo(CharacterInfo value) {
        this.characterInfo = value;
    }

    // ── NameplateTarget ─────────────────────────────────────────────────────────
    @Override
    public String getNameplateText() {
        return characterInfo != null ? characterInfo.displayName : "";
    }

    @Override
    public Color getNameplateColor() {
        return Faction.color(characterInfo != null ? characterInfo.faction : null);
    }

    @Override
    public Signal0 getNameplateChangedSignal() {
        return nameplateChanged;
    }

    /**
     * Host-authoritative runtime faction change (D3). Updates this body's faction and, on a
     * networked host, replicates it so every client re-targets identically — target discovery reads
     * {@code characterInfo.faction} fresh each scan, so the change takes effect on the next scan.
     * A client applying an inbound swap (GameManager.applyCharacterFaction) calls this too, but the
     * {@code isServer()} gate stops it echoing back. Emits {@link #nameplateChanged} so cosmetics that
     * key off faction (e.g. the nameplate tint) re-apply on every peer, host and client alike.
     */
    public void setFaction(String faction) {
        if (characterInfo == null || faction == null) return;
        characterInfo.faction = faction;
        nameplateChanged.emit();
        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (netNode instanceof com.openworld.net.NetworkManager net && net.isNetworked() && net.isServer()) {
            net.broadcastWorldEvent(com.openworld.game.GameManager.WORLD_EVENT_FACTION_SWAP,
                    characterInfo.characterId, 0f, java.util.List.of(faction));
        }
    }

    /** The controller currently driving this body — null only before _ready() resolves it. */
    public Controller getController() {
        return controller;
    }

    /**
     * Remove the current Controller child and return it so the caller can
     * reparent it to a different Controllable (vehicle hot-swap).
     * If no controller is attached, returns null.
     */
    public Controller detachController() {
        if (controller == null) return null;
        Controller ctrl = controller;
        removeChild(ctrl);
        controller = null;
        return ctrl;
    }

    /**
     * Add ctrl as a child controller, replacing any existing one. The outgoing controller is
     * removed from the tree AND freed — a swapped-out controller is owned here and has no other
     * referent. A caller that wants to keep the old controller (vehicle enter/exit hot-swap) must
     * use {@link #detachController()} instead, which removes without freeing and returns it.
     * Without the free, every swap (puppet spawn → NetworkController, player-leaves → bot
     * AIController, scene PlayerController → NetworkController) orphaned a parentless, unfreed
     * node — the "removed with remove_child() but not freed" leak reported at exit.
     */
    public void attachController(Controller ctrl) {
        if (controller != null && controller != ctrl) {
            Controller old = controller;
            removeChild(old);
            old.queueFree();
        }
        controller = ctrl;
        addChild(ctrl);
    }

    // ── Ragdoll (bodies extracted to CharacterRagdoll — WS5 god-class split) ────

    /**
     * Applies a physics response to a bullet hit — a stagger velocity kick while alive, or a
     * bone impulse on a started ragdoll. Public surface unchanged for ImpactManager /
     * ExplosionManager / Vehicle; delegates to {@link CharacterRagdoll}.
     */
    public void applyHitImpulse(Node hitNode, Vector3 bulletDir, float damage) {
        ragdoll.applyHitImpulse(hitNode, bulletDir, damage);
    }

    /** Apply a physics impulse to a named bone during ragdoll. Delegates to {@link CharacterRagdoll}. */
    public void applyBoneImpulse(String boneName, Vector3 impulse) {
        ragdoll.applyBoneImpulse(boneName, impulse);
    }

    public Node3D getCameraRoot() { return cameraRoot; }

    /**
     * Makes this character's Camera3D the active viewport camera.
     * Called by Vehicle.tryExit() when the player leaves the vehicle.
     */
    public void makeCameraActive() {
        if (activeCamera != null) activeCamera.makeCurrent();
    }

    /**
     * Claims the viewport for this body's camera if — and only if — this body is
     * the one the local peer should be looking through: single-player (no
     * NetworkManager, or not networked) always claims; networked, only the body
     * this peer owns (isAuthorityFor) does. A ServerProxyController-driven body
     * reports isAuthority()=true (the server simulates it) but is NOT locally
     * owned, so gating on ownership — not on the attached controller — is what
     * keeps the host's own view from being stolen by remote bodies it simulates.
     * Deferred from _ready() (see the activeCamera resolution above) so this runs
     * once activeCamera/characterInfo are populated and every other _ready() that
     * might also touch the viewport (e.g. vehicle occupancy) has settled.
     */
    @Register
    public void activateCameraIfOwned() {
        if (activeCamera == null) return;
        // isAuthorityFor answers "do I simulate this body" — right for the physics
        // gate, wrong for "is this MY viewpoint": AI characters default ownerPeerId
        // to SERVER_PEER_ID, so on the host (localPeerId == SERVER_PEER_ID) every
        // AI reports isAuthorityFor() == true and would otherwise steal the
        // viewport the instant it spawns. Only a human-driven body (Player) is ever
        // a valid local viewpoint — AICharacter is a sibling type, never a Player.
        if (!(this instanceof Player)) return;
        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (isLocallyOwnedPlayer()) {
            activeCamera.makeCurrent();
        } else if (netNode instanceof com.openworld.net.NetworkManager net && net.isNetworked()
                   && controller instanceof PlayerController) {
            // Ghost Player body: World.tscn's pre-placed Player on the client machine.
            // removeLocalPrePlacedPlayer() queues it free on connect, but a physics frame
            // can still run before queueFree resolves. Disable physics and collision here
            // so the body cannot block the local player's movement or occupy physics space.
            setPhysicsProcess(false);
            Node mc = getNodeOrNull("MovementController");
            if (mc != null) mc.setPhysicsProcess(false);
            setCollisionLayer(0);
            setCollisionMask(0);
        }
    }

    /**
     * True when this body is the local peer's own player — the one we drive: single-player always,
     * or, when networked, the body this peer is authoritative for ({@code ownerPeerId == localPeerId}).
     * Gated to {@link Player} so it is never true for AI (on the host the AI's ownerPeerId is
     * SERVER_PEER_ID, so it would otherwise report as "ours"). This is the ownership signal — the
     * camera being current is a consequence of it, not the source of truth.
     */
    /** Public ownership check — for world volumes (e.g. {@code InteriorVolume}) that affect only the local player's experience. */
    public boolean isLocalOwnedPlayer() { return isLocallyOwnedPlayer(); }

    private boolean isLocallyOwnedPlayer() {
        if (!(this instanceof Player)) return false;
        Node netNode = getNodeOrNull("/root/NetworkManager");
        if (!(netNode instanceof com.openworld.net.NetworkManager net) || !net.isNetworked()) return true;
        return net.isAuthorityFor(characterInfo);
    }

    /**
     * Hide this body's own nameplate — you never see your own. Decided purely by ownership (not by
     * the camera): a Player we locally own hides it; AI and other peers' players keep the
     * Character.tscn default (visible), so networked peers still see each other's plates. Deferred
     * from _ready (like activateCameraIfOwned) so ownerPeerId/characterInfo are resolved first.
     * Replaces the old per-scene `visible = false` override on Player.tscn (which also hid remote
     * players') and the camera-coupled hide inside activateCameraIfOwned.
     */
    @Register
    public void applyNameplateVisibility() {
        if (!isLocallyOwnedPlayer()) return;
        Node nameplate = getNodeOrNull("Nameplate");
        if (nameplate instanceof Node3D np) np.setVisible(false);
    }

    public boolean isAlive() {
        return healthNode == null || !healthNode.isDead();
    }

    public void setHeadVisible(boolean visible) {
        for (Node3D headMesh : headMeshes) headMesh.setVisible(visible);
        headVisibleApplied = visible;
    }

    /**
     * Re-derives head-mesh visibility from the live view. Cheap to call every frame — it touches
     * the meshes only when the answer changes.
     *
     * <p><b>The head is hidden for exactly one reason: the camera on screen is behind THIS
     * character's own eyes.</b> Everything else follows from asking that one question of whoever
     * owns the camera — which changes when the character sits down:
     *
     * <ul>
     *   <li><b>On foot</b> — the FPS rig is mounted on {@code neck_01}, so {@link #isFpsMode} IS
     *       the answer.</li>
     *   <li><b>Driving</b> — the camera is the carrier's, and the carrier has three views. Only its
     *       cockpit view is in the driver's skull; its TPS boom and its bonnet mount (0, 0.34,
     *       -0.80, over the nose) are not, so the head stays on in both. The carrier is the one
     *       that knows, so it is asked: {@code Vehicle.isViewInsideOccupantHead}.</li>
     *   <li><b>Riding</b> — a passenger keeps their own camera when they sit down, so they are the
     *       on-foot case again. Deriving that instead of special-casing "in a vehicle" is what
     *       stops a passenger in FPS looking at the inside of their own head.</li>
     * </ul>
     *
     * <p>This used to be latched — {@code setCameraMode} called {@code setHeadVisible(!fps)} once,
     * at the toggle — and that is why a player who entered a vehicle while in FPS drove a headless
     * character: nothing on the way into the seat had any reason to put it back. Deriving it
     * removes the whole class instead of adding a fourth site that remembers to. Note how little
     * the cockpit view cost here for the same reason: one more question asked of one more owner,
     * not a fourth place that remembers to set a flag.
     */
    @Register
    public void refreshHeadVisibility() {
        boolean insideOwnEyes = (vehicleDriver && currentVehicleNode instanceof Vehicle v)
                ? v.isViewInsideOccupantHead(this)
                : isFpsMode;
        boolean visible = !insideOwnEyes;
        if (visible != headVisibleApplied) setHeadVisible(visible);
    }

    /**
     * Looks up a physical bone node by bone name within this character's
     * physicalBoneSimulator.  Works with both B2 (bones inside CharacterVisuals)
     * and any legacy embedded setup where physicalBoneSimulator was resolved in _ready().
     */
    public Node3D getPhysicalBoneNode(String boneName) {
        if (physicalBoneSimulator == null) return null;
        Node n = physicalBoneSimulator.getNodeOrNull(new NodePath("Physical Bone " + boneName));
        return (n instanceof Node3D nd) ? nd : null;
    }

    /** Same convention as {@code TPSCameraController.applyRecoil}: positive pitch is UP. */
    public void applyRecoil(double pitchKick, double yawKick) {
        controlRotation.recoilPitch += pitchKick;
        controlRotation.recoilYaw   += yawKick;
    }

    public void setCameraMode(boolean fps) {
        isFpsMode = fps;
        // ActiveCamera is the single rendering camera — no makeCurrent() switching needed.
        // Controllers write their proxy transform to it each frame based on isFpsMode.
        refreshHeadVisibility();
    }

    // ── Override in subclasses ────────────────────────────────────────────────
    @Register
    public void onDied() {
        GD.print(getName() + " died");
        ragdoll.enableDeathVisuals();
        // Authority-only side effect: the dropped weapons are the host's authoritative world
        // state. A non-authority puppet (applyReplicatedDeath) must NOT drop, or it would spawn
        // orphan, unsynced pickups on the client.
        if (weaponController != null) weaponController.dropAllWeapons();
    }

    /**
     * Non-authority death: a replicated {@code currentHealth} reached zero on a puppet
     * (NetworkController-driven body). Plays the ragdoll visuals only — no weapon drops,
     * no playerDied/game-over, no EventBus kill notification (all emitted once, on the
     * authority, inside {@code Health.applyDamage}). NetworkController stops driving this
     * body's transform after calling it, so the ragdoll isn't dragged by further snapshots.
     */
    public void applyReplicatedDeath() {
        ragdoll.enableDeathVisuals();
    }
}

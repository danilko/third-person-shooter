package com.openworld.character;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.*;
import com.openworld.carrier.vehicle.Vehicle;
import godot.core.Callable;
import godot.core.MethodCallable;
import godot.core.NodePath;
import godot.core.StringName;
import godot.core.StringNames;
import godot.core.Vector3;
import com.openworld.ai.AIBehaviorConfig;
import com.openworld.ai.AIController;
import com.openworld.ai.AILodLevel;
import com.openworld.ai.character.AttackState;
import com.openworld.ai.character.EscortState;
import com.openworld.ai.character.FleeState;
import com.openworld.ai.character.RefillAmmoState;
import com.openworld.camera.AICameraController;
import com.openworld.control.CharacterController;
import com.openworld.control.Controller;
import com.openworld.control.UserCommand;
import com.openworld.game.PlayerRegistry;
import com.openworld.movement.character.MovementController;
import com.openworld.movement.character.MovementType;
import com.openworld.movement.character.StanceName;
import com.openworld.weapon.WeaponItem;
import com.openworld.weapon.WeaponType;
import com.openworld.world.SpatialEntityGrid;
import com.openworld.world.StimulusManager;

/**
 * AI-controlled character body — hardware and sensing only.
 *
 * The brain (AIController) generates a UserCommand each physics tick; this class
 * owns the body capabilities it needs: navigation, line-of-sight raycasting, aim
 * hardware, weapon selection, and patrol geometry.
 *
 * All per-AI tuning lives in AIBehaviorConfig. Swap a different .tres preset in the
 * inspector to change archetype (soldier, guard, civilian…) without touching code.
 */
@Script(className = "AICharacter")
public class AICharacter extends Character {

    public static final float EYE_HEIGHT        = 1.63f;
    public static final float TARGET_BODY_HEIGHT = 1.05f;

    // ── Behaviour configuration ───────────────────────────────────────────────

    /** Per-AI tuning resource. If null, shared DEFAULTS are used. */
    @Export public AIBehaviorConfig behaviorConfig;

    /** Shared defaults — allocated once; never mutated. */
    private static final AIBehaviorConfig DEFAULTS = new AIBehaviorConfig();

    /**
     * The tuning config, falling back to the shared defaults when none is assigned.
     * Deliberately NOT named {@code getBehaviorConfig} — a JavaBean-shaped getter is merged
     * with the exported {@code behaviorConfig} field into one accessor-bound property, which
     * would make Godot read (and save) DEFAULTS whenever the field is unset.
     */
    public AIBehaviorConfig behaviorConfigOrDefaults() {
        return behaviorConfig != null ? behaviorConfig : DEFAULTS;
    }

    /**
     * World-space Area3D that refills all weapons on entry.
     * Scene-specific node reference — lives here rather than in AIBehaviorConfig.
     */
    @Export public Area3D ammoRefill;

    /**
     * NodePath (from scene root) of the Character to escort in EscortState.
     * Resolved to escortTarget in _ready(). Leave empty for non-escort AIs.
     */
    @Export public NodePath escortTargetPath = new NodePath();

    /**
     * NodePath to this AI's {@link AISquad} for editor-placed squads (PLAN.md E3). Resolved in
     * {@code _ready()}. Zone-spawned AI are assigned a squad programmatically via {@link #setSquad}
     * instead. Leave empty for a solo AI.
     */
    @Export public NodePath squadPath = new NodePath();

    /** Runtime squad — shared group awareness (E3). Null = solo. Accessed via {@link #activeSquad()}. */
    private AISquad squad;

    /**
     * Runtime escort target — populated from escortTargetPath in _ready() or set
     * directly via setEscortTarget() from mission code (e.g. MissionDirector).
     */
    public Character escortTarget;

    // ── Sensor throttle constants ─────────────────────────────────────────────
    private static final StringName CHARACTERS_GROUP     = new StringName("characters");
    private static final double     TARGET_SCAN_INTERVAL = 0.4;
    private static final double     LOS_CACHE_INTERVAL   = 0.05;

    // ── Distance-based LOD (PLAN.md Part D / D2) ──────────────────────────────
    // Re-evaluated on the ~2 s lodTimer from nearestPlayerDist():
    //   ACTIVE  (< 80 m)   full FSM + NavAgent + AnimationTree.
    //   PASSIVE (80–200 m) simplified tick: no pathfinding / no FSM transitions (AIController
    //                      returns a hold-heading command) and no AnimationTree writes
    //                      (AnimationController holds the last pose).
    //   FROZEN  (> 200 m)  Character._physicsProcess is skipped entirely. MovementController
    //                      still runs as a separate node, decelerating the AI to rest.
    private static final float LOD_ACTIVE_DIST = 80.0f;
    private static final float LOD_FREEZE_DIST = 200.0f;
    /**
     * Process-global multiplier on the LOD distances, set by the active region (PLAN.md I4
     * {@code RegionConfig.aiLodBias}): a rural region pushes it {@code > 1} (AI stay active farther
     * out), a dense city {@code < 1} (tighter budget). 1.0 = the built-in defaults. Static because
     * the active region is a world-wide property, not per-AI.
     */
    private static float lodDistanceBias = 1.0f;
    private double      lodTimer = 0.0;
    private AILodLevel  lodLevel = AILodLevel.ACTIVE;

    /** Set the world-wide LOD-distance multiplier (ZoneManager.applyRegion). Clamped to a sane floor. */
    public static void setLodDistanceBias(float bias) {
        lodDistanceBias = bias > 0.1f ? bias : 0.1f;
    }

    /** Current LOD tier — read by AIController (FSM gating) and AnimationController (pose gating). */
    public AILodLevel getLodLevel() { return lodLevel; }

    /** Back-compat shorthand: kept so existing FROZEN-only callers keep working. */
    public boolean isLodFrozen() { return lodLevel == AILodLevel.FROZEN; }

    private AILodLevel classifyLod(float nearestPlayer) {
        if (nearestPlayer > LOD_FREEZE_DIST * lodDistanceBias) return AILodLevel.FROZEN;
        if (nearestPlayer > LOD_ACTIVE_DIST * lodDistanceBias) return AILodLevel.PASSIVE;
        return AILodLevel.ACTIVE;
    }

    // ── Movement-state dedup (Perf 3) ─────────────────────────────────────────
    // Prevents Character.setMovementState emitting changedMovementState every frame
    // when the (type, stance) pair is unchanged. Not applied to the base class
    // because the player relies on the signal firing even without type changes
    // (combat-speed-factor updates from changedCombatState need a fresh speed apply).
    private MovementType lastEmittedMoveType  = null;
    private StanceName   lastEmittedMoveStance = null;

    @Override
    public void setMovementState(MovementType type) {
        if (type == lastEmittedMoveType && currentStanceName == lastEmittedMoveStance) return;
        lastEmittedMoveType   = type;
        lastEmittedMoveStance = currentStanceName;
        super.setMovementState(type);
    }

    // ── AI hardware ───────────────────────────────────────────────────────────
    private NavigationAgent3D  navAgent;
    private AICameraController aiCamera;

    // ── Body state ────────────────────────────────────────────────────────────
    private boolean   isDead        = false;
    private Character currentTarget;
    private Vector3   spawnPosition;

    // ── Sensor cache ─────────────────────────────────────────────────────────
    private double    targetScanTimer     = 0.0;
    private double    losCacheTimer       = 0.0;
    private boolean   cachedLoS           = false;
    private Character cachedTargetForBone = null;
    private Node3D[]  cachedBoneNodes     = null;
    private Node3D    cachedVisibleBone   = null;

    // ── Best-weapon cache (-1 = stale) ────────────────────────────────────────
    private int cachedBestWeapon = -1;

    // ── Vehicle-target tracking ───────────────────────────────────────────────
    private Node cachedTargetVehicle = null;

    // ── Lifecycle ─────────────────────────────────────────────────────────────
    @Register
    @Override
    public void _ready() {
        useWeaponSpread = false;
        super._ready();
        navAgent = (NavigationAgent3D) getNode("NavigationAgent3D");
        if (cameraRoot instanceof AICameraController ac) aiCamera = ac;

        spawnPosition = new Vector3(getGlobalPosition());

        // Stagger scan timers so all AIs don't expire on the same frame.
        targetScanTimer = godot.global.GD.randfRange(0f, (float) TARGET_SCAN_INTERVAL);
        losCacheTimer   = godot.global.GD.randfRange(0f, (float) LOS_CACHE_INTERVAL);
        // Stagger LOD checks too.
        lodTimer = godot.global.GD.randfRange(0f, 2.0f);

        if (weaponController != null) {
            weaponController.ammoChanged.connectUnsafe(
                    MethodCallable.createUnsafe(this, "onAmmoChanged"),
                    godot.api.Object.ConnectFlags.DEFAULT);
        }

        // Resolve escort target from NodePath if provided.
        if (escortTargetPath != null && !escortTargetPath.getPath().isEmpty()) {
            Node owner = getOwner();
            if (owner != null) {
                Node n = owner.getNodeOrNull(escortTargetPath.getPath());
                if (n instanceof Character c) setEscortTarget(c);
            }
        }

        // Resolve squad from NodePath (editor-placed squads); zone-spawned AI use setSquad() instead.
        if (squadPath != null && !squadPath.getPath().isEmpty()) {
            Node n = getNodeOrNull(squadPath);
            if (n instanceof AISquad s) setSquad(s);
        }

        if (controller instanceof AIController aiCtrl) aiCtrl.start();
    }

    @Register
    @Override
    public void _exitTree() {
        if (squad != null && godot.global.GD.isInstanceValid(squad)) squad.unregister(this);
        super._exitTree();
    }

    // ── Squad (E3) ──────────────────────────────────────────────────────────────

    /** The squad if still valid, else null (also nulls a stale ref to a freed squad node). */
    private AISquad activeSquad() {
        if (squad != null && !godot.global.GD.isInstanceValid(squad)) squad = null;
        return squad;
    }

    public AISquad getSquad() { return activeSquad(); }

    /** Assign (or clear) this AI's squad, moving its registration. Safe across recycled reuse. */
    public void setSquad(AISquad s) {
        if (squad == s) return;
        if (squad != null && godot.global.GD.isInstanceValid(squad)) squad.unregister(this);
        squad = s;
        if (squad != null) squad.register(this);
    }

    /**
     * Adopt a target pushed by a squad-mate (PLAN.md E3) without waiting for the next scan: set it as
     * the current target, drop stale bone caches, and seed last-known so the FSM converges even before
     * personal LoS. Faction was already verified by the spotter.
     */
    public void adoptSquadTarget(Character target, Vector3 pos) {
        if (target == null || isDead()) return;
        if (currentTarget != target) {
            currentTarget       = target;
            cachedTargetForBone = null;
            cachedBoneNodes     = null;
            cachedVisibleBone   = null;
            cachedTargetVehicle = null;
            cachedLoS           = false;
            losCacheTimer       = 0;
        }
        if (controller instanceof AIController ai && pos != null) ai.setLastKnownTargetPosition(pos);
    }

    /** Tell squad-mates within alert range about this AI's confirmed target (LoS or being shot). */
    public void broadcastToSquad(Character target, Vector3 pos) {
        AISquad s = activeSquad();
        if (s != null && target != null) s.broadcastSpotted(this, target, pos);
    }

    /**
     * Assigns the escort target and connects to its Health.hit signal so
     * EscortState can react immediately when the target takes a hit.
     * Safe to call at runtime from mission code.
     */
    public void setEscortTarget(Character target) {
        escortTarget = target;
        if (target == null) return;
        Node h = target.getNodeOrNull("Health");
        if (h instanceof Health health) {
            health.hit.connectUnsafe(
                    MethodCallable.createUnsafe(this, "onEscortTargetDamaged"),
                    godot.api.Object.ConnectFlags.DEFAULT);
        }
    }

    /** Called when the escort target's Health emits the hit signal. */
    @Register
    public void onEscortTargetDamaged(float amount) {
        if (controller instanceof AIController aiCtrl) aiCtrl.setEscortTargetAttacked();
    }

    /**
     * Lightweight LOD gate. Checks distance to nearest player every 2 s; freezes
     * the AI when all players are beyond LOD_FREEZE_DIST. When frozen, the FSM,
     * all signal emissions, and all AnimationTree writes are skipped entirely.
     * MovementController (_physicsProcess is a separate node) still runs and
     * decelerates the character to rest.
     */
    @Register
    @Override
    public void _physicsProcess(double delta) {
        lodTimer -= delta;
        if (lodTimer <= 0.0) {
            lodTimer = 2.0;
            AILodLevel next = classifyLod(nearestPlayerDist());
            if (next != lodLevel) {
                AILodLevel prev = lodLevel;
                lodLevel = next;
                // Returning to ACTIVE from a tier that didn't run the FSM (FROZEN skips it, PASSIVE
                // holds heading without it): clear stale nav/search state so the AI re-plans from
                // its current position instead of resuming a mid-lunge nav target.
                if (next == AILodLevel.ACTIVE && prev != AILodLevel.ACTIVE
                        && controller instanceof AIController ai) {
                    ai.clearNavTarget();
                    ai.resetSearchTimer();
                }
            }
        }
        if (lodLevel == AILodLevel.FROZEN) return;  // skip entire FSM + animation tick
        // Drop any target/bone references that have been freed or pulled out of the tree (e.g. a
        // zone-streamed body despawned by ZoneManager.unload). Dereferencing such a node's
        // global transform logs "Node not inside tree" and, once it is freed, segfaults — so the
        // FSM must never see a dangling target.
        validateCurrentTarget();
        // PASSIVE: super still runs, but AIController.gatherInput returns a hold-heading command
        // (no NavAgent / FSM) and AnimationController skips its AnimationTree writes.
        super._physicsProcess(delta);
    }

    /**
     * Clears {@link #currentTarget} and all its derived bone caches if the target has been freed or
     * removed from the scene tree. Cheap (a validity + in-tree check) and runs every active frame;
     * the guard is what makes streamed/despawned bodies safe to reference between target scans.
     */
    private void validateCurrentTarget() {
        if (currentTarget == null) return;
        if (!godot.global.GD.isInstanceValid(currentTarget) || !currentTarget.isInsideTree()) {
            currentTarget       = null;
            cachedTargetForBone = null;
            cachedBoneNodes     = null;
            cachedVisibleBone   = null;
            cachedTargetVehicle = null;
            cachedLoS           = false;
        }
    }

    private float nearestPlayerDist() {
        float min = Float.MAX_VALUE;
        Vector3 myPos = getGlobalPosition();
        // O(playerCount) via PlayerRegistry instead of an O(characterCount) group scan +
        // instanceof filter (PLAN.md Part D pre-D1 quick win).
        for (Player p : PlayerRegistry.getPlayers()) {
            if (!godot.global.GD.isInstanceValid(p)) continue;  // defensive: list holds in-tree bodies
            float d = (float) myPos.distanceTo(p.getGlobalPosition());
            if (d < min) min = d;
        }
        return min;  // Float.MAX_VALUE if no players in scene → freeze
    }

    // ── Controller access ─────────────────────────────────────────────────────
    public boolean isDead() { return isDead; }

    public CharacterController getCharacterController() {
        return (controller instanceof CharacterController c) ? c : null;
    }

    // ── Sensing / target discovery ────────────────────────────────────────────

    public Character          getTarget()   { return currentTarget; }
    public void               clearTarget() { currentTarget = null; }
    public NavigationAgent3D  getNavAgent() { return navAgent; }

    /**
     * Full O(n) group scan for the nearest live hostile. Scans both on-foot
     * Characters and Vehicle occupants.
     *
     * Null-faction characters (characterInfo == null or faction == null) are treated
     * as opponents so AI always attacks unknown combatants.
     */
    // Scratch list reused across discoverTarget() scans so a grid query allocates nothing per call.
    private final java.util.List<Node> targetQueryScratch = new java.util.ArrayList<>();
    // Set by evaluateCandidate() as an out-param alongside its return value (single-threaded).
    private float candidateDist;

    private Character discoverTarget() {
        String myFaction = characterInfo != null ? characterInfo.faction : Faction.ENEMY;

        // Squad-shared target (E3) takes priority over a personal scan, so a mate keeps converging on
        // a threat a squad-mate spotted even before it can see the threat itself. getSharedTarget()
        // self-clears a dead/freed target, falling back to the scan below.
        AISquad s = activeSquad();
        if (s != null) {
            Character shared = s.getSharedTarget();
            if (shared != null && shared != this && shared.currentVehicleNode == null) {
                String tf = (shared.characterInfo != null && shared.characterInfo.faction != null)
                        ? shared.characterInfo.faction : "";
                if (Faction.areHostile(myFaction, tf)) return shared;
            }
        }

        float closestDist = Float.MAX_VALUE;
        Character closest = null;
        Vector3 myPos = getGlobalPosition();

        // D1: query only the cells overlapping the detection circle instead of the whole
        // "characters" group. Falls back to the group scan when the grid AutoLoad is absent
        // (e.g. minimal test scenes) so behaviour is identical, just slower, without it.
        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) {
            grid.queryRadius(myPos, behaviorConfigOrDefaults().detectionRange, targetQueryScratch);
            for (Node node : targetQueryScratch) {
                Character candidate = evaluateCandidate(node, myFaction, myPos);
                if (candidate != null && candidateDist < closestDist) {
                    closestDist = candidateDist;
                    closest     = candidate;
                }
            }
        } else {
            for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
                Character candidate = evaluateCandidate(node, myFaction, myPos);
                if (candidate != null && candidateDist < closestDist) {
                    closestDist = candidateDist;
                    closest     = candidate;
                }
            }
        }
        return closest;
    }

    /**
     * Returns the targetable Character for a candidate node — the node itself for an on-foot
     * Character, or the occupant for a Vehicle — when it is a live hostile, else null. On a hit,
     * {@link #candidateDist} is set to the distance used for nearest-selection (vehicle distance is
     * measured to the vehicle, matching the prior behaviour).
     */
    private Character evaluateCandidate(Node node, String myFaction, Vector3 myPos) {
        if (node instanceof Character c) {
            if (c == this || !c.isAlive()) return null;
            // Unknown faction → treated as opponent (empty string is hostile to all named factions).
            String tf = (c.characterInfo != null && c.characterInfo.faction != null)
                    ? c.characterInfo.faction : "";
            if (!Faction.areHostile(myFaction, tf)) return null;
            if (c.currentVehicleNode != null) return null;   // vehicle entry handles this
            candidateDist = (float) myPos.distanceTo(c.getGlobalPosition());
            return c;
        } else if (node instanceof Vehicle v) {
            Character occ = v.getOccupant();
            if (occ == null || !occ.isAlive() || !v.isAlive()) return null;
            String tf = (occ.characterInfo != null && occ.characterInfo.faction != null)
                    ? occ.characterInfo.faction : "";
            if (!Faction.areHostile(myFaction, tf)) return null;
            candidateDist = (float) myPos.distanceTo(v.getGlobalPosition());
            return occ;
        }
        return null;
    }

    /**
     * Nearest world position worth investigating that this AI can currently hear (PLAN.md E2): a
     * GUNSHOT from a hostile/unknown faction, or any EXPLOSION / VEHICLE_CRASH, within
     * {@code hearingRadius} (capped by each stimulus's own audible radius). Ignores its own events and
     * allied gunfire. Returns null when nothing relevant is audible — PatrolState uses it to wake into
     * SearchState toward the sound. No-op (null) without the StimulusManager AutoLoad (test scenes).
     */
    public Vector3 hearAlarm() {
        StimulusManager sm = StimulusManager.get();
        if (sm == null) return null;
        float radius = behaviorConfigOrDefaults().hearingRadius;
        Vector3 myPos = getGlobalPosition();
        String myFaction = characterInfo != null ? characterInfo.faction : Faction.ENEMY;
        Vector3 best = null;
        float bestDist = Float.MAX_VALUE;
        for (StimulusManager.Stimulus s : sm.getStimuli()) {
            if (s.source == this) continue;
            boolean investigate = switch (s.type) {
                case GUNSHOT                  -> Faction.areHostile(myFaction, s.sourceFaction);
                case EXPLOSION, VEHICLE_CRASH -> true;
                default                       -> false;
            };
            if (!investigate) continue;
            float heardWithin = Math.min(radius, s.radius);
            float d = (float) myPos.distanceTo(s.origin);
            if (d <= heardWithin && d < bestDist) { bestDist = d; best = s.origin; }
        }
        return best;
    }

    /**
     * Throttled target scan — runs discoverTarget() at most once per TARGET_SCAN_INTERVAL.
     * Always runs the full scan when the timer fires so the AI switches to the nearest
     * threat (e.g. a player walking past a lower-priority target).
     */
    private void tickTargetScan(double delta) {
        targetScanTimer -= delta;
        if (targetScanTimer > 0) return;
        targetScanTimer = TARGET_SCAN_INTERVAL;

        Character previous = currentTarget;
        currentTarget = discoverTarget();
        if (currentTarget != previous) {
            cachedTargetForBone = null;
            cachedBoneNodes     = null;
            cachedVisibleBone   = null;
            cachedTargetVehicle = null;
            cachedLoS           = false;
            losCacheTimer       = 0;
        }
    }

    /** Throttled target refresh. Call from states that need an up-to-date target. */
    public void refreshTarget(double delta) { tickTargetScan(delta); }

    /**
     * Throttled visibility check for patrol/search detection.
     * Includes distance pre-filter and optional FOV cone (detectionFovDeg).
     */
    public boolean canSeeTarget(double delta) {
        tickTargetScan(delta);
        if (currentTarget == null) return false;
        Vector3 myPos = getGlobalPosition();
        float dist = (float) myPos.distanceTo(currentTarget.getGlobalPosition());
        if (dist > behaviorConfigOrDefaults().detectionRange) return false;

        float fovDeg = behaviorConfigOrDefaults().detectionFovDeg;
        if (fovDeg < 360.0f && movementDirection.lengthSquared() > 0.001f) {
            Vector3 forward  = movementDirection.normalized();
            Vector3 toTarget = currentTarget.getGlobalPosition().minus(myPos).normalized();
            double dotXZ = forward.getX() * toTarget.getX()
                         + forward.getZ() * toTarget.getZ();
            if (dotXZ < Math.cos(Math.toRadians(fovDeg * 0.5))) return false;
        }

        return hasLineOfSight(delta);
    }

    /**
     * Builds the priority-ordered bone list for the current target.
     * Called once per target; result is cached in cachedBoneNodes.
     */
    private void resolveTargetBones() {
        cachedTargetForBone = currentTarget;
        cachedVisibleBone   = null;
        String[] names;
        switch (behaviorConfigOrDefaults().aimBodyPart.toUpperCase()) {
            case "HEAD":  names = new String[]{"head_2",   "spine_03", "spine_01"}; break;
            case "BODY":  names = new String[]{"spine_01", "spine_03", "thigh_l" }; break;
            case "LEGS":  names = new String[]{"thigh_l",  "thigh_r",  "spine_01"}; break;
            default:      names = new String[]{"spine_03", "spine_01", "head_2"  }; break; // CHEST
        }
        cachedBoneNodes = new Node3D[names.length];
        for (int i = 0; i < names.length; i++)
            cachedBoneNodes[i] = currentTarget.getPhysicalBoneNode(names[i]);
    }

    /**
     * LoS check with a short result cache (~3 frames at 60 Hz).
     * On-foot targets: walk the priority bone list, stop at first exposed bone.
     * Vehicle targets: cast to vehicle cabin centre.
     */
    public boolean hasLineOfSight(double delta) {
        if (currentTarget == null || aimRay == null) return false;

        Node currentVehicle = currentTarget.currentVehicleNode;
        if (currentVehicle != cachedTargetVehicle) {
            cachedTargetVehicle = currentVehicle;
            cachedVisibleBone   = null;
            losCacheTimer       = 0;
        }

        losCacheTimer -= delta;
        if (losCacheTimer > 0) return cachedLoS;
        losCacheTimer = LOS_CACHE_INTERVAL;

        if (currentVehicle instanceof Node3D vehicleNode) {
            Vector3 cabin = vehicleNode.getGlobalPosition().plus(new Vector3(0f, 0.5f, 0f));
            aimRay.setTargetPosition(aimRay.toLocal(cabin));
            aimRay.forceRaycastUpdate();
            cachedLoS = !aimRay.isColliding()
                    || (aimRay.getCollider() instanceof Node n
                        && (n == vehicleNode || vehicleNode.isAncestorOf(n)));
            return cachedLoS;
        }

        if (currentTarget != cachedTargetForBone) resolveTargetBones();
        if (cachedBoneNodes == null) { cachedLoS = false; return false; }
        cachedVisibleBone = null;
        for (Node3D bone : cachedBoneNodes) {
            if (bone == null) continue;
            aimRay.setTargetPosition(aimRay.toLocal(bone.getGlobalPosition()));
            aimRay.forceRaycastUpdate();
            if (aimRay.isColliding()
                    && aimRay.getCollider() instanceof Node3D n
                    && currentTarget.isAncestorOf(n)) {
                cachedVisibleBone = bone;
                cachedLoS = true;
                return true;
            }
        }
        cachedLoS = false;
        return false;
    }

    // ── Aim hardware ──────────────────────────────────────────────────────────

    public void aimAtPosition(Vector3 target, double delta) {
        if (aiCamera == null || target == null) return;
        aiCamera.setAimTarget(target);
    }

    public void clearCameraAimTarget() {
        if (aiCamera != null) aiCamera.clearAimTarget();
    }

    /** Bypasses the anti-spam timer; used by AttackState for direct stance control. */
    public void forceSetStance(StanceName stanceName) {
        if (isStanceBlocked(stanceName)) return;
        super.forceSetStance(stanceName);
        setMovementState(currentMovementType);
    }

    public void snapAimRay(Vector3 worldTarget) {
        if (aimRay == null || worldTarget == null) return;
        aimRay.setTargetPosition(aimRay.toLocal(worldTarget));
        aimRay.forceRaycastUpdate();
    }

    /**
     * World position to aim at on the current target.
     * Vehicle occupant: vehicle cabin centre. On foot: last confirmed visible bone.
     */
    public Vector3 getAimBonePosition() {
        if (currentTarget.currentVehicleNode instanceof Node3D vn)
            return vn.getGlobalPosition().plus(new Vector3(0f, 0.5f, 0f));
        if (cachedVisibleBone != null) return cachedVisibleBone.getGlobalPosition();
        if (cachedBoneNodes != null && cachedBoneNodes.length > 0 && cachedBoneNodes[0] != null)
            return cachedBoneNodes[0].getGlobalPosition();
        Node3D headBone = currentTarget.getPhysicalBoneNode("head_2");
        return headBone != null ? headBone.getGlobalPosition() : currentTarget.getGlobalPosition();
    }

    /**
     * hitChance reduced proportionally by current speed.
     * Stationary = full hitChance; at 4 m/s = hitChance × (1 − moveAccuracyPenalty).
     */
    public float computeEffectiveHitChance() {
        float moveFactor = Math.min(1.0f, (float) getVelocity().length() / 4.0f);
        return behaviorConfigOrDefaults().hitChance * (1.0f - moveFactor * behaviorConfigOrDefaults().moveAccuracyPenalty);
    }

    /**
     * Hit → exact aim-bone position. Miss → random scatter:
     *   aimScatterRadius × (hDist/10) + hDist × tan(weaponSpreadDeg)
     */
    public Vector3 computeAimTarget(boolean isHit, float hDist) {
        Vector3 base = getAimBonePosition();
        if (isHit) return base;
        float weaponSpreadM = 0f;
        if (weaponController != null) {
            float spreadDeg = weaponController.getCurrentSpreadDeg();
            weaponSpreadM = hDist * (float) Math.tan(Math.toRadians(spreadDeg));
        }
        float maxOffset = behaviorConfigOrDefaults().aimScatterRadius * (hDist / 10f) + weaponSpreadM;
        float offset    = godot.global.GD.randf() * maxOffset;
        float angle     = godot.global.GD.randf() * (float) (Math.PI * 2.0);
        return base.plus(new Vector3(
                offset * (float) Math.cos(angle),
                offset * (float) Math.sin(angle), 0f));
    }

    // ── Weapon / ammo ─────────────────────────────────────────────────────────

    public int selectBestWeapon() {
        if (cachedBestWeapon >= 0) return cachedBestWeapon;
        if (weaponController == null) { cachedBestWeapon = 0; return 0; }
        int bestIndex = -1;
        float bestDamage = -1f;
        for (int i = 1; i < weaponController.getSlotCount(); i++) {
            if (!weaponController.hasAmmoForWeapon(i)) continue;
            WeaponItem s = weaponController.getWeaponItem(i);
            if (s != null && s.damage > bestDamage) { bestDamage = s.damage; bestIndex = i; }
        }
        cachedBestWeapon = bestIndex >= 0 ? bestIndex : 0;
        return cachedBestWeapon;
    }

    /**
     * True when the selected weapon can actually be fired — including the permanent
     * fist (slot 0, isInfiniteAmmo). selectBestWeapon() deliberately skips slot 0 when
     * ranking *preferred* weapons (it's the fallback, not a pick), so checking
     * `selectBestWeapon() > 0` here would wrongly mark a fist-only AI as "out of ammo"
     * and route it to RefillAmmoState/FleeState forever instead of letting it brawl.
     */
    public boolean hasAnyAmmo() {
        return weaponController != null && weaponController.hasAmmoForWeapon(selectBestWeapon());
    }

    /**
     * Engagement range to use for Attack-state transitions: the smaller of the
     * behaviour config's general attackRange and the selected weapon's effective
     * range. Without this a melee AI (weaponRange ~1.5 m) would try to fight from
     * AIBehaviorConfig.attackRange away — strafing and "shooting" at empty air
     * instead of closing the distance to where its weapon can actually connect.
     */
    /** True when the AI's selected weapon is melee/fist — used to switch off ranged-style strafing. */
    public boolean isMeleeEngagement() {
        if (weaponController == null) return false;
        WeaponItem weapon = weaponController.getWeaponItem(selectBestWeapon());
        return weapon != null && weapon.getWeaponType() == WeaponType.MELEE;
    }

    public float getEffectiveAttackRange() {
        float configRange = behaviorConfigOrDefaults().attackRange;
        if (weaponController == null) return configRange;
        WeaponItem weapon = weaponController.getWeaponItem(selectBestWeapon());
        if (weapon == null) return configRange;
        return Math.min(configRange, weapon.getEffectiveRange());
    }

    @Register
    public void onAmmoChanged(int magazine, int reserve) { cachedBestWeapon = -1; }

    public boolean isAtAmmoRefill() {
        if (ammoRefill == null) return false;
        return (float) getGlobalPosition().distanceTo(ammoRefill.getGlobalPosition()) <= 1.5f;
    }

    // ── Navigation ────────────────────────────────────────────────────────────

    public void setNextPatrolTarget() {
        float angle = godot.global.GD.randf() * (float) Math.PI * 2.0f;
        float dist  = godot.global.GD.randf() * behaviorConfigOrDefaults().patrolRadius;
        navAgent.setTargetPosition(spawnPosition.plus(new Vector3(
                (float) Math.cos(angle) * dist, 0.0f, (float) Math.sin(angle) * dist)));
    }

    // ── Signal receivers ──────────────────────────────────────────────────────

    @Register
    public void onEnemyDamaged(float amount) {
        CharacterController ctrl = getCharacterController();
        if (ctrl != null) ctrl.onDamagedByAttacker(currentTarget);
        // Being shot is a confirmed sighting too (E3): rally the squad onto the attacker. The damage
        // signal only carries the amount, so we use currentTarget (the AI's believed attacker) — set
        // when it already sees the shooter, which is the common "shoot one, the squad turns" case.
        if (currentTarget != null) broadcastToSquad(currentTarget, currentTarget.getGlobalPosition());
    }

    /**
     * React to being carjacked (PLAN.md I3c) — called host-side on the ejected driver right after it is
     * unseated and back on foot. {@code AIBehaviorConfig.reactToCarjack} decides the response:
     * <ul>
     *   <li>{@code FLEE} (default, civilian) — panic and run from the carjacker ({@link FleeState}).
     *   <li>{@code FIGHT} (gang/hostile) — flip this body's allegiance to {@code ENEMY} (a targeted,
     *       host-authoritative faction swap replicated over {@code WORLD_EVENT_FACTION_SWAP}) and engage
     *       the carjacker via the normal Attack/Chase path; squad mates can join for free.
     * </ul>
     */
    public void reactToCarjack(Character carjacker) {
        if (carjacker == null || !(controller instanceof AIController ai)) return;
        if ("FIGHT".equalsIgnoreCase(behaviorConfigOrDefaults().reactToCarjack)) {
            setFaction(Faction.ENEMY);
            adoptSquadTarget(carjacker, carjacker.getGlobalPosition());
        } else {
            ai.forceFlee(carjacker.getGlobalPosition());
        }
    }

    @Register
    @Override
    public void onDied() {
        isDead = true;
        super.onDied();
    }

    /**
     * Re-initialise this body for a (re)spawn — PLAN.md Part E / E1. Called by ZoneManager
     * after the body is (re)added to the tree: repositions it, re-anchors the patrol center to the
     * new position, full-heals, clears all sensor caches + FSM memory, and re-registers in the
     * spatial grid. Safe on a fresh instance too (a harmless re-init over what {@code _ready} did).
     *
     * <p>A pooled body's {@code _ready()} does NOT run again on tree re-entry, so the work
     * {@code _ready} normally does for spawn placement (capturing {@code spawnPosition}) and the
     * grid registration must be redone here explicitly.
     */
    public void activateForSpawn(Vector3 worldPos) {
        setGlobalPosition(worldPos);
        spawnPosition = new Vector3(getGlobalPosition());
        isDead = false;

        currentTarget       = null;
        cachedTargetForBone = null;
        cachedBoneNodes     = null;
        cachedVisibleBone   = null;
        cachedTargetVehicle = null;
        cachedLoS           = false;
        cachedBestWeapon    = -1;
        lastEmittedMoveType   = null;
        lastEmittedMoveStance = null;

        lodLevel        = AILodLevel.ACTIVE;
        lodTimer        = godot.global.GD.randfRange(0f, 2.0f);
        targetScanTimer = godot.global.GD.randfRange(0f, (float) TARGET_SCAN_INTERVAL);
        losCacheTimer   = godot.global.GD.randfRange(0f, (float) LOS_CACHE_INTERVAL);

        // Drop references carried over from the previous life so a recycled body never points at a
        // freed node: the camera's aim target and any escort target (and its Health.hit connection).
        clearCameraAimTarget();
        if (escortTarget != null) setEscortTarget(null);

        if (healthNode != null) healthNode.resetFull();

        SpatialEntityGrid grid = SpatialEntityGrid.get();
        if (grid != null) grid.register(this, getGlobalPosition());

        if (controller instanceof AIController aiCtrl) aiCtrl.resetState();
    }
}

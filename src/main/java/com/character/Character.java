package com.character;

import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.annotation.RegisterSignal;
import godot.api.*;
import godot.core.*;
import godot.global.GD;

import java.lang.Math;

@RegisterClass
public class Character extends CharacterBody3D {

    // ── Signals ──────────────────────────────────────────────────────────────
    @RegisterSignal
    public final Signal1<JumpState> pressedJump = new Signal1<>(this, new StringName("pressed_jump"));

    @RegisterSignal
    public final Signal1<RollState> pressedRoll = new Signal1<>(this, new StringName("pressed_roll"));

    @RegisterSignal
    public final Signal1<Stance> changedStance = new Signal1<>(this, new StringName("changed_stance"));

    @RegisterSignal
    public final Signal0 fireWeapon = new Signal0(this, new StringName("fire_weapon"));

    @RegisterSignal
    public final Signal0 notFireWeapon = new Signal0(this, new StringName("not_fire_weapon"));

    @RegisterSignal
    public final Signal1<MovementState> changedMovementState = new Signal1<>(this, new StringName("changed_movement_state"));

    @RegisterSignal
    public final Signal1<Vector3> changedMovementDirection = new Signal1<>(this, new StringName("changed_movement_direction"));

    @RegisterSignal
    public final Signal1<CombatState> changedCombatState = new Signal1<>(this, new StringName("changed_combat_state"));

    @RegisterSignal
    public final Signal1<Integer> changedWeapon = new Signal1<>(this, new StringName("changed_weapon"));

    @RegisterSignal
    public final Signal0 reloadWeapon = new Signal0(this, new StringName("reload_weapon"));

    // ── Exports ───────────────────────────────────────────────────────────────
    @RegisterProperty
    public int maxAirJump = 1;

    @Export
    @RegisterProperty
    public Dictionary<String, JumpState> jumpStates = new Dictionary<>(String.class, JumpState.class);

    @Export
    @RegisterProperty
    public Dictionary<String, NodePath> stances = new Dictionary<>(String.class, NodePath.class);

    @Export
    @RegisterProperty
    public Dictionary<String, CombatState> combatStates = new Dictionary<>(String.class, CombatState.class);

    @Export
    @RegisterProperty
    public RollState rollState = null;

    @RegisterProperty
    @Export
    public WeaponController weaponController;

    @RegisterProperty
    @Export
    public NodePath cameraRootPath = new NodePath("CameraRoot");

    @RegisterProperty
    @Export
    public NodePath aimTargetPath = new NodePath("CameraRoot/Yaw/Pitch/Pivot/SpringArm/Camera/AimTarget");

    @RegisterProperty
    @Export
    public NodePath aimRayPath = new NodePath("CameraRoot/Yaw/Pitch/Pivot/SpringArm/Camera/AimRay");

    @RegisterProperty
    @Export
    public NodePath physicalBoneSimulatorPath = new NodePath("MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/PhysicalBoneSimulator3D");

    // ── Protected state ───────────────────────────────────────────────────────
    protected int airJumpCounter = 0;
    protected Vector3 movementDirection = new Vector3();
    protected StanceName currentStanceName = StanceName.UPRIGHT;
    protected MovementType currentMovementType = MovementType.IDLE;
    protected boolean isRolling = false;
    protected boolean combat = false;

    protected Timer stanceAntispamTimer;
    protected Timer rollTimer;
    protected Health healthNode;
    protected Marker3D aimTarget;
    protected RayCast3D aimRay;

    protected Node3D cameraRoot;
    protected PhysicalBoneSimulator3D physicalBoneSimulator;

    // ── Tick counter (stamped onto every CharacterInput for network ordering) ─
    protected long currentTick = 0;

    // ── Lifecycle ─────────────────────────────────────────────────────────────
    @RegisterFunction
    @Override
    public void _ready() {
        if (hasNode("StanceAntispamTimer")) {
            stanceAntispamTimer = (Timer) getNode("StanceAntispamTimer");
        }
        if (hasNode("RollTimer")) {
            rollTimer = (Timer) getNode("RollTimer");
            if (rollTimer != null && rollState != null) {
                rollTimer.setWaitTime(rollState.getRollDuration());
            }
        }
        healthNode = (Health) getNode("Health");
        if (hasNode(aimTargetPath)) {
            aimTarget = (Marker3D)  getNode(aimTargetPath);
        }
        if (hasNode(aimRayPath)) {
            aimRay = (RayCast3D) getNode(aimRayPath);

        }

        if (hasNode(cameraRootPath)) {
            cameraRoot = (Node3D) getNode(cameraRootPath);
        }
        if (physicalBoneSimulatorPath != null && !physicalBoneSimulatorPath.isEmpty() && hasNode(physicalBoneSimulatorPath)) {
            physicalBoneSimulator = (PhysicalBoneSimulator3D) getNode(physicalBoneSimulatorPath);
            for (int i = 0; i < physicalBoneSimulator.getChildCount(); i++) {
                Node child = physicalBoneSimulator.getChild(i);
                if (child instanceof PhysicalBone3D bone) {
                    // let the character's own bone to be exception list
                    aimRay.addException(bone);
                }
            }
        }

        changedMovementDirection.emit(Vector3.Companion.getBACK());
        setMovementState(MovementType.IDLE);
        setStance(currentStanceName);
        setCombatState();
        setWeapon(0);
    }

    // ── Physics loop: gather → apply ─────────────────────────────────────────
    @RegisterFunction
    @Override
    public void _physicsProcess(double delta) {
        CharacterInput input = gatherInput(delta);
        input.tick = currentTick++;
        applyInput(input, delta);
    }

    /**
     * Produce a CharacterInput for this tick.
     *
     * Override in subclasses:
     *   - Player  : sample Input singleton (keyboard/mouse)
     *   - Enemy   : run AI FSM and translate decisions into fields
     *   - Network : pop from received-input buffer (future)
     *
     * The base implementation returns an empty (no-op) input.
     */
    protected CharacterInput gatherInput(double delta) {
        return new CharacterInput();
    }

    /**
     * Apply a CharacterInput snapshot to this character's state.
     *
     * All signal emissions and state transitions live here so that any
     * input source (local, AI, network) produces identical results.
     */
    protected void applyInput(CharacterInput input, double delta) {

        // ── Movement direction ─────────────────────────────────────────────
        if (input.movementDirection.lengthSquared() > 0.001) {
            movementDirection = input.movementDirection;
            changedMovementDirection.emit(movementDirection);
        }
        setMovementState(input.movementType);

        // ── Floor / jump counter ───────────────────────────────────────────
        if (isOnFloor()) {
            airJumpCounter = 0;
        } else if (airJumpCounter == 0) {
            airJumpCounter = 1;
        }

        // ── Combat state ───────────────────────────────────────────────────
        if (input.wantCombat != combat) {
            combat = input.wantCombat;
            setCombatState();
        }

        // ── Aim target ─────────────────────────────────────────────────────
        if (input.aimTargetPosition != null && aimTarget != null) {
            aimTarget.setGlobalPosition(input.aimTargetPosition);
        }
        
        // ── Fire / not-fire ────────────────────────────────────────────────
        if (!isRolling) {
            if (input.fire) {
                fireWeapon.emit();
            } else {
                notFireWeapon.emit();
            }
        } else {
            notFireWeapon.emit();
        }

        // ── Reload ─────────────────────────────────────────────────────────
        if (input.reload) {
            reloadWeapon.emit();
        }

        // ── Jump ───────────────────────────────────────────────────────────
        if (input.jump && !isRolling && airJumpCounter <= maxAirJump) {
            if (!isStanceBlocked(StanceName.UPRIGHT)) {
                if (currentStanceName != StanceName.UPRIGHT) {
                    setStance(StanceName.UPRIGHT);
                } else {
                    String jumpName = (airJumpCounter > 0) ? "AirJump" : "GroundJump";
                    JumpState js = jumpStates.get(jumpName);
                    if (js != null) {
                        pressedJump.emit(js);
                    }
                    airJumpCounter++;
                }
            }
        }

        // ── Roll ───────────────────────────────────────────────────────────
        if (input.roll && !isRolling && isOnFloor() &&
                movementDirection.lengthSquared() > 0.001 &&
                (weaponController == null || !weaponController.isWeaponReloading())) {
            if (rollTimer == null || rollTimer.getTimeLeft() <= 0) {
                roll(true);
            }
        }

        // ── Stance ─────────────────────────────────────────────────────────
        if (input.desiredStance != null && isOnFloor() &&
                (rollTimer == null || rollTimer.getTimeLeft() <= 0)) {
            setStance(input.desiredStance);
        }

        // ── Weapon switch ──────────────────────────────────────────────────
        if (input.desiredWeapon >= 0) {
            setWeapon(input.desiredWeapon);
        }
    }

    // ── Shared helpers ────────────────────────────────────────────────────────
    protected void setCombatState() {
        changedCombatState.emit(combatStates.get(combat ? "Combat" : "NoCombat"));
    }

    @RegisterFunction
    public void completedRoll() {
        roll(false);
    }

    protected void roll(boolean isRoll) {
        isRolling = isRoll;

        if (currentStanceName != StanceName.CROUCH) {
            StanceName disabledStanceName = isRoll ? currentStanceName : StanceName.CROUCH;
            StanceName enabledStanceName  = isRoll ? StanceName.CROUCH  : currentStanceName;

            NodePath disabledPath = stances.get(disabledStanceName.getKey());
            if (disabledPath != null) {
                Stance s = (Stance) getNode(disabledPath);
                if (s != null && s.getCollider() != null) s.getCollider().setDisabled(true);
            }

            NodePath enabledPath = stances.get(enabledStanceName.getKey());
            if (enabledPath != null) {
                Stance s = (Stance) getNode(enabledPath);
                if (s != null && s.getCollider() != null) s.getCollider().setDisabled(false);
            }
        }

        if (isRoll) {
            if (rollTimer != null) {
                rollTimer.start();
            }
            pressedRoll.emit(rollState);
        }
    }

    public void setMovementState(MovementType type) {
        NodePath path = stances.get(currentStanceName.getKey());
        if (path == null) return;
        Stance stanceNode = (Stance) getNode(path);
        if (stanceNode == null) return;
        currentMovementType = type;
        changedMovementState.emit(stanceNode.getMovementState(type));
    }

    protected void setStance(StanceName stanceName) {
        if (stanceAntispamTimer != null && stanceAntispamTimer.getTimeLeft() > 0) return;

        if (stanceAntispamTimer != null && getTree() != null) {
            stanceAntispamTimer.start();
        }

        StanceName next = (stanceName == currentStanceName) ? StanceName.UPRIGHT : stanceName;
        if (isStanceBlocked(next)) return;

        NodePath currentPath = stances.get(currentStanceName.getKey());
        if (currentPath != null) {
            Stance s = (Stance) getNode(currentPath);
            if (s != null && s.getCollider() != null) s.getCollider().setDisabled(true);
        }

        currentStanceName = next;
        NodePath nextPath = stances.get(currentStanceName.getKey());
        if (nextPath != null) {
            Stance s = (Stance) getNode(nextPath);
            if (s != null) {
                if (s.getCollider() != null) s.getCollider().setDisabled(false);
                changedStance.emit(s);
            }
        }

        setMovementState(currentMovementType);
    }

    protected boolean isStanceBlocked(StanceName stanceName) {
        NodePath path = stances.get(stanceName.getKey());
        if (path == null) return false;
        Stance s = (Stance) getNode(path);
        return (s != null) && s.isBlocked();
    }

    public void setMovementDirection(Vector3 movementDirection) {
        this.movementDirection = movementDirection;
    }

    public void setWeapon(int weapon) {
        changedWeapon.emit(weapon);
    }

    // ── Ragdoll ───────────────────────────────────────────────────────────────
    protected void enableRagdoll() {
        setPhysicsProcess(false);
        if (physicalBoneSimulator == null) return;
        for (int i = 0; i < physicalBoneSimulator.getChildCount(); i++) {
            Node child = physicalBoneSimulator.getChild(i);
            if (child instanceof PhysicalBone3D bone) {
                // Allow ragdoll bones to rest on world geometry (layer 1)
                bone.setCollisionLayer(1);
            }
        }
        physicalBoneSimulator.physicalBonesStartSimulation();
        GD.print("enableRagdoll");
    }

    /**
     * Apply a physics impulse to a named bone during ragdoll.
     * Only has effect when the ragdoll is active (call after enableRagdoll or on death).
     */
    public void applyBoneImpulse(String boneName, Vector3 impulse) {
        if (physicalBoneSimulator == null) return;
        for (int i = 0; i < physicalBoneSimulator.getChildCount(); i++) {
            Node child = physicalBoneSimulator.getChild(i);
            if (child instanceof PhysicalBone3D bone && boneName.equalsIgnoreCase(String.valueOf(bone.getName()))) {
                bone.applyCentralImpulse(impulse);
                return;
            }
        }
    }

    // ── Override in subclasses ────────────────────────────────────────────────
    @RegisterFunction
    public void onDied() {
        GD.print(getName() + " died");
        // Disable animation tree
        AnimationTree animationTree = (AnimationTree) getNode("AnimationTree");
        animationTree.setActive(false);

        enableRagdoll();

        // Disabled current stance to let ragdoll take over
            NodePath enabledPath = stances.get(currentStanceName.getKey());
            if (enabledPath != null) {
                Stance s = (Stance) getNode(enabledPath);
                if (s != null && s.getCollider() != null) s.getCollider().setDisabled(true);
            }
    }
}

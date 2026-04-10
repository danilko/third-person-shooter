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
    public final Signal1<JumpState> pressedJump = Signal1.create(this, "pressedJump");

    @RegisterSignal
    public final Signal1<RollState> pressedRoll = Signal1.create(this, "pressedRoll");

    @RegisterSignal
    public final Signal1<Stance> changedStance = Signal1.create(this, "changedStance");

    @RegisterSignal
    public final Signal0 fireWeapon = Signal0.create(this, "fireWeapon");

    @RegisterSignal
    public final Signal0 notFireWeapon = Signal0.create(this, "notFireWeapon");

    @RegisterSignal
    public final Signal1<MovementState> changedMovementState = Signal1.create(this, "changedMovementState");

    @RegisterSignal
    public final Signal1<Vector3> changedMovementDirection = Signal1.create(this, "changedMovementDirection");

    @RegisterSignal
    public final Signal1<CombatState> changedCombatState = Signal1.create(this, "changedCombatState");

    @RegisterSignal
    public final Signal1<Integer> changedWeapon = Signal1.create(this, "changedWeapon");

    @RegisterSignal
    public final Signal0 reloadWeapon = Signal0.create(this, "reloadWeapon");

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
    public NodePath aimTargetPath = new NodePath("AimTarget");

    @RegisterProperty
    @Export
    public NodePath aimRayPath = new NodePath("AimRay");

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

        // ── AimRay → AimTarget (WeaponController raycast origin) ──────────
        if (combat && aimTarget != null && aimRay != null) {
            aimRay.setTargetPosition(aimRay.toLocal(aimTarget.getGlobalPosition()));
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
            reloadWeapon.emitSignal();
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

    // ── Override in subclasses ────────────────────────────────────────────────
    @RegisterFunction
    public void onDied() {
        GD.print(getName() + " died");
    }
}

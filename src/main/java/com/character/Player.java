package com.character;

import com.game.EventBus;
import com.ui.CharacterHUD;
import godot.annotation.Export;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.annotation.RegisterProperty;
import godot.api.*;
import godot.core.*;
import godot.global.GD;

@RegisterClass
public class Player extends Character {

    // ── Player-specific exports ───────────────────────────────────────────────
    @RegisterProperty
    @Export
    public NodePath rayCastNodePath = new NodePath("CameraRoot/Yaw/Pitch/Pivot/SpringArm/Camera/RayCast3D");

    @RegisterProperty
    @Export
    public NodePath hudPath = new NodePath("UI");

    // ── Player-specific nodes ─────────────────────────────────────────────────
    private RayCast3D rayCast3D;
    private CharacterHUD hud;
    private Timer aimStayTimer;

    // ── Lifecycle ─────────────────────────────────────────────────────────────
    @RegisterFunction
    @Override
    public void _ready() {
        super._ready();

        aimStayTimer = (Timer) getNode("AimStayTimer");

        rayCast3D = (RayCast3D) getNode(rayCastNodePath);
        rayCast3D.addException(this);

        if (hasNode(hudPath)) {
            hud = (CharacterHUD) getNode(hudPath);
            hud.onHealthChanged(healthNode.getCurrentHealth());
        }
    }

    // ── Input gathering (human input → CharacterInput) ────────────────────────
    /**
     * Polls the Input singleton and assembles a CharacterInput for this tick.
     *
     * All keyboard/mouse sampling is centralised here so the same struct can
     * later be produced by a network layer (received from server) without
     * touching any other code path.
     */
    @Override
    protected CharacterInput gatherInput(double delta) {
        CharacterInput input = new CharacterInput();

        Input inp = Input.INSTANCE;

        // ── Movement ──────────────────────────────────────────────────────
        float moveX = inp.getActionStrength("left")    - inp.getActionStrength("right");
        float moveZ = inp.getActionStrength("forward") - inp.getActionStrength("back");
        input.movementDirection.setX(moveX);
        input.movementDirection.setZ(moveZ);

        if (input.movementDirection.lengthSquared() > 0.001) {
            input.movementType = inp.isActionPressed("walk", false)
                    ? MovementType.WALK
                    : MovementType.SPRINT;
        }

        // ── Combat / aim ──────────────────────────────────────────────────
        boolean aimOrFire = inp.isActionPressed("aim", false)
                         || inp.isActionPressed("fire", false);

        if (aimOrFire) {
            aimStayTimer.stop();
        } else if (combat && (inp.isActionJustReleased("aim", false)
                           || inp.isActionJustReleased("fire", false))) {
            // Start the hold-window exactly once: on the frame aim/fire is released.
            // Using isActionJustReleased avoids restarting the timer every frame after
            // it finishes (which would prevent combat from ever exiting).
            aimStayTimer.start();
        }

        // Stay in combat while pressing OR while the hold-window timer is still running
        input.wantCombat = aimOrFire || (combat && !aimStayTimer.isStopped());

        // ── Aim target: derive from camera raycast ─────────────────────────
        if (input.wantCombat) {
            // Reset horizontal spread rotation added by WeaponController
            Vector3 rayDeg = rayCast3D.getRotationDegrees();
            rayCast3D.setRotationDegrees(new Vector3(rayDeg.getX(), 0.0f, 0.0f));

            if (rayCast3D.isColliding() &&
                    rayCast3D.getCollisionPoint()
                             .minus(rayCast3D.getGlobalTransform().getOrigin())
                             .length() > 0.1) {
                input.aimTargetPosition = rayCast3D.getCollisionPoint();
            } else {
                input.aimTargetPosition = rayCast3D.toGlobal(rayCast3D.getTargetPosition());
            }
        }

        // ── Fire ──────────────────────────────────────────────────────────
        input.fire = inp.isActionPressed("fire", false);

        // ── Reload ────────────────────────────────────────────────────────
        input.reload = inp.isActionJustPressed("reload", false);

        // ── Jump ──────────────────────────────────────────────────────────
        input.jump = inp.isActionJustPressed("jump", false);

        // ── Roll ──────────────────────────────────────────────────────────
        input.roll = inp.isActionJustPressed("roll", false);

        // ── Stance toggle ─────────────────────────────────────────────────
        for (String stanceKey : stances.keys()) {
            if (inp.isActionJustPressed(stanceKey.toLowerCase(), false)) {
                input.desiredStance = StanceName.fromKey(stanceKey);
                break;
            }
        }

        return input;
    }

    // ── Signal receivers ──────────────────────────────────────────────────────
    @RegisterFunction
    @Override
    public void onDied() {
        setProcessInput(false);
        Node eventBusNode = getNodeOrNull("/root/EventBus");
        if (eventBusNode instanceof EventBus) {
            ((EventBus) eventBusNode).playerDied.emit();
        }
    }

    @RegisterFunction
    public void onPlayerDamaged(float amount) {
        if (hud != null) {
            hud.onHealthChanged(healthNode.getCurrentHealth());
        }
    }
}

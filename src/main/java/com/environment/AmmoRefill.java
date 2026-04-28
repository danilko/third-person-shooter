package com.environment;

import com.character.WeaponController;
import com.game.EventBus;
import godot.annotation.RegisterClass;
import godot.annotation.RegisterFunction;
import godot.api.Area3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.core.NodePath;
import godot.global.GD;

/**
 * Refills all weapons for any character that enters the area.
 * Emits EventBus.ammoPickedUp so the HUD and other listeners can react.
 *
 * body_entered fires with two possible payloads:
 *   - CharacterBody3D root (Player/Enemy)  → body itself has WeaponController
 *   - PhysicalBone3D child (ragdoll)        → body.getOwner() has WeaponController
 */
@RegisterClass(className = "AmmoRefill")
public class AmmoRefill extends Area3D {

    private static final NodePath WEAPON_CONTROLLER = new NodePath("WeaponController");

    @RegisterFunction
    public void onBodyEntered(Node3D body) {

        Node character = resolveCharacter(body);
        if (character == null) {
           return;
        }

        WeaponController wc = (WeaponController) character.getNode(WEAPON_CONTROLLER);
        wc.fillWeaponAmmo();

        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) {
            bus.ammoPickedUp.emit(0);
        }
    }

    /**
     * Instanced scene roots (Player, Enemy) return null from getOwner().
     * Their physics children (PhysicalBone3D) return the character root from getOwner().
     */
    private Node resolveCharacter(Node3D body) {
        if (body.hasNode(WEAPON_CONTROLLER)) return body;
        Node owner = body.getOwner();
        if (owner != null && owner.hasNode(WEAPON_CONTROLLER)) return owner;
        return null;
    }
}

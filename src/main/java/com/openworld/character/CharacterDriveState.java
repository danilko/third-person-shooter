package com.openworld.character;

import com.openworld.util.CollisionLayers;
import com.openworld.carrier.vehicle.VehicleWeaponMode;
import godot.api.Node;
import godot.api.Node3D;
import godot.core.Vector3;
import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.movement.character.MovementController;
import com.openworld.movement.character.StanceName;
import com.openworld.net.NetworkController;
import com.openworld.net.NetworkManager;

/**
 * Vehicle drive-state transitions extracted from {@link Character} (WS5 god-class split).
 *
 * Plain collaborator — NOT a {@code @Script} Node — owned by a single {@link Character},
 * so the extraction is scene-safe (no {@code .gdj}, no scene {@code ext_resource} change).
 * {@code Character} keeps the public {@code enterDriveState}/{@code exitDriveState}/
 * {@code applyPassengerWeaponInput} surface that {@code Vehicle} calls and delegates here.
 *
 * The pre-drive snapshot (stance/combat/rotation, restored on exit) lives here. The
 * outward-visible drive flags ({@code vehicleWeaponMode}/{@code currentVehicleNode}) stay on
 * {@code Character} because other systems (NetworkController, NetworkManager) read them.
 */
final class CharacterDriveState {

    private final Character owner;

    private StanceName preDriveStance   = StanceName.UPRIGHT;
    private boolean    preDriveCombat   = false;
    private Vector3    preDriveRotation = Vector3.Companion.getZERO();

    CharacterDriveState(Character owner) {
        this.owner = owner;
    }

    /**
     * Puts the character into the DRIVE_CARRIER state for the given weapon mode.
     * Handles collision, stance, combat state, and physics processing — Vehicle only needs
     * to hot-swap the controller after this returns.
     */
    void enter(VehicleWeaponMode mode, Node vehicleNode) {
        enter(mode, vehicleNode, true);
    }

    /**
     * @param isDriver seat 0 = true: processing is turned OFF (input flows through the
     *        vehicle's hot-swapped controller). Passengers keep their processing ON so their
     *        own controller still gathers input — {@code Character.applyInput} reduces it to
     *        weapon-use while seated (the GTA drive-by model); movement is dead anyway
     *        (MovementController off, body pinned to the seat by the vehicle each tick).
     */
    void enter(VehicleWeaponMode mode, Node vehicleNode, boolean isDriver) {
        preDriveStance    = owner.currentStanceName;
        preDriveCombat    = owner.combat;
        preDriveRotation  = owner.getGlobalRotation();
        owner.vehicleWeaponMode = mode;
        owner.currentVehicleNode = vehicleNode;
        owner.vehicleDriver = isDriver;
        owner.setCollisionLayer(0);  // remove from all layers while in vehicle
        owner.forceSetStance(StanceName.DRIVE_CARRIER);
        // Reset the MeshRoot local rotation so the mesh aligns with the body.
        // MovementController normally controls this; since it is disabled the mesh
        // would otherwise stay at whatever facing angle it had when the player stopped.
        Node meshRoot = null;
        if (owner.visualsInstance != null && owner.meshConfig != null && !owner.meshConfig.meshRootPath.isEmpty()) {
            meshRoot = owner.visualsInstance.getNodeOrNull(owner.meshConfig.meshRootPath);
        }
        if (meshRoot == null) meshRoot = owner.getNodeOrNull("MeshRoot");
        if (meshRoot instanceof Node3D mr) mr.setRotation(Vector3.Companion.getZERO());
        // Show character weapon for any mode where the vehicle has no weapon of its own.
        // PASSENGER_WEAPON: character fires their weapon via vehicle camera → must be in combat.
        // NONE: vehicle has no weapon; character still holds their weapon visually while riding.
        // VEHICLE_WEAPON: vehicle fires its own weapon; leave the character's combat state as-is.
        if (mode != VehicleWeaponMode.VEHICLE_WEAPON) {
            owner.combat = true;
            owner.setCombatState();
        }
        // The carrier's camera is the carrier's own -- over the bonnet, not behind this
        // character's eyes -- so the head comes back the moment the seat is taken, whatever the
        // on-foot view was. Called here as well as per-frame so the swap lands on THIS frame.
        owner.refreshHeadVisibility();
        if (isDriver) {
            owner.setProcess(false);
            owner.setPhysicsProcess(false);
        }
        Node mc = owner.getNodeOrNull("MovementController");
        if (mc != null) mc.setPhysicsProcess(false);
    }

    /** Restores the character's pre-drive state. Called by {@code Vehicle.tryExit} before the controller is returned. */
    void exit() {
        owner.currentVehicleNode = null;
        owner.vehicleDriver = false;
        // Restore body rotation so MovementController's playerInitRotation stays valid.
        owner.setGlobalRotation(preDriveRotation);
        owner.setCollisionLayer(CollisionLayers.CHARACTER);
        owner.setProcess(true);
        owner.setPhysicsProcess(true);
        Node mc = owner.getNodeOrNull("MovementController");
        if (mc != null) mc.setPhysicsProcess(true);
        owner.forceSetStance(preDriveStance);
        owner.combat = preDriveCombat;
        owner.setCombatState();
        owner.vehicleWeaponMode = VehicleWeaponMode.NONE;
        // ... and it goes again on foot if the player was in FPS when they got in.
        owner.refreshHeadVisibility();
    }

    /**
     * Relays fire/reload commands from the vehicle controller to this character's weapon system
     * each physics frame. Only called when {@code vehicleWeaponMode == PASSENGER_WEAPON}.
     *
     * @param fire          true while the fire button is held
     * @param reload        true on the frame the reload button is just-pressed
     * @param desiredWeapon weapon slot to switch to, or negative for no change
     * @param aimTargetPos  world-space point the vehicle camera is aimed at
     */
    void applyPassengerWeaponInput(boolean fire, boolean reload, int desiredWeapon, Vector3 aimTargetPos) {
        // Through Character.applySeatedAimTarget, never the marker directly: the carrier's firing
        // sector is applied there, once, for the bones and the bullet and the reticle alike.
        owner.applySeatedAimTarget(aimTargetPos);
        if (fire) owner.fireWeapon.emit();
        else      owner.notFireWeapon.emit();
        if (reload) owner.reloadWeapon.emit();
        if (desiredWeapon >= 0) owner.setWeapon(desiredWeapon);
    }
}

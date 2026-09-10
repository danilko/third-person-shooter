package com.openworld.character;

import godot.annotation.Export;
import godot.annotation.Script;
import godot.api.Resource;
import godot.core.Dictionary;
import godot.core.NodePath;
import godot.core.VariantArray;
import com.openworld.movement.character.Stance;

/**
 * Per-mesh configuration resource embedded in a CharacterVisuals scene.
 * All NodePaths are relative to the CharacterVisuals root node.
 * Swapping the characterVisuals PackedScene on Character also swaps this config.
 */
@Script(className = "MeshConfig")
public class MeshConfig extends Resource {

    @Export
    public NodePath animationTreePath = new NodePath("AnimationTree");

    @Export
    public NodePath physicalBoneSimulatorPath = new NodePath(
            "MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/PhysicalBoneSimulator3D");

    @Export
    public NodePath weaponAttachmentPath = new NodePath(
            "MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/WeaponAttachment");

    @Export
    public NodePath aimSpineModifierPath = new NodePath(
            "MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/SpineAimModifier");

    /** The shoulders/head aim modifier — see {@link ShoulderAimModifier}. */
    @Export
    public NodePath shoulderAimModifierPath = new NodePath(
            "MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/ShoulderAimModifier");


    @Export
    public NodePath fpsCameraMarkerPath = new NodePath(
            "MeshRoot/Model/Godot_Chan_Stealth/Skeleton3D/NeckAttachment/MarkerFPSCamera");

    @Export
    public NodePath meshRootPath = new NodePath("MeshRoot");

    /** Head mesh nodes to hide in FPS mode — paths relative to CharacterVisuals root. */
    @Export
    public VariantArray<NodePath> headMeshPaths = new VariantArray<>(NodePath.class);

    /** All weapon-socket Marker3D nodes — paths relative to CharacterVisuals root. */
    @Export
    public VariantArray<NodePath> socketPaths = new VariantArray<>(NodePath.class);

    /**
     * Stance name → CollisionShape3D path relative to CharacterVisuals root.
     * Keys must match the keys in Character.stances ("Upright", "Crouch", "Crawl", …).
     * DriveCarrier and other physics-free stances omit an entry here.
     * Wired by Character.wireFromMeshConfig() into each Stance's collider field.
     */
    @Export
    public Dictionary<String, NodePath> stanceColliderPaths = new Dictionary<>(String.class, NodePath.class);

    /**
     * Stance name → RayCast3D (ceiling probe) path relative to CharacterVisuals root.
     * Only stances that need ceiling detection (Upright, Crouch) require an entry.
     */
    @Export
    public Dictionary<String, NodePath> stanceRaycastPaths = new Dictionary<>(String.class, NodePath.class);

    /**
     * PhysicalBone3D node-name → damage multiplier.
     * Health uses this if non-empty; otherwise falls back to the built-in GodotChan table.
     */
    @Export
    public Dictionary<String, Float> boneHitMultipliers = new Dictionary<>(String.class, Float.class);
}

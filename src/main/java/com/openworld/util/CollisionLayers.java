package com.openworld.util;

import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.item.AmmoRefill;
import com.openworld.item.Pickup;

/**
 * Named collision layer constants matching project.godot [layer_names].
 *
 * Layer map:
 *   1  world     (bitmask  1) — StaticBody3D world geometry (Godot default layer)
 *   2  character (bitmask  2) — Live CharacterBody3D capsule
 *   3  pickup    (bitmask  4) — item.PickupBody, the RigidBody3D a world item rides
 *   4  hitbox    (bitmask  8) — PhysicalBone3D ragdoll bones (live, for hitscan)
 *   5  vehicle   (bitmask 16) — Vehicle RigidBody3D
 *
 * Usage guide:
 *   setCollisionLayer(WORLD | CHARACTER)           — bitmask API
 *   setCollisionLayerValue(LAYER_HITBOX, false)    — 1-based layer number API
 *
 * Common combined masks:
 *   CHARACTER body mask:      WORLD | CHARACTER | VEHICLE  = 19
 *   AimRay (character):       WORLD | PICKUP | HITBOX | VEHICLE = 29  (Character.tscn — PICKUP is
 *                             what lets a bullet detonate a dropped grenade)
 *   AimRay (vehicle):         WORLD | HITBOX | VEHICLE          = 25
 *   PickupArea mask:          CHARACTER                    =  2
 *   AmmoRefill mask:          CHARACTER                    =  2
 *   Vehicle EntranceArea:     CHARACTER                    =  2
 *   Vehicle SpringArm mask:   WORLD | VEHICLE              = 17
 *   PickupBody mask:          WORLD                        =  1
 *   Ragdoll bone mask:        WORLD                        =  1
 */
public final class CollisionLayers {
    private CollisionLayers() {}

    // ── Bitmasks (use with setCollisionLayer / setCollisionMask) ──────────────
    public static final int WORLD     =  1;  // layer 1
    public static final int CHARACTER =  2;  // layer 2
    public static final int PICKUP    =  4;  // layer 3
    public static final int HITBOX    =  8;  // layer 4
    public static final int VEHICLE   = 16;  // layer 5

    // ── Layer numbers, 1-based (use with setCollisionLayerValue / setCollisionMaskValue) ──
    public static final int LAYER_WORLD     = 1;
    public static final int LAYER_CHARACTER = 2;
    public static final int LAYER_PICKUP    = 3;
    public static final int LAYER_HITBOX    = 4;
    public static final int LAYER_VEHICLE   = 5;
}

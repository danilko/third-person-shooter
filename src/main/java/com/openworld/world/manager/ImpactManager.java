package com.openworld.world.manager;

import com.openworld.character.Character;
import com.openworld.weapon.Detonatable;
import com.openworld.character.Health;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node;
import godot.api.Texture2D;
import godot.core.NodePath;
import com.openworld.character.CharacterVisuals;
import com.openworld.weapon.WeaponController;
import com.openworld.world.Breakable;
import com.openworld.world.HitInfo;
import com.openworld.world.HittableBody;
import com.openworld.world.SurfaceType;

/**
 * World-level singleton that resolves every bullet impact into its full consequences.
 *
 * WeaponController detects the hit (raycast → HitInfo) and calls processHit().
 * It knows nothing about surfaces, Health, particles, or decals.
 *
 * To add a new hit effect (sound, physics impulse, screen shake …):
 *   add a private method here and call it from processHit(). Nothing else changes.
 *
 * Network note: processHit() is a pure function of its arguments. With
 * CharacterInput.aimTargetPosition baked on the originating client (spread already
 * applied), every client replays the same call and reaches the same result.
 *
 * Discovery: registers itself in group "impact_manager".
 */
@Script(className = "ImpactManager")
public class ImpactManager extends Node {

    private ParticleManager particleManager;
    private DecalManager    decalManager;

    @Register
    @Override
    public void _ready() {
        addToGroup("impact_manager");
    }

    /**
     * Resolve one bullet impact into all its consequences.
     *
     * @param info         hit geometry: node, world point, surface normal
     * @param damage       base damage from WeaponStats
     * @param weaponName   display name for kill notifications
     * @param weaponIcon   icon shown in kill feed (may be null)
     * @param attackerName display name for kill notifications
     */
    public void processHit(HitInfo info, float damage,
                           String weaponName, Texture2D weaponIcon,
                           String attackerName, String attackerFaction) {
        processHit(info, damage, weaponName, weaponIcon, attackerName, attackerFaction, null);
    }

    /**
     * @param attackerPos world position of the shooter, threaded to {@link Health#takeDamage} so the
     *                    HUD can show a damage-direction indicator. Null when unknown.
     */
    public void processHit(HitInfo info, float damage,
                           String weaponName, Texture2D weaponIcon,
                           String attackerName, String attackerFaction,
                           godot.core.Vector3 attackerPos) {
        // Walk the parent chain once to resolve surface type, health owner, and
        // character — previously done by three independent traversals per hit.
        HitContext ctx = resolveHitContext(info.hitNode);

        ParticleManager pm = getParticleManager();
        if (pm != null) pm.spawn(ctx.surface, info.hitPoint);

        DecalManager dm = getDecalManager();
        if (dm != null && info.hitNormal != null) dm.spawn(info.hitPoint, info.hitNormal);

        if (ctx.healthOwner != null) {
            float bodyDamage = damage;
            // Tire hit (the TireHit collider under a VehicleWheel): the wheel takes the
            // damage — flats on the simulating peer, replicated via the snapshot flatMask —
            // and only a reduced passthrough continues to the vehicle body Health. The
            // vehicle counterpart of the character bone-multiplier model.
            if (ctx.wheel != null) bodyDamage = ctx.wheel.applyTireDamage(damage);
            Health health = (Health) ctx.healthOwner.getNode(new NodePath("Health"));
            health.takeDamage(info.hitNode, bodyDamage, weaponName, weaponIcon, attackerName, attackerFaction, attackerPos);
        }

        if (ctx.character != null && info.hitNormal != null) {
            ctx.character.applyHitImpulse(info.hitNode, info.hitNormal.times(-1f), damage);
        }

        if (ctx.detonatable != null) ctx.detonatable.detonate();

        // Destructible world geometry (breakable glass/wall — PLAN.md I2). Host-authoritative:
        // Breakable.applyDamage ignores the hit on a non-server peer and replicates the break itself.
        if (ctx.breakable != null) ctx.breakable.applyDamage(damage, attackerPos);
    }

    /**
     * Cosmetic-only impact resolution — particles + decal, no damage/impulse/detonation. Used by a
     * networked client to show its own predicted bullet impact (the shooter feels instant feedback)
     * while the host owns the actual damage via the MSG_SHOT path (Round 8 — host-resolved bullets).
     */
    public void processVisualHit(HitInfo info) {
        HitContext ctx = resolveHitContext(info.hitNode);
        ParticleManager pm = getParticleManager();
        if (pm != null) pm.spawn(ctx.surface, info.hitPoint);
        DecalManager dm = getDecalManager();
        if (dm != null && info.hitNormal != null) dm.spawn(info.hitPoint, info.hitNormal);
    }

    // ── Hit context resolution ────────────────────────────────────────────────

    private static class HitContext {
        final Character   character;
        final Node        healthOwner;
        final SurfaceType surface;
        final Detonatable detonatable;
        final Breakable   breakable;
        final com.openworld.carrier.vehicle.VehicleWheel wheel;
        HitContext(Character c, Node h, SurfaceType s, Detonatable d, Breakable b,
                   com.openworld.carrier.vehicle.VehicleWheel w) {
            character = c; healthOwner = h; surface = s; detonatable = d; breakable = b; wheel = w;
        }
    }

    /**
     * Walks the parent chain once to collect surface type, health owner, and character.
     * On a character hit all three resolve to the same ancestor — previously done by
     * three independent traversals in resolveSurfaceType / walkToHealthOwner / applyHitImpulse.
     *
     * getOwner() is not used because CharacterVisuals is added at runtime (addChild),
     * so bones inside it report CharacterVisuals as their owner, not the Character body.
     */
    private static HitContext resolveHitContext(Node hitNode) {
        if (hitNode == null) return new HitContext(null, null, SurfaceType.DEFAULT, null, null, null);
        // A world item's collider belongs to its PickupBody, and the ITEM is that body's CHILD --
        // so a walk that only goes up would pass straight over it. Shooting a dropped grenade has to
        // find the ThrowableItem's Detonatable, and the item is also where a HittableBody surface
        // type would live. Start at the item: everything above the body is above the item too.
        if (hitNode instanceof com.openworld.item.PickupBody body && body.carriedItem() != null) {
            hitNode = body.carriedItem();
        }
        Character character = null;
        Node healthOwner = null;
        SurfaceType surface = SurfaceType.DEFAULT;
        Detonatable detonatable = null;
        Breakable breakable = null;
        com.openworld.carrier.vehicle.VehicleWheel wheel = null;

        Node current = hitNode;
        while (current != null) {
            if (detonatable == null && current instanceof Detonatable d) detonatable = d;
            if (breakable == null && current instanceof Breakable b) breakable = b;
            // A TireHit collider's parent chain passes through its VehicleWheel before
            // reaching the Vehicle (which owns the Health) — capture it for tire damage.
            if (wheel == null && current instanceof com.openworld.carrier.vehicle.VehicleWheel vw) wheel = vw;
            if (surface == SurfaceType.DEFAULT) {
                if (current instanceof Character c) {
                    surface = SurfaceType.FLESH;
                    character = c;
                } else if (current instanceof HittableBody hb) {
                    surface = hb.resolveSurfaceType();
                }
            } else if (character == null && current instanceof Character c) {
                character = c;
            }
            if (healthOwner == null && current.hasNode(new NodePath("Health"))) {
                healthOwner = current;
            }
            if (character != null && healthOwner != null && surface != SurfaceType.DEFAULT
                    && detonatable != null && breakable != null) break;
            current = current.getParent();
        }
        return new HitContext(character, healthOwner, surface, detonatable, breakable, wheel);
    }

    // ── Lazy singleton lookups ────────────────────────────────────────────────

    private ParticleManager getParticleManager() {
        if (particleManager != null) return particleManager;
        Node found = getTree().getFirstNodeInGroup("particle_manager");
        if (found instanceof ParticleManager pm) particleManager = pm;
        return particleManager;
    }

    private DecalManager getDecalManager() {
        if (decalManager != null) return decalManager;
        Node found = getTree().getFirstNodeInGroup("decal_manager");
        if (found instanceof DecalManager dm) decalManager = dm;
        return decalManager;
    }
}

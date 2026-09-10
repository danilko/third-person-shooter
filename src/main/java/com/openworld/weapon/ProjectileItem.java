package com.openworld.weapon;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.*;
import godot.core.Vector3;
import godot.global.GD;
import com.openworld.character.Character;
import com.openworld.item.Pickup;
import com.openworld.world.manager.ExplosionManager;

/**
 * Weapon that fires a physical projectile scene instead of hitscan.
 * Semi-auto only: one shot per trigger pull (auto = false in scene).
 *
 * Explosion radius/damage/pushForce are configured here and injected into each
 * RocketProjectile at spawn time. All blast logic (VFX + damage) is handled by
 * ExplosionManager — this class only spawns the projectile and plays weapon feedback.
 *
 * Scene setup (e.g. ATL4.tscn):
 *   Node3D + ProjectileItem script
 *     CollisionShape3D   (the WORLD body's shape — lent to the PickupBody built at drop time;
 *                         the layer/mask live on that body, not here)
 *     PickupArea (Area3D)
 *       CollisionShape3D (detection sphere, layer 0 / mask character layer 2)
 *     Muzzle (Marker3D)
 *       MuzzleVFX (instance MuzzleVFX.tscn)
 *   projectile_scene → RocketProjectile.tscn
 *   auto = false (semi-auto), magazine = 1, reserve = 3
 */
@Script(className = "ProjectileItem")
public class ProjectileItem extends WeaponItem {

    /** Physics scene to spawn on each shot. */
    @Export public PackedScene projectileScene;

    /** Speed injected into each spawned projectile (m/s). */
    @Export public float projectileSpeed = 25f;

    /** Explosion blast radius injected into each spawned projectile (metres). */
    @Export public float explosionRadius = 8f;

    /** Max damage at the epicentre injected into each spawned projectile. */
    @Export public float explosionMaxDamage = 120f;

    /** Push force applied to bodies in the blast, injected into each spawned projectile. */
    @Export public float explosionPushForce = 20f;

    private GPUParticles3D muzzleFlashFx;
    private AnimationPlayer muzzleFlashAnimPlayer;

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    @Register
    @Override
    public void _ready() {
        super._ready();  // Pickup._ready — group + pickupId registration for replication
        Node muzzle = getNodeOrNull("Muzzle");
        Node vfx    = (muzzle != null) ? muzzle.getNodeOrNull("MuzzleVFX") : null;
        if (vfx != null) {
            muzzleFlashFx         = (GPUParticles3D)  vfx.getNodeOrNull("MuzzleFlash");
            muzzleFlashAnimPlayer = (AnimationPlayer) vfx.getNodeOrNull("AnimationPlayer");
        }
    }

    // ── WeaponAction ──────────────────────────────────────────────────────────

    @Override public WeaponType getWeaponType()    { return WeaponType.RANGED; }
    @Override public float getCurrentSpreadDeg()   { return 0f; }

    /** Semi-auto lock: one shot per trigger pull. */
    @Override
    public boolean canUse() {
        return isSemiAutoReady();
    }

    @Override
    public void useWeapon() {
        isWeaponFired = true;
        decrementMagazine();
        playFireAudio();
        triggerMuzzleFlash();
        applyRecoil();
        spawnProjectile(false);
    }

    /**
     * Puppet replay of a remote launch (Round 11 — WeaponController.playRemoteFireCue):
     * fire audio + muzzle flash + a COSMETIC rocket aimed at the replicated aim point, so
     * every peer sees the rocket fly and explode. No ammo, no recoil, no damage — all
     * authority-side.
     */
    @Override
    public void playRemoteFireCue() {
        playFireAudio();
        triggerMuzzleFlash();
        spawnProjectile(true);
    }

    // stopUseWeapon() (clears the semi-auto lock) is inherited from WeaponItem.

    @Override
    public void onReloadComplete() {
        fillMagazine();
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private void playFireAudio() {
        if (weaponAudio == null || fireAudio == null) return;
        weaponAudio.stop();
        weaponAudio.setStream(fireAudio);
        weaponAudio.play();
    }

    private void triggerMuzzleFlash() {
        if (muzzleFlashFx == null || muzzleFlashAnimPlayer == null) return;
        muzzleFlashFx.setSpeedScale(fireRate);
        muzzleFlashAnimPlayer.setSpeedScale(5f);
        muzzleFlashAnimPlayer.play("MuzzleFlash");
    }

    private void applyRecoil() {
        if (!(owningCharacter instanceof Character c)) return;
        float horizRecoil = (float) GD.randfRange(-recoil * 0.3f, recoil * 0.3f);
        c.applyRecoil(recoil, horizRecoil);
    }

    private void spawnProjectile(boolean cosmetic) {
        if (projectileScene == null || owningCharacter == null) return;

        // Spawn geometry is derived IDENTICALLY on every peer — authority and puppet alike — so the
        // rocket leaves the barrel along the same line for the shooter and every observer. Origin is
        // the weapon's own Muzzle marker; direction is the replicated aim point (getAimTargetPosition,
        // the same value that drives spine IK and rides in every snapshot). This matches
        // FirearmItem.playRemoteFireCue's convention. Only damage differs by `cosmetic`, never the
        // trajectory — previously authority used Muzzle+aimRay while the puppet used chest+aimTarget,
        // and that divergence (not the collision layer) is what made the sync-side rocket misbehave.
        Node muzzle = getNodeOrNull("Muzzle");
        Vector3 spawnPos = (muzzle instanceof Node3D m3d)
                ? m3d.getGlobalPosition()
                : owningCharacter.getGlobalPosition().plus(new Vector3(0f, 1.4f, 0f));

        Vector3 aimDir;
        if (owningCharacter instanceof Character c) {
            Vector3 dir = c.getAimTargetPosition().minus(spawnPos);
            if (dir.lengthSquared() < 1e-6f) return;
            aimDir = dir.normalized();
        } else if (muzzle instanceof Node3D m3d) {
            aimDir = m3d.getGlobalBasis().getZ().times(-1f).normalized();  // muzzle forward (-Z)
        } else {
            return;
        }

        Node projectile = projectileScene.instantiate();

        // Inject all parameters before the node enters the tree.
        if (projectile instanceof RocketProjectile rp) {
            rp.speed              = projectileSpeed;
            rp.cosmetic           = cosmetic;
            rp.explosionRadius    = explosionRadius;
            rp.explosionMaxDamage = explosionMaxDamage;
            rp.explosionPushForce = explosionPushForce;
            if (!cosmetic) {
                rp.attackerName       = resolveAttackerName();
                rp.attackerFaction    = resolveAttackerFaction();
                rp.weaponDisplayName  = getDisplayName();
                rp.weaponIcon         = weaponIcon;
            }
        }

        getTree().getCurrentScene().addChild(projectile);

        if (projectile instanceof Node3D n3d) {
            n3d.setGlobalPosition(spawnPos);
            // Orient -Z (Godot forward) toward aim direction so _physicsProcess
            // constant-thrust formula globalBasis.getZ() * -speed stays aligned.
            n3d.lookAt(spawnPos.plus(aimDir));
        }
        // Initial velocity to start moving immediately before first _physicsProcess tick.
        if (projectile instanceof RigidBody3D rb) {
            rb.setLinearVelocity(aimDir.times(projectileSpeed));
            // Correctness guard (secondary to the unified spawn above): a weapon never collides
            // with / detonates on its own shooter. The rocket's mask includes the character layer,
            // so this covers point-blank / against-a-wall edges where the muzzle sits near the body.
            if (owningCharacter != null) rb.addCollisionExceptionWith(owningCharacter);
        }
    }
}

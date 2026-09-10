package com.openworld.weapon;

import com.openworld.world.manager.ExplosionManager;
import godot.annotation.Export;
import godot.annotation.Script;
import godot.api.*;
import godot.core.Vector3;
import com.openworld.character.Character;
import com.openworld.item.AmmoRefill;
import com.openworld.item.Pickup;

/**
 * Weapon item that throws a physical projectile scene (e.g. a grenade) along the
 * aim direction with an upward arc.
 *
 * Slot: THROWABLE (slot 5). Each throw consumes one carry-count unit (magazine field).
 * No reserve / reload model — carry count is restocked by picking up more throwables
 * of the same weaponId or by visiting an AmmoRefill station.
 *
 * Pick-up stacking:
 *   When a ThrowableItem pickup is touched, if the character already has a ThrowableItem
 *   with the same weaponId in their THROWABLE slot, the carry count is merged (up to
 *   magazineSize = maxCarryCount). If the slot is free, the item is equipped normally.
 *   If no merge is possible and the slot is occupied by a different type, the standard
 *   interact-to-swap flow applies.
 *
 * Drop packaging:
 *   Dropping packages the entire remaining carry count into a single world pickup because
 *   ThrowableItem IS the pickup node. Dropping is blocked when magazine == 0 (nothing to
 *   package). The empty slot stays active so the character can pick up more grenades later.
 *
 * Single-use throwables (e.g. a one-shot signal flare):
 *   Set magazineSize = 1 in the inspector.
 *
 * Scene setup (e.g. T1.tscn):
 *   Node3D + ThrowableItem script
 *     CollisionShape3D   (the WORLD body's shape — lent to the PickupBody built at drop time;
 *                         the layer/mask live on that body, not here)
 *     PickupArea (Area3D)
 *       CollisionShape3D (detection sphere — layer 0 / mask character layer 2)
 *   Connections: PickupArea.body_entered → on_body_entered
 *                PickupArea.body_exited  → on_body_exited
 *
 * Configure magazine=1, magazineSize=6 (or 1 for single-use), reserve=0, reserveMax=0.
 * Set projectileScene to the projectile scene (e.g. T1Projectile.tscn); explosion
 * parameters live in the projectile scene itself, not here.
 */
@Script(className = "ThrowableItem")
public class ThrowableItem extends WeaponItem implements Detonatable {

    public ThrowableItem() {
        // All throwable by default should require manual throw
        auto = false;
    }

    /** Physics scene to instantiate on each throw (e.g. T1Projectile.tscn). */
    @Export public PackedScene projectileScene;

    /** Speed of the thrown projectile in m/s. */
    @Export public float throwSpeed = 12f;

    /** Degrees above the aim direction to arc the throw trajectory. */
    @Export public float arcAngleDeg = 25f;

    /** Explosion radius when the world pickup is shot (metres). */
    @Export public float explosionRadius    = 5f;
    /** Max damage at the epicentre when the world pickup is shot. */
    @Export public float explosionMaxDamage = 80f;
    /** Push force applied to bodies in the blast when the world pickup is shot. */
    @Export public float explosionPushForce = 15f;

    // ── Pickup override — stack merging ───────────────────────────────────────

    /**
     * Auto-pickup when:
     *   (a) the THROWABLE slot is free — normal equip, or
     *   (b) the character already has a ThrowableItem with the same weaponId that has
     *       room below magazineSize — merge without requiring interact.
     * Any other case (different type occupying slot, or stack already full) falls
     * through to the interact-prompt path.
     */
    @Override
    protected boolean shouldAutoPickup(Node character) {
        Node wcNode = character.getNodeOrNull(WEAPON_CONTROLLER_PATH);
        if (!(wcNode instanceof WeaponController wc)) return false;
        if (wc.isSlotFreeFor(resolveSlotType())) return true;
        if (weaponId.isEmpty()) return false;
        WeaponItem existing = wc.findWeaponByIdAndType(weaponId, resolveSlotType());
        return existing instanceof ThrowableItem ti && ti.magazine < ti.magazineSize;
    }

    /**
     * If a same-type stack exists with room, add what fits and queueFree this pickup
     * immediately — no partial remainders left in the world.
     * Otherwise falls back to standard equip via WeaponController.requestEquip().
     */
    @Override
    protected void onCharacterEntered(Node character) {
        Node wcNode = character.getNodeOrNull(WEAPON_CONTROLLER_PATH);
        if (!(wcNode instanceof WeaponController wc)) return;

        if (!weaponId.isEmpty()) {
            WeaponItem existing = wc.findWeaponByIdAndType(weaponId, resolveSlotType());
            if (existing instanceof ThrowableItem ti && ti.magazine < ti.magazineSize) {
                ti.magazine += Math.min(ti.magazineSize - ti.magazine, magazine);
                // Emit unconditionally so the slot UI / nameplate (which re-scan every slot on
                // ammoChanged) refresh the merged count immediately — even when the throwable is
                // NOT the active weapon. notifyAmmoChange(ti) no-ops on an inactive stack, which is
                // why the count previously only updated after a later fire/weapon-switch.
                wc.refreshActiveAmmoDisplay();
                wc.resetFireTimerForEquip(ti);
                equipped = true;
                queueFree();
                return;
            }
        }

        // No merge possible — standard equip (free slot or displacement via interact)
        equipped = true;
        wc.requestEquip(this);
    }

    // ── WeaponAction ──────────────────────────────────────────────────────────

    @Override public WeaponType getWeaponType()   { return WeaponType.THROWN; }
    @Override public WeaponSlotType resolveSlotType() { return WeaponSlotType.THROWABLE; }
    // Semi-auto by default (auto = false): one grenade per trigger pull. Without the
    // isSemiAutoReady() gate a held throw key spawned multiple grenades back-to-back
    // (capped only by fireRate) both locally and across LAN — the double-throw bug.
    @Override public boolean canUse()             { return magazine > 0 && isSemiAutoReady(); }

    @Override
    public void useWeapon() {
        isWeaponFired = true;
        decrementMagazine();
        spawnProjectile(false);
        playThrowAudio();
    }

    /**
     * Puppet replay of a remote throw (Round 11 — WeaponController.playRemoteFireCue):
     * throw audio + a COSMETIC projectile aimed at the replicated aim point, so every peer
     * sees the grenade arc and explosion. No ammo decrement, no damage — both are
     * authority-side (the thrower already spawned the real, damaging projectile).
     */
    @Override
    public void playRemoteFireCue() {
        playThrowAudio();
        spawnProjectile(true);
    }

    private void playThrowAudio() {
        if (weaponAudio != null && fireAudio != null) {
            weaponAudio.stop();
            weaponAudio.setStream(fireAudio);
            weaponAudio.play();
        }
    }

    /**
     * Clears the THROWABLE slot when the last grenade is thrown so any other throwable
     * type can be auto-picked up immediately (no interact-to-swap required).
     * The consumed ThrowableItem node is queueFreed — it has no remaining value.
     */
    @Override
    public void onMagazineEmpty() {
        if (weaponController != null) weaponController.clearActiveSlot();
    }

    /**
     * Only create a world pickup when there are grenades to package.
     * (Manual drop of a 0-count slot is a no-op.)
     */
    @Override
    public boolean shouldDropToWorld() { return isDroppable && magazine > 0; }

    // ── Detonatable ───────────────────────────────────────────────────────────

    /**
     * Explodes the world pickup in place when shot by a bullet.
     * Scales linearly with magazine count (capped at 4×) so a stacked pickup
     * produces a proportionally larger blast than a single grenade.
     * No-op if already equipped (in a character's inventory) or not in the tree.
     */
    @Override
    public void detonate() {
        if (equipped || !isInsideTree()) return;
        Node m = getTree().getFirstNodeInGroup("explosion_manager");
        if (m instanceof ExplosionManager mgr) {
            float scale = Math.min(Math.max(magazine, 1), 4);
            mgr.triggerExplosion(getGlobalPosition(),
                                 explosionRadius    * scale,
                                 explosionMaxDamage * scale,
                                 explosionPushForce * scale,
                                 "", "", getDisplayName(), weaponIcon, null);
        }
        queueFree();
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    /**
     * Spawns the thrown projectile. {@code cosmetic} true is the puppet replay: aim comes from
     * the replicated aim point (no aimRay on a puppet) and the projectile deals no damage.
     */
    private void spawnProjectile(boolean cosmetic) {
        if (projectileScene == null || owningCharacter == null) return;

        Vector3 aimDir = resolveAimDir();
        if (aimDir == null) return;

        // Arc: rotate upward around the axis perpendicular to aim and world-up
        Vector3 right = aimDir.cross(Vector3.Companion.getUP()).normalized();
        if (right.lengthSquared() < 0.001f) right = Vector3.Companion.getRIGHT();
        Vector3 throwDir = aimDir.rotated(right, (float) Math.toRadians(arcAngleDeg)).normalized();

        // Spawn near the character's shoulder, slightly forward of the body
        Vector3 spawnPos = owningCharacter.getGlobalPosition()
                .plus(new Vector3(0f, 1.4f, 0f))
                .plus(aimDir.times(0.4f));

        Node projectile = projectileScene.instantiate();

        // Inject attacker identity before the node enters the tree.
        // Explosion parameters are scene-configured inside the projectile scene itself.
        if (projectile instanceof T1Projectile gp) {
            gp.cosmetic = cosmetic;
            if (!cosmetic) {
                gp.attackerName      = resolveAttackerName();
                gp.attackerFaction   = resolveAttackerFaction();
                gp.weaponDisplayName = getDisplayName();
                gp.weaponIcon        = weaponIcon;
            }
        }

        getTree().getCurrentScene().addChild(projectile);

        if (projectile instanceof Node3D n3d) n3d.setGlobalPosition(spawnPos);
        if (projectile instanceof RigidBody3D rb) {
            rb.setLinearVelocity(throwDir.times(throwSpeed));
            // Never let the thrown projectile collide with its own thrower — it spawns at the
            // body centre (chest), so a mask that includes the character layer would otherwise
            // bounce it off / detonate it against the thrower. Matches ProjectileItem's guard.
            if (owningCharacter != null) rb.addCollisionExceptionWith(owningCharacter);
        }
    }

    /**
     * Aim direction, derived identically on every peer (authority and puppet) from the replicated
     * aim point (getAimTargetPosition — the same value that drives spine IK and rides in every
     * snapshot), matching FirearmItem.playRemoteFireCue. Previously authority used the precise
     * aimRay while puppets used the aim point, which diverged the throw arc between the thrower and
     * observers; deriving both from the one replicated quantity keeps the grenade consistent.
     */
    private Vector3 resolveAimDir() {
        if (!(owningCharacter instanceof Character c)) return null;
        Vector3 from = owningCharacter.getGlobalPosition().plus(new Vector3(0f, 1.4f, 0f));
        Vector3 dir = c.getAimTargetPosition().minus(from);
        return dir.lengthSquared() < 1e-6f ? null : dir.normalized();
    }
}

package com.openworld.net;

import com.openworld.net.NetMessageCodec.DecodedSnapshot;
import com.openworld.net.DeathLatch;
import com.openworld.net.SnapshotInterpolator;
import com.openworld.net.TimestampUnwrapper;
import com.openworld.net.Vec3;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.core.Vector3;
import com.openworld.ai.AIController;
import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.character.Character;
import com.openworld.character.Health;
import com.openworld.control.Controller;
import com.openworld.control.PlayerController;
import com.openworld.control.UserCommand;
import com.openworld.weapon.WeaponController;

/**
 * Controller for non-authority peers — attached in place of PlayerController/AIController
 * on bodies this peer does not drive (Phase 7's spawn rewrite assigns it).
 *
 * isAuthority() returns false, so Character._physicsProcess skips gatherInput/applyInput/
 * physics entirely (see the early return there) — this body's transform/combat/stance/
 * health/weapon are driven *only* by MSG_SNAPSHOT, replacing what MultiplayerSynchronizer
 * used to do automatically.
 *
 * Position/facing use **near-time snapshot smoothing** (see {@link SnapshotInterpolator}):
 * the latest snapshot is dead-reckoned forward by local frame delta and the rendered
 * transform eases toward that projection — no render clock to fall behind, ~0 steady-state
 * lag. Combat/stance/health/weapon-slot are discrete — applied once on receipt, not
 * interpolated, the same way applyInput only calls setCombatState()/setStance() on change.
 */
@Script(className = "NetworkController")
public class NetworkController extends Controller {

    // ── Near-time snapshot smoothing (engine-free, unit-tested in SnapshotInterpolatorTest) ──
    private final SnapshotInterpolator interpolator = new SnapshotInterpolator();
    // Unwraps the wire's 32-bit senderTimeMs into a never-wrapping 64-bit timeline (serial
    // arithmetic) so replication survives the ~24.8-day int rollover instead of freezing when a
    // wrapped timestamp reads as "stale". Engine-free, unit-tested in TimestampUnwrapperTest.
    private final TimestampUnwrapper senderClock = new TimestampUnwrapper();
    // Fires the ragdoll once when replicated health first hits zero (engine-free, unit-tested
    // in DeathLatchTest). After it fires we stop driving the body's transform so the corpse
    // ragdoll isn't dragged around by subsequent snapshots.
    private final DeathLatch deathLatch = new DeathLatch();
    // Gate discrete-state apply to forward snapshots only — keyed on the sender's wall-clock
    // timestamp (not tick), so a host re-broadcasting a client-owned puppet (whose tick is frozen)
    // can't get its combat/stance/health/weapon stuck on the first received value. Uses the
    // unwrapped 64-bit timeline so the gate doesn't jam at the int rollover.
    private long latestDiscreteTimeMs = Long.MIN_VALUE;
    // Fire-as-state: play the muzzle/tracer cue when the snapshot's rolling shot counter changes.
    // haveFireSeq suppresses a spurious cue on the very first snapshot (no prior value to diff).
    private boolean haveFireSeq = false;
    private int     lastFireSeq = 0;
    // Reload-as-state: play the reload animation/audio cue when the snapshot's rolling reload
    // counter changes, so a reloading enemy is visibly telegraphed (a tactical tell that they
    // can't return fire mid-reload). haveReloadSeq suppresses a spurious cue on the first snapshot.
    private boolean haveReloadSeq = false;
    private int     lastReloadSeq = 0;

    @Override
    public boolean isAuthority() { return false; }

    @Override
    public UserCommand gatherInput(double delta) {
        return new UserCommand();
    }

    /**
     * Push a freshly-decoded MSG_SNAPSHOT into the interpolation buffer and apply its
     * discrete fields immediately. Stale/duplicate ticks (possible on an unsequenced,
     * unreliable channel) are dropped by the interpolator; the discrete fields are
     * separately gated on forward progress so a reordered packet can't roll them back.
     *
     * <p>{@code applyHealth} is false when the HOST applies an owning client's upstream report:
     * under ownership-based authority the client owns its locomotion but the host owns health,
     * so an owner's reported health must never overwrite the host's authoritative value (a
     * client could otherwise heal itself). Clients applying the host's downstream snapshot pass
     * true — health there IS authoritative.
     */
    public void receiveSnapshot(DecodedSnapshot snapshot, boolean applyHealth) {
        Vector3 pos = snapshot.position();
        Vector3 vel = snapshot.velocity();
        Vector3 aim = snapshot.aimTarget();
        long senderMs = senderClock.unwrap(snapshot.senderTimeMs());
        interpolator.addSample(snapshot.tick(), senderMs / 1000.0,
                new Vec3(pos.getX(), pos.getY(), pos.getZ()),
                new Vec3(vel.getX(), vel.getY(), vel.getZ()),
                snapshot.yaw(),
                new Vec3(aim.getX(), aim.getY(), aim.getZ()));

        if (senderMs > latestDiscreteTimeMs) {
            latestDiscreteTimeMs = senderMs;
            applyDiscreteState(snapshot, applyHealth);
        }
    }

    /** Advances the interpolation clock and applies the resulting bracketed/dead-reckoned transform every rendered frame. */
    @Register
    @Override
    public void _process(double delta) {
        // Once dead, the ragdoll (separate PhysicalBone3D physics) owns the body's pose —
        // keep advancing the interpolation clock so buffered snapshots still drain, but stop
        // writing the transform/facing/aim or we'd drag the corpse along the snapshot path.
        if (deathLatch.isDead()) return;
        SnapshotInterpolator.Output out = interpolator.advance(delta);
        if (out == null) return;
        if (getParent() instanceof Character c) {
            Vec3 a = out.aim();
            // Aim always applies — a seated passenger's spine IK still tracks the owner's look.
            c.applyReplicatedAim(new Vector3((float) a.x(), (float) a.y(), (float) a.z()));
            // While seated, the vehicle's occupant pin (Vehicle._physicsProcess, which runs on
            // every peer including puppets) owns this body's transform — writing the snapshot
            // path here would drag the passenger toward its ~100 ms-stale interpolated
            // position and jitter it against the seat (Round 11 N3).
            if (c.currentVehicleNode != null) return;
            Vec3 p = out.position();
            Vec3 v = out.velocity();
            Vector3 velocity = new Vector3((float) v.x(), (float) v.y(), (float) v.z());
            c.applyReplicatedTransform(new Vector3((float) p.x(), (float) p.y(), (float) p.z()), velocity);
            c.applyReplicatedFacing((float) out.yaw());
            // Drive the locomotion blend from the interpolated state, so the puppet's legs
            // animate walk/strafe. AFTER applyReplicatedFacing: the strafe blend resolves the
            // world-space velocity against MeshRoot's facing, which that call has just written.
            c.applyReplicatedLocomotion(velocity);
        }
    }

    /**
     * Reliable death path (Round 11 N2 — MSG_ELIMINATION): force this puppet dead now,
     * without waiting for a health==0 snapshot to reach the latch. Idempotent — the latch
     * fires once, so a snapshot-driven death that already ran (or runs later) is harmless.
     * Latching also stops {@link #_process} driving the transform, freeing the ragdoll.
     */
    public void forceReplicatedDeath() {
        if (!deathLatch.update(0f)) return;
        if (getParent() instanceof Character c) c.applyReplicatedDeath();
    }

    /** Combat/stance/health/weapon-slot — applied once per snapshot, never interpolated. */
    private void applyDiscreteState(DecodedSnapshot snapshot, boolean applyHealth) {
        if (!(getParent() instanceof Character c)) return;

        // Combat/stance/movement are forced by the drive state while seated (every peer ran
        // enterDriveState from the occupancy event) — re-applying the replicated values would
        // fight it. Health/weapon/fire below still apply (Round 11 N3).
        if (c.currentVehicleNode == null) {
            c.applyReplicatedCombatAndStance(snapshot.combat(), snapshot.stanceOrdinal());
            c.applyReplicatedMovementType(snapshot.movementTypeOrdinal());
        }

        if (applyHealth) {
            godot.api.Node healthNode = c.getNodeOrNull(new godot.core.NodePath("Health"));
            if (healthNode instanceof Health health) {
                health.applyReplicatedHealth(snapshot.currentHealth());
                // applyReplicatedHealth deliberately stays silent (no died signal); drive the
                // ragdoll here off the edge-triggered latch so a defeated puppet collapses on
                // the client instead of standing frozen in its aim pose.
                if (deathLatch.update(snapshot.currentHealth())) c.applyReplicatedDeath();
            }
        }

        godot.api.Node weaponNode = c.getNodeOrNull(new godot.core.NodePath("WeaponController"));
        if (weaponNode instanceof WeaponController wc) {
            // Only switch when the puppet actually HAS a weapon in the replicated slot.
            // Until the reliable pickup event (MSG_PICKUP_TAKEN) equips it, the slot is
            // empty here and onSetWeapon would silently no-op — retrying it every
            // snapshot (~33 ms) is the old infinite fist↔weapon switch loop. Holding the
            // current slot instead is self-healing: the next snapshot after the pickup
            // event lands switches cleanly.
            int slot = snapshot.activeSlotIndex();
            // applyReplicatedWeaponSlot (not onSetWeapon): snap the puppet's slot + play the draw
            // as cosmetic only, so the switch shows up as promptly as position/stance and a fire
            // cue never lands mid-draw. See WeaponController.applyReplicatedWeaponSlot.
            if (wc.getWeapon() != slot && wc.getWeaponItem(slot) != null) wc.applyReplicatedWeaponSlot(slot);
            // Track the owner's active-weapon ammo so consumption that rides no other message
            // (a thrown grenade) is reflected on the puppet — and, on the host, in the manifest
            // this copy feeds. Puppet-only: the owner's own echo never reaches here (it goes
            // through applyOwnBodyDiscreteState, which keeps the owner authoritative).
            wc.applyReplicatedActiveMagazine(snapshot.activeMagazine());
            // Fire replicated as state: a changed shot counter means the authority fired since the
            // last snapshot → play the cue here. Mirror the value so a host re-broadcasting this
            // puppet carries the right counter onward to the other clients.
            if (haveFireSeq && snapshot.fireSeq() != lastFireSeq) wc.playRemoteFireCue();
            wc.setReplicatedFireSeq(snapshot.fireSeq());
            lastFireSeq = snapshot.fireSeq();
            haveFireSeq = true;
            // Reload replicated as state, same change-detection model as fire: a changed reload
            // counter means the authority started a reload since the last snapshot → play the
            // reload cue here. Mirror the value so a re-broadcasting host carries it onward.
            if (haveReloadSeq && snapshot.reloadSeq() != lastReloadSeq) wc.playRemoteReloadCue();
            wc.setReplicatedReloadSeq(snapshot.reloadSeq());
            lastReloadSeq = snapshot.reloadSeq();
            haveReloadSeq = true;
        }
    }
}

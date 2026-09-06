package com.openworld.net;

import com.openworld.character.Character;
import com.openworld.character.CharacterInfo;
import com.openworld.weapon.FirearmItem;
import com.openworld.character.Health;
import com.openworld.movement.character.MovementType;
import com.openworld.net.NetworkController;
import com.openworld.character.Player;
import com.openworld.movement.character.StanceName;
import com.openworld.weapon.WeaponController;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.ENetConnection;
import godot.api.ENetPacketPeer;
import godot.api.Node;
import godot.api.StreamPeerBuffer;
import godot.api.Time;
import godot.core.Error;
import godot.core.PackedByteArray;
import godot.core.StringName;
import godot.core.VariantArray;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.control.Controllable;
import com.openworld.control.Controller;
import com.openworld.control.PlayerController;
import com.openworld.debug.DebugHarness;
import com.openworld.game.GameManager;
import com.openworld.game.mission.MissionManager;
import com.openworld.item.Pickup;
import com.openworld.net.session.PersistentPlayerId;
import com.openworld.weapon.WeaponItem;

/**
 * Peer-lifecycle + replication transport — registered as an AutoLoad singleton named
 * "NetworkManager".
 *
 * AutoLoad entry (add to project.godot after running ./gradlew build):
 *   [autoload]
 *   NetworkManager="*res://src/main/java/com/openworld/net/NetworkManager.java"
 *
 * Owns a raw {@link ENetConnection}/{@link ENetPacketPeer} transport directly —
 * deliberately bypassing {@code MultiplayerAPI}/{@code SceneMultiplayer} (and thus
 * {@code rpc()}/{@code MultiplayerSynchronizer}) so this class has full control over
 * framing, validation, rate-limiting, and channel assignment (see NETWORK_REWRITE_PLAN.md).
 * Every system built on top of it must check {@link #isNetworked()} before touching the
 * network — single-player keeps working with no connection at all.
 *
 * Replication model (ownership-based authority): every entity has ONE owner
 * ({@code CharacterInfo.ownerPeerId}) that simulates it locally. Owning clients report
 * their body's state upstream as {@code MSG_SNAPSHOT} ({@link #sendOwnedState}); the host
 * applies it to a {@code NetworkController} puppet and re-broadcasts everything it knows as
 * {@code MSG_SNAPSHOT_BATCH} (~30 Hz both directions, {@link #REPLICATION_INTERVAL_MS}).
 * The host stays authoritative for health/damage ({@code MSG_DAMAGE_REQUEST}), bullet
 * resolution ({@code MSG_SHOT}), spawning ({@code MSG_SPAWN}/{@code MSG_DESPAWN}), world
 * events ({@code MSG_WORLD_EVENT}), and ownership migration ({@code MSG_OWNERSHIP}).
 */
@Script(className = "NetworkManager")
public class NetworkManager extends Node {

    private static final int DEFAULT_MAX_CLIENTS = 32;
    private static final StringName CHARACTERS_GROUP = new StringName("characters");
    private static final StringName STREAMED_VEHICLE_GROUP =
            new StringName(com.openworld.carrier.vehicle.Vehicle.STREAMED_GROUP);

    // Client-side ghost reconcile (PLAN.md I3c): streamed ambient traffic churns fast, so a late-join
    // baseline spawn can race a despawn and leave a "ghost" the host no longer simulates (no snapshots →
    // stuck at its spawn point in the ground). Every VEHICLE_RECONCILE_INTERVAL_MS the client frees any
    // STREAMED_GROUP vehicle it has not received a snapshot for in STREAMED_VEHICLE_GHOST_TIMEOUT_MS.
    private static final int STREAMED_VEHICLE_GHOST_TIMEOUT_MS = 2500;
    private static final int VEHICLE_RECONCILE_INTERVAL_MS = 1000;
    private final Map<String, Integer> vehicleLastSnapshotMs = new HashMap<>();
    private int lastVehicleReconcileMs = 0;

    private static final int CHANNEL_COUNT = 4;   // Phase 3 assigns per-message reliability per channel

    // ── Message framing (Phase 2) ─────────────────────────────────────────────
    //
    // Wire format: [u8 msgType][payload...]. Every tag has a NetMessageCodec
    // encode/decode pair and a handle*Message dispatch case in onPacketReceived.

    // Tags 2/3/6 (MSG_USER_COMMAND/MSG_CHARACTER_STATE/MSG_WEAPON_FIRE) are retired:
    // the command-upstream model was superseded by ownership-based authority (owners
    // report state via MSG_SNAPSHOT), and fire cues ride the snapshot's fireSeq counter.
    private static final int MSG_IDENTIFY          = 1; // Phase 5 — identifyPeer
    private static final int MSG_DAMAGE_REQUEST    = 4; // Phase 5 — requestDamage
    private static final int MSG_DAMAGE_BROADCAST  = 5; // Phase 5 — broadcastDamage
    private static final int MSG_SNAPSHOT          = 7; // owning client → host upstream state report
    private static final int MSG_SPAWN             = 8; // Phase 7
    private static final int MSG_DESPAWN           = 9; // Phase 7
    private static final int MSG_SNAPSHOT_BATCH    = 10; // Round 5 — one frame for every replicated character per broadcast tick
    private static final int MSG_SHOT              = 11; // Round 8 — client→host host-resolved bullet (client predicts cosmetics)
    private static final int MSG_WORLD_EVENT       = 12; // Round 8 Step 4 — host→all reliable world-state seam (doors/mission/story/pickups)
    private static final int MSG_OWNERSHIP         = 13; // Round 8 Step 3 — entity authority migration (vehicle driver-authority)
    private static final int MSG_PICKUP_REQUEST    = 14; // Phase D — client→host: grant me this world pickup
    private static final int MSG_PICKUP_TAKEN      = 15; // Phase D — host→all: pickup collected; every peer mirrors the collect
    private static final int MSG_WEAPON_DROPPED    = 16; // Phase E — owner→host→all: weapon returned to world; every peer mirrors the drop
    private static final int MSG_ELIMINATION       = 17; // Round 11 N2 — host→all: reliable death/kill event (kill feed, mission, forced puppet death)
    private static final int MSG_INVENTORY         = 18; // Round 11 N2 — host→all: per-character slot manifest; the inventory self-heal backstop
    private static final int MSG_VEHICLE_SNAPSHOT  = 19; // Round 11 N3 — driving client→host: owned vehicle state report (quat orientation)
    private static final int MSG_VEHICLE_SNAPSHOT_BATCH = 20; // Round 11 N3 — host→all: every replicated vehicle's state per broadcast tick
    private static final int MSG_VEHICLE_SEAT_REQUEST   = 21; // Round 11 N3 — client→host: ask to (un)seat a character (host-arbitrated)
    private static final int MSG_VEHICLE_OCCUPANCY      = 22; // Round 11 N3 — host→all: authoritative seat state + locomotion owner (atomic)
    private static final int MSG_WEAPON_SWITCH          = 23; // G4-1 — owner→host→all: ordered equip-start event (puppet draws promptly, fire can't precede draw)
    private static final int MSG_VEHICLE_SPAWN          = 24; // I3b — host→all: streamed ambient-traffic vehicle spawn (counterpart of MSG_SPAWN)
    private static final int MSG_WAYPOINT               = 25; // I5  — owner→host→all: GPS waypoint set/clear (faction-coloured teammate marker)

    /** WeaponController's slotTypes table has 7 entries (FIST/PRIMARY×2/SECONDARY/MELEE/THROWABLE/CONSUMABLE) — bounds isValidSnapshot's activeSlotIndex check. */
    private static final int WEAPON_SLOT_COUNT = 7;

    /** Hard cap on inbound packet size — generous for today's small command/snapshot payloads (MSG_SNAPSHOT is ~50 bytes), still blocks multi-KB garbage. */
    private static final int MAX_PACKET_BYTES = 4096;
    /** characterId/persistentPlayerId are short UUID-ish strings — generous headroom without allowing megabyte strings. */
    private static final int MAX_STRING_LENGTH = 64;

    // Sized for the two traffic directions this limiter covers per peer:
    //   Server→client: ~20 msg/s batched snapshots + occasional spawns/damage/cosmetics.
    //   Client→server: 60 Hz UserCommands (one per physics tick) — the steady-state driver.
    // Refill at 70/s keeps 60 Hz input below the refill rate (net +10/s), so the bucket
    // fills over time and the limiter never fires during normal continuous input. Capacity
    // at 60 absorbs short bursts (connection handshake, rapid weapon-fire cosmetics) without
    // dropping. The old 45/s refill was tuned only for ~20 msg/s snapshot traffic; it
    // starved 60 Hz UserCommands after 4 s of sustained input, dropping ~25 % of commands.
    private static final double RATE_LIMIT_CAPACITY = 60.0;
    private static final double RATE_LIMIT_REFILL_PER_SECOND = 70.0;

    private final Map<Integer, TokenBucket> rateLimiters = new HashMap<>();

    // ── Channel assignment (Phase 3) ──────────────────────────────────────────
    //
    // Maps each MSG_* tag to its ENet channel + reliability flag so a flood of
    // unreliable snapshots can never head-of-line-block a reliable damage event —
    // the shared-RPC-channel hazard the old MultiplayerAPI transport had.
    // CHANNEL_COUNT = 4 above already provisions channels 0-3 in hostServer/joinServer.
    //
    //   0 — control/handshake/events (must arrive): IDENTIFY, DAMAGE_REQUEST,
    //       DAMAGE_BROADCAST, SPAWN, DESPAWN, SHOT,
    //       WORLD_EVENT, OWNERSHIP                         → FLAG_RELIABLE
    //   2 — continuous snapshots (drop freely):           SNAPSHOT/BATCH → 0 (unreliable)
    //   (channels 1/3 — the retired command-upstream and weapon-fire-cue lanes — are
    //   currently unassigned; CHANNEL_COUNT stays 4 so they're provisioned for reuse)
    //
    // sendMessage/broadcastMessage are the only call points that need to know this
    // table — handlers just hand them an already-framed payload from
    // NetMessageCodec and never touch channel/flag numbers directly.

    private record ChannelSpec(int channel, long flags) { }

    private static ChannelSpec channelSpecFor(int msgType) {
        return switch (msgType) {
            // MSG_INVENTORY shares channel 0 (not a spare lane) deliberately: manifests must stay
            // ORDERED with MSG_PICKUP_TAKEN/MSG_WEAPON_DROPPED. A manifest is built from host
            // state that already includes every event sent before it, so same-channel ordering
            // guarantees a manifest can never be overtaken by an older event it supersedes (a
            // cross-channel manifest racing ahead of its grant echo would double-equip).
            case MSG_IDENTIFY, MSG_DAMAGE_REQUEST, MSG_DAMAGE_BROADCAST, MSG_SPAWN, MSG_DESPAWN, MSG_SHOT,
                    MSG_WORLD_EVENT, MSG_OWNERSHIP, MSG_PICKUP_REQUEST, MSG_PICKUP_TAKEN,
                    MSG_WEAPON_DROPPED, MSG_ELIMINATION, MSG_INVENTORY,
                    MSG_VEHICLE_SEAT_REQUEST, MSG_VEHICLE_OCCUPANCY, MSG_WEAPON_SWITCH,
                    MSG_VEHICLE_SPAWN, MSG_WAYPOINT ->
                    new ChannelSpec(0, ENetPacketPeer.FLAG_RELIABLE);
            case MSG_SNAPSHOT, MSG_SNAPSHOT_BATCH, MSG_VEHICLE_SNAPSHOT, MSG_VEHICLE_SNAPSHOT_BATCH ->
                    new ChannelSpec(2, 0L);
            default -> throw new IllegalArgumentException("No channel mapping for MSG_* tag " + msgType);
        };
    }

    /** Sends an already-framed `[u8 msgType][...]` payload to one peer on its assigned channel/flags. No-op if not connected or the target is unknown. */
    public void sendMessage(int targetPeerId, PackedByteArray framedPayload) {
        ENetPacketPeer target = amServer ? peersById.get(targetPeerId)
                : (targetPeerId == SERVER_PEER_ID ? serverPeer : null);
        if (target == null) return;
        ChannelSpec spec = channelSpecFor(peekMsgType(framedPayload));
        target.send(spec.channel(), framedPayload, (int) spec.flags());
    }

    /** Server → every connected peer, optionally skipping one (e.g. the originator). No-op on clients. */
    public void broadcastMessage(PackedByteArray framedPayload, Integer excludePeerId) {
        if (!amServer) return;
        for (Integer peerId : peersById.keySet()) {
            if (excludePeerId != null && excludePeerId.equals(peerId)) continue;
            sendMessage(peerId, framedPayload);
        }
    }

    /** Reads the MSG_* tag byte off an encoded frame without disturbing it — mirrors onPacketReceived's getU8() framing on the receive side. */
    private static int peekMsgType(PackedByteArray framedPayload) {
        StreamPeerBuffer buf = new StreamPeerBuffer();
        buf.setDataArray(framedPayload);
        return buf.getU8();
    }

    public static final int SERVER_PEER_ID = 1;   // matches ENet/Godot convention
    private int localPeerId = SERVER_PEER_ID;     // this process's own id; clients learn theirs via the identify handshake (Phase 5/7)

    // ── Raw ENet transport (bypasses MultiplayerAPI/SceneMultiplayer entirely) ──
    //
    // ENet hands out ENetPacketPeer objects, not Godot-style integer ids — the
    // server assigns its own sequential ids on CONNECT and tracks both directions
    // so message handlers (Phase 2+) can address peers by a stable int.

    private ENetConnection connection;
    private ENetPacketPeer serverPeer;             // client-side only: this process's link to the server
    private boolean amServer;
    private final Map<Integer, ENetPacketPeer> peersById = new HashMap<>();
    private final Map<ENetPacketPeer, Integer> idsByPeer = new HashMap<>();
    private int nextPeerId = 2;                    // 1 is reserved for the server itself

    /**
     * Drains the ENet event queue at render rate (not physics rate — network I/O
     * should never wait on the physics tick). Replaces the old MultiplayerAPI
     * peerConnected/peerDisconnected *signal* hooks: connect/disconnect now arrive
     * as events from this same poll loop, and (from Phase 2 on) so will every
     * application message.
     */
    // ── Net observability (Round 11 N1 — "fail loudly") ───────────────────────
    //
    // Every silent drop/no-op in the replication path increments a NetStats counter; the
    // non-zero counters are dumped here every NETSTATS_DUMP_INTERVAL_MS so a "very rare"
    // desync leaves evidence. loggedOnceKeys backs logOnce(): per-key one-shot diagnostics
    // (e.g. snapshots for a character this peer doesn't know) that would otherwise spam at
    // the 30 Hz snapshot rate.

    private static final int NETSTATS_DUMP_INTERVAL_MS = 10_000;
    private int lastNetStatsDumpMs = 0;
    private final java.util.Set<String> loggedOnceKeys = new java.util.HashSet<>();

    /** Prints {@code message} the first time {@code key} is seen, then never again (until reconnect — resetTransport clears the set). */
    private void logOnce(String key, String message) {
        if (loggedOnceKeys.add(key)) GD.print(message);
    }

    @Register
    @Override
    public void _process(double delta) {
        if (connection == null) return;
        int now = nowMs();
        if (now - lastNetStatsDumpMs >= NETSTATS_DUMP_INTERVAL_MS) {
            lastNetStatsDumpMs = now;
            String dump = com.openworld.net.NetStats.consumeDumpLine();
            if (!dump.isEmpty()) GD.print(dump);
        }
        // Loop guards on `connection != null`, not `true`: dispatchEnetEvent may tear the
        // transport down mid-drain (a client's host-loss DISCONNECT → onHostLost → leaveSession
        // nulls `connection`), and the next service(0) would NPE on it.
        while (connection != null) {
            VariantArray<Object> event = connection.service(0);
            ENetConnection.EventType type = ENetConnection.EventType.Companion.from(asLong(event.get(0)));
            // service() always returns a 4-element array — [type, peer, data, channel] — even
            // when nothing happened, so EventType (not array size) is the real "drained" signal.
            if (type == ENetConnection.EventType.NONE) break;
            if (type == ENetConnection.EventType.ERROR) {
                GD.print("NetworkManager: ENet service error — resetting transport");
                resetTransport();
                return;
            }
            dispatchEnetEvent(type, event);
        }
        // Host-loss watchdog (client only): ENet's own DISCONNECT can lag, so an app-level
        // staleness check declares the host gone when no packet has arrived for HOST_TIMEOUT_MS —
        // see onHostLost. Skipped on the host and once already handled this session.
        if (connection != null && !amServer && !hostLossHandled
                && now - lastServerPacketMs > HOST_TIMEOUT_MS) {
            onHostLost("timeout (" + HOST_TIMEOUT_MS + "ms with no packet)");
        }
        // Client-side ghost-vehicle reconcile (PLAN.md I3c) — only a client has puppet streamed cars.
        if (connection != null && !amServer && now - lastVehicleReconcileMs >= VEHICLE_RECONCILE_INTERVAL_MS) {
            lastVehicleReconcileMs = now;
            reconcileStreamedVehicles(now);
        }
        // Per-peer vehicle/character inventory dump (temporary diagnostic — set NET_DEBUG_VEHICLES=false
        // to silence). Both peers log every ~2 s so a side-by-side compare shows exactly where a vehicle,
        // its owner/freeze state, its driver, or its snapshot feed diverges between host and client.
        if (NET_DEBUG_VEHICLES && connection != null && now - lastVehicleDiagMs >= VEHICLE_DIAG_INTERVAL_MS) {
            lastVehicleDiagMs = now;
            dumpVehicleDiagnostics();
        }
    }

    // ── Temporary networked-vehicle diagnostics ───────────────────────────────
    public static boolean NET_DEBUG_VEHICLES = false;      // flip to true to dump per-vehicle/character net state
    private static final int VEHICLE_DIAG_INTERVAL_MS = 2000;
    private int lastVehicleDiagMs;
    // Vehicle snapshot entries received per id since the last dump (cleared each dump).
    private final java.util.Map<String, Integer> vehicleSnapCount = new java.util.HashMap<>();

    private static String shortId(String id) {
        if (id == null || id.isEmpty()) return "?";
        return id.length() <= 8 ? id : id.substring(0, 8);
    }

    /** Logs this peer's full vehicle inventory + seated-occupant + snapshot-feed state for cross-peer compare. */
    private void dumpVehicleDiagnostics() {
        if (getTree() == null) return;
        String role = amServer ? "HOST" : "CLI";
        StringBuilder sb = new StringBuilder();
        sb.append("=== NETDIAG ").append(role).append(" peer=").append(localPeerId).append(" ===\n");
        int vehicleCount = 0;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (!(node instanceof com.openworld.carrier.vehicle.Vehicle v) || v.getCharacterInfo() == null) continue;
            vehicleCount++;
            CharacterInfo info = v.getCharacterInfo();
            String ctrl = v.getController() == null ? "none"
                    : v.getController().getClass().getSimpleName();
            com.openworld.character.Character occ = v.getOccupant();
            String occStr = occ == null ? "-"
                    : shortId(occ.characterInfo != null ? occ.characterInfo.characterId : "?")
                      + "/" + occ.getClass().getSimpleName() + (occ.isVisible() ? "" : ",HIDDEN");
            Vector3 p = v.getGlobalPosition();
            Integer snaps = vehicleSnapCount.getOrDefault(info.characterId, 0);
            sb.append(String.format(
                    "  VEH %s own=%d auth=%b sim=%b frozen=%b vis=%b grp=%s ctrl=%-24s occ=%s snaps2s=%d pos=(%.1f,%.1f,%.1f)\n",
                    shortId(info.characterId), info.ownerPeerId, isAuthorityFor(info), v.isLocallySimulated(),
                    v.isFreezeEnabled(), v.isVisible(),
                    v.isInGroup(STREAMED_VEHICLE_GROUP) ? "STREAM" : "SCENE", ctrl, occStr, snaps,
                    p.getX(), p.getY(), p.getZ()));
        }
        // Players/characters (the "can't see the other player" symptom).
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (!(node instanceof com.openworld.character.Character c) || c.characterInfo == null) continue;
            if (node instanceof com.openworld.carrier.vehicle.Vehicle) continue;
            String ctrl = c.getController() == null ? "none" : c.getController().getClass().getSimpleName();
            String seat = c.currentVehicleNode instanceof com.openworld.carrier.vehicle.Vehicle sv
                    && sv.getCharacterInfo() != null ? shortId(sv.getCharacterInfo().characterId) : "-";
            Vector3 p = c.getGlobalPosition();
            sb.append(String.format("  CHR %s own=%d auth=%b vis=%b ctrl=%-18s seat=%s pos=(%.1f,%.1f,%.1f)\n",
                    shortId(c.characterInfo.characterId), c.characterInfo.ownerPeerId,
                    isAuthorityFor(c.characterInfo), c.isVisible(), ctrl, seat, p.getX(), p.getY(), p.getZ()));
        }
        sb.append("  totals: vehicles=").append(vehicleCount);
        GD.print(sb.toString());
        vehicleSnapCount.clear();
    }

    /**
     * Client-side ghost reconcile (PLAN.md I3c). Streamed ambient traffic spawns/despawns constantly, so a
     * late-join baseline spawn can land after the host already despawned that car (or a despawn is missed),
     * leaving a puppet the host no longer simulates — it gets no snapshots and sits frozen at its spawn
     * point (often sunk into the ground). The host broadcasts a snapshot for every live streamed car ~30×/s,
     * so "no snapshot for {@link #STREAMED_VEHICLE_GHOST_TIMEOUT_MS}" reliably means the car is gone on the
     * host. Free those; a car never yet seen (just spawned, snapshot not arrived) is seeded with a grace
     * window instead of freed.
     */
    private void reconcileStreamedVehicles(int now) {
        if (getTree() == null) return;
        for (Node node : getTree().getNodesInGroup(STREAMED_VEHICLE_GROUP)) {
            if (!(node instanceof com.openworld.carrier.vehicle.Vehicle v) || v.getCharacterInfo() == null
                    || v.getCharacterInfo().characterId.isEmpty() || v.isQueuedForDeletion()) continue;
            // A car the local player is driving (carjacked/entered) is ours — never reconcile it away.
            if (isAuthorityFor(v.getCharacterInfo())) continue;
            String id = v.getCharacterInfo().characterId;
            Integer last = vehicleLastSnapshotMs.get(id);
            if (last == null) { vehicleLastSnapshotMs.put(id, now); continue; }   // first sighting → grace
            if (now - last > STREAMED_VEHICLE_GHOST_TIMEOUT_MS) {
                vehicleLastSnapshotMs.remove(id);
                GameManager gm = gameManager();
                if (gm != null) gm.despawnReplicatedCharacter(id);
                else v.queueFree();
            }
        }
        // Drop bookkeeping for vehicles that no longer exist locally (freed elsewhere), so the map
        // doesn't grow unbounded across the session's churn.
        vehicleLastSnapshotMs.keySet().removeIf(id -> findControllableById(id) == null);
    }

    // ── Host-loss detection (client only) ─────────────────────────────────────
    //
    // A client whose host vanishes must not sit frozen: it detects loss two ways — the ENet
    // DISCONNECT event (handlePeerDisconnected) and a no-packet-for-N-seconds watchdog (above) —
    // then tears the dead session down and hands control to GameManager to surface a recovery
    // prompt. hostLossHandled makes the two paths idempotent; resetTransport clears it.

    /** No server packet for this long ⇒ declare the host lost. 5 s: long enough to ride out a stall, short enough not to leave the player guessing. */
    private static final int HOST_TIMEOUT_MS = 5000;
    private int lastServerPacketMs;           // client: wall-clock of the last packet from the server
    private boolean hostLossHandled;          // guards the event + watchdog paths against double-firing

    /** Tears down a dead client session and notifies GameManager. No-op on the host / single-player / when already handled. */
    private void onHostLost(String reason) {
        if (amServer || hostLossHandled || connection == null) return;
        hostLossHandled = true;
        GD.print("NetworkManager: host lost — " + reason + " — leaving session");
        GameManager manager = gameManager();
        // Tear the transport down first so isNetworked() reads false when the recovery UI runs.
        leaveSession();
        if (manager != null) manager.onHostLost(reason);
    }

    // Broadcast every Nth physics tick instead of every tick — at the default 60 Hz
    // physics rate, every 3rd tick ≈ 20 Hz, the industry-standard authoritative
    // snapshot rate for third-person shooters. NetworkController already
    // interpolates across arbitrary tick gaps (interpDuration/SNAPSHOT_TICK_RATE_HZ),
    // so dropping from 60 Hz to 20 Hz is a pure bandwidth win with no receive-side
    // change needed — it was already built to handle exactly this.
    // The single replication rate shared by BOTH directions, on a wall-clock interval (NOT every N
    // physics ticks). ~30 Hz. The host broadcasts its snapshot batch at this rate, and the owning
    // client throttles its upstream sendOwnedState() to the same rate — so host→client and
    // client→host stream at identical density and the interpolator behaves symmetrically (the old
    // split — client flooding 60 Hz, host throttled to 20 Hz — was the whole reason host→client was
    // janky while client→host was smooth). Wall-clock (not tick count) keeps the rate steady even
    // when host physics dips below 60 Hz (two instances, one CPU). 30 packets/s stays well under the
    // per-peer receive rate limit (refill 70/s) even with fire cues. nowMs() is the same monotonic
    // clock stamped on each snapshot.
    private static final int REPLICATION_INTERVAL_MS = 33;
    // Must start at 0, NOT Integer.MIN_VALUE: nowMs() is a small positive int, and
    // (now - Integer.MIN_VALUE) overflows to a large negative number that is always < the
    // interval — which would silently skip every send forever. From 0 the first tick
    // (now is already thousands of ms by the time networking is up) fires immediately.
    private int lastSnapshotBroadcastMs = 0;   // host downstream broadcast pacing
    // Client upstream pacing, PER ENTITY (N3): a driving client reports both its character
    // and its vehicle — one shared timestamp would let whichever sends first starve the other
    // to ~0 Hz every tick.
    private final Map<String, Integer> lastOwnedStateSendMsById = new HashMap<>();

    /** True when {@code entityId}'s upstream report is due; stamps the send time when it is. */
    private boolean throttleOwnedSend(String entityId) {
        int now = nowMs();
        Integer last = lastOwnedStateSendMsById.get(entityId);
        if (last != null && now - last < REPLICATION_INTERVAL_MS) return false;
        lastOwnedStateSendMsById.put(entityId, now);
        return true;
    }

    /**
     * Sender's monotonic wall-clock (ms since engine start, truncated to int) stamped on every
     * outgoing snapshot — the interpolation timeline on the receiver (see SnapshotInterpolator /
     * DecodedSnapshot.senderTimeMs). A real clock instead of a tick count keeps remote playback
     * locked to real time even when the sender's physics runs below 60 Hz (two instances, one CPU).
     */
    private int nowMs() {
        return (int) Time.INSTANCE.getTicksMsec();
    }

    /**
     * Server-only gather + broadcast loop — runs on the physics tick (not the render-rate
     * {@link #_process}) because the snapshot's tick/position/velocity are physics-tick
     * concepts; sampling them at render rate would just resend the same physics-tick
     * values multiple times. Builds ONE batched MSG_SNAPSHOT_BATCH frame for every
     * replicated character and sends it as a single broadcast every
     * {@link #REPLICATION_INTERVAL_MS} ms (Round 5 — see
     * NETWORK_REWRITE_PLAN.md "Bug 4"; was previously one MSG_SNAPSHOT per character
     * per physics tick — ~300 msg/s with 5 live characters, which overwhelmed the
     * per-sender rate limiter and caused ~90% packet loss on every kind of replicated
     * state). Replaces what MultiplayerSynchronizer used to push automatically for
     * global_position/velocity/facing/combat/stance/health/weapon-slot.
     *
     * Gated on a wall-clock interval ({@link #REPLICATION_INTERVAL_MS}) rather than a
     * physics-tick count so the 20 Hz send rate survives a sub-60 Hz host physics step.
     */
    @Register
    @Override
    public void _physicsProcess(double delta) {
        if (!isNetworked() || !amServer) return;
        int now = nowMs();
        if (now - lastSnapshotBroadcastMs < REPLICATION_INTERVAL_MS) return;
        lastSnapshotBroadcastMs = now;

        List<NetMessageCodec.DecodedSnapshot> entries = new ArrayList<>();
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (!(node instanceof Character c) || c.characterInfo == null) continue;
            Health health = findHealth(c);
            WeaponController wc = findWeaponController(c);
            if (health == null || wc == null) continue;
            // Host broadcasts every body it knows: host-owned bodies (AI, host's own player) from
            // their live sim, and client-owned bodies from the puppet transform the owning client
            // reported (its NetworkController placed it). Health is always the host's authoritative
            // value here, regardless of who owns locomotion. senderTimeMs is the host's wall clock
            // at send — the receiver's interpolation timeline.
            entries.add(new NetMessageCodec.DecodedSnapshot(c.characterInfo.characterId, c.getCurrentTick(),
                    c.getGlobalPosition(), c.getVelocity(), c.getAimTargetPosition(), c.isCombat(),
                    c.getStanceOrdinal(), wc.getReplicatedActiveSlot(), c.getMovementTypeOrdinal(), c.getFacingYaw(),
                    health.getCurrentHealth(), nowMs(), wc.getFireSeq(), wc.getActiveMagazine(), wc.getReloadSeq()));
        }
        broadcastSnapshotBatchChunked(entries);
        broadcastVehicleSnapshots();
        sweepInventoryBroadcast(now);
        sweepVehicleOccupancyBroadcast(now);
    }

    // N2-style state-sync backstop for vehicle seats: a missed/raced MSG_VEHICLE_OCCUPANCY
    // self-heals within one sweep (applyVehicleOccupancy is idempotent). Vehicle counts are
    // small, so this re-broadcasts every vehicle each sweep instead of round-robining.
    private static final int OCCUPANCY_SWEEP_INTERVAL_MS = 1000;
    private int lastOccupancySweepMs = 0;

    /** Host: every OCCUPANCY_SWEEP_INTERVAL_MS, re-broadcast each vehicle's authoritative seats + owner (all seats — a missed passenger exit self-heals too). */
    private void sweepVehicleOccupancyBroadcast(int now) {
        if (now - lastOccupancySweepMs < OCCUPANCY_SWEEP_INTERVAL_MS) return;
        lastOccupancySweepMs = now;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (!(node instanceof com.openworld.carrier.vehicle.Vehicle v) || v.getCharacterInfo() == null
                    || v.getCharacterInfo().characterId.isEmpty() || v.isQueuedForDeletion()) continue;
            for (int seat = 0; seat < v.getSeatCount(); seat++) {
                Character occupant = v.getSeatOccupant(seat);
                String occupantId = occupant != null && occupant.characterInfo != null
                        ? occupant.characterInfo.characterId : "";
                broadcastVehicleOccupancy(v.getCharacterInfo().characterId, occupantId,
                        v.getCharacterInfo().ownerPeerId, !occupantId.isEmpty(), seat);
            }
        }
    }

    /**
     * Host → one peer: late-join occupancy baseline — one MSG_VEHICLE_OCCUPANCY per OCCUPIED
     * seat (vacant is the scene default, nothing to converge). Sent AFTER the spawn baseline
     * on the same reliable channel so the occupant body exists on the joiner before it seats.
     */
    public void sendBaselineVehicleOccupancy(int targetPeerId) {
        if (!isServer() || getTree() == null) return;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (!(node instanceof com.openworld.carrier.vehicle.Vehicle v) || v.getCharacterInfo() == null) continue;
            for (int seat = 0; seat < v.getSeatCount(); seat++) {
                Character occupant = v.getSeatOccupant(seat);
                if (occupant == null || occupant.characterInfo == null) continue;
                sendVehicleOccupancy(targetPeerId, v.getCharacterInfo().characterId,
                        occupant.characterInfo.characterId, v.getCharacterInfo().ownerPeerId, true, seat);
            }
        }
    }

    /**
     * Host gather for vehicles (Round 11 N3) — the vehicle half of the broadcast tick.
     * Host-simulated vehicles (parked / host driver) read live physics state; client-driven
     * vehicles are VehicleNetworkController puppets here, so the entry re-broadcasts the
     * puppet's interpolated transform + the controller's cached driving inputs. Health is
     * always the host's authoritative value, and fireSeq reads the WeaponController either
     * way (the puppet path mirrors the wire value via setReplicatedFireSeq, exactly like
     * the character batch).
     */
    private void broadcastVehicleSnapshots() {
        List<NetMessageCodec.DecodedVehicleSnapshot> entries = new ArrayList<>();
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (!(node instanceof com.openworld.carrier.vehicle.Vehicle v) || v.getCharacterInfo() == null
                    || v.getCharacterInfo().characterId.isEmpty() || v.isQueuedForDeletion()) continue;
            Health health = findHealth(v);
            if (health == null) continue;
            WeaponController wc = findWeaponController(v);
            int fireSeq = wc != null ? wc.getFireSeq() : 0;

            float steerAngle, throttle;
            boolean handbrake, brake, slipping;
            int flatMask;
            if (v.getController() instanceof com.openworld.net.VehicleNetworkController vnc) {
                steerAngle = vnc.getLastSteerAngle();
                throttle = vnc.getLastThrottle();
                handbrake = vnc.getLastHandbrake();
                brake = vnc.getLastBrake();
                slipping = vnc.getLastSlipping();
                flatMask = vnc.getLastFlatMask();
            } else {
                steerAngle = v.getCurrentSteerAngle();
                throttle = v.getCurrentThrottle();
                handbrake = v.isHandbraking();
                brake = v.isBraking();
                slipping = v.isSlipping();
                flatMask = v.getFlatMask();
            }
            entries.add(new NetMessageCodec.DecodedVehicleSnapshot(v.getCharacterInfo().characterId,
                    nowMs(), v.getGlobalPosition(), v.getGlobalBasis().getRotationQuaternion(),
                    v.getLinearVelocity(), v.getAngularVelocity(), steerAngle, throttle,
                    handbrake, brake, slipping, flatMask, health.getCurrentHealth(), fireSeq));
        }
        broadcastVehicleBatchChunked(entries);
    }

    /** Splits the gathered vehicle entries into ≤{@link #MAX_BATCH_PAYLOAD_BYTES} frames — see {@link #broadcastBatchChunked}. */
    private void broadcastVehicleBatchChunked(List<NetMessageCodec.DecodedVehicleSnapshot> entries) {
        broadcastBatchChunked(entries, NetMessageCodec.VEHICLE_SNAPSHOT_ENTRY_FIXED_BYTES,
                e -> e.vehicleId().length(), "vehicle_batch_split",
                chunk -> NetMessageCodec.encodeVehicleSnapshotBatch(MSG_VEHICLE_SNAPSHOT_BATCH, chunk));
    }

    /**
     * Soft payload budget per MSG_SNAPSHOT_BATCH frame (Round 11 N1). Well under
     * MAX_PACKET_BYTES (4096) — whose breach the receiver punishes by dropping the WHOLE
     * frame, a silent ceiling of ~40 characters with UUID ids — and sized to fit a single
     * MTU so a growing character count degrades into more frames, never into lost frames.
     */
    private static final int MAX_BATCH_PAYLOAD_BYTES = 1100;

    /** Fixed bytes per snapshot entry beyond the characterId text: u32 len + i64 tick + 9 floats + flags u16 + yaw/health/time + fireSeq u8 + activeMagazine u16 + reloadSeq u8 — see NetMessageCodec.putSnapshotEntry. */
    private static final int SNAPSHOT_ENTRY_FIXED_BYTES = 66;

    /** Splits the gathered entries into as many ≤{@link #MAX_BATCH_PAYLOAD_BYTES} frames as needed and broadcasts each. */
    private void broadcastSnapshotBatchChunked(List<NetMessageCodec.DecodedSnapshot> entries) {
        broadcastBatchChunked(entries, SNAPSHOT_ENTRY_FIXED_BYTES,
                e -> e.characterId().length(), "snapshot_batch_split",
                chunk -> NetMessageCodec.encodeSnapshotBatch(MSG_SNAPSHOT_BATCH, chunk));
    }

    /**
     * Splits gathered snapshot entries into as many ≤{@link #MAX_BATCH_PAYLOAD_BYTES} frames as
     * needed and broadcasts each (Round 11 N1 MTU batch splitting). Shared by the character and
     * vehicle snapshot streams — the chunking is identical; only the per-entry fixed size, the
     * id-length accessor, the split-stat key, and the wire encoder differ (the wire formats
     * themselves stay separate by design).
     */
    private <T> void broadcastBatchChunked(List<T> entries, int entryFixedBytes,
            java.util.function.ToIntFunction<T> idLength, String splitStat,
            java.util.function.Function<List<T>, PackedByteArray> encode) {
        if (entries.isEmpty()) return;
        List<T> chunk = new ArrayList<>();
        int chunkBytes = 3;   // tag u8 + count u16
        for (T e : entries) {
            int entryBytes = entryFixedBytes + idLength.applyAsInt(e);
            if (!chunk.isEmpty() && chunkBytes + entryBytes > MAX_BATCH_PAYLOAD_BYTES) {
                com.openworld.net.NetStats.increment(splitStat);
                broadcastMessage(encode.apply(chunk), null);
                chunk = new ArrayList<>();
                chunkBytes = 3;
            }
            chunk.add(e);
            chunkBytes += entryBytes;
        }
        broadcastMessage(encode.apply(chunk), null);
    }

    /** The GameManager AutoLoad, or null if absent — dedup of the repeated {@code /root/GameManager} lookup. */
    private GameManager gameManager() {
        return getNodeOrNull("/root/GameManager") instanceof GameManager m ? m : null;
    }

    /**
     * Counts + logs a rejected inbound message uniformly (Round 11 N1 observability): bumps a
     * per-message {@code drop_invalid_<statKey>} NetStats counter and logs the drop. Replaces the
     * ~20 hand-written prelude lines — previously only 4 also bumped a counter, so now every drop
     * reason is visible in NetStats. Callers still {@code return} after invoking it.
     */
    private void dropInvalid(String statKey, String msgLabel, int senderPeerId) {
        com.openworld.net.NetStats.increment("drop_invalid_" + statKey);
        GD.print("NetworkManager: dropping invalid " + msgLabel + " from " + senderPeerId);
    }

    private void dispatchEnetEvent(ENetConnection.EventType type, VariantArray<Object> event) {
        if (event.size() < 2 || !(event.get(1) instanceof ENetPacketPeer peer)) return;
        switch (type) {
            case CONNECT -> handlePeerConnected(peer);
            case DISCONNECT -> handlePeerDisconnected(peer);
            case RECEIVE -> {
                Integer senderId = idsByPeer.get(peer);
                if (senderId != null) onPacketReceived(senderId, peer.getPacket());
            }
            default -> { }
        }
    }

    private void handlePeerConnected(ENetPacketPeer peer) {
        int id = amServer ? nextPeerId++ : SERVER_PEER_ID;
        peersById.put(id, peer);
        idsByPeer.put(peer, id);
        rateLimiters.put(id, new TokenBucket(RATE_LIMIT_CAPACITY, RATE_LIMIT_REFILL_PER_SECOND));
        GD.print("NetworkManager: peer connected — " + id);

        if (amServer) {
            GameManager manager = gameManager();
            if (manager != null) manager.onPeerConnected(id);
        } else {
            // World.tscn pre-places a local "Player" body for single-player/dev
            // convenience (see DebugHarness). That body is meaningless once we become
            // a network client — the server spawns and replicates the authoritative
            // body for our PersistentPlayerId via spawnPlayerBody/announceSpawn — so
            // remove it now, before any spawn traffic can arrive, to avoid ending up
            // with two "self" bodies (one frozen by the authority check, one driven).
            removeLocalPrePlacedPlayer();
            // Same rationale for scene-authored vehicles (PLAN.md netcode WS3): the host owns and
            // re-supplies them via sendBaselineVehicleSpawns with the host's UUID, so drop our local
            // copies (whose ids would diverge) before any spawn traffic arrives.
            removeLocalPrePlacedVehicles();
            // CONNECT-event handshake trigger — replaces the MultiplayerAPI onConnectedToServer
            // signal that no longer fires (Phase 1 stopped assigning a MultiplayerPeer).
            // Reports our stable identity and (via the server's MSG_IDENTIFY reply) learns
            // localPeerId — see handleIdentifyMessage.
            identifyPeer(PersistentPlayerId.getOrCreate());
        }
    }

    /** Frees World.tscn's pre-placed local Player on the connecting client — see handlePeerConnected. */
    private void removeLocalPrePlacedPlayer() {
        if (getTree() == null) return;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (node instanceof Player player) {
                // Freeing a vehicle occupant out from under the Vehicle would leave a
                // dangling occupant reference and a permanently-current vehicle camera
                // (Vehicle.tryExit's makeCameraActive() would never run to reclaim it) —
                // exit cleanly first so the vehicle's own state stays consistent.
                if (player.currentVehicleNode instanceof com.openworld.carrier.vehicle.Vehicle vehicle) {
                    vehicle.tryExit();
                }
                GD.print("NetworkManager: removing locally pre-placed Player " + player.getName()
                        + " — the server will spawn our networked body");
                player.queueFree();
                return;
            }
        }
    }

    /**
     * Frees World.tscn's scene-authored vehicles on the connecting client (PLAN.md netcode WS3).
     * Vehicle identity is now a host-stamped UUID; a client's locally-instanced scene car would carry
     * a divergent UUID, so its enter/exit/snapshot/damage requests could not be resolved on the host
     * (the NO_VEHICLE exit bug). The host re-supplies every vehicle through sendBaselineVehicleSpawns
     * with the authoritative UUID, exactly as it does the pre-placed Player. Exit any occupant first so
     * the vehicle's camera/occupant state tears down cleanly. Snapshot the group up front — queueFree
     * mutates the live group during iteration.
     */
    private void removeLocalPrePlacedVehicles() {
        if (getTree() == null) return;
        java.util.List<com.openworld.carrier.vehicle.Vehicle> scene = new java.util.ArrayList<>();
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (node instanceof com.openworld.carrier.vehicle.Vehicle v) scene.add(v);
        }
        for (com.openworld.carrier.vehicle.Vehicle v : scene) {
            if (!GD.isInstanceValid(v)) continue;
            if (v.getOccupant() != null) v.tryExit();
            GD.print("NetworkManager: removing locally pre-placed Vehicle " + v.getName()
                    + " — the host will re-supply it with its authoritative id");
            v.queueFree();
        }
    }

    private void handlePeerDisconnected(ENetPacketPeer peer) {
        Integer id = idsByPeer.remove(peer);
        if (id == null) return;
        peersById.remove(id);
        rateLimiters.remove(id);
        GD.print("NetworkManager: peer disconnected — " + id);

        GameManager manager = gameManager();
        if (amServer && manager != null) {
            manager.onPeerDisconnected(id);
        } else if (!amServer) {
            // A client only ever has the server as a peer — its disconnect is host loss.
            onHostLost("transport disconnect");
        }
    }

    private static long asLong(Object variant) {
        return variant instanceof Number n ? n.longValue() : 0L;
    }

    // ── Message dispatch (Phase 2) ────────────────────────────────────────────
    //
    // Central entry point for every inbound application-layer byte packet —
    // framing, rate limiting, and validation all funnel through here so no
    // individual MSG_* handler has to re-implement them. A malformed/oversized/
    // flooded payload must be droppable without throwing — wrapped in a
    // catch-all so a hostile or buggy peer can never crash the process or
    // corrupt peersById/idsByPeer/gameplay state.

    private void onPacketReceived(int senderPeerId, PackedByteArray data) {
        try {
            // Any byte from the server proves it's alive — stamp liveness before validation so
            // even a malformed/rate-limited packet defers the host-loss watchdog (see onHostLost).
            if (!amServer) lastServerPacketMs = nowMs();
            if (data.getSize() == 0 || data.getSize() > MAX_PACKET_BYTES) {
                com.openworld.net.NetStats.increment("drop_packet_size");
                GD.print("NetworkManager: dropping packet from " + senderPeerId
                        + " — invalid size " + data.getSize());
                return;
            }

            TokenBucket bucket = rateLimiters.get(senderPeerId);
            if (bucket != null && !bucket.tryConsume()) {
                com.openworld.net.NetStats.increment("drop_rate_limited");
                GD.print("NetworkManager: rate limit exceeded for peer " + senderPeerId + " — dropping packet");
                return;
            }

            StreamPeerBuffer buf = new StreamPeerBuffer();
            buf.setDataArray(data);
            int msgType = buf.getU8();

            switch (msgType) {
                case MSG_SNAPSHOT -> handleSnapshotMessage(senderPeerId, buf);
                case MSG_SNAPSHOT_BATCH -> handleSnapshotBatchMessage(senderPeerId, buf);
                case MSG_IDENTIFY -> handleIdentifyMessage(senderPeerId, buf);
                case MSG_DAMAGE_REQUEST -> handleDamageRequestMessage(senderPeerId, buf);
                case MSG_DAMAGE_BROADCAST -> handleDamageBroadcastMessage(senderPeerId, buf);
                case MSG_SHOT -> handleShotMessage(senderPeerId, buf);
                case MSG_WORLD_EVENT -> handleWorldEventMessage(senderPeerId, buf);
                case MSG_OWNERSHIP -> handleOwnershipMessage(senderPeerId, buf);
                case MSG_PICKUP_REQUEST -> handlePickupRequestMessage(senderPeerId, buf);
                case MSG_PICKUP_TAKEN -> handlePickupTakenMessage(senderPeerId, buf);
                case MSG_WEAPON_DROPPED -> handleWeaponDroppedMessage(senderPeerId, buf);
                case MSG_ELIMINATION -> handleEliminationMessage(senderPeerId, buf);
                case MSG_INVENTORY -> handleInventoryMessage(senderPeerId, buf);
                case MSG_SPAWN -> handleSpawnMessage(senderPeerId, buf);
                case MSG_DESPAWN -> handleDespawnMessage(senderPeerId, buf);
                case MSG_VEHICLE_SNAPSHOT -> applyVehicleSnapshotEntry(senderPeerId, NetMessageCodec.decodeVehicleSnapshot(buf));
                case MSG_VEHICLE_SNAPSHOT_BATCH -> {
                    for (NetMessageCodec.DecodedVehicleSnapshot snap : NetMessageCodec.decodeVehicleSnapshotBatch(buf)) {
                        applyVehicleSnapshotEntry(senderPeerId, snap);
                    }
                }
                case MSG_VEHICLE_SEAT_REQUEST -> handleVehicleSeatRequestMessage(senderPeerId, buf);
                case MSG_VEHICLE_OCCUPANCY -> handleVehicleOccupancyMessage(senderPeerId, buf);
                case MSG_VEHICLE_SPAWN -> handleVehicleSpawnMessage(senderPeerId, buf);
                case MSG_WEAPON_SWITCH -> handleWeaponSwitchMessage(senderPeerId, buf);
                case MSG_WAYPOINT -> handleWaypointMessage(senderPeerId, buf);
                default -> GD.print("NetworkManager: dropping packet from " + senderPeerId
                        + " — unknown message tag " + msgType);
            }
        } catch (Exception e) {
            com.openworld.net.NetStats.increment("drop_malformed");
            GD.print("NetworkManager: dropping malformed packet from " + senderPeerId + " — " + e.getMessage());
        }
    }

    /**
     * Client-side handler for an inbound MSG_SNAPSHOT — routes the decoded carrier to the
     * named character's NetworkController, which owns the interpolation buffer (Phase 4).
     * A character with no NetworkController (e.g. our own locally-authoritative body, or
     * a body we don't yet know about) silently ignores the snapshot — there's nothing to
     * drive on this peer for it.
     */
    private void handleSnapshotMessage(int senderPeerId, StreamPeerBuffer buf) {
        applySnapshotEntry(senderPeerId, NetMessageCodec.decodeSnapshot(buf));
    }

    /**
     * Client-side handler for an inbound MSG_SNAPSHOT_BATCH — one frame carrying every
     * replicated character's state for a tick (Round 5 — see NETWORK_REWRITE_PLAN.md
     * "Bug 4"). Decodes the count-prefixed entry list and routes each one through the
     * exact same per-entry validate-and-apply path the singular MSG_SNAPSHOT uses, so
     * NetworkController/Character never need to know whether a snapshot arrived solo
     * or batched.
     */
    private void handleSnapshotBatchMessage(int senderPeerId, StreamPeerBuffer buf) {
        for (NetMessageCodec.DecodedSnapshot snap : NetMessageCodec.decodeSnapshotBatch(buf)) {
            applySnapshotEntry(senderPeerId, snap);
        }
    }

    /** Shared by {@link #handleSnapshotMessage} and {@link #handleSnapshotBatchMessage} — validate, locate, hand to NetworkController. */
    private void applySnapshotEntry(int senderPeerId, NetMessageCodec.DecodedSnapshot snap) {
        if (!isValidSnapshot(snap)) {
            dropInvalid("snapshot", "MSG_SNAPSHOT entry", senderPeerId);
            return;
        }
        Character character = findCharacterById(snap.characterId());
        if (character == null) {
            // A snapshot for a body this peer doesn't know means spawn/baseline never landed
            // (or the entity died locally) — the exact "entity exists on one side only" desync
            // class. Counted + logged once per id so the 30 Hz stream can't spam.
            com.openworld.net.NetStats.increment("snapshot_unknown_character");
            logOnce("snap-unknown:" + snap.characterId(),
                    "NetworkManager: receiving snapshots for unknown character " + snap.characterId()
                            + " — this peer has no body for it (missed spawn/baseline?)");
            return;
        }
        if (character.getController() instanceof NetworkController nc) {
            // On the host this is an owning client's upstream report (client owns locomotion,
            // host owns health → applyHealth=false). On a client this is the host's authoritative
            // downstream snapshot (applyHealth=true).
            if (isServer()) snap = clampUpstreamPosition(snap);   // Step 4 — cheap host validation
            nc.receiveSnapshot(snap, !isServer());
        } else if (isAuthorityFor(character.characterInfo)) {
            // Our own locally-owned body: locomotion is authoritative locally (never overwritten).
            // Health + weapon slot are host-authoritative — apply them so host→client damage and
            // weapon changes show up on the owning client.
            applyOwnBodyDiscreteState(character, snap);
        }
    }

    // ── Host validation clamp (Step 4) ────────────────────────────────────────
    //
    // A client owns its own locomotion (ownership-based authority), so the host normally applies
    // its reported position verbatim. This is the single cheap sanity gate: if a report jumps
    // further than physically plausible in the elapsed time (a buggy/teleporting/forged client), the
    // host eases the accepted position toward the report at a bounded speed instead of snapping —
    // smooth for everyone, while a teleport can't blink a body across the map or through a wall. It
    // never fires in normal play (sprint ≈ 8 m/s ≪ the cap) — "smooth > anti-cheat", with the seam
    // here to tighten later. Keyed per character on the client's own send timestamps.

    /** Generous plausible-speed ceiling (m/s) — well above on-foot sprint; only a genuine teleport exceeds it. */
    private static final double MAX_PLAUSIBLE_SPEED = 40.0;
    /** Vehicle counterpart — above any config's maxSpeed (default 20 m/s) with downhill/boost headroom. */
    private static final double MAX_PLAUSIBLE_VEHICLE_SPEED = 60.0;
    /** Clamp only kicks in past this much overshoot, so float jitter near the ceiling never trips it. */
    private static final double CLAMP_MARGIN = 1.5;
    /** Cap the elapsed-time term so a long delivery gap can't inflate the allowed distance back into "teleport ok". */
    private static final double CLAMP_MAX_DT_SECONDS = 0.5;

    private final Map<String, double[]> lastAcceptedUpstream = new HashMap<>();   // characterId → {x, y, z, timeMs}

    private NetMessageCodec.DecodedSnapshot clampUpstreamPosition(NetMessageCodec.DecodedSnapshot snap) {
        Vector3 pos = clampUpstreamPosition(snap.characterId(), snap.position(), snap.senderTimeMs(), MAX_PLAUSIBLE_SPEED);
        if (pos != snap.position()) {
            snap = new NetMessageCodec.DecodedSnapshot(snap.characterId(), snap.tick(), pos, snap.velocity(),
                    snap.aimTarget(), snap.combat(), snap.stanceOrdinal(), snap.activeSlotIndex(),
                    snap.movementTypeOrdinal(), snap.yaw(), snap.currentHealth(), snap.senderTimeMs(), snap.fireSeq(),
                    snap.activeMagazine(), snap.reloadSeq());
        }
        return snap;
    }

    /** Entity-generic core of the clamp — returns the accepted position (same reference when unchanged). */
    private Vector3 clampUpstreamPosition(String entityId, Vector3 pos, int senderTimeMs, double maxSpeed) {
        double[] prev = lastAcceptedUpstream.get(entityId);
        Vector3 accepted = pos;
        if (prev != null) {
            double dt = Math.min(CLAMP_MAX_DT_SECONDS, Math.max(0.001, (senderTimeMs - prev[3]) / 1000.0));
            double maxDist = maxSpeed * dt * CLAMP_MARGIN;
            double dx = pos.getX() - prev[0], dy = pos.getY() - prev[1], dz = pos.getZ() - prev[2];
            double dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
            if (dist > maxDist && dist > 0.0) {
                double s = maxDist / dist;
                accepted = new Vector3((float) (prev[0] + dx * s), (float) (prev[1] + dy * s), (float) (prev[2] + dz * s));
                GD.print("NetworkManager: clamped implausible upstream move for " + entityId
                        + " (" + String.format("%.1f", dist) + "m > " + String.format("%.1f", maxDist) + "m)");
            }
        }
        lastAcceptedUpstream.put(entityId, new double[]{accepted.getX(), accepted.getY(), accepted.getZ(), senderTimeMs});
        return accepted;
    }

    private void applyOwnBodyDiscreteState(Character character, NetMessageCodec.DecodedSnapshot snap) {
        Health health = findHealth(character);
        if (health != null) health.applyReplicatedHealth(snap.currentHealth());
        // Weapon slot is owner-authoritative, like stance/combat/locomotion: it is driven by this
        // peer's own input, and inventory changes arrive via the reliable MSG_PICKUP_TAKEN /
        // MSG_WEAPON_DROPPED events. Applying the echoed slot here created a feedback loop
        // (Round 10.2): the echo lags every local switch by the host puppet's transition time
        // plus RTT, so right after a switch the echo always disagrees with the local slot, each
        // disagreement triggers another onSetWeapon, and the body oscillates between the two
        // slots forever.
        // No position reconcile under ownership-based authority: the owning client's locomotion
        // is authoritative locally, so there is nothing to correct and nothing to snap.
    }

    // ── Vehicle replication (Round 11 N3) ─────────────────────────────────────

    /**
     * Shared receive path for MSG_VEHICLE_SNAPSHOT (driving client → host) and each
     * MSG_VEHICLE_SNAPSHOT_BATCH entry (host → all) — the vehicle counterpart of
     * {@link #applySnapshotEntry}. Authority split: locomotion belongs to the vehicle's
     * owner (applied via VehicleNetworkController), health to the host (a client applies
     * the host's value; the host never applies a client's).
     */
    private void applyVehicleSnapshotEntry(int senderPeerId, NetMessageCodec.DecodedVehicleSnapshot snap) {
        if (!isValidVehicleSnapshot(snap)) {
            dropInvalid("vehicle_snapshot", "vehicle snapshot", senderPeerId);
            return;
        }
        if (NET_DEBUG_VEHICLES)   // diagnostic: count received entries per id (incl. unknown), reset each dump
            vehicleSnapCount.merge(snap.vehicleId(), 1, Integer::sum);
        com.openworld.carrier.vehicle.Vehicle vehicle =
                findControllableById(snap.vehicleId()) instanceof com.openworld.carrier.vehicle.Vehicle found
                        ? found : null;
        if (vehicle == null) {
            com.openworld.net.NetStats.increment("vehicle_snapshot_unknown");
            // The snapshot stream is the source of truth for streamed vehicles (PLAN.md netcode WS1).
            // A snapshot routinely outruns its reliable MSG_VEHICLE_SPAWN (spawn rides ENet channel 0,
            // snapshots channel 2 — no cross-channel ordering) or arrives ahead of the late-join
            // baseline. Rather than drop it (the old "unknown vehicle" flood), a CLIENT lazy-spawns the
            // puppet here from the snapshot, so the car is born at a live transform and never sits sunk.
            // Idempotent with the reliable spawn — spawnReplicatedVehicle guards duplicate ids; the
            // puppet is born hidden (Vehicle.applyAuthorityState) until its first placement reveals it.
            // yaw = 0: the body is invisible until the first interpolated snapshot supplies its full
            // orientation, so the initial spawn yaw is never seen. The host never lazy-spawns (it owns
            // the authoritative bodies); an unknown snapshot there is a genuine anomaly, so it still drops.
            if (isServer()) return;
            GameManager gm = gameManager();
            if (gm != null) {
                // ephemeral=true: only streamed traffic races its spawn this way. A persistent vehicle
                // arrives via the reliable baseline (ephemeral=false), which then UPGRADES this body out
                // of STREAMED_GROUP in spawnReplicatedVehicle — so a persistent car is never left
                // reconcile-eligible just because its snapshot beat its baseline.
                gm.spawnReplicatedVehicle(new NetMessageCodec.DecodedVehicleSpawn(
                        snap.vehicleId(), "neutral", snap.position(), 0f, SERVER_PEER_ID, true));
                vehicle = findControllableById(snap.vehicleId()) instanceof com.openworld.carrier.vehicle.Vehicle lazy
                        ? lazy : null;
            }
            if (vehicle == null) return;   // container not ready — the next snapshot retries
        }
        // A despawned (wrecked) vehicle lingers in the tree until end of frame — don't
        // attach controllers to or place a node that is already being freed.
        if (vehicle.isQueuedForDeletion()) return;
        if (isAuthorityFor(vehicle.getCharacterInfo())) {
            // Our own echo in the host batch: locomotion is ours; health is host-authoritative,
            // so a driving CLIENT adopts it (mirrors applyOwnBodyDiscreteState).
            if (!isServer()) {
                Health health = findHealth(vehicle);
                if (health != null) health.applyReplicatedHealth(snap.health());
            }
            return;
        }
        if (isServer()) {
            // Upstream report — only the vehicle's owning peer may drive it.
            if (vehicle.getCharacterInfo().ownerPeerId != senderPeerId) {
                com.openworld.net.NetStats.increment("vehicle_snapshot_not_owner");
                return;
            }
            Vector3 pos = clampUpstreamPosition(snap.vehicleId(), snap.position(), snap.senderTimeMs(),
                    MAX_PLAUSIBLE_VEHICLE_SPEED);
            if (pos != snap.position()) {
                snap = new NetMessageCodec.DecodedVehicleSnapshot(snap.vehicleId(), snap.senderTimeMs(), pos,
                        snap.orientation(), snap.linearVelocity(), snap.angularVelocity(), snap.steerAngle(),
                        snap.throttle(), snap.handbrake(), snap.brake(), snap.slipping(),
                        snap.flatMask(), snap.health(), snap.fireSeq());
            }
        }
        // Lazy puppet attach: scene-placed vehicles on a client have no join hook, so the
        // first snapshot is what flips them to frozen+interpolated. Idempotent.
        vehicle.applyAuthorityState();
        if (vehicle.getController() instanceof com.openworld.net.VehicleNetworkController vnc) {
            vnc.receiveSnapshot(snap, !isServer());
            // Client: stamp the last-snapshot time so the ghost reconcile (above) keeps a live car alive.
            if (!isServer()) vehicleLastSnapshotMs.put(snap.vehicleId(), nowMs());
        }
    }

    private boolean isValidVehicleSnapshot(NetMessageCodec.DecodedVehicleSnapshot snap) {
        godot.core.Quaternion q = snap.orientation();
        if (q == null) return false;
        double qLen = Math.sqrt(q.getX() * q.getX() + q.getY() * q.getY() + q.getZ() * q.getZ() + q.getW() * q.getW());
        return isValidIdentifier(snap.vehicleId())
                && isFiniteVector3(snap.position())
                && isFiniteVector3(snap.linearVelocity())
                && isFiniteVector3(snap.angularVelocity())
                && isFiniteDouble(qLen) && qLen >= 0.9 && qLen <= 1.1
                // steerAngle is the actual wheel rotation (radians) — bound by any sane
                // tireMaxTurnDegrees config (90° = 1.6 rad), not the ±1 input range.
                && snap.steerAngle() >= -1.6f && snap.steerAngle() <= 1.6f
                && snap.throttle() >= -1.5f && snap.throttle() <= 1.5f
                && isFiniteDouble(snap.health()) && snap.health() >= 0;
    }

    /**
     * Driving client → host: reports the owned vehicle's state — the vehicle counterpart of
     * {@link #sendOwnedState}, called from PlayerController's vehicle input path and
     * throttled per-entity to the shared replication rate.
     */
    public void sendOwnedVehicleState(com.openworld.carrier.vehicle.Vehicle vehicle) {
        if (!isNetworked() || isServer() || vehicle == null || vehicle.getCharacterInfo() == null
                || vehicle.getCharacterInfo().characterId.isEmpty()) return;
        if (!throttleOwnedSend(vehicle.getCharacterInfo().characterId)) return;
        Health health = findHealth(vehicle);
        float healthValue = health != null ? health.getCurrentHealth() : 0f;
        WeaponController wc = findWeaponController(vehicle);
        int fireSeq = wc != null ? wc.getFireSeq() : 0;
        sendMessage(SERVER_PEER_ID, NetMessageCodec.encodeVehicleSnapshot(MSG_VEHICLE_SNAPSHOT,
                new NetMessageCodec.DecodedVehicleSnapshot(vehicle.getCharacterInfo().characterId, nowMs(),
                        vehicle.getGlobalPosition(), vehicle.getGlobalBasis().getRotationQuaternion(),
                        vehicle.getLinearVelocity(), vehicle.getAngularVelocity(),
                        vehicle.getCurrentSteerAngle(), vehicle.getCurrentThrottle(),
                        vehicle.isHandbraking(), vehicle.isBraking(), vehicle.isSlipping(),
                        vehicle.getFlatMask(), healthValue, fireSeq)));
    }

    /** Client → host: ask the host to (un)seat a character (host-arbitrated enter/exit). No-op on host/single-player — they arbitrate directly. */
    public void requestVehicleSeat(String vehicleId, String characterId, boolean entering, int seatIndex) {
        if (!isNetworked() || isServer()) return;
        sendMessage(SERVER_PEER_ID, NetMessageCodec.encodeVehicleSeatRequest(MSG_VEHICLE_SEAT_REQUEST,
                vehicleId, characterId, entering, seatIndex));
    }

    /** Host → all: the authoritative seat state + locomotion owner for one vehicle seat. */
    public void broadcastVehicleOccupancy(String vehicleId, String occupantCharacterId, int ownerPeerId,
            boolean entering, int seatIndex) {
        if (!isNetworked() || !isServer()) return;
        broadcastMessage(NetMessageCodec.encodeVehicleOccupancy(MSG_VEHICLE_OCCUPANCY,
                vehicleId, occupantCharacterId, ownerPeerId, entering, seatIndex), null);
    }

    /** Host → one peer: late-join occupancy baseline entry for a single occupied seat. */
    public void sendVehicleOccupancy(int targetPeerId, String vehicleId, String occupantCharacterId,
            int ownerPeerId, boolean entering, int seatIndex) {
        if (!isNetworked() || !isServer()) return;
        sendMessage(targetPeerId, NetMessageCodec.encodeVehicleOccupancy(MSG_VEHICLE_OCCUPANCY,
                vehicleId, occupantCharacterId, ownerPeerId, entering, seatIndex));
    }

    /** Host-side seat arbitration entry — decode/validate here, grant logic in GameManager (the established split). */
    private void handleVehicleSeatRequestMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedVehicleSeatRequest req = NetMessageCodec.decodeVehicleSeatRequest(buf);
        if (!isValidIdentifier(req.vehicleId()) || !isValidIdentifier(req.characterId())) {
            dropInvalid("vehicle_seat_request", "MSG_VEHICLE_SEAT_REQUEST", senderPeerId);
            return;
        }
        if (!isServer()) return;
        GameManager manager = gameManager();
        if (manager != null) {
            manager.processVehicleSeatRequest(senderPeerId, req.vehicleId(), req.characterId(),
                    req.entering(), req.seatIndex());
        }
    }

    /** Client-side occupancy apply — the host is the seat authority; a stray client-sent one is dropped. */
    private void handleVehicleOccupancyMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedVehicleOccupancy occ = NetMessageCodec.decodeVehicleOccupancy(buf);
        if (!isValidIdentifier(occ.vehicleId()) || !isBoundedString(occ.occupantCharacterId())
                || occ.ownerPeerId() < 0) {
            dropInvalid("vehicle_occupancy", "MSG_VEHICLE_OCCUPANCY", senderPeerId);
            return;
        }
        if (isServer()) return;
        GameManager manager = gameManager();
        if (manager != null) {
            manager.applyVehicleOccupancy(occ.vehicleId(), occ.occupantCharacterId(), occ.ownerPeerId(),
                    occ.entering(), occ.seatIndex());
        }
    }

    /** Mirrors isValidUserCommand's checks, scoped to the snapshot's smaller field set — see NETWORK_REWRITE_PLAN.md Phase 4. */
    private boolean isValidSnapshot(NetMessageCodec.DecodedSnapshot snap) {
        return isValidIdentifier(snap.characterId())
                && snap.tick() >= 0
                && isFiniteVector3(snap.position())
                && isFiniteVector3(snap.velocity())
                && isFiniteVector3(snap.aimTarget())
                && snap.stanceOrdinal() >= 0 && snap.stanceOrdinal() < StanceName.values().length
                && snap.activeSlotIndex() >= 0 && snap.activeSlotIndex() < WEAPON_SLOT_COUNT
                && snap.movementTypeOrdinal() >= 0 && snap.movementTypeOrdinal() < MovementType.values().length
                && Float.isFinite(snap.yaw())
                && isFiniteDouble(snap.currentHealth()) && snap.currentHealth() >= 0;
    }

    /**
     * Bidirectional identify handshake — see NETWORK_REWRITE_PLAN.md Phase 5.
     * Server branch is identifyPeer's old body verbatim (senderPeerId comes straight from
     * onPacketReceived now, so getMultiplayer().getRemoteSenderId() is no longer needed),
     * plus the missing reply that hands the client its assigned id. Client branch completes
     * what the "(Phase 5/7)" comment on localPeerId always anticipated landing here.
     */
    private void handleIdentifyMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedIdentify id = NetMessageCodec.decodeIdentify(buf);
        if (!isValidIdentify(id)) {
            dropInvalid("identify", "MSG_IDENTIFY", senderPeerId);
            return;
        }
        if (isServer()) {
            // Reply with the assigned peer id BEFORE onPeerIdentified runs — it immediately
            // sends baseline + self MSG_SPAWN traffic on the same reliable/ordered channel
            // (channelSpecFor groups MSG_IDENTIFY with MSG_SPAWN/MSG_DESPAWN), so the client
            // must already know its own localPeerId by the time those frames arrive.
            // Otherwise spawnReplicatedCharacter's isAuthorityFor check (which keys off
            // localPeerId) misclassifies bodies at spawn time — e.g. the client's own
            // server-spawned body would wrongly get a permanent NetworkController
            // (NetworkController.isAuthority() is hardcoded false — the misclassification
            // can never self-correct later) while a remote peer's body would wrongly keep
            // its live local controller.
            sendMessage(senderPeerId, NetMessageCodec.encodeIdentify(MSG_IDENTIFY, "", senderPeerId));
            GameManager manager = gameManager();
            if (manager != null) {
                manager.onPeerIdentified(senderPeerId, id.persistentPlayerId());
            }
        } else {
            localPeerId = id.assignedPeerId();
            GD.print("NetworkManager: assigned local peer id " + localPeerId);
        }
    }

    /** Client → authority: apply damage the relaying peer already resolved against its raycast — requestDamage's old body, isMultiplayerAuthority() swapped for isAuthorityFor() (Phase 0 checklist item 22). */
    private void handleDamageRequestMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedDamageRequest req = NetMessageCodec.decodeDamageRequest(buf);
        if (!isValidDamageRequest(req)) {
            dropInvalid("damage_request", "MSG_DAMAGE_REQUEST", senderPeerId);
            return;
        }
        // Entity-generic (N3): victims may be Characters or Vehicles — both carry Health.
        com.openworld.control.Controllable victim = findControllableById(req.victimCharacterId());
        if (!(victim instanceof Node victimNode) || !isServer()) return;

        Health health = findHealth(victimNode);
        if (health == null) return;
        // attackerPos is null on this relay path (the client sent only the attacker's name, not a
        // position). The common firearm path resolves on the host via MSG_SHOT, where the shooter's
        // host-side position is known. The broadcast (incl. the direction cue) now happens inside
        // applyDamage on the server — covering host-originated damage too — so no explicit broadcast here.
        health.applyNetworkDamage(req.finalDamage(), req.headshot(), req.weaponName(),
                req.attackerName(), req.attackerFaction(), null);
    }

    /** Authority → all: momentary hit-reaction cue — broadcastDamage's old body, isMultiplayerAuthority() swapped for isAuthorityFor(). Non-authority peers only; the authority already played it locally via applyDamage. */
    private void handleDamageBroadcastMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedDamageBroadcast cast = NetMessageCodec.decodeDamageBroadcast(buf);
        if (!isValidDamageBroadcast(cast)) {
            dropInvalid("damage_broadcast", "MSG_DAMAGE_BROADCAST", senderPeerId);
            return;
        }
        com.openworld.control.Controllable victim = findControllableById(cast.victimCharacterId());
        if (!(victim instanceof Node victimNode) || isAuthorityFor(victim.getCharacterInfo())) return;

        Health health = findHealth(victimNode);
        if (health != null) health.hit.emit(cast.damage());

        // Drive this peer's HUD damage-direction indicator: emit characterDamagedFrom with the attacker
        // world position the host included. HUDManager filters to the local player, so this is only
        // meaningful for a client's own body (host-originated or another client's host-resolved shot).
        if (cast.hasSource() && victim.getCharacterInfo() != null) {
            Node busNode = getNodeOrNull("/root/EventBus");
            if (busNode instanceof com.openworld.game.EventBus bus) {
                bus.characterDamagedFrom.emit(victim.getCharacterInfo(), cast.source());
            }
        }
    }

    /**
     * Client → host: resolve a host-authoritative bullet (Round 8 — "client-predicted + host-resolved").
     * The shooter's client already played its own cosmetics and applied no damage; the host raycasts
     * the reported post-spread ray against its own authoritative positions and applies DAMAGE only.
     * The muzzle/tracer cue for every remote viewer (the host's screen and the other clients) rides
     * the shooter's snapshot fireSeq (fire-as-state), so this no longer relays a separate cue.
     * Host-only; a client that receives this drops it.
     */
    private void handleShotMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedShot shot = NetMessageCodec.decodeShot(buf);
        if (!isValidShot(shot)) {
            com.openworld.net.NetStats.increment("shot_invalid");
            dropInvalid("shot", "MSG_SHOT", senderPeerId);
            return;
        }
        if (!isServer()) return;

        Character shooter = findCharacterById(shot.shooterCharacterId());
        if (shooter == null || shooter.characterInfo == null) {
            // Round 11 N1: every dropped shot is a bullet the shooting player fired and
            // nobody else ever saw — never drop one silently.
            com.openworld.net.NetStats.increment("shot_unknown_shooter");
            GD.print("NetworkManager: dropping MSG_SHOT from peer " + senderPeerId
                    + " — unknown shooter " + shot.shooterCharacterId());
            return;
        }
        // Light ownership check: only the peer that owns the shooter may fire it.
        if (shooter.characterInfo.ownerPeerId != senderPeerId) {
            com.openworld.net.NetStats.increment("shot_not_owner");
            GD.print("NetworkManager: dropping MSG_SHOT from peer " + senderPeerId
                    + " — does not own shooter " + shot.shooterCharacterId());
            return;
        }

        WeaponController wc = findWeaponController(shooter);
        if (wc == null) {
            com.openworld.net.NetStats.increment("shot_no_weapon_controller");
            GD.print("NetworkManager: dropping MSG_SHOT for " + shot.shooterCharacterId()
                    + " — no WeaponController on host copy");
            return;
        }
        FirearmItem firearm = wc.getWeaponItem(shot.weaponSlot()) instanceof FirearmItem f ? f : null;
        if (firearm == null) {
            // The host puppet's slot diverged from the owner's inventory (the "client fires,
            // host shows nothing" bug class — MSG_INVENTORY reconciliation heals the state,
            // this keeps the bullets landing meanwhile). Fall back to the puppet's current
            // item, then any held firearm: stats stay host-authoritative either way, only
            // the damage value may briefly come from a sibling weapon. Counted so divergence
            // frequency stays visible.
            com.openworld.net.NetStats.increment("shot_slot_mismatch");
            if (wc.getCurrentWeaponItem() instanceof FirearmItem current) {
                firearm = current;
            } else {
                for (int i = 0; i < wc.getSlotCount() && firearm == null; i++) {
                    if (wc.getWeaponItem(i) instanceof FirearmItem any) firearm = any;
                }
            }
            if (firearm != null) {
                com.openworld.net.NetStats.increment("shot_slot_fallback");
                logOnce("shot-fallback:" + shot.shooterCharacterId(),
                        "NetworkManager: MSG_SHOT slot " + shot.weaponSlot() + " empty on host copy of "
                                + shot.shooterCharacterId() + " — resolving with '" + firearm.getDisplayName()
                                + "' until inventory reconciles");
            } else {
                com.openworld.net.NetStats.increment("shot_dropped_no_firearm");
                GD.print("NetworkManager: dropping MSG_SHOT for " + shot.shooterCharacterId()
                        + " — host copy holds no firearm at all (slot " + shot.weaponSlot() + ")");
                return;
            }
        }
        firearm.resolveServerShot(shot.origin(), shot.direction());   // raycast + damage (cosmetics ride fireSeq)
    }

    /**
     * Host → all: the reliable world-state seam (Step 4 — doors/mission/story/pickups/spawn director).
     * Clients only apply (the host is the sole owner of world truth); a stray client-sent one is
     * dropped. Hands the decoded event to GameManager, which routes it to whichever system owns that
     * eventType — the single place new networked world state plugs in.
     */
    private void handleWorldEventMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedWorldEvent event = NetMessageCodec.decodeWorldEvent(buf);
        if (!isValidWorldEvent(event)) {
            dropInvalid("world_event", "MSG_WORLD_EVENT", senderPeerId);
            return;
        }
        if (isServer()) return;   // host owns world state; never accepts an inbound world event
        GameManager manager = gameManager();
        if (manager != null) manager.onWorldEvent(event.eventType(), event.key(), event.value(), event.args());
    }

    /**
     * Ownership migration (Round 8 Step 3). On the host this is a client's request to take/release an
     * entity's authority (e.g. enter/exit a vehicle): the host applies it and re-broadcasts the
     * authoritative result so every copy agrees. On a client it is the host's authoritative migration
     * to apply locally. The host re-broadcast excludes nobody — re-applying the same ownerPeerId is
     * idempotent, and it guarantees the requesting client converges on the host-confirmed value.
     */
    private void handleOwnershipMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedOwnership own = NetMessageCodec.decodeOwnership(buf);
        if (!isValidIdentifier(own.characterId()) || own.newOwnerPeerId() < 0) {
            dropInvalid("ownership", "MSG_OWNERSHIP", senderPeerId);
            return;
        }
        // Round 11 N3: clients no longer originate ownership changes — vehicle authority
        // migrates inside the host-arbitrated MSG_VEHICLE_OCCUPANCY grant. An inbound
        // client→host ownership request is therefore always illegitimate (forged or stale).
        if (isServer()) {
            com.openworld.net.NetStats.increment("ownership_client_rejected");
            GD.print("NetworkManager: rejecting client-originated MSG_OWNERSHIP from peer " + senderPeerId);
            return;
        }
        applyOwnershipLocal(own.characterId(), own.newOwnerPeerId());
    }

    /**
     * Reassigns an entity's authoritative owner — HOST-ONLY since Round 11 N3 (vehicle driver
     * migration now rides inside the MSG_VEHICLE_OCCUPANCY grant; handleOwnershipMessage rejects
     * client-originated requests). No-op in single-player. ownerPeerId lives on the shared
     * CharacterInfo that Controller.isAuthority() reads, so the sim/replication split follows
     * the migration automatically.
     */
    public void setEntityOwner(String characterId, int newOwnerPeerId) {
        if (!isNetworked()) return;
        if (!isServer()) {
            GD.printErr("NetworkManager: setEntityOwner is host-only — clients request authority"
                    + " through host-arbitrated grants (ignored for " + characterId + ")");
            return;
        }
        applyOwnershipLocal(characterId, newOwnerPeerId);
        broadcastMessage(NetMessageCodec.encodeOwnership(MSG_OWNERSHIP, characterId, newOwnerPeerId), null);
    }

    /** Writes the new ownerPeerId onto the entity's shared CharacterInfo (works for any Controllable — Character or Vehicle). */
    private void applyOwnershipLocal(String characterId, int newOwnerPeerId) {
        CharacterInfo info = findCharacterInfoById(characterId);
        if (info != null) info.ownerPeerId = newOwnerPeerId;
    }

    /** Resolves a characterId to any owned entity's CharacterInfo via the "characters" group (Characters and Vehicles both register there). */
    private CharacterInfo findCharacterInfoById(String characterId) {
        com.openworld.control.Controllable c = findControllableById(characterId);
        return c != null ? c.getCharacterInfo() : null;
    }

    /**
     * Server → client: instantiate a remote-spawned character — folds in the
     * previously-approved G3 MultiplayerSpawner follow-up (see NETWORK_REWRITE_PLAN.md
     * Phase 7). Clients only; the server is the sole spawn authority and never
     * receives this (a client could otherwise spoof world population). Delegates the
     * actual scene load/instantiate/stamp to GameManager.spawnReplicatedCharacter,
     * which mirrors spawnPlayerBody's shape — same "NetworkManager decodes, GameManager
     * acts" split as handleIdentifyMessage → onPeerIdentified.
     */
    private void handleSpawnMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedSpawn spawn = NetMessageCodec.decodeSpawn(buf);
        if (!isValidSpawn(spawn)) {
            dropInvalid("spawn", "MSG_SPAWN", senderPeerId);
            return;
        }
        if (isServer()) return;

        GameManager manager = gameManager();
        if (manager != null) manager.spawnReplicatedCharacter(spawn);
    }

    /**
     * Client → host: arbitrate a world-pickup request (Phase D — host-arbitrated pickups).
     * Host-only; decode/validate here, grant logic in GameManager.processPickupRequest
     * (same "NetworkManager decodes, GameManager acts" split as spawn/identify).
     */
    private void handlePickupRequestMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedPickupRequest req = NetMessageCodec.decodePickupRequest(buf);
        if (!isValidIdentifier(req.pickupId()) || !isValidIdentifier(req.characterId())) {
            dropInvalid("pickup_request", "MSG_PICKUP_REQUEST", senderPeerId);
            return;
        }
        if (!isServer()) return;
        GameManager manager = gameManager();
        if (manager != null) {
            manager.processPickupRequest(senderPeerId, req.pickupId(), req.characterId());
        }
    }

    /**
     * Host → all: a pickup was collected — mirror the collect locally (Phase D). Clients only;
     * the host already applied it before broadcasting (its own collect, or the grant path).
     */
    private void handlePickupTakenMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedPickupTaken taken = NetMessageCodec.decodePickupTaken(buf);
        if (!isValidIdentifier(taken.pickupId()) || !isValidIdentifier(taken.characterId())
                || taken.magazine() < 0 || taken.reserve() < 0) {
            dropInvalid("pickup_taken", "MSG_PICKUP_TAKEN", senderPeerId);
            return;
        }
        if (isServer()) return;
        GameManager manager = gameManager();
        if (manager != null) {
            manager.applyReplicatedPickup(taken.pickupId(), taken.characterId(), taken.magazine(), taken.reserve());
        }
    }

    /** Client → host: ask to collect a world pickup with an owned character. No-op on host/single-player (they collect directly). */
    public void requestPickup(String pickupId, String characterId) {
        if (!isNetworked() || isServer()) return;
        GD.print("NetworkManager: requesting pickup '" + pickupId + "' with character " + characterId);
        sendMessage(SERVER_PEER_ID, NetMessageCodec.encodePickupRequest(MSG_PICKUP_REQUEST, pickupId, characterId));
    }

    /** Host → all: announce a collected pickup so every client mirrors it. No-op off the host. */
    public void broadcastPickupTaken(String pickupId, String characterId, int magazine, int reserve) {
        if (!isNetworked() || !isServer()) return;
        broadcastMessage(NetMessageCodec.encodePickupTaken(MSG_PICKUP_TAKEN, pickupId, characterId, magazine, reserve), null);
    }

    /** Host → one peer: grant confirmation/late-join baseline entry for a single taken pickup. */
    public void sendPickupTaken(int targetPeerId, String pickupId, String characterId, int magazine, int reserve) {
        if (!isNetworked() || !isServer()) return;
        sendMessage(targetPeerId, NetMessageCodec.encodePickupTaken(MSG_PICKUP_TAKEN, pickupId, characterId, magazine, reserve));
    }

    /**
     * Host → one peer: late-join pickup baseline — one MSG_PICKUP_TAKEN per world weapon that is
     * currently held by a character, so a joiner's world doesn't still show items collected before
     * it connected. Sent AFTER sendBaselineSpawns on the same reliable channel, so the holder body
     * always exists on the joiner before its pickup event arrives. Items whose pickupId doesn't
     * resolve on the joiner (e.g. a character's scene-default loadout, whose path-derived id embeds
     * the differently-named owner node) no-op harmlessly there — those weapons already exist inside
     * the replicated character scene anyway.
     */
    public void sendBaselinePickups(int targetPeerId) {
        if (!isServer() || getTree() == null) return;
        for (Node node : getTree().getNodesInGroup(new StringName(com.openworld.item.Pickup.PICKUPS_GROUP))) {
            if (!(node instanceof com.openworld.weapon.WeaponItem item) || !item.isTaken()) continue;
            if (!(item.getOwningCharacter() instanceof Character holder) || holder.characterInfo == null) continue;
            // Loadout items inside character scenes derive path ids that exceed the wire's string
            // cap and embed peer-local node names — they can never resolve on the joiner (its copy
            // ships inside the replicated character scene already), so skip instead of sending a
            // frame the receiver would log-drop as invalid.
            if (!isValidIdentifier(item.pickupId)) continue;
            GD.print("NetworkManager: baseline pickup '" + item.pickupId + "' held by "
                    + holder.characterInfo.characterId + " → peer " + targetPeerId);
            sendPickupTaken(targetPeerId, item.pickupId, holder.characterInfo.characterId,
                    item.getMagazine(), item.getReserve());
        }
    }

    /**
     * Weapon-drop replication (Phase E). On the host this is an owning client's drop report:
     * validate the sender owns the character, apply, and fan out to the other clients (the
     * originator already executed it locally — excluded from the re-broadcast). On a client it
     * is the host's authoritative event (host's own drops, AI death scatter, or a relayed
     * client drop) — just apply.
     */
    private void handleWeaponDroppedMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedWeaponDropped drop = NetMessageCodec.decodeWeaponDropped(buf);
        if (!isValidWeaponDropped(drop)) {
            dropInvalid("weapon_dropped", "MSG_WEAPON_DROPPED", senderPeerId);
            return;
        }
        if (isServer()) {
            Character character = findCharacterById(drop.characterId());
            if (character == null || character.characterInfo == null
                    || character.characterInfo.ownerPeerId != senderPeerId) {
                GD.print("NetworkManager: rejecting MSG_WEAPON_DROPPED from non-owner peer " + senderPeerId);
                return;
            }
            broadcastMessage(NetMessageCodec.encodeWeaponDropped(MSG_WEAPON_DROPPED, drop.characterId(),
                    drop.slot(), drop.oldPickupId(), drop.newPickupId(), drop.position(), drop.impulse(),
                    drop.magazine(), drop.reserve()), senderPeerId);
        }
        GameManager manager = gameManager();
        if (manager != null) {
            manager.applyReplicatedDrop(drop.characterId(), drop.slot(), drop.oldPickupId(),
                    drop.newPickupId(), drop.position(), drop.impulse(), drop.magazine(), drop.reserve());
        }
    }

    private boolean isValidWeaponDropped(NetMessageCodec.DecodedWeaponDropped drop) {
        return isValidIdentifier(drop.characterId())
                && drop.slot() >= 0 && drop.slot() < WEAPON_SLOT_COUNT
                && isValidIdentifier(drop.oldPickupId())
                && isValidIdentifier(drop.newPickupId())
                && isFiniteVector3(drop.position())
                && isFiniteVector3(drop.impulse())
                && drop.magazine() >= 0 && drop.reserve() >= 0;
    }

    /**
     * Owner/host → network: announce a weapon returned to the world (Phase E — called by
     * WeaponController.announceWeaponDropped, which already gated on who may announce).
     * Host: broadcast to every client. Client: report to the host, which validates and fans out.
     */
    public void sendWeaponDropped(String characterId, int slot, String oldPickupId, String newPickupId,
            godot.core.Vector3 position, godot.core.Vector3 impulse, int magazine, int reserve) {
        if (!isNetworked()) return;
        PackedByteArray frame = NetMessageCodec.encodeWeaponDropped(MSG_WEAPON_DROPPED, characterId,
                slot, oldPickupId, newPickupId, position, impulse, magazine, reserve);
        if (isServer()) broadcastMessage(frame, null);
        else sendMessage(SERVER_PEER_ID, frame);
    }

    /**
     * Owner/host → network: announce the START of a weapon switch (G4-1 — called by
     * WeaponController.onSetWeapon, which already gated on local authority for this body).
     * Host: broadcast to every client. Client: report to the host, which validates and fans out.
     * Puppets snap the slot + start an aligned cosmetic draw immediately, so a remote switch lands
     * as promptly as the owner's instead of a full draw-animation late.
     */
    public void sendWeaponSwitch(String characterId, int targetSlot) {
        if (!isNetworked()) return;
        PackedByteArray frame = NetMessageCodec.encodeWeaponSwitch(MSG_WEAPON_SWITCH, characterId, targetSlot);
        if (isServer()) broadcastMessage(frame, null);
        else sendMessage(SERVER_PEER_ID, frame);
    }

    /** MSG_WEAPON_SWITCH (owner → host → all): apply the equip-start on this peer's copy; host validates owner + fans out. */
    private void handleWeaponSwitchMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedWeaponSwitch sw = NetMessageCodec.decodeWeaponSwitch(buf);
        if (!isValidIdentifier(sw.characterId()) || sw.targetSlot() < 0 || sw.targetSlot() >= WEAPON_SLOT_COUNT) {
            dropInvalid("weapon_switch", "MSG_WEAPON_SWITCH", senderPeerId);
            return;
        }
        Character character = findCharacterById(sw.characterId());
        if (isServer()) {
            if (character == null || character.characterInfo == null
                    || character.characterInfo.ownerPeerId != senderPeerId) {
                GD.print("NetworkManager: rejecting MSG_WEAPON_SWITCH from non-owner peer " + senderPeerId);
                return;
            }
            broadcastMessage(NetMessageCodec.encodeWeaponSwitch(MSG_WEAPON_SWITCH, sw.characterId(),
                    sw.targetSlot()), senderPeerId);
        }
        if (character == null) return;
        WeaponController wc = findWeaponController(character);
        if (wc != null) wc.applyReplicatedWeaponSlot(sw.targetSlot());
    }

    /**
     * Owner → network: set/clear a player's GPS waypoint (I5). Mirrors {@link #sendWeaponSwitch} —
     * host broadcasts to every client; a client reports to the host, which validates the owner and
     * fans out. Low-frequency (on map click), so reliable. {@code pos} is ignored when
     * {@code hasWaypoint} is false (a clear).
     */
    public void broadcastWaypoint(String characterId, boolean hasWaypoint, godot.core.Vector3 pos) {
        if (!isNetworked()) return;
        PackedByteArray frame = NetMessageCodec.encodeWaypoint(MSG_WAYPOINT, characterId, hasWaypoint, pos);
        if (isServer()) broadcastMessage(frame, null);
        else sendMessage(SERVER_PEER_ID, frame);
    }

    /** MSG_WAYPOINT (owner → host → all): update the WaypointStore on this peer; host validates owner + fans out. */
    private void handleWaypointMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedWaypoint wp = NetMessageCodec.decodeWaypoint(buf);
        if (!isValidIdentifier(wp.characterId())) {
            dropInvalid("waypoint", "MSG_WAYPOINT", senderPeerId);
            return;
        }
        if (isServer()) {
            Character character = findCharacterById(wp.characterId());
            if (character == null || character.characterInfo == null
                    || character.characterInfo.ownerPeerId != senderPeerId) {
                GD.print("NetworkManager: rejecting MSG_WAYPOINT from non-owner peer " + senderPeerId);
                return;
            }
            broadcastMessage(NetMessageCodec.encodeWaypoint(MSG_WAYPOINT, wp.characterId(),
                    wp.hasWaypoint(), wp.position()), senderPeerId);
        }
        if (wp.hasWaypoint()) com.openworld.game.WaypointStore.set(wp.characterId(), wp.position());
        else com.openworld.game.WaypointStore.clear(wp.characterId());
    }

    // ── Reliable elimination + inventory reconciliation (Round 11 N2) ─────────
    //
    // MSG_ELIMINATION: death was previously visible to non-authority peers only as an
    // unreliable health==0 snapshot (DeathLatch) — sufficient for visuals when everything
    // else is in sync, but kill feed / mission progress / a client's own death sequence
    // never crossed the wire at all, and a diverged puppet could stay standing forever.
    // This is the reliable, ordered death event every peer applies.
    //
    // MSG_INVENTORY: the state-sync backstop for the event-replicated inventory
    // (PICKUP_TAKEN/WEAPON_DROPPED). The host round-robins one character's slot manifest
    // per INVENTORY_SWEEP_INTERVAL_MS; receivers reconcile their copies toward it. Any
    // missed/raced inventory event — the root of "client fires but host shows nothing"
    // and "AI never fire on clients" (their runtime-equipped rifles were never
    // replicated by any event) — now self-heals within one sweep cycle.

    private static final int INVENTORY_SWEEP_INTERVAL_MS = 300;
    private int lastInventorySweepMs = 0;
    private int inventorySweepCursor = 0;

    /** Host → all: reliable death/kill event. Called by Health.applyDamage on the authority; no-op off the host. */
    public void broadcastElimination(String victimCharacterId, String victimName, String victimFaction,
            String attackerName, String attackerFaction, String weaponName, boolean headshot) {
        if (!isNetworked() || !isServer()) return;
        broadcastMessage(NetMessageCodec.encodeElimination(MSG_ELIMINATION, victimCharacterId, victimName,
                victimFaction, attackerName, attackerFaction, weaponName, headshot), null);
    }

    /** Client-side MSG_ELIMINATION apply — host owns death truth; a stray client-sent one is dropped. */
    private void handleEliminationMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedElimination elim = NetMessageCodec.decodeElimination(buf);
        if (!isValidElimination(elim)) {
            dropInvalid("elimination", "MSG_ELIMINATION", senderPeerId);
            return;
        }
        if (isServer()) return;
        GameManager manager = gameManager();
        if (manager != null) manager.applyReplicatedElimination(elim);
    }

    private boolean isValidElimination(NetMessageCodec.DecodedElimination elim) {
        return isValidIdentifier(elim.victimCharacterId())
                && isBoundedString(elim.victimName()) && isBoundedString(elim.victimFaction())
                && isBoundedString(elim.attackerName()) && isBoundedString(elim.attackerFaction())
                && isBoundedString(elim.weaponName());
    }

    /** Host: every INVENTORY_SWEEP_INTERVAL_MS, broadcast the next character's slot manifest (round-robin). */
    private void sweepInventoryBroadcast(int now) {
        if (now - lastInventorySweepMs < INVENTORY_SWEEP_INTERVAL_MS) return;
        lastInventorySweepMs = now;
        List<Character> characters = new ArrayList<>();
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (node instanceof Character c && c.characterInfo != null) characters.add(c);
        }
        if (characters.isEmpty()) return;
        inventorySweepCursor = inventorySweepCursor % characters.size();
        broadcastInventoryFor(characters.get(inventorySweepCursor));
        inventorySweepCursor++;
    }

    /** Host → all: one character's authoritative slot manifest. No-op for characters without a WeaponController. */
    public void broadcastInventoryFor(Character character) {
        if (!isNetworked() || !isServer() || character == null || character.characterInfo == null) return;
        WeaponController wc = findWeaponController(character);
        if (wc == null) return;
        broadcastMessage(NetMessageCodec.encodeInventory(MSG_INVENTORY,
                character.characterInfo.characterId, wc.buildInventoryEntries()), null);
    }

    /**
     * Host → one peer: late-join inventory baseline — one manifest per live character, sent AFTER
     * sendBaselineSpawns/sendBaselinePickups on the same reliable channel so the bodies exist and
     * the pickup events have applied before the manifests reconcile whatever those couldn't cover
     * (e.g. AI rifles equipped at runtime, which no pickup event ever carried).
     */
    public void sendBaselineInventories(int targetPeerId) {
        if (!isServer() || getTree() == null) return;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (!(node instanceof Character c) || c.characterInfo == null) continue;
            WeaponController wc = findWeaponController(c);
            if (wc == null) continue;
            sendMessage(targetPeerId, NetMessageCodec.encodeInventory(MSG_INVENTORY,
                    c.characterInfo.characterId, wc.buildInventoryEntries()));
        }
    }

    /** Client-side MSG_INVENTORY apply — host is the manifest source; a stray client-sent one is dropped. */
    private void handleInventoryMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedInventory inv = NetMessageCodec.decodeInventory(buf);
        if (!isValidInventory(inv)) {
            dropInvalid("inventory", "MSG_INVENTORY", senderPeerId);
            return;
        }
        if (isServer()) return;
        GameManager manager = gameManager();
        if (manager != null) manager.applyReplicatedInventory(inv);
    }

    private boolean isValidInventory(NetMessageCodec.DecodedInventory inv) {
        if (!isValidIdentifier(inv.characterId())) return false;
        for (NetMessageCodec.InventorySlotEntry e : inv.entries()) {
            if (e.slot() <= 0 || e.slot() >= WEAPON_SLOT_COUNT) return false;   // slot 0 (fist) never replicates
            if (!isBoundedString(e.weaponId()) || !isBoundedString(e.pickupId())) return false;
            // scenePath is the one wire string a receiver LOADS — bound it to project weapon
            // scenes so a hostile frame can never make a client instantiate an arbitrary
            // resource (same rationale as MSG_SPAWN's sceneSelector enum).
            String path = e.scenePath();
            if (path == null || path.length() > 128) return false;
            if (!path.isEmpty() && !(path.startsWith("res://") && path.endsWith(".tscn"))) return false;
            if (e.magazine() < 0 || e.reserve() < 0) return false;
        }
        return true;
    }

    /** Server → client: remove a server-despawned character. Clients only — same authority rationale as handleSpawnMessage. */
    private void handleDespawnMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedDespawn despawn = NetMessageCodec.decodeDespawn(buf);
        if (!isValidDespawn(despawn)) {
            dropInvalid("despawn", "MSG_DESPAWN", senderPeerId);
            return;
        }
        if (isServer()) return;

        GameManager manager = gameManager();
        if (manager != null) manager.despawnReplicatedCharacter(despawn.characterId());
    }

    /** assignedPeerId is empty/0 in the client→server direction (ignored); persistentPlayerId is empty in the server→client reply (ignored) — exactly one of the two carries real data per direction. */
    private boolean isValidIdentify(NetMessageCodec.DecodedIdentify id) {
        return id.assignedPeerId() >= 0
                && (id.persistentPlayerId().isEmpty() || isValidIdentifier(id.persistentPlayerId()));
    }

    private boolean isValidDamageRequest(NetMessageCodec.DecodedDamageRequest req) {
        return isValidIdentifier(req.victimCharacterId())
                && isFiniteDouble(req.finalDamage()) && req.finalDamage() >= 0
                && isBoundedString(req.weaponName())
                && isBoundedString(req.attackerName())
                && isBoundedString(req.attackerFaction());
    }

    private boolean isValidDamageBroadcast(NetMessageCodec.DecodedDamageBroadcast cast) {
        return isValidIdentifier(cast.victimCharacterId())
                && isFiniteDouble(cast.damage()) && cast.damage() >= 0;
    }


    private boolean isValidShot(NetMessageCodec.DecodedShot shot) {
        return isValidIdentifier(shot.shooterCharacterId())
                && isFiniteVector3(shot.origin())
                && isFiniteVector3(shot.direction())
                && shot.weaponSlot() >= 0 && shot.weaponSlot() < WEAPON_SLOT_COUNT;
    }

    private boolean isValidWorldEvent(NetMessageCodec.DecodedWorldEvent event) {
        if (event.key() == null || event.key().length() > MAX_STRING_LENGTH
                || event.eventType() < 0 || !isFiniteDouble(event.value())
                || event.args() == null || event.args().size() > NetMessageCodec.MAX_WORLD_EVENT_ARGS) {
            return false;
        }
        for (String arg : event.args()) {
            if (arg == null || arg.length() > MAX_STRING_LENGTH) return false;
        }
        return true;
    }

    private boolean isValidSpawn(NetMessageCodec.DecodedSpawn spawn) {
        return isValidIdentifier(spawn.characterId())
                && isBoundedString(spawn.displayName())
                && isBoundedString(spawn.faction())
                && (spawn.sceneSelector() == NetMessageCodec.SCENE_PLAYER || spawn.sceneSelector() == NetMessageCodec.SCENE_AI)
                && isFiniteVector3(spawn.position())
                && spawn.ownerPeerId() >= 0;
    }

    private boolean isValidDespawn(NetMessageCodec.DecodedDespawn despawn) {
        return isValidIdentifier(despawn.characterId());
    }

    /** Looser than isValidIdentifier — allows empty (Health.takeDamage's environmental-damage overload defaults weapon/attacker fields to ""). */
    private static boolean isBoundedString(String s) {
        return s != null && s.length() <= MAX_STRING_LENGTH;
    }

    private static boolean isValidIdentifier(String s) {
        return s != null && !s.isEmpty() && s.length() <= MAX_STRING_LENGTH;
    }

    private static boolean isFiniteVector3(Vector3 v) {
        return v != null && isFiniteDouble(v.getX()) && isFiniteDouble(v.getY()) && isFiniteDouble(v.getZ());
    }

    private static boolean isFiniteDouble(double d) {
        return !Double.isNaN(d) && !Double.isInfinite(d);
    }

    /**
     * Per-peer token bucket — bounds how many application messages a single peer can push
     * per second, independent of ENet's own congestion control (which doesn't protect against
     * an authenticated peer flooding the dispatch/validation path with cheap-to-send garbage).
     */
    private static final class TokenBucket {
        private final double capacity;
        private final double refillPerSecond;
        private double tokens;
        private long lastRefillNanos;

        TokenBucket(double capacity, double refillPerSecond) {
            this.capacity = capacity;
            this.refillPerSecond = refillPerSecond;
            this.tokens = capacity;
            this.lastRefillNanos = System.nanoTime();
        }

        boolean tryConsume() {
            long now = System.nanoTime();
            double elapsedSeconds = (now - lastRefillNanos) / 1_000_000_000.0;
            lastRefillNanos = now;
            tokens = Math.min(capacity, tokens + elapsedSeconds * refillPerSecond);
            if (tokens < 1.0) return false;
            tokens -= 1.0;
            return true;
        }
    }

    // ── Peer lifecycle ────────────────────────────────────────────────────────

    /** Starts a raw ENet server on {@code port}; this peer becomes the authority for everything. */
    public boolean hostServer(int port) {
        ENetConnection host = new ENetConnection();
        // Signature: (bindAddress, bindPort, maxPeers, maxChannels, inBandwidth, outBandwidth).
        // CHANNEL_COUNT must be the maxChannels arg — passing it one slot to the right caps
        // outgoing bandwidth at 4 BYTES/SECOND, which ENet enforces by throttling/dropping
        // unreliable packets (all snapshot channels) into multi-second bursts. 0 = unlimited.
        Error result = host.createHostBound("*", port, DEFAULT_MAX_CLIENTS, CHANNEL_COUNT, 0, 0);
        if (result != Error.OK) {
            GD.print("NetworkManager: failed to host on port " + port + " (" + result + ")");
            return false;
        }
        resetTransport();
        connection = host;
        amServer = true;
        localPeerId = SERVER_PEER_ID;
        GD.print("NetworkManager: hosting on port " + port);
        return true;
    }

    /** Connects to a raw ENet server at {@code address:port} as a client. */
    public boolean joinServer(String address, int port) {
        ENetConnection host = new ENetConnection();
        // Same parameter-order hazard as hostServer: (maxPeers, maxChannels, inBandwidth, outBandwidth).
        Error result = host.createHost(1, CHANNEL_COUNT, 0, 0);
        if (result != Error.OK) {
            GD.print("NetworkManager: failed to create client host (" + result + ")");
            return false;
        }
        ENetPacketPeer peer = host.connectToHost(address, port, CHANNEL_COUNT, 0);
        if (peer == null) {
            GD.print("NetworkManager: failed to connect to " + address + ":" + port);
            return false;
        }
        resetTransport();
        connection = host;
        serverPeer = peer;
        amServer = false;
        // Tighten ENet's own drop timeout so a vanished host surfaces a transport DISCONNECT in a
        // few seconds rather than ENet's ~30 s default — the app-level watchdog (HOST_TIMEOUT_MS)
        // is the binding-independent backstop, this just makes the fast path fast.
        peer.setTimeout(0, 3000, HOST_TIMEOUT_MS);
        // Seed liveness so the watchdog doesn't fire before the first packet has had a chance to land.
        lastServerPacketMs = nowMs();
        GD.print("NetworkManager: connecting to " + address + ":" + port);
        return true;
    }

    /**
     * Releases the previous transport's native resources before swapping it out (or on
     * shutdown — see {@link #_exitTree}). {@link ENetConnection} is {@code RefCounted} but
     * does {@code not} free its underlying ENet host (socket etc.) on GC — {@code destroy()}
     * is the explicit release valve; skipping it is what produced the "1 resources still in
     * use at exit" / "ObjectDB instances leaked at exit" warnings on quit.
     */
    private void resetTransport() {
        if (connection != null) connection.destroy();
        connection = null;
        serverPeer = null;
        amServer = false;
        peersById.clear();
        idsByPeer.clear();
        rateLimiters.clear();
        nextPeerId = 2;
        localPeerId = SERVER_PEER_ID;   // back to the single-player assumption until the next host/join
        hostLossHandled = false;        // a fresh session may legitimately detect host loss again
        loggedOnceKeys.clear();   // a fresh session may legitimately re-hit one-shot diagnostics
        lastAcceptedUpstream.clear();
        lastOwnedStateSendMsById.clear();
    }

    /**
     * Public teardown: drop the live session and return to single-player. Distinct from the private
     * {@link #resetTransport} (an internal swap-out step) — this is the entry point UI/lifecycle code
     * calls before a scene reload so a "restart" actually ends the network session instead of carrying
     * a stale ENet connection across the reload (NetworkManager is an AutoLoad — it outlives the
     * scene). After it, {@link #isNetworked()} is false and every single-player path works again.
     */
    public void leaveSession() {
        if (connection == null) return;
        GD.print("NetworkManager: leaving session");
        resetTransport();
    }

    /** AutoLoad singletons outlive every scene — release the ENet host on shutdown, not just on rehost/rejoin. */
    @Register
    @Override
    public void _exitTree() {
        resetTransport();
    }

    /** True once a raw ENet connection is active — i.e. this session is networked at all. */
    public boolean isNetworked() {
        return connection != null;
    }

    /** True when this peer is the authority (server/host). Single-player counts as authority. */
    public boolean isServer() {
        return connection == null || amServer;
    }

    /** True when this peer owns/drives {@code info} — single-player's localPeerId is always SERVER_PEER_ID. */
    public boolean isAuthorityFor(CharacterInfo info) {
        return info != null && info.ownerPeerId == localPeerId;
    }

    // ── Identity handshake / rejoin (Part G — Step 6) ────────────────────────
    //
    // ENet reassigns peer ids on every (re)connection, so they can't key session
    // state across disconnects. Each client caches a stable UUID (PersistentPlayerId)
    // and reports it once via MSG_IDENTIFY immediately after connecting (the
    // CONNECT-event trigger lives in handlePeerConnected — see NETWORK_REWRITE_PLAN.md
    // Phase 5); the server matches it against MissionManager.activeSessions to tell
    // a rejoin from a fresh join, then replies with the peer id it assigned, which
    // the client adopts as localPeerId (see handleIdentifyMessage / isAuthorityFor).

    /** Client → server: encodes + sends our persistent identity. The reply is handled in handleIdentifyMessage. */
    private void identifyPeer(String persistentPlayerId) {
        sendMessage(SERVER_PEER_ID, NetMessageCodec.encodeIdentify(MSG_IDENTIFY, persistentPlayerId, 0));
    }

    // ── Owned-state upstream (ownership-based authority) ──────────────────────

    /**
     * Owning client → host: reports this body's OWN authoritative state as a single MSG_SNAPSHOT
     * (ownership-based authority — the client owns its locomotion). Replaces the old MSG_USER_COMMAND
     * upstream: instead of sending inputs for the host to re-simulate (the dual-simulation that
     * teleported), the client sends where it actually is. The host applies it to this body's
     * NetworkController puppet (skipping health) and re-broadcasts it to the other peers. Called by
     * PlayerController every physics frame, but throttled here to {@link #REPLICATION_INTERVAL_MS}
     * (~30 Hz) so it streams at the SAME density the host broadcasts — symmetric interpolation both
     * ways, and no 60 Hz flood tripping the host's receive rate limiter. No-op on the host (its own
     * body is broadcast directly).
     */
    public void sendOwnedState(Character body) {
        if (!isNetworked() || isServer() || body == null || body.characterInfo == null) return;
        if (!throttleOwnedSend(body.characterInfo.characterId)) return;
        Health health = findHealth(body);
        WeaponController wc = findWeaponController(body);
        if (health == null || wc == null) return;
        sendMessage(SERVER_PEER_ID, NetMessageCodec.encodeSnapshot(MSG_SNAPSHOT, body.characterInfo.characterId,
                body.getCurrentTick(), body.getGlobalPosition(), body.getVelocity(), body.getAimTargetPosition(),
                body.isCombat(), body.getStanceOrdinal(), wc.getReplicatedActiveSlot(), body.getMovementTypeOrdinal(),
                body.getFacingYaw(), health.getCurrentHealth(), nowMs(), wc.getFireSeq(), wc.getActiveMagazine(),
                wc.getReloadSeq()));
    }

    /**
     * Owning client → host: report a fired shot for host-authoritative resolution (Round 8). The
     * client has already predicted all cosmetics locally; the host raycasts {@code origin/direction}
     * against authoritative positions and applies the damage (see {@link #handleShotMessage}). No-op
     * on the host / single-player, where {@code FirearmItem.performHitscan} resolves locally.
     */
    public void sendShot(String shooterCharacterId, Vector3 origin, Vector3 direction, int weaponSlot) {
        if (!isNetworked() || isServer()) return;
        sendMessage(SERVER_PEER_ID, NetMessageCodec.encodeShot(MSG_SHOT, shooterCharacterId, origin, direction, weaponSlot));
    }

    // ── Damage authority + event broadcasts (Part G — Step 3) ────────────────
    //
    // Health.takeDamage already resolves the bone multiplier/headshot locally
    // (it has the real PhysicalBone3D hitNode, which can't cross the wire as a
    // Variant) and relays only the final numbers here. requestDamage is the
    // client→authority guard; broadcastDamage is a momentary hit-reaction
    // VFX/audio cue — health itself now replicates continuously via MSG_SNAPSHOT
    // (Phase 4's applyReplicatedHealth), so this never carries the health value.
    //
    // The receiver-side bodies live in handleDamageRequestMessage/handleDamageBroadcastMessage/
    // handleWeaponFireMessage (MSG_* dispatch handlers, see NETWORK_REWRITE_PLAN.md Phase 5);
    // these three are now plain sender-side encode+send/broadcast helpers — the
    // names/signatures Health/WeaponController call are unchanged, only the transport is.

    /** Client → authority: encodes + relays a resolved hit to the server (only the server can be addressed directly — star topology). Called by Health.relayDamageToAuthority. */
    public void requestDamage(String victimCharacterId, float finalDamage, boolean headshot,
            String weaponName, String attackerName, String attackerFaction) {
        sendMessage(SERVER_PEER_ID, NetMessageCodec.encodeDamageRequest(MSG_DAMAGE_REQUEST,
                victimCharacterId, finalDamage, headshot, weaponName, attackerName, attackerFaction));
    }

    /**
     * Authority → all: encodes + broadcasts the momentary hit-reaction cue, plus the attacker's world
     * position ({@code hasSource}/{@code source}) so the victim's peer can drive the HUD
     * damage-direction indicator. Called from {@code Health.applyDamage} on the server for every
     * networked victim (host-originated and client-relayed damage alike), so this is now the single
     * broadcast site.
     */
    public void broadcastDamage(String victimCharacterId, float damage, boolean hasSource,
            godot.core.Vector3 source) {
        broadcastMessage(NetMessageCodec.encodeDamageBroadcast(MSG_DAMAGE_BROADCAST, victimCharacterId,
                damage, hasSource, source), null);
    }


    /**
     * Host → all: announce an authoritative world-state change (Step 4 seam). The host applies the
     * change locally itself (it owns the truth) and calls this to replicate it to every client.
     * No-op off the host / single-player. The single entry point every future networked world system
     * (doors, mission/story, pickups, spawn director) sends through — see {@link #handleWorldEventMessage}.
     */
    public void broadcastWorldEvent(int eventType, String key, float value) {
        broadcastWorldEvent(eventType, key, value, java.util.List.of());
    }

    /** Args overload — the trailing string list carries any textual payload an event needs (e.g. a mission's objectiveType/winningFaction/variant/reason). See {@link NetMessageCodec#encodeWorldEvent}. */
    public void broadcastWorldEvent(int eventType, String key, float value, java.util.List<String> args) {
        if (!isServer() || !isNetworked()) return;
        broadcastMessage(NetMessageCodec.encodeWorldEvent(MSG_WORLD_EVENT, eventType, key, value, args), null);
    }

    // ── Spawn/despawn replication (Phase 7 — folds in G3) ─────────────────────
    //
    // Replaces MultiplayerSpawner: the server announces every character it
    // instantiates (scene selector + identity + position + owner) so each client
    // can reconstruct it identically, and sends a one-shot baseline batch to a
    // newly-joined peer covering every already-live character — solving the
    // late-join problem MultiplayerSpawner would have, without its "unsuitable
    // for single-player-as-local-host" awkwardness. Reliable, channel 0 (per
    // channelSpecFor) — a dropped spawn would desync the world permanently.
    //
    // The receiver-side bodies live in handleSpawnMessage/handleDespawnMessage,
    // which delegate to GameManager.spawnReplicatedCharacter/despawnReplicatedCharacter
    // (mirrors the handleIdentifyMessage → onPeerIdentified split).

    /** Server → all: announce a freshly-instantiated character so every client reconstructs it identically. Called wherever the server adds a Character to the "characters" group (GameManager.spawnPlayerBody, DebugHarness's F10/F11 test spawns). */
    public void announceSpawn(Character character) {
        if (!isServer() || character.characterInfo == null) return;
        broadcastMessage(encodeSpawnFor(character), null);
    }

    /** Server → one peer: the late-join baseline — one MSG_SPAWN per already-live character, so a joining/rejoining client doesn't have to wait for the next live spawn to see the existing world. */
    public void sendBaselineSpawns(int targetPeerId) {
        if (!isServer() || getTree() == null) return;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (node instanceof Character c && c.characterInfo != null) sendMessage(targetPeerId, encodeSpawnFor(c));
        }
    }

    /**
     * Server → one peer: late-join baseline of runtime faction-relationship flips (D3). Per-character
     * faction swaps already ride {@link #sendBaselineSpawns} (encodeSpawnFor reads the live faction),
     * so only the global relationship table needs catching up here. Sent as the same MSG_WORLD_EVENT
     * a live flip uses, so the receiver path is identical.
     */
    public void sendBaselineFactionRelationships(int targetPeerId) {
        if (!isServer()) return;
        Node fmNode = getNodeOrNull("/root/FactionManager");
        if (!(fmNode instanceof com.openworld.character.FactionManager fm)) return;
        for (String[] rel : fm.getActiveRelationships()) {
            sendMessage(targetPeerId, NetMessageCodec.encodeWorldEvent(MSG_WORLD_EVENT,
                    com.openworld.game.GameManager.WORLD_EVENT_FACTION_RELATIONSHIP, rel[0], 0f,
                    java.util.List.of(rel[1], rel[2])));
        }
    }

    /**
     * Server → one peer: late-join baseline of already-broken destructibles (I2). Sent as the same
     * MSG_WORLD_EVENT a live break uses, so the receiver path ({@code GameManager.applyBreakableState})
     * is identical. Only broken pieces need sending — a freshly-joined client's geometry starts intact.
     */
    public void sendBaselineBreakables(int targetPeerId) {
        if (!isServer() || getTree() == null) return;
        for (Node node : getTree().getNodesInGroup(
                new StringName(com.openworld.world.Breakable.BREAKABLE_GROUP))) {
            if (node instanceof com.openworld.world.Breakable b && b.isBroken()) {
                sendMessage(targetPeerId, NetMessageCodec.encodeWorldEvent(MSG_WORLD_EVENT,
                        com.openworld.game.GameManager.WORLD_EVENT_BREAKABLE, b.breakableId, 1f,
                        java.util.List.of()));
            }
        }
    }

    /** Server → all: announce a character's removal (queueFree on receipt). */
    public void announceDespawn(String characterId) {
        if (!isServer()) return;
        broadcastMessage(NetMessageCodec.encodeDespawn(MSG_DESPAWN, characterId), null);
    }

    /**
     * Server → all: announce a runtime-streamed ambient-traffic vehicle (I3b) so every client
     * reconstructs it. The vehicle counterpart of {@link #announceSpawn} — used by
     * {@code ZoneManager} when a zone streams traffic in. Despawn rides the generic
     * {@link #announceDespawn} (vehicles share the "characters" id space).
     */
    public void announceVehicleSpawn(com.openworld.carrier.vehicle.Vehicle vehicle) {
        if (!isServer() || vehicle == null || vehicle.getCharacterInfo() == null) return;
        broadcastMessage(encodeVehicleSpawnFor(vehicle), null);
    }

    /** Server → one peer: late-join baseline of EVERY host vehicle — the vehicle counterpart of
     *  {@link #sendBaselineSpawns}. Both runtime-streamed traffic AND scene-authored vehicles are sent:
     *  a connecting client frees its local scene vehicles ({@link #removeLocalPrePlacedVehicles}, like the
     *  pre-placed Player) so the host re-supplies them here carrying the host's UUID. That gives one shared
     *  id per vehicle on every peer — without it a scene car's id diverged and exit requests hit NO_VEHICLE
     *  (PLAN.md netcode WS3). Iterating the "characters" group covers both kinds in one pass. */
    public void sendBaselineVehicleSpawns(int targetPeerId) {
        if (!isServer() || getTree() == null) return;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (node instanceof com.openworld.carrier.vehicle.Vehicle v && v.getCharacterInfo() != null)
                sendMessage(targetPeerId, encodeVehicleSpawnFor(v));
        }
    }

    /** Client-side receiver for MSG_VEHICLE_SPAWN — host-only source (a client can't populate traffic),
     *  mirrors {@link #handleSpawnMessage}. Delegates reconstruction to GameManager. */
    private void handleVehicleSpawnMessage(int senderPeerId, StreamPeerBuffer buf) {
        NetMessageCodec.DecodedVehicleSpawn spawn = NetMessageCodec.decodeVehicleSpawn(buf);
        if (!isValidIdentifier(spawn.vehicleId()) || !isFiniteVector3(spawn.position())) {
            dropInvalid("vehicle_spawn", "MSG_VEHICLE_SPAWN", senderPeerId);
            return;
        }
        if (isServer()) return;
        GameManager manager = gameManager();
        if (manager != null) manager.spawnReplicatedVehicle(spawn);
    }

    private PackedByteArray encodeVehicleSpawnFor(com.openworld.carrier.vehicle.Vehicle vehicle) {
        CharacterInfo info = vehicle.getCharacterInfo();
        // ephemeral = runtime-streamed ambient traffic (in STREAMED_GROUP on the host). Scene-placed /
        // player-driven vehicles are persistent → not reconcile-eligible on the client (WS3 fix).
        boolean ephemeral = vehicle.isInGroup(STREAMED_VEHICLE_GROUP);
        return NetMessageCodec.encodeVehicleSpawn(MSG_VEHICLE_SPAWN, info.characterId, info.faction,
                vehicle.getGlobalPosition(), (float) vehicle.getGlobalRotation().getY(), info.ownerPeerId,
                ephemeral);
    }

    private PackedByteArray encodeSpawnFor(Character character) {
        CharacterInfo info = character.characterInfo;
        int sceneSelector = character instanceof Player ? NetMessageCodec.SCENE_PLAYER : NetMessageCodec.SCENE_AI;
        return NetMessageCodec.encodeSpawn(MSG_SPAWN, info.characterId, info.displayName, info.faction,
                sceneSelector, character.getGlobalPosition(), info.ownerPeerId);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /** Both Character and Vehicle keep their Health as a direct "Health" child. */
    private Health findHealth(Node entity) {
        Node node = entity.getNodeOrNull(new godot.core.NodePath("Health"));
        return node instanceof Health health ? health : null;
    }

    /** Both Character and Vehicle keep their WeaponController as a direct child. */
    private WeaponController findWeaponController(Node entity) {
        Node node = entity.getNodeOrNull(new godot.core.NodePath("WeaponController"));
        return node instanceof WeaponController wc ? wc : null;
    }

    /**
     * Resolves a characterId to any live replicated entity (Character or Vehicle — both join
     * the "characters" group and implement Controllable) — Round 11 N3's entity-generic
     * lookup, so vehicle damage/snapshots/occupancy resolve through the same id space.
     */
    private com.openworld.control.Controllable findControllableById(String characterId) {
        if (getTree() == null) return null;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (node instanceof com.openworld.control.Controllable c && c.getCharacterInfo() != null
                    && characterId.equals(c.getCharacterInfo().characterId)) {
                return c;
            }
        }
        return null;
    }

    /** Resolves a characterId to its live Character via the "characters" group (no path coupling). */
    private Character findCharacterById(String characterId) {
        return findControllableById(characterId) instanceof Character c ? c : null;
    }
}

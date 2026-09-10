package com.openworld.game;

import com.openworld.character.Character;
import com.openworld.control.CharacterController;
import com.openworld.character.CharacterInfo;
import com.openworld.character.Faction;
import com.openworld.net.NetworkController;
import com.openworld.character.Player;
import com.openworld.net.PickupGrantPolicy;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Input;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.PackedScene;
import godot.api.Window;
import godot.core.Callable;
import godot.core.MethodCallable;
import godot.core.StringName;
import godot.core.StringNames;
import godot.core.Vector3;
import godot.global.GD;

import java.util.HashSet;
import java.util.Set;
import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.character.AICharacter;
import com.openworld.character.Health;
import com.openworld.control.Controllable;
import com.openworld.control.PlayerController;
import com.openworld.debug.DebugHarness;
import com.openworld.game.mission.MissionManager;
import com.openworld.item.AmmoRefill;
import com.openworld.item.Pickup;
import com.openworld.net.NetMessageCodec;
import com.openworld.net.NetStats;
import com.openworld.net.NetworkManager;
import com.openworld.net.VehicleSeatPolicy;
import com.openworld.net.session.PersistentPlayerId;
import com.openworld.net.session.PlayerSession;
import com.openworld.ui.HUDManager;
import com.openworld.ui.MenuManager;
import com.openworld.weapon.IconRegistry;
import com.openworld.weapon.WeaponController;
import com.openworld.weapon.WeaponItem;

/**
 * Central game state machine — registered as an AutoLoad singleton named "GameManager".
 *
 * Responsibilities:
 *  - Track current GameState (PLAYING, PAUSED, GAME_OVER)
 *  - Respond to player death (show game-over screen, restart, quit)
 *  - Provide a single entry point for scene transitions
 *
 * AutoLoad entry (add to project.godot after running ./gradlew build):
 *   [autoload]
 *   GameManager="*res://src/main/java/com/openworld/game/GameManager.java"
 *
 * Wire EventBus.playerDied → GameManager.onPlayerDied() in the scene or in _ready().
 */
@Script(className = "GameManager")
public class GameManager extends Node {

    public enum GameState {
        PLAYING,
        PAUSED,
        GAME_OVER
    }

    private GameState currentState = GameState.PLAYING;

    /**
     * characterIds of every live, human-controlled (Player) character. GAME_OVER
     * fires only once this set empties — generalises the old "the player died"
     * single-character rule to co-op, where multiple Players can be present and
     * the session must outlive any individual one of them.
     */
    private final Set<String> alivePlayerCharacterIds = new HashSet<>();

    @Register
    @Override
    public void _ready() {
        // Connect to EventBus once it is available as a sibling AutoLoad.
        // AutoLoads are added in order, so EventBus must be listed first in project.godot.
        Node eventBusNode = getNodeOrNull("/root/EventBus");
        if (eventBusNode instanceof EventBus) {
            EventBus bus = (EventBus) eventBusNode;
            bus.characterSpawned.connectUnsafe(MethodCallable.createUnsafe(this, "onCharacterSpawned"), godot.api.Object.ConnectFlags.DEFAULT);
            bus.characterDied.connectUnsafe(MethodCallable.createUnsafe(this, "onCharacterDied"), godot.api.Object.ConnectFlags.DEFAULT);
            // Note: playerDied is intentionally NOT connected here — it fires per dead
            // body. GAME_OVER is driven by the characterSpawned/characterDied "all dead"
            // tracking below, which emits EventBus.allPlayersDied for MenuManager's UI.
        }

        // Stop all in-flight weapon SFX the instant the OS requests a window close, while every
        // node and the JVM are still alive. At the real quit the godot-kotlin-jvm runtime is torn
        // down BEFORE the final SceneTree node teardown, so a WeaponController's
        // tree_exiting → weaponAudio.stop self-stop never runs for bodies still alive at quit —
        // their in-flight reload/fire AudioStreamPlaybackWAV leaks ("Resource still in use:
        // Rifle_reload.wav" / leaked ObjectDB instances). The root Window's close_requested fires
        // first, while everything is valid, so it is the one reliable place to silence them.
        Window root = (getTree() != null) ? getTree().getRoot() : null;
        if (root != null) {
            root.getCloseRequested().connectUnsafe(
                MethodCallable.createUnsafe(this, "onCloseRequested"),
                godot.api.Object.ConnectFlags.DEFAULT);
        }
    }

    /**
     * Window close handler (see _ready). Stops EVERY audio player in the tree before the quit tears
     * it down. At app exit the godot-kotlin-jvm runtime is cleaned before the final node teardown, so
     * a JVM-registered tree_exiting/_exitTree stop never releases an in-flight playback — any
     * AudioStreamPlayer{,2D,3D} still playing at quit leaks its AudioStreamPlaybackWAV ("Resource
     * still in use" / leaked ObjectDB). This is a single generic sweep on purpose: it is NOT
     * weapon-specific, so future audio (footsteps, engines, ambient, UI) is covered with no per-entity
     * wiring. (Mid-session frees — zone unload, despawn — are a separate concern handled by each audio
     * node self-stopping on its own tree_exiting; see WeaponController and the CLAUDE.md audio quirk.)
     */
    @Register
    public void onCloseRequested() {
        prepareForQuit();
    }

    /**
     * The actual audio-stop sweep (see {@link #onCloseRequested}'s javadoc for why it exists) —
     * factored out so any quit path can call it, not just the OS window-close button. {@code
     * SceneTree.quit()} — the path every in-game Quit button (PauseMenu/MenuManager/GameOverMenu)
     * uses — does NOT emit {@code Window.close_requested} (that signal is specifically the
     * OS/window-manager close request, e.g. the title-bar X or Alt+F4); calling only
     * {@code getTree().quit()} therefore skipped this sweep entirely for the menu-driven quit —
     * the far more common way a player actually exits — leaking every still-playing
     * {@code AudioStreamPlaybackWAV} exactly like the close-requested fix was written to prevent,
     * just on a different path (found while investigating a reported hang-then-OS-kill on
     * ESC-menu quit from a large multi-district scene, where many more AI/vehicles' weapon/engine
     * audio would be live simultaneously than in any single-district test — road_blender_godot.md
     * "hang and eventually close by the system as crash"). Call this BEFORE {@code
     * getTree().quit()} from any quit button; a no-op if called with no scene tree.
     */
    public void prepareForQuit() {
        if (getTree() == null) return;
        stopAllAudio(getTree().getRoot());
    }

    /** Depth-first stop() on every AudioStreamPlayer/2D/3D under {@code node} (inclusive). */
    private void stopAllAudio(Node node) {
        if (node == null) return;
        if (node instanceof godot.api.AudioStreamPlayer p) p.stop();
        else if (node instanceof godot.api.AudioStreamPlayer2D p) p.stop();
        else if (node instanceof godot.api.AudioStreamPlayer3D p) p.stop();
        for (Node child : node.getChildren()) stopAllAudio(child);
    }

    /**
     * Release process-global Godot references on shutdown. {@link IconRegistry} caches feed
     * icons (Texture2D) in a static map for the whole JVM lifetime; clearing it here, as the
     * AutoLoad leaves the tree at engine teardown, drops those references before Godot tears
     * down its resource table — otherwise the still-referenced texture is reported as
     * "1 resource still in use at exit" (leaked ObjectDB instance).
     */
    @Register
    @Override
    public void _exitTree() {
        IconRegistry.clear();
        WaypointStore.clearAll();   // I5 — hygiene + clean restart (Vector3 values, but clear anyway)
    }

    // ── State transitions ─────────────────────────────────────────────────────

    /** Track every spawned human-controlled (Player) character for the "all dead" check. */
    @Register
    public void onCharacterSpawned(Node node, CharacterInfo info) {
        if (node instanceof Player && info != null && !info.characterId.isEmpty()) {
            alivePlayerCharacterIds.add(info.characterId);
        }
    }

    /** GAME_OVER fires once every tracked Player character has died — not on the first. */
    @Register
    public void onCharacterDied(CharacterInfo info) {
        if (info == null) return;
        if (alivePlayerCharacterIds.remove(info.characterId) && alivePlayerCharacterIds.isEmpty()) {
            if (currentState != GameState.PLAYING) return;
            transitionTo(GameState.GAME_OVER);
            // Surface the session-ending game-over screen ONLY now that every player is down.
            // MenuManager listens to allPlayersDied (not the per-body playerDied) so a single
            // co-op teammate's death no longer kicks the whole session — including the host,
            // which holds a Player puppet for the client — to the restart menu.
            Node busNode = getNodeOrNull("/root/EventBus");
            if (busNode instanceof EventBus bus) bus.allPlayersDied.emit();
        }
    }

    public void pauseGame() {
        if (currentState != GameState.PLAYING) return;
        transitionTo(GameState.PAUSED);
        if (getTree() != null) getTree().setPause(true);
    }

    public void resumeGame() {
        if (currentState != GameState.PAUSED) return;
        transitionTo(GameState.PLAYING);
        if (getTree() != null) getTree().setPause(false);
    }

    public void restartLevel() {
        if (getTree() != null) getTree().setPause(false);
        Input.INSTANCE.setMouseMode(Input.MouseMode.CAPTURED);
        transitionTo(GameState.PLAYING);
        // Faction relationships live on the FactionManager AutoLoad, which survives the scene
        // reload below — so a mid-game betrayal would carry into the restarted game. Reset to the
        // shipped DefaultFactions.tres so a restart is a clean slate.
        com.openworld.character.FactionManager factions = getFactionManager();
        if (factions != null) factions.reset();
        // End any live network session before reloading: NetworkManager is an AutoLoad and
        // survives reloadCurrentScene, so without this a "restart" would carry a stale ENet
        // connection into the fresh scene. After leaveSession the reload comes up single-player.
        NetworkManager net = getNetworkManager();
        if (net != null) net.leaveSession();
        if (getTree() != null) getTree().reloadCurrentScene();
    }

    public void loadLevel(String scenePath) {
        transitionTo(GameState.PLAYING);
        if (getTree() != null) getTree().changeSceneToFile(scenePath);
    }

    // ── Getters ───────────────────────────────────────────────────────────────

    public GameState getCurrentState() {
        return currentState;
    }

    public boolean isPlaying() {
        return currentState == GameState.PLAYING;
    }

    // ── Bot-fill / L4D controller swap (Phase 4, Step 5) ─────────────────────

    /**
     * Called when the owning player disconnects.
     * Replaces whatever controller is currently driving the body (ServerProxyController
     * when networked, PlayerController in single-player) with a CharacterController
     * (AI bot) so the game continues without a human driver. Goes through
     * attachController — not a name-keyed lookup — so Character's cached `controller`
     * field stays correct regardless of which controller type is actually attached.
     */
    public void onPlayerLeft(Player body) {
        body.attachController(new CharacterController());
        GD.print("GameManager: player left — bot attached to " + body.getName());
    }

    /**
     * Called when a player reconnects or takes control of a body.
     * Removes the AI CharacterController and reattaches a PlayerController so
     * the human drives the body again.
     */
    public void onPlayerJoined(Player body) {
        // Ownership-based authority: the remote client OWNS its own body and simulates it
        // locally, reporting its state upstream. On the host this body is a puppet driven by
        // those reports — a NetworkController (interpolated from MSG_SNAPSHOT), exactly like
        // every other non-owned body. The host never re-simulates it, which is what removes
        // the dual-simulation teleporting. (Health stays host-authoritative — see
        // NetworkController.receiveSnapshot's applyHealth gate.)
        body.attachController(new NetworkController());
        GD.print("GameManager: player joined — NetworkController (puppet) attached to " + body.getName());
    }

    // ── Network join/rejoin (Part G — Step 6) ────────────────────────────────
    //
    // peerConnected/peerDisconnected fire too early to act on — peerId is reassigned
    // by ENet on every (re)connection, so it can't key session state across disconnects.
    // The real join/rejoin decision happens in onPeerIdentified, once the client reports
    // its PersistentPlayerId via NetworkManager.identifyPeer. That id *becomes* the
    // body's CharacterInfo.characterId on first join — which is what makes "matching
    // characterId-owner" on rejoin meaningful: the same human always owns the same
    // characterId across relaunches, so PlayerSession.characterId doubles as the
    // rejoin key without adding a redundant field.

    private static final String PLAYER_SCENE_PATH = "res://src/main/resources/com/openworld/character/Player.tscn";
    private static final String AI_SCENE_PATH = "res://src/main/resources/com/openworld/character/AICharacter.tscn";
    private static final String VEHICLE_SCENE_PATH = "res://src/main/resources/com/openworld/vehicle/Vehicle.tscn";
    private static final StringName CHARACTERS_GROUP = new StringName("characters");

    /** Server-side: a peer connected at the transport level. Real handling waits for identifyPeer. */
    public void onPeerConnected(int peerId) {
        GD.print("GameManager: peer connected — " + peerId);
    }

    /** Server-side: a peer disconnected — mark its session offline and bot-fill its body. */
    public void onPeerDisconnected(int peerId) {
        GD.print("GameManager: peer disconnected — " + peerId);
        MissionManager missions = getMissionManager();
        if (missions == null) return;

        PlayerSession session = missions.getSession(peerId);
        if (session == null) return;
        session.isConnected = false;

        Player body = findPlayerByCharacterId(session.characterId);
        if (body != null) onPlayerLeft(body);
    }

    /**
     * Server-side: a client reported its stable PersistentPlayerId. Either restores
     * control of an existing (disconnected) body — rejoin — or registers a fresh
     * session and spawns a new one — first join.
     */
    public void onPeerIdentified(int peerId, String persistentPlayerId) {
        MissionManager missions = getMissionManager();
        if (missions == null) return;

        NetworkManager net = getNetworkManager();

        PlayerSession session = missions.findDisconnectedSessionByCharacterId(persistentPlayerId);
        if (session != null) {
            int previousPeerId = session.peerId;
            session.peerId = peerId;
            session.isConnected = true;
            missions.removeSession(previousPeerId);
            missions.registerSession(session);

            Player body = findPlayerByCharacterId(persistentPlayerId);
            if (body != null) {
                if (body.characterInfo != null) body.characterInfo.ownerPeerId = peerId;
                onPlayerJoined(body);
                GD.print("GameManager: peer " + peerId + " rejoined as " + persistentPlayerId);
            }
            // Baseline batch covers the rejoining body too (it's already in "characters") —
            // the rejoining client otherwise has no local copy of the world to resume into.
            // Pickups after spawns: the holder bodies must exist before their pickup events.
            if (net != null) {
                net.sendBaselineSpawns(peerId);
                net.sendBaselineVehicleSpawns(peerId);   // streamed traffic bodies before their occupancy (I3b)
                net.sendBaselinePickups(peerId);
                // Inventories last: bodies exist and pickup events have applied, so the
                // manifests only have to cover what events never carried (Round 11 N2).
                net.sendBaselineInventories(peerId);
                // Occupied vehicles after spawns — the seated body must exist first (N3).
                net.sendBaselineVehicleOccupancy(peerId);
                // Runtime faction-relationship flips (per-character factions ride sendBaselineSpawns).
                net.sendBaselineFactionRelationships(peerId);
                net.sendBaselineBreakables(peerId);   // already-broken destructibles (I2)
            }
            return;
        }

        missions.registerSession(new PlayerSession(peerId, persistentPlayerId, Faction.PLAYER));
        // Baseline first — it can never include the about-to-be-created body (not yet in
        // "characters"), so spawnPlayerBody's own announceSpawn is what tells the new
        // peer about itself, with no risk of a duplicate baseline entry. Pickups after
        // spawns: the holder bodies must exist on the joiner before their pickup events.
        if (net != null) {
            net.sendBaselineSpawns(peerId);
            net.sendBaselineVehicleSpawns(peerId);   // streamed traffic bodies before their occupancy (I3b)
            net.sendBaselinePickups(peerId);
            net.sendBaselineInventories(peerId);   // after spawns + pickups — see rejoin branch
            net.sendBaselineVehicleOccupancy(peerId);   // occupied vehicles after spawns (N3)
            net.sendBaselineFactionRelationships(peerId);   // runtime relationship flips (D3)
            net.sendBaselineBreakables(peerId);   // already-broken destructibles (I2)
        }
        spawnPlayerBody(peerId, persistentPlayerId);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private MissionManager getMissionManager() {
        Node node = getNodeOrNull("/root/MissionManager");
        return node instanceof MissionManager manager ? manager : null;
    }

    private NetworkManager getNetworkManager() {
        Node node = getNodeOrNull("/root/NetworkManager");
        return node instanceof NetworkManager net ? net : null;
    }

    private com.openworld.character.FactionManager getFactionManager() {
        Node node = getNodeOrNull("/root/FactionManager");
        return node instanceof com.openworld.character.FactionManager fm ? fm : null;
    }

    private Node resolveCharactersContainer() {
        if (getTree() == null) return null;
        Node scene = getTree().getCurrentScene();
        return scene != null ? scene.getNodeOrNull("Characters") : null;
    }

    /**
     * Looks up an optional {@code PlayerSpawn} {@link Node3D} at the scene root — the convention a
     * host scene (e.g. {@code WorldMaster.tscn}) uses to mark where its populated area actually is.
     * Returns null when absent (legacy test scenes like {@code World.tscn}/{@code SoloPiece.tscn}
     * carry no such node), so callers must have an origin-relative fallback.
     */
    private Node3D resolveSpawnAnchor() {
        if (getTree() == null) return null;
        Node scene = getTree().getCurrentScene();
        Node marker = scene != null ? scene.getNodeOrNull("PlayerSpawn") : null;
        return marker instanceof Node3D anchor ? anchor : null;
    }

    /**
     * A joining/rejoining player's spawn position: jittered around the scene's {@code PlayerSpawn}
     * marker when present, else the legacy origin-relative box (mirrors
     * {@code DebugHarness.spawnTestAI}'s exact range) so scenes without the marker convention
     * (world-scale test scenes predating it) keep behaving exactly as before.
     */
    private Vector3 jitteredSpawnPosition() {
        Node3D anchor = resolveSpawnAnchor();
        if (anchor != null) {
            Vector3 pos = anchor.getGlobalPosition();
            return new Vector3(
                    (float) pos.getX() + GD.randfRange(-4.0f, 4.0f),
                    (float) pos.getY(),
                    (float) pos.getZ() + GD.randfRange(-4.0f, 4.0f));
        }
        return new Vector3(
                GD.randfRange(-12.0f, 18.0f),
                0.9f,
                GD.randfRange(-12.0f, 8.0f));
    }

    private Character findCharacterById(String characterId) {
        if (getTree() == null) return null;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (node instanceof Character c && c.characterInfo != null
                    && characterId.equals(c.characterInfo.characterId)) {
                return c;
            }
        }
        return null;
    }

    private Player findPlayerByCharacterId(String characterId) {
        return findCharacterById(characterId) instanceof Player p ? p : null;
    }

    /** Resolves a pickupId to its live Pickup via the "pickups" group — mirrors findCharacterById. */
    private com.openworld.item.Pickup findPickupById(String pickupId) {
        if (getTree() == null) return null;
        for (Node node : getTree().getNodesInGroup(new StringName(com.openworld.item.Pickup.PICKUPS_GROUP))) {
            if (node instanceof com.openworld.item.Pickup p && pickupId.equals(p.pickupId)) return p;
        }
        return null;
    }

    // ── Host-arbitrated pickups (Phase D — see NETWORK_REWRITE_PLAN.md) ───────

    /**
     * Generous grant radius: the host validates a request against its interpolated (50-100 ms
     * stale) puppet position, so this only rejects requests for items nowhere near the body —
     * a close race is decided by arrival order (first REQUEST wins via ALREADY_TAKEN).
     */
    private static final double PICKUP_GRANT_TOLERANCE_METERS = 4.0;

    /**
     * Host-side arbitration for a client's MSG_PICKUP_REQUEST: evaluate via the unit-tested
     * {@link PickupGrantPolicy}, and on GRANT collect on the host's copy (the requester's puppet
     * equips it) then broadcast MSG_PICKUP_TAKEN to every client — including the requester, whose
     * own collect happens only on that echo (it never collects optimistically).
     */
    public void processPickupRequest(int senderPeerId, String pickupId, String characterId) {
        com.openworld.item.Pickup pickup = findPickupById(pickupId);
        Character character = findCharacterById(characterId);
        boolean senderOwns = character != null && character.characterInfo != null
                && character.characterInfo.ownerPeerId == senderPeerId;
        double distance = (pickup != null && character != null)
                ? pickup.getGlobalPosition().distanceTo(character.getGlobalPosition())
                : Double.NaN;

        PickupGrantPolicy.Verdict verdict = PickupGrantPolicy.evaluate(
                pickup != null, pickup != null && pickup.isTaken(),
                character != null, senderOwns, distance, PICKUP_GRANT_TOLERANCE_METERS);
        if (verdict != PickupGrantPolicy.Verdict.GRANT) {
            GD.print("GameManager: denied pickup request from peer " + senderPeerId
                    + " for '" + pickupId + "' — " + verdict
                    + (pickup != null
                            ? " [taken=" + pickup.isTaken() + " at " + pickup.getPath().getPath()
                                    + " distance=" + distance + "]"
                            : " [no pickup with that id in 'pickups' group]"));
            return;
        }
        GD.print("GameManager: granted pickup '" + pickupId + "' to " + characterId
                + " (peer " + senderPeerId + ", distance=" + distance + ")");

        // Capture the item state BEFORE collecting (a throwable merge can free the node and
        // its magazine field is consumed into the stack). Resolve the collect FIRST — now
        // synchronous — then broadcast only if the host actually consumed it. Broadcasting
        // before resolution was the divergence: if the equip bounced the item back to the
        // world (slot-displacement guard), every client still deleted it from their world.
        int magazine = pickup.getReplicatedMagazine();
        int reserve = pickup.getReplicatedReserve();
        pickup.applyReplicatedPickup(character, magazine, reserve);
        NetworkManager net = getNetworkManager();
        if (net != null && pickup.isTaken()) {
            net.broadcastPickupTaken(pickupId, characterId, magazine, reserve);
        }
    }

    /** Client-side MSG_PICKUP_TAKEN apply — mirror the collect on this peer's copies. Idempotent. */
    public void applyReplicatedPickup(String pickupId, String characterId, int magazine, int reserve) {
        com.openworld.item.Pickup pickup = findPickupById(pickupId);
        Character character = findCharacterById(characterId);
        if (pickup == null || character == null) {
            GD.print("GameManager: PICKUP_TAKEN apply failed for '" + pickupId + "' → " + characterId
                    + (pickup == null ? " [pickup not found by id]" : "")
                    + (character == null ? " [character not found by id]" : ""));
            return;
        }
        if (pickup.isTaken()) {
            GD.print("GameManager: PICKUP_TAKEN no-op for '" + pickupId + "' — already taken locally");
            return;
        }
        pickup.applyReplicatedPickup(character, magazine, reserve);
    }

    /**
     * MSG_WEAPON_DROPPED apply (Phase E): take the weapon out of the character's slot and return
     * it to the world at the originator's position/impulse, adopting the event's newPickupId so
     * item identities converge across peers. When the slot is already empty — the displacement
     * race, where this peer's own replicated equip self-dropped the item an instant earlier —
     * fall back to the world item under its pre-drop id and converge it in place.
     */
    public void applyReplicatedDrop(String characterId, int slot, String oldPickupId, String newPickupId,
            Vector3 position, Vector3 impulse, int magazine, int reserve) {
        Character character = findCharacterById(characterId);
        if (character == null) return;
        Node wcNode = character.getNodeOrNull("WeaponController");
        if (wcNode instanceof com.openworld.weapon.WeaponController wc
                && wc.applyReplicatedDrop(slot, newPickupId, position, impulse, magazine, reserve)) {
            return;
        }
        if (findPickupById(oldPickupId) instanceof com.openworld.weapon.WeaponItem world && !world.isTaken()) {
            world.pickupId = newPickupId;
            world.setMagazine(magazine);
            world.setReserve(reserve);
            // null parent: this item is already lying in the world under its own PickupBody —
            // converge it in place rather than re-homing it to the scene root.
            world.placeInWorld(null, position, impulse);
        }
    }

    // ── Host-arbitrated vehicle seats (Round 11 N3) ───────────────────────────

    // Deferred vehicle occupancy (PLAN.md netcode WS2): a MSG_VEHICLE_OCCUPANCY can arrive before the
    // vehicle and/or occupant body has spawned on this peer (cross-ordering with the spawn/baseline
    // stream — worse now that streamed cars lazy-spawn from snapshots). The latest desired seat per
    // vehicle is parked here and re-applied the instant both bodies exist (flushed from
    // spawnReplicatedVehicle / spawnReplicatedCharacter), so a driver never lingers pinned-but-unseated
    // in a standing pose waiting on the ~1 Hz occupancy sweep.
    /** Keyed "vehicleId#seatIndex" — one deferred entry per seat, so a car can wait on several occupants. */
    private final java.util.Map<String, PendingSeat> pendingSeats = new java.util.HashMap<>();
    private record PendingSeat(String vehicleId, String occupantCharacterId, int ownerPeerId,
            boolean entering, int seatIndex) {}

    /** Resolves a vehicleId to its live Vehicle via the "characters" group — mirrors findCharacterById. */
    private com.openworld.carrier.vehicle.Vehicle findVehicleById(String vehicleId) {
        if (getTree() == null) return null;
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (node instanceof com.openworld.carrier.vehicle.Vehicle v && v.getCharacterInfo() != null
                    && vehicleId.equals(v.getCharacterInfo().characterId)) {
                return v;
            }
        }
        return null;
    }

    /**
     * Host-side arbitration for a MSG_VEHICLE_SEAT_REQUEST (and the host's own enter/exit,
     * which Vehicle.requestEnter/requestExit route here with senderPeerId = SERVER_PEER_ID):
     * evaluate via the unit-tested {@link com.openworld.net.VehicleSeatPolicy}, and on GRANT seat
     * the character on the host's copy and broadcast MSG_VEHICLE_OCCUPANCY to every client —
     * including the requester, whose own enter/exit happens only on that echo (mirrors the
     * pickup grant: no optimistic local enter to roll back).
     */
    public void processVehicleSeatRequest(int senderPeerId, String vehicleId, String characterId,
            boolean entering, int requestedSeat) {
        com.openworld.carrier.vehicle.Vehicle vehicle = findVehicleById(vehicleId);
        Character character = findCharacterById(characterId);
        boolean senderOwns = character != null && character.characterInfo != null
                && character.characterInfo.ownerPeerId == senderPeerId;

        // PLAN.md I3c — carjack: a player asking to enter a car driven by an AI evicts that AI driver
        // first (host-authoritative), reacts it (flee/fight), and drops the lane-follow brain so the
        // seat is now empty and player-drivable; the normal enter policy below then grants it.
        if (entering && vehicle != null && vehicle.isAiOccupied()
                && character instanceof Player && senderOwns) {
            Character ejected = vehicle.getOccupant();
            forceVehicleExitSeat(vehicle, 0);
            vehicle.removeAiDriverBrain();
            if (ejected instanceof AICharacter ai) ai.reactToCarjack(character);
        }

        // Multi-seat: resolve the concrete seat host-side. Enter picks via the pure policy
        // (SEAT_AUTO scans driver-first); exit resolves whichever seat the character holds.
        int seat;
        com.openworld.net.VehicleSeatPolicy.Verdict verdict;
        if (entering) {
            seat = vehicle != null
                    ? com.openworld.net.VehicleSeatPolicy.pickSeat(vehicle.buildSeatOccupancy(), requestedSeat)
                    : -1;
            double distance = (vehicle != null && character != null)
                    ? vehicle.getGlobalPosition().distanceTo(character.getGlobalPosition())
                    : Double.NaN;
            verdict = com.openworld.net.VehicleSeatPolicy.evaluateEnter(
                    vehicle != null, vehicle != null && vehicle.isAlive(),
                    vehicle != null && seat < 0,
                    character != null, character != null && character.isAlive(), senderOwns,
                    distance, com.openworld.net.VehicleSeatPolicy.ENTER_TOLERANCE_METERS);
        } else {
            seat = (vehicle != null && character != null) ? vehicle.findSeatOf(character) : -1;
            verdict = com.openworld.net.VehicleSeatPolicy.evaluateExit(
                    vehicle != null, seat >= 0, senderOwns);
        }
        if (verdict != com.openworld.net.VehicleSeatPolicy.Verdict.GRANT) {
            GD.print("GameManager: denied vehicle " + (entering ? "enter" : "exit") + " request from peer "
                    + senderPeerId + " for '" + vehicleId + "' — " + verdict);
            return;
        }
        GD.print("GameManager: granted vehicle " + (entering ? "enter" : "exit") + " '" + vehicleId
                + "' ↔ " + characterId + " seat " + seat + " (peer " + senderPeerId + ")");

        // Ownership migrates ONLY with the driver seat (seat 0) — passengers never own the
        // vehicle's locomotion. Ordering unchanged: ownership BEFORE the seat change, so the
        // hot-swapped controller's isAuthority() reads the new owner the moment it lands.
        // applyAuthorityState ordering: BEFORE tryEnter (clear the puppet controller —
        // seeding velocities — ahead of the live hot-swap) but AFTER tryExit (the live
        // controller must leave the vehicle before a puppet controller can take over).
        CharacterInfo info = vehicle.getCharacterInfo();
        if (seat == 0) {
            info.ownerPeerId = entering ? senderPeerId : NetworkManager.SERVER_PEER_ID;
        }
        if (entering) {
            if (seat == 0) vehicle.applyAuthorityState();
            vehicle.tryEnter(character, seat);
        } else {
            vehicle.tryExit(seat);
            if (seat == 0) vehicle.applyAuthorityState();
        }

        NetworkManager net = getNetworkManager();
        if (net != null && net.isNetworked()) {
            net.broadcastVehicleOccupancy(info.characterId,
                    entering ? characterId : "", info.ownerPeerId, entering, seat);
        }
    }

    /**
     * Host-side FORCED unseat — destruction/cleanup path. Deliberately bypasses
     * VehicleSeatPolicy: the policy's owner check exists to stop one peer unseating another
     * peer's driver, but a host-initiated eviction (vehicle about to be freed) is always
     * legitimate — routing it through processVehicleSeatRequest with SERVER_PEER_ID was
     * denied NOT_OWNER for client drivers, the exit never broadcast, and the driving
     * client's controller was freed inside the despawned vehicle (stuck body, then crash).
     */
    public void forceVehicleExit(com.openworld.carrier.vehicle.Vehicle vehicle) {
        if (vehicle == null || vehicle.getCharacterInfo() == null) return;
        for (int seat = vehicle.getSeatCount() - 1; seat >= 0; seat--) {   // passengers first, driver last
            if (vehicle.getSeatOccupant(seat) != null) forceVehicleExitSeat(vehicle, seat);
        }
    }

    /** Single-seat forced unseat — see {@link #forceVehicleExit}; ownership resets only with seat 0. */
    private void forceVehicleExitSeat(com.openworld.carrier.vehicle.Vehicle vehicle, int seat) {
        if (vehicle == null || vehicle.getSeatOccupant(seat) == null || vehicle.getCharacterInfo() == null) return;
        CharacterInfo info = vehicle.getCharacterInfo();
        if (seat == 0) info.ownerPeerId = NetworkManager.SERVER_PEER_ID;
        vehicle.tryExit(seat);
        if (seat == 0) vehicle.applyAuthorityState();
        NetworkManager net = getNetworkManager();
        if (net != null && net.isNetworked() && net.isServer()) {
            net.broadcastVehicleOccupancy(info.characterId, "", info.ownerPeerId, false, seat);
        }
    }

    /**
     * Host/SP: seat a freshly-spawned AI driver into a streamed traffic car (PLAN.md I3c). The car
     * keeps its own {@link com.openworld.ai.vehicle.VehicleAIController} (Design B) — {@code tryEnter}'s
     * guard leaves it driving and seats the AI as a visible, inert (drive-state physics-off) passenger.
     * The seat replicates to clients via the ~1 Hz occupancy sweep + late-join baseline; we also fire one
     * immediate occupancy broadcast so the driver appears promptly. Streamed traffic is host-owned, so
     * the vehicle's {@code ownerPeerId} is already the server.
     */
    public void seatTrafficDriver(Vehicle vehicle, AICharacter driver) {
        if (vehicle == null || driver == null || vehicle.getOccupant() != null) return;
        vehicle.tryEnter(driver);
        NetworkManager net = getNetworkManager();
        if (net != null && net.isNetworked() && net.isServer()
                && vehicle.getCharacterInfo() != null && driver.characterInfo != null) {
            net.broadcastVehicleOccupancy(vehicle.getCharacterInfo().characterId,
                    driver.characterInfo.characterId, vehicle.getCharacterInfo().ownerPeerId, true, 0);
        }
    }

    /**
     * Client-side MSG_VEHICLE_OCCUPANCY apply — every peer (including the requester) runs the
     * seat change locally from the host's authoritative event. Idempotent on purpose: the ~1 Hz
     * occupancy sweep re-sends current state as a self-heal backstop, so re-applying what this
     * peer already shows must be a no-op (plus ownership convergence, which is just a field write).
     */
    public void applyVehicleOccupancy(String vehicleId, String occupantCharacterId, int ownerPeerId,
            boolean entering, int seatIndex) {
        com.openworld.carrier.vehicle.Vehicle vehicle = findVehicleById(vehicleId);
        Character character = entering ? findCharacterById(occupantCharacterId) : null;

        // Defer until BOTH bodies exist on this peer (WS2). Park the latest desired seat and re-apply
        // from spawnReplicatedVehicle / spawnReplicatedCharacter — deterministic, not the ~1 Hz sweep.
        // Converge ownership eagerly if the vehicle is already present so its authority/freeze is right
        // even before the occupant arrives. Keyed per seat — a car can wait on several occupants.
        if (vehicle == null || vehicle.getCharacterInfo() == null || (entering && character == null)) {
            if (vehicle != null && vehicle.getCharacterInfo() != null) {
                vehicle.getCharacterInfo().ownerPeerId = ownerPeerId;
                vehicle.applyAuthorityState();
            }
            pendingSeats.put(vehicleId + "#" + seatIndex,
                    new PendingSeat(vehicleId, occupantCharacterId, ownerPeerId, entering, seatIndex));
            GD.print("GameManager: VEHICLE_OCCUPANCY deferred for '" + vehicleId + "' seat " + seatIndex
                    + " — waiting on " + (vehicle == null ? "vehicle" : "occupant " + occupantCharacterId)
                    + " spawn");
            return;
        }
        pendingSeats.remove(vehicleId + "#" + seatIndex);   // a resolved apply supersedes any parked request

        int seat = Math.min(Math.max(seatIndex, 0), Math.max(0, vehicle.getSeatCount() - 1));
        vehicle.getCharacterInfo().ownerPeerId = ownerPeerId;
        if (entering) {
            if (vehicle.getSeatOccupant(seat) != character) {
                if (vehicle.getSeatOccupant(seat) != null) vehicle.tryExit(seat);   // diverged seat — self-heal
                // Authority first (see processVehicleSeatRequest): on the requester this frees
                // the puppet controller — seeding coast velocities — before the live hot-swap.
                if (seat == 0) vehicle.applyAuthorityState();
                vehicle.tryEnter(character, seat);
            } else if (seat == 0) {
                vehicle.applyAuthorityState();   // already seated — ownership converge only
            }
        } else {
            if (vehicle.getSeatOccupant(seat) != null) vehicle.tryExit(seat);
            if (seat == 0) vehicle.applyAuthorityState();
        }
    }

    /**
     * Re-apply parked occupancy whose bodies have now spawned (WS2). Called after any replicated
     * vehicle/character spawn. Iterates a copy — applyVehicleOccupancy mutates pendingSeats (removes on
     * a resolved apply, re-parks while still unresolved).
     */
    private void retryPendingSeats() {
        if (pendingSeats.isEmpty()) return;
        for (java.util.Map.Entry<String, PendingSeat> e :
                new java.util.ArrayList<>(pendingSeats.entrySet())) {
            PendingSeat seat = e.getValue();
            applyVehicleOccupancy(seat.vehicleId(), seat.occupantCharacterId(), seat.ownerPeerId(),
                    seat.entering(), seat.seatIndex());
        }
    }

    /** Instantiates a fresh Player body owned by {@code peerId}, stamped with the rejoinable characterId, and announces it to every connected peer. */
    private void spawnPlayerBody(int peerId, String characterId) {
        Node container = resolveCharactersContainer();
        if (container == null) {
            GD.print("GameManager: Characters container not found — cannot spawn player body");
            return;
        }

        Object loaded = GD.load(PLAYER_SCENE_PATH);
        if (!(loaded instanceof PackedScene playerScene)) {
            GD.print("GameManager: failed to load " + PLAYER_SCENE_PATH);
            return;
        }

        Node instance = playerScene.instantiate();
        if (!(instance instanceof Player player)) {
            instance.queueFree();
            return;
        }

        CharacterInfo info = new CharacterInfo();
        info.characterId = characterId;
        info.displayName = "Player " + peerId;
        info.faction = Faction.PLAYER;
        info.ownerPeerId = peerId;
        player.characterInfo = info;

        container.addChild(player);
        // Player.tscn ships with a live PlayerController — correct for the human at this
        // machine, wrong for a body spawned on behalf of a remote peer. Under ownership-based
        // authority the owning client simulates its own body and reports state upstream; on the
        // host this body is a NetworkController puppet driven by those reports (interpolated),
        // never re-simulated here. Health stays host-authoritative (NetworkController.receiveSnapshot
        // applyHealth gate). The owning client itself gets a live PlayerController via
        // spawnReplicatedCharacter's isAuthorityFor branch.
        player.attachController(new NetworkController());
        // Player.tscn carries no transform of its own (defaults to the scene origin), and
        // spawning exactly on top of another body there stacks the two CharacterBody3Ds —
        // physics shoves the new one to an arbitrary overlap-resolution position (observed:
        // airborne / inside the vehicle). Jitter around a real anchor so joining players land
        // somewhere sensible and distinct instead of on top of each other.
        Vector3 jitteredSpawn = jitteredSpawnPosition();
        player.setGlobalPosition(jitteredSpawn);
        GD.print("GameManager: spawned new body for peer " + peerId + " (characterId=" + characterId + ")");

        NetworkManager net = getNetworkManager();
        if (net != null) net.announceSpawn(player);
    }

    /**
     * Client-side: instantiate a server-announced character — MSG_SPAWN's receiving
     * side (NetworkManager.handleSpawnMessage). Mirrors spawnPlayerBody's load/
     * instantiate/stamp/addChild shape, generic over the scene selector so it covers
     * both remote players and server-spawned AI (folds in the previously-approved G3
     * MultiplayerSpawner follow-up — see NETWORK_REWRITE_PLAN.md Phase 7).
     */
    public void spawnReplicatedCharacter(NetMessageCodec.DecodedSpawn spawn) {
        if (findCharacterById(spawn.characterId()) != null) return;

        Node container = resolveCharactersContainer();
        if (container == null) {
            GD.print("GameManager: Characters container not found — cannot spawn replicated character " + spawn.characterId());
            return;
        }

        String scenePath = spawn.sceneSelector() == NetMessageCodec.SCENE_PLAYER ? PLAYER_SCENE_PATH : AI_SCENE_PATH;
        Object loaded = GD.load(scenePath);
        if (!(loaded instanceof PackedScene scene)) {
            GD.print("GameManager: failed to load " + scenePath);
            return;
        }

        Node instance = scene.instantiate();
        if (!(instance instanceof Character character)) {
            instance.queueFree();
            return;
        }

        CharacterInfo info = new CharacterInfo();
        info.characterId = spawn.characterId();
        info.displayName = spawn.displayName();
        info.faction = spawn.faction();
        info.ownerPeerId = spawn.ownerPeerId();
        character.characterInfo = info;

        // addChild BEFORE attachController — mirrors spawnPlayerBody. Character._ready()
        // (which fires on addChild, never during instantiate()) is what populates the
        // cached `controller` field by scanning getChildren(); calling attachController
        // first leaves `controller` null, so its `removeChild(controller)` guard never
        // fires (the scene's original controller stays attached) and the subsequent
        // _ready() re-scan then overwrites `controller` back to that original — leaving
        // the real NetworkController orphaned and uncached. Adding the child first lets
        // attachController's swap-and-cache do its job correctly.
        container.addChild(character);
        character.setGlobalPosition(spawn.position());

        // The instantiated scene ships with a locally-driving controller
        // (PlayerController/CharacterController) — correct only for bodies *we*
        // drive (ownerPeerId == our localPeerId, e.g. the body the server just
        // spawned for us). Every other replicated body must be swapped to
        // NetworkController so it is driven solely by MSG_SNAPSHOT instead of
        // fighting a live local controller — see NetworkController's class doc.
        NetworkManager net = getNetworkManager();
        if (net == null || !net.isAuthorityFor(info)) {
            character.attachController(new NetworkController());
        }
        GD.print("GameManager: spawned replicated character " + spawn.characterId()
                + " (scene selector " + spawn.sceneSelector() + ")");
        retryPendingSeats();   // this body may be a driver a deferred occupancy was waiting on (WS2)
    }

    /**
     * Client-side: instantiate a host-announced streamed traffic vehicle (I3b) — MSG_VEHICLE_SPAWN's
     * receiving side. Mirrors {@link #spawnReplicatedCharacter}: the scene is the single bounded
     * Vehicle.tscn (no wire path). We are never authority for it (ownerPeerId = host), so
     * {@code Vehicle.applyAuthorityState} attaches a {@code VehicleNetworkController} and freezes the
     * body; the existing MSG_VEHICLE_SNAPSHOT_BATCH then drives it. Tagged into {@code STREAMED_GROUP}
     * so a later late-join baseline / despawn treats it like the host's.
     */
    public void spawnReplicatedVehicle(NetMessageCodec.DecodedVehicleSpawn spawn) {
        if (getTree() == null) return;
        StringName streamedGroup = new StringName(Vehicle.STREAMED_GROUP);
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {   // already present? idempotent
            if (node instanceof Controllable c && c.getCharacterInfo() != null
                    && spawn.vehicleId().equals(c.getCharacterInfo().characterId)) {
                // Idempotent — but reconcile the persistent/ephemeral flag (WS3 fix): a snapshot can
                // lazy-spawn a body as ephemeral before its reliable baseline (ephemeral=false) lands.
                // When the authoritative baseline says persistent, UPGRADE it out of STREAMED_GROUP so it
                // is no longer reconcile-eligible. (We never downgrade persistent → ephemeral.)
                if (!spawn.ephemeral() && node instanceof Vehicle existing && existing.isInGroup(streamedGroup)) {
                    existing.removeFromGroup(streamedGroup);
                }
                return;
            }
        }
        Node container = resolveCharactersContainer();
        if (container == null) {
            GD.print("GameManager: Characters container not found — cannot spawn replicated vehicle " + spawn.vehicleId());
            return;
        }
        Object loaded = GD.load(VEHICLE_SCENE_PATH);
        if (!(loaded instanceof PackedScene scene)) {
            GD.print("GameManager: failed to load " + VEHICLE_SCENE_PATH);
            return;
        }
        Node instance = scene.instantiate();
        if (!(instance instanceof Vehicle vehicle)) {
            if (instance != null) instance.queueFree();
            return;
        }
        CharacterInfo info = new CharacterInfo();
        info.characterId = spawn.vehicleId();
        info.faction = spawn.faction();
        info.ownerPeerId = spawn.ownerPeerId();
        vehicle.characterInfo = info;

        container.addChild(vehicle);   // Vehicle._ready fires here (populates id/group/authority)
        // Only ephemeral streamed traffic is reconcile-eligible (WS3 fix). A persistent vehicle
        // (scene-placed / player-driven, re-supplied via the baseline) is NOT tagged, so the client
        // ghost-reconcile never frees it during a snapshot gap.
        if (spawn.ephemeral()) vehicle.addToGroup(streamedGroup);
        vehicle.setGlobalPosition(spawn.position());
        vehicle.setGlobalRotation(new Vector3(0f, spawn.yaw(), 0f));
        vehicle.applyAuthorityState();   // attach VehicleNetworkController + freeze now (don't fall pre-snapshot)
        GD.print("GameManager: spawned replicated vehicle " + spawn.vehicleId());
        retryPendingSeats();   // a deferred occupancy may have been waiting on this vehicle (WS2)
    }

    /** Client-side: remove a server-despawned entity (Character or Vehicle) — MSG_DESPAWN's receiving side (NetworkManager.handleDespawnMessage). */
    public void despawnReplicatedCharacter(String characterId) {
        if (getTree() == null) return;
        pendingSeats.remove(characterId);   // drop any parked occupancy for a vehicle that's now gone (WS2)
        for (Node node : getTree().getNodesInGroup(CHARACTERS_GROUP)) {
            if (node instanceof com.openworld.control.Controllable c && c.getCharacterInfo() != null
                    && characterId.equals(c.getCharacterInfo().characterId)) {
                // Never free an occupied vehicle outright: if the occupancy-exit event was
                // missed/raced, the seated character's controller is a CHILD of this node and
                // would be freed with it — a permanently stuck (and crash-prone) body. tryExit
                // recovers the controller and drive state first; harmless if already vacant.
                Character despawnedVehicleDriver = null;
                if (node instanceof com.openworld.carrier.vehicle.Vehicle vehicle && vehicle.getOccupant() != null) {
                    Character occ = vehicle.getOccupant();
                    // An ephemeral traffic car's AI driver is paired with it — free it too, so a lost
                    // driver-despawn never strands the driver on foot ("character but no vehicle").
                    // A carjacked car holds a Player → never freed; a persistent car keeps its occupant.
                    if (occ instanceof AICharacter && vehicle.isInGroup(new StringName(Vehicle.STREAMED_GROUP))) {
                        despawnedVehicleDriver = occ;
                    }
                    vehicle.tryExit();
                }
                // Mirror of the above for the OTHER ordering (PLAN.md I3c streamed traffic): freeing a
                // SEATED character (e.g. a despawned AI driver whose despawn arrives before its car's)
                // must unseat it first, or the vehicle's `occupant` dangles → Vehicle._physicsProcess
                // dereferences a freed node (`get_global_transform "!is_inside_tree"` → use-after-free).
                if (node instanceof Character ch && ch.currentVehicleNode instanceof Vehicle seat
                        && seat.getOccupant() == ch) {
                    seat.tryExit();
                }
                node.queueFree();
                if (despawnedVehicleDriver != null && GD.isInstanceValid(despawnedVehicleDriver))
                    despawnedVehicleDriver.queueFree();   // its own MSG_DESPAWN is idempotent if it also lands
                return;
            }
        }
    }

    /**
     * Host-side entry point for removing a replicated entity: announce the despawn FIRST
     * (announceDespawn previously had no callers — clients kept freed entities forever),
     * then free locally. The seam Round 11 N3 vehicle destruction and any future corpse
     * cleanup go through; harmless in single-player (announce no-ops). Entity-generic —
     * {@code entity} is the Node to free, {@code info} carries the replicated id.
     */
    public void despawnAuthoritative(Node entity, CharacterInfo info) {
        if (entity == null) return;
        NetworkManager net = getNetworkManager();
        if (net != null && net.isNetworked() && net.isServer() && info != null) {
            net.announceDespawn(info.characterId);
        }
        entity.queueFree();
    }

    /** Character overload — keeps existing N2 call sites untouched. */
    public void despawnAuthoritative(Character character) {
        if (character == null) return;
        despawnAuthoritative(character, character.characterInfo);
    }

    // ── Reliable elimination + inventory reconciliation (Round 11 N2) ─────────

    /**
     * Client-side MSG_ELIMINATION apply (NetworkManager.handleEliminationMessage): force the
     * victim dead immediately (reliable path — no waiting on an unreliable health==0
     * snapshot) and re-emit the elimination on the local EventBus so the kill feed
     * (HUDManager), mission progress (MissionManager), and the all-players-dead GAME_OVER
     * tracking (onCharacterDied above) behave identically on every peer. The weaponIcon
     * Texture2D never crosses the wire — it's resolved locally by the source name from
     * IconRegistry (every peer ships the same weapon/vehicle scenes), so the client kill
     * feed shows the same icon the host does. An unregistered source resolves to null,
     * which the feed already tolerates.
     */
    public void applyReplicatedElimination(NetMessageCodec.DecodedElimination elim) {
        Character victim = findCharacterById(elim.victimCharacterId());
        NetworkManager net = getNetworkManager();
        if (victim == null) {
            com.openworld.net.NetStats.increment("elimination_unknown_victim");
            GD.print("GameManager: MSG_ELIMINATION for unknown victim " + elim.victimCharacterId());
        } else {
            Node healthNode = victim.getNodeOrNull("Health");
            if (healthNode instanceof com.openworld.character.Health health) health.applyReplicatedHealth(0f);
            if (victim.getController() instanceof NetworkController nc) {
                nc.forceReplicatedDeath();
            } else if (net != null && net.isAuthorityFor(victim.characterInfo)) {
                // Our own body: the host owns our health, so our death arrives here rather
                // than through Health.applyDamage → died → onDied. Player overrides
                // applyReplicatedDeath to also run the player-death UI path.
                victim.applyReplicatedDeath();
            }
        }
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) {
            godot.api.Texture2D weaponIcon = com.openworld.weapon.IconRegistry.get(elim.weaponName());
            bus.characterEliminated.emit(elim.attackerName(), elim.attackerFaction(),
                    elim.victimName(), elim.victimFaction(), elim.weaponName(), weaponIcon, elim.headshot());
            if (victim != null && victim.characterInfo != null) bus.characterDied.emit(victim.characterInfo);
        }
    }

    /**
     * Client-side MSG_INVENTORY apply (NetworkManager.handleInventoryMessage): reconcile the
     * character's WeaponController toward the host's manifest. Bodies this peer owns get the
     * add-only treatment (heal a lost grant, never fight the owner's live inventory) — see
     * WeaponController.applyReplicatedInventory.
     */
    public void applyReplicatedInventory(NetMessageCodec.DecodedInventory inv) {
        Character character = findCharacterById(inv.characterId());
        if (character == null) {
            com.openworld.net.NetStats.increment("inventory_unknown_character");
            return;
        }
        Node wcNode = character.getNodeOrNull("WeaponController");
        if (!(wcNode instanceof com.openworld.weapon.WeaponController wc)) return;
        NetworkManager net = getNetworkManager();
        boolean addOnly = net != null && net.isAuthorityFor(character.characterInfo);
        wc.applyReplicatedInventory(inv.entries(), addOnly);
    }

    // ── World-state event seam (Round 8 Step 4) ───────────────────────────────
    //
    // Single client-side entry point for host-authoritative world events
    // (NetworkManager.handleWorldEventMessage → here). The host applies each change locally when it
    // occurs and broadcasts it via NetworkManager.broadcastWorldEvent; clients receive it here and
    // route by eventType to the system that owns it — the scaffold doors / mission / story / the
    // spawn director register their cases on as those features become networked. Kept deliberately
    // small (eventType + key + scalar) so adding a new networked world fact is "add a constant +
    // a case", never a new message/codec/handler.

    /** A character (key = characterId) refilled at an AmmoRefill station — host-detected, mirrored on every peer. */
    public static final int WORLD_EVENT_AMMO_REFILL = 1;

    /** A vehicle (key = vehicleId) was destroyed — every peer plays the same wreck cosmetics (Round 11 N3); the despawn follows on the same ordered channel. */
    public static final int WORLD_EVENT_VEHICLE_WRECK = 2;

    /** Mission started — key = missionId, args = [objectiveType]. Host MissionManager broadcasts; clients re-emit EventBus.missionStarted for the HUD banner. */
    public static final int WORLD_EVENT_MISSION_STARTED = 3;

    /** Mission completed — key = missionId, args = [winningFaction, outcomeVariant]. */
    public static final int WORLD_EVENT_MISSION_COMPLETED = 4;

    /** Mission failed — key = missionId, args = [reason]. */
    public static final int WORLD_EVENT_MISSION_FAILED = 5;

    /** Runtime faction-relationship flip (D3) — key = factionA, args = [factionB, relationship]. Host FactionManager.setRelationship broadcasts; clients re-apply. */
    public static final int WORLD_EVENT_FACTION_RELATIONSHIP = 6;

    /** Runtime per-character faction swap (D3) — key = characterId, args = [faction]. Host Character.setFaction broadcasts; clients re-apply. */
    public static final int WORLD_EVENT_FACTION_SWAP = 7;

    /** Destructible state change (I2) — key = breakableId, value = 1 broken / 0 intact. Host Breakable broadcasts; clients re-apply cosmetically. */
    public static final int WORLD_EVENT_BREAKABLE = 8;

    /** Routes a decoded MSG_WORLD_EVENT to the owning system. Extend the switch as networked world state is added. */
    public void onWorldEvent(int eventType, String key, float value, java.util.List<String> args) {
        switch (eventType) {
            case WORLD_EVENT_AMMO_REFILL -> applyAmmoRefill(key);
            case WORLD_EVENT_VEHICLE_WRECK -> applyVehicleWreck(key);
            case WORLD_EVENT_MISSION_STARTED -> applyMissionStarted(key, args);
            case WORLD_EVENT_MISSION_COMPLETED -> applyMissionCompleted(key, args);
            case WORLD_EVENT_MISSION_FAILED -> applyMissionFailed(key, args);
            case WORLD_EVENT_FACTION_RELATIONSHIP -> applyFactionRelationship(key, args);
            case WORLD_EVENT_FACTION_SWAP -> applyCharacterFaction(key, args);
            case WORLD_EVENT_BREAKABLE -> applyBreakableState(key, value);
            // case WORLD_EVENT_DOOR -> applyDoorState(key, value);
            default -> GD.print("GameManager: unhandled world event type " + eventType + " key=" + key);
        }
    }

    /** Safe positional read from a world-event args list — empty string for a missing index (a client must never index past a short/garbage frame). */
    private static String arg(java.util.List<String> args, int index) {
        return args != null && index < args.size() && args.get(index) != null ? args.get(index) : "";
    }

    // Mission lifecycle is host-authoritative (MissionManager runs/tracks only there). On clients we
    // re-emit the matching EventBus signal so the HUD banner and any listener behave identically to
    // the host — but we never re-run host-only tracking (countHostilesByFaction); clients are pure
    // mirrors of mission state.

    private void applyMissionStarted(String missionId, java.util.List<String> args) {
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) bus.missionStarted.emit(missionId, arg(args, 0));
    }

    private void applyMissionCompleted(String missionId, java.util.List<String> args) {
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) bus.missionCompleted.emit(missionId, arg(args, 0), arg(args, 1));
    }

    private void applyMissionFailed(String missionId, java.util.List<String> args) {
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) bus.missionFailed.emit(missionId, arg(args, 0));
    }

    /** Client-side: mirror a host faction-relationship flip onto this peer's FactionManager (D3). */
    private void applyFactionRelationship(String factionA, java.util.List<String> args) {
        com.openworld.character.FactionManager factions = getFactionManager();
        // No re-broadcast: FactionManager.setRelationship gates its broadcast on isServer().
        if (factions != null) factions.setRelationship(factionA, arg(args, 0), arg(args, 1));
    }

    /** Client-side: mirror a host per-character faction swap (D3). Re-targeting follows on the next AI scan. */
    private void applyCharacterFaction(String characterId, java.util.List<String> args) {
        Character character = findCharacterById(characterId);
        // No re-broadcast: Character.setFaction gates its broadcast on isServer().
        if (character != null) character.setFaction(arg(args, 0));
    }

    /**
     * Mirrors a host-confirmed vehicle destruction's COSMETICS on this peer (explosion VFX +
     * wreck scene — never damage: the host already applied that authoritatively and clients
     * relaying explosion damage would double-apply it). The node removal arrives separately
     * as MSG_DESPAWN right behind this event on the same ordered channel.
     */
    private void applyVehicleWreck(String vehicleId) {
        com.openworld.carrier.vehicle.Vehicle vehicle = findVehicleById(vehicleId);
        if (vehicle != null) vehicle.playWreckCosmetics();
    }

    /**
     * Client-side: mirror a host-confirmed destructible state change (I2). value=1 → break, 0 → restore.
     * Cosmetic only (no re-broadcast): {@code Breakable.breakNow/restore} are called with broadcast=false
     * so a re-broadcasting host doesn't echo. Also used by {@code NetworkManager.sendBaselineBreakables}
     * to bring a late-joiner up to date on already-broken pieces.
     */
    private void applyBreakableState(String breakableId, float value) {
        com.openworld.world.Breakable b = findBreakableById(breakableId);
        if (b == null) return;
        if (value >= 0.5f) b.breakNow(false); else b.restore(false);
    }

    private com.openworld.world.Breakable findBreakableById(String breakableId) {
        if (getTree() == null || breakableId == null) return null;
        for (Node node : getTree().getNodesInGroup(
                new StringName(com.openworld.world.Breakable.BREAKABLE_GROUP))) {
            if (node instanceof com.openworld.world.Breakable b && breakableId.equals(b.breakableId)) {
                return b;
            }
        }
        return null;
    }

    /** Mirrors a host-confirmed ammo refill onto this peer's copy of the character. */
    private void applyAmmoRefill(String characterId) {
        Character character = findCharacterById(characterId);
        if (character == null) return;
        Node wcNode = character.getNodeOrNull("WeaponController");
        if (wcNode instanceof com.openworld.weapon.WeaponController wc) wc.fillWeaponAmmo();
    }

    /**
     * Client-side: the host vanished (NetworkManager.onHostLost already tore the dead session
     * down — isNetworked() is false here). Surface a recovery prompt so the player can restart
     * into single-player rather than sitting frozen against stale puppets. The restart path
     * (MenuManager.restart → restartLevel) reloads the scene into a fresh single-player world.
     */
    public void onHostLost(String reason) {
        GD.print("GameManager: host lost — " + reason);
        if (currentState == GameState.PLAYING) transitionTo(GameState.GAME_OVER);
        Node busNode = getNodeOrNull("/root/EventBus");
        if (busNode instanceof EventBus bus) bus.connectionLost.emit(reason);
    }

    private void transitionTo(GameState next) {
        GD.print("GameManager: " + currentState + " → " + next);
        currentState = next;
    }
}

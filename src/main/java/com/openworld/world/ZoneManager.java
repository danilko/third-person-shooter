package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.DirectionalLight3D;
import godot.api.Environment;
import godot.api.FileAccess;
import godot.api.NavigationRegion3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.PackedScene;
import godot.api.ResourceLoader;
import godot.api.WorldEnvironment;
import godot.core.Color;
import godot.core.Error;
import godot.core.NodePath;
import godot.core.StringName;
import godot.core.Vector3;
import godot.global.GD;

import com.openworld.ai.vehicle.VehicleAIController;
import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.character.AICharacter;
import com.openworld.character.AISquad;
import com.openworld.character.Character;
import com.openworld.character.CharacterInfo;
import com.openworld.character.FactionManager;
import com.openworld.character.Player;
import com.openworld.game.GameManager;
import com.openworld.game.PlayerRegistry;
import com.openworld.net.NetworkManager;
import com.openworld.weapon.WeaponController;
import com.openworld.weapon.WeaponItem;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;

/**
 * Streams open-world zones in and out around players (PLAN.md Part E / E1) — registered as the
 * AutoLoad singleton "ZoneManager".
 *
 * <p>{@link ZoneMarker}s register here; each tick the manager loads a zone when a player comes
 * within its {@code loadRadius} and unloads it when all players are beyond {@code unloadRadius}
 * (hysteresis prevents boundary flicker). Loading streams in the zone's {@link SpawnConfig} groups
 * (recycled through a {@link SpawnPool}) and {@link NamedCharacterConfig} story AI; unloading
 * returns pooled bodies to the pool and frees named ones.
 *
 * <p><b>Authority:</b> spawning/despawning AI is host-only ({@code NetworkManager.isServer()}) —
 * clients receive the bodies through the existing {@code announceSpawn → MSG_SPAWN} replication
 * path, and late-joiners via {@code sendBaselineSpawns}. Optional zone <i>geometry</i> is purely
 * cosmetic and is instanced locally on every peer.
 *
 * <p>Mirrors {@link SpatialEntityGrid}'s AutoLoad shape: JVM-static {@link #get()} set in
 * {@code _ready()} and cleared in {@code _exitTree()}, where pooled bodies + loaded geometry are
 * also freed (leak discipline — see CLAUDE.md "Known Quirks").
 */
@Script(className = "ZoneManager")
public class ZoneManager extends Node {

	private static ZoneManager instance;

	/** The live manager, or null if the AutoLoad isn't present (test scenes). */
	public static ZoneManager get() { return instance; }

	/** Seconds between load/unload evaluations. */
	@Export public float evalInterval = 0.5f;

	/**
	 * Max new zone stream-in pipelines <b>started</b> in a single eval tick. Starting a pipeline is
	 * cheap now (it only issues a threaded {@code ResourceLoader} request — parse happens on engine
	 * worker threads), so this no longer guards a freeze; it only caps how many multi-MB district
	 * parses run concurrently (memory/IO pressure right after a teleport or at spawn, when several
	 * neighbours qualify together — district centers are ~504m apart vs. the default 402m
	 * {@code loadRadius}). Main-thread work (instantiate / tree entry / spawning) is serialized to
	 * one zone at a time and time-sliced by {@link #streamBudgetMs} regardless of this value.
	 * Unloads are not capped (they are batched under the same budget).
	 */
	@Export public int maxLoadsPerTick = 2;

	/**
	 * Per-frame main-thread time budget (milliseconds) for streaming work: entering district
	 * geometry children into the tree, spawning streamed AI/vehicles, and batched unload frees.
	 * The pipeline always makes at least one step of progress per frame even when a single step
	 * (e.g. instancing one heavy building) blows the budget, so a task can never stall. ~4ms
	 * leaves headroom inside a 60Hz physics tick; lower it on Steam Deck if streaming visibly
	 * dents the frame rate, raise it to stream faster.
	 */
	@Export public float streamBudgetMs = 4.0f;

	/** Max recycled AI bodies the pool retains. */
	@Export public int poolCapacity = 64;

	/** Print per-zone load/unload decisions + pool stats to the Output log (E1 walk-test aid). */
	@Export public boolean debugLog = true;

	/**
	 * Recycle AI bodies through the {@link SpawnPool} across load/unload. <b>Default off / EXPERIMENTAL:</b>
	 * reusing a full character body subtree (detach via {@code removeChild}, re-attach via {@code addChild})
	 * is unsafe in godot-kotlin-jvm — the body carries a {@code top_level} camera, a muzzle-flash
	 * {@code GPUParticles3D}, and a nameplate {@code SubViewport}, and re-attaching that subtree
	 * dereferences transforms/particles in a half-initialised state ({@code get_global_transform "not
	 * inside tree"} / {@code particles is null}) → native use-after-free crash. With it off, unload frees
	 * and load instantiates fresh — correct and stutter-tolerable; spreading spawns across frames is the
	 * proper perf answer (TODO) rather than body reuse. Leave off unless you are actively hardening reuse.
	 */
	@Export public boolean recycleBodies = false;

	private static final String AI_SCENE_PATH =
			"res://src/main/resources/com/openworld/character/AICharacter.tscn";

	private final List<ZoneMarker> markers = new ArrayList<>();
	private final Map<ZoneMarker, LoadedZone> loaded = new HashMap<>();
	private SpawnPool pool;
	private double evalTimer = 0.0;
	/** Instance id of the scene root last seen, to detect a scene reload/restart (this AutoLoad survives it). */
	private long lastSceneInstanceId = 0;
	/** The loaded zone whose RegionConfig is currently applied globally (I4) — null = baseline / no region. */
	private ZoneMarker activeRegionMarker = null;
	// The scene environment as authored, captured the first time a region overrides it, so leaving every
	// region restores the original look instead of leaving the last region's lighting/fog stuck.
	private boolean envBaselineCaptured = false;
	private Color   baselineLightColor;
	private boolean baselineFogEnabled;
	private float   baselineFogDensity;

	/** Per-loaded-zone bookkeeping so unload returns exactly what load created. */
	private static final class LoadedZone {
		final List<AICharacter> pooled = new ArrayList<>();   // SpawnConfig AI → returned to pool
		final List<Character>   named  = new ArrayList<>();   // NamedCharacterConfig AI → freed
		final List<AISquad>     squads = new ArrayList<>();   // one per SpawnConfig group (E3) → freed
		final List<Vehicle>     vehicles = new ArrayList<>(); // VehicleSpawnConfig traffic (I3) → freed
		final Map<Vehicle, AICharacter> driverOf = new HashMap<>(); // traffic car → its AI driver (I3c)
		Node geometryInstance;                                // cosmetic, freed on unload
	}

	// ── Incremental streaming pipeline (the anti-freeze rework) ────────────────
	//
	// A zone crossing used to be one synchronous load(): GD.load-parse a 7–19MB district .tscn on
	// the main thread, instantiate ~1600 nodes, enter 500+ static bodies + a NavigationRegion3D
	// into the tree, then spawn every AI/vehicle — all inside a single physics frame (the district
	// -border freeze). Streaming is now a per-marker task state machine:
	//
	//   GEO_REQUEST → GEO_WAIT (engine worker threads parse the PackedScene; main thread polls)
	//     → GEO_INSTANTIATE (one frame: instantiate off-tree, strip children for batched entry)
	//     → GEO_ENTER (children re-enter the tree a budget-slice per frame; a NavigationRegion3D
	//        gets a frame alone — its nav-map sync is a one-off spike)
	//     → SPAWN (AI/named/vehicle spawns drained as work items under the same budget)
	//   and for unload: FREE_BODIES → FREE_GEO (batched queueFree instead of one 1600-node drop).
	//
	// While a task is in flight the marker is neither in `loaded` nor eligible for a second task;
	// the LOD-low placeholder stays up until the full-detail children are all in (no visual hole).
	// A load task whose player retreats past unloadRadius is cancelled (teardown of whatever made
	// it in so far). Synchronous teardown paths (marker exiting the tree, scene reload, AutoLoad
	// exit) still exist — see unloadImmediate/teardownZone.

	private enum Phase { GEO_REQUEST, GEO_WAIT, GEO_INSTANTIATE, GEO_ENTER, SPAWN, FREE_BODIES, FREE_GEO }

	private static final class StreamTask {
		final ZoneMarker marker;
		final boolean isLoad;          // false = batched unload
		final LoadedZone lz;
		Phase phase;
		String threadedPath = "";      // res:// path handed to loadThreadedRequest ("" = none)
		Node geoRoot;                  // instanced district root (in tree, children re-entering)
		final ArrayDeque<Node> pendingChildren = new ArrayDeque<>();  // OFF-tree until entered
		final ArrayDeque<Runnable> spawnWork = new ArrayDeque<>();
		int recycled = 0, freed = 0;   // unload stats (debug log)
		StreamTask(ZoneMarker marker, boolean isLoad, LoadedZone lz, Phase phase) {
			this.marker = marker; this.isLoad = isLoad; this.lz = lz; this.phase = phase;
		}
	}

	/** In-flight stream-in/out tasks, insertion-ordered (first task gets the frame budget). */
	private final Map<ZoneMarker, StreamTask> tasks = new LinkedHashMap<>();

	/** Markers whose next GEO_REQUEST must bypass the engine resource cache — set by
	 * {@link #reloadZone} (debug hot-reload after an external rebake), consumed in
	 * {@code beginGeometry}. */
	private final Set<ZoneMarker> replaceOnNextLoad = new HashSet<>();

	@Register
	@Override
	public void _ready() {
		instance = this;
	}

	@Register
	@Override
	public void _exitTree() {
		if (instance == this) instance = null;
		// In-flight tasks: only the OFF-tree staged children need explicit freeing (everything
		// in-tree — geometry roots, spawned AI — goes down with the SceneTree at engine teardown).
		for (StreamTask t : tasks.values()) freePendingChildren(t);
		tasks.clear();
		replaceOnNextLoad.clear();
		for (LoadedZone lz : loaded.values()) {
			if (lz.geometryInstance != null && GD.isInstanceValid(lz.geometryInstance))
				lz.geometryInstance.queueFree();
		}
		loaded.clear();
		markers.clear();
		routes.clear();
		if (pool != null) pool.clear();
	}

	// ── Route registry ──────────────────────────────────────────────────────────
	//
	// Name → live lane node, maintained by VehicleRoute._ready/_exitTree AND (as of the
	// road_kit_authoring integration) PathLaneRoute._ready/_exitTree — the same register-with-
	// AutoLoad idiom Character uses with SpatialEntityGrid. Typed against the Lane interface (not
	// the concrete VehicleRoute class) so a hand-authored Marker3D-chain network and a
	// Blender-generated PathLaneRoute network can both participate in ambient/disposable traffic
	// spawning — LaneGraph/VehicleAIController were already Lane-polymorphic; this registry was
	// the one remaining VehicleRoute-typed surface. Lookups that used to recursively walk the
	// ENTIRE scene tree per spawn (tens of thousands of JVM-bridge calls with a district streamed
	// in — the periodic maintainTraffic hitch) are now a map read. A TreeMap keeps names sorted,
	// so a prefix query iterates its matches in name order for free — the same deterministic order
	// the old collect-and-sort produced for the round-robin spawn spread.

	private final TreeMap<String, Lane> routes = new TreeMap<>();

	public void registerRoute(Lane route) {
		if (route instanceof Node3D n) routes.put(n.getName().toString(), route);
	}

	public void unregisterRoute(Lane route) {
		if (!(route instanceof Node3D n)) return;
		String name = n.getName().toString();
		if (routes.get(name) == route) routes.remove(name);
	}

	/** One-line streaming/traffic summary for the perf HUD ({@code PerfDebugOverlay}) — the
	 * counters that actually move when the open world hitches. Debug-only consumer. */
	public String debugStatsLine() {
		return "zones=" + loaded.size() + " tasks=" + tasks.size()
				+ " routes=" + routes.size() + " poolIdle=" + (pool != null ? pool.idleCount() : 0);
	}

	/** Read-only, name-sorted view of the live lane registry (debug overlay / diagnostics). */
	public java.util.SortedMap<String, Lane> getRoutes() {
		return java.util.Collections.unmodifiableSortedMap(routes);
	}

	/** Live lane by exact node name (O(log n)), or null when absent/freed. */
	public Lane routeByName(String name) {
		if (name == null || name.isEmpty()) return null;
		Lane r = routes.get(name);
		return r != null && r instanceof Node3D n && GD.isInstanceValid(n) ? r : null;
	}

	// ── Marker registry ───────────────────────────────────────────────────────

	public void registerMarker(ZoneMarker marker) {
		if (marker != null && !markers.contains(marker)) {
			markers.add(marker);
			warnIfMisSized(marker);
		}
	}

	/** Read-only view of the registered zone markers (I5 map/minimap region outlines). */
	public List<ZoneMarker> getMarkers() { return markers; }

	/** The nearest loaded zone carrying a RegionConfig (see {@link #updateActiveRegion}), or null
	 * if the local player isn't currently near any loaded region — e.g. a debug HUD readout. */
	public ZoneMarker getActiveRegionMarker() { return activeRegionMarker; }

	/** Nearest REGISTERED zone marker to the local player, loaded or not (unlike
	 * {@link #getActiveRegionMarker}, which only considers already-loaded zones) — e.g. a debug
	 * "which district am I over" readout that should work even before anything streams in. */
	public ZoneMarker getNearestMarker() {
		ZoneMarker best = null;
		float bestDist = Float.MAX_VALUE;
		for (ZoneMarker m : markers) {
			if (!GD.isInstanceValid(m) || m.zone == null) continue;
			float d = localPlayerDistXZ(m.getGlobalPosition());
			if (d < bestDist) { bestDist = d; best = m; }
		}
		return best;
	}

	/**
	 * Debug-only: drop this zone's cached {@link Zone#geometry} and re-stream it from disk,
	 * bypassing the engine resource cache — picks up an external rebake
	 * ({@code tools/build_piece.sh}) without restarting the game. Refuses while a stream task is
	 * in flight (the pipeline owns the marker then — retry once it settles). The zone is only
	 * unloaded here; the normal eval tick streams it back in because the player is still inside
	 * {@code loadRadius}, so the "marker with an in-flight task is in neither set" invariant holds.
	 */
	public boolean reloadZone(ZoneMarker marker) {
		if (marker == null || marker.zone == null) return false;
		if (tasks.containsKey(marker)) {
			GD.print("ZoneManager: reload of '" + marker.zone.zoneId
					+ "' refused — stream task in flight, retry");
			return false;
		}
		marker.zone.geometry = null;   // kill the parsed-scene short-circuit in beginGeometry
		replaceOnNextLoad.add(marker);
		warnIfStaleBinary(marker.zone);
		if (loaded.containsKey(marker)) beginUnload(marker);
		if (debugLog) GD.print("ZoneManager: hot-reload queued for zone '"
				+ marker.zone.zoneId + "'");
		return true;
	}

	/**
	 * {@link #resolveGeometryPath} prefers a sibling {@code .scn} over the baked {@code .tscn};
	 * {@code build_piece.sh} refreshes the binary as its final step, but a by-hand rebake that
	 * only rewrote the {@code .tscn} would be silently shadowed by the stale binary. Debug-gated
	 * log only, no behavior change.
	 */
	private void warnIfStaleBinary(Zone zone) {
		if (!debugLog) return;
		String p = zone.geometryPath;
		if (p == null || !p.endsWith(".tscn")) return;
		String bin = p.substring(0, p.length() - ".tscn".length()) + ".scn";
		if (!ResourceLoader.INSTANCE.exists(bin, "") || !ResourceLoader.INSTANCE.exists(p, "")) return;
		if (FileAccess.getModifiedTime(p) > FileAccess.getModifiedTime(bin)) {
			GD.print("ZoneManager: zone '" + zone.zoneId + "' — stale .scn shadows a fresh .tscn"
					+ " (reload will use the OLD binary; run ConvertPieces to refresh it)");
		}
	}

	/**
	 * Debug-gated sanity check on a zone's trigger radii. Both {@code loadRadius} and
	 * {@code unloadRadius} are measured from the marker (zone <b>center</b>), independent of
	 * {@code size}, so for sane streaming they must satisfy
	 * {@code unloadRadius > loadRadius > halfExtent} — otherwise the zone unloads while the player is
	 * still inside/near the spawn box (flicker, or "everything unloads the moment I step out").
	 * Logs once at registration; no behavior change. See CLAUDE.md "Sizing a zone".
	 */
	private void warnIfMisSized(ZoneMarker marker) {
		if (!debugLog || marker.zone == null) return;
		Zone z = marker.zone;
		float halfExtent = (float) Math.max(z.size.getX(), z.size.getZ()) * 0.5f;
		if (z.unloadRadius <= z.loadRadius || z.loadRadius < halfExtent) {
			GD.print("ZoneManager: zone '" + z.zoneId + "' has mis-sized radii (load="
					+ z.loadRadius + " unload=" + z.unloadRadius + " halfExtent="
					+ String.format("%.1f", halfExtent) + "m). Expected unloadRadius > loadRadius > "
					+ "halfExtent (radii are center-relative — see CLAUDE.md 'Sizing a zone').");
		}
	}

	public void unregisterMarker(ZoneMarker marker) {
		if (marker == null) return;
		StreamTask task = tasks.remove(marker);
		if (task != null) {
			// Mid-stream marker removal: free the OFF-tree staged children (they'd leak — nothing
			// owns them) and tear down whatever the task already put in the world (AI live under
			// the Characters container, not the marker, so they do NOT die with it).
			freePendingChildren(task);
			teardownZone(marker, task.lz);
		}
		if (loaded.containsKey(marker)) unloadImmediate(marker);
		markers.remove(marker);
	}

	// ── Streaming tick ──────────────────────────────────────────────────────────

	@Register
	@Override
	public void _physicsProcess(double delta) {
		detectSceneReload();
		cullFinishedVehicles();   // every frame (anti-jam) — a DESPAWN car must not idle at the lane end
		processStreamTasks();     // every frame — the time-sliced streaming pipeline

		evalTimer -= delta;
		if (evalTimer > 0.0) return;
		evalTimer = evalInterval;

		int loadsThisTick = 0;
		for (ZoneMarker marker : new ArrayList<>(markers)) {
			if (!GD.isInstanceValid(marker) || marker.zone == null) continue;
			float dist = nearestPlayerDistXZ(marker.getGlobalPosition());
			StreamTask task = tasks.get(marker);
			if (task != null) {
				// In flight — leave the pipeline to it. One exception: a player who turned around
				// mid-stream. Once they're past unloadRadius (same hysteresis as a loaded zone),
				// abandon the load — otherwise sprinting past a district forces a full load
				// immediately followed by a full unload.
				if (task.isLoad && dist > marker.zone.unloadRadius) cancelLoad(task);
				continue;
			}
			boolean isLoaded = loaded.containsKey(marker);
			if (!isLoaded && dist < marker.zone.loadRadius) {
				if (loadsThisTick >= maxLoadsPerTick) continue;   // next tick(s) pick up the rest
				beginLoad(marker);
				loadsThisTick++;
			} else if (isLoaded && dist > marker.zone.unloadRadius) {
				beginUnload(marker);
			} else if (debugLog && dist < marker.zone.unloadRadius * 1.2f) {
				// Approach feedback (only for zones the player is near, so far zones stay quiet).
				GD.print("ZoneManager: zone '" + marker.zone.zoneId + "' nearestPlayer="
						+ String.format("%.1f", dist) + "m  [load<" + marker.zone.loadRadius
						+ " unload>" + marker.zone.unloadRadius + "]  "
						+ (isLoaded ? "LOADED" : "idle"));
			}
		}

		updateActiveRegion();
		maintainTraffic();
	}

	/**
	 * Pick the active region (PLAN.md I4) = the nearest loaded zone that carries a {@link RegionConfig},
	 * and apply its <i>global</i> ambience (faction table, AI-LOD bias, lighting/fog, music) only when it
	 * changes — so several simultaneously-loaded zones don't thrash the environment, and the player's
	 * surroundings track whichever region they're closest to. Leaving every region restores baseline.
	 */
	private void updateActiveRegion() {
		ZoneMarker nearest = null;
		float best = Float.MAX_VALUE;
		for (Map.Entry<ZoneMarker, LoadedZone> e : loaded.entrySet()) {
			ZoneMarker m = e.getKey();
			if (!GD.isInstanceValid(m) || m.zone == null || m.zone.regionConfig == null) continue;
			float d = localPlayerDistXZ(m.getGlobalPosition());   // ambience follows THIS peer's player
			if (d < best) { best = d; nearest = m; }
		}
		if (nearest == activeRegionMarker) return;
		activeRegionMarker = nearest;
		applyRegion(nearest != null ? nearest.zone.regionConfig : null);
	}

	/**
	 * Push a region's global ambience into the shared systems (PLAN.md I4). A null {@code rc} restores
	 * baseline (faction defaults, LOD 1.0, the authored environment) — called when the player leaves the
	 * last region. Every step is null/absent-safe so test scenes without a FactionManager or
	 * WorldEnvironment simply skip that effect.
	 */
	private void applyRegion(RegionConfig rc) {
		// Faction relationships (host + clients resolve locally; betrayal flips still replicate).
		Node fmNode = getNodeOrNull(new NodePath("/root/FactionManager"));
		if (fmNode instanceof FactionManager fm) fm.applyTable(rc != null ? rc.factionTable : null);

		// AI level-of-detail range (process-global).
		AICharacter.setLodDistanceBias(rc != null ? rc.aiLodBias : 1.0f);

		// Scene environment: sun colour temperature + fog. Capture the authored look once so we can revert.
		applyEnvironment(rc);

		if (debugLog) {
			GD.print("ZoneManager: active region → "
					+ (rc != null ? "'" + rc.regionName + "' (aiDensity=" + rc.ambientAiDensity
						+ " vehDensity=" + rc.vehicleDensity + " lodBias=" + rc.aiLodBias + ")"
						: "none (baseline restored)"));
		}
	}

	private void applyEnvironment(RegionConfig rc) {
		DirectionalLight3D light = findInScene(DirectionalLight3D.class);
		WorldEnvironment we = findInScene(WorldEnvironment.class);
		Environment env = (we != null) ? we.getEnvironment() : null;

		if (!envBaselineCaptured && (light != null || env != null)) {
			if (light != null) baselineLightColor = light.getColor();
			if (env != null)  { baselineFogEnabled = env.isFogEnabled(); baselineFogDensity = (float) env.getFogDensity(); }
			envBaselineCaptured = true;
		}
		if (!envBaselineCaptured) return;

		// Each field is FULLY established to this region's look: the region's override if it specifies
		// one (> 0), otherwise the captured baseline. So switching from a foggy region into a non-foggy
		// one (or leaving all regions, rc == null) reverts that field to baseline instead of leaving the
		// previous region's fog/tint stuck — the "fog bleeds into the other region" bug.
		boolean useTemp = rc != null && rc.lightTemperature > 0f;
		if (light != null) {
			if (useTemp) light.setColor(kelvinToColor(rc.lightTemperature));
			else if (baselineLightColor != null) light.setColor(baselineLightColor);
		}
		boolean useFog = rc != null && rc.fogDensity > 0f;
		if (env != null) {
			if (useFog) { env.setFogEnabled(true); env.setFogDensity(rc.fogDensity); }
			else { env.setFogEnabled(baselineFogEnabled); env.setFogDensity(baselineFogDensity); }
		}
	}

	/** Region density multiplier on a base spawn count, clamped to ≥ 0; identity at density 1.0. */
	private static int scaledCount(int base, float density) {
		if (density == 1.0f) return base;
		int n = Math.round(base * density);
		return n < 0 ? 0 : n;
	}

	/** Depth-first search of the current scene for the first node of the given type (null if none). */
	@SuppressWarnings("unchecked")
	private <T extends Node> T findInScene(Class<T> type) {
		if (getTree() == null) return null;
		Node scene = getTree().getCurrentScene();
		return scene != null ? (T) findOfType(scene, type) : null;
	}

	private Node findOfType(Node node, Class<?> type) {
		if (type.isInstance(node)) return node;
		for (Node child : node.getChildren()) {
			Node hit = findOfType(child, type);
			if (hit != null) return hit;
		}
		return null;
	}

	/**
	 * Compact colour-temperature → RGB approximation (Tanner Helland), normalized to a Color with white
	 * at ~6600 K: warmer (lower K) tints orange, cooler (higher K) tints blue — the city↔mountain shift.
	 */
	private static Color kelvinToColor(float kelvin) {
		double t = Math.max(1000f, Math.min(40000f, kelvin)) / 100.0;
		double r, g, b;
		if (t <= 66) {
			r = 255;
			g = 99.4708025861 * Math.log(t) - 161.1195681661;
		} else {
			r = 329.698727446 * Math.pow(t - 60, -0.1332047592);
			g = 288.1221695283 * Math.pow(t - 60, -0.0755148492);
		}
		if (t >= 66)      b = 255;
		else if (t <= 19) b = 0;
		else              b = 138.5177312231 * Math.log(t - 10) - 305.0447927307;
		return new Color((float) clamp01(r / 255.0), (float) clamp01(g / 255.0), (float) clamp01(b / 255.0), 1.0f);
	}

	private static double clamp01(double v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }

	/**
	 * Per-frame anti-jam sweep (PLAN.md I3b): free any traffic vehicle that reached a dead-end lane
	 * ({@link VehicleAIController#isFinished()}) the moment it finishes, instead of letting it idle at
	 * the lane end until the throttled {@link #maintainTraffic} tick (where stacked finished cars would
	 * collide into a pile-up). Cheap — an `isFinished` check, no distance maths. Top-up still happens on
	 * the throttled tick. Host/SP-only, like the spawn (synced model — clients mirror via despawn).
	 */
	private void cullFinishedVehicles() {
		if (loaded.isEmpty()) return;
		NetworkManager net = networkManager();
		if (net != null && net.isNetworked() && !net.isServer()) return;
		for (LoadedZone lz : loaded.values()) {
			if (lz.vehicles.isEmpty()) continue;
			Iterator<Vehicle> it = lz.vehicles.iterator();
			while (it.hasNext()) {
				Vehicle v = it.next();
				boolean dead = !GD.isInstanceValid(v);
				if (dead || (v.getController() instanceof VehicleAIController c && c.isFinished())) {
					freeTrafficCar(lz, v, net);
					it.remove();
				}
			}
		}
	}

	/**
	 * Ephemeral ambient-traffic upkeep (PLAN.md I3b). Ambient cars are <b>disposable</b>: what is kept
	 * constant is the <i>population</i> near the player, not any individual vehicle. Each loaded zone is
	 * (1) <b>culled</b> — vehicles that reached a dead-end lane ({@link VehicleAIController#isFinished()},
	 * from the lane-graph chaining) or drifted beyond {@code unloadRadius} of every player are freed; and
	 * (2) <b>topped up</b> — respawned to the zone's configured count at a route entry. This is the
	 * non-circular counterpart to a ring road: a car drives its lane, chains through junctions while
	 * lanes connect, and is recycled (despawn + fresh spawn — never teleported, which would pop / break
	 * snapshot interpolation) once it runs out of network.
	 *
	 * <p>Host/SP-only (PLAN.md I3c synced model) — the host owns the traffic population; clients mirror
	 * each spawn/despawn over MSG_VEHICLE_SPAWN / MSG_DESPAWN and free ghosts via the client reconcile.
	 */
	/** Eval ticks between periodic traffic-status debug lines (~10 s at evalInterval 0.5). */
	private static final int TRAFFIC_LOG_EVERY = 20;
	private int trafficLogTick = 0;

	private void maintainTraffic() {
		if (loaded.isEmpty()) return;
		NetworkManager net = networkManager();
		if (net != null && net.isNetworked() && !net.isServer()) return;
		Node container = charactersContainer();
		if (container == null) return;
		boolean logStatus = debugLog && ++trafficLogTick % TRAFFIC_LOG_EVERY == 0;

		for (Map.Entry<ZoneMarker, LoadedZone> e : loaded.entrySet()) {
			ZoneMarker marker = e.getKey();
			LoadedZone lz = e.getValue();
			if (!GD.isInstanceValid(marker) || marker.zone == null) continue;
			List<VehicleSpawnConfig> configs = new ArrayList<>();
			for (VehicleSpawnConfig vc : marker.zone.vehicleSpawnConfigs) if (vc != null) configs.add(vc);
			if (configs.isEmpty() && lz.vehicles.isEmpty()) continue;

			// (1) Cull finished / dead / out-of-range cars (driver freed with the car). A car a player
			// carjacked is released, not freed, by freeTrafficCar — so reclaiming it on out-of-range just
			// untracks it and hands it to the player.
			Iterator<Vehicle> it = lz.vehicles.iterator();
			while (it.hasNext()) {
				Vehicle v = it.next();
				boolean dead = !GD.isInstanceValid(v);
				boolean fin = !dead && v.getController() instanceof VehicleAIController c && c.isFinished();
				// A car that left the road (junction overshoot, deck edge) free-falls forever —
				// its XZ stays inside the zone so the range check never reclaims it.
				boolean fell = !dead && v.getGlobalPosition().getY() < FELL_OUT_Y;
				boolean far = !dead && !fin && !fell
						&& nearestPlayerDistXZ(v.getGlobalPosition()) > marker.zone.unloadRadius;
				if (dead || fin || fell || far) {
					// "finished" reclaims should be ~0 away from map edges once lanes chain through
					// junctions (roads-v2 Phase 1) — a steady stream of them means broken wiring.
					if (debugLog) GD.print("ZoneManager: traffic reclaim in '" + marker.zone.zoneId
							+ "' (" + (dead ? "dead" : fin ? "route-finished"
									 : fell ? "fell-out" : "out-of-range") + ")");
					freeTrafficCar(lz, v, net);
					it.remove();
				}
			}

			// Periodic health line: cars moving + on-lane counts are the headless-smoke signal that
			// traffic actually flows and junction chaining works (roads-v2 Phase 1) — a fleet that is
			// routed but 0-moving, or moving but unrouted, is each its own distinct failure.
			if (logStatus && !lz.vehicles.isEmpty()) {
				int moving = 0, routed = 0;
				Vehicle sample = null;
				for (Vehicle v : lz.vehicles) {
					if (!GD.isInstanceValid(v)) continue;
					if (sample == null) sample = v;
					Vector3 vel = v.getLinearVelocity();
					double sp = Math.sqrt(vel.getX() * vel.getX() + vel.getZ() * vel.getZ());
					if (sp > 2.0) moving++;
					if (v.getController() instanceof VehicleAIController c && c.getRoute() != null) routed++;
				}
				GD.print("ZoneManager: traffic status '" + marker.zone.zoneId + "': "
						+ lz.vehicles.size() + " cars, " + moving + " moving, " + routed + " routed"
						+ (sample != null ? "  sample pos=" + sample.getGlobalPosition()
							+ " vel=" + sample.getLinearVelocity() : ""));
			}

			// (2) Top each zone back up to its configured vehicle count (region-density scaled to match
			// the load count, PLAN.md I4), cycling configs round-robin.
			float vehDensity = marker.zone.regionConfig != null
					? marker.zone.regionConfig.vehicleDensity : 1.0f;
			int target = 0;
			for (VehicleSpawnConfig vc : configs) target += scaledCount(vc.count, vehDensity);
			Vector3 center = marker.getGlobalPosition();
			int spawnIdx = lz.vehicles.size();
			for (int k = lz.vehicles.size(); k < target && !configs.isEmpty(); k++) {
				VehicleSpawnConfig vc = configs.get(k % configs.size());
				Lane route = findRoute(vc.routeName, center, marker.zone.unloadRadius, spawnIdx);
				// Spawn-gate by PLAYER distance, not just zone distance: the cull above reclaims any
				// car farther than unloadRadius from every player, but findRoute only checks the lane
				// entry against the ZONE CENTER — on a 504 m district a far-side entry can sit beyond
				// unloadRadius from the player, so the car would be reclaimed next tick and respawned
				// forever (a 4-cars-per-tick reclaim/spawn loop in the log). Skip that lane for now
				// (spawnIdx still advances, so the round-robin tries other lanes); 0.9 leaves
				// hysteresis between the spawn gate and the reclaim radius.
				if (route != null) {
					Vector3 entry = route.entryPoint();
					if (entry != null
							&& nearestPlayerDistXZ(entry) > marker.zone.unloadRadius * 0.9f) {
						spawnIdx++;
						continue;
					}
				}
				Vehicle v = spawnVehicle(vc, center, container, route, spawnIdx++, lz);
				if (v != null) {
					lz.vehicles.add(v);
					if (debugLog) GD.print("ZoneManager: traffic spawn in '" + marker.zone.zoneId
							+ "' lane=" + (route instanceof Node3D n ? n.getName().toString() : "<none>"));
				}
			}
		}
	}

	/**
	 * This is an AutoLoad, so it survives {@code reloadCurrentScene()}/{@code changeSceneToFile()}
	 * (restart, level change). Pooled bodies are parentless — held only by the {@link SpawnPool}
	 * deque, NOT in the scene tree — so a scene reload does <b>not</b> free them; re-acquiring one
	 * into the new scene resurrects a body that references freed nodes → crash. Detect the scene
	 * swap and drop all carried-over state so the new scene starts clean. ({@code loaded} entries key
	 * off the old scene's now-freed markers; those bodies were children of the freed scene and are
	 * gone with it — only the pool needs explicit freeing.)
	 */
	private void detectSceneReload() {
		if (getTree() == null) return;
		Node scene = getTree().getCurrentScene();
		long id = scene != null ? scene.getInstanceId() : 0;
		if (id == lastSceneInstanceId) return;
		lastSceneInstanceId = id;
		loaded.clear();
		// In-flight tasks referenced the old scene (markers, containers) — all of that died with it.
		// Only the OFF-tree staged children survive a scene swap; free them or they leak.
		for (StreamTask t : tasks.values()) freePendingChildren(t);
		tasks.clear();
		replaceOnNextLoad.clear();
		if (pool != null) { pool.clear(); pool = null; }
	}

	/** Horizontal (XZ) distance to the nearest live player, or MAX_VALUE if none. */
	private float nearestPlayerDistXZ(Vector3 center) {
		float min = Float.MAX_VALUE;
		for (Player p : PlayerRegistry.getPlayers()) {
			if (!GD.isInstanceValid(p)) continue;
			Vector3 pp = p.getGlobalPosition();
			float dx = (float) (pp.getX() - center.getX());
			float dz = (float) (pp.getZ() - center.getZ());
			float d = (float) Math.sqrt(dx * dx + dz * dz);
			if (d < min) min = d;
		}
		return min;
	}

	/**
	 * Horizontal distance to <b>this peer's own</b> player only (PLAN.md I4 active region), or MAX_VALUE
	 * if none. Streaming load/unload uses {@link #nearestPlayerDistXZ} (any player — every co-op player
	 * must get their nearby zones), but region <i>ambience</i> (fog/light/faction/LOD) is local
	 * presentation and must follow the LOCAL player: otherwise a remote player walking into a foggy zone
	 * would fog every peer (that zone is "nearest to a player") until all players leave it.
	 * {@code isLocalOwnedPlayer()} is true for the single-player sole player and, when networked, only the
	 * body this peer is authoritative for — so it resolves to one player per peer.
	 */
	private float localPlayerDistXZ(Vector3 center) {
		float min = Float.MAX_VALUE;
		for (Player p : PlayerRegistry.getPlayers()) {
			if (!GD.isInstanceValid(p) || !p.isLocalOwnedPlayer()) continue;
			Vector3 pp = p.getGlobalPosition();
			float dx = (float) (pp.getX() - center.getX());
			float dz = (float) (pp.getZ() - center.getZ());
			float d = (float) Math.sqrt(dx * dx + dz * dz);
			if (d < min) min = d;
		}
		return min;
	}

	// ── Load / unload (task creation) ───────────────────────────────────────────

	private void beginLoad(ZoneMarker marker) {
		// NOTE: the marker is NOT in `loaded` and its LOD-low placeholder stays up until the
		// pipeline finishes — completeLoad() flips both.
		tasks.put(marker, new StreamTask(marker, true, new LoadedZone(), Phase.GEO_REQUEST));
		if (debugLog) GD.print("ZoneManager: streaming IN zone '" + marker.zone.zoneId + "'…");
	}

	private void beginUnload(ZoneMarker marker) {
		LoadedZone lz = loaded.remove(marker);
		if (lz == null) return;
		marker.setLoadedVisual(false);
		tasks.put(marker, new StreamTask(marker, false, lz, Phase.FREE_BODIES));
		if (debugLog) GD.print("ZoneManager: streaming OUT zone '" + marker.zone.zoneId + "'…");
	}

	// ── Streaming pipeline ──────────────────────────────────────────────────────

	private void processStreamTasks() {
		if (tasks.isEmpty()) return;
		long budgetNanos = (long) (streamBudgetMs * 1_000_000L);
		long start = System.nanoTime();
		boolean workerTaken = false;   // one budgeted task per frame keeps the budget honest
		for (StreamTask t : new ArrayList<>(tasks.values())) {
			if (!GD.isInstanceValid(t.marker) || t.marker.zone == null) { discardTask(t); continue; }
			// Threaded-load polling is near-free — poll every waiting task every frame so a parse
			// finishing on a worker thread is picked up promptly even while another zone streams.
			if (t.phase == Phase.GEO_WAIT) {
				pollThreadedLoad(t);
				if (t.phase == Phase.GEO_WAIT) continue;   // still parsing on the worker
			}
			if (workerTaken) continue;
			workerTaken = true;
			stepTask(t, start, budgetNanos);
		}
	}

	/** Advance one task until it completes, yields the frame, or the budget runs out. */
	private void stepTask(StreamTask t, long start, long budgetNanos) {
		boolean cont = true;
		while (cont && tasks.get(t.marker) == t && (System.nanoTime() - start) < budgetNanos) {
			cont = switch (t.phase) {
				case GEO_REQUEST     -> beginGeometry(t);
				case GEO_WAIT        -> false;                 // worker thread owns it; poll next frame
				case GEO_INSTANTIATE -> { instantiateGeometry(t); yield true; }
				case GEO_ENTER       -> enterGeometryBatch(t, start, budgetNanos);
				case SPAWN           -> spawnBatch(t, start, budgetNanos);
				case FREE_BODIES     -> freeBodiesBatch(t, start, budgetNanos);
				case FREE_GEO        -> freeGeometryBatch(t, start, budgetNanos);
			};
		}
	}

	/**
	 * Resolve the zone's geometry piece to the path actually loaded: a sibling binary {@code .scn}
	 * wins over the baked text {@code .tscn} when it exists (see {@code PieceBinaryConverter} —
	 * binary skips the multi-MB text/base64 parse), else the wired path, else "" (not authored yet).
	 * `exists` guards against error spam either way (incremental authoring, no master re-bake).
	 */
	private String resolveGeometryPath(Zone zone) {
		String p = zone.geometryPath;
		if (p == null || p.isEmpty()) return "";
		if (p.endsWith(".tscn")) {
			String bin = p.substring(0, p.length() - ".tscn".length()) + ".scn";
			if (ResourceLoader.INSTANCE.exists(bin, "")) return bin;
		}
		return ResourceLoader.INSTANCE.exists(p, "") ? p : "";
	}

	/** GEO_REQUEST: kick the threaded parse (or skip straight ahead). Returns false when now waiting. */
	private boolean beginGeometry(StreamTask t) {
		Zone zone = t.marker.zone;
		if (zone.geometry != null) { t.phase = Phase.GEO_INSTANTIATE; return true; }   // already cached
		String path = resolveGeometryPath(zone);
		if (path.isEmpty()) { beginSpawnPhase(t); return true; }   // geometry-less zone (AI only)
		// Hot-reload (reloadZone): REPLACE re-reads the file from disk AND refreshes the engine
		// cache in place — plain REUSE would hand back the stale parse, IGNORE would leave the
		// stale entry cached for the next plain load.
		Error err = replaceOnNextLoad.remove(t.marker)
				? ResourceLoader.loadThreadedRequest(path, "", false, ResourceLoader.CacheMode.REPLACE)
				: ResourceLoader.loadThreadedRequest(path);
		if (err != Error.OK) {
			GD.printErr("ZoneManager: loadThreadedRequest failed for '" + path + "': " + err);
			beginSpawnPhase(t);
			return true;
		}
		t.threadedPath = path;
		t.phase = Phase.GEO_WAIT;
		return false;
	}

	/** GEO_WAIT: check on the worker-thread parse; cache + advance when it lands. */
	private void pollThreadedLoad(StreamTask t) {
		ResourceLoader.ThreadLoadStatus st = ResourceLoader.loadThreadedGetStatus(t.threadedPath);
		if (st == ResourceLoader.ThreadLoadStatus.IN_PROGRESS) return;
		if (st == ResourceLoader.ThreadLoadStatus.LOADED
				&& ResourceLoader.loadThreadedGet(t.threadedPath) instanceof PackedScene ps) {
			t.marker.zone.geometry = ps;   // cached on the resource, same as the old sync path
			t.phase = Phase.GEO_INSTANTIATE;
			return;
		}
		GD.printErr("ZoneManager: threaded load of '" + t.threadedPath + "' failed (" + st + ")");
		beginSpawnPhase(t);
	}

	/**
	 * GEO_INSTANTIATE: instantiate the district off-tree — node construction only, no physics/render
	 * registration yet — then strip its children so GEO_ENTER can re-enter them a slice per frame
	 * (tree entry is where the 500+ static-body/collider/render registrations actually happen).
	 * The bare root enters the tree here (near-free: one Node3D). This is the single largest
	 * remaining per-frame step (~1600 node constructions); if it ever reads as a hitch on Steam
	 * Deck, the next lever is baking districts as sub-chunk scenes, not shrinking the budget.
	 */
	private void instantiateGeometry(StreamTask t) {
		Node geo = t.marker.zone.geometry.instantiate();
		if (geo == null) { beginSpawnPhase(t); return; }
		for (Node child : new ArrayList<>(geo.getChildren())) {
			child.setOwner(null);   // removeChild keeps pack-time owner; re-adding then warns per node
			geo.removeChild(child);
			t.pendingChildren.add(child);
		}
		t.marker.addChild(geo);
		t.geoRoot = geo;
		t.lz.geometryInstance = geo;
		t.phase = Phase.GEO_ENTER;
	}

	/**
	 * GEO_ENTER: re-enter stripped children under the budget (children keep their local transforms,
	 * so batched re-parenting is layout-identical). Always at least one per frame (guaranteed
	 * progress). A {@link NavigationRegion3D} yields the rest of the frame — entering it triggers
	 * the NavigationServer map sync, a one-off spike that shouldn't share a frame with anything.
	 */
	private boolean enterGeometryBatch(StreamTask t, long start, long budgetNanos) {
		boolean first = true;
		while (!t.pendingChildren.isEmpty() && (first || (System.nanoTime() - start) < budgetNanos)) {
			first = false;
			Node child = t.pendingChildren.poll();
			boolean navRegion = child instanceof NavigationRegion3D;
			t.geoRoot.addChild(child);
			if (navRegion) return false;
		}
		if (!t.pendingChildren.isEmpty()) return false;   // budget spent — resume next frame
		// Full detail is genuinely all in — only now swap the placeholder out (no visual hole).
		t.marker.setLoadedVisual(true);
		t.marker.removeLodLow();
		beginSpawnPhase(t);
		return true;
	}

	/** Enter SPAWN with the zone's population queued as work items; empty queue completes at once. */
	private void beginSpawnPhase(StreamTask t) {
		t.phase = Phase.SPAWN;
		buildSpawnWork(t);
		if (t.spawnWork.isEmpty()) completeLoad(t);
	}

	/** SPAWN: drain spawn work items under the budget, at least one per frame. */
	private boolean spawnBatch(StreamTask t, long start, long budgetNanos) {
		boolean first = true;
		while (!t.spawnWork.isEmpty() && (first || (System.nanoTime() - start) < budgetNanos)) {
			first = false;
			t.spawnWork.poll().run();
		}
		if (t.spawnWork.isEmpty()) completeLoad(t);
		return true;
	}

	private void completeLoad(StreamTask t) {
		tasks.remove(t.marker);
		loaded.put(t.marker, t.lz);
		if (debugLog) {
			GD.print("ZoneManager: LOADED zone '" + t.marker.zone.zoneId + "' — "
					+ t.lz.pooled.size() + " ambient, " + t.lz.named.size() + " named, "
					+ t.lz.vehicles.size() + " vehicles; pool idle now "
					+ (pool != null ? pool.idleCount() : 0));
		}
	}

	/**
	 * Queue the zone's population as one-per-frame-amortizable work items (PLAN.md E1's deferred
	 * "frame-spread spawning"): squad creation, each ambient AI, each named AI, each traffic car.
	 * Host/SP-only, mirroring the old synchronous load — clients reconstruct via MSG_SPAWN /
	 * MSG_VEHICLE_SPAWN. Captured references (container, center) are per-load; a scene reload
	 * mid-task drops the whole task in detectSceneReload, so no stale-capture risk.
	 */
	private void buildSpawnWork(StreamTask t) {
		NetworkManager net = networkManager();
		if (net != null && net.isNetworked() && !net.isServer()) return;
		Zone zone = t.marker.zone;
		Node container = charactersContainer();
		if (container == null) {
			GD.print("ZoneManager: Characters container not found — cannot stream zone '"
					+ zone.zoneId + "'");
			return;
		}
		Vector3 center = t.marker.getGlobalPosition();
		// Region density scales this zone's own spawn counts (PLAN.md I4). 1.0 / no region = unchanged.
		float aiDensity  = zone.regionConfig != null ? zone.regionConfig.ambientAiDensity : 1.0f;
		float vehDensity = zone.regionConfig != null ? zone.regionConfig.vehicleDensity  : 1.0f;

		for (SpawnConfig cfg : zone.spawnConfigs) {
			if (cfg == null) continue;
			// One squad per SpawnConfig group so the band shares awareness (PLAN.md E3). The squad
			// node is created by the first work item of the group; members read it via the holder.
			final AISquad[] squadRef = new AISquad[1];
			t.spawnWork.add(() -> {
				AISquad squad = new AISquad();
				container.addChild(squad);
				t.lz.squads.add(squad);
				squadRef[0] = squad;
			});
			int n = scaledCount(cfg.count, aiDensity);
			for (int i = 0; i < n; i++) {
				final int idx = i;
				t.spawnWork.add(() -> spawnAmbient(t.lz, cfg, squadRef[0], idx, center, zone, container));
			}
		}
		for (NamedCharacterConfig nc : zone.namedCharacters) {
			if (nc == null) continue;
			t.spawnWork.add(() -> spawnNamed(t.lz, nc, center, container));
		}
		for (VehicleSpawnConfig vc : zone.vehicleSpawnConfigs) {
			if (vc == null) continue;
			int n = scaledCount(vc.count, vehDensity);
			for (int i = 0; i < n; i++) {
				final int idx = i;
				t.spawnWork.add(() -> {
					Lane route = findRoute(vc.routeName, center, zone.unloadRadius, idx);
					Vehicle v = spawnVehicle(vc, center, container, route, idx, t.lz);
					if (v != null) t.lz.vehicles.add(v);
				});
			}
		}
	}

	/** One ambient SpawnConfig AI (extracted from the old synchronous load loop, one work item). */
	private void spawnAmbient(LoadedZone lz, SpawnConfig cfg, AISquad squad, int index,
							  Vector3 center, Zone zone, Node container) {
		if (!GD.isInstanceValid(container)) return;
		SpawnPool sp = pool();
		AICharacter ai = sp.acquire();
		if (ai == null) return;
		boolean recycled = sp.wasLastAcquireRecycled();

		CharacterInfo info = new CharacterInfo();
		info.characterId = UUID.randomUUID().toString();
		info.displayName = cfg.faction + " " + (index + 1);
		info.faction = cfg.faction;
		ai.characterInfo = info;
		if (cfg.behaviorConfig != null) ai.behaviorConfig = cfg.behaviorConfig;

		container.addChild(ai);
		ai.activateForSpawn(randomPointInBox(center, zone.size));
		if (squad != null && GD.isInstanceValid(squad)) ai.setSquad(squad);
		// A recycled body keeps its weapon, so skip the equip — unless it somehow came back
		// unarmed (defensive: never leave a streamed AI with only fists).
		if (!recycled || !isArmed(ai)) equipWeapon(ai, cfg.weaponScenePath, container);

		lz.pooled.add(ai);
		NetworkManager net = networkManager();
		if (net != null) net.announceSpawn(ai);
	}

	/** One named story AI (extracted from the old synchronous load loop, one work item). */
	private void spawnNamed(LoadedZone lz, NamedCharacterConfig nc, Vector3 center, Node container) {
		if (!GD.isInstanceValid(container)) return;
		AICharacter ai = instantiateNamed(nc);
		if (ai == null) return;

		CharacterInfo info = new CharacterInfo();
		info.characterId = nc.characterId.isEmpty() ? UUID.randomUUID().toString() : nc.characterId;
		info.displayName = nc.displayName;
		info.faction = nc.faction;
		ai.characterInfo = info;
		if (nc.behaviorConfig != null) ai.behaviorConfig = nc.behaviorConfig;

		container.addChild(ai);
		ai.activateForSpawn(center.plus(nc.offset));
		equipWeapon(ai, nc.weaponScenePath, container);

		lz.named.add(ai);
		NetworkManager net = networkManager();
		if (net != null) net.announceSpawn(ai);
	}

	/**
	 * FREE_BODIES: batched teardown of the zone's population, one body per step under the budget.
	 * Freeing an AI body (skeleton + nameplate SubViewport + camera) is the expensive unit here;
	 * squads (plain Nodes) are freed all at once when the bodies are done.
	 */
	private boolean freeBodiesBatch(StreamTask t, long start, long budgetNanos) {
		NetworkManager net = networkManager();
		LoadedZone lz = t.lz;
		boolean first = true;
		while (first || (System.nanoTime() - start) < budgetNanos) {
			first = false;
			if (!lz.pooled.isEmpty()) {
				freeAmbient(t, lz.pooled.remove(lz.pooled.size() - 1), net);
			} else if (!lz.named.isEmpty()) {
				Character ai = lz.named.remove(lz.named.size() - 1);
				if (!GD.isInstanceValid(ai)) continue;
				if (net != null && ai.characterInfo != null) net.announceDespawn(ai.characterInfo.characterId);
				silenceWeaponAudio(ai);
				ai.queueFree();
			} else if (!lz.vehicles.isEmpty()) {
				Vehicle v = lz.vehicles.remove(lz.vehicles.size() - 1);
				freeTrafficCar(lz, v, net);
			} else {
				break;
			}
		}
		if (lz.pooled.isEmpty() && lz.named.isEmpty() && lz.vehicles.isEmpty()) {
			for (AISquad squad : lz.squads) if (GD.isInstanceValid(squad)) squad.queueFree();
			lz.squads.clear();
			lz.driverOf.clear();
			t.phase = Phase.FREE_GEO;
		}
		return true;
	}

	/** One pooled ambient body: despawn-announce, silence audio, recycle-or-free (see recycleBodies). */
	private void freeAmbient(StreamTask t, AICharacter ai, NetworkManager net) {
		if (!GD.isInstanceValid(ai)) return;
		if (net != null && ai.characterInfo != null) net.announceDespawn(ai.characterInfo.characterId);
		silenceWeaponAudio(ai);  // stop in-flight SFX while still in-tree (audio-leak quirk)
		// Only HEALTHY bodies are recycled (and only when recycleBodies is on). A body that died
		// while the zone was loaded is ragdolled (physics off, collision shapes disabled, weapons
		// dropped) — activateForSpawn does not undo that, so resurrecting it crashes. Dead bodies
		// (and everything when recycling is off) follow the normal free flow.
		if (recycleBodies && ai.isAlive() && !ai.isDead()) {
			pool().release(ai);
			t.recycled++;
		} else {
			ai.queueFree();
			t.freed++;
		}
	}

	/**
	 * FREE_GEO: dismantle the district a budget-slice per frame instead of queueFree-ing a
	 * ~1600-node subtree in one go (all of it would be destructed at the same frame's end —
	 * the unload-side hitch). Children are detached (tree exit = physics/render dereg) and
	 * individually queueFree'd; the bare root goes last, then the placeholder tier returns.
	 */
	private boolean freeGeometryBatch(StreamTask t, long start, long budgetNanos) {
		Node geo = t.lz.geometryInstance;
		if (geo == null || !GD.isInstanceValid(geo)) { completeUnload(t); return true; }
		List<Node> kids = new ArrayList<>(geo.getChildren());
		int i = kids.size() - 1;
		boolean first = true;
		while (i >= 0 && (first || (System.nanoTime() - start) < budgetNanos)) {
			first = false;
			Node child = kids.get(i--);
			if (!GD.isInstanceValid(child)) continue;
			geo.removeChild(child);
			child.queueFree();
		}
		if (i < 0) {
			geo.queueFree();
			completeUnload(t);
		}
		return true;
	}

	private void completeUnload(StreamTask t) {
		tasks.remove(t.marker);
		t.marker.instantiateLodLow();   // full detail is gone — bring the placeholder back
		if (debugLog) {
			GD.print("ZoneManager: UNLOADED zone '"
					+ (t.marker.zone != null ? t.marker.zone.zoneId : "?")
					+ "' — " + t.recycled + " recycled, " + t.freed + " freed; pool idle now "
					+ (pool != null ? pool.idleCount() : 0));
		}
	}

	// ── Task cancellation / synchronous teardown ────────────────────────────────

	/** Abandon an in-flight LOAD (player retreated). Whatever made it in tears down synchronously. */
	private void cancelLoad(StreamTask t) {
		tasks.remove(t.marker);
		freePendingChildren(t);
		teardownZone(t.marker, t.lz);
		if (GD.isInstanceValid(t.marker)) {
			t.marker.setLoadedVisual(false);
			t.marker.instantiateLodLow();
		}
		if (debugLog && t.marker.zone != null) {
			GD.print("ZoneManager: load of zone '" + t.marker.zone.zoneId
					+ "' cancelled (player left mid-stream)");
		}
		// A still-running threaded parse can't be cancelled; it completes into the resource cache
		// and makes the next approach near-instant. Harmless.
	}

	/** Free a task's OFF-tree staged children — nobody owns them, so they'd otherwise leak. */
	private void freePendingChildren(StreamTask t) {
		for (Node n : t.pendingChildren) if (GD.isInstanceValid(n)) n.queueFree();
		t.pendingChildren.clear();
	}

	/** Drop a task whose marker died (its in-tree nodes die with the marker/scene). */
	private void discardTask(StreamTask t) {
		tasks.remove(t.marker);
		freePendingChildren(t);
	}

	/** Synchronous unload — marker leaving the tree / AutoLoad teardown (never the streaming tick). */
	private void unloadImmediate(ZoneMarker marker) {
		LoadedZone lz = loaded.remove(marker);
		if (lz == null) return;
		if (GD.isInstanceValid(marker)) {
			marker.setLoadedVisual(false);
			marker.instantiateLodLow();   // full detail is gone — bring the placeholder back
		}
		teardownZone(marker, lz);
		if (debugLog) {
			GD.print("ZoneManager: UNLOADED zone '"
					+ (marker.zone != null ? marker.zone.zoneId : "?")
					+ "' (immediate); pool idle now " + (pool != null ? pool.idleCount() : 0));
		}
	}

	/** Free everything a LoadedZone tracks, in one go (cancel / unregister / exit paths). */
	private void teardownZone(ZoneMarker marker, LoadedZone lz) {
		NetworkManager net = networkManager();
		StreamTask stats = new StreamTask(marker, false, lz, Phase.FREE_BODIES);   // recycle/free counters only
		for (AICharacter ai : lz.pooled) freeAmbient(stats, ai, net);
		lz.pooled.clear();
		for (Character ai : lz.named) {
			if (!GD.isInstanceValid(ai)) continue;
			if (net != null && ai.characterInfo != null) net.announceDespawn(ai.characterInfo.characterId);
			silenceWeaponAudio(ai);
			ai.queueFree();
		}
		lz.named.clear();
		for (AISquad squad : lz.squads) if (GD.isInstanceValid(squad)) squad.queueFree();
		lz.squads.clear();
		// Ambient vehicles (I3) + their AI drivers (I3c) — freed together, not pooled (full vehicle
		// subtree reuse is unsafe, same reason recycleBodies is off for characters; the body owns a
		// top_level camera + viewport). A player-carjacked car is released, not freed (see freeTrafficCar).
		for (Vehicle v : new ArrayList<>(lz.vehicles)) freeTrafficCar(lz, v, net);
		lz.vehicles.clear();
		lz.driverOf.clear();
		if (lz.geometryInstance != null && GD.isInstanceValid(lz.geometryInstance))
			lz.geometryInstance.queueFree();
	}

	// ── Helpers ───────────────────────────────────────────────────────────────

	private SpawnPool pool() {
		if (pool == null) {
			Object aiScene = GD.load(AI_SCENE_PATH);
			pool = new SpawnPool(aiScene instanceof PackedScene ps ? ps : null, poolCapacity);
		}
		return pool;
	}

	private AICharacter instantiateNamed(NamedCharacterConfig nc) {
		PackedScene scene = nc.scene;
		if (scene == null) {
			Object loadedScene = GD.load(AI_SCENE_PATH);
			if (loadedScene instanceof PackedScene ps) scene = ps;
		}
		if (scene == null) return null;
		Node inst = scene.instantiate();
		if (inst instanceof AICharacter ai) return ai;
		if (inst != null) inst.queueFree();
		return null;
	}

	private Node charactersContainer() {
		if (getTree() == null) return null;
		Node scene = getTree().getCurrentScene();
		return scene != null ? scene.getNodeOrNull("Characters") : null;
	}

	private NetworkManager networkManager() {
		Node n = getNodeOrNull("/root/NetworkManager");
		return n instanceof NetworkManager net ? net : null;
	}

	/**
	 * Loads + equips a weapon onto the AI — same deferred path DebugHarness/WeaponPickup use
	 * (the WeaponItem must be inside the tree before WeaponController reparents it onto the body).
	 */
	private boolean isArmed(AICharacter ai) {
		Node wcNode = ai.getNodeOrNull(new NodePath("WeaponController"));
		return wcNode instanceof WeaponController wc && wc.isArmed();
	}

	/** Stop a body's weapon SFX while it is still in-tree, before freeing it (audio-leak quirk). */
	private void silenceWeaponAudio(Node body) {
		Node wcNode = body.getNodeOrNull(new NodePath("WeaponController"));
		if (wcNode instanceof WeaponController wc) wc.silenceAudio();
	}

	private void equipWeapon(AICharacter ai, String weaponScenePath, Node container) {
		Node wcNode = ai.getNodeOrNull(new NodePath("WeaponController"));
		if (!(wcNode instanceof WeaponController wc)) return;
		Object loaded = GD.load(weaponScenePath);
		if (!(loaded instanceof PackedScene weaponScene)) return;
		Node inst = weaponScene.instantiate();
		if (!(inst instanceof WeaponItem weapon)) { if (inst != null) inst.queueFree(); return; }
		container.addChild(weapon);
		weapon.setGlobalPosition(ai.getGlobalPosition());
		wc.requestEquip(weapon);
	}

	/**
	 * Instantiates one ambient vehicle (I3), stamps a host UUID <b>before</b> addChild (required by
	 * {@code Vehicle._ready} for replication to resolve — Round 11 N3), places it on the route, and
	 * attaches a {@link VehicleAIController} bound to that route. Returns the body, or null on failure.
	 */
	private Vehicle spawnVehicle(VehicleSpawnConfig vc, Vector3 center, Node container,
								 Lane route, int index, LoadedZone lz) {
		Object loaded = GD.load(vc.vehicleScenePath);
		if (!(loaded instanceof PackedScene scene)) return null;
		Node inst = scene.instantiate();
		if (!(inst instanceof Vehicle v)) { if (inst != null) inst.queueFree(); return null; }

		// ALWAYS a fresh per-instance CharacterInfo — NEVER reuse the one the scene supplied. Vehicle.tscn
		// embeds a single CharacterInfo sub-resource that (unless resource_local_to_scene) is SHARED by
		// every Vehicle.tscn instance; mutating it here rewrote the id + ownerPeerId of every other vehicle
		// (the player's car included), collapsing all vehicles onto one shared id — the cause of the
		// stuck/teleporting traffic, can't-exit, and ownership-migrates-to-all bugs.
		v.characterInfo = new CharacterInfo();
		v.characterInfo.characterId = UUID.randomUUID().toString();
		v.characterInfo.displayName = "Traffic " + (index + 1);
		v.characterInfo.faction = vc.faction;

		container.addChild(v);
		v.setGlobalPosition(vehicleStartPoint(route, center, index));

		VehicleAIController ctrl = new VehicleAIController();
		ctrl.cruiseThrottle = vc.cruiseThrottle;
		v.attachController(ctrl);
		if (route != null) ctrl.setRoute(route);

		// Host-owned synced ambient traffic (PLAN.md I3c): announce the streamed car so clients
		// reconstruct it as a snapshot-driven puppet. The STREAMED_GROUP tag drives both the late-join
		// baseline and the client ghost-reconcile. Host-only inside announceVehicleSpawn; harmless in SP.
		v.addToGroup(new StringName(Vehicle.STREAMED_GROUP));
		NetworkManager net = networkManager();
		if (net != null) net.announceVehicleSpawn(v);

		AICharacter driver = spawnTrafficDriver(vc, v, container);
		if (driver != null) lz.driverOf.put(v, driver);
		return v;
	}

	/**
	 * Spawn a visible AI driver and seat it in the traffic car (PLAN.md I3c). The car keeps its own
	 * {@link VehicleAIController} (Design B) and the AI rides as a non-driving, drive-state-inert passenger.
	 * Host-owned + replicated: the driver body is announced (MSG_SPAWN) and {@code seatTrafficDriver}
	 * broadcasts the seat occupancy so clients show the driver. The driver rides unarmed (its default fist
	 * suffices for a FIGHT carjack reaction). Returns the driver, or null on failure.
	 */
	private AICharacter spawnTrafficDriver(VehicleSpawnConfig vc, Vehicle v, Node container) {
		Object loaded = GD.load(AI_SCENE_PATH);
		if (!(loaded instanceof PackedScene scene)) return null;
		Node inst = scene.instantiate();
		if (!(inst instanceof AICharacter ai)) { if (inst != null) inst.queueFree(); return null; }

		CharacterInfo info = new CharacterInfo();
		info.characterId = UUID.randomUUID().toString();
		info.displayName = "Driver";
		info.faction = vc.faction;
		ai.characterInfo = info;
		if (vc.behaviorConfig != null) ai.behaviorConfig = vc.behaviorConfig;

		container.addChild(ai);
		ai.activateForSpawn(v.getGlobalPosition());
		NetworkManager net = networkManager();
		if (net != null) net.announceSpawn(ai);

		if (getNodeOrNull("/root/GameManager") instanceof GameManager gm) gm.seatTrafficDriver(v, ai);
		else v.tryEnter(ai);   // SP fallback if the GameManager AutoLoad is somehow absent (test scenes)
		return ai;
	}

	/**
	 * Free a host-owned traffic car together with its paired AI driver (PLAN.md I3c), announcing each
	 * despawn so clients mirror, and dropping the pairing. A car a <b>player</b> has carjacked is never
	 * yanked away — it is released to the player (untracked, not freed); its (already-ejected) original
	 * driver is still freed. Safe if either node is already invalid.
	 */
	private void freeTrafficCar(LoadedZone lz, Vehicle v, NetworkManager net) {
		AICharacter driver = lz.driverOf.remove(v);
		boolean vValid = GD.isInstanceValid(v);
		// Unseat the driver from its car first, so neither node dangles when freed: a seated occupant
		// is dereferenced every frame in Vehicle._physicsProcess, and freeing the car or the driver out
		// of order leaves a use-after-free. Only when the driver is still the occupant — a carjacked car
		// holds a Player and must never be auto-unseated.
		if (vValid && driver != null && v.getOccupant() == driver) v.tryExit();
		if (driver != null && GD.isInstanceValid(driver)) {
			if (net != null && driver.characterInfo != null) net.announceDespawn(driver.characterInfo.characterId);
			silenceWeaponAudio(driver);
			driver.queueFree();
		}
		if (!vValid) return;
		if (v.getOccupant() instanceof Player) return;   // carjacked → released to the player, don't free
		if (net != null && v.getCharacterInfo() != null) net.announceDespawn(v.getCharacterInfo().characterId);
		silenceWeaponAudio(v);
		v.queueFree();
	}

	/** Metres between successive cars queued at a one-way lane's entry (≈ one car length). */
	private static final float VEHICLE_QUEUE_SPACING = 6.0f;

	/** World-Y below every drivable surface (bay floor is -2, decks ramp ≥ -1) — a traffic car
	 *  under this has fallen out of the world and is reclaimed. */
	private static final float FELL_OUT_Y = -30.0f;

	/**
	 * Start position for a streamed vehicle.
	 *
	 * <p>A <b>loop</b> (ring) lane distributes cars around the ring (so they don't all stack at one
	 * point). A one-way {@code DESPAWN} lane instead spawns at the <b>lane entry</b> ({@code waypoints[0]},
	 * the zone-side end) — a topped-up car then enters where traffic originates rather than popping in
	 * mid-corridor in the player's view (PLAN.md I3b "Respawn polish"). Successive cars are nudged a few
	 * metres up the first lane segment so they queue in-lane instead of overlapping. Falls back to the
	 * zone center when the route has no markers.
	 */
	private Vector3 vehicleStartPoint(Lane route, Vector3 center, int index) {
		if (route == null) return center;
		double total = route.total();
		if (total <= 1e-6) {
			Vector3 sp = route.startPoint();
			return sp != null ? sp : center;
		}
		if (route.isLoop()) {
			// Arc-length scatter (was a raw marker-index scatter, VehicleRoute-only via
			// waypoints()) -- not expressible against the Lane interface, since a PathLaneRoute
			// has no discrete marker list, only a baked Curve3D. Spacing is now a fixed physical
			// distance (VEHICLE_QUEUE_SPACING) rather than however dense the author's markers
			// happen to be -- visually similar or better for existing VehicleRoute loops, never
			// worse, but not byte-identical (see road_blender_godot.md Phase 6 notes).
			return route.pointAtLength((index * VEHICLE_QUEUE_SPACING) % total);
		}
		// Step forward along the smoothed/offset path, but never past it (stay within the lane).
		double step = Math.min(index * VEHICLE_QUEUE_SPACING, total * 0.9);
		return route.pointAtLength(step);
	}

	/**
	 * Resolve a {@link Lane} for spawn <b>index</b> of a zone at <b>center</b>. Three lookup
	 * strategies, in order: (1) exact node-name match; (2) <b>zone-id equality</b> — every
	 * {@link PathLaneRoute} whose {@link PathLaneRoute#zoneId} equals {@code routeName} exactly
	 * (the property-based zone tag {@code lib/lane_kit.py}'s combiner stamps on every lane —
	 * road_kit_authoring's replacement for the old name-prefix convention, see
	 * road_blender_godot.md Phase 6 P6.4); (3) otherwise {@code routeName} is treated as a
	 * <b>prefix</b> (roads-v2 Phase 1 — e.g. {@code "art_"} = the master arterial lanes,
	 * {@code "District_X__"} = that district's authored lanes) — the original, still-unchanged
	 * behavior for a {@link VehicleRoute} district. Either strategy (2)/(3) collects the matching
	 * plain lanes (never a turn connector — spawning mid-junction would drop a car inside the box)
	 * whose entry lies within {@code maxDist} of the zone (a map-wide prefix/zone must not spawn a
	 * car kilometres away), and picks round-robin by spawn index — that spread IS the multi-lane
	 * spawn distribution. Null when nothing matches (car spawns unrouted at the center).
	 *
	 * <p>All lookups go through the {@link #routes} registry (never a scene-tree walk); each pass
	 * reads only plain-Java state per candidate ({@link #isSpawnCandidate}), so it stays cheap at
	 * hundreds of lanes — works identically for a {@link VehicleRoute} or a {@link PathLaneRoute}.
	 * The zone-id pass is a full scan of {@link #routes} (no sorted-key shortcut, unlike the
	 * prefix pass) — fine at authoring-time lane counts, not meant for world-wide scale in one
	 * zone's spawn tick.
	 */
	private Lane findRoute(String routeName, Vector3 center, float maxDist, int index) {
		if (routeName == null || routeName.isEmpty()) return null;
		Lane exact = routeByName(routeName);
		if (exact != null) return exact;

		List<Lane> zoneMatches = new ArrayList<>();
		for (Lane r : routes.values()) {
			if (r instanceof PathLaneRoute p && routeName.equals(p.zoneId)
					&& isSpawnCandidate(r, center, maxDist)) {
				zoneMatches.add(r);
			}
		}
		if (!zoneMatches.isEmpty()) return zoneMatches.get(Math.floorMod(index, zoneMatches.size()));

		List<Lane> matches = new ArrayList<>();
		for (Map.Entry<String, Lane> e : routes.tailMap(routeName).entrySet()) {
			if (!e.getKey().startsWith(routeName)) break;   // sorted map — past the prefix block
			if (isSpawnCandidate(e.getValue(), center, maxDist)) matches.add(e.getValue());
		}
		if (matches.isEmpty()) return null;
		return matches.get(Math.floorMod(index, matches.size()));
	}

	/** Shared "is this lane a legal ambient-spawn point" filter for both {@link #findRoute}
	 *  passes: a live node, not a turn connector (spawning mid-junction would drop a car inside
	 *  the box), and — when a zone/distance context is given — within {@code maxDist} of
	 *  {@code center}. */
	private boolean isSpawnCandidate(Lane r, Vector3 center, float maxDist) {
		if (!(r instanceof Node3D n) || !GD.isInstanceValid(n)) return false;
		// .lanekit v2 says so EXPLICITLY. The old rule below — "spawnable iff the turn letter is
		// blank" — is what made every exported through lane unspawnable: the exporter omitted
		// `turn`, WorldBaker defaulted a "through" lane to "S", and this test then rejected it.
		// A boolean the exporter states outright cannot fail that way; the inference stays only
		// for districts already baked against v1.
		if (r instanceof PathLaneRoute p && p.spawnableExplicit) {
			if (!p.spawnable) return false;
		} else {
			String turn = r.getTurn();
			if (turn != null && !turn.isEmpty()) return false;
		}
		Vector3 sp = r.entryPoint();
		if (sp == null) return false;
		if (center != null && maxDist > 0) {
			double dx = sp.getX() - center.getX(), dz = sp.getZ() - center.getZ();
			if (dx * dx + dz * dz > (double) maxDist * maxDist) return false;
		}
		return true;
	}

	private Vector3 randomPointInBox(Vector3 center, Vector3 size) {
		float hx = (float) size.getX() * 0.5f;
		float hz = (float) size.getZ() * 0.5f;
		return new Vector3(
				(float) center.getX() + GD.randfRange(-hx, hx),
				(float) center.getY(),
				(float) center.getZ() + GD.randfRange(-hz, hz));
	}
}

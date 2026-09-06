# CLAUDE.md — Codebase Reference

Third-person shooter experiment using **Godot 4.7** with the **godot-kotlin-jvm** plugin.
All game logic is written in **Java** (a few stubs in Kotlin). GDScript is not used.

---

## Build & Run

```bash
./gradlew build          # compile + generate the registrars Godot loads
```

Open `project.godot` with the **Godot Kotlin/JVM editor** (not the standard editor).
Plugin version: `0.17.1-4.7.2` — keep it equal to the editor binary's own
(`godot --version` → `4.7.2.stable.jvm.0.17.1`). JVM toolchain: **JDK 17**.

Scenes/resources reference scripts by their **source `.java` path**
(`res://src/main/java/com/openworld/.../X.java`). As of 0.17 that is the *only* way for a
project class: `.gdj` registration files are now emitted **only for registered classes coming
from external dependencies**, of which this project has none — so `gdj/` stays empty and is not
a fallback. Source of truth is always `src/main/java/` — never edit generated files.

---

## Vocabulary — there are TWO world concepts, not four (2026-09-05)

`district` is **retired as a mechanism**. It described a 504 m grid tile with neighbours, shared
edges, a `.seam.json` and a registry position, and the world is one continuous island now
(`WORLD_REBUILD_PLAN.md` step 3: *author one continuous world, let the export cut it* — with the
ground authored once there is no seam to verify). It survives only as a **place name**, the thing it
always meant to a player: `zone_id = "harbour"`.

| concept | what it is | where |
|---|---|---|
| **zone** | the ONE streaming unit, at any scale — a neighbourhood, a car park, a building interior. Scale-free and grid-free: it has a centre, load/unload radii, spawn configs and optional geometry. "A cell inside a cell" is just *a zone inside a zone*; it needs no new type. | `world.Zone`, `world.ZoneMarker`, `world.ZoneManager` (AutoLoad), and the Blender-side `region_` marker that bakes into one |
| **piece** | the authoring/bake unit: one `.blend` -> one baked `.tscn`. A build-pipeline word, not a gameplay one, and it collides with nothing. | `piece_registry`, `build_piece.sh`, `world/pieces/`, `PieceBinaryConverter` |

Renamed with it, so nothing keeps the old spelling: `WorldZone*` -> `Zone*` (213 references),
`DistrictBinaryConverter` -> `PieceBinaryConverter` (its `districtsDir` export is `piecesDir`),
`MultiDistrictStreamTestHost` -> `MultiZoneStreamTestHost`, `hosts/ConvertDistricts.tscn` ->
`hosts/ConvertPieces.tscn`, and the baked-output folder `world/districts/` -> `world/pieces/`.
**`IntersectionZone` is a different thing** (an `Area3D` marking a junction, `GROUP =
"intersection"`) and was deliberately left alone.

Prose below and in `blender/*.md` still says "district" where it is describing **history** — the
archived 6x6 grid world, `District_*.blend`, `check_seams.py`. That is left standing on purpose:
rewriting it would make the record lie about what was actually built. New writing uses **zone** and
**piece**.

## Source Layout

All code lives under the **`com.openworld`** root, organized **by domain/concern** (not layer-first).
Scripts are referenced from scenes by `.java` path (`res://src/main/java/com/openworld/.../X.java`);
`.gdj` is dependency-only under 0.17 and unused here. The two reorg scripts (`tools/reorg_stage1.py`, `tools/reorg_stage2.py`)
and `tools/REORG_PROGRESS.md` document the move; reuse their pattern for future moves.

```
src/main/java/com/openworld/
  character/      # character bodies + visuals + data: Character (gatherInput/applyInput loop),
                  #   Player, AICharacter, Health, AnimationController, CharacterInfo,
                  #   CharacterVisuals, MeshConfig, CharacterRagdoll, NameplateTarget (interface),
                  #   CharacterDriveState, CharacterReplication, Faction, FactionManager (AutoLoad),
                  #   FactionTable (relationship matrix Resource)
  ai/             # AI brain + FSM: AIController, AIState (base), AIBehaviorConfig, AILodLevel
    character/    #   7 behaviour states: Patrol/Chase/Attack/Search/RefillAmmo/Escort/Flee
    vehicle/      #   VehicleAIController (reserved for future vehicle AI states)
  control/        # controller framework + input: Controllable, Controller, CharacterController,
                  #   PlayerController, UserCommand (per-tick input snapshot), ModalInput
  camera/         # AI/Player/FPS/TPS/Vehicle CameraControllers, CameraMode, ControlRotation
  movement/character/  # MovementController, MovementState, MovementType, Stance, StanceName,
                  #   CombatState, JumpState, RollState
  weapon/         # WeaponController (slot inventory), WeaponItem (extends item.Pickup),
                  #   WeaponAction, WeaponType, WeaponSlotType, FirearmItem, Melee/Knife/Axe/Fist,
                  #   ThrowableItem, ProjectileItem, RocketProjectile, T1Projectile, Detonatable,
                  #   IconRegistry
  world/          # world types: HitInfo, HittableBody, SurfaceType, SpatialEntityGrid (AutoLoad),
                  #   WaterVolume (swim/float Area3D), WorldBounds (the logic wall)
    manager/      #   world-level singleton systems: Impact/Particle/Decal/Explosion/BulletTracer
  item/           # Pickup (RigidBody3D base for world pickups), AmmoRefill station
  carrier/vehicle/ # Vehicle, VehicleWheel, VehicleConfig, VehicleWeaponMode
  game/           # EventBus (AutoLoad signals), GameManager (PLAYING/PAUSED/GAME_OVER FSM),
                  #   PlayerRegistry (AutoLoad — live Player list for AI LOD)
    mission/      #   MissionInfo, MissionManager, MissionObjectiveType
  net/            # NetworkManager (AutoLoad RPC), NetMessageCodec, NetworkController,
                  #   VehicleNetworkController, snapshot interpolators, policies, NetStats, Vec3/Quat
    session/      #   PlayerSession, PersistentPlayerId
  ui/             # CharacterHUD, Crosshair, HUDManager, PauseMenu, RadialMenu, Feed,
                  #   Nameplate (generic billboard, any NameplateTarget), WeaponSlotsUI/Item, …
  util/           # ObjectPool, generic helpers
  debug/          # DebugHarness (temporary test-spawn harness); headless test stands —
                  #   DriveTestHost (vehicle physics soak, fixed timeline) and
                  #   HandlingTestHost + ScriptedInputController (step-by-step handling
                  #   cases, each with its control — hosts/HandlingTest.tscn)

src/main/resources/com/openworld/  # .tscn/.tres (internal layout NOT yet remapped to new java pkgs)
  character/Character.tscn, Player.tscn, AICharacter.tscn
  weapon/AR4.tscn, PI52.tscn, …    world/World.tscn, WorldSystems.tscn    ui/…
src/test/java/com/openworld/net/   # headless unit tests for the engine-free net logic
```

> AutoLoads (`project.godot`): `EventBus`, `GameManager` (`game`), `MissionManager`
> (`game.mission`), `NetworkManager` (`net`), `PlayerRegistry` (`game`), `SpatialEntityGrid`
> (`world`), `FactionManager` (`character`), `ZoneManager` (`world`),
> `StimulusManager` (`world`).

---

## Core Architecture Pattern

### UserCommand loop

Every character (player or AI) runs the same two-step cycle each physics frame. The "brain"
is a separate `Controller` (`com.openworld.control`) attached to the body via the `Controllable`
interface — not a `Character` subclass override:

```
Character._physicsProcess(delta)
    1. command = controller.gatherInput(delta)   ← per-body Controller provides the source
    2. applyInput(command, delta)                ← base class applies to shared state
```

`UserCommand` (`com.openworld.control`) is a plain struct holding all per-tick intent:
`movementDirection`, `movementType`, `wantCombat`, `fire`, `reload`, `jump`,
`desiredStance`, `desiredWeapon`, `aimTargetPosition`, `tick`/`sequenceNumber`.

- **`PlayerController`** polls the `Input` singleton (keyboard/mouse).
- **`AIController`** runs the AI FSM and writes decisions into the command.
- **`NetworkController`** (`com.openworld.net`) injects the host-broadcast snapshot on
  non-authority peers — the interchangeable third source the pattern was built for.

The `tick`/`sequenceNumber` counter makes commands totally ordered for replay/reconciliation.

### Scene Inheritance

```
CharacterBody3D (Character.tscn)
    shared subtree: Health, WeaponController, AnimationController,
                    MovementController, ragdoll skeleton, stances
    ├── Player.tscn       — adds: PlayerController, camera, HUD wiring, AimStayTimer
    └── AICharacter.tscn  — adds: AIController, AICameraController, NavigationAgent3D, SightRay
```

### Camera Hierarchy (both characters)

```
CameraController (Node3D, top-level)
  └── Yaw (Node3D)
        └── Pitch (Node3D)
              └── Pivot (Node3D)
                    └── SpringArm3D
                          └── Camera3D
                                ├── AimRay (RayCast3D)   ← fire direction
                                └── SightRay (RayCast3D) ← LoS only (AICharacter)
```

`PlayerCameraController._input` accumulates `InputEventMouseMotion.getRelative()` deltas into
`pendingYaw / pendingPitch` on every mouse event; `gatherLookInput` consumes and resets them
each physics tick. This captures every mouse event between physics steps rather than sampling
only the last velocity (`getLastMouseVelocity`), which dropped intermediate events.
`AICameraController.gatherLookInput` → derives yaw/pitch delta from `aimTarget` world position.
(`PlayerCameraController` is the on-foot TPS/FPS controller; `TPSCameraController` /
`FPSCameraController` are its view sub-types. All live in `com.openworld.camera`.)

Recoil is stored as `recoilPitch / recoilYaw` on `CameraController` and decays via
`GD.lerp(…, 0, recoilRecoverySpeed * delta)` each frame — fully separate from the
mouse-intent `pitch/yaw` so recovery never fights aim. `recoilPitch` is **subtracted** per shot
(`recoilPitch -= pitchKick`) because negative pitch = look up in Godot's convention.
`recoilRecoverySpeed = 8.0` gives a snappy per-shot kick that clears in ~0.3 s; sustained fire
builds a learnable upward drift (~1.7° at full spray) rather than a persistent offset.

---

## Performance Foundation (Part D)

Drop-in accelerators that change nothing visually. Each degrades gracefully if its AutoLoad is
absent (test scenes), so they are safe to rely on but never required for correctness.

### Spatial partitioning — SpatialEntityGrid (`com.openworld.world`, AutoLoad, D1)

Uniform XZ spatial hash (`cellSize` exported, 50 m). `Character` and `Vehicle` (both in the
"characters" group) `register()` in `_ready()`, re-bucket via a throttled `updateSpatialCell()`
(0.25 s) called at the **top of `_physicsProcess`, before the non-authority early return** — so
puppet bodies (e.g. remote players on the host) keep their cell current too — and `unregister()`
in `_exitTree()`. `AICharacter.discoverTarget()` calls `queryRadius(pos, detectionRange)` instead of
`getNodesInGroup("characters")`, falling back to the group scan when `SpatialEntityGrid.get()` is
null. Reached via a JVM-static `get()`; all maps + the static are cleared in `_exitTree()`
(leak discipline).

### AI level-of-detail — AILodLevel (`com.openworld.ai`, D2)

`AICharacter` carries an `AILodLevel { ACTIVE, PASSIVE, FROZEN }` set every 2 s from
`nearestPlayerDist()` (which now iterates `PlayerRegistry`, not the group): `< 80 m ACTIVE`,
`80–200 m PASSIVE`, `> 200 m FROZEN`.
- **FROZEN** — `AICharacter._physicsProcess` returns before `super` (whole FSM + animation tick
  skipped). MovementController, a separate node, still decelerates the body to rest.
- **PASSIVE** — `AIController.gatherInput` returns a hold-heading command (no NavAgent / FSM /
  aim), and `AnimationController._physicsProcess` skips **all AnimationTree writes** when
  `getLodLevel() != ACTIVE` — those JVM-bridge calls are the dominant per-AI cost, so this is the
  real mid-range win.
- Returning to ACTIVE from any non-ACTIVE tier clears stale nav/search state.
`isLodFrozen()` is kept as a back-compat shorthand for `lodLevel == FROZEN`.

### Faction relationships — FactionManager / FactionTable (`com.openworld.character`, D3)

`Faction.areHostile()` is a **thin delegate** to the `FactionManager` AutoLoad (registered via
`Faction.setRegistry`), so **all call-sites stay unchanged** and there is no duplicated rule.
`FactionManager.areHostile()` is the single owner of the logic: NEUTRAL is never hostile → an
explicit `FactionTable` entry wins (`HOSTILE/DESPISE` → hostile) → otherwise the inherent default
(same faction allied, different factions hostile). The table (`DefaultFactions.tres`, a flat
`Dictionary<String,String>` keyed `"a>b"`) only needs **overrides** of that default — it ships just
the editable `player↔enemy = HOSTILE` rows. `setRelationship(a,b,rel)` flips a relationship at
runtime (mission betrayals; required before Part F). With no registry loaded (engine-free tests)
`Faction.areHostile()` returns false.

#### Multi-faction & runtime changes (high-level — details will firm up with Part F mission state)

Factions are arbitrary strings on `CharacterInfo.faction`, resolved fresh every target scan, so any
number of parties works with **no code change** — author a `FactionTable` (`.tres`) with only the
pairs that *differ* from the default (different faction ⇒ hostile, same ⇒ allied, `"neutral"` ⇒
never fights). Two runtime levers, both **host-authoritative and replicated** over the world-event
seam (`WORLD_EVENT_FACTION_*`):
- `FactionManager.setRelationship(a, b, rel)` — flip a whole party relationship (the betrayal beat).
- `Character.setFaction(s)` — swap one character's allegiance (e.g. a cornered NPC turning hostile).

Use these setters, **not** a raw `characterInfo.faction = …` write, so the change syncs to clients
(and rides the late-join baseline). **Lifetime:** the live table is a `duplicate()` of the shipped
`.tres`, and `FactionManager` is an AutoLoad, so a flip persists across scenes/missions until
`FactionManager.reset()` restores defaults (already called on full restart in
`GameManager.restartLevel`; call it at mission end for per-mission scope — auto-scoping belongs with
Part F). Behaviours that *react* to factions (a bystander fleeing when a fight erupts, corner
detection that triggers a swap) are AI-perception features not built yet — they'll land with Part E2
`StimulusManager` / the AI FSM.

---

## Open World Simulation (Part E)

### Zone streaming — ZoneManager / Zone / SpawnPool (`com.openworld.world`, E1)

As a player walks toward a populated area an AI group streams in; walking away streams it back
out — no scene stutter, no O(n) tree scans, host-authoritative + replicated spawns. Five pieces:

- **`Zone`** (`@Script extends Resource`) — placeholder-AABB zone data: `zoneId`,
  `size` (full XZ extents of the spawn box; *center is the marker's world position*),
  `loadRadius`/`unloadRadius` (hysteresis — unload **>** load avoids boundary flicker), nullable
  `geometry` (PackedScene, cosmetic), and two collections built with class tokens —
  `VariantArray<SpawnConfig> spawnConfigs` (ambient groups) and
  `VariantArray<NamedCharacterConfig> namedCharacters` (story AI with stable ids for Part F).
- **`SpawnConfig`** — `faction`, nullable `behaviorConfig` (else AICharacter `DEFAULTS`), `count`,
  `weaponScenePath` (AR4 default). Ambient AI share the `AICharacter.tscn` archetype.
- **`NamedCharacterConfig`** — stable `characterId`, `displayName`, `faction`, nullable `scene`
  (else AICharacter.tscn), `behaviorConfig`, `weaponScenePath`, `offset` (relative to marker).
- **`ZoneMarker`** (`@Script extends Node3D`) — the inspector-friendly in-scene anchor
  (a `Resource` AutoLoad can't take an inspector-assigned `.tres`, and a marker is positioned by
  dragging). Holds `@Export Zone zone`; **its global position is the zone center**. Registers
  with the manager in `_ready()`, deregisters in `_exitTree()` (the same register-with-AutoLoad
  idiom `Character` uses with `SpatialEntityGrid`).
- **`SpawnPool`** — plain Java helper (not an AutoLoad, not `util/ObjectPool` which throws on
  exhaustion and isn't tree-aware), owned by the manager. `acquire()` polls a `Deque<AICharacter>`
  (recycling a detached body, validated by `isInstanceValid`) or instantiates fresh;
  `wasLastAcquireRecycled()` lets load skip re-equipping an already-armed recycled body;
  `release(ai)` removes from tree + enqueues up to `poolCapacity`, else `queueFree`. **Only healthy
  bodies are pooled** — dead AI follow the normal death/ragdoll→free flow, so a recycled body never
  needs un-ragdolling.
- **`ZoneManager`** (`@Script extends Node`, AutoLoad) — mirrors `SpatialEntityGrid`'s
  shape (JVM-static `instance`/`get()`, `_exitTree()` frees geometry + clears maps + `pool.clear()`
  for leak discipline). Throttled tick (`evalInterval`, 0.5 s) over registered markers computes
  nearest-player XZ distance via `PlayerRegistry.getPlayers()` (O(playerCount)); `< loadRadius` →
  `load`, `> unloadRadius` → `unload`.

**Sizing a zone (radii are center-relative, NOT edge-relative).** Both `loadRadius` and
`unloadRadius` are measured from the **marker (zone center)** and are **fully independent of
`size`** (the spawn box). So the unload trigger can and *should* be much larger than the box — a
player stepping a few metres past the box edge does **not** unload (you'd have to reach
`unloadRadius` from the center). Defaults: `size = (60,10,60)` (30 m half-extent),
`loadRadius = 200`, `unloadRadius = 350` — unload only fires 350 m from center. Recommended
relationship (`halfExtent = max(size.x, size.z)/2`):
`unloadRadius > loadRadius > halfExtent`, e.g. `loadRadius ≈ halfExtent + pre-spawn lead (~150 m)`
and `unloadRadius ≈ loadRadius + hysteresis margin (~150 m)`. `ZoneManager.warnIfMisSized`
(debug-gated) logs once at registration when a `.tres` violates this (the cause of "everything
unloads the moment I step out" — a too-small `unloadRadius`).

**Authority:** AI spawn/despawn is host-only — geometry is instanced on every peer but the spawn
work is skipped on a non-server client (`net.isNetworked() && !net.isServer()`); clients receive
the bodies through the existing `announceSpawn → MSG_SPAWN → GameManager.spawnReplicatedCharacter`
path, and late-joiners via `sendBaselineSpawns`. Unload calls `announceDespawn(characterId)`
before pooling/freeing.

**Incremental streaming pipeline (the district-border anti-freeze rework):** a zone crossing used
to be one synchronous `load()` — `GD.load`-parse a 7–19 MB district `.tscn` on the main thread,
instantiate ~1600 nodes, tree-enter 500+ static bodies + a `NavigationRegion3D`, then spawn every
AI/vehicle, all in a single physics frame. Streaming is now a per-marker **task state machine**
(`StreamTask` in `ZoneManager`), processed every physics frame under a time budget
(`streamBudgetMs`, exported, 4 ms, ≥1 step of progress per frame so tasks can't stall):
`GEO_REQUEST → GEO_WAIT` (PackedScene parsed on **engine worker threads** via
`ResourceLoader.loadThreadedRequest`; main thread only polls) `→ GEO_INSTANTIATE` (one frame:
instantiate off-tree — node construction only — then strip children) `→ GEO_ENTER` (children
re-enter the tree a budget-slice per frame — tree entry is where physics/render registration
happens; a `NavigationRegion3D` gets a frame alone for its nav-map sync spike) `→ SPAWN`
(AI/named/vehicle spawns drained as work items — the formerly-deferred "frame-spread spawning",
now done). Unload is likewise batched: `FREE_BODIES → FREE_GEO` (per-child detach+free instead of
one ~1600-node `queueFree`). Rules that fall out of this: a marker with an in-flight task is in
neither `loaded` nor eligible for a second task; the LOD-low placeholder stays up until full
detail is **completely** entered (no visual hole) and returns only when the last child is freed;
a load whose player retreats past `unloadRadius` mid-stream is **cancelled** (partial content
torn down synchronously; off-tree staged children are explicitly freed — they'd leak otherwise,
same in `detectSceneReload`/`_exitTree`). Synchronous teardown still exists for marker-exit /
scene-reload / AutoLoad-exit (`unloadImmediate`/`teardownZone`). `maxLoadsPerTick` (now 2) only
caps how many *threaded parses* start per eval tick — main-thread work is always serialized to
one zone per frame. The remaining single-frame cost is `instantiate()` of the whole district
(~1600 node constructions, no registration); if that ever reads as a hitch on Steam Deck the next
lever is baking districts as sub-chunk scenes, not shrinking the budget.

**Binary district scenes:** `resolveGeometryPath` prefers a sibling `.scn` over the wired
`geometry_path` `.tscn` when it exists — the baked districts are multi-MB *text* scenes whose
parse dominates stream-in time even on a worker thread. `PieceBinaryConverter`
(`hosts/ConvertPieces.tscn`, one-shot batch job in the `WorldBaker` idiom, mtime-skips
unchanged files) resaves them all; `blender/tools/build_piece.sh` runs it automatically as its final step
(so a fresh bake is never shadowed by a stale `.scn`) — re-run it manually only after baking a
district by hand. `.scn` files are derived artifacts (delete-and-regenerate safe); the `.tscn`
stays the source of truth. No master re-bake or `geometry_path` edit is needed for the preference
to kick in.

**District authoring seam (see `blender/AUTHORING_GUIDE.md`):** a district rebuild
regenerates the `.blend` **in place** — only the procedural collections (`STREET`, `MARKERS`,
`STREET_LOD_LOW`, `ROADS_SRC`) are wiped; `MANUAL` (hand-authored content, exported + baked) and
`NEIGHBOR_REF` (read-only library-linked neighbour/master context from `tools/link_neighbors.py`,
dropped by every export) survive. `build_piece.sh District_<theme>_<gx>_<gy>` (stem form) is the
bake-only loop for hand-edited blends — it skips the regen entirely. Because linked libraries can
carry same-named collections (several linked `STREET`s), every collection lookup in the Blender
pipeline is local-only (`library is None`) — keep new lookups that way.

**What streams vs. what's static (a common confusion):** only two things are added on load and
removed on unload — the **AI bodies** and the zone's **`geometry` PackedScene** (instanced as a
marker child, `queueFree`d on unload). Anything authored directly into the `ZoneMarker` *scene*
(the debug box from `showDebugVolume`, or any mesh you drop under the marker node) is **static scene
content — it never streams**; it is the persistent zone *footprint/outline*. To make a mesh stream
in/out, assign it to the Zone's **`geometry`** field, not as a marker child (a Blender-exported
zone-chunk `.tscn`, same convention every district piece's `geometry_path` already uses). Zones also
do **not** carry their own navigation — AI use the level's `NavigationRegion3D`; nav is a
parent/world concern.

**Body recycling is OFF by default (`recycleBodies`, EXPERIMENTAL).** Reusing a full character body
subtree (detach via `removeChild`, re-attach via `addChild`) is **unsafe** in godot-kotlin-jvm: the
body carries a `top_level` camera (`TPSCameraController.setAsTopLevel`), a muzzle-flash
`GPUParticles3D`, and a nameplate `SubViewport`, and re-attaching that subtree leaves them
half-initialised — `get_global_transform "not inside tree"` / `particles is null` errors, then a
native use-after-free **segfault on zone enter** (no AI death required). So unload frees and
load instantiates fresh — correct, and no longer a stutter concern: spawns are frame-spread by the
streaming pipeline's SPAWN phase (see above), which was always the proper perf answer, not body
reuse. The `SpawnPool` + `activateForSpawn` reset path is kept
behind the flag for future hardening only. When recycling *is* on, only `isAlive() && !isDead()`
bodies are pooled (a dead body is ragdolled and `activateForSpawn` does not un-ragdoll it).

**The manager is an AutoLoad, so it survives `reloadCurrentScene()`/restart** — and pooled bodies are
parentless (held only by the deque, not in the scene tree), so a scene reload does **not** free them.
`detectSceneReload()` (top of `_physicsProcess`, compares the current-scene instance id) drops
`loaded` + frees the pool on any scene swap, so a restart never resurrects a body from the old scene
into the new one (this was a reproducible restart crash).

**Despawn safety — dangling references:** streaming despawns bodies that other systems may still
reference between target scans. `AICharacter.validateCurrentTarget()` (run every active frame, before
`super._physicsProcess`) drops `currentTarget` + its bone caches the moment the target is freed or
pulled out of the tree — without it, the FSM dereferences an out-of-tree node (`get_global_transform`
/ `look_at` "Node not inside tree") and segfaults once it is freed. `activateForSpawn` also clears the
recycled body's carried-over camera aim target and escort reference for the same reason.

**Pooled-reuse reset constraint (critical):** a pooled body's `_ready()` does **not** re-run on
tree re-entry, so reuse must re-initialize explicitly. `load()` calls
`AICharacter.activateForSpawn(worldPos)` **after `addChild`**, which sets global position,
**re-captures `spawnPosition`** (the patrol anchor, otherwise only set in `_ready()`), clears
`isDead` + all sensor caches, resets LOD + staggered timers, `Health.resetFull()`, re-registers the
`SpatialEntityGrid`, and calls `AIController.resetState()` (FSM back to `initialState()`, all timers
+ last-known targets cleared). Without the re-anchor a recycled AI would patrol around its *previous*
spawn point.

**Debug visualization + walk-test setup:** `ZoneManager.debugLog` (exported, on) prints each
load/unload decision, an approach-distance line while a player is near, and per-load recycled-vs-fresh
+ pool-idle counts. `ZoneMarker.showDebugVolume` (exported, on) builds at runtime a translucent
box (the spawn volume, `zone.size`) plus flat rings at `loadRadius`/`unloadRadius`; the box tints
**green while streamed in, cyan while idle** (driven by `setLoadedVisual` from the manager) — so you
can see a zone and walk into it. Both are pure debug aids, off via their export flags for shipping.
The `DebugHarness` **F12** key drops a code-built `Zone`/`ZoneMarker` in front of the
nearest player (`spawnDebugZone()`, no `.tscn`/`.tres` needed) if you want a quick zone to walk-test
without editing a scene — the standalone example zone scene this used to point at (`zones/DebugZone
.tscn`/`.tres`, `zones/DebugZoneGeometry.tscn`) was retired once the real 36-district open world
(`assets/world_source/`, `hosts/WorldMaster.tscn`) existed to walk-test against instead.

### Ambient traffic & the road graph (roads-v2)

**Generated data, not runtime inference.** `assets/world_source/lib/road_graph.py` (pure Python,
`python3 lib/road_graph.py` self-tests) turns an abstract centerline graph — junction nodes +
polyline edges with `lanes`/`oneway`/`class` — into everything the runtime consumes: per-direction
per-lane offset routes (`<edge>_<F|R><lane>`, keep-left, trimmed at junction stop lines), bezier
**turn connectors** (`c<node>_<in>_<turn>`, short names — Blender's 63-char object-name cap) carrying
`turn` (L/S/R) + `approach` (N/E/S/W) metas, and `intersection_` markers. Turning is a **data
lookup**: each chained lane's `_0` empty stamps `next_routes` (its connectors) + straight-biased
`next_weights` (0.6/0.2/0.2); `LaneGraph` endpoint clustering is only the legacy fallback (now
straightness-biased via `VehicleRoute.start/endTangentXZ`). Weighted choice = `util/WeightedPick`
(engine-free, unit-tested; malformed weights degrade to uniform, never throw). Keep-left legality:
1-lane approach → L/S/R; ≥2 lanes → curb lane (idx 0) L+S, median (n−1) R+S, middle S only; target
lane clamps by index — that clamp is the whole mixed-lane-count answer. `assemble.lay_road_graph()`
emits it all (raises on any name collision/overflow — Blender auto-rename would corrupt the baker's
name grouping).

**Graph sources:** master backbone — `build_world.backbone_graph()` (junction-split, 2 lanes per
direction, 336 lanes + 568 connectors + 49 junctions; `radius_fn` forces stop lines to the 21 m
paved footprint) + `backbone_deck()`, an always-resident **collision-only deck** under every
arterial (without it, cars outside streamed districts fall into the void — PLATEAU districts have
no always-resident ground) in the same exported `ARTDECK` collection. Authored in the master
blend (debuggable there), NOT runtime Java. **There is deliberately no world-spanning safety
floor** (`build_world.safety_floor()`/`--with-floor` and the per-district
`add_ground_safety_plane()` were both removed outright) — a collision-only floor a
meter-plus below visual ground silently trapped `Character`/`Player` bodies with no recovery
path, since neither has any fall-out-of-world safety net (unlike vehicles, which
`ZoneManager.maintainTraffic` reclaims below `Y = -30`). Falling off a road or off the
ArtDeck now falls through, same as any other gap in authored ground — see
`AUTHORING_GUIDE.md` for the districts/void-cell design this replaced it with.

> **A SEABED IS NOT A SAFETY FLOOR** (island v3, 2026-08-30 — `blender/WORLD_REBUILD_PLAN.md`).
> `Island_base` has a `Seabed` sheet under the whole sea and it does not reopen the above. A safety
> floor is *invisible*, *collision-only* and sits a metre under the **visible ground**, so a body
> that falls through a hole lands somewhere it cannot see with no way back — which is why it was
> removed. A seabed is the terrain **continuing past the waterline**: visible, sloped, walkable in
> both directions, and it does not exist under the island at all (`island_v3_terrain.Terrain
> .surface` returns the land wherever there is land). Falling off a cliff onto land still falls.
> Two rules came with it, both cheap to get wrong: a ground sheet must be forced to **face up**
> (`recalc_face_normals` infers "out" from the shape, and an open sheet with no skirt came out
> entirely inverted — invisible from above AND unwalkable to Recast, while its collision proxy
> worked perfectly), and a solid-but-unwalkable surface takes the `-noped` marker (below) so it
> does not bake a 4 km walkable sheet under the sea.
>
> **The beach is the part that is easy to get wrong while fixing the fall-through.** The land stops
> 0.60 m above the water, so a sea floor starting at the waterline rings the island with a 0.80 m
> wall — swimmable *to*, impossible to climb (`MovementController.stepHeight` is 0.35 m), i.e.
> falling out of the world replaced by being locked out of it. `SHORE_Z` starts the floor 0.20 m
> **under the land**: one step down onto ~27 m of dry sand before the water. Measured on 16 shore
> rays: worst natural-coast step **0.206 m**, 0 samples with no surface.
> `blender/tools/check_island_water.py` is that layer's gate (sea-below-land, continuity, the beach
> step, cliff/quay classification, and the markers).
>
> **All water is ONE swim volume** (`water_sea`, baked to a `WaterVolume`), and one box can cover
> the sea, the bay and the lagoon because the land is above every part of it: a box whose TOP is the
> water line cannot touch a character standing on land. `Character` decides wade-vs-swim from the
> true depth under the body, so the beach wades and only real depth swims. **`WorldBaker.buildWater`
> used to build a bare `Area3D` on the default collision mask** — no script, so `setInWater` was
> never called, and mask 1 never sees a character body (they are on `CollisionLayers.CHARACTER`).
> Either half alone was fatal, and together they looked exactly like a swim feature that was never
> written; `SwimState` and the buoyancy spring had been complete since I1.
>
> **The world edge is a LOGIC WALL, not an invisible collider** — `world.WorldBounds`, baked from a
> `bounds_<id>` marker. Soft inward push inside `softMargin`, hard position clamp past the edge with
> only the OUTWARD velocity component cancelled (so swimming home is unresisted), and a `floorY`
> kill-Z that puts a body back at `PlayerSpawn`. A collider would also stop bullets, ragdolls and
> vehicles, would give a body sliding along it no "leaving the area" moment, and — the deciding one —
> could never recover a body that is ALREADY outside. **The wall stands inside the sea floor's own
> edge, not at the world square**: `SEABED_MARGIN` 288 m puts the floor at ±2304 and
> `BOUNDS_INSET` 144 m puts the wall at ±2160 — a 4.6 km sea with 202 m of water at the
> tightest heading and 498 m at the median, over 1 km only on the diagonals. Both extremes were
> walk-tested: the wall AT the world square read as a fence 700 m offshore, and a floor out at
> the ±3800 m budget read as 2.0 km of empty ocean. 3.5 km is not available — the land's own
> bounding box is 3.72 × 3.94 km, because the offshore airport reaches y = −1976 m. The *accurate*
per-district ground is PLATEAU terrain: originally imported via `extract_plateau.py --dem`
(CityGML `dem:TINRelief`) → `plateau_import.import_terrain`, which built a real sloped ground mesh
(visual + collision) and draped roads onto it — districts extracted without `--dem` have no
continuous ground and fall through in the gaps (no safety-floor catch anymore, see above). **That
extraction/import tooling was removed** once every PLATEAU-derived district/overlay/building asset
had already produced its permanent output `.blend` — see `AUTHORING_GUIDE.md` §2/§6. Terrain is
now hand-owned exactly like everything else; road authoring specifically is the
`road_kit_authoring` addon's point/port graph + `.lanekit.json` v2 sidecar (see "Ambient traffic &
the road graph" below), not the old `road_<name>` centerline/`.roads.json` pipeline that predates
it.

**Spawning:** region markers carry `traffic_count`/`traffic_route` → `WorldBaker.buildZone` builds a
`VehicleSpawnConfig`. `traffic_route` is a route-name **prefix** (`"art_"`, or `"<piece>__"` once a
sidecar exists — the master build flips the meta by checking for the sidecar, so re-run it after
authoring): `ZoneManager.findRoute(name, center, maxDist, index)` matches exact first, else
prefix-collects plain lanes (never turn connectors) whose entry is within `unloadRadius`,
round-robin by spawn index in name order — that spread IS the multi-lane spawn distribution.
**All lane lookups are registry reads, never scene-tree walks:** `VehicleRoute._ready/_exitTree`
register/deregister with a `TreeMap` on `ZoneManager` (the Character↔SpatialEntityGrid
idiom; sorted names make the prefix query ordered for free), and `entryPoint()` caches the first
marker position per tree entry. The old recursive whole-tree scans (two per spawn, tens of
thousands of JVM-bridge calls with a district streamed in) ran inside the 0.5 s `maintainTraffic`
tick and were the "periodic hitch in all movement" regression; `VehicleRoute.resolveRoute`/
`pickNextRoute` (every lane end) go through the same registry.
`maintainTraffic` reclaims: dead / route-finished / **fell-out** (Y < −30 — an off-road car
free-falls with unchanged XZ, so the range check alone never catches it) / out-of-range, then tops
back up (GTA disposable traffic). `debugLog` prints spawn/reclaim/status lines — "N cars, M moving,
K routed" is the headless-smoke signal (routed-but-0-moving = falling through missing ground;
route-finished churn = broken junction wiring).

**Junction discipline:** `CruiseState`'s curvature probe cannot see past the current route, so
`VehicleAIController` clamps throttle (`junctionThrottleScale`, 0.45) within `junctionSlowdown`
(18 m) of any chained lane end and while riding an L/R connector — without this cars enter 90°
turns at full cruise speed and fly off. Phase 2 (JunctionArbiter FCFS grant sets + timed signals
keyed on the baked `approach`/`turn`) and Phase 3 (highway ring + ramps + `speedLimit`) are next —
see PLAN.md "Roads & Traffic v2".

**Known noise:** instancing `Vehicle.tscn` from code logs a `CharacterInfo` ClassCastException
(the scene-embedded sub-resource's JVM script binds late, so the setter receives a plain
`Resource`) — harmless: every spawn path immediately overwrites `characterInfo` with a fresh
instance per the shared-sub-resource identity rule.

### Terrain & seam alignment (ground is NOT a flat plane)

Each district's real ground is a **DEM-derived heightfield** — originally imported via
`extract_plateau.py --dem` → `lib/plateau_import.py: import_terrain` (one continuous sloped mesh
per district, visual + collision in one; that extraction/import code has since been removed, see
`AUTHORING_GUIDE.md` §2/§6 — every district's terrain is now a permanent, hand-owned part of its
`.blend`, this just describes how it originally got its shape) — genuinely non-flat wherever DEM
data was extracted, not a flat square. What makes adjacent districts agree at their shared edge is
a **theme-elevation-step + taper system**, not literal shared/welded geometry:

- `lib/world_grid.py`'s `THEMES` dict assigns each of the 7 region themes a flat **baseline**
  elevation (harbor 0, city 2, resid 4, rural 10, mtn 40, snow 90, industry 0) — a coarse
  staircase across the whole map.
- The original import blended the real (sloped) terrain height toward that baseline within a
  border margin of every district edge (`plateau_import.seam_taper()`, no longer present but
  baked into every existing district's terrain), so the interior keeps real terrain shape but
  every edge lands at a known, neighbor-predictable value.
- A `.seam.json` sidecar per district (36 of the 39 district files have one) records each edge's world
  position, elevation, the neighbor's expected elevation, and route-name chaining;
  `tools/check_seams.py` (pure Python, no Blender needed) verifies two adjacent `.seam.json` files
  agree — this is the existing, already-automated seam-alignment QA step, run whenever
  neighboring districts change.

So cross-district alignment for elevation is a **solved, working system** — districts don't need
literal vertex-welded ground, they need matching boundary *values* (elevation, route endpoints),
which the taper + `.seam.json` + `check_seams.py` trio already enforces.

**Road authoring is now the POINT/PORT GRAPH** (`blender/ROAD_POINT_GRAPH.md`, the design of
record; `blender/addons/road_kit_authoring/point_*.py`). A road is an ordered chain of **road
points** — an Empty that is simultaneously a *station* (its own cross-section: lanes per direction,
median, kerbs, footways, structure) and a *port* (its typed `SEGMENT` / `JUNCTION` / `AUX` links).
A junction is a clique over `JUNCTION` links whose member points **are** the stop lines; a ramp is
an aux slot plus an `AUX` link. Every along-the-length change — lane drop, lane opening, one-way,
an acceleration lane with its taper — is just *"two stations that differ"*. The authored record is
a git-diffable `<stem>.roads.json`; the Empties are a **view** of it. Build emits per road run a
swept `__surface` carrier (a GN layer stack), `__edges` kerb/footway runs placed against the paved
**outline** so gores open by themselves, `__edges` kerb/footway/**barrier** runs per junction
corner too, a pad per junction, a paved **gore strip** per ramp, the terrain cut, and split
`-colonly` road/footway collision proxies. `blender/tools/check_roads.sh` is the one command
that runs the gate (18 checks, including a full-plugin pass that drives every operator, draws every panel, and asserts every operator is reachable from a button). `Author ▸ Learn ▸ Add Sample Network` builds a worked example of all four link types; the step-by-step guide is in the addon's `README.md`.
**The Empty's transform IS the road frame** — position is the station, **local +Y is travel
direction** (points draw as `ARROWS` so that axis is visible; `SINGLE_ARROW` draws along +Z and
showed the wrong one), roll is banking, and `tangent_mode = MANUAL` makes the rotation drive the
curve, with `handle_in`/`handle_out` in metres (0 = the chord). **Rotating a point IS the bend
gesture — there is no mode to set first.** Points are *born facing their road* (`new_point` takes a
`facing`; `Extend Road`/`Insert Point` pass the chain direction — never via `face_matrix`, which
reads a `matrix_world` still identity on a just-created object and would move the station to the
world origin), and the tool stamps the facing it gave each one in `RKA_Point.auto_tangent` (derived
state, **not** in `.roads.json`). `read_point` then promotes an AUTO point whose facing has left
that baseline to MANUAL — a *read-side* derivation, so it takes effect in the overlay, the gate,
Build and the export at once with no write and no handler. The baseline is what separates a
rotation from a **drag** (a translate changes the chain tangent while leaving the rotation alone,
so recomputing-and-comparing would falsely promote every dragged point). `point_ops.sync_facings()`
is the write half — promotion first, *then* re-face what the tool still owns — run by Build, by the
live rebuild, and by the `Follow Road (Auto)` button; `point_profile.chain_facings()` is the one
owner of "which way does this station face". The overlay draws the **resolved centreline**
(`point_profile.centreline_runs()`, resample-only so it is cheap enough for a per-frame draw
handler), so a rotation reshapes the road live without any rebuild — `rka_live_rebuild` (off by
default) is only about the *mesh*. **Straightness is measured, never authored** (`road_points.segment_bend_deg`) — there is no straight/curved flag to keep in sync. The
`Connections` panel lists the active point's links with derived span / straight-vs-bend / taper
verdict (the taper number comes from the gate's own `taper_min_length`, so the two cannot
disagree), and `Connect Selected` is anchored on the **active** point — `AUX` is directed
(mainline → ramp), so `selected_objects` order was a coin flip. The two previous models are gone: the mesh-graph `graph_*.py` is
archived under `legacy_graph/` (not imported) and the per-piece generators (`ops_placement.py`,
`ops_intersection.py`, `ops_segment.py`, …) were **deleted** — see `legacy_graph/README.md`.
District_industry_5_1's hand-authored `MANUAL` collection predates this and is still valid baked
geometry; it is no longer the authoring reference.

**Style, markings and the Path3D preview (2026-08-27, `blender/ROAD_STYLE_AND_PATH_PREVIEW.md`).**
Three things landed together; the doc is the design of record and its §7 is what actually shipped.

- **The exported Path3D IS the lane, now.** `WorldBaker` builds every `Curve3D` an ambient car
  drives from the `.lanekit.json` `curve` block, and `point_export` used to refit those handles
  from the chord through the neighbouring stations — discarding the authored tangents
  (`station_axis`, the R-key bend) the road is actually swept from. At an open end that chord sat
  **70°** off the true heading: measured **22.57 m** of error on the addon's own sample network,
  with a green gate and perfect geometry. Handles now come from the lane's own sampled tangent,
  length least-squares-fitted, subdividing only where a cubic cannot follow to 0.05 m → **0.09 m**
  worst, with *fewer* control points. Two rules fell out and both are load-bearing: the fit error
  must be measured **curve-to-lane**, not lane-to-curve (a cubic that bulges wide and comes back
  passes near every sample while sitting a lane's width off the road), and the split must
  **bisect** (splitting at the worst sample slivers a span whose real problem is a bad handle).
- **`Preview ▸ Geometry` draws the Path3D by default**, not the lane polyline — the two are
  different objects, and drawing the wrong one is how the above survived. `Both` overlays them with
  orange rungs where they part; `path_deviation` is the matching gate finding, from the same
  function, so picture and finding cannot disagree.
- **One material registry.** `point_build`'s parallel `rka_*` materials are gone; roads now use
  `kit_common.MATS` like every other builder — which had carried `M_LineW`/`M_LineY`, described in
  its own source as lane lines, with **no user at all**. `RoadData` gained a style slot per layer
  (a datablock NAME, so `.roads.json` round-trips; blank = default; a missing name falls back AND
  warns), `median_style` moved from the point to the road as an enum
  (`NONE`/`PAINT_DOUBLE_Y`/`RAISED`/`WALL`), and **lane markings are built at last** from
  `lane_profile.marking_runs`, which had computed every painted boundary since the profile model
  landed and which nothing had ever swept. Dashes are cut in Python (the addon's own rule: Python
  owns the curve, GN only sweeps); paint is lifted `PAINT_Z_BIAS` and is deliberately **not** in
  the collision proxies.
- **A profile asset replaces a layer's parametric band with a swept section the artist modelled**
  (`GN_PointProfile`, `point_style`, `assets/world_source/kit/road_kit.blend` via
  `tools/build_road_kit.py`, library-linked). SWEPT, never tiled — rigid pieces round a 9 m corner
  sit ~12.7° apart and open a real ~7.8 cm gap at every joint, which is what retired the previous
  model's asset style; tiling is for lamp posts, and that is `GN_PointAssets`. Three measured
  gotchas: `Curve to Mesh` **drops the profile's materials** (zero slots, even from a two-material
  section — so the addon reads it off the asset), `obj.bound_box` is **stale on a freshly linked
  object** (reported a 0.32 m kerb as 2.32 m — read the curve data), and an asset layer has no
  `WidthAttr` so it **bypassed `layer_has_content`** until `ASSET_REQUIRE` gave it the same gate
  (a named barrier was building along an at-grade pedestrian street).
- **The `JCT_*` Empty is a handle and now sits where the junction is.** It was written once at
  `Make Intersection` and never re-derived, while `JunctionSolve.centre` recomputes the same
  centroid every solve — two owners, one frozen: dragging one mouth 42 m left the grip **10.44 m**
  off, so G and R pivoted on a point with nothing there (and `Auto Setback`, which moves every
  unlocked mouth, caused it immediately). It now follows the centre at Build, at Auto Setback and
  on live settle, moving no mouth. Z rotation is **unlocked** (scale and out-of-plane rotation stay
  locked, which is what the original lock was actually right about) so a whole crossing can be
  turned; `stamp_baseline` moved to the point's **parent frame** so that gesture promotes **0**
  arms to MANUAL instead of all of them, while a hand rotation of one arm still promotes exactly
  that one.

**One owner per derived fact — the four rules the 2026-08-25 fixes added** (`ROAD_POINT_GRAPH.md`
§8f has the full write-up; each of these was a user report):

- **Direction has ONE owner: `point_model.station_axis`.** The carriageway honoured a MANUAL
  tangent while `point_solve.mouth_axis` and `point_validate._axis` each re-derived the direction
  from the *neighbour's position* — so rotating an intersection mouth bent its street and left the
  pad exactly where it was. Both now delegate. Rotating a mouth turns its cap, its two fillets and
  its turn paths.
- **A pad always tessellates.** `point_solve.pad_triangles` fans from the ring's **kernel point**
  (`fan_origin`, found by pushing the apex inside the edges it is outside of) and `ear_clip`s when
  no kernel exists; `build_pad` sweeps exactly that. `pad_not_star_shaped` is a **WARN**, not an
  ERROR — a 2 cm fold from a hand-drag used to refuse the whole build and name as its remedy an
  `Auto Setback` that then reported "moved 0". Never let a hand-drag be a build failure.
- **A ramp is the aux slot's CONTINUATION, not a lane beyond it.** `point_profile.aux_edge_offset`
  returns the aux slot's **through-lane-side** edge (the gore line), so `lanes_fwd = 3,
  aux_fwd = 1` is a four-lane carriageway whose outermost lane leaves. `point_solve.ramp_target`
  is the single owner of where the mouth belongs — and `Align Ramp To Aux` also **faces** it down
  the mainline (`MANUAL`), because two bands cut on different planes touch at one vertex and open
  from the next. Divergence is authored at the ramp's *next* point. `solve_gore` paves the wedge
  between the two roads' own paved edges, from where the signed gap changes sign to a 4 m nose.
  `check_tapers` exempts the station that owns the `AUX` link: a **departing** lane is not a
  **merging** lane and needs no merge taper.
- **Reachability is not geometry, and had no eye.** An `AUX` link exported as *nothing*: the ramp
  lane had no predecessor, so no ambient car could ever reach a ramp anywhere in the world, with a
  green gate and perfect geometry. `point_export.wire_ramps` emits the edge (directed by the ramp
  point's role) and `_aux_handoffs` **ends the exit lane at its gore** so the successor is within
  `CHAIN_TOL`. `point_preview` (the **Preview** panel) draws the *exported* lane graph — directed
  lanes, `next` edges, agents walking it on the exported weights — and reports `broken` /
  `open_end` / `unreached` / `ramp_orphans`. When adding anything to the lane graph, check it
  there: a build being green says nothing about whether traffic can get to it.

**Five more owners, from the 2026-08-26 follow-up** (`ROAD_POINT_GRAPH.md` §8g; same shape as §8f
— a rule right for the case in front of you, applied to one nobody had looked at):

- **An exit is a BLOCK of aux slots, not one slot.** `point_profile.aux_block` returns the whole
  run of same-direction aux slots and the edge facing the through lanes; `aux_edge_offset` is that
  edge. Anchoring on the *outermost* slot was right at `aux_fwd = 1` and put a two-lane ramp half
  on the carriageway at `aux_fwd = 2`.
- **The merge taper is the metric standard × the road's `taper_factor`.** `TAPER_LINEAR_ABOVE` is
  70 km/h (it was 60, which over-demanded by half across the whole 60–70 band). `taper_factor`
  (`ROAD_FIELDS`, default 1.0) exists because **the world is not 1:1** — shortening a taper for a
  compressed map is a visible authored decision on the road, never a constant bent in the checker.
- **A barrier's HEIGHT is authored, its PLACEMENT is derived.** `RoadData.barrier_height` (0 =
  none) × the rule in `solve_road`: fenced along the whole length when `ped_access` is off, and
  only where `delta >= BARRIER_MIN_DELTA` when it is on. It is a layer in `edge_spec()` on the same
  `deck` node group as the kerb, so it rides the **outline** — which is why it opens across a gore
  and closes past the nose with no ramp-specific code at all.
- **A junction corner IS an edge run.** `point_solve.junction_corners` emits one `Corner` per real
  corner from `intersection_kit.build_junction_curb_segments` (the same curve the pad boundary is
  rounded with) and `point_build.build_junction_edges` sweeps it with the ordinary `edge_spec()`.
  Before this, every crossing in the world had four missing pavement corners.
- **`point_edges.Band.carries_edge` — a pad hands the furniture on, a gore does not.** A run must
  not suppress its kerb against a footprint that continues it (that gap was the missing corner
  pavement), but it MUST open across one that does not. A run is a member of both, so membership
  alone cannot tell them apart; keying on it left a barrier stub standing across the gore paint.
- **`intersection_kit.curb_edges(..., tail_length=)` anchors each arm's kerb ray on that arm's own
  `tail_center`, not the origin** (§8h). The origin-anchored ray passes through the cap only
  because a plain arm's tail centre is a multiple of its direction — an off-ray `tail_pos` (which
  is what `_PadArm` sets from the AUTHORED mouth) does not satisfy that, so a **rotated** mouth's
  corner left the cap ~50° out and its footway met the street in a notch. Opt-in by parameter, and
  byte-identical for any arm without `tail_pos`; `Arm.tail_center`'s docstring records the opposite
  scope limit, which was right for the model that wrote it and wrong for this one.
- **`point_edges.covered(..., outward=)` is DIRECTIONAL: "does the pavement continue past this
  line", not "is there asphalt within 0.6 m".** Where a ramp leaves along the mainline's outer
  edge, both edges are the outer boundary of the same pavement, and the undirected `NEAR_PAD` slop
  had each band suppress the OTHER's parapet — 11 m of unwalled edge at the top of a 14 m drop.
  The probe is taken `NEAR_PAD` **outboard** and must land strictly inside another band.
  `measure_on_asphalt` uses the same tolerance for the same reason: it measures standing ON asphalt.
- **A directional `covered` asks TWO questions, and a run's END is CLIPPED, not rounded (§8h.4).**
  Two user reports, opposite ends of one ramp, one root cause — start/end decided per 4 m sample
  about features metres across. (a) The outboard probe can step clean OVER a band narrower than
  `NEAR_PAD`: at a mouth the mainline's outer edge lies 0.5 m inside the ramp's band, the probe
  landed 3 cm past it, and both parallel edges kept a wall — "one extra wall at the ramp
  connection". So `covered` now also suppresses when the point ITSELF is `BURIED_TOL` (5 cm) inside
  another band. That is a tolerance for *exactly on*, not a margin — an edge on another band's
  boundary is the shared outer boundary and keeps its wall, which is §8h.2's case. (b)
  `point_edges.open_runs` returns `Run(i0, i1, head, tail)` — still `(i0, i1)` when indexed — whose
  `head`/`tail` are **bisected onto the covering band's boundary** (`_clip_end`), so a run ends at
  the mouth it hands over at and starts at the gore nose it must meet, instead of a sample either
  side. `sub_polyline` + `pe.run_values` emit points and attributes from one place so they cannot
  come out different lengths. Together these let the gore cap sit FLUSH on the strip's last pair
  (`GoreSolve.nose`) with both flank walls meeting it — three walls, one closed corner.
- **A GORE OWNS ITS OWN NOSE — neither flanking road can (§8h.3).** A gore is bare paint
  (`Band.carries_edge` False), so both flanking walls open across it: right along the join, where a
  wall would stand in the exit lane, and wrong at the wide end, where the two roads have parted and
  their walls restart `GORE_NOSE_WIDTH` apart with an open V between them at the tip of a viaduct.
  Neither road can fill it — the stretch is the other one's asphalt — so `point_solve._gore_nose`
  emits an ordinary `Corner` and `point_build.build_gore_edges` sweeps it with the ordinary
  `edge_spec()` (`GORE_*__edges_nose`). **What** it carries is still the roads': each end reads that
  road's own solved `rka_wall_h`/`rka_curb_h*`/`rka_walk_h*` and the run blends between them, so a
  fenced highway meeting a fenced ramp is a wall, an approach declaring a footway gets a kerbed
  island, and a pair declaring neither builds nothing. `GoreSolve.ped_access` is both flanks'
  answer, so the proxy is `-noped` between an expressway and its ramp and walkable between two
  streets. **Where** it sits is derived: one `GORE_STEP` past the nose on each road's own edge — the
  first sample outside the gore's polygon, i.e. exactly where `open_runs` lets that flank's wall
  resume. `point_build.edge_run_values`/`build_edge_run` are now the one owner of the per-vertex
  furniture arithmetic, shared by a road flank, a junction corner and this.

**And the gesture round, 2026-08-26** (`ROAD_POINT_GRAPH.md` §8i; six user reports). §8f–§8h were
all *one fact with two owners*; §8i is the other face — **a fact the artist was made to declare
that the model already knew**, or **an ordering the tool imposed because nobody had asked the
question from the other side**:

- **`Extend Road` works from EITHER end.** `_next_point_name` only ever hands out the next free
  index, so a new point was always born at the *tail* of the names however the artist got there:
  extending `..._p000` misfiled it at the far end of a road it starts, and (the head has no `prev`
  to take a chord from, so it fell back to its own `+Y` = the way the road already runs) placed it
  *forward*, back down the road. `chain_unlinked` on an untouched pair, on the next Build. Now the
  head grows away from the chain and `_renumber(..., at=0)` prepends; an interior point is refused
  by name.
- **Which point is the MAINLINE is a fact, not click order** (`point_ops.resolve_aux_pair`). `AUX`
  stays directed (mainline → ramp) but the gesture works from either end — an entrance ramp reads
  "ramp joins road", and insisting the mainline be active made every merge unauthorable with the
  button the panel offers. `Make Ramp` shares the resolution.
- **ONE ramp role.** `RAMP_ENTRY`/`RAMP_EXIT` fed exactly one decision (which way
  `point_export.wire_ramps` points the lane edge) while `point_solve` derived the same thing from
  the chain — and when the two disagreed the geometry was perfect, the gate green, and the traffic
  wired backwards. `point_model.ramp_is_entrance` is the one owner: the mouth's position in its own
  **run** plus which way the ramp's lanes run (`lanes_bwd = 1, lanes_fwd = 0` is ordinary, and its
  head is where cars come *out*). `road_runs` moved to `point_model` for this (a run is a fact about
  the chain and its links); `point_solve.road_runs` is an alias. The ramp edge is now **added to**
  the junction connectors rather than dropped when a lane has both — §8f.4's orphan again.
- **The taper is PER CARRIAGEWAY and only WITHIN a run.** Summing both sides doubled the demand
  (336 m where the standard asks 168) on the theory that two opposite lane drops compound; they do
  not — a merge is one driver on one side of the divide, so the demand is the *wider* of the two
  changes. And walking the chain pairwise measured a taper straight across a junction gap, where the
  pad joins the mouths and no carriageway exists. `taper_factor` is unchanged (1.0 is the book) but
  the finding now names the factor that would pass.
- **A gore's nose carries the RAMP's section, uniformly** — not a blend of the two flanks (§8h.3's
  blend was right only while both roads declared the same *kind* of furniture; a fenced ramp leaving
  a kerbed street gave a wall of falling height standing in a widening footway). The mainline's kerb
  and footway run on past it unbroken. The mainline's values are the fallback only when the ramp
  declares nothing at all.
- **A Blender `EnumProperty` is stored by ORDINAL, not identifier** — introduced and caught in this
  same round. Inserting `RAMP` into the middle of `ROLES` re-read every saved role in every `.blend`
  (`RAMP_EXIT` → `RAMP_ENTRY`) with nothing to see in any diff. **The enum tuples in `point_model`
  are append-only**; `_enum_items` emits the 5-tuple form so the numbers are written down.
- **`Author ▸ Corridor ▸ Split To New Road`** is the repair when a stretch lands in the wrong road
  (a ramp grown with `Extend Road` off its mainline). Links are object pointers, so the `AUX` link
  survives the move. `chain_unlinked` is a **WARN** now, not an ERROR: `road_runs` already builds a
  split chain correctly. The real defect — a point joined to nothing — arrives under its own name,
  `point_stranded`.
- **`Author ▸ Repair ▸ Tidy Roads`** does the same filing with **no selection**, from the links
  alone: a point whose `SEGMENT` links all land in one *other* collection moves there (placed next
  to the neighbour it joins — appending would repeat §8i.1's defect), and a collection holding more
  than one **corridor** splits. A *corridor* is not a *run*: `point_model.road_corridors` breaks
  only where two chain-adjacent points carry neither a `SEGMENT` nor a `JUNCTION` link, because a
  crossing does not split a street — `road_runs` breaks at the junction gap too, since a lane must
  not be swept across a pad. One owner, shared with `check_chains`. A split-out corridor something
  `AUX`-links into is named `<road>_ramp`.
- **`Author ▸ Repair ▸ Repair Links` now exists.** The gate had named it as the remedy since step 1
  (`uid_duplicate`: "run Repair Links") and it had never been built. It **drops** what cannot be
  honoured (a `None`/self/non-point/no-road target, a duplicate row — a pair carries at most ONE
  link) and the ramp's half of an `AUX` pair; **restores** the missing half of a `SEGMENT`/
  `JUNCTION` link and completes a junction component into its clique; and **writes back** the uid
  `read_network`'s `dedupe_uids` re-allocates, which nothing had ever persisted (so the warning
  could not be cleared). Type conflict between the two rows of a pair: `AUX` > `JUNCTION` >
  `SEGMENT`.
- **A finding must name an object in the MESSAGE, not just the subject.** Rule 5 was half-kept:
  `Validate` translated `Finding.obj` but not the body, where most uids are ("is chain-adjacent to
  `p_862c8815`", "move `p_5dd247b1` further away"), and `Build`/`Export` translated neither.
  `point_validate.describe(finding, labels)` is the one owner (regex over `p_` + 8 hex);
  `point_model.point_labels()` supplies `{uid: "<road>/<object>"}`.
- **`Add Sample Network` is authored BY THE GESTURES now**, not by the internal helpers. As a
  data-model fixture it could be perfect while `Extend Road` grew roads backwards and `Make Ramp`
  refused half the ramps in the world — the smoketest pressed the button and covered neither. It
  now contains the arrangements that were broken (a head extension, aux lanes on *both*
  carriageways over one span, one ramp that is an exit at one end and an entrance at the other,
  and a fenced-ramp-to-kerbed-street gore), so it is evidence rather than illustration; under the
  old taper rule it would be red. `is_loop` is the one shape it does not carry yet.
- **A junction arm offers only the lanes that exist AT THE STOP LINE** (`point_export._arm_lanes`)
  — a pre-existing bug the new sample surfaced. `lane_movements.target_lane` preserves distance
  from the **kerb**, and an arm was offering every lane of its run including one that opens 200 m
  past the stop line and is zero width there: both approach lanes shifted one outboard, the
  straight-ahead movement fed a lane that is not there yet, and the exit's **median** lane came
  out with no predecessor — at every junction whose exit arm has an aux lane in the same run.
  Which end is zero decides it, so it is asked per end; `spawnable` is the same fact from one side
  and is NOT the test. Found by `Preview ▸ Flow Report`, which the smoketest now asserts is clean
  on the sample.
- **Object names are GLOBAL — renumber the DESTINATION first when points move between
  collections.** `_renumber`'s two passes only protect against collisions *within* one collection;
  a point that has left but not yet been renamed still holds `<src>_pNNN`, so renumbering the
  source first gets `main_p000.001` back from Blender — a point whose name sorts outside its own
  chain, which is the one thing the name order has to guarantee. Asserted in the coverage
  smoketest (no `.` in any point name).

**And the duplicate-and-branch round, 2026-08-27** (`ROAD_POINT_GRAPH.md` §8j; four user reports
about authoring a *second* ramp). Every one was a derived fact resolved through a proxy that is
usually right — a **uid** where the ground truth is an **object**, or the **walk direction** where
the ground truth is the **road** — and every one failed by returning a plausible number rather than
an error:

- **Membership is read off the OBJECT; links resolve by OBJECT IDENTITY.** Duplicating a road
  collection gave every copy the original's uid, so `dedupe_uids` dropped the copy's own internal
  wiring (right for Shift+D on one Empty, wrong for a whole road) and `read_network`'s
  `{old_uid: new_uid}` remap — only a function while uids are unique, which is exactly what they
  are not at that moment — rewrote **both** roads' point lists, leaving the ORIGINAL orphaned.
  `point_model.relink_from_objects` keeps a link row that stays inside the re-allocated set (the
  copy's own wiring) and drops one that leaves it (a clone's inherited connectivity); a link to an
  object in no road collection is dropped, not resolved by uid onto whoever shares it. `net.labels`
  comes from the same read so §8i.10's rule survives the dedupe.
- **`Author ▸ Ramp ▸ Branch Ramp Here`** is the gesture for a ramp that starts mid-corridor.
  `Extend Road` refuses an interior station and is right to, but the thing the artist was doing had
  no gesture at all. It opens the aux slot back to the first span long enough to hold the taper
  `check_tapers` asks for (an aux count is an integer, so the slot goes zero-to-full across exactly
  ONE span — opening it on more stations moves which span the change lands on, never lengthens it),
  places and faces the mouth via `Align Ramp To Aux`, bends the second station **outboard**, and
  leaves the far end active.
- **Outboard is a fact about the road, not about the walk.** `point_solve._signed_gap` took its
  normal off the chord it was walking, so the upstream reading was the downstream one sign-flipped:
  "which way do the bands part" picked upstream unconditionally, the two edges were paired running
  opposite ways in world space, and the gore's nose cap came out **22 m long, laid across the
  merge** — with a green gate, zero residual and zero angle. It now takes a `sense`. The same fix
  ended the arterial footway that was built across the ramp mouth: `open_runs` opens a kerb across
  whatever the gore actually covers.
- **A ramp that bends back ACROSS the road it leaves had no eye on it.** Both ramp checks measure
  the mouth, and `Align Ramp To Aux` sets both, so a ramp that leaves correctly and then drives
  through the carriageway passed the whole gate with no gore built and nothing said.
  `ramp_divergence` measures the station *after* the mouth; `ramp_wrong_side` (ERROR) /
  `ramp_parallel` (WARN). The sample network's own exit ramp was authored that way.
- **`ramp_frame_sign` is the product of TWO signs**: which carriageway the aux slot is on
  (`aux_fwd` vs `aux_bwd` — traffic through a reverse slot runs against the station axis) and which
  way the ramp's own lanes run. Three places derived it and all three assumed +1, so a
  reverse-carriageway ramp was faced, placed and edged as a forward one — a two-lane entrance came
  out as a 600 m hairpin with a 38 m wall down the middle. `Make Ramp` also stopped writing
  `aux_fwd` unconditionally: `ramp_carriageway` reads it off which side the mouth is on.
- **One aux slot, one ramp, per run.** `point_profile.aux_slot_ids` is the one owner of which lanes
  leave (`_aux_handoffs` was still handing over the outermost slot only, so a 2-lane ramp's inner
  lane had no predecessor — §8g.1 one level up). The structural half is reported, not fixed: a run
  exports ONE lane per slot, so two ramps on one run — or one station wired to two ramps, where a
  duplicated ramp collection lands — both claim `AF0` and one is reachable by no car.
  `check_aux_slots` (ERROR) names both and the two ways out: the other carriageway, or a run break.

**The diverge/merge round, 2026-08-27** (`ROAD_POINT_GRAPH.md` §8k). Two ramps hanging off ONE
mainline station — a two-lane exit that splits, two ramps merging back into one two-lane slot — is
the ordinary shape of an interchange, and §8j.6 had just declared it an error. That was right about
the *run* and wrong about the *station*: a station's aux block is several slots, and what was
missing was which of them is THIS ramp's.

- **`point_solve.aux_allocation` divides a station's aux block among its ramps**, each taking as
  many slots as it declares lanes. Order is derived from where the artist put the mouths (nearest
  the through lanes takes the innermost slot) — not click order (§8i.2) and not uid order, which is
  invisible and would reshuffle a network on a rename. `aux_gore_offset` replaces "the block's gore
  line" everywhere, and `wire_ramps` hands each ramp only its own aux lanes. `aux_slot_shared` is
  narrowed to the same slot claimed twice IN ONE RUN; over-subscription is its own finding.
- **A gore is against the neighbour on the INBOARD side, which is not always the mainline.** The
  outer ramp's is the INNER RAMP; measured against the mainline its wedge is struck across the
  inner ramp's asphalt. `inboard_neighbour` chooses it and reads that road's outboard edge with its
  own `ramp_frame_sign`.
- **Two boundaries pair by PROJECTION, never by index** (`_project_signed`). Equal-arclength walks
  assume both advance together; two sibling ramps do not, so by 90 m the samples lag six metres and
  the perpendicular offset was measured against a point that is not opposite. Two ramps with a real
  5 m hole between them read as a gap of zero and no gore was paved.
- **Which carriageway a ramp is on is a fact about where its mouth is** (`ramp_carriageway`).
  `Make Ramp` wrote `aux_fwd` unconditionally, putting a westbound ramp's slot on the eastbound
  carriageway.
- **"Does this lane exist at the stop line" is asked of the WIDTHS, not of the receiver.**
  `merge_into`/`opens_from` are both None when `lane_taper_route` cannot resolve a receiver —
  indistinguishable from a full-length lane. Declaring `aux_bwd` on the arterial was enough to make
  the junction arm offer the forward aux lane, shift every straight movement one lane outboard, and
  leave an ordinary through lane reachable by nothing (§8i.13 again, from the other side).
  `LaneRoute.i0`/`i1` are the question actually being asked.

**One ramp out and one ramp in at one station, 2026-08-27** (`ROAD_POINT_GRAPH.md` §8l) — the
ordinary half-interchange, and what `Add Sample Network` now carries (the westbound ramp two lanes
wide, plus a spur branched mid-corridor). Four things were in the way:

- **`aux_block` answered "forward" for a reverse ramp.** A station handing a ramp to each
  carriageway declares `aux_fwd` AND `aux_bwd`, and the case-free "most slots, ties to FWD" reading
  then put the reverse ramp's mouth on the forward gore line. `aux_block(profile, direction)` takes
  the side when the caller knows it, and `point_solve.ramp_side_of` knows it — §8j.4's rule asked
  per RAMP instead of per station. `aux_allocation` allocates each carriageway separately,
  `inboard_neighbour` only pairs same-side siblings, and `check_aux_slots` no longer reads one ramp
  on each side as a collision (`AF*`/`AR*` ids keep them apart by construction).
- **An entrance's lane was cut off the wrong end.** `_aux_handoffs` ends an aux lane at its gore
  because *an exit lane has left with the ramp*; applied to an entrance it handed the merging ramp
  the stretch of slot UPSTREAM of the merge — a successor whose head was 600 m back down the road.
  An entrance's lane is the acceleration lane and BEGINS at the gore.
- **Nothing could see that, because every check asked whether an edge EXISTS.** `broken` is no
  successor, `unreached` no predecessor, `ramp_orphans` nothing leads to a ramp — a successor
  pointing the wrong way is healthy by all three. `flow_report` now also reports **`misjoined`**: a
  successor whose head is not where this lane's tail is (`merge` edges exempt — a taper hand-over is
  lateral and its target spans the run).
- **`unreached` stopped reporting non-`spawnable` lanes.** A deceleration lane that opens after a
  junction is supposed to have no predecessor: it is entered by a lane change, and a lane-change
  edge is `inner_lane`/`outer_lane`, not `next`. It keeps its bite where it matters — a full-width
  through lane is spawnable, which is §8k.5's case.

**NAME ORDER IS CHAIN ORDER, AND A HAND EDIT CAN BREAK IT** (2026-08-31, `ROAD_POINT_GRAPH.md`
§8m). `point_model.read_network` sorts a road's point objects **by name** and that order IS the
chain; the links are the separate, authored fact. A rename, a `Shift+D` or a point dragged between
collections therefore reorders the road silently — the build follows the links and is correct,
while the file no longer reads the way it behaves. `point_model.link_order` is the one owner of
"what order do the links put these in" (idempotent on a correct road, and it leaves a **branching**
stretch alone rather than inventing an order for it); `Author ▸ Repair ▸ Renumber Roads` renames to
match — links are authored, a name is derived, so the name is what is wrong — and
`chain_out_of_order` / `chain_branched` are the WARNs that say so. Two gestures came with it:
**`Merge Points`** (a contiguous run of ONE road collapses to one station; the first survives with
its uid and section, outward links carried over — points in *different* roads are a junction, not a
merge) and **`Insert Point` from a single selection** (splits the span after it; `_next_in_chain`
derives the neighbour from the link AND the order, since either alone is ambiguous).

**A RUN'S END IS NOT A ROAD'S END, AND A PAD IS SIZED BY THE WHOLE SECTION** (2026-09-04,
`ROAD_POINT_GRAPH.md` §8o, `WORLD_REBUILD_PLAN.md` `W16`). Two junction defects, both of them the
footway reaching a solve that had never been asked for it. (1) `road_points.chain_tangents` is
handed a RUN, and a run stops at a junction mouth, so its end tangent falls back to the one
available chord — right for a road that ends, wrong for a mouth, where the road carries on across
the pad and the cap is cut on `point_model.station_axis`. They agree only while a road runs
straight through a crossing: at a 12.2 deg bend the street's kerb line finished **2.23 m** off the
pad corner it must meet. `point_profile.run_end_axes` gives the two end stations the chain's
direction (keeping their own Z slope, so a graded approach does not kink) and the sweep, the export
and the overlay all take it. (2) `point_solve.corner_setback` passed the CARRIAGEWAY half-width to
`corner_clearance`, whose own docstring quotes the DECK half — indistinguishable while every
footway was 0 m wide, and the day one was not, every pad was sized for a 21 m road that is 29 m
wide, leaving corners whose cap points are 3.31 m apart for a kerb that has to turn 95 deg through
them. **A number derived from a section must be derived from the whole section:** while a layer is
optional and zero everywhere, every consumer standing for "the road's width" holds a value that
merely happens to be right.

**A CONSISTENCY CHECK CANNOT SEE A FACT THAT WAS NEVER WRITTEN DOWN** (2026-09-04,
`ROAD_POINT_GRAPH.md` §8n, `WORLD_REBUILD_PLAN.md` `W14`). Every gate check reads the authored
graph and asks whether it is consistent, which is blind to a junction that was never authored at
all: two roads whose end stations sit at the identical position with no link between them have no
link to be asymmetric, no chain with a hole and no clique to be incomplete, so an 839 m road can
be an unreachable component of the island with the gate reading 0/0. The cause was
`seed_district_roads.seg_intersect` testing `0.0 <= t <= 1.0` on a crossing that lands on a shared
VERTEX — the plan's own rule is that an arterial terminates on another arterial, and `resample`'s
float drift put `t` at `1.0000000006`, so **four of the island's sixteen crossings were dropped at
the ninth decimal place**. Bounds belong in **metres** (`TOUCH`, 5 cm), never in parameter space:
`t` and `u` are fractions of two segments of different lengths and one number cannot mean the same
thing to both. `point_validate.check_meetings` / `road_end_unjoined` is the eye — open ends only
(a ramp mouth stands beside its mainline by construction) and in **3D** (a street under a deck is
not a meeting).

**A STATION'S CROSS-SECTION COMES FROM THE ROAD, NOT THE STATION** (2026-09-04,
`WORLD_REBUILD_PLAN.md` `W3`). `point_model.resolve_point` gives an `INHERIT` station its road's
`base` and takes only the four `DELTA_FIELDS` (`lanes_fwd/bwd`, `aux_fwd/bwd`) from the point —
lane width, median, footway widths, kerb heights and design speed all come from `RoadData.base`.
So writing a footway width onto every point of a seeded road is writing it where nothing reads it:
`seed_district_roads` did exactly that, and **the island had no pavement at all** — `rka_walk_hl`
was 0.0 on every sample of every road, for as long as the point-graph seeder had existed, with a
green gate. A field being *per-point in the schema* does not make the point its owner; only
`profile_mode = OVERRIDE` does. The barrier needed nothing: `point_solve.solve_road` derives it
(fenced end to end where `ped_access` is off, and where `delta >= BARRIER_MIN_DELTA` or on piers
where it is on) and the missing footway had only left it standing on the kerb line instead of at
the pavement's outboard edge.

**A MOUTH SLIDES ALONG ITS OWN ROAD, AND THE SEEDER MUST NOT GUESS HOW FAR** (2026-09-04,
`ROAD_POINT_GRAPH.md` §8p, `WORLD_REBUILD_PLAN.md` `W18`). Two owners of "where is the stop line":
`seed_district_roads` placed every junction mouth at a provisional **14 m** and pruned the stations
inside `14 + MIN_SPAN` of the crossing; `bpy.ops.rka.auto_setback()` then solved the real distance
(**17.9–35.5 m** over the island's ten pads) and walked the mouth out over the top of a station
that had survived a window computed from the wrong number. `point_solve.solved_setback` is the one
owner now — a function of the ARMS alone, so `seed_district_roads.place_setbacks` can ask it with
planned arms before an Empty exists. Nothing could see the result: two stations with the same
profile make `check_tapers` return on `dw == 0` and `station_coincident` only fires at 1e-6, so the
island shipped an ordinary station **0.19 m** past a mouth with a green gate —
`point_validate.check_mouth_clearance` / `station_crowds_mouth` is that eye. And `auto_setback`
placed each mouth on a ray from the pad CENTROID, which is on none of the roads: identical to the
crossing on a symmetric X (which is why it was invisible), **12 m** off it on a 5-arm pad, enough
to leave one approach 40° from its own alignment and 3.54° from the next arm — two carriageways
leaving on top of each other. The centre is projected onto each mouth's own axis first, so a mouth
can only slide along its road. **Still open (`W17`): `Auto Setback` is not idempotent** — pressing
it twice moved 17 of 35 mouths by up to 30 m and a fourth press by 58, because
`recommended_tail_length` only searches upward from the widest mouth while the mouths move the
centroid it measures from. The build presses it once; do not press it twice by hand.

**A GESTURE THAT HANDS OUT A `JUNCTION` LINK OWES THE WHOLE PAD** (2026-09-04,
`ROAD_POINT_GRAPH.md` §8q, `WORLD_REBUILD_PLAN.md` `W19`). A pad is three facts written together —
the members form a **clique**, each is typed `INTERSECTION`, each hangs off the `JCT_*` handle — and
`point_ops.make_pad` was the only thing that had ever written them. `Merge Points` correctly carried
"every link that left the collapsed run" onto the survivor and stopped there, so merging a plain
station into a crossing (the plain station is `doomed[0]`, so it survives) left the pad a
**component, not a clique**, with mouths still typed `SEGMENT` and unparented — 8 gate errors from
one gesture, and `Auto Setback` then solving a clique that is not one.
`point_ops.complete_junction_cliques` is the shared owner; `Repair Links` recovers a file already
broken by it. A merge smoketest that only merges **interior** stations cannot see this.

**`resample` NEEDS AN OPPOSITE NUMBER** (same round). It lays a station every `spacing` metres so a
road has stations where it needs them; on the straight legs between two authored plan vertices those
are exactly collinear and cost geometry, build time and a lane control point each for nothing.
`seed_district_roads.simplify` is **Douglas-Peucker** over the 3D polyline — never a pairwise
"is this station between its neighbours" walk, which measures each candidate against a line that
already reflects previous removals and lets a shallow curve walk off by many times the tolerance one
legal step at a time. Run twice and unioned: against the ROAD at `STATION_MERGE_TOL` (0.25 m) and
against the GROUND at `GROUND_MERGE_TOL` (1.0 m, looser because a station's `ground_z` is what
`road_support` sizes its piers from — a straight deck over a bay must not collapse to its two shore
stations). Junction mouths are protected outright: a stop line is not a shape. Measured on the
island: **433 → 184 stations** for **+0.02 m** of drape error (worst at-grade road-to-ground gap
0.32 → 0.34 m, against a 0.35 m `MovementController.stepUpLedge`).

**A CORNER IS A BEND STATION, AND ONLY THE JOINT HAS TO MOVE** (2026-09-04,
`ROAD_POINT_GRAPH.md` §8r, `WORLD_REBUILD_PLAN.md` `W15`). **A run never spans two road
collections** (`point_solve.road_runs` walks one road's own points), so two plan arterials meeting
end-to-end each sweep their own carriageway to the joint at their OWN heading — sections cut on
planes 98–124° apart agree nowhere, and the island's three corners ended **24–28 m apart** with a
SEGMENT link across the hole. Rounding the corner and using a bend station are the same answer:
`seed_district_roads.fillet_corner` trims both chains by the tangent length, lays an arc of
`CORNER_RADIUS_FACTOR` (3.0) × the **deck** half-width (43.5 m on a T2 — derived from the section
per `W16`, clamped by `CORNER_MAX_CHAIN_FRACTION` when the road is too short), and **splits it at
its midpoint** so each road carries half the turn and they meet **tangentially** — the
straight-through joint the seeder already built correctly, with each street keeping its name and
every lane id. A 2-arm pad is refused by the model; merging the two roads costs a street its
identity. Measured: gap **25.6 → 0.00 m**, deflection **106/124/98° → ~7°** (one arc step).
**The geometry meeting is not the graph chaining:** the fix moved the flow verdict from `open_end`
to **`broken`** (a tail on a head with no edge), and `point_export.wire_joints` emits the
hand-over — matched by geometry, not lane index — taking `broken` to 0. The runtime's
`LaneGraph` proximity fallback would have hidden it, which is exactly why it is exported.

**A PAD IS A PLANE AND THE GROUND UNDER IT IS NOT** (2026-09-05, `ROAD_POINT_GRAPH.md` §8s,
`WORLD_REBUILD_PLAN.md` `W20`/`W21`). `point_solve._idw_z` interpolates a junction's surface from
its mouths so a pad meets every approach at that approach's own elevation — right, and it stops a
junction on a grade from stepping — but between the mouths the ground goes on doing whatever it
does, and where it rises inside the footprint the pad is **under** it. **The collider is why that
matters:** `point_build.cut_ground` punches a vertical prism through the terrain over every band
including the pad, so the burial is invisible, but `Ground-colonly` is deliberately in a collection
the cut does **not** recognise (continuous collision under the island is the base layer's whole
job), so the player walks on ground the eye says is not there. Measured at the port crossing, four
arterials on ground falling 2.09 m across the pad: **0.924 m** of ground proud of the pad, against
a 0.35 m `stepUpLedge`. `seed_district_roads.pad_lifts` probes the footprint on a 2 m grid and
raises **every mouth by one uniform offset** (`_idw_z` is linear in the mouth heights, so one pass
is exact and the pad keeps its tilt), applied as a **floor before the grade cone**
(`height_profile(..., floors=)`) so the approaches ramp instead of stepping and `road_support`
grows the embankment for free. `PAD_BURY_TOL` (5 cm — `point_edges.BURIED_TOL`'s "exactly on"
idea, not a margin) keeps it off flat pads, whose raw burial measures 0.000000–0.000122 m against
the slope's 0.924. **Still open (`W21`): the terrain collider is never cut**, so ANY road surface
below the ground is unreachable — `W20` fixed the one place it happened, not the class.

**THE ROAD DEFORMS THE GROUND; IT DOES NOT CUT IT** (2026-09-06, `ROAD_POINT_GRAPH.md` §8v,
`WORLD_REBUILD_PLAN.md` `W13`/`W21` — both closed). The boolean cut is **deleted**
(`point_build.cut_ground`, `clear_cuts`, `_cut_tube`, `_cut_section`, `_outward_offsets`,
`_self_intersects`, the `Cut Ground` build option and the exporter's cutter-hiding workaround).
Four rounds of fixing it each found a real defect — a zero-thickness cutter that had never cut
anything (`W22`); a sampler reading the terrain the PREVIOUS build had cut, so 29 of the touge's 66
stations found no ground and were never cut again; a switchback's cutter passing through itself,
which an exact solver answers by leaving the ground standing, silently — and the measurement that
settled it is that **from identical inputs (same network, same terrain, same sampled `ground_z`
byte for byte) a second Build took the touge from 2 buried stations to 9.** That is the tool, not
the road. It was also never the industry approach: terrain is a **heightfield**, a heightfield has
no topology to cut, and from Unreal's `Deform Landscape to Splines` to a Houdini road HDA the
operation is to write the road's elevation INTO the field — where the collision IS that field, so
visual and collider cannot disagree. (Manual cut-and-stitch is real but narrow: terrain holes plus
a hand-modelled piece, for what a 2.5D field cannot express — tunnels, overhangs, an abutment into
a cliff.)

`island_v3_terrain.Carve` is the rule — `z = min(z, road_corridor_z)`, and **carve-only is
load-bearing**: `road_support` already owns FILL (NONE/FILL/PIER/CUT from `surface_z − ground_z`),
so rising ground would build the same embankment twice, and a rule that raises ground would **fill
the bay** under `W1`'s 18.20 m bridge. `min` needs no bridge case because it cannot raise ground.
Terrain owns the cut and its batter; `road_support` owns the fill and the piers — opposite signs of
one number. **One direction of derivation:** a road's profile is derived FROM the ground
(`bench_profile`/`grade_cone`/`height_profile`), so a `Carve` is a separate VIEW (`carved_field`)
handed only to the ground-MESH builder — `build_island_base` emits the ground **twice** (natural →
seed + solve → carved), alignment against the original terrain and terrain deformed to the finished
alignment, never back. **The verge is what makes a 12 m grid exact:** the flat shelf runs one full
`GROUND_CELL` past the pavement, so no triangle spanning the road can have a raised corner —
measured 1 185 377 samples, **0 proud / 0.000 m**, against 450 at 3.508 m with no verge; subdivision
was measured and rejected as the lever (12 m → 3 m moved the worst intrusion only 3.508 → 0.647 m,
still past the 0.35 m `stepUpLedge`, for 16× the vertices). **A hairpin needs no case** — the lower
leg wins where two legs overlap and each leg clears itself, which is the geometry that defeated the
boolean four times. Measured on the built island, 28 295 samples: ground proud of the road **2 of
225 at 3.20 m → 0 at 0.00 m** on the visual and **0 at 0.00 m on the collider**, `Ground` 30 577 →
**21 547** verts, **collider identical to the visual** (that is `W21` closed by construction), and
repeated Build stable. One trap it introduced, with an eye on it: a carved ground is not the
natural ground, and a second Build stamped "the ground meets the road here" onto **195 of 225**
stations (biggest 9.46 m) — `point_build.CARVED_FLAG` refuses that with a message, re-measured 0 of
225. `point_build.road_corridors` / `band_corridors` is the addon's whole remaining contribution:
the centreline and half-width of what it just built, the width read off the swept band.

**THE GROUND CUT HAD NEVER CUT ANYTHING** (2026-09-05, `ROAD_POINT_GRAPH.md` §8t,
`WORLD_REBUILD_PLAN.md` `W22`). `point_build.cut_ground` built its cutters with
`bmesh.ops.solidify(bm, geom=[face], thickness=…)` and got **no thickness at all** — measured across
all 32, `z_min == z_max == -40.00`, so every boolean was a no-op and the evaluated terrain came back
with exactly its base vertex count. It stayed invisible for as long as every road was draped ON the
ground (a road 0.16 m proud needs no cut to look right) and surfaced only where a road went *below*
it: the buried junction pad (`W20`) and the touge's slot (`W13`) are the same defect seen twice.
The solid is built explicitly now. Three rules came with it: **a road is cut as a TUBE, not one
lofted outline** (`band_of` walks left-edge-out/right-edge-back, so a switchback's outline
self-intersects and its single n-gon cap is geometry the exact boolean will not arrange — a tube has
only two small end caps); **coincident rings are welded, not emitted** (at grade the cut depth is 0
and the middle rings coincide, and a solid of degenerate quads is non-manifold, which the boolean
also answers by doing nothing, silently); and **the reach is asymmetric** — full depth downward, but
upward only to each section's own daylight point, because ground above that is hillside the batter
has run out to meet (a local downward reach was measured too: holes 57% → 21% for no gain).
`road_support.cut_footprint`/`CUT_SLOPE` give a CUT the batter only a FILL had — steeper than the
fill (1:1 vs 1:1.5) because a cut face is undisturbed ground while a fill is placed earth on its
angle of repose. And **`CUT_MAX` had two owners that disagreed 8×**: it said a trench deeper than
3 m is a TUNNEL while `island_v3_terrain.MAX_BENCH` said 25, so the touge's 8.1 m bench was
classified as a tunnel nothing builds; `MAX_BENCH` reads `CUT_MAX` now. Measured: samples cut
**6% → 57%**, ground standing proud on eleven of twelve roads **up to +0.42 m → +0.00 m**.
**Still open (`W13`): the shrine touge is the twelfth** — still 0% cut and 8.71 m under its
hillside — and it is a SAMPLER problem, not a geometry one. The hairpin theory was tested twice and
reverted (chunking the cutter every 150° of turn, and a local skirt instead of the full depth:
neither moved the touge, and the second cost 57% → 21% of through-holes elsewhere). At the deepest
station `ground_sampler` returns ground 3 m **below** the road while the station's stored `ground_z`
says the road is 8 m **below** the ground — an 11 m disagreement — so `_cut_section` gets a daylight
height of 0 and correctly removes nothing. `scene.ray_cast` respects modifiers and punches through
whatever `is_terrain` rejects; that is where the 11 m comes from.

**A MATERIAL IS AN ASSET, NOT A CONSTANT IN A BUILDER** (2026-09-05, user-asked: default the
plugin's materials to the ones in `assets/`). `kit_common.mat()` used to get-or-**create** every
material from its own Python table, in whichever `.blend` was building — so the look of the world
lived in code, and it was not even one datablock: `road_kit.blend`'s profile sections carry their
own `M_Concrete` (built by that same function, in that file), so a district linking a kerb section
got the kit's concrete on the kerb and a locally-created one on the deck beside it. Identical, and
two materials in the exported scene. **The kit file is the material library now** —
`blender/tools/build_road_kit.py` writes every `MATS`/`TILED_MATS` entry into
`assets/world_source/kit/road_kit.blend` with a **fake user** (a `.blend` drops any zero-user
datablock on save, so without the flag the kit shipped only the 3 of 25 its sections happened to
use). `mat()` is still the ONE resolver and grew one lookup, not a second registry: a datablock of
that name already in the file wins (`bpy.data.materials.get` prefers a LOCAL one over a linked one
— measured — so a hand-edit survives a rebuild), else it is **linked** from the kit, else built
from the table as the BOOTSTRAP (`build_road_kit.py` sets `kc.USE_MATERIAL_LIBRARY = False`: the
tool that writes the library must not read it). Linked, not appended — an appended copy drifts —
and a linked material *is* writable from Python in a background process, so the base-colour
flattening below is unaffected. `point_build.material()`/`MATERIAL_KEYS` and `point_style.resolve`
are **untouched**: the addon had one default-material lookup and still has one; adding a second
there would have been the very defect this closes. Measured on `Island_base`: the same 11
materials with the same user counts, every one now `lib=//../kit/road_kit.blend`, all 11 baked
with an `albedo_color`, `check_roads.sh` PASS=18.

**A PROCEDURAL BASE COLOUR CANNOT CROSS glTF, AND IT LEAVES NO TRACE WHEN IT FAILS**
(2026-09-05, user-reported "materials seem lost"). glTF carries a base colour as either a CONSTANT
factor or an IMAGE texture. Blender's exporter reads the Principled BSDF's Base Color: unlinked it
writes the constant, linked to an image it writes the texture, and **linked to anything else — a
Checker, a Noise, a Mix — it writes nothing**, so Godot renders the surface pure white with no
warning on either side. `kit_common.get_tiled_mat` builds exactly that shape, for a good reason (a
world-position checker survives a curved corner without the UV pinch a tangent-frame pattern gets,
which is what a footway wrapping a junction fillet needs) — so **`M_ConcreteTile`, the PAVEMENT,
had been white in-game ever since**, while looking right in every Blender render. The authoring
intent belongs in Blender and the constraint belongs to the seam, so the fix is at the seam:
`export_world.py` temporarily replaces any Base Color driven by a non-image node with a
representative constant (a Checker's is the mean of its two colours; anything else falls back to
the material's viewport `diffuse_color`) and restores it after export. **Verified by reading the
baked `.tscn`:** every named material now carries an `albedo_color` (the one remaining blank is an
unused glTF default with 0 users). When a surface looks white in Godot and right in Blender, check
this before suspecting the mesh.

**A GROUND CUTTER IS A MODIFIER INPUT, NOT CONTENT — AND IT MUST BE UNLINKED, NEVER REMOVED**
(2026-09-05, user-reported "one white ground cover the main pave segment, like an open box over the
road"). `point_build.cut_ground` builds one solid per road/pad and hands it to the terrain as a
BOOLEAN target; they are scene objects in `ROAD_MANAGER_GEN/CUTTERS`, and the glTF exporter takes
the whole scene — so all 32 baked into the game as raw white boxes straddling every road. It went
unseen for as long as `bmesh.ops.solidify` was silently producing zero-thickness sheets at z = −40
(the `W22` defect): invisible cutters that cut nothing. Giving them real volume made them real
geometry in Godot. **Removing the objects is not the fix** — the boolean would lose its target and
the terrain would export UNCUT, undoing `W22` where it matters. `export_world.py` UNLINKS them from
every collection instead: verified, the evaluated terrain reads 30577 vertices linked, unlinked and
relinked alike, because the modifier's own reference keeps the datablock alive while the exporter,
which walks the scene rather than `bpy.data`, no longer sees it. Baked scene: 32 cutter nodes → 0,
Ground still 38153 vertices against 19477 uncut.

**The Blender→Godot axis mapping is (x, y, z) → (x, z, −y), and it is correct** — verified to three
decimals on a baked road (`export_yup=True`). A piece that "looks flipped" in Godot is far more
likely a lost material than a transform; measure a known object's bounds on both sides before
chasing the export.

**A HILL ROAD IS DERIVED, NOT DRAWN** (island v3, 2026-08-31 — `blender/WORLD_REBUILD_PLAN.md`
`W2`). `island_v3_terrain.hill_road` walks the height field to produce a switchback alignment:
write a heading as `cos φ · uphill + sin φ · contour` and the height gained per metre is
`|∇z| cos φ`, so asking for the road's grade fixes `φ = acos(grade / |∇z|)` — on a 92% flank an 8%
road runs 85° off the fall line, and flipping the contour term's sign IS the hairpin. It is not a
grid search on purpose: the legal headings lie within ~5° of the contour, a grid offers 8 or 16 of
them, so A* returns nothing and that reads as "there is no route up this hill" (which the plan had
recorded as a measured fact). Three rules came with it and each was a defect first — **the hairpins
are part of the climb** (a half-turn moves the road `2R` across the slope, so it gains `2R·m` in
`πR`; legs walked at the limit average 10.1% on an 8.1% road, and the grade cone "corrects" that by
lifting the foot of the mountain, giving a 57 m sky-road that every per-sample check passes);
**a benched road is judged over its whole alignment**, not sample by sample
(`island_v3_terrain.BENCHED_CLASSES`, `alignment_grade`, `bench_depth`, `MAX_BENCH`) — its hairpins
are cut-and-fill platforms and the ground across a turn swings far more steeply than the road does;
and **the station rule has one owner** (`island_v3_terrain.stations`), because a resample that only
lays even arc-length stations steps clean over a 38 m hairpin at 70 m spacing and the cone then
fills the cut corner (25.6 m of pier where the model said 14.7). `grade_cone` is likewise one
function shared by the seeder that builds the road and the gate that predicts it.

**A MOUNTAIN ROAD IS A BENCH, NOT A VIADUCT** (2026-08-31). `seed_district_roads.height_profile`
raises a draped profile with a grade cone, which is right for a bay crossing (you do not cut a
bridge into water) and wrong for a hill — it answered every hairpin with a pier and left 71 of the
shrine touge's 90 stations elevated, up to 16.3 m, over a hillside the ground cut had already
punched a road-shaped slot through. `island_v3_terrain.bench_profile` takes the MIDPOINT of the two
envelopes (least legal profile above the ground, greatest below); it is grade-legal for free because
`|dz| <= limit*span` is convex, and it turns +16.3/−0.0 into a symmetric ±8.1 at an unchanged 8.10%.
Applied only to `BENCHED_CLASSES`. **The ground cut itself was never the problem** — measured at the
centreline stations it leaves ground standing proud at 1 of 419, by 0.18 m (`cut_ground` is a
vertical prism from −40 to +40 m over each band's footprint). What is still missing is a CUT BATTER:
`road_support` builds an embankment toe for FILL and columns for PIER and nothing at all for CUT, so
a benched stretch comes out in a vertical-walled slot.

**A SHALLOW CROSSING NEEDS A BIG PAD** (2026-08-31). `point_solve.auto_setback` searched only for a
tail that fits every turn MOVEMENT and never asked whether the arms' own CAPS stay in ring order —
so `pad_not_star_shaped` named `Auto Setback` as its remedy and `Auto Setback` answered "moved 0".
The ring stays in order while `atan(half_a/d) + atan(half_b/d) <= theta`, i.e. `h/tan(theta/2)` at
equal widths: 14.5 m at a square crossing, **27.2 m at 56°**. `corner_clearance`/`corner_setback`
add that term. It is the CAP-ORDER test, not "where the outer edges cross" (30.8 m at 56°), which
over-demands by half — a fillet may bulge past that point, and asking for it grows every square
junction for a fold that is not there.

**LANES ARE 4.5 m, WHICH IS AN ARCADE NUMBER** (`seed_district_roads.LANE_WIDTH`, and
`point_model`'s own default, 2026-08-31). The car's hull is 2.00 m with wheels at ±1.1 m, so its
footprint is ~2.4 m: in a book-correct 3.25 m lane that is 74% of the lane and it drives like a
lorry in a tunnel. 4.5 m leaves 1.05 m either side. `island_v3_plan.ROAD_HALF` carries the same
arithmetic (T2 = 4 lanes + 3 m median + two 4 m footways = 29.0 m).

**THE VEHICLE SPEED-FEEL OVERLAY IS DELETED** (2026-08-31, walk-test: "too much… hard to see
road"). `SpeedFX`/`SpeedLines` and `SpeedLines.gdshader` are gone from `Vehicle.tscn`, not hidden.
`VehicleCameraController`'s FOV/accel/NOS/spring-arm terms are kept as exported knobs and ship at
**0**, with their previous values recorded on each field — a camera that cannot react to speed at
all is a decision to take deliberately, not by deletion. The planned replacement is world-space (a
trail or wheel effect on the car), never a screen overlay.

**`-noped` HAS A READER ON THE GODOT SIDE NOW** (2026-08-30). `point_build.collision_name` had
stamped the marker into every carriageway `-colonly` proxy since the point graph landed,
*specifically* so the navmesh would skip them, and nothing in Java had ever looked at it —
`NavBaker` parsed the whole root, so every carriageway baked into the navmesh the marker exists to
keep it out of. `NavBaker.NO_PED_TOKEN` is that reader: it clears each matching body's
`collision_layer` for the duration of `parseSourceGeometryData` (Godot's `STATIC_COLLIDERS` parser
tests the layer against the navmesh's geometry mask) and restores it before `pack()` — reversible,
and it cannot drop geometry from the SAVED scene the way detaching nodes could. What the marker
means on this side is "solid, but not walkable ground", so it is **not road-only**: the island's
`Seabed-noped-colonly` is its second user. Note the honest limit measured on `Island_base`:
excluding the carriageway removed only 11 of 1232 navmesh vertices, because the road stands 0.16 m
proud of ground that is continuous underneath it — a street stays crossable (correctly), and the
case the marker really decides is an on-ramp on piers, where there is no ground under the deck.

**Road/geometry alignment across a shared seam is a separate, still-manual concern.** Because Blender's Library Override
system can move/rotate a linked object as a whole but can never edit linked mesh/curve vertex
data, aligning road geometry that genuinely spans two districts' seam needs either (a) read-only
whole-world context while editing one district locally (`tools/link_neighbors.py`, extendable to
every built district, not just immediate neighbors), or (b) a temporary scoped multi-district
edit session — append (not link) just the districts that need reconciling into one scratch file,
edit their `MANUAL` content together with full Edit Mode access, then write each district's
result back into its own file. See `AUTHORING_GUIDE.md` for the current tooling around this.

### AI spatial perception — StimulusManager (`com.openworld.world`, AutoLoad, E2)

Poll-based, spatial channel for AI-perceptible world events — the AI counterpart to `SpatialEntityGrid`
(events instead of bodies). **Not EventBus:** EventBus fans every signal to every listener (right for
UI, wrong for AI at open-world scale); a stimulus is instead *dropped at a world position with an
audible `radius`*, and AI **poll** their own neighbourhood. EventBus is unchanged and keeps all UI
signals — only AI-perception events live here.

- **`StimulusManager`** mirrors the AutoLoad shape (JVM-static `get()`, `_exitTree()` clears). Holds a
  `List<Stimulus>` aged out after `stimulusLifetime` (5 s) in `_process`. `post(type, origin, radius,
  source, sourceFaction)` drops one; `getStimuli()` exposes the live list read-only (same backing-list
  convention as `PlayerRegistry`). `Stimulus` is an immutable plain object; `Type` =
  `GUNSHOT, EXPLOSION, VEHICLE_CRASH, DEAD_BODY, PLAYER_SPOTTED` (last two reserved for later).
- **Emit (authority side-effect paths only**, so the host that simulates the AI sees them):
  `FirearmItem.useWeapon` → `GUNSHOT` at the muzzle (`gunshotHearingRadius`, exported, 150 m — *not* the
  puppet `playRemoteFireCue`); `ExplosionManager.triggerExplosion` → `EXPLOSION` at the blast center
  (heard past the blast); `Vehicle._integrateForces` → `VEHICLE_CRASH` on a fast impact (throttled
  ~1×/s).
- **Poll:** `AICharacter.hearAlarm()` returns the nearest investigate-worthy origin within
  `behaviorConfig.hearingRadius` (capped by each stimulus's own radius), ignoring its own events and
  *allied* gunfire (`Faction.areHostile` for `GUNSHOT`; explosions/crashes alert everyone).
  `PatrolState` calls it after the visual-target check and, on a hit, sets the controller's
  last-known-position and transitions to `SearchState` (which already navigates there). `DebugHarness`
  **F8** drops a synthetic enemy `GUNSHOT` at the player for a vision-free walk-test.

**Networked propagation:** stimuli are a local per-peer list (puppet AI don't think). A remote
*client's* gunshot is delivered to host AI **for free via the existing fire replication** — when the
client's `fireSeq` bumps, the host runs `FirearmItem.playRemoteFireCue` on that puppet and (gated to
`isServerPeer()`) posts the same GUNSHOT stimulus there, where the AI poll. No new network message;
other clients run the cue but don't post (their AI are puppets). Non-gunshot stimuli
(explosion/crash) still post only where their authoritative side-effect runs.

### Squad awareness — AISquad (`com.openworld.character`, E3)

Shared group targeting so shooting one AI turns the whole nearby band toward the shooter within a
frame, instead of each AI waking on its own ~0.4 s scan.

- **`AISquad`** is a `Node` (editor-placed, or one created per `SpawnConfig` by `ZoneManager`).
  Members `register`/`unregister`; it holds a `sharedTarget` + `sharedLastKnownPosition`.
  `getSharedTarget()` self-clears a dead/freed/out-of-tree target (the "lose track" path);
  `clearThreat()` drops a still-alive one.
- **Spot → broadcast:** `broadcastSpotted(spotter, target, pos)` records the target and pushes it to
  every member within `alertBroadcastRadius` (60 m of the spotter) via `AICharacter.adoptSquadTarget`
  — which sets `currentTarget` + last-known immediately (skipping the scan interval). Triggers:
  `AttackState` on confirmed LoS, and `AICharacter.onEnemyDamaged` (being shot is a sighting too — uses
  `currentTarget`, the believed attacker). Squad-mates are one faction and the spotter already verified
  hostility, so adopters skip a redundant faction check.
- **Converge:** `AICharacter.discoverTarget()` consults `getSharedTarget()` **before** its own scan, so
  a mate keeps the shared target across rescans; `PatrolState` chases a squad-adopted target even
  without personal LoS yet. `AICharacter` holds the squad via `activeSquad()` (nulls a stale ref to a
  freed squad node — pooling/reuse safe); `setSquad` moves registration; `ZoneManager` frees each
  per-group squad on unload.

`AISquad._process` also implements **lose-track**: if no member has spotted the shared target for
`forgetDuration` (8 s) it `clearThreat()`s and members fall back to their own scans (death is handled
sooner by `getSharedTarget`'s self-clear).

> **Part E (E1–E3) complete.** Only deferred item left: body-recycling (E1, off by default — subtree
> reuse unsafe; frame-spread spawning is the real perf fix, TODO).

---

## AI (7-state singleton FSM)

The AI body is `AICharacter`; its brain is `AIController` (`com.openworld.ai`), which runs the
FSM in `gatherInput`. States are **stateless** singleton objects under `com.openworld.ai.character`.
All mutable data lives on `AICharacter`. `AIState.update()` returns the next state; a different
reference triggers a transition.

| State | Key behaviour |
|:------|:--------------|
| `PatrolState` | NavAgent random walk within `patrolRadius` of spawn. → Chase/Attack on sight. → Search on hit. |
| `ChaseState` | Sprint to target (or last known pos). → Attack when in range + LoS. → Patrol after `LOST_PLAYER_TIMEOUT` (3 s). |
| `AttackState` | Strafe laterally. Reaction delay before first shot. Per-shot `hitChance` roll. Suppression fire for `suppressionDuration` after losing LoS. → Search when suppression expires. → RefillAmmo when dry. |
| `SearchState` | Sprint to last known position, strafe to peek. Re-engage on sight. → Patrol after 5 s. |
| `RefillAmmoState` | Sprint to `ammoRefill` Area3D. Fill all weapons on arrival. → Patrol. |
| `EscortState` | Follow + defend a designated `Character`. → Attack if the escort target is attacked. |
| `FleeState` | Sprint away from an attacker for a set distance. → Patrol on arrival. |

Targeting is faction-aware (`AICharacter.discoverTarget()`): same-faction AI ignore each other
and neutral factions are never targeted (friendly escorts / non-hostile NPCs).

### Key AICharacter fields (timers all on AICharacter, not states)

```java
double attackTimer         // counts down per-shot cooldown
double lostPlayerTimer     // time since last LoS in Attack/Chase
double reactionTimer       // counts up from AttackState.enter; fires when >= reactionTime
double underAttackTimer    // set to UNDER_ATTACK_DURATION (2.5 s) on damage
double strafeTimer         // counts down; refresh strafe direction on <= 0
double searchTimer         // counts up in SearchState
Vector3 lastKnownPlayerPosition
Vector3 currentAimTarget   // where AimRay is tracking this frame
```

### SightRay vs AimRay separation

- **SightRay**: pure LoS check — `hasLineOfSight()`. Never moves the camera.
- **AimRay**: fire direction — `AICharacter.snapAimRay(target)` forces it to point at the
  computed aim target just before `command.fire = true`.
- These are independent so accurate LoS never implies accurate aim.

---

## Combat / Weapon System

All weapon classes live in `com.openworld.weapon`; `WeaponItem extends item.Pickup`
(weapon items *are* the world pickup nodes).

### Semi-auto / full-auto lock (WeaponItem)

The "one use per trigger pull" lock lives in the base `WeaponItem`, not per-subclass:
`isWeaponFired` is set in `useWeapon()` and cleared in `stopUseWeapon()` (trigger release);
`isSemiAutoReady()` returns `!isWeaponFired || auto`. Every weapon gates `canUse()` on it, so a
non-`auto` weapon fires exactly once per pull regardless of `fireRate`. `FirearmItem`,
`ProjectileItem`, and `ThrowableItem` all share this — previously the lock was copy-pasted into
the firearm/projectile only and **missing from `ThrowableItem`**, which let a held throw key
spawn multiple grenades back-to-back (the double-throw bug). `MeleeItem` keeps its own
timer-based model (overrides `stopUseWeapon()` to a no-op).

### Spread formula (FirearmItem)

```
totalSpreadDeg = (spread + currentBloom + speed_m_s × 0.03) × stanceMultiplier
```

Stance multipliers: UPRIGHT 1.0×, CROUCH 0.7×, CRAWL 0.5×, airborne 2.0×.
The multiplier applies to the **entire** expression — crouching reduces both the base
accuracy penalty and the movement penalty proportionally.

Bloom accumulation: `currentBloom += bloomPerShot` on each shot; decays at `bloomDecaySpeed`
deg/s every physics frame. Key relationship: if `bloomDecaySpeed < bloomPerShot × fireRate`
bloom accumulates during sustained fire; if greater, each shot clears before the next (semi-auto).

Current weapon values:

| Weapon | `spread` | `bloomPerShot` | `bloomDecaySpeed` | `bloomMax` | `recoil` |
|:-------|:--------:|:--------------:|:-----------------:|:----------:|:--------:|
| Rifle  | 0.01°    | 0.05°          | 0.3°/s            | 0.25°      | 1.0°     |
| Pistol | 0.05°    | 0.05°          | 2.0°/s            | 0.2°       | 0.5°     |

- Rifle: first shot 0.01° (~1 cm at 50 m); full spray 0.26°; bloom accumulates over ~1.25 s of fire.
- Pistol: first shot 0.05° (5 px crosshair gap from draw — visibly less precise than rifle); bloom
  clears between taps so sustained semi-auto accuracy stays near base spread.

Spread is applied as a **circular cone** in `FirearmItem.applySpread`: random perpendicular axis +
`sqrt(rand) × halfSpread` angle → uniform disk distribution (no diagonal bulge from independent
pitch/yaw sampling). It rotates the shot **direction** around the muzzle→target line (see
"Two-stage hit resolution" below) rather than the AimRay's own transform, so the cone stays centred
on the shot whatever the ray is resting at. Skipped entirely at zero spread.

AI bypasses spread entirely (`useWeaponSpread = false` on the AICharacter); accuracy is controlled
by `hitChance` + `aimScatterRadius` in `AttackState`.

### Crosshair (weapon-normalized spread)

`Crosshair._process` maps `weaponController.getCrosshairSpreadFraction()` (a 0..1 value) to a fixed
pixel range `[minSpreadPixels, maxSpreadPixels]` (default `3..90 px`, exported). It does **not** use the
raw degrees directly — that was the old `getCurrentSpreadDeg() × spreadPixelsPerDeg` model, which ran a
wide-cone weapon (shotgun: up to ~8°) off-screen and made small-spread weapons barely move.

**The fraction is normalized per weapon** (`FirearmItem.getCrosshairFraction`): `currentSpread /
worstCase`, where `worstCase = spread + bloomMax + CROSSHAIR_REF_SPEED(6 m/s) × MOVEMENT_SPREAD_PER_MPS`
— this weapon's realistic on-ground max. So **every weapon shares one reticle scale with no per-weapon
crosshair tuning** (the gun's existing `spread`/`bloomMax` drive it), the opening is **capped** (never
off-screen — airborne spread just clamps to 1), and movement/bloom/stance always move the reticle a
visible fraction of the range. The reticle therefore reliably means "current inaccuracy with this
weapon": a rifle reads ~2% at rest and opens dramatically when moving/spraying; a shotgun rests wider
(~37%) and tops out under bloom without exploding off-screen. `getCurrentSpreadDeg()` is unchanged and
still drives the **actual** bullet cone — the crosshair fraction is purely cosmetic.
Arms snap outward at `crosshairExpandSpeed = 60` (near-instant on shot) and contract at
`crosshairContractSpeed = 8` (tracks bloom recovery — a clear "accurate again" signal).

### Two-stage hit resolution — sight → muzzle (the cover fix)

A bullet is resolved in **two legs**, not one, because in third person the camera is not where the
gun is. `FirearmItem.fireShot`:

1. **Sight leg** (`resolveSightPoint`) — the camera `AimRay` (player) or the ray `AttackState`
   already snapped onto its scatter point (`AICharacter.snapAimRay`, AI) answers *where this shot is
   aimed*: its collision point, else its far end. This is the same point the crosshair sits on and
   the point `UserCommand.aimTargetPosition` feeds to the spine IK — so what the character visibly
   aims at is what the bullet is sent toward.
2. **Muzzle leg** (`resolveShot` → `trace`) — the bullet is traced **from the weapon's own `Muzzle`
   marker** to that point, with the spread cone sampled around *that* line. Whatever it hits first
   is the hit, the tracer end, and the impact VFX.

Leg 2 is the fix for *"my character is fully behind a wall and I still killed someone."* A single
camera-origin trace started above/beside the shoulder, on the free side of the cover the character
was visibly hiding behind. The muzzle leg starts at the gun, so that cover blocks the shot — while
the bullet still converges exactly on the crosshair, so aiming is unchanged.

Details that matter:

- **The hug-the-wall bypass is closed.** `resolveShotOrigin` first traces the shooter's chest → its
  own muzzle; if *that* is blocked the barrel has clipped through geometry and the shot origin falls
  back to the chest, so the wall is still hit. Without it, pressing against cover puts the muzzle on
  the far side and the trace starts past the very wall that should stop it.
- **On-foot only** (`useMuzzleTrace`). A seated occupant's gun and a vehicle's own mounted weapon sit
  inside/against the carrier's collision, where a muzzle-origin trace is blocked by the carrier
  itself — and a drive-by is not the exploit this guards. Those keep the camera-origin trace.
  `@Export muzzleTrace` (default true) disables the whole thing per weapon.
- **`trace` borrows the character's AimRay** (`setPosition`/`setTargetPosition` +
  `forceRaycastUpdate`, then restores the **local** values — restoring globals would drift), the
  same idiom `resolveServerShot` uses. So a trace inherits the ray's collision mask and its
  self-exceptions (own body + ragdoll bones) with no query setup, and can never self-hit.
- **Networked:** the origin a client reports in `MSG_SHOT` is now the **muzzle**, so the host's
  `resolveServerShot` re-runs the very same cover test against authoritative positions instead of a
  camera-origin one. No message/format change.
- Shotguns resolve the sight leg + origin **once** per trigger pull and only re-sample the cone per
  pellet.
- Melee (`MeleeItem`) still cone-casts from the camera; its `meleeRange` is measured from the torso,
  which bounds the same problem to arm's reach.

### Hit detection, damage, and impact VFX

`WeaponController.onWeaponFire()` collects hit data into a `HitInfo` and delegates entirely:

```
WeaponController
  → HitInfo(hitNode, collisionPoint, collisionNormal)
  → ImpactManager.processHit(info, damage, weapon, attacker)
        │
        ├─ spawnImpactParticles()   resolveSurfaceType → ParticleManager.spawn(type, point)
        ├─ spawnDecal()             DecalManager.spawn(point, normal)
        └─ applyDamage()            owner.getNode("Health").takeDamage(...)
```

**`HitInfo`** bundles `(hitNode, hitPoint, hitNormal)`. Adding future effects never
changes the `processHit` signature — just add a private method in `ImpactManager`.

**`resolveSurfaceType`** priority (two `instanceof` checks, no node-tree scan):
1. `owner instanceof Character`       → `FLESH`  (automatic)
2. `owner instanceof HittableBody hb` → reads `hb.surfaceType` directly
3. fallback                           → `DEFAULT`

**`ParticleManager`** — fire-and-forget pool. Acquire → position → emit → release immediately.
  Scene setup: one `GPUParticles3D` template per type container; `_ready()` duplicates it
  to `poolSizePerType` (default 16) automatically — only the template needs editor config.

**`DecalManager`** — held pool. Acquire → show → age in `_process` → release after `decalLifetime`.
  Scene setup: one `Decal` template as direct child; `_ready()` duplicates it to `poolSize`
  (default 16) automatically — only the template needs texture + size set in the editor.
  Decal oriented by building `Basis(right, normal, fwd)` so local +Y = surface normal
  (Decal projects along local -Y, so +Y = outward normal shoots the projection into the surface).

All three world managers (`ImpactManager`, `ParticleManager`, `DecalManager`) live in
`World.tscn` and are discovered via Godot groups — `WeaponController` and `ImpactManager`
lazily cache references on first use to avoid `_ready()` ordering issues.

Damage multipliers are resolved by bone name in `Health.getDamageMultiplier()`:
- `Physical Bone head_2` → 4.0× (headshot)
- Upper torso → 1.0×, mid torso / arms → 0.75×, legs → 0.5×

On death, `Health` emits to `EventBus.characterEliminated(attacker, victim, weapon, headshot)`.

### Networked combat cosmetics — fire / reload / melee replay (puppets)

Combat is replicated as **state, not events**: `WeaponController` carries two rolling u8 counters
sampled into every snapshot — `fireSeq` (bumped in `onWeaponFire`, all weapon types) and `reloadSeq`
(bumped at the end of `onWeaponReload`). `NetworkController.applyDiscreteState` change-detects each:
when a counter differs from the last seen value it calls `wc.playRemoteFireCue()` /
`wc.playRemoteReloadCue()` and mirrors the value forward (so a re-broadcasting host carries the right
counter to other clients). No separate droppable fire/reload message exists. `SNAPSHOT_ENTRY_FIXED_BYTES`
in `NetworkManager` must equal the per-entry byte count in `NetMessageCodec.putSnapshotEntry`
(currently 65 = …+ fireSeq u8 + activeMagazine u16 + reloadSeq u8) or MTU chunking mis-sizes frames.

**The core rule — a puppet replays cosmetics only, never re-derives damage, and derives the shot
identically to the authority:**
- Every weapon's `playRemoteFireCue()` reconstructs the shot from the **replicated logical state**
  shared by all peers: the weapon's **own `Muzzle` marker** for the origin and the replicated
  **`getAimTargetPosition()`** point (which also drives spine IK and rides in every snapshot) for the
  direction — *never* the local `aimRay`/crosshair (puppets have none) and *never* the animating pose.
  Authority and puppet run the **same** origin/direction derivation; only damage differs.
- Damage is **authority-only**, gated by the `cosmetic` flag: `RocketProjectile`/`T1Projectile` spawned
  with `cosmetic = true` play VFX (`ExplosionManager.spawnExplosion`) but skip
  `triggerExplosion`/attacker injection; `FirearmItem` puppet draws a tracer but runs no hitscan;
  `MeleeItem.playRemoteFireCue` plays swing audio only and must **not** call `startSwing()` (its hit
  window applies damage). `WeaponItem.playRemoteFireCue()` is an empty default — every concrete weapon
  type that can fire must override it or it is silent/invisible on other peers (this was the melee bug).
- Spawned projectiles add `addCollisionExceptionWith(owningCharacter)` — a secondary guard so a
  weapon never collides with / detonates on its own shooter (the rocket's `collision_mask` includes
  the character layer); it is not the consistency fix, the unified muzzle+`aimTarget` spawn is.

**Switch timing (CS/PUBG-snappy):** the deploy is `transitionTimer = 1/switchSpeed` (~0.45 s at
`switchSpeed = 2.2`); the post-deploy fire lockout `onWeaponTransitionComplete` starts is a small fixed
`WeaponController.DRAW_SETTLE_SECONDS` (0.08 s), **not** a second full `1/switchSpeed`. So total
switch ≈ deploy time (was ~2/switchSpeed — the old double-duration that felt sluggish). The
draw-settle still exists only to stop a held fire button launching on the first mid-draw frame; its
duration is intentionally tiny. (The `WeaponProgress` HUD ring reads `getSwitchProgress` /
`getReloadProgress` off these timers — see HUD system.)

**Weapon switch — ordered equip event (so a remote switch is neither late nor early, and fire can't
render before draw):** the owner's switch is two-phase — `onSetWeapon` starts `transitionTimer`
(holster, old weapon still shown) and only `onWeaponTransitionComplete` raises the new weapon
(`onWeaponEquip`) — so the new weapon comes up at `switchStart + transitionTime`. Both `onSetWeapon`
and the puppet path share `beginWeaponTransition(slot)`, so **a puppet runs the same transition and
raises the weapon at the same offset from switch-start** — timing-identical, off only by latency.
This requires delivering the switch at switch-*start*: `onSetWeapon` emits a reliable, ordered
`MSG_WEAPON_SWITCH(charId, targetSlot)` the instant the owner begins (gated on `isAuthorityFor`; host
validates owner + re-broadcasts excluding the originator), and the per-tick snapshot replicates
`getReplicatedActiveSlot()` (the **target** during a transition, not the post-animation
`activeSlotIndex`) as the drop-heal backstop. (Pitfall: an earlier version *snapped* the puppet's
weapon up instantly — fine while delivery was *late* via the post-transition slot, but once delivery
became prompt it drew a full `transitionTime` too early.) The puppet gates its cue with the **same**
condition as the owner's `onWeaponFire` — `isWeaponTransitioning() || fireTimer.getTimeLeft() > 0`
(`fireTimer` = draw-settle started by `onWeaponTransitionComplete`) — dropping any cue inside the
draw window (`fire_cue_predraw_suppressed`). The replicated path only ever runs on puppets (the owner
uses `onWeaponFire`/`onSetWeapon`), so these timers never carry two meanings on one body. Host-side
fire-timing *validation* is deferred (H3).

### Ragdoll on death (Character.enableRagdoll)

1. `setPhysicsProcess(false)` on both `Character` and `MovementController`.
2. Disable all `CollisionShape3D` stance capsules.
3. Set `collisionMask` layer 1 on each `PhysicalBone3D` so bones rest on the floor.
4. `physicalBoneSimulator.physicalBonesStartSimulation()`.

### Nameplate (`ui.Nameplate`) — generic, reusable across entity types

`ui.Nameplate` (scene `ui/Nameplate.tscn`) is a **generic** floating plate: a single `SubViewport`
rendered to a billboard `Sprite3D`, holding two UI sub-scenes — `CharacterHealthUI.tscn` (`HealthUI`,
name + health, top) and `CharacterWeaponUI.tscn` (`WeaponUI`, bottom strip). `Nameplate.refreshWeapon`
lists **every** carried weapon (one line per occupied slot, `<slot> <name> <mag>/<reserve>`, active slot
marked with a leading `>`) — a full-inventory readout for cross-network debugging, not just the active
weapon. The weapon block is for cross-network debugging (shown for all factions now; gameplay
faction-visibility filtering is later).

**It carries no entity-specific logic** — it binds to its parent purely through the
`character.NameplateTarget` interface (`getNameplateText()`, `getNameplateColor()`,
`getNameplateChangedSignal()`) plus two conventionally-named sibling nodes it discovers itself
(`Health`, `WeaponController` — same names on `Character` and `Vehicle`). So **any type reuses the same
scene/script** by implementing `NameplateTarget` and supplying its own rules. `NameplateTarget` lives
in the `character` package (not `ui`) only to avoid a package cycle — `ui` already depends on
`character`. It's instanced in **`Character.tscn`** (base, node named `Nameplate`), so
AI *and* every networked player gets one. `Character.applyNameplateVisibility` looks this node up by
that exact name (`getNodeOrNull("Nameplate")`) to hide the locally-owned body's own plate — keep the
node name and the lookup string in sync.

`Character implements NameplateTarget`: colour = own faction. `Vehicle implements NameplateTarget`:
colour = its *driver's* faction (neutral when empty/defeated), health + weapon = the *carrier's* own
(found via the shared sibling-node lookup) — see "Carrier nameplate".

**Visibility is decided at runtime by ownership, not per scene and not by the camera:** the plate
defaults visible, and `Character.applyNameplateVisibility` (deferred from `_ready`) hides it **only on
the body we locally own** — `isLocallyOwnedPlayer()` = single-player, or networked + `isAuthorityFor`,
gated to `Player` so AI is never affected. Ownership is the real signal (the camera being current is a
consequence of it); keying on it also stays correct while spectating / viewing another camera. This
replaced both the old `visible = false` override on `Player.tscn` (which also hid *remote* players'
plates) and a camera-coupled hide inside `activateCameraIfOwned`. AI and other peers' players keep the
default, so networked peers see each other's.

**It reflects replicated state with no extra net message** by reacting to signals that already fire on
the puppet apply paths:
- weapon/ammo ← `WeaponController.ammoChanged` (emitted in `applyReplicated*` on puppets).
- name/color/weapon ← `NameplateTarget.getNameplateChangedSignal()`. For `Character` that's the
  registered `nameplateChanged` (Signal0), emitted in `setFaction` (so a replicated
  `WORLD_EVENT_FACTION_SWAP` recolours on every peer, not just at spawn) and alongside `changedWeapon`.
  A carrier would emit it on driver enter/exit (replicated for free via `MSG_VEHICLE_OCCUPANCY`).

#### Carrier nameplate (implemented)

`Vehicle` reuses `ui/Nameplate.tscn` unchanged (instanced as a `Nameplate` node in `Vehicle.tscn`) and
implements `NameplateTarget`:
- `getNameplateColor()` = driver present & alive ? `Faction.color(driver.faction)` : `NEUTRAL` — the
  **driver seat occupant determines the colour; neutral when not ridden or driver exits/defeated**.
- health + weapon/ammo are the **carrier's own** `Health` / `WeaponController` — no code change in
  `Nameplate`; its `../Health` + `../WeaponController` sibling lookup resolves to the vehicle's nodes
  (same node names as on `Character`).
- emits `nameplateChanged` in `tryEnter`/`tryExit`; both run on **every peer** (host-arbitrated seat
  change), so the tint re-derives everywhere with no new message — occupancy already replicates.
- **Auto-exit when the seated occupant is defeated** ("shot through the open vehicle") is still a
  separate *Vehicle gameplay* concern, not nameplate (not built): on the occupant's `Health.died` the
  host would run `Vehicle.tryExit()` + broadcast occupancy; the plate then goes neutral *because* the
  seat emptied — `tryExit` already emits `nameplateChanged`. Damage reaching a seated occupant is
  hit/collision routing on the occupant's `Health`.

> A base `Carrier` class above `Vehicle` was considered and **deferred**: reuse is achieved through
> the `NameplateTarget` / `Controllable` interfaces (the codebase idiom), so a class hierarchy buys
> nothing while `Vehicle` is the only concrete carrier. Extract `Carrier` when a second carrier type
> (boat/aircraft/mount) actually exists and shows what is genuinely shared.

---

## Event System (EventBus AutoLoad)

`EventBus` is a global `Node` registered as AutoLoad. Any node reaches it via
`getNodeOrNull("/root/EventBus")`.

Key signals (not exhaustive — see `EventBus.java` for the full set, which also includes
`all_players_died`, `player_spawned`, `pickup_interact_changed`, `player_ammo_changed`,
`weapon_picked_up`, and the multi-character spawn/health/ammo signals used by `HUDManager`):

| Signal | Emitter | Payload |
|:-------|:--------|:--------|
| `player_died` | `Player.onDied()` | — |
| `enemy_killed` | (future use) | score: `int` |
| `player_health_changed` | (future use) | currentHealth: `float` |
| `ammo_picked_up` | (future use) | weapon index: `int` |
| `character_eliminated` | `Health.takeDamage()` | `Signal7`: victimId, victimName, victimFaction, attackerName, attackerFaction, icon, headshot |

`GameManager` connects `playerDied → onPlayerDied()` in `_ready()`.
The HUD (`HUDManager`/`CharacterHUD`) connects `characterEliminated` for the kill feed.

---

## HUD system (`com.openworld.ui`)

`HUDManager` (CanvasLayer in `HUDManager.tscn`) owns the on-screen HUD. Two pieces worth knowing:

### Situational widget visibility (declarative table + runtime overrides)

Instead of scattered `show()/hide()`, visibility is driven by a `Situation` enum — `ON_FOOT`,
`VEHICLE_DRIVE`, `VEHICLE_PASSENGER_WEAPON`, `VEHICLE_MOUNTED_WEAPON` (the in-vehicle case is derived
from `Vehicle.getWeaponMode()` in `situationForVehicle`). A code table `BASE_LAYOUT`
(`EnumMap<Situation, Set<String>>`) lists which **table-managed widgets** are visible per situation.
Widgets are direct `Control` children discovered by **node name** in `_ready` (`FootHUD`, `VehicleHUD`,
`WeaponSlotsUI`, `DamageIndicator`, future `Minimap`); `Feed`/`StatusFeed`/`Crosshair`/`WeaponRadialMenu`
are intentionally excluded (feeds are always-on; the crosshair self-gates in `refreshCrosshair`; the
radial menu is a self-managed input overlay). **Player health (`FootHUD`) is in every vehicle situation**
so it stays visible while riding (the seated occupant is exposed). Add a widget = drop the node in
`HUDManager.tscn` + add its name to the relevant `BASE_LAYOUT` sets — no new code.
Runtime flexibility: `setWidgetEnabled(id, bool)` / `clearWidgetOverride(id)` (a `widgetOverrides` map
that wins over the table) force a widget on/off regardless of situation (per-carrier/gameplay tweaks).
The table is **code, not an exported `Dictionary`** — a nested generic `Dictionary` export crashes the
godot-kotlin-jvm registration scanner (see Known Quirks).

### Weapon switch/reload progress ring (`WeaponProgress`)

`WeaponProgress` (`ui/WeaponProgress.tscn`, a radial `TextureProgressBar` centered on the crosshair —
renamed from the old unused `WeaponReloadProgression`) polls the active player's `WeaponController` each
frame: it shows + fills 0→100% during a weapon switch (`getSwitchProgress`) or reload
(`getReloadProgress`), hidden otherwise (switch tints cyan, reload amber). The ring texture is generated
procedurally in `_ready` (a transparent annulus), so no ring asset is needed. Not table-managed (it
self-hides); wired in `HUDManager.wirePlayer` like `WeaponSlotsUI`. The progress getters return -1 when
their timer is idle. The ring texture is generated by default, but assign an `@Export ringTexture` (a
baked PNG) to use an asset instead (cheaper load, exact look). **Centering:** with
`nine_patch_stretch = false` a `TextureProgressBar` draws its texture at **native size, top-left aligned**
(it does NOT stretch to or centre within the control rect). So the control rect must be **symmetric AND
exactly the texture size** — `WeaponProgress.tscn` uses offsets `-32/-32/32/32` (a 64 px rect) to match
`RING_PX = 64`; the radial fill pivot (`radial_center_offset` 0,0 = rect centre) then coincides with the
texture centre on the screen centre, lining up with the crosshair dot. A rect *larger* than the texture
(e.g. the earlier `±34` → 68 px) leaves the 64 px ring pinned top-left, ~2 px off-centre — the bug. Keep
the scene offsets and `RING_PX` in sync, and don't override the offsets in `HUDManager.tscn` (use the
scene defaults). Same rule for a baked `ringTexture`: make its pixel size equal the rect.

### Damage-direction indicator (`DamageIndicator`)

Industry-standard directional hit cue: on local-player damage a red arc appears around the crosshair
**rotated to the attacker's bearing** (top=front, sides=left/right, bottom=behind), fading over
`fadeSeconds`; repeated hits from one bearing stack opacity up to `maxAlpha` (a small pooled set of arcs
handles multi-source hits). Bearing is computed relative to the player camera's facing, so it stays
correct as the camera turns.

**Data path:** `EventBus.characterDamagedFrom(CharacterInfo victim, Vector3 sourceWorldPos)` — emitted in
`Health.applyDamage` (single-player/host, attacker world pos threaded through
`ImpactManager.processHit` → `Health.takeDamage`; weapons supply it via `WeaponItem.resolveAttackerPosition`,
explosions via the blast center). `HUDManager.onCharacterDamagedFrom` filters to the local player and
calls `DamageIndicator.onDamagedFrom`. **Networked:** the host is the single broadcast site —
`Health.applyDamage` calls `NetworkManager.broadcastDamage(victimId, damage, hasSource, source)` for every
server-applied hit (host-originated AND client-relayed), and `MSG_DAMAGE_BROADCAST` now carries the
attacker world position (`hasSource` u8 + Vec3 — cheaper than a UUID and exact); the victim's client
re-emits `characterDamagedFrom` in `handleDamageBroadcastMessage`. The per-hit broadcast moved **out** of
`handleDamageRequestMessage` into `applyDamage` (the relay path flows through `applyDamage` on the host
anyway), so it is now the one place the hit cue + direction are sent.

---

## MovementController flags (Player vs AICharacter)

| Export flag | Player | AICharacter |
|:------------|:------:|:-----------:|
| `worldSpaceMovement` | `false` | `true` |

Player input is camera-relative (rotated by `camRotation` at the source, in `PlayerController`).
AI input is world-space (set directly by the AI FSM).

**Combat facing is the AIM yaw, not the camera yaw.** `MovementController.aimYaw()` yaws `meshRoot`
toward `Character.getAimTargetPosition()` — the same world point the spine IK (`SpineAimModifier`,
a `LookAtModifier3D` tracking the `AimTarget` marker) and the bullet converge on. The TPS camera
sits off the shoulder, so its yaw and the body→aim-point yaw differ by several degrees at close
range: keying the body off the camera left the visible body/gun square to a wall while the shot
went past it. It falls back to the raw `camRotation` for a non-`Character` body or an aim point
within 0.5 m horizontally (directly overhead/underfoot). Together with the muzzle leg of the
two-stage trace this makes the three agree — body, bone-driven gun, and bullet.

---

## Godot-Kotlin-JVM Specifics

- **Annotations (0.17 API — the pre-0.17 `@Register*` family is gone).** The plugin runs in the
  default `Inferred` mode, where an annotation implies what it needs:

  | intent | annotation | replaced |
  |:--|:--|:--|
  | expose a class as a Godot script | `@Script` / `@Script(className = "X")` | `@RegisterClass` |
  | let Godot call an ordinary method | `@Register` | `@RegisterFunction` |
  | show/edit a property in the Inspector | `@Export` (alone — it implies registration) | `@RegisterProperty` + `@Export` |
  | register a property without exporting it | `@Visible` | bare `@RegisterProperty` |
  | name a signal's arguments | `@Emit(parameters = {…})` (optional) | `@RegisterSignal` |

  In **Java** `@Script`'s element is `className` — `@Script("X")` does not compile (Java has no
  positional annotation arguments), it must be `@Script(className = "X")`. Do not use
  `import godot.annotation.*;` alongside `import godot.api.*;`: **`godot.api.Script` exists**, and
  two on-demand imports make `@Script` ambiguous. Import the annotations explicitly.
- Signal declarations: `Signal0 / Signal1<T> / … / Signal7<…>` declared as `public final` fields —
  **registered automatically**, no annotation. The registrar names the signal after the *field*
  (`playerDied` → `player_died`, via `convertToSnakeCase`), which is why the field name and the
  `new StringName("player_died")` handed to the constructor must stay in sync.
- **A Java field and its JavaBean accessors are ONE property.** 0.17's language adapter merges
  `public T x` with `getX`/`isX`/`setX` into a single logical property and then binds it *through
  the accessors*. Consequences, each of which bit this codebase during the 4.7 upgrade:
  - A **getter-only** exported property is registered `READ_ONLY` (and its generated registrar does
    not even compile) — the scene/`.tres` value would never reach the object. Every `@Export` field
    with an accessor must have **both** halves; `Crosshair.showCrosshair` and `Door.locked` gained a
    getter, and ~50 tuning fields (`MovementState`, `SwimState`, `VehicleRoute`, …) gained a setter.
  - A **convenience method that merely looks like a getter** silently hijacks the property. Three
    were renamed rather than completed, because their return value is not the field's:
    `AICharacter.behaviorConfigOrDefaults()` (falls back to shared DEFAULTS),
    `WeaponItem.resolveSlotType()` and `HittableBody.resolveSurfaceType()` (enum views of an
    `int`/`String` field). Name a derived reader `resolveX()`/`xOrDefaults()`, never `getX()`.
  - A **registered method must not be overloaded** — the registrar emits a bare `Class::method`
    reference, which is ambiguous in Kotlin. `WorldBaker.bake()` / `WorldPreviewBuilder.buildPreview()`
    keep the no-arg registered form; their static helpers are `bakeScene(…)` / `buildPreviewScene(…)`.
- Class registration is byte-compatible across package moves: `@Script(className=…)` is
  an explicit string (and the default is the simple class name), both package-independent — so the
  `com.openworld.*` reorg did not change any registered type name.
- Always run `./gradlew build` before opening the editor so registration is up to date.
- `GD.lerp`, `GD.lerpAngle`, `GD.clamp`, `GD.randf`, `GD.randfRange` are the GDScript global equivalents.
- `StringName` is used for signal names and node lookups; `NodePath` for node paths.

---

## Known Quirks / Gotchas

- `AICharacter.onDied()` must set `isDead = true` **before** calling `super.onDied()` (which stops
  physics processing). If `isDead` is not set first, `gatherInput` can still run on the
  same frame via a pending physics callback.
- **Do not export a nested/raw generic `Dictionary` from a `@Script` class** (e.g.
  `@Export Dictionary<String, Dictionary>`). The godot-kotlin-jvm `classGraphSymbolsProcess`
  registration scanner chokes on the raw nested type parameter and dies with `Java heap space` /
  `Requested array size exceeds VM limit` (NOT a real memory shortage — bumping `org.gradle.jvmargs`
  does not help). Use a flat `Dictionary<String, String>` (compose keys, e.g. `"a>b"`) — the shape
  the codebase already uses (`MeshConfig.boneHitMultipliers`). This bit `FactionTable` (D3).
- **A `.tscn`-embedded sub-resource is SHARED across every instantiation of that scene** (Godot
  reference semantics), so an *identity/mutable* Resource embedded in a scene (e.g. the
  `CharacterInfo` on `Character.tscn`/`AICharacter.tscn`/`Player.tscn`/`Vehicle.tscn`) is the **same
  object** on every instance — mutating one (stamping a per-instance `characterId`) rewrites them all.
  This collapsed all streamed traffic onto one id (stuck/can't-exit/ownership-migrates — I3b). **Own
  identity in code, not the scene:** spawn code builds a fresh `new CharacterInfo()` before `addChild`,
  and `Character._ready`/`Vehicle._ready` **privatize** a scene-supplied (empty-`characterId`) one via
  `CharacterInfo.copyOf` before stamping the UUID. **Do NOT use `resource_local_to_scene = true` on a
  JVM-scripted Resource** to force per-instance copies — its instantiate-time `duplicate()` reenters the
  godot-kotlin-jvm `TransferContext` shared buffer and throws `Shared Buffer Error: JVM expected a LONG
  but received a BOOL` (the int `ownerPeerId` read colliding with the bool `resource_local_to_scene`
  write). Copy fields in plain Java instead. Read-only shared configs (`VehicleConfig`,
  `AIBehaviorConfig`) are fine embedded/shared — never mutated per-instance, so leave them as-is (and
  do not add `resource_local_to_scene` to them either).
- `AimStayTimer` in `PlayerController.gatherInput`: uses `isActionJustReleased` (not `isActionPressed`)
  to start the timer so it starts exactly once and doesn't restart every frame after it ends.
- `WeaponController.onWeaponFire` saves and restores `aimRay3D` rotation when applying spread;
  `AICharacter.snapAimRay` pre-positions the ray before firing so the spread rotation must be
  skipped for AI (`useWeaponSpread = false`).
- `ParticleManager` pool containers must be named exactly after the `SurfaceType` constant
  (e.g. node named `"FLESH"`, not `"Flesh"`). A missing container is silently skipped in `_ready()`.
- To add a new surface type: (1) add constant to `SurfaceType.java`, (2) add a child container
  under `ParticleManager` in the editor with `GPUParticles3D` children, (3) for world geometry
  attach `HittableBody` script and set `surfaceType` in the inspector.
- `ImpactManager.processHit()` is the single place to add new hit effects (decals, sounds,
  physics impulse). `WeaponController` does not need to change for any of those additions.
- `PhysicalBoneSimulator3D` children must be added to `aimRay.addException()` in both
  `Character._ready()` and `AICharacter._ready()` (for SightRay) to prevent self-hits.
- Process-global static state that holds Godot **resources** (e.g. `IconRegistry.ICONS`, a static
  `Map<String,Texture2D>`) outlives the engine and surfaces as `1 resource still in use at exit` /
  `ObjectDB instances leaked at exit`. Clear such caches on shutdown: `IconRegistry.clear()` is
  called from `GameManager._exitTree()` (the AutoLoad leaving the tree at engine teardown). Add the
  same release for any future static Godot-object cache. (`Color`/`Vector3`/`Quat` statics are value
  types — they don't leak.)
- **Swapping a node must free the outgoing one.** `Character.attachController` /
  `Vehicle.attachController` replace the controller with `removeChild(old); old.queueFree();` — a
  `removeChild` *without* the free leaves a parentless, never-freed node (Godot reports it at exit as
  `Leaked instance: Node … - removed with remove_child() but not freed`, and any resource it holds —
  e.g. its `.java` script — as `resources still in use`). This bites every controller swap: puppet
  spawn (→ `NetworkController`), player-disconnect (→ bot `CharacterController`), scene
  `PlayerController` → `NetworkController`. The retain-and-reuse path is the separate
  `detachController()` (removes, returns, does **not** free — used by vehicle enter/exit hot-swap).
  Same rule for any other `removeChild` that isn't handing the node to a new parent.
- **A 3D audio playback still running when its node is freed mid-session leaks at exit.** Freeing an
  `AudioStreamPlayer3D` while it is playing leaves the `AudioStreamPlaybackWAV` + its stream held by
  the audio server (`Leaked instance: AudioStreamPlaybackWAV` / `Resource still in use:
  Rifle_reload.wav`). This is **networked-only** in practice: the client frees its pre-placed `Player`
  on connect (`NetworkManager.removeLocalPrePlacedPlayer` → `queueFree`) while the spawn-time
  equip/reload SFX (`WeaponController` plays `getReloadAudio()` on equip) is still sounding — in
  single-player no body is freed mid-session, so the cue always finishes. Fix: `WeaponController._exitTree`
  calls `weaponAudio.stop()` (guarded by `GD.isInstanceValid`), releasing the playback on every
  teardown path. **But `stop()` from a *different* node's `_exitTree` is unreliable when a whole body
  subtree is freed at once** (E1 zone-unload frees armed AI mid-session; the player's gun audio leaked
  the same way at app exit — fire SFX `Rifle_fire.wav`): the sibling `WeaponAudio` `AudioStreamPlayer3D`
  can exit the tree *before* `WeaponController._exitTree` runs, orphaning its in-flight playback first.
  **Mid-session fix: the audio node stops *itself* on its own `tree_exiting`** — `WeaponController._ready`
  connects `weaponAudio`'s `tree_exiting` → `weaponAudio.stop`; that signal fires while the node is still
  valid and in-tree, so the playback is released no matter who frees the body or in what order (despawn,
  disconnect, zone-unload). Belt-and-suspenders: `WeaponController.silenceAudio()` (public) is also called by
  `ZoneManager.unload` before it frees/recycles a body (stop a touch earlier, while fully in-tree).
  For any other node that plays audio and can be freed while playing, prefer the **self-stop on
  `tree_exiting`** pattern over a parent/sibling `_exitTree` stop.
  **App-exit does NOT go through `tree_exiting`** (this was the residual leak): at real quit the
  godot-kotlin-jvm runtime is torn down ("Cleaning JVM Memory…") *before* the final SceneTree node teardown,
  so a JVM-registered `tree_exiting` → stop never runs for any body still alive at quit — the local player's
  (or any still-loaded AI's) in-flight reload/fire playback leaks (`Resource still in use: Rifle_reload.wav`).
  The reliable hook is the **root `Window.close_requested` signal**, which fires while every node and the JVM
  are still alive: `GameManager._ready` connects it to `onCloseRequested`, which does **one generic
  depth-first sweep from the root, calling `stop()` on every `AudioStreamPlayer{,2D,3D}`**. This is
  deliberately *not* weapon-specific — any future audio (footsteps, engine, ambient, UI) is covered with **no
  per-entity wiring**, so a new sound-emitting node never needs its own quit handler. (`_notification`/
  `NOTIFICATION_WM_CLOSE_REQUEST` is *not* overridable in this binding — use the Window signal. AutoLoad
  `_exitTree` is too late: AutoLoads are freed *after* the main scene, so the player's `weaponAudio` is
  already gone — that's why static caches like `IconRegistry` clear fine there but live-node audio can't.)
  **Division of labour:** app-exit = this one global sweep; **mid-session** frees (zone unload, despawn,
  disconnect — app keeps running, so `close_requested` has not fired) = each audio node self-stops on its own
  `tree_exiting` (a one-liner, only needed on nodes that can be freed *while playing*). The two are
  complementary; neither replaces the other.
- Weapon scenes are discovered dynamically: `WeaponController` iterates children of
  `WeaponAttachment` at `_ready()` — add a new weapon by adding a `Marker3D` wrapper with a
  `WeaponItem` subclass scene (e.g. `FirearmItem`) as its only child.
- `WeaponPickup` finds its `WeaponItem` child lazily in `onCharacterEntered` (not `_ready()`)
  because `WeaponController.spawnPickup()` reparents the weapon after `addChild()`, so `_ready()`
  fires before the weapon is attached.
- `Pickup.pause()` calls `setFreezeEnabled(true)` (not `setFreeze`) — the Kotlin/JVM binding
  exposes the Godot 4 `freeze` property as `setFreezeEnabled / isFreezeEnabled`.
- `ENetConnection.createHost/createHostBound` take `(… maxPeers, maxChannels, inBandwidth,
  outBandwidth)` — all-int positional args. Putting the channel count one slot too far right
  silently caps outgoing bandwidth at N bytes/s (ENet then throttle-drops unreliable packets
  into multi-second bursts). `connectToHost` differs: its 3rd param IS `channel_count`.
- `NodePath.toString()` returns `"NodePath(<subnames>)"` — the `:property` subname part only,
  which is **empty for plain node paths** — NOT the path string. Use `nodePath.getPath()`
  (the Kotlin `path` property) whenever a path string is needed, e.g. `getPath().getPath()`
  on a Node. `StringName.toString()` is unaffected (it calls the native string operator).

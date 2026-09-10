# Terrain3D + road-generator transition

> **Progress tracker for a multi-session effort.** Decided 2026-09-06. Ground and roads leave the
> Blender bake pipeline for in-engine **Terrain3D 1.0.2** + **road-generator 0.9.3**, both
> GDExtension/addon, both under `addons/` and tracked with git LFS. Companion docs:
> `blender/WORLD_REBUILD_PLAN.md` (the island the heightmap comes from) and
> `blender/ROAD_POINT_GRAPH.md` (the road model being retired — read §8v first, it is the rule the
> addon already implements natively).

## Why

`blender/ROAD_POINT_GRAPH.md` §8v ends with the rule the whole road pipeline converged on: **the
road deforms the height field, it does not cut it** — terrain is a heightfield, a heightfield has no
topology to cut, so the operation is to write the road's elevation into the field. Four rounds of
fixing a boolean cut each found a real defect, and the measurement that settled it is that from
byte-identical inputs a second Build took the touge from 2 buried stations to 9.

road-generator's `RoadTerrain3DConnector` **is** that rule, implemented natively and interactively:
it writes road elevation into the Terrain3D height map, so visual and collider cannot disagree
because they are the same field. Keeping the Blender pipeline would mean maintaining our own
implementation of something the tool now does — and doing it offline, through a bake, instead of
under the artist's hands.

## Scene split

| scene | what it is |
|---|---|
| `world/World.tscn` | the real world. Terrain3D over `assets/terrain3d/island`, `vertex_spacing = 2.0`, ±2304 m of ground, `WorldBounds.half_extent = 2160`. |
| `world/DebugWorld.tscn` | a small-scale version of the *entire* world — a zoo/demo, everything the real world has at a size that iterates fast. 1024×1024, two islands, 2 zones, one road. |

Both are gated by `tools/godot/check_world_envelope.gd` (9 checks each: water top is sea level,
kill-Z < water bottom < terrain min < 0, wall inside the terrain edge, water past the terrain edge,
water mask sees characters, mesh box == collision box, background NONE).

## Steps

- [x] **1 — Heightmap bridge.** `tools/island_to_terrain3d.py` samples
  `island_v3_terrain.Terrain.surface` onto the Terrain3D lattice;
  `tools/godot/import_island_terrain.gd` imports headless;
  `tools/godot/verify_island_terrain.gd` reads it back. **Worst delta 6 mm**, with a mirror-separation
  check so the control can actually fail. Sea level is now **Y = 0**.
- [x] **2 — The world scenes.** Split, water as an `Area3D` box volume, `WorldBounds` depths,
  ridged mountain shaping (`tools/godot/shape_terrain.gd`), height/slope painting
  (`tools/godot/paint_terrain.gd`), a generated sand texture (`tools/make_sand_texture.py`).
- [x] **3 — First road + the lane adapter.** Below.
- [ ] **4 — Retire the Blender road path.** `assets/world_source/tools/env.sh` still points at the
  obsolete JVM-module Godot binary; that file is the marker for what is still wired to the old
  pipeline.

## Step 3 — what landed, and what it measures

**The adapter is one indirection, not a second `Lane` implementation.** `world.Lane` is the
interface `LaneGraph`, `VehicleAIController` and `ZoneManager` are already written against, and
`PathLaneRoute` already implements it over a native `Curve3D`. road-generator's `RoadLane` *is* a
`Path3D`. Writing a new `Lane` against `RoadLane` would have duplicated the arc-length cache,
`pointAtLength`, junction hand-over and spawn filtering — the parts that took the work — and given
the copies room to disagree.

- **`PathLaneRoute.sourcePath`** (`@Export NodePath`, empty = the original `"Path3D"`-child
  behaviour) points a lane at an externally owned `Path3D`. Needed because the generator owns its
  lane nodes and destroys and re-creates them on every rebuild, so they cannot be renamed or
  reparented.
- **`world.RoadNetworkBridge`** walks `RoadManager.get_containers()` → `get_segments()` →
  `get_lanes()` and publishes one `PathLaneRoute` per `RoadLane`, translating the addon's
  `lane_next` NodePath into this project's name-based `nextRoutes`. `sourcePath` is set **before**
  `addChild`, because `addChild` runs `_ready` immediately and `_ready` is where the curve is read.

**The road.** `DebugWorld`'s first road runs from zone `debug_a` across island A's plateau and
descends its 21% shoulder. A road cannot go straight down that, and this codebase already has the
answer — `island_v3_terrain.hill_road`: a heading φ off the fall line gives grade `|∇z|·cos φ`, so
an 8% road on a 21% flank runs ~67° off the fall line. The alignment is that traversing descent:
11 stations, **8.3–8.5% on every descending span**, with a small authored cut at the plateau edge so
the connector has both a cut and a drape to do.

**Measured.**

| check | result |
|---|---|
| lanes published (headless, code-built road) | 6 lanes, **0 unresolved sources**, 4 chained |
| lanes published (authored `DebugWorld.tscn`) | **20 lanes from 20 RoadLanes, 18 chained** — the 2 unchained are the two road-end lanes, one per direction |
| lane continuity | exact: `Lane_pR0_nR0` ends at `(-192, 14, -24.333)`, `Lane_pR0_nR0_1` starts there |
| terrain flattened under the road | **worst 0.026 m** from `road_y + offset` over 11 stations |
| flattening is local | off-road plateau untouched at 14.00; natural shoulder at 11.92 |
| persisted to disk | 0.026 m re-read from `assets/terrain3d/debug_world` with **no connector run** |
| runtime, AutoLoads live | zone streams, 3 cars spawn, **3 routed**, 1 driving, 0 reclaims |
| envelope gate | `OK 9 checks` on both scenes |

### The connector is an AUTHORING step, not a runtime one

`configure_road_update_signal` returns early when `Engine.is_editor_hint()` is false, so
`auto_refresh` never wires up in a running game. That is correct — the connector writes into the
Terrain3D **height map**, which lives in `data_directory` on disk. In the editor you press its
Refresh button; `tools/godot/bake_road_terrain.gd` is the same operation headless, and it **fails a
bake that changed nothing**, because a silent no-op looks exactly like a working one.

`do_full_refresh()` only *queues* the segments — the work happens in the connector's
`_physics_process` (raycasts must run on the physics thread). Read the terrain back after frames
have run, never straight after the call.

### The lane registry is no longer complete at spawn time

Baked `VehicleRoute`s registered in `_ready`, so the registry was always populated before
`ZoneManager`'s first eval tick. road-generator builds its lanes through `call_deferred` and the
bridge publishes a few frames after that — measured, **the zone LOADED one log line before the
bridge published**, so its whole fleet spawned unrouted and sat at the zone centre forever. The
addon also destroys and re-creates every lane on a road rebuild, which can strand a car mid-drive.
So "the lane registry is complete at spawn time" is no longer a safe assumption for anyone.

`ZoneManager.maintainTraffic`'s cull gained one more reason — **`unrouted`**, alongside dead /
route-finished / fell-out / out-of-range — and the existing top-up respawns the car properly.

**Re-routing the car in place was tried first, and was wrong.** Adopting a route and placing the
car itself is a *second* placement path competing with `vehicleStartPoint`, and two owners of one
fact disagree: measured, it left two cars on one lane nose to tail with the front one stuck in
`BrakeState` at 0.00 m/s while a third drove the road normally at 9.7 m/s. Reclaiming routes the
whole thing through the ONE owner that already spreads a fleet by `VEHICLE_QUEUE_SPACING`, needs no
new constant and no snap-distance judgement call, and matches the design — ambient traffic is
disposable. Measured after the change: **three `unrouted` reclaims, once each, then never again**,
and `3 routed` from then on with no churn. The status line's `routed` count is the eye that makes
the failure visible either way; it is what caught this.

## Open

- **Why a car gets stuck in the first place is still undiagnosed** — the `stalled` reclaim below
  clears it, but it is a safety net, not a cause. Two shapes were measured: one car resting **1.4 m
  proud of the terrain** at velocity ~0, and one sitting in `BrakeState` with its forward obstacle
  ray permanently tripped by a car ahead it never got past. Both are vehicle physics/AI on a newly
  authored road, not the lane graph.
- **`findRoute`'s round-robin picks lanes in NAME order, which is not CHAIN order.** A car that
  lands on the terminal lane of a direction drives one segment and finishes. `debug_a`'s
  `route_name` is `"Lane_pR"` (the direction that chains through the whole road) rather than
  `"Lane_"` for that reason — a workaround, not a fix.
- **A dark band under the horizon** seen from altitude. The water-edge and `ground_color`
  hypotheses were both tested and disproved; most likely Sky3D's atmosphere below its horizon line.
- **`Character.tscn` camera `far = 100000`** (100 km) — almost certainly unintended, and harmful to
  depth precision.
- **Terrain3D `region_size` has no measurable frame-time effect** — A/B'd four sizes twice; the
  control run inverted the result. Do not tune it for performance.

### A car can be alive, routed, in range and still not drive

`maintainTraffic` reclaimed on dead / route-finished / fell-out / out-of-range, and a stuck car is
none of those — so it was immortal, and it held a slot the top-up would otherwise have filled with a
working car. Distance never covered it either: the stall happens near the spawn, i.e. **near the
player**, which is exactly where the out-of-range test is furthest from firing.

`stalled` is the reclaim reason for it (`vehicleStallTimeout`, 12 s), and it is measured on
**`routeProgress`, not on speed** — `VehicleAIController.stalledFor()`. That distinction is
load-bearing: the first version used speed and missed a car that oscillated back and forth across 6 m
of plateau at 0.3-1.0 m/s indefinitely, moving the whole time and advancing along its lane not at
all. Progress is what the car is for. The reclaim is gated on `stallReclaimMinDist` (60 m) so a car
is never popped out of existence in front of anyone.

Measured on DebugWorld: **3 `unrouted` + 2 `stalled` reclaims, then steady state** at
`3 cars, 3 moving, 3 routed` with no churn — against `1 moving` before.

### A mission vehicle must not be ambient traffic

There is no mission-vehicle concept yet (`VehicleSpawnConfig` is purely ambient; `NamedCharacterConfig`
is the story-AI analogue with a stable id, built for Part F). When one is added, the hazard runs the
*opposite* way to the obvious one: a mission car placed in a zone's `lz.vehicles` is **already**
reclaimable by out-of-range, and now by `stalled` too — the mission would break silently, with the
reclaim line the only trace. So a mission vehicle belongs outside the disposable list entirely, the
way `NamedCharacterConfig` sits beside `SpawnConfig`.

And "the mission vehicle is gone, so the mission failed" belongs to the mission system, not here:
`MissionManager.failMission(reason)` and `EventBus.missionFailed` already exist, and `Door` already
shows the pattern (it rides `mission_completed`). `ZoneManager` deciding to fail missions would put
the reclaim policy and the mission policy in two places, which is the defect shape this codebase
keeps cataloguing. `ESCORT` / `DELIVER` / `RACE` are already in `MissionObjectiveType` and all three
imply something that must survive.

## Traps that cost real time here

- **An exported *Node* reference needs `node_paths=PackedStringArray("a", "b")` in the `.tscn` node
  header.** Write the NodePath without it and the property stays **silently null** — the connector
  was configured, looked configured, and did nothing.
- **godot-jvm registers methods snake_cased.** `publishedCount()` is `published_count`. And a
  GDScript error inside a `SceneTree._initialize` aborts before `quit()`, so a wrong name reads as a
  **hang**, not an error. The same shape as `ClassDB.instantiate("<a JVM class>")` returning null —
  a godot-jvm class is a **script**, so load the `.java` and call `.new()`.
- **Godot's prints are block-buffered when piped**, so a `timeout`-killed run shows **no output at
  all**, including prints that already executed. Run headless checks under `stdbuf -oL`.
- **`RoadPoint.traffic_dir` and `.lanes` are TYPED arrays.** Assigning a plain `Array` throws and
  *aborts the rest of the setup*, leaving a road with no lanes and no obvious cause. Build them as
  `Array([...], TYPE_INT, "", null)`.
- **`Terrain3D.data` is null until `_ready`**, its regions are not loaded for several frames after
  that (`get_height` returns NaN meanwhile), and writing `region_size` as a scene property **dumps
  core** on instantiate.
- **`world_background` defaults to FLAT**, which is an infinite ground plane to the horizon. NONE.

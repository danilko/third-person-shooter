# World rebuild — island v3 from the ground up

**Status: STEP 1 DONE, STEP 2 ALL BUT DONE — the whole island's ground and roads are playable
in-game as one BASE piece (2026-08-29), walk-tested 2026-08-30, and since then the sea has a FLOOR,
a BEACH and a SWIM VOLUME, the bay has a BRIDGE, the world has an edge, the spur has a mountain
road, lanes are arcade-wide, the network is JOINED where it meets, and the streets have a KERB AND
A PAVEMENT (2026-09-04).** `W1`–`W5`, `W9`, `W10`, `W12`, `W14`–`W16` and `W18`–`W20`, `W22` are closed;
`W6`–`W8`, `W11` and `W17` stand. Written 2026-08-28, picked up 2026-08-29. **Everything still open is in one
place — ["Open items — the follow-up register"](#open-items--the-follow-up-register).**
`check_island_ground.py` reads **12 of 12**, and the world is **4032 m / 8×8 districts**
(`island_v3_geom.SCALE = 2.0`) with the gate still green. What step 1 took — and the one finding
in this document that turned out to be wrong — is below, followed by the scale.

Every metre quoted in "Step 1" is a **plan-scale** figure (the 2016 m frame `island_v3_geom` is
still authored in); multiply by `SCALE` = 2.0 for the built world.
Companion docs: `ROAD_STYLE_AND_PATH_PREVIEW.md` (what the road kit can do now, and every
measurement quoted below), `ROAD_POINT_GRAPH.md` (the road model), `AUTHORING_GUIDE.md`
(district/piece conventions), the addon's `README.md` (the artist-facing guide).

---

## The decision

**Stop building detail on the 6×6 district pieces. Rebuild the world from island v3's ground up.**

Take from the old pieces only what is worth harvesting — the building library, and street-layout
ideas — never the districts as they stand. Get the island's overall **land/mesh and road network
right and aligned first**, then detail outward.

### Why, in evidence

- **The registered pieces are the archived world.** They are the 6×6 grid (positions −1260…1260,
  world 3024 m). Island v3 is the current plan: 4×4, 2016 m. They only partly overlap.
- **The two do not correspond.** Island v3's road network puts **0 m** through
  `District_city_3_1`; its eight arterial crossings land in eight different districts, none of
  them the one piloted. A district's roads and the world plan's roads are simply different
  objects right now.
- **Island v3's ground cannot carry its own roads.** `blender/tools/check_island_ground.py`
  reports **2 of 7** arterials sitting on ground a vehicle could follow (§ below). No amount of
  district-level detailing changes that, and every hour spent detailing on top of it is spent in
  the wrong frame.

---

## Where things stand (all green, `blender/tools/check_roads.sh` → PASS=18)

The road kit itself is ready and is **not** what this plan is about. Shipped over 2026-08-27/28:

- exported **Path3D is the lane** (was up to 22.57 m off; now 0.06–0.09 m), with the preview
  drawing the export by default and `path_deviation` in the gate;
- **style slots** per layer (material or swept profile asset), **lane markings** built at last,
  one material registry shared with the rest of the repo;
- the **junction handle** follows the live intersection centre and turns about Z;
- **`Add Sample Network` is repeatable** (it used to raise on the second press);
- the **ground sampler punches through** buildings and old road meshes to real terrain, and
  `terrain_objects` finds a real district's ground;
- the **ground cut is reversible** — no more 17 → 34 → 51 boolean accumulation per rebuild.

Known gap, deliberately not wired: **a profile asset's own width does not reach the solve**
(`check_asset_width` warns; the structural fix is threading `Style` into `solve_road`). Author
**materials-only** until it is done — see `ROAD_STYLE_AND_PATH_PREVIEW.md` §"The one gap".

### Tools that exist now and this plan depends on

| tool | does |
|---|---|
| `blender/tools/check_island_ground.py` | **the gate for step 1** — per-arterial ground coverage, worst step, grade, gap coordinates |
| `blender/tools/seed_district_roads.py` | clips `island_v3_geom.ARTERIALS` to a district, resamples to stations, authors them **through the addon's own operators**; `--links N` adds local streets |
| `blender/tools/build_road_kit.py` | rebuilds `assets/world_source/kit/road_kit.blend` (6 profile sections) |
| `blender/tools/build_piece.sh` | export + bake + navmesh + `.scn` for one piece |
| `blender/tools/check_roads.sh` | the road-kit gate, 18 checks |

Dead code to ignore: **`blender/tools/island_v3_to_roadkit.py`** drives
`rka.build_segment_from_curve` / `rka.build_intersection`, both deleted in the point-graph
rewrite. `seed_district_roads.py` is its replacement. `island_v3_roads.blend` is the retired
lane-kit's output (`RKA_LANE_PREVIEW*`), not point-graph data.

---

## The old world is archived (done 2026-08-28)

`archive/` holds it, moved not deleted, with `archive/README.md` giving the restore commands:

- `archive/world_6x6/` — the 36 district sources (161 MB), every baked district (379 MB), the
  baked `World_master`, and `world_master.blend` / `world_session.blend`;
- `archive/retired_road_model/` — `island_v3_roads*.blend` (lane-kit output), the v1 `.lanekit.json`
  sidecars, `lane_kit.blend`, `curb_kit.blend`, `intersection_prototype.*`;
- `archive/dead_tools/island_v3_to_roadkit.py`.

Kept in the tree: `island_v3{,_buildings,_full}.blend` (the plan), `assets/world_source/buildings/`
(the harvest library — `RecycledBuildingKit.blend` is standalone, so it cost nothing),
`assets/world_source/plateau/`, and `kit/road_kit.blend`.

Live-tree changes: `World_master.tscn` is an **empty but valid** `Node3D` so both world hosts still
load; `SoloPiece.tscn` / `SoloIntersection.tscn` instance no piece; and `build_piece.sh` now
**inserts** the `piece` resource and node when absent (it used to only substitute a path, which
would have silently no-opped against a host with nothing wired). Verified: all three hosts load
with exit 0 and no archive-related resource errors, `check_roads.sh` PASS=18.

The two diagnostic hosts that named archived content are gone too:
`debug/TrafficCrashDiagnosticHost.java` with its paired `TrafficCrashDiagnostic.tscn`, and
`debug/LaneKitCombineTestIndustry51.tscn` (**scene only** — its script `LaneKitCombineTestHost.java`
is shared with `LaneKitCombineTest.tscn` and stays). Removing a `@Script` class changes registrar
generation, so that was verified with a full `./gradlew build` — **BUILD SUCCESSFUL** — plus a load
of `WorldMaster`, `SoloPiece` and the surviving `LaneKitCombineTest`.

---

## Step 1 — one continuous ground  ✅ DONE 2026-08-29

    blender --background assets/world_source/island_v3.blend \
            --python blender/tools/check_island_ground.py
    →  7 of 7 road(s) sit on ground a vehicle could follow

**The problem, as measured on 2026-08-28** (2 of 7): four arterials crossed a vertical 80–120 m
wall, Chuo-dori showed 2 stations of "NO GROUND" at (48, 410), and Hama-dori had a 2 m land-edge
step at the harbour. Three classes, and one of the three was a misreading — see below.

### The heightfield — `tools/island_v3_terrain.py` (new, pure Python, self-testing)

`python3 tools/island_v3_terrain.py` runs the self-tests and prints the arterial table in **55 ms**,
which is what made this a tight loop: every routing and shaping decision below was tried against
the model first and only then built.

Nested closed rings with heights ARE a contour map, so the conversion is well defined:
**between two adjacent contours the height is linear in Euclidean distance to each of them.** That
is the textbook contour→DEM rule and it is the one that makes GRADE controllable — the slope
between two rings is exactly `dz / (the gap between them)`.

Three decisions were needed on top of it, and each is written down where it lives:

- **A derived toe ring.** The plan has no 0 m contour: the massif's outermost is already **+120**,
  which is exactly why the prisms had a 120 m wall at their own outline. `FOOT_GRADE` offsets a
  toe outward from the outermost contour by `z / grade`. It is the ONE free parameter and the
  whole "route around it or climb it" trade is made against it. Set just gentler than each hill's
  own interior spacing, because a mountain base is concave and nothing gentler is defensible:
  **MASSIF 0.80** against an interior of 0.92–1.67, **SPUR 0.60** against 0.48–0.57.
- **The coast is a CUT, not a taper.** Two alternatives were tried and both were worse.
  `G.pull_ashore` (what the prism builder did) projects off-land ring vertices radially onto the
  coastline, which lands the toe, the +120 and the +240 contours on the *same* stretch of shore
  with no gap between them — the wall again, moved to the water. A shore clamp ("the ground may
  climb no faster than k inland") reads well and then eats the mountain: at a walkable 0.60 the
  +380 summit came out at **74 m**. So the contour field is evaluated as authored and the ground
  MESH is cut to the coastline; where a hill reaches the water the result is a sea cliff of
  whatever height the plan's own contours put there, and every district north of the massif is
  `void`/`mtn`.
- **A platform's edge is deliberate, per edge.** `Land_Harbour` (+2) and `Land_Airport` (+4) stay
  real steps. Each ~20 m piece of their outline is classified once by probing 12 m outboard: land
  → it ramps at `PLATFORM_EDGE_GRADE`, sea → it keeps its full step as a quay wall. The airport is
  offshore and ramps nowhere; the harbour's north edge is the neck and does. The taper is cut at
  **3/4** of the mainline limit it has to satisfy — at exactly the limit, a 10 m sample of a 50 m
  ramp reads 3.9% against 4.0%, and a gate that passes on rounding is not a gate.

### The mesh — `build_island_v3.build_ground`

ONE object, `Ground`, in `TERRAIN`. It replaces `Land_Main`, `Land_Harbour`, `Land_Airport` and
every `Massif_band_*` / `Spur_band_*` prism, so exactly one thing in the file answers "how high is
the ground here" — the same rule `point_build.is_terrain` follows for a district. At `SCALE` 2.0
that is 72 507 verts / 71 620 faces on a fixed 12 m grid; 0 loose verts, 0 non-manifold edges, 0
degenerate faces, and 0 holes under 17 263 on-land probes.

- it is **cut to `G.on_land`** by marching squares, bisecting each coastline crossing from the
  inside point toward the outside one so the two cells sharing an edge compute the identical
  vertex and the mesh stays watertight;
- it carries a **skirt** on every boundary edge — coast, bay bank, lagoon bank, all the same loop;
- **the bay and the lagoon are real holes now.** That is what makes `BAY_BRIDGE` load-bearing
  rather than decorative, and it is why the gate had to learn what a bridge is.

Everything the plan draws on the ground now **drapes** on it (`drape` / `footprint_relief`): the
`Z_*` bands survive as what they always were — a draw order — but they are an offset from the
terrain, not an absolute. A zone outline at +0.12 over the spur was 120 m underground.
Buildings too (`island_v3_buildings.py` emitted every lot at z = 0). Footprint prisms sit at the
**lowest** ground under themselves, not the average, because a plate at the average FLOATS over
half of itself; and 102 farm parcels whose plot spans more than 3 m of relief are **dropped and
counted** rather than drawn as fins sticking out of the mountain — flank terraces are a modelling
job, not a flat plate.

### The routing — `island_v3_geom.ARTERIALS` + `ARTERIAL_CLASS`

**Every arterial routes around; none climbs.** That is the answer to "route around the relief or
climb it", and it is measured rather than chosen: the plan's own contour spacing is 92–167% inside
the massif and 48–57% inside the spur, so the gentlest ground either hill has is far steeper than a
touge (8.1%). There is no alignment at any grade class that gets a road up them. The hills are
served by the T1 continuations that were always meant to — `WESTRAD` to the tunnel portal, `TOUGE`
to the pass — and those are not arterials.

`ARTERIAL_CLASS` is the other half: the three named trunk routes are mainlines (4%), the
cross-streets and the port distributor are locals (8%), and the extra 4% is what lets them run
close to a hill's foot instead of a safe distance from it. Four roads moved:

| road | was | now |
|---|---|---|
| Chuo-dori | ran to (96, 792), 190 m up the massif | ends at the foot, (52, 430), on Nogyo-michi |
| Yamate-dori | started (-800, 214) on the spur's +140 summit | starts at the spur's foot on Nishi-dori |
| Nogyo-michi | ran (-556, 556)→(742, 456) across the massif's flank | along the massif's southern foot, south of the lagoon |
| Nishi-dori | ran up the spur's ridge to +140 | the spur's EASTERN FOOT, Rinkai-dori → Nogyo-michi |

Every end lands on another arterial or the ring/coast. The network is shorter — 7 073 m of
arterial against 8 684 m — because the western upland is a hill, and a hill with a trunk road over
it was never real. (14 147 m of arterial at `SCALE` 2.0.)

`BAY_BRIDGE` also grew: the bay's edge is at x = 131 / x = 367 on that line, so a deck ending on
Hama-dori's own vertices landed **7 m and 0 m** from the water. Each end now reaches ~20 m onto
firm ground.

### The finding in this document that was wrong

> *"**Chuo-dori crosses open water with no bridge**, at (48, 410) and (50, 420): the ray goes road
> → rail → `Sea`. Verified against a 64-deep probe, so it is a real hole, not the ray giving up."*

There is no water at (48, 410) and Chuo-dori does not cross the river anywhere. The sampler
ray_cast the whole SCENE and stepped down past anything that was not a ground object, up to 8
times. `RAIL_BRANCH` is drawn at exactly z = 0.000 where it runs at grade — which is exactly where
`Land_Main`'s top face was. The ray hit the rail, stepped to z = -0.001, and never saw the land
1 mm above it. A deeper probe cannot fix that; it steps past the answer on the first punch.

So **no river bridge on Chuo-dori was authored**, because none is needed. What the finding was
really about is that the ground was being *asked of the wrong thing*: `check_island_ground.py` now
asks the GROUND OBJECTS THEMSELVES (`obj.ray_cast`, topmost hit wins), so nothing standing on the
ground can hide it and there is no punch limit to tune. This is rules 1 and 2 of this document
arriving together — a fact with two owners, caught only by probing real content.

### What else the gate learned

- **A bridge is the right answer to water.** A station with no ground that a `BRIDGES` deck spans
  is `carried`, not a gap; Hama-dori has 24 of them and they are the bay crossing.
- **A pair straddling a hole is not a ground step.** There is no ground between the two banks of
  the bay, and measuring across it would invent a cliff or not depending on where the sampling
  happened to land.
- **A road's grade limit is the road's** (`G.arterial_class` → `P.MAX_GRADE`); `--limit` still
  overrides the lot.

### Elsewhere, as fallout

- **`island_v3_plan.ROAD_HALF` / `BRIDGES` / `bridge_at`** — half-widths and the bridge list moved
  to the plan, because three things now ask the same question (the builder draws them, the terrain
  model credits them, the gate ray-casts them).
- **`IC_CHUO`'s touchdown moved** to (205.0, 445.4) on Nogyo-michi. (74, 600) was a Chuo-dori
  vertex; Chuo-dori now ends at the foot, so that point is 100 m up a 92% flank with no road on it.
  The new position is where the pair's *worse* radius peaks — exit 69.8 m / entry 226.6 m against
  33.9 / 92.1 — so both ramps clear the 59.1 m tier minimum for the first time.
- **Pre-existing and NOT touched:** six of the nine interchange ramps still report `ok=False`, and
  the warning `build_roads` prints for them names the grade when the failing constraint is usually
  the radius. Identical before and after this work; it belongs to whoever next opens the ramp
  fitter.

## The world is 4032 m — `island_v3_geom.SCALE` (2026-08-29)

`SCALE` 2.0, `GRID_N` 8, `DISTRICT` unchanged at 504 m: **4032 x 4032 m, 8 x 8 districts**. The gate
stayed **7 of 7 with no re-routing**, which is the whole reason the mechanism is a uniform scale.

**The budget is ±3800 m from the origin on each axis.** At 2.0 the frame reaches ±2016 m, the drawn
coastline ±1967 m and the outermost object (the sea plate) ±2256 m — comfortably inside it, and
`WORLD` is `DISTRICT * GRID_N`, so 8 × 504 m is the largest clean multiple of the authored plan that
fits. Float precision is not what sets that budget: a float32 resolves ~0.24 mm at 2 km, and Godot's
transforms only start hurting near 10⁴–10⁵ m.

> A 1512 m / 3×3 variant (`SCALE` 0.75) was built and measured first, on the belief that precision
> was the constraint. It is recorded here because its measurements are what taught the rules below,
> and because the mechanism is one constant either way.

**`island_v3_geom.SCALE` is that constant.** Every table in that file is still authored at the plan's
own 2016 m scale — the numbers read as the metres someone drew — and is multiplied on the way out
through `_s` / `_p` / `_ps` / `_rects` / `_hill`. What carries it and what does not is the design:

| carries `SCALE` (layout) | stays 1:1 (engineering / real-world) |
|---|---|
| coast, water, islets | lane width, `BLOCKS` block/lot/alley sizes, `BLOCK`/`STREET` |
| contour rings — radii **and heights** | `MAX_GRADE`, `RAMP_MIN_RADIUS`, `RAIL_MIN_RADIUS` |
| every centreline, the ring inset, the spiral ramp's radius | `CORRIDOR`, pier section, deck thickness, fill/cut thresholds |
| zone envelopes, landmarks, sectors, stations | `STATION_WALK` — ten minutes is ten minutes |
| `DECK_Z` / `RAIL_Z` / `ISLAND_Z` / bridge decks / platform heights | `GROUND_CELL` — a fixed 12 m, see below |

**THE LAYOUT SCALE IS 3D, NOT 2D.** A ramp's length is derived from the deck height:
`run_needed(12 m, 6%)` is 200 m of run whatever size the island is. At 0.75, scaling XY alone took
`IC_CHUO_EN` from a 219 m radius to **19 m**. Scaling the elevated network's heights with the plan
is what makes the whole thing scale-invariant — and at 2.0 it is also what makes the vertical read
as part of the same world: a 760 m coastal peak over a 4 km island (Hakone's pass is 874 m), a 24 m
expressway deck, the reclaimed harbour and airport at +4 and +8 m.

**Scaling up gives the interchange fitter its room back**, because `RAMP_MIN_RADIUS` is fixed at
59.1 m — a car's cornering does not scale. Measured on the same nine ramps: **6 tight at 0.75, 3 at
2.0**, and `RAIL_MAIN` now meets its 400 m mainline radius with 0 vertices under it.
`NEEDS_AUTHORING` is the measured set, so it moves with `SCALE` and a regression still trips it.

Two more things had to stop being scale-carried or start being:

- **`spiral_ramp`'s radius now carries `SCALE`.** It was a bare 40 m while its `z_top`/`z_bot`
  scaled, so the loop's height moved with the world and its radius did not: the airport descent came
  out at 6.37% against a 6% limit purely because the helix stayed the size it was drawn at.
- **`GROUND_CELL` stopped carrying it** and is a fixed 12 m. Scale-carrying it kept the cell COUNT
  constant, which is the wrong invariant — what a player sees is metres. At 2.0 it silently became a
  24 m grid, and the place that shows is the COASTLINE, which the marching-squares clip resolves to
  one cell. The contour field itself is smooth over hundreds of metres and would not have cared.

Also re-derived rather than re-typed: **the theme map is authored at its own 4×4 resolution** and
sampled by `G.theme_at(gx, gy)`. Indexed by the district grid directly, `GRID_N` 8 would have meant
64 hand-written cells describing a transect with four bands in it — a theme is a fact about where
you are on the island, the grid is a streaming decision. And a terrain band is `(rx, ry, z, tag)`:
**an elevation is a number, not a substring of `"+320 snow"`**, which is what lets heights scale.

## Step 2 — the road network on that ground  ← IN PROGRESS

### The BASE piece is playable (2026-08-29)

    blender --background --python blender/tools/build_island_base.py
    NAV_HALF=2016 blender/tools/build_piece.sh Island_base
    <godot-jvm> --path . res://src/main/resources/com/openworld/world/hosts/SoloPiece.tscn

**The whole island's ground and roads, in one piece, in the game.** That is deliberately the FIRST
thing built rather than a district: it is the cheapest possible answer to "is this island any good?"
— one command produces a world you can walk and drive end to end, with no buildings, no props and no
districts to author first. High-level argument before any detailing, which is what step 3's
BASE-vs-TOWN split says the world is made of anyway.

What is in it, and nothing else: the ground heightfield + its collider, the bay/lagoon/sea plates
(visual only), and the plan's seven arterials as point-graph roads. No zone markers, no spawn
regions — a `region_` marker here would make this piece stream its own content and quietly become
a district.

Measured, on the baked scene:

| | |
|---|---|
| roads / junctions | 7 roads, 18 runs, 7 pads, 1 end-to-end joint |
| gate | **0 errors, 7 warnings** (`rka.validate`) — every pad fold and mouth kink cleared by the 4 km scale |
| lanes | **160** exported `Path3D`s, from the `.lanekit.json` sidecar |
| ground | quadtree 12–192 m: 19 464 verts / **37 150 triangles**, one `ConcavePolygonShape3D`; **0 holes in 17 263 on-land samples** |
| whole scene | 198 k verts / 288 k triangles; **5.9 MB `.bin`, 9.7 MB `.scn`** (was 15.1 / 22.3) |
| navmesh | 1 232 vertices over the whole island (Recast cells derived from the extent, ~1.97 m) |
| world extent | ±2256 m on X and Y against a ±3800 m budget |
| in game | loads clean; **160 AI vehicles spawned on all 160 lanes** and drove 40 s with no error |

### The ground is a QUADTREE — squares, not fragments

A regular heightfield spends the same triangles on the dead-flat harbour apron as on the massif's
flank. On this island that is most of it: at 12 m cells, the great majority of the mesh was
describing surfaces that are already planar.

**The first attempt was a planar dissolve** (`bmesh.ops.dissolve_limit`, 1°), and by the numbers it
was excellent — 143 236 → 18 781 triangles for 8 cm of deviation at the 95th percentile. It was
still wrong, and the reason is worth writing down: `dissolve_limit` merges any coplanar-enough faces
it can reach, so what comes out is **long ragged n-gons at whatever angle the coastline and the
contour rings happen to meet**. Correct geometry, unreadable topology, and nothing a chunked-LOD
scheme or a hand edit can hold on to. A terrain mesh is a structure, not just a set of triangles.

**`_quadtree_leaves` buys the same order of saving in SQUARES.** Cell size adapts by halving from
`GROUND_COARSE_CELL` (192 m) down to `GROUND_CELL` (12 m); a block splits when the real surface
departs from the plane through its own four corners by more than `GROUND_TOLERANCE`. Measured
against the uniform 12 m grid, 8000 on-land raycasts:

| tolerance | verts | tris | of grid | rms | p95 | max |
|---:|---:|---:|---:|---:|---:|---:|
| grid | 72 507 | 143 236 | 100% | — | — | — |
| 0.05 m | 27 365 | 52 952 | 37.0% | 0.002 | 0.000 | 0.05 |
| 0.10 m | 25 439 | 49 100 | 34.3% | 0.007 | 0.004 | 0.15 |
| **0.25 m** | **19 464** | **37 150** | **25.9%** | **0.033** | **0.071** | **0.37** |
| 0.50 m | 16 342 | 30 906 | 21.6% | 0.070 | 0.168 | 0.74 |
| 1.00 m | 15 155 | 28 532 | 19.9% | 0.137 | 0.298 | 1.31 |

0.25 m keeps **74% of the triangles gone for 7 cm at the 95th percentile** — and is *more* accurate
in the worst case than the dissolve was (0.37 m against 0.73 m), because a square cannot reach
across a ridge the way a dissolved region can. It costs about twice the dissolve's triangles, which
at 37 150 for a whole 4 km island is not a price worth arguing about. Below 0.25 the curve goes
flat: the remaining cost is the coastline, which is pinned at the finest cell by the next rule.

Three things make it work, and each was a defect first:

* **A coarse cell may never touch a CLIPPED cell.** `_clip_cell` puts a vertex wherever the shore
  crosses a cell edge, at a fraction no coarse neighbour would place one — the two disagree along a
  shared edge, and the skirt pass then reads that as *two* boundary edges and walls the coast twice.
  So a block coarsens only when it is all land **including one finest cell of margin**, which keeps
  every clipped cell surrounded by finest cells and resolves the shore exactly as the uniform grid
  did.
* **2:1 balance plus hanging nodes.** A leaf may only neighbour leaves within one level of itself,
  so a shared edge carries at most one hanging node and `_leaf_ring` places it at the midpoint —
  a ring of 4 to 8 points. Without it a 192 m cell against a 12 m one leaves a 15-vertex edge on one
  side and a bare segment on the other.
* **`_triangulate`, and this one cost a hole in the world.** None of these faces is planar. Blender's
  boolean solver triangulates an n-gon itself, and on a 48 m eight-sided cell it made a choice that
  **dropped the face**: `point_build`'s road cut punched a 40 m hole clean through the ground beside
  Chuo-dori (`check_island_ground.py` fell to 5 of 7) with the leaf present and the quadtree's own
  coverage check clean. `obj.ray_cast` and the glTF exporter each tessellate n-gons independently
  too. Triangulating here means all three see the surface that was measured, and it costs nothing —
  an n-gon of *v* vertices is *v*−2 triangles either way. The 12 m grid's quads were small enough to
  get away with it, and that is all; the defect was latent there too.

**Then smooth shading with a 40° crease** (`_shade_smooth_by_angle`, `GROUND_CREASE_DEG`). This is
the half that decides how it LOOKS, and it is why the instinct to answer faceting with more
triangles is the wrong one: a heightfield's triangles describe a genuinely smooth surface, so
per-vertex normals remove the facets without adding one. The crease keeps a cliff a cliff — the
coastal skirt meets the ground at ~90°, and smoothing that joint would round the shoreline into the
sea plate.

**Ergo: 12 m cells is not "high resolution" — it is the finest cell, and it is the right one.**
Nothing smaller than a cell exists in this mesh anyway; cover, ditches and berms are town content,
not terrain. What the ground owes the game is the large-scale landform and a walkable surface, and
it pays that in 37 150 triangles — `.bin` 15.1 → 5.9 MB, `.scn` 22.3 → 9.7 MB.

`_dissolve_planar` is kept at `GROUND_DISSOLVE_DEG = 0.0`. It is the right tool for a mesh that is
not a heightfield — an imported scan, a hand-modelled landmark — and the wrong one for this.

### The collider is linked LAST, and that fixed the unsampled stations

`point_build.ground_sampler` raycasts downward and punches through anything that is not terrain,
restarting 1 mm below each hit. A collider sitting COINCIDENT with the visual ground is such a hit —
and the 1 mm restart then begins *below* the very surface it was looking for, so the sample returns
nothing. Which of two coplanar faces the ray reports first is arbitrary, so this read as a handful of
`ground_unsampled` stations that **moved around whenever the mesh changed at all** (8 of them became 12 the first time the
ground was decimated, which is what exposed it). `build_island_base` now holds
the uncut heightfield aside and links `Ground-colonly` after `rka.point_build`: one ground to hit
during the build, 215 samples instead of 210, and **7 warnings, down from 8**.

### Seven things that had to be fixed to get there, each a real defect

1. **`seed_district_roads.seed` takes a REGION, not a district.** Its square was a module constant;
   the base piece is the same job over 1512 m. A seeder that could only see 504 m would have had to
   be copied, and then there would be two owners of "how a plan road becomes an authored road".
2. **A crossing at a chain's END is a T, not an X.** The plan's arterials terminate on each other by
   design, so four of the island's eight crossings sit on somebody's last station — and splicing a
   mouth either side of one put an arm PAST the end of the road. The chuo × nogyo pad came out with
   5 arms, two collinear, and `Auto Setback` then pushed a mouth out until the ring folded **135 m**
   past its own centroid. Where BOTH roads end there, it is not a junction at all: they are one
   corridor, and get a `SEGMENT` link.
3. **A mouth REPLACES a station it lands on.** Stations are 70 m apart and a crossing falls where it
   falls, so a mouth 14 m from the crossing regularly landed centimetres from the next station —
   an arm with no length, which the junction solve then faced and set back like a real one. The
   prune window is `setback + MIN_SPAN`, not `setback`: at exactly `setback` a station 13.7 m out
   survives and is then 30 cm from the mouth, which is the same defect with a smaller number.
   *(2 and 3 together: 15 warnings → 6, and every `path_deviation` gone.)*
4. **The ground COLLIDER must not be terrain.** `point_build.is_terrain` matches anything in a
   `TERRAIN`/`GROUND`/`MANUAL` collection, so the collider parked beside its visual collected all
   25 of the road network's ground-cut booleans — the always-resident ground came out with a
   road-shaped hole at every road, which is exactly the layer that must never have one. It lives in
   `TERRAIN_COL` now: the VISUAL ground is cut (nothing z-fights under the tarmac) and the
   COLLISION ground is continuous under the whole island, with the road proxies simply on top.
5. **`NavBaker` clipped every piece to one 504 m district**, and then could not bake the big one at
   all. The base piece got a navmesh over its middle 503 m and nothing else; `clipHalfExtent`
   overrides it (`NAV_HALF=2016`), an override rather than "derive it from the scene AABB" because
   the district value is not a measurement, it is the seam rule. Given the real extent, Recast then
   returned a navmesh of **zero vertices** — silently, with no error — because `CELL_SIZE` 0.5 m
   over 4 km is an 8062-cell grid. `navCellSize` derives the cell from the extent (2048 cells a
   side), which leaves a district byte-identical and gives the 4 km piece ~1.97 m cells: coarse
   against a 0.4 m agent, and the honest shape of the trade — a world-sized navmesh is for open
   ground, fine-grained navigation belongs to the per-district town chunks that stream.
7. **F4 asked for `VehicleRoute`, not `Lane`.** `Lane` exists precisely so a scene can mix a
   hand-authored marker route with a Blender-computed `PathLaneRoute`, and
   `VehicleAIController.setRoute` has always taken the interface — `DebugHarness` was the straggler,
   so it reported "no routes found" over a world with 160 lanes in it. It also spawns on
   `pointAtLength(0)` now, the path a car actually drives, not the raw unsmoothed centreline.

### The stripes that were not there

The first 4 km preview render showed even horizontal bands of sea straight through the ground, which
reads exactly like rows of missing terrain — and was chased as one. It is not: an exact per-object
raycast found **0 holes in 17 263 on-land samples**, in the base piece AND in `island_v3.blend`,
which has no road booleans at all.

**A depth buffer's precision is the RATIO `clip_end/clip_start`, not `clip_end`.** The preview
camera ran 100 km far against Blender's default 10 cm near — 10⁶ — and at that ratio the ground and
the sea plate 0.6 m below it fall inside one depth quantum 4 km from the camera. `add_camera_sun`
now sets `clip_start` to 1/10000 of the far plane. The world was never wrong; the picture was.

Two rules fell out, and both are already written down as rules 1 and 2 of this document: measure
against real content, and when two things disagree find the one owner. The intermediate probe that
DID report holes was itself the bug — it punched through at most 10 surfaces before giving up, which
is the very same defect `check_island_ground.py`'s sampler was fixed for in step 1.

**The game's own cameras now run 100 000 m far against a 0.1 m near** (`Character.tscn`'s
`ActiveCamera` and every vehicle's, plus `SceneShotHost`'s defaults) so the far side of a 4 km island
is actually in frame — it was 8192 m, which cut the view off inside the island. The near plane was
raised from Godot's 0.05 default in the same edit and for the reason above: 100 000 / 0.05 is 2×10⁶,
the very ratio that produced the bands, where 100 000 / 0.1 is half of it. Nothing renders within
10 cm of a third-person camera, so the near plane costs nothing. Godot 4's reverse-Z float depth is
far more tolerant of a big ratio than Blender's viewport, but the cheap half of the fix is free.

### The network is 19% shorter than the plan, and that is a RETREAT, not a design (open)

Step 1 rerouted four arterials off the relief, because once the ground stopped being flat prisms
they were climbing 48–167% grades. That was the right call *per road* and it quietly cost the world
its whole northwest reach. Measured, in plan units (`SCALE`-normalised so the two are comparable):

| road | plan | now | extent, plan → now |
|---|---:|---:|---|
| Chuo-dori | 1409 | 1044 | y[−598, **792**] → y[−598, **430**] |
| Rinkai-dori | 1648 | 1648 | unchanged |
| Yamate-dori | 1615 | 1029 | x[**−800**, 812] → x[**−215**, 812] |
| Nogyo-michi | 1390 | 987 | x[−556, 742] y[456, **700**] → x[−185, 778] y[345, **470**] |
| Hama-dori | 1428 | 1428 | unchanged |
| Nishi-dori | 826 | 569 | x[**−800, −624**] → x[**−430, −185**] |
| Port road | 368 | 368 | unchanged |
| **total** | **8684** | **7074** | **−19%** |

And the number that matters more than length — **land within 250 m of an arterial: 90.5% → 70.0%**.

Every metre of that loss is in the northwest. Nishi-dori was a *west coast* road at x ≈ −650…−800
and now runs 400 m inland along the spur's foot; Yamate-dori lost its entire western half;
Nogyo-michi lost its western third and its northern swing; Chuo-dori stops at the massif foot
instead of reaching the north shore. The three unchanged roads are the three that were already on
flat ground.

**The plan's spread is the better world and should be restored.** A 4 km island whose roads all
huddle in the southeast reads as a smaller island than it is, and it leaves the massif and spur —
the two most interesting pieces of terrain — with no way to reach them. The current routing is what
the grade checker permits, not what the world wants.

What it needs is what a real mountain road has, and none of it is exotic: **switchbacks** (the grade
limit is on the road, not on the hill — a longer road at the same grade climbs), **cut and fill**
(the road is already cut into the terrain; the support rule already derives piers), and a **tunnel or
viaduct** where neither is enough. `TOUGE`/`WESTRAD` in the plan exist for exactly this. Doing it
properly means the reroute becomes an authoring job on the hill roads rather than a straightening of
the flat ones — which is the next thing worth doing after the local grid, and bigger than it (`W2`).

### Two handling bugs from the first walk-test (2026-08-30, both fixed)

**A player could enter an ambient car but not drive or leave it.** `isAiOccupied()` asks about the
SEAT (`occupant != null && !(occupant instanceof Player)`); `tryEnter`'s hot-swap guard asked about
the CONTROLLER (`controller instanceof VehicleAIController`). Design B puts the lane-follow brain on
the **vehicle**, so a car spawned by `ZoneManager` or `DebugHarness` F4 with **no rider at all**
is AI-driven with an empty seat — false by the first test, true by the second. The player took the
ordinary enter path (no carjack, so nothing dropped the brain), the guard then refused the
controller hot-swap, and the exit branch in `Vehicle._physicsProcess` reads the *vehicle's* own
controller, which was still the AI's. Seated in a car that kept driving itself, with a key that
reached nothing. `Vehicle.isAiDriven()` is now the one owner of "an AI brain is driving this", and
`tryEnter` drops it when a **`Player`** takes seat 0 — a seated **AI** is still cargo and the brain
still drives, which is what the guard was right about. It runs inside `tryEnter`, which is the
occupancy-event executor, so the networked path is covered by the same three lines.

**The character could not step onto a kerb.** `CharacterBody3D` has no step-up: a vertical face is a
wall whatever its height, and `floor_block_on_wall` — on, and correctly so, since it is what stops a
body climbing steep slopes — kills the upward component of the slide. Measured across Yamate-dori,
the carriageway proxy stands **0.160 m** proud of the ground either side of it, so every road on the
island had a 16 cm wall down both edges. `MovementController.stepUpLedge` (`stepHeight` 0.35 m,
`stepReach` 0.45 m) probes up-forward-down after `moveAndSlide` and only when it reported both a
floor **and** a wall, so it costs three physics queries at a ledge and nothing otherwise; a landing
whose normal is not walkable is refused and the exact starting transform restored. `stepReach`
cannot be this frame's motion: contact means the capsule's *surface* is on the face, so the centre
is still a radius short, and advancing only the ~7 cm a walk covers per frame drops the body back
onto the ledge's top **edge**, whose normal is not walkable — it would judder and never step.

This is deliberately a character fix, not a world one. 0.15 m is what a kerb is; ramping it or
lowering it would be authoring around a missing feature, and stairs and low ruins want the same
thing.

**Found while measuring, not fixed:** the footway proxy sits at terrain level, flush — the raised
surface on that section is the **carriageway**, not the pavement (`W3`). And nothing on the Godot
side reads the `-noped` suffix (`NavBaker.parseSourceGeometryData` takes the whole root), so the
carriageway bakes into the navmesh that the marker exists to keep it out of (`W4`). Both are
road-kit/bake concerns rather than handling ones. **Neither fix above is verified in motion** — a
headless run presses no keys (`W5`).

### The sea had no floor, and the bay had no bridge (2026-08-30, both fixed)

Two reports from the same walk-test, and they turned out to be one hole with two names: *"the ocean
seems to flow higher than common ground"* and *"the player should not fall off the sea to
infinite"*.

**The water was 5 cm ABOVE its own shore, measured.** `Water_Bay`/`Water_Lagoon` were prisms drawn
from `Z_SEA` (−0.60) up to `Z_WATER` (**+0.05**) while the flat land sits at `BASE_Z` (0.00).
`Z_WATER` is not a height — it is a **draw-order band** in `build_island_v3`'s own list
(`Z_SEA, Z_LAND, Z_WATER, Z_ZONE`), and in the top-down PLAN diagram those bands are right: water
has to draw over land to be visible at all. Carried into a piece you walk around in it is simply
the sea above the beach. The base piece now draws **one translucent plate at `SEA_LEVEL_Z`** and no
per-body plates at all: the sea plate already spans the world, the bay and the lagoon are holes cut
in the land, and the same plate shows through both. `island_v3_terrain.SEA_LEVEL_Z` is the one
owner; `build_island_v3.Z_SEA` reads it.

**The sea now has a floor, and it is not a safety floor.** `island_v3_terrain.seabed(x, y)` is
`SHORE_Z` (−1.00) at the waterline easing to `SEA_FLOOR_Z` (−24 m) over `SHELF_RUN` (350 m) of
**distance from the nearest shoreline** — `min` over `G.LAND + G.WATER`, because a land boundary and
a water body's bank are the same line seen from two sides. That one choice is why the bay needs no
special case: its middle is ~50 m from its own bank, so a 100 m ria comes out a shallow inlet while
the open sea reaches the full depth. `build_island_v3.build_seabed` sweeps it with the **same
quadtree, the same coastline samples and the same `_edge_crossing`** as the ground, clipped to the
other side (`_clip_cell(..., keep_land=False)`, which still hands the bisection its arguments
land-first — anchoring it on the side being kept would give the two sheets two different vertices
at one crossing and open a crack down the whole coast). `GROUND_SKIRT_Z` **is** `SHORE_Z`, so the
land's skirt foot and the seabed's shoreline are the same number at the same point and the
waterline is continuous ground. Measured after: 8 rays from 200 m inland to 600 m offshore,
**0 samples with no surface under them**.

This is deliberately not the world-spanning safety floor CLAUDE.md records as removed, and the
difference is the whole design. A safety floor is an invisible collision lid a metre under the
visible ground: you fall through a gap, land on nothing you can see, and there is no way back — it
was removed for exactly that and nothing here brings it back, because **this surface does not exist
under the island at all**. A seabed is the terrain continuing past the waterline: visible, sloped
and walkable in both directions, which is what every open-world coast ships. Falling off a cliff
onto land still falls.

**A GROUND SHEET FACES UP, and nothing said so.** `recalc_face_normals` infers "out" from the shape
as a whole; the ground gets it right for free because its skirt closes it downward, and the seabed —
an open sheet with no skirt — came out with **all 21 764 faces pointing down**. Two things then fail
and neither looks like a normals bug: the sea floor is invisible from above (backface culling) and
**Recast will not rasterize a down-facing triangle as walkable**, so it contributed no navmesh. The
collision proxy worked perfectly throughout, which is what made it pass every test that only asked
whether you could stand on it. `_face_upward` is now the one owner, run for both sheets (a no-op for
the ground, so its mesh is unchanged: 19 464 v / 37 150 f before and after).

**The seabed is `-noped`.** Once it faced the right way it was walkable ground, and a 4 km walkable
sheet under the sea is a route an AI would plan across the bottom of the bay. It carries the road
kit's own marker (`Seabed-noped-colonly`) rather than a second rule, which is what `W4` had just
made mean something.

**`W1`: Hama-dori crosses the bay on a bridge now, and the road kit built it.** The seeder's
fallback for a station the ground sampler missed was a bare `0.0`, so the crossing ran 466 m dead
flat at sea level on nothing but its own tarmac. Two owners of "is this crossing carried":
`island_v3_plan.BRIDGES` said yes and `island_v3_terrain.report` had been counting those 47 stations
as `carried` since step 1 — and the seeder had never heard of it. `seed_district_roads
.height_profile` asks `P.bridge_at` and then imposes the standard **two-pass grade cone** at the
road's own class limit (`P.MAX_GRADE["mainline"]` = 4%), so a +18 m deck grows the ~450 m of legal
approach it needs at each end instead of a cliff at the abutment. Nothing bridge-specific is built:
`road_support.support_kind` reads the same `delta` it always reads and the kit's existing pier
layer does the rest — **18 stations elevated, up to 26.4 m, deck at 18.20 m and columns reaching
−8.32 m**, on the sea floor, with the gate at 0 errors and 0 warnings.

The other half of that is `ground_under`: `point_build.sample_ground` leaves a MISS alone on
purpose ("a road over water keeps whatever it had"), so a bay station would have handed the support
solver a 0 and grown columns that stop at the water line. The seeder stamps `ground_z` from
`IT.seabed` — not an invented number, the same model the seabed mesh is built from. The sampler
still cannot see the sea floor, and that is deliberate: a seabed inside a `TERRAIN` collection would
be **drapeable ground**, Hama-dori would have been laid 2 m under the bay, and `W1` would have
closed itself by hiding.

### `W4` — the Godot side reads `-noped` now (2026-08-30, fixed, with its control)

`NavBaker` collects every `CollisionObject3D` whose name carries `-noped`, clears its
`collision_layer` for the duration of the parse, and restores it before `pack()`. Godot's
`STATIC_COLLIDERS` parser tests each body's layer against the navmesh's own geometry mask, so that
is a two-line, fully reversible "not this one" — where lifting the nodes out of the tree would have
to restore index *and* every descendant's owner before packing, and any slip there drops geometry
from the **saved scene**, not just from the navmesh.

Measured on `Island_base`, three bakes: **skipped 0 → 1232 vertices** (identical to the bake before
the change, so the pipeline is deterministic), **skipped 25 → 1221**, and the control —
masking the single ground body instead — **239**. The mask is genuinely consulted.

The honest reading of the small delta: at 1.97 m cells the carriageway is **0.16 m proud of ground
that is continuous underneath it**, so excluding the road removes its own nav surface and the ground
under it still bakes. A street stays crossable, which is what a street should be; the case the
marker exists for — an on-ramp climbing away from the ground on piers, where there is no ground
underneath — is where the exclusion is decisive.

### `W5` — the two handling fixes are verified in motion (2026-08-30, closed)

    godot --headless res://src/main/resources/com/openworld/world/hosts/HandlingTest.tscn

`HandlingTestHost` + `ScriptedInputController` (`com.openworld.debug`) drive the fixes instead of
asking a person to press F. The UserCommand loop is built to take its input from an interchangeable
third source, so the honest answer to "a headless run presses no keys" is to **be** that source —
`ScriptedDriveController` replays a fixed timeline and is right for a physics soak; this one is
written from the outside step by step so a failure names the step. `enterExit` is a one-shot,
because the real source is `isActionJustPressed` and a held-true flag would enter and leave the car
on alternating ticks.

**Every case runs with its control**, which is the point: a character climbing a kerb proves nothing
unless the same walk is blocked when the feature is off.

| case | result |
|---|---|
| kerb 0.16 m, `stepHeight` 0.35 | **PASS** — rose 0.159 m, reached x = 11.69 |
| kerb 0.16 m, `stepHeight` 0 (control) | **PASS** — blocked; rose −0.001 m, stopped at x = −0.28 |
| wall 0.60 m, `stepHeight` 0.35 (control) | **PASS** — blocked; rose −0.001 m, stopped at x = −0.35 |
| the car is AI-driven with an empty seat (the case under test) | **PASS** — `isAiDriven` true, `isAiOccupied` false |
| the EntranceArea reports the walker | **PASS** — `nearbyVehicle` set |
| player takes the wheel | **PASS** — occupant = player, `isAiDriven` false, vehicle controller hot-swapped |
| the car drives on the player's own throttle | **PASS** — 114.3 m in 4.0 s |
| the player gets back out | **PASS** — occupant null, controller back on the player |

The kerb height is the measured 0.160 m the carriageway proxy actually stands proud of the ground,
not a round number, and the wall is 0.60 m — comfortably over `stepHeight`, so "it steps onto a
kerb" and "it does not climb walls" are two findings rather than one.

### All water is one swim volume, and the world has a logic wall (2026-08-30, `W9` + `W10`)

**`W9` — the swim system had no water to run in, and it was the `W4` shape exactly.**
`SwimState`, the buoyancy spring, `Character.setInWater`, the depth-based wade-vs-swim decision and
`Boat`'s float-to-surface scan have all been complete since I1. `WorldBaker.buildWater` built a
**bare `Area3D`** for a `water_` marker — no script, so nothing ever called `setInWater` — **and left
its `collision_mask` at the default** (layer 1, world), which never sees a character body at all
(characters are on `CollisionLayers.CHARACTER`). Either half alone was fatal, and together they look
identical to "the swim feature was never written". It now bakes a real `WaterVolume` with the
character mask and its own layer cleared (it detects; nothing needs to detect it).

**ONE volume covers every body of water, and it can because the land is above all of it.** The flat
island is at `BASE_Z` (0.00) and the surface is at `SEA_LEVEL_Z` (−0.60), so a box whose TOP is the
water line cannot touch a character standing on land however far it spreads — while the bay, the
lagoon and the open sea are all holes in that land and are covered for free, with no per-body plate
to author and none to forget. `build_island_base.build_markers` emits it as `water_sea`,
4608 × 43.4 × 4608 m, top at the water line. `Character` then decides wade-vs-swim from the true
depth **under the body**, so the beach wades and only real depth swims.

**THE BEACH IS THE PART THAT IS EASY TO GET WRONG WHILE FIXING THE FALL-THROUGH.** The land stops
0.60 m above the water, so a sea floor starting at the waterline rings the island with a 0.80 m wall
— swimmable *to*, impossible to climb, because `stepHeight` is 0.35 m. You would have replaced
falling out of the world with being locked out of it. `SHORE_Z` is **−0.20**: the floor starts 0.20 m
under the land, which is one step, and slopes ~27 m of DRY sand before it passes the water line.
Measured on the built mesh over 16 shore rays: **9 natural coasts, worst 1 m step 0.206 m**, 0 samples
with no surface. The 5 cliff and 2 quay headings are walls the plan authored on purpose
(`Platform` keeps its full step against the sea) — swimming to one and finding no way up is expected
there, and ladders and slipways are content, not a ground defect.

**`W10` — the logic wall.** `world.WorldBounds`, baked from a `bounds_world` marker
(`halfExtent` = the world square, 2016 m; `softMargin` 80 m; `floorY` −64 m). Inside the soft band
the body is pushed inward with a force ramping quadratically from nothing to `pushSpeed`; past the
edge the position is clamped back and the **outward component only** of the velocity is cancelled,
so turning round and swimming home meets no resistance at all. Players every frame off
`PlayerRegistry`; everything else in the `characters` group on a 0.5 s sweep — the cadence and the
reason `maintainTraffic` already uses.

Not an invisible collider, and the three reasons are worth keeping: a ring of static walls collides
with bullets, ragdolls and vehicles as well as with the player; a `CharacterBody3D` pressed into one
slides along it at full speed forever with no moment to hang a "you are leaving the area" on; and it
cannot do the thing this must also do — **recover a body that is already outside**, which a collider
would simply keep out there. The kill-Z is the same idea downward: below `floorY` the body is put
back at `PlayerSpawn` rather than falling for ever.

**The sea floor deliberately reaches PAST the wall.** `SEABED_MARGIN` (288 m — 24 cells, chosen so
the padded grid stays a whole multiple of the quadtree's 192 m root) extends the seabed to ±2304 m
around a boundary at ±2016 m. A wall standing on the last triangle of the world is a wall with a
hole under it the moment anything overshoots by one frame of motion. `_world_grid(margin)` pads in
WHOLE cells, so every interior sample lands on the coordinates the unpadded grid would have given
and both sheets are still cut from one set of `G.on_land` samples.

**The gate:** `blender/tools/check_island_water.py` — sea-below-land, surface continuity, the beach
step against `stepHeight`, cliff/quay classification, and the two markers. It is the water layer's
`check_island_ground.py`.

**In motion**, `HandlingTest.tscn` grew six more cases, all passing: dry ground is not water →
walking into deep water starts a swim (stance `SWIM`) → swimming back out ends it; the wall turns a
walker back (stopped at x = 15.4 against a wall at 20 after 10 s of walking outward); a body dropped
**80 m outside** is pushed back in within half a second; a body dropped below the kill-Z is rescued.

### `W2` — the network reaches the island again (2026-08-31)

**First, a number nobody had.** The 90.5% → 70.0% coverage pair this document quoted was worked out
by hand, once, and could not be re-checked after any change. `tools/island_v3_reach.py` is that
metric as a tool — land within 250 m of a road, by distance field over land samples, with the
unserved land grouped into connected PATCHES so the answer says *where* and not only *how much*
("every road is 260 m from its neighbour" and "one whole headland has no road" are opposite
problems with the same percentage). Its numbers are not comparable to the hand ones and are the
ones to use from here.

    before   7 roads, 14 148 m,  52.5% of land served,  4.71 km² unserved
    after   12 roads, 23 743 m,  70.9% of land served,  2.89 km² unserved

**What was actually missing was measured before anything was drawn.** Five patches, and the slope
mix inside each one is what decided the work:

| patch | area | where | ground |
|---|---:|---|---|
| 0 | 3.07 km² | the northwest | 51% steeper than 60%; only 27% flat |
| 1 | 0.64 km² | the airport island | 91% flat |
| 2 | 0.57 km² | the south-west shore | 99% flat |
| 3 | 0.23 km² | the harbour's own apron | 93% flat |

Three of the four were flat land with no road on it, which is not a terrain problem and never was —
**four authored arterials** close them (`Kitahama-dori`, `Nishihama-dori`, `Futo-dori`, `Kuko-dori`).
Each was measured flat and hole-free *before* it was written down, each is in plan units like
everything else in `island_v3_geom`, and each starts and ends on an existing vertex of another
arterial — the file's own "never in mid-air" rule, and also what makes the seeder build a junction
there instead of two roads that pass near each other. `Kuko-dori` runs Hama-dori → `AIRPORT_BRIDGE`
→ `AIRPORT_ROAD`, so `bridge_at` carries its 14 over-water stations and `W1`'s ramping machinery
puts it on the +24 m deck with no new code at all.

#### The hill road is DERIVED, because it cannot be drawn

`WESTRAD` and `TOUGE` have been in `island_v3_geom` since the plan was written — the hand-drawn
mountain roads, authored against the old contour PRISMS. Measured against the real heightfield for
the first time, they run at **48% and 105%**. That is the same finding as step 1's arterials and
nobody had re-asked it; a line on a map cannot know a hill.

`island_v3_terrain.hill_road` walks the field instead. The whole switchback is one piece of
trigonometry: write a heading as `cos φ · uphill + sin φ · contour`, and the height gained per metre
is `|∇z| cos φ`, so asking for the road's grade fixes `φ = acos(grade / |∇z|)` and nothing else is
free. On a 92% flank an 8% road runs 85° off the fall line — which is to say very nearly along the
contour, which is what a mountain road looks like from the air. Flipping the sign of the contour
term IS the hairpin.

**It is not a grid search, and that matters.** A* over the terrain cannot express this problem: on a
steep flank the legal headings lie within about 5° of the contour, a grid offers 8 or 16 of them, so
every edge it can take is illegal and it returns nothing — which reads as *"there is no route up
this hill"*. That is how this document came to record exactly that as a measured fact.

**Three things had to be got right, and each was a real defect first.**

- **The hairpins are part of the climb.** A half-turn of radius `R` on a slope `m` moves the road
  `2R` across the slope, so it gains `2R·m` in `πR` of path — on the spur (58%) that is 21 m in
  57 m, a 37% grade, and eight of them contributed more than half of the entire 280 m climb. Walk
  the legs at the limit and the alignment averages 10.1% against an 8.1% road. Nothing downstream
  can absorb that: `height_profile`'s grade cone can only RAISE, so it corrects an over-steep
  alignment by lifting its lower end until the whole thing fits — the first measurement of this road
  was a **57 m fill at the foot of the mountain**. A sky-road, green by every check, produced by a
  generator and a corrector that were each right on their own. `hill_road` therefore *bisects* the
  leg grade until the alignment's own average (`alignment_grade`) is inside the limit.
- **A mountain road is BENCHED, so a per-sample step is the wrong question to ask of it.** Its
  hairpins are cut-and-fill platforms: the GROUND across a turn swings far more steeply than the
  road ever does, and measured like a trunk road the only legal alignment on the hill reports 85% at
  every hairpin. `BENCHED_CLASSES` names the classes judged on the pair that actually has to hold —
  the average grade over the whole length, and a ceiling (`MAX_BENCH`, 25 m) on what the benching
  costs in structure. Both gates use it, from one function, so neither can pass what the other fails.
- **The station rule had two owners and the docstring believed the wrong one.**
  `seed_district_roads`'s module header has said "resample … THROUGH the authored points" since the
  file was written; the code laid `total/spacing` stations by arc length and let the shape fall
  where it may. Invisible on a plan arterial — 7 gentle points across the island — and fatal on a
  derived alignment whose shape IS its vertices: the shrine road's hairpins are 38 m arcs against a
  70 m spacing, so every corner was cut, the ground was sampled ACROSS each turn instead of round
  it, and the cone filled the difference. **25.6 m of pier where the model predicted 14.7**, with
  the model and the build each internally consistent and disagreeing by 11 m of structure.
  `island_v3_terrain.stations` is the one owner now, used by the seeder and by `bench_depth`.

The result is `Shrine touge`: **3 573 m of road climbing 0 → 280 m at an average 7.8%**, 51 authored
points, worst fill 16.3 m — a piered hairpin stack up the spur's eastern flank to the shrine
plateau, which was 0.36 km² of flat land with no way to reach it.

**The massif stays wilderness, and that is a decision.** 51% of the unserved northwest is steeper
than 60%, its only flat ground is the 760 m summit, and 697 m of climb at 8.1% is 8.6 km of road —
more tarmac than the entire rest of the island — for scenery. One `hill_road` call adds it the day
that changes; what is left unserved now is 64% steeper than 60%, which is a mountain, not a gap.

**A fourth defect fell out of the extra roads, and it was pre-existing.** The mouth splice held
segment indices computed against the chain *before* any splice rewrote it; the descending-order
walk was only ever an argument about which mutations invalidate which indices, and at 15 crossings
instead of 8 it raised an `IndexError` on a chain a previous splice had shortened. A stale index is
the defect, not the order it is used in — the crossing POINT is the durable fact (it is a place on
the ground, not an offset into a list), so the segment is re-found from it against the chain as it
is now.

#### The ground does the work, the wall is the last resort — and the sea is 4.6 km (2026-08-31)

From a walk-test question: *"why need `WorldBounds` — could a big sea below common ground not cover
most of it?"* It can, and it took two readings to find the number.

**Two layers, two jobs, and both were already built.** `Sea` (a translucent plate at
`SEA_LEVEL_Z`, no collision) plus `water_sea` (one Area3D over the whole world) answer
**swimming** — water with collision would be a lid you cannot dive through, water without it stops
nothing. `Seabed` (real, sloped, collidable, `SHORE_Z` to `SEA_FLOOR_Z` over `SHELF_RUN`) answers
**not falling**. What was ever in question was neither: only how far the floor under the water
reaches, which is `SEABED_MARGIN`.

| `SEABED_MARGIN` | sea floor | wall | water from land to wall (min / median) | verdict |
|---:|---|---|---|---|
| 288 (was) | ±2304 m | ±2016 m | — | the wall did the ground's job: a fence **700 m offshore** |
| 1728 | ±3744 m | ±3600 m | 1704 / **2057 m** | the ground did all the work: *"so empty"* |
| **288 + inset** | **±2304 m** | **±2160 m** | **202 / 498 m** | both |

The fix was never the floor's size — it was that the wall sat at the world square while the floor
reached past it, so the 288 m behind it was decorative. `BOUNDS_INSET` (144 m) puts the wall inside
the floor's own edge instead, which is the rule that matters (a wall on the last triangle has a hole
under it the moment anything overshoots by a frame's motion) and moves it 144 m further out for
free. Over 1 km of water on only the two diagonals, where a square sea round a roughly round island
is furthest out.

**Why not the 3.5 km that was asked for: the LAND does not fit inside it.** The main coastline is
~±1330 m, which is what the eye reads — but the offshore airport reaches y = −1976 m and the north
coast y = +1960, so the land's own bounding box is **3.72 × 3.94 km**. A ±1750 m sea leaves 226 m of
the airport and the north shore hanging over the edge with nothing under them. The next step down
that does tile the quadtree, 192, puts the wall 88 m from the furthest land — inside its own 80 m
soft band, so a player standing on the airport's south quay would already feel the push.

**Both halves of `WorldBounds` earn their place, and they earn it differently.** The kill-Z
constrains the player not at all — it fires only when a body is already below the sea floor, and it
is the only fall-out-of-world net a CHARACTER has (a vehicle gets `maintainTraffic`'s Y < −30
reclaim; a character gets nothing). The XZ wall is genuinely reached: 500 m of water is under a
minute in a boat. It stays a LOGIC wall for the three reasons already recorded, of which the
deciding one is that a collider ring can never recover a body that is already outside.

#### A shallow crossing needs a big pad#### A shallow crossing needs a big pad — `W12`, and it was never a hand-drag (2026-08-31)

`Auto Setback` is the remedy `pad_not_star_shaped` names, and on the island's two folded pads it
answered **"moved 0"**. It was right by its own lights: `recommended_tail_length` grows the tail
until every turn MOVEMENT fits inside the pad — a statement about the paths cars drive — and at
18.2 m every turn fitted. It says nothing about the arms' own asphalt.

The ring walks the CAPS by bearing, so it stays in order exactly while the left corner of one arm
sits at a smaller bearing than the right corner of the next:

    atan(half_a / d) + atan(half_b / d)  <=  the angle between them

which at equal half-widths is the familiar `h / tan(theta/2)`: **14.5 m at a square crossing and
27.2 m at 56 degrees**, and that is the whole story of why a shallow junction has to be a big one.
Chuo × Rinkai crosses at 56.2° with 29 m arterials and its mouths sat at 18.2 m.
`point_solve.corner_clearance` / `corner_setback` add the term; `auto_setback` takes the max of it
and the turn search.

Deliberately the CAP-ORDER test and **not** "where the outer edges cross"
(`(h_b + h_a cos t)/sin t`, 30.8 m at 56°), which is the other natural reading and over-demands by
half — the fillet is allowed to bulge past that point, and asking for it would grow every square
junction from 14.5 m to 20.5 m for a fold that is not there. Self-tested at three angles, plus the
invariant that a corner belongs to both arms and cannot depend on which is asked first.

#### The touge is IN the mountain now, not on stilts (2026-08-31)

Two reports from one walk-test: *"the island seems to have a lot of modifier cuts for the road, but
they don't really have impact on the ground below"*, and *"for the touge, the road should be into
the mountain rather than use a bridge"*. They are the same finding.

**The ground cut was never broken** — measured, because the first probe agreed with the report and
was wrong: sampling the evaluated road meshes found 66 points with ground above them, and every one
was a PIER FOOT or swept edge geometry rather than tarmac. At the road's own centreline stations the
answer is **1 of 419**, 0.18 m, at the harbour platform's step. `cut_ground` builds a vertical prism
from −40 m to +40 m over each band's footprint and differences it out; nothing survives inside a
road's footprint. Hand-building the cut would be work for nothing.

**What was wrong is that the road was floating over the hill it had cut.** `height_profile`'s grade
cone only ever RAISES — right for the bay crossing (you do not cut a bridge into water), wrong for a
hill: it answered every hairpin with a pier and left **71 of the touge's 90 stations elevated, up to
16.3 m**, standing over a hillside the cut had already punched a road-shaped slot through. That is
exactly the "lots of cuts that do nothing" the report describes.

`island_v3_terrain.bench_profile` is the midpoint of the two envelopes — the least grade-legal
profile above the ground and the greatest one below it. It is grade-legal for free, because
`|dz| <= limit * span` is convex and the average of two profiles that satisfy it satisfies it too:

| | max fill | max cut | worst grade |
|---|---:|---:|---:|
| raise-only | 16.27 m | 0.00 m | 8.10% |
| bench = midpoint | **8.13 m** | **8.13 m** | 8.10% |

Built, the touge went from 71 elevated stations to **29, up to 8.2 m**. It applies only to
`BENCHED_CLASSES`, so both bridges are untouched.

**What it does NOT do, and the report predicted it: batter the cut faces.** `road_support` grows an
embankment toe for FILL and columns for PIER and builds **nothing at all** for CUT, while the ground
cut is that vertical prism — so a benched stretch comes out in a vertical-walled slot rather than
between sloped faces. A cut batter (the `fill_footprint` equivalent for the other sign of `delta`)
is the piece the road kit still owes a mountain road; until it exists those faces are hand work.

#### A lane curve carries no up vector

Godot bakes one by propagating a frame along the curve, rotating it about an axis derived from
consecutive tangents — and on the benched hairpins that axis comes out 0.05% off unit, printing
`The axis Vector3 (...) must be normalized` once per lane (19 on an otherwise clean load). Nothing
reads it: `PathLaneRoute` takes `getBakedPoints()` for XZ arc length and there is no `PathFollow3D`
on a lane anywhere in the project. `WorldBaker` sets `up_vector_enabled = false`, which removes the
computation rather than silencing its complaint.

#### Three seeder defects `W2` uncovered (2026-08-31)

All three were latent for as long as the plan's roads only ever crossed each other **in mid-span**.
`W2`'s four new arterials each *terminate on another road's own vertex* — deliberately, because
that is `island_v3_geom`'s "never in mid-air" rule — and that one authoring choice walked into all
of them. They were found as junction pads that fold and then as a build that refused outright, and
each was a fact with two owners or a place with two names.

- **`clip_runs` densified, so `resample` could not tell an authored vertex from fill.** It sampled
  the plan polyline every 8 m; once the station rule started preserving authored vertices (§`W2`)
  every sample looked like one and survived the 10 m floor. The island came out with **1 504
  stations instead of 367** — four times the road geometry and four times the build. It now emits
  the polyline's own vertices plus the boundary crossings; `step` is only how finely the crossing
  is resolved, and a sample in the middle of a straight segment says nothing.
- **One place was found as two crossings, or as three.** `crossings` is pairwise and returns the
  same crossing twice when it lands on a chain's own VERTEX (both segments meeting there hit the
  other chain at the same point) — the splice then inserted two mouths either side and called the
  pad with six arms, two pairs coincident. And where **three** roads meet, three pairwise crossings
  cut the same chain three times an arm's length apart, giving two junctions on top of each other.
  Crossings are deduped by point, then **clustered into places**: each chain is cut once per place,
  at the cluster's centroid, and every mouth of that place goes into one junction. Nishi-dori ×
  Nogyo-michi × Shrine touge now builds as a single three-arm crossing.
- **`stations` emitted the last point twice.** The final even mark is `total * n / n` and the final
  cumulative length is `total` — the same number reached two ways, differing in the last bit often
  enough that `set` kept both, and the "never drop the ends" rule then kept the second. Two stations
  1e-13 m apart: `station_coincident`, "a zero-length taper", which refuses the build. Marks are
  rounded to a micron before the set.

Result: the build is green again — **0 errors and 0 warnings** — and the station count is back to
426. The two folded pads that survived this round were not caused by the new roads and are closed
separately, above.

#### Two things the new roads taught the ground gate

`check_island_ground.py` measures the BUILT mesh, and it had two rules that were right for the plan
blend and wrong for the piece.

- **A road-kit bridge is not in `BRIDGES`.** That collection is `build_island_v3.build_bridges`'s
  output and exists only in the plan blend; in `Island_base` the crossing is built by the road kit
  as part of the road (`W1`), so there is no separate deck object to find — and asking only for one
  reported Hama-dori and Kuko-dori as **142 stations of open water** while both stood on their own
  columns. The carrier now also accepts `island_v3_plan.bridge_at`, which is the same question that
  PUT the road up there, so the two gates agree by construction rather than by coincidence.
- **A station on a deck is not standing on the ground beneath it**, so it must not contribute a
  ground STEP either. The airport bridge lands on the +8 m reclaimed platform whose seaward edge is
  an authored quay wall, and measuring that wall as a grade the road has to climb reported a **7 m
  WALL under a road 16 m above it**. The carrier is asked at every station now, not only where the
  ground is missing, and a carried station is skipped by the step scan the same way a hole already
  was.

### Lanes are 4.5 m — an arcade number, not a highway one (2026-08-31)

Reported from the walk-test: *"the current vehicle will occupy entire road… on arcade and GTA the
road is slightly larger."* Measured: the car's hull is **2.00 m** wide and its wheels sit at ±1.1 m,
so its real footprint is ~2.4 m. In a 3.25 m lane that is **74% of the lane** with 0.42 m of air
either side — correct to the book (a real lane is 3.25–3.5 m and a real car is 1.8 m) and wrong for
the game, because a 2.4 m car in a 3.25 m lane drives like a lorry in a tunnel.

`seed_district_roads.LANE_WIDTH` is **4.5 m**, which leaves 1.05 m either side (53% of the lane).
`point_model`'s own default moved with it, so a hand-authored road comes out the same width as a
seeded one, and `island_v3_plan.ROAD_HALF` carries the arithmetic (T2 = 4 lanes + 3 m median + two
4 m footways = 29.0 m, half 14.5; T3 = 16.0 m, half 8.0). The lane is the player's margin for error
and generosity there is what makes a car feel placeable at speed.

### The speed-feel overlay is gone (2026-08-31)

Reported: *"too much… impact the real driving visual/hard to see road."* The package was an 18°
FOV widening plus a 5° throttle surge, a 6° NOS kick, a 2 m spring-arm pull-back **and** a
full-screen peripheral speed-line shader.

The overlay is **deleted** — the `SpeedFX`/`SpeedLines` nodes, the `ShaderMaterial`, and
`SpeedLines.gdshader` itself, not merely hidden, so nothing switches it back on by accident. The
camera terms are kept as knobs and ship at **0**, with their previous values recorded on each field:
a camera that cannot react to speed at all is a decision to take deliberately rather than by
deletion, and one edit brings any part of it back.

The replacement is planned to be **world-space rather than screen-space** — a trail light or a
wheel/exhaust effect that sits on the car and never covers the road. Nothing in the camera is in its
way; it belongs on the vehicle.

### `W14` — a crossing on a shared vertex was decided at the ninth decimal place (2026-09-04)

Found from the other end: the artist opened `Island_base.blend`, saw the airport link starting as a
stub lying **on Hama-dori's carriageway, joined to nothing**, and deleted it by hand — the two
points from Hama-dori's own station up to Kuko-dori's third. This is that repair traced back to
what caused it, and the cause turned out to be four defects, not one.

**The plan's rule is that an arterial terminates ON another arterial** — "never in mid-air",
`island_v3_geom.ARTERIALS` — so seven of the island's sixteen crossings sit exactly on a shared
vertex. `seed_district_roads.seg_intersect` solved those to `t = 1, u = 0` **in exact arithmetic**;
`resample` walks the polyline accumulating float error, so the station that should land on the
shared vertex lands ~5e-7 m off it, and the strict `0.0 <= t <= 1.0` then read `t = 1.0000000006`
— 0.6 nanometres past the end — and returned `None`. Whether two roads that meet were connected at
all came down to which side of 1.0 the ninth decimal fell on:

| place | `t` | found? |
|---|---|---|
| Nogyo x Kitahama | 0.99999999 | yes |
| Rinkai x Nishihama | 0.0 (exact) | yes |
| **Hama x Kuko** | **1.0000000006** | **no** |
| **Port x Futo** | **1.0000000047** | **no** |
| **Yamate x Nishi** | — | **no** |
| **Port x Nishihama** | — | **no** |

**12 crossings were found; there were 16.** What the four missing ones cost, in the shipped file:

- **Kuko-dori** (the airport link) began with an 84 m stub from Hama-dori's carriageway with no
  junction at either end of it — the thing the artist deleted;
- **Futo-dori** (the harbour apron, 839 m) was a separate component of the graph. Its first station
  sits at exactly the same coordinates as Port road's last one, `(-600, -1720, 4.00)`, with no link
  between them. No car could ever reach it;
- **Yamate-dori**'s west end and **Port road**'s north end were unattached the same way.

**And the gate was 0 errors, 0 warnings the whole time**, which is the part worth keeping. Every
check in `point_validate` reads the authored graph and asks whether it is consistent — and a
junction that was never authored is consistent with itself: no link to be asymmetric, no chain with
a hole, no clique to be incomplete. `check_meetings` is the eye that was missing (`road_end_unjoined`,
WARN): a point where a road STOPS, standing within `CHAIN_TOL` of a point in another road with no
link to it. In 3D and open-ends-only, so a street running under a deck is not a finding.

**The fix is a tolerance in METRES, not in parameter space** (`seed_district_roads.TOUCH`, 5 cm).
`t` and `u` are fractions of two segments of different lengths, so one number cannot mean the same
thing to both; 5 cm is five orders of magnitude above the drift and three below `MIN_SPAN`, so it
can never invent a crossing between two roads that merely pass close by. The double hit a vertex
touch produces was already `crossings`' job to dedupe.

**Kuko-dori now starts where it actually joins the network.** With the detector fixed, the stub
comes back as a proper T — and it should not, which is the artist's call and now the plan's: the
Hama-dori vertex is 53 m (106 m built) short of where Kuko-dori crosses Rinkai-dori, and two pads
that close together on an 8% airport link buy a stretch of road Rinkai-dori already provides. The
head vertex is **derived, not copied** (`island_v3_geom._terminate_on`): a literal would make that
file the second owner of Rinkai-dori's alignment, and the road would silently stop 40 m short the
day somebody moved the other one.

**Result** — `blender --background --python blender/tools/build_island_base.py`:

    16 crossing(s), 12 road(s)      (was 12)
    == gate: 0 error(s), 0 warning(s)
    231 -> 225 lane(s), 10 junction(s)

and the flow report's dead ends fall from **33 to 15**, the 15 remaining being real: the coast, the
apron, the runway. `check_island_ground.py` still reads 12 of 12 and reach is unchanged at 70.9% —
the 106 m Kuko-dori lost was duplicate.

### `W3` — the island has a pavement now, and it never had one before (2026-09-04, closed)

Asked for from a walk-test ("include road gutter/curb on ground roads, and for bridge/higher road
points, will use wall for most"). Both halves turned out to be one defect, and it is `W3` seen from
the other end.

`W3` recorded that "the raised surface is the carriageway (0.160 m proud of the ground on both
sides); the pavement is not raised at all". Measured across Yamate-dori station 15, what is 0.160 m
proud is the **raised median** — the only piece of edge furniture with a width to build.
`rka_walk_hl` was **0.0 on every sample of every road on the island**.

**The cross-section is the ROAD's, not the station's.** `seed_district_roads.author_road` wrote the
tier's footway width onto every point, with a comment explaining that it is a point field — which
is true of the schema and false of the model. Every station the seeder authors is `INHERIT`, and
`point_model.resolve_point` gives an INHERIT station its road's `base` with only the four
`DELTA_FIELDS` (the lane counts) taken from the point. So the write was discarded by the very next
read, silently, since this file was written. `new_road` already sets the lane counts, lane width,
median and design speed on the base for exactly this reason; the footway and the kerb belong beside
them.

**The wall needed nothing.** `point_solve.solve_road` has derived it correctly all along — a road
nobody may walk on is fenced end to end, one they may walk on is fenced where it is off the ground
(`delta >= BARRIER_MIN_DELTA`, or on piers). What the missing footway did was leave it standing on
the kerb line; with a pavement it stands at the outboard edge of the pavement, where it belongs.

Measured on the rebuilt piece, visual and `-colonly` proxy agreeing at every sample:

| | Yamate-dori st. 15 (ground) | Hama-dori st. 37 (bay bridge) |
|---|---|---|
| carriageway | +-10.5 m at 0.000 | +-10.5 m at 18.000 |
| kerb + footway | 10.5 -> 14.5 m at **+0.150** | 10.5 -> 14.5 m at **18.150** |
| barrier | none (`delta` 0) | **1.0 m**, at +-14.66 m, top **19.15** |
| median | +-1.5 m at 0.160 | +-1.5 m at 18.160 |

The wall is in the collision proxy, not only the visual: `hama_dori_2_walk-walk-colonly` carries
512 vertices above 18.4 m, so a car cannot drive off the bay bridge and the pavement is walkable.
321 of that run's 491 carrier samples carry a wall — the elevated part of it, and nothing else.

### `W16` — the pad was cut on one plane and the street on another, and sized for a road 8 m narrower than it is (2026-09-04)

Two user reports from the first walk of the new pavement, and both are the footway finally reaching
a solve that had never been asked for it.

**(a) "the segment edges to junction edges are not align... when the intersection and connection
are not perpendicular cross".** Measured on the shipped file, from each road flank's own kerb line
to the pad cap corner it has to meet:

| junction | crossing | worst gap |
|---|---|---|
| JCT_0008 Yamate x Hama | 95 / 85 deg | **0.000 m** |
| JCT_0002 Chuo x Yamate | 84 / 96 deg | **0.000 m** |
| JCT_0005 Hama x Rinkai | 58 / 127 deg | **0.940 m** |
| JCT_0007 Rinkai x Kuko | 83 / 146 / 131 deg | **2.230 m** |

Square crossings were perfect and skew ones were not, which is the shape of a **direction** bug.
`point_model.station_axis` is the one owner of "which way does a station face" (§8f.1) and the pad
cuts every cap on it. The carriageway is swept by `road_points.chain_tangents`, which is handed a
**RUN** — and a run stops at the mouth, so its end tangent falls back to "the single available
chord", which is right for a road that ends and wrong for a mouth: the road does not end there, it
carries on across the pad, and `station_axis`'s central difference reaches the mouth on the other
side to say so. At Rinkai x Kuko the cap was cut at **16.07 deg** and the tarmac at **28.3 deg** —
12.2 deg apart, which over a 21 m carriageway is the 2.23 m notch.

`point_profile.run_end_axes` closes it: a run's first and last AUTO station takes its plan-view
direction from the chain, keeping the **Z slope of its own chord** so a graded approach (the bay
bridge, the touge) does not get a kink in its vertical profile. Wired into the sweep
(`solve_road`), the export (`build_run`) and the overlay (`centreline_runs`) so all three cut on
the same plane. **Worst gap over all ten junctions: 3.721 m -> 0.000 m.**

**(b) "2 angles seem correct, but 2 other angles seem more square... the sidewalk is not align edge
but kind of overlap each sidewalk".** `corner_clearance`'s own docstring quotes "14.5 m at a square
crossing" — a T2 road's **deck** half-width (4 lanes + median + two 4 m footways = 29.0 m). Its
caller passed `half_in`/`half_out`, which are `lane_profile.paved_extents`: the **carriageway**,
10.5 m. Indistinguishable for as long as the footway width never reached the solve (`W3`), and the
moment it did, every pad on the island stood revealed as sized for a 21 m road that is 29 m wide.

What that costs is a corner with no room to turn in. At Yamate x Hama, set back 14 m, the two arms'
cap points at the 85 deg corner were **3.31 m** apart: the kerb had to turn 95 deg in 3.3 m — a
~2 m radius against an authored `fillet_radius` of 6 — and a 4 m footway swept round a 2 m radius
folds through itself. The 95 deg corner had 6.58 m and looked fine. Two right, two square, exactly
as reported. `corner_setback` now measures the deck (`half + 2 * walk`, the same arithmetic
`edge_run_values` sweeps), which is the number its own docstring always meant:

| | before | after |
|---|---|---|
| shortest corner run on the island | **3.15 m** | **14.95 m** |
| Yamate x Hama corner runs | 8.05 / 4.32 m | **15.18 / 11.23 m** |
| Yamate x Hama setback | 14.00 m | 17.94 m |
| Chuo x Rinkai (56 deg) setback | 21.67 m | 29.16 m |

29.16 m at 56 degrees is `corner_clearance`'s own published figure (27.2 m plus the 2 m margin) —
the pads are the size that function has been asking for since it was written.

Gate 0/0, `check_roads.sh` PASS=18, 12 of 12 roads on ground, reach unchanged at 70.9%, flow report
0 broken / 0 misjoined, navmesh 1624 vertices.

### Open items — the follow-up register

Everything found and not yet closed, with a stable tag so it can be referred to. The **why** for each
lives in the section named on its last line; this list is the index, not a second account of it.
Order is roughly by how much it costs to be wrong about, not by effort.

#### Closed on 2026-09-06

**`W13` + `W21` — the road DEFORMS the ground now; it no longer cuts it. BOTH CLOSED.** Four rounds
of fixing the boolean each found a real defect (`W22`: the cutter was a zero-thickness sheet and no
boolean had ever cut anything; §8u: the sampler raycast the terrain the PREVIOUS build had cut, so
29 of the touge's 66 stations found no ground at all; §8u: a switchback's cutter passes through
itself and the exact solver leaves such ground standing, silently). The measurement that settled it:
from identical inputs — same network, same terrain, same sampled `ground_z` byte for byte — a second
Build took the touge from 2 buried stations to 9. **That is the tool, not the road.** And it was
never the industry approach: terrain is a heightfield, a heightfield has no topology to cut, and
from Unreal's `Deform Landscape to Splines` to a Houdini road HDA the operation is to write the
road's elevation INTO the field.

`island_v3_terrain.Carve` is that rule — `z = min(z, road_corridor_z)`, **carve-only**, because
`road_support` already owns FILL and because a rule that raises ground would fill the bay under
`W1`'s bridge. `build_island_base` emits the ground **twice**: natural (roads are routed and graded
against it), then carved to the finished alignment — one direction of derivation, never back. A
`min` cannot open a hole, so **the collider is now a copy of the visual ground**, which is `W21`
closed by construction rather than worked around. Measured, 28 295 samples along and across every
road, both sheets:

| | boolean | carve |
|---|---|---|
| ground proud of the road, visual | 2 of 225 stations, worst 3.20 m | **0**, worst **0.00 m** |
| ...on the collider | never cut at all | **0**, worst **0.00 m** |
| `Ground` vertices | 19 477 → 30 577 (36 039 with `use_self`) | 19 477 → **21 547** |
| collider vs visual | different meshes by design | **identical** |
| repeated Build | 2 → 9 buried stations | **stable** |

The verge is what makes a 12 m grid exact: the flat shelf extends one full cell past the pavement,
so no triangle spanning the road can have a raised corner — **1 185 377 samples, 0 proud, 0.000 m**,
against 450 proud at 3.508 m with no verge. Subdivision was measured and rejected as the lever
(12 m → 3 m took the worst intrusion only 3.508 → 0.647 m, still past the 0.35 m step, for 16× the
vertices). A hairpin needs no case: the lower leg wins between two legs and each leg clears itself.

`point_build.cut_ground` and its five helpers are deleted, with the `Cut Ground` build option and
the exporter's cutter-hiding workaround. One thing it introduced and one eye was put on it: a
carved ground is not the natural ground, and a second Build was stamping "the ground meets the road
here" onto **195 of 225** stations (biggest 9.46 m); `point_build.CARVED_FLAG` now refuses that with
a message — re-measured, 0 of 225.
→ `ROAD_POINT_GRAPH.md` §8u, §8v.

#### Closed on 2026-09-05

**`W23` — one streaming concept. DONE.** There were four names for overlapping things (`piece`,
`district`, `region_` marker, `WorldZone`); there are now two. `district` is retired as a MECHANISM
and survives only as a place name (`zone_id = "harbour"`) — the world is one continuous island, so
the 504 m grid tile with neighbours, a `.seam.json` and a registry position has nothing left to be.

| renamed | to |
|---|---|
| `world.WorldZone` / `WorldZoneMarker` / `WorldZoneManager` | `world.Zone` / `ZoneMarker` / `ZoneManager` (213 references) |
| `DistrictBinaryConverter` (export `districtsDir`) | `PieceBinaryConverter` (export `piecesDir`) |
| `MultiDistrictStreamTestHost` + its scene | `MultiZoneStreamTestHost` / `MultiZoneStreamTest.tscn` |
| `hosts/ConvertDistricts.tscn` | `hosts/ConvertPieces.tscn` |
| `src/main/resources/com/openworld/world/districts/` | `.../world/pieces/` |
| the `project.godot` AutoLoad | `ZoneManager=` |

**`piece` was deliberately left alone** — it is the authoring/bake unit, a build-pipeline word that
collides with nothing, and merging it into `zone` would fuse two genuinely different things (a file
you bake vs a runtime streaming volume). **`IntersectionZone` was also left alone**: it is an
`Area3D` marking a junction (`GROUP = "intersection"`), not a streaming zone, and folding it in
would have been a find-and-replace pretending to be a decision.

Verified at every stage rather than at the end: `./gradlew build` clean after each rename (the
registrar is regenerated from the class names), `SoloPiece.tscn` loads with no error, and a full
`build_piece.sh Island_base` runs end to end through the new path — `WorldBaker` -> `world/pieces/`,
`NavBaker` 1710 vertices, `PieceBinaryConverter: done — 1 converted`.

**Blender was untouched, as measured:** `blender/**` mentions the old names only in prose, never as
a class name or a `res://` path. Prose that describes HISTORY — the archived 6x6 grid,
`District_*.blend`, `check_seams.py` — is left standing on purpose; rewriting it would make the
record lie about what was built. New writing uses **zone** and **piece** (CLAUDE.md, "Vocabulary").


**`W22` — the ground cut had never cut anything. CLOSED.** `cut_ground` built its cutters with
`bmesh.ops.solidify`, which produced no thickness: all 32 measured `z_min == z_max == -40.00`, every
boolean was a no-op and the evaluated terrain came back with exactly its base vertex count. Invisible
while every road was draped ON the ground; the buried pad (`W20`) and the touge's slot (`W13`) are
the same defect seen twice. The solid is built explicitly now — a TUBE for a road (a self-intersecting
outline has no cap the exact boolean can arrange), coincident rings welded rather than emitted as
degenerate faces, and the upward reach stopping at each section's own daylight. With it,
`road_support.cut_footprint`/`CUT_SLOPE` give a CUT the batter only a FILL had, and `CUT_MAX` — which
said 3 m while `island_v3_terrain.MAX_BENCH` said 25 and the touge digs 8.1 — becomes the one owner.
Measured: samples cut **6% → 57%**, and ground standing proud on the other eleven roads
**up to +0.42 m → +0.00 m**.
→ `ROAD_POINT_GRAPH.md` §8t.


**`W20` — the port pad was buried by its own ground. CLOSED.** A pad is a plane through its mouths
and the ground under it is not one; where the ground rises inside the footprint the pad sits under
it, and because the island's collision mesh is deliberately **uncut** (continuous collision is the
base layer's job) the player walks on terrain the eye says is not there. Measured at the port
crossing, four arterials on ground falling 2.09 m across the pad: **0.924 m** of ground standing
proud of the pad, against a 0.35 m step limit. `seed_district_roads.pad_lifts` probes the footprint
and raises every mouth of that pad by one uniform offset, applied as a floor **before** the grade
cone so the approaches ramp instead of stepping. `PAD_BURY_TOL` (5 cm, `point_edges.BURIED_TOL`'s
idea) keeps it off the eight flat pads, whose raw burial measures 0.000000–0.000122 m. One pad
lifts, by **1.17 m** — the user's hand-lift, derived.
→ `ROAD_POINT_GRAPH.md` §8s.

#### Closed on 2026-09-04

**`W15` — a joint only worked while the two roads were straight through. CLOSED, by rounding the
corner rather than by merging the roads.** The three joints ended **24-28 m apart** at 98-124 deg
with a link across the hole. `seed_district_roads.fillet_corner` trims both chains by the tangent
length, lays an arc of `CORNER_RADIUS_FACTOR` (3.0) × the DECK half-width — 43.5 m on a T2 — and
**splits it at its midpoint** so each road carries half the turn and they meet TANGENTIALLY, which
is the straight-through joint the seeder already built correctly. Each street keeps its name and
every lane id. Then `point_export.wire_joints` emits the lane hand-over: rounding the corner moved
the flow verdict from `open_end` to **`broken`** (geometry fixed, graph still silent) and the edge
took it to **0**. Measured: gap **25.6 → 0.00 m**, deflection **106/124/98 → 7.3/7.5/7.3 deg**.
→ `ROAD_POINT_GRAPH.md` §8r.


**`W19` — the port junction was a 5-arm pad with a stub, and the island carried 249 stations that
said nothing. CLOSED.** Three findings from one session of hand-editing `Island_base.blend`.
(1) `Merge Points` carried the links that left a collapsed run and nothing else, so merging a plain
station into a crossing left the pad a **component, not a clique**, with mouths still typed
`SEGMENT` and unparented from the `JCT_*` handle — 8 gate errors from one gesture.
`point_ops.complete_junction_cliques` is the shared owner now (`Merge Points` calls it, `Repair
Links` recovers a file already broken: 8 → 0). (2) Four arterials meet at the port and the plan
wrote that one place as **three coordinates** — Chuo-dori's head sat 76 m past it, outside
`CROSSING_NEAR`, so it ran *through* the pad to a dead end and contributed two arms. All four
terminate on the shared vertex now: **5 arms → 4**, no stub. (3) `resample` had no opposite number,
so every straight leg carried a station every 70 m that was exactly collinear;
`seed_district_roads.simplify` (Douglas-Peucker, road at 0.25 m ∪ ground at 1.0 m, mouths protected)
takes them back — **433 → 184 stations** for **+0.02 m** of drape error (worst at-grade road-to-ground
gap 0.32 → 0.34 m, against a 0.35 m `stepUpLedge`).
→ `ROAD_POINT_GRAPH.md` §8q.


**`W18` — the seeder guessed the stop line, and a mouth walked off its own road. CLOSED.** The
island's `.blend` had two stations deleted by hand at the Kuko junction; comparing it against what
its own seeder produces showed why they were there. `seed_district_roads` placed every mouth at a
provisional **14 m** and pruned the stations inside that window; `Auto Setback` then solved
**17.9–35.5 m** and moved the mouth out over the top of what had survived. `point_solve
.solved_setback` is now one owner, asked by both — the seeder with planned arms
(`place_setbacks`), before an Empty exists. The gate could not see the result at all (two stations
with the same profile make `check_tapers` return on `dw == 0`), so the island shipped an ordinary
station **0.19 m** past Rinkai-dori's mouth at the Chuo crossing plus two more at 5.7 and 6.9 m,
reading 0/0: `check_mouth_clearance` / `station_crowds_mouth` is that eye. And `auto_setback` set
each mouth back along a ray from the pad CENTROID, which is on none of the roads — 12 m off the
crossing at the port junction, enough to put `port_road`'s approach **40 deg** off its own
alignment and 3.54 deg from Chuo-dori's arm. The centre is projected onto each mouth's own axis
now, so a mouth slides along its road and never sideways off it.
→ `ROAD_POINT_GRAPH.md` §8p.

**The airport link leaves the crossing, not 104 m past it (with `W18`).** `island_v3_geom` used to
put Kuko-dori's head where its own first leg met Rinkai-dori — 104 m east of where Rinkai already
crosses Hama-dori, so the two pads stood with 48 m of carriageway between their stop lines and
~150 m of Rinkai-dori read as one unmarked lozenge. That is the same defect the *previous* Kuko fix
was for; moving the head off Hama-dori's vertex halved the distance and did not close it. Three
roads meeting at one place is one junction: `_crossing_of` + `_terminate_at` put the head on the
crossing itself and the island's ten pads became **nine**, the east one a 5-arm star whose tightest
corner is 43.8 deg. (`_terminate_on` is gone — it had no other user.)


**`W14` — a crossing on a shared vertex was a coin flip. CLOSED.** `seg_intersect`'s bounds are in
metres now (`TOUCH`, 5 cm), not in parameter space: 12 -> **16 crossings**, four severed places
rejoined (Hama x Kuko, Port x Futo, Yamate x Nishi, Port x Nishihama), dead-end lanes 33 -> 15.
Kuko-dori starts at Rinkai-dori, derived from Rinkai's own vertices, which is the layout the artist
authored by hand. `check_meetings` / `road_end_unjoined` is the eye that was missing.
→ "`W14` — a crossing on a shared vertex was decided at the ninth decimal place".

**`W16` — pad and street cut on different planes, and a pad sized for the wrong road. CLOSED.**
A run's end tangent now comes from `station_axis` (`point_profile.run_end_axes`), so the worst
flank-to-cap gap over the island's ten junctions is **3.721 m -> 0.000 m**; and `corner_setback`
measures the **deck** half-width, not the carriageway's, so the shortest corner run is
**3.15 m -> 14.95 m** and a 4 m footway has room to turn. Both are the footway reaching a solve
that had never been asked for it.
→ "`W16` — the pad was cut on one plane and the street on another".

**`W3` — the footway. CLOSED, and it was never built at all.** The seeder wrote the tier's footway
width on the STATION; every station is `INHERIT`, so the road's `base` is what is read and the
write was discarded. Ground roads now carry a 0.15 m kerb and a 4.0 m pavement, in the visual and
in the `-colonly` proxy; elevated stretches carry the 1.0 m wall the solver had always derived,
now standing at the outboard edge of that pavement. Navmesh 1232 -> 1619 vertices.
→ "`W3` — the island has a pavement now, and it never had one before".

#### Closed on 2026-08-31

**`W12` — the folded junction pads. CLOSED**, and it was not a setback anyone needed to drag.
`auto_setback` searched only for a tail that fits every TURN MOVEMENT and never asked whether the
arms' own CAPS stay in ring order; at Chuo x Rinkai's 56 deg crossing that binds first. Both folds
are gone and the island's gate is **0 errors, 0 warnings**.
→ "A shallow crossing needs a big pad".

**`W2` — the network reaches the island again. CLOSED.** Reach **52.5% → 70.9%** of land within
250 m of a road (`tools/island_v3_reach.py`, the metric this had never had); 7 → 12 roads,
14.1 → 23.7 km. Four authored arterials close the three flat gaps and `Shrine touge` — 3 573 m of
derived switchback at an average 7.8% — climbs the spur to its shrine plateau.
→ "`W2` — the network reaches the island again".

#### Closed on 2026-08-30

**`W9` — all water is a swim volume. CLOSED.** `WorldBaker` bakes a real `WaterVolume` with the
character mask; one `water_sea` box covers the sea, the bay and the lagoon; `SHORE_Z` gives a 27 m
beach with a 0.206 m step so you can get back out. Gate: `check_island_water.py`.
→ "All water is one swim volume, and the world has a logic wall".

**`W10` — the world edge. CLOSED, as a logic wall.** `WorldBounds` from a `bounds_world` marker —
a soft inward push, a hard clamp past the edge, and a kill-Z rescue; the seabed reaches 288 m
further so the wall never stands on the last triangle.
→ same section.

**`W1` — Hama-dori's bay crossing. CLOSED, as a bridge** (the option chosen over moving the road
ashore). 18 stations elevated to the plan's own `Bay_bridge` deck, graded in and out at 4%, deck at
18.20 m, columns to −8.32 m on the new sea floor; gate 0/0.
→ "The sea had no floor, and the bay had no bridge".

**`W4` — `-noped`. CLOSED.** `NavBaker` masks those bodies out of the parse; measured 1232 → 1221
vertices with a 239-vertex control. The seabed is its second user.
→ "`W4` — the Godot side reads `-noped` now".

**`W5` — the handling fixes. CLOSED, in motion.** `HandlingTest.tscn`, 8 cases, 8 PASS, three of
them controls.
→ "`W5` — the two handling fixes are verified in motion".

#### Found in the world, still open




**`W17` — `Auto Setback` is not idempotent, and its docstring says it is.** Pressing it a second
time on the island moved 17 of 35 mouths by up to 30 m, and a fourth press by 58: a monotonic
runaway, because `recommended_tail_length` only ever searches UPWARD from the widest mouth it is
handed, while the mouths themselves move the centroid it measures from — and the AUTO facings are
derived from the mouths in turn. `W18`'s axis projection bounds it (the drift no longer compounds
sideways) but does not close it; the centre, the mouths and their facings are one coupled system
and settling it wants a fixed-point solve, not a single pass. Harmless in the build, which presses
the button exactly once; a live trap for anyone who presses it twice by hand.
→ `ROAD_POINT_GRAPH.md` §8p.




**`W11` — the last 29% of the island, and how much of it is a gap at all.** After `W2`, 2.89 km²
is more than 250 m from a road: 2.17 km² of it is the massif and **64% of that is steeper than
60%**, which is a mountain rather than a gap (see the wilderness decision in `W2`). What is left
worth having is ~0.5 km² — the airport apron's far side (one road cannot cover a 1 160 m apron) and
a 0.18 km² pocket on the south-west shore between Rinkai-dori and Nishihama-dori. Both are local
streets, so they belong with `W7`, not with the arterial network.

#### Planned work, not yet started

**`W6` — the bridges and the elevated T1 loop.** The plan already places them and the support rule
already derives piers — `W1` is the proof of that path end to end, and what is left here is the
*decoration*: a pillar variant is the road's own support and belongs in the style system as a
`pillar_asset` slot through the already-written `GN_PointAssets`; a **named hero bridge** (Rainbow
Bridge) is not a support at all but authored `MANUAL`/`OVERLAY` content, and the road system owes it
only `pillar_skip` plus a stable reference to fit to.
→ `ROAD_STYLE_AND_PATH_PREVIEW.md`.

**`W7` — the local street grid.** Authored, not planned: `seed_district_roads`'s `--links N` spaces
cross-streets evenly and can already do it. Costing it against a district's baked `_Road` ribbon is
only worth it once the arterials read right, which is `W2`.

**`W8` — traffic regions.** There are none in the base piece, so cars only appear via `DebugHarness`
**F4**. Zone markers are town content and belong with step 3's BASE/TOWN split, not here.

## Step 3 — regions, then detail

- split the island into pieces/regions on the **new** ground, registering them in
  `piece_registry`; do not reuse the 6×6 grid's positions;
- **harvest before archiving** the old pieces. They hold real baked content — `Piece_3_2` alone is
  827 meshes of buildings and terrain. Decide what the harvest is (a building library? per-piece
  appends?) **while the pieces are still wired up**; parking the blends first makes it harder;
- detail outward per region, roads first then ground detail — the cut is non-destructive and now
  reversible, so iterating between the two is safe, and the road is what asks the questions of the
  ground that make terrain edits informed.

### The split itself — BASE vs TOWN, not district-per-file  (design, 2026-08-29)

**The question.** Instead of separating the road out per district, keep the **ground mesh + road
network as one base in one blend** and make each district a **collection** of town content under
it; then LOD/stream the town by distance while the base is always there, so ground behaviour is
consistent — or let the exporter cut it on the fly.

**This is the right shape, and the project has already paid for the version of it that is missing.**
The evidence is in CLAUDE.md, in two places that are the same finding from opposite sides:

- `backbone_deck()` exists — an always-resident **collision-only deck under every arterial** —
  because "cars outside streamed districts fall into the void: PLATEAU districts have no
  always-resident ground". That is a hand-built, arterial-only version of exactly the base layer
  being asked for.
- `ZoneManager.maintainTraffic` reclaims any vehicle below `Y = −30`, and the documented
  headless smoke signal is "routed-but-0-moving = falling through missing ground". Traffic has to
  route through parts of the world nobody is looking at, so the **lane graph and the ground under
  it cannot be streamed content**.

The thing that has to be always-resident is therefore **not the ground you see — it is the ground
you touch and the graph you route on**. That split is what makes the budget work:

| layer | resident | what it is |
|---|---|---|
| `GROUND_COL` | **always** | one collision-only mesh of the whole island. 18 540 faces today — a single `ConcavePolygonShape3D` at that size is nothing. |
| `ROAD_COL` + lane graph | **always** | the road surface's `-colonly` proxy and the exported `Path3D` lanes. Traffic routes everywhere, always. |
| ground / road **visual** | streamed | the same surfaces, auto-cut into chunks, LOD'd by distance. |
| town (buildings, props, detail) | streamed | one collection per district — what `ZoneManager` already streams. |

**Author one continuous world; let the EXPORT cut it.** This is the part worth building, because it
decouples the chunk grid from the authoring: the artist never draws a district boundary into the
geometry, and the grid can be re-cut without re-authoring. The primitive already exists —
`build_island_v3.build_ground` cuts a heightfield to the coastline by marching squares, bisecting
each crossing from the inside point outward so neighbouring cells produce the identical vertex.
**Cutting to a district square instead of the coastline is that same function with a different
`inside` predicate.** Seams are watertight by construction rather than by agreement.

**Where the per-district `.blend` survives, and why.** One blend for the entire world is a single
point of contention and Blender has no partial loading; at final detail that will bite. The repo's
own idiom answers it: keep the **base** (ground, roads, markers) in the master blend, and keep each
district's **town** content in its own file **library-linked into the master as a collection**.
"Districts are collections under one blend" is then true from the master's point of view, while an
artist can still open one district alone — and `link_neighbors.py` / `link_world.py` /
`instance_collection` already do exactly this kind of linking.

**What it removes.** The `.seam.json` + `check_seams.py` machinery exists *only* because adjacent
districts are separate files whose vertex data cannot be edited together (AUTHORING_GUIDE.md's
"Road/geometry alignment across a shared seam is a separate, still-manual concern"). With the
ground authored once, a seam is not something to verify — there is no seam. Step 1 already made the
island's ground a single object, so most of that is banked.

**Sequencing: this is a step 3 decision and step 2 must land first.** The road network is half the
base layer, and you cannot choose where to cut a base that does not exist yet. Build the arterials
on the ground (step 2), then cut.

**One thing the slim-down bought.** An always-resident whole-island collision layer is only
affordable because the world is 1512 m; at 2016 m it is 1.8× the faces for the same design, and at
the retired 3024 m grid it would not have been on the table at all.

### The split, as it stands on 2026-09-05 — and ONE streaming concept, not four

Step 2 has landed (the roads reach the ground, the joints chain, the cut cuts, the export is clean),
so the split is now the next real piece of work. What follows is the concrete shape, and a naming
decision that has to be taken WITH it rather than after.

**Four names currently describe overlapping things**, which is the user's question and it is a fair
one:

| name | what it is today | after the split |
|---|---|---|
| `piece` | a `.blend` + its baked `.tscn` (`piece_registry`, `build_piece.sh`) | **keeps its job** — the authoring/bake unit. A build-pipeline word, not a gameplay one. |
| `district` | a 504 m grid tile: neighbours, shared edges, a `.seam.json`, a registry position | **retires as a mechanism.** The world is one continuous island now; step 3's own rule is "author one continuous world, let the EXPORT cut it", and with the ground authored once there is no seam to verify. |
| `region_` marker | the Blender-side marker that bakes into a zone | **stays**, as the authoring handle for a zone. |
| `Zone` / `ZoneMarker` / `ZoneManager` | the runtime streaming unit | **becomes the only streaming concept.** |

**So: one streaming concept, and it is the zone.** A zone is already scale-free and grid-free — it
has a centre, load/unload radii, spawn configs and optional geometry, and none of that cares whether
it is a neighbourhood, a car park or a building interior. "A cell inside a cell" is then just *a zone
inside a zone*, which needs no new type and is already true today (zones live inside districts).
`district` survives only as a **name for a place** (`zone_id = "harbour"`) — which is all it ever
meant to a player.

**Rename `Zone` -> `Zone`, and do it as part of this step.** It lives in `com.openworld.world`,
so `world.Zone` stutters where `world.Zone` / `world.ZoneMarker` / `world.ZoneManager` reads
cleanly, and the "World" prefix only ever existed to disambiguate it from the grid concept that is
being retired here. It is a real simplification and it is not free: the class name is the registered
script name, and every `.tscn`/`.tres` referencing those scripts has to move with it. That cost is
lowest **now**, while the district-as-grid concept is being taken out anyway and before any town
content is authored against the old names — doing it later means touching every district blend.

**The layer split itself is unchanged** from the 2026-08-29 design above (`GROUND_COL` + the road
proxies and lane graph always resident, ground/road visual and town content streamed), and step 2
has since made two of its assumptions true rather than hoped-for: the lane graph is continuous
end to end (`W15`), and the ground under it is genuinely cut to the roads (`W22`), so "the ground
you touch and the graph you route on" is now a thing that exists and can be split off.

**Order of work:**

1. ~~close `W13`/`W21`~~ — **done**: the road deforms the heightfield instead of booleaning it,
   so the ground clears every road and the collider IS the visual;
2. ~~rename to one streaming concept~~ — **done**, `W23`;
3. cut the exported ground/road visual into chunks by the same marching-squares primitive
   `build_island_v3.build_ground` already uses, with a district-square `inside` predicate instead of
   the coastline — so chunk seams are watertight by construction and the grid can be re-cut without
   re-authoring;
4. author town content per zone, library-linked into the master.

## Rules to carry in (each one was paid for)

1. **Measure against real content before trusting a synthetic pass.** Two silent, load-bearing
   defects (the sampler taking rooftops, the cut finding no terrain) survived every test on the
   sample network and fell out of one probe against a real district.
2. **One owner per derived fact.** Both of those were the same shape: two halves asking different
   questions about the same thing.
3. **Author through the operators an artist presses.** A fixture that writes the data model can be
   perfect while the gestures it bypasses are broken.
4. **A build that fails the gate is a failed build** — and when it fails, the PREVIOUS build's
   geometry is still on screen, which reads as "it built but the markings did not".
5. **Materials-only until the asset width reaches the solve.**

---

## Resuming

    python3 tools/island_v3_terrain.py                 # self-tests + the arterial table, 55 ms
    blender/tools/check_roads.sh                       # 18 checks, expect PASS=18
    blender --background --python blender/tools/build_island_base.py     # the playable base piece
    NAV_HALF=2016 blender/tools/build_piece.sh Island_base
    blender --background assets/world_source/island_v3.blend \
            --python blender/tools/check_island_ground.py    # expect 12 of 12
    blender --background assets/world_source/pieces/Island_base.blend \
            --python blender/tools/check_island_water.py     # expect OK
    python3 tools/island_v3_reach.py                  # expect 70.9%

Rebuild the island itself with
`blender --background --python blender/tools/build_island_v3.py -- --full` (relief + streets +
parcels), then `island_v3_buildings.py`. Both read the ground from `tools/island_v3_terrain.py`.

Also:

    godot --headless res://src/main/resources/com/openworld/world/hosts/HandlingTest.tscn   # 14 cases
    blender --background --python-exit-code 1 assets/world_source/pieces/Island_base.blend \
            --python blender/tools/check_island_water.py        # the water layer's gate
    python3 tools/island_v3_reach.py                   # W2's gate: reach %, and WHERE the gaps are

**Pick up the work at ["Open items"](#open-items--the-follow-up-register).** `W1`–`W5`, `W9`, `W10`,
`W12`–`W16`, `W18`–`W23` are closed; `W11` and `W17` are found and open, and
`W6`–`W8` are planned.
`W7` (the local street grid) is the one that moves the reach number, and `island_v3_reach.py` says
exactly where to put the streets.

**And the ground the roads sit in is a solved problem now.** `W13`/`W21` closed by replacing the
boolean with a height-field carve, so a new road needs nothing done to the terrain: rerun
`build_island_base.py` and the ground is re-emitted with that road cut into it, visual and collider
alike. The check is `0 proud` on both sheets — the script prints the corridor count and the verge,
and `ROAD_POINT_GRAPH.md` §8v carries the measurement.
`W6` (bridge and pillar decoration) is what the new touge and the two bridges are asking for.

The pilot district from 2026-08-28 (`District_pilot_3_2`, 89 lanes, 4 junctions, walkable via
`SoloPiece.tscn`) is **disposable** — it proved the kit end-to-end on real content and is not part
of this plan. Delete `assets/world_source/pieces/District_pilot_3_2.*` and
`src/main/resources/com/openworld/world/pieces/District_pilot_3_2.*` when done with it.

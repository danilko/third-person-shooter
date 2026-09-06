# Blender → Godot Conventions

Reference for authoring world/character content in Blender for import into Godot 4.6.
This is a living document — update it as pipeline decisions are made, **before**
content production scales up. Authoring real geometry against undecided conventions means redoing
it once they're set — the same retrofit-cost logic rule is built on.

> **This is the decided-conventions reference, not the day-to-day workflow doc.** For "how do I
> actually change a kit piece / building / district and test it," see
> `assets/world_source/AUTHORING_GUIDE.md` — it documents the real, working commands
> (`kit/build_*.py` → `tools/build_piece.sh` → `hosts/SoloPiece.tscn`).

## Status

Sections below are **proposals to confirm**, not decided defaults — tick each off
as the team agrees on it, and update the section with the actual decision.

- [x] Coordinate / scale convention — 1 unit = 1 m (DECIDED) — see "Coordinate & scale" below
- [x] Asset granularity & instancing (kit of at-origin prefabs) — see "World composition" below
- [x] Pivot / origin convention (grid tiles vs. free-standing) — see "World composition" below
- [x] Building ↔ zone ↔ region model — see "World composition" below
- [x] Zone-edge seam contract (chunks fit/align across files) — see "World composition" below
- [x] Collision-proxy authoring — hand-authored `-col`/`-colonly` proxies (DECIDED) — see "Kit authoring" below
- [x] Naming conventions for collision / navigation / gameplay markers — see "Kit authoring" below + I6a table
- [x] Zone-chunking size — starter **56 m** (8 road tiles / 14 wall modules), tunable — see "Kit authoring" below
- [x] Per-chunk navigation + chunk adjacency (abut, don't overlap) — see "Zone chunking" below
- [x] Road / traffic-lane authoring (3.5 m lane modules + `VehicleRoute` lanes) — see "Roads & AI traffic" below
- [ ] LOD / poly budget per region tier
- [x] Terrain strategy — hand-modeled chunked mesh (DECIDED) — see "Terrain strategy" below
- [ ] Import / version pipeline
- [ ] Source-control strategy for `.blend` binaries

---

## Coordinate & scale

- 1 Blender unit = 1 Godot unit = 1 meter. Author at real-world scale — every
  distance-based system in the codebase (`LOD_FREEZE_DIST = 200m`, `swimSpeed`,
  `loadRadius`/`unloadRadius`, `relevanceRadius = 200m`, …) assumes meters.
  Off-scale content silently breaks all of them at once.
- Blender is Z-up; Godot is Y-up. Godot's built-in `.blend` importer handles this
  automatically — don't "fix" orientation by rotating the root in Blender, or
  you'll double-correct on import.
- **Character reference height: ~1.75 m standing** (`Character.tscn` capsule; crouch ~1.10 m, crawl
  ~0.56 m thick). Size buildings/props against this, not the other way — the 3 m wall/floor module
  (see "Kit authoring" below) was chosen to look right next to a ~1.75 m adult, not an arbitrary
  round number. All import `.glb.import` files use `root_scale=1.0` (no importer-side scale
  correction) — a scale mismatch is always an authored-dimension problem, not an import setting.

## World composition — assets, instancing & the building ↔ zone ↔ region model — DECIDED

The world is **three nested tiers**, finest to coarsest. **Each tier is authored once, at its own
origin, and *referenced* by the tier above — never copied/flattened into a bigger file.** This is the
standard AAA modular-kit + instancing pattern (a building module placed 200× is 200 lightweight
references to one source, not 200 copies of its geometry). It is also exactly what E1 zone-streaming
already consumes, so finer granularity costs nothing architecturally — it *is* the architecture.

### The three tiers

| Tier | Code type | What it is | Authored as | Placed into the tier above by |
|:-----|:----------|:-----------|:------------|:------------------------------|
| **Asset** (building / prop / road tile) | (none — a plain scene) | The finest reusable piece. | Its own `.blend` → its own `.tscn`, **at origin** (e.g. `world/buildings/Door.tscn`, `world/roads/Road2LaneStraight.tscn`). | **Instancing** — the zone chunk holds an `instance=` reference + a transform, not duplicated mesh. |
| **Zone** | `Zone` + `ZoneMarker` | The **streaming unit**: a spawn box (`size`) + AI/traffic population + an optional `geometry` PackedScene (the chunk = ground + instanced buildings + roads for that cell). Marker world position = zone **center**. | A `.tres` (the data) on a `ZoneMarker` (the placed node); the chunk it streams is a `.tscn` assigned to `Zone.geometry`. | Placed in the world / world-layout (drag the marker; the chunk streams in around it). |
| **Region** | `RegionConfig` | **A tuning profile, NOT a spatial container.** Faction rules + AI/vehicle density + AI-LOD bias + lighting/fog/music. The *active* zone's region drives global ambience; per-zone densities scale that zone's own spawns. | A `.tres`, assigned to one **or shared by many** zones (`Zone.regionConfig`). | Referenced by zones — a "downtown" region `.tres` shared by every downtown zone. |

> **Key mental model:** Region (tuning, 1→many) ⊃ Zone (streaming chunk, *contains* assets) ⊃
> Buildings/props/roads (instanced at-origin assets). A "region" is **not** a bigger box you model
> inside — it's a profile several zones point at. Don't try to model a region as one mesh; model
> *zones* (chunks), and tag groups of them with a shared `RegionConfig`.

### Nested instancing — geometry lives only at the leaves (the full chain)

The tiers compose by **scene instances all the way down** — Godot stores `ext_resource` + `instance=`
+ a transform at every level, so **real geometry exists in exactly one file per asset (the leaf kit
piece)**; everything above is references:

```
Wall.tscn / Door.tscn             ← LEAF: real geometry + collision (the only geometry-holders)
   ↑ instanced by
Building.tscn                     ← references only
   ↑ instanced by
Zone.tscn (= Zone.geometry)  ← references only; the streaming unit
   ↑ placed / streamed by
City / World                      ← ZoneMarkers + AutoLoads (placement, NOT resident geometry)
   ⟂ Region (RegionConfig)        ← cross-cutting tuning tag on zones, not a geometry tier
```

- **Geometry nesting = kit → building → zone.** Resident scene instances; a zone (chunk) loads/unloads
  as a unit and inside it buildings-as-instances are what you want. Edit a leaf → reflects in every
  building → zone → everywhere.
- **zone → city/world = the streaming + placement layer, NOT a resident mega-scene.** A flat
  `City.tscn` that instances every zone would load the whole map and defeat E1 streaming. The top tier
  is the set of `ZoneMarker`s (cheap); each marker's heavy geometry rides in its streamed
  `Zone.geometry` and is instanced on demand by `ZoneManager`.
- **Region is not a geometry tier** — it's a `RegionConfig` profile several zones share; it cross-cuts
  the chain. Don't model it as a mesh.
- **The only geometry NOT instanced:** the leaf kit pieces (one shared copy) and the per-zone
  **terrain/ground base** (unique per location). That's the "except for the base".
- **Each level needs the baker instancing pass** (above), but it **composes**: a baked `Building.tscn`
  is just another `.tscn` the zone bake instances. **glTF can't carry "instance of res://X.tscn",** so
  each assembly level is authored as `instance_<assetId>` markers and baked (or assemble the upper tiers
  in the Godot editor, which stores native instances with no bake).
- **Inherited vs computed:** collision rides inside the leaf instances (inherited free); **navmesh is
  baked per zone-chunk** against the assembled collision (computed at the zone level, per E1).

### Granularity & instancing rule

- **Go as fine as reuse pays off.** A building, a streetlight, a road tile, a market stall = its own
  at-origin `.tscn`. Anything placed more than once **must** be an instance, never copied geometry.
- **Never hand-author a flattened mega-`.tscn`.** Disk bloat, no dedup, no single-fix-everywhere, no
  source of truth. The flattened/merged form is a **baker output** (`WorldBaker`), produced from the
  instanced source only if profiling demands batched draw calls — not an authoring artifact.
- **Very-high-count identical props** (foliage, fence posts, streetlights) → `MultiMeshInstance3D`
  (GPU instancing, one draw call), not N scene instances.
- **Terrain ground is the one thing NOT recycled** — it is unique per location (see Terrain strategy).
  The recycle-by-instance rule is for buildings/props/roads.

### Instancing status — baker instancing pass IMPLEMENTED

The "instance, never copy" rule is now produced by the baker. Two levels of reuse:

- **Mesh-resource dedup — free, no markers.** Author repeats as Blender **linked duplicates (Alt+D)** /
  collection instances; glTF exports them sharing one mesh, Godot imports a shared `ArrayMesh`. Good for
  dumb visual modules: edit the mesh → reflects on all. But it is geometry-in-Godot, not scene reuse (no
  shared collision/script/children), and the geometry is still exported (larger files).
- **Scene instancing (`instance=` to one `.tscn`) — `WorldBaker` swap (DONE).** Required for kit pieces
  that are full scenes (`Door.tscn`, a scripted building) AND for small files at city scale (no geometry
  exported — only a reference + transform).

**How the bake swap works (`WorldBaker.buildInstance`):**
1. **Authoring marker:** an empty named `instance_<assetId>` **or** any node carrying a custom property
   `asset_path = res://…/kit/SM_Res_Wall_Solid_2x3.glb` (the meta path is the **duplicate-safe** bridge —
   it survives Blender's `.NNN` rename; the name path strips a trailing `.NNN`). `instance_<assetId>`
   resolves to `kitDir + assetId + ".tscn"` (`kitDir` is an exported field, default `…/world/kit/`); the
   `asset_path` form can point directly at a staged kit `.glb` — Godot's importer registers an imported
   `.glb` as a loadable `PackedScene` like any other. **Use `kit_common.instance_marker()`, not a bare
   empty** — it attaches a real visual proxy under the marker (the kit piece's own mesh, imported once
   per unique asset and shared as a linked duplicate across every placement — see
   `kit_common._attach_proxy`) so you *see* the piece while authoring, discarded by the baker at swap
   time — so the `.blend`/viewport shows real geometry but the exported glTF and baked output stay tiny
   (markers, not geometry). Verified byte-identical `instance=` output with the proxy on or off.
2. **Swap:** `GD.load(asset_path)` → `instantiate()` (which sets `scene_file_path`) → `setGlobalTransform`
   to the marker's full transform (rotation/scale included) → `addChild` to the root → free the marker +
   proxy. The count prints as `instances=N`.
3. **Owner gotcha (the bit that makes it an instance, not inlined):** `pack()` records a child as an
   `instance=` **only if just the instance *root* is owned by the pack root and its internals are NOT
   re-owned.** `setOwnerRecursive` therefore owns the instance root and **stops** (skips its subtree).
   Re-owning the internals would inline (flatten) the geometry — the whole point lost.
4. **Result:** the baked output holds `ext_resource type="PackedScene"` + `instance=ExtResource(...)`
   per placement → edit the referenced kit leaf, every placement updates. Composes recursively (a baked
   `Building.tscn` is just another instanceable `.tscn`).

**Working examples (the building tier's starting template):** `assets/world_source/buildings/
PLATEAU_TokyoTower.blend`/`PLATEAU_ShibuyaScramble.blend` (hand-modeled landmark placeholders,
placed via `build_district.place_landmark()`) and `buildings/RecycledBuildingKit.blend` (55 real
PLATEAU buildings recycled from already-extracted precinct data, one top-level collection per
placeable asset — see `AUTHORING_GUIDE.md` §10) are the pattern to copy/extend by hand for any new
hand-crafted building (see `AUTHORING_GUIDE.md` §2's edit-channels table). A marker-based
building assembled from staged kit leaves (`kit_common.instance_marker`, one `asset_path` marker per
leaf, baked to `ext_resource`+`instance=` node references with zero inlined mesh data) is the same
mechanism at building scale — `world/buildings/Door.tscn` is a real baked single-asset example of
what one placed leaf looks like on the Godot side.

> **Still pending (separate follow-up):** per-chunk bake + the zone/city **assembler** (the baker still
> emits one monolithic output). The instancing pass above is independent and works now; it just also
> applies per-chunk once that lands.

> **What real district content actually uses (read this before assuming `instance=` everywhere):**
> the mechanism above is verified correct and works (`ExampleBuilding.tscn` above, and the cross-district
> seam-route markers in `towns/districts/build_district.py`) — but the bulk-content `towns/` generator
> pipeline (see `assets/world_source/AUTHORING_GUIDE.md`, the operative day-to-day workflow doc) does
> **not** place mass-repeated kit pieces (streetwalls, road tiles) as `instance_<assetId>` markers. It
> instead uses `mmesh_<piece>` markers, which `WorldBaker.buildMultiMeshes` collapses into one
> `MultiMeshInstance3D` per asset (GPU instancing — one draw call for thousands of identical wall
> panels/road tiles, at the cost of no per-instance collision, which is why streetwall solidity comes
> from separate
> `buildings._building_collision` convex-box proxies, see `AUTHORING_GUIDE.md` §10); towers/hero
> buildings are realized as unique geometry instead. Confirmed in the committed
> `districts/District_city_1_1.tscn` (Shibuya): zero `instance=` references, all `MultiMeshInstance3D` +
> realized meshes. Both strategies are legitimate and both ship — `instance=` for scenes that need
> their own collision/script/children as a unit, `MultiMesh` for dumb high-count visual repeats — but
> don't expect editing a kit leaf to propagate via `instance=` for city-scale content; it propagates
> because `MultiMesh` re-reads the same shared mesh resource on re-bake.

### Pivot / origin convention (so a piece lands predictably in any tool)

Decide the origin **per asset class** and never deviate — placement math in Blender, the baker, and
Godot all assume it:

- **Grid tiles** (road tiles, **zone/terrain chunks**): origin = **footprint center, at ground level**
  (`y = 0` is the ground plane). Center pivot makes 90°/180° rotation snap cleanly and lets tiles abut.
  This matches the existing road kit ("tiles centered on origin so they abut").
- **Free-standing buildings / props**: origin = **footprint center at ground contact** (`y = 0` = the
  face that meets the ground). Dropping the asset at a world point plants it on the ground — no
  per-asset Y fiddling.
- **All classes:** **+Z = forward / north**, consistent with the road kit. 1 unit = 1 m (see above).

### Zone-edge seam contract (how separately-authored chunks fit/align)

This is what lets two zones modeled in **separate `.blend` files** meet without gaps or z-fighting:

- A chunk's footprint = **exactly the zone cell size** (e.g. `N×N` m), centered on origin → its edges
  sit at `±N/2`. Neighbours therefore **abut** on a grid line (never overlap — same rule as roads/navmesh).
- **The outer seam band is contracted, the interior is free.** Agree a fixed ground height across the
  outer ~2 m of every edge (e.g. `y = 0`, flat) so neighbours meet cleanly; sculpt freely inside that
  band. Only the seam is a contract.
- **Roads/lanes cross a boundary on a grid line**, at the standard lane offset (±1.75 m, see Roads), at
  the seam height — so a `lane_*` route continues unbroken into the next chunk.
- Each chunk carries its **own baked `NavigationRegion3D`**; abutting navmesh edges stitch within
  `edge_connection_margin` (see "Zone chunking"). The seam contract is what keeps those edges colinear.

### Cross-tool alignment (Blender ↔ Godot) — pick ONE placement source of truth

Big-world pipelines **author assets in the DCC (Blender) and assemble in the engine (or a layout
tool); they do not keep both editors showing the same full world live** — that sync is a tar pit.
What is exchanged is **layout data (instance transforms)**, not geometry. For this project:

- **Blender = asset library (at-origin pieces) + the world-layout source** (lightweight: empties /
  blockout proxies showing *where* each chunk/asset goes). **Godot = the baked runtime**, assembled by
  `WorldBaker` from that layout. **Place a given thing in one tool only** so the two never diverge;
  Godot's editor is a viewer of the baked result, not a parallel placement surface (and Blender's
  placement ergonomics are better for dense layout anyway).
- **A shared grid + identical snap increment in both tools is the real alignment mechanism** — set the
  Blender snap increment and the Godot grid to the zone cell size (and the 7 m road sub-grid). Edges
  land on grid lines → pieces meet automatically, no eyeballing.
- **Optional connection sockets** for modular joins: named empties at the meeting faces
  (`socket_in` / `socket_out`) so one piece's out-socket lands on the next's in-socket.

### Where your existing `building.blend` fits (recipe)

1. **Export the building as its own at-origin asset** → `world/buildings/<Name>.tscn` (footprint center
   at ground contact, +Z forward). Add `-col` collision proxies; keep it self-contained.
2. **Instance it into the zone chunk(s)** that need it — the chunk `.tscn` (which becomes a
   `Zone.geometry`) references the building + a transform. Placing it five times across a district
   = five instances of the one asset.
3. **The chunk is one zone cell** — sized to the grid, honouring the seam contract, carrying its own
   ground mesh + navmesh + instanced buildings/roads.
4. **Tag the zone with a `RegionConfig`** (or point it at a shared region `.tres`) for ambience —
   several downtown zones share one "downtown" region.
5. **The baker assembles/streams it.** Re-bake when the source changes. You never hand-place the
   building into a giant world file; it stays one editable asset, referenced everywhere it appears.

## Kit authoring — grid, sizing & the minimal test kit (DECIDED starter)

Concrete starter standard so a first kit can be built now. **Values are a starter set — tunable, but
lock before content volume grows** (the retrofit-cost rule). Governing rule: **each tier's dimension is
an integer multiple of the tier below, so pieces tile and abut** (same rule as roads / zone chunks).

| Tier | Module | Starter size | Multiple-of rule |
|:--|:--|:--|:--|
| Fine snap | — | 0.25 m | snap all placement to this |
| Wall / floor module | wall segment | **4 m** wide × **3 m** floor height × 0.2 m thick; floor/roof tile 4×4 m | building dims = multiples of 4 m |
| Building footprint | building | multiples of 4 m (e.g. 8×8, 12×8) | fits inside a road-bounded lot |
| Road tile | road | **7 m** (lane 3.5 m) — existing kit | — |
| Zone cell | chunk | **56 m** (= 8 road tiles = 14 wall modules); `Zone.size = (56,10,56)` | multiple of **both** 7 m and 4 m |
| City | zone grid | N×N zone cells, 56 m spacing | — |

56 m is the smallest clean cell that tiles **both** the 7 m road grid (×8) and the 4 m wall grid (×14)
— use it for the test (the `Zone.size` default of 60 is fine if you don't need road tiling, but 56
keeps everything on-grid). Pivots are already set per asset class (grid tiles: footprint center at
ground; free-standing: center at ground contact; **+Z = forward**; 1 unit = 1 m).

### Collision per kit piece — hand-authored proxies (DECIDED)

**Hand-author a low-poly proxy per leaf piece** (boxes for walls/floors) — better perf + control than
auto-generating collision from the visual mesh. Name it with a Godot import suffix so the importer
builds the `CollisionShape3D` automatically (no hand-editing the leaf scene); the proxy lives in the
**leaf** `.tscn`, so every instance inherits collision for free:

| Suffix | Effect on import |
|:--|:--|
| `<Name>-colonly` | collision **only**, visual mesh removed — use for a separate box proxy (walls/buildings) |
| `<Name>-col` | static collision **+** keeps the visual mesh — use when the visual *is* simple enough to be the proxy |
| `<Name>-convcol` | convex collision sibling — for props that need it |
| `<chunk>-navmesh` | becomes a `NavigationRegion3D` (per-chunk pedestrian nav, E1) |

### Naming standard (the discovery + instancing bridge)

| What | Blender name / form | Becomes (Godot) |
|:--|:--|:--|
| Leaf kit visual | normal name, **own Collection** (`Wall_4m`, `Door_Single`) | exported `kit/.../Wall_4m.tscn` |
| Leaf collision proxy | `<Name>-colonly` / `-col` | `CollisionShape3D` inside that leaf |
| Placement of a kit piece / building / zone | empty `instance_<assetId>` (or custom prop `asset_path = res://…`) | scene **instance** (baker swap) |
| Zone / region anchor | `zone_<id>` / `region_<id>` (+ size/radii/region meta) | `ZoneMarker` + `Zone` (+ `RegionConfig`) |
| Spawn / lane / water / junction | `spawn_<faction>_<n>` / `lane_<route>_<n>` / `water_<id>` / `intersection_<id>` (I6a table) | gameplay nodes |
| LOD | rely on Godot 4 auto-mesh-LOD on import; only hero assets need hand `_LOD0/_LOD1` | — |

> Lock these names before volume grows — the baker + importer discover everything by them, and renaming
> hundreds of placed objects later is exactly the retrofit cost this doc exists to avoid.

### Minimal test kit — DONE, superseded by real content

The originally-planned 6-step minimal exercise (leaf kit → building → zone → region → city, verifying
chunks abut) is superseded — real content now exercises every step of that chain with actual kit
pieces, not placeholder boxes:

1. **Leaf kit** — the real `kit/build_*.py` library (walls/roads/props/highrise/infra), staged as
   `.glb` under `res://…/world/kit/`, each with a real `-colonly`/`-convcolonly` proxy.
2. **Building** — `assets/world_source/buildings/PLATEAU_TokyoTower.blend`/`RecycledBuildingKit.blend`
   (real geometry, individually hand-editable, placed via `build_district.place_landmark()`/
   `lib/recycled_buildings.py`) — the working building-tier template, see this file's "Nested
   instancing" section above and `AUTHORING_GUIDE.md` §10.
3. **Zone** — real district pieces (`District_city_1_1`/Shibuya, `District_city_2_1`, `District_resid_1_2`)
   with working `-colonly` ground, real `Zone` streaming.
4. **Region** — `RegionConfig` per theme (`city`/`resid`/`rural`/`mtn`/`snow`/`harbor`), assigned via
   `world_grid.THEMES`.
5. **City/World abutting chunks** — Shibuya/`city_2_1`/`resid_1_2` are real ADJACENT districts (not
   just a same-side-by-side test pair) with verified-touching seams (`tools/check_seams.py` PASS) —
   see `AUTHORING_GUIDE.md` §4.

That validates kit → building → zone → region → world with a handful of boxes, before any real art.

## Collision-proxy authoring — see "Kit authoring" above (DECIDED: hand-authored proxies)

- **Decision: hand-authored low-poly proxies per kit piece** (boxes for walls/floors), named with the
  Godot import suffix (`-colonly` / `-col`) so the importer builds the `CollisionShape3D` automatically.
  Better performance + control than auto-generating collision from the visual mesh. Full suffix table +
  per-tier sizing in **"Kit authoring"** above; collision lives in the **leaf** `.tscn` so every instance
  inherits it.

## Naming conventions for gameplay-relevant objects

The codebase already relies on naming-convention-driven discovery in several
places (e.g. `ParticleManager` requires child containers named exactly after
`SurfaceType` constants; `WeaponController` discovers weapons via `Marker3D`
wrappers — see `CLAUDE.md` "Known Quirks"). The Blender pipeline should follow
the same pattern so content authors can place gameplay objects without hand-editing
scenes afterward. Proposed scheme — confirm before the first real zone is built:

| Authored as (Blender) | Converts to (Godot) | Used by |
|:-----------------------|:--------------------|:--------|
| Empty named `spawn_<faction>_<n>` | `Marker3D` + `SpawnConfig` entry | E1 `Zone` |
| Empty named `portal_<zoneA>_<zoneB>` | `PortalTrigger` (`Area3D`) | I2 `InteriorZone` |
| Empty named `water_<id>` (bounding box) | `Area3D` in group `"water"` | I1 `SwimState` |
| Empty named `lane_<route>_<n>` | `Marker3D` child of a `VehicleRoute` | I3 traffic lane (pure pursuit, **not** navmesh) |
| Empty named `zonetrigger_<beatId>` | `ZoneTrigger` (`Area3D`) | F3 |
| Mesh suffixed `-col` | `CollisionShape3D` | all world geometry |

A Godot `EditorScenePostImport` script should walk each imported scene and
convert these by name/prefix into the right node + script attachment. Renaming
hundreds of already-placed objects after the fact is exactly the retrofit cost
this document exists to avoid — lock the prefix scheme down first.

## Zone chunking

- `ZoneManager` (PLAN.md E1) streams geometry per `Zone` based on
  `loadRadius` / `unloadRadius` (hysteresis). Author and export geometry in chunks
  matching the chosen zone-grid size — **not** as one monolithic scene — or E1 has
  nothing to stream in/out. The chunk is assigned to the `Zone.geometry`
  PackedScene field (it instances on **every peer**, host and client — geometry is
  cosmetic/local, only AI bodies are host-authoritative). A mesh placed as a child
  of the `ZoneMarker` is static scene furniture and does **not** stream.
- Decide the zone-grid size *before* any real geometry is modeled; it constrains
  border layout, road continuity (I3), and region-transition placement (I4).

### Setting up a zone (recipe) — E1

1. **Pick a zone-grid cell size** (e.g. 60–120 m square) and model/export each
   geometry chunk to those exact extents so chunks **abut** (see below). This is
   the value `Zone.size` (X/Z) should match — the spawn box is meant to cover
   the authored chunk footprint; Y is the vertical spawn band (~10 m is plenty for
   ground AI).
2. **Place a `ZoneMarker`** (a plain `Node3D` + `ZoneMarker` script — the current
   codebase places these via `assets/world_source/towns/build_world.py`'s `region_<theme>_<gx>_<gy>`
   markers, baked by `WorldBaker`, rather than hand-duplicating a scene) at the chunk **center** —
   the marker's world position *is* the zone center — and assign a `Zone` `.tres`/resource
   with `size` = the chunk extents.
3. **Set the trigger radii** (both measured from the center, independent of `size`):
   `loadRadius ≈ size/2 + pre-spawn lead (~150 m)` so AI stream in *before* the player
   reaches the box, and `unloadRadius ≈ loadRadius + hysteresis margin (~150 m)`. The
   invariant `unloadRadius > loadRadius > max(size.x,size.z)/2` must hold or the zone
   flickers / unloads while the player is still on it (`ZoneManager.warnIfMisSized`
   logs a debug warning otherwise). Neighbour zones' `loadRadius` should reach past the
   `unloadRadius` you're leaving so there's no dead frame with nothing loaded.
4. **Assign the chunk mesh to `Zone.geometry`** (the PackedScene field) so it streams
   with the zone. A mesh dropped as a *child of the marker* is static furniture and never
   streams. Populate `spawnConfigs` (ambient AI) and `namedCharacters` (story AI).

### Navigation per chunk — DECIDED (E1)

- Each streamed zone-geometry chunk **carries its own baked `NavigationRegion3D`**
  inside its `geometry` PackedScene (baked offline against that chunk's collision).
  Streaming the chunk in adds its navmesh to the navigation map; streaming out
  removes it. The current single world-baked `NavigationRegion3D` is a placeholder —
  AI path *through* any building you stream in until that building's chunk owns nav.
- Multiple `NavigationRegion3D`s feed one navigation map; Godot stitches them where
  their navmesh **edges fall within the map's `edge_connection_margin` (~0.25 m)**.

### Chunk adjacency: abut, don't overlap — DECIDED (E1)

- **Geometry + navmesh chunks must be edge-adjacent (abutting), never overlapping.**
  Overlapping navmeshes create ambiguous/duplicate regions and seams; only abutting
  edges (within `edge_connection_margin`) stitch cleanly.
- The **load/unload *trigger* radii are the part that overlaps** — `loadRadius` of a
  neighbour reaches past the `unloadRadius` of the one you're leaving, so a player
  crossing a border is inside the next zone's `loadRadius` before leaving the
  current zone's `unloadRadius` (no dead frame with nothing loaded). Distinct from
  the geometry chunks, which abut. There is **no** cross-zone connection/portal graph
  in E1 — zones are independent and AI roam freely between them (the zone box is a
  spawn + load/unload trigger, not a movement fence).

## Roads & AI traffic lanes — DECIDED (I3)

**Core rule: a navmesh does not drive cars.** A navmesh is a 2-D walkable polygon with no concept of
lanes or direction, so it can keep a *pedestrian* on a surface but cannot make a car hold a lane or go
one way. AI traffic (PLAN.md I3) therefore follows an explicit **directional lane path** — a
`VehicleRoute` node whose ordered `Marker3D` children are the lane centerline, in travel order. The car
uses **pure pursuit** (aims a fixed look-ahead ahead *along* the polyline), which keeps it in-lane and
damps steering oscillation. The vehicle navmesh layer was removed; do **not** bake nav for roads.

So a road has **two independent halves**, authored separately:

### 1. Road mesh (Blender) — visuals + collision, on a grid
- **Lane width = 3.5 m** (matches `SingleRoadMesh.tscn`, the placeholder block: 3.5 m wide × 1 m
  forward). A 2-way street = two lanes = **7 m** wide.
- **Block set — modular kit on a 7 m grid, +Z = forward, tiles centered on origin so they abut
  (never overlap, same rule as zone chunks above).** Placeholder kit (replace each with a Blender
  mesh of the same footprint): `resources/.../world/roads/`
  - **`Road2LaneStraight.tscn`** — 7 m × 7 m 2-lane straight (center + edge lane lines). A 7 m
    2-lane tile is the convention (easier than two single-lane rows, matches lane-marking textures);
    `SingleRoadMesh` (a single 3.5 m lane) is kept only as the raw dimension reference.
  - **`Road4Way.tscn`** — 7×7 cross intersection. **Carries an `IntersectionZone`** (right-of-way,
    below) so dropping a 4-way tile brings its own traffic control.
  - **`RoadCorner.tscn`** — 7×7 corner (for ring roads / 90° turns; the lane route defines the curve).
  - *(Model a 3-way `T` tile the same way when needed.)*
  - Each tile carries its **own collision** (StaticBody3D, layer 1) — the imported Blender mesh
    replaces both the visual and that collision. Build a layout by placing tiles on the grid
    (rotate straights 90° for the cross axis) — any baked district piece's `lane_*` markers show
    the pattern in practice (`assets/world_source/lib/road_network.py`).

- **Junction right-of-way (I3b):** the `Road4Way` tile's `IntersectionZone` (an `Area3D`, `collision_mask`
  = vehicle layer) arbitrates crossing — first AI vehicle to arrive holds the junction, others yield
  until it clears (deadlock-free single-occupancy). Place a junction tile and the zone comes with it;
  no per-junction wiring. (Concurrent non-conflicting movements + signals = the fuller lane-graph, still
  future work.)

### 2. Lane paths (`VehicleRoute`) — the directional graph
- **One `VehicleRoute` per lane/direction.** Markers run along the lane centerline, **offset ±1.75 m**
  from the road center, spaced ~5–15 m (denser on curves). Marker order = travel direction.
- **Drive side = a content choice (tunable), not a code flag** — it's purely which side of the road
  centerline each direction's lane markers sit on. To flip it, mirror the lane-offset sign on every
  route; if a route generator lands later (I3b lane-graph), drive-side becomes one parameter there.
- **Project default: drive on the LEFT** (the map targets a Japan-like setting — Japan is left-hand).
  With +Z = north, +X = east, keep-left means: northbound uses the **west** lane (X = −1.75),
  southbound the east lane (X = +1.75), eastbound the **north** lane (Z = +1.75), westbound the south
  lane (Z = −1.75). The near-side (easy, no-oncoming-cross) turn is a **left** turn. Every district
  piece's `lane_*` seam routes (`assets/world_source/towns/districts/build_district.py`'s
  `emit_seam_routes`) are authored this way. For right-hand traffic, negate all those offsets.
- **2-way street** = two routes, opposite directions, one per lane.
- **4-way junction** = one route per movement you actually want (through + each turn). Cars on different
  routes cross at the center and brake for each other via a forward obstacle ray; **true right-of-way /
  signals is the deferred lane-graph (PLAN.md I3b)** — until then, expect bumping at busy junctions.
- `VehicleRoute.loop`: `true` = ring road (cars circulate); `false` = one-way through lane (car drives
  to the last marker and stops — junction-to-junction chaining is I3b).
- **Authoring source of truth = Blender empties** named `lane_<route>_<n>` (see the table above); an
  `EditorScenePostImport` step groups them into `VehicleRoute` nodes. Press **F4** in-game
  (DebugHarness) to drop one AI car on every `VehicleRoute` found in the current scene, to test any
  authored layout — real district pieces (`build_piece.sh <name>` → `hosts/SoloPiece.tscn`) or the
  full baked world both work.

> **Future (I3b):** a connected lane-graph (junction connectivity + right-of-way/signals), an optional
> generator that builds `VehicleRoute`s from Blender curves/empties, and networked spawn replication of
> streamed traffic.

## Blender-authored world & Java baker — DECIDED MECHANISM (I6a)

**Goal: Blender is the source of truth for geometry *and* gameplay markers; a Java baker converts the
imported scene into a native `.tscn`.** No hand-placement of roads/lanes/zones in `.tscn`.

- **Conversion mechanism — a Java `WorldBaker` that bakes to a native `.tscn`** (NOT
  `EditorScenePostImport`). **Verified:** godot-kotlin-jvm `0.15.0-4.6` exposes **no editor API**
  (`EditorScenePostImport`/`EditorPlugin` are absent from every dependency jar), so a Java post-import
  script is impossible, and a GDScript one breaks the no-GDScript rule + can't reuse the Java converters.
  Instead `world/WorldBaker.java` loads the imported `.blend`/`.glb` scene, walks it, converts **named**
  objects → gameplay nodes (reusing `VehicleRoute`, `Zone`, etc.), sets `owner` on every node, and
  `PackedScene.pack` + `ResourceSaver.save`s a native `.tscn`. Re-bake when the `.blend` changes (manual,
  not auto-on-reimport — the one trade-off vs. a post-import hook, accepted to stay all-Java).
- **Three ways to trigger the bake** (all bake `source_scene_path` → `output_scene_path`):
  - **Headless CLI — the scriptable one-shot (`BakeWorld.tscn`, `bake_on_ready` + `quit_when_done`):**
    ```
    <godot-jvm-binary> --headless --path <project-root> \
      res://src/main/resources/com/openworld/world/BakeWorld.tscn
    ```
    Passing the scene path as the positional arg runs it as the main scene; `_ready` bakes, prints a
    per-type summary, and `getTree().quit()` exits the process — no interactive editor. *(Depends on
    godot-kotlin-jvm bootstrapping the JVM under `--headless`; if that fails on your build, use one of the
    two fallbacks below — neither quits the running instance.)*
  - **Editor:** open `BakeWorld.tscn`, **F6** ("Run Current Scene").
  - **In-game:** `DebugHarness` **F5**.
  Only the `bake_on_ready` auto-path quits (gated by `quit_when_done`); the `bake()` method never does.
- **The prefix decides the type; Blender custom properties → Godot node metadata carry the parameters**
  (faction, count, loop, lane offset, zone radii, region tuning) — read via `Node.getMeta` with defaults.
  **glTF `extras` storage (VERIFIED on 4.6 — the original scalar-meta assumption was WRONG and
  the baker's glTF path never actually worked until fixed):** Godot's glTF importer does **not**
  split a node's Custom Properties into per-key node metas. It stores **all of them together under a
  single `extras` meta whose value is a `Dictionary`** (`node.getMeta("extras")` → `Dictionary(N)`).
  So a per-key `getMeta("size")` finds nothing and every param silently defaults. `WorldBaker.metaRaw`
  therefore resolves each key by checking the direct meta first (native `.tscn` `metadata/size = …`
  path) **then** falling back to the `extras` Dictionary (`d.get(key)`) — handling both source kinds.
  Two more gotchas baked into that resolver: (a) an untyped Godot **`Dictionary.get(missingKey)`
  returns `kotlin.Unit`, not `null`** (guard with a `present()` check or every node reads as having
  every key); (b) a JSON **array imports as a Godot `Array`/`VariantArray`, not a `Vector3`** — so
  `size: [x,y,z]` is read by `metaVec3`'s array branch. All `RegionConfig` tuning stays **scalar**
  (`light_temperature`, `fog_density`, `ambient_ai_density`, `vehicle_density`, `ai_lod_bias`,
  `region_name`, `faction_table` path) inside that dict. (Ultimate fallback if an importer drops
  custom props entirely: encode params in the name, e.g. `spawn_enemy_3`.)
- **`pack()` gotcha:** every node to be saved must have `owner` set to the pack root or it's silently
  dropped — the baker sets owner recursively before packing.

**Authoring naming (I6a foundation — model + name in Blender, the baker builds the node):**

| Blender object | Name / form | Becomes (Godot) |
|:---------------|:------------|:----------------|
| Lane centerline empties | `lane_<route>_<n>` (ordered) | one `VehicleRoute` per `<route>` + ordered `Marker3D` children |
| Ambient spawn | `spawn_<faction>_<n>` (+ `count` meta) | `SpawnConfig` on the nearest `zone_` |
| Zone / region anchor | `zone_<id>` / `region_<id>` (+ size/radii meta) | `ZoneMarker` + `Zone` (+ `RegionConfig`) |
| Water volume | `water_<id>` | `Area3D` (+ `CollisionShape3D`) in group `"water"` (I1) |
| Junction | `intersection_<id>` | `IntersectionZone` (`Area3D`, I3b) |
| Collision proxy | mesh suffix `-col` | `CollisionShape3D` (Godot importer, on import) |

> Earlier this section proposed `EditorScenePostImport`; superseded by the Java baker above after
> verifying the editor API is unavailable in the JVM binding.

### Authoring a source + running the baker (recipe — I6a)

A ready-made demo source lives beside the baker — start from it, or from the real pipeline:
- **`WorldSource.tscn`** — a small layout hand-built as native Godot nodes (no Blender needed), proving
  the baker path in isolation. Its named empties (`region_downtown`, `lane_loopA_*`, `spawn_enemy_0`,
  `water_pond`, `intersection_main`) mirror the naming table above with their params as node metadata.
- **The real thing:** `assets/world_source/world_master.blend` → `tools/export_world.py` →
  `World_master.gltf` is the full-scale, currently-baked version of the exact same convention (36
  `region_<theme>_<gx>_<gy>` zone markers, real `lane_*`/`water_*`/`intersection_*` empties) — open it
  in Blender to see the naming convention used for real, at world scale, rather than a synthetic demo.

1. **Author in Blender.** Model real geometry around/under the empties — Geometry Nodes road sweep along
   the `lane_*` centerline empties, building meshes, terrain. Keep the marker **names** and add params as
   **Custom Properties** (numbers/bools/strings; a `size` as a 3-number array). Add `-col` collision
   proxies for meshes that need them.
2. **Export into `res://`** as glTF (or save the `.blend`) so Godot imports it. Custom properties ride
   along as glTF `extras` → Godot node metadata.
3. **Point the baker at it:** set `BakeWorld.tscn`'s `source_scene_path` to your imported asset,
   leave `output_scene_path` at `World_baked.tscn`.
4. **Bake** via the headless CLI (or F6 / F5 — see "Three ways to trigger" above). The baker writes
   `World_baked.tscn`.
5. **The game loads `World_baked.tscn`** — the bake scene is a **dev/tool scene, separate from
   `World.tscn`**; re-run the baker whenever the source changes.
- **Roads (mesh) via Geometry Nodes:** draw road **centerline curves**; a GN setup sweeps the tile
  profile along them (surface, curbs, lane-line UVs). GN output exports fine — glTF/.blend export the
  *evaluated* mesh. Godot imports baked mesh + bakes collision from `-col` proxies.
- **Lanes via curves, not hundreds of markers (the scale win):** author **one centerline curve per
  road**; a GN/Python step offsets ±1.75 m per lane and reverses the opposite direction — so
  **drive-side is the offset sign, generated** (the tunable knob, see "Roads & AI traffic"). Bridge to
  Godot: glTF doesn't round-trip curves, so bake each lane to **named empties** (`lane_<route>_<n>`,
  exported as nodes) or a **JSON sidecar** of points; post-import builds a `VehicleRoute` per route.
  *(Recommended code follow-up: let `VehicleRoute` optionally hold a baked `Curve3D` so one Blender
  curve = one lane node — pure pursuit samples the curve. Do this with I6.)*
- **Junctions / zones / spawns / water = named empties** → post-import creates the Area3D/marker nodes
  (`intersection_<id>` → `Road4Way`'s `IntersectionZone`, `spawn_*`, `water_*`, `zone_*`, …).
- **Stays Godot-side (don't move to Blender):** pedestrian `NavigationRegion3D` bake (scriptable on
  import, but Godot's bake; lanes need none), all networking/gameplay logic (Java), final `.tscn`
  assembly + AutoLoads, `Zone` streaming wiring (chunks exported per grid cell per "Zone chunking").
- **Caveats:** glTF triangulates curves (use the empties/JSON bridge); GN can emit geometry for export
  but not real empties (use a Python script for marker/curve data); the whole pipeline hinges on the
  **stable naming scheme** — lock names before volume grows (the retrofit cost this doc exists to avoid).

## LOD / poly budget (Steam Deck target)

- Define a poly-count ceiling per region tier (dense city street vs. distant
  mountain silhouette, etc.), sized to D's frame-budget targets
  (`< 16 ms` across the FROZEN / PASSIVE / ACTIVE AI tiers).

### Mesh LOD — DECIDED: lean on Godot 4 auto-LOD; hand-LOD only hero assets

- **Default: Godot 4 generates mesh LODs automatically on import** (continuous distance-based decimation,
  no naming, no authoring). For the kit (walls, props, road tiles) this is the workflow — **author one
  mesh, let the importer build the LOD chain**. It "just works" through instancing: a `MeshInstance3D`
  inside an instanced leaf carries its own auto-LOD, so every placement LODs for free.
- **Hand-authored LODs only for hero/large assets** where auto-decimation looks wrong (a landmark
  building silhouette, complex rooflines). Author `_LOD0/_LOD1/_LOD2` in Blender (Decimate per level) and
  wire them under a single node; reserve this for the few assets that need it, not the kit.
- **Zone-level LOD is orthogonal and already exists:** E1 streams whole chunks in/out by distance, and D2
  freezes/passivates AI. Mesh LOD handles the *near-field* detail of a loaded chunk; streaming handles the
  *far field*. Don't conflate them.
- Keep the LOD method consistent per region tier — mixed budgets make D's profiling meaningless.

### MultiMeshInstance3D — for high-count identical static props

- **Use `MultiMeshInstance3D` for many identical, static, non-interactive pieces** — streetlights,
  fence/railing posts, bollards, trees/foliage, parked-prop clutter. One mesh + a transform buffer = **one
  draw call for thousands**, far cheaper than N scene instances (which each cost a node + cull + draw).
- **Scene-instance (the kit `.tscn`) vs. MultiMesh — pick by interactivity:**
  - **Scene instance** when the piece has collision/script/children or is individually edited/streamed
    (buildings, doors, breakables, anything gameplay touches). Reuse = "edit one `.tscn`, all update".
  - **MultiMesh** when it's pure dressing: identical, static, no per-instance logic. Reuse = one shared
    mesh; per-instance is *only* a transform (no script/collision per item — add a few manual collision
    bodies separately if a subset needs it).
- **Authoring → bake path:** scatter the props in Blender (particle/Geometry-Nodes instances on a
  surface, or hand-placed linked dups), tag the group (e.g. an empty `mmesh_<assetId>` over the cluster,
  or a custom property) and **let a baker pass collect those transforms into one `MultiMeshInstance3D`**
  per chunk (a future sibling to the `instance_` pass — same marker-collect idea, different output node).
  Until that pass exists, build a `MultiMeshInstance3D` by hand in the chunk for the worst offenders
  (foliage/lights). Glass-jaw: a MultiMesh is a single AABB for culling, so keep each one **per chunk**,
  not world-spanning, or it never culls.

## Terrain strategy — DECIDED: hand-modeled chunked mesh

**Decision: ground is hand-modeled mesh, authored in Blender, chunked per zone** — each zone's ground
rides in that zone's `geometry` PackedScene (so terrain streams in/out with the zone). **Not** a Godot
terrain plugin (Terrain3D) as the primary ground.

The deciding factor is **pipeline coherence**, not terrain features in the abstract. The whole world
flow is *Blender = source of truth → `WorldBaker` → native `.tscn`*. Terrain3D breaks that: it is
**engine-authored** (sculpt + splat-paint *in Godot*, can't be baked from Blender), and gameplay
objects then get placed relative to a Godot-sculpted surface — exactly the placement ergonomics we're
avoiding. Hand-mesh terrain keeps one workflow, places everything in Blender, supports arbitrary
topology (cliffs/overhangs/caves, which a heightmap can't), and **its one real weakness — no built-in
streaming/LOD — is already solved by the zone-chunk model** (the ground is just part of each chunk's
geometry, so it streams for free and honours the seam contract above).

| | Hand-mesh terrain (chosen) | Terrain3D plugin |
|:--|:--|:--|
| Fits Blender→baker pipeline | **Yes — one workflow** | No — a second, engine-only authoring surface |
| Object placement | In Blender (our strength) | In Godot, relative to the heightmap |
| Topology | Arbitrary (cliffs/overhangs/caves) | Heightmap only |
| Streaming / LOD | Via zone chunks (already built) | Built-in clipmap |
| In-engine sculpt / splat paint | No (UV/shader in Blender) | Yes |
| Dependency | None | Plugin coupled to `0.15.0-4.6` |

- **Collision:** trimesh `StaticBody3D` per chunk (from the chunk's mesh or a `-col` proxy) — per-chunk
  keeps each collision body manageable and unloads with the zone.
- **Reserved exception:** if a future **large natural wilderness** region is added (vast mountain /
  countryside where clipmap LOD + splat painting genuinely pay off), Terrain3D may be adopted **for that
  region only**, as a separate subsystem *under* the city, accepting the pipeline exception (objects
  there placed in Godot). It must not become the primary ground or the whole placement workflow leaves
  Blender. Switching the *primary* ground type later means re-authoring all terrain — hence deciding now.

## Import / version pipeline

- Confirm everyone runs compatible Blender + godot-kotlin-jvm plugin versions
  (current project: plugin `0.15.0-4.6`, JDK 17 — see `CLAUDE.md`).
- Agree on import presets per asset class (static mesh vs. skeleton+animation —
  `assets/merged_animation.blend` is the existing example of the latter) so
  re-imports stay reproducible across machines.

### `.blend` direct import vs. Blender → glTF — RECOMMENDED: glTF for world content

Godot **can** import `.blend` directly (it shells out to a configured Blender install on import). It's
convenient for a solo iteration loop, but for *this* project's world pipeline, **export Blender → glTF
into `res://` and keep the `.blend` outside `res://`** (in `assets/`/`art_src/`). Why:

- **No per-machine Blender dependency.** Direct `.blend` import requires every contributor (and any CI
  bake box) to have a matching Blender installed and the editor path configured, or the project fails to
  import. glTF is self-contained — `git clone` + open just works.
- **Keeps `res://` and git light.** A `.blend` under `res://` drags the binary into the game repo and
  gets a Godot `.import` sidecar; glTF is leaner and is the clean seam for the future source-art repo
  split (commit the exported result; `.blend` lives in LFS / a separate repo — see "Source control").
- **The baker pipeline wants an explicit export step anyway.** Custom properties ride along as glTF
  `extras` → node metadata (the marker params the baker reads). The marker/curve bridges (lanes, the
  `instance_`/`asset_path` swap) already assume an export step, so direct `.blend` import buys little.
- **Caveat (already noted under "Lanes via curves"):** glTF doesn't round-trip curves and triangulates
  them — bake lanes to named empties / a JSON sidecar, not curves.

Net: direct `.blend` import is fine for a quick personal look; **glTF-into-`res://` is the convention for
shared/baked world content.** (Skeletal character assets like `merged_animation.blend` can keep whatever
import preset already works — this decision is about world/kit geometry.)

## Source control for `.blend` files

- `.blend` files are large binaries — the repo already carries
  `assets/merged_animation.blend` and `assets/ui/AssaultRifle_5.blend`. Decide
  *before* volume grows: Git LFS, a separate asset repo, or committing only the
  exported/imported result and keeping `.blend` sources elsewhere.

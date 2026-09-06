# Archive — the 6×6 world and the retired road model

Archived **2026-08-28**. Nothing here was deleted; every file was moved, so `git log --follow`
still reaches its history. This directory exists because the project pivoted to rebuilding the
world from **island v3's ground up** — see **`blender/WORLD_REBUILD_PLAN.md`**, the plan of record.

## Why these were archived

The registered district pieces were the **6×6 grid** (36 districts, 504 m each, world 3024 m).
Island v3 — the current plan — is **4×4**, 2016 m. They are different worlds in different frames,
and they only partly overlap: island v3's road network puts **0 m** through `District_city_3_1`,
and its eight arterial crossings land in eight different districts. Detailing the old pieces was
investment in the frame being replaced.

The road content here is older still: it was built by the **mesh-graph / lane-kit** road model,
retired in the point/port-graph rewrite. Its operators (`rka.build_segment_from_curve`,
`rka.build_intersection`) no longer exist.

## What is here

| path | was | size |
|---|---|---|
| `world_6x6/world_source/pieces/` | the 36 district source `.blend`s + seam/lanekit sidecars | 161 MB |
| `world_6x6/world_source/pieces.json` | the piece registry that named them | |
| `world_6x6/world_source/world_master.blend` | the 6×6 master source | |
| `world_6x6/world_source/world_session.blend` | the multi-piece edit session over those pieces | |
| `world_6x6/districts/` | every baked district (`.tscn`/`.scn`/`.gltf`) | 379 MB |
| `world_6x6/master/World_master.*` | the baked 6×6 world the zone markers lived in | |
| `retired_road_model/` | `island_v3_roads*.blend` (lane-kit output, `RKA_LANE_PREVIEW*`), the v1 `.lanekit.json` sidecars, `lane_kit.blend`, `curb_kit.blend`, `intersection_prototype.*` | |
| `dead_tools/island_v3_to_roadkit.py` | drove `rka.build_segment_from_curve` / `rka.build_intersection`, both deleted | |
| `dead_tools/debug/TrafficCrashDiagnosticHost.java` + `TrafficCrashDiagnostic.tscn` | traffic-crash diagnostic hard-wired to `District_industry_5_1.tscn`; the scene referenced only this script, so the pair moved together | |
| `dead_tools/debug/LaneKitCombineTestIndustry51.tscn` | lane-kit combine test pointing at a district `.lanekit.json` that no longer exists. **Only the scene** — its script `LaneKitCombineTestHost.java` is shared with `LaneKitCombineTest.tscn` and stays | |

## What was deliberately KEPT in the tree

- `assets/world_source/island_v3.blend` / `_buildings` / `_full` — **the plan**, still current.
- `assets/world_source/buildings/` — `RecycledBuildingKit.blend` + manifest, the building library
  to harvest from. It is standalone, so archiving the pieces did not cost it.
- `assets/world_source/plateau/` — PLATEAU source data (122 MB).
- `assets/world_source/kit/road_kit.blend` — the **current** profile-section kit.

## What changed in the live tree

- `src/…/world/master/World_master.tscn` is now an **empty but valid** `Node3D` scene, so
  `hosts/WorldMaster.tscn` and `hosts/WorldMasterDebug.tscn` still load. The world streams nothing
  until a new one is baked over it. It is a baked artifact —
  `hosts/BakeWorldMaster.tscn` regenerates it.
- `hosts/SoloPiece.tscn` and `hosts/SoloIntersection.tscn` no longer instance a piece (there is
  none to instance). `blender/tools/build_piece.sh` now **inserts** the `piece` resource and node
  when they are absent rather than only substituting a path, so the first piece baked for the new
  world wires itself up.
- `src/…/world/districts/` is empty and kept, because that is where new bakes land.
- Two diagnostic hosts wired to archived districts were removed from the live tree (see the table).
  Removing a `@Script` class changes registrar generation, so this was verified with a full
  `./gradlew build` (BUILD SUCCESSFUL) as well as a scene load.

## Restoring

    git mv archive/world_6x6/world_source/pieces      assets/world_source/pieces
    git mv archive/world_6x6/world_source/pieces.json assets/world_source/pieces.json
    git mv archive/world_6x6/districts/*              src/main/resources/com/openworld/world/districts/
    git mv archive/world_6x6/master/World_master.tscn src/main/resources/com/openworld/world/master/

The zone markers in the archived `World_master.tscn` reference districts by `geometry_path`
**string**, resolved lazily at stream time — not by `ext_resource` — so restoring the master
without the districts loads fine and simply streams nothing.

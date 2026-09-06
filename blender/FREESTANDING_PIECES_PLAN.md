# Freestanding "Piece" authoring + repo layout split

> Durable copy of the approved design plan, kept here (not just in ephemeral plan-mode state) so
> work can resume across sessions. See git history of this file for revisions.

## Context

The world-authoring pipeline (`assets/world_source/`) currently forces every piece of world
content into one of two rigid boxes — a grid-bound **district** (`District_<theme>_<gx>_<gy>`,
504 m×504 m, seam-checked against grid neighbours) or an always-resident **overlay**
(`overlays/Overlay_<Name>.blend`, world-space, never seam-checked, built by a separate script).
This fights organic layouts (an island, a peninsula, a Forza/GTA-III-style airport-across-the-bay
area) and is the source of constant "what is an overlay, where does this go" confusion for the
artist. The one existing island+bridge case (Haneda + Rainbow Bridge) was hand-hacked as raw
coordinate constants in `world_grid.py` and never wired to a real streamed zone.

Verified before planning: **the Godot runtime has zero grid dependency.** `Zone` is an AABB
`size` + `loadRadius`/`unloadRadius`; `ZoneMarker` is a plain `Node3D` at an arbitrary world
position; `ZoneManager` treats every marker as an independent point, no lattice math anywhere;
`WorldBaker` copies whatever position the source Blender empty already has. The grid — and the
overlay/district split itself — is purely a Blender-authoring-side convention. No `src/main/java`
change is anticipated anywhere in this plan.

**Three decisions made during planning:**

1. **No "always-resident overlay" concept at all.** An overlay (e.g. Rainbow Bridge) becomes
   nothing more than **a piece that sits between two other pieces** — it streams in when a player/
   car approaches, exactly like a district, using the same load/unload-radius mechanism, just
   tuned (larger radius, sized to its span) so it's fully loaded before anything reaches it. This
   deletes the `STREAMED` vs `ALWAYS_RESIDENT` kind distinction entirely — there is only ever one
   kind of piece, so the artist never makes an overlay-vs-district decision at all, matching the
   user's original ask exactly. (This does not touch the separate, unrelated always-resident
   backbone/`ARTDECK` collision deck authored directly in the master blend — that's master-level
   road-network geometry, not a Piece.)
2. **Split code from data at the repo root**, mirroring the existing `src/` (Java code) vs.
   `assets/` (binary game assets) split. Today `assets/world_source/` interleaves Python
   code (the addon, `lib/`, `tools/`, per-folder `build_*.py` scripts) with data (`.blend` files,
   `.json` sidecars) in the same directories, which is awkward to navigate and awkward to think
   about from a "what's source, what's an artifact" standpoint. A new top-level `blender/`
   directory becomes the code root; `assets/world_source/` becomes pure data.
3. **Ground splitting stays manual through Phase 5** — a piece's ground is authored to its own
   footprint, and an oversized landmass is hand-split into adjacent pieces, exactly like districts
   today. **Automatic splitting of one continuous ground mesh into multiple pieces is wanted
   eventually**, but is real additional scope (cut-line retopology, not just object assignment) and
   is deliberately deferred to its own future design pass (§I / Phase 7) rather than guessed at here.

**Per prior direction: no backward-compatibility shims.** Both changes are one-time, direct
migrations (a piece-registry migration + a `git mv` layout migration) — nothing branches on
old-vs-new after they run.

---

## Design

### A. Repo layout split — `blender/` (code) vs `assets/world_source/` (data)   [DONE — Phase 0]

New top-level `blender/` directory (sibling to `src/`, `assets/`; root `tools/` was folded in and
removed):

```
blender/
  addons/road_kit_authoring/     # the Blender addon
  lib/                            # assemble.py, kit_common.py, lane_kit.py, road_network.py,
                                   #   session_common.py, world_grid.py, piece_registry.py
  tools/                          # every build/session/CLI script -- *.py and *.sh alike,
                                   #   including build_world.py (master assembly),
                                   #   build_curb_kit.py / build_lane_kit.py / etc. (kit-library
                                   #   builders), build_tokyo_tower.py (a building), and
                                   #   install_blender_addon.sh -- ONE grab-bag, not split by
                                   #   which asset domain a script happens to build (an initial
                                   #   split into separate towns/kit/buildings code folders,
                                   #   mirroring the old data-folder names, was folded into
                                   #   tools/ shortly after Phase 0 once it was clear the
                                   #   code/data split removed any reason to keep them apart --
                                   #   see the git history around 2026-08-01)
  AUTHORING_GUIDE.md              # documents the tooling/workflow, travels with the code
  FREESTANDING_PIECES_PLAN.md     # this plan's durable copy, written here (not under assets/)
```

`assets/world_source/` keeps only data:

```
assets/world_source/
  pieces/            # renamed from districts/ — now also holds the former overlay .blend (see B);
                       #   "districts" stopped being an accurate name once a piece can be any shape
  kit/                # *.blend + *.json only
  buildings/          # *.blend + *.json + *.md only
  plateau/data/        # unchanged (already pure data)
  world_master.blend, world_session.blend
  pieces.json, void_overrides.json
```

`overlays/` is retired (its one real file moved into `pieces/`). `overlays/Overlay_Arterial.blend1`
is a stray `.blend1` autosave with no matching `.blend` — left in place for manual cleanup rather
than silently relocated.

**Status: implemented and verified 2026-07-31, folded into its final one-`tools/`-folder shape
2026-08-01.** Every `__file__`-relative path constant across the addon, `lib/`, and every
`tools/*.py`/`*.sh` script was audited and fixed to correctly split "find a sibling code file"
(now under `blender/`) from "find a data file" (now under `assets/world_source/`) — including two
real bugs the initial move exposed: `kit_common.py`'s `_res_to_abspath` had a hardcoded
dirname-depth off by one, and `install_blender_addon.sh`'s `readlink -f` aborted under `set -e` on
a symlink still pointing at the pre-move location. The later `towns/`+`kit/`+`buildings/` →
`tools/` fold needed no path-constant changes at all (every affected script stayed exactly one
directory level under `blender/`, so every `dirname(dirname(__file__))`-style computation stayed
correct) — only external string references (docstrings, error messages, `build_world.sh`, the
addon's `ops_world_session.py`) needed updating to the new filenames. Verified via: addon
re-links/re-enables cleanly from the new path; `blender --background --python
blender/tools/build_world.py` reproduces the exact pre-move summary line; two addon smoketests
confirm imports resolve end-to-end through the real Blender addon-symlink mechanism.

### B. Piece model — one kind, no overlay special case

A Piece is identified by an anchor Empty carrying custom properties:

| Property | Meaning |
|---|---|
| `rka_piece_id` | filename stem, e.g. `District_city_1_1` (migrated) or a new freestanding name |
| `rka_piece_footprint` | `[x, y, z]` size — arbitrary, not required to be 504 m or square |
| `rka_piece_load_radius` / `rka_piece_unload_radius` | per-piece hysteresis (today implicit per grid district in the bake step — confirm exact current value when implementing migration) |
| `rka_piece_theme` | optional; defaults if absent |

No `rka_piece_kind` — every piece streams the same way. The anchor's own `.location` is the piece's
world center (replacing `world_grid.district_center()`/`elev_at()`'s lookup-table role).

`assets/world_source/pieces.json` is the one registry (git-tracked, same precedent as
`void_overrides.json`). `blender/lib/piece_registry.py` (pure Python, no `bpy`) exposes:

```python
all_pieces()          # every piece, read purely from pieces.json
piece_by_id(id)
set_piece(id, footprint, position, load_radius, unload_radius, theme)
```

`world_grid.py`'s `MAP`/`THEMES`/`GRID_N`/`district_center`/`elev_at` are retired from the "what
pieces exist" role entirely once migration runs — kept only for the pure elevation/theme gradient
math (e.g. a "not yet built" placeholder-plate preview for an original grid square with no piece
authored yet).

### C. Phase migration (one-time, deleted after use)

`blender/tools/migrate_to_pieces.py`, run once against the post-layout-split repo:

- For every non-void grid cell with a built `assets/world_source/pieces/<stem>.blend`: write a
  `pieces.json` entry — `id=stem`, `footprint=[DISTRICT,40,DISTRICT]`,
  `position=(*district_center(gx,gy), elev_at(gx,gy))`, `theme=theme_at(gx,gy)`, and whatever
  load/unload radius `WorldBaker`/`build_world.py` currently applies (verify exact source during
  implementation).
- For `pieces/Overlay_RainbowBridge.blend`: write a `pieces.json` entry with **no special kind** —
  `id=Overlay_RainbowBridge`, its real world-space `position`/`footprint` (already world-space, no
  translation needed), and **load/unload radii sized generously to its full span plus margin** so
  it's guaranteed fully streamed in before a car reaches either end (this is the one place
  migration must actually think, not just copy — the old always-resident behavior must be
  replicated via radius tuning, not dropped). Note as a follow-up check: the bridge's footprint
  now also participates in `check_seams.py` proximity adjacency against its landing districts for
  the first time (overlays never seam-checked before) — verify this doesn't flag a false mismatch.
- After running once and verifying `build_world.py`'s output is equivalent (see Verification),
  delete the migration script.

### D. Master build (`blender/tools/build_world.py`) — one loop, no branch

Replace the `for gy in range(GRID_N): for gx in range(GRID_N):` loop with
`for piece in piece_registry.all_pieces():`. The existing `region_<stem>` marker-emission code
already takes an arbitrary `location`/`size` per iteration — only the iteration source changes, and
every piece (former district or former overlay alike) goes through the exact same emission path,
with no kind branch. The void/placeholder-plate logic keeps consulting `world_grid.MAP`/`THEMES`
directly for "grid squares from the original layout with no piece yet" — a separate, smaller
concern from the piece loop.

### E. Session tooling (`lib/session_common.py`, `open_world_session.py`,
`writeback_world_session.py`, addon panel)

- One `Piece__<id>` wrapper convention (replaces `District__`/`Overlay__`). `resolve_item()`
  classifies via `piece_registry.piece_by_id()` — no regex, no file-existence probing.
- `open_world_session.py`/`writeback_world_session.py` iterate `piece_registry.all_pieces()`.
- Addon panel: merge "Multi-District Group" + "World Session" into one "Pieces" section, one
  vocabulary. Add "Place Piece Anchor" — drop an Empty at the 3D cursor, fill in
  id/footprint/radii, calls `piece_registry.set_piece()`.

### F. Automatic content → piece assignment

Spatial-containment pass at write-back time: any top-level object/collection not already under a
`Piece__<id>` wrapper is tested against every piece's world-space footprint AABB and
auto-assigned to the containing piece. Unassigned content (origin outside every footprint) is
flagged in the write-back report, never silently dropped. A `rka_piece_override` custom property
handles genuine straddling-content ambiguity (e.g. a bridge deck spanning two footprints). Ships
`--dry-run`-first.

### G. Seam-checking generalization (`tools/check_seams.py`)

Replace `abs(ax-bx) + abs(ay-by) == 1` on `(gx,gy)` with geometric footprint-proximity: two pieces
are adjacent if their world-space AABBs touch or nearly touch (epsilon), computed from
`pieces.json`. `.seam.json`'s format and comparison logic are unchanged — only adjacency discovery
changes. This now includes the former overlay for the first time (see C).

### H. Build scripts — one script, not a dispatch

Since there's no kind distinction anymore, `build_overlay.sh` is **deleted outright** rather than
merged/dispatched — `build_piece.sh` already handles every piece uniformly once the registry
drives it. One button in the panel, zero district-vs-overlay branch anywhere.

### I. Auto-chunking large ground meshes (deferred — own design pass, not detailed here)

Today (and through Phase 5 of this plan) ground splitting stays **manual**: a piece's ground is
authored to fit its own footprint, and a landmass bigger than one piece is hand-split into adjacent
pieces stitched via the existing seam-taper/`.seam.json` convention — exactly how districts work
today, just no longer grid-forced. Per user direction, **automatic splitting is wanted eventually**,
but is real additional scope, not a corollary of §F's object-assignment pass: §F assigns
already-separate objects to the piece whose footprint contains them; this is instead **slicing one
continuous authored mesh** into multiple piece-sized chunks — a materially different problem
(cut-line retopology, UV/material continuity across the cut, generating correct `.seam.json` data
for the synthetic new edges instead of just verifying hand-authored ones). Deliberately left
undesigned here: flagged as a **future phase, to be scoped in its own planning pass** once the
manual piece model (Phases 0–5) is working end-to-end and there's a real oversized landmass to
drive the requirements instead of guessing at them upfront.

---

## Rollout phases

**Phase 0 — Repo layout split (§A). DONE (2026-07-31).**

**Phase 1 — Piece registry + migration (§B, §C, §D). DONE (2026-07-31).** `lib/piece_registry.py` +
`pieces.json` written; `migrate_to_pieces.py` run once against the 36 districts + Rainbow Bridge
(district radii left `null` — no district ever carried explicit `load_radius`/`unload_radius`
meta, so `WorldBaker`'s size-based default formula still applies unchanged; the bridge's
footprint/position were measured from its own `*_Span` mesh's real geometry, not guessed, and its
radii deliberately widened — `half_extent + 300` / `+300` hysteresis — to replicate its old
always-resident guarantee via streaming instead). `tools/build_world.py`'s grid loop now sources
every marker's position/footprint/radii from the registry (§D) through one shared
`_emit_region_marker()` path, with a new pass after the grid walk for registered-but-not-gridded
pieces — this is what actually makes `Overlay_RainbowBridge` a normal streamed piece for the first
time (previously it was invisible to `build_world.py` entirely, a separate always-resident
pipeline). Migration script deleted after verifying below.

**Phase 2 — Spike the freestanding case. DONE (2026-08-01).** Also required loosening
`build_piece.sh`'s `District_*`-only name check (part of §H's end-state, pulled forward since
Phase 2 needed a real bake to test with) — no other change needed, it already handled any piece
uniformly once the registry drove it. Hand-authored `Piece_SpikeTest` (a trivial ground pad,
`assets/world_source/pieces/Piece_SpikeTest.blend`, built by a throwaway one-off script, not kept
in the repo — the `.blend` is the artifact, same as any hand-authored piece), registered directly
via `piece_registry.set_piece()` (no grid cell, no district naming, floated above the existing
spawn point purely so a headless run could observe it loading without a scripted walk-in), rebuilt
`world_master.blend` (`freestanding=2` — bridge + spike), baked via `build_piece.sh
Piece_SpikeTest` (worked unmodified) and `build_world.sh` (`zones=38`). **Verified via headless
`WorldMasterDebug.tscn`:** `ZoneManager` streamed in `District_city_1_1`,
`Overlay_RainbowBridge` (now genuinely distance-triggered, not hardcoded — ~913 m from spawn,
inside its 1149 m `load_radius`), and `Piece_SpikeTest` (0.5 m from spawn, inside its 250 m
`load_radius`), each logged with the exact same `streaming IN zone '<id>'…` / `LOADED zone
'<id>'…` messages — **no kind branch anywhere in the runtime path**, confirming the whole
redesign's core hypothesis end-to-end in the actual running game, not just in Blender.

**Phase 3 — Generalize session tooling. DONE (2026-08-01).** `session_common.py` rewritten:
single `Piece__<id>` wrapper (`District__`/`Overlay__` retired), `resolve_item(name)` is now a
pure `piece_registry.piece_by_id()` lookup (no `districts_dir`/`overlays_dir` params anywhere —
`piece_registry.PIECES_DIR` centralizes that), and `append_district_content`/
`append_overlay_content`'s district-vs-zero-offset branch collapsed into one
`append_piece_content()` that always uses the piece's registered `position` (a district's happens
to have been computed via `district_center`/`elev_at` at migration time; the code doesn't know or
care). `open_district_group.py`/`writeback_district_group.py`/`open_world_session.py`/
`writeback_world_session.py`/`session_dirty.py`/`ops_group_edit.py` all updated to match — the
world-session tools iterate `piece_registry.all_pieces()` (filtered to ones with a built `.blend`)
instead of a grid scan unioned with a directory listing. `writeback_world_session.py`'s per-item
build dispatch (district → `build_piece.sh`, overlay → `build_overlay.sh`) collapsed to always
`build_piece.sh`, since Phase 2 already proved it handles any piece. The grid-coordinate seam-pair
regex survives as explicitly-scoped scaffolding in `writeback_world_session.py`/
`ops_group_edit.py` (a freestanding piece id just doesn't match it and is excluded) until §G
replaces it with real footprint-proximity adjacency in Phase 5. Addon panel: "Multi-District
Group" + "World Session" merged into one "Pieces" box (world-session view when the open file *is*
world_session.blend, else a "Scoped Group" sub-section — mutually exclusive now, resolving a
pre-existing ambiguity where opening the session file itself made both boxes claim it
simultaneously); new **Place Piece Anchor** operator (`ops_world_session.py`) drops an Empty at
the 3D cursor tagged with `rka_piece_id`/`rka_piece_footprint`/`rka_piece_load_radius`/
`rka_piece_unload_radius`/`rka_piece_theme` custom properties (§B's anchor convention) and calls
`piece_registry.set_piece()` immediately — the artist still authors + saves the piece's own
`pieces/<id>.blend` by hand afterward; "Refresh World Session" picks it up once that file exists,
same as a newly-built district. **Verified:** fresh `open_world_session.py` run appended all 37
pieces correctly under `Piece__<id>` (`WORLD_NAV: 36 grid-cell labels, 1 freestanding-piece
label`); `writeback_world_session.py --dry-run` reports 0 changed/37 unchanged (every id resolves
through the registry) and `--dry-run --force-all` reports all 37 as valid targets; a real
`open_district_group.py`/`writeback_district_group.py --dry-run` round trip against two real
districts (`District_city_1_1`, `District_city_2_1`) resolves and reports correct object counts.

**Phase 4 — Automatic content-to-piece assignment** per §F.

**Phase 5 — Generalize seam-checking + retire `build_overlay.sh`** per §G/§H. **`build_overlay.sh`
retired (2026-08-01)**, pulled forward during the grid-addressing migration (see "2026-08-01 —
Auto-fit signed grid addressing" below) once every piece — grid or freestanding — got a uniform
id and `build_piece.sh` already handled both; the seam-adjacency regex was also replaced with a
registry `grid`-field lookup at the same time. §G's actual geometric-footprint-proximity
adjacency (as opposed to the existing `grid`-cell Manhattan-distance heuristic, which still
excludes freestanding pieces on purpose) is **still not done**.

**Phase 6 — Documentation.** Rewrite `AUTHORING_GUIDE.md` around "pieces" as the one concept.

**Phase 7 (future, separate design pass) — Auto-chunking large ground meshes** per §I. Not detailed
in this plan; scope it fresh once Phases 0–5 are working and a real oversized-landmass case exists
to design against.

---

## Verification

- **Phase 0:** addon reloads/symlinks correctly; `blender --background --python
  blender/tools/build_world.py` before/after the `git mv` produces identical output. **PASSED.**
- **Phase 1:** diff the migrated run's summary line/counts and `region_*` marker properties
  (position, size, theme meta, traffic route, and — new — the bridge's radii) against pre-migration
  behavior. **PASSED** — `districts=36 linked=36 plates=0 void=0` unchanged, zero
  "not in pieces.json" fallback warnings (full coverage), `region_District_city_1_1` byte-identical
  properties, new `region_Overlay_RainbowBridge` present with sane position/footprint/radii and a
  new `freestanding=1` field in the summary line reflecting it. One known side-effect flagged, not
  fixed here: districts with baked `NEIGHBOR_REF` content (`link_neighbors.py`) carry a stale
  absolute path to the old `districts/` folder inside the `.blend` itself (data baked into the
  file, not a Python path constant) — Blender prints a harmless missing-library warning when such
  a file is opened directly; re-running `link_neighbors.py` (already fixed for the new layout)
  against it refreshes the reference. `NEIGHBOR_REF` is dropped before export, so this never
  reaches the game.
- **Phase 2:** headless `WorldMasterDebug.tscn` run, watch `ZoneManager.debugLog` for the new
  freestanding zone's load/unload lines and the traffic health summary. **PASSED** (see above) —
  load lines confirmed for both new freestanding pieces; the run only covered LOAD (piece placed
  near spawn so a short headless run could observe it, no scripted walk-away), not a full
  load→unload cycle — the same distance-threshold code path handles both directions identically
  (no separate logic to verify), so this wasn't pursued further, but a live walk-out is easy to
  confirm by hand if wanted.
- **Phase 3:** `open_world_session.py` → hand-edit → `writeback_world_session.py` round trip
  against a migrated former district, confirm identical behavior to today. **PASSED** (see above)
  — a real hand-edit-and-write-back wasn't exercised against production district files (the
  dry-run paths + a real read-only append/dry-run-writeback round trip cover the discovery,
  classification, and object-count mechanics without risking production `.blend` corruption); the
  actual per-object append/un-shift math is unchanged code, only its offset SOURCE changed
  (`piece["position"]` instead of a branch), so this is considered adequately covered.
- **Phase 4:** `--dry-run` report on intentionally-straddling content, confirm it's flagged not
  silently misfiled; real write-back confirms exact per-piece object membership.
- **Phase 5:** `check_seams.py` against the freestanding piece + a real neighbour, and against the
  (now seam-checked) Rainbow Bridge piece and its landing districts.
- Throughout: no `src/main/java` changes anticipated — treat any apparent need for one as a sign
  the design has drifted and stop to reassess.

---

## 2026-08-01 — Auto-fit signed grid addressing (separate follow-on plan, DONE)

Full design doc: see git history of `/home/danilko/.claude/plans/glittery-shimmying-sunbeam.md`
(plan-mode artifact, not checked in) for the complete rationale; summarized here per this file's
own "append a dated section instead of a new file" convention.

**What changed.** All 37 pieces (36 grid districts + the bridge) renamed from
`District_<theme>_<gx>_<gy>`/`Overlay_RainbowBridge` to the uniform `Piece_<gx>_<gy>` scheme —
`gx,gy` computed once via the new `world_grid.grid_cell_of(world_x, world_y)` (unclamped
floor-division, so it extends to negative/out-of-range cells with zero renumbering of anything
already built) and stored explicitly as a `grid` field on each `pieces.json` entry (not just
derivable from `position`) plus `rka_piece_grid`/`rka_piece_id`/`rka_piece_theme` custom
properties on each piece's own top-level content collection. The bridge computes to the same
nominal cell as an existing district (`(2,3)`); resolved via a suffix (`Piece_2_3` / `Piece_2_3_b`)
rather than merging the two — merging would have regressed the bridge's independently-tuned wide
streaming radius (`half_extent + 300`) into one shared marker, discussed and confirmed with the
user. Migration script (`migrate_to_grid_ids.py`, one-time, deleted after use) also deleted each
piece's stale old-named baked Godot output.

Every remaining `District_`/`Overlay_`-shaped construction/parse site was swept and fixed:
`world_grid.py` (`piece_stem` → `piece_id_for_cell`, `LANDMARKS`' vestigial `.tscn` field
dropped), `build_world.py`, `ops_group_edit.py` (regex `DISTRICT_RE` → registry `grid`-field
lookup), `open_world_session.py`, `writeback_world_session.py` (seam-adjacency regex →
`grid`-field lookup, still gated to real grid cells only — a freestanding piece's footprint can
span several cells, so a Manhattan-distance-1 check doesn't mean anything for it; real
geometric-proximity adjacency is still §G/Phase 5, not done), `link_neighbors.py`,
`link_landmark_preview.py`, `link_world.py`, `ops_export.py` + `panel.py` (Godot Export gate:
`District_`-prefix check → registry lookup, so a freestanding piece's own file can now use
one-click export too — previously it couldn't), `check_lanekit_connectivity.py` (dead
`District_`-regex auto-offset → registry `position` lookup — this one was a **real latent bug**,
not just a rename: after the id rename it would have silently stopped auto-offsetting *any*
piece, always falling back to `(0,0,0)`).

**`build_overlay.sh` retired outright** (pulled forward from §H, since every piece now bakes
through `build_piece.sh` uniformly and nothing can pass `build_overlay.sh`'s `Overlay_*` prefix
gate anymore) — its stale output directory (`world/overlays/`) deleted too. **The bridge's
permanent `OverlayRainbowBridge` node in `hosts/WorldMaster.tscn`** (the pre-redesign
always-resident model, superseded back in Phase 1 once the bridge got a normal `ZoneMarker`
but never actually removed until now) **was also removed** — leaving it in would have double-
rendered/double-collided the bridge once its `ZoneMarker` started streaming it in normally.

**"Add Piece" now auto-suggests** (`RKA_OT_place_piece_anchor.invoke`): pre-fills `piece_id`/
`theme` from `grid_cell_of`/`suggest_piece_id`/`theme_at` at the dropped cursor position (still
editable — a hero piece can keep a custom name), stamps `rka_piece_grid`, and warns (non-blocking
— footprints are arbitrary, two anchors can legitimately share a cell's nominal address) if
another registered piece already owns that cell.

**Verification — PASSED:**
- `grid_cell_of()` recovered all 36 districts' exact existing `(gx,gy)` from their real
  `position`, pre-migration (the correctness precondition the whole migration depended on).
- Rebuilt all 37 pieces (`build_piece.sh`) and the master (`build_world.sh`) from scratch after
  the rename: zero failures, summary line byte-identical to the pre-migration baseline
  (`districts=36 linked=36 freestanding=1 harbor=7 city=7 resid=7 rural=8 mtn=4 snow=1
  industry=2`), master zone count `zones=37`. `World_master.tscn` has zero remaining
  `Overlay_RainbowBridge` references; the bridge's `ZoneMarker_Piece_2_3_b` carries the same
  `load_radius=1149.7`/`unload_radius=1449.7` verified earlier in Phase 2.
- Addon register/unregister cycle: clean.
- Fresh `open_world_session.py` run: all 37 `Piece__<id>` wrappers, no stale-library warnings.
  (One pre-existing `world_session.blend` wrapper from *before* this migration,
  `District_harbor_3_0`, still had its dirty flag set from unwritten edits; verified its content
  — an `Intersection_4WAY_006` — was already present, and more complete, in the real renamed
  `Piece_3_0.blend`, i.e. the flag was stale bookkeeping, not at-risk work, before force-dropping
  it.) `writeback_world_session.py --dry-run` reports 0 changed / 37 unchanged.
- **`check_seams.py` spot-check found a pre-existing, unrelated bug** (confirmed via a
  byte-identical diff against the pre-migration `.seam.json` content, and reproduced across 3
  independent adjacent pairs): every seam's recorded `world_x`/`world_y` is actually a
  **local** edge offset (±252, i.e. `DISTRICT/2`) despite the field name, and `check_seams.py`
  compares the two sides with a raw equality check and no `district_center` offset applied —
  so it fails on *every* real adjacent pair, not just ones touched by this migration. Not fixed
  here (out of scope for this plan; needs a decision on whether to fix the comparison or
  regenerate the sidecars with true world coordinates) — flagged for a follow-up pass.

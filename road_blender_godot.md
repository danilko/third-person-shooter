# Working plan — Mesh-first road kit (Blender addon) → Godot Path3D

> **Progress tracker for a multi-session effort.** Mark items `[x]` as they land so a fresh
> session can resume exactly where this one stopped. Approved 2026-07-22. Companion docs:
> `assets/world_source/AUTHORING_GUIDE.md` (conventions of record), `PLAN_WORLD_AUTHORING.md`
> (its Phase A is superseded by this doc — see the note there).

## Context (why)

Roads today are generated procedurally: hand-drawn `road_*` centerline curves feed
`assets/world_source/lib/road_graph.py`, which abstracts them into a junction-node/edge graph
and *derives* per-lane offset routes + bezier turn connectors (`generate()`,
`road_graph.py:302-393`). This generator is **too limited** — it can't represent arbitrary
hand-modeled intersections, asymmetric curb placement, or non-uniform merges — and is not to be
extended further.

A modular mesh-based road kit was explored in
`assets/world_source/districts/District_manual_1.blend` (7 pieces on a 5 m grid: a plain lane
tile, a lane-tile-with-curb variant, a standalone curb/gutter strip, and 3-way/4-way intersection
pieces with simplified `.001` collision variants). That file is unwired reference material.

**Goal**: a Blender addon that builds roads/intersections **freely as mesh** — placing,
duplicating, rotating kit pieces, adding curbs to either side as needed — where each lane piece
carries a **hand-authored centerline curve** (not auto-derived from mesh topology). The addon
detects lane-to-lane connections across piece boundaries by endpoint proximity where
unambiguous, with a manual review UI for ambiguous cases (multi-lane intersections). This curve
+ connectivity data exports to a new sidecar and eventually drives native Godot `Path3D`/
`Curve3D` nodes for vehicle navigation and (later) GPS/A* — replacing `VehicleRoute`'s
`Marker3D`-list representation, which today hand-reimplements Catmull-Rom smoothing and
arc-length sampling that `Curve3D` already provides natively.

This starts as a new, parallel pipeline alongside `road_graph.py`/`assemble.lay_road_graph`/
`road_network.py` — but the user has said the existing Blender-side road code isn't working well
and it's fine to remove/replace it outright rather than maintain it forever in parallel. So: no
obligation to keep those generators running once the new pipeline covers their ground — only
constraint is that **already-baked Godot output** (`.tscn`/`.glb`/`.scn` for districts not yet
migrated) keeps working, since baked artifacts don't depend on the Blender scripts that produced
them existing. Placeholder district meshes get replaced one district at a time as the new
pipeline matures; old Blender-side generation code can be deleted once nothing still depends on
regenerating through it (track this per-file in this doc as districts migrate off it).

**Also flagged by the user (2026-07-22), broader world-authoring direction**: the PLATEAU
real-world extraction pipeline (`lib/plateau_import.py`, `lib/plateau_common.py`, `plateau/`
data + extraction scripts) is being deprioritized — going forward, districts/city content are
mostly hand-redrawn/rebuilt using existing PLATEAU output as an approximate reference rather than
regenerated through the extraction pipeline. This is consistent with (but a separate task from)
this doc's shift toward hand-authored, mesh-first content — noted here for context, not scoped
into the phases below.

**Long-term scope.** This is intended to eventually replace *all* street authoring — including
the backbone/arterial cell-grid tile system (`lib/road_network.py` + `kit/build_roads.py` →
`roads_kit.blend`, loaded via `kit_common.load_kits()`, used by `towns/build_world.py` /
`towns/districts/build_district.py`) — not just local district streets. That system is
grid-cell-constrained (an auto-tiler), which is a form of the same "too limited" complaint.
Phases 1-5 below scope the pilot to local/internal streets only and leave `road_network.py`
untouched, but the architecture (multiple authoring modes converging on one universal
lane-centerline-curve + connectivity pipeline, see "Authoring modes" below) is meant to
generalize to backbone/arterial roads later without a redesign — replacing `road_network.py`
is a future phase, not in this doc yet.

All paths relative to `assets/world_source/` unless prefixed `src/`.

## Authoring modes

A lane-bearing mesh can come from any of three methods — the pipeline from here on (centerline
curve authoring → connectivity → export) is identical regardless of which produced the mesh:

1. **Kit-piece placement** — instance a pre-built piece (lane tile, curb, intersection) from
   `kit/lane_kit.blend`; the piece's centerline curve(s) are authored once on the kit piece and
   travel with every instance (Phase 1-2).
2. **Hand-modeled mesh** — fully custom geometry for a one-off shape, placed in a district's
   `ROAD_MANUAL` collection; centerline curve(s) extracted the same way kit pieces are — tag a
   `lanedata` vertex group, run `RKA_OT_centerline_from_vertex_group` (Phase 2).
3. **Curve-driven GN sweep** — draw a curve first, generate the visual mesh from it via a
   geometry-nodes modifier (reusing `kit_common`'s existing GN sweep building blocks,
   e.g. the pattern in `make_road_profile_group`/`road_from_curve`, as a *tool*, not reviving
   `road_graph.py`'s graph-abstraction/generation step); the driving curve **is** the lane
   centerline directly, no separate authoring step. Useful for long freeform runs where hand-
   placing a centerline separately from the mesh would be redundant. Not scheduled into a
   numbered phase yet — add as a `RKA_OT_add_curve_swept_road`-style operator once Phase 2's
   plain centerline authoring is proven, since it shares the same curve-object convention.

All three converge on `lanecl_*`-prefixed curve objects, which is all `lib/lane_kit.py` and the
connectivity/export tooling (Phase 3) ever look at.

## Architecture

**Two authoring tiers** (mirrors the existing kit-library-vs-per-district-blend split already
used for walls/props in `kit_common.py`):

- **Tier 1 — kit library**: new `kit/lane_kit.blend`. One Blender **Collection per reusable
  piece** (lane tile, curb strip, intersection), each holding its mesh(es) + `-colonly` proxy +
  one or more hand-authored **centerline Curve objects** in local space. Intersection pieces
  carry all their internal per-lane/turn curves once, here. Promoted from
  `District_manual_1.blend`'s 7 pieces.
- **Tier 2 — assembly**: a district `.blend`, built with the new addon. Pieces are placed via
  `kit_common.link_collections` + `kit_common.instance_collection` (true library link — pulls
  mesh *and* paired curve(s) together, no metadata-pairing convention needed). One-off custom
  curved segments go in a `ROAD_MANUAL` collection using the same centerline-authoring operator
  as kit pieces. Both feed the same connectivity/validation/export pipeline, which only cares
  about world-space curve objects tagged as lane centerlines.

**Naming**: new centerline curves use prefix `lanecl_` (not `road_`) so the new collector never
cross-picks the old `road_*` collector's objects if both exist in one `.blend` during migration.
New sidecar: `districts/<piece>.lanekit.json` (parallel to, distinct from, `<piece>.roads.json`).
New scratch collection `ROAD_KIT_SRC` (if the addon needs one for in-progress editing) gets added
to `export_world.py`'s drop-list alongside the existing `ROADS_SRC`.

> **Naming collision avoided (2026-07-22):** there is a pre-existing, unrelated, actively-used
> `assets/world_source/kit/build_roads.py` → `roads_kit.blend` (a cell-grid tile kit driven by
> `lib/road_network.py`, loaded via `kit_common.load_kits()` for backbone/arterial visuals in
> `towns/build_world.py` / `towns/districts/build_district.py`). The new Tier-1 kit library in
> this doc is named `kit/lane_kit.blend` (not `road_kit.blend`) to stay unambiguous —
> matches the `lib/lane_kit.py` module name. `road_network.py`/`roads_kit.blend` is a separate
> backbone-arterial concern and is **out of scope** for this doc; do not touch it.

---

## Phase 1 — Kit library + placement addon skeleton

- [x] P1.1 Promote `District_manual_1.blend`'s pieces into `kit/lane_kit.blend`
      (`kit/build_lane_kit.py`, run via `blender --background --python kit/build_lane_kit.py`) —
      5 named Collections (`Kit_LaneStraight5`, `Kit_LaneCurbRightCityGutter5`,
      `Kit_CurbSideCityGutter`, `Kit_Intersection4Way2Lane`, `Kit_Intersection3Way1Lane`), each
      holding just its visual mesh. **No per-piece `-colonly` collision proxy** — collision is
      deferred to a later Godot-side/bake-time pass that merges whole assembled road segments
      into one collision object instead of one box per tile (see the collision strategy note
      below). Also: the `.001` variants are NOT LOD/collision meshes as their names implied — the
      4-way `.001` covers only one quadrant of the footprint, the 3-way `.001` is a
      differently-shaped/sized piece, not a reduction — kept as plain `_variant` objects in the
      same collection for hand review — **needs your eyes in Blender**: open `kit/lane_kit.blend`,
      decide per intersection whether to keep/merge/discard each `_variant` before Phase 2
      centerline authoring.
      Also fixed a truncated source name: `kit_side_ight_city_gutter_curb_w0.6m_l5m` →
      `kit_side_straight_city_gutter_curb_w0p6m_l5m`.
- [x] P1.2 New addon `addons/road_kit_authoring/` (real installable addon: `bl_info`,
      `register()`/`unregister()`, N-panel — not a batch script, since placement/snapping/review
      need live interactive operators): `__init__.py`, `paths.py` (symlink-safe path setup —
      resolves `__file__` through `os.path.realpath` since the addon is dev-installed as a
      symlink, see P1.4 — and locates `kit/lane_kit.blend` + `lib/` from there), `props.py`
      (`RKA_SceneSettings`: grid=5.0, connect_eps=1.0, active_kit_collection, place_direction;
      `RKA_CurveSettings` on `Curve.rka_curve`: lane_width, oneway, road_class, loop,
      end_behavior — registered now, consumed starting Phase 2), `panel.py` (N-panel "Road Kit"
      tab: Kit Library / Placement / Connectivity boxes).
- [x] P1.3 `ops_placement.py`: `RKA_OT_link_kit_library` (links every Collection out of
      `kit/lane_kit.blend` — not hardcoded by name, so new kit pieces need no addon change),
      `RKA_OT_place_piece` (modal, click-to-drop a linked instance of the active kit Collection,
      ray cast to the world Z=0 ground plane, snapped to the grid, via
      `kit_common.instance_collection`; Esc/right-click stops), `RKA_OT_duplicate_piece`
      (offsets selected instance(s) one grid step along their own local placement direction, so
      it stays correct after a 90° rotation), `RKA_OT_rotate_piece_90` (rotate selected
      instance(s) ±90° around world Z in place).
- [x] P1.4 `README.md`: dev-install instructions (symlink into Blender's user addons dir); added
      the pointer + supersession note in `AUTHORING_GUIDE.md` §7 (top of the section, not a
      rewrite).
- [ ] P1.5 Verify: in a scratch district `.blend`, place a run of straight tiles + a 4-way
      intersection via the addon on the 5 m grid with no gaps/overlaps; confirm editing a piece
      in `lane_kit.blend` and reloading the district reflects the edit (link round-trip).
      **Partially done, headless:** a smoke-test script
      (register → link kit library → instance a piece → duplicate → rotate → unregister) ran
      clean under `blender --background`, confirming `link_kit_library` found and linked every
      Collection in `kit/lane_kit.blend` (now 6, not 5 — an extra `Kit_Intersection3Way2Lane`
      exists in the kit file beyond P1.1's original 5; harmless, the operator doesn't hardcode
      names) and that duplicate/rotate mutate the right selected object. **Still needs your
      hands in Blender**: the modal click-to-place path needs a real 3D viewport (can't run
      headless), and the "no gaps/overlaps" visual check + the link-round-trip-after-editing-a-
      piece check are both eyes-on checks — install the addon per the README, place a short
      straight run + a 4-way intersection, and confirm both.

> **Collision strategy note (decided 2026-07-22):** kit pieces in `kit/lane_kit.blend` carry
> **no per-piece `-colonly` proxy** (confirmed: many tiny `StaticBody3D`/`CollisionShape3D` nodes
> down a long street cost more — per-node overhead — than a few large merged colliders for a
> contiguous run). Collision is deferred to a **one-time merge pass on the Godot/bake side**:
> once a run of placed pieces is finalized, generate one collision object per contiguous major
> road segment (not per tile). `kit_common.colonly_swept` already does this kind of consolidation
> for the old curve-swept ribbon system (one continuous collision mesh instead of
> segment-by-segment) — reuse that pattern. Where exactly this merge runs (a Blender-side export
> step vs. a Godot-side `WorldBaker` pass over the placed geometry) is still open — decide when
> Phase 3's export step or Phase 4's bake step is being built. Until then, placed districts using
> this kit have **no road collision** — fine for authoring/visual walk-throughs, not yet for a
> real drivable pilot (Phase 5).

## Kit geometry v2 — parametric ground-up spec (design, 2026-07-23)

Prompted by wanting the kit rebuilt from a clean, exact parametric spec instead of continuing to
refine ad-hoc hand-modeled geometry. This section is the spec + the design answers to "how do
multi-lane roads and intersections compose from the single-lane unit" and "how is direction
represented for a 2-way street" — **not yet built**, except the direction primitive (item 2),
which is a real bug-fix-shaped improvement made immediately. The rest needs your confirmation
before touching geometry, in particular the intersection strategy (item 4).

1. **Single-lane tile primitive (confirmed against the current hand-modeled geometry — already
   matches, nothing to change here).** `LANE_WIDTH = 5.0 m` on X, **centered** (`X ∈ [-2.5,
   +2.5]`, centerline at X=0) so a 90° rotation never shifts the footprint; `LANE_LENGTH = 5.0 m`
   on Y, **endpoint-pivoted** (`Y ∈ [0, 5]`, forward = +Y per `kit_common`'s existing "forward on
   +Y" convention) so chaining tiles along the travel direction is a plain `+5 m` translate with
   no gap/overlap math — this is exactly what `RKA_OT_duplicate_piece` (P1.3) already does.
   Verified: `kit_single_lane_w5m_l5m` bbox is X[-2.49,2.51] Y[0,5]; the curb-right piece's
   drivable surface is the same X[-2.49,2.51] span plus curb geometry tacked on past X=2.51.

2. **Direction is now explicit via vertex-group weight, not inferred from topology (shipped).**
   `kit_common.centerlines_from_vertex_group` orders each lane tail→head by **ascending vertex
   weight** when the two path endpoints' weights differ (tag 0.0 at the start, 1.0 at the end, in
   Blender's Vertex Weights panel); if both ends carry the vertex-group default (1.0, i.e.
   direction hasn't been authored), it falls back to the old topological-walk order — so this is
   fully backward compatible with what's already tagged in `lane_kit.blend`. Verified with a
   synthetic test (weight said "tail is the Y=5 end" while vertex index order said the opposite —
   weight won) and re-confirmed unchanged output on the real, live `lane_kit.blend` (read-only,
   not saved). **Why this matters for your 2-way-street question below:** a single lane-tile mesh
   can serve *either* direction of a 2-way street just by rotating its placed copy 180° around Z
   — weight travels with the vertex, so "head" stays correct after rotation, whereas a purely
   topological walk (arbitrary tie-break) would not reliably survive that rotation. No second
   mirrored mesh needed.

3. **Multi-lane = combine mesh, and the existing extractor already handles it for free (shipped:
   detection + seam marking; deferred: physical joining).** Per your sketch: place N single-lane
   tiles side by side (`X` offset by `LANE_WIDTH` each), and each seam between two tiles gets a
   marking based on whether the two lanes run the same or opposite direction. **Not** joined into
   one physical mesh yet — kept as separate placed instances during authoring (matches the P1
   Collision-strategy-note precedent of deferring merges to a later bake pass, not authoring
   time); each tile's `lanedata` stays its own connected component regardless, so
   `centerlines_from_vertex_group` already gives one curve per lane with no new code — proven
   against the 4-way intersection's 16 tagged verts splitting into 8 lanes, and re-verified here
   with synthetic same-direction and opposite-direction pairs.
   - `kit_common.lane_marking_strip(name, x_center, y0, y1, z, width, matkey, coll)` — thin flat
     box (reuses `box()`), `matkey` = `'line_w'` (white, same-direction) or `'line_y'` (yellow,
     opposite-direction) — both material keys already existed in `MATS` (reserved, unused until
     now).
   - `RKA_OT_combine_lanes` (`ops_combine.py`) — takes the selected placed lane instances, sorts
     left-to-right by X, and for each adjacent pair reads direction straight off each instance's
     own world rotation (no mesh evaluation needed): same direction → flush white strip at the
     seam midpoint; opposite direction → yellow strip, **plus a warning if the pair is sitting
     flush with no gap** (real median/barrier spacing is a placement decision left to you — the
     operator doesn't auto-relocate geometry, just tells you when a yellow-marked seam has no
     separation). New scene settings `lane_surface_z` (0.15, matches the hand-modeled pieces) and
     `lane_marking_width` (0.1). Verified headless (synthetic instances only, live file never
     opened for writing): same-direction flush → 1 white strip at the exact midpoint;
     opposite-direction flush → 1 yellow strip + the expected warning; opposite-direction with a
     0.5 m gap → 1 yellow strip at the correct offset midpoint, no warning.
   - **Gotcha this testing caught, now fixed:** the tile is endpoint-pivoted at local Y=0, so
     naively rotating a copy 180° *in place* for the reverse lane spins its footprint to the
     wrong road segment entirely (Y∈[0,5] becomes Y∈[-5,0] — zero overlap with its
     same-direction neighbour). `RKA_OT_duplicate_piece` now has a `reverse` option (a second
     panel button, "Duplicate (reverse)") that rotates 180° **and** pushes the placement one
     grid-step further along the source's original forward direction, so the reversed copy's
     footprint correctly lands back on the same segment. Verified end-to-end headless: place →
     duplicate-reverse → combine produces one correctly-positioned yellow seam, no manual
     Y-offset math required from you.
   - A future "merge by distance" cleanup pass to weld touching boundary edges into one
     watertight combined mesh (deferred per above) is safe: only truly-coincident verts merge,
     and lane centerlines sit at each tile's own mid-width, never coincident with a neighbor's.

4. **Intersections composed from lane pieces, not one hand-sculpted mesh — RESOLVED (2026-07-23),
   prototype shipped.** Neither of the two options originally listed here was taken. Prompted by a
   reference traffic-engineering diagram (curb-radius + turn-swept-path diagram, kerb radius
   R=3.5m real-world minimum), the answer is **closed-form 2D corner-fillet math**, computed
   fresh from nothing but a list of approach-arm angles — no hand-tagging, no `road_graph.py`
   dependency (not even as a utility function):
   - **`lib/intersection_kit.py`** (new, pure Python, no bpy — `python3 lib/intersection_kit.py`
     self-tests, same convention as `road_graph.py`/planned `lane_kit.py`). `Arm(name, angle_deg,
     lane_width, lanes)` describes one approach by its outward angle only. `corner_fillet(edge_a,
     edge_b, radius, segments)` is the core primitive: exact tangent-arc rounding of two rays
     meeting at a vertex (closed-form, not Blender's GN Fillet Curve node — kept as plain Python
     so it self-tests with numeric assertions and has zero Blender dependency). `build_curb_corners`
     walks the arms sorted by angle and fillets every angularly-consecutive pair **except a
     "through pair"** (`is_through_pair`, ~180° apart — e.g. a T-junction's main street): there the
     curb is already one continuous straight line, so it's skipped entirely rather than filleted —
     this is what makes a T-junction "3-way with a direct through movement" fall out for free,
     verified in `self_test()` by checking the skipped pair's two curb-edge lines really are
     collinear. `build_lane_movements` generates every ordered arm-pair × lane-index movement: a
     straight 2-point line for a through-pair, a filleted arc (same `corner_fillet` primitive) at
     **`radius = kerb_radius + (lane_index + 0.5) × lane_width`** for a turn — i.e. **larger than
     the curb radius**, so the AI's driving arc is always a wider, easier swing than the physical
     curb corner, not a tight hug of it. `preset_4way` / `preset_3way_t` / `preset_3way_y` cover
     the three requested junction shapes. Self-tested: exact-radius arc/tangent geometry, 4-way
     rotational symmetry (4 congruent corners), T-junction through-pair skip + collinearity,
     Y-junction's 3 corners (no through-street), 12 lane movements on a 4-way (4 through + 8 turns),
     and that raising `kerb_radius` measurably widens the generated turn arcs.
   - **Default radius is deliberately RELAXED, not the tight real-world minimum.** The reference
     diagram's R=3.5m is a real-world *minimum* (delivery-truck-feasible, tight). Game AI driving
     doesn't need to hug that minimum, so `RKA_OT_build_intersection.kerb_radius` defaults to
     **9.0 m** (exposed, freely tunable per intersection via the operator's F9 redo panel) — a
     wide, comfortable arc. Turn centerlines get an even larger effective radius on top of that
     (see above), so the driving path is always relaxed relative to the curb it's set back from.
   - **`kit_common.poly_curve`** (new) — plain POLY-spline curve through exact points, no NURBS
     smoothing/approximation (unlike `road_from_curve`/`barrier_from_curve`'s NURBS spines, which
     would drift off the hand-computed arc); this is the exact same object shape
     `RKA_OT_centerline_from_vertex_group` builds by hand (`lanecl_*`, `rka_curve` prop group) —
     the computed-geometry counterpart of that hand-tagged one. **`kit_common.flat_ribbon`** (new)
     — a flat quad-strip mesh through exact points (same tangent-offset technique as the existing
     `swept_wall`, just horizontal), the visual driving surface under each generated centerline so
     a turn reads as an actual road, not a bare line. The curb corners themselves reuse the
     existing `swept_wall` directly (already exact-point, already vertical) — no new mesh code
     needed there. **Per user instruction: no `-colonly` collision proxy is generated per piece
     here** — collision for the whole road network is built as one pass over the complete road
     segment at export time, not per authored piece (unlike the wall/building kit pieces, which do
     carry their own `-colonly`).
   - **`RKA_OT_build_intersection`** (new, `ops_intersection.py`) — one click builds a full
     intersection (all curb corners + a `lanecl_*` centerline + a visual asphalt ribbon per legal
     lane movement) into a fresh collection at the 3D cursor; `preset` = `4WAY` / `3WAY_T` /
     `3WAY_Y`, plus `lane_width`, `lanes` (per direction), `kerb_radius`, `tail_length` (how far
     generated geometry reaches out from the corner to meet an approach lane tile), `segments`
     (arc smoothness), `curb_height`/`curb_thickness`. Purely additive — never edits `lane_kit.blend`
     or any existing piece; every run gets its own numbered collection, so presets/radii can be
     compared side by side. Wired into the panel's new "Intersection (prototype)" box (settings
     live on the operator's F9 redo panel, not duplicated into scene properties).
   - **Verified headless, end-to-end**, via `tools/build_intersection_prototype.py` (new,
     unrelated to item 5's still-pending straight/curb-tile generator below): built a 4WAY +
     3WAY_T + 3WAY_Y into `kit/intersection_prototype.blend`
     and re-opened it to check real numbers, not just object counts — all 4 corners of the 4-way
     are exactly congruent (rotational symmetry survived the Python→Blender conversion intact),
     and the T-junction's through-movement centerline is a genuinely straight line at constant
     lateral offset (verified: both endpoints at y=-2.5, only x differs by exactly 2×`tail_length`).
   - **What this does NOT yet do** (fair scope cuts for the prototype, not forgotten): asymmetric
     lane counts (in ≠ out per arm — `Arm` assumes symmetric `lanes`); real swept-path turn shapes
     for large vehicles (the diagram's "previous path too tight" vs "updated path" nuance — a
     single-radius circular fillet is a good first approximation, not a true clothoid/comfort
     curve); visual dashing of the centerline (it's a plain solid line — cosmetic only, easy to add
     later via a points-along-curve instancer); traffic-legality filtering of which turns are
     actually legal (every geometrically-possible movement is generated; L/R/S classification for
     `LaneGraph`/`IntersectionZone` consumption is Phase 4 work, not this prototype).

5. **Suggested next concrete step (not yet done):** a small `bmesh`-based procedural generator,
   e.g. `kit/build_parametric_kit.py`, that builds the item-1 straight tile (and the curb-side
   strip) to the exact spec above and writes it to a **new, separate file** for comparison —
   deliberately not touching the live `kit/lane_kit.blend`, since you're actively hand-authoring
   it right now and a from-scratch rebuild of that file would destroy your in-progress tagging
   work (the 8 tagged 4-way-intersection stubs, the new `Kit_Intersection3Way2Lane` piece). Once
   compared, the parametric version can replace the hand-modeled straight/curb tiles in
   `lane_kit.blend` deliberately (a controlled swap you approve), rather than a script silently
   overwriting the file.

6. **Multi-way, per-arm lanes, graph-ready export, and native Godot `Path3D` consumption —
   SHIPPED (2026-07-24).** Carries item 4's geometry into the game, per explicit user direction:
   build the data as infrastructure for a future cross-network GPS/routing layer (not the routing
   layer itself — vehicle-AI destination logic is out of scope, deferred), and consume it via a
   real native `Path3D`/`Curve3D` route type rather than reusing the `VehicleRoute` Marker3D
   pipeline.
   - `lib/intersection_kit.py`: `preset_nway(angles, lane_width, lanes)` (any arm count, any
     angles); every preset now accepts **per-arm** lane counts (a list parallel to the arm
     angles, `_per_arm` helper, length-validated) instead of one scalar for the whole junction —
     e.g. a 2-lane main street crossing a 1-lane side street. `turn_side(entry_dir, exit_dir)`
     classifies every movement `L`/`S`/`R` from the signed 2D cross product (a through-pair
     movement is always `S`). Every lane gets a globally-unique id
     (`<junction_id>_<from>_<to>_L<lane>`) and a stable per-(arm, lane) **port** — the world
     position + tangent of the far end of that lane's stub — via `build_ports`; a turn and a
     through movement sharing an (arm, lane) reach the *identical* port (verified exactly, not
     approximately, in `self_test()`), since physically they're the same lane before/after the
     junction splits it. `export_dict`/`export_json` (stdlib `json`, still no bpy) write this as
     one graph-shaped `.lanekit.json` sidecar per junction: `arms` (nodes), `lanes` (directed
     edges), `ports` (the seams a future cross-piece linker would connect to) — this is the
     concrete hook for `AUTHORING_GUIDE.md` §11's "always-resident manifest for cross-district A*"
     goal, without building the A* itself. 13 self-test sections now pass (up from 7): per-arm
     lanes + length-mismatch guard, `preset_nway` on an arbitrary 5-way, `turn_side`, cross-junction
     id uniqueness, exact port/lane round-trip, and a real JSON file round-trip.
   - `RKA_OT_build_intersection`: new `NWAY` preset (`arm_angles`, comma-separated degrees) and
     `lanes_arm1..4` per-arm overrides (0 = inherit the shared default) alongside the existing
     three presets; new `export_path` (blank = skip) writes the sidecar via `export_json` once
     geometry is built, using the run's own collection name as `junction_id`.
     `tools/build_intersection_prototype.py` now emits one sidecar per example junction
     alongside `kit/intersection_prototype.blend` — verified end-to-end: regenerated headless,
     spot-checked the JSON directly (ids unique, a turn movement's first point lands exactly on
     its port).
   - **`com.openworld.world.Lane`** (new interface) abstracts a directional lane over its concrete
     representation. **`com.openworld.world.PathLaneRoute`** (new, `implements Lane`) wraps a
     native `Path3D`/`Curve3D` — built at bake time from a sidecar's sampled points
     (`curve.addPoint`, no re-smoothing), with a one-time-cached windowed arc-length search
     (mirroring `VehicleRoute`'s own, since this route type is static/baked and never re-edited at
     runtime) implementing `total()`/`pointAtLength()`/`lengthAtNearest()` off `Curve3D`'s native
     baking. `VehicleRoute` now also `implements Lane` (four thin getters added —
     `getTurn/getApproach/getEndBehavior/getReturnRoute` — since an interface can't expose a public
     field directly; zero behavior change). `LaneGraph` and `VehicleAIController` are retyped from
     `VehicleRoute` to `Lane` throughout (`IntersectionZone` needed no change — verified it never
     referenced `VehicleRoute`). The payoff: `LaneGraph`'s existing endpoint-proximity junction
     derivation — unmodified — now treats *any* mix of `VehicleRoute` and `PathLaneRoute` in one
     scene as one connectivity graph, so a car already goes straight or turns correctly depending
     on whichever successor lane it lands on at a junction (the existing straightness-biased
     weighted pick in `VehicleAIController.advanceToNextRoute`), with **no new AI decision code** —
     exactly the scope the user asked for this pass.
   - `PathLaneRoute` does **not** register with `ZoneManager`'s route registry (spawn-config
     lookups) and its `pickNextRoute()`/`resolveRoute()` always return null — connectivity is
     entirely geometry-derived for this route type. Wiring it as an ambient-traffic zone spawn
     target is a natural but separate future step, not required for junction traversal.
   - **`WorldBaker`**: new opt-in `@Export lanekitPath` (blank = skip — auto-deriving it from a
     district's own filename, à la `resolveGeometryPath`'s sibling-`.scn` preference, is a natural
     follow-up once proven on more than the one prototype fixture). When set, parses the sidecar
     (`godot.api.JSON.parseString` — note the class is `JSON`, all-caps, not `Json`) and builds one
     `PathLaneRoute` per lane entry; a `kind == "turn"` movement's `turn` field is now the real
     `L`/`R` from `intersection_kit.py`'s `turn_side` (not guessed), and `through` maps to `S`.
   - **Verified**: `python3 lib/intersection_kit.py` (13/13), the Blender prototype regeneration,
     and `./gradlew build` all pass. A headless Godot smoke test (bake the prototype's sidecar,
     spawn a car directly onto a straight `PathLaneRoute` and one onto a turning `PathLaneRoute`,
     confirm `LaneGraph` carries each across the junction correctly) is the remaining verification
     step — see the session's task list.
   - **Asymmetric in/out lanes per arm — plan-only, NOT implemented, per explicit user
     instruction.** `Arm` would need `lanes_in`/`lanes_out` instead of one symmetric `lanes`;
     `half_width()` would split into a CCW-side (`lanes_out`) and CW-side (`lanes_in`) offset —
     `curb_edges()` already computes each side from a separate method call, so that part is
     contained. The real gap is `build_lane_movements`'s `n_lanes = min(a.lanes, b.lanes)`
     assumption that incoming lane index *i* always continues onto outgoing lane index *i*: once
     counts differ, *which* incoming lane feeds *which* outgoing lane is a genuine authoring
     decision (nearest-offset match? leftmost-continues-leftmost? explicit per-movement mapping?),
     not just arithmetic — that ambiguity, not the geometry math, is why this stays deferred.
     Real-world need is narrow (contraflow/channelized lanes); revisit when a specific district
     actually needs it.

7. **Explicit per-lane connection mapping — SHIPPED (2026-07-24), answers item 6's asymmetric-
   lanes "which incoming lane feeds which outgoing lane" question directly.** Rather than solve
   that ambiguity with an algorithm (nearest-offset, leftmost-continues-leftmost, ...),
   `build_lane_movements`/`export_dict`/`export_json` now accept an optional `lane_map`:
   `{(from_arm, to_arm): [(in_lane, out_lane), ...]}`, overriding the default lane-*i*-feeds-lane-
   *i* pairing for exactly the arm pairs it names (every other pair keeps the default). Every
   pair is validated against the arms' own lane counts — an out-of-range index raises, not
   silently drops. This is also generally useful beyond the asymmetric-count case (e.g. a
   deliberate lane shift/merge on equal-count arms). **Exposed in the Blender UI**, not just as a
   Python parameter: `RKA_OT_build_intersection` gained a `lane_map` string field (F9 redo panel
   or the panel button), mini-syntax `'From>To:in-out,in-out; From2>To2:in-out'` (e.g.
   `'N>E:0-1,1-0'` to swap two lanes), parsed by the new `ops_intersection.parse_lane_map` with a
   clear operator-error report on malformed syntax. A movement's exported `id` reflects a real
   swap (`..._L0to1` instead of `..._L0`) so it's visually distinguishable from a default-paired
   lane. Self-tested (14/14 total now): default pairing unchanged for un-named arm pairs,
   out-of-range validation, id-suffix on a swap.

8. **Sample Blender file with a live edit → Godot import loop — SHIPPED (2026-07-24).**
   `kit/intersection_prototype.blend` (already existed) is the editable sample: open it, use the
   "Intersection (prototype)" panel's "Build Intersection" (or its F9 redo panel) to change
   preset / per-arm lanes / `lane_map` / kerb radius / arm angles, same as before. New this round:
   the operator gained **`gltf_export_path`** (mirrors `export_path`) — set both it and
   `export_path` to matching locations on one run and you get a synchronized `{.glb, .lanekit.json}`
   pair for that exact junction, with **no separate manual export step**. `gltf_export_path`
   exports only the visual mesh objects (`curb_*` walls, `roadribbon_*` driving surfaces) — not
   the `lanecl_*` data curves, which carry no separate meaning once exported since the JSON
   sidecar is the data source of truth for Godot.
   - `tools/build_intersection_prototype.py` now also writes the 4-way's pair to
     `src/main/resources/com/openworld/world/pieces/District_intersectiondemo.glb` +
     `kit/intersection_prototype.4way.lanekit.json` on every regeneration (kept as the default,
     ready-to-bake fixture).
   - **New `tools/build_intersection_piece.sh`** — deliberately does **not** invoke Blender or
     regenerate the `.blend` (that would wipe manual edits, same reasoning as `build_piece.sh`'s
     stem form): it assumes the `.glb`/`.lanekit.json` pair is already on disk (either from the
     script above or a manual Blender export via the operator's two export fields), triggers a
     Godot import (`--headless --import`), and bakes via a throwaway `WorldBaker` host — same
     `bake_one()` idiom as `build_piece.sh`, minus the `xvfb`/non-headless requirement (no
     MultiMesh content here, so no RenderingServer transform-buffer dependency) — into
     `District_intersectiondemo.tscn`, then repoints **new `SoloIntersection.tscn`** (mirrors
     `SoloPiece.tscn`: Player + WorldSystems + DebugHarness) at it.
   - **The loop**: edit in the Blender GUI (F9 redo, tweak, done) → `tools/build_intersection_piece.sh`
     → open `SoloIntersection.tscn` in the Godot editor (or run it) to walk around and, via
     `DebugHarness`, spawn AI traffic and watch a car actually turn or go straight depending on
     which `PathLaneRoute` it lands on.
   - **Verified**: regenerated the demo, ran the bake script end-to-end (`WorldBaker: lanekit
     sidecar ... → 12 PathLaneRoute(s)`, `pathlanes=12` in the bake summary), and loaded
     `SoloIntersection.tscn` headlessly for 60 frames with no errors — visual walk-testing itself
     is for the user in the editor.
   - **Addon install caveat (bit the user immediately on first try):** `intersection_prototype.blend`
     is built by a *headless* script that registers the addon in-process — that registration is
     never saved into the `.blend` (Blender addon registration is a Python/runtime thing, not
     scene data). Opening the file in an ordinary interactive Blender session has no "Road Kit"
     panel and nothing for F9 to repeat unless the addon is ALSO installed the normal way:
     symlink `assets/world_source/addons/road_kit_authoring` into
     `~/.config/blender/<version>/scripts/addons/`, then enable it in Edit > Preferences > Add-ons
     (per-user, persists across files — a one-time step, not something a script should do).

9. **Straight two-way road segment — SHIPPED (2026-07-24), the piece missing between
   intersections.** Neither `build_curb_corners` (only ever draws the *rounded corners*, not the
   straight curb run along an arm's own sides) nor anything else generated a plain connecting
   stretch of road — every intersection's arms ended in dangling ports with nothing to actually
   connect to. New, in `lib/intersection_kit.py`: `build_straight_segment(p0, p1, lane_width,
   lanes, segment_id)` — a plain 2-point road (curb left/right + N lanes each direction, offset
   outward from centerline exactly like an intersection arm's own lane offsets) — and
   `export_segment_json`, emitting the **exact same lane JSON shape** `build_lane_movements`
   already does (`id`/`points`/`loop`/`turn`/`kind`) — deliberately, so `WorldBaker`'s sidecar
   loader consumes it with **zero Java changes** (verified: baked a segment's own sidecar alone,
   `pathlanes=2` for a 1-lane-each-way segment). This is also the template any future custom piece
   (a bridge/ramp mesh, hand-modeled) should follow to plug into the same lane graph — the
   contract is just "emit a `lanes` array in this shape," not "be built by this generator."
   New **`RKA_OT_build_straight_segment`** (`ops_segment.py`): 3D-cursor start point,
   `direction_deg` + `length`, same `lane_width`/`lanes`/curb/export fields as the intersection
   operator, wired into the panel's new "Straight Segment" box. **Connecting pieces needs no
   explicit stitching step in Blender at all** — position a segment's start/end near an existing
   piece's port (printed by `build_ports`, or just eyeballed) and `LaneGraph`'s existing
   endpoint-proximity clustering links them automatically at Godot bake/runtime, exactly like it
   already does for a hand-authored `VehicleRoute` network. Verified numerically: a segment
   positioned at an intersection's printed port lands ~2.5 m off exactly (half a lane-width — a
   port is the *lane's* position, offset from the road centerline the cursor should target;
   positioning at the centerline instead lands exactly on it) — comfortably inside
   `LaneGraph.JUNCTION_RADIUS` (4.5 m), which exists precisely to tolerate this much authoring
   slop per its own docstring. 16/16 self-tests now (up from 14): curb/lane offset geometry,
   JSON shape + file round-trip.

10. **Custom Blender data fields instead of string mini-syntax — SHIPPED (2026-07-24), per user
    request.** The `lane_map` operator field's `'From>To:in-out,in-out'` mini-syntax was flagged
    as unnecessarily "complex" to hand-write — new `custom_props.py` gives a native-Blender
    alternative that both operators now use:
    - **After every build**, the operator's fully-resolved settings — preset/angles/lane counts,
      and `lane_map` as a **plain nested dict/list** (`{'N>E': [[0,1],[1,0]]}`), not a string — are
      written onto the created Collection as ordinary custom properties (`coll["rka_..."] = ...`,
      Blender's native ID-property system, which already stores nested dict/list structures
      directly via the Python API — no encoding needed). This is a permanent, native-UI-visible
      record of exactly how a piece was built, viewable/editable via Object/Collection Properties >
      Custom Properties even without the addon's redo panel — which is lost the moment the file is
      closed and reopened (F9 only replays within the same undo history).
    - **Before building**, `RKA_OT_build_intersection` checks the *active* collection for an
      `rka_lane_map` custom property first — if present, it wins over the operator's string field
      entirely, so a `lane_map` can be hand-authored/edited as native nested data in Blender's own
      UI (or the Python console: `coll["rka_lane_map"] = {"N>E": [[0, 1]]}`) with no string DSL
      involved. The string field remains for quick one-off/scripted entry
      (`tools/build_intersection_prototype.py` still uses it) — both paths converge on the exact
      same `{(from,to): [(in,out),...]}` shape `build_lane_movements` expects.
    - **Object names shortened to match, once the collection carries the structured record.**
      Every curb/curve/ribbon name used to repeat the full collection name (e.g.
      `curb_Intersection_4WAY_001_N_E`) purely so a human scanning a flat list could tell pieces
      apart — pure redundancy now (and even before this, since Blender's Outliner already nests
      objects under their collection). Confirmed via `WorldBaker`'s prefix table
      (`lane_`/`zone_`/`spawn_`/`water_`/`intersection_`/`instance_`/`mmesh_`) that `curb_`/
      `lanecl_`/`roadribbon_` were never pattern-matched by anything downstream — every Godot-side
      lookup goes through the exported JSON's own `id`, which correctly stays fully qualified
      (must be globally unique across a multi-junction scene) and was NOT shortened. Now:
      `curb_NE`, `lanecl_NE_L0` (or `_L0to1` on a `lane_map` swap, mirroring the JSON id
      suffix), `ribbon_NE_L0` for intersections; `curb_L`/`curb_R`, `lanecl_AB_L0`/`lanecl_BA_L0`
      for segments. Purely a Blender-authoring-convenience rename — verified the full
      export/bake/`PathLaneRouteTestHost` smoke test (`verdict=PASS`) still passes unchanged,
      since none of it depends on these names.

11. **Selectable curb style (BOX / GUTTER) — SHIPPED (2026-07-24), per user request.** Every
    curb generated so far was a plain flat wall (`swept_wall`). The user pointed at a REAL
    hand-modeled piece already in `kit/lane_kit.blend`,
    `kit_side_straight_city_gutter_curb_w0p6m_l5m` (`Kit_CurbSideCityGutter`), and asked for
    something like it. Inspected it read-only (never modified, per the standing constraint) —
    ~0.6 m wide, ~0.2 m tall, a flush road-facing apron stepping up to a flat-topped curb face,
    not a simple box. Literally extracting that mesh's exact topology and bending it along an
    arbitrary-length swept curve isn't a good fit (it's a fixed 5 m straight tile); the user
    explicitly said to simplify to just width/height instead of chasing an exact match.
    - New in `kit_common.py`: **`swept_profile(name, pts, profile_2d, coll, matkey)`** —
      generalizes `swept_wall`'s exact tangent/right-normal-offset technique (no NURBS
      approximation) from a fixed rectangle to an *arbitrary* 2D cross-section (an ordered list of
      `(lateral_offset, height)` pairs) swept along a polyline. **`gutter_curb_profile(width,
      height)`** — a 4-point profile (flush road edge → flush apron edge → curb base → curb top)
      matching the real piece's silhouette at just its width/height, not its literal topology.
    - New `curb_style` enum (`'BOX'` default / `'GUTTER'`) on **both** `RKA_OT_build_intersection`
      and `RKA_OT_build_straight_segment`, dispatched through one shared `build_curb()` helper
      (`ops_intersection.py`, imported by `ops_segment.py`) so the two operators can't drift.
      `curb_thickness` doubles as "gutter width" in GUTTER style (its description says so).
      Persisted via the existing custom-property mechanism (item 10) alongside everything else.
    - **Verified**: built a 4-way + a segment with `curb_style='GUTTER', curb_thickness=0.6,
      curb_height=0.2` headlessly — correct vertex/face counts for the swept profile (a 9-point
      corner arc × 4-point profile → 36 verts/24 faces; a straight 2-point segment side → 8
      verts/3 faces), `rka_curb_style` custom property round-trips. Default is unchanged (`BOX`),
      so the existing demo/bake/smoke-test pipeline needed no other changes and still passes as-is.

12. **Extend/insert/curve network-editing tools — SHIPPED (2026-07-24).** Answers "how do I grow
    a network from here, splice a junction into an existing road, and bend a segment slightly" —
    the angle-tweaking half of the same question was already covered (NWAY's `arm_angles`,
    T/Y's `side_angle`); the missing piece was a global **`rotation_deg`** on
    `RKA_OT_build_intersection` (added to every arm's angle after the preset is built, so
    ANY preset can be aligned to an existing road's direction — not just angle-tweaked in
    isolation) plus the three tools below.
    - **`custom_props.read_arms(coll)`/`read_origin(coll)`** (new) reconstruct an intersection's
      `[(name, angle_deg, lanes), ...]` and raw build-time cursor position from its own
      `rka_arm_names`/`rka_arm_angles`/`rka_arm_lanes`/`rka_origin` custom properties (item 10) —
      `RKA_OT_build_intersection` now also writes `rka_origin`. This is what lets a *later* tool
      reconstruct a piece's exact geometry without re-deriving or guessing it.
    - **`RKA_OT_extend_from_arm`** (new, `ops_segment.py`): activate an intersection's collection,
      pick an arm by name, get a length — builds a new segment starting EXACTLY at that arm's
      stub end, in its exact direction. Because both the intersection's own lane ports and a new
      segment's lane offsets are built the identical "centerline + symmetric `(i+0.5)×lane_width`
      offset" way, this lands at **exact (0 m) alignment**, not just within `LaneGraph`'s 4.5 m
      proximity tolerance — verified numerically (distance = 0.0).
    - **Curved segments**: `build_straight_segment`/`export_segment_json` gained `bend`
      (meters, default 0.0 = byte-identical to the old dead-straight behavior — verified no
      regression) and `segments`. A nonzero `bend` samples a quadratic bezier (control point
      offset from the midpoint) into a polyline, and every lane/curb line is now offset from a
      **local per-point tangent** (not one constant direction) — so a curved segment's lanes and
      curbs genuinely follow the bend, still via the same exact-point sweep technique used
      everywhere else (`swept_wall`/`swept_profile`/`flat_ribbon` already accepted arbitrary-length
      polylines; only the ONE `ops_segment.py` call site that destructured curbs as exactly 2
      points needed fixing). Self-tested: endpoints stay anchored at p0/p1 exactly regardless of
      bend, the midpoint genuinely bulges (not a no-op), arc length exceeds the chord, and a
      bigger `bend` curves more.
    - **`RKA_OT_insert_intersection_on_segment`** (new, `ops_segment.py`, **auto-replace — user
      explicitly confirmed this over a non-destructive report-only alternative**): activate a
      segment's collection, pick a split fraction (+ preset/side angle/optional side-arm length)
      — deletes that segment's objects and collection, builds a new intersection at the split
      point rotated to match the original segment's direction (`rotation_deg`), then calls
      `RKA_OT_extend_from_arm` twice (finding the forward/backward arms by closest angle, not by
      hardcoded name, so this works for any preset) to rebuild the two shorter segments on
      either side — reaching the *exact* original endpoints. Only ever deletes addon-generated
      segment collections, never anything hand-authored. Verified: split a 40 m segment at
      fraction 0.5 → the two rebuilt segments' endpoints are exactly `{0.0, 40.0}` (the original
      p0/p1), spanning the new intersection's footprint in between.
    - Wired into the panel's new "Extend / Insert" box, which shows the right button
      (`Extend From Arm` / `Insert Intersection On Segment`) based on what's active, or a hint to
      activate a piece first.
    - **Verified**: `python3 lib/intersection_kit.py` 17/17; both new operators exercised
      headlessly (exact-alignment extend, correct-span insert); full demo regen + bake +
      `PathLaneRouteTestHost` smoke test (`verdict=PASS`) still pass unchanged, since none of
      this touches the default-behavior path.

13. **Workflow bug fixes — arm markers, cursor auto-advance, F9/curb-toggle, slope, one-mesh
    export — SHIPPED (2026-07-24).** Item 12 built extend/insert/curve, but real use surfaced
    four sharp edges: no visible "arm" to click when extending, new pieces landing at the world
    origin instead of continuing the road, F9 (Adjust Last Operation) silently doing nothing after
    Extend/Insert, and no way to combine or slope a piece.
    - **Root cause of "F9 doesn't work" / "curb toggle doesn't work": nested `bpy.ops.rka.X(...)`
      calls.** `RKA_OT_extend_from_arm` used to build its segment by calling
      `bpy.ops.rka.build_straight_segment(...)` from inside its own `execute()`, and
      `RKA_OT_insert_intersection_on_segment` called `build_intersection`/`extend_from_arm` the
      same way. Each nested `bpy.ops.rka.X()` call pushes its OWN separate undo step, so Blender's
      F9 panel ends up showing the *innermost* operator's properties — not the outer one you
      actually meant to tweak (arm name, split fraction, curb style, bend...). **Fix:** the build
      logic behind every operator was extracted into plain functions with no `bpy.ops` dispatch —
      `build_intersection_geometry()` (`ops_intersection.py`) and `build_segment_geometry()`
      (`ops_segment.py`) — and every operator (`RKA_OT_build_intersection`,
      `RKA_OT_build_straight_segment`, `RKA_OT_extend_from_arm`,
      `RKA_OT_insert_intersection_on_segment`) is now a thin wrapper that calls these directly.
      Insert is now genuinely ONE flat operator/one undo step with its own complete F9 panel
      (fraction/preset/curb style/join-mesh all live and re-apply correctly on redo).
    - **Arm marker Empties ("place arm at end of each intersection")**: `build_intersection_geometry`
      now creates one `arm_<name>` Empty (`SINGLE_ARROW`, oriented along the arm) at each arm's
      tail end, carrying `rka_arm_name`/`rka_arm_angle`/`rka_arm_lanes` custom properties — a
      visible, clickable handle. `RKA_OT_extend_from_arm`'s poll/execute
      (`_resolve_intersection_and_arm`) now accept EITHER the intersection's collection active in
      the Outliner (original workflow, type the arm name) OR one of its `arm_*` Empties as the
      active object (new — click the arm, leave "Arm" blank, it self-fills). Verified headlessly:
      4-way → 4 arm Empties at the correct tail positions; extending from a clicked Empty lands
      exactly on that arm's port.
    - **Cursor auto-advance ("new segment created at origin instead of continuing")**: this was
      real — `RKA_OT_build_straight_segment` never moved the cursor, so repeatedly pressing it
      (the natural "keep going" workflow, as opposed to Extend From Arm) rebuilt at the same spot
      every time, landing at the world origin if the cursor had never been moved. New
      `auto_advance_cursor` prop (default **on**) moves the 3D cursor to the segment's end point
      (XY **and** elevation) after building, so the next press continues the road. Verified: a
      sloped segment (`elevation_delta=3`) starting at z=5 leaves the cursor at z=8; a second
      segment built right after starts exactly there, not back at the original point.
      `RKA_OT_extend_from_arm` never touches the cursor at all (arm data alone fixes its start
      point/direction, so there's no ambiguity to resolve).
    - **Slope ("upload slope, or slightly curve")**: `intersection_kit.build_straight_segment`/
      `export_segment_json` gained `z0`/`z1` (a relative elevation at p0/p1 — constant grade when
      they differ) and `bend_z` (a vertical crest/dip bump at the midpoint, the same
      `4·t·(1-t)` parabola shape used nowhere else in this module — simple and predictable,
      independent of the lateral `bend`). Every returned point is now a 3-tuple `(x,y,z)`; z is
      0.0 throughout when the new params are left at their defaults, so this is a byte-identical
      no-op for every existing caller. One real bug caught by self-test: when `bend == 0` the
      spine was always exactly 2 points, so `bend_z` alone had no midpoint sample to land on —
      fixed by also subdividing the spine (via a bezier whose control point IS the midpoint, which
      degenerates to an exact linear subdivision) whenever `bend_z != 0`, not just when `bend !=
      0`. Exposed on `RKA_OT_build_straight_segment`/`RKA_OT_extend_from_arm` as `elevation_delta`
      / `bend_z`. Self-tested (18/18): flat case unaffected, linear grade hits the right z at the
      midpoint, `bend_z` bumps the midpoint and stays zero at both ends, curbs slope too.
    - **One mesh ("let the intersection mesh be one mesh, or one mesh at export")**: new
      `join_meshes()` helper (`ops_intersection.py`) + `join_visual_mesh` operator prop (default
      off, so existing per-piece-object behavior is unchanged unless asked for) on all four
      operators — selects every curb/ribbon mesh object just built, `bpy.ops.object.join()`s them
      into one, renamed `mesh_<collection name>`. Verified headlessly for both an intersection
      (336 verts, 1 object) and a segment.
    - **What's still NOT built** (scope explicitly deferred, not a bug): true click-and-drag live
      geometry editing ("bevel-style" handles) would need the intersection/segment rebuilt as a
      Geometry Nodes group driven by the arm Empties' transforms — a different architecture from
      today's "Python computes points, `kit_common` sweeps a mesh" pipeline. The arm Empties +
      now-working F9 redo panel are today's practical adjustment path (nudge a number, re-run);
      revisit Geometry Nodes only if that friction turns out to matter in practice.
    - **Verified**: `python3 lib/intersection_kit.py` 18/18; headless demo regen (arm-Empty counts
      match expected: 4-way 32 objects = 4 curb + 12 lanecl + 12 ribbon + 4 arm, T 17, Y 18);
      a dedicated headless script exercising arm-Empty click-to-extend, cursor auto-advance
      (with elevation), and the flattened insert-on-segment op all pass; `join_visual_mesh`
      verified on both an intersection and a segment.

14. **Live click-and-drag editing (handler-driven, NOT a Geometry Nodes rewrite) — SHIPPED
    (2026-07-25).** Item 13 said true drag-to-adjust "bevel-style" handles would need a Geometry
    Nodes rewrite; asked directly, the answer is a **deliberate no** on rewriting the pipeline in
    GN, for a concrete reason: GN can't write the `.lanekit.json` sidecar Godot/WorldBaker actually
    consume, so the tested Python geometry in `lib/intersection_kit.py` would still have to run for
    export regardless — a GN graph would be a SECOND, parallel implementation of the same
    corner-fillet/lane math (through-pair detection, variable arm count, curb-style dispatch),
    with real risk of silently drifting from what actually ships to Godot. Instead: a
    `depsgraph_update_post` handler detects a moved marker Empty and reruns the exact same,
    already-tested functions that back the fresh-build operators — one source of truth, genuinely
    live in the viewport.
    - **`live_edit.py`** (new module, registered as an addon submodule): the handler
      (`_on_depsgraph_update`) scans `depsgraph.updates` for transform changes on an Empty
      carrying `rka_arm_name`/`rka_segend`/`rka_segbend`, and calls
      `ops_intersection.rebuild_intersection_in_place` / `ops_segment.rebuild_segment_in_place` on
      its owning collection. A `_rebuilding` re-entrancy guard plus epsilon-gated marker
      repositioning (only write `.location` back if it actually moved >0.1mm) keeps a rebuild's
      own corrective snap from re-triggering itself indefinitely — verified to settle after at
      most one extra harmless pass, never oscillates.
    - **`rebuild_intersection_in_place(context, coll)`** (`ops_intersection.py`): re-derives each
      arm's ANGLE from its `arm_*` Empty's current bearing off the stored `rka_origin`, deletes the
      old curb/lane objects (`clear_generated_mesh_objects`, keeps every marker Empty), rebuilds via
      a newly-factored `_populate_intersection_mesh` (the same corner/movement/curb/ribbon loop
      `build_intersection_geometry` uses for a fresh build — one function, two callers, so the two
      paths can't drift), then re-snaps each arm back onto the fixed `tail_length` radius (dragging
      an arm ROTATES it around the junction — reshaping the intersection — rather than also
      silently changing tail length, which stays one shared scalar, not per-arm).
    - **`rebuild_segment_in_place(context, coll)`** (`ops_segment.py`): a fresh build now also
      places `segend_A`/`segend_B` (the two endpoints) and `segbend` (the current lateral-`bend` +
      vertical-`bend_z` control point) as marker Empties. Dragging `segend_A`/`segend_B` re-derives
      p0/p1 (and elevation, from their world Z) directly; dragging `segbend` projects its position
      onto the chord's perpendicular+vertical plane to re-derive `bend`/`bend_z` (any along-the-chord
      drag component is ignored, then the marker is re-snapped onto that plane) — so one handle
      each for length/direction, elevation, lateral curve, and vertical crest/dip.
    - **"Build Intersection always uses the cursor, not wherever I just selected" — fixed.**
      `active_marker_position(context)` (`ops_intersection.py`): if the active object is any of
      this addon's marker Empties, both `RKA_OT_build_intersection` and
      `RKA_OT_build_straight_segment` now build starting exactly THERE (and parent the new piece
      as a sibling of the marker's own piece via `parent_collection_of`) instead of at the 3D
      cursor — cursor stays the fallback when no marker is active. Answers "should add arm for
      each segment end?" too: segments now carry the same kind of marker Empties intersections do
      (`segend_A`/`segend_B`), so selecting a segment's end and pressing "Build Intersection"
      lands the new junction there.
    - **`RKA_OT_rebuild_from_handles`** (new, manual fallback): re-runs the same in-place rebuild
      on demand. Exists because a `depsgraph_update_post` handler's dispatch during an actual
      interactive drag could only be partially verified headlessly (see below) — this button is
      the "definitely works regardless" escape hatch, and doubles as the doc-recommended fix if
      "Live Edit From Handles" (new scene toggle, `RKA_SceneSettings.live_edit_enabled`) is off.
    - **What's still NOT built**: per-arm radius (dragging an arm only rotates it, not its
      distance — the data model has one shared `tail_length`, not per-arm; a real ask, revisit if
      it matters in practice), and a `kerb_radius`/`lane_width`/curb-style live handle (still
      F9/custom-property/typed, not drag-adjustable).
    - **Verification & an honest gap**: `python3 lib/intersection_kit.py` 18/18 unchanged; a
      dedicated headless script simulates drags by setting a marker's `.location` and calling
      `rebuild_*_in_place` directly (proves the rebuild math end to end — angle/length/bend/
      elevation all correctly re-derived, arm re-snapped to the fixed radius, repeated rebuilds
      with no further movement are stable/non-oscillating); active-marker-snap verified (`Build
      Intersection` with a `segend_B` marker active lands on it, not a deliberately-different
      cursor position); `join_visual_mesh`/full demo regen still pass unchanged. **Not verified
      headlessly**: whether `depsgraph_update_post` actually fires during a real interactive
      viewport drag — Blender's `--background` scripted mode does not dispatch this handler the
      same way an interactive session does (confirmed: `obj.location = ...` + `view_layer.update()`
      in a background script does NOT fire it, even with `update_tag()`), so this specific claim
      rests on the handler's documented contract (the same mechanism widely used by rigging/
      constraint addons for live updates) rather than a headless proof. `RKA_OT_rebuild_from_handles`
      exists specifically to de-risk this gap.

15. **Live lane-count and arm-count controls — SHIPPED (2026-07-25).** Item 14's drag handler only
    watches marker TRANSFORM changes; a lane/arm COUNT is an integer, not something you drag, and
    hand-editing the `rka_arm_lanes`/`rka_lanes` custom property doesn't trigger the handler at all
    (no transform changed) — so those were still effectively build-time-only. Four new operators,
    all one-click and immediately live (call the item-14 in-place rebuild themselves, no drag or F9
    needed), wired into the panel's "Live Edit" box next to whatever's active:
    - **`RKA_OT_adjust_arm_lanes`** (+/-, active object = an `arm_*` marker): bumps that arm's own
      `rka_arm_lanes` (clamped 1-3) and rebuilds.
    - **`RKA_OT_add_arm`** / **`RKA_OT_remove_arm`**: add places a new `arm_*` Empty at the WIDEST
      angular gap between the intersection's current arms (`_widest_gap_angle`, wrapping-aware) so
      it can't land on top of an existing one, named with the next free letter
      (`_next_arm_name`); remove deletes the active arm marker. Neither needed any change to
      `rebuild_intersection_in_place` itself — it already reads whatever `arm_*` Empties exist in
      the collection with no preset/arm-count hardcoded anywhere, so a 4-way can become a 5-way (or
      a hand-built 7-way back down to a 6-way) with no new geometry code. Remove refuses below 3
      arms (a 2-arm "intersection" is just a through street).
    - **`RKA_OT_adjust_segment_lanes`** (+/-, active = a segment or one of its markers): same
      pattern for a segment's `rka_lanes` (both directions, since segments don't yet have a
      forward/backward split — see the open "direction of lane" question below).
    - **Verified headlessly**: arm lanes bump/clamp and the stored `rka_arm_lanes` custom property
      updates; Add Arm on a 4-way produces a 5th arm at the expected 45° gap-midpoint (all 4 gaps
      were 90°) and one more curb corner; Remove Arm undoes it back to a clean 4-way, then correctly
      refuses a further removal at 3 arms; segment lane bump produces the expected +2 ribbon objects
      (one new lane × two directions).

16. **Open question, deliberately not guessed at: "direction of lane" + "slope looks like a fixed
    piece."** Two asks in the same request whose concrete implementation depends on which of
    several plausible readings is meant — flagged rather than built to avoid wasted work:
    - *Direction of lane* could mean (a) asymmetric lane counts per direction on a segment (e.g. 2
      lanes forward + 1 back) — the exact gap already named as deferred in item 4's "asymmetric
      in/out lanes" design note; (b) a one-way toggle (build only the forward lanes); or (c) which
      physical lane index feeds which on an intersection turn — already solvable today via
      `lane_map` (item on `RKA_OT_build_intersection`), just not obviously discoverable.
    - *Slope "seems like a fixed piece, not an array/follow-curve"* — checked `kit_common.py`'s
      `swept_wall`/`flat_ribbon`/`swept_profile`: all three already sweep along the EXACT input
      3D polyline (each point keeps its own Z; the tangent computed from XY only affects the
      horizontal offset direction, not height) — no flattening bug found there. Most likely reading
      is workflow, not geometry: a straight (unbent) sloped segment is genuinely just 2 points
      tilted as a flat ramp (correct for a straight line — nothing to "follow" yet), which may read
      as "fixed" if a continuously-varying grade (not just a straight tilt) or a curve-object-driven
      path was expected instead of the `bend`/`bend_z` scalar controls item 13 shipped.
    - **Resolved (user chose, 2026-07-25): asymmetric lane counts for "direction of lane" (item
      17), author-a-real-Curve for "slope" (item 18).**

17. **Asymmetric / one-way lanes, for both intersections and segments — SHIPPED (2026-07-25).**
    Directly answers a follow-up requirement ("one road can have only one lane, one way — ensure
    both intersection and segment can accommodate it"), building on the "asymmetric lane counts"
    choice above.
    - **`Arm.oneway`** (`intersection_kit.py`, None default): `'IN'` = this arm only ever
      RECEIVES traffic (0 outgoing lanes — e.g. a one-way street feeding INTO the junction);
      `'OUT'` = only ever SENDS (0 incoming lanes — e.g. a one-way exit). `lanes_in_count()`/
      `lanes_out_count()` gate `build_lane_movements`'s default pairing (now
      `min(a.lanes_in_count(), b.lanes_out_count())`, not `min(a.lanes, b.lanes)`) and
      `build_ports`'s two loops — an OUT-only arm can never be a movement's `from`, an IN-only arm
      can never be a `to`. `half_width()`/curb geometry are UNCHANGED (a one-way arm is still the
      same physical width). Self-tested (test 19): a 4-way with S='IN'/W='OUT' produces zero
      `to=='S'`/`from=='W'` movements, the reverse directions are unaffected, curb corner count
      (4) is untouched, ports only exist for the direction each arm actually carries.
    - **`RKA_OT_set_arm_oneway`** (`ops_intersection.py`, live toggle: Both/In Only/Out Only) on
      the active `arm_*` marker — writes `rka_arm_oneway`, immediately rebuilds. Combine with 1
      lane (`RKA_OT_adjust_arm_lanes`) for a true single-lane one-way arm. `build_intersection_geometry`
      /`RKA_OT_add_arm` write `rka_arm_oneway = ""` (both-ways) on every newly created arm marker
      so the key is always present; `rebuild_intersection_in_place` reads it per arm each pass.
    - **`intersection_kit.build_segment_from_spine`** (new core, see item 18) takes `lanes`
      (forward) and `lanes_backward` (reverse, defaults to `lanes` if `None` — symmetric,
      unchanged); either may be 0 (curb width uses `max(lanes, lanes_backward)`, so the road is
      still full-width on its active side), but BOTH being 0 raises `ValueError` — a road needs a
      lane somewhere. `build_straight_segment` gained the same `lanes_backward` param, delegating
      to the new core (self-test 20: `lanes=1, lanes_backward=0` → exactly 1 lane, curb matches
      the 1-lane width not the old 2-lane width, `lanes=0 & lanes_backward=0` raises, and calling
      the spine path directly reproduces `build_straight_segment`'s result exactly).
    - **`RKA_OT_build_straight_segment`/`RKA_OT_build_segment_from_curve`**: `Lanes Forward`/
      `Lanes Backward` operator props (both default 1, explicit 0 allowed — no magic sentinel).
      **`RKA_OT_extend_from_arm`**: automatically matches the source arm's `oneway` (0 lanes on
      whichever side the arm can't carry) — no manual lanes_backward prop needed there, since the
      arm itself is the source of truth. **`RKA_OT_adjust_segment_lanes`** gained a `backward`
      bool (targets `rka_lanes` vs `rka_lanes_backward` independently) and refuses to drop both to
      0. **Known gap**: `RKA_OT_insert_intersection_on_segment` still treats the split segment's
      lanes as symmetric (reads only `rka_lanes`) — splicing an asymmetric/one-way segment doesn't
      yet propagate that asymmetry to the rebuilt pieces or the new intersection's arms.
    - **Verified headlessly**: a one-way single-lane segment builds exactly 1 ribbon object (not
      2); requesting 0 lanes both directions is refused with a clear error; an intersection arm
      set to `In Only` produces zero movements terminating there (checked directly against
      `build_lane_movements`, not by parsing object names) while the reverse direction is
      unaffected.

18. **Curve-object-driven segment paths — SHIPPED (2026-07-25).** The user's explicit choice for
    "slope looks like a fixed piece": author the road's path as a real Blender Curve object (any
    spline type, Edit Mode, as many control points as wanted) instead of the `bend`/`bend_z`
    scalar model — a genuine multi-point curve/slope, not just one bump.
    - **`intersection_kit.segment_spine_3d`/`build_segment_from_spine`** (new): the p0/p1/bend
      spine-computation and the curb/lane offset logic were split into two functions —
      `build_straight_segment` is now a thin wrapper (`segment_spine_3d` then
      `build_segment_from_spine`), and a caller with its OWN already-resolved 3D spine (a sampled
      curve) calls `build_segment_from_spine` directly. Both paths share one geometry
      implementation — no drift between "straight+bend" segments and "curve-driven" ones.
    - **`_sample_curve_world_points`** (`ops_segment.py`): evaluates a Curve object through the
      depsgraph (`evaluated_get().to_mesh()`, respecting Bezier handles/resolution/modifiers, no
      `bpy.ops`/temp objects) and returns its points as world-space `(x,y,z)` tuples in spline
      order — the standard technique, same vertex order as Blender's own Convert To Mesh for an
      un-beveled curve.
    - **`RKA_OT_build_segment_from_curve`**: select a Curve, run it — builds curb/lane geometry
      following the curve's exact evaluated points, plus a `segcurve_driver` marker Empty
      recording which curve to re-sample on rebuild. **`rebuild_segment_from_curve_in_place`**:
      the live-edit counterpart — re-samples the curve's current points and rebuilds. **`export_
      segment_from_spine_json`** (new): same shape/WorldBaker-compatible sidecar as
      `export_segment_json`, but for an already-ABSOLUTE-Z spine (no separate world-height base
      to add, since curve points already carry real elevation) — self-tested (test 21) for correct
      Godot axis conversion.
    - **`live_edit.py`** extended: the `depsgraph_update_post` handler now also watches Curve
      objects (`is_updated_geometry` for Edit-Mode point drags, `is_updated_transform` for moving
      the whole curve, plus a fallback for Blender versions that report the Curve DATA id instead
      of the Object during Edit-Mode edits) and, via a reverse scan of collections' `rka_curve_object`
      property, rebuilds any segment driven by that curve. `RKA_OT_rebuild_from_handles` and
      `_live_edit_target_collection` (`ops_intersection.py`) also recognize a curve-driven
      segment's `segcurve_driver` marker OR the driving curve object itself as valid "active"
      targets, so the manual fallback works there too.
    - **Verified headlessly**: a 4-point POLY curve produces a segment whose lane centerline
      starts/ends at the curve's actual points (offset by exactly one lane-width, since the curve
      is the ROAD centerline and the lane sits beside it — confirmed this is the expected geometry,
      not a bug); dragging a curve control point + calling `rebuild_segment_from_curve_in_place`
      reshapes the road to match; `RKA_OT_rebuild_from_handles` works with either the curve-segment's
      driver marker OR the curve object itself as the active object.

## On JSON sidecar vs. custom-properties/glTF-extras (design note, not implemented)

Asked whether the Godot-side data interchange (`WorldBaker`'s `lanekitPath` JSON sidecar loader,
already shipped and tested per Kit geometry v2 item 6) should instead read the lane/port data from
Blender custom properties carried through as glTF "extras," eliminating the separate `.lanekit.json`
file. Recommendation: **keep the JSON sidecar as-is; don't build the extras-based path** unless a
concrete need for it shows up. Reasoning:
- The JSON loader is ALREADY WORKING and tested (`PathLaneRouteTestHost`, `verdict=PASS`) — this
  would be a rewrite of a proven Java pipeline for a "fewer files to manage" benefit, not a
  correctness or missing-feature fix.
- glTF "extras" genuinely CAN carry this (custom properties export fine per-node, and a glTF
  extras dict is arbitrary JSON-serializable data — no different in kind from the sidecar), so
  it's not blocked by the earlier-documented "one extras Dictionary" gotcha (that's about a single
  node's several custom properties landing in ONE Godot meta key, not a cross-object collision) —
  but it WOULD require re-deriving `WorldBaker`'s loader to read per-node extras out of the
  imported scene instead of `godot.api.JSON.parseString`-ing a file, a real Java change to
  something that isn't broken.
- The lanecl_* Curve objects the Blender addon already bakes into the .glb are currently INERT —
  WorldBaker's loader ignores the .glb's own geometry entirely and reads the separate JSON instead
  (a genuine duplication, noted here for visibility, not urgency). If this ever gets revisited,
  the more valuable fix is probably making `WorldBaker` consume the lanecl_* curves it already
  imports directly (no sidecar OR extras needed) rather than adding a third representation.
## Phase 2 — Centerline authoring + curb attach

> **Design change (2026-07-23): extracted from tagged mesh topology, not guessed.** The original
> P2.1 (`RKA_OT_add_centerline` = bbox-midline guess, then hand-reshape) is replaced: the artist
> hand-tags each lane's true centerline as a chain of vertices (following existing mesh edges) in
> a `lanedata` vertex group on the mesh — already done in `lane_kit.blend` for both straight
> pieces and (as of this session) 8 of the 4-way intersection's approach stubs. Several *disjoint*
> lane paths can share one `lanedata` group on one mesh (e.g. every movement through an
> intersection) — edge-connectivity alone separates them into distinct lanes, no per-lane group
> needed. `kit_common.centerlines_from_vertex_group(obj, "lanedata")` (new, `lib/kit_common.py`)
> does the extraction: builds the subgraph of mesh edges whose both endpoints are tagged, splits
> it into connected components (one lane each), walks each component from an endpoint (or an
> arbitrary start if it's a closed loop) to produce an ordered point list; a vertex with >2 tagged
> neighbours raises (a lane must be a simple path or loop, never a branch); an isolated tagged
> vertex with no edge partner is skipped with a warning, not an error (lets tagging happen
> incrementally). Verified against the live `lane_kit.blend` (read-only, no save): 2/2 correct on
> both straight pieces, and correctly split the 4-way intersection's 16 tagged verts into 8
> separate 2-point lanes.
> Each extracted curve is inherently **one direction** (it's the mesh's own physically-separate
> drivable strip, not a shared bidirectional line to split later) — see the "One centerline per
> direction, no left/right split" note below for why no separate left/right-lane concept was
> added on top of this.
- [x] P2.1 (superseded — see design-change note above) `ops_centerline.py`:
      `RKA_OT_centerline_from_vertex_group` (active mesh's `lanedata` group → one `lanecl_*` Poly
      curve per edge-connected tagged region, linked into the same collection as the source mesh
      so it travels with every instance). Verified headless: register → run on
      `kit_intersection_4_way_2_lane_straight_2_lane_side` → 8 curves created, 2 points each,
      `loop=False`, matching the source tagging exactly.
- [ ] P2.2 `RKA_OT_add_curb` (snap-attaches a `kit_side_*` curb Collection instance flush to the
      selected lane tile's left/right edge, offset from its bounding box).
- [ ] P2.3 Finish tagging `lanedata` for the remaining pieces/paths in `lane_kit.blend`
      (in progress by hand — 3-way pieces not started yet; 4-way currently has 8 short approach
      stubs, not yet full through-intersection + turn paths) — **your work in Blender, not a
      script step**.
- [ ] P2.4 Verify: curb attaches flush (zero gap) on both edges of a straight run; visual check
      that each kit piece's centerline(s) look correct in the 3D viewport.

> **One centerline per direction, no left/right split (decided 2026-07-23):** the old
> `road_graph.py` model authored ONE curve per multi-lane road cross-section and *derived* the
> per-direction/per-lane offset routes from it (lane count + oneway metadata → generated offset
> curves). This mesh-kit system doesn't need that derivation step: each hand-modeled lane strip
> (a `Kit_LaneStraight5` tile, or one tagged region inside an intersection mesh) is already
> physically one direction of travel, so its extracted `lanecl_*` curve already **is** a
> single-direction lane — a two-way street is built by placing two directional lane tiles
> side-by-side (or tagging two separate regions in one intersection mesh), not by splitting one
> shared centerline. `RKA_CurveSettings.oneway` (already registered, P1.2) is just metadata on an
> already-single-direction curve (true almost always; false only for the rare shared-strip case,
> e.g. a one-lane road used both ways). **Not adding** a separate "right lane / left lane" concept
> at the kit-piece level — it would duplicate information the mesh tagging already encodes.
> Geometry-Nodes-driven lane derivation from a single shared line (the thing being asked about)
> is exactly the pattern being retired with `road_graph.py`; don't reintroduce it here.

## Phase 3 — Connectivity, validation, export sidecar

- [ ] P3.1 New pure-Python (no bpy) module `lib/lane_kit.py`, same convention as
      `road_graph.py`: `LaneCurve(name, pts, lane_width, oneway, cls, loop, end_behavior)`;
      `cluster_endpoints(curves, eps=1.0)` (proximity-groups nearby endpoints — conceptually
      like `road_graph.from_curves`'s `CONNECT_EPS` clustering but standalone, and never
      mutates/splits curve topology); `classify_clusters(clusters)` → unambiguous (auto-link)
      vs ambiguous (needs manual review); `validate(curves, links)` → dangling endpoints,
      direction conflicts, disconnected islands (union-find); `export(curves, links, path)`
      (reuses `save_roads._spline_points`-style world-space sampling — consider relocating
      `_spline_points` into a shared module both import); `if __name__ == "__main__":
      self_test()` (synthetic straight+curb+T-junction scenario).
- [ ] P3.2 Addon operators: `ops_connect.py` (`RKA_OT_auto_connect`, `RKA_OT_review_connections`
      — UIList of ambiguous clusters with click-to-select + Link/Unlink; manual overrides stored
      as `(endpoint_a_key, endpoint_b_key)` pairs in a JSON-encoded custom prop on a
      `RKA_ConnectivityStore` Empty in the district's `MANUAL` collection — auto-detected links
      are never stored, only manual overrides), `ops_validate.py` (`RKA_OT_validate`),
      `overlay_draw.py` (viewport overlay: curve polylines colored by direction, endpoint dots
      green/yellow/red for linked/ambiguous/dangling), `ops_export.py`
      (`RKA_OT_export_lanekit`).
- [ ] P3.3 `tools/save_lane_kit.py` (new, headless: `blender <district>.blend --background
      --python tools/save_lane_kit.py`) — collects `lanecl_*` curves (walking
      `depsgraph.object_instances` to resolve collection-instance duplis), applies manual
      overrides, validates, exports `districts/<piece>.lanekit.json`:
      ```json
      {
        "piece": "District_X",
        "lanes": [{"name": "lanecl_X_12", "points": [[x,y,z], ...], "lane_width": 3.0,
                   "oneway": true, "class": "local", "loop": false, "end_behavior": "CHAIN"}],
        "links": [{"from": "lanecl_X_12", "from_end": "end", "to": "lanecl_X_13",
                   "to_end": "start", "auto": true}],
        "warnings": [{"type": "dangling", "lane": "lanecl_X_9", "end": "start"}]
      }
      ```
- [ ] P3.4 Export flow: `export_world.py` keeps glTF-extras only for small scalar metas
      (unchanged); the lane/connectivity data ships as a **parallel JSON artifact**, copied
      alongside the exported `.glb` into
      `src/main/resources/com/openworld/world/pieces/District_X.lanekit.json`.
- [ ] P3.5 Verify: build a small multi-piece test layout (straight → 4-way intersection →
      straight, plus a T off a side arm) via the addon; run `RKA_OT_validate` → zero
      dangling/island warnings; export and confirm `District_lanekittest.lanekit.json` is
      human-readable/git-diffable like `roads.json`.

## Phase 4 — Godot-side Path3D consumption (deferred until Phase 1-3 proven)

- [ ] P4.1 New `com/openworld/world/PathLaneRoute.java` (new class, **not** an in-place
      `VehicleRoute` rewrite — old baked `.tscn` files must keep working forever): `Node3D`
      holding a native `Path3D` child with `Curve3D` populated from sidecar points
      (`addPoint(pos, ZERO, ZERO)`). Preserves the exact surface
      `VehicleAIController`/`LaneGraph`/`IntersectionZone` depend on: `loop`,
      `nextRoutes`/`nextWeights`, `turn`, `approach`, `laneOffset`, `laneWidth`, `endBehavior`,
      `returnRoute`, `startPoint()`/`endPoint()`, tangents, `total()`, `pointAtLength(s)`,
      `lengthAtNearest(...)`, `pickNextRoute()`, `resolveRoute()`.
- [ ] P4.2 Replace `VehicleRoute.java`'s hand-rolled Catmull-Rom + arc-length arrays
      (`ensureBaked`, `catmull`, `cum[]`, lines 154-240) — in `PathLaneRoute`, use
      `Curve3D.getBakedLength()` for `total()`, `Curve3D.sampleBaked()` for `pointAtLength(s)`,
      `Curve3D.getClosestOffset()` (with a local-window fallback scan over `getBakedPoints()`
      to preserve `lengthAtNearest`'s corner-snap-avoidance). Lane offset still needs custom
      code but derives from `getBakedPoints()` instead of hand-subdividing.
- [ ] P4.3 Extract a small `Lane` interface (`startPoint/endPoint/loop/pickNextRoute/...`)
      implemented by both `VehicleRoute` and `PathLaneRoute`, so
      `LaneGraph`/`IntersectionZone`/`VehicleAIController` call sites work against either
      transparently.
- [ ] P4.4 `WorldBaker.java`: new `lanepath_<name>` marker-prefix branch (parallel to `lane_`,
      ~line 172-195) whose `_ready`-time job is loading sampled points from the district's
      `.lanekit.json` (via `godot.api.Json.parseString` → `Dictionary` walk) and building a
      `PathLaneRoute`; new `buildPathRoute()` method parallel to `buildRoute()`; `links[]` →
      `nextRoutes`/`nextWeights` wiring (default straight-biased weight when unspecified —
      decide this in `lib/lane_kit.py`'s export, keeping graph-shape decisions in the authoring
      tool).
- [ ] P4.5 `LaneGraph.java`/`IntersectionZone.java`: no logic change, just interface-typed call
      sites.
- [ ] P4.6 Note (not built now): sidecar data is flat/keyed/world-space, naturally compatible
      with `AUTHORING_GUIDE.md` §11's "always-resident manifest for cross-district A*" goal
      without needing `road_graph.py`'s graph classes — annotate §11 with this once P4 lands.
- [ ] P4.7 Verify: bake `District_lanekittest` via `tools/build_piece.sh`; load in
      `SoloPiece.tscn`; F4 spawns cars that follow `Path3D` lanes, turn correctly at the
      intersection, yield via unmodified `IntersectionZone`; confirm `LaneGraph` picks correct
      outgoing lanes at the junction.

## Phase 5 — Pilot migration of one real placeholder district (deferred)

- [ ] P5.1 Pick one placeholder district; author its full layout with the (by-then mature)
      addon + kit; export, bake (stem form, not added to `build_district.py`'s CONFIG — same
      precedent as `District_kitdemo_9_9`), walk-test.
- [ ] P5.2 Old/new pipelines coexist per-district automatically: `WorldBaker` picks the path
      based on which sidecar exists next to the district's export (`roads.json`-derived markers
      → old `buildRoute`; `.lanekit.json` → new `buildPathRoute`).
- [ ] P5.3 Verify: side-by-side walk-test against the old placeholder (traffic density/behavior
      parity via `ZoneManager.debugLog`'s routed/moving counts per §7). Once satisfied,
      write it up as the worked example in a new `AUTHORING_GUIDE.md` §7 subsection, the way
      `District_kitdemo_9_9` is today.

## Phase 6 — District + arterial pipeline integration, full road_graph.py replacement (started 2026-07-27)

> **Progress tracker for this specific push — concretizes Phase 3/Phase 5 above, which stayed as
> vague stubs while "Kit geometry v2" (items 1-18) shipped the Java-side `Lane`/`PathLaneRoute`/
> `WorldBaker.lanekitPath` work far ahead of the original phase numbering. Mark items `[x]` as they
> land; a fresh session should read this section first to resume exactly where the last one
> stopped.** Full plan detail lives in the session's own Claude Code plan file; this section is the
> durable, resumable checklist. **Scope note (revised 2026-07-27):** this is now a FULL replacement
> of `road_graph.py` — both district-level `road_*`-curve authoring AND `towns/build_world.py`'s
> master arterial backbone generator — not a "coexist" migration. `road_graph.py` deletion itself
> is deferred to P6.8, once nothing depends on it. A separate, much larger initiative ("Track B":
> Blender as a general level editor, `WorldBaker`'s name-prefix dispatch replaced by custom-
> properties/glTF-extras for EVERY node type not just roads, districts restructured as collections
> instead of separate files) was explicitly raised and explicitly deferred — do not fold it into
> this phase; it needs its own dedicated research+plan pass once P6 ships.

- [x] P6.1 — `ZoneManager`/`Lane`/`PathLaneRoute` retyping (Java). `Lane.entryPoint()` (new
      interface method — NOT redundant with `pointAtLength(0)`, see rationale below); retype
      `ZoneManager`'s registry/`findRoute`/`spawnVehicle`/`vehicleStartPoint` from
      `VehicleRoute` to `Lane`; `vehicleStartPoint` rewritten off `Lane.total()`/`pointAtLength()`
      (was raw `VehicleRoute.waypoints()` — not expressible against `Lane`); `VehicleRoute`/
      `PathLaneRoute` add `entryPoint()`; `PathLaneRoute` gains `_ready()` registration +
      `_exitTree()` deregistration (deliberately absent until now); `RouteDebugOverlay.java` (F3)
      retyped in the same change (reads `getRoutes()`'s value type directly, breaks otherwise).
      **Code complete, `./gradlew build` passes clean (compile + existing test suite).** Runtime
      verification (extended `PathLaneRouteTestHost` registry assertion; stem-form rebuild +
      walk-test of an existing `.roads.json` district) deferred to P6.9 — later phases touch
      overlapping runtime paths, more efficient to verify once at the end.
- [x] P6.2 — Collision-only mesh for road_kit_authoring geometry (Blender). New
      `kit_common.colonly_polygon()` (fan-triangulated flat slab from `junction_pad`'s own
      boundary control points, ignoring fillet radius — coarse is fine, same standard `colonly()`'s
      box proxy already accepts) for pad footprints; wired the *existing* `kc.colonly_swept()` into
      every `curb_loop()` call site (`ops_intersection.py`'s per-corner intersection curbs,
      `ops_segment.py`'s straight/curved-segment L/R curbs, and lane-transition L/R curbs) for curb
      collision. Colonly objects are named off the same `curb_`/`pad_`-prefixed base name (+
      `-colonly`), so `clear_generated_mesh_objects`'s existing prefix-based cleanup sweeps them on
      rebuild with zero new cleanup code. Deliberately NOT added to `visual_objs`/
      `join_visual_mesh`/the addon's own per-piece `gltf_export_path` (would merge collision into
      the rendered mesh) — relies on `export_world.py`'s whole-scene sweep for districts, same as
      every other `-colonly` proxy in the codebase.
      **Verified**: new `smoketest_collision.py` (pad + per-corner curb colonlies with real
      geometry on a 4-way; rebuild leaves no duplicates/orphans; `join_visual_mesh=True` still
      leaves colonly proxies un-joined; straight/curved segment + lane-transition L/R curb
      colonlies; GUTTER curb style too) — passes, and the full smoketest suite (12 files) +
      `python3 lib/intersection_kit.py` self-test still pass with no regressions. Also verified a
      real `bpy.ops.export_scene.gltf` export + re-import round-trip preserves every `-colonly`
      object name intact (the actual mechanism Godot's importer keys off to drop the visual and
      build a `CollisionShape3D`). Relevant to P6.8: `backbone_deck()`'s per-gridline collision
      strip becomes redundant only once the new arterial network (P6.5/P6.7) is both authored AND
      covered by this collision — verify before retiring it, don't remove speculatively;
      `safety_floor()` (world-spanning, road-agnostic catch-all) stays regardless.
- [x] P6.3 — Harden road_kit_authoring against linked library content (Blender). New
      `local_collection(name)`/`local_object(name)` helpers in `ops_intersection.py` (mirror
      `kit_common.get_coll()`'s `.library is None` filter exactly), imported by `ops_segment.py`,
      used via `from . import ops_intersection` in `live_edit.py`. Fixed every unqualified by-name
      lookup: `ops_intersection.py`'s intersection auto-naming loop + curve-owner scan,
      `ops_segment.py`'s three auto-naming loops (segment/curved-segment/transition) + five
      spine-object-by-name lookups (`RKA_OT_select_spine`, `adjust_segment_lanes`'s pre-rebuild
      radius write, `_resolve_curve_object`, both `rebuild_*_in_place` functions),
      `live_edit.py`'s two dirty-collection-detection scans (now skip `coll.library is not None`)
      and `_flush_rebuilds`'s four post-name collection re-resolutions. Left unchanged (by design,
      confirmed intentional): `_resolve_curb_asset`'s lookup into the linked `kit/curb_kit.blend`
      library, and `live_edit.py`'s `for o in bpy.data.objects: if o.data == obj` scan (compares
      data-block IDENTITY, not name — not subject to this failure mode).
      **Bonus fix found along the way**: `kit_common._curb_profile_object`'s module-level cache
      (`_CURB_PROFILE_CACHE`) is a plain Python global that survives a File > New/Open in the same
      Blender session — its staleness check (`obj.name in bpy.data.objects`) crashed with
      `ReferenceError` on a freed cross-file object instead of treating it as a cache miss; wrapped
      in `try/except ReferenceError`. This is a real latent bug independent of P6.3 (bites any real
      session that builds a curb, then opens a different `.blend`), caught by this phase's own
      cross-file smoketest.
      **Verified**: new `smoketest_linked_content.py` — builds a "neighbor" file with an
      auto-named `Segment_001`, saves it, then in a fresh session builds a LOCAL `Segment_001`
      (same auto-name, since it's that file's own first segment too), links the neighbor's
      collection in read-only (`link=True`, mirroring `link_neighbors.py`'s own mechanism),
      confirms `local_collection`/`local_object` resolve to the local objects specifically, that
      `adjust_segment_lanes` on the local piece mutates ONLY local data (the linked collection's
      `rka_lanes` stays untouched), and that a third local segment still auto-names to
      `Segment_002` (not perturbed by the linked `Segment_001`). Full smoketest suite (13 files) +
      `python3 lib/intersection_kit.py` self-test all still pass.
- [x] P6.4 — Multi-piece connectivity/validation/combined export + property-based zone tagging.
      `lib/lane_kit.py` (pure Python, no bpy, 7-section self-test) — `derive_connection_points`
      clusters only PIECE-EXTERNAL points (a junction's own `ports`, already deduplicated per
      arm/lane/direction; a segment/transition's synthesized per-lane start+end), specifically so
      a junction's own internal fan-out is never misreported as inter-piece ambiguity;
      `cluster_points`/`classify_cluster` (isolated/paired/ambiguous, `JUNCTION_RADIUS=4.5` synced
      to `LaneGraph`); `combine_pieces` namespaces every lane/arm id `<piece>__<id>`, tags
      `zone_id`/`piece_id`, returns the exact `{'lanes','arms'}` shape `WorldBaker` already
      consumes. `custom_props.read_arms_full` (new) reconstructs real `intersection_kit.Arm`
      objects (incl. `tail_length`/`oneway`/`traffic_side`, which the older `read_arms` drops) from
      a built intersection's `rka_arm_*` custom props. `tools/save_lane_kit.py` (new, mirrors
      `gen_roads_only.py`) collects every piece in the open `.blend` (dispatch mirrors
      `_rebuild_piece_in_place`'s exact check order), rebuilds each piece's `export_*_dict` call
      from its own stored `rka_*` properties, combines via `lane_kit.combine_pieces`, prints
      isolated/ambiguous warnings, writes `<stem>.lanekit.json` next to the `.blend` (works for
      districts/ or overlays/ unchanged). `ops_connect.py` (manual review UI for ambiguous
      clusters) — **not built**, deferred (not blocking; warnings print to stdout today).
      **Property-based zone tagging (per user decision):** every lane carries `zone_id` (stem
      default, per-piece override via `rka_zone_id`). Java: `PathLaneRoute.zoneId` (new `@Export`),
      populated by `WorldBaker.buildPathLaneRoute` from the sidecar's `zone_id`;
      `ZoneManager.findRoute` gains a zone-id-equality pass (checked before the unchanged
      name-prefix fallback) via a new shared `isSpawnCandidate` filter.
      **Verified end-to-end this session** against the user-designated AI-drive test fixture
      `assets/world_source/debug_road.blend` (5 intersections, 12 segments, 1 transition): `blender
      debug_road.blend --background --python tools/save_lane_kit.py` → 82 lanes/17 arms/18 pieces
      combined into `debug_road.lanekit.json` (4 paired, 24 isolated, 11 ambiguous clusters printed
      — see note below); fed directly to `WorldBaker.bake(..., lanekitPath)` via a new headless
      regression host (`LaneKitCombineTestHost`/`LaneKitCombineTest.tscn`, run via `godot
      --headless res://.../LaneKitCombineTest.tscn`, grep `LKCTEST verdict`) — confirmed all 82
      `PathLaneRoute`s built, all tagged `zoneId="debug_road"`, all 82 registered in
      `ZoneManager.getRoutes()`. `./gradlew build`/`test` pass. `python3 lib/lane_kit.py` and
      `python3 lib/intersection_kit.py` self-tests both pass.
      **Finding, not a bug, and confirmed INTENTIONAL by the user** (dense layout on purpose, to
      simulate real close-quarters traffic — not something to "fix"): `debug_road.blend`'s
      intersections sit close together (~12 m tail_length spacing), so several of that fixture's
      own arm ports legitimately fall within each other's `JUNCTION_RADIUS` (4.5 m) — `lane_kit.py`
      correctly flags these as `ambiguous` (informational only; `WorldBaker`/`LaneGraph` still
      connect everything fine at runtime via the same proximity clustering — this is purely an
      authoring-time heads-up, not a blocker). No action needed before P6.9 walk-testing.
- [x] P6.5 — REMOVED FROM SCOPE (2026-07-27, explicit user instruction: "remove the arterial
      content"). A scaffolding pass was built and verified earlier this session
      (`overlays/Overlay_Arterial.blend` skeleton + `tools/link_overlay_context.py`, a
      generalization of `tools/link_neighbors.py` to link every built district's `STREET` into an
      overlay's `NEIGHBOR_REF`) but has been **deleted** — both files removed, nothing references
      them. Rationale (per the user): the arterial/highway overlay content itself isn't being
      pursued right now; ordinary district-to-district local-road connectivity (see the
      architecture note below, kept — it's the general case, independent of any overlay) covers
      cross-district traffic without needing a dedicated arterial file. If a highway/bridge overlay
      is wanted later, this scaffolding is cheap to rebuild (it's exactly `link_neighbors.py`
      generalized to "every district" + the standard overlay-file skeleton — no new mechanism was
      invented, see the git history around 2026-07-27 if useful as a reference).

  ### Architecture note: cross-piece connectivity is GENERAL, not highway-specific (revised this
  ### session per user correction — still relevant even with P6.5's overlay scaffolding removed,
  ### since district-to-district connectivity, case 1 below, is the general case regardless)

  Earlier framing of this note treated "district road meets a hand-crafted highway ramp" as the
  motivating case for a formal connection contract. **User correction: most district-to-district
  boundaries are ordinary local roads that simply continue across the seam — no highway/overlay
  involved at all** (a district may only ever connect to its neighbors via plain local streets;
  only SOME district pairs are additionally bridged by an arterial/highway overlay). The general
  rule (explicitly: "assume this is what's done in GTA and Forza-like games") is: **at a
  district's edge, find the nearest road in the neighboring district and assume a real, drivable
  connection is meant to exist there** — the same physical-proximity assumption `LaneGraph`
  already makes at runtime for everything else, just applied at the district-to-district seam
  specifically, and independent of whether a highway is involved.

  Two cases, same underlying mechanism, different reasons the geometry needs hand-authoring:
  1. **District ↔ district (the common case).** Two neighboring districts are peers, each authored
     independently against the same visual reference (the existing `link_neighbors.py`/overlay
     `NEIGHBOR_REF` read-only-linked-neighbor convention already used elsewhere in this pipeline —
     no new authoring mechanism needed). An author places their boundary road near where they can
     see the neighbor's already sits; no footprint reservation or socket object is needed on either
     side, since neither district paves over the other.
  2. **District ↔ overlay (highway/bridge ramp).** The overlay's ramp geometry is grade-separated
     and hand-sculpted, and DOES physically overlap/replace a strip of the district's own road
     footprint — this is the one case that still needs the "district reserves the footprint, ramp
     lane spine still exported through the normal pipeline" convention from the original note
     (kept, just scoped correctly now as the exception, not the general rule).

  **Verification tool — no new clustering logic needed, `lane_kit.py`'s existing `combine_pieces`
  already accepts any `{'lanes': [...]}`-shaped dict** (a single Blender piece's `export_*_dict`
  output and an already-combined `<stem>.lanekit.json` sidecar look identical to it — both are
  just a flat lane list), so cross-file checking is a thin CLI wrapper, not a new module:
  `tools/check_lanekit_connectivity.py` (new, small) takes two already-baked `.lanekit.json`
  sidecars (two districts, or a district + an overlay), runs `lane_kit.combine_pieces` across them,
  and reports which lanes paired across the file boundary vs. stayed isolated — the general
  "does this piece's boundary road actually reach its neighbor's" check, usable for EITHER case
  above. Run it after authoring any district (or overlay) against each of its real neighbors,
  before baking — catches a "the road doesn't actually reach the border" mistake at authoring
  time instead of a runtime "car drives into the void" bug at the district seam.

  **Built and verified this session** (`tools/check_lanekit_connectivity.py`, ~70 lines): tested
  against three synthetic sidecars — a road ending exactly at a shared boundary point correctly
  reports one cross-file `[OK]` pairing (plus each side's own far/interior end reported
  `isolated`, as expected — a district's own interior end isn't a boundary question); two
  unrelated far-apart roads correctly report zero cross-file connections plus the `WARNING` line.
  Ready to use once P6.7 produces real neighboring `.lanekit.json` sidecars to check against each
  other.

  **Routing preference (user instruction, recorded for future GPS/pathfinding work — NOT
  implemented, no A*/destination routing exists yet — see `AICharacter`/`VehicleAIController`
  docs: "vehicle-AI destination logic is out of scope, deferred"):** when a cross-network route
  planner eventually exists, prefer a highway/arterial overlay path when one is available (assumed
  fastest), falling back to district-to-district local roads otherwise. Both path types are
  already the same `Lane` abstraction with no source-file distinction at the `LaneGraph` level, so
  this is purely a future path-cost/heuristic concern (e.g. a lower travel-time weight on overlay
  lanes) — no architecture change implied by this preference, just noted here so it isn't lost
  before Part F/routing work begins.
- [x] P6.6 — Auto-detection wiring. `tools/build_piece.sh`'s `bake_one()` reads an outer-scope
      `LANEKIT_PATH` (set only around the full-detail bake call, cleared for the LOD_LOW call —
      LOD_LOW is visual-only, no traffic lanes needed) and conditionally emits a `lanekit_path`
      line into the throwaway bake-host `.tscn`; `LANEKIT_PATH="$BP/districts/$STEM.lanekit.json"`
      when that file exists, else `""` (no line at all). Same conditional added to
      `tools/build_overlay.sh` for `overlays/<Name>.lanekit.json`. `towns/build_world.py`'s region-
      marker loop gains a parallel `has_lanekit` check alongside its existing `has_roads` check:
      `has_roads` wins (prints a WARNING if both sidecars somehow exist — a district mid-migration,
      not a supported permanent state); `has_lanekit` sets `traffic_route = stem` (an EXACT match,
      not a `"<stem>__"` prefix — matches `lib/lane_kit.py:combine_pieces`'s default `zone_id`,
      hits `ZoneManager.findRoute`'s new zone-id-equality pass from P6.4 directly).
      **Verified this session, real bakes (not just syntax-checking):**
      - `bash tools/build_piece.sh District_industry_5_1` (stem form, no sidecar present) →
        `pathlanes=0`, no `lanekit ->` log line — unchanged from before this phase.
      - Copied `debug_road.lanekit.json` to `districts/District_industry_5_1.lanekit.json`
        (synthetic — NOT real content for that district, just a wiring probe), re-ran → `lanekit ->
        .../District_industry_5_1.lanekit.json` printed, `WorldBaker: ... pathlanes=82` (matches
        the sidecar's own lane count exactly). Removed the probe file, re-ran once more →
        `pathlanes=0` again, confirming the OFF path is genuinely restored, not sticky.
      - Same on-then-off probe repeated for `tools/build_overlay.sh Overlay_RainbowBridge` — same
        result (`pathlanes=82` with the probe, `pathlanes=0` without).
      - Every probe run's re-baked `.gltf`/`.tscn`/`.scn` (LFS-tracked binaries) was reverted via
        `git checkout --` afterward — the actual byte content between two "no sidecar" bakes was
        NOT identical (a few dozen bytes of unexplained non-determinism in the existing Blender
        glTF-export/Godot-bake pipeline, e.g. an export timestamp — pre-existing, unrelated to
        this phase's change; confirmed by bit-diffing that a repeat "no lanekit" bake still
        differs from the committed baseline even on an UNMODIFIED code path). Not a regression
        introduced here, but worth knowing the "byte-identical" verification goal doesn't
        literally hold for these binary bake artifacts across separate invocations regardless of
        lanekit involvement — `pathlanes=0`/`traffic_route`/`traffic_count` field-level equality
        is the real, confirmed invariant.
      - `towns/build_world.py`'s `has_lanekit` change: verified by `python3 -m py_compile` +
        manual trace against the existing `has_roads` pattern it mirrors — a full master-world
        rebuild was deliberately NOT run this session (an expensive, wide-blast-radius operation
        touching every district; the code change itself is a small, mechanically-obvious
        conditional with no district-specific logic to miss). Batch into P6.9's end-to-end pass,
        same deferral precedent as P6.1's own runtime verification.
- [x] P6.7(a) — `District_industry_5_1` migration — DONE, automatic first pass (per user: "try 5_1
      with automatic setup first, will then try to handcraft update later" — NOT meant to be
      pixel-perfect, a base to hand-tune).

  **CRITICAL BUG found and fixed while migrating this district** — `intersection_kit.export_dict`/
  `export_json` exported every junction's lanes/ports in **local, junction-centered coordinates**,
  never adding the junction's own world position back in (`build_lane_movements`/`build_ports`
  work in a frame where an `Arm` carries only an angle, never a world position — nothing in the
  whole call chain ever added the junction's actual `(cx, cy)`). This affected EVERY intersection
  ever built off-origin via this addon, including the one production caller
  (`RKA_OT_build_intersection`'s own `export_path` field) — invisible until now because every
  existing self-test/fixture happened to be built at or very near world origin. Went unnoticed by
  P6.4's own "verified end-to-end" pass against `debug_road.blend` too: its 5 intersections
  ARE scattered across real world positions (confirmed via `rka_origin`), but debug_road's own
  ambiguous-cluster report was itself the tell — 5 physically-distinct junctions' ports were
  clustering at one small local-ish point, which should never happen for real off-origin junctions.
  **Fix**: `export_dict`/`export_json` gained a `center=(x, y)` parameter (default `(0, 0)` — every
  existing call site is byte-identical unless it opts in), added to every lane point and port
  position (never to a tangent — a direction, not translated). Both real call sites fixed:
  `RKA_OT_build_intersection`'s `export_path` (now passes `center=(cx, cy)`) and
  `tools/save_lane_kit.py`'s `_export_intersection` (now passes `center=(origin[0], origin[1])`).
  New self-test #36 in `intersection_kit.py` (default byte-identical to omitting the param;
  non-zero center translates every lane/port point by exactly that offset; tangents untouched).
  **Re-verified `debug_road.blend` after the fix**: paired connections jumped from 4 to **32** (up
  from a badly broken baseline this bug had been silently producing) — the remaining 10 ambiguous
  clusters are a real, separate authoring quirk in that fixture (3 near-duplicate parallel segments
  stacked at the same points, `Segment_007/008/009`), not a bug in the export.

  **Migration script**: new `tools/migrate_district_5_1_lanekit.py` (one-shot, headless) — reads
  the existing `.roads.json` sidecar (4 curves: `road_spine` arterial + `road_north_st` local
  share an EXACT point, a real 3-way junction; `road_se_st`/`road_sw_ave` are standalone, no
  junction), computes each arm's bearing from the junction to its curve's next point, builds one
  NWAY intersection (`kerb_radius=6`, `tail_length=8`, `lane_width=5`, all addon operator
  defaults) via `ops_intersection.build_intersection_geometry`, then extends a segment from each
  arm's own marker position through that road's remaining original curve points (preserving every
  bend — NOT straight-lined) via `ops_segment._build_segment_from_points`; the two standalone
  streets get the same treatment with no intersection. New pieces live in a `MANUAL` collection
  (survives a future non-stem-form regen, unlike `STREET`/`MARKERS`/`ROADS_SRC`). Clears the OLD
  206 `lane_*`/`intersection_*` Empties from `MARKERS` so export doesn't double up. Result: 1
  intersection + 5 segments, `save_lane_kit.py` → 28 lanes.
  **Known finding, left for hand-tuning (not fixed automatically — matches the user's own
  framing):** arms A (west spine, bearing 131.6°) and C (north_st, bearing 107.4°) are only 24°
  apart — a genuinely tight/acute junction angle in the SOURCE data — so their ports land within
  `JUNCTION_RADIUS` of each other, producing one 12-member `ambiguous` cluster (still resolves
  fine at Godot runtime via `LaneGraph`'s own proximity connectivity; purely an authoring-time
  clarity issue). Widening the angle, shrinking `kerb_radius`/lane count for that junction, or
  accepting it are all reasonable hand-tuning options.
  `districts/District_industry_5_1.roads.json` deleted (superseded — its data no longer matches
  what's baked; this is a **scoped, per-file** version of P6.8's broader cleanup, not the full
  `road_graph.py` code removal, which still has a real remaining dependent: `build_world.py
  --with-lanes`).
  **Verified**: `blender ... save_lane_kit.py` → 28 lanes/6 pieces; `tools/build_piece.sh
  District_industry_5_1` (real bake, not a probe this time) → `WorldBaker: ... routes=0 zones=0
  junctions=0 pathlanes=28` (routes/junctions=0 confirms the OLD system found nothing — clean
  cutover, no double network); a second `LaneKitCombineTestHost` scene
  (`LaneKitCombineTestIndustry51.tscn`, new — the host script's `lanekitPath`/`expectedZoneId`
  are now `@Export`-overridable, defaulting to the original `debug_road` fixture so the existing
  test is unaffected) confirms all 28 `PathLaneRoute`s built + registered + tagged
  `zoneId="District_industry_5_1"`. Full regression re-run after the `center` fix: `./gradlew
  build`/`test`, `python3 lib/intersection_kit.py` + `lib/lane_kit.py` self-tests, all 13
  `addons/road_kit_authoring/smoketest_*.py`, `PathLaneRouteTest.tscn` (PASS, unaffected —
  its fixture happened to be built near-origin), both `LaneKitCombineTest*.tscn` scenes — all pass.

  **NOT yet built** (walk-test / visual confirmation): stem-form bake done, but no interactive
  Godot walk-test / F3 debug-overlay visual check was performed this session (headless-only
  verification, per this session's tooling — the user's own promised hand-tuning pass is the
  natural point to do this visually).

- [x] P6.7(b) — REMOVED FROM SCOPE (2026-07-27, same instruction as P6.5). No arterial network
      migration is planned; District_industry_5_1 (P6.7(a)) was the only P6.7 target.
- [x] P6.8 — DONE (2026-07-27; per explicit user instruction — "no need backward compatible as
      will regenerate" — full removal, no dual-path kept anywhere). Everything that imported or
      called `road_graph.py` is gone:
      - `towns/districts/build_district.py`: `load_roads_sidecar`/`import_roads_src`/
        `emit_authored_roads` deleted, and their one caller (the `roads = load_roads_sidecar(cfg);
        if roads: ...` block in `build()`).
      - `tools/gen_roads_only.py`, `tools/save_roads.py` — deleted (`tools/save_lane_kit.py` is
        their successor, already shipped in P6.4).
      - `tools/build_kitdemo.py` — deleted too (found while grepping for real importers, not
        originally listed in this plan): its whole purpose was demoing `road_graph.py`'s
        divided-road/median features via `gen_roads_only.generate(...)`; no `District_kitdemo_9_9`
        was ever actually built, so nothing else references it.
      - `towns/build_world.py`: `backbone_graph()` deleted entirely (was the master arterial lane
        generator — moot now that P6.5/P6.7(b) are removed from scope), along with the
        `--with-lanes`/`--driving-side` CLI flags and the `ART_LANES`/`ART_STOP_RADIUS` constants
        it alone used. The per-district `traffic_route` logic simplified to a single `has_lanekit`
        check (no `has_roads`, no dual-path, no `"art_"` fallback — every district's ambient
        traffic now comes from its own `.lanekit.json` or nothing). **Kept unconditionally**, as
        planned: `backbone_deck()` (ArtDeck collision strips, `--with-deck`) and `safety_floor()`
        (`--with-floor`) — both are pure Blender-side collision geometry with **zero dependency on
        `road_graph.py`** (confirmed by reading them directly, not assumed), independent of
        whether any lane graph exists on top.
      - `lib/assemble.py`: `lay_road_graph()` deleted too (found while confirming zero remaining
        importers before deleting `road_graph.py` itself — it was `road_graph.py`'s only remaining
        caller once `backbone_graph()`/`emit_authored_roads()` were gone, so it became dead code).
      - `lib/road_graph.py` itself — deleted. **Verified via repo-wide grep for actual `import
        road_graph`/`from road_graph import` statements (not just the string "road_graph", which
        still appears in a handful of docstrings/comments referencing it historically — those are
        fine, left as-is) — zero real importers remained before deletion.**
      - `AUTHORING_GUIDE.md` §7 rewritten from the old generator-driven workflow (~250 lines) to
        document the current `road_kit_authoring` addon loop (build pieces → `save_lane_kit.py` →
        `build_piece.sh`/`build_overlay.sh` auto-detect `lanekit_path` → `zone_id`-matched ambient
        traffic); the §4 edit-channels list and §11 GPS section's `road_graph.py` references
        updated to match (the GPS section's planned A* design now reads from `.lanekit.json`
        sidecars instead).
      **Verified**: `python3 -m py_compile` on every touched `.py` file;
      `./gradlew build`+`test`; `python3 lib/intersection_kit.py` + `lib/lane_kit.py` self-tests;
      all 13 `addons/road_kit_authoring/smoketest_*.py`. District_industry_5_1's own bake (P6.7(a))
      already exercises the new `build_world.py` `has_lanekit` path end-to-end in spirit (same
      check, just not run through an actual master-world rebuild this session — see P6.9).
- [x] P6.9 — Final end-to-end verification. `./gradlew build`+`test`; `python3
      lib/intersection_kit.py` + `lib/lane_kit.py` self-tests; full 13-file road_kit_authoring
      smoketest suite; two real `towns/build_world.py` runs (default `minimal` — `decks=0
      floor=0` — and `--full` — `decks=14 floor=1` — confirming `backbone_deck()`/`safety_floor()`
      still work standalone after `backbone_graph()`'s removal, and the simplified `has_lanekit`
      `traffic_route` logic runs without error across all 36 real districts). Left
      `world_master.blend` in its default `minimal`-mode regenerated state afterward.
      P6.1's `PathLaneRouteTestHost` registry-assertion extension — **still not done** (low value
      now: superseded in spirit by the multi-district test below, which exercises the same
      registration path against two REAL districts instead of one synthetic fixture).

  ### Multi-district streaming + connectivity test (2026-07-27, user-requested follow-up)

  User asked whether `debug_road.blend` could exercise real multi-district `ZoneManager`
  streaming (not just single-district pipeline loading, already covered by P6.4/P6.7's tests)
  without needing the full 36-district production world. Investigated and confirmed: districts
  are authored in **local** coordinates and positioned entirely by their `ZoneMarker`'s own
  world transform at stream time (`ZoneManager`: `t.marker.addChild(geo)`, read directly, not
  assumed) — so `ZoneManager` streaming needs no production-scale setup at all; any host scene
  with `ZoneMarker`/`Zone` objects works, exactly the `DebugHarness.spawnDebugZone()`
  (F12) precedent, just with real baked district geometry instead of a code-built box. Explicitly
  scoped OUT: physically co-authoring multiple districts in one `.blend` split into multiple
  output scenes at export time — overlaps with the deferred Track B "restructure districts as
  collections" redesign; the existing per-district-file + `link_neighbors.py`-style read-only
  reference convention already solves "see my neighbor while I author the boundary road" without
  new export machinery.

  **Built:**
  - `districts/District_test_8_8.blend` — a copy of `debug_road.blend` (untouched, still usable
    standalone) plus one new standalone stub segment (`Segment_to_test_b`, local (100,0)→(150,0))
    reaching toward where the companion district connects. (First attempt used `debug_road`'s
    existing `Segment_010` far end as the connection point — **wrong**: re-verification showed
    that end was already connected to `Intersection_4WAY_004` inside `debug_road` itself, not a
    free stub; `debug_road.blend`'s only genuinely isolated point turned out to be
    `Intersection_4WAY_001`'s unused east arm, too close to the rest of the network to be useful
    for a streaming-distance test — hence the new dedicated stub instead of reusing existing
    geometry.)
  - `districts/District_test_7_8.blend` (`tools/build_district_test_b.py`, new) — a small
    companion: one 3-way intersection + 3 short segments (one reaching to local origin as the
    connector, matching the "district content is local, positioned by its marker" finding above).
  - **Found + fixed a real gap in `tools/check_lanekit_connectivity.py`**: it compared two
    sidecars' RAW local coordinates with no world offset applied — meaningless for real districts,
    which are never in a shared coordinate frame until positioned by their markers (silently
    produced a garbage 0-connections result on the first attempt, then plausible-looking but
    ultimately spurious `ambiguous` clusters on the second — both traced directly to the missing
    offset, not a clustering bug). Added `--offset-a`/`--offset-b x,y,z`, defaulting to
    `world_grid.district_center`/`elev_at` automatically for any `District_<theme>_<gx>_<gy>`-
    named stem on the real grid (so real production districts need no manual offset), explicit
    for anything else (these off-grid test districts, or an overlay). Re-verified with the fix:
    `--offset-b 150,0,0` → exactly 2 clean `[OK]` cross-file pairings (both lane directions) at
    world (150, 0.15, ∓2.5) — the two-way stub meeting the companion's connector precisely.
  - `tools/migrate_district_5_1_lanekit.py`-style headless build → both districts baked via
    `tools/build_piece.sh` (stem form): `District_test_8_8` → 84 `PathLaneRoute`s,
    `District_test_7_8` → 12 (note: baking a second district after the first repoints
    `SoloPiece.tscn` at whichever was baked last — `build_piece.sh`'s own documented behavior, not
    a bug; it now points at `District_test_7_8`).
  - `src/main/java/com/openworld/debug/MultiZoneStreamTestHost.java` +
    `MultiZoneStreamTest.tscn` (new) — builds both districts' `Zone`/`ZoneMarker` in
    CODE (the `spawnDebugZone()` idiom, no master `.blend`/region-marker authoring needed for a
    lightweight test), each with a `VehicleSpawnConfig.routeName = zoneId` (exercising P6.4's
    zone-id-equality `findRoute` path), plus a `Characters` container Node (`ZoneManager`
    silently skips ambient/vehicle spawns without one — found by the first run logging "Characters
    container not found"). Spawns a real `Player`, teleports it near both zone centers then far
    away, logs `PathLaneRoute` + vehicle counts at each phase.
  - **Verified, real end-to-end PASS**: both zones streamed in with correct geometry
    (`pathLanes=84`/`12`, matching each district's own bake exactly) AND real ambient traffic (3
    vehicles each, 6 total, correctly routed via each zone's own `zoneId`); both fully unloaded
    (`pathLanes=0`, `vehicles=0`) once the player retreated. `MDSTEST verdict=PASS`.

  **Net result**: the multi-district streaming + cross-district connectivity question is now
  answered with a real, repeatable, headless test — not just a design argument. The
  `check_lanekit_connectivity.py` offset fix is the concrete, generally-useful artifact any real
  future district-pair check will need.
- [x] P6.10 — DONE (2026-07-27). `WorldPreviewBuilder.java` (new, `@Tool`-annotated, same
      `bakeOnReady`/`quitWhenDone` offline-batch idiom as `WorldBaker`/`NavBaker`/
      `PieceBinaryConverter`) + `hosts/BuildWorldPreview.tscn`. Answers "can't see districts
      assembled together in the Godot editor" (confirmed real: `hosts/WorldMaster.tscn` has zero
      static `District_*` references, every district is 100% runtime-streamed — opening it in the
      editor shows only region markers). **Built to the revised scope** (user follow-up,
      recorded earlier this session): selective, not "load all 36 at once" — `districtStems` is a
      comma-separated allow-list (blank = every district, for the rare case that's actually
      wanted).
      **Positions read from the real baked master, never recomputed**: rather than re-deriving
      `lib/world_grid.py:district_center` math in Java (a real risk of drift between the two
      languages), `WorldPreviewBuilder` instantiates the already-built `World_master.tscn`,
      walks its `ZoneMarker` children, and reads `(zoneId, geometry_path,
      globalPosition)` straight off each one — the exact same values `ZoneManager` streams
      districts at in the real game. Each selected district's own already-baked `.tscn` (full
      detail, the same file that streams in-game) is instanced as a plain child at that position
      — no `ZoneMarker`/`Zone` wrapper, nothing that would make `ZoneManager` try
      to stream it.
      **Bug found and fixed while verifying, not assumed correct from the design:** the first
      version called `setOwnerRecursive` on every instanced district (owning every descendant,
      not just the root) — `pack()` responded by FLATTENING each district's full content
      (every individual building/road mesh listed by name) into the output file instead of
      recording a lightweight `instance=` PackedScene reference. Confirmed directly by inspecting
      the actual output file (grew to reference individual `MeshInstance3D` nodes by the
      thousands). Fixed by mirroring `WorldBaker`'s own `instanceRoots` exclusion exactly (owner
      stamped on the instance ROOT only, its internals left untouched) — re-verified: a 3-district
      test (`District_industry_5_1`/`District_city_1_1`/`District_harbor_0_0`, via
      `debug/WorldPreviewTest.tscn`, kept as a permanent regression check) now produces a 16-line
      output file with exactly 3 lightweight `instance=` references, each carrying the correct
      world `transform` (spot-checked `District_industry_5_1`'s against the exact position
      confirmed earlier this session by the F1-landing diagnostic: `(1260, 0, 756)` — matches
      exactly). A second `!is_inside_tree()` bug (setting a freshly-created, not-yet-parented
      preview root's children's global positions before adding the root to the live tree) was
      also found and fixed the same way — verified against real output, not assumed from reading
      the code.
      **Verified**: `./gradlew build`+`test` pass; real headless run against 3 real districts
      produces a correct, lightweight, directly-openable-in-the-editor static preview scene.

  ### Toolchain upgrade: godot-kotlin-jvm 0.15.0-4.6 -> 0.16.3-4.6.3 (2026-07-27, user-requested)

  Binary path (every tool script's `GODOT` default, `reference_godot_jvm_binary` memory) switched
  to `/data/danilko/bin/godot.linuxbsd.editor.x86_64.jvm.0.16.3`; `build.gradle.kts` plugin
  version was already bumped by the user to `0.16.3-4.6.3`, but the `godot {}` DSL block was not
  yet updated to match — a real, breaking, multi-version API jump, not a drop-in bump. Two
  breaking changes found and fixed (confirmed via an actual full rebuild, not assumed from a
  changelog):
  1. **`godot {}` DSL renames**: `registrationFileBaseDir` -> `registrationFilesDirectory`;
     `isRegistrationFileGenerationEnabled` -> `disableGdj` (renamed **and inverted** — `true` used
     to mean "generate", `false` now means "don't disable", i.e. still generate). Confirmed via
     `javap` on the new plugin jar's `GodotExtension.class` (`~/.gradle/caches/modules-2/.../
     godot-gradle-plugin/0.16.3-4.6.3/.../godot-gradle-plugin-0.16.3-4.6.3.jar`) rather than
     guessing from the compiler's bare "unresolved reference" error. The commented-out hierarchy/
     fqName flags renamed too (`registrationFilesLayoutMode`/`registrationNameMode`, now enums) —
     left commented, matching their prior state. `.gdj` generation stays ON (kept as the existing
     safety net, unchanged intent) — the new plugin ALSO always generates its own Entry-metadata
     registration format under `build/` regardless of this flag (a second, parallel mechanism now
     — the old checked-in `src/main/resources/META-INF/services/godot.registration.Entry` legacy
     service file is superseded by it and was removed, per the plugin's own build-log message).
  2. **`Callable.createUnsafe(Object, StringName)` removed entirely** — the reflection-by-name
     signal-binding idiom used at ~48 call sites across 15 files. Replaced by
     `MethodCallable.createUnsafe(Object, String)` (`godot.core.MethodCallable`, confirmed via
     `javap` on `godot-core-library-debug-0.16.3-4.6.3.jar` — the arity-typed `MethodCallable0..16`
     family alongside it suggests a longer-term, more type-safe direction, but the plain
     `createUnsafe` overload still covers this project's existing pattern unchanged) — same
     `(target, methodName)` shape, just a plain `String` instead of `StringName`/
     `StringNames.toGodotName(...)` for the name argument. Every call site fixed mechanically
     (`Callable.createUnsafe(X, StringNames.toGodotName("Y"))` / `Callable.createUnsafe(X, new
     StringName("Y"))` -> `MethodCallable.createUnsafe(X, "Y")`) + `import godot.core.
     MethodCallable;` added to each of the 15 files; the old `Callable` import/type usage
     elsewhere (e.g. `connect(new StringName("signal"), ...)`'s own signal-name argument, `Callable`
     as a variable/return type) is untouched — only the `createUnsafe` factory call itself moved.
  **Verified**: `./gradlew build`+`test` pass; the new binary actually RUNS correctly too, not
  just compiles — `QuitSignalCheck.tscn`, `LaneKitCombineTest.tscn` (82 lanes, PASS), and
  `MultiZoneStreamTest.tscn` (84+12 lanes, 6 vehicles, PASS) all re-run clean under
  `godot.linuxbsd.editor.x86_64.jvm.0.16.3`/Godot 4.6.3. The old 0.15.0 binary is left on disk
  untouched as a fallback, just no longer what any tool script defaults to.
      needed) — an enable-as-needed / Terrain3D-style toggle so only the district(s) you're
      actively debugging are loaded, still deferred (low priority, no immediate need identified).**

  ### Diagnosed + fixed: "character falls through ground in District_industry_5_1 via F1/manual
  ### move" (2026-07-27, user-reported bug — this is what P6.10 was actually needed FOR, not a
  ### generic all-districts preview; investigated instead of building the preview tool first)

  **Method — no viewer tool needed to find this**, a targeted headless diagnostic sufficed:
  new `F1LandingDiagnosticHost`/`F1LandingDiagnostic.tscn` streams District_industry_5_1 in at
  its REAL master-world marker position (`(1260, 0, 756)`, read directly out of
  `World_master.tscn`, not guessed), spawns a `Player` at the exact point
  `DebugHarness.teleportToNextZone()` (F1) lands one (`marker + (0,3,0)`), and (a) raycasts
  straight down a 1000 m column, (b) traces the player's Y position every physics frame for the
  first 3 s (the actual streaming-race window — GEO_ENTER is budget-sliced across frames, so a
  teleport onto a COLD zone could in principle free-fall before geometry finishes entering).

  **Result: the F1 landing point itself is NOT the bug.** Raycast hits real `StaticBody3D`
  collision at Y≈0.046; the player free-falls only ~3 m (the intentional F1 teleport height) and
  settles at Y≈0.037 by t=0.42s, then stays perfectly stable for the full 3 s trace (min-Y = the
  settle height, no dip below it) — no streaming-race free-fall either; this district's own
  content is small enough (6 pieces) that GEO_ENTER finishes in well under one second. Confirmed
  the terrain (`District_industry_5_1_Terrain-col`, 508×508 m, centered at the district's own
  local origin — i.e. covers the FULL zone footprint including the exact F1 landing point) is
  intact and correctly collision-enabled after the P6.7 migration.

  **What WAS actually broken (two real, confirmed, now-fixed bugs) — the likely actual
  explanation for the reported symptom, found by checking what the diagnostic's isolated
  single-zone test couldn't cover: the REAL, full master world's own baked state:**
  1. **`World_master.tscn` (the currently-shipped baked master) had ZERO `SafetyFloor`/`ArtDeck`
     content** (`grep -c "SafetyFloor\|ArtDeck"` → 0) — confirmed **pre-existing** (unmodified by
     git blame/status before this session's fix; not something P6.1-P6.9 introduced). Per
     `towns/build_world.py`'s own docstring this is the documented DEFAULT ("minimal" mode ships
     with no floor) — but it means **any** genuine gap **anywhere** in the 36-district world (a
     district edge, a thin spot, a district that free-falls off its own PLATEAU extraction edge)
     is a literal infinite fall with nothing to catch it — the exact reported symptom, just not
     necessarily originating in District_industry_5_1's own content specifically.
  2. **`World_master.tscn`'s own region marker for District_industry_5_1 still had the STALE
     `route_name = "District_industry_5_1__"`** (the OLD road_graph.py prefix convention) — a
     direct, concrete consequence of P6.7's migration never being followed by a master re-bake.
     Harmless for the falling bug specifically (doesn't affect collision), but means ambient
     traffic for this district was silently broken in the shipped master until fixed (the
     `ZoneManager.findRoute` zone-id-equality pass never matched a `"...__"`-suffixed
     `traffic_route` against the new `zone_id`-tagged lanes).

  **Fix**: `bash tools/build_world.sh --full` — re-ran `towns/build_world.py --full` (now:
  `decks=14 floor=1`, no lanes term any more per P6.8's `backbone_graph()` removal) →
  `export_world.py` → `BakeWorldMaster.tscn`. Verified: `World_master.tscn` now has 45
  SafetyFloor/ArtDeck matches, and District_industry_5_1's `route_name` is now the corrected
  `"District_industry_5_1"` (matches `zone_id` exactly, hits the P6.4 zone-id-equality
  `findRoute` pass). `./gradlew build`+`test` still pass after the rebuild.

  **Not fully closed**: this fix addresses the WORST-CASE consequence (an infinite fall now has a
  floor to catch it, and traffic works) and one confirmed real bug (stale route metadata), but the
  diagnostic could NOT reproduce a genuine hole specifically at the F1 landing point or prove that
  is where the user actually observed the fall — the isolated single-district test that ruled that
  spot out doesn't cover interactions with the OTHER 35 loaded districts, other AI, or a different
  in-district position ("manually move over" could mean anywhere in the 504×504 footprint, not
  just the marker/center). If the fall still reproduces after this fix, the next step is a more
  targeted repro: the exact position/circumstance (walking vs. F1, where in the district) would let
  a follow-up diagnostic raycast/trace that specific spot instead of just the marker position.

  ### Fixed: hang-then-OS-kill on ESC → Quit from `WorldMasterDebug.tscn` (2026-07-27, user-reported)

  **Root cause, confirmed empirically (not assumed):** `PauseMenu`/`MenuManager`/`GameOverMenu`'s
  Quit buttons all called `getTree().quit()` directly. `GameManager`'s audio-stop-on-quit sweep
  (`onCloseRequested` — see the existing "3D audio playback still running when its node is freed
  mid-session leaks at exit" Known Quirk) is wired to the root `Window`'s `close_requested` signal
  — which is specifically the OS/window-manager close request (title-bar X, Alt+F4), **not**
  emitted by a script calling `SceneTree.quit()`. Verified directly with a new throwaway check
  (`QuitSignalCheckHost`/`QuitSignalCheck.tscn`, kept as a permanent regression check — it
  documents a genuinely non-obvious Godot behavior): connect to `close_requested`, call
  `getTree().quit()`, confirm the handler **never fires**. This meant the in-game ESC → Quit path —
  the way a player actually exits, far more often than clicking the OS close button — completely
  skipped the sweep, leaking every still-playing `AudioStreamPlaybackWAV` exactly like the
  original close-requested fix was written to prevent. In a large multi-district scene
  (`WorldMasterDebug.tscn`, many simultaneous AI/vehicles with weapon/engine audio) there's far
  more in-flight audio than any single-district test ever exercises, plausibly explaining a
  hang-long-enough-to-look-like-a-crash at the native/JVM teardown stage.

  **Fix**: `GameManager` gained a public `prepareForQuit()` (the sweep itself, factored out of
  `onCloseRequested()`, which now just delegates to it) that any quit path can call. All three
  Quit buttons (`PauseMenu.onQuitPressed`, `MenuManager.quit`, `GameOverMenu.onQuitPressed`) now
  look up the `GameManager` AutoLoad and call `prepareForQuit()` immediately before
  `getTree().quit()`. `./gradlew build`+`test` pass. **Not independently confirmed to fully
  resolve the hang** (a genuine interactive repro — actually pressing ESC → Quit in a running
  session and observing whether it still hangs — wasn't done, since this session has no
  interactive display access); the empirical signal-firing gap is real and fixed regardless of
  whether it's the *complete* explanation for the reported symptom.

  ### Fixed: Blender crash moving/rotating MANY road_kit_authoring pieces at once (2026-07-27,
  ### user-reported — confirmed as a KNOWN, already-partially-addressed issue, not a new bug)

  `RKA_OT_freeze_for_move`'s own docstring already documents this exact failure mode and why
  time-based debouncing alone can't fully close it: "a depsgraph-driven rebuild, even delayed, can
  still land while Blender's own modal Transform operator is still holding the selection (a slow
  drag, a mid-drag pause)" — a real risk for a deliberate multi-piece realignment (the user's own
  stated use case: trying to move/rotate every piece in `debug_road.blend` at once to align with
  District_industry_5_1's road). The addon already ships a fully safe escape hatch for exactly
  this (`Freeze For Move` sets `rka_live_edit=False` so `live_edit.py`'s depsgraph handler skips
  the piece entirely, no matter how the transform is done or how long it takes) — but only for
  ONE piece at a time, requiring it to be run once per piece for a whole-file rearrange.

  **Fix (a bulk application of the already-verified-safe mechanism, not new reentrancy-handling
  logic — deliberately low-risk):** two new operators, `RKA_OT_freeze_all_for_move` /
  `RKA_OT_unfreeze_all_and_rebuild` (`ops_intersection.py`), each a straight loop over every LOCAL
  `_is_piece_collection()` match setting/clearing `rka_live_edit` (+ rebuilding on unfreeze) —
  idempotent, no pivot/active-object side effects (unlike the single-piece version, there's no one
  correct pivot for an arbitrary multi-piece selection; the user sets Pivot Point themselves).
  Wired into the panel next to the existing single-piece pair. New
  `smoketest_freeze_all.py` (14th smoketest, full suite still passes): builds 2 intersections + 1
  curve-spine segment, freezes all in one call, simulates a real group transform (moves all 3
  pieces' own handles simultaneously — the exact crash scenario) and confirms a depsgraph
  evaluation queues NONE of them for a live-edit rebuild while frozen, then unfreezes + rebuilds
  all and confirms every piece's geometry picked up its new position. `./gradlew build` unaffected
  (Python-only change). **Workflow for the user's actual "align to District_industry_5_1" goal**:
  `Freeze ALL For Move` → select everything → Grab/Rotate/Move → `Unfreeze ALL & Rebuild`.

  ### Fixed: "all vehicles crash at a point rather than follow the Path3D" in District_industry_5_1
  ### (2026-07-27, user-reported — two independent bugs found and fixed)

  **Bug 1 — triplicated junction segment.** `District_industry_5_1.blend` had THREE near-identical
  copies of the same road (`Segment_007`/`008`/`009`, all sharing the exact same `rka_p0` —
  confirmed by direct property comparison, not inferred) stacked on top of each other at the
  `Intersection_4WAY_001` south arm — almost certainly from re-running a build operator without
  clearing the previous result during earlier authoring. Found via `tools/save_lane_kit.py`'s own
  connectivity report: it flagged 10 `[AMBIGUOUS]` 4-way connection-point clusters (every junction
  port had 4 candidate connections instead of 1). Fixed by deleting the two duplicate collections
  (`Segment_008`/`009`, keeping `007`) directly in Blender; re-running `save_lane_kit.py` dropped
  the ambiguous-cluster count from 10 to the 2 that are legitimately expected (a 2-lane→1-lane
  `Transition_001` merge point, not an authoring error).

  **Bug 2 (the real cause of the reported symptom) — every lane, not just junction turn
  connectors, was excluded from ambient-traffic spawn candidacy.** `intersection_kit.py` stamps a
  `turn` letter (`S`/`L`/`R`) on EVERY lane it exports, including plain straight road segments —
  intentional internal steering-behavior metadata (`assert (m["turn"]=="S") == (m["kind"]==
  "through")` is one of its own self-test invariants). But `ZoneManager.isSpawnCandidate`
  treats ANY non-empty `turn` as "this is a junction connector, never spawn ambient traffic here"
  — the old `road_graph.py`/`assemble.py` convention, where only real junction connector markers
  carried a `turn` meta at all. Left as-is, `findRoute()` had **zero** legal spawn candidates in
  this district (confirmed directly: `0` non-turn lanes out of 74), so `vehicleStartPoint(null,
  center, index)` placed **every** ambient vehicle at the exact same point — the zone center —
  where they immediately piled into/on top of each other. That pile-up, not a physics explosion,
  is what "crash at a point" was describing; a new headless diagnostic
  (`TrafficCrashDiagnosticHost`/`TrafficCrashDiagnostic.tscn`, kept for future regression checks)
  confirmed pre-fix: 7 of 8 spawned vehicles ended up clustered within ~5 m of `(1260, ~1, 756)`
  (the marker position), all asleep, zero net movement, for the entire 45 s run.

  **Fix**: `lib/lane_kit.py`'s `combine_pieces` now blanks `turn` on any lane whose piece is NOT a
  junction (`piece_dict.get("arms")` empty — segments/transitions never carry an `arms` list, only
  `export_dict`/junction pieces do), restoring the old convention exactly: only real junction-piece
  lanes are excluded from spawn candidacy. `python3 lib/lane_kit.py` self-tests still pass
  unchanged (none of them touch `turn`); re-running `save_lane_kit.py` produced the same
  40 paired/2 isolated/2 ambiguous connectivity result (this fix doesn't touch connectivity) but
  raised legal spawn candidates from 0 to 26. Incidentally also fixes
  `VehicleAIController.approachingJunction()`'s slowdown cue for segments, which required a blank
  `turn` on the CURRENT lane and could never have fired for a segment before this fix either.
  **Verified end-to-end**: rebuilt the district (`tools/build_piece.sh District_industry_5_1`),
  reran `TrafficCrashDiagnosticHost` — all 8 vehicles now spread across the district footprint
  (X 1373–1490, Z 772–902, genuinely distinct positions) and drive with **zero** speed/teleport
  anomalies over the full 45 s run. `./gradlew build` passes.

  **Open question, not investigated further this session**: whether this pile-up bug was also a
  contributing factor in the separate SIGSEGV-on-quit report from `WorldMasterDebug.tscn` (a pile
  of interpenetrating `RigidBody3D` vehicles being frozen/despawned mid-collision is a plausible
  way to destabilize native physics state) — plausible but unconfirmed; a direct SIGTERM-based quit
  repro attempt this session reproduced only non-fatal shutdown warnings (`BUG: Unreferenced static
  string`, leaked `PagedAllocator`/RID counts — process still exited cleanly), not the user's actual
  SIGSEGV. Recommend the user retest `WorldMasterDebug.tscn`'s quit path now that the pile-up is
  fixed before investing in native-crash-backtrace-symbolication work.

  ### Added: one-click "Export to Godot" button in the road_kit_authoring panel (2026-07-27,
  ### user-requested — was previously a manual two-command terminal sequence)

  New `addons/road_kit_authoring/ops_export.py` (`RKA_OT_export_to_godot`, registered in
  `__init__.py`'s `MODULES`) + a "Godot Export" box at the top of the panel
  (`panel.py`): one button that saves the current file, regenerates the combined lanekit sidecar
  (`tools/save_lane_kit.py`), then runs the full export/bake/navmesh/binary-convert pipeline
  (`tools/build_piece.sh <stem>`, stem form) — the exact two-command sequence used by hand earlier
  this session to re-export `District_industry_5_1` after the "vehicles crash at a point" fix.
  Only enabled (`poll()`) for a file saved as `District_<theme>_<gx>_<gy>.blend` — that's the only
  form `build_piece.sh` bakes without also trying to regenerate the .blend from a town config; the
  panel shows a hint to rename/save when it isn't.

  Both stages run as real subprocesses (`blender --background ... save_lane_kit.py`, then
  `bash build_piece.sh <stem>`) driven by a **modal timer**, not a blocking `execute()` — a full
  export/bake takes ~20-40s and would otherwise freeze the whole Blender UI. Output streams live to
  the System Console via a background reader thread + `queue.Queue` drain in `modal()` — deliberately
  NOT a plain `readline()` loop in `modal()` itself, which would block Blender's main thread whenever
  the subprocess pauses between lines (caught and fixed before shipping, not a released bug).

  **Verified**: all 15 `smoketest_*.py` still pass after the registration change (import/register/
  unregister wiring is sound). Modal-timer/window-manager plumbing can't be exercised headlessly (no
  event loop in `--background` mode to pump `TIMER` events, and `bpy.types.Operator` subclasses can't
  be freely instantiated outside Blender's operator system to unit-test directly) — instead, the
  actual custom risk surface (the two-stage subprocess sequence + reader-thread/queue drain pattern,
  copied verbatim into a standalone harness outside `bpy`) was run end-to-end against the real
  `District_industry_5_1.blend`: both stages exited 0, no hang, no lost output lines. This run also
  served as a genuine re-export — the `.blend` had been resaved (further manual road edits) after the
  session's earlier export, so this confirms the shipped `District_industry_5_1.tscn` now reflects
  those edits too; connectivity unchanged (still 40 paired/2 isolated/2 legitimate ambiguous
  clusters) and a `TrafficCrashDiagnosticHost` rerun still shows 0 anomalies with vehicles actively
  driving throughout. `./gradlew build` unaffected (Python-only addon change).

  ### Fixed: "Export to Godot" button crashed on click — `AttributeError: 'WindowManager' object
  ### has no attribute 'modal_handlers_add'` (2026-07-27, user-reported, first real click of the
  ### button)

  Plain typo in `ops_export.py`'s `invoke()`: `wm.modal_handlers_add(self)` should be
  `wm.modal_handler_add(self)` (singular — confirmed correct spelling already used elsewhere in
  the addon, `ops_placement.py`). This is exactly the code path flagged at the time as untestable
  headlessly ("Modal-timer/window-manager plumbing can't be exercised headlessly... `bpy.types.
  Operator` subclasses can't be freely instantiated outside Blender's operator system") — the
  standalone logic harness used instead covered the subprocess/queue-drain risk surface but never
  actually called this line, so the typo shipped. Fixed; grepped the rest of the addon for the
  same typo (none found — `ops_placement.py` already had the correct spelling). User's install is
  a symlink into the repo (`README.md`'s documented dev-install convention), so the fix takes
  effect immediately, no reinstall/reload needed. Full 16-file smoketest suite still passes
  (registration unaffected, as expected for a one-line runtime typo).

  ### Fixed: vehicles sinking below the visual road, still following their PathLaneRoute
  ### (2026-07-27, user-reported — "vehicle drop beneath road mesh then still follow path3d
  ### though but in ground instead of at road mesh")

  **Root cause, confirmed by reading the actual build code (not assumed): the drivable pavement
  itself had ZERO collision on a segment or lane transition — only the two curb EDGES did.**
  `_populate_segment_mesh_gn`/`_populate_transition_visuals` (`ops_segment.py`) called
  `kit_common.colonly_swept` for each curb LINE only (`curb_thickness/2` wide — a thin strip
  right at each road edge); nothing covered the open pavement between them. A vehicle driving the
  middle of a lane (the normal case — cars don't hug the curb) had literally nothing under it and
  fell straight through to whatever's below (terrain, or nothing), while its `PathLaneRoute`
  correctly guided its XZ position throughout — a pure geometric path, unaffected by missing
  physics collision, exactly matching the reported "still follows the Path3D, but sunk into the
  ground." Junction pads were never affected (P6.2's `colonly_polygon` already covers a whole
  intersection's footprint, curbs included) — this was segments/transitions only, i.e. most of a
  district's actual road length. The legacy point-segment path (`_populate_segment_mesh`, pre-GN)
  had it even worse — no collision at all, not even on the curb edges.

  **Fix**: new `kit_common.colonly_swept_between(name, left_pts, right_pts, coll, z0, z1)` builds
  a pavement collision slab from the SAME left/right curb-line points the visual pavement/curbs
  already use — pointwise midpoint centerline, pointwise half-width = half the L/R separation, so
  it naturally follows a tapering width (a lane-count transition) with zero extra per-caller math.
  Wired into all three build paths (`_populate_segment_mesh_gn`, `_populate_transition_visuals`,
  and the legacy `_populate_segment_mesh`, which gets curb collision for the first time too, not
  just pavement). New `pave_*` prefix added to `clear_generated_mesh_objects`'s cleanup sweep (a
  genuinely new prefix, unlike curb/pad's existing-name reuse) so live-edit rebuilds don't orphan
  or duplicate it. Colonly proxies stay excluded from `visual_objs`/join/export, same convention
  as every other `-colonly` proxy.

  **Verified**: `smoketest_collision.py` updated (was hardcoding "2 curb colonlies" per
  segment/transition — now asserts 3, incl. a `pave_*` one with real geometry, plus a
  rebuild-doesn't-duplicate check); new `smoketest_curb_style_panel.py` also confirms the pavement
  colonly survives a curb-style change untouched (curb style and pavement collision are
  independent). Full 15-file smoketest suite + both `lib/*.py` self-tests pass.
  **Retroactively applied to `District_industry_5_1`**: a piece's mesh is baked into the `.blend`
  at build/rebuild time, so `save_lane_kit.py`/`build_piece.sh` alone would only re-export/bake
  whatever geometry was ALREADY there — the fix needed the district's existing pieces to actually
  regenerate. New `tools/rebuild_all_pieces.py` (headless, reusable for any future addon
  geometry-generation change) opens a district/overlay, calls
  `ops_intersection._rebuild_piece_in_place` on every local piece collection (the same dispatcher
  a single manual "Rebuild From Handles" click already uses), and saves — ran it against
  `District_industry_5_1.blend` (all 16 pieces rebuilt cleanly), then re-ran `save_lane_kit.py`
  (connectivity unchanged: still 40 paired/2 isolated/2 legitimate ambiguous) and
  `build_piece.sh District_industry_5_1` (74 pathlanes baked). Directly inspected the baked
  `District_industry_5_1.tscn`: 11 new `pave_*` `StaticBody3D`/`CollisionShape3D` nodes (one per
  segment/transition), confirming the collision actually reached the shipped scene, not just the
  `.blend`. `TrafficCrashDiagnosticHost` rerun: 8 vehicles, 0 speed/teleport anomalies over the
  full run. `./gradlew build` unaffected (Python-only fix; no Java touched).

  ### Fixed: curb enable/disable "not working through panel" (2026-07-27, user-reported)

  **Root cause**: there was no way to change curb style on an ALREADY-BUILT segment/transition via
  the Sidebar panel at all — `curb_l_style`/`curb_r_style` only ever appeared on the build
  operator's own F9 "Adjust Last Operation" popup, which Blender itself silently stops applying
  the moment any other action runs (standard Blender behavior, not a bug in this addon) — so
  clicking anything else first (very easy to do) made curb style look broken/unresponsive from
  then on with no persistent control to fall back to.

  **Fix**: new `ops_segment.RKA_OT_set_curb_style` (`side`: L/R/BOTH, `style`: NONE/BOX/GUTTER/
  ASSET) sets `rka_curb_l_style`/`rka_curb_r_style` directly on the target piece and rebuilds in
  place via the existing `_rebuild_piece_in_place` dispatcher — works on whatever piece is
  currently active/selected, independent of build history. Wired into `panel.py` as a persistent
  "Curb Style" button row (Left/Right × None/Box/Gutter/Asset, current style shown depressed) for
  both GN segments and lane transitions — the legacy point-segment path is deliberately excluded
  (`poll()` requires `rka_curve_object`) since it never supported per-side or ASSET curb styles at
  all. `ASSET`'s `asset_collection` field is set via the operator's own F9 redo panel immediately
  after clicking (same established convention the build operators already use for this exact
  field, not a new UX pattern).

  **Verified**: new `smoketest_curb_style_panel.py` — poll() correctly fails/succeeds; disabling
  the Left curb removes exactly that object (Right curb + pavement collision untouched); one
  "BOTH" call re-enabling both sides as GUTTER produces no orphaned/duplicate curb or pavement
  colonly objects; the same operator works on a lane transition too. Full 15-file smoketest suite
  passes. `./gradlew build` unaffected.

  **Design question answered, not built (already covered by existing code): does an asymmetric
  curb-kit mesh (e.g. `Kit_CurbSideCityGutter_Attach_Left_Side`, authored to attach on the left
  only) need a two-level gutter+curb split, or per-side mirroring in the addon?** Neither — the
  existing ASSET curb path already handles exactly this. `kit_common.curb_asset_row`'s
  `rot_offset_deg` param (exposed as `curb_asset_rot_offset_r`, default 180°) spins every R-side
  instance an extra 180° around its own Z so an asymmetric piece's authored "front" face keeps
  facing away from the road on BOTH sides, even though both curb lines are sampled in the same
  spine direction. So: set `curb_l_style='ASSET'` and `curb_r_style='ASSET'` with the SAME
  `curb_asset_collection` ('Kit_CurbSideCityGutter_Attach_Left_Side') on both sides — the addon
  automatically flips the right-side row to face correctly, no separate right-side asset, no
  two-level split, no code change needed. (This is unrelated to `colonly_swept_between`'s
  independent pavement collision above — an ASSET-style curb gets no visual collision of its own
  either way, same as BOX/GUTTER; only the pavement between the two curb lines does.)

  ### Fixed: "lane map" (Lane Map Override) also "not work[ing] with same reason as curb in
  ### panel" (2026-07-27, user-reported — same F9-only bug pattern as curb style, one commit later)

  Same root cause as the curb-style fix above, confirmed by re-reading the panel's own label text
  before touching anything: `RKA_OT_build_intersection`'s `lane_map` field (mini-syntax
  `'From>To:in-out,in-out; ...'`, parsed by `parse_lane_map`) only ever appeared on Blender's F9
  "Adjust Last Operation" panel — the panel's own hint text literally said "F9 ... to tweak
  preset/radius/lanes/lane_map/traffic side," i.e. it openly documented the limitation the user
  hit. The underlying rebuild path (`rebuild_intersection_in_place`) already read
  `custom_props.read_lane_map_override(coll)` fresh every rebuild — so the DATA layer supported
  changing it after the fact (hand-edit the `rka_lane_map` Custom Property's raw nested dict via
  Blender's own Object/Collection Properties panel), just not a friendly UI path to do so.

  **Fix**: new `ops_intersection.RKA_OT_set_lane_map` — an `invoke_props_dialog` popup (immune to
  the F9 staleness problem since it opens fresh on every click, not chained to build history) with
  a text field pre-filled from the intersection's current override (if any), using the exact same
  mini-syntax/`parse_lane_map` validation the build operator uses. On OK: parses the text (a
  malformed clause reports an error and changes NOTHING — no partial/corrupt state), writes
  `rka_lane_map` via `custom_props.lane_map_to_custom`, and rebuilds in place; blank text clears
  the override entirely (back to default i→i pairing). Wired into `panel.py`'s intersection
  section as a persistent "Set/Edit Lane Map Override" button; the stale F9-only hint text was
  also corrected.

  **Verified**: new `smoketest_lane_map_panel.py` — poll() fails/succeeds correctly; a malformed
  string is rejected via the expected `bpy.ops` `RuntimeError` (an ERROR-reported CANCELLED)
  with `rka_lane_map` left completely unset, not partially written; a valid override round-trips
  exactly through `read_lane_map_override`; **applying a SECOND, different override to the SAME
  already-built intersection works and fully replaces the first** (the actual bug being fixed —
  not build-time-only); clearing restores the exact original default `lanecl_*` set. Full 16-file
  smoketest suite + both `lib/*.py` self-tests pass. `./gradlew build` unaffected (Python-only).

  ### Fixed: junction pad visual mesh floating ~15-20cm below its own collision (2026-07-27,
  ### user-reported — "collision map seem far from close to real mesh, vehicle and character seem
  ### to be some distance from the road mesh")

  **Root cause, confirmed by direct isolated measurement, not assumed:** `GN_JunctionPad`'s `Fill
  Curve` node (N-gons mode) silently flattens every evaluated vertex to world **Z=0**, regardless
  of the input curve's actual height. Proven in isolation — a plain closed curve with every point
  at Z=0.15 fed through Fill Curve ALONE (no Fillet Curve involved) still evaluates to Z=0.0
  everywhere. This sank the visual pad's baked/exported mesh to Z=0 while `colonly_polygon`'s
  collision proxy (built independently straight from the same boundary points, never routed
  through Fill Curve) correctly sat at `lane_surface_z` (~0.15m default) — a genuine ~15-20cm
  vertical gap between the rendered road surface and where vehicles/characters actually rest,
  exactly the reported symptom. (Segments/transitions were unaffected — their pavement uses `Curve
  to Mesh`, a different node with different semantics, not `Fill Curve`.)

  **Fix**: `make_junction_pad_group` (`kit_common.py`) now restores Z after Fill Curve
  unconditionally — `Separate XYZ` (X/Y passthrough) → `Combine XYZ` (Z from a new `Pad Z` group
  input) → `Set Position` (Position, not Offset — REPLACES the wrong Z rather than stacking onto
  it). Since every junction pad is flat by construction (`_populate_intersection_mesh.to3r` uses
  one constant `z` for the whole boundary), `junction_pad()` feeds `Pad Z` from
  `boundary_pts_radius[0][2]` — correct regardless of whatever height Fill Curve's internals
  happen to compute.

  **Gotcha hit while re-applying to the real district (worth remembering for any future GN
  node-group structural change): `bpy.data.node_groups.get("GN_JunctionPad")`'s get-or-create
  pattern means an EXISTING `.blend` already has the OLD (buggy) node group data baked in — the
  Python code change alone does NOT retroactively fix it.** Re-running `rebuild_all_pieces.py`
  against `District_industry_5_1.blend` without first deleting the stale node group would have
  silently kept reusing the old graph forever. Fix applied as an explicit extra step:
  `bpy.data.node_groups.remove(bpy.data.node_groups.get("GN_JunctionPad"), do_unlink=True)` before
  rebuilding, so `make_junction_pad_group()`'s `.get()` returns `None` and creates a fresh,
  corrected group. No general-purpose "purge stale node groups" tooling exists yet — this was a
  one-off manual step; add one if a future structural GN change needs the same treatment more than
  once.

  **Verified**: `smoketest_collision.py` gained a permanent regression check — evaluates the pad's
  real (modifier-applied) mesh and asserts every vertex sits at exactly `lane_surface_z`, and that
  the colonly's Z range straddles that height (was previously unchecked — the earlier `smoketest_
  collision.py` from P6.2 only verified vertex/polygon COUNTS, never actual world height, which is
  how this shipped unnoticed). Isolated fix verified in a clean Blender session first (evaluated
  pad mesh: `[0.0]` before fix → `[0.15]` after). Re-applied to the real district (purge stale node
  group + `rebuild_all_pieces.py` + `save_lane_kit.py` + `build_piece.sh`, connectivity unchanged:
  40 paired/2 isolated/2 legitimate ambiguous) and confirmed **in the actual shipped Godot scene**
  via a throwaway headless GDScript: the pad visual's AABB (Y≈4.6933) now sits exactly 5cm below
  its collision proxy's top surface (Y≈4.7433 = 4.6933+0.05, matching `colonly_polygon`'s
  documented `z1=0.05` margin exactly) instead of the previous ~15-20cm gap. `TrafficCrashDiagnosticHost`
  rerun: 0 anomalies over the full 45s run. `./gradlew build` passes (Python-only fix).

  **On the user's direct question, "is convex shape more accurate?" — no, and switching would make
  it WORSE, not better, for this specific proxy.** The reported gap was a Z-coordinate bug, not a
  shape-precision issue — `ConcavePolygonShape3D` (the trimesh Godot's importer already builds from
  every `-colonly` mesh here) is the CORRECT choice for this static geometry: Jolt (this project's
  physics backend) handles static concave trimesh fine, and a junction pad's real footprint is
  inherently non-convex (a plus/cross/T shape with arm-tail protrusions and open space between
  arms). A convex hull of that shape would bulge outward across every concave notch between arms,
  creating solid phantom collision in open space that should be walkable/driveable-around — a
  worse mismatch than the coarse-but-topologically-correct fan-triangulated polygon already in use
  (which only differs from the visual at ROUNDED corners, per `colonly_polygon`'s own documented
  trade-off, not across whole regions). Convex hulls are the right call for small DYNAMIC
  (RigidBody) parts, not large static level geometry like this.

  **On "AI vehicle eventually falls off road, reaches ground, collides again to a point" — very
  likely a direct downstream consequence of the Z-mismatch bug above, not investigated as a
  fully separate issue this session.** A ~15-20cm vertical step exactly where a segment's pavement
  collision meets an intersection's pad collision (or vice versa) is exactly the kind of seam
  discontinuity that could catch/bounce a vehicle off its intended path at a junction crossing,
  eventually landing it off the collision footprint entirely and onto the terrain below (the
  previously-fixed "vehicles crash at a point" pile-up bug is unrelated — that was a spawn-position
  bug, already confirmed fixed and unaffected by anything in this session). Recommend the user
  retest with the freshly rebaked `District_industry_5_1` now that the seam height is corrected; if
  vehicles still eventually leave the road, the next diagnostic step is capturing the EXACT
  position/circumstance of a drop-off (mirroring the `F1LandingDiagnosticHost` precedent) rather
  than guessing further — a systematic terrain-slope funneling effect (multiple already-fallen cars
  physically rolling toward the same low point) is also a plausible, bug-free explanation for
  "collide again to a point" specifically, and wouldn't need a code fix at all.

  ### Follow-up (2026-07-27, same day): "vehicle are all on ground still... even in beginning" —
  ### the Z-fix above did NOT fully explain it; built a much more precise diagnostic, ruled out
  ### road-collision COVERAGE as the cause, still open

  User reported vehicles still ending up on the ground after the pad Z-height fix. Extended
  `TrafficCrashDiagnosticHost` with a downward raycast at every 0.5s sample (`isSupported`: is a
  vehicle within 2m of whatever a raycast finds under it) and a "DEPARTED SURFACE"/"(re)LANDED"
  transition log — this is a genuinely new capability, not present when the earlier "0 anomalies"
  verification ran (that check only looked at speed/teleport spikes, never height-vs-collision).

  **Result: vehicles start correctly supported (confirmed at t=0.5s, resting right at the pad/
  pavement collision), then the large majority depart the surface within the first few seconds of
  driving and several settle permanently 2-7m below the intended road height** — this is real,
  confirmed, and matches the report closely (not a rare edge case).

  Two hypotheses tested, in order:
  1. **Lateral drift off the curb edge** (curb walls are only ~0.15m tall, trivially hoppable).
     Added `kit_common.SHOULDER_MARGIN` (2.0m, collision-only, both `colonly_swept_between` and
     `colonly_polygon`) as an invisible shoulder. **Did not reduce departures** — same vehicles,
     same approximate times, same approximate positions.
  2. **A seam gap between adjacent pieces** (each piece's collision starts/ends exactly at its own
     p0/p1, meeting the next piece's collision at an exact shared boundary — two disjoint static
     meshes touching exactly is a classic "fell through the crack" scenario). A per-tick trace
     (not just 0.5s samples) of one departure showed a clean sign: velocity.y transitions smoothly
     from ~0 to increasingly negative matching free-fall acceleration exactly, while the vehicle is
     otherwise moving steadily forward (accelerating out of a stop, not cornering) — consistent
     with driving through a lengthwise gap. Added `kit_common.SEAM_OVERLAP` (1.5m, extends each
     piece's collision past its own endpoints along its own tangent so adjacent pieces' collision
     volumes overlap instead of exactly touching). **Also did not reduce departures** — verified
     the fix genuinely reached the shipped scene first (direct Blender-side measurement: a test
     segment's collision ring separation grew from a 40.0m spine to a 43.0m collision span, exactly
     40 + 1.5 + 1.5; lateral width grew from 10.0m to 14.0m, exactly +2×2.0 — both confirmed
     correctly baked and exported, ruling out a "fix didn't actually apply" explanation), then
     re-ran the diagnostic and got near-bit-identical departure times/positions to the run without
     either fix.

  **This directly rules out road-COLLISION-COVERAGE (width or length) as the cause of the
  remaining departures** — the collision at the exact point in question is empirically confirmed
  generous (14m wide, well past any plausible steering drift) and long (overlapping seams), yet
  vehicles still cleanly lose support there. `Vehicle.tscn` was also checked directly: `continuous_cd
  = true` already on (rules out simple high-speed CCD tunneling through a thin static mesh) and its
  own collision shape is a proper `ConvexPolygonShape3D`, nothing obviously wrong on that side
  either — though the vehicle's own physics/collision behavior wasn't investigated as deeply as the
  road side was this session.

  **One separate, distinct, CONFIRMED contributing cause found and left as-is (a data/authoring
  issue, not an addon bug):** `District_industry_5_1` has one genuine dangling dead-end —
  `Intersection_4WAY_001`'s E arm, flagged `[ISOLATED]` by `save_lane_kit.py`'s own connectivity
  lint (no connecting segment was ever built there). Three separate departure events across two
  diagnostic runs landed within 6.8-8.1m of that exact point (world ~1429, 826) — a car reaching
  the physical end of a dead-end arm's pavement has nothing beyond it (arm tail-caps are
  deliberately curb-free, since that's normally where a connecting road continues) and simply
  drives off the edge. Not fixed here — capping or extending that arm is an authoring decision for
  whoever built this district, not something to silently change; flagged for the user's judgement.
  This does NOT explain the other, geographically scattered departures.

  **Status at the time: genuinely still open** (see the next entry below — user pointed at a
  specific piece pair and found the real remaining cause of the EARLY departures the same day).

  ### Follow-up (2026-07-27, same day): user pinpointed the actual culprit —
  ### `Segment_012`/`Intersection_4WAY_003` — a corrupted spine, not lane markings

  User's hypothesis was that yellow/white lane-marking geometry was contributing extra collision
  height ("spike/bump" causing trucks to slide). Checked directly: `nodes/use_node_type_suffixes=
  true` in the district's `.gltf.import` confirms Godot's suffix-based collision convention is
  active (only `-colonly`/`-col`/`-convcolonly`-suffixed meshes become collision — `mark_*`
  objects carry no such suffix and never did), and neither `colonly_swept_between` nor
  `colonly_polygon` reads marking geometry at all — both are built directly from curb-line/
  boundary points, independent of `_populate_lane_markings`. So the marking hypothesis itself
  doesn't hold up — but the underlying report (a real physical spike at that exact piece) was
  correct, and the actual cause was found directly: **`Segment_012`'s own spine (`spine_
  Segment_012`, a 12-point curve) had 5 interior control points with wildly corrupted local Z**
  (-3.2 to -3.7, vs. the correct flat 0.15 baseline every other point on this segment uses, and
  matching what both its own endpoints (`rka_p0`/`rka_p1`, both Z=4.693) and the connecting
  `Intersection_4WAY_003` (arms + pad all cleanly flat at Z=4.693) agree the road should be) — a
  ~4.5m elevation rollercoaster within one ~80m stretch, almost certainly from an old manual
  live-edit drag that moved some spine points vertically by accident. Both the visual pavement AND
  its collision faithfully reproduced this bad shape (not a mismatch bug like the pad Fill-Curve
  issue earlier in this section — genuinely bad source geometry, correctly rendered/collided
  either way), which is exactly what "vehicle hits a spike/bump and slides" looks like when driven
  over. `Intersection_4WAY_003` itself was checked and is clean (currently a 3-arm junction despite
  the "4WAY" collection name — not a bug, just a stale preset name after an arm was removed).

  **Fix**: reset the 5 corrupted points' local Z back to 0.15 (matching every other point on the
  same segment) and reran `_rebuild_piece_in_place` to regenerate pavement/curb/collision from the
  corrected spine — an authoring-data fix, not a code fix (no `.py` files changed for this entry).
  Backed up the `.blend` first. Re-ran the full pipeline (`save_lane_kit.py` → `build_piece.sh`):
  connectivity **improved as a side effect** (2 isolated → 0 isolated, 74 → 66 lanes — the
  corrupted spine's self-intersecting shape was almost certainly generating spurious/duplicate
  lane movement data near the spike, cleaned up for free by fixing the geometry it derives from).

  **Verified with `TrafficCrashDiagnosticHost`'s departure tracking (see previous entry): the first
  "DEPARTED SURFACE" event moved from t≈2.5s (every prior run, consistently) to t≈35s** — this
  segment/intersection pair was the dominant cause of the EARLY, concentrated wave of failures
  multiple vehicles hit almost immediately. Total departure count over the full 45s run is
  similar (~20-25), confirming this was NOT the cause of the scattered LATER departures already
  flagged as unexplained in the previous entry (road-collision coverage ruled out there; vehicle-
  side physics/AI still the leading unexplored hypothesis for those). Full 16-file smoketest suite
  + `./gradlew build` pass (no code touched, geometry-only fix).

  **Takeaway for any future "spike/bump at a specific piece" report**: check the piece's own spine
  control points' Z values FIRST (`spine_<name>.data.splines[0].points[i].co.z`, compare against
  neighbors and against `rka_p0`/`rka_p1`) before suspecting markings, curb style, or collision
  code — a hand-dragged live-edit spine point is a much more likely, much more localized cause of
  a single-piece anomaly than anything in the shared generation code (which would show up
  everywhere, not at one named piece).

  ### Follow-up (2026-07-27, later same day): user reverted the slope fix, found the REAL
  ### corner-wedge cause (a naive tangent, not markings), cut departures the rest of the way with
  ### slower AI speed, and fixed a real "floating" collision gap

  1. **Reverted the Segment_012 spine flatten.** User pushed back: is a vertical drop not just
     legitimate ground-slope-following (which they plan to lean on more heavily later)? Re-examined
     the actual numbers — the profile (0.15→-3.2→-3.57→-3.74→-2.78→0.82→0.15) is a steep-but-coherent
     dip-and-rise (~23% grade), not obviously random jitter, and there wasn't strong evidence either
     way. Restored the pre-fix `.blend` from the session's own backup (taken before the flatten) and
     re-exported. Elevation IS fully supported end-to-end now (visual AND collision both correctly
     follow arbitrary spine Z, confirmed throughout this section) — nothing about that capability
     needed the flatten in the first place.
  2. **The screenshotted corner "wedge" collision (user's AABB hypothesis, again not the mechanism
     — see the note in the pad-height entry above for why AABB isn't used here at all):** the REAL
     cause was `colonly_swept`'s per-vertex lateral direction — a naive central-difference chord
     (`cpts[i+1] - cpts[i-1]`) that doesn't match either adjacent edge's true perpendicular at a
     SHARP corner, unlike the visual mesh (Blender's native Curve-to-Mesh/Fillet Curve, which miters
     corners correctly). Replaced with a proper 2D polyline miter join (bisect each point's own
     incoming/outgoing edge normals, scale by `1/cos(half the turn angle)`, capped at
     `miter_limit=4` to avoid a runaway spike at a near-reversal). A width-consistency scan across
     every `curb_`/`pad_`/`pave_` colonly in the district (ring-to-ring width ratio, flags >2x
     jumps) found zero anomalies after the fix — inconclusive on its own since the SAME scan also
     found zero on the pre-fix backup, but the miter join is strictly more geometrically correct
     regardless (a central-difference chord is never more accurate than a proper per-edge miter for
     offsetting a polyline), so kept as a real improvement either way.
  3. **AI speed reduction was the single biggest win.** `VehicleAIController.cruiseSpeed` 11→7 m/s
     (~40→~25 km/h, user-requested experiment). Departures dropped from dozens starting at t≈2.5s
     (every prior run) to ONE starting at t≈37-41.5s. Per-road-type (highway faster) and
     per-archetype (a future racing AI faster still) speed variation is a planned follow-up, noted
     in the code, not built — today's change is a single shared global default.
  4. **A real, confirmed "floating" bug, separate from the wedge/AABB question**: user reported
     characters/vehicles visibly floating above the road rather than standing on it. Root cause:
     `colonly_swept_between`/`colonly_polygon` both defaulted `z1=0.05` — the collision TOP sat 5cm
     above the true road height on every single piece in the district, everywhere, not just at
     corners. Changed default to `z1=0.0` on both (collision top now exactly at road height,
     verified directly: pad visual Z 4.6933 == pad colonly top Z 4.6933, pavement colonly top Z
     4.6933 too). No real tradeoff — the coarse-polygon-vs-fillet concern `colonly_polygon`'s `z1`
     margin used to hedge against is about XY shape at corners, not Z.
  5. **`SHOULDER_MARGIN`/`SEAM_OVERLAP` shrunk 2.0/1.5 → 0.4/0.5m.** With the speed fix doing most of
     the real work, the wide margin was mostly just extra invisible collision floating past the
     visible curb with little safety benefit left to justify it — and was itself visible/reported
     (collision sitting perceptibly off the curb line). Kept small and nonzero, not zero, for a
     little remaining forgiveness on ordinary wheel/foot clipping.

  **Verified**: full 16-file smoketest suite + `./gradlew build` pass after each change. Full
  pipeline re-run (purge-not-needed since none of these are cached GN node groups — plain Python
  functions pick up changes on next rebuild automatically) — connectivity unchanged (40 paired/0
  isolated/2 legitimate ambiguous once the dangling-arm segment was later reconnected upstream of
  this entry... actually unchanged at 0 isolated either way this pass). `TrafficCrashDiagnosticHost`
  final state: 1 departure in the full 45s run (down from dozens), 0 anomalies.

  ### Follow-up (2026-07-27, later same day): root-caused and closed — replaced every hand-rolled
  ### road-collision approximation with an EXACT copy of the evaluated visual mesh

  Two screenshots (Blender viewport, wireframe overlay) made the actual mechanism obvious: at
  intersection corners the collision visibly diverged from the curved/filleted visual pad, and
  more broadly the collision read as consistently oversized versus the road mesh, unlike
  `District_industry_5_1_Landmark_000_h116m`'s collision (built by the pre-existing
  `colonly_mesh()`, which copies a real building's exact mesh — "very accurate" per the user's own
  comparison). Root cause, once framed that way: `colonly_polygon` (pad) deliberately used the RAW,
  un-filleted boundary points ("ignoring each point's fillet radius... a slightly-squared-off
  coarse footprint") and `colonly_swept`/`colonly_swept_between` (curbs, pavement) were a
  hand-rolled hand-normal sweep — both were APPROXIMATIONS of the real visual shape by original
  design (P6.2), never an exact match, unlike `colonly_mesh()`'s already-accurate approach for
  real-world (PLATEAU) geometry.

  **Fix**: new `kit_common.colonly_mesh_evaluated(visual, coll, name=None)` — evaluates the visual
  object through the depsgraph (`bpy.data.meshes.new_from_object`, the exact same bake glTF export
  itself performs) and copies THAT mesh as the collision proxy, byte-for-byte matching whatever the
  viewer actually sees (fillets, tapers, bends, all of it) with zero hand-rolled geometry math to
  keep in sync. Replaces `colonly_polygon` for the pad (`ops_intersection.py`,
  `colonly_mesh_evaluated(pad, coll)`) and `colonly_swept`/`colonly_swept_between` for every curb
  and pavement call site in both the GN segment and lane-transition paths (`ops_segment.py`).
  `curb_loop()`/`junction_pad()` already name their own objects `curb_.../pad_...`, so those calls
  need no override; the pavement case passes `name="pave_<piece>"` explicitly since the source
  object is `spine_<piece>` (never deleted/recreated by a rebuild) and the existing
  `clear_generated_mesh_objects` cleanup sweep keys off the `pave_` prefix, not `spine_`.

  **One real architectural difference worth knowing**: `GN_RoadProfile` (pavement) already extrudes
  a genuine 0.4m-thick deck internally, so its collision copy is solid, same as before. `GN_
  JunctionPad` (the pad) does NOT extrude at all — it's a flat, zero-thickness "Fill Curve" N-gon,
  so its collision copy is now ALSO genuinely flat (a single-layer mesh, not a z0/z1-extruded slab
  like the old `colonly_polygon` produced). This is a real, if minor, trade-off: a flat single-sided
  trimesh is normal and sufficient for a static ground/floor collider (very common in general), but
  it is a slightly higher theoretical tunneling risk for something moving fast straight down onto
  it than a slab with real depth would be. Not treated as a bug (nothing in this session's testing
  demonstrated a problem, `Vehicle.tscn` already has `continuous_cd = true`), documented rather than
  "fixed" by bolting artificial thickness onto a mesh that's authored to be flat — revisit only if
  a real tunneling case ever surfaces.

  **Verified**: vertex-count-exact match confirmed directly across all 5 real intersections in
  `District_industry_5_1` (pad visual vs. pad colonly vertex counts identical, e.g. 70==70, 104==
  104 — not just "close," structurally IDENTICAL geometry). `smoketest_collision.py`'s pad-height
  regression check updated (the pad colonly is now flat with no z0/z1 range to straddle — checks a
  1mm epsilon against the exact height instead). Full 16-file smoketest suite + `./gradlew build`
  pass. Full pipeline re-run on the real district; connectivity unchanged (40 paired/0 isolated/2
  legitimate ambiguous). `TrafficCrashDiagnosticHost`: **zero departures over the full 45s run** —
  the best result of this entire investigation, combining every fix in this section (pad Z-height,
  slower AI cruise speed, exact-mesh collision, corrected z1=0 flush height, shrunk shoulder
  margin/seam overlap).

  ### Tooling: centralized the Godot binary path (2026-07-27, user-reported)

  The Godot binary was renamed/replaced (`godot.linuxbsd.editor.x86_64.jvm.0.16.3` →
  `godot.linuxbsd.editor.x86_64.jvm`, no version suffix) outside any session activity, breaking 4
  separate hardcoded `GODOT="${GODOT:-/data/.../jvm.0.16.3}"` lines across `tools/build_piece.sh`/
  `build_overlay.sh`/`build_world.sh`/`build_intersection_piece.sh`. New `tools/env.sh` (sourced by
  all 4 via `source "$BP/tools/env.sh"` right after `BP` is computed) is now the single place that
  default lives — still honors an environment override (`GODOT=/other/path tools/build_piece.sh`).
  Verified: all 4 scripts pass `bash -n`; `build_piece.sh District_industry_5_1` runs end-to-end
  through the shared env with no hardcoded path left in any script. See `reference_godot_jvm_binary`
  memory (updated) for the current path.

**Why `Lane.entryPoint()` is a new interface method, not just `pointAtLength(0)`:**
`VehicleRoute.entryPoint()` caches the RAW `startPoint()` (unsmoothed/unoffset); `pointAtLength(0)`
returns the smoothed+lane-offset baked path's start instead — a different value whenever
`laneOffset != 0` — and `ensureBaked()`'s cache-validity check still re-walks `waypoints()`
(JVM-bridge marker-child calls) on every invocation just to check `pts.size()`, even when the bake
itself is skipped. `entryPoint()` exists specifically so `ZoneManager.findRoute`'s
per-candidate prefix scan (hundreds of lanes) doesn't pay that walk; reusing `pointAtLength(0)`
instead would silently reintroduce the exact hitch the caching was written to avoid.

**Track B (deferred, NOT part of P6 — its own future initiative):** Blender as a general
game-level editor (roads + zones + buildings etc., one or more plugins); `WorldBaker`'s
name-prefix dispatch (`zone_`/`spawn_`/`water_`/`instance_`/`mmesh_`, not just roads) replaced by
custom-properties/glTF-extras throughout; districts restructured as collections (possibly one
shared file, or a lighter shared "connections layer" for seams/ground/overlay attach points
specifically — true global editability needs this, since Blender library links are read-only by
construction, not a missing flag) instead of one `.blend` per district, for easier world-level
counting/design; a lightweight test/dev Godot scene (a small district/zone subset) to replace
loading the full map for iteration.

### Follow-up (2026-07-28): panel usability gaps — piece selection couldn't bootstrap from
### nothing, and material was never actually changeable at all

Two more user-reported panel usability gaps, both fixed with the same "persistent panel control,
not a build-time-only F9 field" shape as the curb-style/lane-map fixes earlier in this doc:

- **`RKA_OT_select_piece`'s own `poll()` requires something piece-related ALREADY active/selected**
  — confirmed a real bug, not a misunderstanding: with nothing selected (a fresh session, or after
  deselecting), the button is simply disabled, so there was no panel-only way to select a FIRST
  piece at all, only via clicking something in the Outliner/viewport first. Fixed with new
  `RKA_OT_select_piece_by_name` (`coll_name` StringProperty, unconditional poll) plus a "Pieces in
  this file" list in the panel (`_is_piece_collection` scan, one button per piece, sorted by name)
  — click any piece directly, no pre-existing selection needed. `RKA_OT_select_piece` itself is
  unchanged (still useful once something IS active, just no longer the only entry point).
- **Material (`matkey`, "asphalt"/"concrete") was never actually configurable, at any point** —
  not a regression of the F9 pattern, worse: it was a hardcoded Python string literal at every
  `junction_pad`/`curb_loop`/`road_spine` call site, never exposed as an operator property, never
  read from a custom property. New `RKA_OT_set_pavement_matkey`/`RKA_OT_set_curb_matkey` (two
  separate operators, not one with a `target` enum, specifically so the panel can use
  `layout.operator_menu_enum` for a clean dropdown over the full ~19-entry `kit_common.MATS`
  registry) set `rka_pad_matkey`/`rka_pave_matkey`/`rka_curb_matkey` and rebuild. **Pavement needed
  a special case for a GN segment/transition**: `rebuild_segment_gn_in_place`/`rebuild_lane_
  transition_in_place` deliberately never delete/recreate the spine object (its own control points
  ARE the live-edited shape), so a plain rebuild doesn't reach it the way pad/curb regeneration
  does — new `kit_common.set_road_spine_material(spine_obj, matkey)` updates the spine's live
  "Road" GN modifier's Material input directly, called in addition to the rebuild. An
  intersection's pad has no such problem (fully regenerated every rebuild, reads `rka_pad_matkey`
  fresh) — the direct spine update is just a no-op there (`local_object("spine_<name>")` doesn't
  resolve on an intersection collection).

**Verified**: new `smoketest_matkey_panel.py` — confirms the change reaches the REAL material slot
(read back via each piece's own GN modifier's Material input, since pad/curb/spine are GN-Curve
objects, not `obj.data.materials`) for both an intersection (pad + curb) and a GN segment
(pavement spine + curb), not just a stored-and-ignored custom property. `smoketest_select_piece.py`
extended: confirms `select_piece`'s poll() fails from a clean slate (the bug) while `select_piece_
by_name`'s poll() always succeeds, and that it actually selects the named piece with nothing
previously active. Full 17-file smoketest suite passes (one run in the full-suite loop hit a
Blender-process crash inside `colonly_mesh_evaluated`/`bpy.data.meshes.new_from_object` that did
NOT reproduce across 4 subsequent full/standalone reruns — flaky under rapid sequential background-
process spawning, not a deterministic logic bug; worth knowing about if it resurfaces, not treated
as unsolved). `./gradlew build` unaffected (Python-only).

### Follow-up (2026-07-28): the "curves up at segment ends" report — root-caused and fixed;
### it was shading, not geometry

User clarified the key fact that cracked this: the curve-up happens at **every** segment end/
connection, universally, while "the overall spine collection remain straight" — ruling out a
per-piece data corruption (like `Segment_012`'s spine earlier in this doc) in favor of something
systemic in the shared GN pavement generator itself.

**Verified directly, not guessed:** built an isolated, perfectly flat/straight 5-point test spine
and read back its EVALUATED mesh's actual vertex Z values at every ring, including the very first
and last — completely flat, zero deviation, everywhere. This ruled out a geometry/position bug
entirely (matching the user's own observation that the spine stays straight) and pointed at
**shading**: printed each face's `use_smooth` flag on that same test mesh and found all 14 faces
(top, bottom, side walls, AND the end cap) came back `True` — `GN_RoadProfile`'s `Set Shade Smooth`
node was applied to the WHOLE mesh with no sharp edges marked, so Blender interpolates normals
smoothly ACROSS the sharp 90-degree edges between the flat top/bottom and the vertical side/
end-cap faces. This is a purely visual illusion, worst exactly at an end cap where top+bottom+two
sides+the cap all converge on one vertex at sharply different angles — reading exactly as "the
road curves up at the end," with zero actual vertex-position change, consistent with everything
the user reported.

**Fix**: `GN_RoadProfile` now shade-smooths SELECTIVELY — a per-face normal check (`|normal.Z| >
0.9`) restricts smoothing to the drivable top/bottom surface (which SHOULD look continuous through
a gentle curve), leaving side walls and the end cap flat-shaded (correct, since they're genuinely
flat planar quads that shouldn't blend into their neighbors). One real implementation snag caught
and fixed before verifying further: `Curve to Mesh`'s own output apparently comes in already
smooth-shaded by default, so a single selective `Set Shade Smooth` pass only ever ADDS smoothing
to the selected faces while silently leaving the already-smooth unselected ones untouched (i.e. no
visible difference from before) — needed an unconditional flatten-everything pass FIRST, then the
selective re-smooth pass second.

**Gotcha hit reapplying to the real district (a NEW variant of the by-now-familiar stale-node-group
problem, worth remembering)**: `District_industry_5_1.blend`'s spine objects' "Road" GN modifiers
referenced `GN_RoadProfile.001`, not `GN_RoadProfile` — an auto-renamed duplicate accumulated from
an earlier session, not the plain name `make_road_profile_group()`'s get-or-create checks for. This
compounds with the already-known fact that a rebuild never touches a spine object's own modifier
(only the INITIAL `road_spine()` call sets it) — so simply deleting the (wrong-named, nonexistent)
"GN_RoadProfile" and rebuilding, the pattern that worked for the pad/Fill-Curve fix, silently did
NOTHING here (confirmed: zero stale group found, zero fresh group created, since nothing in the
rebuild path ever calls `make_road_profile_group()` for an existing spine). Fixed by directly
migrating every spine's "Road" modifier from the old `.001` group to a freshly-created correct one,
preserving each one's current material/thickness by reading them off the OLD modifier before the
swap, then removing the old (now-zero-users) node group. **Takeaway**: when a structural GN
node-group change needs to reach EXISTING content, check what node group name existing modifiers
ACTUALLY reference (`obj.modifiers[...].node_group.name`) rather than assuming the canonical
name — don't assume the purge-and-rebuild pattern that worked for the pad transfers unchanged.

**Verified**: real district spine (`spine_Segment_002`) evaluated mesh checked directly after the
migration — top face smooth, all 4 side/cap faces flat, matching the isolated test. Full 17-file
smoketest suite + `./gradlew build` pass (no test needed new assertions — this was a pure visual
fix, no existing test asserted on `use_smooth` either way). Re-ran the full pipeline;
connectivity unchanged. `TrafficCrashDiagnosticHost`: 0 anomalies, consistent with the previous
best result (a pure shading fix was never expected to change physics/collision behavior, and
didn't).

### Follow-up (2026-07-28, same day): the shading fix made a REAL geometry gap visible — segment
### ends had solid end-cap walls, closing off the road instead of staying open

User's follow-up, immediately after the shading fix landed: with normals no longer smoothed across
sharp edges, the end cap (present all along — see below) now reads clearly as a **solid wall
blocking the road**, at every connection, not just one screenshot's worth. Root cause: `Extrude
Mesh` (`GN_RoadProfile`'s deck-thickness step) automatically walls off EVERY open boundary edge of
the swept ribbon when extruding the whole region (`Individual=False`) — that includes the two
SHORT edges at the curve's own start/end, not just the two long sides. This was true from the very
first version of `GN_RoadProfile`; the shading fix didn't create it, it just stopped hiding it
(smooth-shaded normals blended the cap into its neighbors, making it much harder to notice as a
separate solid panel). Two adjacent pieces meeting at a connection each keep their own cap, so
every seam in the whole network showed a double wall instead of one continuous deck.

**Fix**: tag the curve's own start/end points (`Curve Endpoint Selection`) with two SEPARATE
boolean attributes (`rka_is_start`/`rka_is_end`) before `Curve to Mesh`, read them back after
`Extrude Mesh`, and delete any face whose corners are ALL-start or ALL-end via `Delete Geometry`
(FACE domain) — a real cap face's 4 corners all come from the single boundary ring, so this
identifies exactly the 2 cap faces regardless of segment length or curvature, without touching the
top/bottom/side faces at all.

**Real bug caught and fixed before verifying further, not assumed to work**: the first version
used ONE unified `rka_is_end` flag (start OR end together). Tested against a 2-point (single-span)
segment specifically because it's the SHORTEST possible case, and it came back completely empty
(`to_mesh()` returned `None`) — for exactly 2 points, BOTH rings are boundary rings with nothing
interior between them, so the ONE connecting side-wall face's corners are ALL tagged too (2 from
the start ring, 2 from the end ring, both "true" under the unified flag), and it got deleted right
alongside the actual caps. Splitting into separate start/end tags, each independently required to
cover ALL 4 corners, fixed it: a mixed-ring face now fails BOTH the all-start and the all-end test.
Verified directly across 2-point, 3-point, and 5-point test spines — each correctly loses exactly
its 2 end caps and keeps every other face, including the boundary-adjacent side panels.

**Same node-group migration gotcha as before, with a new wrinkle**: `make_road_profile_group()`'s
get-or-create found the file's OWN "GN_RoadProfile" (correctly named this time, from the PREVIOUS
follow-up's migration) and happily returned the stale pre-cap-deletion structure — a purge would
normally fix this, but since the name was already canonical, the group had to be renamed out of
the way first (`old_ng.name = "GN_RoadProfile_STALE"`) to force a genuinely fresh one, then all 11
spines migrated to it (material/thickness preserved) exactly as before.

**One accepted, minor trade-off, not a bug**: a genuine unconnected dead-end segment now has an
open cross-section at its tip (no visible wall) instead of a capped one — the top/bottom driving
surface itself is untouched right up to the very last point either way, so nothing about support/
collision changes; the only visual difference is you can now see "through" a true dead end's empty
interior if you look at it exactly end-on. Judged a clear net improvement: true dead ends are rare,
while this was breaking every single normal connection in the network.

**Verified**: isolated 2/3/5-point tests (above); real district (`spine_Segment_002`: 5 faces incl.
2 caps -> 3 faces, 0 caps). Full 17-file smoketest suite + `./gradlew build` pass. Full pipeline
re-run; connectivity unchanged (40 paired/0 isolated/2 legitimate ambiguous).
`TrafficCrashDiagnosticHost`: 0 anomalies, matching the shading-fix run (removing invisible
double-wall geometry was never expected to change collision/physics, and didn't).

### Follow-up (2026-07-28, same day): curbs hung BELOW the road surface instead of rising above
### it — a real, previously-known Blender quirk that only got compensated for in one of the two
### places it needed it

User's report: at an intersection, walking from a connecting segment's lane toward the pad, there
was a visible height gap right at the connection — "intersection generated mesh is on upper of
curb rather than bottom of curb like segment." Measured directly at `Intersection_4WAY_002`: pad
Z = 4.6933, but its own curb spanned `[4.5433, 4.6933]` — the curb's TOP touches the pad, and its
BASE hangs 0.15m (exactly `curb_height`) below it, instead of the curb's BASE touching the pad and
its TOP rising 0.15m above. Segments showed the identical pattern (not actually inconsistent with
intersections, contrary to how the report initially read) — so this wasn't a segment-vs-
intersection mismatch, it was one shared bug affecting every curb in the district equally.

**Root cause, and why it was easy to miss**: `GN_CurbLoop`'s `Curve to Mesh` sweep maps the curb
profile's local +Y (its own "up" axis, height 0 -> `curb_height`) to world **-Z**, not +Z. This is
a documented, ALREADY-KNOWN Blender quirk in this exact codebase — `GN_BarrierProfile` (a
different profile-sweep, for ramp parapet walls) has an explicit comment about it ("profile-Y ->
world -Z here, so a -Height/2 lift puts the wall base ON the deck edge") and compensates with a
negation. `_curb_profile_object` (the curb's own BOX/GUTTER cross-section builder) was written
independently and never got the same treatment — its profile points go `0 -> +height` directly,
with nothing correcting for the sweep's own Z-inversion, so the curb built exactly backwards:
base at road+0 mapping to the WRONG side, top at road+height mapping BELOW it.

**Fix**: negate the height/Y component when storing `_curb_profile_object`'s BOX/GUTTER points
into the profile curve — but NOT inside `gutter_curb_profile()` itself, which is also used
independently by `ops_intersection.build_curb`'s GUTTER branch through `swept_profile` (a
different, hand-rolled, already-correctly-oriented sweep) — negating there would have silently
broken that other, unrelated path. Applied the negation locally, right where `_curb_profile_object`
consumes `gutter_curb_profile()`'s output.

**Two false alarms caught and resolved during verification, not shipped as real bugs**: (1) a
first GUTTER-style spot check appeared to show the fix losing all height variation entirely
(`[5.0]` only) — turned out to be a stale-depsgraph bug in the VERIFICATION SCRIPT itself (captured
`evaluated_depsgraph_get()` before creating the object being tested), not in `kit_common.py`; a
fresh depsgraph fetched after creation showed the correct `[5.0, 5.15]`. (2) After rebuilding the
real district, `RKA_CurbProfile_BOX` showed "34 users" which looked alarming (suggesting the OLD,
unfixed profile was still in heavy use) — direct inspection showed the opposite: the base-named
object had the CORRECT (negated) point data and was genuinely referenced by all 34 real curb
modifiers; two separate `.001`/`.002`-suffixed objects (with the OLD, unfixed values) were the
actual leftovers, referenced by nothing, and were removed.

**Verified**: isolated BOX + GUTTER curb tests (new `smoketest_curb_rises_up.py`, permanent
regression check) — both correctly span `[road_z, road_z + curb_height]`. Real district
(`curb_Intersection_4WAY_002_0`): now `[4.6933, 4.8433]`, matching the pad exactly at its base and
rising the full `curb_height` above. Confirmed via direct modifier inspection that all 34 real
curb objects in the district reference the corrected profile, not a stale one. Full 19-file
smoketest suite + `./gradlew build` pass. Full pipeline re-run; connectivity unchanged.
`TrafficCrashDiagnosticHost`: 0 anomalies.

---

## Critical files

- `assets/world_source/lib/kit_common.py` (reuse `link_collections`, `instance_collection`, `colonly*`)
- `assets/world_source/lib/road_graph.py` (reference only — do not extend)
- `assets/world_source/tools/save_roads.py` (`_spline_points` reused, not forked)
- `assets/world_source/tools/gen_roads_only.py` (structural template for `save_lane_kit.py`)
- `assets/world_source/AUTHORING_GUIDE.md` (§2 regen contract, §7/§11 to extend later)
- `PLAN_WORLD_AUTHORING.md` (Phase A flagged superseded)
- `src/main/java/com/openworld/world/WorldBaker.java`, `VehicleRoute.java`, `LaneGraph.java`,
  `IntersectionZone.java` (Phase 4)
- `assets/world_source/districts/District_manual_1.blend` (source material for Phase 1)

## Docs & memory (cross-cutting, after each phase lands)

- [x] `AUTHORING_GUIDE.md` §7/§8/§11 (2026-07-27) — §7 already carried a "Replaced" note + full
      road_kit_authoring workflow write-up from earlier in Phase 6; §11 already referenced P6.8's
      backbone removal. This pass added: the one-click "Godot Export" button as a shortcut for §7
      steps 2-3, a paragraph documenting P6.2's per-piece `-colonly` collision (and why it's kept
      separate from ground/terrain collision, not merged), and fixed §8's road-placement table row
      (was still describing the retired `road_<name>` curve convention).
- [x] Memory (2026-07-27) — `project_roads_v2.md` and `project_district_authoring.md` were stale
      (pre-Phase-6: described the retired `road_graph.py`/`.roads.json` pipeline as current, and an
      unstarted Phase 2/3 roadmap that Phase 6 completed and superseded). Rewritten; see memory
      files themselves for content.

---

### Follow-up (2026-07-28): Segment_002 seam gap — content bug, not a code bug

**User report**: with the previous session's fixes in place, a screenshot from the LIVE Godot
playtest of `District_industry_5_1` still showed a visible step/wall at the Segment_002 ↔
Intersection_4WAY_002 connection — the pavement sat noticeably lower than the intersection pad
at that one seam. User was explicit this was seen live, not just in the Blender viewport, and
that the spine visually looked aligned in Blender ("the spine of segment did align with the
intersection perfectly, the problem is not the spine, but the generated road mesh").

**Investigation**: rather than trust the Blender-viewport read, measured every segment/transition
spine endpoint against its nearest arm marker across the whole district (tight XY radius, exact
arm Z comparison). 13 of 14 connections matched to the sub-millimeter (dz = 0.0000). Exactly one
did not: `Segment_002.start`, at world XY (200.47, -80.67) — 0.068m from `Intersection_4WAY_002`'s
arm `E` marker (200.54, -80.66, Z=4.6933) — had spine Z = **5.1139**, a 0.42m vertical mismatch.
The collection's own `rka_p0` custom prop stored the same wrong Z, confirming the drift was baked
into the spine's actual control-point data, not a display artifact. Every other piece in the file
(checked directly: node group id-props, all 34 curb `-colonly`/visual pairs, every pad Z) was
clean — this was an isolated single-point authoring drift on one segment, most likely from an
earlier manual Edit-Mode drag (the file's live-edit workflow allows dragging spine control points
directly) that moved the point in Z without keeping it pinned to the arm it's supposed to meet.

**Fix**: snapped `Segment_002`'s spine start point (and the stale `rka_p0` Z) back to
4.693283557891846 (the arm's own Z, identical to `Intersection_4WAY_002`'s pad and every other
verified connection in the district), then ran `_rebuild_piece_in_place` to regenerate the
pavement/curb/collision from the corrected spine, saved, and re-ran the full
`build_piece.sh District_industry_5_1` export/bake pipeline so the live Godot scene picks it up.

**Verified**: post-fix spine points both read Z=4.6933; evaluated pavement top surface now matches
the pad exactly at the seam. Full 19-file smoketest suite passes. `District_industry_5_1_before_
seg002_zfix.blend` kept as a scratchpad backup before the edit.

**Lesson for later phases**: this is exactly the class of bug P6.4's planned `lane_kit.py`
connectivity validator (endpoint clustering + Z-consistency check across pieces) is meant to catch
automatically at authoring time instead of via a live-playtest screenshot — worth prioritizing that
check specifically (not just the ambiguous-cluster detection it already does) once Track A's
multi-piece export work resumes.

---

### Follow-up (2026-07-28): curb end-caps blocking every segment connection — the REAL bug behind the "bump"

**User pushed back** on the Segment_002-only fix above: all 14 segments stiowed the issue
live in Godot, and it reproduced on a completely fresh build too (build intersection → Extend
From Arm → export → walk from the segment toward the intersection = a bump). User's own read was
sharp and correct: "the road generated segment is below spline/curve of segment road, so the
align is at top of the curb of segment, not at the road level" — i.e. something about the CURB,
not the pavement, was the actual obstacle.

**Investigation**: rather than trust Blender's viewport again, traced the *actual exported glTF
bytes* end-to-end for a fresh `build_intersection` → `extend_from_arm` → `export_world.py` repro
(parsed the `.gltf`/`.bin` directly with a small script, not just Blender's own `to_mesh()`).
Pavement top surface and pad top surface matched exactly (0.15) in the real export — the pavement
was never the problem, confirming the user's own diagnosis. `curb_Segment_001_L`'s exported mesh
told the real story: at X=12 (the exact connection point to the intersection arm), all 4 corners
of the BOX curb's cross-section were present, forming a complete, solid end wall — a physical
curb-height (0.15m) block sitting right across the road at the seam.

**Root cause**: `kit_common.make_curb_loop_group()` (`GN_CurbLoop`) had
is `c2m.inputs["Fill Caps"].default_value = True` hardcoded, unconditionally. A segment/transition's
own L/R curb is built from an OPEN boundary curve (`curb_loop(..., closed=False)`) — Curve to
Mesh's Fill Caps then caps BOTH ends with the profile's own cross-section, i.e. a solid block
right where the curb should stay open into the next piece. An intersection's own curb loop is
CYCLIC (`closed=True`) — a closed curve has no ends to cap in the first place, so the flag was
always a silent no-op there, which is exactly why "the intersection seems to work correctly" while
every single segment didn't: this bug only ever had a case to bite on for segments/transitions.

**Fix**: `Fill Caps = False` unconditionally — simpler than the earlier `GN_RoadProfile` fix (that
one needed manual endpoint-tag + delete-geometry plumbing because `Extrude Mesh` auto-caps
regardless of any input; Curve to Mesh has a direct boolean for exactly this). Safe for the closed
case since there was nothing to cap there either way.

**Migration + re-verification**: `District_industry_5_1.blend` had a stale `GN_CurbLoop` (built
before this fix) baked into 34 curb modifiers — migrated the group (same rename-old/recreate/
reattach-modifiers pattern used for `GN_JunctionPad`/`GN_RoadProfile` earlier), rebuilt all 16
pieces, re-ran the full `build_piece.sh District_industry_5_1` export/bake. New permanent
regression test `smoketest_curb_open_ends.py`: an open BOX/GUTTER curb has zero cap faces at
either end and still spans its full length; a closed intersection curb loop is unaffected (144
faces, unchanged shape). Full 20-file smoketest suite passes.

**Lesson**: when a Blender-side fix is verified only via `to_mesh()`/the viewport, that is NOT
sufficient proof the *exported* result is correct — this session's earlier "spine matches pad
exactly, must be a stale-cache/session issue" conclusion was right about the pavement but missed
the curb entirely because the pavement was never actually broken. Next time a "generated mesh
doesn't match its source" report survives a live Blender-data check, trace the real exported
`.gltf`/`.bin` bytes before concluding the geometry is correct.

---

### Follow-up (2026-07-28): the REAL bug — GN_RoadProfile never had a top face

**User was right to keep pushing.** The curb Fill-Caps fix above was real but not the actual cause
of "the road sits below the spine/pad." User gave a precise ASCII diagram of the symptom (spine/
curb/yellow-line all at one height, the actual generated road mesh geometry sitting noticeably
below it) and explicitly said to stop trusting side/edge checks and verify from the center of the
mesh outward from the spine. That pushback was correct and led directly to the real bug.

**Investigation**: rather than trust any more Blender-side `to_mesh()` checks, went one level
deeper than the earlier curb investigation — actually loaded the baked `.tscn` into a live Godot
`SceneTree` and ran `PhysicsServer3D` raycasts straight down through the real collision, exactly
what a walking character's ground check does. Result: every raycast down a segment's corridor hit
`Y = road_z - thickness` (the BOTTOM of the pavement deck), never `road_z` (the top) — a full 0.4m
short, consistently, for every segment. A face dump of the pre-deletion `GN_RoadProfile` output
made the shape of the bug obvious: exactly 5 faces for a 2-point spine — 1 relocated ribbon + 2
side walls + 2 end caps — never a distinct 6th "top" face.

**Root cause**: `GeometryNodeExtrudeMesh`, given a single flat selected face with nothing else
attached to anchor it (exactly what `GN_RoadProfile`'s Curve-to-Mesh ribbon is), MOVES that face to
the offset position and walls its boundary — it does not also leave a copy behind at the original
position. This is standard Blender behavior (the same reason extruding a bare plane in the regular
editor leaves an open-bottom box), but it meant `GN_RoadProfile` never had a genuine top surface at
`road_z` since the group was first created — the "road" every screenshot ever showed was actually
the *relocated bottom* face (normal still pointing up, so it looked plausible), sitting a full
`thickness` (0.4m) below where the drivable surface should be.

**Why this survived multiple earlier verification passes**: every prior check (this session's and
presumably earlier) was a vertex-Z-range scan (`sorted(set(v.z for v in mesh.vertices))`). That
kind of check ALWAYS showed both `road_z` and `road_z - thickness` present as vertex values — but
only because the *side wall* vertices span both heights, not because a face actually covered the
top. A vertex existing at a height is not proof a face exists there. Only a raycast (or an explicit
"does every vertex of some face sit at this exact height" check) can tell the difference, which is
exactly why this took a live Godot physics raycast to finally catch.

**Fix**: `c2m`'s ribbon output now fans out to two places — unmodified straight into a new
`GeometryNodeJoinGeometry` (becomes the genuine, never-relocated top face) AND into `ext` as before
(becomes the bottom + side walls, still end-capped by the existing `del_cap` logic, which was
never actually the problem). Blender GN sockets support fan-out to multiple consumers with no
explicit duplicate/copy node needed.

**Migration + re-verification**: `District_industry_5_1.blend` had a stale `GN_RoadProfile` (built
before this fix) baked into 11 pavement modifiers — migrated the group (same pattern as
`GN_JunctionPad`/`GN_CurbLoop` earlier), rebuilt all 16 pieces, re-ran the full
`build_piece.sh District_industry_5_1` pipeline. Verified THREE independent ways: (1) a headless
Blender face dump shows a genuine top face at `road_z` with an up-facing normal for 2/3/5-point
spines; (2) a fresh `build_intersection` → `extend_from_arm` → `export_world.py` repro's raw
`.gltf`/`.bin` bytes AND its baked `.tscn`'s `ConcavePolygonShape3D` data both show the top face
present; (3) a live Godot `PhysicsServer3D.intersect_ray()` walk down the real
`District_industry_5_1.tscn`'s Segment_005 corridor now consistently returns `Y=4.6933` (matching
the pad and every other reference point) instead of falling through to the bottom.
`smoketest_open_ended_deck.py` rewritten with a positive `_has_face_at_z` check (every vertex of
some face must sit at the expected height, not just any vertex present in the mesh) so this exact
class of bug can never hide behind a vertex-range check again. Full 20-file smoketest suite passes.

**Lesson, stated plainly**: a vertex existing at the right Z is not proof a face exists there.
Any future "does the collision/visual mesh actually cover this surface" question needs either a
raycast against the real baked scene, or an explicit per-face all-corners-at-height check — never
just a vertex Z-range scan.

---

### Follow-up (2026-07-28): simplified GN_RoadProfile to a flat plane (the actual permanent fix)

**User's own question, after the top-face fix above landed and was verified**: "why is the road
segment a box being pushed down, why not just a plane, like the intersection [pad]?" — and the same
question for transitions (they share the same pipeline, so the answer applies to both).

That question is the real fix, better than the top-face patch. `GN_RoadProfile` no longer sweeps a
ribbon and extrudes it into a solid `Thickness`-deep slab (which needed endpoint tagging, cap-face
deletion, a two-pass shading fix, and finally a Join Geometry patch just to behave like a normal
road — see the several follow-ups above). Nothing in the codebase ever read or depended on the
pavement having real volume, and `GN_JunctionPad` already proved a flat, zero-thickness swept/
filled mesh collides and renders correctly for exactly this kind of surface. `GN_RoadProfile` is
now: Curve to Mesh (profile line, scaled by the spine's per-point Radius) → Set Material → Set
Shade Smooth → done — the same shape as `GN_JunctionPad`'s Fillet/Fill-Curve pipeline. No side
walls, no end caps, no top/bottom distinction — the entire bug class from the last three follow-ups
is structurally impossible now, for segments AND transitions (both go through `road_spine()`).
`Thickness` is kept as an accepted-but-unused group input purely so existing
`road_spine()`/`road_from_curve()`/`assemble.py` callers need no signature changes.

**Migration + verification**: migrated `District_industry_5_1.blend`'s `GN_RoadProfile` again (11
pavement modifiers), rebuilt all 16 pieces, re-ran the full export/bake. `smoketest_open_ended_deck.py`
rewritten a second time to check the new invariant directly: exactly one face per span, every
vertex at road_z, normal up. Final end-to-end proof: a live Godot `PhysicsServer3D` raycast walked
across a full segment↔intersection corridor in the real district returned a single value —
`Y=4.6933` — for every sample point along the path, no discontinuity anywhere. Full 20-file
smoketest suite passes.

**Also (same request)**: `marking_ribbon()` (yellow/white lane-marking strips) now lifts its
vertices `z_lift=0.01` (default) above the spine it samples — it was previously exactly coplanar
with the pavement (both read the same spine Z), which z-fights in render. One-line change, no
other call sites needed updating.

**Retrospective**: three follow-ups in a row (Segment_002's authored Z, curb Fill Caps, and the
missing top face) all turned out to be real bugs, but the missing-top-face one was the load-bearing
issue the user kept correctly insisting was still there after each partial fix. The lesson from the
`_has_face_at_z` note earlier still stands, but there's a second one here: when a "fix" requires
progressively more special-case machinery (tag endpoints, delete caps, two-pass shading, then patch
in a missing face) to keep behaving correctly, that is itself a signal to step back and ask whether
the underlying approach is right at all — which is exactly what the user's question did.

### Tooling: the Godot binary is the STOCK one now (2026-09-08, user-requested)

`blender/tools/env.sh`'s `GODOT` default moved from `godot.linuxbsd.editor.x86_64.jvm` to
`/data/danilko/bin/Godot_v4.7.2-stable_linux.x86_64`, and every remaining reference to the old
binary outside this file's dated history was updated with it (`AIM_PLAN.md`, `CLAUDE.md`'s
Build & Run, 35 stale `.jvm.0.15.0` entries in `.claude/settings.local.json`).

**Why the old one is now actively wrong, not merely old.** `build.gradle.kts` is on
`com.utopia-rise.godot-jvm` `1.0.0-dev3`, which ships the runtime **with the project** as the
`addons/jvm/` GDExtension. A binary with the JVM module compiled in therefore loads it twice, and
the failure does not name the binary: `Attempt to register extension class 'JvmScript', which
appears to be already registered` → `Version mismatch! C++ module is : 0.17.1-4.7.2 / Jar is :
1.0.0-dev3` → **every AutoLoad failing with "does not inherit from 'Node'"**. That reads as a
completely broken project, which is exactly how it cost a debugging detour this session before
`AimDebugAuto.tscn` was run under the stock binary and came back clean.

The entries above this one keep the old paths on purpose — they are dated records of what was true
when they were written, and `env.sh` has been the single owner of the default since 2026-07-27.

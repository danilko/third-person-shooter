# Road authoring — the point/port graph

Written 2026-08-22. **This is the design of record for the road rebuild.**
`ROAD_KIT_REDESIGN.md` (2026-08-13) is still the source of the 13 measured defects this design is
shaped by, and its rules are cited throughout. `ROAD_KIT_MIGRATION_STATUS.md` and
`ROAD_JOINT_TRANSITION_STUDY.md` describe models that are now historical — read them for *why*
things were tried, not for what to build.

`AUTHORING_GUIDE.md` remains the guide to using whatever exists today. The current mesh-graph addon
(`addons/road_kit_authoring/graph_*.py`) stays registered and the island stays buildable until this
design ships (§8 step 7) — see §8 for why that ordering is deliberate.

## In one page

**The move.** Cross-section stops living on a mesh *edge* and lives on a **road point** — an Empty
that is simultaneously a station (lanes per direction, median, kerbs, footways, structure) and a
**port** (its typed links to other points). A road is a collection of ordered points; a junction is a
clique of mutually-linked points; a ramp is an aux slot plus an AUX link. Taper, lane drop, merge,
one-way and aux lanes all become "two stations that differ", which deletes ~900 lines of
special-case inference from the current addon.

**Why now.** The current model must special-case every along-the-length change (a dozen `aux_*` /
`merge_*` / `RAMP_*` constants), infers ramp connectivity instead of authoring it, and presents the
network as 1619 identical grey edges you cannot select or read.

**Scope.** New addon (12 modules) + 4 new pure libs; the island is regenerated afterwards and gets no
vote on the design. The Godot side is **also** in scope: `.lanekit` v2 with real bezier handles, a
`junctions[]` array and an explicit `spawnable` flag, plus the matching `WorldBaker` /
`PathLaneRoute` changes. Realistically 10–15 kLOC of Blender code and ~1 kLOC of Java.

**The six things that decide whether this works:**

1. **§8 step −1 — ship the Godot fixes first, on today's pipeline.** Measured: `zone_id` on 0 of 924
   lanes, `turn` missing on all 351 through lanes, so **every through lane is unspawnable today**.
   Hours of work, immediate payoff, no dependency on the rewrite. Do not hostage it.
2. **§8 step 0 — do NOT archive the old addon on day one.** Archiving it leaves the world unbuildable
   across steps 1–6, and the most likely death of this project is attention moving to the game while
   three half-migrated road systems sit in the tree. Archive at step 7.
3. **§1.2c — the authored record is a git-diffable `.roads.json`, not `.blend` IDProperties.** This
   is the assumption that made rewrites #1 and #2 total losses: there was nothing to migrate. The
   Empties become a view over the file.
4. **§2.1a — the taper ROUTE rule.** Lane *widths* are free from `lane_profile.interpolate()`; lane
   *routes* are not — measured, not suspected. One owner, with numeric tests.
5. **§8 step 0.5 — the spike.** 2 000 Empties, `links` CollectionProperty, depsgraph+timer. Measures
   undo, drag, Shift+D uid collision, linked-point delete. Decides the carrier before any real code.
6. **§3.5 — collision is a deliverable, not a footnote.** Today's roads emit *none*, which silently
   costs the pedestrian navmesh, bullet-impact surfaces and car ground. Paid for by demoting
   superelevation and sight-distance from gate failures to advisory colour (§5).

Plus two standing rules: **`Auto Setback` is the default path** (§2.2 — nobody hand-places 200 junction
mouths), and **the gate is built first** and wired into Build (§5).

## Context

### Why

The addon has been rewritten twice and both models put the cross-section in the wrong place.

The current mesh-graph model (`graph_*.py`, ~6 kLOC) stores `lanes_fwd`, `median_width`,
`sidewalk_*` on the **edge domain** — one constant cross-section per segment. A real road changes
along its length, so every such change had to be bought back as a special case bolted onto that
constant:

`lane_transition_length`, `aux_taper_length`, `aux_buffer_length`, `aux_lanes_left/right`,
`aux_median_left/right`, `derived_offsets()`, `taper_breakpoints`, `aux_scale_keys`,
`align_ramp_ends`, `merge_corridor_ends` (192 lines / 59 branches), `ramp_candidates` (277 lines),
`auto_aux_lanes`, `ramp_plan`, `ramp_services`, and the constants `AUX_WEAVE_HOLD`,
`AUX_TAPER_MIN_LENGTH`, `AUX_MERGE_BUFFER`, `RAMP_WALL_OPEN`, `MERGE_WALL_GAP`,
`MERGE_WALL_MAX_FRACTION`, `MERGE_JOINT_MAX`, `RAMP_OVERSHOOT`, `JOIN_OVERSHOOT`,
`ALIGN_BLEND_LENGTH`, `NOSE_MAX_CHAIN_FRACTION`, `RAMP_SIDE_WINDOW`.

Every one of them expresses *"the cross-section is different here than it is there"* — which is free
once the cross-section lives on a **station**. The repo's own `ROAD_KIT_REDESIGN.md` reached this
conclusion in 2026-08-13 (defect 2: *"cross-section is per-station, not per-piece"*) and the
mesh-graph rewrite two days later did not adopt it.

Two further consequences:

- **Connectivity is inferred, not authored.** `ramp_candidates` *guesses* which carriageway serves a
  ramp and which side it joins on; three modules depend on that guess. Redesign defect 10 —
  *"connectivity is authored data, not inferred from distance"* — is unfixed.
- **The network is illegible.** 1619 identical grey edges, no per-road identity, no viewport
  feedback of authored values, and the object you can click is generated output — there is a
  dedicated *Edit Road Graph* button whose only job is to work around that.

### What this plan is

A **from-scratch authoring model**, designed to the stated requirement and not constrained by any
existing Blender-side code, file or `.blend`. The old addon is archived — but at the *end* (§8 step
7), not the start, so the world stays buildable throughout. The island road network is regenerated
from scratch (or hand-authored) afterwards — it does not shape the design.

**The Godot side is in scope too.** The `.lanekit.json` schema and the Java that reads it
(`WorldBaker` → `PathLaneRoute` → `LaneGraph`) are redesigned to v2 (§6) rather than preserved —
today's dense-polyline lanes throw away the tangents Blender already has, and the runtime pays for it
in cornering quality and ~2 800 static nodes per world load. What must not break is the *behaviour*
already shipped: districts baked against v1 keep working, and every v1 field stays readable.

---

## 1. The model

Four concepts. Nothing else is a first-class thing.

| Concept | Is | Blender representation |
|---|---|---|
| **Road** | An ordered corridor — a chain of points | A Collection under `ROAD_MANAGER` |
| **Point** | A station **and** a port: position, cross-section, connections | An Empty (arrow shape) with `obj.rka_pt` |
| **Link** | An authored connection between two points, typed | An entry in `point.rka_pt.links` (Object pointer + type) |
| **Junction** | A set of points mutually linked as a pad | Derived from the link graph; owns a parent Empty |

### 1.1 Scene layout

```
ROAD_MANAGER/                      authored input — the ONLY thing hand-edited
  road_shinbashi/                  one collection per road corridor
    p000  p001  p002 ...           road points, ordered
  road_loop_e/
  ramp_ic_chuo_en/
  JUNCTIONS/
    JCT_0007                       parent Empty at the clique centroid; members parented to it

ROAD_MANAGER_GEN/                  generated — wiped per road, never hand-edited
  road_shinbashi__surface
  road_shinbashi__edges
  road_shinbashi__collision-colonly
  JCT_0007__pad   JCT_0007__corners
  PREVIEW/  (lane curves, flow chevrons)
```

Two lifetime rules, both from hard-won experience (redesign defect 4: object-lifetime bookkeeping
was half the old addon):

1. **Authored and generated never share a collection.** A rebuild only ever clears inside
   `ROAD_MANAGER_GEN`. Nothing under `ROAD_MANAGER` is deleted by any build, ever.
2. **One generated surface object per road**, plus its outline and its collision proxy. Layers are
   modifiers, not sibling objects.

### 1.2 The point

```python
class RKA_Point(PropertyGroup):        # obj.rka_pt
    # ── identity ────────────────────────────────────────────────────────────
    is_point:   BoolProperty           # marks this Empty as a road point
    uid:        StringProperty         # unique per scene; survives RENAME. Must NOT survive Shift+D
    role:       Enum{SEGMENT, INTERSECTION, RAMP_ENTRY, RAMP_EXIT, TERMINUS}
    schema_ver: IntProperty            # migration hook

    # ── cross-section: INHERIT the road's base profile, or OVERRIDE it here ──
    profile_mode: Enum{INHERIT, OVERRIDE}   # ONE bit, whole-profile, shown in the overlay
    #   plus a SMALL set of genuine deltas that a station may change while still inheriting:
    #   lanes_fwd, lanes_bwd, aux_fwd, aux_bwd — the 95% case of "what changes along a road"

    # ── cross-section AT THIS STATION, measured outward from this point ──────
    lanes_fwd:  IntProperty            # 0 allowed  ->  one-way
    lanes_bwd:  IntProperty
    lane_width: FloatProperty          # + optional per-side override
    drop_side_fwd / drop_side_bwd: Enum{KERB, MEDIAN}   # WHICH lane a count decrease removes
    aux_fwd:    IntProperty            # auxiliary lanes, ALWAYS outboard of the standard lanes
    aux_bwd:    IntProperty
    aux_side:   Enum{KERB, MEDIAN}     # which end of the group the aux opens at (offside exits)
    shoulder_left_width / shoulder_right_width         # lane-drop-into-shoulder, SLOT_SHOULDER
    parking_left_width  / parking_right_width          # SLOT_PARKING
    mark_left:  Enum{NONE, SINGLE_W, DOUBLE_Y, ...}    # per slot boundary; drives marking_runs
    design_speed: FloatProperty        # drives the taper-rate, radius and superelevation checks

    median_width / median_style / median_asset
    left_kerb  / left_kerb_height  / left_walk_width  / left_walk_asset  / left_rail / left_props
    right_kerb / right_kerb_height / right_walk_width / right_walk_asset / right_rail / right_props
    deck_thickness / pillar_spacing / pillar_width / pillar_asset / ground_z

    # ── shape ───────────────────────────────────────────────────────────────
    tangent_mode: Enum{AUTO, SHARP, MANUAL}   # AUTO = Catmull-Rom through neighbours
    handle_in / handle_out: FloatProperty     # MANUAL: length; direction = the Empty's own rotation

    # ── junction only ───────────────────────────────────────────────────────
    fillet_radius / allow_cross / allow_uturn
    traffic_light / traffic_light_radius   # maps 1:1 onto intersection_kit.Arm, which already
                                           # supports the near/far dual-pole Japanese layout

    # ── connections ─────────────────────────────────────────────────────────
    links: CollectionProperty(RKA_Link)

class RKA_Link(PropertyGroup):
    target: PointerProperty(type=Object, override={'LIBRARY_OVERRIDABLE'})
    type:   Enum{SEGMENT, JUNCTION, AUX}
```

Every field declares `override={'LIBRARY_OVERRIDABLE'}` and `links` adds `use_insertion` — without
them a library-linked neighbour road (`tools/link_neighbors.py` seam editing) is read-only *and
invisible to the gate*. Decide up front whether cross-district seam work is link-override or
append-and-write-back; the schema differs.

### 1.2a Road-level base profile, station-level delta

A point in `INHERIT` mode costs one bool and takes the **road collection's** base profile; a point in
`OVERRIDE` mode replaces only the fields its mask names. This is exactly OpenDRIVE's shape (road
reference line + `laneSection` deltas along it), and it separates the two things that a naive
node-carries-everything model conflates:

- **topology nodes** — junctions, ramp gores. Genuinely vertices.
- **profile stations** — a lane opening, a median widening. Not topology at all.
- **shape points** — a bend. Neither.

Without it, a road whose cross-section changes 20 times is 20 objects each carrying a full ~30-field
cross-section, changing the lane width means editing 20 objects, and adding a bend forces you to
declare a cross-section there. (Blender's alt-click propagation does **not** cover
`CollectionProperty` items and is not a substitute.) `Insert Point` creates an `INHERIT` point;
`Collapse Redundant Stations` removes any `OVERRIDE` point whose values equal the interpolation
either side, so object count can go *down*.

**It is deliberately one bit, not a 30-field mask.** A per-field `BoolVectorProperty` over ~30 fields
is invisible state — Blender has no built-in per-field inherit/override widget, so it would be real
unbudgeted UI work, and nobody can hold a 30-bit mask in their head. A whole-profile switch plus four
genuine deltas is legible, and **the overlay must show which stations are overrides**, or the artist
cannot find where a cross-section changes. The same reasoning applies to `Apply Cross-Section To
Selection` (§4.2): the mask defaults to nothing and the panel prints the field list, because
structurally it is the same gesture as the old brush that silently rewrote medians.

**A junction may sit in the INTERIOR of a chain.** This has to be said explicitly or the model
re-creates redesign defect 3: if every crossing split both streets, a ring with 12 crossings would be
12 chains again — exactly the thing §1.3 exists to prevent — and each fragment would carry its own
base profile to keep in sync by hand. So an `INTERSECTION` point is an ordinary member of its road's
ordered chain that additionally carries `JUNCTION` links to members of other roads. `Split Road`
remains available; it is never *required* by a crossing.

**The point's world position is the profile anchor.** The profile is an ordered slot list built
**median-outward**, with `lane_profile.Profile.anchor = DIVIDE` — so `s = 0` is the centre divide and
lanes expand either way from the point exactly as specified. Read `matrix_world.translation`, never
`location` — junction members are parented.

**The Empty's transform IS the road frame at that station** — no extra properties needed:

| Transform channel | Means |
|---|---|
| translation | the station: position + **elevation** |
| local **+Y** (yaw) | travel direction, drawn as an arrow. `AUTO` aligns it to the chain tangent; `MANUAL` lets the artist rotate it — which is exactly how an intersection mouth's angle is set |
| roll about +Y | **superelevation / banking**. `road_geometry.required_superelevation(speed, radius)` computes the suggestion; the artist can override, and `Validate` reports the shortfall |
| pitch | *derived*, never authored — it is the grade between neighbouring stations |

FWD lanes sit on the keep-left side of the +Y axis, BWD on the other. Vertical curvature comes from
the same Catmull-Rom that smooths the plan view, so a crest or sag is authored by placing stations
at different Z.

**One owner of "where is slot *i*".** `lane_profile.slot_offset()` is the single lateral-position
function; no other module may compute a lateral offset. This is redesign defect 1 and it is the one
rule that must never be relaxed.

### 1.2c The authored record is a file, and the Empties are a view of it

**This is the assumption to break, and it is the one that made rewrites #1 and #2 total losses.**
Both stored the authored road state exclusively inside `.blend` IDProperties, so when the model
changed there was nothing to migrate — only a binary file to regenerate or abandon.

The source of truth is a **git-diffable `<stem>.roads.json` sibling of the `.blend`** — the same
convention `.lanekit.json` and `.seam.json` already use. The Empties are a **projection**: read on
load, written on build. One serializer/deserializer pair, and the discipline that the `.blend` is
not canonical.

What that buys, in order of how much it matters here:

- **A migration path to a fifth model.** Neither previous rewrite had one.
- **Roads become reviewable** — a lane count change is a diff line, not an opaque `.blend` delta.
- **The generated 90 % is authored headlessly.** `tools/island_v3_plan.py` already computes
  corridors, ramps, grades and supports in pure Python; it writes `.roads.json` directly and never
  opens a 2 000-Empty scene.
- **Shift+D / uid corruption stops being a data-integrity problem** and becomes a view problem —
  reload the view and it is gone.
- **Undo for generated content is `git checkout`.**

The three new pure libs plus the sidecar already *are* a headless DSL; this makes that explicit
instead of half-true. Adopt OpenDRIVE's vocabulary verbatim in the record and in the libs — *road /
laneSection / lane / junction / connection*, with `s` offsets — because the model is already
isomorphic to it (§1.2a), it makes the design explainable in one sentence, and it makes a future
`.xodr` export (RoadRunner / SUMO / esmini validation) mechanical rather than a fourth rewrite.

### 1.2b Identity and deletion — the two things that silently corrupt the graph

**A uid must survive a rename and must NOT survive Shift+D.** Blender has no `duplicate_post` handler
and `Object.copy()` deep-copies IDProperties verbatim. Duplicating a *whole road* is fine — Blender's
ID-remap pass rewrites pointers within the duplicated set. Duplicating **one** point gives a clone
carrying the *same uid*, whose links point at the **original's** neighbours while those neighbours
still point at the original: a half-connected graph with a uid collision, and nothing reports it.

Fix: the dirty pass rebuilds a scene-level `uid → object` dict and **validates uniqueness every
time**. A collision reallocates the *newer* object's uid and drops its inbound links, reported by
name.

**Deleting a point needs its own operator.** `PointerProperty(type=Object)` is a real, user-counted
ID reference. `bpy.data.objects.remove()` nulls it (the dangling-link path works), but merely
*unlinking* a point from its collection does **not** free it — it lives on as a zero-collection
zombie held by its referrers, invisible in the outliner, and it survives Purge Orphans. `Delete
Point` strips inbound links first, then removes.

### 1.3 A road is an ordered chain

`road_x` = `p000 → p001 → … → pN`, **FWD is increasing index**. That one convention removes the
per-edge left/right ambiguity the mesh-graph model has today (flipping an edge swaps
`sidewalk_left`/`sidewalk_right`).

A road is divided only by something real — a junction, or an authored break. **Never** by a lane
count changing (redesign defect 3: a 3278 m ring with zero crossings was built as 12 pieces).

Branches are separate roads joined by links. A road may be as long as the artist wants.

**A closed loop** is `pN → p000` by an ordinary SEGMENT link, and the road carries `is_loop`. Every
chain walk, arclength measure and profile interpolation needs an explicit wrap case — this is not
free, and it gets its own self-test (the island's 3278 m ring is a loop, so it is not a corner case).
The export sets `loop` on those lanes; `PathLaneRoute.loop` and `Curve3D.setClosed` already consume
it.

**A roundabout is a one-way loop road with N junctions on it** — the same thing OpenDRIVE does. The
fillet maths cannot produce a circulatory carriageway, so this is written down as *the* way to build
one rather than discovered later as a model change.

---

## 2. Connections

### 2.1 SEGMENT — point to point

The carriageway continues from A into B. Both stations build their slot list median-outward with
stable ids:

```
             median                                                        kerb
   [ F0 ] [ F1 ] [ F2 ] [ AF0 ]           standard lanes, then aux, outboard
```

A slot present at one station and absent at the other has **width 0** there, and
`lane_profile.interpolate()` (already written, self-tested) resolves the **surface**:

- **2 → 1 fwd** — `F1` is width 0 at B. It merges away over the A→B distance. Merging drops the
  **outermost** standard lane, because ids are assigned median-outward.
- **1 → 2 fwd** — `F1` is width 0 at A. It opens outboard.
- **`lanes_bwd = 0`** — a one-way road, at whichever stations it applies.
- **aux opening/closing** — `aux_fwd` 0 → 1 → 0 across three points *is* the acceleration lane, its
  taper, its buffer run and its close.

**Which lane dies is authored, not implied.** An integer `lanes_fwd` cannot say *which* lane is
lost, so each direction carries `drop_side ∈ {KERB, MEDIAN}` (default KERB). Verified against
`lane_profile` — both cases already resolve correctly and **no lateral spine shift is needed**,
because `ANCHOR_DIVIDE` holds `s = 0` on the divide in both:

```
3->2 fwd, drop_side=KERB     t=0  F0 2.25  F1 5.75  F2 9.25      t=1  F0 2.25  F1 5.75  F2 0.0w@7.50
3->2 fwd, drop_side=MEDIAN   t=0  F0 2.25  F1 5.75  F2 9.25      t=1  F0 0.0w@0.50  F1 2.25  F2 5.75
```

### 2.1a The taper ROUTE rule — the one thing that is NOT free

The widths are free. **The lane *routes* are not**, and this is the single most important correction
to make before writing code. Measured directly against `lane_profile.py`:

| Symptom | Measured | Consequence |
|---|---|---|
| A merging lane's centreline ends on the **lane line**, not in the receiving lane | 3→2 fwd: `F2` dies at offset **7.50**; `F1` centre is **5.75** and its outboard edge is **7.50** | the car finishes the merge straddling the paint, half a lane off |
| An opening aux lane's route **carries its first-live offset backwards** | `AF0` offsets `[7.85, 7.85, 7.85, 8.03, …]` while `F1` is at **5.75** | the route head sits 2.1 m outboard of the through lane — two wheels off the asphalt |
| `LANE_MIN_WIDTH = 0.5` **truncates the run** | `AF0` run starts at sample `i0 = 2` of 11 | the polyline stops ~20 % of the taper short — well past `LaneGraph.JUNCTION_RADIUS` (4.5 m), so it never chains and `maintainTraffic` reclaims the car as route-finished |

The first two are the exact defect `graph_export.py` already documents and hand-patches in prose; the
plan must not re-inherit it by reusing `lane_profile` unmodified.

**`lib/road_points.lane_taper_route()` is the single owner of the fix**, and nothing else may
compute a tapering lane's centreline:

1. While a slot is below `LANE_MIN_WIDTH`, **clamp its centreline to its inboard neighbour's
   centreline** (`lane_neighbors` gives the neighbour) instead of to its own zero-width position.
2. Blend from the clamped position to the true centreline across the live part of the taper, so the
   route enters the lane smoothly rather than stepping onto it.
3. Emit the run over the **full** station-to-station span — never truncated by `LANE_MIN_WIDTH`.
4. Emit an explicit successor from the dying slot into the receiving slot with
   `next_kinds = MERGE`, so `LaneGraph.explicitSuccessorsOf` closes the gap regardless of geometry.

Smoketest, with numbers: max lateral deviation from the receiving centreline ≤ 0.3 m, and the
exported tail within 4.5 m of its successor's head.

**Nothing about a taper is a length property.** The taper length is the distance the artist put
between the two points. The rate is *validated*, not authored — and the threshold is **derived from
`design_speed`** (`L ≈ W·S`, so a 3.5 m drop at 100 km/h wants 100–200 m, not a flat constant). A
violation is reported and drawn in red on the link.

### 2.2 JUNCTION — the intersection pad

Points with `role = INTERSECTION` that are mutually linked with `type = JUNCTION` form one **clique**
= one pad. The clique gets a stable `junction_id` and an auto-created parent Empty `JCT_<id>` at the
centroid; members are parented to it, so **dragging the parent moves the whole junction and dragging
a member moves one arm's stop line**.

**The member points ARE the stop lines.** The artist places the mouth directly. This is the single
biggest behavioural change from the current model, where the mouth is wherever a hidden setback
solve lands — and it is what permanently kills the junction-crater class of bug (a 15° crossing
asking a 136.7 m setback; 24 of 45 island pads over 1000 m²). A bad setback is now *a visible point
in the wrong place that you drag*, not a number you cannot see.

**This is what `blender/lib/intersection_kit.py` was already written for.** It is pure Python,
self-tested, and **arm-centric**: you hand it `Arm`s (position, bearing, lane counts, widths) and it
returns the pad. That is precisely the new model's shape — a member point *is* an `Arm` — whereas
`road_graph_solve.py` is node-centric (it *derives* where the arms end). Reuse
`build_junction_boundary`, `build_curb_corners`, `corner_fillet`, `build_lane_movements`,
`build_ports`, `turn_side`, `worst_movement_overshoot`, `recommended_tail_length` (the last two are
`Auto Setback`'s maths) rather than re-deriving any of it.

Pad construction:

1. Each member contributes a **mouth**: a cross-bar of that point's paved width (kerb to kerb),
   perpendicular to its forward axis, at its position. **The median is ignored** (per the
   requirement); the **side settings — kerb, footway, rail — are kept.**
2. Members are sorted by bearing about the centroid.
3. Between angularly adjacent mouths, a **corner fillet** of `fillet_radius`, tangent to both
   outer kerb lines; the corner's kerb + footway interpolate between the two arms' side settings.
4. Pad polygon = mouth cross-bars + fillet arcs, **triangle-fanned from the centroid**. A fan is a
   valid tessellation **iff the pad is star-shaped about the centroid** — that is the condition, and
   the gate checks it, because a hand-dragged mouth pulled closer to the centroid than a
   neighbouring fillet breaks it. (n-gon tessellation of a concave non-planar pad left measured
   0.38–0.49 m holes, which is why a fan and not an n-gon.)
5. Z follows the mouths, so a junction on a grade tilts instead of stepping.

**`Auto Setback` is whole-clique, idempotent and non-destructive — and it is the default path.**
"Place four mouths by hand" is not what any shipping tool does: RoadRunner solves junction extents
and exposes the result as draggable; CityEngine generates the crossing from street width; Cities:
Skylines solves it outright. The universal pattern is **solve, then override**, and at 49+ junctions
(150–200 mouths, re-placed every time an approach's lane count changes) hand placement is not a
workflow.

So: the points remain the authoritative stop lines — that debuggability win is the whole reason for
this design — but `Auto Setback` runs the **existing `road_graph_solve.solve()` over the entire
clique at once** and writes the member positions. This matters because that solver's couplings do not
survive being applied one point at a time: it takes the **max over both corners at a node**, clamps
**per chain jointly** (`max_trim_fraction` over the *sum* of a chain's two ends), bounds with
`max_setback_factor`, and only builds a corner fillet when `setback ≥ apex_reach + fillet_back`. Drag
one mouth in isolation and the neighbouring fillet silently stops being tangent.

Each member carries `setback_solved` (the last computed value) and `setback_locked`. Every build
re-solves the unlocked members and preserves the locked ones. The default loop is therefore *place
four points roughly, press Build* — with hand override where the 15°-skew cases need it.

**`setback_locked` is an explicit toggle with an overlay glyph — never inferred from "the artist
dragged it".** An accidental nudge would otherwise lock a mouth permanently and every future
`Auto Setback` would silently skip it: exactly the invisible state this rewrite exists to kill.

**Turn paths.** For each ordered member pair (A in, B out) and each (in-lane, out-lane), a cubic
bezier from A's lane end along A's inbound axis to B's lane start along B's outbound axis. Legality
is one shared rule set (`lib/lane_movements.py`), used by both the emitter and the *"why is there no
turn here"* explainer:

- no U-turn to the same arm unless `allow_uturn`;
- `allow_cross = 0` → no movement crossing opposing traffic;
- keep-left lane legality: 1-lane approach → L/S/R; ≥2 lanes → kerb lane L+S, median lane R+S,
  middle lanes S only; target lane clamped by index.

### 2.3 An intersection point may instead connect to a segment point

Per the requirement: an `INTERSECTION` point with a `SEGMENT` link to an ordinary point is simply a
segment connection — the pad-forming rule only applies to `JUNCTION` links. A T-junction is
therefore three intersection points in a clique, each continuing into its own road by a SEGMENT
link.

### 2.4 AUX — the ramp

No pad. No nose special case. An edge-alignment constraint and two authored links.

```
road_loop_e:   p012            p013            p014            p015
               aux_fwd 0       aux_fwd 1       aux_fwd 1       aux_fwd 0
               lanes_fwd 3     lanes_fwd 3     lanes_fwd 3     lanes_fwd 3
                                    │ AUX link
ramp_ic_en:                         p000 (RAMP_EXIT, lanes_fwd 1, lanes_bwd 0) → p001 → …
```

- The mainline point at the gore carries its ordinary `SEGMENT` link (main lanes → main lanes,
  **aux excluded**) plus an `AUX` link to the ramp's first point.
- The ramp point connects **only** to the aux slot, and to nothing else.
- **The constraint is edge alignment, not a pad**: the ramp's lane band edge at that station must
  coincide with the aux slot's edge computed from the mainline point's own profile.
  `Align Ramp To Aux` snaps the ramp point's position and axis; `Validate` reports the residual in
  metres and refuses to hide it.
- Buffer is authoring, exactly as specified: `p015 aux_fwd = 0` downstream gives the run in which
  the aux converts back, and the taper falls out of §2.1.
- The gore geometry is not drawn by a rule — it is the **outline of the union** of the two bands
  (§3.2). Where the mainline band and the ramp band overlap and separate, the boundary *is* the
  gore.

An entry ramp is the mirror image with `RAMP_ENTRY`.

---

## 3. Geometry

### 3.1 Sweep

Python resolves; Geometry Nodes sweeps. The split is deliberate and stays.

- **Python emits one polyline carrier per road** — sampled along the point chain (default 4 m step,
  a forced sample at every station), carrying the resolved per-sample attributes (paved half-width,
  lateral shift, median, kerb offsets/heights, footway widths, deck, pillar height, asset indices).
- **A GN layer stack sweeps it**: spine → carriageway → **markings** → median → kerbs → footways →
  deck → pillars → asset rows → finish. Each layer is pass-through (defect 7: a layer that replaces
  its input is a bug). Every socket names its unit (defect 8).
- Layers with nothing to build are not attached — a zero-width band still emits polygons, and a
  Named Attribute pointing at a missing name silently reads 0.

**Markings are a layer, not an afterthought.** `lane_profile.marking_runs()` already exists and is
self-tested, and it already solves the hard case ("the line appears when the lane does", so paint
survives a taper for free) — but a road with no centreline or lane paint reads as a grey ribbon from
the third-person camera and destroys the player's read of which way traffic goes. `mark_left` is on
the point (§1.2); build it.

**A declarative layer/attribute registry, not a growing bag of names.** Every layer declares the
attributes it reads (name, unit, default, required), and the build **asserts** each one exists on the
carrier. This is what keeps §3.3a's "adding a band is cheap" true: the carrier already needs ~10
per-sample attributes and each new band adds one to three, a missing Named Attribute reads 0 rather
than erroring, and at 30+ names an undeclared carrier becomes exactly the "one bag of constants" the
edge domain was — only wider and failing silently. It is also what makes a Blender upgrade that
rewires a socket a one-adapter change instead of an invisible regression.

### 3.2 The road edge is a boundary, not an offset

Everything outboard of the asphalt — kerb, gutter, footway, wall, railing, props — is placed by
successive outward offset from **the boundary of the union of every ribbon**, not by lateral offset
from one centreline. Measured on the current island: 257 of 3736 centreline kerb samples stand on
another road's asphalt; 0 of 3441 outline vertices do.

This is what makes gores, merges, flyovers and junction approaches work with *no* case analysis, and
it is why the whole `merge_corridor_ends` / `MERGE_WALL_*` / `RAMP_WALL_OPEN` tier does not exist in
this design. It is the `union → inflate` shape of a polygon-clipper pipeline.

**The one shape a boundary walk cannot express — decided now, not deferred.** Two ribbons that run
parallel and overlapping without ever converging have no crossing for the walk to find. There are
**60 such ends on the current island**, so this is not hypothetical, and "we'll escalate if it shows
up" is precisely how the previous two models died: a shape the core rule cannot express, bought back
one special case at a time.

**Decision: do the union with Blender's own mesh booleans over the band meshes**, and keep the
boundary walk only as the fast path where it provably agrees. `pyclipper` is not in Blender's bundled
Python and pure-Python Clipper ports are too slow for a live rebuild, but the boolean machinery is
already in-tree and already used for ground cutting (`legacy/ops_ground.py`). The walk stays because
it is cheap and gives clean parameterised runs for band offsets; the boolean is the authority when
they disagree, and the gate reports any disagreement over tolerance by object name. Settle this
**before step 4**, on one of the 60 real cases — not on a synthetic one.

### 3.3 Understructure and ground — derived from one number, never authored separately

This is the answer to "can the architecture be expanded for ground mesh and pillars when a segment
or aux lane is above ground level", and it is already the project's design of record —
`tools/island_v3_plan.py` §6, pure Python and self-tested:

```
delta = surface_z - ground_z      ->      support kind

  delta >  FILL_MAX (4.0)         PIER     soffit slab + columns every PIER_SPACING (30 m)
  delta >  AT_GRADE_TOL (0.4)     FILL     earth embankment, 1:1.5 batter
 |delta| <= AT_GRADE_TOL          NONE     the road sits on the ground; CUT the terrain under it
  delta >= -CUT_MAX (3.0)         CUT      trench walls
  else                            TUNNEL   bored, with a portal at each end
```

Four rules make this an *extension point* rather than a feature:

1. **`ground_z` is sampled inside `Build`, unconditionally — never by a button.** It raycasts the
   terrain under each point; the artist drags the road's Z and the support re-derives. Because the
   rule is a pure function of `delta` it can live in Geometry Nodes and update live during the drag.
   *Making this a button the artist must remember is precisely the `Cut Ground Under Road` failure
   this plan names as the confirmed root cause of the mesh-hole reports.* A road-level `follow
   terrain` toggle sets the Z; the sampling itself is never optional.
2. **The understructure shares the road's spine** (redesign defect 6 — deriving support per *segment*
   duplicated 94 objects over 26 km for a 24 km network, and built one ramp twice). It is a layer on
   the same carrier, never its own object.
3. **It follows the outline, so aux lanes and ramps are handled for free.** The deck and the pier
   caps are sized from the §3.2 boundary, not from a centreline half-width — so where an aux lane
   opens, the deck widens with it, and a ramp peeling off a viaduct carries its own deck out of the
   same union. This is the case the question is really about, and it needs no ramp-specific code.
4. **Pillars need a per-station escape hatch.** `PIER_SPACING = 30 m` with no override puts a column
   inside a building or in the street below — the canonical viaduct case — and rule 1 of §1.1 forbids
   hand-editing generated output, so the artist's only recourse would be moving the whole road. Add
   `pillar_skip` / `pillar_offset` per station **and** a gate clash test against district content.
5. **FILL is a battered trapezoid, not a prism.** The old model drew the embankment as a
   vertical-sided box at road width and separately fed it the *toe* width — a 16.5 m embankment under
   a 4.5 m ramp. `fill_footprint(surface_z, ground_z, half_width)` already returns the true toe
   half-width; the ground cut must use the **toe**, and the visible batter must use the **slope**.
   Name the unit in every socket.

**Ground is part of the same pass, not a separate manual step.** At `NONE` the road cuts the terrain;
at `FILL` it cuts to the toe and raises the embankment to meet the kerb; at `PIER` the terrain is
untouched except at the column footings; at `CUT`/`TUNNEL` it cuts and adds walls or a bore. All four
are the same operation with a different profile, driven off the §3.2 outline. That closes the
confirmed root cause of the "mesh holes" reports — `Cut Ground Under Road` being a manual button the
bake pipeline never called.

### 3.3a How the architecture extends — the recipe

The layer stack is the extension point, and the cost of a new band is deliberately small: **a couple
of point properties + one GN layer + one gate check.** Concretely, these all fit with no model
change:

| Want | Add |
|---|---|
| Noise barrier / crash barrier on an elevated deck | a band at the outline, height from a point property — the same shape as the kerb/wall already is |
| Retaining wall in a CUT | a `CUT` profile variant on the ground layer |
| Tunnel bore + portals | a `TUNNEL` profile variant, portals at the kind transition |
| Bridge parapet, bearing, abutment | bands + a station type at the `FILL→PIER` transition |
| Street lighting, catenary, signage rows | an asset row layer keyed to the footway offset (already how props work) |
| Drainage, kerb inlets | an asset row at the kerb line |
| Rail (tram in the carriageway, or its own alignment) | a slot `kind`, since `lane_profile` already has kinds beyond TRAVEL |
| Bus lane / cycle lane / parking bay | a slot `kind` — `SLOT_PARKING` and `SLOT_SHOULDER` exist already |

The two rules that keep this from rotting: **every layer is pass-through** (a layer that replaces its
input is a bug — redesign defect 7, which left bare columns holding no deck), and **no layer computes
a lateral offset** — it asks `slot_offset()` or the outline.

### 3.4 LOD

`build_piece.sh` bakes a second, cheap piece from whatever is in the `STREET_LOD_LOW` collection, and
`ZoneManager` shows it while a district is unloaded. The build emits a **flat, kerbless,
asset-free** version of each road surface into that collection from the same carrier — one extra
GN stack configuration, not a second model.

### 3.5 Collision — a first-class deliverable, not a footnote

**Today's graph roads export with no collision at all.** That one fact quietly costs four systems,
which is why this is promoted rather than mentioned:

| Consumer | What it loses |
|---|---|
| `NavBaker` (`ParsedGeometryType.STATIC_COLLIDERS`) | roads and footways contribute **nothing** to the pedestrian navmesh — on-foot AI path over whatever terrain collision happens to sit under the asphalt |
| `ImpactManager.resolveSurfaceType` | falls through to `DEFAULT` — bullets hitting a road produce generic decals and particles; there is no asphalt or concrete surface |
| vehicles | `ZoneManager.debugLog`'s own signal for this is "routed-but-0-moving = falling through missing ground" |
| the player | walks on terrain, not on the road they can see |

So the build emits, per road and per junction:

- **Separate proxies for carriageway and for footway/kerb** — the navmesh and the impact system must
  be able to tell a pavement from a road; one merged proxy cannot say it.
- **A `surface_type` tag on each proxy** (`ASPHALT`, `CONCRETE`, …) so `HittableBody` /
  `ImpactManager` resolve it. Adding a surface type is already a documented three-step recipe;
  this just supplies the tag from the profile's slot kind.
- **A `ped_access` bool per road**, routing the proxy to a layer `NavBaker` skips. Without it a PIER
  deck bakes as walkable geometry and an on-ramp is a continuous walkable slope — **AI will walk onto
  the expressway.** `AGENT_MAX_CLIMB = 0.5` makes a 0.15 m kerb climbable and a 1.0 m expressway wall
  not, so the ground case is already right; the elevated case is not.

Road collision stays a separate mesh from ground/terrain collision — they change on independent
schedules.

### 3.6 What the game needs the road to emit

The plan's job is not only a correct road. These are cheap **because the geometry already computes
them**, and expensive to retrofit because they change the schema:

- **Crossing markers.** §2.2 step 1 already builds a mouth cross-bar per junction member — *that is
  the crosswalk*. Emit it as a marker and ped-crossing behaviour, crosswalk paint and "AI stops at
  the kerb" all have an anchor. Ambient pedestrians are the cheapest alive-ness in an open-world
  shooter and currently have nothing to key on.
- **Parking-stall markers** from the `SLOT_PARKING` band (position + heading every ~6 m). Parked cars
  are simultaneously street life and cover, and `Vehicle` already supports parked-`setSleeping`.
- **`road_class` reaching the spawner**, so an arterial is busy and a backstreet is dead. Today
  traffic density is per zone-region marker only.
- **A road name** per road, for minimap/UI later.
- **The always-resident lane manifest.** District lanes stream out, so GPS / police routing / race
  lines cannot use live `PathLaneRoute` nodes — the answer is a baked always-resident graph from the
  sidecars. **This is the one moment the schema is open**; emit the manifest in v2 or re-open it
  later.

---

## 4. Authoring UX

### 4.1 Operators

| Operator | Gesture |
|---|---|
| `New Road` | creates the collection + first point |
| `Extend Road` | duplicate the end point forward along the tangent, auto-link SEGMENT (the E-key loop) |
| `Insert Point` | split a link; the new point's profile is **interpolated**, so inserting changes nothing visually |
| `Connect Selected` | 2 points → offers SEGMENT / JUNCTION / AUX filtered by role, validates before writing |
| `Make Intersection` | N points → sets roles, builds the JUNCTION clique, creates `JCT_<id>` parent |
| `Make Ramp` | mainline point + ramp point → sets roles, writes the AUX link, runs Align |
| `Align Ramp To Aux` | snaps the ramp point onto the aux slot edge |
| `Auto Setback` | moves intersection points to a solved stop-line distance |
| `Split Road` / `Reverse Road` / `Merge Roads` | corridor surgery |
| `Copy / Paste Cross-Section` | between points, with a field mask |
| `Select Road` / `Select Junction` | select the whole corridor or the whole clique |
| `Duplicate Road` / `Duplicate Junction` | the **sanctioned** copy gestures — raw Shift+D corrupts uids and links (§1.2b), so the overlay tints an unsanctioned clone red rather than letting Build be where you find out |
| `Repair Links` | actionable fix for the dangling/zombie cases the gate reports — the artist cannot see them in the outliner |
| `Fit Ramp Grade` | uses `island_v3_plan.run_needed(dz, kind)` + `grade_profile()` to place a ramp's Z stations, instead of iterating by trial against a gate that only complains afterwards |
| `Build` / `Build All` / `Validate` / `Export` | |

**Craft tooling, not topology tooling — this is the week-one ask.** Object-mode Empties lose Edit
Mode's proportional edit, edge slide and vertex snapping, so these are not luxuries:

- **snapping** (point-to-point, grid, terrain surface) and **measure** (span between two points,
  total road length in the panel)
- **`Resample / Insert N Stations Evenly`** — mandatory the moment a road is lengthened
- **road styles** — save and apply a named base profile across roads (the road-level base profile is
  half of this; the library is the other half)
- **mirror / symmetry**, **bulk select by class** ("every T1 road"), numeric station-spacing entry
- **reference plan underlay** — `island_v3_plan.road_corridors()` already computes the corridors;
  instantiating them as guides is nearly free and is how a district actually gets laid out
- **`Move Junction With Neighbours`** — a falloff drag, because moving a `JCT_*` alone leaves a kink
  20 m out on every approach
- **follow terrain** as a road-level toggle, never a button (see §3.3)

### 4.2 Panel

A **point inspector**, not a stamping brush: the N-panel edits the active point's own properties
directly, so what you see is what that station is. Junction settings appear when a `JCT_*` parent is
active, and the **road's base profile** (§1.2a) when a road collection is.

Multi-point editing still needs a real operator — `Apply Cross-Section To Selection`, with a field
mask. Blender's native alt-click propagation does **not** reach `CollectionProperty` items and only
works within a homogeneous selection showing the same property, so it is not a substitute. What is
being avoided is the *old* brush's failure mode — a scene-level stamp with eleven `use_*` toggles,
eight ticked by default, that silently rewrote the median while you meant to change a lane count.
Here the source is the **active point**, the mask defaults to **nothing**, and the panel prints the
exact field list before you press it.

### 4.3 Overlay

A GPU overlay is the only way a network of hundreds of points is legible. It draws, per point: lane
count numerals per direction, a travel-direction arrow, an aux badge, the kerb/footway extent; per
link: a coloured line by type (SEGMENT / JUNCTION / AUX) with the **taper-rate violation drawn in
red**; per junction: the clique ring and its member bearings.

### 4.4 Live rebuild

**Split it in two — this is the pattern that works, and it deletes the whole re-entrancy bug class.**
A trailing-edge debounce does *not* solve the modal case: a G-drag that pauses for 120 ms fires it,
and creating or removing objects while `transform.translate` is modal fights or crashes the operator,
with no clean public "is a modal operator running" API.

- **During the drag — the §4.3 GPU overlay only.** A `draw_handler` needs no `bpy.data` write, so it
  is always safe, and the artist sees the ribbon, lane counts and link colours follow the point in
  real time.
- **On settle — the mesh rebuild.** `depsgraph_update_post` marks the moved point's road dirty plus
  any road across a JUNCTION or AUX link; a `bpy.app.timers` debounce rebuilds only those, gated on
  `bpy.context.mode == 'OBJECT'` and an empty `window.modal_operators`. Full-network rebuild stays a
  button.

Six concrete landmines, all of which must be handled explicitly:

| | |
|---|---|
| `depsgraph.updates[i].id` is the **evaluated** datablock | reach the authored Empty via `.id.original`, and filter on `is_updated_transform` |
| writing into `ROAD_MANAGER_GEN` re-triggers the handler | guard with a re-entrancy flag **and** "ignore any update whose id lives in GEN", or the debounce never settles |
| `bpy.app.timers` do not survive a file load, and non-`@persistent` handlers are cleared on `load_post` | re-arm from a `@persistent load_post` |
| undo is a memfile snapshot | `undo_post` re-marks everything dirty; also the reason object count is a real cost (§8) |
| `obj.parent = jct` does **not** set `matrix_parent_inverse` | set `obj.matrix_parent_inverse = jct.matrix_world.inverted()` yourself (`parent_set(keep_transform=True)` needs a context override headless), and **lock the JCT parent's rotation and scale** or a stray R/S rescales every mouth width |
| `matrix_world` is stale until the depsgraph updates | never read a member's world position in the same pass that moved its parent |

**Position ownership at a junction is decided, not left ambiguous:** a member point belongs to both a
road chain and a `JCT_*` parent, and **the JCT owns position**. The chain reads `matrix_world`
(§1.2 already requires this), and `Select Road` **excludes junction members** from a drag — otherwise
selecting the road and dragging tears the junction apart.

---

## 5. Validation — the gate

`ROAD_KIT_REDESIGN.md` §5a: **a build that fails the connectivity check is a failed build.** The
gate is built first and wired into `Build`, not left as an advisory console script.

| Check | Fails on |
|---|---|
| identity | duplicate uid; dangling link target; a zero-collection zombie point |
| links | role/type mismatch, asymmetric JUNCTION, AUX from a non-ramp role |
| taper rate | a lane drop/open faster than the `design_speed`-derived `L ≈ W·S` |
| taper route | a merging lane's tail more than 0.3 m off the receiving centreline, or more than 4.5 m from its successor's head (§2.1a) |
| ramp | aux-edge residual over tolerance |
| junction | zero-area or self-intersecting pad; **pad not star-shaped about its centroid** (the fan's precondition); a member no movement reaches |
| alignment | a mouth whose angle differs from its road's tangent by more than a threshold |
| horizontal | curve radius below `road_geometry.min_radius(design_speed, e_max)` |
| vertical | grade over `max_grade` |
| lanes | any drivable lane with no authored successor and no predecessor |
| naming | a lane id that is not globally unique across the world |
| support | a point with no sampled `ground_z`; an embankment toe overlapping a neighbouring road or building footprint; a pier landing inside another road's carriageway |

Each failure names the **object** to fix, because the artist fixes objects, not indices.

**Advisory, not gate failures: superelevation and crest/sag sight distance.** They stay as overlay
colour and a panel readout. This is a third-person shooter, not a traffic simulator: ambient cars run
a throttle governor with pure-pursuit steering and cannot perceive banking, and neither can the
player at 60–100 km/h. A gate that fires on every hand-authored road and gets overridden is a dead
gate — the plan's own defect 11 — and it would spend the fidelity budget on highway QA the game
cannot see while the roads still have no collision.

---

## 6. Godot export — `.lanekit` v2, redesigned rather than preserved

The sidecar and the Java that reads it are **also in scope**. Today's schema is a flat lane list of
dense polylines, and it forces three avoidable weaknesses:

| Today | Why it hurts |
|---|---|
| `points` is a dense polyline; `Curve3D` control points get **zero in/out handles** | `getBakedPoints()` then returns the polyline back — the "curve" is not a curve. Cars corner-cut and micro-jitter through every bend, and a 21-point lane is 21 control points where 4 would do |
| 924 lanes → **924 `PathLaneRoute` nodes**, each with a `Path3D` child and a `Curve3D` resource | ~2 800 nodes and resources instantiated per world load, all static, all registering individually |
| No speed limit, road class, junction id, grade or banking | the AI cannot slow for a bend it has not entered, and roads-v2 Phase 2's `JunctionArbiter` (signals keyed on approach/turn) has no data to key on |

### 6.1 What v2 emits

**Lane geometry as real cubic beziers.** Each control point is `{p, in, out}`, fed straight into
`Curve3D.addPoint(pos, in, out)`. The Blender side already *has* the tangents — the chain is a
Catmull-Rom/bezier spline (§1.2) and turn connectors are literally beziers — so today's polyline is
information being **thrown away**, not information we lack. Expect roughly a 5× drop in control
points and a genuinely smooth path.

**Per-lane metadata the AI can actually use:** `speed_limit`, `road_class`, `lane_index`,
`lane_width`, `grade`, `banking`, `junction_id`, `approach`, `turn`, `spawnable`.

`spawnable` replaces the `turn == ""` convention outright. Deriving "can a car spawn here" from
whether a *turn letter* is blank is why all 351 island through lanes are currently unspawnable — an
explicit boolean cannot fail that way.

**A `junctions[]` array**, which does not exist today: id, centre, member arms with bearing and
lane ranges, signal grouping, right-of-way. This is the data `JunctionArbiter` needs, and it is free
on the Blender side because the clique already knows all of it. Emitting it now means the world does
not need re-baking when Phase 2 lands.

**`arms[]`** for per-arm lane width, and `roads[]` for road-level identity (name, class, zone).

### 6.2 The Godot side

- `WorldBaker.buildPathLaneRoute` reads `{p, in, out}` and the new fields.
- `PathLaneRoute` gains `speedLimit`, `roadClass`, `junctionId`, `spawnable`, `grade`, `banking`;
  `ZoneManager.isSpawnCandidate` tests `spawnable` instead of inferring from `turn`.
- `VehicleAIController` gets a real speed target per lane instead of inferring from turn letters.
- **Separable and later:** consolidate the 924 nodes into **one `LaneNetwork`** holding the lanes as
  data, removing ~2 800 nodes from every world load. `Lane` being an interface is *not* enough to
  make this a drop-in: `LaneGraph.forScene` gates on `anyLane instanceof Node` with a live
  `getTree()`, `LaneGraph.collect` walks the scene tree for `Lane` **nodes**,
  `ZoneManager.registerRoute` keys on `Node3D.getName()`, and `PathLaneRoute.resolveRoute` /
  `entryPoint` are node-based. A data-only lane breaks all four. Worth doing, sized as a real runtime
  refactor with a before/after node-count and load-time measurement — **after** v2 lands, never
  alongside the authoring rewrite.

**Still true, and still checked by the gate:** Godot axes `(bx, bz, −by)` with exactly one conversion
site; lane names globally unique (`ZoneManager.routeByName` is one flat world-wide `TreeMap`);
`next` / `next_weights` / `next_kinds` positionally parallel; a lane's tail within 4.5 m of its
successor's head; `loop` emitted for a ring road. `blender/lib/lane_kit.py` `combine_pieces()`
remains the emitter back-end — it is extended with the v2 fields, not replaced.

### 6.3 The v1 fields, for reference

The full field list `WorldBaker.buildPathLaneRoute` reads today — the v2 superset must keep every one
of these working, because the district pieces already baked against them:

`id`, `points`, `kind`, `from_arm`, `turn`, `loop`, `zone_id`, `next`, `next_weights`, `next_kinds`,
`inner_lane`, `outer_lane`, `link_group`, `link_role`, plus top-level `arms[].{name, lane_width}`.

Ids stay `<arm>_<F|R><idx>` / `c<junction>_<in>__<out>` **for outliner legibility only** — the arm is
read from the `from_arm` field, never parsed out of the id, so uid naming is free. (The
`lastIndexOf('_')` parsers in `WorldBaker` serve the Empty-marker `VehicleRoute` path, not the
sidecar.) A raw GUID would be unreadable in the Godot outliner; pick a slug and let the gate check
global uniqueness.

`blender/lib/lane_kit.py` `combine_pieces()` already namespaces ids by piece, stamps `zone_id`, emits
`arms`, and resolves `next_refs → next/next_weights/next_kinds` and
`neighbor_in/out → inner_lane/outer_lane`. `point_export.py` produces per-road piece dicts and hands
them to it; v2 extends it rather than replacing it.

**Lane-change adjacency is not optional.** An exit ramp is *unusable* if a car cannot move from a
through lane into the aux lane — redesign defect 10 was exactly this ("the gore geometry is fine,
the exits are unusable, because an exit lane has no way in except a lane change and no lane-change
edge exists"). In this model the aux lane is a slot in the same station's profile, so
`lane_profile.lane_neighbors()` yields the adjacency for free. Emit `inner_lane` / `outer_lane` on
every lane. (`WorldBaker` already parses them into `PathLaneRoute`; nothing in the AI reads them
*yet*, so this also flags a matching Godot-side gap.)

**Three live defects that v2 removes by construction:**

- **Every exported through lane is unspawnable today** — the exporter omits `turn`, `WorldBaker`
  defaults `kind == "through"` to `"S"`, and `isSpawnCandidate` rejects any non-empty turn. So all
  351 island through lanes bake as junction interior and ambient traffic spawns unrouted at the zone
  centre. → the explicit `spawnable` flag (§6.1).
- **`lane_width` is dropped** — Java reads it only from a top-level `arms` array nothing emits.
  → `arms[]`, plus per-lane `lane_width`.
- **`zone_id` is absent**, so `ZoneManager.findRoute` strategy 2 can never match. → emit it.

---

## 7. Files

### New addon — `blender/addons/road_kit_authoring/`

| Module | Owns |
|---|---|
| `point_model.py` | `RKA_Point` / `RKA_Link`, uid allocation, chain + clique walking, schema migration, resolvers |
| `point_ops.py` | every operator in §4.1 |
| `point_profile.py` | station → `lane_profile.Profile`; the slot-id vocabulary (`F0…`, `R0…`, `AF0…`, `MED`) |
| `point_solve.py` | chain → arclength samples; clique → mouths, fillets, pad polygon, turn paths |
| `point_build.py` | carrier + outline emit, GN stack, collision proxies, `ROAD_MANAGER_GEN` lifetime |
| `point_nodes.py` | the GN layer vocabulary (spine / band / deck / assets / pillars / finish) |
| `point_edges.py` | the union-boundary outline and the outward band offsets |
| `point_export.py` | `.lanekit.json` emit + viewport lane preview |
| `point_validate.py` | the §5 gate |
| `point_overlay.py` | the §4.3 GPU overlay |
| `point_panel.py` | the point inspector |
| `point_live.py` | depsgraph dirty-set + debounced rebuild |

### New pure libs — `blender/lib/` (no bpy; `python3 lib/<x>.py` self-tests)

- `road_points.py` — chain/station model, Catmull-Rom + bezier tangents, arclength sampling,
  profile interpolation between stations.
- `lane_movements.py` — junction movement legality, the single rule set behind both the emitter and
  the explainer.
- `road_outline.py` — band union boundary + successive outward offsets.
- `road_support.py` — the `delta = surface_z − ground_z` rule and the FILL/PIER/CUT/TUNNEL profiles.
  **Move** `support_kind` / `fill_footprint` / `pier_stations` here from `tools/island_v3_plan.py`
  and have the plan import them, so the island planner and the road builder cannot disagree about
  what goes under a surface (that divergence is defect 1's shape, one level up).

### Godot-side changes (§6)

`WorldBaker` (bezier control points + the v2 fields), `PathLaneRoute` (`speedLimit`, `roadClass`,
`junctionId`, `spawnable`, `grade`, `banking`), `ZoneManager.isSpawnCandidate` (test
`spawnable`), `VehicleAIController` (speed target from `speedLimit`, bend anticipation from
`road_class`/`grade`). `LaneNetwork` is a separate, later change.

### Reused as-is (engine-free, self-tested, and they fit this model without bending it)

`lane_profile.py` (slots, `interpolate`, `slot_offset`, `lane_runs`, `marking_runs`,
`lane_neighbors` — the design already depends on it; note `marking_runs` makes a lane line that
opens and closes with a ramp the *same* mechanism as the lane, so markings survive a taper for
free), `intersection_kit.py` (the arm-centric junction library — see §2.2), `road_geometry.py`
(curvature / design-speed analysis), `kit_common.py` (collision proxy + export conventions).
`road_graph_solve.py` is **imported, not mined** — `Auto Setback` calls `solve()`. Copy-pasting half
of it drops the clamp system its fillet construction is coupled to (§2.2); mine only what genuinely
does not fit. `lane_kit.py` `combine_pieces()` is the export back-end (§6).

**Ground conforming is a first-class step, not a manual button.** `legacy/ops_ground.py` boolean-cuts
a road's own XY footprint out of the terrain, and it was wired to exactly one panel button that the
bake pipeline never called — the confirmed root cause of the "mesh holes" reports. In the new addon
the footprint is a by-product of the outline (§3.2), so `Cut Ground` runs as part of `Build All` and
is covered by the gate.

### Archived — at step 7, not step 0

All `graph_*.py` and `smoketest_graph_*.py` move to `legacy_graph/` and are unregistered **once the
island regenerates green on the new model**. The existing `legacy/` folder (25 kLOC of the
per-piece model, already dead) is **deleted in the same commit** — shipping three road systems is
how this repo got here.

### Island

`blender/tools/island_v3_to_points.py` — a **fresh** generator from `tools/island_v3_plan.py`, written
after the model is proven. It authors ramps explicitly (the plan layer already knows every
gore/touchdown pair), so connectivity is data rather than inference. Hand-authoring any part of the
island is acceptable; the island does not get a vote on the design.

---

## 8. Order of work

Gate first (redesign defect 11: *a gate that cannot pass is worse than no gate*).

| # | Step | Done when |
|---|---|---|
| **−1** ✅ **DONE 2026-08-22** | **Ship the Godot fixes against TODAY's pipeline.** Measured on the shipped sidecar: `zone_id` on **0 of 924** lanes, `turn` key missing on all **351** through lanes (→ `WorldBaker` defaults `"S"` → `isSpawnCandidate` rejects → **every through lane unspawnable**), no `arms[]` (so every lane baked at the 3.5 m default) | **Done in `graph_export.py` alone — no Java change needed**, because `present("")` is true in `WorldBaker.jsonString`, so an empty string overrides the `"S"` default where a missing key cannot. `collect(zone_id=…)` defaults to the graph object's name; new `arms_of(lanes)` derives the array from the through lanes. Re-exported: 351/351 through lanes now `turn: ""` **and** carry `zone_id`, 107 arms with real widths (4.5 / 3.25, not 3.5), lane+connector counts **unchanged** at 351/573, `check_lanekit_graph.py` green |
| 0 | Write this design into the repo as `blender/ROAD_POINT_GRAPH.md` (the design of record). **Leave `graph_*.py` registered and the island buildable.** The new addon registers alongside under its own panel | the doc is committed; both panels load; the old pipeline still regenerates the island |
| **0.5** ✅ **DONE 2026-08-22** | **SPIKE** — 2 000 Empties (40 roads × 50), each with a 10-field `PropertyGroup` + a `links` `CollectionProperty`, plus `depsgraph_update_post` and a timer. Blender 5.2, headless | **Results in §8a. Both decisions settled: Objects are the carrier (the contingency is NOT needed), and all four predicted correctness landmines are real.** |
| **1** ✅ **DONE 2026-08-22** | `lib/road_points.py` (incl. `lane_taper_route`), `lib/lane_movements.py`, `point_model.py`, `point_profile.py`, `point_validate.py` | **All six `python3` self-tests green** (33 assertions). The testbed — 6-point road + 4-arm junction + ramp — is built in `point_validate.build_testbed()` purely through the data model, gates clean, and round-trips byte-stable through `.roads.json` (4.5 KB, 324 diffable lines) with the gate still green on the RELOADED record. Seven deliberate defects are caught by code and object name: half-written JUNCTION link, chain hole, dangling link, taper-too-short, ramp edge residual, ramp with no aux slot, ramp back-link. **Four corrections were forced out by writing the tests** — see §8b |
| **2** ✅ **DONE 2026-08-22** | `point_ops.py` (15 operators) + `smoketest_point_ops.py` | **All 8 operator smoketests green** under `blender --background --python-exit-code 1`. The step-1 testbed is rebuilt entirely through `New Road` / `Extend Road` / `Make Intersection` / `Make Ramp` / `Insert Point` / `Delete Point` / `Connect Selected` / `Apply Cross-Section` and gates clean; the `.roads.json` written by `Save Road Record` rebuilds the Empties byte-identically via `Load Road Record`. Registers alongside `graph_*` with a clean disable/re-enable cycle. Two real bugs found — see §8c. **Deferred to their own steps** (not part of step 2's criterion): `Auto Setback` and `Fit Ramp Grade` (need the solver, step 4), `Split`/`Reverse`/`Merge Roads`, `Resample`, `Duplicate Road`/`Duplicate Junction`, `Repair Links`, and the craft tooling of §4.1 |
| **3** ✅ **DONE 2026-08-22** (Blender + Java; in-engine run BLOCKED, see §8d) | `point_export.py` + `.lanekit` **v2** + the Java reader (§6.1–6.2) | **8 export self-tests green**, and `check_lanekit_graph.py` passes on the testbed: 14 through lanes + 14 connectors, **junction gaps 0.000 m** (v1 needed up to 4.5 m of slack), 0 reversed. 3 bezier control points replace 60 polyline points on a mainline lane. `spawnable` is explicit, `junctions[]`/`arms[]`/`roads[]` emitted, aux lanes carry `inner_lane`. Java side compiles and `./gradlew build` is green on the 0.16.3 plugin: `WorldBaker` reads `{p, in, out}` with a v1 `points` fallback, `PathLaneRoute` gained `speedLimit`/`roadClass`/`junctionId`/`grade`/`banking`/`spawnable`+`spawnableExplicit`, `isSpawnCandidate` tests the flag (v1 inference kept), `CruiseState` paces off `effectiveCruiseSpeed()`. **Not yet run inside Godot** — see §8d |
| **4** ✅ **DONE 2026-08-22** | `point_solve.py`, `point_edges.py`, `point_nodes.py`, `point_build.py`, `lib/road_support.py` | **10 solve + 7 edge self-tests green under plain `python3`, 10 build smoketests green headless**, proven against the three shapes that killed the previous two models: a **gore**, a **15-degree skew junction**, and a **parallel overlap that never converges**. `lib/road_support.py` MOVED out of `tools/island_v3_plan.py` (which re-exports it), verified behaviour-neutral. **§3.2's open decision is settled — differently from the plan: there is no polygon clipper and no boundary walk, see §8e.** Two real defects found by the tests — see §8e. |
| **5** ✅ **DONE 2026-08-22** | `point_panel.py`, `point_overlay.py`, `point_live.py` | **9 smoketests green.** `point_live.dirty_set()` is asserted DIRECTLY rather than inferred from what geometry exists: dragging a point marks its road plus every road across a JUNCTION or AUX link, and nothing else; a write into `ROAD_MANAGER_GEN` marks nothing (the debounce settles); undo re-marks everything. **A seventh landmine, not on the plan's list of six, was found here and is real** — see §8e. |
| 6 ⏸ **DEFERRED (user's call, 2026-08-22)** | `island_v3_to_points.py` | The island is to be redesigned or re-authored on the new model later; it explicitly gets no vote on the design, so it is not a gate on steps 4–7. The step-4/5 acceptance shapes (gore, skew, parallel overlap) are therefore **constructed deliberately in the smoketests** rather than found on the island — which is stricter, not weaker: they are reproducible and they fail loudly. |
| **7** ✅ **DONE 2026-08-22** | **Archive.** `graph_*.py` + `smoketest_graph_*.py` → `legacy_graph/`, `legacy/` (91 files, the per-piece model) **deleted** | **ONE road system in the tree.** `smoketest_point_addon.py` asserts it: the addon enables and registers the point model end to end, the archived graph model registers NOTHING, no `graph_*.py` remains beside the live modules, and disable leaves no operator, panel or `Scene` property behind. `island_v3_to_graph.py` and `ramp_merge_testbed.py` moved into `legacy_graph/tools/` with the model they drive. `legacy_graph/README.md` records what was replaced, why, and where each surviving piece went. |

**Two models at once is a real cost — and the smaller one.** Running the old and new addon side by
side for the duration violates the "one owner of the cross-section" rule *at the tool level*, which
is why the earlier draft archived on day one. But archiving at step 0 leaves the world unbuildable
across steps 1–6, and the most likely way this project dies is exactly that: attention moves to the
game, and the repo ends up carrying three half-migrated road systems instead of two. The mitigation
is discipline, not sequencing — **the new addon never reads or writes the old model's data**, so
there is no shared owner to diverge; they are two programs that happen to ship together.

**Why the spike is step 0.5.** The two things that can kill this design are the Blender carrier under
real load and uid/link integrity under Shift+D and undo. Neither is answerable by a pure-Python
self-test, and both are one-line changes now and rewrites at step 5.

**The contingency — measured and NOT needed.** The fallback was a hybrid: Objects only for genuine
ports, a mesh (vertex = station) for the ~90 % that are only shape points. §8a says the object cost
is not there. **Objects for every point, as the requirement asks.** Recorded so it is rejected by a
measurement rather than by preference — and so it can be revisited if the viewport caveat in §8a
turns out to bite.

## 8a. Spike results (2026-08-22, Blender 5.2 headless, 2 000 points / 2 003 objects)

**Performance — the object-count worry does not survive contact:**

| | measured |
|---|---|
| build the 2 000-point scene | 0.045 s |
| **undo push** (the drag-loop tax — global undo is a memfile snapshot) | 0.057 s first, **0.003 s** thereafter |
| save `.blend` / reload `.blend` | 0.010 s / 0.073 s |
| 50 transforms with a `depsgraph_update_post` handler attached | 0.030 s total = **0.6 ms per transform**, of which **0.044 ms** is the handler |

A 3 ms undo snapshot and a 0.6 ms transform are not a workflow problem. **Caveat, stated rather than
hidden:** headless has no viewport, so this measures data-model cost, not viewport redraw or Outliner
cost during a real drag. Those are the remaining unknowns, and they are re-measurable the first time
a real scene exists — but they are redraw costs, not architecture costs.

**Consequence for §1.2a:** `INHERIT`/`OVERRIDE` is **not** justified by performance — 30 fields ×
2 000 objects is cheap. It stays purely on *authoring ergonomics* grounds (not editing 20 objects to
change one lane width), which is why it is one legible bit plus four deltas and not a 30-field mask.

**Correctness — all four predicted landmines confirmed, none surprising:**

| | result | what it forces |
|---|---|---|
| Shift+D on **one** point | clone carries the **same uid**, and its link points at the **original's** neighbour | §1.2b uid-uniqueness validation every dirty pass; `Duplicate Road` / `Duplicate Junction` as the sanctioned gestures |
| Shift+D on a **whole road** | **49/49** links remapped inside the duplicated set | whole-road duplication is safe — Blender's ID-remap pass does the right thing |
| `bpy.data.objects.remove()` | holder's pointer **nulled** | the dangling-link path works as designed |
| collection **unlink only** | object still in `bpy.data.objects`, `users = 1` — an invisible **zombie** | `Delete Point` must strip inbound links first; the gate must report zombies |
| `bpy.app.timers` across a file load | **does not survive** | §4.4's `@persistent load_post` re-arm is mandatory, not defensive |

---

## 8b. Step 1 results (2026-08-22) — the four corrections the tests forced

Recorded here for the same reason §8a is: each of these was *believed settled in the plan text* and
turned out to be wrong the moment a test was written against it. Two are model corrections, two were
bugs in my own new code.

**1. A through road contributes TWO mouths to a crossing, so chain-adjacent `INTERSECTION` points are
joined by the PAD, not by a SEGMENT link.** The gate's `check_chains` originally demanded a SEGMENT
link between every pair of chain-adjacent points. That is unsatisfiable for the mouths either side of
a junction — and "fixing" it by splitting the street at the crossing would have reintroduced redesign
defect 3 (the 3278 m ring built as 12 pieces) in a new hat, which is the exact thing §1.2a's
"a junction may sit in the INTERIOR of a chain" exists to prevent. `check_chains` now accepts SEGMENT
**or** JUNCTION adjacency.

**2. `design_speed` is a ROAD-level fact and belongs on the base profile.** Setting it on an
`INHERIT` station is silently ignored — it is not one of the four `DELTA_FIELDS`, and it is not one of
the per-station passthroughs (shape / structure sampling / junction state). This is correct and worth
keeping (a road has one design speed; a station that genuinely differs uses `OVERRIDE`), but it is
exactly the kind of silent no-op §1.2a warns about, so `point_validate.self_test` now proves it.

**3. The taper-route check indexed a full-length array as if it were run-relative.** `LaneRoute.points`
spans **every** sample of the chain — that is §2.1a rule 3, the fix for `LANE_MIN_WIDTH` truncation —
while `i0..i1` merely report where the lane physically *exists*. Comparing `points[-1]` against
`recv.points[i1 - recv.i0]` compared two different **longitudinal** positions and reported a 20.00 m
error on a perfectly good merge. The invariant that is actually worth gating is that a merging lane's
route **ends on its receiver's centreline** (measured: 0.00 m on the testbed's 2→1 merge), plus
`len(points) == len(samples)` as the direct statement of rule 3.

**4. The ramp residual must be measured to the ramp's nearest BAND EDGE, not its centre.** §2.4 says
the constraint is edge alignment; the first implementation subtracted a half-width from a
centre-to-edge distance, which is only right when the ramp leaves on one particular side. It now takes
the smaller distance over the ramp band's two edges — and for a one-way ramp `paved_extents` gives
`(0.0, w)`, so the ramp point's own divide **is** its inboard edge, which is why the testbed ramp sits
exactly on the aux slot edge at `(480, 11.0)` with a 0.00 m residual.

**Two things the plan got right and the tests confirmed numerically:** `drop_side = MEDIAN` needs
lane RENUMBERING at the narrow station (`F1, F2`, not `F0, F1`) but **no lateral spine shift** — the
surviving lanes hold their offsets, `2.1a`'s measured `F1 1.75 / F2 5.25` — and an aux lane's whole
life (`aux_fwd` 0 → 1 → 1 → 0 across four stations) is the acceleration lane, its taper, its buffer
and its close, with markings following for free from `marking_runs`, and **no special-case code**.

**One latent sharp edge, deliberately not fixed yet:** two points can carry only ONE link between
them, and `PointData.link_to` *retypes* an existing link rather than adding a second. That is the
right model (an AUX link and a SEGMENT link between the same pair would be contradictory), but the
silent retype belongs behind `Connect Selected`'s validation in step 2, not in the raw model.

## 8c. Step 2 results (2026-08-22) — the two bugs the operator tests found

**1. Blender object names are GLOBAL, and the chain order IS the name order.** `_next_point_name`
checked for a free name only *within the road's collection*, so the second road's first point became
`p000.001` and the ramp's became `p000.005`. Since `points_in` sorts by name, a road's chain order
was then something nobody authored — and it happened to sort correctly in the testbed, which is the
worst kind of pass. Points are now named `<road>_p000`, which also makes the outliner legible
(`road_main_p004 -> ramp_e_p000` instead of `p004 -> p000.005`). `_renumber` renames in two passes
for the same reason: renaming straight onto a name another point still holds gets it suffixed.

**2. `matrix_world` is stale in exactly the pass that matters.** `Make Intersection` creates the
`JCT_*` parent and immediately reads its `matrix_world` to compute `matrix_parent_inverse`. Without a
`view_layer.update()` between the two, every mouth jumps by the parent's offset the moment it is
parented — and since a member point's world position **is** its stop line, that silently misplaces
the whole pad. The same update now guards `read_network`, because a member's world position is what
it reads. This is landmine 6 of §4.4, confirmed in practice rather than in principle.

**Also settled by writing the tests:** `bpy.ops` converts a reported `ERROR` into a `RuntimeError`,
so an operator that *refuses* (the empty cross-section mask) is caught, never compared against
`{'CANCELLED'}`. And the repo addon is symlinked into Blender's addons directory and auto-enables, so
a headless test must register only if `RKA_OT_validate` is not already there — calling `register()`
unconditionally dies with `already registered as a subclass 'RKA_Link'`.

## 8d. What is NOT done, and what blocks it (2026-08-22)

**The Godot-side runtime verification of step 3 is blocked by something that predates this work.**
`build.gradle.kts` in the working tree bumps godot-kotlin-jvm to **0.17.0-4.7.2**, and 0.17 replaced
`@RegisterClass` / `@RegisterFunction` / `@RegisterProperty` with a single `@Register` annotation.
Every one of the ~100 registered Java classes still uses the old API, so `./gradlew build` fails with
`cannot find symbol: class RegisterClass` in files nothing in this rewrite touches
(`MovementState`, `GameManager`, ...).

Established by measurement, not assumption: reverting **only** `build.gradle.kts` to 0.16.3-4.6.3
makes `./gradlew build` **succeed** with all of step 3's Java changes in place. So the road work is
green and the 0.17 annotation migration is a separate, pre-existing task. The bumped file has been
left exactly as it was found -- reverting someone's in-progress upgrade is not this work's call.

Until that migration lands, step 3's last acceptance clause -- *"a Godot bake spawns cars off it with
smooth bezier paths"* -- cannot be run. Everything upstream of it is verified: the schema, the
emitter, the standing gate, and the Java that reads it (compiled).

**Superseded by §8e** (2026-08-22): `point_edges.py` and `Auto Setback` are now done. The §3.2
decision was settled on a *constructed* parallel overlap rather than one of the island's 60 — see
§8e for why that is stricter rather than weaker, and for the answer, which is not the one the plan
proposed.

**Still not started, with reasons:**

* **The Godot in-engine run of step 3.** Blocked above; nothing in the road work can unblock it.
* **`Fit Ramp Grade`** — `road_support.run_needed` / `grade_profile` are in place, so this is now
  only an operator shell over them.
* **The remaining 4.1 craft tooling** (`Split`/`Reverse`/`Merge Roads`, `Resample`,
  `Duplicate Road`/`Duplicate Junction`, `Repair Links`, snapping, measure, road styles, follow
  terrain as a road-level toggle, `Move Junction With Neighbours`, the reference plan underlay).
  None of it is on the model's critical path; **all of it is on the artist's**, and it is the
  natural next block of work now that the model, the geometry and the gate are green.
* **§3.6's game-facing emissions** — crossing markers, parking-stall markers, `road_class` reaching
  the spawner, the always-resident lane manifest. All cheap *because the geometry already computes
  them*, and all schema changes, so they want doing while `.lanekit` v2 is still young.
* **Markings.** `lane_profile.marking_runs()` is written and self-tested and `mark_left` is on the
  point, but no marking carrier is emitted yet — so a road still reads as a grey ribbon from the
  third-person camera. It is a `_polyline_object` call per run against the existing band group, not
  new machinery.
* **LOD (§3.4)** — the `STREET_LOD_LOW` flat/kerbless/asset-free variant is one more GN stack
  configuration off the same carrier, not a second model.
* **`LaneNetwork` node consolidation (step 4b)** — separable, and gated on the same 0.17 migration.

## 8e. Steps 4, 5 and 7 results (2026-08-22)

### §3.2's open decision, settled — and settled AGAINST the plan

The plan left one thing to decide on real content: two ribbons that run parallel and overlapping
**without ever converging** have no crossing for a boundary walk to find (60 such ends on the
previous island). Its provisional answer was *"do the union with Blender mesh booleans, keep the
walk as a fast path, gate on disagreement"*.

**That answer is rejected.** Working through what the union polygon is actually *for* turned up
exactly two consumers, and neither one needs a union:

1. **Where does a kerb stop?** → *"is this kerb sample standing on another road's asphalt?"* That
   is a **point-in-polygon** test against each band. It needs no crossing, so the parallel-overlap
   case — the one that killed the walk — is not a special case here at all.
2. **What footprint does the ground cut use?** → cutting the terrain with the union of N bands is
   identical to cutting it with each band in turn (**difference distributes over union**). The
   union polygon is never built.

So there is **no polygon clipper, no boundary walk, and no boolean-vs-walk disagreement to gate
on**. `pyclipper` is not in Blender's bundled Python and pure-Python Clipper ports are too slow for
a live rebuild; not needing one is strictly better than choosing one. The cost, stated plainly:
where two ribbons overlap, the kerb **opens** rather than tracing a new line around the combined
shape. At a gore that is exactly right. Where a merged outer edge really is wanted, the artist
authors the outer road's own footway — a visible authored fact rather than an emergent one.

Two refinements the tests forced:

- **A bare containment test is not enough.** An *exactly aligned* ramp — which is what
  `Align Ramp To Aux` produces and what the gate demands — touches the mainline band tangentially
  with **zero** overlap, and a kerb built right up to that tangent is a wall across the join. So
  the predicate is a **signed** distance with a `NEAR_PAD` (0.6 m, a kerb-plus-gutter width), not a
  boolean.
- **Elevation is part of the test.** A viaduct 12 m over a street overlaps it in XY and must keep
  every metre of its parapet, so suppression needs the other band's surface within `Z_TOL` (3 m).

### The corner that escapes: the 15-degree skew, measured

`intersection_kit.build_junction_boundary` rounds the corner between two angularly-adjacent arms at
the point where their two outer kerb **lines** intersect. For arms 15° apart those lines are nearly
parallel, so they meet far past anything the artist placed — **measured on a 15° crossing of a 2×2
arterial: a corner 36.9 m outside the pad**, which makes the ring non-star, folds the triangle fan,
and reads in-game as a black crater.

That is the **same defect** as the previous model's hidden setback solve asking a 15° crossing for
a 136.7 m setback. It had simply moved from the setback to the fillet, and the plan did not predict
it. `point_solve.clamp_corners` kills it with the model's own rule — **the point is the stop
line**: a corner needing more room than the arms were given is not a corner, and the two
carriageways just run into each other, exactly as an angularly-adjacent *through* pair already
does. The straight edge left behind is the sharp gore a 15° X-crossing really has. Cap points
(radius 0) are never dropped, so the pad can never shrink inside a mouth.

### `Auto Setback` was a silent no-op, for a reason worth writing down

`Arm.tail_center` ignores `tail_length` entirely when `tail_pos` is set — and `solve_junction` sets
`tail_pos` on every arm, because pinning the cap at the authored mouth *is* the model. So
`recommended_tail_length`, which searches by growing `tail_length` and re-measuring, moved nothing
and returned its start value unchanged. It reported success and did nothing. The fix is that the
search runs on a **parallel set of probe arms** that sit on their own angle rays, and the answer is
written back onto the authored points.

### The seventh live-rebuild landmine (the plan listed six)

**`point_model.read_network()` begins with `view_layer.update()`** — it has to, or a junction
member's `matrix_world` is stale and every mouth reads at its parent's old offset. But
`view_layer.update()` issues a depsgraph update, which re-enters `depsgraph_update_post`, which
reads the network again: **unbounded recursion**. Blender reports it as a `RecursionError` inside
`PointData.__init__` — a place with no connection to the cause. The `_building` flag does not cover
it (the handler is not building), so `on_depsgraph` needs a re-entrancy guard of its own, and
`rebuild()` must raise `_building` *before* its own `read_network` call.

### The gate grew two checks that could not exist before the solve

`check_pads` (the triangle fan's star-shaped precondition, in metres, plus zero-area and
unreachable-arm) and the `pier_skipped` finding are facts about the **resolved** pad and support,
not about the authored links — which is why they arrive with step 4 rather than step 1.

### Full-plugin coverage (2026-08-23) — and the two real bugs it found

`smoketest_point_coverage.py` drives **every registered operator** end to end in one scene and
**executes every panel's `draw()`** against a recording stub layout, resolving each
`prop(data, "name")` against that data's real RNA and each `operator("rka.x")` against `bpy.ops`.
The coverage assertion at the bottom **fails if a new operator is added without a test**, and a
second one fails if the sidebar offers a button this file does not drive.

Why panel drawing is tested at all: `--background` never draws a UI, so `draw()` is the one part of
an addon no headless test touches — and its failure mode is a property-name typo that surfaces only
when a human opens the sidebar and gets an empty panel plus a console traceback. 4 panels × 4
contexts (a plain station, a junction mouth, the `JCT_*` parent, nothing selected), 38 props and 10
buttons, all resolving.

**Two genuine defects, both found by driving the whole plugin rather than its modules:**

- **The sampled ground never reached the Empties.** `build_network` reads a fresh `NetworkData` and
  the solve stamped `ground_z` onto *that*, which the operator then dropped. So the panel's
  "Ground Z (sampled)" readout stayed 0 forever, `.roads.json` never carried a ground height, and
  the gate's `ground_unsampled` warning could not clear however many times Build ran. **Nothing
  failed; the number simply never arrived** — which is why no module test could see it.
  `write_ground_back()` now stamps the objects, and `has_ground_z` is set **only on a real
  raycast hit**: a road over water keeps what it had rather than being handed an invented 0.
- **The terrain raycast sampled the road's own output.** `ground_sampler` excluded generated
  geometry by testing the collection *name* for a `ROAD_MANAGER_GEN` prefix — and generated
  collections are named after their **road** (`main`, `cross`, `ramp`), so the check matched
  nothing and skipped nothing. Each build therefore sampled the surface the previous build swept:
  **measured, `ground_z` went from −7.0 m to +0.16 m — the road's own kerb top** — the support
  flipped `PIER → NONE`, and every rebuild lifted the road further. Membership in the collection
  tree is the fix, computed by one owner (`point_build.gen_collection_names`, which `point_live`
  now shares) because a name is not membership. The regression test builds three times and
  compares; reintroducing the old check fails it immediately.

### The panel had no buttons (2026-08-23) — user-reported, and the sharpest finding yet

*"I don't see how to connect two road points as segments/intersections/ramp."* Correct: **9 of 19
operators had no button anywhere in the sidebar** — `Extend Road`, `Insert Point`, `Delete Point`,
`Connect Selected`, `Disconnect Selected`, `Make Intersection`, `Make Ramp`, `Align Ramp To Aux`,
`Apply Cross-Section`. Every gesture needed to author a road at all. The plugin was fully working,
fully tested, and **completely unusable**.

The omission was invisible from inside because the coverage test asserted one direction only —
*every button the sidebar offers is driven by a test* — and never the converse, *every operator is
reachable from a panel*. Both directions are asserted now, and removing the new `Author` panel
reproduces the report exactly, naming all nine.

The lesson worth keeping: **a registered, working, tested operator with no button does not exist**.
Test coverage of the operator layer says nothing about whether a human can reach it, and the two
failure modes look identical from the console — green.

Also added, because "is it possible to build a sample road?" deserves a yes you can press:
`Author ▸ Learn ▸ Add Sample Network` builds two streets crossing, an elevated highway on piers and
an exit ramp — **all four link types** — gate-green with no hand fixing, and the smoketest builds
and exports it. `make_junction()` was extracted from `Make Intersection` so the gesture and the
sample cannot disagree about what a junction is. The step-by-step is in the addon's `README.md`.

### The tangent bridge was never built, and MANUAL was dead state (2026-08-23)

Second user report, same shape as the first: *"can the road between two points use the current
facing as normal — straight when they agree, bent when they don't?"* That is `tangent_mode =
MANUAL`, which the model declared, `road_points.chain_tangents()` implemented correctly, and
`point_profile.stations()` defeated with one line:

```python
tangent = None          # unconditionally, for every station, in every mode
```

So rotating a point did nothing, `handle_in` / `handle_out` were read by no one, and **nothing
anywhere read the Empty's rotation** — `read_point` took `matrix_world.translation` and stopped.
Three declared features, a library that honoured them, and no bridge. The gate could not catch it:
every check passed, because a road that ignores its authored facing is still a valid road.

What the fix is made of, and why each part is where it is:

- **`PointData.tangent` lives beside `pos`, not in `POINT_FIELDS`.** It is a *transform* channel —
  the artist authors it by rotating the Empty — so it is read from `matrix_world.col[1]` and written
  back with `face_matrix()`. It is carried **only in MANUAL**, which keeps `.roads.json` free of a
  facing on every point nobody has shaped.
- **Straightness is DETECTED, never authored.** `segment_bend_deg()` measures the worse of the two
  chord-to-tangent angles. A Hermite whose tangents both lie along the chord *is* the chord, so
  "straight" needs no flag — and a flag is one more thing that can disagree with the geometry.
- **Handle lengths are metres, `0` = the chord.** They change how hard the curve leaves and
  arrives, never which way. The self-test asserts exactly that: a shorter handle tightens the bow
  and leaves the first step's direction untouched.
- **`Face Road (Manual)` is the anti-footgun.** A fresh Empty's +Y is world +Y, so flipping an
  east-west road to MANUAL without it snaps the road 90°. `Face Road` aligns the facing to the
  chain, making the switch a measured no-op; the test asserts the bow stays under 1e-6.
- **Points are drawn `ARROWS`, not `SINGLE_ARROW`.** A single-arrow Empty draws along **+Z** — it
  was showing the artist an axis the model never reads while hiding the one it does.
- **"If two points can't express it, add a third"** is the model's answer to a compound curve, and
  it is why no curve-type property was added. That instinct came from the user and it is right.

### The overlay was never following the drag (found while fixing the above)

`point_overlay._network()` keyed its cache on `len(bpy.data.objects) + frame_current`. Moving or
rotating a point changes neither, so **the overlay drew from a stale network** — while its own
module docstring, and `rka_live_rebuild`'s property description, both promise "the overlay follows
the drag either way". The cache is now an explicit revision bumped from
`point_live.on_depsgraph`, **before** the live-rebuild gate — invalidating is setting one int, and
it must not be conditional on a rebuild the artist switched off precisely because they only wanted
the overlay.

### Connections were invisible, which is why connecting felt unreliable

Links live in a `CollectionProperty` that **no panel drew**. There was no confirmation anywhere
that a connect had worked short of pressing Build. The new `Connections` panel lists every link on
the active point — type (editable in place), target (a button that jumps there), an X, and the
derived span / `straight` vs `bend N°` / taper verdict. Every readout is **derived**, and the taper
verdict calls `point_validate.taper_min_length` — the gate's own function — so the panel and the
gate cannot disagree.

It also fixes a real bug: `Connect Selected` did `a, b = selected_points(context)`, i.e.
`context.selected_objects` order, which is **arbitrary**. `AUX` is directed (mainline → ramp), so
the Aux button was a coin flip that agreed with the panel's own "active = mainline" hint about half
the time. `resolve_pair()` now anchors on the **active** object, and takes an optional named target
— which is the actual answer to *"selecting exactly two points is fiddly"*.

### Rotating a point still did nothing, because the point was born AUTO and facing north (2026-08-25)

Third report, and the first two fixes made it *worse* rather than causing it:

> "did try to setup a new point and connect to another point through extend road, however, find
> that the connections between points not always align to the normal of the point (i.e. if rotate
> 75 degree around z axis, the previous connection to the point to new point seem connect to old
> angle of the target point and not align to new point."

Reproduced headlessly in four lines — `new_road`, two `extend_road`, rotate the end point 75° about
Z, solve: **the centreline came back byte-identical.** Two defects, stacked:

1. **`new_point` never set a rotation.** A fresh Empty is identity, so local +Y is world **+Y** no
   matter which way the road runs. The `ARROWS` display was therefore showing the artist an axis
   the road did not have — and `Face Road (Manual)` existed precisely to paper over it.
2. **`tangent_mode` defaulted to `AUTO`, and `read_point` read the rotation only in `MANUAL`.** So
   the rotation was not merely mis-drawn, it was *ignored*: the chain kept taking its Catmull-Rom
   tangent from the neighbours' positions. Everything the previous fix built (the bridge through
   `stations()`, the overlay's MANUAL branch) was reachable only after pressing a button nothing
   told you to press.

**The gesture is now the rotation**, and it rests on one idea: the tool stamps the facing it gives
a point (`RKA_Point.auto_tangent`, derived state, deliberately **not** in `.roads.json`), so a hand
rotation is *measurable* — it is a deviation from a known zero. That distinction is the whole
design, because the obvious alternative (recompute the chain tangent and compare) promotes every
point the artist merely **dragged**: a translate changes the chain tangent while leaving the
rotation alone.

- **Points are born facing their road.** `new_point` takes a `facing`; `Extend Road` and
  `Insert Point` supply the chain direction. (`face_matrix` cannot be used at birth — it reads
  `matrix_world`, which is still identity on an object created microseconds ago, so it wrote the
  station back to the world origin. Rotation and baseline are set from the vector already in hand.)
- **Promotion is derived in `read_point`, not by a handler.** An AUTO point whose facing has left
  its baseline is read as `MANUAL` with that facing. No write, so it is safe mid-modal and safe
  inside a draw handler — which means the overlay, the gate, `Build` and the headless export all
  see the rotation the instant it happens, through the one function they already share.
- **`point_ops.sync_facings()` is the write half**, run by `Build`, by the live rebuild, and by a
  `Follow Road (Auto)` button. Promotion is tested **first**, then every point the tool still owns
  is re-faced to the chain and re-stamped — order matters, or the re-face would erase the rotation
  it is meant to notice.
- **`point_profile.chain_facings()` is the one owner** of "which way does this station face".
  `align_tangent` was mining its own copy of that walk; it now calls this.
- Setting `tangent_mode` back to `AUTO` re-stamps the baseline from an `update=` callback, so the
  next read does not instantly re-adopt the rotation the artist just gave up. (`self.id_data`
  compared with `is` never matched — `obj.rka_pt` builds a fresh RNA wrapper per access.)

**And the second half of the report — "real time vs rebuild on each time".** The overlay drew a
fixed 8 m arrow per point and nothing else, so the road's actual shape was invisible until Build.
It now draws the **resolved centreline** (`point_profile.centreline_runs()` — `resample` only, no
widths, no support, no ground raycast, because a draw handler runs per region per frame). Combined
with read-time promotion, the spine bends under the R-drag itself. `rka_live_rebuild` stays off by
default; it is for the *mesh*, and it is no longer what the artist needs in order to see the shape.

Coverage: **33 checks**, 23 operators, 6 panels, both directions still asserted.

### One command

`blender/tools/check_roads.sh` runs the whole thing — 10 pure-Python self-tests, a fresh `.lanekit`
v2 export through `check_lanekit_graph.py`, and 5 headless Blender smoketests. **18 checks, green.**
`--quick` skips Blender (~2 s). The repo has no CI and the previous addon's ~3.5 kLOC of hand-run
smoketests did not prevent this rewrite, so the point is that a hook or an Action can run it as a
unit.

---

## 8f. Step 8 results (2026-08-25) — four user-reported defects, and the eye that would have caught them

Four reports from a walkthrough of `Add Sample Network`, all four real, and one common thread: the
model had a **single owner** for every derived fact *except direction and reachability*, and both
of those had two owners that quietly disagreed.

### 8f.1 A rotated intersection mouth bent its street and left the pad alone

**Reported:** *"seem no direct way to change rotation of intersection; when I rotate `demo_cross_p006`
on Z only the road changes, the intersection does not align to the new point's normal."*

**Cause, exactly.** The carriageway asked `road_points`, which honours `PointData.tangent`. The pad
asked `point_solve.mouth_axis`, which re-derived the direction from **the chain neighbour's
position** — a value a rotation cannot change. `point_validate._axis` had a third copy of the same
derivation, so the gate agreed with the pad and not with the geometry.

**Fix:** `point_model.station_axis(net, uid)` is now the one owner of *"which way does this station
face"* — `MANUAL` tangent first, the central-difference chord otherwise. `mouth_axis` and
`_axis` both delegate. Rotating a mouth now turns its cap, both neighbouring fillets and that arm's
turn paths, live, with no mode to set first. **Rule 6 already said the transform is the road frame;
what was missing was that only one module believed it.**

### 8f.2 Nudging a mouth refused the whole build, and the remedy did nothing

**Reported:** *"when I try moving the intersection pad it errors out."* Reproduced: dragging one
mouth 4 m produced `pad_not_star_shaped: the pad ring folds 0.02 m past its own centroid` — a hard
gate ERROR, nothing built — and the suggested remedy, `Auto Setback`, then reported *"moved 0
mouth(es)"* and `CANCELLED`.

**Cause.** Star-shapedness is a property of the ring **and the apex together**, and the apex was
hard-coded to the centroid. A ring perfectly fannable from a point 70 cm off-centre failed.

**Fix, in three parts:**

- `point_solve.fan_origin` searches for a **kernel point** — push the apex along the inward normals
  of whatever edges it is outside of, a few iterations — and `point_solve.ear_clip` catches the
  genuinely non-star ring. `pad_triangles` is now the one owner of how a pad is tessellated, and
  `point_build.build_pad` sweeps exactly what it returns. **A pad can no longer be a black crater
  OR a refused build.**
- `pad_not_star_shaped` drops to **WARN**, still in metres, because it usually does mean a mouth
  wants pulling out — but a 2 cm fold must not be able to stop a build. *A gate that cannot pass is
  worse than no gate* (defect 11) cuts both ways: so does a gate that fires on a hand-drag.
- `Auto Setback` returns `FINISHED` with *"already at the solved setback"* at zero moved. A remedy
  a finding names must never read as "it did not work" when it means "they are already right".

### 8f.3 The ramp was stuck on the side of the road, with a hole beside it

**Reported:** *"the ramp lane enter edge is not aligned with the main lane point normal, also the
mesh pad is not formed, rather more like just stuck on the side."*

**Three separate defects behind one symptom:**

1. **Wrong anchor.** `aux_edge_offset` returned the aux slot's **outboard** edge, so the ramp was a
   lane *beyond* the exit lane: three through lanes, then a fourth that stays, then a fifth that
   leaves. It now returns the slot's **through-lane-side** edge — the gore line — so the aux slot
   **is** the exit lane and the ramp is its continuation. Case-free: the anchor is whichever of the
   slot's edges is nearer the standard travel lanes, which resolves a kerb-side and an offside exit
   with no side table.
2. **No facing.** `Align Ramp To Aux` translated the point and left its rotation alone, so the
   ramp's cross-section was cut at the ramp's heading and the mainline's at the mainline's. Two
   bands cut on different planes touch at **one vertex** and open from the next one — which is what
   "stuck on the side" is. It now also faces the mouth down the mainline and pins it `MANUAL`;
   divergence is authored by rotating the **next** point, which is what a parallel-type exit is.
3. **No gore.** The wedge between the two receding edges was nobody's geometry. `point_solve.
   solve_gore` now emits it: a strip between the two roads' **own** paved edges (`RoadSolve.edges_*`,
   so it cannot drift from either), from the **theoretical gore** — where the signed gap changes
   sign, i.e. where the bands actually part; paving the overlap upstream of it would lay a second
   surface on the mainline — to the **nose** at `GORE_NOSE_WIDTH` (4 m). `point_build.build_gore`
   sweeps it into `ROAD_MANAGER_GEN/GORES` with a `-noped-colonly` proxy, and `point_edges` treats
   it as a band so kerbs open across it.

A fourth thing fell out: `check_tapers` applied the **merge** taper rule to a lane that *departs*.
A departing lane needs no merge length — nobody moves sideways at a gore — so forcing one made the
aux slot taper for hundreds of metres past the ramp, a fourth lane running to nowhere, and that
taper is what kept the ramp's band overlapping the mainline's for the whole of it. A width change
at the station that owns the `AUX` link is now exempt, and **only** there.

### 8f.4 The sample's ramp was unreachable, and nothing could see it

**Reported:** *"demo_hwy_p008 is 3 lanes per way, but should be 4 for forward, and have the 4th
outermost as aux for the ramp."*

Two answers, and the second is the more important one.

**It already was four**, and the tool would not say so: `lanes_fwd = 3` with `aux_fwd = 1` printed
as `3|3 +1/0`, which everyone reads as three. The overlay now prints `3+1|3` and the Road Point
panel prints `carriageway: 4 fwd / 3 bwd, 26.5 m paved`. **An aux lane is a lane** — it is paved,
it is exported, and a car drives on it.

**But the exported graph really was broken.** `demo_hwy_AF0` merged back into `demo_hwy_F2` and
`demo_ramp_F0` had **no predecessor at all**: an `AUX` link exported as *nothing*. No ambient car
could ever reach a ramp, anywhere in the world. In game that reads only as "that ramp is always
empty", which nobody attributes to authoring — and no gate could catch it, because the gate checks
geometry and this is **reachability**.

- `point_export.wire_ramps` emits the edge, directed by the ramp point's role (`RAMP_EXIT` takes
  the aux lane onto the ramp; `RAMP_ENTRY` brings the ramp into the aux slot), lane-for-lane
  median-outward.
- An exit lane's exported polyline now **ends at its gore** (`_aux_handoffs`): the aux slot keeps
  tapering downstream as pavement recovery, but as a *lane* it has left with the ramp. Without the
  trim the successor's head sat 240 m from the predecessor's tail — far outside `CHAIN_TOL` — so
  the edge would have existed and still never chained.

### 8f.5 …and the eye: `point_preview`

The through-line of 8f.4 is that **the authored graph and the exported graph are different objects,
and only one of them ships**. Everything in the sidebar drew the first. So `point_preview` draws the
second: `export_network()` rendered in the viewport — directed lanes, chevrons, the `next` edges
tail-to-head, and optional agents walking the graph on the exported weights.

Its diagnosis is about **reachability**, which had no eye at all:

| | |
|---|---|
| `broken` | no successor, and the tail sits on the head of a lane going the same way — it should have chained |
| `open_end` | no successor, running off the edge of the network — expected, listed separately so it cannot drown the line above |
| `unreached` | no predecessor, with a same-way tail on its head |
| `ramp_orphans` | any lane of a `ramp` road that nothing leads to — named, because this is 8f.4's symptom |

The direction gate (`JOIN_MAX_TURN_DEG`, 75°) is what makes those usable: without it every road in
the world reports itself broken, because a carriageway's far end sits exactly on the head of its own
opposite-direction twin. *A report nobody reads is a report that does not exist* — the same rule as
the gate.

`flow_batches()` is deliberately split from the GPU submission so the whole overlay's arithmetic is
asserted headless, the same way every geometry module here keeps its maths out of `bpy`.

### What this cost, in owners

| Fact | Was | Is |
|---|---|---|
| which way a station faces | 3 derivations (`road_points`, `mouth_axis`, `_axis`) | `point_model.station_axis` |
| where the ramp mouth belongs | 2 (`point_ops._aux_target`, `check_ramps`) | `point_solve.ramp_target` |
| how a pad is tessellated | 2 (`solve_junction`, `build_pad`) | `point_solve.pad_triangles` |
| where the gore line is | — | `point_profile.aux_edge_offset` |
| whether traffic can get there | nothing | `point_preview.flow_report` |

## 8g. Step 9 results (2026-08-26) — the follow-up four

All four from a second walkthrough of the sample. Each one is small; three of them are the same
shape as §8f, which is worth naming: **a rule that is right for the case in front of you, applied
to a case you have not looked at yet.**

### 8g.1 A two-lane exit anchored on the wrong slot

`aux_edge_offset` picked the **outermost** aux slot and returned its through-lane-side edge. At
`aux_fwd = 1` that is the only slot, so it was right; at `aux_fwd = 2` it put a two-lane ramp half
on the carriageway and half off the pavement.

The unit of an exit is the **block**, not a slot. `point_profile.aux_block()` returns the whole
run of same-direction aux slots and the edge of it that faces the through lanes, so widening an
exit widens it outward and never moves the join. Still case-free about side: the block is the aux
slots on the side of the profile with the most of them, and the gore edge is whichever of the
block's two edges is nearer **that direction's** standard lanes — which resolves a kerb-side and an
offside exit with no table.

### 8g.2 The taper demand, twice wrong

Two separate things, and the second is a design position rather than a bug.

- **The crossover was at 60 km/h; the metric standard puts it at 70.** So every road in the
  60–70 band was asked for half again the length the book asks for (3.5 m at 60 km/h: 126 m against
  81 m). Fixed to `TAPER_LINEAR_ABOVE = 70.0`.
- **The world is not 1:1.** This map compresses Tokyo into ~6 km, so a taper computed from real
  design speeds legitimately eats a district. The answer is **not** to bend the constant quietly:
  `RoadData.taper_factor` (default `1.0` — the book) scales the demand per road, appears on the
  **Road** panel, and is named in the finding when it fires. Shortening a taper is now a visible
  authored decision on the road it applies to, rather than a number in a checker nobody reads.

### 8g.3 No wall anywhere, and no way to ask for one

The edge stack was kerb + footway and nothing else, so an elevated expressway and its ramp had a
14 m drop off either side with nothing on it. Added a **Barrier** layer to `edge_spec()` — the same
`deck` node group the kerb uses, so there is no second idea of what a wall is.

The split that matters: **height is authored (`RoadData.barrier_height`), placement is derived.** A
road with `ped_access` off is fenced along its whole length; a road people may walk on is fenced
only where `delta >= BARRIER_MIN_DELTA` — a viaduct's parapet, not every slightly-raised street.
That is the same "you author the value, Build decides where" rule the supports already follow.

And it needed no ramp-specific code at all. Because the barrier rides the **outline** like the kerb,
`point_edges.open_runs` opens it across the gore and closes it past the nose for free — which is
exactly the property §3.2 bought and the previous model's `RAMP_WALL_OPEN` tier never achieved.

### 8g.4 Intersections had no pavement, and the fix broke the gore

A pad was bare asphalt to its own boundary with every street's footway stopping dead at its mouth —
four missing pavement corners at every crossing in the world. `point_solve.junction_corners()` now
emits one `Corner` per real corner from `intersection_kit.build_junction_curb_segments` (the SAME
corner curve the pad boundary is rounded with), and `point_build.build_junction_edges` sweeps it
with the ordinary `edge_spec()`. A junction corner **is** an edge run; it just happens to be an arc.

Two things that were not obvious:

- **The street's kerb was being suppressed against its own pad.** A run ends AT its mouth, on the
  pad boundary, and the corner's furniture starts at that same point — so `NEAR_PAD` opened a gap
  between the two at all four corners. A run must not suppress its furniture against a footprint
  that carries it onward.
- **…but a gore is not a pad**, and the first version of that fix keyed on membership alone, which
  cannot tell them apart: the run is a member of both. It left a 9 m **barrier stub standing across
  the gore paint**. `Band.carries_edge` is now a flag the band's *builder* sets — a pad hands the
  furniture on, a gore does not — rather than a rule inferred at the point of use.

### The owners this added

| Fact | Owner |
|---|---|
| where an exit block begins | `point_profile.aux_block` |
| how long a merge taper must be | `point_validate.taper_min_length` × the road's `taper_factor` |
| where a wall stands | `point_solve.solve_road` (derived) × `RoadData.barrier_height` (authored) |
| a pad's own kerb line | `point_solve.junction_corners` |
| whether a footprint hands the furniture on | `point_edges.Band.carries_edge` |

## 8h. Step 10 results (2026-08-26) — two holes the first pass of §8g left

Both user-reported against the sample, both in the same place §8g had just changed, and both the
same shape as everything in §8f/§8g: **a helper that re-derives a position instead of asking the
one owner.**

### 8h.1 A rotated mouth's corner footway met the street in a notch

The kerb *lines* matched exactly — the corner's endpoints ARE the arm's cap corners, measured to
1e-9. What did not match was the direction the footway extended outboard in: at the rotated mouth
the corner's end ran at 121° where the street's own normal said 70°, so the two footways started
from the same point and diverged. 51° of notch.

`intersection_kit.curb_edges` builds each arm's kerb-edge ray as `perp * width + t * direction` —
**a line through the ORIGIN**. That passes through the arm's cap corner only because a plain arm's
`tail_center` is itself a multiple of `direction`. `_PadArm` sets `tail_pos` to the *authored*
mouth, which after a rotation is **not** on the arm's angle ray, so the corner vertex landed on a
line the arm's kerb never touches — and everything downstream of it (the pad's own fillet included)
left the cap at the wrong angle.

`Arm.tail_center`'s docstring records the opposite as a deliberate scope limit — *"`curb_edges` /
`_junction_corner_vertex` never call this at all"* — and for the model that wrote it that was
right: there an off-ray cap matched an external port and must not perturb its neighbours. **In the
point/port model the mouth's position and rotation ARE the stop line** (rule 4), so the corner
between two mouths has to be built from where those caps actually are. So it is opt-in by
parameter (`curb_edges(..., tail_length=)`), which is byte-identical for every arm without
`tail_pos` — the anchor is on the ray already — and changes only the set that was wrong.

### 8h.2 An 11 m hole in the parapet, exactly where the drop is

Where a ramp leaves along the mainline's outer edge, the two roads' outer edges are within
`NEAR_PAD` of each other for the first tens of metres. `covered()` applied that slop
**omnidirectionally**, so each band suppressed the *other's* wall and neither was built: measured
on the sample, 11 m of unwalled edge at the top of a 14 m drop, on the one stretch that most needs
one. Mutual suppression — two coincident edges each believing the other covers them.

The predicate was answering the wrong question. What the furniture turns on is *"could a vehicle
drive across this line"* — i.e. **does the pavement continue past it** — and that is directional.
`covered(..., outward=)` now probes `NEAR_PAD` metres **outboard of the edge** and asks for strict
containment. A ramp's inboard edge probes toward the mainline it is leaving (open, as before); its
outer edge probes into empty air (keep). The mainline's wall now runs to the mouth and the ramp's
takes over from it, with a metre of overlap rather than a hole.

`measure_on_asphalt` moved to `pad = 0` in the same breath: it measures what its name says — a
sample *standing on* asphalt — and once the build rule became directional, a kept sample may
legitimately sit a few centimetres outside a band with nothing beyond it.

### 8h.3 The gore nose was an open V, and neither road could close it

Reported against the sample as *"add one extra wall to the ramp connection — or can the ramp's own
wall be the wall for the merge?"* The answer to the second half is **no, and it cannot be**, which
is what makes this a third owner rather than a wiring fix.

A gore is bare paint — `point_edges.Band.carries_edge` is `False` for one, deliberately (§8g.4) —
so **both** flanking walls open across it. Along the join that is exactly right: a wall there
stands in the exit lane. At the **wide end** it is exactly wrong. Measured on the sample: the
mainline's wall resumed at `(596.0, 334.3)`, the ramp's inboard wall began at `(593.2, 339.1)`,
and the 4.6 m between them — the `GORE_NOSE_WIDTH` the paint stops at — was nobody's geometry, at
the tip of a 14 m viaduct. Neither road can fill it, because both are suppressed there for the
same correct reason: the stretch is the other one's asphalt.

Neither road owns it, so the **gore** does. `point_solve._gore_nose` emits an ordinary `Corner` —
the same class a junction corner is — and `point_build.build_gore_edges` sweeps it with the
ordinary `edge_spec()`, for the reason §8g.4 gives: a gore with its own idea of what a kerb or a
wall looks like is how the two drift apart. Three copies of the per-vertex furniture arithmetic
became one (`point_build.edge_run_values` / `build_edge_run`), shared by a road's flank, a junction
corner and this.

**What the cap carries is not decided at the gore either.** Each end reads the furniture its own
road already solved (`_flank_edge_furniture` → that road's `rka_wall_h` / `rka_curb_h*` /
`rka_walk_h*`), so `solve_road`'s barrier rule stays the single owner of *"is this road fenced"*,
and the run **blends** between the two ends. A highway meeting a fenced ramp is a wall; an approach
that declares a footway gets a kerbed island; a pair that declares neither builds nothing — the
empty case, not a special case for expressways. `GoreSolve.ped_access` is likewise **both** flanks'
answer, so the proxy bakes `-noped` between an expressway and its ramp and walkable between two
streets, instead of a hardcoded `False`.

**Where the cap sits is derived too:** it IS the gore strip's last pair, flush with the paint, and
both flanking walls resume on that same line — see §8h.4, which is what makes three walls meeting
at a point possible at all.

### 8h.4 One extra wall at the mouth, and a gap at the nose — one root cause

Both reported against the sample after §8h.3 shipped, at opposite ends of the same ramp, and they
turned out to be the same bug seen from two sides: **the furniture's start and end were being
decided per 4 m sample, about features metres across.**

**The extra wall.** At the mouth the mainline's outer edge lies ~0.5 m *inside* the ramp's band —
the ramp is only half a metre wider there. `covered(..., outward=)` probes `NEAR_PAD` (0.6 m)
outboard, so the probe stepped clean **over** the ramp band and landed 3 cm past its outer edge:
not covered, keep the wall. Both parallel edges kept one, half a metre apart, for the length of the
overlap. §8h.2 replaced an undirected test with a directional one; this is the case where the
directional test alone is also not enough.

So a directional `covered` asks **two** questions, and either suppresses:

* *does the pavement **continue** past this line?* — the probe, `NEAR_PAD` outboard;
* *is this line **buried** under someone else's pavement?* — the point itself, `BURIED_TOL` inside.

`BURIED_TOL` (5 cm) is a tolerance for "exactly on", not a margin: an edge sitting **on** another
band's boundary is the shared outer boundary, answers no to both, and keeps its wall — which is
precisely 8h.2's case, so that fix survives intact. `measure_on_asphalt` takes the same tolerance
(`pad = -BURIED_TOL`, a negative pad reading as "at least this far inside") from the same constant.

**The gap at the nose.** Fixing the predicate is not enough on its own, because the *answer* was
still being rounded to a sample. `open_runs` returned index pairs, so a run ended at the last live
sample — up to 4 m short of the mouth it hands over at, and up to 4 m short of the nose it has to
meet. The §8h.3 cap papered over the nose half by sitting a `GORE_STEP` downstream, which then left
a visible gap between the gore mesh and the wall closing it.

`open_runs` now returns `Run(i0, i1, head, tail)` — still `(i0, i1)` when indexed, so nothing that
only wants the sample range changed — where `head`/`tail` are the endpoints **bisected onto the
covering band's own boundary** (`_clip_end`, 12 halvings ≈ 0.25 mm, 24 predicate calls per run).
The run then stops exactly where this edge stops being the outer boundary of the pavement, which is
what a run *means*. `sub_polyline` and `run_values` emit the polyline and its attributes from one
place so they cannot come out different lengths.

Measured on the sample, the three walls now meet:

| | before | after |
|---|---|---|
| `demo_hwy` outer wall ends | `(564.00, 338.03)` — 4 m past the mouth | `(560.42, 338.45)` — at the mouth |
| `demo_ramp` outer wall starts | `(560.00, 338.50)` | unchanged — now the single wall |
| gore cap | `(596.0, 334.3)` → `(594.7, 339.9)`, off the paint | `(594.00, 334.53)` → `(592.88, 339.01)` = the strip's last pair |
| `demo_hwy` wall resumes | `(596.00, 334.30)` | `(593.85, 334.55)` |
| `demo_ramp` inboard wall starts | `(593.19, 339.14)` | `(592.78, 338.96)` |

### The pattern, stated once

Every defect in §8f, §8g and §8h is the same one: **a second derivation of a fact that already has
an owner.** `mouth_axis` re-deriving direction from a neighbour's position; `aux_edge_offset`
picking a slot instead of asking for the block; `curb_edges` re-deriving a cap from an angle; a
distance test standing in for a directional one. The model's answer is not "be careful" — it is
that each of these now delegates, and the delegation is asserted.

§8h.3 is the mirror image of the same rule and worth naming separately: not a fact with two
owners, but a fact — *what stands at the tip of a gore* — with **none**. Both roads were correct
to decline it. When that happens the answer is a new owner that delegates for everything it does
not itself decide (the geometry is the gore's; the furniture is still the roads').

## 8i. The gesture round (2026-08-26)

Six user reports in one pass, and unlike §8f–§8h they are not about geometry: every one is a place
where the tool made the artist declare, or re-declare, something the model already knew — or
refused a gesture because it insisted on being handed the facts in one particular order.

### 8i.1 `Extend Road` from the head grew the road backwards, into an unbuildable chain

**Reported:** *"when using extend point function, on demo_main_p000, will result error if simply
build road immediately, as seem the direction/configuration is wrong."*

The chain order **is** the object-name order and `_next_point_name` can only ever hand out the
next free index, so a new point was always born at the **tail** of the names — whichever point was
active. Extending from `..._p000` therefore produced a point that was misfiled at the far end of a
road it sits at the start of, and (with no `prev` to take a chord from, the head fell back to its
own `+Y`, which is the way the road already runs) placed **forward**, back down the road it was
meant to grow away from. The chain order then disagreed with both the geometry and the link, and
the next Build reported `chain_unlinked` on a pair of points nobody had touched.

Both halves are one fix, in `RKA_OT_extend_road`: grow away from the chain (the neighbour is the
point *after* the head, and the offset is negated), and `_renumber(..., at=0)` so the name order
still matches the road. `+Y is travel` means a prepended point faces **into** the road. An
interior point is refused by name — "extend" has no meaning in the middle of a chain, and picking
an end on the artist's behalf is how a road silently grows the wrong way.

### 8i.2 `Aux` only connected one way round, so half of all ramps were unauthorable

**Reported:** *"it is not possible to setup segment to aux, always need aux to segment in panel,
but should work as either way."*

`AUX` is directed (mainline → ramp) and §8f fixed the Aux button's *source* to be the **active**
point. That was right for the coin-flip it replaced and wrong as the whole rule: an entrance ramp
reads "ramp joins road", so the ramp is the natural point to have active, and the operator refused
outright ("the AUX target must be a RAMP_ENTRY/RAMP_EXIT point"). Every merge in a network had to
be authored from the other end or not at all.

**Which of two points is the mainline is a fact about the two points, not about click order.**
`point_ops.resolve_aux_pair` scores both readings — declaring an aux slot makes you the mainline,
being one-way and/or already a ramp makes you the ramp — and the active point breaks a genuine tie,
so the documented behaviour stays true everywhere it was ever true. The link itself is still
written mainline → ramp. `Make Ramp` takes the same pair resolution.

### 8i.3 One ramp role, because the direction was already in the graph twice

**Reported:** *"is there a reason for ramp exit/entry as logic should be same so should only be
one?"*

There was a reason and it was not a good one. `RAMP_ENTRY`/`RAMP_EXIT` fed exactly one decision —
which way `point_export.wire_ramps` points the lane-graph edge — while `point_solve` derived the
same thing from the chain (`_chain_direction`) without consulting the role at all. Two owners of
one fact, and the failure mode is silent: a ramp geometrically perfect, gate-green, with its
traffic wired backwards, which reads in game only as *"no car ever uses that ramp"*. The test file
had it in three of four aux pairs.

`point_model.ramp_is_entrance` is now the one owner, and it reads two things the model already
holds: **where the mouth sits in the ramp's own run** (head or tail) and **which way the ramp's
lanes run** (FWD is increasing index). Traffic *leaves* the mouth — an exit — when the mouth is the
run's head and the ramp declares forward lanes, or its tail and the ramp declares reverse ones.
Both readings are needed: `lanes_bwd = 1, lanes_fwd = 0` is an ordinary way to draw a ramp and its
head is where cars come **out**. The role is now `pm.RAMP`; the two legacy spellings still load and
are the tiebreak for a ramp run of a single point, which has neither head nor tail.

**A run, not the collection's chain.** `run_of` is what makes this work on a ramp grown inside its
mainline's collection: such a point is the head of its own run and the middle of the names.
`road_runs` therefore moved to `point_model` (a run is a fact about the chain and its links — the
solve does not find it) and `point_solve.road_runs` is an alias.

**And the edge is ADDED to the junction connectors, not chosen instead of them.** An aux lane that
hands off to a ramp and then runs on to a crossing has both successors; `elif l["id"] in
ramp_links` silently dropped every exit that sits on a junction approach — §8f.4's orphan again, in
the one arrangement where the lane looked healthy enough not to check.

### 8i.4 The taper demand was summed across both carriageways, and measured across gaps

**Reported:** *"is it possible to reduce taper required distance, why adding aux on one way need to
increase taper when the other side already have aux?"*

`check_tapers` took `paved_extents` on each side and compared the **totals**, on the stated theory
that "two lanes dropping in opposite directions over the same span is twice the disturbance". It is
not. **A merge is one driver moving sideways on one carriageway.** Opening an aux lane on the left
while the right already has one is two tapers for two drivers who never meet, and adding them
doubled the demanded length — 336 m where the standard asks 168. The demand is now the **wider of
the two sides' changes**, never their sum, and `point_panel.link_facts` reads it the same way so
the row and the gate still cannot disagree.

The same function also walked the chain pairwise, straight across **run breaks**: a lane count that
differs across a junction pad, where the pad joins the two mouths and no carriageway exists at all,
demanded a merge length for a stretch of road that is not there. It now iterates
`point_solve.road_runs`.

`taper_factor` was already the answer to "make it shorter" and is unchanged (1.0 is the book) — but
the finding now names **the factor that would pass**, because "lower taper_factor" on its own left
the artist guessing at a number the gate had already computed.

### 8i.5 A gore between a fenced ramp and a kerbed street built a shape neither road has

**Reported:** *"aux ramp enter to segment, as the two road uses different side material (wall vs
side walk), please fill gore with the ramp material, and the sidewall just continue as is."*

§8h.3 gave the gore's nose its own owner and had it **blend** the two flanks' furniture across the
cap. That is right only while both roads declare the same *kind* of furniture. A ramp leaving an
ordinary street is the common case and they never do — the ramp is fenced (`ped_access` off) and
the street is kerbed and paved — so the cap came out a wall of falling height standing in a footway
of growing width: a section neither road has anywhere else, wedged in the one place both of them
end.

**The gore is the ramp's divergence, so the gore's nose is the ramp's.** `_gore_nose` now carries
the ramp's own solved kerb/footway/barrier **uniformly** along the cap, with the mainline's values
as the fallback for a ramp that declares nothing at all (taking its zeroes would leave the V open
again). The mainline's own kerb and footway run on past it unbroken — they never stopped;
`point_edges.open_runs` only ever opened them *across* the paint.

### 8i.6 …and the one this round introduced: a Blender enum is stored by ORDINAL

Adding `RAMP` to `ROLES` between `INTERSECTION` and `RAMP_ENTRY` re-read every saved role in every
`.blend`: `EnumProperty` persists its **index**, not its identifier, so every stored `RAMP_EXIT`
came back as `RAMP_ENTRY` with nothing to see in any diff. Caught by reading the test file back,
not by any check. **The enum tuples in `point_model` are append-only**, and `_enum_items` now emits
the 5-tuple form so the numbers are at least written down in the source rather than implied by
position.

### 8i.7 A point could be in the wrong collection and nothing would file it

**Reported:** *"is it possible to move the point to correct collections depend on its set up?"*

§8i.1 fixed the gesture that *made* the mess and §8h's `Split To New Road` gave a way out of it,
but both need the artist to notice and to select the right points. The connections already say
where every point belongs, so nothing needs selecting: `Tidy Roads` reads the graph.

Two moves, and the split between them is `road_runs` vs a new **corridor**:

- **A mis-filed point** has no `SEGMENT` link inside its own collection and all its `SEGMENT`
  links in one other. It moves there, placed **next to the neighbour it joins** — the chain order
  is the name order, so appending would put it at the far end of the road it just joined (§8i.1's
  defect, arrived at from the other direction). Only `SEGMENT` counts for this: a ramp is
  `AUX`-linked into another road and belongs in neither that one nor nowhere.
- **A collection holding more than one corridor** splits. `point_model.road_corridors` is the rule,
  and it is deliberately NOT `road_runs`: a **run** breaks at a junction gap because a lane must not
  be swept across a pad, but a crossing does not split a street, so a run break is not a filing
  break. A **corridor** breaks only where two chain-adjacent points carry *neither* a `SEGMENT` nor
  a `JUNCTION` link. One collection should hold exactly one. `check_chains` now reports its breaks
  from the same function, so the warning and the repair cannot disagree about what a road is.

A split-out corridor that something `AUX`-links into is named `<road>_ramp`, because that is what
it is and it is how this happens.

**And the bug this introduced, caught by its own test:** object names are GLOBAL, so `Split To New
Road` renumbering the *source* before the *destination* handed out a name a moved point was still
holding, and Blender gave back `main_p000.001` — a point whose name sorts outside its own chain,
which is the one thing the name order has to guarantee. Destination first, and the coverage test
now asserts no `.` appears in any point name anywhere.

### 8i.8 The gate named a remedy that did not exist

**Reported (indirectly):** *"also auto remove fail connections."*

`uid_duplicate` said *"run Repair Links"*. `link_dangling`'s comment said `Repair Links` "exists as
an actionable fix rather than advice". §7's operator table listed it. It was deferred out of step 2
and never came back — so the one class of defect an artist genuinely **cannot see** (a link row
pointing at a deleted object, a half-written junction, a Shift+D clone carrying somebody else's
uid) was reported with a fix nobody could run.

It does exactly the repairs that have one right answer, and nothing else:

| | |
|---|---|
| **drop** | a `None` target, a self-link, a non-point target, a point in no road, a duplicate row — a pair carries at most ONE link, which is `link_objects`' invariant |
| **drop** | the ramp's half of an `AUX` pair (`aux_backlink`) — `AUX` is directed |
| **restore** | the missing half of a `SEGMENT`/`JUNCTION` link, and a junction component completed into the clique `Make Intersection` would have written |
| **write back** | a re-allocated uid — `read_network` already computed it every read and nobody ever persisted it, so the warning could not be made to go away |

Where the two rows of one pair disagree about type, the more explicit gesture wins: `AUX` over
`JUNCTION` over `SEGMENT`. A junction and a ramp are things you went and did; `SEGMENT` is what
`Extend Road` writes by default.

### 8i.9 Every finding named an object you could not find

**Reported:** *"the error point name (p_xxxx) seem not match the object name in blender object
panel."*

Rule 5 is *"every finding names the OBJECT to fix, because artists fix objects, not indices"*, and
`Validate` did translate the finding's **subject**. It did not translate the message **body**, and
that is where most of the uids are: *"is chain-adjacent to `p_862c8815`"*, *"move `p_5dd247b1`
further away"*, *"the gore line of `p_a12588f3`"*. So a finding named an object you could find in
the outliner and then told you to go and fix one you could not — which is the same as not naming it.
`Build` and `Export` did not translate even the subject.

`point_validate.describe(finding, labels)` is the one owner (three reporting sites had three
different amounts of this), `point_model.point_labels()` supplies `{uid: "<road>/<object>"}`, and
the substitution is a regex over `p_` + 8 hex — the shape `new_uid` builds and nothing else in a
message looks like.

    before  ramp_edge_residual: p_862c8815 -- the ramp mouth is 182.57 m from the gore line of p_a12588f3
    after   ramp_edge_residual: demo_hwy/demo_hwy_p006 -- the ramp mouth is 182.57 m from the
            gore line of demo_hwy/demo_hwy_p003

### 8i.10 The sample was a fixture for the DATA MODEL, and it hid two bugs

`Add Sample Network` wrote its scene with the internal helpers (`new_point`, `link_objects`) so
that it would produce the same network however the gestures behaved. That is the right choice for
a *content* fixture and the wrong one here: it meant the sample could be perfect while `Extend
Road` grew a road backwards (§8i.1) and `Make Ramp` refused half the ramps in the world (§8i.2),
and the smoketest that presses the button covered neither.

It now builds every road by driving the operators. Two things fell out of that immediately:

- The sample **contains the arrangements that were broken** — a head extension, aux lanes on both
  carriageways over one span, and one ramp that is an exit at one end and an entrance at the
  other — so it is now evidence, not just an example. Under the old taper rule it would be **red**.
- Putting an auxiliary lane in the same run as a junction mouth surfaced a **pre-existing
  reachability bug** nothing had ever exercised. `lane_movements.target_lane` preserves distance
  from the **kerb**, and a junction arm was offering every lane of its run — including one that
  opens 200 m past the stop line and is zero width there. Both approach lanes shifted one lane
  outboard: the straight-ahead movement fed a lane that does not exist yet, and the exit's
  **median** lane came out with no predecessor at all. A through lane on a main road that nothing
  can reach, at every junction whose exit arm has an auxiliary lane anywhere in the same run.

`point_export._arm_lanes` is the fix: an arm offers only the lanes that exist **at the stop line**.
Which end is zero decides it, so it is asked per end — `spawnable` is the same fact seen from one
side and is *not* the test, because a lane that runs full width from the stop line and tapers away
300 m later is a fine thing to leave a junction in, and an unspawnable one.

This is §8f.4 for the third time (*"reachability is not geometry, and had no eye"*), and it is the
third time the eye is what found it: `Preview ▸ Flow Report` said `UNREACHED demo_main_1_F0`. The
smoketest now asserts the sample's report is clean, which is the cheapest possible guard against
the fourth.

### The pattern, stated once (again)

§8f–§8h were *a fact with two owners*. §8i is its other face: **a fact the artist was made to
declare that the model already knew** (the ramp's direction, which point is the mainline, which
collection a point belongs in), or **an ordering the tool imposed because it had never been asked
the question from the other side** (the head of a chain, either end of an AUX link). The remedy is
the same shape — derive it, and assert the derivation — and the tell is the same too: the artist
saying *"why do I have to…"*.

§8i.8 and §8i.9 are a third thing again, and the cheapest of the lot to have caught: **the tool
saying something that was not true.** A gate that names a remedy nobody can run, and a finding that
names an object nobody can find, both cost exactly as much trust as a wrong answer — and neither
was a hard problem, only an unchecked claim. The coverage smoketest asserts every operator the
sidebar offers is reachable and driven; nothing asserted that the *prose* pointed at anything real.

## 8j. The duplicate-and-branch round (2026-08-27)

Four user reports from one session, all of them about *authoring a second ramp*: copy the ramp
collection, aim it a different way, and branch another one from the middle of the highway. Every
one of them turned out to be a fact resolved through the **uid** where the ground truth was an
**object**, or a direction resolved through the **walk** where the ground truth was the **road**.

### 8j.1 Duplicating a road collection orphaned the original and disconnected the copy

> *"Copy the ramp collection directly and modify to different way of exit, seem all connect, but
> error out with all points like `point_orphan: demo_ramp.002/demo_ramp_p004.001 -- point belongs
> to no road collection` even though it is in a road collection."*

Two defects, one cause. `Object.copy()` deep-copies IDProperties, so every point of the copy
carries the original's uid; `dedupe_uids` re-allocates the newer one and drops its links, which is
right for Shift+D on **one** Empty and wrong for a whole road — a copied collection's link rows
already point at the copies, so its internal wiring was thrown away and the new road arrived as
five loose points. Then `read_network` patched road membership with a `{old_uid: new_uid}` map,
which is only a function while uids are unique, and the one moment it runs is the moment they are
not: the same old uid sat in two roads, so the remap rewrote **both**, and the ORIGINAL road forgot
its own points. `point_labels` compounded it by reading the *stored* uid, so the finding named the
copy for a point that belonged to the original.

- **Membership is read off the object**, never remapped by uid (`read_network` builds each road's
  point list from the objects it just read).
- **Links are resolved by object identity**, after the dedupe (`point_model.relink_from_objects`),
  keeping a row that stays inside the re-allocated set — the duplicated road's own wiring — and
  dropping one that leaves it, which is the clone's inherited connectivity. That one rule
  separates Shift+D on a point from a duplicated collection with no mode and no flag.
- A link to an object in **no** road collection is dropped rather than resolved by uid onto
  whichever real point happens to share it. That was silently re-wiring a live road to a deleted
  one: the sample file had a `demo_ramp_p000.001` in no collection at all, still AUX-linked.
- `net.labels` is filled by the same read, so every finding names the object the *resolved* uid
  belongs to (§8i.9's rule, which the dedupe had been quietly defeating).
- `Repair Links` clears only the rows that leave the re-allocated set, for the same reason.

### 8j.2 There was no gesture for a ramp that starts mid-corridor

> *"It is hard to extend from mid of highway or road a new point (extend road will error out to
> mention inject at mid of lane)."*

`Extend Road` refuses an interior station and is right to — "extend" has no meaning in the middle
of a chain, and picking an end for the artist is §8i.1 all over again. But the thing the artist was
doing had no gesture at all: it took `New Road` at a guessed position, a lane count, a role, a
one-way declaration, an aux slot, `Aux`, `Align Ramp To Aux` and a station bent outboard by hand,
with a red gate at every step between.

**`Author ▸ Ramp ▸ Branch Ramp Here`** is that sequence, from any point of the corridor, with every
number that CAN be derived derived: the aux slot is opened back to the first span long enough to
hold the taper `check_tapers` asks for (`open_aux_slot` — an aux count is an integer, so the slot
goes zero-to-full across exactly ONE span, and that span is what the gate measures); the mouth is
placed and faced by `Align Ramp To Aux`; and the second station is bent **outboard**, the direction
`ramp_target`'s `side` says the slot is on. The far end is left active so `Extend Road` carries on
from it. `Extend Road`'s refusal now names this and `Insert Point` as the two gestures that do have
a meaning there.

### 8j.3 The gore's direction search was a coin flip, and it landed a wall across the ramp

> *"Is it possible not to generate `GORE_p_696286_walk-walk-noped-colonly` for road to ramp, as it
> blocks ramp completely."*

It is not the proxy that was wrong: the **cap was 22 m long**, laid across a merge instead of across
the gore, and its collision proxy is the part you walk into. `_signed_gap` took its normal off the
chord it was walking, so the upstream reading was the downstream one with its sign flipped —
"which way do the two bands part" therefore picked upstream *unconditionally*, the mainline's
samples were paired against the ramp's running the other way in world space, and the nose landed at
the mouth. Nothing reported it, because a flipped reading is a perfectly plausible number: the gap
simply came back positive where the two bands overlap. The residual was zero and the angle was
zero throughout.

**Outboard is a fact about the road, not about the walk.** `_signed_gap` takes a `sense` (+1 with
the mainline's travel, −1 against it) so the two readings in the direction search are comparable at
all. A gore whose gap already exceeds the nose width at the mouth is not a wedge and builds
nothing — that only happens where `ramp_edge_residual` is already reporting.

The same fix removed the second half of the report — *"`demo_main_1_walk-walk-colonly` may reduce
one step from ramp"*: `point_edges.open_runs` opens a road's kerb across whatever the gore actually
covers, so a gore covering the wrong 1.3 m left the arterial's footway built across the ramp mouth.
With the gore covering the right stretch, no edge-run vertex in the sample stands on another band's
asphalt.

### 8j.4 A ramp could leave, turn round, and drive back through the road it left

Both ramp checks measured the **mouth** — where it stands and which way it faces — and
`Align Ramp To Aux` sets both, so a ramp that leaves correctly and then bends back **across** the
carriageway passed the whole gate. Its band overlaps the mainline's for its whole length, no wedge
exists, `solve_gore` returns None, and the artist is told nothing: no gore, no nose, no error. The
**sample network's own exit ramp** was authored exactly that way — a fixture cannot be evidence
while it is authored the way the bug is. `point_solve.ramp_divergence` measures the station *after*
the mouth against the outboard normal; `ramp_wrong_side` (ERROR) and `ramp_parallel` (WARN) report
it.

### 8j.5 A ramp on the reverse carriageway was faced, placed and edged as if it were on the forward one

Three places derived "does the ramp's frame agree with the mainline's" and all three assumed yes.
It is the product of **two** signs, and both are facts the model already holds: which carriageway
the aux slot is on (`aux_fwd` versus `aux_bwd` — traffic through a reverse slot runs against the
station axis), and which way the ramp's own lanes run (§8i.3's `lanes_bwd`). `ramp_frame_sign` is
the one owner; `ramp_target` swaps the paved extents by it, `ramp_facing` signs the Empty's arrow
by it, and `solve_gore` picks the ramp's inboard edge by it. Faced the wrong way, a two-lane
entrance came out as a 600 m hairpin whose gore was a 38 m wall down the middle of the ramp.
`Make Ramp` also stopped writing `aux_fwd` unconditionally: **which carriageway is a fact about
where the mouth is** (`ramp_carriageway`, §8i.2's sibling).

### 8j.6 Two ramps on one run silently shared one lane, and one of them was unreachable

§8g.1 taught `aux_edge_offset` that an exit is a **block** of slots and the geometry followed;
`point_export._aux_handoffs` was never told, so with `aux_fwd = 2` the ramp's second lane had no
predecessor at all — paved, exported, gate-green, unreachable. `point_profile.aux_slot_ids` is now
the one owner of *which lanes leave*, shared by the hand-off table and the gate.

The structural half is not fixed and is reported instead: a run exports **one lane per slot**
(`road_points.lane_taper_route` blends a slot's route across the whole run), so two ramps on one
run — or one station wired to two ramps, which is where a duplicated ramp collection lands — both
claim `AF0`, the second hand-off overwrites the first, and one of the two ramps is reachable by no
car in the world. `check_aux_slots` (ERROR) names both ramps and the two ways out: the other
carriageway, or a run break. The sample stays on the right side of it — its two-lane connector
leaves and joins on the **westbound** carriageway of both roads.

### The pattern, stated once (again)

§8j.1, §8j.3 and §8j.5 are the same sentence: **a derived fact resolved through a proxy that is
usually right.** A uid usually identifies one object; a walked chord usually runs with the road; a
ramp's frame usually agrees with its mainline's. Each held until the exact case the artist reached
for — duplicate a road, author an entrance, exit the other carriageway — and each failed *quietly*,
returning a plausible number rather than an error. §8j.4 and §8j.6 are §8f.4 again: **reachability
and shape had no eye on them**, so the gate stayed green while a ramp was unusable.

## 8k. The diverge and the merge (2026-08-27)

> *"Is it possible to do a ramp with a different way on the same highway point and join on the
> same road point, to check that the merge logic is okay?"*

Two ramps hanging off ONE mainline station — a two-lane exit that splits, and two ramps merging
back into one two-lane slot — is the ordinary shape of an interchange, and §8j.6 had just declared
it an ERROR: `aux_slot_shared`, on the grounds that a run exports one lane per slot. That reasoning
was right about the *run* and wrong about the *station*. A station's aux block is several slots;
what was missing was the answer to **which of them is THIS ramp's**.

### 8k.1 A station's aux block is DIVIDED among its ramps

`point_solve.aux_allocation` is the one owner: `{ramp_uid: [slot_id, …]}`, each ramp taking as many
slots as it declares lanes. Everything that used to ask for "the block's gore line" now asks for
this ramp's — `aux_gore_offset` — so two ramps at one station get two different mouths instead of
being placed on top of each other, and `point_export.wire_ramps` hands each one only its own aux
lanes instead of writing them all to the last ramp read.

**Order is derived from where the artist put the mouths** — nearest the through lanes takes the
innermost slot. It cannot be click order (§8i.2's mistake) and must not be uid order, which is
invisible and would reshuffle a network on a rename. Measuring the authored position is stable
under `Align Ramp To Aux`, because aligning preserves the order it read.

`aux_slot_shared` survives, narrowed to what is genuinely unrepresentable: the same slot claimed
twice *in one run*. Over-subscription — more ramp lanes than the block has slots — is now its own
finding, `aux_block_oversubscribed`, with the actionable remedy (widen the block).

### 8k.2 A gore is against the neighbour on the INBOARD side, which is not always the mainline

The innermost ramp's inboard neighbour is the mainline, whose through-lane edge is exactly where
that ramp's band starts. The outer ramp's is **the inner ramp**. Measured against the mainline
instead, the outer ramp's wedge is struck across the inner ramp's asphalt — §8j.3's overlap, one
participant further out. `point_solve.inboard_neighbour` chooses it; `solve_gore` takes it as
`inboard`, and reads that road's OUTBOARD edge, with its own `ramp_frame_sign`, because a sibling
may be reversed while we are not.

### 8k.3 Two boundaries must be paired by PROJECTION, never by index

`solve_gore` walks both boundaries in equal arclength steps and compared `a[i]` against `b[i]`,
which silently assumes the two advance together. A ramp faced down its mainline does; two sibling
ramps peeling off one station do not — the outer one is longer, so by 90 m its samples lag its
neighbour's by six metres of arclength, and the perpendicular offset was measured against a point
that is not opposite at all. **Two ramps with a real 5 m hole between them read as a gap of zero**
and no gore was paved between them, on a viaduct, over the drop. `_project_signed` finds the point
of the inboard boundary actually opposite each sample; that also gives the strip a proper ladder
instead of triangles skewed by the same drift.

This is §8j.3's sentence again — *a derived fact resolved through a proxy that is usually right* —
and the proxy here is the sample index.

### 8k.4 Which carriageway a ramp is on is a fact about where its mouth is

`Make Ramp` wrote `aux_fwd` unconditionally, so a ramp leaving the westbound side got its slot
opened on the eastbound one: geometry on the wrong side of the road, fed by traffic going the other
way. `point_solve.ramp_carriageway` reads the sign of the mouth's lateral offset — no aux slot has
to exist yet, which is what lets `Make Ramp` ask before it opens one.

### 8k.5 "Does this lane exist at the stop line" must be asked of the WIDTHS, not of the receiver

`merge_into`/`opens_from` name the lane a taper hands over TO, and `road_points.lane_taper_route`
leaves **both** None when it cannot resolve a receiver — indistinguishable, to a reader, from a
lane that runs the full length. Declaring `aux_bwd` on the arterial was enough to leave the forward
aux lane's receiver unresolved; the junction arm then offered that lane as one that exists at the
stop line, `lane_movements.target_lane` shifted every straight-ahead movement one lane outboard,
and `demo_main_1_F0` — an ordinary through lane leaving the crossing — came out reachable by
nothing. §8i.13's failure exactly, reached from the other side. `LaneRoute.i0`/`i1` are the first
and last sample at which the slot is a usable lane, which is the question actually being asked, and
they are right whether or not a receiver was found.

### What survives

The allocation is what the model gained, and it stays: a station may hand its block to several
ramps, and the two ways to get the geometry wrong — ramps that cross, and ramps that converge onto
the same line — both read as z-fighting asphalt rather than as an error, so the split is asserted
rather than looked at. `point_validate`'s self-test carries the case (`aux_fwd = 2` divided between
two one-lane ramps, and the over-subscribed block named). §8l is what the sample carries.

## 8l. One ramp out and one ramp in, at one station (2026-08-27)

> *"I mean a point in hw and road point able to accept a ramp for the incoming way and a ramp for
> the exit way."*

Not two ramps going the same way — **one leaving and one joining, at the same station**. That is
the ordinary half-interchange: eastbound traffic exits the expressway onto the arterial, westbound
traffic enters from it. The two ramps are one-way, they share both mainline stations, and they run
on **opposite carriageways** — which is also what makes it a straight run rather than a loop, since
two one-way ramps between the same two points travelling the same way would have to double back.

Four things were in the way, and each was the same shape as the rounds before it.

### 8l.1 `aux_block` answered "forward" for a reverse ramp

A station that hands a ramp to each carriageway declares `aux_fwd` **and** `aux_bwd`, and
`aux_block`'s case-free reading — the side with the most slots, ties to FWD — then answered FWD for
both. The reverse ramp's mouth was placed on the forward carriageway's gore line, on the wrong side
of the road. `aux_block(profile, direction)` takes the carriageway when the caller knows it, and
`point_solve.ramp_side_of` is what knows it: §8j.4's rule (*which carriageway is a fact about where
the mouth is*) asked per ramp instead of per station. `aux_allocation` allocates each carriageway's
block separately, `inboard_neighbour` only pairs siblings on the same side, and `check_aux_slots`
stops reading a station with one ramp on each side as a collision — the `AF*` / `AR*` ids keep them
apart by construction.

### 8l.2 An entrance's lane was cut off the wrong end

`_aux_handoffs` ends an aux lane at its gore, because *an exit lane has left with the ramp*. That
branch was written for exits and applied to both, so the lane a **merging** ramp handed into was
the stretch of aux slot **upstream** of the merge: `demo_ramp_F0` ended at x = 860 and its
successor's head was at x = 264, six hundred metres back down the road. An entrance's lane is the
acceleration lane and it *begins* where the ramp arrives, so the cut is the other way.

### 8l.3 …and nothing could see it, because every check asked whether an edge EXISTS

`broken` is *no successor*; `unreached` is *no predecessor*; `ramp_orphans` is *nothing leads to a
ramp*. A successor pointing 600 m in the wrong direction is healthy by all three. `flow_report`
now also reports **`misjoined`** — a successor whose head is not where this lane's tail is — which
is the question none of them were asking. `merge` edges are exempt: a taper hand-over is lateral
and its target legitimately spans the whole run.

`unreached` also stopped reporting lanes that are not `spawnable`. A deceleration lane that opens
after a junction *is supposed to* have no predecessor — it is entered by a lane change, and a
lane-change edge is `inner_lane`/`outer_lane`, not `next`. It kept its bite where it matters: a
full-width through lane is spawnable, which is exactly the `demo_main_1_F0` case in §8k.5.

### The sample carries it

`Add Sample Network` now has `demo_hwy_p002` and `demo_main_p007` each accepting an **exit** and an
**entrance**, and the westbound ramp is **two lanes**, so the multi-lane block is still exercised:

```
demo_hwy_AF0    -> demo_ramp_F0                      (eastbound exit)
demo_ramp_F0    -> demo_main_1_AF0 -> demo_main_1_F1 (…and its merge)
demo_main_1_AR0 -> demo_ramp_b_F0                    (westbound exit, 2 lanes)
demo_main_1_AR1 -> demo_ramp_b_F1
demo_ramp_b_F0  -> demo_hwy_AR0    -> demo_hwy_R2    (…and its merge)
demo_ramp_b_F1  -> demo_hwy_AR1
```

Plus a one-lane **spur** branched from the middle of the arterial with `Branch Ramp Here`, which is
also the sample's walkable gore: it leaves a kerbed street and is itself walkable, so its nose is a
kerbed island and its proxy is **not** `-noped`, while every gore touching the fenced expressway
is. An all-`-noped` answer would also be produced by a constant; having both is the rule being
`ped_access`-driven.

## 8m. Too many stations, too few, and what a hand edit costs (2026-08-31)

Three gestures, one question, and the question is the interesting part: *"is it okay to let points
connect randomly because of a manual edit?"*

**No, and the reason is one line in the reader.** `point_model.read_network` sorts a road's point
objects **by name** and that order IS the chain — `road_runs`, `road_corridors`, every sweep and the
export all read it. The LINKS are a separate fact and they are the authored one. So a hand edit that
breaks the naming (a rename, a `Shift+D`, a point dragged into another collection) silently
reorders the road: the links still say what is joined to what, the build follows them and is
correct, and the file no longer reads the way it behaves. Nothing said so.

- **`point_model.link_order(net, road)`** is the one owner of "what order do the links put these
  points in". Components are maximal sets joined by `SEGMENT` or `JUNCTION` (`road_corridors`' rule
  — a crossing does not split a street); each is walked from an end. A component whose links
  **branch** has no linear order at all — three points on one station is a junction, not a chain —
  so it comes back in `tangled` and is left alone, because guessing an order for it would be
  inventing one. It is **idempotent on a correct road**: each walk starts at whichever end already
  sorts first and components come back in the order their earliest member currently sits, which is
  the property that makes it safe to run over everything.
- **`Author ▸ Repair ▸ Renumber Roads`** renames to match. Links are authored, a name is derived, so
  when they disagree the name is the one that is wrong — never the other way round.
- **`chain_out_of_order`** (WARN) and **`chain_branched`** (WARN) are the gate saying it out loud.
  WARN, not ERROR, because the build is not wrong: it follows the links. What is wrong is that the
  drawing and the behaviour have parted, and the remedy is one button.

**`Merge Points`** collapses a contiguous run of one road's points into one station. The first of
the run survives with its uid, its cross-section and its facing — a merged station is a *station*,
not an average of several — and only its position moves, to the centroid (or the active point, by
property). Every link that **left** the run is carried onto the survivor; links inside the run are
what is being collapsed. Two refusals, and both are the model rather than a limitation: a
**non-contiguous** selection is "delete everything between them" wearing a friendlier name
(`Delete Point` says that out loud), and points in **different roads** are not a merge at all — a
crossing is a clique of `JUNCTION` links between points that each stay in their own road, and
collapsing them would delete the junction. That gesture is `Connect Selected` / `Make Intersection`.

**`Insert Point` takes one point now**, not only two. Selecting a single station and splitting the
span *after* it is the gesture an artist actually makes; the downstream neighbour is derived from
the `SEGMENT` link and the chain order together (`_next_in_chain`), because either alone is
ambiguous — the link says which two points are joined and the order says which of them is
downstream. At the tail there is no span and it refuses by name, pointing at `Extend Road`.

Both renumber, like every other structural gesture, so the invariant the whole model rests on —
name order **is** chain order — survives the edit.

## 8n. A junction that was never authored is consistent with itself (2026-09-04)

Every check in §8a–§8m reads the authored graph and asks whether it is **consistent**. That is the
right question for a fact with two owners, and it is blind to a fact with none.

Two roads whose end stations sit at the *identical position* with no link between them pass the
whole gate: no link to be asymmetric (there is no link), no chain with a hole (both chains are
whole), no clique to be incomplete (there is no clique). The only symptom is that no car can get
from one to the other, and nothing in the model says so. It shipped: `WORLD_REBUILD_PLAN.md` `W14`
— an 839 m road in the island's harbour whose first station is exactly on another road's last one,
unreachable, with the gate reading **0 errors, 0 warnings**.

`point_validate.check_meetings` is the missing eye. `road_end_unjoined` (WARN, like
`chain_unlinked`, and for the same reason — the file no longer reads the way it behaves, and both
remedies are one button) fires on a point where a road **stops** — one `SEGMENT` link or none, no
junction or ramp business of its own — standing within `CHAIN_TOL` of a point in another road with
no link either way. Two deliberate narrowings, both load-bearing:

- **open ends only.** A road that merely passes near another one is not a defect, and a ramp mouth
  is authored to stand beside its mainline (it is exempt as an `AUX` target).
- **in 3D.** A street that runs *under* an expressway is separated by the deck. Measuring in plan
  would report every underpass in the world.

The general rule, for the next check: *a consistency check cannot see something that was never
written down.* When a fact can be **absent** rather than **contradictory**, it needs a check that
asks the geometry, not the graph.


## 8o. A run's end is not a road's end (2026-09-04)

Two user reports about the junctions, after the footway existed for the first time. Both are §8f.1
and `W3` meeting: a **direction** with two owners, and a **width** the solve had never been given.

### 8o.1 The pad's cap and the street's own end section were cut on different planes

`point_model.station_axis` is the one owner of "which way does a station face", and the pad cuts
every cap on it (§8f.1). The carriageway is swept by `road_points.chain_tangents` — which is handed
a **RUN**, and a run stops at the mouth, so its end tangent falls back to "the single available
chord". That fallback is right for a road that ENDS and wrong for a mouth: the road does not end
there, it carries on across the pad, and `station_axis`'s central difference reaches the mouth on
the other side to say so.

They agree exactly while a road runs straight through a crossing, which is why every square
junction on the island measured 0.000 and every skew one did not. At Rinkai x Kuko the cap was cut
at 16.07 deg and the tarmac at 28.3 deg — 12.2 deg, which over a 21 m carriageway leaves the road's
own kerb line **2.23 m** off the pad corner it has to meet.

`point_profile.run_end_axes` answers the two end stations from the chain and nothing else (every
interior station's central difference is already the same calculation on the same neighbours). The
**Z slope stays the run's own**: `station_axis` is a plan-view frame by design, and forcing a flat
tangent would kink the vertical profile of every graded approach. Wired into the sweep, the export
and the overlay together, because the whole point is that they cut on one plane.

### 8o.2 A pad was sized for the carriageway, and a footway is pavement too

`corner_clearance`'s own docstring quotes "14.5 m at a square crossing" — a T2 road's **deck** half
(4 lanes + median + two 4 m footways = 29.0 m). `corner_setback` passed `half_in`/`half_out`, which
are `lane_profile.paved_extents`: the carriageway, 10.5 m. The two were indistinguishable for as
long as no road had a footway, and the day one did, every pad stood revealed as sized for a 21 m
road that is 29 m wide.

A corner then has no room to turn in. At a 95/85 crossing set back 14 m the two cap points at the
85 deg corner are **3.31 m** apart: the kerb turns 95 deg in 3.3 m, a ~2 m radius against an
authored `fillet_radius` of 6, and a 4 m footway swept round a 2 m radius folds through itself. The
95 deg corner had 6.58 m and looked right — "2 angles seem correct, but 2 other angles seem more
square", which is what a 2:1 asymmetry in corner length looks like from the ground.

Shortest corner run on the island: **3.15 m -> 14.95 m**. The pads are now the size
`corner_clearance` has been asking for since it was written.

**The rule.** A number that is derived from a section must be derived from the WHOLE section. When
a layer is optional and has been zero everywhere, every consumer that quietly stands for "the
road's width" is holding a value that happens to be right, and none of them will say so.


## 8p. A stop line the seeder guessed, and a mouth that left its own road (2026-09-04)

Two defects, one seam: **`seed_district_roads` and `Auto Setback` both decide where a stop line
goes, and they were not asking the same question.** Found by comparing the island's `.blend` against
what its own seeder produces — a hand edit had deleted two stations, and the interesting part was
why they were there.

- **The seeder guessed the setback, and the guess was always low.** Every mouth went in at a flat
  14 m and every station within `14 + MIN_SPAN` of the crossing was pruned as belonging to the
  junction. `Auto Setback` then solved the real distance — **17.9 to 35.5 m** over the island's ten
  pads — and walked each mouth out over the top of whatever had survived a window computed from the
  wrong number. `point_solve.solved_setback` is now the one owner: extracted from `auto_setback`,
  it is a function of the ARMS alone, so the seeder can ask it with planned arms
  (`seed_district_roads._PlannedArm`, `place_setbacks`) before a single Empty exists and prune the
  real window.
- **Nothing could see the result.** Both stations carry the same profile, so `check_tapers` returns
  on `dw == 0` before it ever measures the span, and `station_coincident` only fires at 1e-6. The
  island shipped an ordinary station **0.19 m** past Rinkai-dori's mouth at the Chuo crossing, plus
  two more at 5.66 m and 6.87 m, with the gate reading 0/0.
  `point_validate.check_mouth_clearance` / **`station_crowds_mouth`** is that eye: measured along
  the RUN (a mouth's other neighbour is across the pad, which is exactly where a short gap belongs),
  ERROR under `CHAIN_TOL` and WARN under `MIN_MOUTH_CLEAR`.
- **A MOUTH SLIDES ALONG ITS OWN ROAD. IT DOES NOT MOVE SIDEWAYS OFF IT.** `auto_setback` wrote
  `centre + out_dir * tail`, a ray from the pad CENTROID — and the centroid is on none of the roads.
  On a symmetric X it is the crossing and the two are the same point, which is why this was
  invisible for as long as every pad was one. On the island's 5-arm port junction the centroid sits
  **12 m** off the crossing, so `port_road`'s mouth landed metres to the side of its own centreline;
  the next station along was still where the road is, so the approach left the pad on a bearing
  **40 deg** from its own alignment, ending up **3.54 deg** from Chuo-dori's arm — two carriageways
  leaving on top of each other, with the pavement drawn as a 90 m spike. The centre is now projected
  onto each mouth's own axis first, so the mouth cannot leave the centreline; the distance to the
  centroid comes out `hypot(tail, offset)`, never less than `tail`, so every corner still clears.
- **The no-move test has to be on the POSITION.** `abs(old - tail) < 1e-4` compared a distance to
  the centroid against a distance along the arm. Once those stopped being the same number, a mouth
  that was already exactly right still reported as moved — a settled solve reading as a drifting one.

**Still open: `Auto Setback` is not idempotent, and its docstring says it is.** Pressing it a second
time on the island moved 17 of 35 mouths by up to 30 m, and a fourth press by 58 — a monotonic
runaway, because `recommended_tail_length` only ever searches UPWARD from the widest mouth it is
given while the mouths themselves move the centroid it measures from. The fixes above bound it
(the mouth stays on its axis, so the drift no longer compounds sideways) but do not close it: the
centre, the mouths and the AUTO facings derived from them are one coupled system and settling it
needs a fixed-point solve, not a single pass. It is harmless in the build, which presses the button
exactly once — and it is a live trap for anyone who presses it twice by hand. `WORLD_REBUILD_PLAN.md`
`W17`.

## 8q. A gesture that hands out a JUNCTION link owes the whole pad (2026-09-04)

Three user reports from one session of hand-editing `Island_base.blend`, and the first is the
interesting one because the gesture was *right about the thing it was written for*.

- **`Merge Points` carried the links and nothing else.** §8m gave it the rule "every link that LEFT
  the run comes with it", which is correct and was all it did. But a pad is three facts written
  together and `make_pad` was the only thing that had ever written them: the members form a
  **clique**, each is typed `INTERSECTION`, and each hangs off the `JCT_*` handle. Merging a plain
  station with the mouth beside it made the *plain station* the survivor (it is `doomed[0]`), so it
  inherited the JUNCTION links while staying `SEGMENT` and unparented — and where two members of one
  pad merged, the rest still pointed at a uid that no longer existed. The island's port junction came
  out a 4-node component with **3** edges and two mouths typed `SEGMENT`: **8 gate errors from one
  gesture**, and `Auto Setback` then solving a clique that is not one.
  `point_ops.complete_junction_cliques` is the shared owner — it completes the clique, sets the
  roles, and re-parents to the handle (a mouth that is not parented does not turn with the crossing
  and `junction_centre` does not count it). `Merge Points` calls it; `Repair Links` recovers a file
  already broken by it (measured: 8 errors → 0, 3 links written, 2 roles fixed, 2 mouths parented).
  The coverage smoketest had a merge case already and it covered none of this, because it merges
  **interior** stations only — the new case merges *into* a crossing and asserts all three facts.
- **A road that ends 76 m past a junction still contributes two arms.** Four arterials meet at the
  island's port, and the plan wrote that one place as *three* coordinates: Port road's head and
  Nishihama-dori's tail shared `(-96, -560)`, Hama-dori's tail sat 12 m away and Chuo-dori's head
  **76 m** away. The seeder clusters crossings within `CROSSING_NEAR` (21 m), so Chuo-dori never
  joined the cluster: it ran *through* the pad to a dead end, and a road that passes through
  contributes a mouth either side. The result was a 5-arm pad with a stub hanging off it. All four
  now terminate on the one vertex, which is the plan's own rule ("terminates on another arterial,
  never in mid-air") — one arm each, a 4-arm crossing.
- **`resample` had no opposite number.** It lays a station every `spacing` metres so a road HAS
  stations where it needs them; on the straight legs between two authored plan vertices those are
  exactly collinear and cost geometry, build time and a lane control point each for nothing — the
  island's Port road carried a station every 67 m down 232 m of dead-straight quay.
  `seed_district_roads.simplify` is Douglas-Peucker over the 3D polyline, run twice and unioned:
  against the ROAD at `STATION_MERGE_TOL` (0.25 m) and against the GROUND at `GROUND_MERGE_TOL`
  (1.0 m). Two rules behind that shape:
  - **Douglas-Peucker, never a pairwise walk.** The recursion always measures against the chord
    between two points that are being KEPT, so the tolerance bounds the distance from the original
    polyline outright. A local "is this station on the line between its neighbours" test measures
    each candidate against a line that already reflects previous removals, and a long shallow curve
    then walks off by many times the tolerance one legal step at a time.
  - **The ground is simplified too, and looser.** A station's `ground_z` is what `road_support` sizes
    its piers from, so a dead-straight deck over a bay must not collapse to its two shore stations —
    the seabed between them would be read as a straight line and every pier cut to the wrong depth.
  Mouths are protected outright: a stop line is not a shape, it is an agreement between two roads
  about where the pad hands over.
  Measured on the island: **433 → 184 stations (249 dropped, 57%)** for **+0.02 m** of drape error —
  the worst road-to-ground gap on any at-grade road went 0.32 m → 0.34 m, against a
  `MovementController.stepUpLedge` of 0.35 m. The bridges and the benched touge are unchanged
  (17.9 m and 21.6 m are the bay and airport decks; 8.7 m is the touge's own cut/fill).

## 8r. A corner is a bend, and only the JOINT has to move (2026-09-04)

`W15`, closed. Where two plan arterials meet end-to-end the seeder links them SEGMENT-to-SEGMENT,
and **a run never spans two road collections** (`point_solve.road_runs` walks one road's own
points), so each sweeps its own carriageway to the joint at its OWN heading. Two sections cut on
planes 98-124 deg apart agree nowhere: measured on the island, the two chains ended **24-28 m
apart** with a link across the hole and no road built in it.

**The question was whether to round the corner or use a bend station. They are the same answer.**
The model's own answer for a road that turns is a bend station -- the Empty's rotation IS the
gesture -- and a bend station is exactly what a rounded corner is made of. What could not stay was
the *joint sitting in the middle of the turn*.

- A **2-arm pad** was tried and rejected: `check_junctions` says in as many words that "a pad of 2
  arms is a segment connection, not a junction", `lane_movements` wires only the innermost lane
  across it, and every mouth comes out 40-53 deg off its pad centre.
- **Merging the two roads into one** works geometrically and costs a street its name --
  Nishihama-dori swept as part of Rinkai-dori, and every lane id, `traffic_route` prefix and future
  zone reference with it.
- **What shipped:** `seed_district_roads.fillet_corner` trims both chains back by the tangent
  length, lays a circular arc between the tangent points, and **splits the arc at its midpoint** so
  each road carries half the turn. At that midpoint both roads run in the same direction, which is
  the straight-through joint this branch already built correctly. A street name changing halfway
  round a bend is what streets do.

The radius is **derived from the section**, never a constant (`W16`): `CORNER_RADIUS_FACTOR` (3.0)
times the DECK half-width, so the inner deck edge turns through `R - half = 2 * half` -- 29 m on a
T2 arterial, 16 m on a T3 street. The touge's `HAIRPIN_RADIUS` (12 m) is the tightest thing in the
world and it is a mountain switchback on the narrower section; an arterial rounding a headland is
not that. A fillet that wants more tangent than the road has is clamped to
`CORNER_MAX_CHAIN_FRACTION` and the radius recomputed from what fits, so the arc still MEETS both
tangents.

Two things fell out of it, and both are the shape this document keeps recording:

- **The geometry meeting is not the graph chaining.** With the corner rounded the flow report's
  verdict moved from `open_end` (a lane running off the edge of the network) to **`broken`** (a tail
  sitting on a head with no edge between them) -- the road fixed and the lane graph still silent.
  The runtime would have recovered it, since `LaneGraph` derives a proximity edge inside
  `JUNCTION_RADIUS`, and that is precisely the reason to emit it here instead: §8f.4's rule is that
  reachability is not geometry and needs its own eye, and leaving the one road-to-road hand-over in
  the world to a distance test puts it where no check can see it.
  `point_export.wire_joints` emits the edge, matched by GEOMETRY (a lane's tail and its successor's
  head are the same physical point) rather than by lane index, because which of the two chains runs
  which way is an accident of how the plan was authored. `broken` **12 -> 0**.
- **A measurement outlives the thing it measured.** `_joint_bend` took a vector from the place
  centroid to each mouth, which is only meaningful while the two mouths stand either side of it.
  Once the fillet made both chains END on the same point, that reading collapsed and reported a
  confident **180 deg** on three corners whose real deflection is **7**. `_chain_bend` asks each
  road's own chain at the shared station.

Measured, all three joints: gap **25.6 -> 0.00 m**, deflection **106 / 124 / 98 -> 7.3 / 7.5 /
7.3 deg** (one arc step of `CORNER_ARC_STEP_DEG`, i.e. the arc's own discretisation, not a corner).

## 8s. A pad is a plane and the ground under it is not (2026-09-05)

`W20`. A junction pad meets every approach at that approach's own elevation -- `point_solve._idw_z`
interpolates the surface from the mouths, and that rule is right, it is what stops a junction on a
grade from stepping. Between the mouths, though, the surface is interpolated while the **ground goes
on doing whatever it does**. Where the ground rises inside the footprint, the pad is under it.

**Why that matters is the COLLIDER, not the eye.** `point_build.cut_ground` punches a vertical prism
through the terrain over every band, pads included, so the burial is invisible -- but the island's
collision mesh (`Ground-colonly`) is deliberately in a collection the cut does **not** recognise as
terrain, because continuous collision under the whole island is the base layer's whole job
(`build_island_base`). So the player walks on ground the eye says is not there, standing above a
road that has vanished under their feet. Measured at the island's port crossing, where four
arterials meet on ground that falls **2.09 m** across the pad: the interior stood **0.924 m** proud
of the pad surface -- against a `MovementController.stepUpLedge` of 0.35 m, a wall rather than a
step. Reported by the user, who lifted the pad by hand.

`seed_district_roads.pad_lifts` derives it: probe the ground on a 2 m grid over the pad's own
footprint, take the largest amount by which it stands above `_idw_z`, and raise **every mouth of
that pad by that one number**. Three things about the shape of it:

- **A uniform offset, never per mouth.** `_idw_z` is linear in the mouth heights, so adding the same
  number to all of them raises the whole surface by exactly that number -- one pass is exact, no
  iteration, and the pad keeps its tilt. Raising them by different amounts would re-shape the pad to
  fit a hummock, which is not what a junction does.
- **Applied as a FLOOR before the grade cone** (`height_profile(..., floors=)`), so the approaches
  grow the ramp up to the raised pad instead of stepping at the stop line. The embankment then
  follows for free: `road_support.support_kind` reads the same `delta`. Measured, the port
  approaches now sit up to 1.47 m proud at the mouth with a p95 of 0.58 m -- a short local ramp, not
  a road on stilts.
- **`PAD_BURY_TOL` is a tolerance for *exactly on*, not a margin** -- the same 5 cm and the same idea
  as `point_edges.BURIED_TOL`. Without it every junction in the world was lifted by the full
  `PAD_CLEARANCE` for raycast noise: measured over the island's nine pads, the eight on flat ground
  read a raw burial of **0.000000 to 0.000122 m** and the one on a slope reads **0.924** -- four
  orders of magnitude apart, so the trigger is not a judgement call. One pad lifts, by 1.17 m; the
  other eight are untouched.

**What this does NOT fix, and the user named it: the collider is uncut.** Lifting the pad clears
*this* burial, but any road surface that sits below the terrain is still unreachable, because the
cut is a visual-only operation by design. The general answer is either to cut the collider too
(which reopens the fall-through the base layer exists to prevent) or to build the road's own
collision proxy as the thing the player stands on and accept the terrain proxy beneath it -- a
`-colonly` ordering question, not a road-shape one. Recorded as `W21`.

## 8t. The ground cut had never cut anything (2026-09-05)

Started as `W13` -- "a cut has no batter", the benched touge running in a vertical-walled slot --
and the first measurement said something larger: **every cutter in the world was a flat sheet.**

`cut_ground` built its solid with `bmesh.ops.solidify(bm, geom=[face], thickness=2*depth)`, and
that produced no thickness at all: measured across all 32 cutters, `z_min == z_max == -40.00`. So
every boolean was a no-op, the evaluated terrain came back with **exactly** its base vertex count
(19477), and no road anywhere had ever had ground removed from under it. It was invisible for as
long as every road was draped ON the terrain -- a road standing 0.16 m proud of the ground needs no
cut to look right -- and it surfaced the moment two roads went *below* it: the user's buried
junction pad (`W20`) and this slot are the same defect seen twice.

`_cut_tube` / the lofted branch build the solid explicitly instead. Three things came out of it:

- **A road is cut as a TUBE, not as one lofted outline.** `band_of` walks the left edge out and the
  right edge back, so a road that switches back on itself produces a self-intersecting outline, and
  the single n-gon cap across it is geometry the exact boolean declines to arrange. A tube has only
  two small end caps.
- **Coincident rings are welded, not emitted.** Where a stretch is at grade the cut depth is 0 and
  the two middle rings land on the same points; a quad with two coincident corners is a degenerate
  face and a solid built from them is not manifold -- which the boolean also answers by doing
  nothing, silently.
- **The reach is asymmetric on purpose.** Downward it uses the full depth, upward it stops at each
  section's own daylight point plus `CUT_BELOW`, because ground above that is hillside the batter
  has just run out to meet. Making the downward reach local as well was tried and measured: holes
  fell from 57% of samples to 21% for no gain, so it stays global.

**The batter itself** is `road_support.cut_footprint` + `CUT_SLOPE` -- `fill_footprint` for the
other sign of `delta`, and steeper than the fill (1:1 against 1:1.5) because a cut face is
undisturbed ground with the cohesion to stand while a fill is placed earth on its angle of repose.
`support_profile` now answers `toe_half_width` and `batter_slope` for a CUT as it always did for a
FILL.

**And `CUT_MAX` had two owners that disagreed by 8x.** `road_support.CUT_MAX` said a trench deeper
than **3 m** is a TUNNEL; `island_v3_terrain.MAX_BENCH` -- the depth a benched road is deliberately
allowed to dig -- said **25 m**; the shrine touge measures **8.1 m** and therefore fell in the gap,
classified as a tunnel that nothing builds. A benched road is a CUT by construction, so the number
that decides where a cut stops has to cover what the bench rule may dig. `MAX_BENCH` reads
`CUT_MAX` now.

Measured over the island, road centrelines, ground standing proud of the road surface:

| | before | after |
|---|---|---|
| samples with the ground cut through | 6% | **57%** |
| roads with ground standing proud | 1 of 12 (+8.71 m) | **1 of 12 (+8.71 m)** |
| worst proud, the other 11 roads | up to +0.42 m | **+0.00 m** |

**`W13` IS NOT CLOSED, and the cause is NOT the boolean.** The shrine touge -- the one road the
finding was written about -- is still 0% cut and still stands 8.71 m under its hillside. Two repairs
were tried against the hairpin theory and BOTH were measured and reverted: splitting the cutter
wherever the road has turned 150 deg (so no chunk's footprint can overlap itself) changed the touge
not at all, and giving each cross-section a local 3 m skirt instead of the full depth (so a
switchback's loops cannot enclose one another) changed it not at all either, while dropping
through-holes elsewhere from 57% of samples to 21%. Neither is in the tree.

**What the measurement actually says**, at the deepest station (-796.9, 775.4), road surface
172.04:

    band.surface_z   172.04      the road, as the sweep built it
    ground_fn        169.04      the live sampler: ground 3 m BELOW the road
    rka_delta         -8.00      the solve: road 8 m BELOW the ground
    -> _cut_section h  0.00      so the cutter has nothing to remove, and removes nothing

`ground_sampler` and the station's own stored `ground_z` disagree by **11 m** in the same place, and
`_cut_section` asks the sampler. `scene.ray_cast` does respect modifiers, so the sampler is reading
the terrain as already cut and punching through whatever it does not consider terrain
(`gen_collection_names`, `is_terrain`) -- which on a mountainside, with the road's own generated
output in the scene, is where the 11 m has to be coming from. That is a sampler question, not a
geometry one, and it is where the next attempt should start: the hairpins are exonerated.

## 8u. The sampler was reading the terrain the last build had already cut (2026-09-05)

§8t ended by naming the sampler and it was right. Two defects, one in each half of the cut, and
both of them the same shape as everything in §8f-§8t: **a fact with two owners, answering by
returning a plausible number rather than an error.**

### A ROAD'S OWN OUTPUT IS NOT TERRAIN -- AND A BOOLEAN IT HUNG ON THE TERRAIN IS ITS OUTPUT TOO

`ground_sampler` already keeps that rule for MESHES: it punches through a road's own generated
geometry rather than sampling it, because the ray otherwise lands on the surface the last build
swept and the road walks a little further up every rebuild. It did not keep it one level down. The
cut is a BOOLEAN MODIFIER hung on the terrain, `scene.ray_cast` evaluates modifiers, and
`build_network` created the sampler while the previous build's 32 booleans were still on the ground
-- so a ray aimed at a deep station went straight down the road-shaped slot the LAST build punched
and found nothing at all. `_cut_section` reads `gz is None` as a daylight height of **0** and
removes nothing, which re-cuts the road out of existence one press at a time: **once the slot is
there, the hillside can never be seen again.**

Measured on `Island_base`'s shrine touge, 66 stations, in the file as shipped:

| | stations with NO ground under them |
|---|---|
| with the previous build's cuts standing | **29 of 66** |
| after `clear_cuts` | **0 of 66** |

It is invisible in the tool that builds the island, because `build_island_base.py` builds a fresh
scene and presses Build exactly once. It bites the ARTIST, who presses it twice -- which is rule 3
("author through the operators an artist presses") seen from the maintenance side rather than the
authoring side.

`build_network` now clears the cuts *before* it samples, and hands the ONE sampler to the solve, the
write-back **and** the cut. Those were two samplers, created at different points in the build, so a
station's stored `ground_z` came from a different reading of the scene than the daylight height its
own cutter was shaped from -- two owners of "how high is the ground here", free to disagree by
whatever the build did in between. That is the 11 m §8t measured.

### A SWITCHBACK'S CUTTER SELF-INTERSECTS, AND THE EXACT SOLVER HAS TO BE TOLD

With the sampler fixed the touge still stood **8.00 m** under its hillside at 18 of 225 stations,
and the cutter looks perfect from every angle the addon can ask about it:

    verts 6572, faces 6579, non-manifold edges 0, loose verts 0
    a point at the buried station tests INSIDE by ray parity

That is exactly why this survived three attempts. A hairpin stacks two loops of road metres apart
in Z and on top of each other in plan, and `_cut_tube` reaches `depth` BELOW each section's own road
surface (§8t: making that local was measured and cost 57% -> 21% of through-holes elsewhere), so one
loop's solid passes clean through the next. The result is closed, manifold and **self-intersecting**
-- and for a doubly-covered region Blender's exact solver has no defined inside, so it leaves the
ground standing, silently. `use_self` is the flag that means precisely this.

One cutter against a copy of the island's ground, the 10 deepest touge stations:

| boolean | still buried | worst |
|---|---|---|
| EXACT, defaults | 10 / 10 | 7.99 m |
| EXACT, `use_self` | **2 / 10** | **3.20 m** |
| EXACT, `use_hole_tolerant` | 2 / 10 | 3.20 m |
| FLOAT | 7 / 10 | 7.99 m |

**`use_hole_tolerant` is NOT what is set**, although it gives the same numbers about 5x faster.
This operand has no holes -- it self-intersects -- and leaning on a flag documented for the other
condition is a fix that can evaporate on a Blender upgrade with nothing on screen to see. The whole
island, station by station:

| | before | after |
|---|---|---|
| stations with ground standing proud | 18 of 225 | **2 of 225** |
| worst proud | 8.00 m | **3.20 m** |
| roads affected | 1 of 12 (the touge) | 1 of 12 (the touge) |

### What is left of `W13`: TWO HAIRPIN APEXES, AND THEY ARE TRIPLE-COVERED

The residue is stations 30 and 49 of the touge, both at the apex of a hairpin, at **3.06 m** and
**3.20 m**. The number that names them is the parity: a ray from either apex crosses the cutter's
own surface **3** times, against 0 or 1 at every neighbour that cuts clean -- three shells of the
same solid overlap there, not two. Reversing the cutter's faces was tried and measured (9 -> 36
buried), so the winding is right by construction and `recalc_face_normals` is not the cause. Start
at the triple cover.

## 8v. The road deforms the ground; it no longer cuts it (2026-09-06)

**THE BOOLEAN IS GONE.** `cut_ground`, `clear_cuts`, `_cut_tube`, `_cut_section`,
`_outward_offsets` and `_self_intersects` were deleted, along with the `Cut Ground` build option and
the exporter workaround that hid the cutters from glTF. What replaced them is
`island_v3_terrain.Carve`: the roads are a **ceiling on the height field**, and the ground mesh is
sampled from the field with that ceiling applied.

### Why, after four rounds of fixing the boolean instead

Each round found a real defect and each fix was correct; the pattern only reads as a verdict on the
tool when they are put in a row.

| round | what was wrong |
|---|---|
| `W22` §8t | the cutter was a zero-thickness sheet -- **no boolean had ever cut anything** |
| §8u | the sampler raycast the terrain the PREVIOUS build had cut, so a deep station found no ground, read a daylight height of 0, and was never cut again |
| §8u | a switchback's cutter passes through itself; the exact solver has no defined inside for a doubly-covered region and leaves the ground standing, silently |
| -- | and it could never touch the COLLIDER, because a boolean removes material and the always-resident ground is the one layer that must never have a hole (`W21`) |

The measurement that settled it: from **identical inputs** -- the same network, the same terrain,
the same sampled `ground_z` byte for byte -- pressing Build a second time took the touge from 2
buried stations to 9 (3.20 m to 6.92 m). That is not a bug to chase.

**And it was never the industry approach.** Open-world terrain is a heightfield; a heightfield has
no topology to cut. From Unreal's `Deform Landscape to Splines` to a Houdini road HDA, the operation
is to write the road's elevation INTO the field, and the terrain's collision is that same field, so
the visual and the collider cannot disagree. Manual cut-and-stitch is real but narrow: it is for
what a 2.5D field cannot express -- tunnels, overhangs, a bridge abutment into a cliff -- where the
practice is to punch a terrain hole and hand-model the piece that closes it.

### The rule, and the two halves that make it safe

    z = min(z, road_corridor_z(x, y))          # roads may DIG, never FILL

**Carve-only is load-bearing, not a simplification.** Unreal's deform *sets* the height, raising and
lowering. Copying that would be wrong twice: `road_support` already owns FILL (it derives
NONE/FILL/PIER/CUT from `surface_z - ground_z` and builds the toes and columns), so rising ground
would build the same embankment a second way; and it would **fill the bay**, where `W1` carries
Hama-dori 18.20 m over open water. `min` needs no special case for a bridge, because it cannot
raise ground. The two are on opposite signs of one number: **the terrain owns the cut and its
batter, `road_support` owns the fill and the piers.**

**One direction of derivation.** A road's profile is derived FROM the ground (`bench_profile`,
`grade_cone`, `height_profile` all sample it), so carving the field the router reads makes the next
pass derive a lower road, which carves deeper, forever. A `Carve` is therefore a separate VIEW
(`carved_field`) handed only to the ground-mesh builder. Alignment against the original terrain,
terrain deformed to the finished alignment, never back -- the order a real road is built in.
`build_island_base` emits the ground **twice** for exactly this reason: natural, seed and solve,
then carved.

### The verge -- why a 12 m grid is enough, and subdivision is not the lever

The carved FIELD can never stand above the road; a MESH can, because it samples at grid nodes and a
cell straddling the paved edge has one corner at road level and the next up the batter, so the
triangle between them cuts over the carriageway. Widen the flat shelf by one full cell and every
node within the paved width -- and the first one outside it -- is capped, so no triangle spanning
the road can have a raised corner. That is an argument, not a tuning.

Measured over the whole network, **1 185 377 samples** at 0.25 m across every road and 2 m along it:

| verge | samples proud | worst |
|---|---|---|
| 0 x cell | 450 | 3.508 m |
| 0.5 x cell | 35 | 0.941 m |
| **1.0 x cell** | **0** | **0.000 m** |

Subdividing instead converges far too slowly to be the answer: 12 m -> 3 m cells took the worst
intrusion only 3.508 m -> 0.647 m -- still past `MovementController.stepUpLedge` (0.35 m) -- for 16x
the vertices, because the error scales with the batter's own 1:1 gradient. The verge removes the
cell from the road's edge instead. (Had it not worked, the next move was **breaklines** -- the road's
edges as triangulation constraints -- not more subdivision.)

**A hairpin needs no case at all.** Two legs of a switchback both propose a cap where they overlap
and the lower wins, which draws the cut face between them; at either leg's own centreline that
leg's cap is the lower, so each is clear, and the nose of ground left standing between them is what
a switchback bench looks like. The geometry that defeated the boolean four times falls out of `min`
with nothing written for it -- which is the strongest reason to believe the operator.

### What it measures, on the built island

| | boolean | carve |
|---|---|---|
| stations with ground proud (visual) | 2 of 225, worst 3.20 m | **0**, worst **0.00 m** |
| ...on the COLLIDER | never cut at all (`W21`) | **0**, worst **0.00 m** |
| `Ground` vertices | 19 477 -> 30 577 (36 039 with `use_self`) | 19 477 -> **21 547** |
| collider vs visual | different meshes by design | **identical** |
| repeated Build | 2 -> 9 buried stations | **stable**, 0 every time |
| terrain evaluation | 3.5 s, or 43.6 s with `use_self` everywhere | none -- there are no modifiers |

28 295 samples at 4 m along and +/-6 m across every road, both sheets, all zero.

### The one thing it introduced, and the eye that was put on it

A ground carved to its roads is not the natural ground, and `write_ground_back` could not tell the
difference: a second Build in the saved file sampled a surface that MEETS the road and stamped it
back as terrain -- **195 of 225 stations, the biggest by 9.46 m**, quietly reclassifying a benched
stretch as at-grade. No geometry changed (a CUT's batter is the terrain's now, so a CUT reading as
NONE builds the same nothing), but it destroyed the record every support decision is made from.
`point_build.CARVED_FLAG` is set by the carve and read by `write_ground_back`, which now refuses
with a message. Re-measured: **0 of 225 rewritten.**

## 9. Verification

**ONE COMMAND, and it exists** (`blender/tools/check_roads.sh`). The repo has no CI, and the
previous addon's ~3.5 kLOC of hand-run smoketests did not prevent this rewrite -- hand-run
discipline is exactly what decays once a project gets boring, so a git hook or an Action can run
the whole gate as a unit:

```bash
blender/tools/check_roads.sh            # 18 checks: 11 self-tests + the lanekit gate + 5 smoketests
blender/tools/check_roads.sh --quick    # pure-Python only, no Blender, ~2 s
```

What it runs, and what each step is for:

```bash
# 1. pure-Python self-tests (no Blender)
python3 blender/lib/road_points.py       # chain, tangents, arclength, lane_taper_route (2.1a)
python3 blender/lib/lane_movements.py    # junction movement legality, the ONE rule set
python3 blender/lib/lane_profile.py      # slots, interpolate, slot_offset -- the cross-section
python3 blender/lib/road_support.py      # delta -> NONE/FILL/PIER/CUT/TUNNEL
python3 blender/addons/road_kit_authoring/point_model.py      # schema + .roads.json round-trip
python3 blender/addons/road_kit_authoring/point_profile.py    # station -> Profile, drop_side, loop
python3 blender/addons/road_kit_authoring/point_solve.py      # carrier numbers, pad, turns, setback
python3 blender/addons/road_kit_authoring/point_edges.py      # where the kerb opens (3.2)
python3 blender/addons/road_kit_authoring/point_validate.py   # the gate itself
python3 blender/addons/road_kit_authoring/point_export.py     # .lanekit v2

# 2. the standing gate, on a freshly exported testbed
python3 blender/tools/check_lanekit_graph.py <testbed>.lanekit.json

# 3. headless smoketests  (--python-exit-code MUST precede --python)
blender/tools/run_smoketests.sh point
```

**The island is deliberately NOT in this list** (step 6 deferred, 2026-08-22). The acceptance
shapes it would have supplied -- a gore, a skew junction, a parallel overlap -- are constructed in
`smoketest_point_build.py` instead. That is stricter, not weaker: they are reproducible, they are
named, and they fail loudly.

**Smoketests** (`smoketest_point_*.py`), each asserting an invariant, never an object name:

- **model** — uid survives a rename; **Shift+D on one point yields a NEW uid and no dangling inbound
  link**; a deleted link target degrades to a reported dangling link, never a traceback; unlinking a
  point does not leave a zombie.
- **taper widths** — 3 → 2 lanes across two stations; measured half-width falls monotonically over
  exactly the authored distance, and `drop_side` decides which lane goes.
- **taper routes** (§2.1a, the one with numbers) — the merging lane's tail is within **0.3 m** of the
  receiving centreline, the opening aux lane's head is not outboard of its inboard neighbour, the run
  spans the **full** station-to-station distance, and the tail is within **4.5 m** of its successor's
  head.
- **offside** — `drop_side = MEDIAN` kills `F0` and slides `F1`/`F2` inward, with the divide fixed.
- **one-way** — `lanes_bwd = 0` sweeps single width, not double (redesign defect 1).
- **junction** — pad covers every mouth, no self-intersection, connector endpoints within 4.5 m of
  the lanes they bridge, pad follows the grade.
- **ramp** — after `Align Ramp To Aux` the ramp band edge and the aux slot edge agree within
  tolerance; the validator reports the residual *before* alignment.
- **outline** — no kerb sample stands on another road's asphalt at a gore.
- **live** — dragging one point marks exactly its road + link-neighbours dirty and rebuilds nothing
  else.
- **collision** — every road and pad emits a `-colonly` proxy covering its own span.
- **support** — the same road at Z = 0 / 2 / 12 yields NONE / FILL / PIER with no other edit; the
  FILL toe half-width equals `fill_footprint()` and the batter is 1:1.5, not a vertical prism; a
  viaduct with an aux lane widens its deck with the lane; the deck is never left holding nothing
  (defect 7).
- **export** — v2 round-trips through `check_lanekit_graph.py`; a bezier lane's `getBakedPoints()` is
  smooth (max heading step below a threshold) where the v1 polyline was not; `spawnable` is true on
  through lanes and false on connectors; `arms`, `zone_id` and `junctions` present.

### Traps already paid for — do not re-learn

- `--python-exit-code 1` must come **before** `--python`, or every test silently reports success.
- Blender exits 0 on a script crash without that flag.
- `--background` never calls an operator's `invoke()`; `INVOKE_DEFAULT` degrades silently to
  `EXEC_DEFAULT` with property defaults, so headless tests must pass invoke-computed values.
- `lib/` is reached by a bare `sys.path.insert`, so Blender's *Reload Scripts* never reloads it —
  the addon's `unregister()` must keep purging those `sys.modules` entries.
- Reading materials off evaluated GN geometry segfaults roughly 1 run in 5.
- A collection lookup must be local-only (`library is None`) — linked libraries carry same-named
  collections.

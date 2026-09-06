# Road style (materials & profile assets) + the Path3D preview — design

Companion to **`ROAD_POINT_GRAPH.md`** (the design of record) and the addon's `README.md`.

> **STATUS: BUILT, 2026-08-27.** All six sequenced steps are implemented and the gate is green
> (`check_roads.sh` → **PASS=18 FAIL=0**, up from 17 — `point_style.py` joins the pure-Python
> self-tests), verified end to end into Godot. What actually landed, and where it differed from
> this design, is recorded in §7. Read §7 first; the rest is the reasoning that got there.

Three independent features, plus the defect the first one found:

1. **Preview the Path3D Godot will actually drive** — directed, in the viewport, before export.
2. **Author what the road is MADE OF** — a material *or* a swept profile asset per layer:
   carriageway, median (none / double-yellow / raised / wall), the outboard side stack
   (gutter → kerb → footway → wall), and the white lane markings.
3. **Grab a junction by its centre** — the `JCT_*` handle follows the live intersection centre
   instead of a creation-time snapshot, and turning the whole crossing becomes possible.

Everything below is measured against the shipped code at 2026-08-27, not inferred. The gate was
green (`check_roads.sh` → **PASS=17 FAIL=0**) before and after every measurement.

---

## 0. What the end-to-end pipeline does today (verified, not assumed)

Ran the whole chain headless on the addon's own `Add Sample Network`:

```
rka.demo_network → rka.validate → rka.point_build → rka.export_lanekit
  → build_piece.sh District_roadtest_9_9
      → export_world.py (glTF)  → WorldBaker  → NavBaker  → PieceBinaryConverter
```

Result — **the pipeline works end to end**:

| Stage | Outcome |
|---|---|
| Build | 6 roads, 8 runs, 1 pad, 5 gores, 33 edge runs, 28 collision proxies, 75 GEN objects |
| Gate | GREEN (37 warnings, all `ground_unsampled` — no terrain in the sample file) |
| Export | 43 lanes, 1 junction → `.lanekit.json` v2 (336 KB) |
| Bake | `pathlanes=43`, 43 `Path3D` + 43 `Curve3D`, 47 `MeshInstance3D`, 28 `StaticBody3D` |
| Navmesh | baked, 50 vertices |

So this design changes **nothing structural** in the pipeline. Both features are additive to
`point_build` / `point_export` / `point_preview`, and neither needs a Godot-side format change.

### The three gaps that pipeline run exposed

**(a) The exported Path3D is not the road.** `WorldBaker` builds each `Curve3D` from the lane's
`curve` block (real bezier handles); `point_preview` draws the lane's `points` block (the 4 m
polyline). They are different objects, and I measured how different, on the sample network:

```
lanes with a curve: 43     off by >0.5 m: 10     >1.0 m: 5     WORST: 22.57 m
  22.571 m  demo_ramp_F0      5.436 m  demo_spur_F0      5.167 m  demo_ramp_b_F0
```

Twenty-two metres. Gate green, geometry perfect, `Preview ▸ Traffic Flow` shows nothing wrong —
because it draws the polyline. Root cause is exact, see §1.1. **Not yet shipped**: every
`.lanekit.json` currently in `assets/world_source/` is v1 (no `curve` block), so `WorldBaker`
falls back to the polyline. It bites the first time the island's roads are re-exported from this
addon. That is precisely the class of defect `point_preview` exists to prevent, one level below
where it currently looks.

**(b) The road has five flat colours and no markings.** `point_build.MATERIALS` is
`asphalt / concrete / footway / median / barrier`, get-or-created as `rka_*`. The whole sample
network's material list is exactly those five. Meanwhile `blender/lib/kit_common.py:MATS` — the
material set every *other* builder in this repo uses — already defines `M_Asphalt`, `M_Concrete`,
`M_LineY` and `M_LineW` ("yellow lane line", "white lane line"). **Two material registries, and
the roads use the one that has no lane lines in it.** In Godot they arrive as embedded
`StandardMaterial3D` sub-resources named `rka_asphalt`, `rka_median`, … one copy per district.

**(c) The vocabulary for both asks is already built and unused.**
- `point_nodes.make_assets_group()` (GN_PointAssets — Collection Info → Instance on Points,
  per-point index) is fully written, documented, and **referenced by nothing**.
- `point_solve.CARRIER_ATTRS` declares `rka_sp_asset` ("asset row spacing") — read by nothing.
- `point_model.POINT_FIELDS` declares `median_style` (string) — read by nothing.
- `lane_profile.marking_runs()` computes **every painted boundary's type and per-station
  offsets**, with the correct presence rule ("something must exist on both sides, or it is the
  road's outer edge"), is self-tested by `point_profile`, and **is swept by nothing**.

The white strips are not a new subsystem. They are a solved problem that was never plugged in.

---

## 1. Part A — the Path3D preview

### 1.1 First, fix what it would be previewing

`point_export.curve_points()` places control points at the stations (right) and then hands them to
`_catmull_handles()`, which sets each handle to `±(P_{i+1} − P_{i−1}) / 6` — a refit **from the
chord through the neighbours**. But the road's centreline is not a catmull through the stations:
`road_points.resample()` builds a **Hermite per span** from `chain_tangents` (the station's
authored facing — the `R`-key bend gesture, `station_axis`) scaled by the authored `handle_out` /
`handle_in` lengths in metres.

So the export throws the authored shape away and refits. Two owners of one curve — the same defect
shape as every entry in `ROAD_POINT_GRAPH.md` §8f. Measured on `demo_ramp_F0`:

```
ctrl0  handle vs true tangent = 19.0°
ctrl4  handle vs true tangent = 70.3°     ← the open end
```

At an open end `_catmull_handles` uses `prev = ctrl[i]`, i.e. the chord back to the previous
station, and its docstring defends that as "extrapolating a phantom neighbour invents curvature
nobody authored". The premise is wrong *here*: the tangent at the end is not unknown — it is
authored, and `Sample.tan` already carries it. A 70° error times a 27.6 m handle is the 22.57 m.

**The fix is an exact conversion, not a better approximation.** A Hermite span `(p0, p1, m0, m1)`
IS the cubic bezier with `out = m0/3`, `in = −m1/3`. `point_solve` already has both endpoints'
sampled tangent and the span length. So:

- `curve_points(points, indices)` → `curve_points(samples, indices)`, taking each control point's
  handle from **that sample's own tangent** × (span/3), per side, instead of the neighbour chord.
- Lane centrelines are *offset* curves, so use the **offset polyline's** own sampled tangent at
  the station, not the road centreline's — they differ on any bend, which is the same reason
  `GN_PointSpine` stores one shared `rka_lat` instead of letting each layer re-derive a frame.
- Junction connectors already emit real handles from `ps.bezier_through` ("nothing is
  approximated on the way out") — leave them alone. Their 0.09 m reading is the *polyline* being
  coarse (`n=9`), not the curve being wrong, which is worth knowing when reading the preview.

**One owner:** after this, `point_solve` owns the road's curve and `point_export` converts it.
Nothing re-derives a tangent.

### 1.2 The preview itself

`point_preview` already has the whole apparatus — the export-document cache, the revision stamp
shared with the overlay, the chevrons, the successor hairlines, the `broken`/`unreached` defect
draws, and the agents. It draws `l["points"]`, in exactly two places (lines 195 and 268). The
feature is a **third draw source and a toggle**, not a new module:

| `Preview ▸ Geometry` | Draws | Answers |
|---|---|---|
| `Lane polyline` (today) | `l["points"]` | where the asphalt is |
| **`Path3D (export)`** *(new, default)* | `l["curve"]` evaluated as a cubic bezier | **where the cars will be** |
| **`Both`** *(new)* | polyline in its lane colour, Path3D over it in white, **the gap filled red** | where they disagree, and by how much |

- **Evaluate the curve the way Godot does.** `Curve3D` tessellates a cubic bezier per span from
  `p`, `out`, `in` — sample the same cubic in the preview. Do NOT re-run `curve_points`; read the
  document's own `curve` block, so the preview cannot drift from the file that ships.
- **Direction is already solved and should be reused, not re-invented**: the existing chevrons
  (`CHEVRON_EVERY = 22.0`), lane colouring (blue carriageway / amber connector / green ramp /
  violet merge) and the cars all key off `LaneGeo`, so pointing `LaneGeo` at the bezier samples
  gives direction, turn colour and moving agents on the Path3D for free. The cars then walk **the
  Path3D**, which is the honest simulation: today they walk a polyline no car will ever drive.
- **A new defect class, drawn and reported.** `deviation` = max distance from each Path3D sample
  to the lane polyline. Draw any lane over `PATH_DEVIATION_WARN` (0.5 m ≈ a third of a car) in
  **orange**, and list it in `Flow Report` as `path_off_road: demo_ramp_F0 — 22.57 m`. That line
  is the whole feature: it is the one thing no existing check could say.
- **Closed lanes**: honour `l["loop"]` (Godot sets `curve.setClosed`), or a ring road previews
  open while it ships closed.

### 1.3 Where it goes in the panel

`RKA_PT_preview` gains one enum row (`rka_preview_geometry`) above the existing Flow/Cars
toggles, and one line in the report box. **No new operator** — so nothing new for
`smoketest_point_coverage` to demand a button for, though its existing "every panel draws"
assertion covers the row.

### 1.4 The gate gets the same eye

A preview only helps the artist who looks. `point_validate` gains **`path_deviation`** (WARN at
0.5 m, ERROR at 2.0 m), computed from the same function the preview draws with — one owner, so
the panel and the gate cannot disagree, exactly as the taper number already works. With §1.1 done
this should be ~0 everywhere; it is the tripwire that keeps it there.

---

## 2. Part B — road style: materials and profile assets

### 2.1 The shape of it

Today each layer in `point_build.surface_spec()` / `edge_spec()` hardcodes
`Material=material("asphalt")`. The change is that **each layer names a style slot**, and a slot
resolves to *either* a material (paint the parametric band) *or* a profile asset (sweep the
artist's own cross-section instead).

```
                          slot           default              may be an asset?
  surface  Carriageway    surface        M_Asphalt            no  (width is per-point)
           Median         median         per median_style     yes (jersey barrier, planter)
           Deck           deck           M_Concrete           no
           Pillars        pillar         M_Concrete           yes (a real pier)
  edges    Gutter  (new)  gutter         M_Concrete           yes
           Kerb           kerb           M_Concrete           yes ← the common one
           Footway        footway        M_ConcreteTile       yes
           Barrier        barrier        M_Barrier            yes (jersey / parapet / guardrail)
  marks    Markings (new) mark_w/mark_y  M_LineW / M_LineY    no  (paint is flat by definition)
```

### 2.2 Where the choice is authored, and how it survives

**One line per slot in `point_model.ROAD_FIELDS`**, holding a **datablock name** (`'s'`, blank =
built-in default):

```python
("surface_mat", 's', ""),  ("median_mat", 's', ""),  ("kerb_mat", 's', ""),  …
("kerb_asset",  's', ""),  ("barrier_asset", 's', ""),  …
("style",       's', ""),   # optional: a named preset that supplies every blank slot
```

Names, not pointers, because **rule 3 — the record is the file**: `<stem>.roads.json` is the
source of truth and must round-trip. The field table already drives defaults, JSON and the bpy
PropertyGroup from one declaration, so each of these is genuinely one line and cannot drift.
Resolution is one new module, `point_style.resolve(road) -> Style`, called by Build and by
nothing else.

`median_style` — **already declared, already unused** — becomes the enum the user asked for and
is the one field that drives *geometry*, not just paint:

| `median_style` | Median layer builds |
|---|---|
| `NONE` | nothing (today's `median_width = 0` case) |
| `PAINT_DOUBLE_Y` | a flush band at `PAINT_Z_BIAS`, `M_LineY`, at the double-yellow width — no raised geometry |
| `RAISED` | today's band at `rka_med_z` (kerb height), `M_Concrete` |
| `WALL` | `RAISED` **plus** the barrier profile swept down the median line |
| `ASSET` | sweep `median_asset` along the median line at its own real dimensions |

Note `PAINT_DOUBLE_Y` is where the "double yellow" ask lands, and `lane_profile` already has the
`MARK_DOUBLE_Y` constant and the rule for when a median makes it redundant ("a median with real
width IS the separator" — `profile_from_scalars` deliberately leaves that boundary `MARK_NONE`).
Do not paint both; that rule is already written and correct.

**Per-point override** is deliberately *not* added. A style change along a road is a different
road ("two stations that differ" is for the cross-section, not for what it is made of), and a
per-point material would multiply the layer stack by the number of stations.

### 2.3 Profile assets — what I verified, and the limit

Probed in Blender 5.2 headless before designing around it:

| Probe | Result |
|---|---|
| Sweep a curve object as `Curve to Mesh`'s Profile Curve | ✅ works; **the asset's own real dimensions are preserved** (a 0.15 m kerb comes out 0.15 m) |
| Does the profile's material ride through? | ❌ **no** — evaluated mesh has zero material slots |
| Multi-spline profile with two materials + `material_index`? | ❌ **no** — 1 index, no `material_index` attribute, no slots |

So the design must say plainly: **`Curve to Mesh` drops the profile's materials.** Two
consequences, both deliberate:

- **The addon reads the material off the asset** (`asset.data.materials[0]`) and feeds it to the
  layer's existing `Material` socket. Swapping the asset therefore changes shape *and* material
  together, which is the property the ask was about — but it is the addon doing it, not Blender.
- **One asset = one material = one layer.** A single asset covering "gutter + kerb + footway + wall"
  in three colours is **not possible through this node**, and pretending otherwise is how the
  previous model's asset story went wrong (see `project_road_kit_asset_bugs`). The escape hatch, if
  it is ever wanted: group the profile's splines by `material_index` and sweep once per group —
  N layers from one asset. Costed, not built; the four separate slots cover the ask.

**A profile asset is a SWEPT SECTION, never a tiled instance.** This is the hard-won lesson from
round 3 of the previous addon: rigid 2 m pieces tiled around a 9 m corner sit ~12.7° apart and
open a real ~7.8 cm gap at every joint — an inherent limit of tiling rigid pieces on a curve, not
a phase bug. A swept profile is continuous on any curvature by construction. So:

- **kerb, gutter, footway, wall, median → profile assets** (`GN_PointProfile`, new — a thin
  variant of `make_band_group` with `Object Info → Profile Curve` in place of the line primitive,
  `Scale = 1`, i.e. the asset's real size wins over the configured width).
- **lamp posts, signs, fence posts, bollards → `make_assets_group`**, the tiling group that is
  already written and unused. That is what it is for, and discrete furniture is the one thing that
  *should* be discrete.

**The width read-back rule.** With a profile asset the asset's own measured width is the truth,
and the solve must consume it — a 3.0 m footway asset against a configured 3.5 m opens a real
lateral gap between kerb and footway. That exact bug is on the record (round 2, finding 2). So
`point_style.resolve` measures the asset (`bound_box`) and hands the number to
`point_solve.solve_road`, which already owns every lateral offset. **No second offset owner.**

### 2.4 Where the assets live

A `ROAD_KIT` collection of profile curve objects (`RKA_PROFILE_kerb_std`, `…_kerb_granite`,
`…_wall_jersey`, `…_gutter_v`), built by a new `tools/build_road_kit.py` in the existing
`build_curb_kit.py` idiom and **library-linked** from `assets/world_source/kit/road_kit.blend`
(`paths.KIT_BLEND` is already there). Linked, so editing that one file restyles every kerb in the
world — which is the "modify shape/materials more easily" ask in its strongest form.

Two traps already paid for, carried forward: the picker must **not** filter `library is None`
(kit pieces are deliberately linked — that filter once emptied the whole dropdown), and every
collection lookup in this pipeline stays local-only, since linked libraries carry same-named
collections.

The panel gets a `Road ▸ Style` box: one `prop_search` per slot against `bpy.data.materials` /
the `ROAD_KIT` collection, plus `median_style`. Two new operators, both with buttons (the coverage
smoketest demands it): **`Link Road Kit`** (link `road_kit.blend`'s collection into this file) and
**`Apply Style To Selected Roads`**.

### 2.5 The white strips

`lane_profile.marking_runs(profile_set, n)` already returns
`[{slot_id, mark, i0, i1, offsets}]` per painted boundary. Build sweeps them exactly the way it
already sweeps kerbs, which is the point — no new shape, no new lifetime:

```
point_solve.solve_road  → marks: [MarkRun(offsets[], mark, i0, i1)]
point_build.build_marks → one `<road>__marks_<slot>` polyline per run
                          (_polyline_object — already the shared "carrier polyline carrying
                          per-point attributes" helper, whose docstring literally names
                          "a marking line" as one of its three uses)
                          swept by the band group at z = PAINT_Z_BIAS
```

- **width** from a new `rka_mark_w` in `CARRIER_ATTRS` (0.15 m default), **material** from the
  mark type: `SOLID_W`/`DASH_W` → `M_LineW`, `SOLID_Y`/`DASH_Y`/`DOUBLE_Y` → `M_LineY`.
- **`DOUBLE_Y` is two runs** at ±(gap/2) off the boundary, not one wide band.
- **dashes**: a new `GN_PointDash` (Resample by length → delete alternate spans) so a dash is
  geometry, not a texture — this project has no image textures at all, deliberately.
- **markings need no `open_runs` treatment.** A kerb rides the *outline* and must open at every
  gore and mouth; a lane marking is an *internal* boundary of a carriageway that is continuous by
  construction, and `marking_runs`'s own presence rule ("something on both sides") already ends
  it where the lane ends. Do not reach for `point_edges` here.
- **Collision: none.** Markings are visual. They must be excluded from the `-colonly` proxy pass,
  or every district grows a paper-thin StaticBody per stripe.

### 2.6 The two material registries

> **Closed twice.** The merge below shipped in §7 (one registry, `kit_common.MATS`). On
> **2026-09-05** the registry itself moved out of Python: `kit_common.mat()` now **links** every
> `M_*` datablock from `assets/world_source/kit/road_kit.blend` — the same kit file that already
> held the profile sections — instead of get-or-creating a copy in whichever `.blend` is building.
> See §8 "Materials come from the asset kit".


Fold `point_build.MATERIALS` into `kit_common.MATS`/`get_mat` — the set the rest of the world
already shares — so a road's asphalt is the same datablock as a car park's, `M_LineW` finally has
its user, and `M_ConcreteTile` (the procedural world-position checker built precisely so a tiled
pavement survives a curved corner without UV pinch) becomes available to footways for free.
Keep `rka_*` names resolving to the `M_*` datablocks for one release so existing `.blend`s do not
lose their shading.

**Godot side.** Materials arrive as embedded `StandardMaterial3D` sub-resources carrying
`resource_name = "rka_asphalt"` — one copy per district, so retuning road shading today means
editing 36 baked scenes. Since the name survives the bake, the cheap fix is a `WorldBaker` lookup:
if `res://…/materials/<name>.tres` exists, assign that shared resource instead of the embedded
one. One `.tres` per material, edited once, applies to every district on next bake. Out of scope
for the Blender work; noted because it is what makes the style slots worth having in-game.

---

## 3. Part C — the junction handle: rotate and move about the intersection centre

**This is a bug, not only an ergonomics ask, and it measures 10.44 m.**

### 3.1 What is actually happening

`point_ops.make_junction` sets the `JCT_*` Empty's origin to the mouths' centroid **once, at
creation** (`jct.location = centre`, line 907) and *nothing ever re-derives it*. Meanwhile
`point_solve.JunctionSolve.centre` recomputes the mouths' centroid **live**, and that is what the
pad, the fillets, the turn paths and the export all use.

So there are **two owners of "where is this junction"**, and one of them is frozen at creation
time. Probed headlessly on the sample network — one ordinary authoring move, dragging a single
mouth 42 m to widen its approach:

```
jct origin  = (250.00, 0.00, 0.00)      ← what G and R pivot around
real centre = (260.00, 3.00, 0.00)      ← JunctionSolve.centre, what you see on screen
DRIFT       = 10.44 m
```

`Auto Setback` makes this worse by design — it "solves the whole clique and moves every unlocked
mouth", so the very first thing the recommended workflow does after `Make Intersection` is move
the centre out from under the handle. That is the report exactly: the pad rotates about *the
origin* rather than about the centre of the intersection.

**The built geometry is correct throughout** — the solve never reads the Empty's position. This is
purely the authoring handle lying about where the junction is, which is why it has gone unnoticed
by every check: nothing downstream is wrong, only the artist's grip.

### 3.2 Rotating it is refused outright

`make_junction` sets `jct.lock_rotation = (True, True, True)` and `lock_scale = (True, True,
True)`, with one justification for both: *"a stray R or S on the parent would rescale or spin
every mouth at once, and a mouth's width is its lane count — not something a transform may quietly
restate."*

That reason is exactly right for **scale** and does not transfer to **rotation**. A mouth's width
is its lane count; a mouth's *direction* is already a transform — the authored facing
(`station_axis`, the `R`-key gesture), which `ROAD_POINT_GRAPH.md` §8f made the single owner of an
arm's direction precisely so that rotating a mouth turns its cap, its fillets and its turn paths.
Turning the whole crossing is the same gesture one level up.

And the model already supports it. `facing_of` reads `matrix_world.col[1]` — **world space,
deliberately**, its docstring saying that is "what 'the direction this point faces' means once the
object is parented to a `JCT_*`". Forcing a 30° parent rotation in the probe:

```
rotating the parent 30 deg turns each arm facing by: [30.0, 30.0, 30.0, 30.0]
```

Four arms, exactly 30° each, so the pad, caps, fillets and turn paths would all follow **with no
new code**. The gesture is refused by a lock, not unsupported.

### 3.3 The fix

**Rule: the `JCT_*` Empty is a HANDLE, and a handle must sit where the thing it handles is.**
`JunctionSolve.centre` stays the one owner; the Empty *follows* it. Nothing derived may ever read
the Empty's position — that stays true, and is what keeps this change unable to move any geometry.

**(a) Re-centre the handle onto the live centroid.** New `point_ops.recentre_junction(jct)`. The
subtlety is that moving a parent moves its children, so each child's world transform is captured
before and restored after — the same dance `make_junction` already performs when it parents, and
with the same trap called out in its own comment (*"`matrix_world` is STALE until the depsgraph
updates"*):

```python
kids   = [c for c in jct.children if is_point(c)]
worlds = [c.matrix_world.copy() for c in kids]        # BEFORE
jct.matrix_world.translation = centroid(worlds)
context.view_layer.update()                            # the parent matrix is stale until this
for c, w in zip(kids, worlds):
    c.matrix_parent_inverse = jct.matrix_world.inverted()
    c.matrix_world = w                                 # not one mouth has moved
```

Run it at three existing seams, never per drag frame: at the end of `make_junction` (a no-op
there, and that is the point — the same function guarantees the invariant), at the end of
`Auto Setback` (the largest single source of drift), and in **`point_live`'s settle pass** plus
`Build` for hand drags. Settle-time, because moving a parent mid-modal would fight the running
transform operator — `point_live` already draws exactly this line for geometry.

Plus one explicit **`Junction ▸ Recentre Handle`** button, because every existing `.blend` already
carries drifted handles and they need a repair gesture (and because `smoketest_point_coverage`
requires every operator to be reachable from a button).

**(b) Unlock Z rotation only.** `lock_rotation = (True, True, False)`, `lock_scale` unchanged:

| Axis | Locked? | Because |
|---|---|---|
| scale | **yes**, all three | a mouth's width is its lane count — the original reason, unchanged |
| rotation X / Y | **yes** | the pad is solved in plan with a Z-up frame; tilting it out of plane has no meaning |
| **rotation Z** | **no** | turning the crossing — supported by the model already, per the probe |

**(c) The one real side effect, and the right answer to it.** In the probe, rotating the parent
promoted **4 of 4 arms from AUTO to MANUAL**: `was_rotated` compares the arm's world facing
against the baseline `stamp_baseline` wrote, and that baseline is stored **in world space**, so a
parent rotation moves the facing out from under its own reference. Left alone, turning a junction
would silently bake four hand-authored facings that `Follow Road (Auto)` can then never
re-straighten — and rotating back would not undo it.

**Store the baseline in the same frame as the facing it is compared against** — the point's parent
space. Then a parent rotation carries facing *and* baseline together and promotes nothing, with no
delta-tracking anywhere. For an unparented point (every point that is not a junction mouth) local
*is* world, so this is a byte-identical no-op for the whole rest of the network, which is what
makes it safe. It also keeps the §8i rule intact for the reason that rule exists: **a parent
rotation is the pad's frame changing, not the artist turning that one arm** — the same distinction
already drawn between a rotation (authored) and a drag (not).

The fallback, if parent-space baselines turn out to disturb something: re-stamp each AUTO arm's
baseline through the parent's rotation delta on settle. Same outcome, more moving parts, one more
thing to keep in sync — prefer the frame fix.

**(d) One thing no code can fix, so say it in the panel.** Blender rotates about the scene's
**Transform Pivot Point**. With the default *Median Point* and the `JCT_*` selected alone, the
median is its origin, so (a) makes it exactly right. If the artist has the pivot set to *3D
Cursor*, it will rotate about the cursor no matter what we do — one `icon='INFO'` line under the
Junction panel's rotation controls.

### 3.4 While we are here

The authored parent is named `JCT_%04d` and the **generated pad** is named `JCT_<uid[:8]>` — two
different schemes both matching `startswith("JCT_")`, which `point_panel.active_junction` and
several smoketests key on. It works today because they live in different collections
(`ROAD_MANAGER/JUNCTIONS` vs `ROAD_MANAGER_GEN/JUNCTIONS`), but it is one careless `bpy.data`
scan away from confusing the two. Worth a distinct prefix for the generated one (`PAD_`) if this
part is touched.

---

## 4. What this does NOT change

- **No `.lanekit.json` format change** — §1.1 changes the *values* in the existing `curve` block.
  `WorldBaker`, `PathLaneRoute`, `LaneGraph`, `VehicleRoute` are all untouched.
- **No new object lifetime.** Marks and profile sweeps land in `ROAD_MANAGER_GEN` under the
  existing per-road group; rule 2 (authored and generated never share a collection) holds.
- **No second lateral-offset owner.** Every new number comes from `lane_profile.slot_offset` by
  way of `point_solve`, including the asset-measured widths.
- **`build_piece.sh` is unchanged.** More geometry and more materials ride the same glTF.
- **No geometry moves in Part C.** The solve never reads the `JCT_*` Empty's position, so
  re-centring the handle and unlocking its Z rotation cannot shift a pad, a mouth or a lane. The
  first junction rotation an artist performs will, of course — that is the feature.

---

## 5. Verification plan

Added to `check_roads.sh` alongside the existing 17:

| Check | Asserts |
|---|---|
| `point_export.self_test` (extended) | on a bent, unevenly-spaced ramp, **max Path3D-vs-polyline deviation < 0.05 m** — the 22.57 m regression test, pure Python, no Blender |
| `point_style.self_test` | slot resolution: blank → default, name → datablock, missing name → default **+ a gate warning** (never a silent black road) |
| `smoketest_point_build` (extended) | a marking run exists where `marking_runs` says one should, at the right offset, with the right material, and **not** in the `-colonly` proxy; a profile-asset kerb sweeps at the asset's own measured height |
| `smoketest_point_coverage` | the two new operators have buttons; the Preview enum draws |
| `smoketest_point_ops` (extended) | after `Make Intersection` **and** after `Auto Setback` **and** after a hand mouth-drag + settle, `JCT_*.matrix_world.translation` equals `JunctionSolve.centre` to 1e-4; **not one mouth's world transform changed** across the recentre |
| `smoketest_point_coverage` (extended) | rotating a `JCT_*` 30° about Z turns all N arm facings by 30° and promotes **zero** arms to MANUAL; rotating one arm by hand still promotes exactly that one; `lock_scale` still `(True, True, True)`; `Recentre Handle` has a button |
| end-to-end | `build_piece.sh` on the sample network: `pathlanes=43` unchanged, material count rises from 5, no new `StaticBody3D` from markings |

---

## 6. Sequencing

Each step is shippable on its own and green before the next starts.

1. **§1.1 the export fix** — smallest, highest value, fixes a 22 m defect before any content is
   re-exported with it. Regression test first.
2. **§1.2–1.4 the Path3D preview + `path_deviation`** — the eye that keeps step 1 true.
3. **§3 the junction handle** — independent of everything else, and the smallest fix in the
   document: one new function, one `lock_rotation` tuple, one baseline frame. Do it whenever it
   is convenient; it blocks nothing and nothing blocks it.
4. **§2.6 material registry merge + §2.1/2.2 the style slots** (materials only, no assets) —
   the carriageway/median/kerb/footway/wall colours become authorable. `median_style` lands here.
5. **§2.5 markings** — the white strips; needs step 4's `M_LineW`/`M_LineY`.
6. **§2.3/2.4 profile assets + `road_kit.blend`** — the largest, and the only one that needs new
   art. The `GN_PointProfile` group and the width read-back are the whole of it.
7. *(deferred)* discrete furniture via the already-written `make_assets_group`; the Godot shared
   `.tres` material library; multi-material profiles by spline group.

---

## 7. What was built (2026-08-27)

Every step is in, gate green at each one. Where reality differed from the design above, it is
because something was measured rather than assumed — those are the entries worth reading.

### The numbers

| | before | after |
|---|---|---|
| worst exported Path3D error, sample network | **22.57 m** | **0.093 m** |
| lanes over 1 m off | 5 | 0 |
| control points, through lanes | 328 (naive subdivision) | 235 |
| road materials | 5 `rka_*`, a second registry | 7 `M_*`, the world's one registry |
| lane markings | none | 6 objects, `M_LineW` / `M_LineY`, out of collision |
| junction handle drift after one mouth drag | **10.44 m** | 0.0000 m |
| `check_roads.sh` | 17 | **18** |

### Where it differed from the design

- **§1.1 needed more than the Hermite→bezier conversion.** Exact conversion is impossible: a lane
  centreline is an *offset* curve, and the offset of a Hermite is not a Hermite. So the handle
  DIRECTION comes from the lane's own one-sided sampled tangent, the LENGTH from a closed-form
  circular-arc fit refined by 2-unknown least squares (Schneider's form, tangents fixed), and the
  span subdivides only where a cubic still cannot follow to `CURVE_FIT_TOL` (0.05 m). Three
  measurements drove that: the arc form alone left 2.89 m on a ramp; **the fit error had to be
  measured curve-to-lane, not lane-to-curve** (a cubic that bulges wide and comes back passes near
  every sample while sitting a lane's width off the road — the two disagreed by 1.4 m); and the
  split must **bisect**, because splitting at the worst sample shaves one-sample slivers off a span
  whose real problem is a mis-set handle (21 of 36 control points went into three such runs while
  two 90 m spans were never split at all).
- **The `BOTH` preview's rungs had to be measured geometrically, not by index.** Pairing sample
  *i* of the polyline with sample *i* of the curve measures parameterisation mismatch: 926 rungs
  drew on a network whose worst real gap was 0.0988 m. Each rung's foot is now taken at a fraction
  of the path's own arclength and dropped onto the nearest point of the lane.
- **§3's parent-frame baseline worked exactly as designed** — rotating a junction 30° turns all
  four arms by 30.0° and promotes **0 of 4** to MANUAL (it was 4 of 4), while a hand rotation of
  one arm still promotes exactly that one.
- **Profile assets needed three things the design did not name**, each found by measuring:
  1. **A section is drawn outward-and-up and swept inward-and-down** (profile +X → world −Y,
     +Y → −Z). `GN_PointProfile` applies the π rotation the module has documented in
     `_PROFILE_FIX` since the beginning and never used — plus a **mirror + flip-faces** on the
     right-hand flank, since both `__edges` carriers run the same way along the road and an
     asymmetric kerb would otherwise face inward on one side.
  2. **`obj.bound_box` is a lie on a freshly linked object.** It reported a 0.32 × 0.17 m kerb as
     2.32 × 2.17 m in a background session. Every asset dimension is now read off the curve data.
  3. **An asset layer had no gate.** `layer_has_content` keys on `WidthAttr`/`ThicknessAttr`, and
     an asset layer has neither — so a named barrier built along an at-grade pedestrian street
     whose parametric wall was correctly zero. `ASSET_REQUIRE` supplies the same attribute the
     parametric layer was gated on, and `ASSET_Z_ATTR` anchors each section by its FOOT (a jersey
     barrier hung from `rka_wall_z`, the wall's *top*, floated 0.96 m into the air).
- **Dashes are laid on ONE CLOCK PER RUN** (2026-08-28, user-reported). Walking each boundary from
  its own first point restarts the grid wherever that boundary opens, so an auxiliary lane opening
  4.00 m into a run came out 4.00 m off a 9.00 m period — half a period, which reads as that lane's
  paint being wrong rather than merely offset. The grid is now cut in the **centreline arclength**
  domain (`Sample.s`), shared by every boundary of the run, which also keeps them square through a
  bend where the outer line's own arclength runs ahead of the inner one's. Regression-tested in
  `point_solve.self_test` (every boundary's dash grid must be a subset of the widest, never an
  offset copy), so it runs in `--quick`.
- **Dashes are cut in Python, not Geometry Nodes.** A GN dash means resample-and-delete-alternate-
  spans, a node graph guessing at three lines of arithmetic. "Python owns the curve, GN only
  sweeps" is the rule this addon is built on, and a dash is a shorter curve.
- **`median_style` moved from the point to the ROAD.** It was declared in `POINT_FIELDS` (unused)
  from the start, but it decides which geometry the divide builds and the divide's material is one
  material for the whole run. `median_width` still varies per station.

### New surface

`point_style.py` (the slot resolver), `tools/build_road_kit.py` → `assets/world_source/kit/
road_kit.blend` (six profile sections), `GN_PointProfile`, two operators (`rka.recentre_junctions`,
`rka.link_road_kit`), two gate checks (`path_deviation`, `style_missing`), one preview mode enum,
and one `Road ▸ Style` panel box. Twelve `ROAD_FIELDS` entries, one `POINT_FIELDS` removal, two
`CARRIER_ATTRS` (`rka_mark_w`, `rka_wall_foot`), two `kit_common.MATS` keys.

### The one gap a pilot needs to know about (2026-08-28)

**A profile asset's own width does not reach the solve.** The section is swept at its authored
size — that half works, and is tested — but `point_solve` still places every layer outboard of it
from the AUTHORED widths, because the solve is not style-aware. Measured on the shipped kit:

| named | its own width | the road reserves | result |
|---|---|---|---|
| `RKA_PROFILE_footway_slab` | 2.00 m | 3.00 m (`left_walk_width`) | **1.00 m of slack** between the pavement edge and the barrier |
| `RKA_PROFILE_kerb_granite` | 0.32 m | 0.08 m (`kerb_height × KERB_THICKNESS`) | **0.24 m of overhang** into where the footway starts |

This is round 2 of the previous addon's asset work, one level up — and it is worth noticing that
nothing about the built geometry *looks* broken: every piece is the right shape, correctly swept,
in the right place for what it was told. The only symptom is a gap found by flying up to it.

`point_validate.check_asset_width` now reports the **footway** case by name and in metres, with a
remedy (author the width to match the section). The **kerb** case is deliberately not warned about:
its reserved width is derived from `kerb_height`, so no value of any authored field makes a 0.32 m
section fit — "set kerb_height to 0.32" would be advice for a 64 cm kerb, and a finding whose
remedy makes things worse is worse than no finding.

**The structural fix** is to thread the resolved `Style` into `solve_road` so an asset's measured
width replaces the authored one at the single place lateral offsets are computed — never as a
second offset owner (rule 1). That is a signature change through `solve_road` and its callers, not
a large job, but it is a real one and it is not done.

**What this means for sequencing:** a district piloted with **materials only** (every `*_asset`
slot blank) has no exposure to this at all, and picks up any later change to the defaults for
free, because a blank slot means "the layer's default" and is resolved at build time. Assets are
the part to wire before leaning on them.

### What the district pilot found before a single road was authored (2026-08-28)

Probing `Piece_3_1` — a real grid district: DEM terrain, ~1000 buildings, one previously baked road
mesh — turned up two defects in opposite directions, neither of which the synthetic sample network
could ever have shown, because it has no buildings and no terrain.

**1. The sampler took whatever the ray hit first.** 300 downward rays over the district:

| hit | casts | z |
|---|---|---|
| terrain | 134 | −1.0 … 1.0 |
| **buildings** | **96** | 0.9 … **66.2 m** |
| **the district's old baked road** | **70** | −0.9 … 0.9 |

So roughly a third of a road's stations would have sampled their `ground_z` off a **rooftop**, and
every support — embankment, pier, trench — is derived from exactly that number.

**2. The ground cut found no terrain at all.** `terrain_objects` scanned a fixed list of collection
names (`TERRAIN`/`GROUND`/`MANUAL`); a district baked by this pipeline puts its ground in `STREET`,
named `District_<theme>_<gx>_<gy>_Terrain-col`. So the cut silently did nothing on every district
the world is actually made of, while passing every test on the sample.

The two halves were **asking different questions about the same thing** — the §8f shape again.
`point_build.is_terrain` is now the one owner, used by both; and the sampler **punches through**
non-terrain hits rather than taking the first, so a road under a 40 m building still finds the
ground. Regression-tested on a synthetic scene with a building standing on terrain (assert the
sampler returns 0, not 40) and with the off-the-edge case (assert `None`, not an invented height).

**This is the argument for the pilot in one paragraph:** two silent, load-bearing defects, found by
one probe against real content, before any authoring. Neither was a code-reading matter — both
required measuring what the ray actually hit.

### The pilot itself — District_city_3_2, end to end (2026-08-28)

The pilot target moved from `3_1` to **`3_2`** on evidence: island v3's road network puts **0 m**
through `District_city_3_1` (it sits at y = −756, south of the island's southernmost road), and
1079 m through `3_2`. Worth recording that the two worlds only partly line up — the registered
pieces are the archived 6×6 grid, island v3 is the current 4×4 plan, and eight of its arterial
crossings land in eight different districts, none of them `3_2`. So `3_2` gets two of the plan's
arterials running through it that never meet, which is what a **trunk** plan is: it says where the
arterials go and nothing about the blocks between them.

`blender/tools/seed_district_roads.py` is the bridge that was missing. `island_v3_to_roadkit.py`
did this job for the previous road model and is dead code now (it drives
`rka.build_segment_from_curve` and `rka.build_intersection`, both deleted in the point/port
rewrite); the *plan* survived that rewrite untouched. The seeder clips `island_v3_geom.ARTERIALS`
to a district's own square, resamples the coarse plan polylines to stations, and authors each run
**through the operators an artist presses** (`rka.new_road` / `rka.extend_road`) — `Add Sample
Network`'s own rule, and for its own reason. `--links N` adds the local streets the trunk plan
does not describe, each of which crosses every arterial it reaches.

    Piece_3_2 → 5 roads, 4 four-way intersections, gate GREEN
             → 106 generated objects, 4 pads, 13 marking objects, 34 collision proxies
             → 89 lanes, 4 junctions → 89 Path3D in Godot, 447 collision bodies,
                navmesh 11,789 vertices, worst path deviation 0.06 m

Two defects fell out of the run, both caught by the gate rather than by looking:

- **The seeder spliced its second crossing into the wrong segment.** Crossing indices are computed
  against the original chain, and inserting a pair of mouths shifts every index after it — so a
  road crossed twice put all four mouths of its second pad on one line. `pad_degenerate`, "the
  mouths are coincident or collinear". Insertions now run from the far end backwards. The gate
  catching a *seeder* bug rather than an authoring one is the system working.
- **A turn connector's polyline was too coarse to represent its own curve.** `bezier_through`
  sampled a 90° turn at n=9, so the `points` array sat 0.22 m inside the exact `curve` — and since
  `path_deviation` measures curve against polyline, that read as the worst "path off road" number
  on the whole district while nothing was wrong. `CONNECTOR_SAMPLES = 18` → 0.06 m.

### Roads first or ground first? (2026-08-28) — the cut decides it, and it had a leak

The question is whether authoring order matters, and it turns on one property: **the ground cut is
non-destructive.** (Still true, and more so: since 2026-09-06 there is no cut at all — the ground
MESH is re-emitted from the height field with the roads as a ceiling on it, so authoring order is
free for a stronger reason than the boolean gave. `ROAD_POINT_GRAPH.md` §8v.) `cut_ground` never edits the terrain mesh — it hangs a `BOOLEAN` modifier per
road band off it. So ground detail authored *before* the roads survives them, and a road moved
later re-cuts from the same untouched ground. **Order is free.**

Except it was not, because nothing undid the previous cut. The cutter objects were created in the
**scene root**, where `clear_all` (which only ever clears `ROAD_MANAGER_GEN`, rule 2) could not
see them, and the modifiers were never removed. Measured on the pilot district:

    build 1: 17 booleans on the terrain      build 2: 34      build 3: 51

Unbounded. Twenty iterations on a district's roads would leave 340 mesh booleans stacked on a
28,000-vertex terrain — the artist experiences that as Blender becoming unusable, with nothing on
screen to explain it, and would reasonably conclude they should not iterate on roads over finished
ground. The wrong lesson, from a leak.

Cutters now land in `ROAD_MANAGER_GEN/CUTTERS` and `clear_cuts` removes the last build's modifiers
first: **17, 17, 17**, terrain mesh untouched at 28,176 verts. Regression-tested
(`smoketest_point_build`: stable across 3 rebuilds, no dangling boolean, and an explicit assert
that the terrain mesh was not edited).

**So the recommended order is roads-then-ground-detail**, not because ground-first breaks, but
because the road is what *asks questions of* the ground: `delta = surface_z − ground_z` chooses
embankment, pier or trench per station, and the gate reports where the answer is unreasonable. Lay
the network first and the terrain edits you then make are answering real questions ("this pier is
14 m tall — do I want a viaduct here, or should the ground rise?") instead of guessing. The cut
being reversible is what makes that safe to iterate.

### Is island v3's ground something roads can be laid on? No — 2 of 7 (2026-08-28)

Asked before committing to rebuilding the world from island v3 up, and answered by a new
repeatable check, `blender/tools/check_island_ground.py`:

```
road            length   zmin   zmax   gap   step  verdict
Chuo-dori         1409   -0.0  120.0     2  120.0  NO GROUND x2, e.g. (48, 410) -- bridge it or move it
Rinkai-dori       1648   -0.0    0.0     0    0.0  ok
Yamate-dori       1615   -0.0  140.0     0   80.0  WALL 80 m (800% grade) at (-390, 241)
Nogyo-michi       1390   -0.0  120.0     0  120.0  WALL 120 m (1200% grade) at (50, 620)
Hama-dori         1428   -0.0    2.0     0    2.0  WALL 2 m (20% grade) at (-13, -509)
Nishi-dori         826   -0.0  140.0     0   80.0  WALL 80 m (800% grade) at (-634, 10)
Port road          368    2.0    2.0     0    0.0  ok
```

Three distinct classes, none of them visible in top view — which is why nothing had caught them:

1. **Four arterials cross a vertical wall.** The island's relief is `MASSIF` and `SPUR` in
   `island_v3_geom` — nested ellipse contour rings (+120/+240/+320/+380 and +80/+140) emitted as
   **prisms**: flat-topped plateaus with sheer sides. The arterials clip their footprints, so the
   ground under them jumps 80–120 m between adjacent samples. `point_solve` derives every support
   from `delta = surface_z − ground_z`, so building there yields piers and trenches from a 1200%
   grade.
2. **Chuo-dori crosses open water with no bridge.** At (48, 410) the ray goes road → rail → `Sea`:
   the river notches the land polygon and nothing spans it. Verified against a 64-deep probe, so
   it is a real hole and not the ray giving up through stacked content.
3. **A 2 m land-edge step** where Hama-dori meets the harbour platform — small, real, and the kind
   of thing that becomes a visible ledge in game.

**The terrain is a blockout, and says so** — `build_island_v3.py`'s own docstring calls the file
"a traceable, true-scale plan you author road_kit_authoring pieces ON TOP OF. It is not the [final
geometry]". Nothing is wrong with the plan; what is missing is the step that turns contour rings
into a continuous surface. That is a well-defined job: nested closed rings with heights ARE a
contour map, so a heightfield interpolated between them gives one ground that both the roads
sample and the eye sees.

**This is the argument for doing ground before detail, and for doing it once:** every road on that
terrain is currently unbuildable, and no amount of district-level detailing changes that.

### Not built

The Godot-side shared-material library (§2.6's last paragraph): materials still bake as embedded
`StandardMaterial3D` sub-resources, one copy per district, carrying `resource_name = "M_Asphalt"`
and so on. The name survives the bake, so the `WorldBaker` lookup that swaps in a shared `.tres`
remains a small, self-contained follow-up. Discrete furniture through `make_assets_group` and
multi-material profiles are still deferred, as designed.

---

## 8. Materials come from the asset kit (2026-09-05)

**A MATERIAL IS AN ASSET, NOT A CONSTANT IN A BUILDER.** §2.6 merged two Python registries into
one; it left the *look of the world* defined in a Python table, get-or-**created** in whichever
`.blend` happened to be building. Two consequences, and the second one is the one that shows:

- an artist who wants a different asphalt has to edit `blender/lib/kit_common.py`, which is not
  where an artist works;
- it was not even one datablock. `road_kit.blend`'s profile sections carry their **own**
  `M_Concrete` — they were built by this same function, in that file — so a district that links a
  kerb section gets the kit's `M_Concrete` on the kerb and a locally-created `M_Concrete` on the
  deck beside it. Identical, indistinguishable, and two materials in the exported scene. Two owners
  of one fact, again.

**The kit file is the material library.** `blender/tools/build_road_kit.py` now writes every
`kit_common.MATS` / `TILED_MATS` entry into `assets/world_source/kit/road_kit.blend` with a
**fake user** — which is the whole mechanism: a `.blend` drops any zero-user datablock on save, and
the kit holds six cross-sections, not a scene, so without the flag it shipped exactly the handful
of materials its sections happened to carry (measured: 3 of 25).

**`kit_common.mat()` is still the one resolver, and it grew one lookup, not a second registry:**

1. a datablock of that name already in the file wins — a hand-edited local material, or the library
   material a previous call already linked. (`bpy.data.materials.get` prefers a LOCAL datablock
   over a linked one of the same name — measured, so a local override is unambiguous);
2. otherwise **link** it from the kit;
3. otherwise build it from the table. That is the BOOTSTRAP: it is how `build_road_kit.py` authors
   the library in the first place (it sets `kc.USE_MATERIAL_LIBRARY = False`, so the tool that
   writes the library never reads it), and it is what keeps every builder working in a checkout
   where the kit has not been built.

Linked and not appended, for the same reason the profile sections are: an appended copy drifts.
A linked material **is** writable from Python in a background process (measured), so
`export_world.py`'s base-colour flattening is unaffected.

**Measured on `Island_base`, before and after:** the same 11 materials with the same user counts
(asphalt 64, concrete 88, concrete-tile 79, median 44, line-w 22, barrier 6, …), every one of them
now `lib=//../kit/road_kit.blend` instead of local — so the look is byte-for-byte the decision it
was, sourced from a file an artist can open. The baked `.tscn` carries all 11 with an
`albedo_color`, including the procedural `M_ConcreteTile` (the flattening at the seam still runs)
and `M_Water`'s alpha. `check_roads.sh` PASS=18.

**What did NOT change, on purpose.** `point_build.material()` / `MATERIAL_KEYS` and
`point_style.resolve` are untouched: the addon had exactly one default-material lookup and it still
has one. The change belongs in `kit_common`, which is where "what is a material" is answered for
the whole repo — putting a second resolver in the addon would have been the same defect this fixes.

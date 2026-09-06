# road_kit_authoring — the point/port road graph

Design of record: **`blender/ROAD_POINT_GRAPH.md`**. This file is the map.

## The model in four lines

| Concept | Is | Blender |
|---|---|---|
| **Road** | an ordered corridor — a chain of points | a Collection under `ROAD_MANAGER` |
| **Point** | a **station** *and* a **port**: position, cross-section, connections | an Empty (`obj.rka_pt`) |
| **Link** | an authored connection, typed `SEGMENT` / `JUNCTION` / `AUX` | an entry in `point.rka_pt.links` |
| **Junction** | a clique over `JUNCTION` links; the members **are** the stop lines | derived; owns a `JCT_*` parent |

Every along-the-length change — lane drop, lane opening, one-way, an acceleration lane with its
taper and buffer — is just *"two stations that differ"*. That is what deletes the ~900 lines of
special-case inference the previous (mesh-graph) model needed for the same roads.

## Modules

| Module | Owns | bpy? | Self-test |
|---|---|---|---|
| `point_model` | the schema + the git-diffable `.roads.json`; the Empties are a **view** of it | optional | `python3` |
| `point_profile` | station → `lane_profile.Profile`; the slot ids `F0.. R0.. AF0.. MED` | no | `python3` |
| `point_solve` | chain → carrier numbers; clique → pad, fillets, turn paths; `Auto Setback` | no | `python3` |
| `point_edges` | the road **edge**: where kerbs open, from the paved footprints | no | `python3` |
| `point_validate` | **the gate** — a build that fails it is a failed build | no | `python3` |
| `point_style` | what a road is **made of**: a material or a swept profile asset, per layer | optional | `python3` |
| `point_export` | `.lanekit.json` **v2** — bezier handles FITTED to the lane, `junctions[]`, `spawnable` | no | `python3` |
| `point_nodes` | the GN vocabulary: spine / band / deck / **profile** / pillars / assets / finish | yes | — |
| `point_build` | carrier + stack + pads + collision + the corridors the terrain carve reads; `ROAD_MANAGER_GEN` lifetime | yes | smoketest |
| `point_ops` | the authoring gestures (§4.1) | yes | smoketest |
| `point_panel` | the point **inspector** + the **Connections** list (deliberately not a stamping brush) | yes | smoketest |
| `point_overlay` | the GPU overlay — what makes hundreds of points legible, and what follows a drag | yes | smoketest |
| `point_preview` | the **traffic-flow preview** — the exported **Path3D**, its defects, and cars walking it | yes | smoketest |
| `point_live` | depsgraph dirty set + debounced rebuild; geometry on **settle** only | yes | smoketest |

## Verify

```bash
blender/tools/check_roads.sh          # 18 checks — self-tests, the lanekit gate, the smoketests
blender/tools/check_roads.sh --quick  # pure-Python only, no Blender, ~2 s
```

`smoketest_point_coverage.py` is the whole-plugin one: it drives **every registered operator** and
**executes every panel's `draw()`**, and it fails if you add an operator or a sidebar button without
a test behind it.

## Building your first road (View3D ▸ N-panel ▸ **Road Kit**)

**Fastest:** `Author ▸ Learn ▸ Add Sample Network`, then `Build ▸ Build Roads`. Everything below
is what it did, by hand — and it really did it by hand: **the sample is authored by pressing the
same operators you press**, so the button that teaches the gestures is also the button that
exercises them. You get

- two streets crossing (a pad whose mouths are the stop lines, and whose corners grow pavement);
- an arterial grown from **both ends** — the last extension prepends at `_p000`;
- an elevated 3+3 expressway on piers with a **weaving section**: an auxiliary lane on *each*
  carriageway opening over the same span;
- **a half-interchange**: `demo_hwy_p002` and `demo_main_p007` each accept **a ramp out and a ramp
  in**. One ramp carries eastbound traffic off the expressway and onto the arterial; the other
  carries westbound traffic back, and is **two lanes wide**. They run on opposite carriageways,
  which is what makes it a straight run rather than a loop — and nothing declares any of it: which
  carriageway each is on is read off which side its mouth sits, and exit-versus-entrance off the
  ramp's own chain;
- a one-lane **spur** branched from the middle of the arterial with `Branch Ramp Here`;
- and therefore five gores of two kinds: fenced-to-fenced and fenced-ramp-to-kerbed-street (where
  the nose carries the ramp's wall and the street's footway runs on past it), plus the spur's,
  which is walkable at both flanks and so is the one gore whose proxy is **not** `-noped`.

It is gate-green and its exported lane graph has no broken or unreachable lanes — check with
`Preview ▸ Flow Report`. Press it again to start over; `Replace` clears the previous one.

Not in it yet: a **ring road** (`is_loop` on the Road panel). A loop is a chain whose last point is
`Connect`ed back to its first, so the sample is a `Connect` away from carrying one.

Seven panels: **Author** (the gestures), **Road Point** (the active station), **Connections**
(what it is joined to), **Junction**, **Road** (the corridor's base profile), **Preview** (the
exported traffic graph), **Build**.

### 1. A straight road

1. `Author ▸ Corridor ▸ New Road` — creates a `ROAD_MANAGER/road_new` collection and its first
   point. Open the redo panel (bottom-left) to set the name, lane counts and width up front.
2. With that point active, press `Extend Road` repeatedly. Each press adds a point along the chain
   tangent and links it `SEGMENT`. Drag points with **G** like any Empty; the overlay follows.
   It works from **either end**: from the last point it appends, from `_p000` it prepends (and
   renumbers, so the name order still matches the road). An interior point is refused by name —
   "extend" has no meaning in the middle of a chain.
3. `Build ▸ Build Roads`.

The chain order **is** the object-name order (`road_new_p000`, `_p001`, …), and **FWD is increasing
index** — that is what fixes which side is left.

`Extend Road` works from **either end** of a chain and refuses an interior point, naming the two
gestures that do have a meaning there: `Insert Point` to add a station *between* two, and
`Branch Ramp Here` to start a new road leaving this one.

If a stretch ends up in the wrong road — the usual way being a ramp grown with `Extend Road` off
its mainline instead of `New Road`, because it is the gesture already under your hand — press
`Author ▸ Repair ▸ Tidy Roads`. **It needs no selection**: the connections already say where every
point belongs. It moves a point whose `SEGMENT` links all land in one other road into that road
(next to the neighbour it joins, not at the end), and splits a collection holding more than one
corridor into one collection each — naming a corridor something `AUX`-links into `<road>_ramp`.
Links are object pointers, so nothing has to be rewired and the `AUX` link from the mainline
survives the move.

`Author ▸ Corridor ▸ Split To New Road` is the same move done by hand, when you want to choose the
points and the name yourself. Until you run either, the gate says so as a **warning**
(`chain_unlinked`) and the build treats the two stretches as separate runs, which is correct — it
is untidy, not broken.

### 2. Change the cross-section along it

Select a point and edit **Road Point ▸ Cross-section**. A lane count that differs from its
neighbour *is* the taper — there is no taper length to set, because **the taper length is the
distance you put between the two points**. If it is too short for the road's `design_speed` the
gate says so and the link draws **red** in the viewport.

The length it asks for is the real merge-taper standard (`L = W·S²/155` up to 70 km/h, `0.6·W·S`
above it) — 3.5 m at 80 km/h wants 168 m. **The world is not 1:1**, so if that eats a district,
lower **`taper_factor`** on the **Road** panel rather than fighting the gate: it scales the demand,
`1.0` is the book, and it is a visible authored decision on the road it applies to. The finding
names **the factor that would pass**, so there is no number to guess at.

It is measured **per carriageway** and **only within a run**, which is the answer to "why did
adding an aux lane on one side get more expensive when the other side already had one": a merge is
one driver moving sideways on one side of the divide, so the demand is the wider of the two sides'
changes, never their sum. And a lane count that differs across a **junction** costs nothing — the
pad joins those two mouths, not carriageway.

Two things are exempt outright: a lane **departing onto a ramp** (nobody merges at a gore), and a
width change at the station that owns the `AUX` link.

- `lanes_fwd` / `lanes_bwd` — `0` on one side gives a one-way street.
- `drop_side` — *which* lane a decrease removes (kerb-side by default; `MEDIAN` for an offside exit).
- `aux_fwd` — an auxiliary lane, always outboard (`aux_side = MEDIAN` for an offside exit).
  `0 → 1 → 1 → 0` across four points is an acceleration lane, its taper, its buffer run and
  its close. It **counts as a lane**: `lanes_fwd = 3, aux_fwd = 1` is a four-lane carriageway,
  and both the panel and the overlay (`3+1|3`) say so.

Editing many points at once: select them, make the one you want to copy **from** active, then
`Author ▸ Cross-section brush ▸ Apply Cross-Section` and tick **only** the group you mean.

### 2a. One station too many, or one too few

Two gestures, both in **Author ▸ Corridor**, and neither changes the road's shape on its own:

**`Insert Point`** adds a station. Select **one** point and it splits the span *after* it; select
**two** linked points and it splits the span between them. The new station's cross-section is
inherited from the upstream one, so inserting changes nothing you can see — which is the point: you
insert because you want somewhere to *put* a change, not to make one. At the tail there is no span
to split and it says so; growing the road is `Extend Road`.

**`Merge Points`** is the opposite. Select **two or more points that are next to each other in the
chain** and they collapse into one station. The first of the run survives — it keeps its uid, its
cross-section and its facing, so the merged station is a station and not an average of several —
and only its position moves, to the centroid of the run (tick **At Active** in the redo panel to
keep the active point's position instead). Every link that *left* the run comes with it: the road
either side, a junction clique, a ramp. Links inside the run are what you are collapsing, so they
go.

Two refusals worth knowing, because both are the model talking:

* **not next to each other** — merging `p002` with `p009` is "delete everything between them"
  wearing a friendlier name, and `Delete Point` already says that out loud;
* **not in the same road** — two points in *different* roads that ought to be the same place are
  not a merge. A crossing is a clique of `JUNCTION` links between points that each stay in their
  own road, and collapsing them into one would delete the junction. That gesture is
  `Connect Selected` or `Make Intersection`.

Both renumber the chain afterwards, so the name order still is the chain order.

### 3. Connect two points

Two ways, and **the active point is always the source** in both:

**By selection** — select two points and press `Author ▸ Connect`. Whatever you clicked *last* is
the source — for `Segment` and `Junction`. `Aux` works from either end (see the table below).

**By name** — select one point and use `Connections ▸ Connect To`: pick the other from the object
field, then press a type button. No two-object selection to wrangle, which is the better path in a
dense network.

| Button | Means |
|---|---|
| **Segment** | the carriageway continues from one into the other |
| **Junction** | both become intersection mouths of one pad |
| **Aux** | mainline → ramp — the link is **directed**, but the gesture is not: whichever of the two points declares the aux slot is the mainline, so it connects either way round. The other one is stamped `RAMP`. |

### 3a. Seeing what a point is connected to

The **Connections** panel lists every link on the active point, one row each:

    [type ▾]  demo_main_p004  [X]
              216.0 m -- straight

- the **type** dropdown retypes the link in place;
- the **name** is a button — press it to jump to the other end and walk the graph;
- **X** cuts that one link;
- underneath, the span, and whether that stretch runs **straight** or **bends N°**;
- a **taper too short** warning when the width change is too abrupt for the design speed — the
  same rule the gate uses, so the panel and the gate cannot disagree.

Everything on the row is **derived**. There is no stored "straight or curved" flag to keep in sync;
the geometry already knows.

### 3b. Bending a road

**Select the point and rotate it with `R`. That is the whole gesture.** The point's rotation *is*
the road's direction there, so the road bends to follow it — live in the overlay, before any
rebuild, with no mode to set first. Points are drawn as `ARROWS` so you can see which axis is
which: **local +Y is travel**.

Two things make that work, and both are automatic:

- **A point is born facing its road.** `New Road`, `Extend Road` and `Insert Point` set the
  Empty's rotation to the chain direction, and `Build` re-faces every point the tool still owns.
  So the arrow you are looking at is the direction the solver actually uses — it is never the
  world +Y of a fresh Empty pretending to be a road heading east.
- **A rotation is adopted, a drag is not.** The tool remembers the facing it last gave each point;
  turn the Empty away from that and the point becomes `MANUAL` on the spot, everywhere — overlay,
  gate, build and export all read it through the same function. *Moving* a point changes the chain
  direction without touching the rotation, so a drag is never mistaken for a bend.

`Shape ▸ tangent_mode` shows what happened and lets you take it back: set it to `AUTO` and the
point rejoins the chain (the arrow re-straightens at the next `Follow Road (Auto)` or `Build`).
`Face Road (Manual)` straightens the facing *and* pins the point to `MANUAL` — useful to
re-straighten a bend you no longer want while keeping hand control of it.

- **Straight is detected, never authored.** Two facings that agree with the chord give a dead
  straight run; the Connections row says `straight`.
- **`Leaves` / `Arrives`** are handle lengths in metres (`0` = automatic). They change how *hard*
  the curve leaves and arrives, never which way.
- **If two points cannot express the shape, add a third.** That is the model's answer to a
  compound curve — not a curve-type setting on the link.

`Disconnect` removes a link; the `Author ▸ Connect` buttons grey out until exactly two points are
selected, and the `Connections` ones until you have named a target.

### 4. An intersection

A crossing does **not** split either street. Give each street a point where the stop line should
be — four points for a four-way — select them all, and press `Author ▸ Junction ▸ Make
Intersection`. That writes the full clique and parents every mouth to a `JCT_*` Empty.

- **The points are the stop lines.** Drag one to move that arm's stop line; drag the `JCT_*` parent
  to move the whole junction.
- **Rotate a mouth and the pad turns with it.** A mouth's arm direction is its authored facing
  (`point_model.station_axis`), the same source the carriageway uses — so the cap, the fillets
  either side, that arm's turn paths and its two corner footways all follow the arrow. There is no
  mode to set first and no separate re-solve.
- A pad **always tessellates**: the fan's apex is the ring's kernel point, not the centroid, and a
  ring with no kernel is ear-clipped. Pulling a mouth in too far is a warning about tidiness, not
  a refused build.
- **The pad grows its own kerb and footway.** One run per real corner, on the same corner curve
  the pad boundary is rounded with, built with the two adjoining arms' own footway widths — so
  the pavement wraps the crossing instead of every street's footway stopping dead at its mouth.
  A through-pair contributes none: the road runs straight on and its own edge run owns that
  stretch already.
- **Move or turn the whole crossing by its handle.** The `JCT_*` Empty sits on the live centre of
  its own mouths — it follows them, so dragging one mouth does not leave the grip behind. **G**
  moves the junction; **R** turns it about **Z**, which turns every arm's facing together, and with
  it the cap, the fillets, the turn paths and the corner pavement. (Scale stays locked: a mouth's
  width is its lane count. So does rotation out of plane — the pad is solved in plan.) Turning the
  pad does **not** count as hand-rotating its arms, so an `AUTO` mouth still follows its own road
  afterwards.
  Blender pivots about the scene's **Transform Pivot Point**: the default *Median Point* is the
  handle's own origin and is what you want. Set to *3D Cursor* it turns about the cursor instead,
  and the Junction panel says so. `Junction ▸ Recentre Handles` repairs a file authored before the
  handle followed anything — Build and Auto Setback keep it honest from now on.
- Rough placement is fine: `Junction ▸ Auto Setback` solves the whole clique and moves every
  unlocked mouth. Tick **Setback Locked** on a mouth you have placed by hand.
- A through street contributes **two** mouths, and they are joined by the pad, not by carriageway
  — so do **not** put a `SEGMENT` link between them.

### 5. A ramp

**The one-button way — `Author ▸ Ramp ▸ Branch Ramp Here`.** Make active *any* point of the road
the ramp leaves (or joins) — **the middle of a corridor is the normal case**, and `Extend Road`
deliberately refuses one because "extend" has no meaning there — and press it. It opens the aux
slot back over the length the taper standard asks for, creates the ramp road with its mouth on the
gore line and faced down the mainline, bends the second station **outboard**, and leaves that far
end active so `Extend Road` carries straight on from it. Its options are the ones you would have
had to know anyway: how many lanes leave, which carriageway (`Forward` / `Reverse`), whether it is
an **entrance** rather than an exit, and how far along / outboard / down the second station sits.

If the report says the slot *"opens over 90 m (wants 336)"*, the run has no span long enough for
the taper at that speed — move the mainline stations apart, or lower the road's `taper_factor`.

**The step-by-step way**, when the ramp already exists:

1. `New Road` for the ramp, `lanes_bwd = 0`.
2. On the mainline point where the exit lane opens, set `aux_fwd = 1`, and on the point at the
   gore as well — so the lane runs at full width for the deceleration length. (Use `aux_bwd` for
   a ramp off the **reverse** carriageway; the slot has to be on the side the ramp is.)
3. Select the ramp's mouth and the mainline point — **either order** — and press
   `Author ▸ Ramp ▸ Make Ramp`. The point declaring the aux slot is the mainline, and **which
   carriageway is read off which side the mouth is on** — you do not tell it.

   There is **one ramp role** (`RAMP`), not an exit one and an entry one. Which way traffic runs
   through the link is derived (`point_model.ramp_is_entrance`) from where the mouth sits in the
   ramp's own run and which way the ramp's lanes run — so an entrance is authored exactly like an
   exit, and there is no second declaration to get backwards. (`RAMP_ENTRY`/`RAMP_EXIT` still load
   from older files.)
4. On the *next* mainline point past the gore, set `aux_fwd` back to `0`.

**The aux BLOCK is the exit lane, and the ramp is its continuation.** `lanes_fwd = 3` with
`aux_fwd = 1` is a **four-lane** carriageway whose outermost lane leaves — the panel and the
overlay both say so (`3+1|3`, and a `carriageway: 4 fwd` read-out), because counting it as three
and treating the ramp as a fifth lane beyond it is the one thing that makes an exit look wrong.

`aux_fwd = 2` is a **two-lane exit**, and the ramp continues **both**: the gore line is the
*innermost* aux slot's inner edge, not the outermost's, so widening the exit widens it outward and
never moves the join. Give the ramp `lanes_fwd = 2` to take the whole block (`Branch Ramp Here`
does, from its `aux_lanes`).

**One point can take a ramp OUT and a ramp IN.** Declare a slot on each carriageway — `aux_fwd`
for the one that leaves eastbound, `aux_bwd` for the one that joins westbound — and wire both. They
are different pavement with different slot ids, so they never collide, and **you do not say which
ramp is on which**: it is read off which side of the road its mouth sits. Remember that an
entrance's slot opens *downstream* of its mouth and an exit's *upstream*, and that on opposite
carriageways "downstream" is opposite ends of the chain.

**Two ramps may also share one carriageway's block** — a two-lane exit that splits, or two ramps
merging into one two-lane slot. Widen the block (`aux_fwd = 2`) and wire both: it is **divided**
between them, a slot each, **nearest the through lanes takes the inner slot**, read off where you
put the mouths. The outer ramp's gore then opens against the **inner ramp** rather than against the
mainline, because that is what is beside it. Keep the outer one outboard the whole way — two ramps
that *cross*, or that converge onto the same line, both read in the viewport as z-fighting asphalt
rather than as an error.

**One aux slot, one ramp, though.** A run exports one lane per slot, so two ramps claiming the same
`AF0` on the same unbroken stretch leave only one of them reachable by any car — the gate says so
(`aux_slot_shared`) and names the ways out: widen the block so they take a slot each, put the
second on the **other carriageway** (`aux_bwd` against `aux_fwd`, different slot ids), or break the
run between them, which a junction already does. Asking for more ramp lanes than the block has
slots is `aux_block_oversubscribed`. Nothing about the geometry looks wrong when either happens,
which is exactly why they are checked.

**A ramp must leave *outboard*.** The station after the mouth has to stand away from the through
lanes, on the side the aux slot is. Bend it back across the carriageway and the two bands overlap
for the ramp's whole length: no gore can be paved at all, and the only thing that says so is
`ramp_wrong_side`. If the ramp needs to end up on the other side of the road, it crosses **over or
under** further along — never at the mouth.


`Make Ramp` calls `Align Ramp To Aux`, which does **two** things:

- puts the ramp's mouth on the **gore line** — the aux slot's edge on the through-lane side —
  at the mainline station's own cross-section, so the two bands are cut on the same plane;
- **faces the mouth down the mainline**, and pins it `MANUAL`. A parallel-type exit leaves
  *parallel*; you author the divergence by rotating the ramp's **next** point.

You never author the gore. Build emits it: a paved strip between the mainline's outer edge and the
ramp's inboard edge, running from the theoretical gore (where the two edges actually part) to the
nose (where the gap reaches ~4 m and kerb takes over). It lands in `ROAD_MANAGER_GEN/GORES` with
its own `-noped-colonly` proxy.

**And Build caps the nose.** Past the nose the two roads have parted and each carries its own wall
again — but the V between them belongs to neither: both are silent there for the same correct
reason, that the stretch is the other one's asphalt. So the gore closes it, with a short
`GORE_*__edges_nose` run swept from the ordinary kerb/footway/barrier stack. The cap IS the gore
strip's last pair, flush with the paint, and both flanking walls resume on that same line — three
pieces of wall meeting at a point, with nothing to line up by hand.

You do not author that either, and there is no gore-specific knob for it. **The gore is the ramp's
divergence, so the nose carries the ramp's own section** — uniformly, not blended with the
mainline's:

| The ramp declares | The nose becomes |
|---|---|
| a barrier (`ped_access` off, the usual expressway ramp) | a wall closing the V, at the ramp's own height |
| a kerb and footway | a kerbed pedestrian island, walkable proxy |
| nothing (`barrier_height = 0`, no kerb, no footway) | nothing built — the mainline's values are the fallback only when the ramp declares *nothing at all* |

A ramp leaving an ordinary street is the common case where this matters: the ramp is fenced and the
street is kerbed and paved, and blending the two gave a wall of falling height standing in a footway
of growing width — a section neither road has anywhere else. The **mainline's own kerb and footway
run on past the nose unbroken**; they never stopped, they were only ever opened *across* the paint.

The collision proxy follows the same source: `-noped` unless **both** roads allow pedestrians, so a
gore between an expressway and its ramp never bakes as a navmesh strip in the middle of an exit,
while a gore between two ordinary streets does.

The gate reports both numbers rather than hiding them: the station residual in metres
(`ramp_edge_residual`, an error) and the divergence angle in degrees (`ramp_diverge_angle`, a
warning past 8°). A lane *departing* onto a ramp is exempt from the merge-taper rule — it is
not merging into traffic, so the mainline's edge may come back at gore rates rather than tapering
a lane to nowhere for the next 200 m.

### 6. A highway, and what holds it up

An expressway is an ordinary road with more lanes, a higher `design_speed`, no footways, and
**`ped_access` off** on the **Road** panel (without that, AI walk onto it).

You never author its supports. Give the points a **Z** and Build derives everything from
`delta = surface_z − ground_z`: at grade it cuts the terrain, a little up is an earth embankment
with a real 1:1.5 batter, well up is a deck on piers, down is a trench or a bore. The terrain is
raycast on **every** Build — there is no "sample ground" button to forget.

### 6a. Kerbs, footways and walls

Everything outboard of the asphalt rides the road's **outline**, never its centreline — which is
what makes it open by itself at a gore, a merge or a junction mouth, with no special case anywhere.
Three layers, in order outward:

| Layer | From | Where |
|---|---|---|
| kerb | `left_kerb_height` / `right_kerb_height` | every open stretch |
| footway | `left_walk_width` / `right_walk_width` | wherever the width is non-zero |
| **barrier** | the road's `barrier_height` | **derived** — see below |

**You author how tall a barrier is; Build decides where it stands.** A road with **`ped_access`
off** (an expressway, a ramp) is fenced along its whole length; a road people may walk on is fenced
only where it is genuinely off the ground — a viaduct's parapet — and not where it merely rides
a low kerb. Set `barrier_height = 0` on the **Road** panel for none at all.

Because it rides the outline like the kerb, the wall **opens across a gore and closes again past
the nose**, and runs on into the next segment of the same road without you joining anything up.
That is the same mechanism, not a second one.

What decides it, exactly, is *"does the pavement continue past this line?"* — probed **outboard**
from the edge, not "is there asphalt somewhere within half a metre". The difference shows where a
ramp leaves along the mainline's own outer edge: both edges are the outer boundary of the same
pavement there, and an undirected test had each road suppress the other's parapet, leaving a hole
in the wall at the top of the drop. Directional, the mainline's wall runs up to the mouth and the
ramp's takes over from it.

It asks two things, and either one opens the furniture: *does the pavement continue past this line*
(probed outboard) and *is this line buried under someone else’s pavement*. The second is what stops
a mainline growing a second wall half a metre inside the ramp that has taken over from it — a band
only slightly wider than the probe would otherwise be stepped clean over. An edge sitting exactly
**on** another road’s boundary is the shared outer edge and keeps its wall.

Where a run stops is not rounded to the 4 m sampling either: the end is clipped onto the boundary
it stops against, so a wall ends **at** the mouth it hands over at and starts **at** the gore nose
it has to meet.

One thing the hand-off cannot do is close the **tip** of a gore: there both roads open, correctly,
and the gap is nobody’s — see the nose cap in §5, “A ramp”.

### 6b. Seeing the traffic before Godot does

`Preview ▸ Traffic Flow` draws **the export, not the authoring**: every lane of `.lanekit.json`,
directed, with chevrons and its successor links. Turn on **Cars** and agents walk that graph,
choosing successors by the exported weights.

And it draws the **Path3D**, not the lane — which are not the same object either. `WorldBaker`
builds every `Curve3D` an ambient car drives from the `curve` block, a fitted bezier through the
stations; the lane polyline is where the asphalt is. The `Geometry` dropdown picks which you see:

| Geometry | Draws |
|---|---|
| **Path3D (export)** *(default)* | the bezier Godot will drive. The cars walk this one |
| Lane polyline | the sampled lane centreline — where the asphalt is |
| Both | the lane in its own colour, the Path3D in white over it, **orange rungs** where they part |

A lane whose path wanders more than 0.5 m from it is drawn **orange** and named in `Flow Report`
as `path_off_road`, in metres. The gate says the same thing from the same function
(`path_deviation`, a warning at 0.5 m and an error at 2 m), so the picture and the finding cannot
disagree.

This is not hypothetical. The export used to refit each lane's handles from the chord through its
neighbours, throwing away the authored tangents the road is actually built from — and at an open
end that chord sat **70°** off the true heading. On this addon's own sample network the shipped
Path3D ran **22.57 m** from the ramp it was supposed to be, with a green gate, perfect geometry
and nothing able to see it. The handles are now fitted to the lane itself (direction from its own
tangent, length by least squares, subdividing only where a cubic genuinely cannot follow), which
brings the same network to **0.09 m**.

That is a different object from everything else in the sidebar, and the difference is the point. A
road can be built, gate-green, and still export a lane nothing can reach — which is exactly what
the sample's exit ramp did until this landed. In game it reads only as "that ramp is always
empty".

| Drawn | Means |
|---|---|
| blue | carriageway |
| amber | junction connector |
| green | a ramp, or the lane that hands off to one |
| violet | a merging lane's successor |
| white hairline | a `next` edge, tail to head |
| **red X** | a lane with **no successor** whose tail sits on the head of a lane going the same way — it should have chained and did not |
| **magenta ring** | a lane **nothing leads to** — always flagged for a ramp |

`Flow Report` prints the whole list to the console, naming each lane. A dead end at the edge of the
network is listed separately as an "open end", because it is expected and would otherwise drown
the two lines that matter.


### 6c. When something is wrong that you cannot see

`Author ▸ Repair` holds the two whole-scene fixers. Neither takes a selection, because both are for
the defects that do not show up in the outliner.

**`Repair Links`** — the one the gate has always named. It *drops* a link row that cannot be
honoured (its target was deleted, it points at itself, at a non-point, at a point in no road, or it
is a second row to a target already linked — a pair of points carries at most one link), drops the
ramp's half of an `AUX` pair (`AUX` is directed; a ramp connects only to the aux slot), *restores*
the missing half of a half-written `SEGMENT` or `JUNCTION` link and completes a junction into the
full clique, and *writes back* a re-allocated uid after a `Shift+D` on a single point. Where the two
rows of a pair disagree about type, the more explicit gesture wins: `AUX` over `JUNCTION` over
`SEGMENT`.

**`Tidy Roads`** — files every point into the road its connections say it belongs to. See §1.

**`Renumber Roads`** — renames every road's points so the chain order is the order its own *links*
put them in. This is the answer to "is it alright to just move things about by hand?": **the chain
order IS the object-name order** (the reader sorts a road's points by name), so a rename, a
`Shift+D`, or a point dragged into another collection silently reorders the road — the links still
say what is joined to what and the build follows them, but the file no longer reads the way it
behaves. Every gesture in this addon renumbers for you; this is the repair for the edits that are
not gestures. It reads the links and renames to match, never the other way round: links are
authored and a name is derived, so when they disagree the name is the one that is wrong. It is
idempotent — running it on a tidy scene renames nothing — and it leaves a stretch whose links
*branch* alone, because three points meeting at one station is a junction and has no chain order to
restore. The gate names both cases: **`chain_out_of_order`** and **`chain_branched`**.

### 6d. What the road is MADE OF — materials, markings and profile assets

Everything above is the road's *shape*. The **Road ▸ Style** box is what it is made of, and it is
authored **per road**: a stretch of the same road paved differently is a different road, so there
is no per-station material to keep in sync.

Each row is one layer, with a **material** and — where it makes sense — an **asset**:

| Layer | Material slot | Asset? |
|---|---|---|
| surface | the carriageway, the pads and the gores | — |
| median | the divide (see below) | yes |
| deck | the structure and its piers | — |
| kerb | every open edge | **yes** — the common one |
| footway | the pavement | yes |
| barrier | the parapet / fence | yes |
| mark w / mark y | the paint | — |

Blank means the layer's default. A name that resolves to nothing **falls back and says so** —
in the build report and as a gate warning (`style_missing`) — because a silently wrong material
looks exactly like a shading mistake.

**Where a default comes from: `assets/world_source/kit/road_kit.blend`.** The same kit file that
holds the profile sections is also the repo's **material library** — every `M_*` datablock, with a
fake user. `kit_common.mat()` *links* from it rather than building its own copy in the district, so
there is exactly one `M_Asphalt` in the world: the deck's, the kerb section's, and a car park's are
the same datablock, and re-shading it in the kit restyles everything at once. Precedence is one
line — a material of that name already in your file wins (so a local hand-edit survives a rebuild),
otherwise the kit's, otherwise it is built from `kit_common`'s own table as a bootstrap for a
checkout where `blender/tools/build_road_kit.py` has not been run yet. Nothing to press: the link
happens on the first material a build asks for.

**The median is a style, not a width.** `median_width` still says how wide the divide is, per
station, and can taper to nothing; `median_style` says what it *is*:

| `median_style` | Builds |
|---|---|
| `NONE` | nothing, whatever the width says |
| `PAINT_DOUBLE_Y` | a flush double-yellow, `M_LineY`, no island |
| `RAISED` *(default)* | the island, its top at kerb height |
| `WALL` | the island, with the barrier standing on it |

**The white strips are automatic.** Every painted boundary — the dashes between lanes, the solid
edge of a gore, the double yellow where two carriageways meet with no median — comes from the
cross-section itself (`Slot.mark_left`), so a line appears when a lane opens, moves outward as a
median widens, and stops when a ramp departs, with nothing to author. Dashes are real geometry
(3 m on, 6 m off) laid on **one clock per run**: every boundary breaks at the same cross-sections,
so a lane that opens in the middle of a run joins the grid the lines either side of it are already
on instead of starting its own (measured before that: an aux lane opening 4.00 m in was 4.00 m off
a 9.00 m period — half a period, which reads as that lane's paint being wrong). It also keeps them
square through a bend, where the outer line's own length runs ahead of the inner one's. The paint
is lifted 1 cm clear of the asphalt, and it is deliberately **not collidable** — a proxy per stripe would put a paper-thin body under every dashed line in the world.
Turn it off per road with **Markings**.

#### A profile asset: authoring the section itself

Naming an asset **replaces** that layer's parametric band with a cross-section you modelled, swept
continuously along the road — so a kerb can have a real chamfer, a barrier can be a New Jersey
section, and changing either is editing one curve rather than a builder.

`Road ▸ Style ▸ Link Road Kit` library-links `assets/world_source/kit/road_kit.blend`, which
ships six sections (`RKA_PROFILE_kerb_std`, `_kerb_granite`, `_gutter_dish`, `_wall_parapet`,
`_wall_jersey`, `_footway_slab`). Linked, so editing that one file restyles every road in the
world that names it. `blender/tools/build_road_kit.py` rebuilds it.

**To author your own:** draw the section in the XY plane, **+X outward from the road, +Y up**,
with the origin on the line the road hands it — the kerb line for a kerb, the foot for a barrier,
the top of the kerb for a footway. Give it a material. Name it `RKA_PROFILE_<layer>_<variant>`.
Three things happen for you and are worth knowing:

- **You draw it the right way up.** `Curve to Mesh` sweeps a section drawn outward-and-up as
  inward-and-down (measured: profile +X → world −Y, +Y → −Z); the group corrects it, so what you
  draw is what you get. It also mirrors the section on the road's right-hand flank, so an
  asymmetric kerb faces outward on **both** sides.
- **Its own size wins — for the sweep.** The section is swept at the dimensions you authored,
  never scaled to a design number. **But the solve does not know that yet:** it still places
  whatever sits outboard of the section from the *authored* width, so a 2.00 m footway section on
  a road reserving 3.00 m leaves a real 1.00 m gap before the barrier. Author the width to match
  the section you picked — the gate warns with both numbers (`asset_width_mismatch`). Until that
  is wired, a district authored with **materials only** avoids the question entirely.
- **It is gated like the layer it replaces.** Naming a barrier asset on an at-grade street with
  pedestrian access still builds no barrier, because `ped_access` and the road's height are what
  decide *where* a wall stands — the asset only decides what it looks like.

**Shape only, though:** `Curve to Mesh` drops a profile's materials (measured — zero slots on the
sweep, even from a two-material section), so the addon reads the material off the asset and feeds
it to the layer. One asset is one material is one layer; a section that genuinely needs two
materials is not expressible, and would want one sweep per spline group.

**Sections, not tiles.** These are swept, never instanced along the edge. Tiling rigid pieces
round a 9 m corner puts each ~12.7° from its neighbour and opens a real ~7.8 cm gap at every joint
— inherent to the geometry, not a phase bug, and the reason the previous model's asset style was
retired. Tiling is right for things that *are* discrete (lamp posts, signs, bollards); that is
`GN_PointAssets`' job, not this one.

### 7. Build, check, export

`Build ▸ Validate` first — **a build that fails the gate is a failed build**, and every finding
names the object to go and fix — in the subject *and* in the message, so the `<road>/<point>` it
tells you to move is one you can type into the outliner's search box. Then `Build Roads`, and `Export .lanekit (v2)` for Godot (it
refuses while the gate is red). `Live Rebuild` re-sweeps a road when its points settle; the overlay
follows the drag either way.

`Save Road Record` writes `<blend>.roads.json` — **that file is the source of truth**, and the
Empties are a view of it. Commit it.

## Six rules that hold the whole thing up

1. **One owner of "where is slot *i*".** `lane_profile.slot_offset()`. No other module computes a
   lateral offset — they ask, or they read the outline.
2. **Authored and generated never share a collection.** A build only ever clears inside
   `ROAD_MANAGER_GEN`. Nothing under `ROAD_MANAGER` is deleted by any build, ever.
3. **The record is the file.** `<stem>.roads.json` is the source of truth; the `.blend` is not.
   Both previous rewrites stored the authored state only in `.blend` IDProperties, so when the
   model changed there was nothing to migrate.
4. **The point IS the stop line.** No hidden setback solve between the artist's Empty and the pad.
   `Auto Setback` writes the points; it does not sit behind them.
5. **A build that fails the gate is a failed build**, and every finding names the **object** to
   fix, because artists fix objects, not indices.
6. **The transform is the road frame — nothing derived is stored.** Position is the station, local
   **+Y** is travel direction, roll is banking, and straightness is *measured*, not flagged. A
   stored "this bit is straight" is one more thing that can disagree with the geometry. The
   corollary the tool owes you: **a point is born facing its road and the arrow is kept honest**,
   because a frame the artist cannot trust is worse than no frame at all.

## The two models this replaced

`legacy_graph/` holds the mesh-graph model (archived, not imported); the per-piece generators were
deleted. See `legacy_graph/README.md` for what was wrong with each, and where the pieces worth
keeping went.

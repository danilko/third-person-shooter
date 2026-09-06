# Tokyo 23-ku → 6 km playable map — Spatial Compression Design

**Status:** design of record for the compressed-Tokyo world.
**Generator:** `tools/plateau2json/tokyo6km_{layout,network,build,preview}.py`
**Output:** `build/tokyo6km/layout.json` (injection surface) + `build/tokyo6km/preview.png`
**Source:** Project PLATEAU (MLIT), CC BY 4.0 — extracted via `tools/plateau2json/extract.py`

---

## 0. The one rule

> **Compress the distance *between* things. Never compress the things themselves.**

`compress.py` already enforces this for a mechanical squeeze. A *game map* needs one more
rule on top, because at the ratios below the arithmetic cannot close by moving things
closer alone:

> **When a band cannot fit even after every gap is gone, DELETE whole blocks.
> Never scale a block, a lane, a runway, or a building.**

A 3.25 m lane stays 3.25 m. A 30 m city block stays 30 m. What changes is *how many* of
them exist between Shinjuku and Tokyo Station.

---

## 1. What has to fit, and what it costs

Real bounding box of everything the map must contain (EPSG:6677, the Tokyo plane CRS):

| | real | compressed | linear ratio |
|:--|--:|--:|--:|
| X (east–west) | 13.6 km | 6.048 km | **2.25 : 1** |
| Y (north–south) | 21.8 km | 6.048 km | **3.61 : 1** |
| area | 296 km² | 36.6 km² | **8.1 : 1** |

…and that is *before* the mountains, which sit **55 km west** of Shinjuku and cannot be
warped in at any ratio.

Hence **two tiers**, and this split is the central design decision:

| tier | applies to | transform | why |
|:--|:--|:--|:--|
| **A — WARP** | Tokyo core: Nakano → Shinonome × Ueno → Haneda | monotonic piecewise-linear, per axis, C0 | continuous, so nothing tears or de-registers; road/rail/terrain/landuse all ride the same map |
| **B — ANNEX** | Okutama massif | **rigid translate** (dx, dy, and a constant dz) — scale exactly 1.0 | 55 km cannot be warped. Cut a 2 km window out of the real mountain and graft it on. Keeps **real slope, real relief, real hairpin radii** — a touge that is scaled is not a touge. |

Tier B's transform, recorded in `layout.json.warp.tier_b`:

```
source  EPSG:6677 window, centre (-66634.3, -21900.0), 2016 × 2016 m
        (Tama gorge floor 1 km south of Okutama station — valley AND ridge in one window)
dx      +64618.3      dy  +23916.0      dz  -280.0      scale  1.0
```

`dz = -280` is a **constant subtraction**, not a scale: it drops the valley floor from
340 m T.P. to 60 m game so it meets the city's elevation band. Every gradient in the block
is untouched.

---

## 2. The warp — where the deletions actually are

Control points are *authored*: the four city tentpoles are nailed to the district centres
they were assigned, and the gaps between them absorb all the error. Full table in
`layout.json.warp.tier_a`.

### X axis (west → east)

| segment | real | game | scale | deleted | what is being deleted |
|:--|--:|--:|--:|--:|:--|
| Nakano → Shinjuku | 3428 m | 1344 m | 0.39 | 2.1 km | Ōkubo/Yoyogi mid-rise infill |
| Shinjuku → Yotsuya | 2388 m | 506 m | **0.21** | 1.9 km | **Ichigaya office plateau** |
| Yotsuya → Palace | 2064 m | 500 m | 0.24 | 1.6 km | Kōjimachi ministry blocks |
| Palace → Tokyo Stn | 1294 m | 506 m | 0.39 | 0.8 km | Marunouchi outer ring |
| **Tokyo Stn → Akihabara** | 544 m | 504 m | **0.93** | 0.04 km | **nothing — the hero corridor** |
| Akihabara → Haneda col. | 1114 m | 640 m | 0.57 | 0.5 km | Nihonbashi east |
| Haneda col. → Toyosu | 956 m | 600 m | 0.63 | 0.4 km | Tsukiji/Kachidoki |
| Toyosu → Shinonome | 362 m | 361 m | **1.00** | 0 | nothing — waterfront is 1:1 |

### Y axis (south → north)

| segment | real | game | scale | deleted | what is being deleted |
|:--|--:|--:|--:|--:|:--|
| north of Ueno | 1747 m | 250 m | **0.14** | 1.5 km | **Sugamo/Ikebukuro homogeneous ward** |
| Akihabara → Ueno | 1708 m | 920 m | 0.54 | 0.8 km | Okachimachi (kept — it's good) |
| Shinjuku → Akihabara | 504 m | 180 m | 0.36 | 0.3 km | Kanda infill |
| Tokyo Stn → Shinjuku | 1404 m | 410 m | 0.29 | 1.0 km | Ōtemachi/Jimbōchō |
| Ginza → Tokyo Stn | 1054 m | 260 m | 0.25 | 0.8 km | Kyōbashi |
| Tokyo Tower → Ginza | 1347 m | 380 m | 0.28 | 1.0 km | Shimbashi/Toranomon |
| Rainbow Br → T. Tower | 2431 m | 400 m | 0.16 | 2.0 km | Shibaura/Konan wharf sheds |
| **Odaiba → Oi wharf** | 3660 m | 450 m | **0.12** | **3.2 km** | **Shinagawa–Ōmori sprawl** |
| Oi → Keihinjima | 2108 m | 250 m | 0.12 | 1.9 km | **Ōmori–Kamata sprawl** |
| Keihinjima → runway N | 2109 m | 250 m | 0.12 | 1.9 km | outer Keihin yards |
| runway N → terminal | 743 m | 200 m | 0.27 | 0.5 km | apron taxiways |

**Read the two tables together and the design states itself:** the single densest,
most-photographed 544 m in Tokyo (Tokyo Station → Akihabara) survives at **93 %**, while
the 9.5 km of homogeneous low-rise between the bay and the airport is cut by **88 %**.
That asymmetry *is* the map.

---

## 3. District matrix — 12 × 12 × 504 m

The engine's district stays **504 m** (72 × 7 m cells) — the proven streaming chunk size,
deliberately unchanged. Only `GRID_N` moves 6 → 12, so the world becomes **6048 m**.
Centre-origin: the map spans `[-3024, +3024]` on both axes, exactly
`blender/lib/world_grid.py`'s convention.

```
        gx0    gx1    gx2    gx3    gx4    gx5    gx6    gx7    gx8    gx9    gx10   gx11   
gy11 |  void   mtn    mtn    snow   mtn    mtn    rural  rural  rural  void   void   void   
gy10 |  mtn    mtn    MTN    mtn    mtn    rural  rural  resid  rural  rural  void   void   
gy9  |  mtn    mtn    mtn    MTN    rural  rural  resid  resid  resid  rural  rural  void   
gy8  |  mtn    mtn    rural  rural  resid  resid  resid  resid  resid  resid  rural  rural  
gy7  |  rural  rural  resid  resid  resid  resid  city   city   resid  resid  resid  rural  
gy6  |  rural  resid  resid  resid  CITY   city   city   city   CITY   city   resid  resid  
gy5  |  rural  resid  resid  city   city   city   city   CITY   city   city   resid  resid  
gy4  |  rural  resid  resid  resid  city   city   city   city   city   city   harbor harbor 
gy3  |  rural  resid  resid  resid  resid  city   city   city   HARBOR harbor harbor harbor 
gy2  |  void   resid  resid  resid  resid  resid  resid  harbor harbor harbor harbor void   
gy1  |  void   void   resid  indus  indus  indus  indus  INDUS  harbor HARBOR harbor void   
gy0  |  void   void   void   indus  indus  indus  indus  indus  harbor harbor void   void   

UPPERCASE = tentpole cell.  40 resid / 24 city / 22 rural / 17 void / 15 harbor / 15 mtn / 10 industry / 1 snow
127 BUILT districts of 144 — 17 void cells stream nothing at all.
```

Compass relationships are **all real**: Shinjuku west, Akihabara east, both on the same
latitude band (they really are 505 m apart in northing); Tokyo Station just south and
central; the bay south-east; Haneda due south; Okutama north-west. Nothing was rotated to
make the layout convenient.

### Tentpole anchors (game metres, centre-origin)

| id | label | game XY | district | footprint |
|:--|:--|--:|:--|:--|
| `shinjuku` | Shinjuku / Kabukicho | `(-756, 150)` | gx4 gy6 | 3 × 2 |
| `tokyostation` | Tokyo Station / Marunouchi | `(756, -260)` | gx7 gy5 | 3 × 3 |
| `akihabara` | Akihabara / Kanda | `(1,260, 330)` | gx8 gy6 | 2 × 2 |
| `haneda` | Haneda Airport | `(1,980, -2,500)` | gx9 gy1 | 4 × 3 |
| `harbor` | Tokyo Bay waterfront / Odaiba | `(1,330, -1,450)` | gx8 gy3 | 3 × 3 |
| `industry` | Keihin industrial belt | `(572, -2,150)` | gx7 gy1 | 5 × 2 |
| `mountain` | Okutama massif (annexed) | `(-2,016, 2,016)` | gx2 gy10 | 4 × 4 |
| `touge` | Okutama touge pass | `(-1,450, 1,750)` | gx3 gy9 | 2 × 2 |

### The one deliberate violation: Haneda's runway

Real 16R/34L is **3000 m**. Warped it becomes a 400 m stub; scaled it breaks rule zero.
So the **middle is deleted** and a **1300 m** runway is authored at the true **157°**
heading — a 1.3 km drag strip, which is what an airport is *for* in a driving game. The
other three runways are deleted outright, not shrunk.

```
north end (1246.0, -1781.7)   south end (1754.0, -2978.3)   width 60 m
```

Runway on gx8–9, terminals east on gx9–10 — the real way round (Runway A is Haneda's
westernmost strip).

---

## 4. Transitional buffer zones — how the cuts are hidden

A deletion is only as good as its stitch. Each buffer is a *designed district band* whose
job is to break the eyeline so the two things it joins never share a frame.

| buffer | joins | length | deletes | device |
|:--|:--|--:|--:|:--|
| `BUF_YOTSUYA` | Shinjuku ↔ Tokyo Stn | 1006 m | 3.7 km | Sotobori moat cut + 900 m Chūō rail trench |
| `BUF_SHIMBASHI` | Tokyo Stn ↔ harbour | 810 m | 2.4 km | C1 viaduct + Shiodome tower wall |
| **`BUF_SHINAGAWA`** | harbour ↔ industry | 700 m | **6.1 km** | Shinagawa rail cutting + Ōi container stacks + Shinkansen viaduct |
| `BUF_TAMA` | industry ↔ Haneda | 500 m | 1.8 km | Tama river mouth + airport perimeter fence |
| **`BUF_GORGE`** | Shinjuku ↔ mountain | 1000 m | **52 km** | climbing valley → **tunnel portal** |

Three devices do all the work, and they are the only three you need:

1. **A wall you cannot see past** — an elevated deck, a container stack, a Shinkansen
   viaduct. The horizon *is* the structure, so the missing kilometres are simply not in
   frame. This is `BUF_SHINAGAWA`, and it hides the single biggest cut on the map.
2. **A void you cannot cross** — water (Tama river mouth, the Haneda channel) or a rail
   cutting. The eye reads "edge of a thing", not "edge of the map".
3. **A tunnel** — the only honest way to hide a *discontinuity*. `BUF_GORGE` deletes 52 km
   in 1 km of road: density ramps resid → rural → nothing, valley walls close in, then
   R4/Chūō dives into the portal at `(-2450, 900)` on a 300° heading and surfaces inside
   the annexed block. **The Tier A ↔ Tier B seam is never in frame.** That is why the
   annexation is invisible and the warp is not.

---

## 5. Road hierarchy

Four tiers, mapped onto grid structure the engine already builds.

| tier | what | width | spacing | grade |
|:--|:--|--:|:--|:--|
| **T1** | elevated expressway (Shuto) | 22 m deck | radial | deck **+12 m**, no at-grade junctions, ramps only |
| **T2** | arterial | 27 m | **every district seam, 504 m** | at grade |
| **T3** | local street | 14 m | every region line, **168 m** (3/district/axis) | at grade |
| **T4** | alley (*roji*) | 4.5 m | **per theme, 45–60 m** | at grade, dead-end tolerant |

**T2 is literally `build_world.make_grid()`'s existing arterial backbone**, unchanged —
13 N–S + 13 E–W lines. Real Tokyo arterial spacing is 300–600 m, so 504 m is authentic for
free. Named: Meiji-dōri (V4), Sotobori-dōri (V6), Chūō-dōri (V7), Harumi-dōri (V9),
Kannana (V2), Kanpachi (V1); Yasukuni-dōri (H6), Eitai-dōri (H5), Sakurada-dōri (H4),
Dai-ichi Keihin (H2), Ōme-kaidō (H8).

### T1 network — two nested circuits

| id | route | length | role |
|:--|:--|--:|:--|
| `C1` | Inner Circular (都心環状線) — **closed** | **4.00 km** | the short lap. Wraps Palace / Tokyo Stn / Ginza. |
| `B_WANGAN` | Bayshore (湾岸線) | 4.00 km | **the top-speed straight.** East edge → Odaiba → Ōi → Haneda. |
| `R11_DAIBA` | Route 11 Daiba Line | 1.39 km | **Rainbow Bridge.** Closes C1 ↔ Wangan. |
| `R1_HANEDA` | Route 1 Haneda Line | 4.35 km | inland run to the airport. |
| `R4_SHINJUKU` | Route 4 → Chūō Expwy | 3.05 km | west radial → mountain approach → tunnel. |
| `R5_IKEBUKURO` | Route 5 Ikebukuro Line | 2.72 km | north-west radial. |
| `R6_UENO` | Route 6 Mukōjima Line | 2.02 km | north-east radial past Akihabara. |
| `TOUGE` | Okutama pass road (T2) | 2.98 km | **the mountain tongue.** |

- **Short lap = C1, 4.0 km.** Real C1 is 14.8 km, so 0.27 — consistent with the map's
  overall squeeze, and a genuinely good circuit length.
- **Long lap = C1(Shimbashi→Tameike) + R11 + Wangan + R1 ≈ 10.5 km**, closing at
  Shimbashi. Core → Rainbow Bridge → bay → airport → back inland.

**The touge is spec'd, not sketched:** 2978 m of road for a **240 m** climb =
**8.1 % ruling grade**, 4 hairpin pairs, 11 m minimum hairpin radius, 2.75 m lanes,
guardrail on the valley side only. It stops at the **pass (300 m)** — the 620 m summit
above it is scenery. Driving to the summit would force an 18 % wall.

### Rail — an addition, and the primary occluder

| id | line | length | deck |
|:--|:--|--:|--:|
| `YAMANOTE` | Yamanote loop — **closed** | 8.91 km | +8 m |
| `CHUO` | Chūō → Ōme line to the mountain terminus | 6.18 km | +11 m |
| `SHINKANSEN` | Tōkaidō Shinkansen viaduct, exits SW | 4.65 km | +13 m |
| `KEIKYU` | Keikyū / Keihin-Tōhoku to Haneda | 3.10 km | +8 m |
| `YURIKAMOME` | guideway, rides the Rainbow Bridge lower deck | 2.34 km | +10 m |
| `MONORAIL` | Tokyo Monorail over the water to Haneda | 2.24 km | +14 m |

Rail decks sit **below** the +12 m expressway deck so the two cross cleanly. Level
crossings (踏切) go on T3 streets only — never T2 — so a closing crossing is a gameplay
beat, not a traffic deadlock.

---

## 6. Asset placement & the density gradient

Per-theme street/massing rules, in `layout.json.street_rules` (every district carries its
own copy under `districts[].street_rules`, so a district builder reads one object).

| theme | T3 spacing | T4 alley | **block retention** | max sightline | dead-ends | storeys |
|:--|--:|--:|--:|--:|--:|--:|
| `city` | 168 m | **45 m** | **0.35** | **180 m** | 30 % | 6–14 |
| `resid` | 168 m | 60 m | 0.45 | **140 m** | 45 % | 2–4 |
| `industry` | 252 m | — | 0.60 | 400 m | 20 % | 1–3 |
| `harbor` | 252 m | — | 0.70 | 900 m | 10 % | 1–6 |
| `rural` | 336 m | — | 0.55 | 350 m | 55 % | 1–2 |
| `mtn` / `snow` | — | — | 1.00 | 250 / 300 m | 80 / 90 % | 1–2 |

**`block_retention` is the most important number on the page.** It is set to the *local
warp scale*, because that is the only way the arithmetic closes: at scale 0.35 you cannot
keep every real block and you must not shrink them, so **you keep one cross-street in
three and delete the other two**. Same idea for buildings — cull whole footprints from the
PLATEAU extract, never rescale one.

### Vibe rules — dense, authentic, and cheap in the same move

The Japanese-density look and the LOD budget are *the same lever*, which is why this map
can be dense on moderate hardware:

- **Zero lot line in `city`.** `setback_m = 0`. Buildings meet, signage overhangs the
  alley. There is no gap for the camera to see through — so the far field is never drawn.
- **Neon verticality on the short axis.** 6–14 storeys with signage stacked *vertically*
  up the façade. Height reads as density from the street without adding footprint count.
- **Elevated rail is a free occluder wall.** The Yamanote/Chūō viaducts cut every city
  district in half at eye level. Put the *izakaya under-guard strip* there — the most
  recognisably Tokyo space on the map is also a hard occlusion plane.
- **T4 alleys jog.** No straight local street may run more than 3 blocks without a dogleg;
  arterials dogleg at every second seam. This is what enforces `max_sightline_m`.
- **Sightlines shorten as density drops — deliberately inverted.** `resid` has the
  *shortest* sightline (140 m) because 2-storey Kamata low-rise with 4 m alleys occludes
  harder than a tower district. So the cheap-to-render theme is also the cheapest to
  *cull*. `harbor` is allowed 900 m only because there is almost nothing in that cone.
- **`mtn` has no street grid at all.** Ridges are the occluder. Terrain does the culling
  for free — which is the other reason the mountain is annexed at 1:1: real Okutama
  topography occludes better than anything that could be authored.
- **Fog/light per theme** already exists in `world_grid.THEMES` (`fog`, `light`) — the
  gradient neon 4200 K/0.004 → mountain 6000 K/0.014 does the far-field fade.

### Land and water are DATA, not districts (correction)

The first version of this document drew the coastline and the bay as hand-authored
polygons and coloured whole 504 m districts as "harbor". **That was wrong**, and it is
worth stating plainly because it is the easiest mistake to make here:

> **A district is a streaming container. It is not the shape of the land, and it is not
> the road network.** Land outline, water and roads all come from the survey; the grid is
> laid *over* them.

Concretely, and all verified against the real extract (`tokyo6km_real.py` renders it):

- **The open bay is not in PLATEAU at all.** Every ward dataset stops dead at its own
  shoreline — the `wtr` module's five 海 features across all seven bay wards are 40 m
  harbour curves, not Tokyo Bay. So the coast is derived the other way round:
  **land = the union of the `luse` chōme polygons** (376,617 of them; they tile every
  square metre of land and nothing offshore), **water = the complement**, with rivers and
  canals cut back in on top. Wharves, slips, reclaimed islands and the Sumida delta all
  fall out of the survey instead of being drawn by hand.
- **"No polygon here" only means water *inside the extract window*.** Outside it, absence
  means *no data* — painting that as sea invented an ocean across the whole northern half
  of the map on the first pass. The window is recorded as `EXTRACT_BBOX_6677`.
- **Land and water warp POINT-WISE** (they are `compress.py`'s FIELD layers). Anything
  else tears adjacent polygons apart and leaves gaps along every shared edge.
- **REGISTRATION BEATS RIGIDITY — everything takes the same point-wise warp.** An earlier
  pass translated compact roads rigidly and warped elongated ones on a single axis, while
  land and water warped point-wise. **Three different transforms cannot register:** roads
  drifted off their own blocks and read as horizontally squeezed against the land. Roads
  now take the identical point-wise warp, so a street lands exactly on its block and the
  Palace moat and its ring road line up to the metre.
- **Width is given back as a stroke, not by refusing to warp.** The point-wise warp does
  squeeze a footprint, so nothing is ever drawn narrower than `MIN_ROAD_M = 6 m`. That
  honours "never compress the thing itself" *without* breaking the map's coherence — the
  earlier rigid-translate "fix" bought width at the cost of registration, which was the
  wrong trade.

#### Reduction: it is essentiality, not de-duplication

There is **no literal duplication to remove** — all 244,452 road footprints are distinct
(checked: zero repeated centroid/vertex-count keys, zero repeated feature ids). What reads
as "duplicated" is **fragmentation plus redundant parallel streets**. So the reduction is
a three-stage essentiality filter, in priority order:

| stage | rule | effect |
|:--|:--|--:|
| **protect** | every street within **100 m of a tentpole** is kept, whatever its size | landmarks stay legible |
| **protect** | function **1 / 2 / 3** (expressway, national, prefectural) is never dropped | arterial skeleton intact |
| **cull** | drop fragments **< 100 m²** | −51.6% of polygons, only −10% of road area |
| **quota** | rank the rest by width inside each **56 m bucket**, keep the top **2** | even density, widest road wins |

**244,452 → 22,417 (9.2%).** The quota matters more than the threshold: ranking by width
means the most *drivable* road in each neighbourhood is the one that survives, which is
strictly better than the uniform random thinning it replaces — that chewed holes in
arterials at random. Detail is spent where the player looks and taken from everywhere else.

The `harbor`/`industry`/`city` themes in §3 remain valid — but they are **content rules**
(what buildings, density, lighting and AI go there), not land shape.

### Land geometry (blockout volumes, `layout.json.land`)

| id | kind | extent | note |
|:--|:--|:--|:--|
| `tokyo_bay` | water polygon | SE half, `z = 0` | free culling, free skyline |
| `haneda_island` | landfill rect | `(1008,-3024)–(2520,-1512)`, `z = 4` | reachable **only** by the Wangan viaduct and the Keikyū tunnel |
| `tamagawa` | river polyline, 150 m | SW edge → bay | 4 bridges are the only crossings — chokepoints |
| `okutama_block` | annexed DEM | `(-3024,1008)–(-1008,3024)`, base 60 m → summit 620 m | NW skybox + NW culling wall |
| `chuo_portal` | tunnel portal | `(-2450, 900)`, heading 300°, 260 m | **the seam hider** |

---

## 6b. Shrinking the built world — void districts and the map edge

Streaming cost is paid **per built district**, so the cheapest optimisation available is
to not build one. The matrix in §3 was re-cut for exactly this:

| | first pass | reduced | change |
|:--|--:|--:|--:|
| `void` (nothing authored, nothing streams) | 2 | **17** | +15 |
| `resid` (most cost per unit of player interest) | 60 | **40** | −20 |
| `mtn` + `rural` (cheap, no street grid) | 29 | **38** | +9 |
| `city` (the reason the map exists) | 24 | **24** | **untouched** |
| **built districts** | 142 | **127** | **−15** |

Residential was the target because it is the *taper* between the city and the edge, not a
destination — so it never needs two districts of depth. It is now a ring one district
deep. The city core is untouched; the saving comes entirely from land the player only
drives *through*.

### The map edge: why not an air wall

An invisible wall in open, drivable ground is the one solution that always reads as
broken — the player can see road they cannot reach, and the fiction dies at the exact
moment they test it. Better options, in descending order of quality, and what this map
uses:

1. **Impassable terrain — the real answer.** Water and cliffs need no explanation and no
   collision trickery. This map is already bounded that way on **all four sides**:
   sea to the south and east, the Tama river mouth to the south-west, and the annexed
   Okutama massif forming a continuous north-west wall (which is a second reason to
   annex it at 1:1 — real ridges are unclimbable *by shape*, not by rule).
2. **Authored terminal geometry.** Where a road must simply stop, end it *on purpose*:
   a barrier, a construction hoarding, a closed tunnel gate. It costs a prop and reads
   as intentional rather than as a limit.
3. **Soft redirect.** The outer arterial ring curves the player back inward, so the edge
   is rarely approached head-on in the first place.
4. **Non-collidable distant backdrop.** Beyond the playable edge, one always-resident
   low-poly silhouette mesh — no collision, no streaming, no AI, no nav. It buys horizon
   depth for almost nothing and is what makes a 6 km map feel like it sits inside a
   bigger city.

Use an air wall only as a **last-resort backstop behind one of the above** (e.g. just
past the shoreline so a boat-less player cannot swim to the horizon), never as the
primary edge. Two related traps worth naming: **teleport-back** and **instant death at
the border** are both worse than an air wall, because they punish curiosity instead of
quietly redirecting it.

> **Caution specific to this engine.** `build_world.safety_floor()` and the per-district
> ground plane were removed for a good reason (CLAUDE.md): a collision-only floor below
> visual ground silently trapped `Character`/`Player` bodies with no recovery path. Do
> **not** reintroduce a world-spanning invisible plane as an "edge" — vehicles are
> reclaimed below `Y = -30` by `ZoneManager.maintainTraffic`, but characters are not.
> A void district must be genuinely unreachable, not merely floored.

---

## 7. Injecting into the Blender pipeline

`build/tokyo6km/layout.json` is the injection surface. Every coordinate is a **game metre,
centre-origin, X = east, Y = north** — i.e. already in `world_grid.to_world()` space, so
nothing needs converting.

```bash
# 1. pull the real geometry (once). land use covers all 23 wards in 14 tiles;
#    water needs the 2025 per-ward datasets (the 2022 set ships no wtr module).
python3 tools/plateau2json/extract.py \
  --input /data/danilko/plateau_model/13100_tokyo23-ku_2022_citygml_1_2_op \
  --out build/plateau_tokyo6km --layers landuse \
  --bbox 139.641 35.527 139.805 35.737 --origin 139.7671 35.6812

python3 tools/plateau2json/extract.py \
  --input /data/danilko/plateau_model/13100_tokyo23-ku_2022_citygml_1_2_op \
  --out build/plateau_tokyo6km_road --layers road \
  --bbox 139.641 35.527 139.805 35.737 --origin 139.7671 35.6812

python3 tools/plateau2json/extract.py \
  --input /data/danilko/plateau_model/131{01,02,03,08,09,11,13}_*_pref_2025_citygml_1_op \
  --out build/plateau_tokyo6km_water --layers water \
  --bbox 139.641 35.527 139.805 35.737 --origin 139.7671 35.6812

# 2. the design data + the two previews
python3 tools/plateau2json/tokyo6km_build.py   --out build/tokyo6km
python3 tools/plateau2json/tokyo6km_preview.py --out build/tokyo6km/preview.png --size 2400
python3 tools/plateau2json/tokyo6km_real.py    --out build/tokyo6km/real.png    --size 2600
```

`preview.png` is the **schematic** (themes, circuits, rail). `real.png` is the **real
geometry** — organic land, water and the actual street network warped into game space,
with the district grid drawn as a thin overlay to show it is only a container.

> **Extractor fix required and applied:** one source tile
> (`13100_tokyo23-ku_2022 … 53394520_tran_6668_op.gml`) carries a run of NUL bytes
> mid-attribute. `extract.py` aborted the whole run on its `ParseError`, throwing away 300
> already-parsed tiles. It now skips a malformed tile, keeps going, and reports the skips
> in `manifest.json` under `malformed_tiles` — while still streaming (never `list()`-ing a
> tile), so the bounded-memory guarantee holds.

`preview.png` ships a sidecar with `metres_per_pixel` (2.52 at size 2400) and `extent_m`,
same contract as `preview_png.py` — drop it on a 6048 m Blender plane and it lines up 1:1
with every coordinate in `layout.json`.

**Consumption map:**

| consumer | reads |
|:--|:--|
| `blender/lib/world_grid.py` | `grid`, `matrix` → set `GRID_N = 12`, replace `MAP` with the 12 rows |
| `blender/tools/build_world.py` | `tentpoles` → `LANDMARKS` slots; `land` → harbour/island/river blockout |
| `road_kit_authoring` (`ops_placement`/`ops_segment`) | `roads.highways[].points`, `roads.arterials[].points` → node/edge graph per district |
| per-district builders | `districts[].street_rules` → T3/T4 generation + `block_retention` culling |
| `WorldBaker` / traffic | `roads.arterials` → `traffic_route` prefixes; `roads.outer_circuit` → race routes |
| PLATEAU import | `warp.tier_a` (city) / `warp.tier_b` (mountain) to place real extracted geometry |

### Required pipeline changes

1. **`world_grid.py`: `GRID_N` 6 → 12**, `MAP` → the 12 rows in §3. `DISTRICT`, `CELL`,
   `to_world()`, seam naming, `piece_id_for_cell()` are all unchanged — the grid math is
   already `GRID_N`-parametric. `WORLD` becomes 6048, `ORIGIN` 3024.
2. **`BAY_*`/`ISL_*`/`BR_*` constants** in `world_grid.py` are sized for the 3024 m world —
   replace with `layout.json.land` (`tokyo_bay`, `haneda_island`, the R11 Rainbow Bridge
   polyline).
3. **`compress.py --size 6048`**, not the 6144 default, so the compressed PLATEAU extract
   registers exactly on the 12 × 504 m grid.
4. **Streaming radii**: `ZoneManager` load/unload radii were tuned for a 3 km world.
   With 144 districts, re-check `loadRadius ≈ halfExtent + 150` / `unloadRadius ≈
   loadRadius + 150` per `AUTHORING_GUIDE.md` — the *count* changes, the per-district cost
   does not.

### Open action items

- **Okutama DEM is not extracted yet.** Every `assets/world_source/plateau/data/okutama*.json`
  has `terrain: []` / `terrain_total_in_tiles: 0`. Tier B needs real terrain:
  ```
  python3 tools/plateau2json/extract.py \
    --input /data/danilko/plateau_model/13_tokyo_city_2023_citygml_1_op \
    --out build/plateau_json_okutama --layers terrain,road \
    --bbox 139.07 35.79 139.13 35.83 --origin 139.10 35.81
  ```
  Then rigid-translate by `warp.tier_b` (dx/dy/dz) — no scale.
- **Some precinct anchors are mislabelled.** `nakano.json`, `suginami.json`,
  `nishitokyo.json`, `itabashi.json` were all sampled near central Shinjuku (139.69–139.73),
  not at their real ward centroids. This design uses correct projected coordinates
  (`tokyo6km_layout.REAL`) rather than those files; re-extract before using them for
  outer-ward content.
- **A separable X × Y warp cannot give the north-east and the south-east different
  squeezes.** Asakusa's real easting drags it to the waterfront column, which is why
  `R6_UENO` terminates at Ueno. If north-east content is wanted later, that needs a second
  Tier-B annex, not a warp tweak.

# Aim, facing and the camera frame — plan of record

Status as of **2026-09-08** — **W1, W2, W3 and W4 are all closed.** Written to be picked up cold in
a later session.

The gate for everything here is one command:

```bash
/data/danilko/bin/Godot_v4.7.2-stable_linux.x86_64 --headless --path . \
    res://src/main/resources/com/openworld/world/hosts/AimDebugAuto.tscn
```

It presses its own keys and moves its own mouse through the real `Input` singleton, so the shipped
`PlayerController → Character → MovementController → AnimationController` path is what runs. Today
it reports:

```
yaw 12/12 passed, pitch 3/3 passed, strafe 10/10 passed, ai 8/8 passed
AI camera worst 0.8 deg off its aim (limit 6.0) -- was 90.1 before AIM_PLAN.md W3
```

**All four families pass and the stand exits 0**, and nothing in this plan is open.
`AimDebug.tscn` is the same stand with `auto_drive` off, for walking around by hand.

Each family was verified to FAIL when the defect it covers is put back — a gate that passes both
ways proves nothing. Reverting `AnimationController.onSetMovementDirection` to the old frame takes
`strafe` to **0/10** and `ai` to **4/8**; putting the `Pivot` flip back in `Character.tscn` takes
`ai` to **0/8** at 179.3° of camera error.

Two things this stand still cannot see, both checked with one-off probes under
`tools/godot/` conventions and recorded in W3 below: **which shoulder** the TPS boom sits over, and
whether **FPS and TPS agree**. Add a case before trusting either again.

**That is the STOCK Godot binary, and it is the only one to use now** — `blender/tools/env.sh`
holds the same path as the one default every tool script shares. godot-jvm `1.0.0-dev3` ships as
the `addons/jvm/` GDExtension, so the runtime comes from the project; a custom build with the JVM
module compiled in loads it twice and dies with `Version mismatch! C++ module is : 0.17.1-4.7.2 /
Jar is : 1.0.0-dev3` plus every AutoLoad failing to instantiate — which reads as a broken project
rather than a wrong binary.

Companion probe, for the spawn-order question specifically: the same binary with
`--headless --path . --script tools/godot/probe_camera_frame.gd`.

---

## Already fixed (2026-09-08) — do not redo

| symptom | cause | where |
|---|---|---|
| WASD directions rotated 90° in the debug stand | the stand rotated the body AFTER `addChild`, so `_ready()`-time frames were baked at the wrong yaw | `AimDebugHost.spawnPlayer` places before `addChild` |
| the movement frame depended on when `_ready()` ran | `PlayerController` rebuilt it as `getCurrentYaw() + body.rotation.y` | takes it off `ActiveCamera`'s world basis; `getCurrentYaw()` deleted |
| the mesh compensated for a rotation the body no longer had | `MovementController.playerInitRotation` cached in `_ready()` | reads the body's **current** yaw each frame |
| **lower body ~90° off the aim, while AI looked correct** | a scene-exported node reference is a **second JVM wrapper** with all Java fields at defaults, so `getAimTargetPosition()` returned the body's own position and `aimYaw()` fell back to `camRotation` | `MovementController.body()` re-resolves the live instance |
| AI animation LOD never skipped | same stale wrapper on `AnimationController.player` | `AnimationController.lodBody()` |

Full write-up in `CLAUDE.md`, "SOLVED: the player's ~90° body rotation while the AI is correct".

---

## W1 — Crouch and crawl did not aim in elevation — **CLOSED 2026-09-08**

Result: `pitch 3/3`, and the chest now points where the camera looks in every stance.

| stance | FOLLOW before | FOLLOW after | chest-to-view offset after (up / down) |
|---|---:|---:|---:|
| upright | 0.15 *(mis-measured; really 0.94)* | **0.94** | +0.0° / +7.6° |
| crouch  | 0.00 | **0.94** | +0.0° / +7.5° |
| crawl   | 0.00 | **0.96** | +0.0° / +5.8° |

### What was actually wrong — three things, and the plan named one of them

1. **The instrument was measuring the wrong axis.** `AimDebugHost.chestAxis()` took
   `spine_03 → head_2` — a bone PAIR, which is the right instinct for YAW and was carried into the
   pitch cases without re-deriving it. That vector runs UP the neck, and elevation is a useless
   coordinate for a near-vertical vector: tipping the chest 25° moves an up-axis that starts at 82°
   by almost nothing. **Upright was never at 0.15** — with the modifier unlimited its chest tracks
   the target at 0.94 while the up-axis metric reports 0.14 for the very same motion. The gate now
   measures `spine_03`'s own local **+Z**, justified by measurement: in the rest pose that axis
   lands on world −Z (the mesh forward) and `+X` lands exactly on the `clavicle_l − clavicle_r`
   axis, so the basis IS the chest frame for this rig. Note the sign is the OPPOSITE of the
   camera's — a camera looks down −Z, and copying that convention onto the bone read a perfect
   track as FOLLOW −0.94. The up axis is still printed beside it, since an authored pose that
   pitches the whole torso shows there and not in the forward.
2. **The modifier was switched off in crouch and crawl** (the plan's finding 1, and the user-visible
   bug). `Character.tscn` set `spine_aim_max_angle = 0.0` and `updateAimModifiers` read it as a
   boolean, so those stances held whatever the clip authored whatever the player looked at.
3. **`spineAimMaxAngle` is now `spineAimEnabled`, a boolean — after being made a real angle and
   measured.** W1a did push it into the modifier's `use_angle_limitation` /
   `primary_limit_angle` / `secondary_limit_angle`, which works; the measurement is what retired it.
   **The limit is taken from the bone's REST pose**, so what it must cover is the camera's range
   PLUS however far the stance's clip already holds the chest from the aim. Upright FOLLOW by limit:
   60 → **0.47**, 90 → 0.70, 110 → 0.83, 135 → 0.92, off → 0.94. Crawl, whose prone clip starts
   furthest away, needs **300** to reach 0.89 — against an engine default of **360**, i.e. uncapped
   (measured off the node; the damp thresholds are 1.0, so no damping is involved). **A cap loose
   enough not to hurt is indistinguishable from no cap, and it bites hardest exactly where the aim
   needs the most help.** Nothing is lost by dropping it: the modifier runs only in combat, and in
   combat `MovementController.aimYaw()` turns the whole mesh to the aim point (12 yaw cases at
   0.0°), so the grotesque twist a cap would prevent cannot arise. A stance that ever does need
   limiting wants PER-AXIS limits — one symmetric number cannot serve a yaw cap and an elevation
   range at once.

`Character.tscn` now carries no crouch/crawl override (they inherit `true`). **DriveCarrier and Swim
set `spine_aim_enabled = false`** — deliberately, not by omission: the gate covers neither a seated
nor a swimming body, and "this stance does not aim" is exactly what the field now says.

### W1c — crawl: the ELEVATION is fixed, the YAW is the part that needs animation

Crawl now tracks **0.96** with its chest **+0.0°/+5.8°** off the view — as good as upright. That
only became true when the angle cap came off (finding 3): at 135 it read 0.43 with the chest 68°
below the view at the up end, which is what a walk-test would show as "crawl still aims wrong".

**What crawl still needs authored is the YAW.** `CLAUDE.md` records the clip's chest sitting −114°
off its own hips, and a spine look-at provably cannot repair that — the modifier overwrites
`spine_03` only, so its result stays a function of a prone `spine_02` (solving a corrected
`spine_03` into the clip changed the render by exactly zero). That needs a prone aim set: an
aim-offset blendspace in `assets/merged_animation.blend` plus an AnimationTree branch.
`aim_pistol_crawl-loop` is the natural starting point — **which is the reason not to delete the
orphan aim clips.**

Crawl is **not** inert in elevation any more (0.43, and at full depression its chest is within 5.8°
of the view). It saturates at the UP end (−68.0°) because a prone clip starts its chest pitched far
down, so reaching +55° eats the whole limit. `CLAUDE.md`'s "a spine look-at cannot repair crawl" is
still true but is about the **yaw**: the clip's chest sits −114° off its own hips, and the modifier
overwrites `spine_03` only, so its result stays a function of a prone `spine_02`. That half still
needs an authored prone aim set. Elevation no longer does.

### W1d — decided: the spine is the whole mechanism, and the weapon IK is deleted

The plan asked what the spine/arms elevation split should be. It is measured now: with the chest
tracking the view to within **0.0°** at the up end there is no residual for the arms to take. So
`AnimationController.aimIk` (`TwoBoneIK3D`), `weaponIKTarget`, `weaponIKBasePosition` and
`Stance.weaponIKOffset` — `@Export`ed, wired in no scene, read by nothing — are **removed** rather
than wired. No authored data is left without a consumer.

### The crouch aim CLIP needed no fix, and should NOT be deleted (asked during this session)

`assets/merged_animation.blend` does carry `aim_pistol_crouch-loop`, `aim_pistol_crawl-loop` and
their rifle siblings, and they are real stance poses (crouch: pelvis 0.486 vs upright 0.767, knee
bent 98.7° vs 14.7°). But `WeaponBlend` is a **filtered** `Blend2` that takes only clavicles, arms,
hands and fingers from the aim branch — and over those **38 filtered bones the crouch and crawl
clips are bit-identical to the upright clip** (worst rotation 0.0000°, worst translation 0.00000 m).
The author built them by changing only the lower body, which the filter discards. So splitting
`WeaponAim` into a per-stance branch would change the rendered result by exactly zero, and the
crouch aiming defect was entirely finding 2 above.

**Keep them anyway.** They cost two keyframes each, they carry a genuine crouch/prone lower body,
and `aim_pistol_crawl-loop` is where the prone aim set W1c needs would be authored. Deleting them
would remove the starting point for the one piece of this that really is content work. The
`orphan_clips` warning below is the cheaper answer to "why did editing this change nothing".

Two things came out of that dead end and are worth keeping:

- `check_character_anim.py` gained **`orphan_clips`** (WARN): a clip that exists, exports, and is
  named by no AnimationTree node is unreachable, so authoring into it changes nothing while looking
  like a broken animation. It names 13 today, including `crouch_idle`, which `CLAUDE.md` already
  records as a one-line scene fix nobody has made.
- `tools/godot/probe_character_aim.gd` was writing `parameters/AimStanceTransition/…`,
  `WeaponAimCrouch` and `WeaponAimCrawl`, **none of which exist**. `AnimationTree.set()` on an
  unknown parameter is silently ignored, so the probe read as though it were switching a per-stance
  aim branch that has never existed. Fixed, with the reason recorded at the call site.

## W4 — Aiming in stances whose body must not move — **CLOSED 2026-09-08** (crawl measured; drive/swim wired, unmeasured)

**Question:** can `assets/merged_animation.blend` be re-organised so crouch / crawl / swim aim
pistol+rifle work correctly? **Answer: the blend alone cannot do it, and the half it can do is
smaller than it looks. Measure before authoring anything.**

### The constraint that decides this

`WeaponBlend` (`CharacterVisuals_GodotChan.tscn`) is a **filtered `AnimationNodeBlend2`**, and its
filter is exactly: `clavicle_l/r`, `upperarm_l/r`, `lowerarm_l/r`, `hand_l/r` and all 30 finger
bones. **No spine, no neck, no pelvis, no legs.** So of any aim clip, only the ARMS are ever used —
everything else comes from the stance's own locomotion branch, and `spine_03` on top of that is
overwritten outright by the SpineAimModifier.

Two consequences, both measured:
1. **The existing per-stance aim clips are inert by construction.** `aim_pistol_crouch-loop`,
   `aim_pistol_crawl-loop` and their rifle siblings are real stance poses, but over the 38 filtered
   bones they are **bit-identical** to the upright clip (worst rotation 0.0000°). Their crouch and
   prone content lives entirely in bones the filter throws away.
2. **There is no per-stance aim branch to wire them into anyway.** `WeaponAim` is one
   `AnimationNodeBlendSpace1D` holding `aim_pistol` at 0 and `aim_rifle` at 1, and nothing switches
   on stance. (`probe_character_aim.gd` used to write `AimStanceTransition` / `WeaponAimCrouch` /
   `WeaponAimCrawl` — parameters that have never existed; `AnimationTree.set()` ignores an unknown
   parameter silently, which is how it looked like a branch was being switched.)

So authoring a beautiful prone aim pose changes nothing today, twice over.

### If per-stance ARM poses are ever wanted for their own sake (NOT needed for correctness)

1. ~~**ARM THE STAND FIRST.**~~ **Done** — `AimDebugHost.armStand()` instances an AR4 under
   `MarkerAR4`, and `gun-off-aim` is now an assertion. The reasoning is kept because it is the rule:
   `AimDebugAuto` used to report `gun-off-aim n/a (unarmed stand)`, because
   `CharacterVisuals_GodotChan` instances only `Fist` under `WeaponAttachment` — a MeleeItem with
   no `Muzzle`. The gun angle is **the only number that says whether a per-stance aim clip is
   needed at all**: the chest already tracks the aim in every stance, and the muzzle rides a
   `BoneAttachment3D` on `hand_r`, so an arm pose authored for standing is exactly what would show
   up here and nowhere else. Instance AR4 under `MarkerAR4`, equip it through `WeaponController`,
   and turn `gunAimError()` into an assertion. **Do not author clips before this exists** — that is
   the mistake the four inert clips already represent.
2. **Then, only if the gun measures off:** add the branch — `WeaponAim` becomes an
   `AnimationNodeTransition` over one blendspace per stance — and have
   `AnimationController.onSetStance` drive it beside `StanceTransition`, with `onWeaponEquip`
   setting every blendspace's `blend_position`.
3. **Then author the ARMS** in the existing crouch/crawl clips. Nothing else in them will be read.

### Per stance, on today's measurements

- **Crouch — nothing needed, and the user's own read is right.** It shares the upright aim pose and
  measures identically to upright (FOLLOW 0.94, chest 0.0°/7.5° off the view, shoulders-vs-hips
  −0.0°). A crouched torso holding a standing arm pose is anatomically fine; the legs come from
  `CrouchMovementBlend` regardless.
- **Crawl, DriveCarrier, Swim — solved below by `ShoulderAimModifier`, not by clips.** The chest
  measurement that first read "crawl is fine" (FOLLOW 0.96) was measuring direction, not
  plausibility: it was hitting the target by standing the torso up. **The `−114°` chest-vs-hips
  figure quoted in `CLAUDE.md` is from locomotion clips with the modifier OFF and does not describe
  the aiming case at all.**

### The prone target — SOLVED 2026-09-08 by `ShoulderAimModifier`, no clips needed

**The requirement, as stated:** prone elevation via the spine is NOT wanted. The body stays flat on
the ground exactly as the clip authored it — but the **shoulders and head move toward the aim so
the weapon still aims correctly**. Same for DriveCarrier and Swim, which did not aim at all.

**Why the old mechanism could not express it.** `SpineAimModifier` is a `LookAtModifier3D` on
`spine_03` with one strategy: rotate that bone until the chest points at the target. Measured, that
hit the target by standing the torso up — `aim up, crawl` gave chest **+55.0°**, i.e. 55° above
horizontal on a body lying on the ground. Switching it off (the old `spine_aim_max_angle = 0.0`)
is why crawl, DriveCarrier and Swim did not aim at all. It is wrong in KIND, not in degree.

**The observation that made it cheap.** `clavicle_l`, `clavicle_r` and `neck_01` are all **direct
children of `spine_03`**. So the aim rotation the spine modifier would have applied at `spine_03`
can be applied one level down, at those three children, about each child's own origin: the arms —
and therefore `hand_r`, `WeaponAttachment` and the gun's `Muzzle` — and the head swing to the aim
exactly as before, while the spine, pelvis and legs do not move at all.

`character.ShoulderAimModifier` (a custom `SkeletonModifier3D`, since a `LookAtModifier3D` aims a
bone's OWN forward axis and a clavicle's forward runs sideways along the collarbone) computes the
delta the chest would have taken and applies it to those three bones. It sits **after** the spine
modifier in the skeleton's child list, because skeleton modifiers run in tree order.
`Stance.shoulderAimEnabled` selects it, mutually exclusive with `spineAimEnabled` — both would
apply the rotation twice. Crawl, DriveCarrier and Swim now set it.

**Measured on `AimDebugAuto`** (which now arms itself with an AR4, since only `Fist` is instanced in
the visuals and a MeleeItem has no `Muzzle`):

| stance | chest swing | head FOLLOW | gun off aim (up / down) |
|---|---:|---:|---:|
| upright (spine aim) | −122.5° | 0.94 | 1.5° / 5.9° |
| crouch (spine aim)  | −122.6° | 0.94 | 1.5° / 6.0° |
| **crawl (shoulder aim)** | **0.00°** | **0.93** | **1.5° / 6.1°** |

Crawl's spine does not move **at all** across a 130° view swing, its head tracks at 0.93, and its
gun points as accurately as upright's — to within 0.2°. The chest sits at −88.1° in both halves of
the pair, i.e. the prone pose is preserved untouched.

Two things the gate gained with it: a **head FOLLOW** column (a stance passes if EITHER mechanism
aims — what must never happen is neither), and a **gun-off-aim** assertion at 12°, deliberately
generous because it is a COSMETIC bound: the bullet is traced muzzle → sight point, so the shot
converges on the crosshair whatever the barrel's visual angle.

**Not covered by the stand: DriveCarrier and Swim.** They are wired identically to crawl and the
mechanism is stance-independent, but neither has a case here — one needs a vehicle, the other water.
Add them before trusting the numbers for those two.

**No aim-offset clips are needed for this.** The top/normal/bottom set below remains the answer if
per-stance ARM poses are ever wanted for their own sake (a prone firing grip that looks different
from a standing one), but it is now a look-and-feel choice, not a correctness fix — and the
`WeaponBlend` filter already carries exactly clavicles+arms+hands, so it would need no filter
change. **Correction to the previous entry:** I said the filter must widen to spine/neck/head. That
is true only for authoring a TORSO pose — which this requirement explicitly does not want.

### The eye that keeps this from rotting

`check_character_anim.py`'s **`orphan_clips`** WARN lists every exported clip no AnimationTree node
names — 13 today, the four aim clips among them. If a per-stance branch lands, they leave that list;
while they are on it, editing them cannot change the game. **Keep the clips**: they cost two
keyframes each, and `aim_pistol_crawl-loop` is where step 3 would be authored.

---

## W5 — The interactive workbench (`AimWorkbench.tscn`), added 2026-09-08

`AimDebugAuto` presses its own keys and asserts; it cannot show an artist how a pose looks. The
workbench is the same host with `workbench = true` and adds three things a driven test cannot give:
a body that HOLDS a pose while you look at it, an aim point you can put anywhere, and a camera
bolted to neither.

- **The mannequin** is a real `AICharacter` — same `Character`, `MovementController`,
  `AnimationController` and `AICameraController` the game runs — with its `AIController` swapped for
  a `ScriptedInputController`, so it exercises the AI RIG without the AI BRAIN wandering off
  mid-inspection. The Player stands beside it, driven by hand: the two setups, side by side.
- **The aim ball** is a movable sphere. Driving it needs BOTH halves of how an AI aims: the command
  carries the world point (`Character.applyInput` moves the `AimTarget` marker from it) AND
  `AICameraController.setAimTarget` swings the camera rig, which owns the `AimRay` the marker hangs
  off. Measured: with the command alone the gun sat **91.6°** off the ball, because the rig dragged
  the marker back every frame.
- **Free-fly** (`G`) freezes the Player, deliberately: the camera and the body both want WASD, and
  freezing also holds the pose still, which is the point of flying around it. The mannequin keeps
  running so its aim tracks while you circle.

`ScriptedInputController` gained `wantCombat`, `desiredStance`, `desiredWeapon` and
`aimTargetPosition` — the pose half of a `UserCommand`, next to the movement half it already had.

Measured on the mannequin (upright, AR4, ball 8 m ahead): body facing the ball **0.0°**, chest
**5.7°**, head **26°**, gun **3.3°**.

Three traps it surfaced, each now handled in the tool and worth knowing:

1. **Comparing a rotation to a direction.** `meshRoot`'s rotation is LOCAL to the body and the mesh
   is `-Z` FORWARD, so its facing is `body + mesh + 180`. Read raw it said −180° while the character
   faced the ball exactly. (`driveStep` had this right already; the new print did not.)
2. **The first `Muzzle` in a subtree is not the held weapon.** `WeaponAttachment` carries a marker
   per weapon type and more than one can hold an instance. `activeMuzzle()` asks
   `WeaponController.getCurrentWeaponItem()`, falling back to the rifle in `MarkerAR4`.
3. **A weapon switch must finish before a gun angle means anything** — 63.0° mid-transition,
   3.3° settled.

Artist- and designer-facing documentation: **`blender/CHARACTER_AIM_AUTHORING.md`**, which answers
"do I need an aim animation per stance" (no — one per weapon type; aiming is procedural) and lists
the rig contract for a new model.

---

## W6 — Per-stance yaw limits, and what the workbench grew (2026-09-08)

### The yaw cap is per stance, and it is a YAW cap only

`Stance.aimYawLimit` — **upright 80°, crouch 75°, crawl 45°, DriveCarrier 45°, Swim 45°**
(the last two are a judgement call in the same spirit as crawl; nobody has walked them yet).
Symmetric, measured from straight ahead. Elevation is deliberately NOT capped: the camera already
bounds it to −55…+75, and any limit tight enough to be a meaningful chest cap measurably clips
ordinary aiming (a symmetric 60 took upright's chest FOLLOW 0.94 → 0.47).

Two consumers, one number: `SpineAimModifier` gets it as `primary_limit_angle` (the primary
rotation is about the bone's own up axis, i.e. the yaw) with the secondary left at 179°;
`ShoulderAimModifier` clamps it itself.

**Why the shoulder path clamps by hand, and the two wrong frames it cost.** The clamp must be
about the axis that leaves elevation alone, and against a reference that survives a prone body:

1. **In the reference BONE's frame** — wrong. A prone `spine_03` is pitched ~88° face-down, so its
   local x/z plane is nearly the world VERTICAL plane and "yaw" there is world PITCH. Crawl's head
   FOLLOW fell 0.93 → **0.32** on a pure elevation swing.
2. **About world up, with straight-ahead from the Skeleton3D node's basis** — wrong. That basis
   carries the glTF import's own orientation, not the mesh's facing: crawl's gun went **42.8°** off.
3. **About world up, with straight-ahead from the bone's own flattened forward** — wrong. A prone
   chest points at the FLOOR, so its horizontal projection is a near-zero vector whose direction is
   noise: **21.2°** off.
4. **About world up, with straight-ahead from `MeshRoot`** — right. That is the node
   `MovementController` yaws, it is upright in every stance because the prone pose lives in the
   bones, and rotating about world up cannot touch elevation by construction. Back to gun 6.1°,
   head 0.93, chest 0.00.

`ShoulderAimModifier.bodyForwardNode` is that reference, wired by `Character` from
`MeshConfig.meshRootPath`. **Left empty the clamp is SKIPPED**, not applied against a bad frame.

**Not asserted headless yet, and the reason is structural:** in combat `MovementController.aimYaw()`
turns the body to the aim, so a settled case never produces the offset the clamp exists to refuse.
The place it can be seen is a **vehicle seat**, where the body cannot turn — which is why the
workbench now has a car.

### The workbench grew (all user-asked)

- **A car and a pool** (`spawnProps`). DriveCarrier and Swim were the two stances the stand could
  not reach, and "wired the same way so probably fine" is exactly the claim this project keeps
  finding false. Walk the Player in with the game's own keys and the readout covers them.
- **Fire** (`F`) through `UserCommand.fire` — the same field a human's trigger sets, so the
  semi-auto lock, the two-stage muzzle trace, recoil, bloom, the tracer and the GUNSHOT stimulus
  all run. A body can point a gun correctly and still put the shot elsewhere; only pulling the
  trigger separates those.
- **A setup line per body**: which controller drives it, which aim mechanism its stance selects and
  with what yaw cap, what it holds, and whether it is in a vehicle. Nearly every defect in this
  area was a stance selecting the wrong mechanism or a body whose controller was not the one being
  reasoned about.
- **`H` drops the Player** — "free move across without any character". A real mode, not a hidden
  mesh: the Player owns the current camera and the mouse capture, so it is frozen and hidden and
  the fly camera takes over.

`ScriptedInputController` gained `fire` and `reload` beside the pose fields.

### Still open here

- **A NETWORK subject is not implemented.** A `NetworkController` puppet needs a NetworkManager
  session to feed it snapshots; standing one up offline is its own piece of work, not a knob.
- **One body that swaps between player and AI control is UNBLOCKED as of W3, and not built.** It was
  blocked because the two rigs were different SHAPES — `AICharacter.tscn` overrode `Pivot` back to
  identity while the player rig carried two cancelling 180° flips — so a body whose brain was
  swapped with `attachController` would have had the wrong camera. Both flips are gone and the
  override with them, so the rigs are now one shape and the swap should just work. The workbench
  still shows the two setups as two bodies side by side; making it one body with a swappable brain
  is now a workbench feature, not a blocked one.

---

## W2 — The combat strafe blend picked its corner in the wrong frame — **CLOSED 2026-09-08**

Result: `strafe 10/10` on the Player and `ai 8/8` on the AI subject, and both go to 0 when the old
frame is put back.

### What was wrong — two frames, neither of them the right one, and a sign

- **Player:** `AnimationController.worldSpaceMovement` was `false` while `PlayerController` converts
  WASD to world space **at the source**, so the direction arriving here was world-space and was used
  unrotated. The corner was therefore picked off the world compass: walking north played the same
  clip whichever way the character faced. Measured on the stand at a view heading of 0, pressing
  forward selected `(1, 0)` — **`walk_right` for a straight-ahead walk**.
- **AI:** `worldSpaceMovement` was `true`, so it rotated by `-camRotation` — the rig's LOCAL yaw
  under `setAsTopLevel` (see W3), not a world direction. It read as plausible only because
  `AICameraController` drives that same yaw toward the AI's aim yaw.
- **And the whole pair was inverted.** `updateAnimationBlend` negated X (`* -1`) to convert into the
  blendspace's frame, which is `+X = walk_right, +Y = walk_forward`. With the frame corrected that
  negation is a 180° error, so it is gone: the stored pair IS the blend position now.

### The fix

`AnimationController.meshRoot` (wired from `MeshConfig.meshRootPath` by `Character.wireFromMeshConfig`,
beside the `ShoulderAimModifier` wiring that needs the same node for the same reason) — the direction
is projected onto the mesh's own **global** basis, `+X` for right and `-Z` for forward. Global, so the
body's yaw and the mesh's local yaw are both accounted for with no compensation term, and one owner
serves player, AI and puppet alike. Both `worldSpaceMovement` flags are deleted:
`AnimationController`'s is replaced by this, and `MovementController`'s was declared and never read.

**A sign test is not enough, and that is the part the plan did not anticipate.** In combat the mesh
faces the AIM point while the camera sits off the shoulder, so walking straight forward leaves a few
degrees of lateral component whose SIGN is perfectly definite — enough to pick a diagonal clip for a
straight-ahead walk, and to flip it left/right as the parallax changes sides.
`AnimationController.AXIS_DEADZONE` is `sin(22.5°)`, the boundary of the eight-way quantisation the
blendspace's own `snap = (1, 1)` already implies, so each corner owns a 45° wedge.

### What went with it

`AnimationController.onSetCamRotation` and its `camRotation` field are **deleted** — nothing read
them once the frame came off `meshRoot`. So is the `set_cam_rotation → AnimationController`
connection in `Character.tscn` and the `ac.onSetCamRotation(yaw)` call in
`CharacterReplication.applyLocomotion`, whose yaw parameter is gone with it
(`Character.applyReplicatedLocomotion(velocity)`, `NetworkController`). A puppet is if anything
better off: `applyReplicatedFacing` writes `MeshRoot` from the same snapshot **before**
`applyReplicatedLocomotion` runs, so the frame is this tick's, not last tick's.

### The gate cases

Ten `STRAFE` cases: four directions at two view headings (the second deliberately not a round
angle — a FRAME error is invisible wherever the frames coincide), plus two in crouch to catch a
per-stance parameter-path typo. They assert the blend **input**, read straight off
`parameters/<Stance>MovementBlend/blend_position`, not the hips: the upright `walk_left`/`walk_right`
clips carry up to 63° of hip yaw of their own, so a hips reading cannot separate "the wrong clip was
chosen" from "the right clip is authored that way". Same separation the yaw cases make between
`meshRoot` and hips.

Each case re-homes the body first. Eight walk cases at sprint speed already drifted; eighteen would
have walked off the stand's 120 m floor and every later case would have measured a fall.

---

## W3 — One camera convention — **CLOSED 2026-09-08**

Result: the AI camera's error against its own aim point went **90.1° → 0.8°**, TPS and FPS now agree
to a dot product of **1.0000**, and `probe_camera_frame.gd` reports a rig root world yaw of **0.00**
whether the body was rotated before or after `add_child()`. The gate asserts the AI camera now.

### What the old shape was

`TPSCameraController` is `setAsTopLevel(true)`, which detaches the rig from the body but **preserves
its global transform at the instant it is called** — so the rig inherited the body's yaw once and
then floated free, and `controlRotation.yaw` was a LOCAL yaw whose parent frame was "whatever the
body's yaw was when `_ready()` ran". On top of that the chain carried **two cancelling 180° Y
flips** (the rig node's own transform and `Pivot`'s), and `AICharacter.tscn` overrode `Pivot` back
to identity — so the player and AI rigs were literally different shapes, and the second flip also
turned `Rot_x(pitch)` into `Rot_x(-pitch)`, which is why positive pitch meant look DOWN and
`applyRecoil` had to subtract to kick upward.

### What changed

1. **The rig states its world basis every frame** — `setGlobalRotation(ZERO)` in
   `TPSCameraController._physicsProcess`, before the positioning block that reads `yawNode`'s global
   basis, and the same line in `FPSCameraController`. This alone took the AI camera 90.1° → 0.8°,
   because setting the world basis absolutely overrides the rig node's own flip as well as the
   frozen frame.
2. **Both flips are out of `Character.tscn`** — TPS root, TPS `Pivot`, FPS root, FPS `Pivot`, all
   four now identity basis with their offsets kept. `AICharacter.tscn`'s `Pivot` override is deleted
   because the base is now identical to it: **one rig shape.**
3. **Positive pitch means LOOK UP**, written down on `ControlRotation`. Every owner of that sign
   moved together: `pitchMin`/`pitchMax` become −75/+55 (the same view range, said the right way
   round), `TPSCameraController.applyRecoil` and `Character.applyRecoil` add instead of subtract,
   and `PlayerCameraController._input` subtracts the mouse's downward-growing Y.
4. **`AICameraController` has no compensation term left** — `targetPitchDeg` is `+atan2(dy, hDist)`,
   and the no-aim-target fallback tracks `Character.getFacingYaw()` (where the MESH points) instead
   of a negated body yaw, which was never a facing at all.
5. `applyRecoil`'s sign comment is gone with the flips, as planned.

### Two deliberate deviations from the plan as written

- **Step 2 (`PlayerController`'s movement frame becomes `controlRotation.yaw` directly) was NOT
  done.** It reads `ActiveCamera`'s world basis, and after W3 the two agree — but `ActiveCamera` is
  the one node EVERY mode writes (TPS, FPS, and the vehicle seat, whose camera does not touch
  `ControlRotation` at all), so it is the better owner of "which way is the human looking", not a
  redundant second derivation. Narrowing it to the character rig's own state would trade a general
  answer for a specific one.
- **The VEHICLE rig is untouched, on purpose.** `VehicleCameraController` has its own
  `yaw`/`pitch`/`pitchMin`/`pitchMax`, its own scene under `Vehicle.tscn`, and still carries the
  `Pivot` flip — so positive pitch is still DOWN there and its `applyRecoil` still subtracts. It
  shares no state with `ControlRotation`, so it is self-consistent as it stands. A note at its
  `applyRecoil` says so, because matching one half of it to the character convention is how a sign
  bug is born.

### The one behaviour change, and it is visible

**The camera swapped shoulders, and it had to be put back in the data.** The shoulder offset is
applied along the `Yaw` node's X; under the old rig that axis pointed *opposite* the camera's own
right, so a POSITIVE `cameraShoulderOffset` produced a LEFT-shoulder camera. Removing the flip makes
the axis honest and mirrors the framing. Measured both ways with a probe
(`yawRight · camRight`: **−1.000 before, +1.000 after**), so the authored values in
`combatstates/*.tres` were negated to keep the side the game ships with — camera displaced
**−0.149 m** along its own right, i.e. still the left shoulder. The sign now means something
(positive = right), and flipping it back is one character per state.

### What the gate could not see, and how it was checked instead

Two one-off probes, kept in the scratch record rather than the repo because they answer questions
the driven stand asks better:

- **The boom.** With a floor under the body: camera **3.08 m** from it, **1.80 m** above, looking at
  it, `AimTarget` ahead of it. A probe without a floor reads nonsense — the body falls and the
  camera's follow-lerp lags, which shows up as a camera "above and behind" that is really just late.
- **One convention, both modes.** Toggling FPS through the real `view` action (not by writing
  `is_fps_mode`, which is a plain Java field and not a registered property — `body.get()` returns
  `<null>`, and a probe that does not check that is silently vacuous): TPS forward vs FPS forward
  **dot = 1.0000**, positions **2.45 m** apart. That is the whole "one convention" claim, measured.

**A third trap, twice:** `atan2(x, z)` is the `+Z`-forward convention and every camera and mesh yaw
in this codebase is Godot's `atan2(-dx, -dz)`. A camera with a world yaw of 0 reads as **180** under
the first. It made a correct rig look 180° wrong once in this session and made the AI camera look
"convergent but spinning" earlier. `AimDebugHost.yaw()` uses the `+Z` form and gets away with it
only because every assertion there is a DIFFERENCE of two yaws taken with the same helper.

---

## Order

~~`W1`~~ → ~~`W4`~~ → ~~`W2`~~ → ~~`W3` step 1 (AI cases)~~ → ~~`W3` steps 1–5~~. **All closed.**

What is left in this area is **content, not code**, and unchanged by any of it: the per-corner hip
swings in `CLAUDE.md`'s table, and crawl's −114° chest-vs-hips YAW, which needs an authored prone
aim set (`aim_pistol_crawl-loop` is the starting point — which is why the orphan aim clips are kept).

Two gaps in the instrument, worth closing before the next change in here: the stand has **no case
for DriveCarrier or Swim** (one needs a vehicle, the other water — the workbench has both, the
driven stand has neither), and **no case for the shoulder side or for FPS**, both of which W3 moved
and both of which were checked with throwaway probes.

Still content, not code, and unchanged by W1: the per-corner hip swings in `CLAUDE.md`'s table, and
crawl's −114° chest-vs-hips yaw.

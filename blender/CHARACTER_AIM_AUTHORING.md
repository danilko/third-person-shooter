# Authoring a character for aiming and shooting

Who this is for: an artist or designer bringing in a **new character model**, or changing the
existing one's weapon poses. It answers one question first, because it decides how much work the
rest is:

> **Do I need a separate aim animation per stance?**
> **No.** Aiming is procedural. You author **one aim pose per weapon type** and the engine points
> the body at the target — in every stance, for the player and the AI alike.

Everything here is measured on the shipped rig by
`world/hosts/AimDebugAuto.tscn` (the gate) and `world/hosts/AimWorkbench.tscn` (the interactive
stand). Numbers quoted are from those, not from intent.

---

## 1. What the engine does for you

Two skeleton modifiers do the aiming at runtime. Neither needs a clip.

| stance | mechanism | what moves |
|---|---|---|
| Upright, Crouch | `SpineAimModifier` (`LookAtModifier3D` on `spine_03`) | the chest turns to the target; arms and head ride along |
| Crawl, DriveCarrier, Swim | `ShoulderAimModifier` | the **clavicles and neck** take the same rotation; **the spine does not move at all** |

The second one exists because a prone or seated body must not stand its torso up to look at
something. `clavicle_l`, `clavicle_r` and `neck_01` are all direct children of `spine_03`, so
applying the aim rotation to them instead moves the arms, the weapon and the head while the spine,
pelvis and legs stay exactly as you posed them.

Which one a stance uses is authored in `Character.tscn` on the stance node —
`spine_aim_enabled` / `shoulder_aim_enabled`, exactly one of them true. Set **both false** for a
stance that must not aim at all.

`aim_yaw_limit` on the same node caps how far off straight-ahead the aim may twist the body, so the
spine and neck cannot reach an angle no person does: **upright 80°, crouch 75°, crawl / seated /
swimming 45°**. It is a YAW cap only — elevation is already bounded by the camera (−55…+75), and a
cap tight enough to matter there measurably clips ordinary aiming.

**Consequence for you:** `aim_pistol` and `aim_rifle` are used in *every* stance. The four
per-stance aim clips that exist in `merged_animation.blend` (`aim_pistol_crouch-loop` and
siblings) are **not used by anything** — over the bones the aim layer actually reads they are
bit-identical to the upright clip. `check_character_anim.py` lists them under `orphan_clips`.

---

## 2. The aim layer reads ONLY the arms

`WeaponBlend` in `CharacterVisuals_GodotChan.tscn` is a **filtered** `AnimationNodeBlend2`. From the
aim pose it takes exactly:

> `clavicle_l/r`, `upperarm_l/r`, `lowerarm_l/r`, `hand_l/r`, and all 30 finger bones.

**No spine, no pelvis, no legs, no head.** Everything else comes from the stance's own locomotion
clip. So in an aim pose, only the arms and hands matter — pose the rest however you like, it is
discarded. This is the single most common surprise: a beautiful crouched aim pose changes nothing,
because its crouch lives in bones the filter throws away.

---

## 3. What you actually author

Per weapon type, ONE clip:

- `aim_<weapon>-loop` — the two-handed hold, arms only, facing straight ahead and level.
- `idle_<weapon>-loop`, `on_air_<weapon>-loop`, `weapon_switch_<weapon>`, `reload` — the
  non-aim poses, which follow the existing clips' pattern.

Rules that are load-bearing:

- **Author the aim pose LEVEL and FORWARD.** The modifier rotates from wherever you left it, so a
  pose that already points somewhere adds a permanent offset to every shot's visual.
- **The `Root` bone is a shared origin, not a per-clip pose.** Every clip in one blendspace ring
  must agree on `Root` — it sits above `pelvis`, so a translation there moves the whole skeleton,
  the gun and every hitbox together, and reads in game as a lurch. `check_character_anim.py`'s
  `root_family` and `gun_travel` checks fail on this.
- **One NLA track per clip, and no active action.** An action that is both active and stashed
  exports twice and merges into one clip with doubled channels.
- The glTF clip name is the ACTION name, and Godot strips a trailing `-loop` (that suffix is what
  sets loop mode).

---

## 4. Bringing in a NEW model

The rig contract is **bone names**, not a specific skeleton. A model of similar proportions needs:

| what | why |
|---|---|
| `spine_03` with `+Z` out of the sternum and `+X` along `clavicle_l - clavicle_r` | both aim modifiers derive the aim rotation from this bone's frame. **Measure it, do not assume** — a camera looks down `-Z`, a bone here does not. |
| `clavicle_l`, `clavicle_r`, `neck_01` as **direct children of `spine_03`** | `ShoulderAimModifier` applies the aim one level down; if they hang elsewhere the spine will move. |
| `head_2`, `thigh_l/r`, `hand_r` | the gate measures through these. |
| a `WeaponAttachment` `BoneAttachment3D` on `hand_r`, with a `Marker<WEAPON>` per weapon | the weapon and its `Muzzle` — the shot's origin — hang off it. |
| the mesh **facing `-Z`** | `MovementController.aimYaw`'s `atan2(-dx, -dz)` is the same fact. |

Different bone names are fine but must be told to the engine: `MeshConfig` (the Resource on the
visuals scene) carries the paths — `aimSpineModifierPath`, `shoulderAimModifierPath`,
`physicalBoneSimulatorPath`, `weaponAttachmentPath`, `meshRootPath` and the per-stance colliders.
`ShoulderAimModifier` itself exports `referenceBone` and `drivenBones`, so a rig that calls its
collarbones something else needs no code change.

**Proportions that differ a lot** (a child, a quadruped, a mech) are where this stops being free:
the aim pose is authored once and the modifier only rotates. Re-author `aim_<weapon>-loop` for that
skeleton; you still do not need one per stance.

---

## 5. How to check your work

```bash
# 1. export, then gate the export
blender -b assets/merged_animation.blend --python blender/tools/export_character.py
python3 blender/tools/check_character_anim.py

# 2. see it move -- interactive
/data/danilko/bin/Godot_v4.7.2-stable_linux.x86_64 --path . \
    res://src/main/resources/com/openworld/world/hosts/AimWorkbench.tscn

# 3. assert it -- headless, exits non-zero on a regression
/data/danilko/bin/Godot_v4.7.2-stable_linux.x86_64 --headless --path . \
    res://src/main/resources/com/openworld/world/hosts/AimDebugAuto.tscn
```

**The workbench** puts a Player (driven by your hands) beside a **mannequin** — a real
`AICharacter` with its brain swapped for a scripted controller, so it holds a pose instead of
wandering off — and a movable pink **aim ball** they track.

```
1 / 2 / 3   mannequin stance: upright / crouch / crawl
4           combat pose on/off        Q / E   weapon slot
F           hold fire                 I J K L / U O   move the aim ball
G           free-fly camera (freezes the Player, since both want WASD)
              while flying: WASD + R/F, mouse look, Shift = fast
H           drop the Player entirely -- fly around with no character at all
Player      WASD + mouse + right-mouse to aim -- your own hands
            walk into the CAR or the POOL to reach DriveCarrier / Swim
```

The overlay prints a **setup line per body** — which controller drives it, which aim mechanism its
stance selects and with what yaw cap, what it is holding, and whether it is in a vehicle.

The overlay and the console print the numbers that matter: the mesh's facing vs the ball, the
**chest** off the ball (is the aim modifier working), the **head** off it, and **gun-off-aim** —
the angle between the barrel and the target.

**What good looks like**, measured on the shipped rig — the gate (Player, a 130° view swing):

| | upright | crouch | crawl |
|---|---:|---:|---:|
| chest follows the swing | 0.94 | 0.94 | **0.00** (spine must not move) |
| head follows | 0.94 | 0.94 | 0.93 |
| gun off aim (up / down) | 1.5° / 5.9° | 1.5° / 6.0° | 1.5° / 6.1° |

and the workbench mannequin (AI rig, upright, holding an AR4, ball 8 m ahead): body facing the ball
to **0.0°**, chest **5.7°**, head **26°**, gun **3.3°**.

A **gun-off-aim** above 12° fails the gate. Note it is a *cosmetic* bound: the bullet is traced from
the muzzle to the crosshair's world point, so the shot lands on the crosshair whatever the barrel's
visual angle. What the number catches is arms that do not carry the weapon to the aim at all.

Two things to know before you trust a gun reading:

- **It is the HELD weapon's muzzle**, from `WeaponController.getCurrentWeaponItem()`. A depth-first
  hunt for any `Muzzle` finds a stowed gun in another marker instead — that read 91.6° on a body
  whose chest was 4.6° off. When the body holds something with no muzzle (slot 0 is the **fist**),
  it falls back to the rifle parked in `MarkerAR4`, which is the authored AR4 hold offset and hangs
  off the same `hand_r` attachment, so it is still a fair measurement.
- **Let a weapon switch finish before reading.** Mid-transition the same mannequin measured 63.0°
  and settled to 3.3°.

---

## 6. Known gaps, so you do not chase them

- **DriveCarrier and Swim** now have a car and a pool in the workbench, but nobody has walked them
  yet, and their 45° yaw caps are a judgement call rather than a measurement.
- **Crawl's YAW** still comes from the clip: the prone clips hold the chest ~114° off their own
  hips. Elevation is solved; a prone body twisting to face sideways is not.
- **Per-stance ARM poses are a look-and-feel option, not a fix.** If you want a prone firing grip
  that reads differently from a standing one, that needs a per-stance branch in the AnimationTree
  (`WeaponAim` is one blendspace today, with no stance switch). The filter already carries the
  arms, so no filter change would be needed — but nothing is broken without it.

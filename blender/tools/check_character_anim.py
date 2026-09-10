#!/usr/bin/env python3
"""Gate for the character animation export. Pure Python on the .glb — no Blender needed.

    python3 blender/tools/check_character_anim.py

Checks the facts that were each a shipped defect (PLAN.md "Crouch/crawl aiming, the crouch
'knock', and the 90-degree-off torso"), so a re-export cannot quietly reintroduce them:

  duplicate_clips   an action exported twice (active + stashed) comes back as ONE animation with
                    doubled channels; every clip must carry exactly BONES*3 channels.
  root_family       every clip in one stance family must agree on the Root bone. Root sits above
                    the pelvis, so a translation on it moves the whole skeleton -- and with it
                    WeaponAttachment (a BoneAttachment3D on hand_r, i.e. the gun, its Muzzle which
                    is FirearmItem.resolveShotOrigin's shot origin) and every PhysicalBone3D
                    hitbox. Points of one blendspace that disagree on Root translate the body,
                    the gun and the hitboxes together: the "knock".
  gun_travel        the same fact measured where it is felt -- hand_r world position across the
                    family, including idle->walk (a blendspace centre that disagrees with its own
                    corners) and the bob inside a single loop (a transition baked into a loop).
  gun_sway          the LATERAL half of that, which the vertical checks scored as PASS while the
                    gun swung 0.14 m side to side: the walk clips' spine_01/spine_02 counter-rotate
                    the pelvis twist, so an upper-body filter that replaces them with a static aim
                    pose lets the twist through into the gun. Asked of FORWARD/BACK clips only --
                    a strafe swings the body for real, a forward walk has nothing to swing about.
  feet_planted      a clip whose feet leave the floor is a Root offset applied without re-posing
                    the legs; the repair for a Root disagreement must not become this.
  tree_refs         every clip CharacterVisuals_GodotChan.tscn names must exist in the export.
  orphan_clips      and the reverse: a clip that exists and is named by no AnimationTree node is
                    unreachable, so authoring into it changes nothing while looking like a broken
                    animation. WARN — an unused clip is a normal work-in-progress state.

Exit code 1 on any ERROR. Thresholds are deliberately a little above the measured-good values,
so ordinary re-authoring passes and a structural regression does not.
"""
import json
import math
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GLB = os.path.join(ROOT, "assets", "merged_animation.glb")
if len(sys.argv) > 1:                      # optional: check some other export (e.g. a backup)
    GLB = sys.argv[1]
TSCN = os.path.join(ROOT, "src", "main", "resources", "com", "openworld",
                    "character", "CharacterVisuals_GodotChan.tscn")

# One entry per blendspace RING: the clips inside a ring are blended continuously, so they must
# share a Root baseline. A ring may legitimately sit at a different baseline from another ring
# (a sprint really does run lower than a walk), which is why sprint is its own entry.
#   aim      the stance's aim clip; the gun measurement composes it over the locomotion clip
#            exactly as the AnimationTree's filtered upper-body layer does.
#   bob/foot per-ring limits — a run bobs and lifts its feet far more than a walk, and saying so
#            here is honest, whereas one global number would either miss the walk defect or
#            reject every run.
FAMILIES = {
    "upright": dict(
        clips=["upright_idle", "upright_walk_forward", "upright_walk_back", "upright_walk_left",
               "upright_walk_right", "upright_walk_forward_left", "upright_walk_forward_right",
               "upright_walk_back_left", "upright_walk_back_right"],
        aim="upright_aim_pistol", bob=0.08, foot=0.30),
    "upright_sprint": dict(
        clips=["upright_run_forward", "upright_run_forward_rifle"],
        aim="upright_aim_rifle", bob=0.40, foot=0.85),
    "crouch": dict(
        clips=["crouch_idle", "crouch_walk_forward", "crouch_walk_back",
               "crouch_walk_left", "crouch_walk_right", "crouch_walk_forward_left",
               "crouch_walk_forward_right", "crouch_walk_back_left", "crouch_walk_back_right"],
        aim="crouch_aim_pistol", bob=0.08, foot=0.30),
    "crawl": dict(
        clips=["crawl_idle", "crawl_forward", "crawl_back", "crawl_left", "crawl_right"],
        aim="crawl_aim_pistol", bob=0.08, foot=0.30),
    # One clip, and that is the shape of the ring rather than an omission: a seated occupant does
    # not locomote (MovementController is switched off in the seat), so DriveCarrier's blendspace
    # plays the one pose at all five points. The ring checks still earn their keep on it -- a seated
    # placeholder must still close its loop and must not swing the gun.
    "drive": dict(
        clips=["drive_idle"],
        aim="drive_aim_pistol", bob=0.08, foot=0.30),
    # SWIM has no AnimationTree state yet -- the stance ships with animationStanceKey = "Crawl" and
    # borrows that ring. These are placeholders so the ring EXISTS to be authored and is measured
    # from the day it is; wiring a Swim blendspace is the change that makes them reachable.
    "swim": dict(
        clips=["swim_idle", "swim_forward", "swim_back", "swim_left", "swim_right"],
        aim="swim_aim_pistol", bob=0.08, foot=0.30),
}

ROOT_BASELINE_MAX = 0.06  # m, spread of per-clip MEAN Root.y inside one ring (good: <= 0.03)
LOOP_CLOSE_MAX    = 0.02  # m, |Root.y(first) - Root.y(last)| (good: <= 0.007)
GUN_STEP_MAX      = 0.07  # m, idle -> moving centre, composed pose (good: <= 0.064, the strafe
                          #    clips legitimately shift the hips; the crouch defect was 0.126)
GUN_SWAY_MAX      = 0.10  # m, LATERAL travel of the gun within one loop, FORWARD/BACK clips
                          #    only -- a strafe legitimately swings the body (walk_right measures
                          #    0.109 clean), while a forward walk has no lateral motion to justify
                          #    any. Clean, against the committed arms-only filter: 0.012-0.082.
                          #    With the upper-body filter widened to spine_01/spine_02 (a shipped
                          #    regression, reverted): 0.123-0.223. The limit separates the two.
                          #    Separate from the
                          #    vertical bob on purpose: widening the upper-body filter to
                          #    spine_01/spine_02 froze the counter-rotation those bones carry
                          #    against the pelvis twist and took upright walk from 0.015 m to
                          #    0.140 m of side-to-side gun swing -- a regression the height-only
                          #    checks scored as PASS.

findings = []


def err(check, msg):
    findings.append(("ERROR", check, msg))


def warn(check, msg):
    findings.append(("WARN", check, msg))


# ----------------------------------------------------------------- glTF reader
def load(path):
    d = open(path, "rb").read()
    _, _, length = struct.unpack("<III", d[:12])
    off, chunks = 12, {}
    while off < length:
        clen, ctype = struct.unpack("<II", d[off:off + 8])
        chunks[ctype] = d[off + 8:off + 8 + clen]
        off += 8 + clen + ((-clen) % 4)
    return json.loads(chunks[0x4E4F534A]), chunks[0x004E4942]


CT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
      5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Rig:
    def __init__(self, J, BIN):
        self.J, self.BIN = J, BIN
        self.nodes = J["nodes"]
        self.parent = {}
        for i, n in enumerate(self.nodes):
            for c in n.get("children", []):
                self.parent[c] = i
        self.bone = {}
        for i in J["skins"][0]["joints"]:
            self.bone.setdefault(self.nodes[i].get("name"), i)
        self.anim = {a["name"]: a for a in J.get("animations", [])}

    def acc(self, i):
        a = self.J["accessors"][i]
        bv = self.J["bufferViews"][a["bufferView"]]
        fmt, sz = CT[a["componentType"]]
        n = NC[a["type"]]
        stride = bv.get("byteStride") or sz * n
        base = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        return [struct.unpack_from("<" + fmt * n, self.BIN, base + k * stride)
                for k in range(a["count"])]

    def duration(self, name):
        d = 0.0
        for ch in self.anim[name]["channels"]:
            ts = [t[0] for t in self.acc(self.anim[name]["samplers"][ch["sampler"]]["input"])]
            if ts:
                d = max(d, ts[-1])
        return d

    def pose(self, name, t):
        out = {}
        for ch in self.anim[name]["channels"]:
            tgt = ch["target"]
            smp = self.anim[name]["samplers"][ch["sampler"]]
            times = [x[0] for x in self.acc(smp["input"])]
            vals = self.acc(smp["output"])
            k = 0
            for idx, tv in enumerate(times):
                if tv <= t + 1e-6:
                    k = idx
            out.setdefault(tgt.get("node"), {})[tgt["path"]] = vals[k]
        return out

    @staticmethod
    def _trs(t, r, s):
        x, y, z, w = r
        xx, yy, zz = x * x, y * y, z * z
        xy, xz, yz = x * y, x * z, y * z
        wx, wy, wz = w * x, w * y, w * z
        R = [[1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
             [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
             [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)]]
        return [[R[i][j] * s[j] for j in range(3)] + [t[i]] for i in range(3)] + [[0, 0, 0, 1]]

    def _rest(self, i):
        n = self.nodes[i]
        if "matrix" in n:
            m = n["matrix"]
            return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
        return self._trs(n.get("translation", [0, 0, 0]),
                         n.get("rotation", [0, 0, 0, 1]), n.get("scale", [1, 1, 1]))

    def world(self, bone, pose):
        i, chain = self.bone[bone], []
        while i is not None:
            chain.append(i)
            i = self.parent.get(i)
        M = [[1 if r == c else 0 for c in range(4)] for r in range(4)]
        for i in reversed(chain):
            p, n = pose.get(i), self.nodes[i]
            if p:
                L = self._trs(p.get("translation", n.get("translation", [0, 0, 0])),
                              p.get("rotation", n.get("rotation", [0, 0, 0, 1])),
                              p.get("scale", n.get("scale", [1, 1, 1])))
            else:
                L = self._rest(i)
            M = self.mm(M, L)
        return M

    @staticmethod
    def mm(A, B):
        return [[sum(A[i][k] * B[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def samples(rig, clip, n=13):
    d = rig.duration(clip)
    return [rig.pose(clip, k / (n - 1) * d if d else 0.0) for k in range(n)]



def tscn_upper_body_filter():
    """Bones the AnimationTree's WeaponBlend layer takes from the aim clip.

    Read from the scene rather than restated here: this list IS the layer boundary, and a gate
    holding its own copy would be the second owner of it (CLAUDE.md's recurring defect).
    """
    if not os.path.exists(TSCN):
        return set()
    s = open(TSCN).read()
    m = re.search(r'^nodes/WeaponBlend/node = SubResource\("([^"]+)"\)', s, re.M)
    if not m:
        return set()
    blk = s.split(f'[sub_resource type="AnimationNodeBlend2" id="{m.group(1)}"]')[1].split("\n\n")[0]
    return {p.split(":")[-1] for p in re.findall(r'"([^"]*Skeleton3D:[^"]+)"', blk)}


def compose(rig, loco_pose, aim_clip, upper):
    """The pose the game actually shows: locomotion, with `upper` replaced by the aim clip."""
    if not aim_clip or not upper:
        return loco_pose
    out = dict(loco_pose)
    for ni, v in rig.pose(aim_clip, 0.0).items():
        if rig.nodes[ni].get("name") in upper:
            out[ni] = v
    return out


# ----------------------------------------------------------------- the checks
def main():
    if not os.path.exists(GLB):
        print(f"check_character_anim: missing {GLB}", file=sys.stderr)
        return 2
    J, BIN = load(GLB)
    rig = Rig(J, BIN)
    nbones = len(J["skins"][0]["joints"])
    expect = nbones * 3

    # -- duplicate_clips ----------------------------------------------------
    for a in J["animations"]:
        if len(a["channels"]) != expect:
            err("duplicate_clips",
                f"{a['name']!r} has {len(a['channels'])} channels, expected {expect} "
                f"({nbones} bones x 3). A count that is an exact multiple means the action was "
                f"exported more than once (active action + stash); keep one NLA track per clip "
                f"and no active action.")

    def clip(name):  # importer strips -loop
        return name + "-loop" if name + "-loop" in rig.anim else name

    # -- root_baseline / loop_transition / gun_travel / feet_planted --------
    # The upper-body layer boundary, read off the scene so the gate and the
    # AnimationTree cannot disagree about which bones the aim clip supplies.
    upper = tscn_upper_body_filter()
    ri = rig.bone["Root"]

    for fam, spec in FAMILIES.items():
        present = [(m, clip(m)) for m in spec["clips"] if clip(m) in rig.anim]
        for m in spec["clips"]:
            if clip(m) not in rig.anim:
                warn("root_baseline", f"{fam}: clip {m!r} is not in the export")
        if not present:
            continue
        aim = clip(spec["aim"]) if clip(spec["aim"]) in rig.anim else None
        if aim is None:
            warn("gun_travel", f"{fam}: aim clip {spec['aim']!r} missing; gun check skipped")

        stats = {}
        for label, c in present:
            ps = samples(rig, c, 17)
            ys = [p.get(ri, {}).get("translation", [0, 0, 0])[1] for p in ps]
            composed = [compose(rig, p, aim, upper) for p in ps] if aim else ps
            stats[label] = dict(
                mean=sum(ys) / len(ys),
                close=abs(ys[0] - ys[-1]),
                gun=[rig.world("hand_r", p)[1][3] for p in composed],
                gun_x=[rig.world("hand_r", p)[0][3] for p in composed],
                gun_z=[rig.world("hand_r", p)[2][3] for p in composed],
                foot=max(max(rig.world(b, p)[1][3] for p in ps) for b in ("ball_l", "ball_r")),
            )

        # a ring must share one Root baseline -- a per-clip offset translates the whole
        # skeleton, and with it the gun, its Muzzle and every PhysicalBone3D hitbox
        lo = min(stats, key=lambda k: stats[k]["mean"])
        hi = max(stats, key=lambda k: stats[k]["mean"])
        spread = stats[hi]["mean"] - stats[lo]["mean"]
        if spread > ROOT_BASELINE_MAX:
            err("root_baseline",
                f"{fam}: Root.y baseline spans {spread:.3f} m inside one blendspace ring "
                f"(limit {ROOT_BASELINE_MAX:.2f}) -- {lo!r} at {stats[lo]['mean']:.3f} vs "
                f"{hi!r} at {stats[hi]['mean']:.3f}.")

        # a loop whose first frame is not its last is a stance TRANSITION baked into a
        # looping clip; it then plays that transition forever
        for label, st in stats.items():
            if st["close"] > LOOP_CLOSE_MAX:
                err("loop_transition",
                    f"{fam}: {label!r} does not close -- Root.y differs by {st['close']:.3f} m "
                    f"between its first and last frame (limit {LOOP_CLOSE_MAX:.2f}). Find the "
                    f"frame that matches the last one and trim everything before it.")

        # the same fact where it is felt: the gun, on the pose that actually ships
        # Compare CENTRES, not floors: a gait legitimately bobs, and the idle clip does not,
        # so a floor-to-floor comparison just reports half the walk's own bob. What must not
        # differ is where the gun sits on average -- that is the step you feel on the first
        # frame of movement (the shipped crouch defect measured 0.126 m here).
        idle = present[0][0]
        centre = lambda l: sum(stats[l]["gun"]) / len(stats[l]["gun"])
        base = centre(idle)
        for label in stats:
            step = abs(centre(label) - base)
            if label != idle and step > GUN_STEP_MAX:
                err("gun_travel",
                    f"{fam}: gun centre shifts {step:.3f} m from {idle!r} to {label!r} "
                    f"(limit {GUN_STEP_MAX:.2f}).")
            bob = max(stats[label]["gun"]) - min(stats[label]["gun"])
            if bob > spec["bob"]:
                err("gun_travel",
                    f"{fam}: gun travels {bob:.3f} m inside {label!r} alone "
                    f"(limit {spec['bob']:.2f} for this ring).")
            sway = max(max(stats[label]["gun_x"]) - min(stats[label]["gun_x"]),
                       max(stats[label]["gun_z"]) - min(stats[label]["gun_z"]))
            lateral = "left" in label or "right" in label
            if not lateral and sway > GUN_SWAY_MAX:
                err("gun_sway",
                    f"{fam}: gun swings {sway:.3f} m sideways inside {label!r} (limit "
                    f"{GUN_SWAY_MAX:.2f}) -- the torso is not absorbing the pelvis twist. "
                    f"Check what the upper-body filter takes from the aim clip: a STATIC "
                    f"spine_01/spine_02 replaces the counter-rotation the walk clip animates.")
            if stats[label]["foot"] > spec["foot"]:
                err("feet_planted",
                    f"{fam}: {label!r} lifts a foot to {stats[label]['foot']:.3f} m (limit "
                    f"{spec['foot']:.2f}) -- a Root offset applied without re-posing the legs.")

    # -- tree_refs ----------------------------------------------------------
    if os.path.exists(TSCN):
        imported = {a[:-5] if a.endswith("-loop") else a for a in rig.anim}
        refs = sorted(set(re.findall(r'^animation = &"([^"]+)"',
                                     open(TSCN).read(), re.M)))
        for r in refs:
            if r not in imported:
                err("tree_refs", f"AnimationTree references clip {r!r}, absent from the export.")
        print(f"  tree_refs: {len(refs)} clips referenced by the AnimationTree")

        # -- orphan_clips ---------------------------------------------------
        # The other direction, which cost a whole investigation: a clip that exists, exports, and
        # is named by NOTHING. Authoring into one changes the game by exactly zero, and it looks
        # like the animation is broken rather than unused. That is what aim_pistol_crouch-loop and
        # its three siblings are today -- real crouch/prone poses whose arms (the only bones
        # WeaponBlend's filter takes from the aim branch) are bit-identical to the upright clip's,
        # so the AnimationTree's single upright WeaponAim blendspace loses nothing by ignoring
        # them. WARN, not ERROR: an unused clip is a normal state for work in progress.
        orphans = sorted(imported - set(refs))
        if orphans:
            warn("orphan_clips",
                 f"{len(orphans)} exported clip(s) referenced by no AnimationTree node, so "
                 f"editing them cannot change the game: {', '.join(orphans)}")

    # -- report -------------------------------------------------------------
    print(f"check_character_anim: {len(J['animations'])} clips, {nbones} bones, "
          f"{len(FAMILIES)} blendspace rings")
    for level, check, msg in findings:
        print(f"  {level} [{check}] {msg}")
    bad = sum(1 for f in findings if f[0] == "ERROR")
    print(f"  {'FAIL' if bad else 'PASS'} — {bad} error(s), "
          f"{len(findings) - bad} warning(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

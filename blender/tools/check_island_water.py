"""check_island_water.py -- IS THE WATER LAYER SOMETHING A PLAYER CAN SURVIVE?

    blender --background --python-exit-code 1 \
            assets/world_source/pieces/Island_base.blend \
            --python blender/tools/check_island_water.py -- [--rays 16]

THE QUESTION THIS ANSWERS. `check_island_ground.py` asks whether the ROADS have ground under them.
This one asks the three things a player finds out the hard way, all of which were wrong at once on
2026-08-30:

    1. IS THE SEA BELOW THE LAND?  The bay and the lagoon were drawn 5 cm ABOVE their own shore,
       because `Z_WATER` is a draw-order band from the top-down plan diagram and not a height.
    2. IS THERE ANYTHING UNDER THE WATER?  There was not. Stepping off any shore dropped the
       player out of the world for good.
    3. CAN YOU GET BACK OUT?  This is the one that is easy to get wrong while fixing 2. The land
       stops at `BASE_Z` and the water is 0.60 m below it, so a sea floor that started at the
       waterline would ring the island with a 0.80 m wall -- swimmable to, and impossible to climb,
       because `MovementController.stepHeight` is 0.35 m. `island_v3_terrain.SHORE_Z` starts the
       floor 0.20 m under the land instead, which is a step you can take and ~27 m of dry sand
       before the water starts.

A WALL IS NOT ALWAYS A DEFECT, and the check has to know the difference or it is useless. Where the
massif reaches the sea the plan authors a 300 m sea cliff on purpose, and the harbour and airport
platforms author a quay wall on purpose (`island_v3_terrain.Platform`: the edge ramps where it meets
LAND and keeps its full step where it meets the sea). Those are reported as what they are. The
verdict is about NATURAL coast only -- the flat shoreline a player will actually walk out of.

    natural   land within `WALL_Z` of sea level -> a beach, and it MUST be climbable
    cliff     land far above sea level -> by design; reported, not judged
    quay      land at a `PLATFORM_SPECS` height -> by design; reported, not judged
"""
import bpy, os, sys, math, argparse
from mathutils import Vector

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in (os.path.join(os.path.dirname(HERE), "lib"), os.path.join(REPO, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import island_v3_geom as G                                                   # noqa: E402
import island_v3_terrain as IT                                               # noqa: E402

#: The character's own ledge step (`MovementController.stepHeight`). Quoted, not derived: this file
#: cannot import Java, and a beach that a character cannot climb is measured against the character.
STEP_HEIGHT = 0.35

#: Land this far above sea level at the shoreline is a CLIFF, not a beach. Generous on purpose --
#: anything under it is somewhere a player will try to walk out of.
WALL_Z = 1.0

#: Sample spacing along a shore ray, metres, and how far either side of the waterline to walk.
SAMPLE = 1.0
OUT_M, IN_M = 60, 20

#: Meshes that ARE something to stand on. The `Sea` plate is not: it is the water surface.
SURFACE_NAMES = ("Ground", "Seabed")


def surfaces():
    return [bpy.data.objects[n] for n in SURFACE_NAMES if n in bpy.data.objects]


def top_hit(surf, x, y):
    """Highest surface z at (x, y), or None. Asked of the ground OBJECTS, never of the scene --
    same rule as `check_island_ground.py`: nothing standing on the ground may hide it."""
    best = None
    for o in surf:
        inv = o.matrix_world.inverted()
        ok, loc, _n, _i = o.ray_cast(inv @ Vector((x, y, 3000.0)), Vector((0.0, 0.0, -1.0)))
        if ok:
            z = (o.matrix_world @ loc).z
            best = z if best is None else max(best, z)
    return best


def coast_along(dx, dy):
    """Radius at which this heading leaves the land, or None if it never does."""
    r = 0.0
    while r < G.ORIGIN:
        if not G.on_land(dx * r, dy * r):
            return r
        r += 5.0
    return None


def classify(land_z):
    if land_z is None:
        return "unknown"
    for (_n, _poly, z) in IT.PLATFORM_SPECS:
        if abs(land_z - z) < 0.5:
            return "quay"
    return "cliff" if land_z > WALL_Z else "natural"


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="check_island_water.py")
    ap.add_argument("--rays", type=int, default=16, help="shore rays around the island (default 16)")
    args = ap.parse_args(argv)

    surf = surfaces()
    bad = 0
    print("surfaces: %s" % ", ".join(o.name for o in surf) or "(none)")
    if len(surf) < 2:
        print("FAIL: expected both %s -- there is no sea floor in this file" % (SURFACE_NAMES,))
        return 1

    # 1. the sea is below the land
    sea = bpy.data.objects.get("Sea")
    if sea is None:
        print("FAIL: no `Sea` plate"); bad += 1
    else:
        top = max((sea.matrix_world @ v.co).z for v in sea.data.vertices)
        ok = top < IT.BASE_Z
        print("sea surface z = %+.2f, flat land z = %+.2f  ->  %s"
              % (top, IT.BASE_Z, "below the land" if ok else "ABOVE THE LAND"))
        bad += 0 if ok else 1

    # 2 + 3. walk each heading from offshore to inland
    print("\n%-4s %-8s %9s %9s  %s" % ("ray", "coast", "land z", "worst step", "verdict"))
    kinds = {"natural": 0, "cliff": 0, "quay": 0, "unknown": 0}
    holes = 0
    worst_beach = 0.0
    for k in range(args.rays):
        a = 2.0 * math.pi * k / args.rays
        dx, dy = math.cos(a), math.sin(a)
        r = coast_along(dx, dy)
        if r is None:
            print("%-4d %-8s %9s %9s  no coast on this heading" % (k, "-", "-", "-"))
            continue
        prof = [(s, top_hit(surf, dx * (r + s * SAMPLE), dy * (r + s * SAMPLE)))
                for s in range(-IN_M, OUT_M + 1)]
        gap = [s for (s, z) in prof if z is None]
        holes += len(gap)
        zs = [(s, z) for (s, z) in prof if z is not None]
        step = max((abs(b[1] - a2[1]) for a2, b in zip(zs, zs[1:])), default=0.0)
        land_z = top_hit(surf, dx * (r - 20.0), dy * (r - 20.0))
        kind = classify(land_z)
        kinds[kind] += 1
        verdict = "ok"
        if gap:
            verdict = "NO SURFACE at %d sample(s) -- a hole to fall through" % len(gap)
        elif kind == "natural":
            worst_beach = max(worst_beach, step)
            if step > STEP_HEIGHT:
                verdict = "CANNOT CLIMB OUT (%.2f m > stepHeight %.2f)" % (step, STEP_HEIGHT)
        else:
            verdict = "%s wall, by design" % kind
        if verdict != "ok" and not verdict.endswith("by design"):
            bad += 1
        print("%-4d %-8s %+9.1f %9.3f  %s" % (k, kind, land_z if land_z is not None else -999.0,
                                              step, verdict))

    print("\ncoast: %d natural, %d cliff, %d quay; %d sample(s) with no surface under them"
          % (kinds["natural"], kinds["cliff"], kinds["quay"], holes))
    print("worst 1 m step on NATURAL coast = %.3f m against stepHeight %.2f m"
          % (worst_beach, STEP_HEIGHT))
    print("a cliff or a quay is a wall the plan authored; swimming to one and finding no way up is\n"
          "expected there -- ladders and slipways are content, not a ground defect.")

    # 4. the runtime markers this all depends on
    for name, need in (("water_sea", "size"), ("bounds_world", "size")):
        e = bpy.data.objects.get(name)
        if e is None:
            print("FAIL: no `%s` marker -- the piece bakes no %s" % (
                name, "swim volume" if name.startswith("water") else "world boundary"))
            bad += 1
        else:
            print("marker %-14s at z=%+.2f  %s=%s" % (name, e.location.z, need, list(e[need])))

    print("\n%s" % ("check_island_water: OK" if bad == 0 else
                    "check_island_water: %d FAILURE(S)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    code = main()
    if bpy.app.background:
        sys.exit(code)

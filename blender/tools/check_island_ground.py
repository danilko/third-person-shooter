"""check_island_ground.py -- IS THE GROUND SOMETHING THE ROADS CAN ACTUALLY BE LAID ON?

    blender --background --python-exit-code 1 \
            assets/world_source/island_v3.blend \
            --python blender/tools/check_island_ground.py -- [--step 10] [--json out.json]

THE QUESTION THIS ANSWERS, and why it is not the same as "does the plan look right in top view".
The island plan authors roads in XY and terrain as separate content, and nothing had ever compared
them. `point_solve` derives every road's support from `delta = surface_z - ground_z`, sampled per
station -- so a ground surface the plan never checked against becomes piers, trenches and
embankments the moment anyone builds. Measured when this was first run, four of the six arterials
crossed a **vertical 80-120 m wall**: the terrain was a stack of contour PRISMS, and the arterials
clipped their footprints. That is what `island_v3_terrain.py` and the routing in
`island_v3_geom.ARTERIALS` were written to answer; this file is the gate that says whether they did.

WHAT IT REPORTS, per road:

    coverage   stations with no ground under them -- over water, or off the map
    carried    stations with no ground that a BRIDGE DECK spans, which is not a hole
    step       the biggest jump in ground height between two samples, and where
    grade      that step as a percentage, against THAT ROAD'S OWN class (`G.arterial_class`)
    climb      total ascent, so a road that climbs steadily is told apart from one that jumps

Green means the terrain is a surface a road can follow. It does NOT mean the road is designed --
gradient, sight lines and junction spacing are the artist's, and this only says the ground under
them is continuous.

TWO THINGS THIS FILE GOT WRONG BEFORE, both worth keeping written down:

  * THE GROUND IS NOT "WHATEVER THE SCENE RAY HITS AFTER N PUNCHES". The sampler used to ray_cast
    the whole SCENE and step down past anything that was not a ground object, up to 8 times.
    `RAIL_BRANCH` is drawn at exactly z = 0.000 where it runs at grade, which is exactly where
    `Land_Main`'s top face was: the ray hit the rail, stepped to z = -0.001, and never saw the
    land 1 mm above it. That reported Chuo-dori as crossing 20 m of OPEN WATER at (48, 410) --
    a finding that survived into a written plan as "author the missing river bridge". There is no
    water there. The ground is now asked of the GROUND OBJECTS THEMSELVES (`obj.ray_cast`, topmost
    hit wins), so nothing standing on it can hide it and there is no punch limit to tune.
  * A ROAD'S GRADE LIMIT IS THE ROAD'S. One global `--limit` held a port distributor and a trunk
    expressway to the same 4%. Each road now declares its class in `island_v3_geom.ARTERIAL_CLASS`
    and is measured against `island_v3_plan.MAX_GRADE[class]`; `--limit` still overrides the lot.
"""
import bpy, os, sys, math, json, argparse
from mathutils import Vector

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in (os.path.join(os.path.dirname(HERE), "lib"), os.path.join(REPO, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import island_v3_geom as G                                                   # noqa: E402
import island_v3_plan as P                                                   # noqa: E402
import island_v3_terrain as IT                                                # noqa: E402

#: Collections whose meshes ARE the ground. Everything else -- a road ribbon, a building, a
#: support column, a parcel outline -- is something standing ON it, and is simply not asked.
GROUND_COLLECTIONS = ("TERRAIN", "LAND")

#: Collections whose meshes CARRY a road over a hole. A station on one of these is not ground and
#: is not a gap either: it is a bridge, which is the correct answer to water.
BRIDGE_COLLECTIONS = ("BRIDGES",)

#: A step bigger than this between two samples is a WALL, not a slope, whatever the grade works
#: out to -- 10 m apart, a 2 m step is already a kerb no car climbs.
WALL_STEP = 2.0

#: Where the downward probe starts. Above the island's highest possible surface, and nothing else.
PROBE_Z = 3000.0


def surface_fn(objs):
    """Topmost hit among `objs`, asked of each object directly. `None` if none of them is here.

    Object-space `ray_cast` rather than `scene.ray_cast` is the whole point: a scene ray answers
    "what is the first thing here", which is a different question and has a different answer the
    moment anything is drawn on the ground.
    """
    probes = [(o, o.matrix_world, o.matrix_world.inverted()) for o in objs]

    def sample(x, y):
        best = None
        for (o, m, inv) in probes:
            org = inv @ Vector((x, y, PROBE_Z))
            direction = (inv.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
            hit, loc, _n, _i = o.ray_cast(org, direction)
            if hit:
                wz = (m @ loc).z
                if best is None or wz > best:
                    best = wz
        return best
    return sample


def collect(names):
    out = []
    for cn in names:
        c = bpy.data.collections.get(cn)
        if c:
            out.extend(o for o in c.all_objects if o.type == 'MESH')
    return out


def _solid_ground(ground, fallback=0.0):
    """`ground`, with a miss answered by the last height it did find. `IT.alignment_grade` and
    `IT.bench_depth` are pure arithmetic over a height FIELD and have no None in their vocabulary;
    a gap is a different finding with its own name (`NO GROUND`), reported before either is asked."""
    state = {"last": fallback}

    def f(x, y):
        z = ground(x, y)
        if z is None:
            return state["last"]
        state["last"] = z
        return z
    return f


def profile(pts, ground, carrier, step):
    """(arc, ground_z or None, x, y, carried_by or None) per station."""
    out, s = [], 0.0

    def station(x, y):
        # THE CARRIER IS ASKED EVEN WHERE THERE IS GROUND. A station on a bridge deck is not
        # standing on the ground beneath it, so it must not contribute a ground STEP either -- the
        # airport bridge lands on a +8 m reclaimed platform whose seaward edge is an authored quay
        # wall, and measuring that wall as a grade the road has to climb reported a 7 m WALL under
        # a road that is 16 m above it on its own columns.
        return (s, ground(x, y), x, y, carrier(x, y))

    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        n = max(1, int(seg / step))
        for k in range(n):
            t = k / float(n)
            out.append(station(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            s += seg / n
    if pts:
        out.append(station(pts[-1][0], pts[-1][1]))
    return out


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=float, default=10.0)
    ap.add_argument("--limit", type=float, default=None,
                    help="one grade for every road; default is each road's own "
                         "island_v3_geom.ARTERIAL_CLASS against island_v3_plan.MAX_GRADE")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    ground_objs = collect(GROUND_COLLECTIONS)
    if not ground_objs:
        raise SystemExit("no %s collection in this file" % "/".join(GROUND_COLLECTIONS))
    bridge_objs = collect(BRIDGE_COLLECTIONS)
    ground = surface_fn(ground_objs)
    bridges = surface_fn(bridge_objs)

    def carrier(x, y):
        """What holds a road up where there is no ground: a `BRIDGES` deck mesh, or the plan's own
        span.

        THE SECOND HALF IS THE BASE PIECE. `BRIDGES` is `build_island_v3.build_bridges`'s output and
        it only exists in the PLAN blend; in `Island_base` the crossing is built by the ROAD KIT as
        part of the road itself -- `seed_district_roads.height_profile` ramps the alignment onto the
        deck height and `road_support` grows the piers -- so there is no separate deck object to
        find, and asking only for one reported Hama-dori and Kuko-dori as 142 stations of open water
        while both were standing on their own columns. `island_v3_plan.bridge_at` is the same
        question `island_v3_terrain.report` asks and the same one that PUT the road up there, so the
        two gates now agree by construction instead of by coincidence."""
        if bridges(x, y) is not None:
            return "bridge"
        span = P.bridge_at(x, y)
        return span[0] if span else None

    print("ground: %d mesh(es) in %s; %d bridge deck(s) in %s"
          % (len(ground_objs), "/".join(GROUND_COLLECTIONS),
             len(bridge_objs), "/".join(BRIDGE_COLLECTIONS)))
    print("%-14s %-8s %7s %6s %6s %5s %7s %8s  %s" %
          ("road", "class", "length", "zmin", "zmax", "gap", "carried", "worst", "verdict"))

    rows, bad = [], 0
    for name, pts in IT.road_network():
        kind = G.arterial_class(name)
        limit = args.limit if args.limit is not None else P.MAX_GRADE[kind]
        prof = profile(pts, ground, carrier, args.step)
        zs = [p[1] for p in prof if p[1] is not None]
        carried = [p for p in prof if p[4]]
        gaps = [p for p in prof if p[1] is None and not p[4]]
        worst, at, climb = 0.0, None, 0.0
        for a, b in zip(prof, prof[1:]):
            # A pair straddling a hole is NOT a ground step. There is no ground between the two
            # banks of the bay; measuring across it would invent a 0 m cliff or a 2 m one
            # depending only on the tide of the sampling.
            if a[1] is None or b[1] is None or a[4] or b[4]:
                continue
            d = b[1] - a[1]
            climb += max(0.0, d)
            if abs(d) > worst:
                worst, at = abs(d), b
        grade = worst / args.step
        verdict = "ok"
        if gaps:
            # NAME THE PLACE. "no ground" is almost always a road crossing water with no bridge
            # authored under it, and the only useful form of that finding is a coordinate.
            verdict = "NO GROUND x%d, e.g. (%.0f, %.0f) -- bridge it or move it" % (
                len(gaps), gaps[0][2], gaps[0][3])
        elif kind in IT.BENCHED_CLASSES:
            # A MOUNTAIN ROAD IS BENCHED, so a per-sample step is the wrong question to ask of it.
            # Its hairpins are cut-and-fill platforms: the GROUND across a turn swings far more
            # steeply than the road ever does, and measured the way a trunk road is measured the
            # only legal alignment on the hill reports 85% at every hairpin. What has to hold is
            # the pair `island_v3_terrain` defines -- the average grade over the whole length
            # (nothing can make a road climb faster than its limit end to end) and a ceiling on how
            # much structure the benching costs. Same rule, same numbers, one owner.
            # The BUILT mesh's own height, with a miss falling back to the last hit — a benched
            # road runs over ground the ray finds everywhere here, and a hole would already have
            # been reported above as `NO GROUND`.
            solid = _solid_ground(ground)
            _c, _l, avg = IT.alignment_grade(solid, pts)
            bench = IT.bench_depth(solid, pts, limit)
            if avg > limit + 1e-9:
                verdict = "average grade %.1f%% > %.1f%% over its own length" % (
                    avg * 100.0, limit * 100.0)
            elif bench > IT.MAX_BENCH:
                verdict = "benched %.1f m > %.1f m -- this is a viaduct, not a road" % (
                    bench, IT.MAX_BENCH)
            else:
                verdict = "ok (benched: avg %.1f%%, worst cut/fill %.1f m)" % (avg * 100.0, bench)
        elif worst >= WALL_STEP:
            verdict = "WALL %.0f m (%.0f%% grade) at (%.0f, %.0f)" % (
                worst, grade * 100.0, at[2], at[3])
        elif grade > limit + 1e-9:
            verdict = "grade %.1f%% > %.1f%% (%s) at (%.0f, %.0f)" % (
                grade * 100.0, limit * 100.0, kind, at[2], at[3])
        if not verdict.startswith("ok"):
            bad += 1
        print("%-14s %-8s %7.0f %6.1f %6.1f %5d %7d %8.2f  %s"
              % (name, kind, prof[-1][0] if prof else 0.0, min(zs) if zs else 0.0,
                 max(zs) if zs else 0.0, len(gaps), len(carried), worst, verdict))
        rows.append(dict(road=name, road_class=kind, limit=limit,
                         length=round(prof[-1][0] if prof else 0.0, 1),
                         zmin=round(min(zs), 2) if zs else None,
                         zmax=round(max(zs), 2) if zs else None,
                         gaps=len(gaps), carried=len(carried),
                         gap_at=[[round(g[2]), round(g[3])] for g in gaps[:8]],
                         worst_step=round(worst, 2),
                         climb=round(climb, 1), verdict=verdict))
    print()
    print("%d of %d road(s) sit on ground a vehicle could follow" % (len(rows) - bad, len(rows)))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(dict(step=args.step, limit=args.limit, roads=rows), fh, indent=1)
        print("wrote %s" % args.json)
    return bad


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)

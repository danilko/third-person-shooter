#!/usr/bin/env python3
"""
island_v3_buildings.py — box/placeholder buildings for the whole island, from the block gradient.

Implements the placement contract in `tokyo-bay-island-v3-cityplanning.md` §7:

    THE GENERATOR OWNS POSITION.  THE AUTHOR OWNS TYPE.

Every building is emitted as a box (or an instance of a kit collection, with `--kit`) carrying
four custom properties:

    rka_kit_id   int   which kit mesh          <- YOU edit this, it is the manual pass
    rka_floors   int   storey count            <- YOU edit this; seeded from station distance
    rka_quarter  str   which planning quarter  <- generated
    rka_lot      str   "<frontage>x<depth>"    <- generated

Position and rotation are NEVER hand-edited: they are derived from the block that produced them,
so moving one by hand desyncs it from the street the moment a road is re-generated. Changing
`rka_kit_id` on a run of selected buildings is a select-and-set on an attribute, survives a road
rebuild, and is what "manual adjustment" means here.

The chain, all of it already in `tools/island_v3_plan.py`:

    zone rect -> block_cells() -> lots() -> one box per lot

`block_cells` and `street_grid` share `_axis_lines` with the same seeded rng, so the blocks are
bounded by the streets that were actually drawn rather than by an independent second grid.

HEIGHT IS NOT RANDOM. Storeys come from the quarter's range scaled by `station_falloff()` — the
second density field from §0 — so towers cluster at the three stations and taper over a 500 m
walk, which is how a Japanese city is actually shaped. Zero-lot-line quarters get no side gap.

RUN:
  blender --background --python blender/tools/island_v3_buildings.py
  blender --background --python blender/tools/island_v3_buildings.py -- --zones neonA,neonB
  blender --background --python blender/tools/island_v3_buildings.py -- --kit ZAKKYO --anchors
"""
import bpy, os, sys, math, random

BLENDER_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO        = os.path.dirname(BLENDER_SRC)
ROOT        = os.path.join(REPO, "assets", "world_source")
sys.path.insert(0, os.path.join(BLENDER_SRC, "lib"))
sys.path.insert(0, os.path.join(REPO, "tools"))

import kit_common as kc
import island_v3_geom as G
import island_v3_plan as P
import island_v3_terrain as IT

FLOOR_H = 3.20          # Japanese commercial storey
ANCHOR_EVERY = 7        # v3 §2b: one block in seven carries a 24 x 34 m anchor
ANCHOR_W, ANCHOR_D = 24.0, 34.0

# Placeholder tint per quarter — these are BLOCKOUT colours for reading the massing from the
# air, not final materials. The kit swap (--kit) replaces the box entirely.
QUARTER_MAT = {"neon_core": "neon", "neon_edge": "screen", "samurai": "wood",
               "teramachi": "red", "resid": "roof", "port": "metal",
               "air": "concrete", "farm": "trim"}


def floors_for(spec, x, y, rng):
    """Storeys from the quarter's range, pulled UP toward the station. `station_falloff` is 1.0
    at a station and 0.0 beyond a 10-minute walk, so the tall end of the range is only reachable
    near one — the density field doing its job instead of a uniform random."""
    lo, hi = spec["floors"]
    if hi <= lo:
        return lo
    t = P.station_falloff(x, y)
    top = lo + (hi - lo) * (0.35 + 0.65 * t)
    return max(lo, min(hi, int(round(rng.uniform(lo, top)))))


def emit_box(name, x, y, bearing, w, d, floors, coll, matkey, props, ground):
    obj = kc.box(name, -w / 2.0, w / 2.0, -d / 2.0, d / 2.0, 0.0, floors * FLOOR_H,
                 coll, matkey)
    obj.location = (x, y, ground.height(x, y))
    obj.rotation_euler = (0.0, 0.0, math.radians(bearing))
    for k, v in props.items():
        obj[k] = v
    return obj


def emit_instance(name, x, y, bearing, floors, coll, kit_coll, props, ground):
    obj = bpy.data.objects.new(name, None)
    obj.instance_type = 'COLLECTION'
    obj.instance_collection = kit_coll
    obj.location = (x, y, ground.height(x, y))
    obj.rotation_euler = (0.0, 0.0, math.radians(bearing))
    obj.empty_display_size = 2.0
    coll.objects.link(obj)
    for k, v in props.items():
        obj[k] = v
    return obj


def build(opts):
    kc.setup_units()
    bpy.ops.wm.read_homefile(use_empty=True)
    kc.setup_units()

    kit_coll = bpy.data.collections.get(opts.kit) if opts.kit else None
    if opts.kit and kit_coll is None:
        print("  NOTE: kit collection '%s' not found — emitting boxes instead. Link or append "
              "the kit .blend first, then re-run." % opts.kit)

    # A BUILDING STANDS ON THE GROUND. It used to be emitted at z = 0 because the island's ground
    # WAS z = 0; since WORLD_REBUILD_PLAN.md step 1 gave it a real surface, a lot in the hillside
    # residential belt would be up to 140 m underground. Same one owner as everything else.
    ground = IT.Terrain(relief=True)

    want = {s.strip() for s in opts.zones.split(",")} if opts.zones else None
    counts, n_anchor, n_block, n_sub, n_clipped = {}, 0, 0, 0, 0
    total = 0

    for zi, (zname, rects, *_rest) in enumerate(G.ZONES):
        if zname == "farm" or (want and zname not in want):
            continue
        coll = kc.get_coll("BLD_%s" % zname)
        for ri, rect in enumerate(rects):
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            quarter, spec = P.block_spec_at(cx, cy, zname)
            cells = P.block_cells(rect, spec, seed=zi * 17 + ri)
            for bi, cell in enumerate(cells):
                n_block += 1
                rng = random.Random((zi * 977 + ri * 131 + bi) & 0xFFFFFFFF)
                bx, by = (cell[0] + cell[2]) / 2, (cell[1] + cell[3]) / 2
                q, qspec = P.block_spec_at(bx, by, zname)
                if qspec is None:
                    # Inside the moat — the castle's innermost ring has no quarter on purpose.
                    # This is v3 §3's "one block, no buildings", enforced by the block gradient
                    # rather than by a hand-drawn exclusion.
                    continue
                mat = QUARTER_MAT.get(q, "trim")

                # one block in seven is an ANCHOR — the thing you navigate by inside the density
                if opts.anchors and q in ("neon_core", "neon_edge") and bi % ANCHOR_EVERY == 0 \
                        and not P.in_road_corridor(bx, by, max(ANCHOR_W, ANCHOR_D)):
                    fl = max(6, floors_for(qspec, bx, by, rng) + 4)
                    emit_box("anchor_%s%d_%03d" % (zname, ri, bi), bx, by, 0.0,
                             ANCHOR_W, ANCHOR_D, fl, coll, "glasscurtain",
                             dict(rka_kit_id=90, rka_floors=fl, rka_quarter=q,
                                  rka_lot="%dx%d" % (ANCHOR_W, ANCHOR_D), rka_anchor=True),
                             ground)
                    n_anchor += 1
                    total += 1
                    continue

                # A T3 block is a STREET spacing, not a building unit — cut it by T4 roji into
                # 45-60 m sub-blocks first, or a 154 m block gets a perimeter and a dead middle.
                for si, sub in enumerate(P.subdivide(cell, qspec["alley"])):
                    n_sub += 1
                    for li, (x, y, bearing, f, d) in enumerate(P.lots(sub, qspec,
                                                                      seed=bi * 31 + si)):
                        if rng.random() > qspec["retain"] * opts.retain_scale:
                            continue        # perimeter retention — not every lot is built
                        # ROADS WIN. The block grid is generated per zone rect and knows
                        # nothing about the arterials or the expressway running through it, so
                        # without this subtraction the two layers overlap — a fit check found
                        # 33% of buildings standing in a road. This is v3 7's "blocks are the
                        # leftover polygons" actually enforced.
                        if P.in_road_corridor(x, y, max(f, d)):
                            n_clipped += 1
                            continue
                        fl = floors_for(qspec, x, y, rng)
                        props = dict(rka_kit_id=rng.randrange(0, 6), rka_floors=fl,
                                     rka_quarter=q, rka_lot="%.1fx%.1f" % (f, d))
                        nm = "bld_%s%d_%03d_%02d_%03d" % (zname, ri, bi, si, li)
                        if kit_coll is not None:
                            emit_instance(nm, x, y, bearing, fl, coll, kit_coll,
                                          props, ground)
                        else:
                            emit_box(nm, x, y, bearing, f, d, fl, coll, mat, props, ground)
                        counts[q] = counts.get(q, 0) + 1
                        total += 1

                    # 会所地 — the dead sub-block core. Only quarters that historically had one
                    # get interior infill, and even then it is sparse back-alley tenement.
                    if qspec["infill"] > 0.0:
                        iw, ih = sub[2] - sub[0], sub[3] - sub[1]
                        n_in = int(iw * ih / 900.0 * qspec["infill"])
                        for k in range(n_in):
                            x = rng.uniform(sub[0] + 6, sub[2] - 6)
                            y = rng.uniform(sub[1] + 6, sub[3] - 6)
                            if not G.on_land(x, y):
                                continue
                            fl = max(1, floors_for(qspec, x, y, rng) - 2)
                            emit_box("infill_%s%d_%03d_%02d_%02d" % (zname, ri, bi, si, k),
                                     x, y, rng.uniform(0, 90), 5.0, 8.0, fl, coll, mat,
                                     dict(rka_kit_id=rng.randrange(0, 3), rka_floors=fl,
                                          rka_quarter=q, rka_lot="5.0x8.0", rka_infill=True),
                                     ground)
                            counts[q] = counts.get(q, 0) + 1
                            total += 1

    print("BUILDINGS: %d instances over %d blocks / %d sub-blocks  (anchors=%d)"
          % (total, n_block, n_sub, n_anchor))
    print("  by quarter: " + "  ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print("  lots clipped by road corridors: %d" % n_clipped)
    print("  budget check: v3 §6 targets ~2,060 instances / ~46 unique meshes")
    if bpy.app.background:
        kc.save_blend(ROOT, opts.out)


def parse_args():
    import argparse
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="island_v3_buildings.py")
    ap.add_argument("--zones", default="", help="comma-separated zone names (default: all)")
    ap.add_argument("--kit", default="",
                    help="name of a linked/appended collection to instance instead of boxes")
    ap.add_argument("--anchors", action="store_true",
                    help="one 24x34 m anchor per seven neon blocks (v3 2b)")
    ap.add_argument("--retain-scale", type=float, default=1.0, dest="retain_scale",
                    help="global multiplier on each quarter's perimeter retention. The "
                         "quarter constants are REAL ratios and should not be edited to hit "
                         "a budget; scale them here instead. ~0.55 lands on v3 6's 2,060.")
    ap.add_argument("--out", default="island_v3_buildings.blend")
    return ap.parse_args(argv)


if __name__ == "__main__":
    build(parse_args())

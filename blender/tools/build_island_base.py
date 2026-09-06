#!/usr/bin/env python3
"""build_island_base.py -> assets/world_source/pieces/Island_base.blend (+ .lanekit.json)

THE WHOLE ISLAND'S GROUND AND ROADS, AS ONE PIECE, SO IT CAN BE PLAYED.

    blender --background --python blender/tools/build_island_base.py
    NAV_HALF=2016 blender/tools/build_piece.sh Island_base
    <godot-jvm> --path . res://src/main/resources/com/openworld/world/hosts/SoloPiece.tscn

`NAV_HALF=2016` is not optional: it is the island's half-extent, and `NavBaker` otherwise clips the
navmesh to one 504 m district — the whole world would bake AI navigation over its middle square and
nothing else. `NavBaker` also derives its Recast cell size from that extent, because 0.5 m cells
over 4 km is an 8062-cell grid and Recast returns a navmesh of zero vertices, silently.

WHY A "BASE" PIECE AND NOT A DISTRICT. `WORLD_REBUILD_PLAN.md` step 3 settles the world's split as
BASE vs TOWN: the ground you TOUCH and the graph you ROUTE on are always resident, and only the
town content streams. This script builds that base. It is also the cheapest possible answer to
"is the island any good?" — one command produces a world you can walk and drive end to end, with
no buildings, no props and no districts to author first, which is exactly the high-level argument
you want to have BEFORE detailing anything.

WHAT IT CONTAINS, and nothing else:

    TERRAIN   `Ground` — the contour heightfield (`tools/island_v3_terrain.py`). The road network's
              ground cut punches the tarmac's footprint out of it, so nothing z-fights.
    TERRAIN_COL  `Ground-colonly`, the same mesh as the collider, deliberately in a collection the
              cut does NOT recognise as terrain: collision under the island is continuous even
              where the visual has been cut away. This is the base layer's whole job.
    SEABED    `Seabed` — the ground UNDER the water, the same quadtree heightfield cut to the
              OTHER side of the coastline, dropping from the shoreline to `SEA_FLOOR_Z` over
              `SHELF_RUN`. It meets the land's coastal skirt exactly (same vertex, same Z), so the
              waterline is continuous ground and stepping off any shore lands you on the sea floor
              instead of dropping you out of the world.
    SEABED_COL `Seabed-noped-colonly`, its collider — same deferred link as the ground's, for the
              same ray-cast reason, and `-noped` so `NavBaker` keeps AI off the sea floor.
    SEA       one translucent plate at `SEA_LEVEL_Z`, visual only. It is the water SURFACE.
    MARKERS   `water_sea` (ONE swim volume over every body of water — see `build_markers`) and
              `bounds_world` (the logic wall). Base-layer facts, not streaming content.

    NOTE ON THE SAFETY FLOOR. CLAUDE.md records that this project deliberately has no
    world-spanning safety floor, and the seabed is not one returning by another name. A safety
    floor is an invisible collision lid a metre under the visible ground; it caught bodies where
    the world had a hole and gave them no way back. The seabed is the terrain continuing past the
    waterline — visible, sloped, and walkable in both directions — and it does not exist under the
    island at all. Falling off a cliff onto land still falls.
    the ROADS the plan's seven arterials, authored as point-graph roads through the addon's own
              operators (`seed_district_roads.seed`, region = the whole island), built, and
              exported to the `.lanekit.json` sidecar `build_piece.sh` hands to `WorldBaker`.

WHAT IT DELIBERATELY DOES NOT CONTAIN: buildings, props, zone markers, traffic spawn regions. Those
are TOWN content and belong to per-district blends linked in later. A `region_`/`spawn_` marker here
would make this piece stream its own content and quietly become a district.
"""
import bpy, os, sys, math, argparse

BLENDER_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))    # blender/
REPO = os.path.dirname(BLENDER_SRC)
PIECES = os.path.join(REPO, "assets", "world_source", "pieces")
for p in (os.path.join(BLENDER_SRC, "lib"), os.path.join(BLENDER_SRC, "addons"),
          os.path.join(BLENDER_SRC, "tools"), os.path.join(REPO, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import kit_common as kc                                                      # noqa: E402
import assemble as asm                                                       # noqa: E402
import island_v3_geom as G                                                   # noqa: E402
import island_v3_terrain as IT                                               # noqa: E402
import build_island_v3 as biv                                                # noqa: E402
import seed_district_roads as seeder                                         # noqa: E402

STEM = "Island_base"


def build_ground(terrain):
    """The island's ground AND the sea floor. The COLLIDERS are deferred — see `add_colliders`.

    ONE grid, both sheets. `_world_grid` samples `G.on_land` over the whole world once and both
    sheets are cut from those same samples, so the coastline they share is the same coastline by
    construction rather than by two agreeing computations.
    """
    world = biv._world_grid(IT.SEABED_MARGIN)
    nv = _emit_ground(terrain, world, None)
    sv, sf, _ss = biv.build_seabed(terrain, kc.get_coll("SEABED"), world)
    print("  seabed: %d verts, %d faces, shore %.2f m -> floor %.1f m over %.0f m of shelf"
          % (sv, sf, IT.SHORE_Z, IT.SEA_FLOOR_Z, IT.SHELF_RUN))
    return nv, world


def _emit_ground(terrain, world, carve):
    """Build (or REBUILD) the one `Ground` object. Called twice: once natural, once carved.

    THE ORDER IS THE WHOLE DESIGN (`W13`/`W21`, 2026-09-06). A road's own profile is derived FROM
    the ground -- `bench_profile`, `grade_cone` and `seed_district_roads.height_profile` all sample
    it -- so the roads must be routed and graded against the NATURAL terrain. Only once the
    alignment is finished is the terrain deformed to it. Carve the field the router reads and the
    next pass derives a lower road, which carves deeper, forever. Alignment against the original
    ground, ground deformed to the finished alignment, never back: that is the order a real road is
    built in, and it is the order every open-world pipeline uses.
    """
    old = bpy.data.objects.get("Ground")
    if old is not None:
        me = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
    nv, nf, nskirt = biv.build_ground(terrain, kc.get_coll("TERRAIN"), world, carve=carve)
    print("  ground%s: %d verts, %d faces (%d skirt), quadtree %.0f-%.0f m at %.2f m tolerance"
          % (" (carved to the roads)" if carve is not None else "",
             nv, nf, nskirt, biv.GROUND_CELL, biv.GROUND_COARSE_CELL, biv.GROUND_TOLERANCE))
    return nv


def carve_ground_to_roads(terrain, world):
    """Re-emit the ground with the built road network as a ceiling on it, and report the depth.

    The corridors come from the SOLVED BANDS (`point_build.road_corridors`), so the surface the
    ground is cleared to is the same surface that was just swept -- not the road's authored numbers
    read a second time."""
    from road_kit_authoring import point_build as pb
    bpy.context.scene[pb.CARVED_FLAG] = False
    corridors = pb.road_corridors()
    if not corridors:
        return None
    carve = IT.Carve(corridors, verge=biv.GROUND_CELL)
    _emit_ground(terrain, world, carve)
    # SAY SO IN THE FILE. A ground with the roads cut into it is not the natural ground, and the
    # one thing that must never confuse the two is `point_build.write_ground_back` -- a second
    # Build in this file would otherwise stamp "the ground meets the road here" onto 195 of 225
    # stations as if it were terrain. See that function.
    bpy.context.scene[pb.CARVED_FLAG] = True
    return carve


def add_colliders():
    """The collider that IS the ground's own mesh — linked LAST, after the roads are built.

    A mesh proxy, not `kc.colonly`'s box: a box over a 4 km island is a flat lid at the summit
    height with the whole world inside it. Post-dissolve the visual is ~19 k triangles, small
    enough to serve directly as one `ConcavePolygonShape3D`, which is the point of an
    always-resident base layer.

    IT IS LINKED AFTER `point_build`, AND THAT IS THE WHOLE REASON THIS IS NOT A ONE-LINER IN
    `build_ground`. `point_build.ground_sampler` raycasts the scene downward and punches through
    anything that is not terrain, restarting 1 mm below each hit. A collider sitting COINCIDENT
    with the visual ground is such a hit — and the 1 mm restart then begins *below* the very
    surface it was looking for, so the sample returns nothing. Which of the two coplanar faces the
    ray reports first is arbitrary, so this read as a handful of `ground_unsampled` stations that
    moved around whenever the mesh changed at all. With the collider absent during the build there
    is exactly one ground to hit, and every station samples it.

    `TERRAIN_COL`, not `TERRAIN`: `point_build.is_terrain` matches anything in a `TERRAIN`/`GROUND`/
    `MANUAL` collection, and a collider parked beside its visual would be sampled as ground in its
    own right.

    IT IS A COPY OF THE VISUAL GROUND, AND THAT IS `W21` CLOSED (2026-09-06). It used to be a copy
    of the ground as it stood BEFORE the road booleans, deliberately: a boolean removes material,
    so cutting the collider would have opened a road-shaped hole in the one layer that must never
    have one — which left the player standing on hillside the eye said was not there wherever a
    road ran below grade. A carve cannot open a hole. It is a `min` on a heightfield, so the carved
    ground is still one continuous sheet, and the collider can simply BE it. One surface, seen and
    stood on, which is the property a heightfield pipeline has for free.
    """
    ground = bpy.data.objects["Ground"]
    col = _link_collider("Ground-colonly", ground.data.copy(), "TERRAIN_COL", "Ground")
    # THE SEABED'S COLLIDER IS THE SAME MESH, and it is linked here for the same reason: the
    # sampler punches through a non-terrain hit and keeps going, but two coplanar faces are still
    # two hits, and the seabed's own visual is already one of them.
    seabed = bpy.data.objects.get("Seabed")
    if seabed is not None:
        # `-noped`: SOLID, BUT NOBODY WALKS THERE. The marker is the road kit's carriageway one and
        # `NavBaker` is its one reader on the Godot side ("this body is not walkable ground"), which
        # is exactly the sea floor's status — you can stand on it so you do not fall out of the
        # world, and an AI must never plan a route across the bottom of the bay. Without it the
        # navmesh grows a 4 km walkable sheet under the sea the moment the seabed faces the right
        # way up.
        _link_collider("Seabed-noped-colonly", seabed.data.copy(), "SEABED_COL", "Seabed")
    return col


def _link_collider(name, mesh, coll_name, proxy_for):
    mesh.name = name
    col = bpy.data.objects.new(name, mesh)
    kc.get_coll(coll_name).objects.link(col)
    mesh.use_fake_user = False
    col.data.materials.clear()
    col.data.materials.append(kc.mat("col"))
    col["proxy_for"] = proxy_for
    # HIDDEN FROM RENDER ONLY. It is a copy of the ground sitting in the same place, so a preview
    # of this file showed the collider's flat `col` material instead of the terrain. `export_world.py`
    # takes the whole scene regardless of render visibility (the exporter is not called with
    # `use_visible`), so this costs the export nothing — the same trick `hide_sources` uses.
    col.hide_render = True
    print("  collider %s: %d verts, %d faces" % (name, len(mesh.vertices), len(mesh.polygons)))
    return col


def build_water():
    """The water SURFACE — one translucent plate at sea level. VISUAL ONLY, no `-colonly` sibling.

    ONE PLATE, and the bay and the lagoon are not separate objects any more. They were prisms from
    `Z_SEA` up to `Z_WATER`, and `Z_WATER` is `+0.05` — a DRAW-ORDER band from the top-down plan
    diagram, where water has to draw over land to be visible. In a piece you walk around in that is
    simply the sea 5 cm ABOVE its own shore, which is what the 2026-08-30 walk-test reported. There
    is nothing for a per-body plate to do here anyway: the sea plate already spans the whole world,
    the bay and the lagoon are holes cut in the land, and the same plate shows through both.

    It sits at `SEA_LEVEL_Z` (-0.60 m), which is BELOW the flat land at `BASE_Z` (0.00) — so every
    shore is a 0.6 m drop to the water, and the seabed continues down from a further 0.4 m under
    that.
    """
    sea = kc.get_coll("SEA")
    # PAST THE FLOOR, always. The plate has to reach further than the seabed does or the sea floor's
    # own outer edge becomes the horizon.
    h = G.ORIGIN + IT.SEABED_MARGIN + 120.0
    kc.box("Sea", -h, h, -h, h, IT.SEA_LEVEL_Z - 0.05, IT.SEA_LEVEL_Z, sea, "water")


def build_markers():
    """The two BASE-LAYER markers: the swim volume and the world boundary.

    These are not the streaming content the module docstring rules out. A `region_`/`spawn_` marker
    would make this piece stream its own AI and quietly become a district; the water and the edge of
    the world are always-resident facts about the base, in the same tier as `GROUND_COL` — and the
    baker's marker convention is how a piece tells the runtime about them.

    `size` is authored in GODOT axis order (x, up, z) because that is what `BoxShape3D.setSize`
    receives verbatim — the same order `build_world.py`'s `water_bay` used. The Empty's own Blender
    Z is the box CENTRE height.
    """
    mk = kc.get_coll("MARKERS")

    # ALL WATER IS ONE VOLUME, and it can be because the land is above it everywhere. The flat
    # island sits at `BASE_Z` (0.00) and the surface is at -0.60, so a box whose TOP is the water
    # line cannot touch a character standing on land however far it spreads — while the bay, the
    # lagoon and the open sea are all holes in that land and are covered for free. `Character`
    # then decides wade-vs-swim from the true depth under the body, so the beach is walkable and
    # only real depth swims.
    half = G.ORIGIN + IT.SEABED_MARGIN
    top, bottom = IT.SEA_LEVEL_Z, IT.SEA_FLOOR_Z - 20.0
    w = bpy.data.objects.new("water_sea", None)
    w.empty_display_type = 'PLAIN_AXES'; w.empty_display_size = 60.0
    w.location = (0.0, 0.0, (top + bottom) * 0.5)
    w["size"] = [2.0 * half, top - bottom, 2.0 * half]
    mk.objects.link(w)

    # THE LOGIC WALL, AT THE HORIZON — not around the island. It stands `BOUNDS_INSET` inside the
    # sea floor's own outer edge, so it is always on real ground with more ground behind it (a wall
    # on the last triangle has a hole under it the moment anything overshoots by a frame's motion),
    # and that edge is now ~2.4 km past the shore rather than 700 m. The ground does the work; this
    # is what is left when the ground runs out.
    wall = half - IT.BOUNDS_INSET
    b = bpy.data.objects.new("bounds_world", None)
    b.empty_display_type = 'CUBE'; b.empty_display_size = wall
    b.location = (0.0, 0.0, 0.0)
    b["size"] = [2.0 * wall, 4000.0, 2.0 * wall]
    b["floor"] = IT.SEA_FLOOR_Z - 40.0
    mk.objects.link(b)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="build_island_base.py")
    ap.add_argument("--spacing", type=float, default=70.0,
                    help="road station spacing, metres (default 70)")
    ap.add_argument("--only", default="", help="comma-separated plan road names")
    ap.add_argument("--links", type=int, default=0, help="local cross-streets (default 0)")
    ap.add_argument("--no-roads", action="store_true", help="ground only — the fastest smoke test")
    ap.add_argument("--no-build", action="store_true",
                    help="author the road points but do not sweep the meshes")
    ap.add_argument("--out", default=os.path.join(PIECES, STEM + ".blend"))
    args = ap.parse_args(argv)

    kc.setup_units()
    asm.wipe_scene()
    for name in ("TERRAIN", "TERRAIN_COL", "SEABED", "SEABED_COL", "SEA", "MARKERS"):
        kc.get_coll(name)

    terrain = IT.Terrain(relief=True)
    _nv, world = build_ground(terrain)
    build_water()
    build_markers()

    if not args.no_roads:
        # THE GROUND MUST EXIST FIRST. Every station is draped by `point_build.ground_sampler`,
        # which raycasts the scene's terrain — seeding into an empty scene would author a
        # perfectly flat road network at z = 0 and only reveal it as floating tarmac in-game.
        print("== seeding the plan's arterials over the whole island")
        made, n_cross = seeder.seed((0.0, 0.0), G.ORIGIN, spacing=args.spacing,
                                    only=args.only, links=args.links)
        print("   %d crossing(s), %d road(s)" % (n_cross, len(made)))
        if made and not args.no_build:
            # AUTO SETBACK BEFORE BUILD. The seeder puts each mouth a fixed 14 m from the
            # crossing, which is a guess; the solver derives the real stop-line distance from the
            # turn paths of the whole clique. Left as-is, five of the island's eight pads reported
            # a folded ring (a WARN — `point_solve.pad_triangles` ear-clips it rather than
            # refusing to build, per ROAD_POINT_GRAPH.md 8f.2 — but a folded pad is still a pad
            # with a crease in it). Whole-clique, always; it is not a per-mouth operation.
            print("== auto setback")
            for o in bpy.context.selected_objects:
                o.select_set(False)
            bpy.ops.rka.auto_setback()
            print("== building road meshes")
            bpy.ops.rka.point_build()

            # AND NOW THE GROUND IS DEFORMED TO THE FINISHED ALIGNMENT -- after the roads, never
            # before. See `_emit_ground` for why that order is the whole design.
            print("== carving the ground to the roads")
            carve = carve_ground_to_roads(terrain, world)
            if carve is not None:
                print("   %d corridor segment(s), verge %.0f m, batter 1:%.2g"
                      % (len(carve), carve.verge, carve.slope))

            # THE GATE, PRINTED. `rka.export_lanekit` already refuses on an error, but a warning
            # is exactly what this script is for: it is the first time the plan's own arterials
            # have been put through the road kit, and the interesting output is what the kit
            # thinks of them.
            from road_kit_authoring import point_model as pm
            from road_kit_authoring import point_validate as pv
            net = pm.read_network()
            findings = pv.validate(net)
            errs = pv.errors(findings)
            print("== gate: %d error(s), %d warning(s)"
                  % (len(errs), len(findings) - len(errs)))
            for f in findings[:20]:
                print("   %-5s %s" % (getattr(f, "level", "?"), pv.describe(f, net.labels)))

    # A camera and a sun so `tools/render.py` gives a usable preview of this file. `export_world.py`
    # drops both (`export_cameras=False, export_lights=False`), so they never reach the game.
    asm.add_camera_sun(kc.get_coll("SEA"), target=(0.0, 0.0, 0.0),
                       cam_loc=(0.0, -G.WORLD * 0.75, G.WORLD * 0.85), lens=32)

    add_colliders()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=args.out)
    print("== saved %s" % args.out)

    if not args.no_roads and not args.no_build:
        # The sidecar is what carries the LANE GRAPH into Godot; `build_piece.sh` picks it up by
        # its sibling name. Exporting refuses on a red gate on purpose — a network whose defects
        # only show up as cars falling through the world is worse than no network.
        side = os.path.splitext(args.out)[0] + ".lanekit.json"
        res = bpy.ops.rka.export_lanekit(filepath=side)
        print("== lanekit export: %s -> %s" % (res, side))


if __name__ == "__main__":
    main()

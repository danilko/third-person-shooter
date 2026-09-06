"""Step 4's acceptance test: the geometry, headless.

    blender --background --python-exit-code 1 \
            --python blender/addons/road_kit_authoring/smoketest_point_build.py

`--python-exit-code 1` MUST come before `--python`, or a crash in here exits 0 and the test
silently "passes".

Proven against the shapes that killed the previous two models, built deliberately rather than
found on the island: a GORE, a SKEW junction, and a PARALLEL OVERLAP that never converges. Every
assertion is on an invariant -- an area, a containment, a run count -- never on an object name.
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "blender", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "lib"))

from road_kit_authoring import point_build as pb          # noqa: E402
from road_kit_authoring import point_edges as pe          # noqa: E402
from road_kit_authoring import point_model as pm          # noqa: E402
from road_kit_authoring import point_solve as ps          # noqa: E402
from road_kit_authoring import point_validate as pv       # noqa: E402

import road_support as rs                                 # noqa: E402


def _wipe():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)


def _eval_mesh(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    return bpy.data.meshes.new_from_object(obj.evaluated_get(dg), depsgraph=dg)


def _area(obj):
    me = _eval_mesh(obj)
    a = sum(p.area for p in me.polygons)
    bpy.data.meshes.remove(me)
    return a


def _tri_count(obj):
    me = _eval_mesh(obj)
    n = len(me.polygons)
    bpy.data.meshes.remove(me)
    return n


def _straight(net, name, y, n=2, length=400.0, z=0.0, x0=0.0, **base):
    base.setdefault("lane_width", 3.5)
    road = net.add_road(pm.RoadData(name, pm.PointData(lanes_fwd=n, lanes_bwd=n, **base)))
    pts = [net.add_station(road, (x, y, z), has_ground_z=True)
           for x in (x0, x0 + length / 2.0, x0 + length)]
    for a, b in zip(pts, pts[1:]):
        net.link(a.uid, b.uid, pm.LINK_SEGMENT)
    return road, pts


def check(msg):
    print("OK:", msg)


def main():
    ok = 0

    # ---- the testbed builds, and the stack actually sweeps something ------------------------
    _wipe()
    net, mp, cp, rr = pv.build_testbed()
    rep = pb.build_network(net, bpy.context.scene, sample_ground=False)
    assert rep["roads"] == 3, rep
    # main and cross each split at the pad (2 + 2); the ramp is one.
    assert rep["runs"] == 5, rep
    assert rep["pads"] == 1, rep
    assert not rep["not_star"], rep["not_star"]
    surfaces = [bpy.data.objects[n] for n in rep["objects"] if n.endswith(pb.SUFFIX_CARRIER)]
    assert len(surfaces) == 5, [o.name for o in surfaces]
    for o in surfaces:
        assert _area(o) > 100.0, (o.name, _area(o))
    check("the testbed builds: 5 runs, 1 pad, every surface has real area")
    ok += 1

    # ---- the carrier carries EVERY declared attribute ----------------------------------------
    me = surfaces[0].data
    missing = [a.name for a in ps.CARRIER_ATTRS if me.attributes.get(a.name) is None]
    assert not missing, missing
    check("the carrier carries every declared attribute -- no band reads a silent 0")
    ok += 1

    # ---- a layer with nothing to build is NOT attached ----------------------------------------
    names = {m.name for m in surfaces[0].modifiers}
    assert "Carriageway" in names and "Spine" in names and "Finish" in names, names
    assert "Pillars" not in names, "an at-grade road must not grow a column row"
    check("an empty layer is dropped, not swept -- a flat road has no Pillars modifier")
    ok += 1

    # ---- THE PAD ------------------------------------------------------------------------------
    pad = next(bpy.data.objects[n] for n in rep["objects"] if n.endswith(pb.SUFFIX_PAD))
    assert _area(pad) > 200.0, _area(pad)
    j = ps.solve_junctions(net)[0]
    poly = [(p[0], p[1]) for p in j.boundary]
    import intersection_kit as ik
    for m in j.mouths:
        for side in (m.half_out, -m.half_in):
            p = (m.pos[0] + m.normal[0] * side, m.pos[1] + m.normal[1] * side)
            assert ik._point_outside_polygon_dist(p, poly) < 0.05, (m.uid, side)
    check("the pad covers every mouth cross-bar and has real area")
    ok += 1

    # ---- THE GORE: the ramp's inboard kerb starts LATE ------------------------------------------
    ramp_edges = [o for o in bpy.data.objects if o.name.startswith("ramp_e" + pb.SUFFIX_EDGE)]
    sides = {o.name.rsplit("_", 2)[-2] for o in ramp_edges}
    assert "left" in sides, sorted(o.name for o in ramp_edges)
    solves = [ps.solve_road(net, net.roads[r], u)
              for r in net.roads for u in ps.road_runs(net, net.roads[r])]
    solves = [s for s in solves if s is not None]
    # The gore bands too: they are what opens the kerb across a ramp join, so a band set without
    # them is not the set the build actually uses.
    bands = pe.collect_bands(solves, ps.solve_junctions(net), ps.solve_gores(net, solves))
    ramp = next(s for s in solves if s.road.name == "ramp_e")
    runs = pe.kerb_runs(ramp, bands)
    assert runs["right"] and runs["right"][0][0] > 0, runs
    kept = [p for r in runs["right"] for p in pe.sub_polyline(ramp.edges_right, r)]
    assert pe.measure_on_asphalt(kept, bands, skip=("ramp_e",)) == 0
    check("the gore opens by itself -- no kerb stands on the mainline's asphalt")
    ok += 1

    # ---- THE BARRIER: authored height, DERIVED placement ---------------------------------------
    # The ramp has no pedestrian access, so it is fenced along its whole length; the arterial is
    # at grade and walkable, so it is not. That split is the whole rule, and it is derived from
    # `ped_access` and `delta` -- there is no per-station wall to place by hand.
    def _edges_of(road):
        # `<road>[_<run>]__edges_<side>_<n>` -- a road that splits at a junction numbers its runs,
        # so the prefix is the road name and the marker is the suffix, not the two concatenated.
        return [o for o in bpy.data.objects
                if o.name.startswith(road) and pb.SUFFIX_EDGE in o.name]

    def _mods(road):
        return {m.name for o in _edges_of(road) for m in o.modifiers}
    assert "Barrier" in _mods("ramp_e"), sorted(_mods("ramp_e"))
    assert "Barrier" not in _mods("road_main"), "an at-grade street is not fenced"
    assert "Sidewalk" in _mods("road_main")
    # It rides the OUTLINE, so it opens at the gore exactly where the kerb does -- one mechanism,
    # not a `RAMP_WALL_OPEN` special case.
    wall_runs = [o for o in _edges_of("ramp_e")
                 if "Barrier" in {m.name for m in o.modifiers}]
    assert wall_runs, "the ramp must carry a barrier somewhere"
    dg = bpy.context.evaluated_depsgraph_get()
    tops = []
    for o in wall_runs:
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg), depsgraph=dg)
        tops.append(max(v.co.z for v in me.vertices))
    # kerb 0.15 + the road's authored 1.0 m barrier, and nothing taller.
    assert abs(max(tops) - 1.15) < 0.02, tops
    check("the barrier is built where the rule says and nowhere else, %.2f m over the deck"
          % max(tops))
    ok += 1

    # ---- THE PAD'S OWN FOOTWAY -----------------------------------------------------------------
    corners = [o for o in bpy.data.objects
               if o.name.startswith("JCT_") and pb.SUFFIX_EDGE in o.name]
    assert len(corners) == 4, [o.name for o in corners]
    for o in corners:
        names = {m.name for m in o.modifiers}
        assert {"Curb", "Sidewalk"} <= names, (o.name, sorted(names))
    # And the street's own footway must REACH it: a run that ends at a mouth must not have its
    # last samples suppressed against the pad it is a mouth of.
    jband = next(b for b in bands if b.owner.startswith("JCT:"))
    assert jband.members, "a pad band must name its members or no run can recognise its own pad"
    mainrun = next(s for s in solves if s.road.name == "road_main")
    runs_m = pe.kerb_runs(mainrun, bands)
    assert runs_m["left"] and runs_m["left"][-1][1] == len(mainrun.edges_left) - 1, \
        "the street's kerb must run all the way into its own junction"
    check("every pad corner carries kerb + footway, and the streets' footways reach them")
    ok += 1

    # ...but a GORE is not a pad. Both are footprints the run is a member of, so membership alone
    # cannot tell them apart -- and treating the gore like a pad left a barrier stub standing
    # across the gore paint, which is the whole reason `carries_edge` is a flag and not a rule.
    gband = next((b for b in bands if b.owner.startswith("GORE:")), None)
    assert gband is not None and not gband.carries_edge, "a gore builds no furniture of its own"
    assert jband.carries_edge, "a pad does"
    ramp2 = next(s for s in solves if s.road.name == "ramp_e")
    inboard = pe.kerb_runs(ramp2, bands)["right"]
    assert inboard, "the ramp keeps an inboard kerb past the nose"
    first = pe.sub_polyline(ramp2.edges_right, inboard[0])[0]
    # `pad=-BURIED_TOL`: the question is whether the furniture STANDS ON the gore, not whether it
    # is within a kerb's width of it -- and not whether it sits exactly on the line, which is where
    # `open_runs` deliberately clips it to.
    assert not pe.covered(first, [gband], pad=-pe.BURIED_TOL), \
        "the ramp's inboard furniture must start PAST the gore, not inside it"
    assert pe.covered(pe.sub_polyline(ramp2.edges_right, (0, 0))[0], [gband] + [
        b for b in bands if b.owner == "road_main"], pad=-pe.BURIED_TOL), \
        "...and it must NOT start at the mouth, which is buried in the mainline"
    check("a gore opens the furniture across it; only a pad hands it on")
    ok += 1

    # ---- THE OUTER EDGE OF THE WORLD keeps its wall --------------------------------------------
    # Where a ramp leaves along the mainline's outer edge the two edges are within `NEAR_PAD` of
    # each other, and an UNDIRECTED slop had each band suppress the OTHER's parapet -- an 11 m hole
    # in the wall at the top of the drop, on the stretch that most needs one. The probe is
    # directional now: "does the pavement CONTINUE past this line", not "is there asphalt nearby".
    ramp3 = next(s for s in solves if s.road.name == "ramp_e")
    # The run the ramp actually leaves from -- `road_main` is split at its junction, so "the
    # mainline" is the run that contains the station carrying the AUX link.
    main_uid = net.aux_pairs()[0][0]
    main3 = next(s for s in solves if main_uid in s.uids)
    outer = pe.kerb_runs(ramp3, bands)["left"]
    assert outer and outer[0][0] == 0, \
        "the ramp's OUTER edge is the edge of the world from its very first sample: %s" % (outer,)
    # ...and the mainline's own outer edge hands over to it rather than both going quiet: between
    # them, every metre of the combined outer boundary is walled up to the gore.
    mrun = pe.kerb_runs(main3, bands)["left"]
    assert mrun, mrun
    hand_x = main3.edges_left[mrun[-1][1]][0]
    ramp_x = ramp3.edges_left[outer[0][0]][0]
    assert hand_x >= ramp_x - 0.01, (
        "the mainline's wall must reach the point the ramp's takes over (%.1f vs %.1f)"
        % (hand_x, ramp_x))
    check("the outer boundary is walled continuously across the ramp join (hand-over at x=%.0f)"
          % ramp_x)
    ok += 1

    # ---- THE PARALLEL OVERLAP that never converges ---------------------------------------------
    _wipe()
    net2 = pm.NetworkData()
    _straight(net2, "main", 0.0, n=2, median_width=1.0, left_walk_width=3.0,
              right_walk_width=3.0)
    _straight(net2, "near", 10.0, n=1, length=600.0, x0=-100.0)
    rep2 = pb.build_network(net2, bpy.context.scene, sample_ground=False)
    left = [o for o in bpy.data.objects if o.name.startswith("main" + pb.SUFFIX_EDGE + "_left")]
    right = [o for o in bpy.data.objects if o.name.startswith("main" + pb.SUFFIX_EDGE + "_right")]
    assert not left, [o.name for o in left]
    assert right, "the clear side must keep its kerb"
    check("two ribbons overlapping without ever converging: the buried kerb opens, the clear one stays")
    ok += 1

    # ---- THE SKEW JUNCTION ----------------------------------------------------------------------
    _wipe()
    net3 = pm.NetworkData()
    a_road = net3.add_road(pm.RoadData("skew_a", pm.PointData(lanes_fwd=2, lanes_bwd=2,
                                                              lane_width=3.5, median_width=1.0)))
    b_road = net3.add_road(pm.RoadData("skew_b", pm.PointData(lanes_fwd=1, lanes_bwd=1,
                                                              lane_width=3.5)))
    # 15 degrees off through -- the geometry that asked the previous model for a 136.7 m setback
    # and left 24 of 45 island pads over 1000 m2.
    ang = math.radians(15.0)
    ap = [net3.add_station(a_road, (x, 0.0, 0.0), has_ground_z=True,
                           role=(pm.INTERSECTION if abs(x) <= 30.0 else pm.SEGMENT))
          for x in (-200.0, -30.0, 30.0, 200.0)]
    bp = [net3.add_station(b_road, (30.0 * math.cos(ang) * s, 30.0 * math.sin(ang) * s, 0.0)
                           if abs(s) == 1 else (200.0 * math.cos(ang) * s,
                                                200.0 * math.sin(ang) * s, 0.0),
                           has_ground_z=True,
                           role=(pm.INTERSECTION if abs(s) == 1 else pm.SEGMENT))
          for s in (-6.6667, -1, 1, 6.6667)]
    for chain in (ap, bp):
        for x, y in zip(chain, chain[1:]):
            if not (x.role == pm.INTERSECTION and y.role == pm.INTERSECTION):
                net3.link(x.uid, y.uid, pm.LINK_SEGMENT)
    arms = [ap[1].uid, ap[2].uid, bp[1].uid, bp[2].uid]
    for i, x in enumerate(arms):
        for y in arms[i + 1:]:
            net3.link(x, y, pm.LINK_JUNCTION)
    # THE DEFAULT PATH: place the mouths roughly, then let Auto Setback solve the whole clique.
    # At 15 degrees the hand-placed 30 m mouths physically overlap each other's caps, which is
    # precisely why "place four mouths by hand" is not a workflow and every shipping tool solves
    # first and exposes the result as draggable (2.2).
    before = ps.solve_junction(net3, arms)
    moved = ps.auto_setback(net3, arms)
    assert moved, "auto setback did nothing on the case that needs it most"
    js = ps.solve_junction(net3, arms)
    assert js.star_ok, (js.star_worst, before.star_worst)
    # THE INVARIANT IS NOT "the pad is small" -- a 15 degree X-crossing genuinely has long acute
    # gores. It is that NO PART OF THE PAD ESCAPES THE MOUTHS: every ring vertex lies within the
    # reach of the arms plus one fillet radius. That is what the previous model could not hold --
    # its corner geometry ran 36.9 m past anything authored, folding the fan into a black crater.
    ring, limit = ps.clamp_corners(
        __import__("intersection_kit").build_junction_boundary(
            [m.arm for m in js.mouths], js.kerb_radius, tail_length=1.0),
        js.mouths, js.centre[0], js.centre[1], js.kerb_radius)
    worst = max(math.hypot(p[0] - js.centre[0], p[1] - js.centre[1]) for p in js.boundary)
    assert worst <= limit + 1e-6, (worst, limit)
    setback = max(math.hypot(net3.points[u].pos[0] - js.centre[0],
                             net3.points[u].pos[1] - js.centre[1]) for u in arms)
    assert setback < 100.0, ("setback ran away", setback)
    rep3 = pb.build_network(net3, bpy.context.scene, sample_ground=False)
    assert rep3["pads"] == 1 and not rep3["not_star"], rep3
    check("a 15-degree skew crossing stays bounded by its own mouths "
          "(pad reach %.0f m <= limit %.0f m, setback %.0f m)" % (worst, limit, setback))
    ok += 1

    # ---- SUPPORT: the same road at three heights, with no other edit ------------------------------
    for z, want_pillars in ((0.0, False), (2.0, False), (14.0, True)):
        _wipe()
        net4 = pm.NetworkData()
        _straight(net4, "via", 0.0, n=2, median_width=1.0, z=z)
        pb.build_network(net4, bpy.context.scene, sample_ground=False)
        surf = next(o for o in bpy.data.objects if o.name.endswith(pb.SUFFIX_CARRIER))
        mods = {m.name for m in surf.modifiers}
        assert ("Pillars" in mods) == want_pillars, (z, mods)
        if want_pillars:
            assert "Deck" in mods, mods
            assert _tri_count(surf) > 200, _tri_count(surf)
    check("NONE / FILL / PIER from one number, with no edit but the road's own Z")
    ok += 1

    # ---- COLLISION is emitted, split, and tagged ---------------------------------------------------
    _wipe()
    net5 = pm.NetworkData()
    _straight(net5, "street", 0.0, n=2, median_width=1.0, left_walk_width=3.0,
              right_walk_width=3.0)
    ramp_road, rpts = _straight(net5, "sliproad", 200.0, n=1, ped_access=False)
    rep5 = pb.build_network(net5, bpy.context.scene, sample_ground=False)
    cols = [o.name for o in bpy.data.objects if o.name.endswith(pb.SUFFIX_COL)]
    assert any(pb.COL_ROAD in n for n in cols), cols
    assert any(pb.COL_WALK in n for n in cols), cols
    assert any(pb.NO_PED_SUFFIX in n for n in cols), cols
    for n in cols:
        assert _area(bpy.data.objects[n]) > 50.0, (n, _area(bpy.data.objects[n]))
    check("every road emits a -colonly proxy, split road/walk, with the no-ped marker (%d)"
          % len(cols))
    ok += 1

    # ---- BUILD IS SAFE TO PRESS TWICE, and never touches ROAD_MANAGER ------------------------------
    before = {o.name for o in bpy.data.objects}
    rep6 = pb.build_network(net5, bpy.context.scene, sample_ground=False)
    after = {o.name for o in bpy.data.objects}
    assert before == after, (before ^ after)
    assert rep6["runs"] == rep5["runs"] and rep6["colonly"] == rep5["colonly"]
    check("Build is idempotent -- a second press produces exactly the same objects")
    ok += 1

    # -- THE GROUND IS THE GROUND, not the first thing the ray hits ------------------------------
    # Measured on the real district `Piece_3_1`: of 300 downward rays, 134 hit terrain, **96 hit
    # buildings** (up to 66.2 m -- a rooftop) and 70 hit the district's previously baked road. A
    # third of a road's stations would have taken their `ground_z` off a roof, and every support is
    # derived from that number. Invisible on a synthetic network, which has neither buildings nor
    # terrain -- so here is one that has both.
    _wipe()
    terrain = bpy.data.meshes.new("Terrain")
    terrain.from_pydata([(-50, -50, 0), (50, -50, 0), (50, 50, 0), (-50, 50, 0)],
                        [], [(0, 1, 2, 3)])
    terrain.update()
    tobj = bpy.data.objects.new("District_test_0_0_Terrain-col", terrain)
    bpy.context.scene.collection.objects.link(tobj)
    # A building standing ON it, in no terrain collection -- exactly a district's STREET content.
    bm_ = bpy.data.meshes.new("Bldg")
    verts = [(-10, -10, 0), (10, -10, 0), (10, 10, 0), (-10, 10, 0),
             (-10, -10, 40), (10, -10, 40), (10, 10, 40), (-10, 10, 40)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    bm_.from_pydata(verts, [], faces)
    bm_.update()
    bobj = bpy.data.objects.new("District_test_0_0_Bldg_001_h40m", bm_)
    bpy.context.scene.collection.objects.link(bobj)
    bpy.context.view_layer.update()

    assert pb.is_terrain(tobj) and not pb.is_terrain(bobj), "one owner of what terrain is"
    names = [o.name for o in pb.terrain_objects(bpy.context.scene)]
    assert names == [tobj.name], "the cut must find the district's ground: %s" % names
    g = pb.ground_sampler(bpy.context.scene)
    # Straight down the middle of the building -- the ray MUST punch through to the ground.
    z_under = g(0.0, 0.0)
    assert z_under is not None and abs(z_under) < 1e-3, \
        "sampled the roof, not the ground: %r (the building is 40 m tall)" % z_under
    # ...and beside it, where there is nothing to punch through.
    z_open = g(30.0, 30.0)
    assert z_open is not None and abs(z_open) < 1e-3, z_open
    # ...and off the terrain entirely, where there IS no ground.
    assert g(500.0, 500.0) is None, "off the terrain the sampler must say so, not invent a height"
    print("OK: the sampler punches through a 40 m building to the ground beneath it, the cut finds "
          "the district's own terrain, and off the edge it returns None")
    ok += 1

    # -- THE CORRIDORS THE TERRAIN CARVE IS DRIVEN FROM --------------------------------------
    # The boolean cut this used to test is gone (see `point_build`'s "the ground cut lives
    # ELSEWHERE"). What replaced it is `road_corridors`: the centreline and half-width of every
    # surface just built, handed to `island_v3_terrain.Carve`. So what has to hold here is that
    # the corridor a road reports IS the road -- a width read off the swept band, not the authored
    # numbers read a second time.
    _wipe()
    ground = bpy.data.meshes.new("Terrain")
    ground.from_pydata([(-200, -200, 0), (200, -200, 0), (200, 200, 0), (-200, 200, 0)],
                       [], [(0, 1, 2, 3)])
    ground.update()
    gobj = bpy.data.objects.new("District_test_0_0_Terrain-col", ground)
    bpy.context.scene.collection.objects.link(gobj)
    net2, _mp2, _cp2, _rr2 = pv.build_testbed()
    pb.build_network(net2, bpy.context.scene, sample_ground=True)
    corr = pb.road_corridors(bpy.context.scene, net2)
    assert corr, "a built network must report corridors for the terrain carve"
    widest = 0.0
    for line, _fallback in corr:
        assert len(line) >= 2, "a corridor is a polyline"
        for pt in line:
            assert len(pt) == 4, "every station carries its own half-width"
            widest = max(widest, pt[3])
    assert widest > 1.0, "the half-width is read off the band, not left at zero: %.3f" % widest
    # THE TERRAIN MESH IS UNTOUCHED, and now by construction rather than by discipline: nothing in
    # the build writes to it at all any more.
    assert len(gobj.data.vertices) == 4, "the build must not edit the terrain mesh"
    assert not gobj.modifiers, "the build must leave no modifier on the terrain"
    print("OK: %d corridor(s) reported for the terrain carve, widest half %.2f m, terrain mesh "
          "and modifier stack untouched" % (len(corr), widest))
    ok += 1

    print("\nALL SMOKETESTS PASSED (%d)" % ok)


main()

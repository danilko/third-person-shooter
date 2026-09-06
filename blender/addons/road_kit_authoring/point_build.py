"""point_build.py -- authored points in, Blender geometry out.

TWO LIFETIME RULES, AND EVERY OBJECT IN THIS FILE OBEYS THEM (1.1):

1. **Authored and generated never share a collection.** A rebuild only ever clears inside
   `ROAD_MANAGER_GEN`. Nothing under `ROAD_MANAGER` is deleted by any build, ever. That is what
   makes Build safe to press with a selection active, mid-edit, at any time.
2. **One generated surface object per road run**, plus its edge furniture and its collision
   proxies. Layers are MODIFIERS, not sibling objects -- object-lifetime bookkeeping was half the
   previous addon (redesign defect 4).

WHAT BUILD DOES, IN ORDER:

    solve   -- `point_solve` resolves every run and every clique into numbers
    ground  -- `ground_sampler` raycasts the terrain under each sample, ALWAYS (3.3 rule 1)
    bands   -- `point_edges` collects every paved footprint
    carrier -- one polyline per run carrying every `point_solve.CARRIER_ATTRS` value
    stack   -- the GN layer stack, one modifier per band that has content
    edges   -- kerb / footway carriers over the OPEN RUNS only, so a gore opens by itself
    pads    -- one triangle-fan mesh per junction clique
    cut     -- the terrain is cut to each road's own footprint, as part of Build
    colonly -- separate carriageway and footway proxies, tagged for surface type and ped access

GROUND CONFORMING IS A STEP, NOT A BUTTON. `Cut Ground Under Road` being a manual panel step the
bake pipeline never called is the confirmed root cause of the "mesh holes" reports, so here the
footprint is a by-product of the outline and the cut runs inside `Build All`.

COLLISION IS A DELIVERABLE. The previous model's roads exported NONE, which silently cost the
pedestrian navmesh, bullet-impact surfaces, car ground and the player's footing all at once (3.5).
"""

import bpy
import bmesh
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "lib"))

import kit_common as kc                                                      # noqa: E402
import road_support as rs                                                    # noqa: E402

try:
    from . import (point_edges as pe, point_model as pm, point_nodes as gn, point_solve as ps,
                   point_style as pstyle)
except ImportError:
    import point_edges as pe                                                 # noqa: E402
    import point_model as pm                                                 # noqa: E402
    import point_nodes as gn                                                 # noqa: E402
    import point_solve as ps                                                 # noqa: E402
    import point_style as pstyle                                             # noqa: E402


SUFFIX_CARRIER = "__surface"
SUFFIX_EDGE = "__edges"
SUFFIX_PAD = "__pad"
SUFFIX_COL = "-colonly"
SUFFIX_GORE = "__gore"

#: Godot reads the surface type off the proxy's own name suffix, the same convention the rest of
#: the kit already uses. Two proxies, never one merged: the navmesh and `ImpactManager` must be
#: able to tell a pavement from a road, and one mesh cannot say it.
COL_ROAD = "road"
COL_WALK = "walk"

#: A road whose `ped_access` is False routes its proxy to a layer `NavBaker` skips. Without this a
#: PIER deck bakes as walkable and an on-ramp is a continuous walkable slope -- AI walk onto the
#: expressway. `AGENT_MAX_CLIMB = 0.5` already makes a 0.15 m kerb climbable and a 1.0 m
#: expressway wall not, so the at-grade case is right already; the elevated case is not.
NO_PED_SUFFIX = "-noped"

#: Road layer -> the key in `kit_common.MATS`, THE repo's one material registry.
#:
#: This used to be a second registry: `point_build` get-or-created its own `rka_asphalt`,
#: `rka_concrete`, `rka_median`, `rka_footway` and `rka_barrier` as flat colours, in parallel with
#: `kit_common.MATS`, which every other builder in the repo already shared -- and which had
#: carried `M_LineW` and `M_LineY`, described in its own source as "white lane line" and "yellow
#: lane line", with no user at all. Two registries meant a road's asphalt was a different
#: datablock from a car park's, and the only materials in the file that were FOR roads were the
#: ones the roads could not reach.
#:
#: The footway deliberately takes `concrete_tile` rather than a flat grey: it is the procedural
#: world-position checker built precisely so a paved surface survives a curved corner without the
#: UV pinch a tangent-frame pattern gets, and a footway wrapping a junction fillet is exactly the
#: case it was built for.
#:
#: AND THE DATABLOCK ITSELF NOW COMES FROM `assets/` (2026-09-05). `kit_common.mat()` LINKS every
#: material from `assets/world_source/kit/road_kit.blend` instead of creating a copy in whichever
#: `.blend` is building, so the road's asphalt, the kerb SECTION's concrete and a building's
#: concrete are one datablock authored in a file an artist can open. This module keeps exactly the
#: one resolver it had -- `material(key)` -> `kc.mat(...)` -- because that is where the change
#: belongs; nothing here needed a second lookup.
MATERIAL_KEYS = {
    "asphalt": "asphalt",
    "concrete": "concrete",
    "footway": "concrete_tile",
    "median": "median",
    "barrier": "barrier",
    "line_w": "line_w",
    "line_y": "line_y",
}


def material(key):
    """The material for a road layer, from `kit_common` -- one registry, shared with the world.

    Get-or-create by name, so a rebuild reuses the same datablock and any hand-edited shading on
    it survives, exactly as before."""
    return kc.mat(MATERIAL_KEYS.get(key, key))


def named_material(name, fallback_key):
    """A material by DATABLOCK NAME (a style slot), falling back to the layer's default.

    Names, not pointers, because `<stem>.roads.json` is the source of truth and has to round-trip
    -- the same reason every other authored reference in this model is a name. A name that
    resolves to nothing falls back rather than building a black road, and `point_validate` says
    so; a silently missing material is indistinguishable from a shading mistake."""
    if name:
        mat = bpy.data.materials.get(name)
        if mat is not None:
            return mat
    return material(fallback_key)


# ------------------------------------------------------------------------------- GEN lifetime

def _local(collections, name):
    """Local-only lookup. Linked libraries carry same-named collections (several linked `STREET`s
    from `link_neighbors.py`), and picking one up would have this build clear a NEIGHBOUR's
    geometry."""
    for c in collections:
        if c.name == name and c.library is None:
            return c
    return None


def gen_root(scene=None):
    """`ROAD_MANAGER_GEN`, created on demand. Never holds anything hand-authored."""
    scene = scene or bpy.context.scene
    c = _local(bpy.data.collections, pm.ROAD_MANAGER_GEN)
    if c is None:
        c = bpy.data.collections.new(pm.ROAD_MANAGER_GEN)
    if _local(scene.collection.children, pm.ROAD_MANAGER_GEN) is None:
        scene.collection.children.link(c)
    return c


def gen_group(name, scene=None):
    """A per-road child of `ROAD_MANAGER_GEN`, emptied before use. Emptying by NAME PREFIX would
    be the bug: two roads called `art` and `art_2` share one."""
    root = gen_root(scene)
    c = _local(root.children, name)
    if c is None:
        c = bpy.data.collections.new(name)
        root.children.link(c)
    clear_collection(c)
    return c


def clear_collection(coll):
    """Free every object in a generated collection, recursively. Objects, not just links: an
    unlinked object is a zero-collection zombie held by its users and survives Purge Orphans."""
    for child in list(coll.children):
        clear_collection(child)
        bpy.data.collections.remove(child)
    for o in list(coll.objects):
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)


def clear_all(scene=None):
    """Wipe every generated object. `ROAD_MANAGER` is not touched -- that is rule 1."""
    root = _local(bpy.data.collections, pm.ROAD_MANAGER_GEN)
    if root is not None:
        clear_collection(root)


# ------------------------------------------------------------------------------- ground sampling

#: Collections whose meshes are terrain outright. A district authored for this kit puts its ground
#: in one of these.
TERRAIN_COLLECTIONS = ("TERRAIN", "GROUND", "MANUAL")

#: ...and the name every district BAKED BY THIS REPO'S PIPELINE gives its ground, which is not in
#: any of those. A grid district's terrain is `District_<theme>_<gx>_<gy>_Terrain-col`, and it
#: lives in `STREET` beside 1000-odd buildings -- so a collection allowlist alone finds no terrain
#: at all on every district the world is actually made of.
TERRAIN_NAME_TOKEN = "Terrain"


def is_terrain(obj):
    """Is this mesh the GROUND? The one owner of that question.

    THE SAMPLER AND THE CUT MUST AGREE, and they did not: `terrain_objects` looked only inside
    `TERRAIN_COLLECTIONS` (finding nothing on a real district, so the ground cut silently never
    ran), while `ground_sampler` raycast the WHOLE scene and took whatever it hit first. Measured
    on `Piece_3_1`, 300 downward rays: 134 hit the terrain, **96 hit buildings** -- up to 66.2 m,
    a rooftop -- and 70 hit the district's previously baked road. A third of a road's stations
    would have sampled their ground off a roof, and the supports are derived from exactly that
    number.

    Neither half was visible on the synthetic sample network, which has no buildings and no
    terrain. It took one probe against a real district."""
    if obj is None or obj.type != 'MESH' or obj.library is not None:
        return False
    if obj.name.startswith("rka_"):
        return False
    if TERRAIN_NAME_TOKEN in obj.name:
        return True
    return any(c.name in TERRAIN_COLLECTIONS for c in obj.users_collection)


def ground_sampler(scene=None, depsgraph=None, top=2000.0):
    """`f(x, y) -> z` -- a downward raycast against the scene's terrain, or None when there is no
    terrain to hit.

    Returned as a CLOSURE and passed into the solve, rather than a method the solve calls, so
    `point_solve` stays free of bpy and every one of its numbers stays testable under plain
    `python3`. 3.3 rule 1: Build calls this unconditionally -- there is no "sample ground" button
    to forget, because forgetting it is the confirmed cause of the mesh-hole reports."""
    scene = scene or bpy.context.scene
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()

    # A ROAD'S OWN OUTPUT IS NOT TERRAIN, and getting this wrong is not a cosmetic bug: the ray
    # lands on the surface the LAST build swept, so `ground_z` climbs to the road's own height,
    # the support flips to NONE, and every rebuild walks the road a little further up. The first
    # version of this check tested the collection NAME for a `ROAD_MANAGER_GEN` prefix -- and
    # generated collections are named after their ROAD (`main`, `cross`, `ramp`), so it matched
    # nothing and skipped nothing. Membership, computed once, is the answer; a name is not.
    skip = gen_collection_names()

    #: How many non-terrain hits one cast may punch through before giving up.
    #:
    #: THIS WAS 8, AND 8 WAS A GUESS ("a city block is a building, its collision proxy, maybe a
    #: canopy -- not thirty things"). Measured on the shrine touge's hairpins, where a road passes
    #: under ITSELF above an always-resident island collision layer, a ray reaches the ground only
    #: after **9 to 12** non-terrain hits: the road's own cutter solid (2-4 times, since a
    #: switchback's cutter overlaps itself), `Ground-colonly`, then the surface, kerb runs and lane
    #: markings of every loop it passes under. At 8 the ray gave up, `sample()` returned None,
    #: `_cut_section` read a daylight height of 0 and the apex was never cut -- the same silent
    #: shape as everything else in this story.
    #:
    #: It is not a budget, it is a runaway guard, and the loop already has a real one (`nz >= z`
    #: stops a degenerate hit spinning). Each punch is one raycast and is only paid where geometry
    #: is actually stacked, so a cap with room in it costs nothing on the 99% of the world that is
    #: one road on one ground.
    max_punch = 64

    def sample(x, y):
        """The GROUND under `(x, y)`, punching through everything that is not it.

        NOT "whatever the first ray hits". A district is a thousand buildings and a previously
        baked road standing on the terrain, and the first hit is one of those two thirds of the
        time -- so the ray restarts just below each non-terrain hit until it reaches the ground or
        runs out of scene. Taking the first hit put a road's `ground_z` on a 66 m rooftop, and
        every support is derived from that number."""
        z = top
        for _ in range(max_punch):
            hit, loc, _n, _i, obj, _m = scene.ray_cast(
                depsgraph, (x, y, z), (0.0, 0.0, -1.0))
            if not hit or obj is None:
                return None
            # A ROAD'S OWN OUTPUT IS NOT TERRAIN -- and neither is the road it replaces, nor a
            # building. Punch through and keep going down.
            if not any(c.name in skip for c in obj.users_collection) and is_terrain(obj):
                return loc.z
            nz = loc.z - 1e-3
            if nz >= z:                       # no progress: a degenerate hit, stop rather than spin
                return None
            z = nz
        return None

    return sample


def gen_collection_names():
    """Every collection name inside `ROAD_MANAGER_GEN`, root included. Recomputed on demand: a
    build creates a per-road child, so a cached set is stale exactly when it matters."""
    root = _local(bpy.data.collections, pm.ROAD_MANAGER_GEN)
    if root is None:
        return frozenset()
    names, stack = {root.name}, [root]
    while stack:
        c = stack.pop()
        for child in c.children:
            if child.name not in names:
                names.add(child.name)
                stack.append(child)
    return frozenset(names)


# ------------------------------------------------------------------------------- the carrier

def _carrier_mesh(name, solve):
    """One polyline, every `CARRIER_ATTRS` value written onto the point domain.

    ASSERTS the full attribute set. A Named Attribute node pointing at a name the mesh does not
    carry reads 0 and sweeps a zero-width band -- silently -- which at thirty-odd names is
    indistinguishable from "my change had no effect" (3.1)."""
    me = bpy.data.meshes.new(name)
    verts = [tuple(s.pos) for s in solve.samples]
    edges = [(i, i + 1) for i in range(len(verts) - 1)]
    if solve.is_loop and len(verts) > 2:
        edges.append((len(verts) - 1, 0))
    me.from_pydata(verts, edges, [])
    me.update()
    for a in ps.CARRIER_ATTRS:
        att = me.attributes.new(name=a.name, type='FLOAT', domain='POINT')
        att.data.foreach_set("value", [float(v.get(a.name, a.default)) for v in solve.values])
    missing = [a.name for a in ps.CARRIER_ATTRS if me.attributes.get(a.name) is None]
    assert not missing, "carrier is missing declared attributes: %s" % missing
    return me


def _mesh_object(name, me, coll):
    o = bpy.data.objects.new(name, me)
    coll.objects.link(o)
    return o


def build_carrier(solve, coll, name, style=None):
    """`<road>__surface` -- the swept road. One object, N modifiers."""
    obj = _mesh_object(name + SUFFIX_CARRIER, _carrier_mesh(name + SUFFIX_CARRIER, solve), coll)
    build_stack(obj, surface_spec(style))
    return obj


def _polyline_object(name, pts, coll, values=None, closed=False):
    """A carrier polyline carrying PER-POINT attribute dicts -- exactly how a kerb run, a footway
    run and a marking line are all emitted. One shape, so a new piece of edge furniture is a call,
    not a code path. `values` is `[{attr: v}]`, one per point; anything absent takes its declared
    default."""
    me = bpy.data.meshes.new(name)
    edges = [(i, i + 1) for i in range(len(pts) - 1)]
    if closed and len(pts) > 2:
        edges.append((len(pts) - 1, 0))
    me.from_pydata([tuple(p) for p in pts], edges, [])
    me.update()
    vals = values or [{}] * len(pts)
    for a in ps.CARRIER_ATTRS:
        att = me.attributes.new(name=a.name, type='FLOAT', domain='POINT')
        att.data.foreach_set("value", [float(v.get(a.name, a.default)) for v in vals])
    return _mesh_object(name, me, coll)


# ------------------------------------------------------------------------------------ the stack

def _layer(name, inner, offset=0.0, offset_attr="", z=0.0, z_attr="", require_attr="", **inputs):
    return {"name": name, "inner": inner, "offset": offset, "offset_attr": offset_attr,
            "z": z, "z_attr": z_attr, "require_attr": require_attr, "inputs": inputs}


#: Where a PROFILE ASSET's origin sits for each slot -- the line the road hands it, which is not
#: always the line the parametric layer is anchored on.
#:
#: A section is authored standing on its own base at (0, 0), so an asset is placed by its FOOT.
#: The parametric layers are not all anchored that way: the kerb is a bar hung down from its top
#: and the barrier likewise, so reusing their `z_attr` for an asset floats the section a whole
#: wall-height into the air. Measured on the jersey barrier: 0.96 m up instead of standing on the
#: footway. One entry per slot that can take an asset, and the ones that already agree say so.
#: What must be non-zero somewhere on a carrier for a slot's ASSET to build at all.
#:
#: An asset layer takes no `WidthAttr`/`ThicknessAttr` -- the section's own size is its size -- and
#: those attributes were what `layer_has_content` gated on. Without this an asset barrier is built
#: along EVERY road that names one, including the at-grade street where `solve_road` decided there
#: should be no barrier: measured, a jersey barrier down both flanks of a pedestrian street whose
#: parametric wall was correctly zero. The question "does this layer have content" is the same
#: question either way; only the attribute that answers it moves.
ASSET_REQUIRE = {
    "kerb": "rka_curb_hl",
    "footway": "rka_walk_hl",
    "barrier": "rka_wall_h",
    "median": "rka_med_h",
}

ASSET_Z_ATTR = {
    "kerb": "",                 # the kerb line, at road level -- the carrier polyline itself
    "footway": "rka_walk_zl",   # on top of the kerb, which is where the parametric band is too
    "barrier": "rka_wall_foot", # the foot, NOT `rka_wall_z`, which is the top
    "median": "rka_med_z",
    "surface": "",
}


def _styled(style, slot, name, inner, flip=False, **kw):
    """One layer, taking its material -- and possibly its whole SHAPE -- from the road's style.

    If the slot names a profile asset, the layer becomes a swept section of the artist's own
    geometry (`GN_PointProfile`) instead of the parametric band, and its material comes off the
    asset (because `Curve to Mesh` drops it -- see `point_style`). Otherwise nothing changes:
    same group, same attributes, only the material is now a slot rather than a constant.

    The asset REPLACES the layer rather than adding one, because the two build the same thing.
    Sweeping both is how a road ends up with a parametric kerb inside a modelled one, z-fighting
    along its entire length."""
    if style is None:
        return _layer(name, inner, **kw)
    asset = style.asset(slot)
    if asset is not None:
        mat = pstyle.asset_material(asset) or style.material(slot)
        # An asset sweep takes NO width/thickness attributes: its own dimensions are its size.
        # Passing them would scale the artist's section by a design number -- the exact mismatch
        # that once put a 3.0 m footway piece where 3.5 m was assumed.
        keep = {k: v for k, v in kw.items()
                if k in ("offset", "offset_attr", "z", "require_attr")}
        keep["z_attr"] = ASSET_Z_ATTR.get(slot, kw.get("z_attr", ""))
        keep["require_attr"] = (kw.get("require_attr")
                                or ASSET_REQUIRE.get(slot, "")
                                or kw.get("WidthAttr", ""))
        return _layer(name, gn.make_profile_group(), Profile=asset, Flip=bool(flip),
                      Material=mat, **keep)
    kw["Material"] = style.material(slot)
    return _layer(name, inner, **kw)


def surface_spec(style=None):
    """THE ROAD SURFACE, AS DATA. Adding a band is one entry here, not a node tree (3.3a).

    Every entry names the attributes it reads, and every one of those names is declared in
    `point_solve.CARRIER_ATTRS`. Nothing in this list computes a lateral offset -- each layer is
    handed one, and every offset came from `lane_profile.slot_offset` by way of the solve.

    NOTE WHAT IS NOT HERE: the kerb and the footway. They ride the OUTLINE, on their own carriers
    over `point_edges.open_runs`, because a kerb swept along the road's own centreline runs
    straight through the asphalt at every gore and merge (3.2 -- measured at 257 of 3736 samples
    on the previous model). Keeping them out of this list is what makes that structural rather
    than a rule someone has to remember."""
    band, deck = gn.make_band_group(), gn.make_deck_group()
    pillars = gn.make_pillars_group()
    # WHICH median material follows `median_style`, which is a fact about the divide, not a
    # separate choice: a painted divide is paint and a raised one is an island, and asking the
    # artist to keep a style enum and a material slot agreeing is how they come to disagree. An
    # explicitly named `median_mat` still wins -- `point_style` only falls back to the default.
    med_slot = "median"
    if style is not None and getattr(style.road, "median_style", None) == pm.MED_PAINT:
        med_slot = "mark_y"
    return [
        _styled(style, "surface", "Carriageway", band, offset_attr="rka_shift",
                WidthAttr="rka_halfw", Material=material("asphalt")),
        # A PAINTED median is flush with the road, which is the same coplanar-surface trap the
        # deck fell into, just narrower -- so lift the paint by the matching bias. A raised median
        # already clears the asphalt and is unaffected.
        _styled(style, med_slot, "Median", band, WidthAttr="rka_med_h", z=ps.PAINT_Z_BIAS,
                z_attr="rka_med_z", Material=material("median")),
        # THE DECK TOP MUST SIT BELOW THE ROAD, NOT ON IT -- a top face at z = 0 is coplanar with
        # the asphalt over the entire road, which is z-fighting across the whole network. It spans
        # the FULL outline (`rka_deck_w`), not just the carriageway, so a viaduct carrying a
        # footway carries the footway too.
        _styled(style, "deck", "Deck", deck, offset_attr="rka_deck_c", z=ps.DECK_Z_BIAS,
                WidthAttr="rka_deck_w", ThicknessAttr="rka_deck_h",
                Material=material("concrete")),
        _layer("Pillars", pillars, offset_attr="rka_deck_c", SpacingAttr="rka_sp_pillar",
               Material=(style.material("deck") if style else material("concrete")),
               require_attr="rka_pillar_param"),
    ]


def edge_spec(style=None, sgn=1.0):
    """THE EDGE FURNITURE, swept along an `__edges` carrier whose polyline IS the kerb line.

    `sgn` is the side (+1 = the polyline's left), and it matters only to a PROFILE ASSET: the
    parametric bands are symmetric and carry the side in their offset value, but an artist's
    asymmetric section has to be mirrored to face outward on the right-hand flank.

    Same two node groups, same attribute names, no second implementation of "what a kerb looks
    like" -- which is the point. Because the polyline is already the line, `rka_curb_ol` is 0 on
    these carriers and the footway is offset outboard by its own half-width; the sign is carried in
    the value, so one spec serves both sides."""
    band, deck = gn.make_band_group(), gn.make_deck_group()
    return [
        _styled(style, "kerb", "Curb", deck, flip=(sgn < 0.0),
                offset_attr="rka_curb_ol", z_attr="rka_curb_hl",
                WidthAttr="rka_curb_tl", ThicknessAttr="rka_curb_hl",
                Material=material("concrete")),
        _styled(style, "footway", "Sidewalk", band, flip=(sgn < 0.0),
                offset_attr="rka_walk_cl", z_attr="rka_walk_zl", WidthAttr="rka_walk_hl",
                Material=material("footway")),
        # THE BARRIER, and it belongs here for the same structural reason the kerb does: swept
        # along the OUTLINE, so `point_edges.open_runs` opens it wherever another road's asphalt
        # is -- at a gore, at a merge, at a junction mouth. A wall on the centreline would run
        # straight across the ramp join, which is the "sometimes missed an entire section of wall"
        # failure the previous model never got on top of. It is the same `deck` group as the kerb:
        # a bar of `WidthAttr` half-thickness whose TOP is at `z_attr`, extruded down by its
        # height, so there is no second idea of what a wall is either.
        _styled(style, "barrier", "Barrier", deck, flip=(sgn < 0.0),
                offset_attr="rka_wall_c", z_attr="rka_wall_z",
                WidthAttr="rka_wall_hw", ThicknessAttr="rka_wall_h",
                Material=material("barrier")),
    ]


def _attr_values(mesh, name):
    att = mesh.attributes.get(name)
    if att is None or not hasattr(att, "data"):
        return None
    try:
        return [d.value for d in att.data]
    except AttributeError:
        return None


def layer_has_content(mesh, entry):
    """Would this layer build anything on THIS mesh?

    A layer whose width is zero everywhere still gets swept: Geometry Nodes happily extrudes a
    zero-width band and emits the polygons anyway. On the previous model that swept the full
    thirteen-layer road stack over every junction corner, producing 11,400 concrete polygons
    totalling 392 m2 of real area. Asking the mesh is better than hand-listing which layers a
    kerb run gets: it stays correct when a layer is added."""
    inputs = entry.get("inputs") or {}
    req = entry.get("require_attr")
    if req:
        vals = _attr_values(mesh, req)
        if vals is None or not any(abs(v) > 1e-6 for v in vals):
            return False
    for key in ("WidthAttr", "ThicknessAttr"):
        name = inputs.get(key)
        if not name:
            continue
        vals = _attr_values(mesh, name)
        if vals is None or not any(abs(v) > 1e-6 for v in vals):
            return False
    return True


def _set(mod, ids, name, value):
    """Set one Geometry Nodes modifier input by interface-socket identifier.

    NOT `mod[socket_id] = value`: this Blender's `NodesModifier` does not support IDProperties at
    all, for any socket type. Inputs live on `mod.properties.inputs`, whose per-socket attributes
    are read-only pointers to a struct carrying the mutable `.value`."""
    if name in ids:
        getattr(mod.properties.inputs, ids[name]).value = value


def build_stack(carrier_obj, spec=None):
    """(Re)build the carrier's modifier stack: head, every layer that has content, finish.

    Rebuilt wholesale rather than reconciled -- the stack is DERIVED from the spec, and
    reconciling a live stack against a spec is exactly the bookkeeping this design deletes. It is
    cheap: modifiers hold no geometry."""
    for m in list(carrier_obj.modifiers):
        carrier_obj.modifiers.remove(m)
    head = carrier_obj.modifiers.new("Spine", 'NODES')
    head.node_group = gn.make_spine_group()
    for s in (spec if spec is not None else surface_spec()):
        if not layer_has_content(carrier_obj.data, s):
            continue
        wrapper, ids = gn.wrap_layer(s["inner"], "GN_PointLayer_" + s["inner"].name)
        mod = carrier_obj.modifiers.new(s["name"], 'NODES')
        mod.node_group = wrapper
        _set(mod, ids, "Offset", float(s.get("offset", 0.0)))
        _set(mod, ids, "OffsetAttr", s.get("offset_attr", "") or "")
        _set(mod, ids, "ZOffset", float(s.get("z", 0.0)))
        _set(mod, ids, "ZOffsetAttr", s.get("z_attr", "") or "")
        for k, v in (s.get("inputs") or {}).items():
            if v is not None:
                _set(mod, ids, k, v)
    tail = carrier_obj.modifiers.new("Finish", 'NODES')
    tail.node_group = gn.make_finish_group()
    return carrier_obj


# ------------------------------------------------------------------------------- edge furniture

def edge_run_values(walk, kerb, wall, sgn):
    """Per-vertex `edge_spec()` attributes for ONE edge run, given what it carries and which way
    its furniture faces (`sgn`: +1 = the polyline's left).

    ONE OWNER, and that is the point of it: a road's flank, a junction corner and a gore's nose
    are all "a polyline that IS the kerb line", they all sweep the same stack, and three separate
    copies of this arithmetic is how the wall on one of them ends up half a kerb-width from where
    it is on the other two. `walk` is a HALF-width throughout, as `rka_walk_hl` is.

    The kerb's own lateral offset is zero -- the polyline is already the line. The footway sits
    outboard of it by its own half-width, on top of the kerb. The barrier stands at the OUTBOARD
    edge of whatever furniture is there: on the deck edge past the footway when there is one,
    right on the kerb line when there is not."""
    out = []
    half_t = ps.BARRIER_THICKNESS * 0.5
    for k in range(len(kerb)):
        h, w, wl = float(kerb[k]), float(walk[k]), float(wall[k])
        out.append({
            "rka_curb_ol": 0.0,
            "rka_curb_hl": h,
            "rka_curb_tl": h * ps.KERB_THICKNESS,
            "rka_walk_cl": sgn * w,
            "rka_walk_hl": w,
            "rka_walk_zl": h,
            "rka_wall_h": wl,
            "rka_wall_hw": half_t if wl > 0.0 else 0.0,
            "rka_wall_c": sgn * (2.0 * w + half_t),
            "rka_wall_z": h + wl,
            # The wall's FOOT: the top of whatever it stands on (the kerb, and the footway is
            # level with it). A parametric barrier hangs down from `rka_wall_z`; a profile ASSET
            # is drawn standing on its base and needs this instead. Same wall, both ends named.
            "rka_wall_foot": h,
        })
    return out


def build_edge_run(points, walk, kerb, wall, sgn, coll, name, style=None):
    """One `__edges` carrier: the polyline, its per-vertex furniture, the `edge_spec()` stack."""
    o = _polyline_object(name, points, coll, edge_run_values(walk, kerb, wall, sgn))
    build_stack(o, edge_spec(style, sgn))
    return o


def build_edges(solve, bands, coll, name, style=None):
    """`<road>__edges_<side>_<n>` -- kerb and footway, over the OPEN RUNS only.

    THIS IS WHERE THE GORE OPENS, and nothing here knows what a gore is. `point_edges.kerb_runs`
    reports the stretches of each paved boundary that are not buried in another road's asphalt;
    the furniture is built on those and nowhere else. There is no `RAMP_WALL_OPEN` constant, no
    merge-corridor solve and no ramp-specific branch -- the previous model needed all three and
    still "sometimes missed an entire section of wall"."""
    out = []
    runs = pe.kerb_runs(solve, bands)
    for side, edge in (("left", solve.edges_left), ("right", solve.edges_right)):
        sgn = 1.0 if side == "left" else -1.0
        kerb_key = "rka_curb_hl" if side == "left" else "rka_curb_hr"
        walk_key = "rka_walk_hl" if side == "left" else "rka_walk_hr"
        for n, run in enumerate(runs[side]):
            pts = pe.sub_polyline(edge, run)
            if len(pts) < 2:
                continue
            # `run_values` and `sub_polyline` are two readings of the same run, clipped ends and
            # all, so the polyline and its attributes cannot come out different lengths.
            vals = pe.run_values(solve.values, run)
            out.append(build_edge_run(
                pts,
                [v[walk_key] for v in vals],
                [v[kerb_key] for v in vals],
                [v["rka_wall_h"] for v in vals],
                sgn, coll, "%s%s_%s_%d" % (name, SUFFIX_EDGE, side, n), style))
    return out


# ------------------------------------------------------------------------------- the markings

SUFFIX_MARKS = "__marks"


def mark_spec(style, yellow):
    """THE PAINT: one flush band, on the marking carrier whose polyline IS the line.

    Lifted by `PAINT_Z_BIAS` for the same reason the painted median is -- a stripe coplanar with
    the asphalt z-fights along its entire length, which at world scale is the whole road network
    flickering."""
    slot = "mark_y" if yellow else "mark_w"
    return [_layer("Paint", gn.make_band_group(), WidthAttr="rka_mark_w", z=ps.PAINT_Z_BIAS,
                   Material=(style.material(slot) if style is not None
                             else material("line_y" if yellow else "line_w")))]


def _polylines_object(name, chains, coll, values):
    """ONE mesh holding SEVERAL disconnected polylines, all carrying the same attributes.

    A dashed lane line is 113 separate pieces of paint on one run of the testbed; as 113 objects
    that is 113 modifier stacks, 113 evaluations and an outliner nobody can read. `Mesh to Curve`
    turns each disconnected chain into its own spline, so one object sweeps them all."""
    me = bpy.data.meshes.new(name)
    verts, edges = [], []
    for chain in chains:
        base = len(verts)
        verts.extend(tuple(p) for p in chain)
        edges.extend((base + i, base + i + 1) for i in range(len(chain) - 1))
    me.from_pydata(verts, edges, [])
    me.update()
    for a in ps.CARRIER_ATTRS:
        att = me.attributes.new(name=a.name, type='FLOAT', domain='POINT')
        att.data.foreach_set("value", [float(values.get(a.name, a.default))] * len(verts))
    return _mesh_object(name, me, coll)


def build_marks(solve, coll, name, style=None):
    """`<road>__marks_w` / `_y` -- every painted lane boundary on this run.

    Grouped by COLOUR and nothing else: a road's white lines are one object and its yellow ones
    another, whatever slot each divides, because that is exactly as fine as the material makes it
    worth splitting. Dashes are already separate polylines by the time they arrive.

    NOT COLLIDABLE, and that is deliberate rather than an oversight: `build_collision` is handed an
    explicit list of surfaces and this is not in it. Paint is 15 cm wide and 1 cm proud; a proxy
    per stripe would put a paper-thin `StaticBody3D` under every dashed line in the world for
    nothing a bullet, a wheel or a navmesh would ever want."""
    runs = ps.solve_marks(solve)
    out = []
    for yellow in (False, True):
        chains = [r.points for r in runs if r.yellow is yellow]
        if not chains:
            continue
        o = _polylines_object("%s%s_%s" % (name, SUFFIX_MARKS, "y" if yellow else "w"),
                              chains, coll, {"rka_mark_w": ps.MARK_WIDTH / 2.0})
        build_stack(o, mark_spec(style, yellow))
        out.append(o)
    return out


# ------------------------------------------------------------------------------- the pad

def build_pad(jsolve, coll, name, style=None):
    """One junction pad, tessellated by `point_solve.pad_triangles` and by nothing here.

    A fan and not an n-gon: n-gon tessellation of a concave, non-planar pad left measured
    0.38-0.49 m holes. But the fan's apex is the ring's KERNEL point, not the centroid -- and when
    even that does not exist the solve ear-clips -- so this always receives a watertight triangle
    list and a pad can no longer be a black crater OR a refused build. The gate still reports that
    the apex had to move, as a warning, because it usually means a mouth wants pulling out."""
    me = bpy.data.meshes.new(name + SUFFIX_PAD)
    tris = jsolve.fan
    verts, faces, seen = [], [], {}
    for tri in tris:
        face = []
        for v in tri:
            key = (round(v[0], 5), round(v[1], 5), round(v[2], 5))
            if key not in seen:
                seen[key] = len(verts)
                verts.append(tuple(float(c) for c in v))
            face.append(seen[key])
        if len(set(face)) == 3:
            faces.append(tuple(face))
    me.from_pydata(verts, [], faces)
    me.update()
    me.validate()
    o = _mesh_object(name + SUFFIX_PAD, me, coll)
    o.data.materials.append(style.material("surface") if style else material("asphalt"))
    return o


def build_junction_edges(jsolve, coll, name, style=None):
    """`JCT_*__edges_c<N>` -- the pad's own kerb and footway, one object per corner.

    Same `edge_spec()` as a road's edges, on the same kind of carrier, deliberately: a junction
    corner IS an edge run, it just happens to be an arc rather than a road's flank, and giving it
    its own idea of what a kerb looks like is how the two drift apart. What it fixes is a plain
    hole in the world -- every crossing was bare asphalt to its own boundary, with each street's
    footway stopping dead at its mouth.

    Outboard is to the RIGHT here: `intersection_kit` emits the pad's boundary CCW, so a corner
    that runs CCW around it has the outside on its right, which is the opposite of a road's LEFT
    flank. That sign is the only thing that differs from `build_edges`."""
    out = []
    for i, c in enumerate(jsolve.corners):
        if len(c.points) < 2:
            continue
        out.append(build_edge_run(c.points, c.walk, c.kerb, c.wall, -1.0, coll,
                                  "%s%s_c%d" % (name, SUFFIX_EDGE, i), style))
    return out


# ------------------------------------------------------------------------------- the gore

def build_gore(gsolve, coll, name, style=None):
    """The paved wedge where a ramp leaves its mainline, as a triangle strip.

    2.4's rule is edge alignment and NO pad -- and that is still right for the JOIN. But a join
    that is a line has nothing under it a metre later: the mainline's edge peels one way, the
    ramp's the other, and the widening wedge between them was nobody's geometry, so the demo read
    as a ramp glued to the side of a road with a hole beside it. This is not a pad (no ring, no
    fan, no fillets): it is a strip between the two roads' OWN paved edges, which is why it cannot
    drift away from either one, and it stops at the nose where a real gore's paint stops."""
    me = bpy.data.meshes.new(name + SUFFIX_GORE)
    verts, faces, seen = [], [], {}
    for tri in gsolve.tris:
        face = []
        for v in tri:
            key = (round(v[0], 5), round(v[1], 5), round(v[2], 5))
            if key not in seen:
                seen[key] = len(verts)
                verts.append(tuple(float(c) for c in v))
            face.append(seen[key])
        if len(set(face)) != 3:
            continue
        # NORMALS UP, always. Which side of the mainline's edge the ramp's lies on flips with
        # `side`, so the strip's winding flips with it -- and a gore whose faces point at the
        # ground is invisible from above and shades black in-engine. The area sign in XY is the
        # cheapest correct test; a gore is never near-vertical.
        a, b, c = (verts[i] for i in face)
        area2 = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        # The pair at the theoretical gore is by definition nearly coincident, so the first
        # triangle is a sliver whose normal is float noise. Drop anything under a square
        # millimetre: it covers nothing and shades badly.
        if abs(area2) < 2e-3:
            continue
        if area2 < 0.0:
            face = [face[0], face[2], face[1]]
        faces.append(tuple(face))
    if not faces:
        bpy.data.meshes.remove(me)
        return None
    me.from_pydata(verts, [], faces)
    me.update()
    me.validate()
    o = _mesh_object(name + SUFFIX_GORE, me, coll)
    o.data.materials.append(style.material("surface") if style else material("asphalt"))
    return o


def build_gore_edges(gsolve, coll, name, style=None):
    """`GORE_*__edges_nose` -- the cap that closes the open V at the gore's wide end.

    A gore is bare paint, so both flanking walls OPEN across it (`point_edges.Band.carries_edge`
    is False for a gore, deliberately) -- right along the join, where a wall would stand in the
    exit lane, and wrong at the wide end, where the two roads have parted and their own walls
    restart `nose_gap` metres apart with nothing between them.

    Same `edge_spec()`, same carrier, same `build_edge_run` as a road's flank and a junction
    corner, for the same reason (8g): a gore with its own idea of what a wall looks like is how
    the two drift apart. WHAT it carries came from the two roads' own solved furniture
    (`point_solve._gore_nose`), so a highway's barrier meeting a ramp's barrier is a wall, an
    approach that declares a footway gets a kerbed island, and a pair that declares neither builds
    nothing -- which is what the empty check below is for, not a special case for expressways."""
    c = getattr(gsolve, "nose", None)
    if c is None or len(c.points) < 2:
        return []
    if math.dist(c.points[0][:2], c.points[-1][:2]) < 1e-3:
        return []                     # a degenerate cap: two coincident edges, nothing to close
    if not any(abs(v) > 1e-6 for v in list(c.kerb) + list(c.walk) + list(c.wall)):
        return []
    return [build_edge_run(c.points, c.walk, c.kerb, c.wall, gsolve.nose_sgn, coll,
                           name + SUFFIX_EDGE + "_nose", style)]


# ------------------------------------------------------------------- the ground cut lives ELSEWHERE
#
# THERE USED TO BE A BOOLEAN CUT HERE, and it was removed on 2026-09-06 rather than fixed again.
# `cut_ground` hung one BOOLEAN modifier per road band on the terrain mesh; `clear_cuts`,
# `_cut_tube`, `_cut_section`, `_outward_offsets` and `_self_intersects` all existed to serve it.
# Every defect it cost was a property of the tool, not of the road:
#
#   * the cutter was a zero-thickness sheet and no boolean had EVER cut anything (`W22`);
#   * the sampler raycast the terrain the PREVIOUS build had already cut, so a deep station found
#     no ground, read a daylight height of 0 and was never cut again (`W13`);
#   * a switchback's cutter passes through itself, and an exact solver has no defined inside for a
#     doubly-covered region: it leaves the ground standing, silently. From identical inputs -- the
#     same network, the same terrain, the same sampled `ground_z` byte for byte -- a second Build
#     took the touge from 2 buried stations to 9;
#   * and it could never be applied to the COLLIDER, because a boolean removes material and the
#     always-resident ground is the one layer that must never have a hole (`W21`).
#
# THE ROAD DEFORMS THE GROUND NOW, which is what every open-world pipeline does. Terrain is a
# heightfield, a heightfield has no topology to cut, and from Unreal's `Deform Landscape to
# Splines` to a Houdini road HDA the operation is to write the road's elevation INTO the field.
# `island_v3_terrain.Carve` is that rule, `build_island_v3.build_ground(carve=...)` samples it, and
# `road_corridors` above is this module's whole contribution: the centrelines and widths of what it
# just built. A `min` cannot open a hole, so the collider is now a copy of the visual ground and
# `W21` is closed by construction rather than worked around.
#
# WHAT THIS COSTS, recorded rather than hidden: the carve needs a terrain that is a FIELD, so it
# serves the island (whose ground is generated from `island_v3_terrain`) and not a district whose
# ground is a hand-modelled mesh. No such district exists -- the world is one continuous island by
# `WORLD_REBUILD_PLAN.md` step 3 -- and a hand-modelled terrain would want the artist to model its
# own cut faces anyway, which is also what production does for a benched mountain road.



# ------------------------------------------------------------------------------- collision

def _evaluated_copy(obj, name, coll):
    """A plain mesh copy of `obj` AS THE VIEWER SEES IT -- modifiers applied.

    Copying the truth rather than approximating it: the previous kit's hand-rolled proxies (a
    corner-squared-off pad boundary that ignored the fillet radius, a per-vertex swept wall)
    visibly diverged from the real curved geometry, which reads in-game as an invisible wall in a
    place that looks walkable."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=False, depsgraph=depsgraph)
    me.name = name
    o = bpy.data.objects.new(name, me)
    o.matrix_world = obj.matrix_world.copy()
    coll.objects.link(o)
    return o


def collision_name(base, kind, ped_access):
    """`<base>-<kind>[-noped]-colonly`.

    TWO PROXIES, NEVER ONE MERGED. `NavBaker` needs to know a pavement from a road, and
    `ImpactManager.resolveSurfaceType` needs an asphalt-or-concrete answer -- one merged proxy
    cannot say either. The `-noped` marker routes a proxy to the layer `NavBaker` skips, which is
    what stops an on-ramp baking as a continuous walkable slope onto the expressway (3.5)."""
    return "%s-%s%s%s" % (base, kind, "" if ped_access else NO_PED_SUFFIX, SUFFIX_COL)


def build_collision(surface_objs, edge_objs, coll, name, ped_access):
    """A `-colonly` proxy per road run: one for the carriageway, one for the footway/kerb.

    Road collision stays a SEPARATE mesh from ground/terrain collision -- they change on
    independent schedules, and merging them means a road edit re-bakes the terrain."""
    out = []
    for objs, kind, ped in ((surface_objs, COL_ROAD, False), (edge_objs, COL_WALK, ped_access)):
        parts = [o for o in objs if o is not None]
        if not parts:
            continue
        merged = None
        for o in parts:
            c = _evaluated_copy(o, "%s_tmp_%s" % (name, kind), coll)
            if merged is None:
                merged = c
                continue
            merged.data = _joined(merged, c)
            bpy.data.objects.remove(c, do_unlink=True)
        if merged is None:
            continue
        merged.name = collision_name(name + "_" + kind, kind, ped)
        merged.data.name = merged.name
        merged.display_type = 'WIRE'
        merged.hide_render = True
        out.append(merged)
    return out


def _joined(a, b):
    """Merge `b`'s mesh into `a`'s and return the new mesh. `bmesh` rather than
    `bpy.ops.object.join`, which needs an active object and a context override and is the usual
    reason a headless build dies three steps later."""
    bm = bmesh.new()
    bm.from_mesh(a.data)
    tmp = bmesh.new()
    tmp.from_mesh(b.data)
    me = bpy.data.meshes.new(a.data.name)
    tmp.to_mesh(me)
    bm.from_mesh(me)
    bpy.data.meshes.remove(me)
    tmp.free()
    out = bpy.data.meshes.new(a.data.name)
    bm.to_mesh(out)
    bm.free()
    return out


# ------------------------------------------------------------------------------- the whole build

def terrain_objects(scene=None):
    """The meshes the ground cut applies to -- `is_terrain`, and nothing else.

    THE SAME QUESTION THE SAMPLER ASKS, through the same function. This used to scan a fixed list
    of collection names and so found NO terrain on any district the pipeline actually bakes (whose
    ground sits in `STREET`), which meant the ground cut quietly did nothing on real content while
    passing every test on the synthetic sample. Local-only, so a linked neighbour's terrain is
    never cut by this district."""
    scene = scene or bpy.context.scene
    skip = gen_collection_names()
    out, seen = [], set()
    for o in scene.objects:
        if o.name in seen or not is_terrain(o):
            continue
        if any(c.name in skip for c in o.users_collection):
            continue
        seen.add(o.name)
        out.append(o)
    return out


#: Set on the scene by whatever deforms the terrain to the roads
#: (`build_island_base.carve_ground_to_roads`). Its ONE reader is `write_ground_back`, which must
#: not mistake a carved ground for the natural one -- see there.
CARVED_FLAG = "rka_ground_carved"


def write_ground_back(net, solves, ground, scene=None):
    """Stamp the sampled `ground_z` onto the AUTHORED Empties, not just the transient network.

    THE BUG THIS FIXES, and why it was invisible: `build_network` reads a fresh `NetworkData` and
    the solve writes the sampled ground onto THAT -- which the operator then drops on the floor.
    So the panel's "Ground Z (sampled)" readout stayed 0 forever, `.roads.json` never carried a
    ground height, and the gate's `ground_unsampled` warning could never clear no matter how many
    times Build ran. Nothing failed; the number simply never arrived. Found by driving the whole
    plugin end to end and reading the gate's own output afterwards.

    A MISS IS NOT A SAMPLE. `has_ground_z` is set only where the raycast actually hit something --
    a road over water, or past the terrain's edge, keeps whatever it had and keeps saying so.
    Claiming a sample that never happened would silently hand the support solver a 0.

    AND IT MUST NOT RUN AGAINST A GROUND THAT IS ALREADY CARVED (`CARVED_FLAG`, 2026-09-06). The
    ground the terrain builder leaves in the file has the roads cut into it, so a second Build in
    that file samples a surface that MEETS the road and stamps that back as "the natural ground
    here". Measured on `Island_base`: **195 of 225 stations rewritten, the biggest by 9.46 m**, and
    a benched stretch silently reclassified from CUT to at-grade. It changes no geometry today --
    a CUT's batter is the terrain's now, so a CUT that reads as NONE builds the same nothing -- but
    it destroys the record of what the natural ground was, which is the input every support
    decision is made from. So it is refused with a message rather than done quietly; the tool path
    is unaffected because `build_island_base` always emits the NATURAL ground before it seeds.

    Returns `(hits, misses)`."""
    if ground is None:
        return (0, 0)
    if scene is not None and scene.get(CARVED_FLAG):
        print("point_build: ground_z NOT re-sampled -- this file's terrain is carved to its roads. "
              "Rebuild the natural ground first (build_island_base.py) if you need it re-derived.")
        return (0, 0)
    by_uid = {}
    for coll in _local_road_collections(scene):
        for o in coll.objects:
            pt = getattr(o, "rka_pt", None)
            if pt is not None and pt.is_point and pt.uid:
                by_uid[pt.uid] = o
    hits = misses = 0
    for s in solves:
        for sm in s.samples:
            if sm.at_station is None:
                continue
            uid = s.uids[sm.at_station]
            z = ground(sm.pos[0], sm.pos[1])
            if z is None:
                misses += 1
                continue
            hits += 1
            data = net.points.get(uid)
            if data is not None:
                data.ground_z, data.has_ground_z = z, True
            obj = by_uid.get(uid)
            if obj is not None:
                obj.rka_pt.ground_z = z
                obj.rka_pt.has_ground_z = True
    return (hits, misses)


def _local_road_collections(scene=None):
    """Road collections under `ROAD_MANAGER`, local-only. Mirrors `point_model.road_collections`
    without importing it at module scope -- this module is imported BY the operators."""
    root = _local(bpy.data.collections, pm.ROAD_MANAGER)
    if root is None:
        return []
    return [c for c in root.children if c.library is None and c.name != pm.JUNCTIONS]


def _road_of(net, uid):
    """Which road a uid belongs to, or None. A pad and a gore each span more than one road and
    must still resolve ONE style; this is how each picks the road it takes it from."""
    for name, road in net.roads.items():
        if uid in road.points:
            return name
    return None


def solve_all(net, ground=None):
    """Solve every road, clique and gore, and collect the bands. `(solves, jsolves, gsolves, bands)`.

    ONE OWNER of the solve ORDER, because two things need it: `build_network`, which emits the
    meshes, and `road_corridors`, which the terrain builder asks for the ground carve. Order is not
    negotiable -- the edge furniture is a fact about TWO roads at once, so every carrier must exist
    before any band is collected."""
    solves, jsolves = [], ps.solve_junctions(net, ground_fn=ground)
    for road in net.roads.values():
        for uids in ps.road_runs(net, road):
            s = ps.solve_road(net, road, uids, ground)
            if s is not None:
                solves.append(s)
    # The gore is solved from the finished carriers, not alongside them: its two boundaries ARE
    # the two roads' own paved edges, so it cannot exist until both roads have some.
    gsolves = ps.solve_gores(net, solves)
    return solves, jsolves, gsolves, pe.collect_bands(solves, jsolves, gsolves)


def band_corridors(bands):
    """Every built surface as `(polyline, half)` for `island_v3_terrain.Carve` -- the ground the
    road needs cleared, expressed as a centreline the terrain can carve to.

    THE WIDTH IS READ OFF THE BAND, not from the road's authored numbers: `band.poly` is the paved
    outline (left edge out, right edge back), so the distance from a spine point to its own two
    edge points IS the half-width there, aux lanes, median tapers and all. One owner, and it cannot
    disagree with the mesh that was just swept from the same band.

    Junction pads and gores are bands too, and they are included on purpose -- a pad is a paved
    surface the ground must clear exactly as a carriageway is. An ELEVATED stretch needs no case:
    the carve is a `min`, so a deck 18 m over the bay proposes a ceiling far above the water and
    changes nothing."""
    out = []
    for band in bands:
        spine = list(getattr(band, "spine", ()) or ())
        poly = list(getattr(band, "poly", ()) or ())
        m = len(spine)
        if m < 2 or len(poly) != 2 * m:
            continue
        line = []
        for i, (sx, sy, sz) in enumerate(spine):
            lx, ly = poly[i][0], poly[i][1]
            rx, ry = poly[2 * m - 1 - i][0], poly[2 * m - 1 - i][1]
            half = max(math.hypot(lx - sx, ly - sy), math.hypot(rx - sx, ry - sy))
            # `sz` is the spine's OWN height, not `band.surface_z(sx, sy)` -- which is a
            # nearest-sample lookup over this very list and would answer with the same number by a
            # longer route, or with a neighbour's where two samples land close together.
            line.append((sx, sy, sz, half))
        out.append((line, 0.0))
    return out


def road_corridors(scene=None, net=None, ground=None):
    """The corridors for a ground carve, solved from the authored network. See `band_corridors`."""
    scene = scene or bpy.context.scene
    if net is None:
        net = pm.read_network(scene)
    if ground is None:
        ground = ground_sampler(scene)
    return band_corridors(solve_all(net, ground)[3])


def build_network(net, scene=None, sample_ground=True):
    """Build everything. Returns a report dict the panel and the smoketests both read.

    ORDER MATTERS AND IS NOT NEGOTIABLE: solve every road and every clique FIRST, collect the
    bands, and only then emit. The edge furniture is a fact about TWO roads at once -- how far a
    ramp is from the road it runs alongside -- so it cannot be answered inside a loop that only
    ever holds one. That was the previous model's `merge_corridor_ends` staging, and it is the one
    structural lesson from it worth keeping."""
    scene = scene or bpy.context.scene

    # THE GROUND THIS SAMPLES IS THE NATURAL ONE, and keeping it that way is the whole reason the
    # carve happens after the build rather than inside it: a road's profile is derived FROM the
    # ground, so sampling a ground already carved to the last pass derives a lower road, which
    # carves deeper, forever. See `island_v3_terrain.Carve` ("one direction of derivation") and
    # `build_island_base._emit_ground`.
    ground = ground_sampler(scene) if sample_ground else None

    solves, jsolves, gsolves, bands = solve_all(net, ground)

    report_ground = write_ground_back(net, solves, ground, scene)

    report = {"roads": 0, "runs": 0, "pads": 0, "gores": 0, "edges": 0, "marks": 0, "colonly": 0,
              "not_star": [], "objects": [], "ground": report_ground}
    clear_all(scene)
    by_road = {}
    for s in solves:
        by_road.setdefault(s.road.name, []).append(s)

    # ONE style per road, resolved once and handed to every layer -- rather than each layer
    # looking a name up for itself, which is how a kerb and the footway beside it come to resolve
    # the same slot differently.
    styles = {name: pstyle.resolve(road, material_fn=material)
              for name, road in net.roads.items()}
    for road_name, runs in by_road.items():
        coll = gen_group(road_name, scene)
        report["roads"] += 1
        style = styles.get(road_name)
        for slot, kind, missing_name in (style.missing() if style else ()):
            report.setdefault("missing_style", []).append((road_name, slot, kind, missing_name))
        for i, s in enumerate(runs):
            name = road_name if len(runs) == 1 else "%s_%d" % (road_name, i)
            surf = build_carrier(s, coll, name, style)
            edges = build_edges(s, bands, coll, name, style)
            marks = build_marks(s, coll, name, style)
            report["runs"] += 1
            report["edges"] += len(edges)
            report["marks"] = report.get("marks", 0) + len(marks)
            report["objects"].append(surf.name)
            cols = build_collision([surf], edges, coll, name, bool(s.road.ped_access))
            report["colonly"] += len(cols)

    if jsolves:
        jcoll = gen_group(pm.JUNCTIONS, scene)
        for j in jsolves:
            name = "JCT_" + j.uids[0][:8]
            if not j.star_ok:
                report["not_star"].append((name, round(j.star_worst, 3)))
            # A pad spans several roads and can only carry ONE look. It takes the style of the
            # road owning its first mouth -- a documented simplification, and the honest one: the
            # alternative is a pad tiled from N styles meeting along invisible seams. Author a
            # crossing of two differently-paved streets by giving them the same surface slot.
            jstyle = styles.get(_road_of(net, j.uids[0]))
            pad = build_pad(j, jcoll, name, jstyle)
            report["pads"] += 1
            report["objects"].append(pad.name)
            corners = build_junction_edges(j, jcoll, name, jstyle)
            report["edges"] += len(corners)
            # The corner footway is walkable, so it must reach the navmesh as a WALK proxy --
            # otherwise AI cross the road at the pad and never use the pavement they can see.
            report["colonly"] += len(build_collision([pad], corners, jcoll, name, True))

    if gsolves:
        gcoll = gen_group(pm.GORES, scene)
        for g in gsolves:
            # THE GORE CARRIES THE RAMP'S SECTION (8h.3) -- so it carries the ramp's style too.
            # The two rules are the same rule: the wedge is the ramp's divergence.
            gstyle = styles.get(_road_of(net, g.ramp_uid))
            gore = build_gore(g, gcoll, "GORE_" + g.ramp_uid[:8], gstyle)
            if gore is None:
                continue
            report["gores"] += 1
            report["objects"].append(gore.name)
            nose = build_gore_edges(g, gcoll, "GORE_" + g.ramp_uid[:8], gstyle)
            report["edges"] += len(nose)
            report["objects"] += [o.name for o in nose]
            # `ped_access` is BOTH flanks' answer (`GoreSolve.ped_access`), not a constant: an
            # island between an expressway and its ramp is not a refuge, and a walkable proxy
            # there is an invitation to stroll onto the carriageway -- but a gore between two
            # ordinary streets is a pedestrian island and must bake as one.
            report["colonly"] += len(build_collision([gore], nose, gcoll,
                                                     "GORE_" + g.ramp_uid[:8], g.ped_access))

    return report


# ------------------------------------------------------------------------------- operators

class RKA_OT_point_build(bpy.types.Operator):
    """Build every road, junction and support from the authored points"""
    bl_idname = "rka.point_build"
    bl_label = "Build Roads"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            from . import point_validate as pv
        except ImportError:
            import point_validate as pv
        try:
            from . import point_ops as po
        except ImportError:
            import point_ops as po
        # BEFORE the read. A point the artist rotated is adopted as MANUAL here, and every arrow
        # the tool still owns is re-faced to the chain -- so what the build sweeps is what the
        # viewport was showing, not a facing that silently went stale two drags ago.
        promoted, _refaced = po.sync_facings(context.scene)
        for name in promoted[:4]:
            self.report({'INFO'}, "%s was rotated -- its facing now shapes the road" % name)
        # ...and every junction handle back onto its own centre, for the same reason: what the
        # artist grabs next must be where the junction actually is. Moves the Empty only -- no
        # mouth, and therefore no geometry, is touched.
        po.recentre_all_junctions(context)
        net = pm.read_network(context.scene)
        findings = pv.validate(net)
        errs = pv.errors(findings)
        if errs:
            # A BUILD THAT FAILS THE GATE IS A FAILED BUILD (5). Reported by OBJECT NAME, because
            # the artist fixes objects, not indices -- `pv.describe` resolves every uid in the
            # line, not just the subject.
            label = net.labels
            for f in errs[:5]:
                self.report({'ERROR'}, pv.describe(f, label))
            self.report({'ERROR'}, "%d gate error(s) -- nothing built" % len(errs))
            return {'CANCELLED'}
        rep = build_network(net, context.scene)
        for name, worst in rep["not_star"]:
            self.report({'WARNING'}, "%s pad ring folds %.2f m -- ear-clipped instead of fanned; "
                                     "Auto Setback tidies it" % (name, worst))
        hits, misses = rep.get("ground", (0, 0))
        if misses:
            # Not an error: a road over water or past the terrain's edge legitimately has no
            # ground under it. But the artist should hear the number rather than discover it as a
            # column growing 40 m to nothing.
            self.report({'WARNING'}, "%d station(s) found no terrain below -- their support "
                                     "still uses the last sampled ground" % misses)
        # A style slot naming a datablock this file does not have falls back rather than building
        # a black road -- but silently falling back is how a whole district ships in the wrong
        # material, so it is reported by name.
        for road_name, slot, kind, missing in (rep.get("missing_style") or ())[:4]:
            self.report({'WARNING'}, "%s: %s %s '%s' not found -- using the default"
                        % (road_name, slot, kind, missing))
        self.report({'INFO'}, "%d road(s), %d run(s), %d pad(s), %d gore(s), %d edge run(s), "
                              "%d marking(s), %d proxy(ies), %d ground sample(s)"
                    % (rep["roads"], rep["runs"], rep["pads"], rep["gores"], rep["edges"],
                       rep.get("marks", 0), rep["colonly"], hits))
        return {'FINISHED'}


class RKA_OT_point_clear(bpy.types.Operator):
    """Delete every generated road object. Authored points are never touched"""
    bl_idname = "rka.point_clear"
    bl_label = "Clear Generated"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        clear_all(context.scene)
        self.report({'INFO'}, "ROAD_MANAGER_GEN cleared")
        return {'FINISHED'}


CLASSES = (RKA_OT_point_build, RKA_OT_point_clear)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

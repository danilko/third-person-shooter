"""point_nodes.py -- the Geometry Nodes vocabulary for the point/port road build.

FOUR GROUPS, AND EVERY ROAD BAND IS ONE OF THEM. Kerb, median, footway, carriageway and deck
differ only in lateral offset, vertical offset, width and material -- all of which are inputs. So
they are one group instantiated N times, not N node trees. Asset rows (kerb pieces, pillars,
railings, lights) are the second group; the deck is the first plus an extrude; the head and the
finish bracket the stack.

    GN_PointSpine    mesh carrier -> curve, and store the lateral frame `rka_lat` ONCE
    GN_PointBand     sweep a unit line scaled per point by a named width attribute
    GN_PointDeck     the same band, extruded DOWN by a per-point thickness attribute
    GN_PointAssets   tile a palette asset along the curve, picked per point by an int attribute
    GN_PointPillars  parametric support columns, soffit down to ground
    GN_PointFinish   drop the carrier curve, keep the meshes

PROVENANCE, STATED PLAINLY. This is a port of the mesh-graph addon's `graph_nodes.py`, which is
the one part of that addon that never knew anything about its model: every group here reads named
per-point attributes off a polyline carrier and has no idea what a lane, an edge or a station is.
Porting it rather than rewriting it keeps three measured, hard-won facts that are invisible in the
finished graph -- `Curve to Mesh`'s `Scale` field being the only real per-point width, the
profile-orientation rotation that keeps kerbs from building inside-out, and the interface-version
stamp that stops a cached group silently swallowing a new socket -- and it is what lets step 7
archive `graph_*.py` without losing them.

NONE OF THESE GROUPS KNOWS WHAT A LANE IS. Every number they read was computed by
`point_solve.solve_road` from `lane_profile`. If a width is wrong it is wrong in one Python
function, not somewhere in a graph. The names they read are declared once in
`point_solve.CARRIER_ATTRS` and asserted onto the carrier by `point_build` -- a Named Attribute
pointing at a name the mesh does not carry reads 0 and builds a zero-width band, silently, which
is indistinguishable from "my change had no effect".
"""
import bpy

ATTR_LAT = "rka_lat"

_UP = (0.0, 0.0, 1.0)
#: Curve to Mesh orients a profile so that the profile's local +X sweeps to world -Y and its
#: local +Y to world -Z -- i.e. a profile drawn "outward and up" comes out "inward and down".
#: Undone as a ROTATION about the profile's own Z (determinant +1, winding preserved) rather than
#: a mirror, which would leave every kerb inside-out and invisible under backface culling.
_PROFILE_FIX = (0.0, 0.0, 3.141592653589793)


#: Bump when any group's INTERFACE changes. A cached group from an older addon version has the
#: old sockets, and reusing it silently drops whatever the new stack tries to feed it -- which
#: reads as "my change had no effect" rather than as an error. Stamped on each built group and
#: checked on reuse.
GROUP_VERSION = 3


def _new_group(name):
    """Get-or-create, so a rebuild reuses the same node group and every modifier pointing at it
    keeps working. Returns (group, True) when a CURRENT-version group already existed; a
    stale-version group is emptied and rebuilt in place, keeping its users' pointers valid."""
    ng = bpy.data.node_groups.get(name)
    if ng:
        if ng.get("rka_group_version") == GROUP_VERSION:
            return ng, True
        ng.nodes.clear()
        for item in list(ng.interface.items_tree):
            ng.interface.remove(item)
        ng["rka_group_version"] = GROUP_VERSION
        return ng, False
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng["rka_group_version"] = GROUP_VERSION
    return ng, False


def _named(ng, name_socket_or_str, data_type='FLOAT', location=(0, 0)):
    n = ng.nodes.new("GeometryNodeInputNamedAttribute")
    n.data_type = data_type
    n.location = location
    if isinstance(name_socket_or_str, str):
        n.inputs["Name"].default_value = name_socket_or_str
    else:
        ng.links.new(name_socket_or_str, n.inputs["Name"])
    return n


def _attr_or_zero(ng, name_socket, data_type='FLOAT', location=(0, 0)):
    """Read a named attribute, falling back to 0 when the name is blank or absent.

    `Input Named Attribute`'s own `Exists` output drives the switch, so a layer that does not need
    per-point variation simply leaves the name empty instead of needing a separate flag."""
    a = _named(ng, name_socket, data_type, location)
    sw = ng.nodes.new("GeometryNodeSwitch")
    sw.input_type = 'FLOAT' if data_type == 'FLOAT' else 'INT'
    sw.location = (location[0] + 180, location[1])
    sw.inputs["False"].default_value = 0
    ng.links.new(a.outputs["Exists"], sw.inputs["Switch"])
    ng.links.new(a.outputs["Attribute"], sw.inputs["True"])
    return sw.outputs["Output"]


# ------------------------------------------------------------------------------------ head/tail

def make_spine_group():
    """GN_PointSpine -- mesh carrier to curve, plus the lateral frame every layer shares.

    `rka_lat = normalize(cross(+Z, tangent))`. Stored once here rather than recomputed per layer
    for two reasons: an offset curve's own tangent differs from the carrier's on any bend (so
    per-layer frames would fan out instead of staying parallel), and one stored vector cannot
    disagree with itself."""
    ng, existed = _new_group("GN_PointSpine")
    if existed:
        return ng
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-600, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (600, 0)
    L = ng.links.new

    m2c = ng.nodes.new("GeometryNodeMeshToCurve"); m2c.location = (-380, 0)
    L(nin.outputs["Geometry"], m2c.inputs["Mesh"])

    tan = ng.nodes.new("GeometryNodeInputTangent"); tan.location = (-380, 240)
    cross = ng.nodes.new("ShaderNodeVectorMath"); cross.operation = 'CROSS_PRODUCT'
    cross.location = (-200, 240)
    cross.inputs[0].default_value = _UP
    L(tan.outputs["Tangent"], cross.inputs[1])
    norm = ng.nodes.new("ShaderNodeVectorMath"); norm.operation = 'NORMALIZE'
    norm.location = (-40, 240)
    L(cross.outputs["Vector"], norm.inputs[0])

    store = ng.nodes.new("GeometryNodeStoreNamedAttribute"); store.location = (200, 0)
    store.data_type = 'FLOAT_VECTOR'; store.domain = 'POINT'
    store.inputs["Name"].default_value = ATTR_LAT
    L(m2c.outputs["Curve"], store.inputs["Geometry"])
    L(norm.outputs["Vector"], store.inputs["Value"])
    L(store.outputs["Geometry"], nout.inputs["Geometry"])
    return ng


def make_finish_group():
    """GN_PointFinish -- drop the carrier curve, keep the meshes.

    The curve must survive the whole stack (every layer re-derives from it) but must not reach the
    exported result: a stray curve component is invisible in the viewport yet is real geometry to
    anything walking the evaluated object, and would ride into the glTF bake and the `-colonly`
    proxy pass."""
    ng, existed = _new_group("GN_PointFinish")
    if existed:
        return ng
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-300, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (300, 0)
    sep = ng.nodes.new("GeometryNodeSeparateComponents"); sep.location = (0, 0)
    ng.links.new(nin.outputs["Geometry"], sep.inputs["Geometry"])
    ng.links.new(sep.outputs["Mesh"], nout.inputs["Geometry"])
    return ng


# --------------------------------------------------------------------------------- inner groups

def _band_core(ng, nin, curve_socket, L, x=0):
    """Unit line swept at a per-point half-width. Shared by the band and the deck."""
    width = _attr_or_zero(ng, nin.outputs["WidthAttr"], 'FLOAT', (x - 700, -260))

    line = ng.nodes.new("GeometryNodeCurvePrimitiveLine"); line.location = (x - 420, -400)
    line.inputs["Start"].default_value = (-1.0, 0.0, 0.0)
    line.inputs["End"].default_value = (1.0, 0.0, 0.0)

    # "Z Up" makes the sweep frame deterministic and world-referenced. The default "Minimum
    # Twist" derives the frame from the curve's own shape, so a band's idea of "up" would depend
    # on where the road happens to bend and two layers on the same carrier could disagree.
    setn = ng.nodes.new("GeometryNodeSetCurveNormal"); setn.location = (x - 300, 0)
    setn.inputs["Mode"].default_value = 'Z Up'
    L(curve_socket, setn.inputs["Curve"])

    c2m = ng.nodes.new("GeometryNodeCurveToMesh"); c2m.location = (x - 140, 0)
    c2m.inputs["Fill Caps"].default_value = False
    L(setn.outputs["Curve"], c2m.inputs["Curve"])
    L(line.outputs["Curve"], c2m.inputs["Profile Curve"])
    L(width, c2m.inputs["Scale"])
    return c2m.outputs["Mesh"]


def make_band_group():
    """GN_PointBand -- a flat ribbon of per-point width. The carriageway, the median, and both
    footways are all this group with a different width attribute and offset."""
    ng, existed = _new_group("GN_PointBand")
    if existed:
        return ng
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ifc.new_socket("WidthAttr", in_out="INPUT", socket_type="NodeSocketString")
    ifc.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-800, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (400, 0)
    L = ng.links.new

    mesh = _band_core(ng, nin, nin.outputs["Geometry"], L)
    setm = ng.nodes.new("GeometryNodeSetMaterial"); setm.location = (60, 0)
    L(mesh, setm.inputs["Geometry"])
    L(nin.outputs["Material"], setm.inputs["Material"])
    L(setm.outputs["Geometry"], nout.inputs["Geometry"])
    return ng


def make_deck_group():
    """GN_PointDeck -- the structure under the carriageway: the same band, extruded DOWN by a
    per-point thickness.

    Extrude Mesh rather than a taller swept profile, because `Curve to Mesh` has exactly ONE
    `Scale` field and a deck needs two independent per-point numbers -- its width and its
    thickness. Extruding the finished band keeps the width per-point correct (it came from the
    sweep) and takes the thickness from `Offset Scale`, which is itself a field. Sweeping a
    rectangle instead would tie thickness to width."""
    ng, existed = _new_group("GN_PointDeck")
    if existed:
        return ng
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ifc.new_socket("WidthAttr", in_out="INPUT", socket_type="NodeSocketString")
    ifc.new_socket("ThicknessAttr", in_out="INPUT", socket_type="NodeSocketString")
    ifc.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-800, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (600, 0)
    L = ng.links.new

    mesh = _band_core(ng, nin, nin.outputs["Geometry"], L)
    thick = _attr_or_zero(ng, nin.outputs["ThicknessAttr"], 'FLOAT', (-700, -560))

    ext = ng.nodes.new("GeometryNodeExtrudeMesh"); ext.location = (100, 0)
    ext.mode = 'FACES'
    # `Offset` MUST BE LINKED -- it is an implicit-field socket that falls back to the face NORMAL
    # when unconnected, so a written `default_value` is ignored and the extrusion follows whichever
    # way the band happens to face. Measured: a 0.2 m kerb built upward from its top to z=[0.20,
    # 0.40] instead of down to [0.00, 0.20]. Same trap as `Instance on Points`' `Instance Index`;
    # if a socket accepts a field, assume writing its default does nothing.
    down = ng.nodes.new("FunctionNodeInputVector"); down.location = (-100, -180)
    down.vector = (0.0, 0.0, -1.0)
    L(mesh, ext.inputs["Mesh"])
    L(down.outputs["Vector"], ext.inputs["Offset"])
    L(thick, ext.inputs["Offset Scale"])

    setm = ng.nodes.new("GeometryNodeSetMaterial"); setm.location = (320, 0)
    L(ext.outputs["Mesh"], setm.inputs["Geometry"])
    L(nin.outputs["Material"], setm.inputs["Material"])
    L(setm.outputs["Geometry"], nout.inputs["Geometry"])
    return ng


def make_profile_group():
    """GN_PointProfile -- sweep an ARTIST'S OWN cross-section along the carrier.

    The band group's profile is a unit line scaled by a per-point width; this one's is whatever
    curve object the road names, swept at its OWN authored size. That is the difference between
    "how wide is the kerb" and "which kerb", and it is the second one designers ask for.

    CONTINUOUS, NOT TILED, and that is the whole reason it is a sweep. The previous model
    instanced rigid kit pieces along the edge: round a 9 m corner a 2 m piece sits ~12.7 deg from
    its neighbour and opens a real ~7.8 cm gap at every joint -- an inherent limit of tiling rigid
    geometry on a curve, not a phase bug, and the reason that whole style was eventually retired.
    A swept section has no joints to open. Tiling still has its place, for things that ARE discrete
    -- lamp posts, signs, bollards -- and that is `make_assets_group`.

    `Curve to Mesh` DROPS the profile's materials (measured, Blender 5.2: zero slots on the
    evaluated sweep, even from a two-material multi-spline profile), so `Material` is an input
    here like everywhere else and `point_style.asset_material` reads it off the asset for the
    caller to pass in. Shape and look still travel together; the addon carries the second one."""
    ng, existed = _new_group("GN_PointProfile")
    if existed:
        return ng
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ifc.new_socket("Profile", in_out="INPUT", socket_type="NodeSocketObject")
    ifc.new_socket("Flip", in_out="INPUT", socket_type="NodeSocketBool")
    ifc.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-800, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (600, 0)
    L = ng.links.new

    # Same deterministic, world-referenced frame the band uses. "Minimum Twist" derives the frame
    # from the curve's own shape, so a profile's idea of "up" would depend on where the road bends
    # and two layers on one carrier could disagree.
    setn = ng.nodes.new("GeometryNodeSetCurveNormal"); setn.location = (-560, 0)
    setn.inputs["Mode"].default_value = 'Z Up'
    L(nin.outputs["Geometry"], setn.inputs["Curve"])

    oi = ng.nodes.new("GeometryNodeObjectInfo"); oi.location = (-560, -240)
    # RELATIVE, not original: the profile is swept in the road's frame, so the asset's own object
    # transform must already be applied. With `Original` a kit piece rotated in its source file
    # sweeps rotated, and nothing in the road says why.
    oi.transform_space = 'RELATIVE'
    L(nin.outputs["Profile"], oi.inputs["Object"])

    # THE ARTIST DRAWS IT THE RIGHT WAY UP, and this is what makes that true. MEASURED, not
    # assumed: a profile point at local +X 2 m comes out at world Y -2, and one at local +Y 5 m at
    # world Z -5 -- so a section drawn "outward and up" sweeps "inward and down". Undone as a
    # ROTATION of pi about the profile's own Z (determinant +1, winding preserved) and never as a
    # mirror, which would leave every kerb inside-out and invisible under backface culling. The
    # constant has been written down in this module since the beginning; nothing had ever used it.
    fix = ng.nodes.new("GeometryNodeTransform"); fix.location = (-420, -240)
    fix.inputs["Rotation"].default_value = _PROFILE_FIX
    L(oi.outputs["Geometry"], fix.inputs["Geometry"])

    # ...AND WHICH SIDE OF THE ROAD IT IS ON. An asymmetric section -- which is every real kerb --
    # must face outward on BOTH flanks, and the two `__edges` carriers run the same way along the
    # road (that is exactly why `edge_run_values` carries `sgn`), so the right-hand one needs the
    # section mirrored. Mirroring inverts the winding, so `Flip Faces` puts the normals back;
    # without it the whole right kerb of every road is backfacing.
    mir = ng.nodes.new("GeometryNodeTransform"); mir.location = (-420, -460)
    mir.inputs["Scale"].default_value = (-1.0, 1.0, 1.0)
    L(fix.outputs["Geometry"], mir.inputs["Geometry"])
    flipf = ng.nodes.new("GeometryNodeFlipFaces"); flipf.location = (-300, -460)
    L(mir.outputs["Geometry"], flipf.inputs["Mesh"])
    pick = ng.nodes.new("GeometryNodeSwitch"); pick.input_type = 'GEOMETRY'
    pick.location = (-180, -300)
    L(nin.outputs["Flip"], pick.inputs["Switch"])
    L(fix.outputs["Geometry"], pick.inputs["False"])
    L(flipf.outputs["Mesh"], pick.inputs["True"])

    c2m = ng.nodes.new("GeometryNodeCurveToMesh"); c2m.location = (-40, 0)
    c2m.inputs["Fill Caps"].default_value = False
    L(setn.outputs["Curve"], c2m.inputs["Curve"])
    L(pick.outputs["Output"], c2m.inputs["Profile Curve"])
    # NO `Scale` LINK. The asset's authored size IS the size -- scaling it by a design width is
    # what once put a 3.0 m footway piece where a 3.5 m one was assumed and opened a real gap.

    setm = ng.nodes.new("GeometryNodeSetMaterial"); setm.location = (200, 0)
    L(c2m.outputs["Mesh"], setm.inputs["Geometry"])
    L(nin.outputs["Material"], setm.inputs["Material"])
    L(setm.outputs["Geometry"], nout.inputs["Geometry"])
    return ng


def make_assets_group():
    """GN_PointAssets -- tile a palette asset along the curve, picked PER POINT by an int
    attribute. Kerb pieces, pillars, railings, streetlights and props are all this group.

    THE TWO CONTRACTS THIS DEPENDS ON, both measured (see `point_build`'s docstring and
    `smoketest_graph_solve`), both silent-wrong-answer if broken:
      * `Collection Info (Separate Children)` emits children in link order, so `point_build`
        keeps that order alphabetical rather than sorting only on the Python side.
      * `Instance Index` MUST BE LINKED. It is an implicit-field socket: left unlinked it falls
        back to the `Index` field and ignores any written default, which would quietly build every
        road with palette entry 0.

    `Selection` drops points whose index is negative, which is how "-1 = no asset, build this band
    parametrically instead" is expressed with no second flag."""
    ng, existed = _new_group("GN_PointAssets")
    if existed:
        return ng
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ifc.new_socket("Palette", in_out="INPUT", socket_type="NodeSocketCollection")
    ifc.new_socket("IndexAttr", in_out="INPUT", socket_type="NodeSocketString")
    ifc.new_socket("SpacingAttr", in_out="INPUT", socket_type="NodeSocketString")
    sp = ifc.new_socket("Spacing", in_out="INPUT", socket_type="NodeSocketFloat")
    sp.default_value = 5.0
    sp.min_value = 0.05
    ifc.new_socket("Align To Curve", in_out="INPUT", socket_type="NodeSocketBool")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-900, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (700, 0)
    L = ng.links.new

    # Spacing: the per-point attribute when present, else the constant. Resample Curve's Length
    # input is evaluated per spline, so a per-edge spacing genuinely varies between roads.
    sp_attr = _named(ng, nin.outputs["SpacingAttr"], 'FLOAT', (-900, -260))
    sp_pick = ng.nodes.new("GeometryNodeSwitch"); sp_pick.input_type = 'FLOAT'
    sp_pick.location = (-700, -260)
    L(sp_attr.outputs["Exists"], sp_pick.inputs["Switch"])
    L(nin.outputs["Spacing"], sp_pick.inputs["False"])
    L(sp_attr.outputs["Attribute"], sp_pick.inputs["True"])
    # A zero/negative spacing would make Resample Curve generate unbounded points.
    guard = ng.nodes.new("ShaderNodeMath"); guard.operation = 'MAXIMUM'
    guard.location = (-540, -260)
    guard.inputs[1].default_value = 0.05
    L(sp_pick.outputs["Output"], guard.inputs[0])

    res = ng.nodes.new("GeometryNodeResampleCurve"); res.location = (-360, 0)
    # `Mode` is a MENU SOCKET here, not a node enum -- this Blender moved several node enums onto
    # sockets (`Set Curve Normal`'s is the same shape). `res.mode = ...` raises AttributeError.
    res.inputs["Mode"].default_value = 'Length'
    L(nin.outputs["Geometry"], res.inputs["Curve"])
    L(guard.outputs["Value"], res.inputs["Length"])

    c2p = ng.nodes.new("GeometryNodeCurveToPoints"); c2p.location = (-180, 0)
    c2p.mode = 'EVALUATED'
    L(res.outputs["Curve"], c2p.inputs["Curve"])

    ci = ng.nodes.new("GeometryNodeCollectionInfo"); ci.location = (-180, -420)
    ci.inputs["Separate Children"].default_value = True
    ci.inputs["Reset Children"].default_value = True
    L(nin.outputs["Palette"], ci.inputs["Collection"])

    idx = _named(ng, nin.outputs["IndexAttr"], 'INT', (-560, 240))
    keep = ng.nodes.new("FunctionNodeCompare"); keep.location = (-360, 300)
    keep.data_type = 'INT'; keep.operation = 'GREATER_EQUAL'
    # Wire by NAME. Older Blenders exposed one A/B socket pair per data type on this node (floats
    # at 0/1, ints at 2/3); this one rebuilds the sockets when `data_type` changes and exposes
    # exactly two. Indexing by position is what breaks across both.
    keep.inputs["B"].default_value = 0
    L(idx.outputs["Attribute"], keep.inputs["A"])

    iop = ng.nodes.new("GeometryNodeInstanceOnPoints"); iop.location = (60, 0)
    iop.inputs["Pick Instance"].default_value = True
    L(c2p.outputs["Points"], iop.inputs["Points"])
    L(ci.outputs["Instances"], iop.inputs["Instance"])
    L(idx.outputs["Attribute"], iop.inputs["Instance Index"])
    L(keep.outputs["Result"], iop.inputs["Selection"])

    rot = ng.nodes.new("GeometryNodeSwitch"); rot.input_type = 'ROTATION'
    rot.location = (-180, 200)
    align = ng.nodes.new("FunctionNodeAlignRotationToVector"); align.location = (-360, 140)
    align.axis = 'Z'
    tan = ng.nodes.new("GeometryNodeInputTangent"); tan.location = (-560, 100)
    L(tan.outputs["Tangent"], align.inputs["Vector"])
    L(nin.outputs["Align To Curve"], rot.inputs["Switch"])
    L(align.outputs["Rotation"], rot.inputs["True"])
    L(rot.outputs["Output"], iop.inputs["Rotation"])

    real = ng.nodes.new("GeometryNodeRealizeInstances"); real.location = (400, 0)
    L(iop.outputs["Instances"], real.inputs["Geometry"])
    L(real.outputs["Geometry"], nout.inputs["Geometry"])
    return ng


def make_pillars_group():
    """GN_PointPillars -- support columns from the deck soffit DOWN TO THE GROUND, at spacing.

    WHY THESE ARE PARAMETRIC AND NOT AN INSTANCED ASSET. A pillar's whole job is to reach the
    ground, so its height is not a property of the pillar -- it is `deck soffit - ground`, which
    varies along every ramp and differs between the elevated loop (12 m up) and a road on grade.
    Instancing a fixed-height kit piece can only ever be right at one elevation: the kit's own
    `Kit_Pillar_*` pieces are 9 m long, so on this island they would hang 3 m short under the loop
    and 9 m through the air under a ramp near grade. So the column is built from the numbers, the
    same way the kerb and the deck are, and `pillar_asset_idx` stays available for decorative
    columns where an author wants a specific mesh.

    The height itself is resolved in Python (`point_build.build_carrier` writes `rka_pillar_h` from
    each point's own elevation), so this group only scales a box -- no node does the arithmetic that
    decides where geometry goes.

    Selection drops any point whose column would be shorter than `MinHeight`: a road on grade has
    a soffit at ground level and needs no supports at all, and without the test it would grow a
    row of zero-height slivers along its entire length."""
    ng, existed = _new_group("GN_PointPillars")
    if existed:
        return ng
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ifc.new_socket("SpacingAttr", in_out="INPUT", socket_type="NodeSocketString")
    sp = ifc.new_socket("Spacing", in_out="INPUT", socket_type="NodeSocketFloat")
    sp.default_value = 20.0
    sp.min_value = 0.05
    mh = ifc.new_socket("MinHeight", in_out="INPUT", socket_type="NodeSocketFloat")
    mh.default_value = 0.5
    ifc.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-1100, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (700, 0)
    L = ng.links.new

    # Spacing: per-point attribute when present, else the constant -- same shape as the asset row,
    # including the guard against a zero length making Resample Curve unbounded.
    sp_attr = _named(ng, nin.outputs["SpacingAttr"], 'FLOAT', (-1100, -280))
    sp_pick = ng.nodes.new("GeometryNodeSwitch"); sp_pick.input_type = 'FLOAT'
    sp_pick.location = (-900, -280)
    L(sp_attr.outputs["Exists"], sp_pick.inputs["Switch"])
    L(nin.outputs["Spacing"], sp_pick.inputs["False"])
    L(sp_attr.outputs["Attribute"], sp_pick.inputs["True"])
    guard = ng.nodes.new("ShaderNodeMath"); guard.operation = 'MAXIMUM'
    guard.location = (-740, -280); guard.inputs[1].default_value = 0.05
    L(sp_pick.outputs["Output"], guard.inputs[0])

    res = ng.nodes.new("GeometryNodeResampleCurve"); res.location = (-560, 0)
    res.inputs["Mode"].default_value = 'Length'
    L(nin.outputs["Geometry"], res.inputs["Curve"])
    L(guard.outputs["Value"], res.inputs["Length"])
    c2p = ng.nodes.new("GeometryNodeCurveToPoints"); c2p.location = (-380, 0)
    c2p.mode = 'EVALUATED'
    L(res.outputs["Curve"], c2p.inputs["Curve"])

    h = _named(ng, "rka_pillar_h", 'FLOAT', (-1100, 260))
    w = _named(ng, "rka_pillar_w", 'FLOAT', (-1100, 180))
    on = _named(ng, "rka_pillar_param", 'FLOAT', (-1100, 100))
    deck = _named(ng, "rka_deck_h", 'FLOAT', (-1100, 20))

    tall = ng.nodes.new("FunctionNodeCompare"); tall.location = (-900, 300)
    tall.data_type = 'FLOAT'; tall.operation = 'GREATER_EQUAL'
    L(h.outputs["Attribute"], tall.inputs["A"])
    L(nin.outputs["MinHeight"], tall.inputs["B"])
    lit = ng.nodes.new("FunctionNodeCompare"); lit.location = (-900, 160)
    lit.data_type = 'FLOAT'; lit.operation = 'GREATER_THAN'
    lit.inputs["B"].default_value = 0.5
    L(on.outputs["Attribute"], lit.inputs["A"])
    both = ng.nodes.new("FunctionNodeBooleanMath"); both.operation = 'AND'
    both.location = (-720, 240)
    L(tall.outputs["Result"], both.inputs[0])
    L(lit.outputs["Result"], both.inputs[1])

    # Drop the column so its TOP meets the soffit: the box is centred, so the point moves down by
    # the deck thickness plus half the column.
    halfh = ng.nodes.new("ShaderNodeMath"); halfh.operation = 'MULTIPLY'
    halfh.location = (-900, -80); halfh.inputs[1].default_value = 0.5
    L(h.outputs["Attribute"], halfh.inputs[0])
    drop = ng.nodes.new("ShaderNodeMath"); drop.operation = 'ADD'; drop.location = (-720, -80)
    L(halfh.outputs["Value"], drop.inputs[0])
    L(deck.outputs["Attribute"], drop.inputs[1])
    neg = ng.nodes.new("ShaderNodeMath"); neg.operation = 'MULTIPLY'
    neg.location = (-560, -80); neg.inputs[1].default_value = -1.0
    L(drop.outputs["Value"], neg.inputs[0])
    off = ng.nodes.new("ShaderNodeCombineXYZ"); off.location = (-380, -80)
    L(neg.outputs["Value"], off.inputs["Z"])
    setpos = ng.nodes.new("GeometryNodeSetPosition"); setpos.location = (-200, 0)
    L(c2p.outputs["Points"], setpos.inputs["Geometry"])
    L(off.outputs["Vector"], setpos.inputs["Offset"])

    cube = ng.nodes.new("GeometryNodeMeshCube"); cube.location = (-200, -320)
    scale = ng.nodes.new("ShaderNodeCombineXYZ"); scale.location = (-20, 220)
    L(w.outputs["Attribute"], scale.inputs["X"])
    L(w.outputs["Attribute"], scale.inputs["Y"])
    L(h.outputs["Attribute"], scale.inputs["Z"])

    iop = ng.nodes.new("GeometryNodeInstanceOnPoints"); iop.location = (160, 0)
    L(setpos.outputs["Geometry"], iop.inputs["Points"])
    L(cube.outputs["Mesh"], iop.inputs["Instance"])
    L(both.outputs["Boolean"], iop.inputs["Selection"])
    L(scale.outputs["Vector"], iop.inputs["Scale"])

    real = ng.nodes.new("GeometryNodeRealizeInstances"); real.location = (340, 0)
    L(iop.outputs["Instances"], real.inputs["Geometry"])
    setm = ng.nodes.new("GeometryNodeSetMaterial"); setm.location = (520, 0)
    L(real.outputs["Geometry"], setm.inputs["Geometry"])
    L(nin.outputs["Material"], setm.inputs["Material"])
    L(setm.outputs["Geometry"], nout.inputs["Geometry"])
    return ng


# -------------------------------------------------------------------------------- layer wrapper

def _mirror_inputs(dst_ifc, src_ng):
    """Copy an inner group's non-geometry inputs onto the wrapper, so wrapping a group exposes all
    of its settings and a future group's new input appears without editing this function."""
    out = {}
    for item in src_ng.interface.items_tree:
        if getattr(item, "item_type", "") != 'SOCKET' or item.in_out != 'INPUT':
            continue
        if item.socket_type == "NodeSocketGeometry":
            continue
        s = dst_ifc.new_socket(item.name, in_out="INPUT", socket_type=item.socket_type)
        if hasattr(item, "default_value") and hasattr(s, "default_value"):
            try:
                s.default_value = item.default_value
            except (TypeError, AttributeError):
                pass
        out[item.name] = s.identifier
    return out


def _offset_stage(ng, L, geo_socket, nin, x=-100):
    """THE one place a layer is displaced, so no two layers can drift apart.

    Lateral = (`Offset` + per-point `OffsetAttr`) along `rka_lat`, read -- never recomputed --
    from what the head group stored. Vertical = (`ZOffset` + per-point `ZOffsetAttr`). Both take a
    per-point attribute as well as a constant: a footway must sit on top of a kerb whose height
    varies per edge, which a constant-only Z cannot express."""
    lat = _named(ng, ATTR_LAT, 'FLOAT_VECTOR', (x - 420, 260))
    off = _attr_or_zero(ng, nin.outputs["OffsetAttr"], 'FLOAT', (x - 420, 80))
    zoff = _attr_or_zero(ng, nin.outputs["ZOffsetAttr"], 'FLOAT', (x - 420, -100))

    total = ng.nodes.new("ShaderNodeMath"); total.operation = 'ADD'; total.location = (x - 60, 80)
    L(off, total.inputs[0])
    L(nin.outputs["Offset"], total.inputs[1])

    ztotal = ng.nodes.new("ShaderNodeMath"); ztotal.operation = 'ADD'
    ztotal.location = (x - 60, -100)
    L(zoff, ztotal.inputs[0])
    L(nin.outputs["ZOffset"], ztotal.inputs[1])

    scale = ng.nodes.new("ShaderNodeVectorMath"); scale.operation = 'SCALE'
    scale.location = (x + 120, 200)
    L(lat.outputs["Attribute"], scale.inputs[0])
    L(total.outputs["Value"], scale.inputs["Scale"])

    zvec = ng.nodes.new("ShaderNodeCombineXYZ"); zvec.location = (x + 120, 20)
    L(ztotal.outputs["Value"], zvec.inputs["Z"])
    add = ng.nodes.new("ShaderNodeVectorMath"); add.operation = 'ADD'
    add.location = (x + 280, 120)
    L(scale.outputs["Vector"], add.inputs[0])
    L(zvec.outputs["Vector"], add.inputs[1])

    setpos = ng.nodes.new("GeometryNodeSetPosition"); setpos.location = (x + 460, 0)
    L(geo_socket, setpos.inputs["Geometry"])
    L(add.outputs["Vector"], setpos.inputs["Offset"])
    return setpos.outputs["Geometry"]


def wrap_layer(inner_ng, name):
    """Turn any "curve in -> geometry out" group into a stack layer, cached by `name`.

    The wrapper does the three things every layer shares and nothing else: pull the carrier curve
    back out of the accumulated geometry, displace it, and join whatever the inner group produced
    onto what was already there. The inner group stays completely unaware of the stack."""
    wrapped, existed = _new_group(name)
    if existed:
        return wrapped, {k: v for k, v in wrapped["sock_ids"].items()}
    ng = wrapped
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    off_s = ifc.new_socket("Offset", in_out="INPUT", socket_type="NodeSocketFloat")
    offa_s = ifc.new_socket("OffsetAttr", in_out="INPUT", socket_type="NodeSocketString")
    z_s = ifc.new_socket("ZOffset", in_out="INPUT", socket_type="NodeSocketFloat")
    za_s = ifc.new_socket("ZOffsetAttr", in_out="INPUT", socket_type="NodeSocketString")
    inner_map = _mirror_inputs(ifc, inner_ng)
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    nin = ng.nodes.new("NodeGroupInput"); nin.location = (-1000, 0)
    nout = ng.nodes.new("NodeGroupOutput"); nout.location = (1000, 0)
    L = ng.links.new

    sep = ng.nodes.new("GeometryNodeSeparateComponents"); sep.location = (-800, -160)
    L(nin.outputs["Geometry"], sep.inputs["Geometry"])
    curve = _offset_stage(ng, L, sep.outputs["Curve"], nin, x=-420)

    # NB `node_tree`, not `node_group` -- the group NODE and the NodesModifier spell this
    # differently, and the node raises AttributeError for the modifier's spelling.
    grp = ng.nodes.new("GeometryNodeGroup"); grp.node_tree = inner_ng; grp.location = (400, -80)
    geo_in = next(s for s in grp.inputs if s.type == 'GEOMETRY')
    L(curve, geo_in)
    for sock_name in inner_map:
        if sock_name in nin.outputs and sock_name in grp.inputs:
            L(nin.outputs[sock_name], grp.inputs[sock_name])

    join = ng.nodes.new("GeometryNodeJoinGeometry"); join.location = (720, 0)
    L(nin.outputs["Geometry"], join.inputs["Geometry"])
    L(next(o for o in grp.outputs if o.type == 'GEOMETRY'), join.inputs["Geometry"])
    L(join.outputs["Geometry"], nout.inputs["Geometry"])

    ids = {"Offset": off_s.identifier, "OffsetAttr": offa_s.identifier,
           "ZOffset": z_s.identifier, "ZOffsetAttr": za_s.identifier}
    ids.update(inner_map)
    ng["sock_ids"] = ids
    return ng, ids

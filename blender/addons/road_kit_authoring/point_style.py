"""point_style.py -- what a road is MADE OF: one slot per layer, resolved once.

THE PROBLEM THIS SOLVES. Every layer in `point_build.surface_spec()` / `edge_spec()` used to name
its material inline -- `Material=material("asphalt")` -- so the whole world's roads were one look,
with no way to author a granite kerb on one street and a concrete one on the next short of editing
the builder. And the choice a designer actually wants to make is not "which colour", it is "which
kerb": a shape and a material together.

THE MODEL. A road declares a NAME per slot (`RoadData.kerb_mat`, `RoadData.kerb_asset`, ...), blank
meaning the layer's default -- and a default is a material LINKED FROM THE ASSET KIT
(`kit_common.mat`, since 2026-09-05), not one the builder invents. So an artist restyles the world
by opening `assets/world_source/kit/road_kit.blend`, and this file's job stays what it was: turning
an authored NAME into a datablock. Two rules fall out of that and both matter:

  * A NAME THAT RESOLVES TO NOTHING FALLS BACK, and says so. A missing material builds a black
    road and a missing profile asset builds nothing at all -- both indistinguishable from a
    shading or authoring mistake, and both silent. `missing()` collects them for the gate.
  * A PROFILE ASSET OWNS ITS OWN SIZE -- FOR THE SWEEP. `Curve to Mesh` preserves the profile's
    real dimensions, so a 0.15 m kerb section sweeps 0.15 m tall and is never scaled to a design
    number.

    **BUT THE SOLVE DOES NOT YET KNOW THAT** (measured, 2026-08-28): `point_solve` still places
    every layer outboard of it from the AUTHORED widths, so naming a 2.00 m footway section on a
    road whose `left_walk_width` reserves 3.00 m leaves 1.00 m of slack between the pavement's real
    edge and the barrier standing beyond it -- and a 0.32 m kerb section overhangs a parametric
    0.15 m kerb into where the footway starts. This is the same defect as round 2 of the previous
    addon's asset work, one level up.

    Wiring it means threading the style into `solve_road` so the asset's measured width replaces
    the authored one at the single place lateral offsets are computed -- NOT adding a second
    offset owner. Until then `point_validate.check_asset_width` reports the mismatch by name and
    in metres, so it is visible rather than silent, and the remedy is to author the width to match
    the section you chose. `asset_width` is that measurement.

AND WHAT `Curve to Mesh` DOES NOT DO -- measured, Blender 5.2, headless: it DROPS the profile
curve's materials. The evaluated sweep comes back with zero material slots, one `material_index`,
and no `material_index` attribute, even for a multi-spline profile carrying two of them. So an
asset carries SHAPE only, and the addon reads the material off the asset's own data and feeds the
layer's `Material` socket -- which is why swapping an asset still changes both. One asset is one
material is one layer; a genuinely multi-material section would need one sweep per spline group,
and is not built.
"""

try:
    import bpy
except ImportError:                                        # pure-Python self-test
    bpy = None

try:
    from . import point_model as pm
except ImportError:
    import point_model as pm                                                 # noqa: E402


#: `slot -> (material field, asset field, the default key in `point_build.MATERIAL_KEYS`)`.
#:
#: ONE declaration, the way `POINT_FIELDS` is one declaration: it drives the resolve, the panel
#: rows and the gate's missing-datablock check, so those three cannot drift apart.
SLOTS = (
    ("surface", "surface_mat", "surface_asset", "asphalt"),
    ("median",  "median_mat",  "median_asset",  "median"),
    ("deck",    "deck_mat",    "",              "concrete"),
    ("kerb",    "kerb_mat",    "kerb_asset",    "concrete"),
    ("footway", "footway_mat", "footway_asset", "footway"),
    ("barrier", "barrier_mat", "barrier_asset", "barrier"),
    ("mark_w",  "mark_w_mat",  "",              "line_w"),
    ("mark_y",  "mark_y_mat",  "",              "line_y"),
)

SLOT_NAMES = tuple(s[0] for s in SLOTS)
_BY_SLOT = {s[0]: s for s in SLOTS}

#: Where a profile asset is looked up. A collection, so the kit can be library-LINKED from one
#: `road_kit.blend` and editing that file restyles every kerb in the world.
#:
#: Looked up WITHOUT a `library is None` filter, on purpose: kit pieces are deliberately linked,
#: and copying the local-only convention from elsewhere in this pipeline is what once emptied an
#: asset dropdown of every real piece.
KIT_COLLECTION = "ROAD_KIT"

#: A profile asset's name prefix, so a picker can offer the kit's contents and nothing else.
ASSET_PREFIX = "RKA_PROFILE_"


class Style(object):
    """One road's resolved look. Built once per build, read by every layer."""

    __slots__ = ("road", "_mats", "_assets", "_missing")

    def __init__(self, road, mats, assets, missing):
        self.road, self._mats, self._assets, self._missing = road, mats, assets, missing

    def material(self, slot):
        return self._mats.get(slot)

    def asset(self, slot):
        """The profile object for this slot, or None -- which means "sweep it parametrically"."""
        return self._assets.get(slot)

    def missing(self):
        """`[(slot, kind, name)]` the road asked for and this file does not have."""
        return list(self._missing)

    def __repr__(self):
        return "<Style %s>" % getattr(self.road, "name", "?")


def kit_collection():
    if bpy is None:
        return None
    return next((c for c in bpy.data.collections if c.name == KIT_COLLECTION), None)


def kit_assets():
    """Every profile asset the file offers, by name."""
    coll = kit_collection()
    if coll is None:
        return {}
    return {o.name: o for o in coll.all_objects if o.type in {'CURVE', 'MESH'}}


def resolve(road, material_fn=None):
    """`RoadData` -> `Style`. `material_fn(key)` supplies a layer's default material."""
    mats, assets, missing = {}, {}, []
    kit = kit_assets()
    for slot, mat_field, asset_field, default_key in SLOTS:
        name = (getattr(road, mat_field, "") or "").strip()
        mat = None
        if name and bpy is not None:
            mat = bpy.data.materials.get(name)
            if mat is None:
                missing.append((slot, "material", name))
        if mat is None and material_fn is not None:
            mat = material_fn(default_key)
        mats[slot] = mat
        if not asset_field:
            continue
        aname = (getattr(road, asset_field, "") or "").strip()
        if aname:
            obj = kit.get(aname)
            if obj is None:
                missing.append((slot, "asset", aname))
            else:
                assets[slot] = obj
    return Style(road, mats, assets, missing)


def _profile_points(obj):
    """The section's own control points, in its local XY plane.

    READ OFF THE CURVE DATA, never `obj.bound_box`. A bound box is cached evaluated state: on a
    freshly LINKED object in a background session it has not been computed, and Blender hands back
    a stale or default box -- measured, it reported a 0.32 x 0.17 m kerb as 2.32 x 2.17 m, exactly
    the previous object's extent plus its own. Every downstream number here is a placement
    offset, so a wrong one is a gap in the road."""
    data = getattr(obj, "data", None)
    pts = []
    for sp in getattr(data, "splines", ()) or ():
        pts += [(p.co[0], p.co[1]) for p in sp.points]
        pts += [(p.co[0], p.co[1]) for p in getattr(sp, "bezier_points", ())]
    return pts


def asset_width(obj):
    """A profile asset's own measured width, in metres -- its extent across the road.

    THE ASSET'S SIZE IS THE TRUTH, not the number configured beside it: feeding a 3.5 m design
    width to a 3.0 m section opens a real lateral gap between the kerb and the footway above it,
    which this project has shipped once already."""
    pts = _profile_points(obj)
    if not pts:
        return 0.0
    xs = [x for x, _y in pts]
    return abs(max(xs) - min(xs)) * abs(getattr(obj, "scale", (1.0,))[0])


def asset_height(obj):
    """A profile asset's own measured height, in metres (its Y extent -- up, once swept)."""
    pts = _profile_points(obj)
    if not pts:
        return 0.0
    ys = [y for _x, y in pts]
    return abs(max(ys) - min(ys)) * abs(getattr(obj, "scale", (1.0, 1.0))[1])


def asset_material(obj):
    """The material the asset itself carries, or None.

    `Curve to Mesh` drops it (measured -- see the module docstring), so the addon reads it here and
    hands it to the layer's own `Material` socket. That is what makes swapping an asset change the
    shape AND the look with one field."""
    if obj is None or getattr(obj, "data", None) is None:
        return None
    mats = [m for m in obj.data.materials if m is not None]
    return mats[0] if mats else None


def self_test():
    """Pure-Python: the slot table is coherent with the field table it names."""
    ok = 0
    fields = {n for n, _k, _d in pm.ROAD_FIELDS}
    for slot, mat_field, asset_field, default_key in SLOTS:
        assert mat_field in fields, "%s names a material field %r that ROAD_FIELDS lacks" % (
            slot, mat_field)
        assert not asset_field or asset_field in fields, \
            "%s names an asset field %r that ROAD_FIELDS lacks" % (slot, asset_field)
    print("OK: every style slot's material and asset field is declared in ROAD_FIELDS")
    ok += 1

    assert len(SLOT_NAMES) == len(set(SLOT_NAMES)), "duplicate slot"
    assert pm.MED_RAISED in pm.MEDIAN_STYLES and pm.MED_NONE == pm.MEDIAN_STYLES[0]
    print("OK: %d slots, unique; the median styles are append-only from NONE" % len(SLOTS))
    ok += 1

    # A blank slot resolves to its default, a named-but-absent one falls back AND is reported --
    # never a black road nobody was told about.
    class _R(object):
        pass
    r = _R()
    for _s, mf, af, _d in SLOTS:
        setattr(r, mf, "")
        if af:
            setattr(r, af, "")
    st = resolve(r, material_fn=lambda k: "MAT:" + k)
    assert st.material("surface") == "MAT:asphalt", st.material("surface")
    assert not st.missing()
    r.kerb_mat = "M_DoesNotExist"
    st2 = resolve(r, material_fn=lambda k: "MAT:" + k)
    assert st2.material("kerb") == "MAT:concrete", "an absent name must fall back"
    if bpy is not None:
        assert st2.missing(), "...and be reported, or it is a silent wrong look"
    print("OK: a blank slot takes the default; an absent name falls back and is reported")
    ok += 1
    print("\nALL SELF-TESTS PASSED (%d)" % ok)
    return ok


if __name__ == "__main__":
    self_test()

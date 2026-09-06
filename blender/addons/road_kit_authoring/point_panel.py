"""point_panel.py -- the POINT INSPECTOR (4.2).

A point inspector, NOT a stamping brush. The N-panel edits the active point's own properties
directly, so what you see is what that station is. Junction settings appear when a `JCT_*` parent
is active; the road's base profile when a road collection is.

WHAT IS DELIBERATELY NOT HERE: a scene-level stamp with eleven `use_*` toggles, eight ticked by
default, that silently rewrites the median while you meant to change a lane count. That was the
previous addon's brush and it is the exact failure this file is shaped to avoid. Multi-point
editing still needs a real operator -- Blender's native alt-click propagation does not reach
`CollectionProperty` items and only works within a homogeneous selection showing the same property
-- so `Apply Cross-Section` (in `point_ops`) exists, its source is the ACTIVE point, its mask
defaults to NOTHING, and this panel prints the exact field list before you press it.

INHERIT vs OVERRIDE IS SHOWN, ALWAYS. A whole-profile switch plus four genuine deltas is legible;
a 30-bit invisible mask is not. But if the artist cannot SEE which stations are overrides, they
cannot find where a cross-section changes -- so the mode is the first row of the box and the
overlay glyphs it too.
"""

import bpy

try:
    from . import (point_build as pb, point_model as pm, point_ops as po, point_style as pstyle,
                   point_preview as pv3, point_profile as pp, point_solve as ps,
                   point_validate as pv)
except ImportError:
    import point_build as pb                                                 # noqa: E402
    import point_model as pm                                                 # noqa: E402
    import point_ops as po                                                   # noqa: E402
    import point_preview as pv3                                              # noqa: E402
    import point_profile as pp                                               # noqa: E402
    import point_solve as ps                                                 # noqa: E402
    import point_style as pstyle                                             # noqa: E402
    import point_validate as pv                                              # noqa: E402


CATEGORY = "Road Kit"


def selected_points(context):
    return [o for o in context.selected_objects
            if getattr(o, "rka_pt", None) is not None and o.rka_pt.is_point]


def active_point(context):
    o = context.active_object
    return o if (o is not None and getattr(o, "rka_pt", None) is not None
                 and o.rka_pt.is_point) else None


def active_junction(context):
    """The `JCT_*` parent, whether the artist clicked the parent or one of its members."""
    o = context.active_object
    if o is None:
        return None
    if o.name.startswith("JCT_"):
        return o
    p = active_point(context)
    if p is not None and p.parent is not None and p.parent.name.startswith("JCT_"):
        return p.parent
    return None


def active_road(context):
    """The road collection the active point belongs to -- found by membership, not by name."""
    p = active_point(context)
    if p is None:
        return None
    for c in p.users_collection:
        if c.library is None and getattr(c, "rka_road", None) is not None and c.rka_road.is_road:
            return c
    return None


class RKA_PT_point(bpy.types.Panel):
    bl_label = "Road Point"
    bl_idname = "RKA_PT_point"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    def draw(self, context):
        col = self.layout.column()
        pt = active_point(context)
        if pt is None:
            col.label(text="Select a road point", icon='INFO')
            col.operator("rka.new_road", icon='ADD')
            return
        p = pt.rka_pt
        box = col.box()
        row = box.row(align=True)
        row.prop(p, "profile_mode", expand=True)
        row.prop(p, "role", text="")
        box.label(text="uid %s" % (p.uid[:8] or "-"), icon='DOT')

        box = col.box()
        box.label(text="Cross-section", icon='MOD_ARRAY')
        r = box.row(align=True)
        r.prop(p, "lanes_fwd", text="Fwd")
        r.prop(p, "lanes_bwd", text="Bwd")
        r = box.row(align=True)
        r.prop(p, "aux_fwd", text="Aux Fwd")
        r.prop(p, "aux_bwd", text="Aux Bwd")
        box.prop(p, "aux_side")
        r = box.row(align=True)
        r.prop(p, "drop_side_fwd", text="Drop Fwd")
        r.prop(p, "drop_side_bwd", text="Drop Bwd")
        box.prop(p, "lane_width")
        box.prop(p, "median_width")
        box.prop(p, "design_speed")
        # THE TOTAL, spelled out. `lanes_fwd` and `aux_fwd` are two fields and the carriageway is
        # their sum, so a station with 3 + 1 reads as "3 lanes" to anyone who does not already
        # know that -- a user-reported confusion on the sample highway, where the fourth
        # (outermost) forward lane IS the aux slot the exit ramp continues.
        ep = effective_point(pt)
        tot_f = int(ep.lanes_fwd) + int(ep.aux_fwd)
        tot_b = int(ep.lanes_bwd) + int(ep.aux_bwd)
        box.label(text="carriageway: %d fwd / %d bwd, %.1f m paved"
                       % (tot_f, tot_b, _paved_width(ep)), icon='DRIVER_DISTANCE')
        if int(ep.aux_fwd) or int(ep.aux_bwd):
            box.label(text="the outermost lane is auxiliary -- a ramp continues it",
                      icon='IPO_EASE_OUT')

        box = col.box()
        box.label(text="Edge", icon='MOD_BEVEL')
        r = box.row(align=True)
        r.prop(p, "left_kerb_height", text="Kerb L")
        r.prop(p, "right_kerb_height", text="Kerb R")
        r = box.row(align=True)
        r.prop(p, "left_walk_width", text="Walk L")
        r.prop(p, "right_walk_width", text="Walk R")
        r = box.row(align=True)
        r.prop(p, "shoulder_left_width", text="Shldr L")
        r.prop(p, "shoulder_right_width", text="Shldr R")
        r = box.row(align=True)
        r.prop(p, "parking_left_width", text="Park L")
        r.prop(p, "parking_right_width", text="Park R")

        box = col.box()
        box.label(text="Shape", icon='CURVE_BEZCURVE')
        box.prop(p, "tangent_mode")
        # DERIVED, never stored: a point still flagged AUTO whose Empty has been turned away from
        # the facing the tool gave it is ALREADY shaping the road (`point_model.read_point`
        # promotes it on read). Saying so here is what stops "I rotated it and nothing happened" --
        # the flag catches up at the next Build or Follow Road.
        if p.tangent_mode == pm.AUTO and pm.was_rotated(pt):
            box.label(text="rotated -- this facing already shapes the road",
                      icon='ORIENTATION_GIMBAL')
        if p.tangent_mode == pm.MANUAL:
            # In MANUAL the ROTATION of this Empty is the road's direction here -- rotate it and
            # the road bends. The handles say how hard it leaves and arrives; 0 = automatic.
            box.label(text="rotate the point to bend the road", icon='ORIENTATION_GIMBAL')
            r = box.row(align=True)
            r.prop(p, "handle_out", text="Leaves")
            r.prop(p, "handle_in", text="Arrives")
            box.label(text="handle length in m, 0 = auto", icon='INFO')
        r = box.row(align=True)
        r.operator("rka.align_tangent", icon='ORIENTATION_NORMAL')
        r.operator("rka.sync_facings", icon='CON_FOLLOWPATH')
        box.prop(p, "roll", text="Banking")

        box = col.box()
        box.label(text="Structure", icon='MESH_CUBE')
        box.prop(p, "deck_thickness")
        r = box.row(align=True)
        r.prop(p, "pillar_spacing")
        r.prop(p, "pillar_skip", text="", icon='CANCEL')
        # `ground_z` is SAMPLED by Build, never authored -- shown read-only so the artist can see
        # what the support derived from without being able to desync it (3.3 rule 1).
        r = box.row()
        r.enabled = False
        r.prop(p, "ground_z", text="Ground Z (sampled)")


class RKA_PT_author(bpy.types.Panel):
    """THE GESTURES. Every operator that edits the graph has a button here.

    This panel exists because it was MISSING, and the omission was invisible from inside: the
    operators were all written, registered and tested, and the coverage test asserted that every
    button the sidebar offers is driven -- but never the converse, that every operator is
    REACHABLE. Nine of nineteen were not, including every gesture needed to author a road at all,
    so the plugin was fully working and completely unusable. Both directions are asserted now.

    Each block also states WHAT TO SELECT, live, against the current selection -- because "select
    exactly 2 points" arriving as a red error after you press the button is the worst possible
    moment to learn it."""

    bl_label = "Author"
    bl_idname = "RKA_PT_author"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    def draw(self, context):
        col = self.layout.column()
        sel = selected_points(context)
        n = len(sel)
        act = active_point(context)

        box = col.box()
        box.label(text="Corridor", icon='CURVE_PATH')
        box.operator("rka.new_road", icon='ADD')
        r = box.row()
        r.enabled = act is not None
        r.operator("rka.extend_road", icon='TRACKING_FORWARDS_SINGLE')
        r = box.row()
        r.enabled = (n in (1, 2))
        r.operator("rka.insert_point", icon='ADD')
        r = box.row()
        r.enabled = (n >= 2)
        r.operator("rka.merge_points", icon='AUTOMERGE_ON')
        r = box.row()
        r.enabled = bool(sel)
        r.operator("rka.split_road", icon='UNLINKED')
        r = box.row()
        r.enabled = bool(sel)
        r.operator("rka.delete_point", icon='X')
        box.label(text=_hint(n, act), icon='INFO')

        box = col.box()
        box.label(text="Repair", icon='MODIFIER')
        # Both act on the WHOLE scene and take no selection: they fix the class of defect an
        # artist cannot see in the outliner, so needing to select it first would be circular.
        box.operator("rka.repair_links", icon='LIBRARY_DATA_BROKEN')
        box.operator("rka.tidy_roads", icon='OUTLINER_COLLECTION')
        box.operator("rka.renumber_roads", icon='SORTSIZE')
        box.label(text="whole scene -- no selection needed", icon='INFO')

        box = col.box()
        box.label(text="Connect", icon='LINKED')
        # The three link types are ONE operator with a `type` property, so the three buttons are
        # three preset calls -- not three operators that could disagree about validation.
        r = box.row(align=True)
        r.enabled = (n == 2)
        for label, ltype, icon in (("Segment", pm.LINK_SEGMENT, 'IPO_LINEAR'),
                                   ("Junction", pm.LINK_JUNCTION, 'SNAP_FACE_CENTER'),
                                   ("Aux", pm.LINK_AUX, 'IPO_EASE_OUT')):
            r.operator("rka.connect_selected", text=label, icon=icon).type = ltype
        r = box.row()
        r.enabled = (n == 2)
        r.operator("rka.disconnect_selected", icon='UNLINKED')
        # The ACTIVE point is the source for SEGMENT and JUNCTION. AUX is directed (mainline ->
        # ramp) but the direction is a fact about the two points, not about click order, so `Aux`
        # works from either end -- see `point_ops.resolve_aux_pair`.
        if n == 2:
            box.label(text="from %s (active); Aux works either way round"
                           % (act.name if act else "?"), icon='CHECKMARK')
        else:
            box.label(text="select 2 points -- or name one in Connections", icon='INFO')

        box = col.box()
        box.label(text="Junction", icon='SNAP_FACE_CENTER')
        r = box.row()
        r.enabled = (n >= 2)
        r.operator("rka.make_intersection", icon='SNAP_FACE_CENTER')
        box.label(text="select every mouth of the crossing (2+)" if n < 2
                  else "%d mouth(es) selected" % n,
                  icon='INFO' if n < 2 else 'CHECKMARK')

        box = col.box()
        box.label(text="Ramp", icon='IPO_EASE_OUT')
        r = box.row()
        r.enabled = (n == 2 and act is not None)
        r.operator("rka.make_ramp", icon='IPO_EASE_OUT')
        # ONE POINT, ANY POINT: the whole reason this exists is that `Extend Road` refuses an
        # interior station and nothing else offered to start a road there.
        r = box.row()
        r.enabled = act is not None
        r.operator("rka.branch_ramp", icon='TRACKING_FORWARDS')
        box.operator("rka.align_ramp_to_aux", icon='SNAP_ON')
        box.label(text="select the ramp's mouth and the mainline point -- either order"
                  if n != 2 else "the point declaring the aux slot is the mainline",
                  icon='INFO')
        box.label(text="Branch: one active point, mid-corridor is fine", icon='INFO')

        box = col.box()
        box.label(text="Cross-section brush", icon='BRUSH_DATA')
        r = box.row()
        r.enabled = (n >= 2 and act is not None)
        # The mask defaults to NOTHING on purpose, and the field list is printed BEFORE you press
        # it -- the old brush silently rewrote the median while you meant to change a lane count.
        r.operator_menu_enum("rka.apply_cross_section", "groups",
                             text="Apply Cross-Section", icon='PASTEDOWN')
        box.label(text="active point is the SOURCE; tick only what you mean to change",
                  icon='INFO')

        box = col.box()
        box.label(text="Learn", icon='HELP')
        box.operator("rka.demo_network", icon='PRESET')
        box.label(text="a worked example of all four link types", icon='INFO')

        box = col.box()
        box.label(text="Select", icon='RESTRICT_SELECT_OFF')
        r = box.row(align=True)
        r.operator("rka.select_road", text="Road", icon='CURVE_PATH')
        r.operator("rka.select_junction", text="Junction", icon='SNAP_FACE_CENTER')


def _hint(n, act):
    if act is None:
        return "no active point -- New Road starts one"
    if n == 1:
        return "1 selected: Insert Point splits the span AFTER it"
    if n == 2:
        return "2 selected: Insert Point splits their link, Merge makes them one"
    if n > 2:
        return "%d selected: Merge collapses a run into one station" % n
    return "active: %s" % act.name


# ------------------------------------------------------------------------ the connections list

def _road_of_object(obj):
    for c in obj.users_collection:
        if c.library is None and getattr(c, "rka_road", None) is not None and c.rka_road.is_road:
            return c
    return None


def effective_point(obj):
    """One object -> its EFFECTIVE `PointData`, INHERIT resolved against its road's base.

    Deliberately per-object rather than `read_network`: a panel `draw` runs on every redraw of
    every viewport, and `read_network` walks the whole scene AND calls `view_layer.update()` --
    which has no business happening inside a draw. This is the same projection, one point wide."""
    p = pm.read_point(obj)
    coll = _road_of_object(obj)
    if coll is None:
        return p
    road = pm.RoadData(coll.name)
    for n, _k, _d in pm.ROAD_FIELDS:
        setattr(road, n, getattr(coll.rka_road, n))
    road.name = coll.name
    for n, _k, _d in pm.POINT_FIELDS:
        setattr(road.base, n, getattr(coll.rka_road.base, n))
    return pm.resolve_point(p, road)


def _paved_width(ep):
    neg, pos = pp.lp.paved_extents(pp.build_profile(ep))
    return neg + pos


def link_facts(a_obj, b_obj):
    """The derived read-outs for one connection: `(span_m, shape_text, taper_text_or_None)`.

    Everything here is DERIVED, never stored. A "straight or curved" flag on a link would be one
    more piece of state to keep in sync with geometry that already knows the answer -- and the
    taper verdict comes from `point_validate.taper_min_length`, the gate's own function, so the
    panel and the gate cannot disagree about what is too abrupt."""
    pa = a_obj.matrix_world.translation
    pb = b_obj.matrix_world.translation
    span = (pb - pa).length

    ea, eb = effective_point(a_obj), effective_point(b_obj)
    chord = tuple(pb - pa)
    t_out = ea.tangent if (ea.tangent_mode == pm.MANUAL and ea.tangent) else chord
    t_in = eb.tangent if (eb.tangent_mode == pm.MANUAL and eb.tangent) else chord
    if ea.tangent_mode != pm.MANUAL and eb.tangent_mode != pm.MANUAL:
        shape = "auto"
    else:
        bend = pp.rp.segment_bend_deg(tuple(pa), t_out, tuple(pb), t_in)
        shape = "straight" if bend < pp.rp.STRAIGHT_TOL_DEG else "bend %.0f deg" % bend

    taper = None
    # PER SIDE OF THE DIVIDE, and the wider of the two -- `point_validate.check_tapers`' rule,
    # read the same way. A merge is one driver moving sideways on one carriageway, so a lane
    # opening on the left costs nothing extra because the right already has one.
    wa, wb = pp.lp.paved_extents(pp.build_profile(ea)), pp.lp.paved_extents(pp.build_profile(eb))
    dw = max(abs(wa[0] - wb[0]), abs(wa[1] - wb[1]))
    coll = _road_of_object(a_obj)
    factor = coll.rka_road.taper_factor if coll is not None else 1.0
    # A lane DEPARTING onto a ramp is not a lane merging into traffic, so it wants no merge taper.
    # Same exemption the gate applies, read the same way, or this row would contradict it.
    departs = any(l.type == pm.LINK_AUX for l in a_obj.rka_pt.links)
    if dw > 1e-6 and span > 1e-6 and not departs:
        want = pv.taper_min_length(dw, min(ea.design_speed, eb.design_speed), factor)
        if span < want:
            taper = "%.1f m of width wants %.0f m" % (dw, want)
    return span, shape, taper


class RKA_PT_links(bpy.types.Panel):
    """WHAT THIS POINT IS CONNECTED TO -- the list that was missing.

    Connectivity is the model's central fact and it was, until now, completely invisible: links
    live in a `CollectionProperty` that no panel drew, so the only way to know whether two points
    were joined was to press Build and look at the result. That is also why connecting felt
    unreliable -- there was no confirmation anywhere that it had worked.

    Each row is one link: its type (editable in place), where it goes, how far, whether that
    stretch is straight or bends, and whether the taper is too abrupt for the design speed. Plus
    the two ways to act on it -- walk to the other end, or cut it."""

    bl_label = "Connections"
    bl_idname = "RKA_PT_links"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return active_point(context) is not None

    def draw(self, context):
        col = self.layout.column()
        obj = active_point(context)
        col.label(text=obj.name, icon='EMPTY_ARROWS')

        # -- connect by NAME, so a dense network needs no fiddly two-object selection ----------
        box = col.box()
        box.label(text="Connect To", icon='LINKED')
        box.prop(context.scene, "rka_connect_target", text="")
        tgt = context.scene.rka_connect_target
        r = box.row(align=True)
        r.enabled = tgt is not None and tgt is not obj and po.is_point(tgt)
        for label, ltype, icon in (("Segment", pm.LINK_SEGMENT, 'IPO_LINEAR'),
                                   ("Junction", pm.LINK_JUNCTION, 'SNAP_FACE_CENTER'),
                                   ("Aux", pm.LINK_AUX, 'IPO_EASE_OUT')):
            op = r.operator("rka.connect_selected", text=label, icon=icon)
            op.type = ltype
            op.target = tgt.name if tgt is not None else ""
        box.label(text="Segment/Junction: the ACTIVE point is the source. Aux: either order",
                  icon='INFO')

        links = list(obj.rka_pt.links)
        if not links:
            col.box().label(text="not connected to anything yet", icon='UNLINKED')
            return
        box = col.box()
        box.label(text="%d connection(s)" % len(links), icon='OUTLINER_DATA_POINTCLOUD')
        for i, l in enumerate(links):
            t = l.target
            row = box.row(align=True)
            if t is None or not po.is_point(t):
                # A dangling target is a REPORTABLE defect, not a traceback -- and the artist
                # cannot see it in the outliner, so this is the only place it surfaces.
                row.alert = True
                row.label(text="dangling link", icon='ERROR')
                row.operator("rka.disconnect_selected", text="", icon='X').target = ""
                continue
            row.prop(l, "type", text="")
            row.operator("rka.jump_to_point", text=t.name,
                         icon='RESTRICT_SELECT_OFF').target = t.name
            row.operator("rka.disconnect_selected", text="", icon='X').target = t.name
            span, shape, taper = link_facts(obj, t)
            sub = box.row()
            sub.enabled = False
            sub.label(text="        %.1f m -- %s" % (span, shape))
            if taper is not None:
                warn = box.row()
                warn.alert = True
                warn.label(text="        taper too short: %s" % taper, icon='ERROR')


class RKA_PT_junction(bpy.types.Panel):
    bl_label = "Junction"
    bl_idname = "RKA_PT_junction"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return active_junction(context) is not None or (
            active_point(context) is not None
            and active_point(context).rka_pt.role == pm.INTERSECTION)

    def draw(self, context):
        col = self.layout.column()
        jct = active_junction(context)
        if jct is not None:
            col.label(text=jct.name, icon='SNAP_FACE_CENTER')
        pt = active_point(context)
        if pt is None:
            col.operator("rka.select_junction", icon='RESTRICT_SELECT_OFF')
            return
        p = pt.rka_pt
        box = col.box()
        box.prop(p, "fillet_radius")
        r = box.row(align=True)
        r.prop(p, "allow_cross", toggle=True)
        r.prop(p, "allow_uturn", toggle=True)
        box.prop(p, "traffic_light", toggle=True)
        box = col.box()
        box.label(text="Setback", icon='DRIVER_DISTANCE')
        r = box.row()
        r.enabled = False
        r.prop(p, "setback_solved", text="Solved")
        # An EXPLICIT toggle with its own glyph -- never inferred from "the artist dragged it".
        # An accidental nudge would otherwise opt this mouth out of every future solve, invisibly.
        box.prop(p, "setback_locked", toggle=True, icon='LOCKED' if p.setback_locked
                 else 'UNLOCKED')
        box.operator("rka.auto_setback", icon='MOD_SIMPLIFY')
        box = col.box()
        box.label(text="Handle", icon='ORIENTATION_GIMBAL')
        box.operator("rka.recentre_junctions", icon='PIVOT_BOUNDBOX')
        if jct is not None:
            off = po.junction_drift(jct)
            if off > 0.5:
                box.label(text="handle is %.1f m off centre" % off, icon='ERROR')
        box.label(text="Grab the JCT_* Empty to move or turn the whole crossing (Z only)",
                  icon='INFO')
        # THE ONE THING NO CODE HERE CAN FIX. Blender rotates about the scene's Transform Pivot
        # Point; with the default Median Point and the handle selected alone, that IS its origin,
        # which is what `Recentre Handles` makes correct. Set to 3D Cursor it will pivot on the
        # cursor no matter where the handle sits, and the artist has to know that is theirs.
        if getattr(context.scene.tool_settings, "transform_pivot_point", '') == 'CURSOR':
            box.label(text="pivot is 3D Cursor -- it will turn about the cursor, not the centre",
                      icon='ERROR')
        col.operator("rka.select_junction", icon='RESTRICT_SELECT_OFF')


class RKA_PT_road(bpy.types.Panel):
    bl_label = "Road"
    bl_idname = "RKA_PT_road"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    def draw(self, context):
        col = self.layout.column()
        road = active_road(context)
        if road is None:
            col.label(text="No road collection for the active point", icon='INFO')
            return
        r = road.rka_road
        box = col.box()
        box.label(text=road.name, icon='OUTLINER_COLLECTION')
        box.prop(r, "road_class")
        box.prop(r, "zone_id")
        box.prop(r, "is_loop", toggle=True)
        box.prop(r, "ped_access", toggle=True)
        col.label(text="Base cross-section (INHERIT stations take this)")
        box = col.box()
        rr = box.row(align=True)
        rr.prop(r.base, "lanes_fwd", text="Fwd")
        rr.prop(r.base, "lanes_bwd", text="Bwd")
        box.prop(r.base, "lane_width")
        box.prop(r.base, "median_width")
        box.prop(r.base, "design_speed")
        col.label(text="Edge & taper")
        box = col.box()
        # WHERE a barrier goes is derived (no ped access, or elevated); this is only how tall.
        box.prop(r, "barrier_height", text="Barrier Height")
        box.label(text="0 = none; built on open edges where the road is elevated"
                       if r.ped_access else "0 = none; built along every open edge (no ped access)",
                  icon='INFO')
        box.prop(r, "taper_factor", text="Taper Factor")
        box.label(text="1.0 = the real merge-taper standard; lower it for a compressed map",
                  icon='INFO')

        # ---- WHAT THE ROAD IS MADE OF ------------------------------------------------------------
        # Per road, not per point: a stretch of the same road paved differently is a different
        # road. Each row is a NAME, so `<stem>.roads.json` round-trips it; blank takes the layer's
        # default. An asset REPLACES its layer's parametric band with the artist's own swept
        # section -- and carries its own material with it.
        col.label(text="Style -- material, or an asset that brings its own shape")
        box = col.box()
        box.prop(r, "median_style", text="Median")
        box.prop(r, "markings", toggle=True,
                 icon='CHECKBOX_HLT' if r.markings else 'CHECKBOX_DEHLT')
        kit = pstyle.kit_collection()
        for slot, mat_field, asset_field, default_key in pstyle.SLOTS:
            row = box.row(align=True)
            row.label(text=slot.replace("_", " "))
            row.prop_search(r, mat_field, bpy.data, "materials", text="", icon='MATERIAL')
            if asset_field:
                sub = row.row(align=True)
                sub.enabled = kit is not None
                if kit is not None:
                    sub.prop_search(r, asset_field, kit, "all_objects", text="", icon='OUTLINER_OB_CURVE')
                else:
                    sub.label(text="", icon='OUTLINER_OB_CURVE')
        if kit is None:
            box.operator("rka.link_road_kit", icon='LINKED')
            box.label(text="link the kit to name a swept section instead of a material",
                      icon='INFO')
        col.operator("rka.select_road", icon='RESTRICT_SELECT_OFF')


class RKA_PT_preview(bpy.types.Panel):
    """THE EXPORT, not the authoring.

    Every other panel here shows what the artist wrote. This one shows what Godot will receive,
    which is a different object: a directed lane graph with explicit successors. A road can be
    built, gate-green and still export a lane nothing can reach -- and the only in-game symptom is
    "that ramp is always empty", which nobody traces back to authoring. So the counts live where
    the artist is already looking, and the defect lines are named lanes, not a total."""

    bl_label = "Preview"
    bl_idname = "RKA_PT_preview"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    def draw(self, context):
        col = self.layout.column()
        scene = context.scene
        r = col.row(align=True)
        r.prop(scene, "rka_preview_flow", toggle=True, icon='FORCE_WIND')
        r.prop(scene, "rka_preview_cars", toggle=True, icon='AUTO')
        # WHICH geometry, first -- because "the lane" and "the Path3D Godot drives" are two
        # different objects and the difference is the thing this panel is for.
        col.prop(scene, "rka_preview_geometry", text="")
        col.prop(scene, "rka_preview_labels", toggle=True, icon='SYNTAX_OFF')
        r = col.row(align=True)
        r.enabled = bool(getattr(scene, "rka_preview_cars", False))
        r.prop(scene, "rka_preview_density")
        r.prop(scene, "rka_preview_speed")
        r = col.row(align=True)
        r.operator("rka.preview_refresh", icon='FILE_REFRESH')
        r.operator("rka.preview_report", icon='INFO')
        if not getattr(scene, "rka_preview_flow", False):
            col.label(text="shows the EXPORTED lane graph, not the points", icon='INFO')
            return
        rep = pv3.report(scene)
        if rep is None:
            col.label(text="export failed -- run Validate", icon='ERROR')
            return
        box = col.box()
        box.label(text="%d lanes, %d junctions, %d spawnable"
                       % (rep["lanes"], rep["junctions"], rep["spawnable"]), icon='CON_FOLLOWPATH')
        # A RAMP nothing leads to is called out FIRST and by name: it is the defect this whole
        # panel was added to make visible, and it reads in-game only as an empty ramp.
        for lane in rep["ramp_orphans"][:4]:
            box.label(text="ramp unreachable: %s" % lane, icon='ERROR')
        for lane, near in rep["broken"][:4]:
            box.label(text="%s -> nothing (touches %s)" % (lane, near[0]), icon='ERROR')
        # The Path3D is not the lane. Named in metres, because the number IS the severity: 0.6 m is
        # a car half off its lane and 22 m is a car in a building.
        for lane, dev in (rep.get("path_off_road") or ())[:4]:
            box.label(text="path %.2f m off road: %s" % (dev, lane), icon='ERROR')
        others = [l for l in rep["unreached"] if l not in rep["ramp_orphans"]]
        if others:
            box.label(text="%d lane(s) with no predecessor" % len(others), icon='QUESTION')
        if rep["open_end"]:
            box.label(text="%d open end(s) at the network edge" % len(rep["open_end"]),
                      icon='CHECKMARK')
        if not rep["ramp_orphans"] and not rep["broken"]:
            box.label(text="every chain closes", icon='CHECKMARK')
        if not rep.get("path_off_road"):
            box.label(text="every Path3D tracks its lane", icon='CHECKMARK')


class RKA_PT_build(bpy.types.Panel):
    bl_label = "Build"
    bl_idname = "RKA_PT_build"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    def draw(self, context):
        col = self.layout.column()
        col.operator("rka.point_build", icon='MOD_BUILD')
        col.operator("rka.validate", icon='CHECKMARK')
        col.operator("rka.export_lanekit", icon='EXPORT')
        col.separator()
        r = col.row(align=True)
        r.operator("rka.save_record", icon='FILE_TICK')
        r.operator("rka.load_record", icon='FILE_REFRESH')
        col.separator()
        r = col.row(align=True)
        r.prop(context.scene, "rka_live_rebuild", toggle=True, icon='TIME')
        r.prop(context.scene, "rka_overlay", toggle=True, icon='OVERLAY')
        col.operator("rka.point_clear", icon='TRASH')


CLASSES = (RKA_PT_author, RKA_PT_point, RKA_PT_links, RKA_PT_junction, RKA_PT_road,
           RKA_PT_preview, RKA_PT_build)


def _point_poll(_self, obj):
    return po.is_point(obj)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    # UI state, not authored data: which point the Connections panel will link the active one to.
    bpy.types.Scene.rka_connect_target = bpy.props.PointerProperty(
        type=bpy.types.Object, poll=_point_poll, name="Connect To",
        description="Link the ACTIVE road point to this one")


def unregister():
    del bpy.types.Scene.rka_connect_target
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

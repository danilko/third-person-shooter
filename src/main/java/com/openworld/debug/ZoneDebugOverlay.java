package com.openworld.debug;

import com.openworld.game.PlayerRegistry;
import com.openworld.world.ZoneManager;
import com.openworld.world.ZoneMarker;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.CanvasLayer;
import godot.api.Control;
import godot.api.Label;
import godot.core.Color;
import godot.core.StringName;
import godot.core.Vector2;
import godot.global.GD;

/**
 * Debug-only HUD readout of "which district/zone is the local player currently over," so
 * walking across the world (or using {@code DebugHarness}'s F1 teleport-cycle) makes zone
 * enter/exit obvious without reading the console log. Builds its own {@link Label} in code
 * (no .tscn needed — same procedural-UI convention {@code ZoneMarker}'s debug visuals use)
 * so it can be dropped into any debug host scene as a single plain node.
 *
 * <p>Shows the nearest registered zone ({@link ZoneManager#getNearestMarker}, works even
 * before anything streams in) plus, when that zone is actually loaded and carries a
 * {@link com.openworld.world.RegionConfig}, the active region name
 * ({@link ZoneManager#getActiveRegionMarker}) — so "loaded vs merely nearby" is visible too.
 */
@Script(className = "ZoneDebugOverlay")
public class ZoneDebugOverlay extends CanvasLayer {

    private Label label;
    private double refreshTimer = 0.0;
    private static final double REFRESH_INTERVAL = 0.25;

    @Register
    @Override
    public void _ready() {
        label = new Label();
        label.setPosition(new Vector2(16, 16));
        label.setText("District: —");
        label.addThemeColorOverride(new StringName("font_color"), new Color(1.0, 1.0, 1.0, 0.85));
        label.addThemeColorOverride(new StringName("font_shadow_color"), new Color(0.0, 0.0, 0.0, 0.9));
        label.addThemeConstantOverride(new StringName("shadow_offset_x"), 1);
        label.addThemeConstantOverride(new StringName("shadow_offset_y"), 1);
        label.setMouseFilter(Control.MouseFilter.IGNORE);
        addChild(label);
    }

    @Register
    @Override
    public void _process(double delta) {
        refreshTimer -= delta;
        if (refreshTimer > 0.0) return;
        refreshTimer = REFRESH_INTERVAL;

        if (PlayerRegistry.getPlayers().isEmpty()) { label.setText("District: — (no player)"); return; }

        ZoneManager mgr = ZoneManager.get();
        if (mgr == null) { label.setText("District: — (no ZoneManager)"); return; }

        ZoneMarker nearest = mgr.getNearestMarker();
        if (nearest == null || nearest.zone == null) { label.setText("District: —"); return; }

        String zoneId = nearest.zone.zoneId;
        ZoneMarker active = mgr.getActiveRegionMarker();
        boolean loaded = active == nearest;

        StringBuilder sb = new StringBuilder("District: ").append(zoneId)
                .append(loaded ? " [LOADED]" : " [streaming...]");
        if (active != null && active.zone.regionConfig != null) {
            sb.append("  Region: ").append(active.zone.regionConfig.regionName);
        }
        label.setText(sb.toString());
    }
}

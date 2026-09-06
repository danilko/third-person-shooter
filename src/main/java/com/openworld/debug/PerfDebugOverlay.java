package com.openworld.debug;

import com.openworld.world.ZoneManager;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.CanvasLayer;
import godot.api.Control;
import godot.api.Label;
import godot.api.Performance;
import godot.core.Color;
import godot.core.StringName;
import godot.core.Vector2;

/**
 * Debug-only HUD readout of the engine performance monitors (Shift+F3 via {@code DebugHarness}) —
 * FPS, frame/physics time, draw calls, primitives, objects drawn, video/static memory, and
 * object/node/orphan counts — plus the JVM heap (all game logic lives there; the engine's
 * MEMORY_STATIC never sees it) and {@link ZoneManager#debugStatsLine} (streaming is this
 * project's dominant perf variable). Builds its own {@link Label} in code, same procedural-UI
 * convention as {@code ZoneDebugOverlay} (which it stacks under at y=40).
 *
 * <p>The orphan-node count doubles as a live leak check: repeated zone hot-reloads (Shift+F5)
 * with a climbing orphan count = a staged-children/pool leak regression.
 */
@Script(className = "PerfDebugOverlay")
public class PerfDebugOverlay extends CanvasLayer {

    private Label label;
    private double refreshTimer = 0.0;
    private static final double REFRESH_INTERVAL = 0.25;
    private static final double MIB = 1024.0 * 1024.0;

    @Register
    @Override
    public void _ready() {
        label = new Label();
        label.setPosition(new Vector2(16, 40));
        label.setText("perf: —");
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
        if (!isVisible()) return;
        refreshTimer -= delta;
        if (refreshTimer > 0.0) return;
        refreshTimer = REFRESH_INTERVAL;
        label.setText(buildText());
    }

    private String buildText() {
        double fps = Performance.getMonitor(Performance.Monitor.TIME_FPS);
        double frameMs = Performance.getMonitor(Performance.Monitor.TIME_PROCESS) * 1000.0;
        double physMs = Performance.getMonitor(Performance.Monitor.TIME_PHYSICS_PROCESS) * 1000.0;
        double drawCalls = Performance.getMonitor(Performance.Monitor.RENDER_TOTAL_DRAW_CALLS_IN_FRAME);
        double prims = Performance.getMonitor(Performance.Monitor.RENDER_TOTAL_PRIMITIVES_IN_FRAME);
        double objsDrawn = Performance.getMonitor(Performance.Monitor.RENDER_TOTAL_OBJECTS_IN_FRAME);
        double vram = Performance.getMonitor(Performance.Monitor.RENDER_VIDEO_MEM_USED) / MIB;
        double memStatic = Performance.getMonitor(Performance.Monitor.MEMORY_STATIC) / MIB;
        double memStaticMax = Performance.getMonitor(Performance.Monitor.MEMORY_STATIC_MAX) / MIB;
        double objCount = Performance.getMonitor(Performance.Monitor.OBJECT_COUNT);
        double nodeCount = Performance.getMonitor(Performance.Monitor.OBJECT_NODE_COUNT);
        double orphanCount = Performance.getMonitor(Performance.Monitor.OBJECT_ORPHAN_NODE_COUNT);
        Runtime rt = Runtime.getRuntime();
        double jvmUsed = (rt.totalMemory() - rt.freeMemory()) / MIB;
        double jvmMax = rt.maxMemory() / MIB;

        StringBuilder sb = new StringBuilder(256);
        sb.append(String.format("FPS %.0f   frame %.1fms  phys %.1fms%n", fps, frameMs, physMs));
        sb.append(String.format("draw %s  prim %s  objs %s   vram %.0f MiB%n",
                compact(drawCalls), compact(prims), compact(objsDrawn), vram));
        sb.append(String.format("mem %.0f/%.0f MiB  jvm %.0f/%.0f MiB%n",
                memStatic, memStaticMax, jvmUsed, jvmMax));
        sb.append(String.format("obj %s  nodes %s  orphans %.0f",
                compact(objCount), compact(nodeCount), orphanCount));

        ZoneManager mgr = ZoneManager.get();
        if (mgr != null) sb.append(System.lineSeparator()).append(mgr.debugStatsLine());
        return sb.toString();
    }

    /** 12345 → "12.3k", 2100000 → "2.1M" — keeps the panel narrow at open-world counts. */
    private static String compact(double v) {
        if (v >= 1_000_000) return String.format("%.1fM", v / 1_000_000.0);
        if (v >= 10_000) return String.format("%.1fk", v / 1_000.0);
        return String.format("%.0f", v);
    }
}

package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Script;
import godot.api.PackedScene;
import godot.api.Resource;
import godot.core.Vector3;
import godot.core.VariantArray;

/**
 * Data for one streamed open-world zone (PLAN.md Part E / E1) — a placeholder AABB zone:
 * a bounding box plus the AI population to stream in around it. Authored as a {@code .tres}
 * and assigned to a {@link ZoneMarker} placed in the level; the marker's world position is
 * the zone <b>center</b>, so designers position a zone by dragging its marker.
 *
 * <p>Geometry is optional — placeholder zones leave {@link #geometry} null and only stream AI;
 * a real zone (once Blender content exists, per BLENDER_CONVENTIONS) sets a geometry scene that
 * is instanced locally on every peer when the zone loads.
 *
 * <p>Load/unload uses hysteresis ({@code unloadRadius} > {@code loadRadius}) so a player lingering
 * at the boundary does not flicker the zone in and out.
 */
@Script(className = "Zone")
public class Zone extends Resource {

    /** Stable id for logging / debugging. */
    @Export public String zoneId = "";

    /** Full extents (metres) of the spawn box, centered on the marker. AI spawn at random XZ
     *  points inside this box at the marker's Y. */
    @Export public Vector3 size = new Vector3(60.0f, 10.0f, 60.0f);

    /** Load when a player comes within this horizontal distance (m) of the marker. */
    @Export public float loadRadius = 200.0f;

    /** Unload when all players are beyond this horizontal distance (m). Must exceed loadRadius. */
    @Export public float unloadRadius = 350.0f;

    /** Optional zone geometry instanced on load (null for placeholder zones). */
    @Export public PackedScene geometry;

    /**
     * Optional {@code res://} path to this zone's geometry piece, resolved lazily on first stream
     * (and cached into {@link #geometry}). This is the incremental-authoring seam: the master wires
     * a <b>predictable</b> path here for every zone up front, so a district piece authored/baked
     * <i>after</i> the master went live is picked up on the next run with <b>no master re-bake</b>.
     * Empty = none. {@code geometry} (a directly-assigned scene) still wins if set.
     */
    @Export public String geometryPath = "";

    /**
     * Optional {@code res://} path to a LOW-DETAIL PLACEHOLDER tier for this zone's geometry (a
     * synthesized "PLATEAU-style" simple-box version — see {@code lib/lod_low.py} — for procedural
     * districts too cheap in object count to bother streaming). Unlike {@link #geometryPath} (lazily
     * streamed in/out with the zone), this tier is instanced by {@link ZoneMarker} EAGERLY at
     * {@code _ready()} — cheap enough to stay resident always — and is only removed for the moment
     * the full-detail {@link #geometry} is actually loaded (then re-instanced the moment it unloads),
     * so a distant/not-yet-streamed district still reads as a real place instead of empty ground.
     * Empty = no placeholder tier was baked for this zone (e.g. a real-data PLATEAU precinct).
     */
    @Export public String lodLowGeometryPath = "";

    /** Ambient AI spawn groups streamed in on load. */
    @Export
    public VariantArray<SpawnConfig> spawnConfigs = new VariantArray<>(SpawnConfig.class);

    /** Named story AI spawned (at stable ids) on load. */
    @Export
    public VariantArray<NamedCharacterConfig> namedCharacters =
            new VariantArray<>(NamedCharacterConfig.class);

    /** Ambient vehicle traffic groups streamed in on load (PLAN.md I3). */
    @Export
    public VariantArray<VehicleSpawnConfig> vehicleSpawnConfigs =
            new VariantArray<>(VehicleSpawnConfig.class);

    /**
     * Optional region tuning (PLAN.md I4): faction rules, AI/vehicle density, LOD range, lighting/fog.
     * Null = a plain zone that changes no ambience. {@code ZoneManager} scales this zone's spawn
     * counts by the densities on load, and applies the global ambience when this becomes the active region.
     */
    @Export public RegionConfig regionConfig = null;

    public Zone() { super(); }
}

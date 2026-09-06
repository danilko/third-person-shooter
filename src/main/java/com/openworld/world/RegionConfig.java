package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Script;
import godot.api.Resource;
import com.openworld.character.FactionTable;

/**
 * Per-region ambience + simulation tuning (PLAN.md I4). A {@link Zone} optionally carries one;
 * when that zone becomes the player's <b>active region</b> (the nearest loaded zone that has a
 * RegionConfig), {@code ZoneManager.applyRegion} pushes these values into the global systems —
 * faction relationships, AI level-of-detail range, and the scene environment — so walking from a
 * dense city zone into a quiet mountain zone visibly shifts faction rules, traffic/AI density, and
 * lighting/fog.
 *
 * <p>Two scopes (see ZoneManager):
 * <ul>
 *   <li><b>Per-zone</b> — {@link #ambientAIDensity} / {@link #vehicleDensity} scale that zone's own
 *       spawn counts as it loads (each loaded zone applies its own).</li>
 *   <li><b>Global/ambient</b> — faction table, {@link #aiLodBias}, lighting/fog, music — driven by
 *       the single <i>active</i> region and (re)applied only when that region changes, so multiple
 *       simultaneously-loaded zones don't thrash the environment.</li>
 * </ul>
 *
 * <p>All effects are local presentation / local faction resolution, so {@code applyRegion} runs on
 * every peer (unlike host-only AI spawning). Every field has a no-op default ({@code 1.0} densities,
 * {@code 1.0} bias, {@code null} table, {@code 0} fog), so a zone with no RegionConfig — or a
 * RegionConfig left at defaults — changes nothing.
 */
@Script(className = "RegionConfig")
public class RegionConfig extends Resource {

    /** Human-readable region name (debug / future HUD). */
    @Export public String regionName = "Region";

    /**
     * Faction relationships to install while this region is active (a {@code .tres} authored like
     * {@code DefaultFactions.tres}). Null leaves the FactionManager's current table untouched, so a
     * neutral region needn't ship a table; a region that wants its own rules (a gang turf where two
     * ambient factions are hostile) assigns one. Applied via {@code FactionManager.applyTable}.
     */
    @Export public FactionTable factionTable = null;

    /** Multiplier on each {@link SpawnConfig#count} (ambient AI) when a zone in this region loads. */
    @Export public float ambientAiDensity = 1.0f;

    /** Multiplier on each {@link VehicleSpawnConfig#count} (ambient traffic) for zones in this region. */
    @Export public float vehicleDensity = 1.0f;

    /**
     * Scales the D2 AI-LOD distances ({@code AICharacter.LOD_*_DIST}) while active: {@code > 1} keeps
     * AI fully simulated farther out (open rural region), {@code < 1} shortens active range (dense
     * city — more AI on screen, so tighten the budget). 1.0 = the built-in defaults.
     */
    @Export public float aiLodBias = 1.0f;

    /**
     * Sun/key-light colour temperature in Kelvin (warm city ≈ 5200, cool mountain ≈ 8000); applied to
     * the scene's {@code DirectionalLight3D} via a Kelvin→RGB approximation. 0 = leave the light alone.
     */
    @Export public float lightTemperature = 0.0f;

    /** Scene {@code Environment} fog density while active (0 = fog off / leave as authored). */
    @Export public float fogDensity = 0.0f;

    /**
     * Name of the {@code AudioServer} bus to bring up as this region's ambient bed (forward-compat
     * hook — empty = no music change; the ambient-music player itself is a later audio task).
     */
    @Export public String ambientMusicBus = "";

    public RegionConfig() { super(); }
}

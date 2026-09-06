package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Script;
import godot.api.Resource;
import com.openworld.ai.AIBehaviorConfig;
import com.openworld.character.Faction;

/**
 * One ambient-vehicle spawn group inside a {@link Zone} (PLAN.md I3).
 *
 * <p>The vehicle counterpart of {@link SpawnConfig}: "spawn N vehicles of scene Z driving route
 * {@link #routeName}". {@code ZoneManager} replays this recipe host/SP-side when a zone loads,
 * attaching a {@link com.openworld.ai.vehicle.VehicleAIController} to each spawned body.
 *
 * <p>{@link #routeName} is resolved to a {@link VehicleRoute} node in the active scene by name
 * (the route is scene content — it can't be referenced from a Resource directly).
 */
@Script(className = "VehicleSpawnConfig")
public class VehicleSpawnConfig extends Resource {

    /** Faction stamped onto each spawned vehicle's CharacterInfo (drives the nameplate tint). */
    @Export public String faction = Faction.NEUTRAL;

    /** Number of vehicles to spawn for this group when the zone loads. */
    @Export public int count = 2;

    /** Vehicle scene each body is instanced from. */
    @Export public String vehicleScenePath =
            "res://src/main/resources/com/openworld/vehicle/Vehicle.tscn";

    /** Cruise throttle fraction (0–1) applied while driving the route. */
    @Export public float cruiseThrottle = 0.4f;

    /** Name of the {@link VehicleRoute} node (in the active scene) these vehicles follow. */
    @Export public String routeName = "";

    /**
     * Behaviour of the AI driver seated in each spawned car (PLAN.md I3c) — null = AICharacter
     * {@code DEFAULTS} (a civilian who flees when carjacked). Assign a config whose
     * {@code reactToCarjack = "FIGHT"} (and/or a hostile faction) for gang/aggressive traffic.
     */
    @Export public AIBehaviorConfig behaviorConfig = null;

    public VehicleSpawnConfig() { super(); }
}

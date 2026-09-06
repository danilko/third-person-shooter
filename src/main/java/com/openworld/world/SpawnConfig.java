package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Script;
import godot.api.Resource;
import com.openworld.ai.AIBehaviorConfig;
import com.openworld.character.Faction;

/**
 * One ambient-AI spawn group inside a {@link Zone} (PLAN.md Part E / E1).
 *
 * <p>Describes "spawn N faction X AIs with behaviour Y, armed with weapon Z" — the recipe
 * {@code ZoneManager} replays when a zone loads. Anonymous/ambient AI all share the one
 * {@code AICharacter.tscn} archetype, so there is no per-config scene field here (named story
 * characters that need a specific scene use {@link NamedCharacterConfig} instead).
 *
 * <p>Patrol radius is <b>not</b> a separate field: it lives on {@link #behaviorConfig}
 * ({@code AIBehaviorConfig.patrolRadius}). Leave {@code behaviorConfig} null to use the
 * AICharacter scene default.
 */
@Script(className = "SpawnConfig")
public class SpawnConfig extends Resource {

    /** Faction string stamped onto each spawned AI's CharacterInfo (see {@link Faction}). */
    @Export public String faction = Faction.ENEMY;

    /** Per-AI behaviour profile. Null → AICharacter's shared DEFAULTS apply. */
    @Export public AIBehaviorConfig behaviorConfig;

    /** Number of AIs to spawn for this group when the zone loads. */
    @Export public int count = 3;

    /** Weapon scene each AI is equipped with on spawn. */
    @Export public String weaponScenePath =
            "res://src/main/resources/com/openworld/weapon/AR4.tscn";

    public SpawnConfig() { super(); }
}

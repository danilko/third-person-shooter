package com.character;

import godot.annotation.RegisterClass;

/**
 * Type-safe representation of the player stances.
 * The {@code key} matches both the Godot node name of the stance child node and
 * the string keys used in the {@link Player#stances} Dictionary.
 */

public enum StanceName {
    UPRIGHT("Upright"),
    CROUCH("Crouch"),
    CRAWL("Crawl");

    private final String key;

    StanceName(String key) {
        this.key = key;
    }

    /** The string key used for Godot Dictionary lookups and node name comparisons. */
    public String getKey() {
        return key;
    }

    /** Parse from a Dictionary key or node name string. Falls back to {@code UPRIGHT}. */
    public static StanceName fromKey(String key) {
        for (StanceName s : values()) {
            if (s.key.equalsIgnoreCase(key)) return s;
        }
        return UPRIGHT;
    }
}

package com.character;


/**
 * Type-safe representation of the three movement states.
 * The {@code key} matches the string used in Godot inspector Dictionary keys and
 * in {@link Stance#getMovementState}. The {@code id} matches the {@code id} field
 * on the corresponding {@link MovementState} resource.
 */

public enum MovementType {
    IDLE("Idle", 0),
    WALK("Walk", 1),
    SPRINT("Sprint", 2);

    private final String key;
    private final int id;

    MovementType(String key, int id) {
        this.key = key;
        this.id = id;
    }

    /** The string key used in Godot inspector / Dictionary lookups. */
    public String getKey() {
        return key;
    }

    /** Matches the {@code id} field on {@link MovementState} resources. */
    public int getId() {
        return id;
    }

    /** Parse from a {@link MovementState#getId()} value. Falls back to {@code IDLE}. */
    public static MovementType fromId(int id) {
        for (MovementType t : values()) {
            if (t.id == id) return t;
        }
        return IDLE;
    }

    /** Parse from a dictionary key string. Falls back to {@code IDLE}. */
    public static MovementType fromKey(String key) {
        for (MovementType t : values()) {
            if (t.key.equalsIgnoreCase(key)) return t;
        }
        return IDLE;
    }
}

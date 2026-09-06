package com.openworld.world;

import godot.core.Vector3;

/**
 * A directional traffic lane, abstracted over its concrete representation — a hand-authored
 * Marker3D-list {@link VehicleRoute} (Catmull-Rom smoothed) or a computed-geometry
 * {@link PathLaneRoute} (native {@code Path3D}/{@code Curve3D}, e.g. from
 * {@code assets/world_source/lib/intersection_kit.py}'s exported junctions). {@link LaneGraph}
 * and {@link com.openworld.ai.vehicle.VehicleAIController} are written against this interface, so
 * a scene can freely mix both representations — {@link LaneGraph}'s endpoint-proximity junction
 * derivation treats any {@code Lane} the same, meaning a hand-authored network and a
 * Blender-generated intersection connect automatically wherever their endpoints coincide, no
 * explicit wiring needed.
 *
 * <p>Exactly the surface {@code LaneGraph}/{@code VehicleAIController} already called on
 * {@code VehicleRoute} before this interface existed — see those two classes for how each method
 * is used.
 */
public interface Lane {

    /** First point of the lane's raw centerline (no smoothing/offset) — used to cluster junctions. */
    Vector3 startPoint();

    /**
     * {@link #startPoint()}, cached for the lifetime of this lane's tree entry (static content,
     * never re-derived). {@code ZoneManager.findRoute}'s spawn-time prefix scan distance-
     * filters every registered lane against this, so it must not re-walk the underlying
     * representation (a {@code VehicleRoute}'s marker children, a {@code PathLaneRoute}'s baked
     * curve) per candidate — a plain {@code pointAtLength(0)} would NOT do, since it returns the
     * smoothed/lane-offset path's start (a different point whenever a lane offset is set) and its
     * own cache-validity check still re-walks the source data every call.
     */
    Vector3 entryPoint();

    /** Last point of the lane's raw centerline (no smoothing/offset) — used to cluster junctions. */
    Vector3 endPoint();

    /** Unit XZ travel direction leaving the first point, or null when undefined (under 2 points). */
    double[] startTangentXZ();

    /** Unit XZ travel direction arriving at the last point, or null when undefined (under 2 points). */
    double[] endTangentXZ();

    /** True = a closed ring (cars circulate forever). False = a one-way lane ending in {@link #getEndBehavior()}. */
    boolean isLoop();

    /** Total arc length of the followed (smoothed/offset or native-baked) path. */
    double total();

    /** Point at arc length {@code s} along the followed path (wraps for a loop, clamps otherwise). */
    Vector3 pointAtLength(double s);

    /**
     * Arc length of the point on the followed path nearest {@code pos}, searched only within
     * ±{@code window} of {@code aroundS} (pass {@code aroundS < 0} for a full-path search on
     * first acquire) — the windowed search is what avoids snapping to the wrong corner on a
     * self-overlapping/tightly-curved path.
     */
    double lengthAtNearest(Vector3 pos, double aroundS, double window);

    /** A weighted-random explicit successor, or null when none is set/resolves (falls back to
     *  {@link LaneGraph}'s geometry-derived connectivity). */
    Lane pickNextRoute();

    /** Resolve a sibling lane by node name, or null if not found. */
    Lane resolveRoute(String name);

    /** Turn movement this lane makes through a junction — "L"/"S"/"R" on a turn/through connector,
     *  "" on a plain lane. */
    String getTurn();

    /** Compass/arm label a car on this connector arrives from, "" on a plain lane. */
    String getApproach();

    /** End-of-lane behaviour: {@link VehicleRoute#END_CHAIN} / {@link VehicleRoute#END_UTURN} /
     *  {@link VehicleRoute#END_DESPAWN}. */
    String getEndBehavior();

    /** Optional explicit return lane name for {@link VehicleRoute#END_UTURN} (else derived from
     *  {@link LaneGraph}). */
    String getReturnRoute();
}

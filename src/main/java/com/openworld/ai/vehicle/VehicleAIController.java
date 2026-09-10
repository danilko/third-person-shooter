package com.openworld.ai.vehicle;

import com.openworld.control.Controllable;
import com.openworld.control.Controller;
import com.openworld.control.UserCommand;
import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.util.WeightedPick;
import com.openworld.world.IntersectionZone;
import com.openworld.world.Lane;
import com.openworld.world.PathLaneRoute;
import com.openworld.world.LaneGraph;
import com.openworld.world.VehicleRoute;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Node;
import godot.api.RayCast3D;
import godot.core.Vector3;
import godot.global.GD;

import java.util.List;

/**
 * AI brain for an ambient-traffic {@link Vehicle} (PLAN.md I3 / I3b).
 *
 * <p>Runs a minimal FSM ({@link CruiseState} ⇄ {@link BrakeState}) in {@link #gatherInput}, mirroring
 * the on-foot {@link com.openworld.ai.AIController}. The controller holds all mutable memory (state +
 * current lane + progress along it); the singleton states are stateless.
 *
 * <p><b>Lane following.</b> The car follows its assigned {@link VehicleRoute} (a directional lane) over
 * that lane's <i>smoothed, lane-offset</i> path, tracked by <b>arc length</b> (monotonic progress, not a
 * global nearest-point search — that snapped at corners). Pure pursuit aims a speed-proportional
 * look-ahead ahead along the path. Steering is emitted as a <b>target wheel angle</b>
 * ({@code UserCommand.steerToTarget}) so the wheel converges and holds it instead of winding the rate
 * integrator — the cornering-wobble fix.
 *
 * <p><b>Junctions / recycling.</b> At a lane's end the car continues via the geometry-derived
 * {@link LaneGraph} (or the lane's explicit {@code nextRoutes}); with no successor it follows the lane's
 * {@code endBehavior} — U-turn (drive back) or despawn (the zone respawns one elsewhere).
 */
@Script(className = "VehicleAIController")
public class VehicleAIController extends Controller {

    /** Throttle fraction applied while cruising along the lane (0–1). */
    @Export public float cruiseThrottle = 0.4f;

    /** Target cruising speed (m/s) — throttle eases to zero above it. Lowered 2026-07-27
     *  (user-requested experiment: does slower ambient traffic stop flying/pushing off the road at
     *  corners/seams?) from 11 (~40 km/h, city pace) to 7 (~25 km/h) — a single global default for
     *  now. Planned follow-up, not built yet: per-road-type speed (highway lanes faster) and
     *  per-archetype speed (a racing AI faster still) — both would read from data (a road/lane
     *  property, or a distinct AIBehaviorConfig-style resource per AI archetype) rather than this
     *  one shared default, once there's a road-type signal to key off of. */
    @Export public float cruiseSpeed = 7f;

    /** Speed band (m/s) over which throttle fades from full to zero above cruiseSpeed. */
    @Export public float cruiseSpeedFalloff = 3f;

    /** Speed-proportional look-ahead: {@code clamp(lookaheadMin + speed·lookaheadSpeedGain, …, lookaheadMax)}. */
    @Export public float lookaheadMin = 4.0f;
    @Export public float lookaheadSpeedGain = 0.5f;
    @Export public float lookaheadMax = 14.0f;

    /** How far beyond the steer look-ahead to probe the lane for an upcoming bend (m) — corner anticipation. */
    @Export public float curvatureProbe = 5.0f;

    /** Heading-error → steer-angle gain. The car commands a target wheel angle ∝ steerGain·sin(error),
     *  saturating to full lock; higher = sharper turn-in. */
    @Export public float steerGain = 2.5f;

    /** How much to cut throttle in turns (0 = never, 1 = stop in a hard turn). */
    @Export public float turnSlowdown = 0.7f;

    /** Distance (m) before a chained lane end at which the car eases off for the junction. The
     *  curvature probe can't see past the current route, so an upcoming 90° turn connector is
     *  invisible until adopted — without this cars enter junctions at full cruise speed and fly
     *  off the turn (roads-v2 Phase 1). */
    @Export public float junctionSlowdown = 18.0f;

    /** Throttle multiplier while approaching a junction or riding a non-straight turn connector. */
    @Export public float junctionThrottleScale = 0.45f;

    private static final double END_THRESHOLD = 3.0;   // m from the lane end = "arrived"

    private Vehicle   vehicleBody;
    private RayCast3D obstacleRay;
    private boolean   resolved = false;

    private VehicleAIState currentState;
    private Lane            route;
    private IntersectionZone currentIntersection;   // junction we're inside, if any (I3b right-of-way)
    private boolean finished = false;               // reached a dead-end lane → zone despawns this car

    private double  routeProgress = 0.0;            // arc length along the current lane (monotonic)
    private boolean progressInit  = false;

    // ── Configuration (set by the spawner before the first tick) ───────────────

    /** Assign the lane this vehicle follows (resets progress + finished) — a {@link VehicleRoute}
     *  or a {@link com.openworld.world.PathLaneRoute}. */
    public void setRoute(Lane route) {
        this.route = route;
        finished = false;
        progressInit = false;
        routeProgress = 0.0;
        stallSeconds = 0.0;
        stallProgressMark = 0.0;
    }

    public Lane getRoute() { return route; }

    /**
     * At a lane end, continue onto a connected lane (the lane's explicit weighted {@code nextRoutes}
     * first, then the geometry {@link LaneGraph} fallback — straightness-biased, so legacy unwired
     * lanes still mostly flow through a junction instead of turning uniformly at random); with no
     * successor, apply the lane's {@code endBehavior} — U-turn back, or mark {@link #isFinished()}
     * (despawn). Returns true if a new lane was adopted (keep driving).
     */
    public boolean advanceToNextRoute() {
        if (finished || route == null) return false;

        Lane next = route.pickNextRoute();   // explicit override first
        if (next == null) {
            List<Lane> succ = LaneGraph.successorsOf(route);
            if (!succ.isEmpty()) {
                double[] out = route.endTangentXZ();
                float[] w = new float[succ.size()];
                for (int i = 0; i < succ.size(); i++) {
                    double[] in = succ.get(i).startTangentXZ();
                    // dot 1 (straight) → ~1.05, right angle → ~0.3, near-reverse → 0.05.
                    double dot = (out != null && in != null) ? out[0] * in[0] + out[1] * in[1] : 1.0;
                    double half = (dot + 1.0) / 2.0;
                    w[i] = (float) (half * half + 0.05);
                }
                next = succ.get(WeightedPick.pick(succ.size(), w, GD.randf()));
            }
        }
        if (next != null) { setRoute(next); return true; }

        if (VehicleRoute.END_UTURN.equals(route.getEndBehavior())) {
            Lane back = route.resolveRoute(route.getReturnRoute());
            if (back == null) back = LaneGraph.reverseOf(route);
            if (back != null) { setRoute(back); return true; }
        }
        finished = true;   // END_DESPAWN, or no reverse lane authored
        return false;
    }

    /** True once this car reached a dead-end lane with no continuation — the zone reclaims it. */
    public boolean isFinished() { return finished; }

    /**
     * Seconds this car has been essentially stationary. A car can fail to drive for reasons its own
     * FSM considers perfectly normal — its forward ray permanently tripped by another car it will
     * never get past, or a body resting somewhere its lane cannot pull it off — and every existing
     * reclaim reason misses that: it is alive, not finished, on the road, in range and routed. This
     * is the eye on it, in the same family as the fell-out check. The zone owns the policy (how long
     * is too long, and how near a player is too near to pop a car); the controller only measures.
     */
    public double stalledFor() { return stallSeconds; }

    /**
     * Metres of lane progress that count as getting somewhere. Deliberately measured on
     * {@code routeProgress} and NOT on speed: a car can be moving and still going nowhere — measured,
     * one oscillated back and forth across 6 m of plateau at 0.3-1.0 m/s indefinitely, dodging a
     * speed threshold entirely while advancing along its lane not at all. Progress is the thing the
     * car is actually for.
     */
    private static final float STALL_PROGRESS = 1.0f;

    private double stallSeconds = 0.0;
    private double stallProgressMark = 0.0;

    // ── FSM tick ───────────────────────────────────────────────────────────────

    @Override
    public UserCommand gatherInput(double delta) {
        UserCommand cmd = new UserCommand();
        resolveBody();
        if (vehicleBody == null) return cmd;

        if (currentState == null) transitionTo(CruiseState.INSTANCE);

        VehicleAIState next = currentState.update(vehicleBody, this, cmd, delta);
        if (next != currentState) transitionTo(next);

        // After the state tick, so routeProgress is this frame's.
        if (routeProgress - stallProgressMark > STALL_PROGRESS) {
            stallProgressMark = routeProgress;
            stallSeconds = 0.0;
        } else {
            stallSeconds += delta;
        }
        return cmd;
    }

    private void transitionTo(VehicleAIState next) {
        if (currentState != null) currentState.exit(vehicleBody, this);
        currentState = next;
        currentState.enter(vehicleBody, this);
    }

    // ── Arc-length lane following ────────────────────────────────────────────────

    /** Advance the monotonic progress by re-projecting the body locally onto the lane (no global snap). */
    public void updateProgress(Vector3 pos) {
        if (route == null) return;
        double window = steeringLookahead() + 8.0;
        routeProgress = route.lengthAtNearest(pos, progressInit ? routeProgress : -1.0, window);
        progressInit = true;
    }

    /** Pure-pursuit steer target: a point one look-ahead ahead of current progress along the lane. */
    public Vector3 lookaheadPoint() {
        return route == null ? null : route.pointAtLength(routeProgress + steeringLookahead());
    }

    /** A point farther still along the lane — used to anticipate an upcoming corner. */
    public Vector3 curvaturePoint() {
        return route == null ? null : route.pointAtLength(routeProgress + steeringLookahead() + curvatureProbe);
    }

    /** True when this is a one-way lane and progress has reached its end. */
    public boolean atRouteEnd() {
        return route != null && !route.isLoop() && routeProgress >= route.total() - END_THRESHOLD;
    }

    /** True while riding a generated turn connector that actually bends (turn L/R, not S). */
    /** The pace this car should hold on the lane it is ACTUALLY on, m/s.
     *
     *  <p>{@code .lanekit} v2 carries a per-lane {@code speed_limit}, so an arterial and a
     *  backstreet no longer share one global {@link #cruiseSpeed} — before this, the only speed
     *  signal the AI had was a turn letter, which says nothing about a straight road. A v1 lane
     *  (or any non-{@code PathLaneRoute}) leaves {@code speedLimit} at 0 and falls back to
     *  {@code cruiseSpeed}, so nothing already baked changes pace.
     *
     *  <p>Plain Java, not {@code @Register}: only {@link CruiseState} calls it, and
     *  exposing it to Godot would be one more registered symbol for no caller. */
    public float effectiveCruiseSpeed() {
        if (route instanceof PathLaneRoute p && p.speedLimit > 0f) {
            return p.speedLimit / 3.6f;                 // km/h -> m/s
        }
        return cruiseSpeed;
    }

    public boolean onTurnConnector() {
        String turn = route == null ? null : route.getTurn();
        return turn != null && !turn.isEmpty() && !"S".equals(turn);
    }

    /** True within {@link #junctionSlowdown} of the end of a chained lane (a junction is ahead —
     *  the successor may be a hard turn the curvature probe cannot see yet). */
    public boolean approachingJunction() {
        String turn = route == null ? null : route.getTurn();
        return route != null && !route.isLoop()
                && VehicleRoute.END_CHAIN.equals(route.getEndBehavior())
                && (turn == null || turn.isEmpty())
                && routeProgress >= route.total() - junctionSlowdown;
    }

    /** Horizontal (XZ) speed of the body in m/s — drives the speed-proportional look-ahead. */
    public float currentSpeed() {
        if (vehicleBody == null) return 0f;
        Vector3 v = vehicleBody.getLinearVelocity();
        return (float) Math.sqrt(v.getX() * v.getX() + v.getZ() * v.getZ());
    }

    /** The current speed-proportional steering look-ahead distance (m), clamped to the configured range. */
    public float steeringLookahead() {
        float look = lookaheadMin + currentSpeed() * lookaheadSpeedGain;
        return Math.max(lookaheadMin, Math.min(lookaheadMax, look));
    }

    // ── Sensing ─────────────────────────────────────────────────────────────────

    /** True when the forward obstacle ray is hitting something within its look-ahead length. */
    public boolean isPathBlocked() {
        return obstacleRay != null && obstacleRay.isColliding();
    }

    // ── Junction right-of-way (I3b) ──────────────────────────────────────────────

    public void enterIntersection(IntersectionZone z) { currentIntersection = z; }
    public void exitIntersection(IntersectionZone z) { if (currentIntersection == z) currentIntersection = null; }

    /** True when we are in a junction another vehicle currently holds — yield (brake) until clear. */
    public boolean shouldYield() {
        return currentIntersection != null && vehicleBody != null && currentIntersection.blocks(vehicleBody);
    }

    // ── Body / hardware resolution ───────────────────────────────────────────────

    private void resolveBody() {
        if (resolved) return;
        // getControllable() (parent-based), NOT getOwner(): runtime-attached controllers have no
        // scene owner (see Controller.isAuthority comment), so getOwner() would be null here.
        Controllable c = getControllable();
        if (c instanceof Vehicle v) {
            vehicleBody = v;
            Node ray = v.getNodeOrNull("ObstacleRay");
            if (ray instanceof RayCast3D r) obstacleRay = r;
            resolved = true;
        }
    }
}

package com.openworld.movement.character;

import godot.api.CharacterBody3D;
import godot.api.KinematicCollision3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.core.Transform3D;
import godot.core.Vector3;
import godot.global.GD;
import static java.lang.Math.atan2;
import com.openworld.character.Character;
import com.openworld.character.Player;
import com.openworld.control.Controller;
import com.openworld.control.PlayerController;
import com.openworld.control.UserCommand;
import com.openworld.net.NetworkController;

@Script(className = "MovementController")
public class MovementController extends Node {

  @Export
  public CharacterBody3D player = null;

  @Export
  public Node3D meshRoot = null;

  @Export
  public double rotationSpeed = 8.0;

  @Export
  public double fallGravity = 45.0;

  /**
   * Tallest ledge the character walks straight up, in metres. 0 disables stepping.
   *
   * `CharacterBody3D` has no step-up of its own: a vertical face is a wall whatever its height, and
   * `floor_block_on_wall` (on, correctly — it is what stops the body climbing steep slopes) kills
   * the upward part of the slide. So the 0.15 m kerb the road kit builds — a real kerb, the height
   * a real kerb is — stopped the player dead at the edge of every footway on the island. That is a
   * character-controller gap, not a world defect: lowering the kerb or ramping it would be
   * authoring around a missing feature, and stairs and low ruins want the same thing.
   *
   * 0.35 m is a touch over the 0.30 m an interior building step is usually capped at, which is the
   * range GTA/PUBG-class third-person characters step without an animation. It is deliberately
   * under the capsule radius' usable range and well under `jumpHeight` — anything taller should
   * cost a vaultment or a jump, not be free.
   */
  @Export
  public double stepHeight = 0.35;

  /**
   * How far forward the step probe reaches, in metres — at least far enough to put the body's
   * CENTRE over the ledge top.
   *
   * It cannot just be this frame's motion. Contact means the capsule's SURFACE is against the kerb
   * face, so the centre is still a radius (0.35 m) short of it; advancing only the 7 cm a walk
   * covers in a frame would drop the body back onto the kerb's top EDGE, whose contact normal is
   * not walkable, and the step would be rejected every frame while the character juddered.
   */
  @Export
  public double stepReach = 0.45;

  /**
   * The same body as {@link #player}, but the JVM instance Godot actually bound the script to.
   *
   * <p>A node reference exported through the scene (`node_paths=PackedStringArray("player")`) is
   * resolved when the scene is instantiated, and godot-kotlin-jvm 0.17 hands back a SECOND JVM
   * wrapper for that engine object — same `get_instance_id()`, different Java object, and none of
   * the state the body's own `_ready()` wrote. Engine calls (`isOnFloor`, `getGlobalPosition`,
   * `moveAndSlide`) go through the bridge and are correct on either wrapper, which is why this hid
   * for so long; Java fields are NOT. Reading `getAimTargetPosition()` off the exported reference
   * therefore got a Character whose `aimTarget` marker was null, so it returned the BODY's own
   * position, `aimYaw()` failed its own "too close to yaw toward" guard, and every aiming character
   * faced `camRotation` — the camera rig's LOCAL yaw, which is not a world direction at all.
   * Measured: the mesh 90 degrees off the aim point in every stance
   * (`world/hosts/AimDebugAuto.tscn`).
   *
   * <p>Resolving the SAME path at runtime returns the live instance, so this keeps the export as
   * the one owner of "which body" and only fixes which wrapper we hold.
   */
  private Character bodyCharacter;
  private boolean bodyCharacterResolved;

  /** The live {@link Character} for {@link #player}, or null if this body is not one. */
  private Character body() {
    if (bodyCharacterResolved && bodyCharacter != null && GD.isInstanceValid(bodyCharacter)) {
      return bodyCharacter;
    }
    bodyCharacterResolved = true;
    bodyCharacter = null;
    if (player == null) return null;
    Node live = getNodeOrNull(player.getPath());
    if (live instanceof Character c) bodyCharacter = c;
    else if (player instanceof Character c) bodyCharacter = c;   // not in a tree yet: better than nothing
    return bodyCharacter;
  }

  private double jumpGravity = fallGravity;
  private Vector3 direction = new Vector3();
  private Vector3 velocity = new Vector3();
  private double acceleration = 0.0;
  private double speed = 0.0;
  private double camRotation = 0.0;
  private boolean combat = false;
  private double combatSpeedFactor = 1.0;
  private double combatAccelerationFactor = 1.0;

  // ── Air strafe (CS/Source-style) ────────────────────────────────────────────
  /** Air acceleration constant — higher gives faster strafe speed-gain. */
  @Export
  public double airAccelerate = 80.0;

  /**
   * Per-tick wish-speed cap that makes air-strafing gain speed: only the velocity
   * component below this cap (projected onto the wish direction) can be added in air,
   * so turning the mouse while strafing curves and accelerates momentum instead of
   * snapping to {@code speed·dir}. Small by design (Quake's classic ~30 ups ≈ 0.8 m/s).
   */
  @Export
  public double airSpeedCap = 0.8;

  /** Downward speed (m/s) required before any fall damage is dealt. 0 disables fall damage. */
  @Export
  public float fallDamageThreshold = 10.0f;

  /** Damage per m/s above fallDamageThreshold on landing. */
  @Export
  public float fallDamageScale = 5.0f;

  /** Swim tunables (buoyancy / reduced gravity / vertical clamp) — used while {@link #swimming}. */
  @Export
  public SwimState swimState = null;

  /** True while the body is in a water volume (set by Character.setInWater → setSwimming). */
  private boolean swimming = false;
  /** World-space Y of the water surface the body floats toward while swimming. */
  private double waterSurfaceY = 0.0;
  /** Per-tick vertical swim intent: +1 ascend, -1 dive, 0 hold (let buoyancy settle to surface). */
  private double swimVertical = 0.0;
  /** Remaining ballistic-breach window (s) after a swim-jump; >0 suspends the buoyancy spring. */
  private double swimJumpTimer = 0.0;

  @Register
  public void setSwimming(boolean value, double surfaceY) {
    swimming = value;
    if (value) waterSurfaceY = surfaceY;
    else { swimVertical = 0.0; swimJumpTimer = 0.0; }
  }

  @Register
  public void setSwimVertical(double value) {
    swimVertical = value;
  }

  /**
   * Swim-jump "breach": a tap of jump near the water surface launches the swimmer up so forward
   * momentum can carry it onto a low ledge/harbor. Ignored unless currently swimming AND near the
   * surface — you can't launch off the seabed. Starts a {@link SwimState#swimJumpDuration} ballistic
   * window; if the lip isn't cleared, buoyancy settles the body back at the surface afterward.
   */
  @Register
  public void swimJump() {
    if (!swimming || swimState == null || player == null) return;
    double bodyY = player.getGlobalPosition().getY();
    double surfaceTarget = waterSurfaceY - swimState.getSubmersionDepth();
    if (bodyY < surfaceTarget - 0.5) return;   // too deep — must be near the surface to breach
    velocity.setY(swimState.getSwimJumpSpeed());
    swimJumpTimer = swimState.getSwimJumpDuration();
  }

  @Register
  @Override
  public void _physicsProcess(double delta) {
    if (player == null || meshRoot == null) return;
    // Non-authority bodies (NetworkController-driven remote peers/AI on a client) must
    // be driven *only* by replicated MSG_SNAPSHOT data — Character._physicsProcess
    // already early-returns for them (see its `!controller.isAuthority()` check), but
    // this controller runs as an independent sibling Node with its own _physicsProcess
    // and was never gated the same way. Left ungated, it called moveAndSlide() with a
    // locally-derived (always-zero `direction`/stale `combat`/`camRotation`) velocity
    // every physics tick — fighting applyReplicatedTransform's direct position writes
    // — and continuously overwrote meshRoot's rotation toward its own locally-computed
    // targetRotation, fighting applyReplicatedFacing's writes. That tug-of-war between
    // local physics and replicated state is what produced "wrong position/direction"
    // and the apparent multi-second catch-up lag (round 5 manual-test report).
    Character self = body();
    if (self != null) {
      Controller ctrl = self.getController();
      if (ctrl != null && !ctrl.isAuthority()) return;
    }

    boolean onFloor = player.isOnFloor();
    boolean wasOnFloor = onFloor;

    Vector3 normDir = direction.normalized();
    Vector3 curVel  = player.getVelocity();

    // ── Horizontal velocity ──────────────────────────────────────────────────
    // Swimming uses the grounded smoothing branch even in deep water (isOnFloor()==false)
    // so the swimmer still accelerates toward its (capped) target speed.
    double newX, newZ;
    if (onFloor || swimming) {
      // Grounded: accelerate toward the target speed·dir with the usual smoothing.
      double targetX = speed * normDir.getX();
      double targetZ = speed * normDir.getZ();
      double t = Math.min(1.0, acceleration * delta);
      newX = GD.lerp(curVel.getX(), targetX, t);
      newZ = GD.lerp(curVel.getZ(), targetZ, t);
    } else {
      // Airborne: Source-style air strafe. Preserve existing horizontal momentum and
      // add only a capped amount along the wish direction — turning the mouse while
      // holding a strafe key curves and gains speed (CS/Quake air control).
      newX = curVel.getX();
      newZ = curVel.getZ();
      if (normDir.lengthSquared() > 0.0001) {
        double curSpeedAlongWish = newX * normDir.getX() + newZ * normDir.getZ();
        double addSpeed = airSpeedCap - curSpeedAlongWish;
        if (addSpeed > 0) {
          double accelSpeed = Math.min(airAccelerate * airSpeedCap * delta, addSpeed);
          newX += accelSpeed * normDir.getX();
          newZ += accelSpeed * normDir.getZ();
        }
      }
    }

    // ── Vertical velocity (integrated on the internal Y, direct — no lerp) ─────
    if (swimming && swimState != null) {
      // Vertical swim model. With no input a buoyancy spring drives the body toward the settle line
      // (surface − settleDepth) and holds it there, so it settles AT the water line instead of
      // popping out the top (the surface-bob flicker). Manual ascend/dive use their own (faster)
      // speeds — held jump rises at swimAscendSpeed (clearly faster than the gentle passive cap),
      // easing in near the surface so it never breaches; held crouch/crawl dives at swimDiveSpeed.
      // The stance reverts (in Character.applyInput) only when the swimmer rests on shallow ground.
      boolean combat = self != null && self.isCombat();
      double settleDepth = combat ? swimState.getAimSubmersionDepth() : swimState.getSubmersionDepth();
      double targetY = waterSurfaceY - settleDepth;
      double bodyY = player.getGlobalPosition().getY();
      double passiveCap = swimState.getMaxVerticalSpeed();   // gentle auto-settle only
      double vy;
      if (swimJumpTimer > 0.0) {
        // Breaching: arc up under the (gentle) swim gravity instead of the surface spring, so the
        // hop can clear a low lip; buoyancy resumes (and re-settles us) once the window expires.
        swimJumpTimer -= delta;
        vy = velocity.getY() - swimState.getSwimGravity() * delta;
      } else if (swimVertical > 0.0) {
        // Held jump → ascend FAST, but ease into the settle line near the surface (don't breach).
        if (bodyY >= targetY) {
          vy = GD.clamp((targetY - bodyY) * swimState.getBuoyancy(), -passiveCap, passiveCap);
        } else {
          vy = swimState.getSwimAscendSpeed();
        }
      } else if (swimVertical < 0.0) {
        vy = -swimState.getSwimDiveSpeed();                  // held crouch/crawl → dive
      } else {
        double diff = targetY - bodyY;                       // no input → passive buoyancy settle
        vy = GD.clamp(diff * swimState.getBuoyancy(), -passiveCap, passiveCap);
        if (Math.abs(diff) < 0.05) vy = 0.0;   // deadzone — avoid micro-jitter once settled
      }
      velocity.setY(vy);
    } else {
      if (onFloor && velocity.getY() < 0) {
        velocity.setY(0);
      }
      if (!onFloor) {
        double g = velocity.getY() >= 0 ? jumpGravity : fallGravity;
        velocity.setY(velocity.getY() - g * delta);
      }
    }
    double newY = velocity.getY();

    player.setVelocity(new Vector3(newX, newY, newZ));
    float appliedVelocityY = (float) newY;
    player.moveAndSlide();
    if (!swimming && stepUpLedge(newX, newZ, delta)) velocity.setY(0.0);

    // Fall damage: compare velocity just before landing to the configured threshold.
    // Skipped while swimming — water cushions the entry/landing.
    if (!swimming && fallDamageThreshold > 0 && !wasOnFloor && player.isOnFloor()
            && appliedVelocityY < -fallDamageThreshold) {
      float fallSpeed = -appliedVelocityY;
      float damage = (fallSpeed - fallDamageThreshold) * fallDamageScale;
      if (self != null && self.healthNode != null) {
        String attackerName    = (self.characterInfo != null) ? self.characterInfo.displayName : "";
        String attackerFaction = (self.characterInfo != null) ? self.characterInfo.faction     : "";
        self.healthNode.takeDamage(null, damage, "Fall", null, attackerName, attackerFaction);
      }
    }

    // Handle Mesh Rotation
    // atan2(-dx, -dz) maps a world-space movement direction to the Y rotation needed
    // for a -Z-forward mesh (Godot convention).  The old formula atan2(dx, dz) was
    // correct for a +Z-forward mesh; negating both components shifts it by π, which
    // is the rotation needed to flip from +Z to -Z facing convention.
    // meshRoot is a CHILD of the body, so its local yaw is (world facing - the body's own yaw).
    // Read the body's yaw every frame rather than caching it in _ready(): a body that is rotated
    // after add_child() -- which is what every code spawn path and AimDebugHost do -- would
    // otherwise keep compensating for a rotation it no longer has, and the mesh renders off by
    // exactly that difference (measured: 88 degrees, tools/godot/probe_camera_frame.gd case B).
    double bodyYaw = player.getRotation().getY();
    double targetRotation;
    if (combat) {
      // Face where the shot actually goes, not where the camera looks. The TPS camera sits off the
      // shoulder, so its yaw and the body->aim-point yaw differ by several degrees at close range —
      // enough for the visible body and gun to be square to a wall while the shot slipped past it.
      // Aiming the body at the same point the spine IK and the bullet converge on
      // (Character.getAimTargetPosition, the AimRay's world hit point) keeps all three honest, which
      // is what makes FirearmItem's muzzle trace read as fair rather than arbitrary.
      targetRotation = aimYaw() - bodyYaw;
    } else if (direction.lengthSquared() > 0.001) {
      // Face movement direction (only when actually moving)
      targetRotation = atan2(-direction.getX(), -direction.getZ()) - bodyYaw;
    } else {
      targetRotation = meshRoot.getRotation().getY(); // hold current facing
    }

    Vector3 currentRot = meshRoot.getRotation();
    double newMeshY = GD.lerpAngle(currentRot.getY(), targetRotation, rotationSpeed * delta);

    // Update only the Y axis
    meshRoot.setRotation(new Vector3(currentRot.getX(), newMeshY, currentRot.getZ()));

  }


  /**
   * Walk up a ledge no taller than {@link #stepHeight} — a kerb, a stair tread, a low ruin.
   * Returns true when the body was actually lifted onto one.
   *
   * UP, FORWARD, DOWN, and revert if the landing is not walkable. Run AFTER `moveAndSlide`, and
   * only when it reported both a floor and a wall: that pair is the whole trigger, so the probe
   * costs three physics queries at a kerb edge and nothing at all the rest of the time. A failed
   * attempt restores the exact transform it started from, so the worst case is the behaviour that
   * was there before.
   *
   * The DOWN leg is what makes it safe, and it is why this is not just "raise the body when
   * blocked": the body settles onto whatever is really there, and a landing whose normal is not
   * walkable — the sloped face of a wall, the rounded top edge of something too narrow to stand on
   * — is refused. Without it the character would climb anything it could touch.
   *
   * Not run while swimming: a wall met in water is a wall.
   */
  private boolean stepUpLedge(double vx, double vz, double delta) {
    if (stepHeight <= 0.0 || player == null) return false;
    if (!player.isOnFloor() || !player.isOnWall()) return false;

    double len = Math.sqrt(vx * vx + vz * vz);
    if (len < 1e-3) return false;                       // standing still: nothing to step onto
    double reach = Math.max(len * delta, stepReach);
    Vector3 ahead = new Vector3(vx / len * reach, 0.0, vz / len * reach);
    Vector3 lift = new Vector3(0.0, stepHeight, 0.0);

    Transform3D start = player.getGlobalTransform();
    // Blocked down here but clear one step up — that is a LEDGE. A wall is blocked at both heights,
    // and testing it first is what keeps the character from climbing buildings.
    if (!player.testMove(start, ahead)) return false;
    if (player.testMove(start.translated(lift), ahead)) return false;

    player.setGlobalTransform(start.translated(lift));
    player.moveAndCollide(ahead);
    KinematicCollision3D landed = player.moveAndCollide(new Vector3(0.0, -(stepHeight + 0.02), 0.0));
    if (landed == null || landed.getNormal().getY() < floorNormalMin()) {
      player.setGlobalTransform(start);
      return false;
    }
    return true;
  }

  /** The cosine of the body's own `floor_max_angle` — one owner, so a steeper body steps steeper. */
  private double floorNormalMin() {
    return Math.cos(player.getFloorMaxAngle());
  }

  /** Horizontal distance (squared, m^2) below which an aim point is too close/underfoot to yaw toward. */
  private static final double AIM_FACING_MIN_DIST_SQ = 0.25;

  /**
   * World-space yaw from the body toward its current aim point. Falls back to the camera yaw when
   * there is no usable point — a non-Character body, or a point directly overhead/underfoot. Since
   * AIM_PLAN.md W3 that fallback is a world direction too, so it degrades instead of inverting.
   */
  private double aimYaw() {
    Character c = body();
    if (c == null) return camRotation;
    Vector3 aim = c.getAimTargetPosition();
    Vector3 pos = player.getGlobalPosition();
    double dx = aim.getX() - pos.getX();
    double dz = aim.getZ() - pos.getZ();
    // The fallback DEGRADES now, which it did not before AIM_PLAN.md W3: `camRotation` is the rig's
    // yaw, and the rig's world basis is stated outright every frame, so it is a genuine world
    // direction and this reads "face where the camera looks". It used to be a LOCAL yaw under a
    // frame frozen by `setAsTopLevel`, i.e. off by whatever the body's spawn rotation was —
    // measured at 90 degrees for a player, and accidentally right for an AI only because
    // AICameraController drives that same yaw toward the AI's aim target. Getting here is still
    // meant to be rare (a point directly overhead/underfoot); reaching it used to mean a defect,
    // because the aim point had resolved to the body's own position off a stale JVM wrapper — see
    // `body()`.
    if (dx * dx + dz * dz < AIM_FACING_MIN_DIST_SQ) return camRotation;
    // Same -Z-forward mapping as the movement branch.
    return atan2(-dx, -dz);
  }

  @Register
  public void jump(JumpState jumpState) {
    velocity.setY(2.0 * jumpState.getJumpHeight() / jumpState.getApexDuration());
    jumpGravity = velocity.getY() / jumpState.getApexDuration();
  }

  @Register
  public void onSetMovementState(MovementState movementState) {
    speed = movementState.getMovementSpeed() * combatSpeedFactor;
    acceleration = movementState.getAcceleration() * combatAccelerationFactor;
  }

  @Register
  public void onSetCombatState(CombatState combatState) {
    combat = combatState.isCombat();
    combatSpeedFactor = combatState.getSpeedFactor();
    combatAccelerationFactor = combatState.getAccelerationFactor();
  }

  @Register
  public void onSetMovementDirection(Vector3 movementDirection) {
    // Direction is always world-space: AI provides it directly, PlayerController
    // pre-rotates camera-relative WASD by the camera yaw before stamping the UserCommand.
    direction = movementDirection;
  }

  @Register
  public void onSetCamRotation(double newCamRotation) {
    camRotation = newCamRotation;
  }
}
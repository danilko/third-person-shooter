package com.openworld.movement.character;

import godot.api.CollisionShape3D;
import godot.api.Node;
import godot.api.RayCast3D;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.core.VariantArray;
import godot.core.Vector3;
import godot.global.GD;

@Script
public class Stance extends Node {

  // --- Movement States ---
  @Export
  public MovementState idleState;

  @Export
  public MovementState walkState;

  @Export
  public MovementState sprintState;

  // --- Camera Data ---
  @Export
  public double cameraHeight = 1.3;

  // --- Animation ---

  /**
   * Optional override for the AnimationTree stance key. When empty (default) the stance's
   * Godot node name drives the {@code StanceTransition} request and {@code …MovementBlend}
   * path. Set this to reuse another stance's animation states — e.g. the SWIM stance sets
   * {@code "Crawl"} as a placeholder until a dedicated swim AnimationTree state exists (I1).
   */
  @Export
  public String animationStanceKey = "";

  // --- Aim ---

  /**
   * Whether this stance aims its chest at the aim target (the spine {@code LookAtModifier3D}).
   *
   * <p>This was {@code spineAimMaxAngle}, a float the code only ever read as a boolean. W1a made it
   * a real angle, pushed into the modifier's {@code use_angle_limitation} /
   * {@code primary_limit_angle} / {@code secondary_limit_angle} — and then measured it, which is
   * why it is a boolean again on purpose. The limit is taken from the bone's REST pose, so what it
   * has to cover is the camera's range PLUS however far the stance's clip already holds the chest
   * from the aim. Upright FOLLOW by limit: 60 -> 0.47, 90 -> 0.70, 135 -> 0.92; crawl, whose prone
   * clip starts furthest away, needs 300 to reach 0.89 against an engine default of 360 (i.e.
   * uncapped). <b>A cap loose enough not to hurt is indistinguishable from no cap, and it bites
   * hardest exactly where the aim needs the most help.</b>
   *
   * <p>Nothing is lost by dropping it: the modifier runs only in combat, and in combat
   * {@code MovementController.aimYaw()} turns the whole mesh to the aim point (AimDebugAuto, 12
   * yaw cases at 0.0 deg), so the grotesque twist a cap would prevent cannot arise. If a stance
   * ever does need limiting, it wants PER-AXIS limits — one symmetric number cannot serve a yaw
   * cap and an elevation range at once.
   */
  @Export
  public boolean spineAimEnabled = true;

  /**
   * Whether this stance aims with its SHOULDERS and HEAD instead of its chest.
   *
   * <p>The complement of {@link #spineAimEnabled}, for a stance whose body must stay where the clip
   * put it — prone, a vehicle seat, swimming. The spine look-at hits the target by rotating the
   * torso, which in those stances is wrong in KIND (measured: aiming up in crawl put the chest 55
   * deg above horizontal on a body lying on the ground), and switching it off is why they did not
   * aim at all. {@link com.openworld.character.ShoulderAimModifier} applies the same aim rotation
   * one bone lower — to {@code clavicle_l}, {@code clavicle_r} and {@code neck_01}, which are all
   * direct children of {@code spine_03} — so the arms, the weapon and the head swing to the aim
   * while the spine, pelvis and legs do not move at all.
   *
   * <p>Set exactly one of the two. Both on would apply the rotation twice.
   */
  @Export
  public boolean shoulderAimEnabled = false;

  /**
   * How far off straight-ahead the aim may turn the body's own bones, degrees, symmetric.
   *
   * <p>A YAW cap, and only a yaw cap: elevation is bounded by the camera itself (-55..+75) and any
   * limit tight enough to be a meaningful chest cap also clips ordinary aiming — measured, a
   * symmetric 60 halved the elevation the chest picks up. What this stops is the spine or neck
   * twisting to an angle no person reaches, which the yaw axis genuinely can, because the body
   * only turns toward the aim at a finite rate and cannot turn at all in a vehicle seat.
   *
   * <p>Per stance because the body's own freedom differs: upright can turn its shoulders a long
   * way, a crouch slightly less, and a prone or seated body very little.
   *
   * <p><b>This is the RIG's reach, not permission to aim.</b> The two are different facts and
   * seating is where they came apart. Whether an occupant MAY aim somewhere belongs to the carrier
   * ({@code VehicleConfig.drivebyAimMin/Max} — it depends which side of the car the seat is and
   * where the doorframe is, which a stance cannot know), and it is applied to the aim POINT
   * ({@code Character.applySeatedAimTarget}) so the bones, the bullet and the reticle all read one
   * clamped direction. This number only says how far the BODY can turn to follow that direction.
   * Keeping a permission-clamp here as well is what produced the drive-by bug: the bones stopped at
   * 45 degrees while the shot carried on to the camera's unclamped point, up to 135 degrees away —
   * <b>two clamps on one fact is worse than either alone</b>, because the tighter one is invisible
   * and the looser one is a lie.
   *
   * <p>Why a seated stance needs the reach stated at all, while the on-foot ones barely do: on foot
   * {@code MovementController.aimYaw()} turns the whole mesh to the aim point, so the residual left
   * for the bones is small and a limit rarely binds. A body in a seat cannot yaw, so every degree
   * comes out of the spine and collarbones and the limit binds constantly. Past it the answer is a
   * different POSTURE rather than a further twist — the occupant turns round in the seat
   * ({@code VehicleConfig.rearAimEnterDeg}), which is why this can stay at a value the arms
   * actually sell and the car can still cover its own back.
   */
  @Export
  public float aimYawLimit = 80.0f;

  /**
   * The TORSO's share of {@link #aimYawLimit}, degrees — how much of the aim the spine may carry
   * before the collarbones and neck take the remainder. Only read when a stance enables BOTH aim
   * modifiers; ignored otherwise, so every single-modifier stance is unaffected.
   *
   * <p>The two modifiers compose rather than double up: {@code ShoulderAimModifier} measures its
   * delta from {@code spine_03}'s CURRENT forward and runs after the spine modifier in tree order,
   * so whatever the torso already covered is not there to cover again. That is what makes a staged
   * aim possible, and it matters most where the body cannot turn — a seated occupant reaching 100
   * degrees on collarbones alone is the dislocated-shoulder pose measured in the prone case
   * (CLAUDE.md, the clavicle-weight sweep); spending 45 of it as torso twist is what a person does.
   *
   * <p>Zero (the default) means the spine carries none of it, i.e. exactly the pre-staging
   * behaviour.
   */
  @Export
  public float spineAimYawLimit = 0.0f;

  /**
   * How far the VIEW may pitch UP from horizontal in this stance, degrees. Positive is up — the
   * {@link com.openworld.camera.ControlRotation} convention (AIM_PLAN.md W3).
   *
   * <p><b>This is the elevation half of the aim limit, and it lives on the CAMERA on purpose.</b>
   * The bones do not choose where they point: the aim target is the point the view ray lands on,
   * so whatever the camera may pitch to is what the spine (upright/crouch) or the shoulders and
   * neck (crawl/swim/drive) must reach. Capping the modifiers instead would need a second owner of
   * the same fact and would immediately disagree with the crosshair — and the one measurement we
   * have of doing it that way is against it: a symmetric {@code LookAtModifier3D} limit is taken
   * from the bone's REST pose, so a 60 deg cap took upright's chest FOLLOW from 0.94 to 0.47 while
   * still permitting the extreme pose it was meant to stop (see {@link #spineAimEnabled}).
   *
   * <p>Asymmetric because a body is: a prone character can drop its gaze along the ground it is
   * lying on, but cannot lift its chin much past 45 deg without pushing itself up on its elbows,
   * which the clip does not do.
   */
  @Export
  public double viewPitchMax = 60.0;

  /** How far the VIEW may pitch DOWN in this stance, as a NEGATIVE number. See {@link #viewPitchMax}. */
  @Export
  public double viewPitchMin = -75.0;

  /**
   * How far above the horizon the aim may take the BONES, degrees — the elevation half of the aim
   * limit, and the companion of {@link #aimYawLimit}.
   *
   * <p><b>Why this is not {@link #viewPitchMax}.</b> They were one number for a while and the
   * measurement is what separated them: swept on this rig the chest tracks the view <b>1:1</b>
   * (view +80 gives chest +80.0), so a view limit generous enough to let a player look at a
   * rooftop is a body arched 80 deg backwards. A human trunk manages about 45. The two cannot be
   * the same number, so the camera keeps the range a player needs and the bones stop at a pose a
   * body can hold — they simply HOLD at the limit and resume tracking when the target comes back
   * down. Nothing is lost from shooting: the bullet is traced from the muzzle to the point the
   * CAMERA ray found ({@code FirearmItem}'s two-stage resolution), so it still converges on the
   * crosshair while the gun is visibly held a little lower. Keep the gap small — every degree
   * between them is visible disagreement between the gun and the reticle.
   *
   * <p>Asymmetric, and the asymmetry is measured: downward the chest saturates at roughly
   * view + 8 deg (view -80 gives chest -72.4), which is just bending over to look at the ground,
   * so the DOWN limit can be generous and only the UP one really bites.
   */
  @Export
  public double aimPitchMax = 45.0;

  /** How far BELOW the horizon the aim may take the bones, as a NEGATIVE number. See {@link #aimPitchMax}. */
  @Export
  public double aimPitchMin = -65.0;

  // --- Collision ---
  @Export
  public CollisionShape3D collider;

  @Export
  public RayCast3D colRaycast;

  /**
   * Stances that require more vertical clearance than this one (i.e. taller stances).
   *
   * When {@code colRaycast} detects a ceiling obstruction, all stances listed here
   * are also considered blocked — preventing the character from standing up into a
   * surface even if they are not the direct target of the transition.
   *
   * Example: Crawl.higherStances = [Crouch, Upright].  When crawling under a low
   * ceiling, neither Crouch nor Upright can be entered until the ceiling clears.
   *
   * Inspector: assign the sibling Stance nodes that are blocked by this stance's ceiling raycast.
   *
   * Typed to {@code Node} (not {@code Stance}) deliberately: a typed {@code Array[Stance]}
   * export hint holds a strong reference to the {@code Stance.gdj} GdjScript that is never
   * released, leaving it "in use at exit" (the long-standing shutdown leak). {@code Node} is a
   * built-in class (no script resource), so the hint references nothing to leak — and Stance
   * IS a Node, so assigning sibling stances still works. {@link #isBlocked} narrows back.
   */
  @Export
  public VariantArray<Node> higherStances = new VariantArray<>(Node.class);

  /**
   * Returns true if this stance's space is obstructed (ceiling too low).
   * Also returns true if any of the {@link #higherStances} are themselves blocked,
   * preventing an upward transition that would collide.
   */
  @Register
  public boolean isBlocked() {
    if (colRaycast != null && colRaycast.isColliding()) return true;
    for (Node taller : higherStances) {
      if (taller instanceof Stance s && s.isBlocked()) return true;
    }
    return false;
  }

  public MovementState getIdleState() {
    return idleState;
  }

  public void setIdleState(MovementState idleState) {
    this.idleState = idleState;
  }

  public MovementState getWalkState() {
    return walkState;
  }

  public void setWalkState(MovementState walkState) {
    this.walkState = walkState;
  }


  public MovementState getSprintState() {
    return sprintState;
  }

  public void setSprintState(MovementState sprintState) {
    this.sprintState = sprintState;
  }

  public String getAnimationStanceKey() { return animationStanceKey; }
  public void setAnimationStanceKey(String v) { this.animationStanceKey = v; }

  public double getCameraHeight() { return cameraHeight; }
  public void setCameraHeight(double v) { this.cameraHeight = v; }

  public boolean isSpineAimEnabled() { return spineAimEnabled; }
  public void setSpineAimEnabled(boolean v) { this.spineAimEnabled = v; }

  public boolean isShoulderAimEnabled() { return shoulderAimEnabled; }
  public void setShoulderAimEnabled(boolean v) { this.shoulderAimEnabled = v; }

  public float getAimYawLimit() { return aimYawLimit; }
  public void setAimYawLimit(float v) { this.aimYawLimit = v; }

  public float getSpineAimYawLimit() { return spineAimYawLimit; }
  public void setSpineAimYawLimit(float v) { this.spineAimYawLimit = v; }

  public double getViewPitchMax() { return viewPitchMax; }
  public void setViewPitchMax(double v) { this.viewPitchMax = v; }

  public double getViewPitchMin() { return viewPitchMin; }
  public void setViewPitchMin(double v) { this.viewPitchMin = v; }

  public double getAimPitchMax() { return aimPitchMax; }
  public void setAimPitchMax(double v) { this.aimPitchMax = v; }

  public double getAimPitchMin() { return aimPitchMin; }
  public void setAimPitchMin(double v) { this.aimPitchMin = v; }

  public CollisionShape3D getCollider() {
    return collider;
  }

  public void setCollider(CollisionShape3D collider) {
    this.collider = collider;
  }

  public RayCast3D getColRaycast() {
    return colRaycast;
  }

  public void setColRaycast(RayCast3D colRaycast) {
    this.colRaycast = colRaycast;
  }

  public VariantArray<Node> getHigherStances() {
    return higherStances;
  }

  public void setHigherStances(VariantArray<Node> higherStances) {
    this.higherStances = higherStances;
  }

  public MovementState getMovementState(MovementType type) {
    switch (type) {
      case IDLE:   return idleState;
      case WALK:   return walkState;
      case SPRINT: return sprintState;
      default:
        return idleState;
    }
  }
}
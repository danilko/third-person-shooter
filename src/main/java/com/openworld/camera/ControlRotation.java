package com.openworld.camera;

import com.openworld.character.Character;

/**
 * Canonical view direction for a character — equivalent to Unreal's AController::ControlRotation.
 * Owned by Character; all camera modes (TPS, FPS) read from this, none own it.
 *
 * <p><b>It is a WORLD rotation, in degrees, and positive pitch means LOOK UP.</b> Both halves of
 * that sentence were untrue before AIM_PLAN.md W3 and both cost real bugs. The rig is
 * {@code setAsTopLevel(true)}, which preserves the global transform at the instant it is called, so
 * {@code yaw} used to be a LOCAL yaw under a frame frozen at the body's {@code _ready()}-time
 * rotation — a caller wanting a world direction had to add the body's yaw back and hope it had not
 * moved ({@code PlayerController} did, and it broke the moment a body was rotated after
 * {@code add_child()}), and {@code MovementController.aimYaw()}'s fallback returned it raw and put
 * the player 90° off. And the rig carried two cancelling 180° Y flips, of which the second
 * ({@code Pivot}'s) turned {@code Rot_x(pitch)} into {@code Rot_x(-pitch)}, so positive pitch meant
 * look DOWN and {@code applyRecoil} had to subtract to kick upward. The flips are gone from
 * {@code Character.tscn} and {@code TPSCameraController} states the rig's world basis every frame,
 * so the camera's world basis is now exactly {@code Rot_y(yaw) · Rot_x(pitch)}.
 *
 * <p>pitchMin / pitchMax are populated by CameraController._ready() from its @Export values so
 * FPSCameraController can clamp pitch without referencing the TPS camera directly. With pitch
 * positive-up they read as "75° down, 55° up" — the same view range as before, said the right way
 * round.
 */
public class ControlRotation {
    public double yaw         = 0.0;
    public double pitch       = 0.0;
    public double recoilPitch = 0.0;
    public double recoilYaw   = 0.0;
    public double pitchMin    = -75.0;
    public double pitchMax    =  55.0;
}

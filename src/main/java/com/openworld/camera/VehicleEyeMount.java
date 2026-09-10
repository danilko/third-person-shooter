package com.openworld.camera;

/**
 * WHICH first-person eye point a carrier renders from — the complement of {@link CameraMode},
 * which says whether the view is first-person at all.
 *
 * <p>The split is deliberate, and it is what lets one view preference follow the player from foot
 * into the seat and back. {@code CameraMode} is the SHARED fact ("am I in first person"), mirrored
 * between {@code Character.isFpsMode} and {@code VehicleCameraController.cameraMode} across the
 * enter/exit seam. This is the CARRIER-LOCAL fact, remembered per vehicle and meaningless on foot,
 * so putting it in {@code CameraMode} would have made a three-state enum of which one state a
 * character can never be in.
 *
 * <ul>
 *   <li><b>COCKPIT</b> — the driver's own eye line ({@code Seats/Seat0/CockpitCameraMount}, a
 *       child of the seat so it follows whoever moves the seat). This is the one view of the three
 *       that is inside the character's skull, so it is also the one that hides the head — see
 *       {@code Character.refreshHeadVisibility}.</li>
 *   <li><b>BONNET</b> — the original {@code FPSCameraMount}, over the nose at (0, 0.34, -0.80).
 *       Not behind anyone's eyes, so the head stays on. Kept because it is a genuinely different
 *       and useful driving view (the car's own front camera), not a worse cockpit.</li>
 * </ul>
 */
public enum VehicleEyeMount {
    COCKPIT,
    BONNET
}

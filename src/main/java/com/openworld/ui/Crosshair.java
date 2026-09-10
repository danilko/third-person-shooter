package com.openworld.ui;

import com.openworld.character.Character;
import com.openworld.weapon.WeaponController;
import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.*;
import godot.core.Color;
import godot.core.VariantArray;
import godot.core.Vector2;
import godot.core.Vector3;
import godot.global.GD;

/**
 * Standalone HUD reticle.
 *
 * Spread is self-managed: each tick the target arm-offset is read directly from
 * the linked {@link WeaponController} (no external push required).  Visibility is
 * controlled by {@link #showCrosshair} — set it to false to collapse the arms to
 * centre without touching scene visibility, so the node can stay active for other
 * purposes (e.g. a vehicle weapon that overrides it later).
 *
 * Previous callers that pushed spread via {@code setPositionX()} still work; the
 * self-managed read simply overwrites that value the next frame if
 * {@code weaponController} is set.
 */
@Script(className = "Crosshair")
public class Crosshair extends Control {

    /**
     * Lerp speed when arms move outward (bloom spike after a shot).
     * Default 60 ≈ instant snap at 60 fps.
     */
    @Export public double crosshairExpandSpeed  = 60.0;

    // Fast enough to track bloom recovery (~0.5s) so the player gets clear visual
    // feedback when they have first-shot accuracy again — same as CS crosshair behaviour.
    @Export public double crosshairContractSpeed = 8.0;

    /**
     * Optional weapon controller for self-managed spread.
     * When set, the crosshair reads {@code getCurrentSpreadDeg()} every frame
     * instead of requiring an external {@code setPositionX()} call.
     */
    @Export public WeaponController weaponController;

    /**
     * Master visibility flag.  When {@code false} the arms lerp back to centre
     * each frame.  Toggle this from combat-state handlers or vehicle enter/exit
     * instead of hiding the node so the lerp animation still plays.
     */
    @Export public boolean showCrosshair = true;

    /**
     * Arm offset (px) at the tightest accuracy (crosshair fraction 0). The minimum gap so the reticle
     * never fully closes.
     */
    @Export public float minSpreadPixels = 3f;

    /**
     * Arm offset (px) at the widest accuracy (crosshair fraction 1). Caps how far the arms open so the
     * reticle never runs off-screen even for a wide-cone weapon (e.g. a shotgun) — the off-screen bug.
     * The fraction is weapon-normalized (WeaponController.getCrosshairSpreadFraction), so this same
     * range serves every weapon with no per-weapon crosshair tuning.
     */
    @Export public float maxSpreadPixels = 90f;

    /**
     * The character whose seated aim the reticle should follow, when there is one.
     *
     * <p>On foot this stays unused and the reticle sits dead centre, because on foot the aim point
     * IS the camera ray's hit — centre of screen and the world point are the same pixel. In a seat
     * they come apart: the camera is free (an arcade chase camera, deliberately) while the weapon
     * is held inside the seat's firing sector, so a centre-screen dot would be a lie about where
     * the bullet goes. Anchoring the reticle to the world point is how GTA says the same thing —
     * the reticle sticks at the edge of the sector and drags along it as you keep pushing.
     */
    public Character aimCharacter;

    /**
     * How far off centre the anchored reticle may travel, as a fraction of the smaller viewport
     * dimension. Past this it is pinned (still pointing the right way) rather than allowed off
     * screen, which is the ordinary case in third person when the camera swings behind the car.
     */
    @Export public float maxAnchorOffsetFraction = 0.42f;

    /** Alpha applied while the seat's firing sector is holding the aim back. */
    @Export public float clampedAlpha = 0.55f;

    /** Anchor follow speed (per second). Fast enough to read as attached, slow enough not to jitter. */
    @Export public double anchorFollowSpeed = 25.0;

    private VariantArray<Node> lines;
    private Node2D centerNode;
    private Node2D reticleNode;
    private Vector2 anchorOffset = Vector2.Companion.getZERO();
    private float positionX = 0f;

    /** External override — still usable but overwritten next frame if weaponController is set. */
    public void setPositionX(float positionX) {
        this.positionX = positionX;
    }

    /** Getter half of the exported {@code showCrosshair} property (0.17 binds a property
     *  that has any accessor through its accessors, so the pair must be complete). */
    public boolean isShowCrosshair() {
        return showCrosshair;
    }

    public void setShowCrosshair(boolean show) {
        this.showCrosshair = show;
    }

    @Register
    @Override
    public void _ready() {
        lines = getNode("Reticle/Lines").getChildren();
        if (getNodeOrNull("Center")  instanceof Node2D c) centerNode  = c;
        if (getNodeOrNull("Reticle") instanceof Node2D r) reticleNode = r;
    }

    /**
     * Where the reticle belongs this frame, in this control's own space — (0,0) is the screen
     * centre, which is where it stays unless a seated occupant's aim has been clamped away from it.
     *
     * <p>A point BEHIND the camera has no projection, and {@code unprojectPosition} on one returns
     * a mirrored position that swings the reticle the wrong way — a real case here, since the chase
     * camera can sit behind the car looking at the boot while the driver aims out of the window. So
     * it is handled explicitly: keep the direction, pin the magnitude.
     */
    private Vector2 resolveAnchorOffset() {
        if (aimCharacter == null || !aimCharacter.isSeatedAimAnchored()) return Vector2.Companion.getZERO();
        Camera3D cam = getViewport() != null ? getViewport().getCamera3d() : null;
        if (cam == null) return Vector2.Companion.getZERO();

        Vector3 world = aimCharacter.getAimTargetPosition();
        Vector2 screen = cam.unprojectPosition(world);
        Vector2 offset = screen.minus(getGlobalPosition());
        if (cam.isPositionBehind(world)) offset = offset.times(-1f);

        Vector2 size = getViewportRect().getSize();
        float limit = (float) (maxAnchorOffsetFraction * Math.min(size.getX(), size.getY()));
        float len = (float) offset.length();
        if (len > limit && len > 1e-3f) offset = offset.times(limit / len);
        return offset;
    }

    @Register
    public void onWeaponFire(float speedScale) {
        // Bloom expansion is already captured by getCurrentSpreadDeg(), so no
        // separate per-shot animation is needed here.
    }

    @Register
    @Override
    public void _process(double delta) {
        // Self-managed spread: map the weapon-normalized accuracy fraction (0..1) to a fixed pixel
        // range. Normalizing per weapon keeps one reticle scale for every gun (no per-weapon tuning),
        // caps the maximum opening (no off-screen), and still reflects movement/bloom/stance within
        // the range — so the reticle reliably shows current accuracy regardless of weapon.
        if (weaponController != null) {
            float frac = weaponController.getCrosshairSpreadFraction();
            positionX = minSpreadPixels + frac * (maxSpreadPixels - minSpreadPixels);
        }

        // Anchor the whole reticle (dot + arms) on the point the weapon can actually reach, so what
        // the player is told is what the bullet does. Unclamped this is exactly (0,0) — an ordinary
        // shot from a seat looks the way it always did.
        Vector2 wanted = resolveAnchorOffset();
        double aw = Math.min(1.0, anchorFollowSpeed * delta);
        anchorOffset = anchorOffset.lerp(wanted, (float) aw);
        if (centerNode  != null) centerNode.setPosition(anchorOffset);
        if (reticleNode != null) reticleNode.setPosition(anchorOffset);
        boolean clamped = aimCharacter != null && aimCharacter.isSeatedAimAnchored()
                && aimCharacter.isSeatAimClamped();
        Color tint = getModulate();
        tint.setA(clamped ? clampedAlpha : 1.0f);
        setModulate(tint);

        // Target: 0 when hidden (arms collapse to centre), positionX when shown.
        float target = showCrosshair ? positionX : 0f;

        for (Node line : lines) {
            Node2D currentLine = (Node2D) line.getNode("LineBase");
            Vector2 currentPos = currentLine.getPosition();
            double speed  = currentPos.getX() < target ? crosshairExpandSpeed : crosshairContractSpeed;
            double weight = Math.min(1.0, speed * delta);
            float  newX   = (float) GD.lerp(currentPos.getX(), target, weight);
            currentLine.setPosition(new Vector2(newX, currentPos.getY()));
        }
    }
}

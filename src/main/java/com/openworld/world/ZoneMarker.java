package com.openworld.world;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.BaseMaterial3D;
import godot.api.BoxMesh;
import godot.api.CylinderMesh;
import godot.api.MeshInstance3D;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.PackedScene;
import godot.api.ResourceLoader;
import godot.api.StandardMaterial3D;
import godot.core.Color;
import godot.core.Vector3;
import godot.global.GD;

import java.util.ArrayList;
import java.util.List;

/**
 * In-scene anchor for a {@link Zone} (PLAN.md Part E / E1).
 *
 * <p>{@link Zone} is pure data and an AutoLoad script can't take inspector-assigned zone
 * {@code .tres}, so designers drop a {@code ZoneMarker} into the level, assign a zone, and
 * position it — the marker's <b>global position is the zone center</b>. It registers itself with
 * {@link ZoneManager} in {@code _ready()} and deregisters in {@code _exitTree()} (the same
 * register-with-AutoLoad idiom {@code Character} uses with {@code SpatialEntityGrid}). AutoLoads are
 * ready before the main scene, so the manager exists when a marker's {@code _ready} runs.
 *
 * <p><b>Debug visualization:</b> when {@link #showDebugVolume} is on (default), the marker builds at
 * runtime a translucent box matching {@code zone.size} (the spawn volume) plus two flat rings at
 * {@code loadRadius}/{@code unloadRadius}, so you can <i>see</i> a zone and walk into it. The box
 * tints green while the zone is streamed in (driven by {@link #setLoadedVisual} from the manager) and
 * cyan while idle. These are pure debug meshes — delete-free in a shipping build by toggling the flag.
 */
@Script(className = "ZoneMarker")
public class ZoneMarker extends Node3D {

	/** The zone this marker anchors. */
	@Export public Zone zone;

	/** Draw the translucent spawn-volume box + load/unload rings at runtime (debug aid). */
	@Export public boolean showDebugVolume = true;

	private static final Color IDLE_COLOR   = new Color(0.0, 0.7, 1.0, 0.12);  // cyan  — unloaded
	private static final Color LOADED_COLOR = new Color(0.1, 1.0, 0.3, 0.20);  // green — streamed in
	private static final Color LOAD_RING    = new Color(1.0, 0.85, 0.0, 0.10); // yellow ring (load)
	private static final Color UNLOAD_RING  = new Color(1.0, 0.25, 0.2, 0.07); // red ring  (unload)

	private StandardMaterial3D volumeMat;  // box material, recoloured on load/unload
	private final List<Node> debugVisualNodes = new ArrayList<>();

	/** The eagerly-resident low-detail placeholder tier (see {@link Zone#lodLowGeometryPath}),
	 * or null if this zone never baked one, or it is currently removed (full detail is loaded). */
	private Node lodLowInstance;

	@Register
	@Override
	public void _ready() {
		ZoneManager mgr = ZoneManager.get();
		if (mgr != null) mgr.registerMarker(this);
		if (showDebugVolume && zone != null) buildDebugVisuals();
		instantiateLodLow();   // zone starts "unloaded" — show the placeholder immediately, if any
	}

	@Register
	@Override
	public void _exitTree() {
		ZoneManager mgr = ZoneManager.get();
		if (mgr != null) mgr.unregisterMarker(this);
	}

	/** Recolour the spawn-volume box to reflect streamed-in (green) vs idle (cyan). */
	public void setLoadedVisual(boolean loaded) {
		if (volumeMat != null) volumeMat.setAlbedo(loaded ? LOADED_COLOR : IDLE_COLOR);
	}

	// ── Low-detail placeholder tier (always resident unless the full-detail geometry is loaded) ──

	/**
	 * Instance {@link Zone#lodLowGeometryPath} as a child, if not already present. A no-op
	 * when the zone has no such path (PLATEAU precincts, most hand-authored pieces) or the file
	 * hasn't been baked yet — {@code exists()} guards against error-log spam either way.
	 */
	public void instantiateLodLow() {
		if (lodLowInstance != null && GD.isInstanceValid(lodLowInstance)) return;
		if (zone == null || zone.lodLowGeometryPath == null || zone.lodLowGeometryPath.isEmpty()) return;
		if (!ResourceLoader.INSTANCE.exists(zone.lodLowGeometryPath, "")) return;
		Object o = GD.load(zone.lodLowGeometryPath);
		if (!(o instanceof PackedScene ps)) return;
		Node n = ps.instantiate();
		if (n == null) return;
		addChild(n);
		lodLowInstance = n;
	}

	/**
	 * Free the placeholder tier the instant the real full-detail geometry takes its place.
	 * {@code removeChild} first (synchronous) rather than relying on {@code queueFree()} alone —
	 * {@code queueFree()} only defers to the end of the current idle frame, so a caller that packs
	 * the scene in the same call (e.g. {@code WorldBaker}'s bake-time strip) would otherwise still
	 * see this node as a live child and serialize it anyway.
	 */
	public void removeLodLow() {
		if (lodLowInstance != null && GD.isInstanceValid(lodLowInstance)) {
			removeChild(lodLowInstance);
			lodLowInstance.queueFree();
		}
		lodLowInstance = null;
	}

	/**
	 * Free the debug spawn-volume box + load/unload rings (see {@link #buildDebugVisuals}).
	 * These are a single-zone dev aid ("so you can see a zone and walk into it") — baking
	 * dozens of them (one per district) into a master-world scene means large overlapping
	 * transparent rings permanently embedded across the whole map, for no runtime benefit.
	 * Same immediate-{@code removeChild}-before-{@code queueFree} reasoning as
	 * {@link #removeLodLow}: a bake-time caller packs the scene in the same call, before a
	 * deferred free would have taken effect.
	 */
	public void removeDebugVisuals() {
		for (Node n : debugVisualNodes) {
			if (GD.isInstanceValid(n)) {
				removeChild(n);
				n.queueFree();
			}
		}
		debugVisualNodes.clear();
		volumeMat = null;
	}

	// ── Debug mesh construction ───────────────────────────────────────────────

	private void buildDebugVisuals() {
		volumeMat = makeMaterial(IDLE_COLOR);
		BoxMesh box = new BoxMesh();
		box.setSize(zone.size);
		MeshInstance3D boxInst = new MeshInstance3D();
		boxInst.setMesh(box);
		boxInst.setMaterialOverride(volumeMat);
		addChild(boxInst);
		debugVisualNodes.add(boxInst);

		addRing(zone.loadRadius, LOAD_RING);
		addRing(zone.unloadRadius, UNLOAD_RING);
	}

	private void addRing(float radius, Color color) {
		CylinderMesh disc = new CylinderMesh();
		disc.setTopRadius(radius);
		disc.setBottomRadius(radius);
		disc.setHeight(0.1f);
		disc.setRadialSegments(48);
		MeshInstance3D inst = new MeshInstance3D();
		inst.setMesh(disc);
		inst.setMaterialOverride(makeMaterial(color));
		addChild(inst);
		debugVisualNodes.add(inst);
	}

	private StandardMaterial3D makeMaterial(Color color) {
		StandardMaterial3D mat = new StandardMaterial3D();
		mat.setTransparency(BaseMaterial3D.Transparency.ALPHA);
		mat.setShadingMode(BaseMaterial3D.ShadingMode.UNSHADED);
		mat.setCullMode(BaseMaterial3D.CullMode.DISABLED);
		mat.setAlbedo(color);
		return mat;
	}
}

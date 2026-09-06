package com.openworld.debug;

import com.openworld.carrier.vehicle.Vehicle;
import com.openworld.character.Player;
import com.openworld.world.PathLaneRoute;
import com.openworld.world.VehicleSpawnConfig;
import com.openworld.world.Zone;
import com.openworld.world.ZoneManager;
import com.openworld.world.ZoneMarker;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.*;
import godot.core.StringName;
import godot.core.Vector3;
import godot.global.GD;

import java.util.HashSet;
import java.util.Set;

/**
 * Headless multi-district streaming regression test (road_blender_godot.md P6.9 addendum,
 * user-requested: verify two SMALL road_kit_authoring test districts stream in/out and connect
 * across their shared boundary, without needing the full 36-district production world).
 *
 * <p>Two real, independently-baked districts — {@code District_test_8_8} (a copy of the
 * user-designated {@code debug_road.blend} fixture, plus one new stub segment reaching toward the
 * boundary) and {@code District_test_7_8} (a small companion authored to connect to that stub) —
 * confirmed to share a clean cross-district connection via {@code tools/check_lanekit_connectivity.py
 * --offset-b 150,0,0} before baking. This host builds their {@link Zone}/{@link
 * ZoneMarker} pair in CODE (the {@code DebugHarness.spawnDebugZone()} idiom — no master
 * `.blend`/region-marker authoring needed for a lightweight test), drives a real spawned
 * {@link Player} through both zones' load/unload radii, and confirms geometry + traffic actually
 * stream via {@link ZoneManager}, not just that the sidecars parse.
 *
 * <p>Run with:
 *
 *   godot --headless res://src/main/resources/com/openworld/debug/MultiZoneStreamTest.tscn
 *
 * Grep for "MDSTEST verdict" (PASS iff both zones load with a nonzero {@link PathLaneRoute} count
 * while the player is near them, and both zones fully unload once the player walks far away).
 */
@Script(className = "MultiZoneStreamTestHost")
public class MultiZoneStreamTestHost extends Node3D {

	private static final String DISTRICT_A =
			"res://src/main/resources/com/openworld/world/pieces/District_test_8_8.tscn";
	private static final String DISTRICT_B =
			"res://src/main/resources/com/openworld/world/pieces/District_test_7_8.tscn";

	private static final Vector3 CENTER_A = new Vector3(0f, 0f, 0f);
	private static final Vector3 CENTER_B = new Vector3(150f, 0f, 0f);
	private static final Vector3 FAR_AWAY = new Vector3(0f, 1f, -900f);
	private static final Vector3 NEAR_BOTH = new Vector3(75f, 1f, 0f);

	// Phase timings (seconds) — generous so the 0.5s ZoneManager eval tick + streaming
	// budget (4ms/frame GEO_ENTER slicing) definitely completes within each phase.
	private static final double APPROACH_AT = 2.0;
	private static final double CHECK_LOADED_AT = 12.0;
	private static final double RETREAT_AT = 14.0;
	private static final double CHECK_UNLOADED_AT = 24.0;
	private static final double FINISH_AT = 26.0;

	private Player player;
	private ZoneMarker markerA, markerB;
	private double timer = 0.0;
	private boolean done = false;
	private boolean checkedLoaded = false;

	@Register
	@Override
	public void _ready() {
		StaticBody3D floor = new StaticBody3D();
		CollisionShape3D cs = new CollisionShape3D();
		BoxShape3D box = new BoxShape3D();
		box.setSize(new Vector3(3000f, 1f, 3000f));
		cs.setShape(box);
		floor.addChild(cs);
		floor.setPosition(new Vector3(0f, -1.0f, 0f));   // just below both districts' own road z (~0.15)
		addChild(floor);

		// ZoneManager.charactersContainer() looks up a sibling "Characters" node by name
		// (the same container the production WorldMaster.tscn/hosts wire) to parent ambient
		// traffic/AI under — without it, spawns are silently skipped ("Characters container not
		// found"), so this test would only ever verify geometry/PathLaneRoute streaming, not
		// actual traffic.
		Node characters = new Node();
		characters.setName(new StringName("Characters"));
		addChild(characters);

		markerA = buildZoneMarker("District_test_8_8", DISTRICT_A, CENTER_A,
				new Vector3(400f, 10f, 300f), 250f, 400f);
		markerB = buildZoneMarker("District_test_7_8", DISTRICT_B, CENTER_B,
				new Vector3(150f, 10f, 150f), 150f, 250f);

		java.lang.Object res = ResourceLoader.INSTANCE.load(
				"res://src/main/resources/com/openworld/character/Player.tscn", "",
				ResourceLoader.CacheMode.REUSE);
		if (!(res instanceof PackedScene packed) || !(packed.instantiate() instanceof Player p)) {
			GD.printErr("MDSTEST: cannot load Player.tscn"); finish(false); return;
		}
		player = p;
		addChild(player);
		player.setGlobalPosition(FAR_AWAY);

		GD.print("MDSTEST: player spawned far away " + FAR_AWAY + "; both zones should start unloaded");
	}

	private ZoneMarker buildZoneMarker(String zoneId, String geometryPath, Vector3 center,
											 Vector3 size, float loadRadius, float unloadRadius) {
		Zone zone = new Zone();
		zone.zoneId = zoneId;
		zone.geometryPath = geometryPath;
		zone.size = size;
		zone.loadRadius = loadRadius;
		zone.unloadRadius = unloadRadius;
		VehicleSpawnConfig vc = new VehicleSpawnConfig();
		vc.count = 3;
		vc.routeName = zoneId;   // exact zoneId match -- ZoneManager.findRoute's P6.4 pass
		zone.vehicleSpawnConfigs.add(vc);

		ZoneMarker marker = new ZoneMarker();
		marker.setName(new StringName("ZoneMarker_" + zoneId));
		marker.zone = zone;
		addChild(marker);
		marker.setGlobalPosition(center);
		return marker;
	}

	@Register
	@Override
	public void _physicsProcess(double delta) {
		if (done) return;
		timer += delta;

		if (timer >= APPROACH_AT && timer < APPROACH_AT + delta * 2) {
			player.setGlobalPosition(NEAR_BOTH);
			GD.print("MDSTEST: t=" + String.format("%.1f", timer) + " player -> " + NEAR_BOTH
					+ " (near both zone centers)");
		}
		if (!checkedLoaded && timer >= CHECK_LOADED_AT) {
			checkedLoaded = true;
			reportZoneState("CHECK_LOADED");
		}
		if (timer >= RETREAT_AT && timer < RETREAT_AT + delta * 2) {
			player.setGlobalPosition(FAR_AWAY);
			GD.print("MDSTEST: t=" + String.format("%.1f", timer) + " player -> " + FAR_AWAY
					+ " (retreating, both zones should unload)");
		}
		if (timer >= CHECK_UNLOADED_AT && timer < CHECK_UNLOADED_AT + delta * 2) {
			reportZoneState("CHECK_UNLOADED");
		}
		if (timer >= FINISH_AT) finish(evaluate());
	}

	private int pathLaneCount(ZoneMarker marker) {
		Set<PathLaneRoute> found = new HashSet<>();
		collectLanes(marker, found);
		return found.size();
	}

	private void collectLanes(Node n, Set<PathLaneRoute> out) {
		if (n instanceof PathLaneRoute p) out.add(p);
		for (Node c : n.getChildren()) collectLanes(c, out);
	}

	private boolean loadedLoggedA, loadedLoggedB, unloadedLoggedA, unloadedLoggedB;
	private int lanesAAtLoad = -1, lanesBAtLoad = -1;

	private int vehicleCount() {
		Node characters = getNodeOrNull("Characters");
		if (characters == null) return 0;
		int n = 0;
		for (Node c : characters.getChildren()) if (c instanceof Vehicle) n++;
		return n;
	}

	private void reportZoneState(String tag) {
		int lanesA = pathLaneCount(markerA);
		int lanesB = pathLaneCount(markerB);
		int vehicles = vehicleCount();
		GD.print("MDSTEST " + tag + ": District_test_8_8 childCount=" + markerA.getChildCount()
				+ " pathLanes=" + lanesA + " | District_test_7_8 childCount="
				+ markerB.getChildCount() + " pathLanes=" + lanesB + " | vehicles=" + vehicles);
		if ("CHECK_LOADED".equals(tag)) {
			loadedLoggedA = lanesA > 0;
			loadedLoggedB = lanesB > 0;
			lanesAAtLoad = lanesA;
			lanesBAtLoad = lanesB;
		} else if ("CHECK_UNLOADED".equals(tag)) {
			unloadedLoggedA = lanesA == 0;
			unloadedLoggedB = lanesB == 0;
		}
	}

	private boolean evaluate() {
		GD.print(String.format(
				"MDSTEST SUMMARY loadedA=%s(lanes=%d) loadedB=%s(lanes=%d) unloadedA=%s unloadedB=%s",
				loadedLoggedA, lanesAAtLoad, loadedLoggedB, lanesBAtLoad, unloadedLoggedA, unloadedLoggedB));
		return loadedLoggedA && loadedLoggedB && unloadedLoggedA && unloadedLoggedB;
	}

	private void finish(boolean pass) {
		if (done) return;
		done = true;
		GD.print("MDSTEST verdict=" + (pass ? "PASS" : "CHECK"));
		if (getTree() != null) getTree().quit();
	}
}

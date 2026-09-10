#!/usr/bin/env python3
"""Author the DebugWorld terrain: two islands in open sea.

This is NOT a window of the real island (that is `island_to_terrain3d.py --half`). A test world
wants a shape chosen for what it exercises, and the piece of real coastline the window happened to
cut was platform edges and a channel — no clean land, nowhere to put a zone.

Two islands, far enough apart that walking between them crosses both zone radii, everything else
below sea level. Small enough to load instantly and cross on foot in a couple of minutes.

**Scale is 1:1 with the real world.** Slopes, beach gradients and the sea level (Y = 0) are the
same numbers the island uses, because a character's step height and a car's suspension do not
scale — a shrunken world would handle differently and nothing measured here would transfer.

    python3 tools/make_debug_world.py

Writes the same r16 + manifest pair `tools/godot/import_island_terrain.gd` consumes.
"""

from __future__ import annotations

import json
import os

import numpy as np

# ---- the world -----------------------------------------------------------------------------
HALF = 512.0            # +/-512 m, so 1024 m across
VERTEX_SPACING = 2.0    # same lattice as the real world
REGION_SIZE = 256       # 512 m per region -> exactly 4 regions

SEABED = -40.0          # flat sea floor between and around the islands
SEABED_EDGE = -55.0     # deepening toward the world edge, so the rim is not a shelf

# centre_x, centre_z, pad_radius, pad_height
#   The two centres are 500 m apart: standing on one, the other is outside the 350 m unload
#   radius, and walking over crosses the 200 m load radius with both briefly resident.
ISLANDS = [
    (-250.0, 0.0, 90.0, 14.0),   # A — the big one; carries the blockout and a zone
    (250.0, 0.0, 70.0, 9.0),     # B — the far one; streams in as you approach
]

# The radial profile is built from CONTROL POINTS, not from one smoothstep across the whole
# island. A single smoothstep puts its steepest part exactly at the waterline — measured 38% on
# the first cut, which is a cliff you cannot walk up, not a beach. The bands below fix the beach
# grade directly and let the shoulder above it be as steep as it likes.
SHOULDER_LEN = 60.0     # pad edge down to BEACH_TOP — a hillside, good for vehicle grade tests
BEACH_TOP = 1.5         # where the gentle band starts, above the water
BEACH_BOTTOM = -2.0     # ...and ends, below it
BEACH_GRADE = 0.06      # 6% through the waterline: walk in and out, wade, then swim
DROP_LEN = 120.0        # below the beach, down to the sea floor


def _smootherstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def field(xs, zs):
    """Height at every (x, z) — the max over the islands and the sea floor."""
    X, Z = np.meshgrid(xs, zs)
    r_world = np.maximum(np.abs(X), np.abs(Z))
    # sea floor deepens toward the rim so the edge does not read as a flat shelf
    h = SEABED + (SEABED_EDGE - SEABED) * _smootherstep((r_world - HALF * 0.55) / (HALF * 0.45))
    for cx, cz, pad_r, pad_h in ISLANDS:
        d = np.hypot(X - cx, Z - cz)
        rs, hs = island_profile(pad_r, pad_h)
        # np.interp CLAMPS past its last control point, so an unguarded island contributes its
        # sea-floor value across the whole world and erases the rim deepening below — measured as
        # a min of -40.00 where the sea floor should reach -55.
        contrib = np.where(d <= rs[-1], np.interp(d, rs, hs), -np.inf)
        h = np.maximum(h, contrib)
    return h.astype(np.float32)


def island_profile(pad_r, pad_h):
    """Radial control points: flat pad, shoulder, gentle beach through the waterline, drop."""
    beach_len = (BEACH_TOP - BEACH_BOTTOM) / BEACH_GRADE
    rs = [0.0,
          pad_r,
          pad_r + SHOULDER_LEN,
          pad_r + SHOULDER_LEN + beach_len,
          pad_r + SHOULDER_LEN + beach_len + DROP_LEN]
    hs = [pad_h, pad_h, BEACH_TOP, BEACH_BOTTOM, SEABED]
    return rs, hs


def measure(xs, zs, h):
    """Report the things a test world has to get right, rather than assuming them."""
    print("  land above Y=0 : %.1f%% of the world" % (100.0 * (h > 0).mean()))
    print("  height range   : %.2f .. %.2f m" % (h.min(), h.max()))
    for i, (cx, cz, pad_r, pad_h) in enumerate(ISLANDS):
        name = "AB"[i]
        rs, hs = island_profile(pad_r, pad_h)
        rr = np.arange(0.0, rs[-1], 0.5)
        prof = np.interp(rr, rs, hs)
        cross = int(np.argmax(prof <= 0.0))
        grad = abs(prof[cross] - prof[cross - 1]) / 0.5 if cross > 0 else float("nan")
        shoulder = (pad_h - BEACH_TOP) / SHOULDER_LEN
        print("  island %s       : pad r=%.0f h=%.1f, waterline r=%.0f, beach %.1f%% / shoulder "
              "%.0f%%, ~%.1f ha dry"
              % (name, pad_r, pad_h, rr[cross], grad * 100.0, shoulder * 100.0,
                 np.pi * rr[cross] ** 2 / 1e4))
    ax, az = ISLANDS[0][0], ISLANDS[0][1]
    bx, bz = ISLANDS[1][0], ISLANDS[1][1]
    print("  island centres : %.0f m apart  (zone load 200 / unload 350)"
          % np.hypot(bx - ax, bz - az))


def main():
    span = REGION_SIZE * VERTEX_SPACING
    loc_min = int(np.floor(-HALF / span))
    loc_max = int(np.ceil(HALF / span)) - 1
    n = (loc_max - loc_min + 1) * REGION_SIZE
    origin = loc_min * span

    xs = origin + np.arange(n) * VERTEX_SPACING
    zs = origin + np.arange(n) * VERTEX_SPACING
    h = field(xs, zs)

    print("debug world: %d x %d texels @ %.1f m, regions %d..%d, origin %.0f"
          % (n, n, VERTEX_SPACING, loc_min, loc_max, origin))
    measure(xs, zs, h)

    out = "assets/terrain3d/_source"
    os.makedirs(out, exist_ok=True)
    lo, hi = float(h.min()), float(h.max())
    norm = np.clip((h - lo) / (hi - lo), 0.0, 1.0)
    (norm * 65535.0 + 0.5).astype("<u2").tofile(os.path.join(out, "debug_height.r16"))

    from PIL import Image
    img = np.zeros(h.shape, dtype=np.uint8)
    land = h > 0
    if land.any():
        img[land] = np.clip(h[land] / max(hi, 1e-6) * 200.0 + 55.0, 55, 255).astype(np.uint8)
    Image.fromarray(img, mode="L").save(os.path.join(out, "debug_height.png"))

    manifest = {
        "height_file": "res://%s/debug_height.r16" % out.replace(os.sep, "/"),
        "data_directory": "res://assets/terrain3d/debug_world",
        "size": [n, n],
        "range": [lo, hi],
        "import_position": [origin, origin],
        "vertex_spacing": VERTEX_SPACING,
        "region_size": REGION_SIZE,
        "region_locations": [loc_min, loc_max],
        "sea_level_y": 0.0,
        "source": "tools/make_debug_world.py (two islands, authored)",
        "islands": [{"centre": [c[0], c[1]], "pad_radius": c[2], "pad_height": c[3]}
                    for c in ISLANDS],
    }
    with open(os.path.join(out, "debug_import.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    print("  wrote %s/debug_height.r16 (%.1f MB) + manifest"
          % (out, os.path.getsize(os.path.join(out, "debug_height.r16")) / 1e6))


if __name__ == "__main__":
    raise SystemExit(main())

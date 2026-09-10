#!/usr/bin/env python3
"""Sample the island height field onto a Terrain3D import lattice.

This is the ONE-WAY bridge out of the Blender world pipeline: it turns
``island_v3_terrain.Terrain.surface`` — the existing, self-tested owner of "what would I land
on if I fell here" — into a heightmap Terrain3D can import, so the hand-sculpting starts from
the island that already exists instead of from a blank field.

**It does not re-derive the field.** The whole class of defect this codebase keeps re-finding is
one derived fact with two owners, so this script only ever *calls* ``Terrain.surface``; there is
deliberately no numpy re-implementation of the hills, platforms or seabed here, and the cost of
that decision (a scalar call per texel) is paid with ``multiprocessing`` instead.

After this runs, ``island_v3_terrain.py`` has done its last job. The terrain is Terrain3D's from
then on: sculpt it in the editor, and the heightmap in ``assets/terrain3d`` is the record.

Two conversions happen here and both are load-bearing:

**Axis mapping.** The Python field is in Blender plan coordinates (``x`` east, ``y`` north, ``z``
up). Godot is y-up with the mapping this project verified to three decimals on a baked road:
``(x, y, z)_blender -> (x, z, -y)_godot``. So a Godot world position ``(gx, _, gz)`` samples the
field at ``(gx, -gz)``. Get this wrong and the island comes out mirrored north-south, which looks
plausible from every angle — hence ``--verify``, which prints the massif peak in both frames.

**Sea level becomes Y = 0.** The field puts the waterline at ``SEA_LEVEL_Z`` (-0.60) and the land
base at ``BASE_Z`` (0.00). Carrying that offset forward buys nothing and costs a constant every
future reader has to know, so every sampled height is shifted by ``-SEA_LEVEL_Z``: the sea is Y=0,
the land base stands +0.60 above it (the beach step the shore was always authored with), and the
water volume's top face is the world origin plane. One number, and it is zero.

Output (into ``--out``, default ``assets/terrain3d/_source``):

  ``island_height.r16``      uint16 little-endian, row-major, rows increasing in world +Z.
                             Terrain3D reads this with an explicit size + height range, which is
                             what ``island_import.json`` carries.
  ``island_import.json``     the manifest ``tools/godot/import_island_terrain.gd`` consumes.
  ``island_height.png``      8-bit preview, for looking at before spending a Godot run on it.

Usage::

    python3 tools/island_to_terrain3d.py                 # 2 m lattice, the default
    python3 tools/island_to_terrain3d.py --vertex-spacing 1.0 --region-size 1024
    python3 tools/island_to_terrain3d.py --verify        # sample-point report, no files written
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import island_v3_geom as G          # noqa: E402
import island_v3_terrain as T       # noqa: E402

# --------------------------------------------------------------------------------------------
# Lattice
# --------------------------------------------------------------------------------------------

#: Terrain3D places region ``loc`` over world ``[loc*region_size*vertex_spacing, (loc+1)*...)``.
#: The import must cover at least the sea floor's own edge, which is where the world ends: the
#: bounds wall stands ``BOUNDS_INSET`` inside it and nothing is reachable past that.
TERRAIN_HALF = G.WORLD / 2.0 + T.SEABED_MARGIN      # 2304 m

#: The field's waterline, moved to the world origin plane. See the module docstring.
SEA_SHIFT = -T.SEA_LEVEL_Z                          # +0.60


def lattice_window(vertex_spacing: float, region_size: int, half: float):
    """Region range and texel grid for an arbitrary half-extent, centred on the origin."""
    span = region_size * vertex_spacing
    loc_min = int(np.floor(-half / span))
    loc_max = int(np.ceil(half / span)) - 1
    n_regions = loc_max - loc_min + 1
    return loc_min, loc_max, n_regions * region_size, loc_min * span


def lattice(vertex_spacing: float, region_size: int):
    """Region range and texel grid covering ``TERRAIN_HALF`` on the Terrain3D lattice.

    Returns ``(loc_min, loc_max, n_texels, origin_m)`` where ``origin_m`` is the world X/Z of
    texel (0, 0) — the import position, which must land on a region boundary or Terrain3D
    silently straddles regions with a half-texel offset.
    """
    span = region_size * vertex_spacing              # metres covered by one region
    loc_min = int(np.floor(-TERRAIN_HALF / span))
    loc_max = int(np.ceil(TERRAIN_HALF / span)) - 1
    n_regions = loc_max - loc_min + 1
    return loc_min, loc_max, n_regions * region_size, loc_min * span


def _row(args):
    """Sample one image row. Runs in a worker process, so it re-creates its own Terrain.

    ``cx``/``cz`` offset the sample into the island in GODOT coordinates while the output stays
    centred on the origin — that is what makes a WINDOW: a real piece of the real island, at 1:1
    scale, re-centred so the small scene stands on its own. Scaling the island down instead would
    shrink every slope and step relative to a character whose step height does not scale, and the
    handling you measured there would not transfer.
    """
    j, origin, n, vs, cx, cz = args
    global _TERRAIN
    try:
        terrain = _TERRAIN
    except NameError:
        terrain = _TERRAIN = T.Terrain()
    gz = origin + j * vs                             # Godot world Z for this row
    by = -(gz + cz)                                  # ...is Blender -Y, offset into the island
    surface = terrain.surface
    out = np.empty(n, dtype=np.float32)
    for i in range(n):
        out[i] = surface(origin + i * vs + cx, by)
    return j, out


def sample(vertex_spacing: float, region_size: int, jobs: int, half=None, centre=(0.0, 0.0)):
    if half is None:
        loc_min, loc_max, n, origin = lattice(vertex_spacing, region_size)
    else:
        loc_min, loc_max, n, origin = lattice_window(vertex_spacing, region_size, half)
    field = np.empty((n, n), dtype=np.float32)
    work = [(j, origin, n, vertex_spacing, centre[0], centre[1]) for j in range(n)]
    t0 = time.time()
    with mp.Pool(jobs) as pool:
        for k, (j, row) in enumerate(pool.imap_unordered(_row, work, chunksize=8)):
            field[j] = row
            if k % 128 == 0:
                done = k / n
                sys.stderr.write("\r  sampling %5.1f%%  %5.0fs" % (done * 100, time.time() - t0))
                sys.stderr.flush()
    sys.stderr.write("\r  sampling 100.0%%  %5.0fs\n" % (time.time() - t0))
    return field + SEA_SHIFT, origin, (loc_min, loc_max), n


# --------------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------------

def write_r16(path: str, field: np.ndarray, lo: float, hi: float) -> None:
    """uint16 over ``[lo, hi]`` — the range Terrain3D is told on import, so it is exact at both
    ends and quantises the middle to ``(hi - lo) / 65535`` metres."""
    norm = np.clip((field - lo) / (hi - lo), 0.0, 1.0)
    (norm * 65535.0 + 0.5).astype("<u2").tofile(path)


def write_preview(path: str, field: np.ndarray, sea: float = 0.0) -> None:
    """8-bit look-at-it check. Sea is flat black so the coastline reads at a glance."""
    from PIL import Image
    land = field > sea
    img = np.zeros(field.shape, dtype=np.uint8)
    if land.any():
        hi = float(field[land].max())
        img[land] = np.clip(field[land] / max(hi, 1e-6) * 200.0 + 55.0, 55, 255).astype(np.uint8)
    Image.fromarray(img, mode="L").save(path)


def landmarks(terrain: "T.Terrain") -> list:
    """The asymmetric points both ends check against.

    A north-south mirror — the Blender ``(x, y, z) -> (x, z, -y)`` mapping applied the wrong way
    round — is the one error in this bridge that yields a perfectly plausible island, so the
    control has to be a place whose height differs from its mirror image. The named massif and
    spur are two; the highest ground in the world is a third that needs no schema knowledge and
    moves a long way under a mirror.
    """
    pts = []

    def add(name, bx, by):
        pts.append({
            "name": name,
            "blender": [round(bx, 3), round(by, 3)],
            "godot": [round(bx, 3), round(-by, 3)],
            "y": round(terrain.surface(bx, by) + SEA_SHIFT, 3),
        })

    for name in ("MASSIF", "SPUR"):
        spec = getattr(G, name, None)
        if isinstance(spec, dict) and "cx" in spec:
            add(name.lower(), float(spec["cx"]), float(spec["cy"]))

    step = 32.0
    axis = np.arange(-TERRAIN_HALF, TERRAIN_HALF, step)
    best = (-1e9, 0.0, 0.0)
    for by in axis:
        for bx in axis:
            z = terrain.height(bx, by)
            if z > best[0]:
                best = (z, bx, by)
    add("peak", best[1], best[2])

    # A seabed point and a shore point: the first proves the sea floor came across as ground
    # rather than a hole, the second is where the beach step lives.
    add("open_sea", 0.0, -(G.WORLD / 2.0 + T.SEABED_MARGIN / 2.0))
    return pts


def verify(vertex_spacing: float, region_size: int) -> None:
    loc_min, loc_max, n, origin = lattice(vertex_spacing, region_size)
    terrain = T.Terrain()
    print("lattice      %d x %d texels @ %.2f m, regions %d..%d, origin %.1f m"
          % (n, n, vertex_spacing, loc_min, loc_max, origin))
    print("world        +/- %.0f m terrain, wall at +/- %.0f m"
          % (TERRAIN_HALF, TERRAIN_HALF - T.BOUNDS_INSET))
    print("sea shift    %+.2f m  (field sea %.2f -> Godot Y 0.00)" % (SEA_SHIFT, T.SEA_LEVEL_Z))
    print()
    print("  %-10s %-24s %-24s %s" % ("landmark", "blender (x, y)", "godot (x, _, z)", "Y"))
    for row in landmarks(terrain):
        print("  %-10s (%8.1f, %8.1f)    (%8.1f, _, %8.1f)    %8.2f"
              % (row["name"], row["blender"][0], row["blender"][1],
                 row["godot"][0], row["godot"][1], row["y"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vertex-spacing", type=float, default=2.0,
                    help="metres per texel (default 2.0 — six times finer than the 12 m ground "
                         "mesh that ships today, at a quarter the data of a 1 m lattice)")
    ap.add_argument("--region-size", type=int, default=512,
                    choices=[64, 128, 256, 512, 1024, 2048],
                    help="Terrain3D region size in texels (default 512)")
    ap.add_argument("--out", default="assets/terrain3d/_source")
    ap.add_argument("--data-dir", default="res://assets/terrain3d/island",
                    help="where the importer will write the region .res files")
    ap.add_argument("--jobs", type=int, default=0, help="worker processes (0 = all cores)")
    ap.add_argument("--half", type=float, default=None,
                    help="emit a WINDOW of this half-extent instead of the whole island")
    ap.add_argument("--centre", default="0,0",
                    help="window centre in GODOT x,z — the piece of the island to cut out")
    ap.add_argument("--name", default="island", help="output file stem")
    ap.add_argument("--verify", action="store_true", help="print the frame check and exit")
    args = ap.parse_args()

    if args.verify:
        verify(args.vertex_spacing, args.region_size)
        return 0

    jobs = args.jobs or mp.cpu_count()
    print("island -> Terrain3D  (%.2f m lattice, region %d, %d workers)"
          % (args.vertex_spacing, args.region_size, jobs))
    cx, cz = (float(v) for v in args.centre.split(","))
    field, origin, (loc_min, loc_max), n = sample(
        args.vertex_spacing, args.region_size, jobs, args.half, (cx, cz))

    lo, hi = float(field.min()), float(field.max())
    os.makedirs(args.out, exist_ok=True)
    r16 = os.path.join(args.out, "%s_height.r16" % args.name)
    write_r16(r16, field, lo, hi)
    write_preview(os.path.join(args.out, "%s_height.png" % args.name), field)

    manifest = {
        "height_file": "res://%s/%s_height.r16" % (args.out.replace(os.sep, "/"), args.name),
        "window_centre": [cx, cz],
        "window_half": args.half,
        "data_directory": args.data_dir,
        "size": [n, n],
        "range": [lo, hi],
        "import_position": [origin, origin],
        "vertex_spacing": args.vertex_spacing,
        "region_size": args.region_size,
        "region_locations": [loc_min, loc_max],
        "sea_level_y": 0.0,
        "sea_shift_applied": SEA_SHIFT,
        "source": "island_v3_terrain.Terrain.surface",
    }
    with open(os.path.join(args.out, "%s_import.json" % args.name), "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    # The read-back control. Written here rather than typed into the Godot script so neither end
    # carries a copy of the other's numbers — the seam has one owner, same as the manifest.
    probe = {
        "vertex_spacing": args.vertex_spacing,
        "region_size": args.region_size,
        "points": landmarks(T.Terrain()),
    }
    with open(os.path.join(args.out, "%s_probe.json" % args.name), "w") as fh:
        json.dump(probe, fh, indent=2)
        fh.write("\n")

    print("  %d x %d texels, Y %.2f .. %.2f m  (%.1f mm per uint16 step)"
          % (n, n, lo, hi, (hi - lo) / 65535.0 * 1000.0))
    print("  regions %d..%d on each axis (%d total), import at (%.1f, %.1f)"
          % (loc_min, loc_max, (loc_max - loc_min + 1) ** 2, origin, origin))
    print("  wrote %s (%.1f MB)" % (r16, os.path.getsize(r16) / 1e6))
    print("  next: godot --headless --script tools/godot/import_island_terrain.gd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

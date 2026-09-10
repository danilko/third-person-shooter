#!/usr/bin/env python3
"""Generate a beach-sand surface texture packed the way Terrain3D wants it.

This project ships exactly two PBR sets — Ground037 (a dirt) and Rock023 — and no sand. Tinting
the dirt was tried first and does not work: Ground037's albedo carries large dark clods, so any
tint of it reads as dry earth at every distance. Sand's signature is the opposite — very fine
grain, low contrast, bright and warm — so it is cheaper to synthesise than to fake.

Terrain3D's packing, which is what makes this drop straight into a Terrain3DTextureAsset:

    <name>_alb_ht.png   RGB = albedo, A = height
    <name>_nrm_rgh.png  RGB = normal (tangent space, +Y up), A = roughness

Two scales are combined so it does not visibly tile: a fine grain that reads underfoot and a
gentle swell that breaks up the middle distance. The normal map is derived from the same height
field the alpha carries, so lighting and parallax agree.

    python3 tools/make_sand_texture.py
"""

from __future__ import annotations

import numpy as np
from PIL import Image

SIZE = 1024
OUT = "assets/terrain3d/textures"

BASE = np.array([0.855, 0.775, 0.616])   # warm, bright, low saturation
DARK = np.array([0.700, 0.618, 0.470])   # damp/shadowed grains


def _tileable_noise(rng, size, cells):
    """Value noise that wraps, so the texture tiles without a seam."""
    g = rng.random((cells, cells)).astype(np.float32)
    g = np.pad(g, ((0, 1), (0, 1)), mode="wrap")
    ys = np.linspace(0, cells, size, endpoint=False)
    xs = np.linspace(0, cells, size, endpoint=False)
    y0 = ys.astype(int); x0 = xs.astype(int)
    fy = (ys - y0)[:, None]; fx = (xs - x0)[None, :]
    fy = fy * fy * (3 - 2 * fy); fx = fx * fx * (3 - 2 * fx)
    a = g[y0][:, x0]; b = g[y0][:, x0 + 1]
    c = g[y0 + 1][:, x0]; d = g[y0 + 1][:, x0 + 1]
    return (a * (1 - fx) * (1 - fy) + b * fx * (1 - fy)
            + c * (1 - fx) * fy + d * fx * fy)


def main() -> int:
    rng = np.random.default_rng(20260907)

    # grain: the fine speckle you see underfoot, kept low-contrast
    grain = rng.random((SIZE, SIZE)).astype(np.float32)
    grain = 0.5 + (grain - 0.5) * 0.35

    # ripples + swell: the two coarser scales that break up tiling
    ripple = _tileable_noise(rng, SIZE, 64)
    swell = _tileable_noise(rng, SIZE, 12)

    height = 0.55 * ripple + 0.30 * swell + 0.15 * grain
    height = (height - height.min()) / (height.max() - height.min())

    shade = 0.75 + 0.25 * height
    albedo = BASE[None, None, :] * shade[:, :, None]
    albedo = albedo * (1 - 0.20 * grain[:, :, None]) + DARK[None, None, :] * (0.20 * grain[:, :, None])
    albedo = np.clip(albedo, 0.0, 1.0)

    alb_ht = np.dstack([albedo, height]).astype(np.float32)
    Image.fromarray((alb_ht * 255).astype(np.uint8), mode="RGBA").save(
        "%s/sand_alb_ht.png" % OUT)

    # normal from the same height field — a wrapped gradient, so the normals tile too
    strength = 6.0
    dzdx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * strength
    dzdy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * strength
    nx, ny, nz = -dzdx, -dzdy, np.ones_like(height)
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + nz * nz)
    normal = np.dstack([nx * inv, ny * inv, nz * inv]) * 0.5 + 0.5
    roughness = np.clip(0.86 - 0.10 * height, 0.0, 1.0)   # sand is rough, barely varying
    nrm_rgh = np.dstack([normal, roughness]).astype(np.float32)
    Image.fromarray((nrm_rgh * 255).astype(np.uint8), mode="RGBA").save(
        "%s/sand_nrm_rgh.png" % OUT)

    print("wrote %s/sand_alb_ht.png and sand_nrm_rgh.png (%dx%d)" % (OUT, SIZE, SIZE))
    print("  height %.3f..%.3f, albedo mean %s" % (
        height.min(), height.max(), np.round(albedo.reshape(-1, 3).mean(0), 3)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Tokyo-Bay Island v3 — draws both plates from tools/island_v3_geom.py.

    python3 tools/island_v3_plates.py          # writes both SVGs into the repo root
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
import random

_here = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location("g", os.path.join(_here, "island_v3_geom.py"))
G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(G)

DISTRICT, GRID_N, WORLD, ORIGIN = G.DISTRICT, G.GRID_N, G.WORLD, G.ORIGIN


# ------------------------------------------------------------------ shared helpers
def mk(S, MX, MY):
    def px(x, y):
        return (MX + (x + ORIGIN) * S, MY + (ORIGIN - y) * S)
    return px


def path(px, pts, close=True):
    d = "M" + " L".join(f"{a:.1f} {b:.1f}" for a, b in (px(*p) for p in pts))
    return d + (" Z" if close else "")


def seg_dist(x, y, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    if L == 0:
        return math.hypot(x - a[0], y - a[1])
    t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / L))
    return math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy))


def in_void(x, y):
    for p in G.PARKS:
        if G.inside(p, x, y):
            return True
    for pl in (G.LOOP + [G.LOOP[0]], G.AIRPORT_RAMP, G.WESTRAD, G.PORTSPUR):
        for a, b in zip(pl, pl[1:]):
            if seg_dist(x, y, a, b) < 24:
                return True
    for a, b in zip(G.RIVER, G.RIVER[1:]):
        if seg_dist(x, y, a, b) < 30:
            return True
    return False


def ok(cx, cy, r):
    x0, y0, x1, y1 = r
    return (x0 <= cx <= x1 and y0 <= cy <= y1 and G.on_land(cx, cy)
            and not in_void(cx, cy))


def block_buildings(out, gx, gy, rect, spec, rng, blk=None):
    _, _, frontage, depth, keep, infill, col = spec
    B, S_ = (blk or G.BLOCK), G.STREET
    ix0, iy0 = gx + S_ / 2, gy + S_ / 2
    ix1, iy1 = gx + B - S_ / 2, gy + B - S_ / 2
    for b in (iy0, iy1 - depth):
        t = ix0
        while t + frontage <= ix1:
            w = min(frontage * rng.uniform(0.75, 1.55), ix1 - t)
            if ok(t + w / 2, b + depth / 2, rect) and rng.random() < keep:
                out.append((t, b, w, depth, col, 0.0))
            t += w + rng.uniform(0.0, frontage * 0.2)
    for b in (ix0, ix1 - depth):
        t = iy0 + depth
        while t + frontage <= iy1 - depth:
            w = min(frontage * rng.uniform(0.75, 1.55), iy1 - depth - t)
            if ok(b + depth / 2, t + w / 2, rect) and rng.random() < keep:
                out.append((b, t, depth, w, col, 0.0))
            t += w + rng.uniform(0.0, frontage * 0.2)
    if infill <= 0:
        return
    u = ix0 + depth + 3
    while u + depth < ix1 - depth:
        v = iy0 + depth + 3
        while v + frontage < iy1 - depth:
            w = frontage * rng.uniform(0.8, 1.4)
            if ok(u + depth / 2, v + w / 2, rect) and rng.random() < infill:
                out.append((u, v, depth, w, col, 0.0))
            v += w + 2.5
        u += depth + 3.5


def danchi_block(out, gx, gy, rect, rng, blk=168.0):
    """12 x 55 m slabs on ONE bearing that ignores the street grid — the signature."""
    br = math.radians(G.DANCHI_BEARING)
    cx0, cy0 = gx + blk / 2, gy + blk / 2
    for k in (-2, -1, 0, 1, 2):
        off = k * 30.0
        ax, ay = cx0 - math.sin(br) * off, cy0 + math.cos(br) * off
        sx = ax - (math.cos(br) * 27.5 - math.sin(br) * 6.0)
        sy = ay - (math.sin(br) * 27.5 + math.cos(br) * 6.0)
        if ok(ax, ay, rect):
            out.append((sx, sy, 55.0, 12.0, "#a49e8b", G.DANCHI_BEARING))


# Block size is a THEME property, not a constant. Japanese blocks run ~50-90 m in a dense
# centre, ~100-170 m in suburbs, and 250 m+ in port/industry. Using one 168 m block
# everywhere was the thing making the neon read like the suburbs at a different colour.
BLOCK_M = dict(neonA=84.0, neonB=84.0, neonC=84.0, resid=168.0, farm=336.0,
               port=252.0, air=252.0)


def anchor_block(out, gx, gy, rect, blk, rng):
    """One large footprint instead of eel-bed frontage — department store / office /
    station building. Real neon districts are mostly 6 x 12 with a few big boxes."""
    w, d = 24.0, 34.0
    cx, cy = gx + blk / 2, gy + blk / 2
    if ok(cx, cy, rect):
        out.append((cx - w / 2, cy - d / 2, w, d, "#7d7768", 0.0))


def scatter(spec):
    out = []
    blk = BLOCK_M.get(spec[0], 168.0)
    for rect in spec[1]:
        x0, y0, x1, y1 = rect
        bx = math.floor(x0 / blk) * blk
        while bx < x1:
            by = math.floor(y0 / blk) * blk
            while by < y1:
                rng = random.Random((int(bx) * 733 + int(by) * 17) & 0xffff)
                ib, jb = int(bx / blk), int(by / blk)
                if spec[0] == "resid" and (ib + jb) % 3 == 0:
                    danchi_block(out, bx, by, rect, rng)
                elif spec[0].startswith("neon") and (ib * 3 + jb) % 7 == 0:
                    anchor_block(out, bx, by, rect, blk, rng)
                else:
                    block_buildings(out, bx, by, rect, spec, rng, blk)
                by += blk
            bx += blk
    return out


def clipped(pts, step=9.0):
    runs, cur = [], []
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.dist(a, b) / step))
        for i in range(n + 1):
            t = i / n
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if G.on_land(*p):
                cur.append(p)
            elif cur:
                runs.append(cur)
                cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) > 1]


def offset_inward(poly, d):
    """Angle-bisector inward offset — the coastal ring road."""
    n = len(poly)
    out = []
    for i in range(n):
        px_, py_ = poly[i]
        ax, ay = poly[i - 1]
        bx, by = poly[(i + 1) % n]
        n1 = (py_ - ay, ax - px_)
        n2 = (by - py_, px_ - bx)
        vx, vy = n1[0] + n2[0], n1[1] + n2[1]
        L = math.hypot(vx, vy) or 1.0
        vx, vy = vx / L, vy / L
        cx = sum(p[0] for p in poly) / n
        cy = sum(p[1] for p in poly) / n
        if (cx - px_) * vx + (cy - py_) * vy < 0:
            vx, vy = -vx, -vy
        out.append((px_ + vx * d, py_ + vy * d))
    return out


RING = None
POP = {}


def arterials():
    global RING
    if RING is None:
        RING = offset_inward(G.MAIN_BASE, G.RING_INSET)
    out = [RING + [RING[0]]]
    out += [pts for _name, pts in G.ARTERIALS]
    return out


def locals_():
    out = []
    for z in G.ZONES:
        if z[0] == "farm":
            continue
        blk = BLOCK_M.get(z[0], 168.0)
        for (x0, y0, x1, y1) in z[1]:
            v = math.ceil(x0 / blk) * blk
            while v < x1:
                out.append([(v, y0), (v, y1)])
                v += blk
            v = math.ceil(y0 / blk) * blk
            while v < y1:
                out.append([(x0, v), (x1, v)])
                v += blk
    return out


def land_fraction(gx, gy, n=12):
    x0, y0 = -ORIGIN + gx * DISTRICT, -ORIGIN + gy * DISTRICT
    c = sum(1 for i in range(n) for j in range(n)
            if G.on_land(x0 + (i + .5) * DISTRICT / n, y0 + (j + .5) * DISTRICT / n))
    return c / (n * n)


def land_area_km2(N=300000):
    rng = random.Random(3)
    hit = sum(1 for _ in range(N)
              if G.on_land(rng.uniform(-ORIGIN, ORIGIN), rng.uniform(-ORIGIN, ORIGIN)))
    return hit / N * (WORLD / 1000.0) ** 2


# ------------------------------------------------------------------- terrain bands
def terrain_paths():
    out, plain = [], 0
    for spec, rot in ((G.MASSIF, 0.0), (G.SPUR, G.SPUR.get("rot", 0.0))):
        cx, cy = spec["cx"], spec["cy"]
        for (rx, ry, z, tag) in spec["bands"]:
            pts = G.pull_ashore(cx, cy, G.ellipse(cx, cy, rx, ry, 44, rot))
            col = BAND_COL.get(tag)
            if col is None:
                col = BAND_PLAIN[min(plain, len(BAND_PLAIN) - 1)]
                plain += 1
            out.append((pts, "+%d%s" % (round(z), (" " + tag) if tag else ""), tag, col))
    return out


#: Keyed by the band's TAG, not by its label text — a label carries the elevation, and the
#: elevation moves with `G.SCALE`. Untagged bands step through `BAND_PLAIN` outermost first.
BAND_COL = {"snow": "#DCE6D4", "peak": "#F1F4EE", "hill": "#AAC191"}
BAND_PLAIN = ["#BDCDA4", "#A6BE8B", "#C3D2AA"]


# ============================================================ PLATE 1 — modeling
def modeling_plate():
    S, MX, MY = 0.44, 58.0, 54.0
    px = mk(S, MX, MY)
    MW = WORLD * S
    W = int(MW + MX * 2)
    H = int(MW + MY + 244)
    o = []
    a = o.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
      f'viewBox="0 0 {W} {H}" font-family="sans-serif">')
    a('<style>.t{font:700 12px sans-serif;fill:#23231f}.s{font:400 9px sans-serif;'
      'fill:#6a685e}.r{font:600 8.5px sans-serif}.hero{font:700 9px sans-serif;'
      'fill:#B23A18}.z{font:700 12.5px sans-serif;fill:#23231f}.zs{font:400 8.5px '
      'sans-serif;fill:#5f5d54}.g{font:600 8px sans-serif;fill:#7d7a6c}'
      '.w{font:500 9px sans-serif;fill:#3f6379}</style>')
    a(f'<rect width="{W}" height="{H}" fill="#EBE6D8"/>')
    a(f'<rect x="{MX:.0f}" y="{MY:.0f}" width="{MW:.0f}" height="{MW:.0f}" fill="#9EB8C6"/>')

    for i in range(21):
        v = -ORIGIN + i * 100.8
        x, _ = px(v, 0)
        _, y = px(0, v)
        a(f'<line x1="{x:.1f}" y1="{MY}" x2="{x:.1f}" y2="{MY+MW:.0f}" stroke="#0000000e"/>')
        a(f'<line x1="{MX}" y1="{y:.1f}" x2="{MX+MW:.0f}" y2="{y:.1f}" stroke="#0000000e"/>')

    for isl in G.ISLETS:
        a(f'<path d="{path(px, isl)}" fill="#DCD6C3" stroke="#5D7E90" stroke-width="1.1"/>')
    a(f'<path d="{path(px, G.MAIN)}" fill="#E5E0CF" stroke="#5D7E90" stroke-width="1.4"/>')
    for p in (G.HARBOUR, G.AIRPORT):
        a(f'<path d="{path(px, p)}" fill="#E0DAC7" stroke="#5D7E90" stroke-width="1.5"/>')

    for pts, lab, tag, col in terrain_paths():
        a(f'<path d="{path(px, pts)}" fill="{col}" fill-opacity="0.95" '
          f'stroke="#798a64" stroke-width="0.8"/>')

    # paddy wash
    for (cx, cy, rx, ry) in ((G._s(180), G._s(590), G._s(460), G._s(190)),
                             (G._s(740), G._s(290), G._s(160), G._s(240)),
                             (G._s(-430), G._s(615), G._s(200), G._s(120))):
        pts = G.pull_ashore(cx, cy, G.ellipse(cx, cy, rx, ry, 40))
        a(f'<path d="{path(px, pts)}" fill="#CBD8AE" fill-opacity="0.55"/>')

    # dune-ridge / back-swamp striping (Echigo-plain pattern): built ridges parallel to
    # the shore, paddy in the troughs between them
    for k in range(9):
        t = k / 8.0
        band = [(600 - 300 * t + 40 * math.sin(t * 3), 120 + 470 * t),
                (880 - 300 * t, 60 + 470 * t)]
        for run in clipped(band, 10):
            a(f'<path d="{path(px, run, False)}" fill="none" stroke="#B7C79A" '
              f'stroke-width="2.4" stroke-opacity="0.75"/>')
    a(f'<path d="{path(px, G.pull_ashore(G._s(560), G._s(430), G.PINE))}" fill="#6F8A5E" '
      f'fill-opacity="0.9" stroke="#4f6543" stroke-width="0.8"/>')
    a(f'<path d="{path(px, G.LAGOON)}" fill="#7FA3B8" stroke="#5D7E90" stroke-width="1"/>')
    for p, col in ((G.MOAT, "#7FA3B8"), (G.CASTLE, "#B4C79A"), (G.SHIBA_PK, "#A6BD8A"),
                   (G.SHRINE_PK, "#9CB681")):
        a(f'<path d="{path(px, p)}" fill="{col}" stroke="#7d8a6b" stroke-width="0.8"/>')

    a(f'<path d="{path(px, G.BAY)}" fill="#9EB8C6" stroke="#5D7E90" stroke-width="1.2"/>')
    a(f'<path d="{path(px, G.RIVER, False)}" fill="none" stroke="#7FA3B8" '
      f'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')

    total, per = 0, {}
    global POP
    POP = dict(zakkyo=0, anchor=0, danchi=0, detached=0, farmhouse=0, shed=0, hangar=0)
    for z in G.ZONES:
        out = scatter(z)
        per[z[0]] = len(out)
        total += len(out)
        if z[0].startswith("neon"):
            POP["anchor"] += sum(1 for b in out if b[4] == "#7d7768")
            POP["zakkyo"] += sum(1 for b in out if b[4] != "#7d7768")
        elif z[0] == "resid":
            POP["danchi"] += sum(1 for b in out if b[5])
            POP["detached"] += sum(1 for b in out if not b[5])
        elif z[0] == "farm":
            POP["farmhouse"] += len(out)
        elif z[0] == "port":
            POP["shed"] += len(out)
        else:
            POP["hangar"] += len(out)
        for (x, y, w, h, col, rot) in out:
            bx, by = px(x, y + h)
            tr = (f' transform="rotate({-rot:.1f} {bx:.1f} {by+h*S:.1f})"' if rot else "")
            a(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{max(w*S,0.8):.1f}" '
              f'height="{max(h*S,0.8):.1f}" fill="{col}" fill-opacity="0.92"{tr}/>')

    for ln in locals_():
        for run in clipped(ln, 12):
            a(f'<path d="{path(px, run, False)}" fill="none" stroke="#F1EDE1" '
              f'stroke-width="{14*S:.1f}" stroke-opacity="0.8"/>')
    for ln in arterials():
        for run in clipped(ln, 10):
            a(f'<path d="{path(px, run, False)}" fill="none" stroke="#FCFAF3" '
              f'stroke-width="{27*S:.1f}" stroke-linecap="round"/>')

    for pl, closed in ((G.RAIL_MAIN, False), (G.RAIL_BRANCH, False),
                       (G.RAIL_AIRPORT, False)):
        a(f'<path d="{path(px, pl, False)}" fill="none" stroke="#38614F" '
          f'stroke-width="1.7" stroke-dasharray="8 4" stroke-linejoin="round"/>')

    for pl, closed in ((G.LOOP, True), (G.AIRPORT_RAMP, False), (G.AIRPORT_ROAD, False),
                       (G.WESTRAD, False), (G.PORTSPUR, False)):
        q = pl + [pl[0]] if closed else pl
        a(f'<path d="{path(px, q, False)}" fill="none" stroke="#8a5320" '
          f'stroke-width="{22*S+2.4:.1f}" stroke-linejoin="round" stroke-linecap="round" '
          f'stroke-opacity="0.5"/>')
        a(f'<path d="{path(px, q, False)}" fill="none" stroke="#EE9B4E" '
          f'stroke-width="{22*S:.1f}" stroke-linejoin="round" stroke-linecap="round"/>')

    for w, c in ((5.0, "#6f6a58"), (3.0, "#FCFAF3")):
        a(f'<path d="{path(px, G.TOUGE, False)}" fill="none" stroke="{c}" '
          f'stroke-width="{w}" stroke-linejoin="round" stroke-linecap="round"/>')

    for (p0, p1), lab, dy in (
            (G.BAY_BRIDGE, "bay bridge · {L:.0f} m · main crossing", -10),
            (G.ARCH_BRIDGE, "arch bridge · {L:.0f} m · old town", -12),
            (G.AIRPORT_BRIDGE, "airport bridge · road+rail · {L:.0f} m", 4)):
        lab = lab.format(L=math.dist(p0, p1))
        x0, y0 = px(*p0)
        x1, y1 = px(*p1)
        a(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
          f'stroke="#B23A18" stroke-width="5" stroke-linecap="round"/>')
        mxp, myp = (x0 + x1) / 2, (y0 + y1) / 2
        a(f'<circle cx="{mxp:.1f}" cy="{myp:.1f}" r="4.2" fill="#EBE6D8" '
          f'stroke="#B23A18" stroke-width="2"/>')
        a(f'<text class="hero" x="{mxp+9:.0f}" y="{myp+dy:.0f}">{lab}</text>')

    x0, y0 = px(*G.RUNWAY[0])
    x1, y1 = px(*G.RUNWAY[1])
    a(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
      f'stroke="#F5F2E8" stroke-width="{45*S:.1f}"/>')
    a(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
      f'stroke="#C9A24A" stroke-width="1" stroke-dasharray="7 7"/>')
    a(f'<text class="s" x="{(x0+x1)/2:.0f}" y="{(y0+y1)/2+17:.0f}" '
      f'text-anchor="middle">runway 520 x 45 m · drag strip</text>')

    for i in range(GRID_N + 1):
        v = -ORIGIN + i * DISTRICT
        x, _ = px(v, 0)
        _, y = px(0, v)
        a(f'<line x1="{x:.1f}" y1="{MY}" x2="{x:.1f}" y2="{MY+MW:.0f}" stroke="#00000030"/>')
        a(f'<line x1="{MX}" y1="{y:.1f}" x2="{MX+MW:.0f}" y2="{y:.1f}" stroke="#00000030"/>')

    for (lab, sub, x, y) in (
            ("MOUNTAIN", "peak +380 · pass +220 · touge", -300, 905),
            ("FARMLAND", "paddies · Z +45", 250, 700),
            ("PADDY TO THE SEA", "dune ridges · troughs · pine belt", 748, 500),
            ("RESIDENTIAL", "danchi + detached · farmhouses blend north", -320, 350),
            ("NEON A", "main core · old town, west bank", -35, -215),
            ("NEON B", "electric town · east bank", 500, -105),
            ("NEON C", "", -552, -108),
            ("HARBOUR", "reclaimed port", -120, -965),
            ("AIRPORT", "", 690, -660)):
        sx, sy = px(x, y)
        a(f'<text class="z" x="{sx:.0f}" y="{sy:.0f}" text-anchor="middle">{lab}</text>')
        if sub:
            a(f'<text class="zs" x="{sx:.0f}" y="{sy+11:.0f}" text-anchor="middle">{sub}</text>')
    for (lab, x, y) in (("BAY", 230, -760), ("OPEN SEA", -840, -830)):
        sx, sy = px(x, y)
        a(f'<text class="w" x="{sx:.0f}" y="{sy:.0f}" text-anchor="middle" '
          f'letter-spacing="2">{lab}</text>')

    for (lab, x, y, kind) in G.LANDMARKS:
        if 'bridge' in lab.lower():
            continue
        sx, sy = px(x, y)
        col = {"asset": "#B23A18", "kit": "#7A5A2E"}.get(kind, "#43413a")
        a(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="3.6" fill="{col}" stroke="#EBE6D8" '
          f'stroke-width="1"/>')
        anc = "end" if x < -200 else "start"
        dx = -8 if anc == "end" else 8
        a(f'<text class="r" x="{sx+dx:.0f}" y="{sy+3:.0f}" text-anchor="{anc}" '
          f'fill="{col}">{lab}</text>')

    nx, ny = MX + MW - 24, MY + 32
    a(f'<path d="M{nx} {ny-20} L{nx+4.5} {ny-10} L{nx-4.5} {ny-10} Z" fill="#23231f"/>')
    a(f'<text class="s" x="{nx}" y="{ny+2}" text-anchor="middle">N</text>')
    by = MY + MW + 24
    a(f'<line x1="{MX}" y1="{by}" x2="{MX+504*S:.1f}" y2="{by}" stroke="#23231f" '
      f'stroke-width="2"/>')
    a(f'<text class="s" x="{MX+504*S+9:.0f}" y="{by+4:.0f}">504 m = 1 district '
      f'(minor grid 100.8 m)</text>')

    la = land_area_km2()
    lines = [
        ("t", f"World {WORLD:.0f} x {WORLD:.0f} m · {GRID_N} x {GRID_N} districts of "
              f"{DISTRICT:.0f} m · centre origin [-{ORIGIN:.0f},+{ORIGIN:.0f}] — "
              f"island_v3_geom.SCALE {G.SCALE:g}, one constant"),
        ("s", f"Land {la:.2f} km² (v1 proposed 2.4 · GTA III 4.38) · buildings on this "
              f"plate: {total} ({per['neonA']}+{per['neonB']}+{per['neonC']} neon · "
              f"{per['resid']} resid · {per['farm']} farm · {per['port']} port)"),
        ("s", "Transect south to north: harbour/bay, three neon centres split by the "
              "river, residential, farmland (one arm to the sea, one climbing the "
              "flank), mountain"),
        ("s", "Roads T1 expressway 22 m elevated (orange) · T2 arterial 27 m on the 504 m "
              "seams + 2 mid-band lines · T3 local 14 m @168 m · T4 alley 4.5 m (generated)"),
        ("s", "Red = hero asset · brown = kit + Geometry Nodes · the castle replaces v2's "
              "Imperial Palace at 260 x 210 m and still shapes every road around it"),
        ("s", "Z: sea 0 · port +2 · neon +3..6 · resid +10..30 · valley +45 · flank "
              "+80..160 · spur hill +140 · pass +220 · peak +380 (snow +300)"),
    ]
    for i, (cls, txt) in enumerate(lines):
        a(f'<text class="{cls}" x="{MX}" y="{MY+MW+52+i*15.5:.0f}">{txt}</text>')
    a('</svg>')
    return "\n".join(o), total, per, la


# ============================================================ PLATE 2 — overview
def overview_plate(total, la):
    S, MX, MY = 0.46, 54.0, 78.0
    px = mk(S, MX, MY)
    MW = WORLD * S
    LEGW = 320.0
    W = int(MX * 2 + MW + LEGW)
    H = int(MY + MW + 90)
    o = []
    a = o.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
      f'viewBox="0 0 {W} {H}" font-family="sans-serif">')
    a('<style>.h{font:700 17px sans-serif;fill:#1e1e1a}.sub{font:400 10.5px sans-serif;'
      'fill:#67655c}.lh{font:700 10px sans-serif;fill:#1e1e1a;letter-spacing:.6px}'
      '.li{font:400 9.5px sans-serif;fill:#3a382f}.lm{font:600 9.5px sans-serif;'
      'fill:#3a382f}.cell{font:600 8px sans-serif;fill:#5c5949}.pin{font:700 8.5px '
      'sans-serif;fill:#fff}.sea{font:500 9.5px sans-serif;fill:#42687e;letter-spacing:2px}'
      '.zt{font:700 11px sans-serif;fill:#23231f}</style>')
    a(f'<rect width="{W}" height="{H}" fill="#F1EDE1"/>')
    a(f'<text class="h" x="{MX}" y="34">Tokyo-Bay Island v3 — plan overview</text>')
    a(f'<text class="sub" x="{MX}" y="52">{WORLD:.0f} x {WORLD:.0f} m · {GRID_N} x {GRID_N} districts of {DISTRICT:.0f} m · '
      f'Plan A&#8217;s fictional island, condensed. Transect south to north: '
      f'harbour, neon x3 split by the river, residential, farmland, mountain. '
      f'Coast pattern after Niigata / the Echigo plain.</text>')
    a(f'<rect x="{MX:.0f}" y="{MY:.0f}" width="{MW:.0f}" height="{MW:.0f}" fill="#9EB8C6"/>')

    for isl in G.ISLETS:
        a(f'<path d="{path(px, isl)}" fill="#DCD6C3" stroke="#5D7E90" stroke-width="1.1"/>')
    a('<defs><clipPath id="ld">')
    for p in G.LAND:
        a(f'<path d="{path(px, p)}"/>')
    a('</clipPath></defs>')
    a('<g clip-path="url(#ld)">')
    a(f'<rect x="{MX:.0f}" y="{MY:.0f}" width="{MW:.0f}" height="{MW:.0f}" fill="#E5E0CF"/>')
    for gy in range(GRID_N):
        for gx in range(GRID_N):
            key = G.theme_at(gx, gy)
            col = G.THEME[key.lower()][0]
            if not col:
                continue
            x0, y0 = px(-ORIGIN + gx * DISTRICT, -ORIGIN + (gy + 1) * DISTRICT)
            a(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{DISTRICT*S:.1f}" '
              f'height="{DISTRICT*S:.1f}" fill="{col}"/>')
    a('</g>')
    a(f'<path d="{path(px, G.BAY)}" fill="#9EB8C6" stroke="#5D7E90" stroke-width="1.2"/>')
    for p in G.LAND:
        a(f'<path d="{path(px, p)}" fill="none" stroke="#4E7183" stroke-width="1.8"/>')

    for pts, lab, tag, col in terrain_paths():
        if tag:                        # only the named bands — snow, peak, hill
            a(f'<path d="{path(px, pts)}" fill="{col}" fill-opacity="0.85" '
              f'stroke="#8fa07c" stroke-width="0.8"/>')

    a(f'<path d="{path(px, G.pull_ashore(G._s(560), G._s(430), G.PINE))}" fill="#6F8A5E" '
      f'fill-opacity="0.9"/>')
    a(f'<path d="{path(px, G.LAGOON)}" fill="#7FA3B8" stroke="#5D7E90" stroke-width="1"/>')
    a(f'<path d="{path(px, G.MOAT)}" fill="#7FA3B8"/>')
    a(f'<path d="{path(px, G.CASTLE)}" fill="#A8BE8C" stroke="#7d8a6b" stroke-width="0.8"/>')
    a(f'<path d="{path(px, G.RIVER, False)}" fill="none" stroke="#7FA3B8" '
      f'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')

    for i in range(GRID_N + 1):
        v = -ORIGIN + i * DISTRICT
        x, _ = px(v, 0)
        _, y = px(0, v)
        a(f'<line x1="{x:.1f}" y1="{MY}" x2="{x:.1f}" y2="{MY+MW:.0f}" stroke="#00000030"/>')
        a(f'<line x1="{MX}" y1="{y:.1f}" x2="{MX+MW:.0f}" y2="{y:.1f}" stroke="#00000030"/>')
    for gy in range(GRID_N):
        for gx in range(GRID_N):
            key = G.theme_at(gx, gy)
            sx, sy = px(-ORIGIN + (gx + .5) * DISTRICT, -ORIGIN + (gy + .07) * DISTRICT)
            a(f'<text class="cell" x="{sx:.0f}" y="{sy:.0f}" text-anchor="middle" '
              f'opacity="{0.85 if key.isupper() else 0.55}">{gx}{gy} {key} '
              f'[{land_fraction(gx,gy)*100:.0f}]</text>')

    for ln in arterials():
        for run in clipped(ln, 12):
            a(f'<path d="{path(px, run, False)}" fill="none" stroke="#FBF8EF" '
              f'stroke-width="3.6" stroke-linecap="round"/>')
    for pl in (G.RAIL_MAIN, G.RAIL_BRANCH, G.RAIL_AIRPORT):
        a(f'<path d="{path(px, pl, False)}" fill="none" stroke="#33604C" '
          f'stroke-width="1.7" stroke-dasharray="8 4" stroke-linejoin="round"/>')
    for pl, closed in ((G.LOOP, True), (G.AIRPORT_RAMP, False), (G.AIRPORT_ROAD, False),
                       (G.WESTRAD, False), (G.PORTSPUR, False)):
        q = pl + [pl[0]] if closed else pl
        a(f'<path d="{path(px, q, False)}" fill="none" stroke="#8a5320" stroke-width="10" '
          f'stroke-linejoin="round" stroke-linecap="round" stroke-opacity="0.45"/>')
        a(f'<path d="{path(px, q, False)}" fill="none" stroke="#EE9B4E" stroke-width="7.6" '
          f'stroke-linejoin="round" stroke-linecap="round"/>')
    for w, c in ((5.0, "#6f6a58"), (3.0, "#FBF8EF")):
        a(f'<path d="{path(px, G.TOUGE, False)}" fill="none" stroke="{c}" '
          f'stroke-width="{w}" stroke-linejoin="round" stroke-linecap="round"/>')
    for (p0, p1) in (G.BAY_BRIDGE, G.ARCH_BRIDGE, G.AIRPORT_BRIDGE):
        x0, y0 = px(*p0)
        x1, y1 = px(*p1)
        a(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
          f'stroke="#B23A18" stroke-width="5.6" stroke-linecap="round"/>')
    x0, y0 = px(*G.RUNWAY[0])
    x1, y1 = px(*G.RUNWAY[1])
    a(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
      f'stroke="#F7F4EA" stroke-width="{45*S:.1f}"/>')

    for (lab, x, y) in (("NEON A", -35, -210), ("NEON B", 500, -110), ("NEON C", -550, -110),
                        ("PADDY TO THE SEA", 762, 452)):
        sx, sy = px(x, y)
        a(f'<text class="zt" x="{sx:.0f}" y="{sy:.0f}" text-anchor="middle">{lab}</text>')

    for i, (lab, x, y, note) in enumerate(G.SECTORS):
        sx, sy = px(x, y)
        a(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="8.4" fill="#B23A18" stroke="#fff" '
          f'stroke-width="1.4"/>')
        a(f'<text class="pin" x="{sx:.1f}" y="{sy+3:.1f}" text-anchor="middle">{lab}</text>')
    for i, (lab, x, y, kind) in enumerate(G.LANDMARKS, 1):
        sx, sy = px(x, y)
        col = {"asset": "#7A1F0B", "kit": "#6B4A1E"}.get(kind, "#3f3d36")
        a(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="8.4" fill="{col}" stroke="#fff" '
          f'stroke-width="1.4"/>')
        a(f'<text class="pin" x="{sx:.1f}" y="{sy+3:.1f}" text-anchor="middle">{i}</text>')
    for (lab, x, y) in (("BAY", 225, -760), ("OPEN SEA", -830, -840)):
        sx, sy = px(x, y)
        a(f'<text class="sea" x="{sx:.0f}" y="{sy:.0f}" text-anchor="middle">{lab}</text>')

    nx, ny = MX + MW - 22, MY + 30
    a(f'<path d="M{nx} {ny-19} L{nx+4.5} {ny-9} L{nx-4.5} {ny-9} Z" fill="#23231f"/>')
    a(f'<text class="sub" x="{nx}" y="{ny+3}" text-anchor="middle">N</text>')
    by = MY + MW + 22
    a(f'<line x1="{MX}" y1="{by}" x2="{MX+504*S:.1f}" y2="{by}" stroke="#23231f" '
      f'stroke-width="2"/>')
    a(f'<text class="sub" x="{MX+504*S+9:.0f}" y="{by+4:.0f}">504 m = 1 district</text>')
    a(f'<text class="sub" x="{MX}" y="{by+22:.0f}">Detail plate: '
      f'tokyo-bay-island-modeling-plate-v3.svg — cell tag is [land %].</text>')

    lx = MX + MW + 26
    y = MY + 6

    def head(t, y):
        o.append(f'<text class="lh" x="{lx}" y="{y}">{t.upper()}</text>')
        o.append(f'<line x1="{lx}" y1="{y+5}" x2="{lx+LEGW-40}" y2="{y+5}" '
                 f'stroke="#00000022"/>')
        return y + 19

    y = head("Transect — south to north", y)
    for (k, note) in (("Harbour / bay / airport", "reclaimed port, 2 bridges"),
                      ("Neon ×3", "A main · B electric · C hillside"),
                      ("Residential", "danchi one bearing + detached"),
                      ("Farmland", "dune ridges + paddy troughs to the sea"),
                      ("Mountain + spur", "touge to the pass, hill behind Neon C")):
        a(f'<text class="lm" x="{lx}" y="{y}">{k}</text>')
        a(f'<text class="li" x="{lx+LEGW-40}" y="{y}" text-anchor="end">{note}</text>')
        y += 15
    y += 12

    y = head("District themes — 16 cells", y)
    for key in ("mtn", "rural", "resid", "city", "harbor"):
        col, name = G.THEME[key]
        a(f'<rect x="{lx}" y="{y-8}" width="13" height="11" fill="{col}" '
          f'stroke="#00000030"/>')
        a(f'<text class="lm" x="{lx+20}" y="{y+1}">{name}</text>')
        y += 16
    a(f'<text class="li" x="{lx}" y="{y+3}">Land {la:.2f} km² — v1 proposed 2.4 km².</text>')
    y += 26

    y = head(f"Race circuit — lap {G.plen(G.LOOP, True):.0f} m", y)
    for (lab, _x, _y, note) in G.SECTORS:
        a(f'<circle cx="{lx+7}" cy="{y-3}" r="7" fill="#B23A18"/>')
        a(f'<text class="pin" x="{lx+7}" y="{y}" text-anchor="middle">{lab}</text>')
        a(f'<text class="li" x="{lx+21}" y="{y+1}">{note}</text>')
        y += 15
    a(f'<text class="li" x="{lx}" y="{y+4}">Long lap via the airport bridge · 520 m drag '
      f'strip ·</text>')
    a(f'<text class="li" x="{lx}" y="{y+16}">hillclimb to the pass (+220 m).</text>')
    y += 36

    y = head("Landmarks", y)
    for i, (lab, _x, _y, kind) in enumerate(G.LANDMARKS, 1):
        col = {"asset": "#7A1F0B", "kit": "#6B4A1E"}.get(kind, "#3f3d36")
        a(f'<circle cx="{lx+7}" cy="{y-3}" r="7" fill="{col}"/>')
        a(f'<text class="pin" x="{lx+7}" y="{y}" text-anchor="middle">{i}</text>')
        a(f'<text class="li" x="{lx+21}" y="{y+1}">{lab}</text>')
        y += 14.6
    y += 8
    y = head("Building population", y)
    a(f'<text class="li" x="{lx}" y="{y-2}" opacity="0.8">type</text>')
    a(f'<text class="li" x="{lx+150}" y="{y-2}" opacity="0.8">footprint</text>')
    a(f'<text class="li" x="{lx+218}" y="{y-2}" opacity="0.8">floors</text>')
    a(f'<text class="li" x="{lx+LEGW-40}" y="{y-2}" text-anchor="end" '
      f'opacity="0.8">count</text>')
    a(f'<line x1="{lx}" y1="{y+2}" x2="{lx+LEGW-40}" y2="{y+2}" stroke="#00000018"/>')
    y += 15
    rows = [("Zakkyo shop-office", "6 x 12", "3-8", POP["zakkyo"]),
            ("Anchor: store / office", "24 x 34", "6-12", POP["anchor"]),
            ("Danchi slab", "12 x 55", "5", POP["danchi"]),
            ("Detached house", "7 x 9", "2", POP["detached"]),
            ("Farmhouse + barn", "12 x 8", "1-2", POP["farmhouse"]),
            ("Shed / tank", "40 x 70", "1", POP["shed"]),
            ("Hangar / terminal", "26 x 34", "1-2", POP["hangar"])]
    for (k, fp, fl, n) in rows:
        a(f'<text class="lm" x="{lx}" y="{y}">{k}</text>')
        a(f'<text class="li" x="{lx+150}" y="{y}">{fp}</text>')
        a(f'<text class="li" x="{lx+218}" y="{y}">{fl}</text>')
        a(f'<text class="lm" x="{lx+LEGW-40}" y="{y}" text-anchor="end">{n}</text>')
        y += 14.6
    a(f'<line x1="{lx}" y1="{y-9}" x2="{lx+LEGW-40}" y2="{y-9}" stroke="#00000018"/>')
    a(f'<text class="lm" x="{lx}" y="{y+3}">total instances</text>')
    a(f'<text class="lm" x="{lx+LEGW-40}" y="{y+3}" text-anchor="end">{total}</text>')
    y += 18
    a(f'<text class="li" x="{lx}" y="{y}">from ~44 unique kit meshes + ~52 hero '
      f'meshes.</text>')
    a(f'<text class="li" x="{lx}" y="{y+12}">Danchi all share one 18&#176; bearing '
      f'(see plate).</text>')
    a('</svg>')
    return "\n".join(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(_here))
    args = ap.parse_args()
    svg, total, per, la = modeling_plate()
    p1 = os.path.join(args.root, "tokyo-bay-island-modeling-plate-v3.svg")
    p2 = os.path.join(args.root, "tokyo-bay-island-overview-v3.svg")
    open(p1, "w").write(svg)
    open(p2, "w").write(overview_plate(total, la))
    print(f"land {la:.2f} km²   buildings {total}  {per}")
    print(f"lap {G.plen(G.LOOP, True):.0f} m · airport ramp "
          f"{G.plen(G.AIRPORT_RAMP):.0f} m · touge {G.plen(G.TOUGE):.0f} m · "
          f"rail {G.plen(G.RAIL_MAIN):.0f} m")
    print(f"ring {G.plen(RING, True):.0f} m · arterials " +
          ", ".join(f"{n} {G.plen(pts):.0f}" for n, pts in G.ARTERIALS))
    print("population", POP)
    print("land fraction per cell (gy%d top):" % (GRID_N - 1))
    for gy in range(GRID_N - 1, -1, -1):
        print("  " + "  ".join(f"{land_fraction(gx,gy)*100:3.0f}%" for gx in range(GRID_N)))
    print(f"wrote {os.path.basename(p1)}, {os.path.basename(p2)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""island_v3_reach.py -- HOW MUCH OF THE ISLAND CAN YOU ACTUALLY DRIVE TO?

    python3 tools/island_v3_reach.py            # self-tests + the reach table, pure Python

THE GATE FOR `W2` (WORLD_REBUILD_PLAN.md "The network is 19% shorter than the plan"). Step 1 gave
the island real terrain and four arterials were rerouted off the relief to stay grade-legal. That
was right per road and it cost the world its whole northwest: the number quoted at the time was
**land within 250 m of an arterial, 90.5% -> 70.0%**, and nothing computed it -- it was worked out
by hand, once, and could not be re-checked after a change.

WHAT IT MEASURES

    reach      % of land within `RADIUS` of any road in the network
    orphan     the largest connected patch of land that is NOT, and where its centre is
    length     total network length, so a reach win that is really just more tarmac shows up

WHY A DISTANCE FIELD AND NOT A CORRIDOR TEST. "Served" is not "inside a corridor": a house 200 m
off a road is served by it (you drive to the end and walk), and a road on the far side of a bay is
not, however close. Distance is measured in the plane and the sample must be ON LAND, which makes
water a natural barrier without a single line about water: the far bank of the bay is 300 m from
Hama-dori across the water and the samples in between simply do not exist.

The `RADIUS` is the plan's own 250 m, which is roughly a 3 minute walk and about one city block
here. It is a coverage yardstick, not a design rule.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import island_v3_geom as G                                                   # noqa: E402
import island_v3_terrain as IT                                               # noqa: E402

#: How far from a road counts as served, metres. The plan's own number.
RADIUS = 250.0

#: Land sampling pitch, metres. 25 m puts ~5 000 samples on this island -- fine enough that a
#: missing 250 m corridor cannot hide in it, coarse enough to stay a sub-second self-test.
SAMPLE = 25.0

#: Road sampling pitch when a polyline is turned into points, metres. Well under `RADIUS` so a
#: long straight leg cannot read as two endpoints with a gap between them.
ROAD_SAMPLE = 40.0


def densify(pts, step):
    out = []
    for a, b in zip(pts, pts[1:]):
        d = math.dist(a, b)
        n = max(1, int(d / step))
        for k in range(n):
            t = k / float(n)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    if pts:
        out.append(pts[-1])
    return out


def network_points(roads, step=ROAD_SAMPLE):
    pts = []
    for _name, poly in roads:
        pts.extend(densify(poly, step))
    return pts


def total_length(roads):
    return sum(math.dist(a, b) for _n, poly in roads for a, b in zip(poly, poly[1:]))


def _bucket(pts, cell):
    grid = {}
    for (x, y) in pts:
        grid.setdefault((int(math.floor(x / cell)), int(math.floor(y / cell))), []).append((x, y))
    return grid


def reach(roads, radius=RADIUS, sample=SAMPLE):
    """`(served, total, unserved_points)` over land samples.

    Bucketed by `radius` so this is O(samples), not O(samples x road points): only the 9 cells
    around a sample can hold a road point within `radius` of it. Without that, 5 000 samples
    against 400 road points is 2 million distance tests every time anyone asks.
    """
    pts = network_points(roads)
    grid = _bucket(pts, radius)
    served, total, missed = 0, 0, []
    n = int(math.ceil(G.WORLD / sample))
    for i in range(n + 1):
        x = -G.ORIGIN + i * sample
        for j in range(n + 1):
            y = -G.ORIGIN + j * sample
            if not G.on_land(x, y):
                continue
            total += 1
            ci, cj = int(math.floor(x / radius)), int(math.floor(y / radius))
            hit = False
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for (px, py) in grid.get((ci + di, cj + dj), ()):
                        if (px - x) ** 2 + (py - y) ** 2 <= radius * radius:
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                served += 1
            else:
                missed.append((x, y))
    return served, total, missed


def patches(points, sample=SAMPLE):
    """Unserved samples grouped into connected patches (8-neighbour), largest first.

    A percentage alone cannot tell "every road is 260 m from its neighbour" from "one whole
    headland has no road at all", and those are opposite problems. The patch list is the half of
    the answer that says WHERE."""
    have = {(round(x / sample), round(y / sample)) for (x, y) in points}
    seen, out = set(), []
    for cell in have:
        if cell in seen:
            continue
        stack, comp = [cell], []
        seen.add(cell)
        while stack:
            (i, j) = stack.pop()
            comp.append((i, j))
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    nb = (i + di, j + dj)
                    if nb in have and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        cx = sum(c[0] for c in comp) / len(comp) * sample
        cy = sum(c[1] for c in comp) / len(comp) * sample
        out.append((len(comp), (cx, cy), comp))
    out.sort(key=lambda p: -p[0])
    return out


def report(roads=None, radius=RADIUS, sample=SAMPLE, top=6):
    # `IT.road_network()`, not `G.ARTERIALS`: the hill roads are derived and are exactly the ones
    # this gate exists to score. Measuring the authored half alone would have called the shrine
    # road's 0.36 km^2 plateau unserved on the day it was built.
    roads = roads if roads is not None else IT.road_network()
    served, total, missed = reach(roads, radius, sample)
    length = total_length(roads)
    area = sample * sample / 1e6
    print("network: %d road(s), %.0f m total" % (len(roads), length))
    print("reach:   %d of %d land samples within %.0f m  ->  %.1f%%   (%.2f km^2 unserved)"
          % (served, total, radius, 100.0 * served / max(1, total), len(missed) * area))
    if missed:
        print("\n%-6s %10s  %s" % ("patch", "area km^2", "centre"))
        for k, (n, (cx, cy), _c) in enumerate(patches(missed, sample)[:top]):
            print("%-6d %10.2f  (%+6.0f, %+6.0f)" % (k, n * area, cx, cy))
    return 100.0 * served / max(1, total)


def _selftest():
    # a single road serves a band `radius` wide either side of it and nothing beyond
    line = [("probe", [(-G.ORIGIN, 0.0), (G.ORIGIN, 0.0)])]
    s1, t1, _m = reach(line, radius=100.0, sample=100.0)
    s2, _t2, _m = reach(line, radius=400.0, sample=100.0)
    assert 0 < s1 < s2 <= t1, (s1, s2, t1)
    # ...and the whole network beats any one of its own roads
    net = IT.road_network()
    whole = report_value(net)
    for name, poly in net:
        assert report_value([(name, poly)]) <= whole + 1e-9, name
    # densify never drops the ends
    d = densify([(0.0, 0.0), (100.0, 0.0)], 40.0)
    assert d[0] == (0.0, 0.0) and d[-1] == (100.0, 0.0) and len(d) >= 3
    print("island_v3_reach: self-tests OK")


def report_value(roads, radius=RADIUS, sample=SAMPLE):
    served, total, _m = reach(roads, radius, sample)
    return 100.0 * served / max(1, total)


if __name__ == "__main__":
    _selftest()
    print()
    report()

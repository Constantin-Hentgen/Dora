"""Pure geometry on tracks: distances, offsets, encoded polylines, the "already done" grid, backtracking and
dead-end spurs. No I/O here, so everything is cheap to test.

A point is `(lat, lon, elevation_or_None)`; functions only read the first two values, so plain `(lat, lon)` works.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0
KM_PER_DEG_LAT = 111.2

Point = tuple[float, float, float | None]
LatLon = tuple[float, float]

# ~30 m cells: a GPS track drifts 5-15 m, so one cell plus its neighbours covers "the same path".
GRID_DEG = 0.0003
Cell = tuple[int, int]


def haversine_km(a: Sequence[float], b: Sequence[float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(h))


def offset(lat: float, lon: float, km: float, bearing_deg: float) -> LatLon:
    """The point `km` away from (lat, lon) towards `bearing_deg` (0 = north). Flat-earth approximation: fine for the
    few-kilometre radii of a loop."""
    b = math.radians(bearing_deg)
    dlat = km / KM_PER_DEG_LAT * math.cos(b)
    dlon = km / (KM_PER_DEG_LAT * math.cos(math.radians(lat))) * math.sin(b)
    return lat + dlat, lon + dlon


def path_km(points: Sequence[Sequence[float]]) -> float:
    return sum(haversine_km(p, q) for p, q in itertools.pairwise(points))


def cumulative_km(points: Sequence[Sequence[float]]) -> list[float]:
    cum = [0.0]
    for p, q in itertools.pairwise(points):
        cum.append(cum[-1] + haversine_km(p, q))
    return cum


def bearing_gap(a: float, b: float) -> float:
    """Smallest angle between two bearings, in degrees (0-180)."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


# ---------------------------------------------------------------------------
# Encoded polylines and the "already done" grid
# ---------------------------------------------------------------------------


def decode_polyline(encoded: str, precision: int = 5) -> list[LatLon]:
    """Google encoded polyline (used by Strava, Dreeve and most fitness apps) → [(lat, lon)]."""
    points: list[LatLon] = []
    index = lat = lon = 0
    factor = 10**precision
    text = encoded or ""
    while index < len(text):
        deltas = []
        for __ in range(2):
            shift = result = 0
            while True:
                if index >= len(text):
                    raise ValueError("truncated encoded polyline")
                b = ord(text[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / factor, lon / factor))
    return points


def cell(lat: float, lon: float) -> Cell:
    return round(lat / GRID_DEG), round(lon / GRID_DEG)


def visited_cells(tracks: Iterable[Sequence[LatLon]]) -> set[Cell]:
    """Cells crossed by past tracks, plus their 8 neighbours (GPS drift). Segments are sampled every ~20 m so that
    sparse polylines (one point every 100 m on straight roads) do not leave holes."""
    cells: set[Cell] = set()
    for pts in tracks:
        for (a, b), (c, d) in zip(pts, pts[1:] or pts, strict=False):  # a single point pairs with itself
            steps = max(1, int(haversine_km((a, b), (c, d)) / 0.02))
            for k in range(steps + 1):
                x, y = cell(a + (c - a) * k / steps, b + (d - b) * k / steps)
                cells.update((x + i, y + j) for i in (-1, 0, 1) for j in (-1, 0, 1))
    return cells


def novelty(points: Sequence[Sequence[float]], done: set[Cell]) -> float:
    """Share of the loop's points that fall in cells never visited before (1.0 = all new)."""
    if not points:
        return 0.0
    return sum(cell(p[0], p[1]) not in done for p in points) / len(points)


# ---------------------------------------------------------------------------
# Backtracking and dead-end spurs
# ---------------------------------------------------------------------------


def backtrack(points: Sequence[Sequence[float]]) -> float:
    """Share of points that come back to a cell left long ago (more than 10 points earlier): 0 for a true loop,
    about 0.5 for a pure out-and-back. U-turns and out-and-back sections are what runners hate most in a loop."""
    seen: dict[Cell, int] = {}
    again = 0
    for i, c in enumerate(cell(p[0], p[1]) for p in points):
        if c in seen and i - seen[c] > 10:
            again += 1
        seen.setdefault(c, i)
    return again / max(1, len(points))


@dataclass(frozen=True)
class SpurRules:
    """When a waypoint lands in a cul-de-sac (slip road, courtyard, dead-end), BRouter goes in and comes back the same
    way. On a map the out-and-back overlaps itself and is invisible, but a watch following the GPX sends the runner
    into it. A spur is a detour that:

    - rejoins the track within `join_km` (30 m) of where it left,
    - after `min_km` (60 m) to `max_km` (2 km) of track, and under `max_share` (30 %) of the loop: longer than that
      it is the loop itself, whose start and finish coincide,
    - comes back on the way out: at least `retrace_share` of the return within `retrace_km` (25 m, which absorbs
      separately mapped sidewalks) of the way out. A detour around a block comes back another way and is kept.
    """

    join_km: float = 0.03
    min_km: float = 0.06
    max_km: float = 2.0
    max_share: float = 0.3
    retrace_km: float = 0.025
    retrace_share: float = 0.6
    passes: int = 3  # nested spurs (a dead-end off a dead-end) need a second pass once the outer one is cut


def _retraced(points: Sequence[Sequence[float]], i: int, j: int, rules: SpurRules) -> bool:
    seg = points[i : j + 1]
    tip = max(range(len(seg)), key=lambda k: haversine_km(seg[0], seg[k]))
    out, back = seg[: tip + 1], seg[tip + 1 :]
    if not back:
        return False
    near = sum(any(haversine_km(q, o) <= rules.retrace_km for o in out) for q in back)
    return near / len(back) >= rules.retrace_share


def find_spurs(points: Sequence[Sequence[float]], rules: SpurRules = SpurRules()) -> list[tuple[int, int]]:
    """Index pairs (i, j): the track leaves point i, goes into a dead-end and is back at point j."""
    cum = cumulative_km(points)
    limit = min(rules.max_km, rules.max_share * cum[-1]) if points else 0
    found: list[tuple[int, int]] = []
    i = 0
    while i < len(points):
        hit = None
        for j in range(i + 2, len(points)):
            span = cum[j] - cum[i]
            if span > limit:
                break
            if span >= rules.min_km and haversine_km(points[i], points[j]) <= rules.join_km:
                hit = j  # keep the furthest rejoin: the whole dead-end, not just its tip
        if hit is not None and _retraced(points, i, hit, rules):
            found.append((i, hit))
            i = hit
        else:
            i += 1
    return found


def cut_spurs(points: Sequence[Point], found: Sequence[tuple[int, int]]) -> list[Point]:
    """Drop the points strictly inside each spur: the track passes the mouth of the dead-end without entering it."""
    keep: list[Point] = []
    last = 0
    for i, j in found:
        keep += points[last : i + 1]
        last = j + 1
    return keep + list(points[last:])


def remove_spurs(points: Sequence[Point], rules: SpurRules = SpurRules()) -> tuple[list[Point], int]:
    """Cut spurs locally, with no extra routing call, in up to `rules.passes` passes. Returns the cleaned points and
    the number of spurs still found afterwards (callers must never return a loop where this is not 0)."""
    pts = list(points)
    for __ in range(rules.passes):
        found = find_spurs(pts, rules)
        if not found:
            break
        pts = cut_spurs(pts, found)
    return pts, len(find_spurs(pts, rules))

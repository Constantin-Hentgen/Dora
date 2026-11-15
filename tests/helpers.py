"""Test doubles: a fake BRouter that 'routes' straight along the waypoints, and point builders."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import httpx

from dora_routes.geo import Point, haversine_km, path_km

HEADER = [
    "Longitude", "Latitude", "Elevation", "Distance", "CostPerKm", "ElevCost", "TurnCost", "NodeCost",
    "InitialCost", "WayTags", "NodeTags", "Time", "Energy",
]  # fmt: skip

Row = tuple[float, str, str]  # (metres, WayTags, NodeTags)


def messages(rows: Sequence[Row]) -> list[list[str]]:
    return [HEADER] + [["0", "0", "0", str(d), "0", "0", "0", "0", "0", way, node, "0", "0"] for d, way, node in rows]


def line(lat0: float, lon0: float, dlat: float, dlon: float, n: int) -> list[Point]:
    return [(lat0 + dlat * k, lon0 + dlon * k, None) for k in range(n)]


def straight_route(waypoints: Sequence[tuple[float, float]], step_km: float = 0.04) -> list[Point]:
    """Points every ~40 m along the straight legs between waypoints."""
    pts: list[Point] = []
    for (a, b), (c, d) in itertools.pairwise(waypoints):
        n = max(1, int(haversine_km((a, b), (c, d)) / step_km))
        pts += [(a + (c - a) * k / n, b + (d - b) * k / n, 35.0) for k in range(n)]
    last = waypoints[-1]
    return [*pts, (last[0], last[1], 35.0)]


@dataclass
class FakeBRouter:
    """Answers like BRouter. The reported track length is `factor` × the straight-line length (streets are longer
    than the crow flies), so the generator has to refit its radius. `rows(bearing_index)` gives the message table."""

    factor: float = 1.25
    rows: Callable[[list[tuple[float, float]]], Sequence[Row]] = lambda wps: [(1000, "highway=path", "")]
    status: int = 200
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="rate limited")
        lonlats = request.url.params["lonlats"].split("|")
        wps = [(float(lat), float(lon)) for lon, lat in (p.split(",") for p in lonlats)]
        pts = straight_route(wps)
        km = path_km(pts) * self.factor
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "track-length": str(round(km * 1000)),
                            "filtered ascend": "42",
                            "messages": messages(self.rows(wps)),
                        },
                        "geometry": {"type": "LineString", "coordinates": [[lon, lat, e] for lat, lon, e in pts]},
                    }
                ],
            },
        )

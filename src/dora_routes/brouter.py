"""BRouter HTTP client and parsing of its GeoJSON answer.

BRouter returns, next to the geometry, a `properties.messages` table: one row per stretch of way, with its length
(`Distance`, metres), the OSM tags of the way (`WayTags`, space-separated `key=value`) and of the node where the row
ends (`NodeTags`). That table is what lets us say how much of a loop is on quiet paths, how often it crosses a street
and how much is paved, without any other data source.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from dora_routes.config import Settings
from dora_routes.geo import LatLon, Point

logger = logging.getLogger(__name__)

PAVED_SURFACES = frozenset(
    {
        "asphalt", "paved", "concrete", "concrete:plates", "concrete:lanes", "paving_stones", "sett", "cobblestone",
        "metal", "wood", "chipseal", "unhewn_cobblestone",
    }
)  # fmt: skip
# Without a `surface` tag these are dirt/gravel in the overwhelming majority of OSM data; every other class
# (streets, footways…) is assumed paved.
UNPAVED_BY_DEFAULT = frozenset({"path", "track", "bridleway"})
CROSSING_NODE_TAGS = frozenset({"highway=crossing", "highway=traffic_signals"})
WAYMARKED = re.compile(r"\broute_hiking_(?:iwn|nwn|rwn|lwn)=yes\b")
BIKE_DESIGNATED = re.compile(r"\bbicycle=designated\b")

# brouter.de answers 403 when hammered. Space call starts and back off once before giving up on a call.
PUBLIC_MIN_INTERVAL_S = 0.5
PUBLIC_BACKOFF_S = 3.0


class BRouterError(RuntimeError):
    pass


@dataclass
class WayStats:
    """Metres per category along a route, read from BRouter's message table."""

    by_class: dict[str, float] = field(default_factory=dict)  # highway class → metres
    bike_shared: dict[str, float] = field(default_factory=dict)  # highway class → metres tagged bicycle=designated
    paved_m: float = 0.0
    marked_m: float = 0.0  # on a waymarked hiking route (OSM route relations, local to international)
    crossings: int = 0  # pedestrian crossings and traffic lights passed

    @property
    def total_m(self) -> float:
        return sum(self.by_class.values())

    def share(self, classes: frozenset[str] | set[str]) -> float:
        total = self.total_m
        return sum(m for k, m in self.by_class.items() if k in classes) / total if total else 0.0

    def bike_share(self, classes: frozenset[str] | set[str]) -> float:
        total = self.total_m
        return sum(m for k, m in self.bike_shared.items() if k in classes) / total if total else 0.0


@dataclass
class Route:
    points: list[Point]
    km: float
    climb_m: int
    stats: WayStats


def _tag(tags: str, key: str) -> str | None:
    m = re.search(rf"(?:^|\s){re.escape(key)}=(\S+)", tags)
    return m.group(1) if m else None


def parse_messages(messages: Sequence[Sequence[str]] | None) -> WayStats:
    """Read BRouter's message table by header name (the column order has changed between BRouter versions)."""
    stats = WayStats()
    if not messages:
        return stats
    header, *rows = messages
    col = {name: i for i, name in enumerate(header)}
    di, ti, ni = col.get("Distance"), col.get("WayTags"), col.get("NodeTags")
    for row in rows:
        way = (row[ti] or "") if ti is not None else ""
        dist = float(row[di] or 0) if di is not None else 0.0
        kind = _tag(way, "highway") or "other"
        stats.by_class[kind] = stats.by_class.get(kind, 0.0) + dist
        surface = _tag(way, "surface")
        if (surface in PAVED_SURFACES) if surface else kind not in UNPAVED_BY_DEFAULT:
            stats.paved_m += dist
        if WAYMARKED.search(way):
            stats.marked_m += dist
        if BIKE_DESIGNATED.search(way):
            stats.bike_shared[kind] = stats.bike_shared.get(kind, 0.0) + dist
        if ni is not None and CROSSING_NODE_TAGS & set((row[ni] or "").split()):
            stats.crossings += 1
    return stats


def parse_geojson(payload: dict[str, Any]) -> Route:
    try:
        feature = payload["features"][0]
        props = feature["properties"]
        coords = feature["geometry"]["coordinates"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BRouterError(f"unexpected BRouter answer: {exc!r}") from exc
    points: list[Point] = [(c[1], c[0], float(c[2]) if len(c) > 2 else None) for c in coords]
    return Route(
        points=points,
        km=float(props.get("track-length") or 0) / 1000,
        climb_m=round(float(props.get("filtered ascend") or 0)),
        stats=parse_messages(props.get("messages")),
    )


class BRouterClient:
    """Async BRouter client shared by all bearings of a request.

    At most `brouter_max_parallel` calls (≤ 4) are in flight. Against the public brouter.de, call starts are also
    spaced by 0.5 s and an HTTP 403 (rate limit) is retried once after a pause: the instance bans clients that send
    bursts."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.public = settings.uses_public_brouter
        self._sem = asyncio.Semaphore(settings.brouter_max_parallel)
        self._pace = asyncio.Lock()
        self._last_start = 0.0
        self.calls = 0
        self._client = httpx.AsyncClient(
            timeout=settings.brouter_timeout_s,
            headers={"User-Agent": settings.http_user_agent()},
            transport=transport,
        )

    async def __aenter__(self) -> BRouterClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _space_out(self) -> None:
        if not self.public:
            return
        async with self._pace:
            wait = self._last_start + PUBLIC_MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()

    async def route(self, waypoints: Sequence[LatLon], profile: str) -> Route:
        params = {
            "lonlats": "|".join(f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints),
            "profile": profile,
            "alternativeidx": "0",
            "format": "geojson",
        }
        async with self._sem:
            for attempt in range(2):
                await self._space_out()
                self.calls += 1
                res = await self._client.get(self.settings.brouter_url, params=params)
                if res.status_code == httpx.codes.FORBIDDEN and self.public and attempt == 0:
                    logger.warning("brouter.de answered 403 (rate limit): backing off %.0f s", PUBLIC_BACKOFF_S)
                    await asyncio.sleep(PUBLIC_BACKOFF_S)
                    continue
                break
        if res.status_code != httpx.codes.OK:
            # BRouter explains routing failures in plain text ("target island detected", "no track found"...).
            raise BRouterError(f"BRouter HTTP {res.status_code}: {res.text[:200].strip()}")
        return parse_geojson(res.json())

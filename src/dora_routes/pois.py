"""Places of a wanted kind (viewpoint, clearing, restaurant…) around a start point, from OpenStreetMap via Overpass.

One small query per request, cached in memory. Overpass asks for an identifying User-Agent and moderate use, so
requests are spaced by a second. Point `OVERPASS_URL` at your own instance for volume use. Only a circle (centre and
radius) is sent, never anything else about the request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from dora_routes.config import Settings
from dora_routes.geo import LatLon, haversine_km

logger = logging.getLogger(__name__)

# kind → Overpass tag filters (any of them matches). The list is closed: it is what the API accepts.
KINDS: dict[str, tuple[str, ...]] = {
    "viewpoint": ('["tourism"="viewpoint"]',),
    "clearing": ('["natural"="clearing"]', '["natural"="grassland"]["name"]'),
    "water": ('["natural"="spring"]', '["amenity"="drinking_water"]', '["natural"="water"]["name"]'),
    "peak": ('["natural"="peak"]',),
    "picnic": ('["tourism"="picnic_site"]', '["leisure"="picnic_table"]'),
    "restaurant": ('["amenity"="restaurant"]',),
    "cafe": ('["amenity"="cafe"]',),
    "supermarket": ('["shop"="supermarket"]',),
}
MIN_INTERVAL_S = 1.0
CACHE_MAX = 128
MAX_RESULTS_PER_KIND = 60


@dataclass(frozen=True)
class Poi:
    kind: str
    name: str
    lat: float
    lon: float


def build_query(kinds: list[str], center: LatLon, radius_km: float) -> str:
    around = f"(around:{radius_km * 1000:.0f},{center[0]:.5f},{center[1]:.5f})"
    parts = [f"nwr{tag}{around};" for kind in kinds for tag in KINDS[kind]]
    return f"[out:json][timeout:15];({''.join(parts)});out center tags {MAX_RESULTS_PER_KIND * len(kinds)};"


class PoiFinder:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self._cache: dict[tuple, list[Poi]] = {}
        self._lock = asyncio.Lock()
        self._last = 0.0
        self.requests = 0

    async def find(self, center: LatLon, radius_km: float, kinds: list[str]) -> list[Poi]:
        """Places of the wanted kinds within `radius_km`, nearest first. Best effort: a failure gives none."""
        kinds = sorted(set(kinds))
        unknown = [k for k in kinds if k not in KINDS]
        if unknown:
            raise ValueError(f"unknown kind(s): {', '.join(unknown)}")
        key = (round(center[0], 3), round(center[1], 3), round(radius_km), tuple(kinds))
        if key in self._cache:
            return self._cache[key]
        query = build_query(kinds, center, radius_km)
        async with self._lock:
            wait = self._last + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(
                    timeout=20, headers={"User-Agent": self.settings.http_user_agent()}, transport=self.transport
                ) as http:
                    self.requests += 1
                    response = await http.post(self.settings.overpass_url, data={"data": query})
                    response.raise_for_status()
                    elements = response.json().get("elements", [])
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("overpass failed, continuing without places: %r", exc)
                return []  # not cached: the next request may work
            finally:
                self._last = time.monotonic()
        found = []
        for el in elements:
            lat = el.get("lat", (el.get("center") or {}).get("lat"))
            lon = el.get("lon", (el.get("center") or {}).get("lon"))
            kind = _kind_of(el.get("tags", {}), kinds)
            if lat is None or lon is None or kind is None:
                continue
            tags = el.get("tags", {})
            found.append(Poi(kind, tags.get("name") or tags.get("name:fr") or "", float(lat), float(lon)))
        found.sort(key=lambda p: haversine_km(center, (p.lat, p.lon)))
        if len(self._cache) >= CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = found
        return found


def _kind_of(tags: dict[str, str], kinds: list[str]) -> str | None:
    """The first wanted kind whose tag filter matches `tags`."""
    for kind in kinds:
        for tag in KINDS[kind]:
            pairs = [p.split("=", 1) for p in tag.strip("[]").split("][")]
            if all(
                (k.strip('"') in tags) if len(p) == 1 else tags.get(k.strip('"')) == v.strip('"')
                for p in pairs
                for k, v in [(p[0], p[-1])]
            ):
                return kind
    return None

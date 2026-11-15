"""Free-text places ("bois de vincennes") → coordinates, with Nominatim.

Nominatim's usage policy: at most 1 request per second, an identifying User-Agent, and cache the results. Requests
go through a process-wide lock that spaces them by a second, and answers (including "not found") are kept in memory.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from dora_routes.config import Settings
from dora_routes.geo import LatLon

logger = logging.getLogger(__name__)

MIN_INTERVAL_S = 1.0
# A ±0.3° box (~30 km) around the hint: Nominatim prefers results inside it without excluding the rest, so
# "gare" finds the nearby station but "Mont Ventoux" still works from Paris.
BIAS_DEG = 0.3
CACHE_MAX = 512


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float


class Geocoder:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self._cache: dict[tuple[str, tuple[float, float] | None, str], Place | None] = {}
        self._lock = asyncio.Lock()
        self._last = 0.0
        self.requests = 0

    async def geocode(self, query: str, near: LatLon | None = None, lang: str = "en") -> Place | None:
        bias = (round(near[0], 1), round(near[1], 1)) if near else None  # nearby hints share a cache entry
        key = (query.strip().casefold(), bias, lang)
        if key in self._cache:
            return self._cache[key]
        params: dict[str, str | int] = {"q": query, "format": "jsonv2", "limit": 1, "accept-language": lang}
        if near:
            lat, lon = near
            box = (lon - BIAS_DEG, lat + BIAS_DEG, lon + BIAS_DEG, lat - BIAS_DEG)
            params["viewbox"] = ",".join(f"{v:.4f}" for v in box)
        async with self._lock:
            wait = self._last + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(
                    timeout=10, headers={"User-Agent": self.settings.http_user_agent()}, transport=self.transport
                ) as client:
                    self.requests += 1
                    res = await client.get(self.settings.nominatim_url, params=params)
            finally:
                self._last = time.monotonic()
        res.raise_for_status()
        hits = res.json()
        place = None
        if hits:
            hit = hits[0]
            name = hit.get("name") or (hit.get("display_name") or query).split(",")[0]
            place = Place(name=name, lat=float(hit["lat"]), lon=float(hit["lon"]))
        if len(self._cache) >= CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = place
        return place

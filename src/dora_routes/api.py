"""Library entry point: `await find_loops(...)`, or `find_loops_sync(...)` outside an event loop.

from dora_routes import find_loops_sync, to_gpx

result = find_loops_sync(start={"lat": 48.835, "lon": 2.44}, distance_km=10)
for i, loop in enumerate(result.loops, 1):
    print(loop.caption)
    open(f"loop-{i}.gpx", "wb").write(to_gpx(loop.points, f"Loop {i}"))
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from dora_routes.activities import get_activity
from dora_routes.brouter import BRouterClient
from dora_routes.captions import caption
from dora_routes.config import Settings, get_settings
from dora_routes.geo import LatLon, decode_polyline, visited_cells
from dora_routes.geocode import Geocoder, Place
from dora_routes.loops import Loop, generate
from dora_routes.models import LoopRequest

_geocoders: dict[int, Geocoder] = {}


class PlaceNotFoundError(LookupError):
    pass


@dataclass
class LoopsResult:
    start: LatLon
    target_km: float
    request: LoopRequest
    place: Place | None = None
    loops: list[Loop] = field(default_factory=list)


def _geocoder(settings: Settings, transport: httpx.AsyncBaseTransport | None) -> Geocoder:
    """One geocoder per settings object, so its cache and its 1 request/s spacing are shared across requests.
    A custom transport (tests) gets a fresh one."""
    if transport is not None:
        return Geocoder(settings, transport)
    return _geocoders.setdefault(id(settings), Geocoder(settings))


async def find_loops(
    request: LoopRequest | None = None,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    **fields: Any,
) -> LoopsResult:
    """Distinct loops around a start point (or a geocoded place), best first. Fields are those of `LoopRequest`.

    Returns fewer loops than asked (possibly none) when BRouter cannot find enough clean ones within the time budget;
    raises `PlaceNotFoundError` when `place` cannot be geocoded and `pydantic.ValidationError` on a bad request."""
    req = request or LoopRequest(**fields)
    settings = settings or get_settings()
    began = time.monotonic()
    activity = get_activity(req.activity)

    place = None
    if req.start is not None:
        start = (req.start.lat, req.start.lon)
    else:
        assert req.place is not None
        near = (req.near.lat, req.near.lon) if req.near else None
        place = await _geocoder(settings, transport).geocode(req.place, near, req.lang)
        if place is None:
            raise PlaceNotFoundError(f"place not found: {req.place!r}")
        start = (place.lat, place.lon)

    done = visited_cells(decode_polyline(t) for t in req.avoid_tracks) if req.avoid_tracks else None
    km = req.target_km()
    budget = max(1.0, settings.request_timeout_s - (time.monotonic() - began))
    async with BRouterClient(settings, transport) as client:
        loops = await generate(
            client,
            start,
            km,
            activity=activity,
            surface=req.surface,
            count=req.count,
            done=done,
            never_done=req.never_done,
            target_climb_m=req.climb_m,
            budget_s=budget,
        )
    for loop in loops:
        loop.caption = caption(loop, activity, req.surface, req.lang)
    return LoopsResult(start=start, target_km=km, request=req, place=place, loops=loops)


def find_loops_sync(request: LoopRequest | None = None, **kwargs: Any) -> LoopsResult:
    """Blocking version of `find_loops`, for scripts and threads without an event loop."""
    return asyncio.run(find_loops(request, **kwargs))

"""`/v1/loops`: generate loops, and download a cached loop's GPX or map."""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from dora_routes.api import PlaceNotFoundError, find_loops
from dora_routes.config import Settings, get_settings
from dora_routes.gpx import to_gpx
from dora_routes.loops import Loop
from dora_routes.mapimage import render_map
from dora_routes.models import (
    Coordinates,
    GeoJSONFeature,
    GeoJSONLineString,
    LoopOut,
    LoopRequest,
    LoopsResponse,
    PassedPlace,
    PlaceOut,
    Shares,
)
from dora_routes.server.cache import ResultCache

logger = logging.getLogger(__name__)

Include = Literal["gpx", "png"]


class ErrorBody(BaseModel):
    detail: str


def gpx_name(loop: Loop, place: str | None) -> str:
    return f"Dora · {place or 'loop'} · {loop.km:.1f} km"


def _climb_met(climb_m: int, target: int | None) -> bool | None:
    return abs(climb_m - target) <= 0.25 * target if target else None


def to_out(
    loop: Loop,
    loop_id: str,
    *,
    base: str,
    name: str,
    png: bytes | None,
    with_gpx: bool,
    target_climb_m: int | None = None,
    wanted: list[str] | None = None,
) -> LoopOut:
    coords = [[lon, lat, *([ele] if ele is not None else [])] for lat, lon, ele in loop.points]
    s = loop.shares
    return LoopOut(
        id=loop_id,
        distance_km=round(loop.km, 2),
        climb_m=loop.climb_m,
        passes=[PassedPlace(kind=p.kind, name=p.name, lat=p.lat, lon=p.lon) for p in loop.passes],
        pass_by_met=(None if not wanted else {p.kind for p in loop.passes} >= set(wanted)),
        climb_target_met=_climb_met(loop.climb_m, target_climb_m),
        bearing=loop.bearing,
        shares=Shares(deserted=s.deserted, paved=s.paved, crowded=s.crowded, busy=s.busy, marked=s.marked),
        crossings=loop.crossings,
        novelty=loop.novelty,
        caption=loop.caption,
        geojson=GeoJSONFeature(
            geometry=GeoJSONLineString(coordinates=coords),
            properties={"name": name, "distance_km": round(loop.km, 2), "attribution": "© OpenStreetMap contributors"},
        ),
        gpx_url=f"{base}/v1/loops/{loop_id}.gpx",
        png_url=f"{base}/v1/loops/{loop_id}.png",
        gpx_base64=base64.b64encode(to_gpx(loop.points, name)).decode() if with_gpx else None,
        png_base64=base64.b64encode(png).decode() if png else None,
    )


Probe = Callable[[Settings, httpx.AsyncBaseTransport | None], Awaitable[Any]]


def build_router(cache: ResultCache, transport: httpx.AsyncBaseTransport | None, probe: Probe) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["loops"])

    @router.post(
        "/loops",
        response_model=LoopsResponse,
        responses={
            404: {"model": ErrorBody, "description": "`place` could not be geocoded."},
            422: {"description": "The request is invalid."},
            502: {"model": ErrorBody, "description": "BRouter (or the geocoder) is unreachable."},
        },
        summary="Loop routes around a point",
    )
    async def create_loops(
        body: LoopRequest,
        request: Request,
        include: Annotated[
            list[Include] | None,
            Query(description="Embed the file in each loop as base64: `gpx`, `png` (slower: draws the map)."),
        ] = None,
    ) -> LoopsResponse:
        """Distinct loops (start = finish) of about the wanted distance, best first. Fewer than `count`, or none,
        come back when too few clean loops exist; an empty list is a valid answer."""
        settings = get_settings()
        try:
            result = await find_loops(body, settings=settings, transport=transport)
        except PlaceNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except httpx.HTTPError as exc:
            logger.warning("geocoder unreachable: %s", exc)
            raise HTTPException(502, "the geocoder is unreachable") from exc
        if not result.loops:
            await _fail_if_brouter_down(settings, transport, probe)
        base = str(request.base_url).rstrip("/")
        place = result.place.name if result.place else None
        out = []
        for loop in result.loops:
            name = gpx_name(loop, place)
            loop_id = cache.put(loop, name)
            png = await render_map(loop.points, settings, transport) if include and "png" in include else None
            if png:
                cache.get(loop_id).png = png  # type: ignore[union-attr]
            out.append(
                to_out(
                    loop,
                    loop_id,
                    base=base,
                    name=name,
                    png=png,
                    with_gpx=bool(include and "gpx" in include),
                    target_climb_m=result.request.climb_m,
                    wanted=result.request.pass_by,
                )
            )
        start = Coordinates(lat=result.start[0], lon=result.start[1])
        place_out = (
            PlaceOut(name=result.place.name, lat=result.place.lat, lon=result.place.lon) if result.place else None
        )
        return LoopsResponse(start=start, place=place_out, target_km=round(result.target_km, 2), loops=out)

    @router.get(
        "/loops/{loop_id}.gpx",
        response_class=Response,
        responses={200: {"content": {"application/gpx+xml": {}}}, 404: {"model": ErrorBody}},
        summary="GPX of a loop returned by POST /v1/loops",
    )
    async def loop_gpx(loop_id: str) -> Response:
        entry = _entry(cache, loop_id)
        name = entry.name
        return Response(
            to_gpx(entry.loop.points, name),
            media_type="application/gpx+xml",
            headers={"Content-Disposition": f'attachment; filename="dora-{loop_id}.gpx"'},
        )

    @router.get(
        "/loops/{loop_id}.png",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}, 404: {"model": ErrorBody}},
        summary="Map image of a loop returned by POST /v1/loops",
    )
    async def loop_png(loop_id: str) -> Response:
        entry = _entry(cache, loop_id)
        if entry.png is None:  # drawn on first request, then kept with the loop
            entry.png = await render_map(entry.loop.points, get_settings(), transport)
        return Response(entry.png, media_type="image/png")

    return router


def _entry(cache: ResultCache, loop_id: str):
    entry = cache.get(loop_id)
    if entry is None:
        raise HTTPException(404, "unknown or expired loop: ids are only kept for a few minutes")
    return entry


async def _fail_if_brouter_down(settings: Settings, transport: httpx.AsyncBaseTransport | None, probe: Probe) -> None:
    """`find_loops` skips bearings that fail, so an unreachable BRouter looks like 'no loops'. Tell the two apart."""
    health = await probe(settings, transport)
    if not health.reachable:
        raise HTTPException(502, f"BRouter is unreachable at {settings.brouter_url}")

"""FastAPI application. Run with `uvicorn dora_routes.server.app:app`; interactive docs at `/docs`."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Response, status
from pydantic import BaseModel

from dora_routes import __version__
from dora_routes.config import Settings, get_settings
from dora_routes.server.cache import ResultCache
from dora_routes.server.loops_api import build_router

logger = logging.getLogger(__name__)

HEALTH_PROBE_TIMEOUT_S = 5.0
# Docker probes /health every 30 s. Against the public brouter.de that must not become a steady trickle of calls,
# so the answer is cached; a self-hosted BRouter is cheap to ask more often.
HEALTH_CACHE_S = 30.0
HEALTH_CACHE_PUBLIC_S = 300.0
# A reverse proxy in front of BRouter answers these when BRouter itself is down.
GATEWAY_ERRORS = {502, 503, 504}


class BRouterHealth(BaseModel):
    url: str
    reachable: bool
    public: bool
    detail: str = ""


class Health(BaseModel):
    status: str
    version: str
    brouter: BRouterHealth


@dataclass
class _Probe:
    at: float
    result: BRouterHealth


_last_probe: _Probe | None = None


async def probe_brouter(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> BRouterHealth:
    """BRouter is reachable when it answers HTTP at all: a bare GET without `lonlats` returns an error message, which
    still proves the server is up. Only connection errors, timeouts and proxy gateway errors count as down."""
    global _last_probe  # noqa: PLW0603 - tiny per-process cache, no need for more machinery
    ttl = HEALTH_CACHE_PUBLIC_S if settings.uses_public_brouter else HEALTH_CACHE_S
    now = time.monotonic()
    if _last_probe and _last_probe.result.url == settings.brouter_url and now - _last_probe.at < ttl:
        return _last_probe.result
    try:
        async with httpx.AsyncClient(
            timeout=HEALTH_PROBE_TIMEOUT_S, headers={"User-Agent": settings.http_user_agent()}, transport=transport
        ) as client:
            response = await client.get(settings.brouter_url)
        result = BRouterHealth(
            url=settings.brouter_url,
            reachable=response.status_code not in GATEWAY_ERRORS,
            public=settings.uses_public_brouter,
            detail=f"HTTP {response.status_code}",
        )
    except httpx.HTTPError as exc:
        logger.warning("BRouter unreachable at %s: %s", settings.brouter_url, exc)
        result = BRouterHealth(
            url=settings.brouter_url,
            reachable=False,
            public=settings.uses_public_brouter,
            detail=type(exc).__name__,
        )
    _last_probe = _Probe(now, result)
    return result


def create_app(brouter_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`brouter_transport` lets tests swap the network (BRouter, Nominatim, tiles) for an `httpx.MockTransport`."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(
        title="Dora",
        version=__version__,
        summary="Loop routes (start = finish) for running, hiking and walking, on BRouter and OpenStreetMap.",
        license_info={"name": "MIT", "identifier": "MIT"},
    )

    app.include_router(
        build_router(ResultCache(settings.result_ttl_s, settings.result_cache_size), brouter_transport, probe_brouter)
    )

    @app.get("/health", response_model=Health, tags=["operations"])
    async def health(response: Response) -> Health:
        """200 when BRouter answers, 503 otherwise (so orchestrators can tell the stack is not ready)."""
        brouter = await probe_brouter(get_settings(), brouter_transport)
        if not brouter.reachable:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="ok" if brouter.reachable else "degraded", version=__version__, brouter=brouter)

    return app


app = create_app()

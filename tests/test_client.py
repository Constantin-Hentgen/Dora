"""DoraClient against the real FastAPI app, wired through httpx's ASGI/WSGI-style transports (no sockets)."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import FakeBRouter
from pydantic import ValidationError
from test_server_loops import START, make_client

from dora_routes import AsyncDoraClient, DoraClient, DoraError, PlaceNotFoundError, ServiceUnavailableError
from dora_routes.models import LoopsResponse
from dora_routes.server import app as app_module


def sync_client(test_client: TestClient) -> DoraClient:
    return DoraClient("http://testserver", transport=test_client._transport)


def test_find_loops_returns_typed_models_and_files() -> None:
    with sync_client(make_client()) as dora:
        result = dora.find_loops(start=START, distance_km=8, count=2, lang="fr")
        assert isinstance(result, LoopsResponse) and len(result.loops) == 2
        loop = result.loops[0]
        assert loop.shares.deserted == pytest.approx(0.8) and "sans voitures" in loop.caption
        assert dora.gpx(loop).startswith(b"<?xml")  # fetched through GET /v1/loops/{id}.gpx
        assert dora.png(loop)[:4] == b"\x89PNG"


def test_include_avoids_the_extra_downloads() -> None:
    test_client = make_client()
    with sync_client(test_client) as dora:
        loop = dora.find_loops(start=START, distance_km=8, count=1, include=("gpx", "png")).loops[0]
        assert loop.gpx_base64 and loop.png_base64
        assert dora.gpx(loop).startswith(b"<?xml") and dora.png(loop)[:4] == b"\x89PNG"


def test_a_bad_request_fails_locally_before_any_call() -> None:
    called = []
    transport = httpx.MockTransport(lambda r: called.append(r) or httpx.Response(200))
    with DoraClient("http://x", transport=transport) as dora, pytest.raises(ValidationError):
        dora.find_loops(start=START, distance_km=8, activity="skiing")
    assert called == []


def test_unknown_place_raises_place_not_found_for_both_doors() -> None:
    with sync_client(make_client()) as dora, pytest.raises(PlaceNotFoundError) as info:
        dora.find_loops(place="nowhere at all", distance_km=8)
    assert isinstance(info.value, DoraError) and info.value.status_code == 404


def test_brouter_down_raises_service_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "_last_probe", None)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with sync_client(TestClient(app_module.create_app(httpx.MockTransport(down)))) as dora:
        with pytest.raises(ServiceUnavailableError, match="unreachable"):
            dora.find_loops(start=START, distance_km=8)
        assert dora.health()["status"] == "degraded"


def test_expired_loop_is_a_clear_error() -> None:
    with sync_client(make_client()) as dora:
        loop = dora.find_loops(start=START, distance_km=8, count=1).loops[0]
        loop = loop.model_copy(update={"id": "gone"})
        with pytest.raises(DoraError) as info:
            dora.gpx(loop)
    assert info.value.status_code == 404 and "expired" in info.value.detail


def test_async_client() -> None:
    async def go() -> None:
        app = app_module.create_app(httpx.MockTransport(FakeBRouter()))
        transport = httpx.ASGITransport(app=app)
        async with AsyncDoraClient("http://testserver", transport=transport) as dora:
            result = await dora.find_loops(start=START, distance_km=5, count=1)
            assert len(result.loops) == 1
            assert (await dora.gpx(result.loops[0])).startswith(b"<?xml")

    asyncio.run(go())

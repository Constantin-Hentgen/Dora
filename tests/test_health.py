import httpx
import pytest
from fastapi.testclient import TestClient

from loopwise import __version__
from loopwise.server import app as app_module


@pytest.fixture(autouse=True)
def no_cached_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "_last_probe", None)


def client_for(handler) -> tuple[TestClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return TestClient(app_module.create_app(httpx.MockTransport(record))), seen


def test_health_ok_when_brouter_answers_even_with_an_error_message() -> None:
    client, seen = client_for(lambda r: httpx.Response(400, text="lonlats parameter not set"))
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok" and body["version"] == __version__
    assert body["brouter"]["reachable"] and not body["brouter"]["public"]
    assert seen[0].headers["user-agent"].startswith("loopwise/")


def test_health_503_when_brouter_is_down() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, __ = client_for(down)
    res = client.get("/health")
    assert res.status_code == 503 and res.json()["status"] == "degraded"


def test_health_503_on_proxy_gateway_error() -> None:
    client, __ = client_for(lambda r: httpx.Response(502))
    assert client.get("/health").status_code == 503


def test_health_probe_is_cached_so_public_brouter_is_not_polled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROUTER_URL", "https://brouter.de/brouter")
    client, seen = client_for(lambda r: httpx.Response(200))
    for __ in range(5):
        assert client.get("/health").json()["brouter"]["public"]
    assert len(seen) == 1


def test_openapi_is_generated() -> None:
    client, __ = client_for(lambda r: httpx.Response(200))
    assert "/health" in client.get("/openapi.json").json()["paths"]

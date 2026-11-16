"""POST /v1/loops, cached GPX/PNG downloads and error mapping, with BRouter, Nominatim and tiles mocked."""

import base64
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import FakeBRouter
from PIL import Image

from dora_routes import Loop
from dora_routes.server import app as app_module
from dora_routes.server.cache import ResultCache

START = {"lat": 48.835, "lon": 2.44}


def tile_png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (256, 256), (170, 210, 160)).save(out, format="PNG")
    return out.getvalue()


def make_client(brouter: FakeBRouter | None = None, nominatim=None, tiles_status: int = 200):
    brouter = brouter or FakeBRouter(rows=lambda wps: [(800, "highway=path", ""), (200, "highway=residential", "")])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "nominatim.openstreetmap.org":
            return nominatim(request) if nominatim else httpx.Response(200, json=[])
        if request.url.host == "tile.openstreetmap.org":
            return httpx.Response(tiles_status, content=tile_png())
        return brouter(request)

    return TestClient(app_module.create_app(httpx.MockTransport(handler)))


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "_last_probe", None)
    monkeypatch.setenv("TILE_CACHE_DIR", str(tmp_path / "tiles"))


def test_post_returns_loops_with_stats_geojson_and_urls() -> None:
    res = make_client().post("/v1/loops", json={"start": START, "distance_km": 8, "lang": "fr"})
    assert res.status_code == 200
    body = res.json()
    assert body["start"] == START and body["target_km"] == 8 and len(body["loops"]) == 3
    loop = body["loops"][0]
    assert abs(loop["distance_km"] - 8) / 8 <= 0.05
    assert loop["shares"] == pytest.approx({"deserted": 0.8, "paved": 0.2, "crowded": 0.0, "busy": 0.0, "marked": 0.0})
    assert (loop["crossings"], loop["novelty"], loop["climb_m"]) == (0, None, 42)
    assert "0 traversée" in loop["caption"] and "sans voitures" in loop["caption"]
    geo = loop["geojson"]
    assert geo["type"] == "Feature" and geo["geometry"]["type"] == "LineString"
    assert geo["geometry"]["coordinates"][0][:2] == [START["lon"], START["lat"]]  # GeoJSON is [lon, lat]
    assert loop["gpx_url"] == f"http://testserver/v1/loops/{loop['id']}.gpx"
    assert loop["gpx_base64"] is None and loop["png_base64"] is None


def test_gpx_and_png_downloads_share_the_loops_points() -> None:
    client = make_client()
    loop = client.post("/v1/loops", json={"start": START, "distance_km": 8, "count": 1}).json()["loops"][0]
    gpx = client.get(f"/v1/loops/{loop['id']}.gpx")
    assert gpx.status_code == 200 and gpx.headers["content-type"].startswith("application/gpx+xml")
    assert gpx.text.count("<trkpt") == len(loop["geojson"]["geometry"]["coordinates"])  # what you see is what you run
    assert "OpenStreetMap contributors" in gpx.text
    png = client.get(f"/v1/loops/{loop['id']}.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(png.content)).format == "PNG"


def test_include_embeds_files_as_base64() -> None:
    res = make_client().post("/v1/loops?include=gpx&include=png", json={"start": START, "distance_km": 8, "count": 1})
    loop = res.json()["loops"][0]
    assert base64.b64decode(loop["gpx_base64"]).startswith(b"<?xml")
    assert base64.b64decode(loop["png_base64"])[:4] == b"\x89PNG"


def test_unknown_or_expired_id_is_404() -> None:
    assert make_client().get("/v1/loops/doesnotexist.gpx").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"distance_km": 8},
        {"start": START},
        {"start": START, "distance_km": 8, "activity": "skiing"},
        {"start": {"lat": 100, "lon": 0}, "distance_km": 8},
        {"start": START, "distance_km": 8, "count": 9},
    ],
)
def test_invalid_requests_are_422(body: dict) -> None:
    assert make_client().post("/v1/loops", json=body).status_code == 422


def test_place_is_geocoded_and_reported() -> None:
    geo = lambda r: httpx.Response(200, json=[{"lat": "48.8333", "lon": "2.4333", "name": "Bois de Vincennes"}])  # noqa: E731
    body = make_client(nominatim=geo).post("/v1/loops", json={"place": "bois de vincennes", "distance_km": 8}).json()
    assert body["place"] == {"name": "Bois de Vincennes", "lat": 48.8333, "lon": 2.4333}
    assert body["start"] == {"lat": 48.8333, "lon": 2.4333}
    assert (
        "Bois de Vincennes"
        in make_client(nominatim=geo)
        .post("/v1/loops?include=gpx", json={"place": "bois de vincennes", "distance_km": 8, "count": 1})
        .json()["loops"][0]["geojson"]["properties"]["name"]
    )


def test_unknown_place_is_404() -> None:
    res = make_client().post("/v1/loops", json={"place": "nowhere at all", "distance_km": 8})
    assert res.status_code == 404 and "not found" in res.json()["detail"]


def test_brouter_down_is_502_not_an_empty_list() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = TestClient(app_module.create_app(httpx.MockTransport(down)))
    res = client.post("/v1/loops", json={"start": START, "distance_km": 8})
    assert res.status_code == 502 and "unreachable" in res.json()["detail"]


def test_no_clean_loop_is_an_empty_list_not_an_error() -> None:
    class NoRoute(FakeBRouter):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="target island detected for section 0")

    res = make_client(NoRoute()).post("/v1/loops", json={"start": START, "distance_km": 8})
    assert res.status_code == 200 and res.json()["loops"] == []  # BRouter answered: it just found nothing


def test_openapi_documents_v1() -> None:
    spec = make_client().get("/openapi.json").json()
    assert {"/v1/loops", "/v1/loops/{loop_id}.gpx", "/v1/loops/{loop_id}.png", "/health"} <= set(spec["paths"])
    assert "LoopsResponse" in spec["components"]["schemas"] and "LoopRequest" in spec["components"]["schemas"]
    assert spec["paths"]["/v1/loops"]["post"]["responses"]["404"]


def test_cache_expires_and_evicts_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("dora_routes.server.cache.time.monotonic", lambda: clock[0])
    cache = ResultCache(ttl_s=10, max_size=2)
    loop = Loop(points=[(48.8, 2.3, None)], km=1, bearing=0)
    a, b = cache.put(loop, "a"), cache.put(loop, "b")
    cache.get(a)  # a is now the most recently used
    c = cache.put(loop, "c")
    assert cache.get(b) is None and cache.get(a) and cache.get(c)  # least recently used went
    clock[0] += 11
    assert cache.get(a) is None and len(cache) == 0

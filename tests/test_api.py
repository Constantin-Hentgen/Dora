"""Library entry point: request validation, geocoding, end-to-end with a fake BRouter."""

import asyncio
import re

import httpx
import pytest
from helpers import FakeBRouter
from pydantic import ValidationError

from dora_routes import LoopRequest, PlaceNotFoundError, find_loops, find_loops_sync
from dora_routes.config import Settings
from dora_routes.geocode import Geocoder


def nominatim(hits):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=hits)

    return handler


def router(brouter: FakeBRouter, geocoder=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "nominatim.openstreetmap.org":
            return (geocoder or nominatim([]))(request)
        return brouter(request)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"distance_km": 10}, "exactly one of `start` or `place`"),
        ({"start": {"lat": 48.8, "lon": 2.3}, "place": "x y", "distance_km": 10}, "exactly one of `start`"),
        ({"start": {"lat": 48.8, "lon": 2.3}}, "exactly one of `distance_km`"),
        ({"start": {"lat": 91, "lon": 2.3}, "distance_km": 10}, "less than or equal to 90"),
        ({"start": {"lat": 48.8, "lon": 2.3}, "distance_km": 0.2}, "greater than or equal to 1"),
        ({"start": {"lat": 48.8, "lon": 2.3}, "distance_km": 10, "activity": "skiing"}, "unknown activity"),
        ({"start": {"lat": 48.8, "lon": 2.3}, "distance_km": 10, "surface": "ice"}, "surface"),
        ({"start": {"lat": 48.8, "lon": 2.3}, "distance_km": 10, "count": 9}, "less than or equal to 5"),
        ({"start": {"lat": 48.8, "lon": 2.3}, "distance_km": 10, "lang": "de"}, "lang"),
        ({"start": {"lat": 48.8, "lon": 2.3}, "distance_km": 10, "avoid_tracks": ["_p~iF~ps|U_"]}, "avoid_tracks[0]"),
        ({"start": {"lat": 48.8, "lon": 2.3}, "distance_km": 10, "colour": "red"}, "Extra inputs"),
        ({"start": {"lat": 48.8, "lon": 2.3}, "duration_min": 3}, "less than 1 km"),
    ],
)
def test_bad_requests_are_rejected(fields: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        LoopRequest(**fields)


def test_duration_becomes_distance() -> None:
    start = {"lat": 48.8, "lon": 2.3}
    assert LoopRequest(start=start, duration_min=60).target_km() == 10  # running default 6:00 min/km
    assert LoopRequest(start=start, duration_min=45, pace_min_per_km=4.5).target_km() == 10
    assert LoopRequest(start=start, duration_min=90, speed_kmh=24).target_km() == 36


def test_find_loops_end_to_end_with_captions() -> None:
    brouter = FakeBRouter(
        rows=lambda wps: [(800, "highway=path", ""), (200, "highway=residential", "highway=crossing")]
    )
    result = find_loops_sync(
        start={"lat": 48.835, "lon": 2.44}, distance_km=8, lang="fr", transport=router(brouter), settings=Settings()
    )
    assert len(result.loops) == 3 and result.target_km == 8
    assert result.loops[0].caption.startswith(f"{result.loops[0].km:.1f}".replace(".", ",") + " km · 80 % sans")
    params = brouter.requests[0].url.params
    assert params["profile"] == "hiking-mountain" and params["format"] == "geojson"
    assert brouter.requests[0].headers["user-agent"].startswith("dora-routes/")


def test_place_is_geocoded_near_the_hint() -> None:
    seen: list[httpx.Request] = []

    def geo(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"lat": "48.8333", "lon": "2.4333", "name": "Bois de Vincennes"}])

    result = find_loops_sync(
        place="bois de vincennes", near={"lat": 48.85, "lon": 2.35}, distance_km=8,
        transport=router(FakeBRouter(), geo), settings=Settings(),
    )  # fmt: skip
    assert result.place and result.place.name == "Bois de Vincennes"
    assert result.start == (48.8333, 2.4333) and result.loops
    assert seen[0].url.params["viewbox"] == "2.0500,49.1500,2.6500,48.5500"


def test_unknown_place() -> None:
    with pytest.raises(PlaceNotFoundError):
        find_loops_sync(place="nowhere at all", distance_km=8, transport=router(FakeBRouter()), settings=Settings())


def test_never_done_with_past_tracks() -> None:
    # A past run covering the start area: loops all start there, but most of their length is new.
    past = "_p~iF~ps|U_ulLnnqC"  # far away from Paris: everything is new
    result = find_loops_sync(
        start={"lat": 48.835, "lon": 2.44}, distance_km=8, avoid_tracks=[past], never_done=True, count=1,
        transport=router(FakeBRouter()), settings=Settings(),
    )  # fmt: skip
    assert result.loops[0].novelty == 1.0 and result.loops[0].caption.endswith("100 % never run")


def test_geocoder_caches_and_spaces_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dora_routes.geocode.MIN_INTERVAL_S", 0.05)
    times: list[float] = []

    def geo(request: httpx.Request) -> httpx.Response:
        times.append(asyncio.get_running_loop().time())
        return httpx.Response(200, json=[{"lat": "1", "lon": "2", "display_name": "Somewhere, Earth"}])

    async def go() -> Geocoder:
        g = Geocoder(Settings(), httpx.MockTransport(geo))
        assert (await g.geocode("Somewhere")).name == "Somewhere"
        await g.geocode("  somewhere ")  # cached
        await g.geocode("Elsewhere")
        return g

    g = asyncio.run(go())
    assert g.requests == 2 and times[1] - times[0] >= 0.045


def test_find_loops_is_awaitable() -> None:
    result = asyncio.run(
        find_loops(start={"lat": 48.835, "lon": 2.44}, distance_km=5, count=1, transport=router(FakeBRouter()))
    )
    assert len(result.loops) == 1

"""BRouter answer parsing (ported from Bob) and client limits."""

import asyncio
import itertools

import httpx
import pytest
from helpers import FakeBRouter, messages

from dora_routes import RUNNING
from dora_routes.brouter import PUBLIC_MIN_INTERVAL_S, BRouterClient, BRouterError, parse_geojson, parse_messages
from dora_routes.config import Settings


def test_surface_crossings_and_waymarked_share() -> None:
    stats = parse_messages(
        messages(
            [
                (400, "highway=footway surface=asphalt", "highway=crossing crossing=traffic_signals"),
                (300, "highway=path route_hiking_lwn=yes", ""),  # no surface on a path: unpaved
                (200, "highway=residential surface=gravel", "highway=traffic_signals"),
                (100, "highway=residential", "kerb=lowered"),  # no surface on a street: paved
            ]
        )
    )
    shares = RUNNING.shares(stats)
    assert stats.crossings == 2
    assert round(shares.paved, 2) == 0.5 and round(shares.marked, 2) == 0.3
    assert stats.by_class == {"footway": 400, "path": 300, "residential": 300}


def test_shared_bike_paths_and_pedestrian_streets_count_as_crowded() -> None:
    stats = parse_messages(
        messages(
            [
                (1000, "highway=path", ""),
                (1000, "highway=path bicycle=designated", ""),
                (1000, "highway=pedestrian", ""),
                (1000, "highway=cycleway", ""),
            ]
        )
    )
    shares = RUNNING.shares(stats)
    assert (shares.deserted, shares.crowded) == (0.25, 0.75)


def test_columns_are_found_by_header_name_not_position() -> None:
    table = [["WayTags", "Distance", "NodeTags"], ["highway=track", "500", ""], ["highway=primary", "500", ""]]
    shares = RUNNING.shares(parse_messages(table))
    assert (shares.deserted, shares.busy, shares.paved) == (0.5, 0.5, 0.5)


def test_geojson_points_climb_and_length() -> None:
    route = parse_geojson(
        {
            "features": [
                {
                    "geometry": {"coordinates": [[2.3, 48.8, 35], [2.31, 48.81]]},
                    "properties": {"track-length": "1234", "filtered ascend": "17.6", "messages": None},
                }
            ]
        }
    )
    assert route.points == [(48.8, 2.3, 35.0), (48.81, 2.31, None)]
    assert (route.km, route.climb_m) == (1.234, 18)
    with pytest.raises(BRouterError):
        parse_geojson({"features": []})


def run(coro):
    return asyncio.run(coro)


def test_routing_error_message_is_kept() -> None:
    async def go() -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(400, text="target island detected for section 0"))
        async with BRouterClient(Settings(), transport) as client:
            await client.route([(48.8, 2.3), (48.81, 2.31)], "hiking-mountain")

    with pytest.raises(BRouterError, match="target island"):
        run(go())


def test_never_more_than_four_calls_in_flight() -> None:
    in_flight = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return FakeBRouter()(request)

    async def go() -> None:
        async with BRouterClient(Settings(), httpx.MockTransport(handler)) as client:
            await asyncio.gather(*(client.route([(48.8, 2.3), (48.81, 2.31)], "p") for __ in range(12)))

    run(go())
    assert peak == 4


def test_public_brouter_is_spaced_and_403_backed_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dora_routes.brouter.PUBLIC_BACKOFF_S", 0.01)
    monkeypatch.setattr("dora_routes.brouter.PUBLIC_MIN_INTERVAL_S", 0.05)
    starts: list[float] = []
    answers = iter([403, 200, 200])
    fake = FakeBRouter()

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(asyncio.get_running_loop().time())
        status = next(answers)
        return fake(request) if status == 200 else httpx.Response(403)

    async def go() -> None:
        settings = Settings(brouter_url="https://brouter.de/brouter")
        async with BRouterClient(settings, httpx.MockTransport(handler)) as client:
            await asyncio.gather(*(client.route([(48.8, 2.3), (48.81, 2.31)], "p") for __ in range(2)))
            assert client.calls == 3  # one call retried after the 403

    run(go())
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert all(g >= 0.045 for g in gaps)
    assert PUBLIC_MIN_INTERVAL_S == 0.5  # the real spacing (patched above only to keep the test fast)

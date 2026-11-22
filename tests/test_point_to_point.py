"""Point-to-point routes: start and finish differ."""

import pytest
from helpers import FakeBRouter
from pydantic import ValidationError
from test_api import router

from dora_routes import LoopRequest, find_loops_sync
from dora_routes.config import Settings
from dora_routes.geo import haversine_km, offset
from dora_routes.loops import detour_height_km

START = (48.644, 1.833)


def run(end_km: float, wanted_km: float, **fields):
    end = offset(*START, end_km, 90)
    brouter = FakeBRouter()
    result = find_loops_sync(
        start={"lat": START[0], "lon": START[1]}, end={"lat": end[0], "lon": end[1]}, distance_km=wanted_km,
        transport=router(brouter), settings=Settings(), **fields,
    )  # fmt: skip
    return result, end, brouter


def test_detours_reach_the_wanted_distance_and_finish_at_the_end() -> None:
    result, end, __ = run(end_km=5, wanted_km=12)
    assert result.loops
    for loop in result.loops:
        assert loop.kind == "point_to_point"
        assert haversine_km(loop.points[-1], end) < 0.05
        assert haversine_km(loop.points[0], START) < 0.05
        assert abs(loop.km - 12) / 12 <= 0.10


def test_direct_route_when_it_is_already_long_enough() -> None:
    result, __, brouter = run(end_km=8, wanted_km=9)  # 8 km crow flies × 1.25 = 10 km
    assert len(result.loops) == 1 and len(brouter.requests) == 1


def test_no_detour_for_an_unreasonable_distance() -> None:
    result, __, ___ = run(end_km=2, wanted_km=40)
    assert len(result.loops) == 1  # the direct route, whose length tells the truth


def test_height_gives_the_wanted_crow_flies_length() -> None:
    h = detour_height_km(5.0, 8.0, 0.5)
    assert 2 * (2.5**2 + h**2) ** 0.5 == pytest.approx(8.0, abs=1e-6)


def test_pass_by_with_end_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not supported with `end`"):
        LoopRequest(
            start={"lat": 48.8, "lon": 2.3}, end={"lat": 48.9, "lon": 2.4}, distance_km=10, pass_by=["viewpoint"]
        )


def test_without_end_loops_stay_loops() -> None:
    result = find_loops_sync(
        start={"lat": START[0], "lon": START[1]}, distance_km=8, transport=router(FakeBRouter()), settings=Settings()
    )
    assert result.loops and all(lp.kind == "loop" for lp in result.loops)

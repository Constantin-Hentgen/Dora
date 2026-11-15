"""Against a live BRouter: `docker compose up -d brouter` (or any BRouter with the Paris tile E0_N45), then

    uv run --frozen python -m pytest -q -m brouter

Skipped by default (see `addopts` in pyproject.toml). BROUTER_URL picks the server (default localhost:17777).
"""

import httpx
import pytest

from dora_routes import find_loops_sync, to_gpx
from dora_routes.config import Settings
from dora_routes.geo import find_spurs, haversine_km

pytestmark = pytest.mark.brouter

# Public places only: parks and city centres.
STARTS = {
    "bois-de-vincennes": (48.8350, 2.4400),
    "buttes-chaumont": (48.8800, 2.3830),
    "notre-dame": (48.8530, 2.3490),
}


@pytest.fixture(scope="module")
def settings() -> Settings:
    s = Settings()
    try:
        httpx.get(s.brouter_url, timeout=3)
    except httpx.HTTPError:
        pytest.skip(f"no BRouter at {s.brouter_url}")
    return s


@pytest.mark.parametrize("name", STARTS)
def test_real_loops_are_clean_and_on_distance(name: str, settings: Settings) -> None:
    lat, lon = STARTS[name]
    result = find_loops_sync(start={"lat": lat, "lon": lon}, distance_km=8, settings=settings)
    assert result.loops, "no loop found"
    errors = [abs(loop.km - 8) / 8 for loop in result.loops]
    assert errors[0] <= 0.1  # the best loop is on distance
    assert all(e <= 0.25 for e in errors)  # fillers, when too few loops land within ±10 %, stay close
    in_band = [e <= 0.1 for e in errors]
    assert in_band == sorted(in_band, reverse=True)  # on-distance loops first
    for loop in result.loops:
        assert find_spurs(loop.points) == []
        assert haversine_km(loop.points[0], (lat, lon)) < 0.2 and haversine_km(loop.points[-1], (lat, lon)) < 0.2
        assert to_gpx(loop.points, name).count(b"<trkpt") == len(loop.points)
        print(f"{name} {loop.bearing:>5.0f}° {loop.caption}")

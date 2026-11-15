"""GPX, map image and captions. The GPX and the map must show exactly the same points."""

import asyncio
import io
import re
from pathlib import Path

import httpx
import pytest
from PIL import Image

from dora_routes import RUNNING, Surface, render_map, to_gpx
from dora_routes.activities import Shares
from dora_routes.brouter import WayStats
from dora_routes.captions import caption
from dora_routes.config import Settings
from dora_routes.loops import Loop
from dora_routes.mapimage import ATTRIBUTION, fit_zoom, project

POINTS = [(48.8, 2.3, 35.0), (48.81, 2.31, None), (48.805, 2.32, 40.4), (48.8, 2.3, 35.0)]


def tile_png(color=(170, 210, 160)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(out, format="PNG")
    return out.getvalue()


def make_loop(km=6.24, deserted=0.8, paved=1.0, marked=0.0, crossings=0, climb=0, novelty=None) -> Loop:
    shares = Shares(deserted=deserted, calm=0.2, busy=0.0, crowded=0.0, paved=paved, marked=marked)
    return Loop(points=POINTS, km=km, bearing=0, climb_m=climb, stats=WayStats(crossings=crossings), shares=shares,
                novelty=novelty)  # fmt: skip


def test_gpx_has_elevation_escaped_names_and_attribution() -> None:
    gpx = to_gpx(POINTS, "Loop 1 · home & co <3").decode()
    assert gpx.startswith('<?xml version="1.0"') and 'version="1.1"' in gpx
    assert '<trkpt lat="48.800000" lon="2.300000"><ele>35</ele></trkpt>' in gpx
    assert '<trkpt lat="48.810000" lon="2.310000"></trkpt>' in gpx
    assert "home &amp; co &lt;3" in gpx and "OpenStreetMap contributors" in gpx and "odbl" in gpx


def test_captions_in_french_match_bob() -> None:
    assert (
        caption(make_loop(novelty=0.92), RUNNING, lang="fr")
        == "6,2 km · 80 % sans voitures ni foule · 0 traversée · 92 % jamais couru"
    )
    road = make_loop(km=6.0, deserted=0.3, crossings=6)
    assert (
        caption(road, RUNNING, Surface.PAVED, "fr")
        == "6,0 km · 100 % bitume · 30 % sans voitures ni foule · 6 traversées"
    )
    trail = make_loop(km=6.0, deserted=0.8, paved=0.1, marked=0.5, crossings=1, climb=120)
    assert (
        caption(trail, RUNNING, Surface.UNPAVED, "fr")
        == "6,0 km · 90 % nature · 80 % sans voitures ni foule · 1 traversée · 50 % sur sentiers balisés · D+ 120 m"
    )


def test_captions_in_english() -> None:
    trail = make_loop(km=6.0, deserted=0.8, paved=0.1, marked=0.5, crossings=1, climb=120, novelty=0.5)
    assert caption(trail, RUNNING, Surface.UNPAVED, "en") == (
        "6.0 km · 90 % unpaved · 80 % away from cars and crowds · 1 crossing · 50 % on waymarked trails"
        " · 120 m climb · 50 % never run"
    )
    assert "0 crossings" in caption(make_loop(), RUNNING, lang="en")


def test_zoom_fits_the_loop() -> None:
    big = [(48.8, 2.3, None), (48.9, 2.5, None)]
    assert fit_zoom(big, 800) < fit_zoom(POINTS, 800)
    x1, y1 = project(48.8, 2.3, fit_zoom(big, 800))
    x2, y2 = project(48.9, 2.5, fit_zoom(big, 800))
    assert abs(x2 - x1) <= 720 and abs(y2 - y1) <= 720


def render(tmp_path: Path, handler, points=POINTS) -> bytes:
    settings = Settings(tile_cache_dir=str(tmp_path / "tiles"), map_size_px=400)
    return asyncio.run(render_map(points, settings, httpx.MockTransport(handler)))


def test_map_is_a_png_with_tiles_and_cached(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def tiles(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert re.fullmatch(r"/\d+/\d+/\d+\.png", request.url.path)
        assert request.headers["user-agent"].startswith("dora-routes/")
        return httpx.Response(200, content=tile_png())

    png = render(tmp_path, tiles)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size == (400, 400)
    assert img.getpixel((5, 5)) == (170, 210, 160)  # a tile, not the blank background
    first = len(seen)
    assert first > 0
    render(tmp_path, tiles)
    assert len(seen) == first  # second render served from the disk cache


def test_map_still_renders_when_tiles_fail(tmp_path: Path) -> None:
    png = render(tmp_path, lambda r: httpx.Response(503))
    assert Image.open(io.BytesIO(png)).size == (400, 400)


def test_gpx_and_map_draw_exactly_the_same_points(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What you see is what your watch follows: the map projects the very points the GPX lists."""
    drawn: list[tuple[float, float]] = []
    real_project = project

    def spy(lat: float, lon: float, zoom: int) -> tuple[float, float]:
        drawn.append((lat, lon))
        return real_project(lat, lon, zoom)

    monkeypatch.setattr("dora_routes.mapimage.project", spy)
    render(tmp_path, lambda r: httpx.Response(200, content=tile_png()))
    gpx = to_gpx(POINTS, "x").decode()
    gpx_points = [(float(a), float(b)) for a, b in re.findall(r'<trkpt lat="([\d.-]+)" lon="([\d.-]+)"', gpx)]
    assert drawn[-len(POINTS) :] == [(round(p[0], 6), round(p[1], 6)) for p in POINTS] == gpx_points


def test_attribution_text() -> None:
    assert ATTRIBUTION == "© OpenStreetMap contributors"

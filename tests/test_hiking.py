"""Hiking: the same generator, configured for trails (and walking at a slower pace)."""

import pytest
from helpers import FakeBRouter
from test_api import router
from test_outputs import make_loop

from dora_routes import LoopRequest, find_loops_sync
from dora_routes.activities import HIKING, Shares, Surface
from dora_routes.captions import caption
from dora_routes.config import Settings
from dora_routes.loops import rank


def test_hiking_captions_are_for_hikers_not_street_crossers() -> None:
    lp = make_loop(km=12.4, deserted=0.7, marked=0.5, crossings=30, climb=310, novelty=0.9)
    assert caption(lp, HIKING, lang="fr") == (
        "12,4 km · 70 % sur sentiers loin des voitures · 50 % sur sentiers balisés · D+ 310 m · 90 % jamais randonné"
    )
    assert caption(lp, HIKING, lang="en") == (
        "12.4 km · 70 % on trails away from cars · 50 % on waymarked trails · 310 m climb · 90 % never hiked"
    )


def test_walking_is_hiking_at_a_slower_pace() -> None:
    start = {"lat": 48.8, "lon": 2.3}
    assert LoopRequest(start=start, duration_min=120, activity="hiking").target_km() == 10  # 12 min/km
    assert LoopRequest(start=start, duration_min=120, activity="hiking", pace_min_per_km=15).target_km() == 8


def test_waymarked_trails_weigh_more_than_for_running() -> None:
    assert HIKING.weights.marked > 1.0 and HIKING.weights.crossings_per_km == 0.0


@pytest.mark.parametrize(
    ("url", "profile"),
    [("http://localhost:17777/brouter", "hiking-trails"), ("https://brouter.de/brouter", "hiking-mountain")],
)
def test_hiking_end_to_end_with_its_profile(url: str, profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dora_routes.brouter.PUBLIC_MIN_INTERVAL_S", 0.0)
    brouter = FakeBRouter(rows=lambda wps: [(800, "highway=path route_hiking_lwn=yes", ""), (200, "highway=track", "")])
    result = find_loops_sync(
        start={"lat": 48.644, "lon": 1.833}, distance_km=12, activity="hiking", count=2, lang="en",
        transport=router(brouter), settings=Settings(brouter_url=url),
    )  # fmt: skip
    assert len(result.loops) == 2
    assert {r.url.params["profile"] for r in brouter.requests} == {profile}
    assert result.loops[0].caption.startswith(f"{result.loops[0].km:.1f} km · 100 % on trails away from cars")
    assert "crossing" not in result.loops[0].caption and "waymarked" in result.loops[0].caption


def test_climb_target_reranks_by_ascent():

    shares = Shares(deserted=0.5, calm=0.2, busy=0.0, crowded=0.0, paved=0.3, marked=0.2)
    kw = {"km": 10, "target_km": 10, "shares": shares, "crossings": 0, "backtrack": 0.0, "surface": Surface.MIXED}
    flat = HIKING.score(**kw, climb_m=20, target_climb_m=400)
    close = HIKING.score(**kw, climb_m=380, target_climb_m=400)
    assert close > flat
    assert HIKING.score(**kw, climb_m=20) == HIKING.score(**kw, climb_m=900)  # no target: ascent is ignored


def test_climb_target_widens_the_distance_band() -> None:
    flat = make_loop(km=12.0, climb=80)
    steep = make_loop(km=14.5, climb=600)  # 21 % too long: outside the usual ±10 % band
    for lp in (flat, steep):
        lp.spurs_left = 0
    kw = {"target_km": 12, "activity": HIKING, "surface": Surface.MIXED, "count": 1}
    assert rank([flat, steep], **kw)[0] is flat
    assert rank([flat, steep], **kw, target_climb_m=600)[0] is steep

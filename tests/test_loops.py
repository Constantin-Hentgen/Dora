"""Scoring, selection and loop construction (ported from Bob, plus distance refitting)."""

import asyncio

import httpx
import pytest
from helpers import FakeBRouter, line

from dora_routes import RUNNING, Surface
from dora_routes.activities import Shares
from dora_routes.brouter import BRouterClient
from dora_routes.config import Settings
from dora_routes.geo import bearing_gap, path_km, visited_cells
from dora_routes.loops import Loop, generate, loop_at, next_radius, pick_distinct, rank

LOOP_PTS = line(48.8, 2.3, 0.001, 0, 40) + line(48.8, 2.31, 0.001, 0, 40)


def shares(deserted=0.0, calm=0.0, busy=0.0, crowded=0.0, paved=1.0, marked=0.0) -> Shares:
    return Shares(deserted=deserted, calm=calm, busy=busy, crowded=crowded, paved=paved, marked=marked)


def score(km: float, target: float, sh: Shares, crossings: int = 0, surface: Surface = Surface.MIXED) -> float:
    return RUNNING.score(km=km, target_km=target, shares=sh, crossings=crossings, backtrack=0, surface=surface)


def test_default_prefers_deserted_paths_over_cars_crowds_and_bikes() -> None:
    alone = score(6, 6, shares(deserted=0.8, calm=0.2), crossings=2)
    streets = score(6, 6, shares(deserted=0.1, calm=0.9), crossings=20)
    crowd = score(6, 6, shares(deserted=0.2, calm=0.2, crowded=0.6), crossings=2)
    avenue = score(6, 6, shares(deserted=0.5, calm=0.2, busy=0.3), crossings=2)
    assert alone > max(streets, crowd, avenue)
    assert score(6.6, 6, shares(deserted=0.8, calm=0.2), 2) > streets  # 10 % too long beats the streets


def test_score_follows_wanted_surface_and_crossings() -> None:
    road = shares(deserted=0.3, calm=0.6, paved=1.0)
    trail = shares(deserted=0.8, calm=0.1, paved=0.1, marked=0.5)
    assert score(6, 6, road, 6, Surface.PAVED) > score(6, 6, trail, 1, Surface.PAVED)
    assert score(6, 6, trail, 1, Surface.UNPAVED) > score(6, 6, road, 6, Surface.UNPAVED)
    assert score(6, 6, road, 6, Surface.PAVED) > score(6, 6, road, 60, Surface.PAVED)  # fewer crossings


def fake_loop(bearing: float, deserted: float, points=LOOP_PTS, spurs_left: int = 0) -> Loop:
    return Loop(points=list(points), km=6, bearing=bearing, shares=shares(deserted=deserted), spurs_left=spurs_left)


def test_prefers_quiet_and_distinct_bearings() -> None:
    loops = [fake_loop(b, 0.9 if b in (0, 180) else 0.3) for b in range(0, 360, 60)]
    picked = rank(loops, target_km=6, activity=RUNNING, surface=Surface.MIXED, count=3)
    assert [lp.bearing for lp in picked][:2] == [0, 180]  # the quietest first
    # 0° and 180° leave nothing 90° away from both: the gap is relaxed to 60° to get a third loop.
    assert len(picked) == 3 and all(
        bearing_gap(a.bearing, b.bearing) >= 60 for i, a in enumerate(picked) for b in picked[i + 1 :]
    )


def test_out_and_back_is_dropped_when_a_real_loop_exists() -> None:
    out = line(48.8, 2.3, 0.001, 0, 40)
    loops = [fake_loop(0, 1.0, out + out[::-1]), fake_loop(120, 0.4), fake_loop(240, 0.4)]
    picked = rank(loops, target_km=6, activity=RUNNING, surface=Surface.MIXED, count=3)
    assert picked and all(lp.bearing != 0 for lp in picked)  # the quietest, but an out-and-back


def test_loops_with_a_spur_left_are_never_returned() -> None:
    loops = [fake_loop(0, 1.0, spurs_left=1), fake_loop(120, 0.4), fake_loop(240, 0.4)]
    assert all(lp.bearing != 0 for lp in rank(loops, target_km=6, activity=RUNNING, surface=Surface.MIXED, count=3))
    assert rank([fake_loop(0, 1.0, spurs_left=1)], target_km=6, activity=RUNNING, surface=Surface.MIXED, count=3) == []


def test_never_done_keeps_new_loops_or_the_most_novel_one() -> None:
    far = line(10.0, 10.0, 0.001, 0, 40) + line(10.0, 10.01, 0.001, 0, 40)
    loops = [fake_loop(0, 1.0), fake_loop(120, 0.2, far)]
    done = visited_cells([[(p[0], p[1]) for p in LOOP_PTS]])
    picked = rank(loops, target_km=6, activity=RUNNING, surface=Surface.MIXED, count=3, done=done, never_done=True)
    assert [lp.bearing for lp in picked] == [120] and picked[0].novelty == 1.0
    only_old = rank([fake_loop(0, 1.0)], target_km=6, activity=RUNNING, surface=Surface.MIXED, count=1, done=done,
                    never_done=True)  # fmt: skip
    assert len(only_old) == 1 and only_old[0].novelty == 0.0  # nothing new: the best we have, flagged


def test_pick_distinct_stops_at_count() -> None:
    loops = [fake_loop(b, 0.5) for b in (0, 90, 180, 270)]
    assert len(pick_distinct(loops, 2)) == 2


def route_with(fake: FakeBRouter, coro_fn):
    async def go():
        async with BRouterClient(Settings(), httpx.MockTransport(fake)) as client:
            return await coro_fn(client), client.calls

    return asyncio.run(go())


@pytest.mark.parametrize("factor", [1.0, 1.25, 1.6])
def test_radius_is_refitted_until_within_five_percent(factor: float) -> None:
    loop, calls = route_with(FakeBRouter(factor=factor), lambda c: loop_at(c, (48.85, 2.35), 10, 45, RUNNING))
    assert abs(loop.km - 10) / 10 <= 0.05
    assert calls <= RUNNING.distance_tries
    if factor == 1.25:
        assert calls == 1  # the default factor is right first time


def test_refit_gives_up_after_three_tries_and_keeps_the_closest() -> None:
    """A router whose length jumps around never converges: three calls, then the closest loop."""
    lengths = iter([1.0, 2.5, 0.6])

    class Erratic(FakeBRouter):
        def __call__(self, request):
            self.factor = next(lengths)
            return super().__call__(request)

    loop, calls = route_with(Erratic(), lambda c: loop_at(c, (48.85, 2.35), 10, 0, RUNNING))
    assert calls == 3
    # 8 km triangle x1.0 = 8 km; refit to 10 km x2.5 = 25 km; refit to 4 km x0.6 = 2.4 km: 8 km is the closest.
    assert loop.km == pytest.approx(8.0, rel=0.02)


def test_generate_end_to_end_with_fake_brouter() -> None:
    def rows(wps):
        # Loops heading east (waypoint east of start) are on paths, others in the streets.
        east = wps[1][1] > wps[0][1] and wps[2][1] > wps[0][1]
        return [(1000, "highway=path" if east else "highway=residential", "")]

    loops, calls = route_with(
        FakeBRouter(rows=rows),
        lambda c: generate(c, (48.85, 2.35), 8, activity=RUNNING, count=3, budget_s=10),
    )
    assert len(loops) == 3
    assert loops[0].shares.deserted == 1.0 and 30 <= loops[0].bearing <= 150
    assert all(lp.points[0][:2] == lp.points[-1][:2] == (48.85, 2.35) for lp in loops)  # start = finish
    assert all(lp.km == pytest.approx(path_km(lp.points) * 1.25, rel=0.01) for lp in loops)
    assert calls == 6  # one call per bearing: the default route factor fits the fake router


def test_budget_drops_slow_bearings() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return FakeBRouter()(request)

    async def go():
        async with BRouterClient(Settings(), httpx.MockTransport(slow)) as client:
            return await generate(client, (48.85, 2.35), 8, activity=RUNNING, budget_s=0.2)

    assert asyncio.run(go()) == []


def test_next_radius_scales_then_interpolates_between_brackets() -> None:
    assert next_radius([(2.0, 6.0)], 8) == pytest.approx(2.0 * 8 / 6)  # one try: proportional
    # 2.13 km → 6.72 km and 2.54 km → 10.13 km (measured at Buttes-Chaumont): interpolate, don't scale back down.
    assert next_radius([(2.13, 6.72), (2.54, 10.13)], 8) == pytest.approx(2.284, abs=0.001)


def test_loops_on_distance_are_picked_before_better_scored_ones_off_distance() -> None:
    short_but_deserted = fake_loop(0, 1.0)
    short_but_deserted.km = 4.5  # -25 %
    on_distance = [fake_loop(b, 0.3) for b in (120, 240)]
    picked = rank([short_but_deserted, *on_distance], target_km=6, activity=RUNNING, surface=Surface.MIXED, count=3)
    assert [lp.bearing for lp in picked] == [120, 240, 0]  # still returned, but last

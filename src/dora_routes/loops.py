"""Loop generation: start → A → B → start, at several bearings, refitted to the wanted distance, cleaned of dead-end
spurs, then filtered and ranked by the activity's score."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from dora_routes.activities import Activity, Shares, Surface
from dora_routes.brouter import BRouterClient, WayStats
from dora_routes.geo import (
    Cell,
    LatLon,
    Point,
    backtrack,
    bearing_gap,
    bearing_to,
    distance_to_path_km,
    haversine_km,
    novelty,
    offset,
    path_km,
    remove_spurs,
)
from dora_routes.pois import Poi

logger = logging.getLogger(__name__)

# Bearings tried per request: 6 (every 60°) is enough to find 3 loops 90° apart; "never done" needs more choice.
BEARINGS = 6
BEARINGS_NEVER_DONE = 8
# Distinct loops: bearings at least 90° apart, relaxed to 60° when there are not enough.
BEARING_GAPS = (90.0, 60.0)
# A wanted place counts as passed when the path comes this close; loops through places are tried at most this many.
PASS_BY_KM = 0.05
MAX_VIA_LOOPS = 4
VIA_RADIUS_RANGE = (0.5, 1.4)  # a place is a candidate waypoint when it is this far, as a share of the loop radius


@dataclass
class Loop:
    """One loop. `points` are exactly what the GPX and the map show: what you see is what your watch follows."""

    points: list[Point]
    km: float
    bearing: float
    climb_m: int = 0
    stats: WayStats = field(default_factory=WayStats)
    shares: Shares = field(default_factory=lambda: Shares(0, 0, 0, 0, 1, 0))
    spurs_left: int = 0
    spurs_cut: int = 0
    backtrack: float = 0.0
    novelty: float | None = None
    score: float = 0.0
    caption: str = ""
    passes: list[Poi] = field(default_factory=list)  # wanted places within PASS_BY_KM of the path
    kind: str = "loop"  # "loop", or "point_to_point" when the finish is not the start
    via: Poi | None = None  # the place this loop was routed through

    @property
    def crossings(self) -> int:
        return self.stats.crossings

    @property
    def crossings_per_km(self) -> float:
        return self.crossings / self.km if self.km else 0.0

    @property
    def start(self) -> LatLon:
        return self.points[0][0], self.points[0][1]


def triangle(start: LatLon, radius_km: float, bearing: float) -> list[LatLon]:
    """Two waypoints at `radius_km`, 30° either side of `bearing`: with the start, an equilateral triangle whose
    perimeter is 3 × radius."""
    a = offset(*start, radius_km, bearing - 30)
    b = offset(*start, radius_km, bearing + 30)
    return [start, a, b, start]


def radius_for(km: float, activity: Activity) -> float:
    return km / (3 * activity.route_factor)


def next_radius(tries: Sequence[tuple[float, float]], target_km: float) -> float:
    """Radius for the next try, from the (radius, measured km) pairs so far.

    Scaling the last radius by target / measured assumes length grows in proportion to the radius. It does not
    quite: a waypoint that moves into a dead-end loses its spur (often 1-2 km) when the spur is cut. So once two tries
    bracket the target, interpolate between the closest bracketing pair instead; on real data this converges where
    plain scaling oscillates around the target."""
    r, k = tries[-1]
    below = [t for t in tries if t[1] < target_km]
    above = [t for t in tries if t[1] > target_km]
    if below and above:
        (r1, k1), (r2, k2) = max(below, key=lambda t: t[1]), min(above, key=lambda t: t[1])
        return r1 + (target_km - k1) * (r2 - r1) / (k2 - k1)
    return r * target_km / k


async def fit_route(
    client: BRouterClient,
    activity: Activity,
    km: float,
    *,
    bearing: float,
    first: float,
    waypoints_for: Callable[[float], list[LatLon]],
    via: Poi | None = None,
    tries_max: int | None = None,
) -> Loop | None:
    """Best route whose waypoints depend on one size parameter (`waypoints_for(size)`). The size is refitted from the
    measured length, up to `distance_tries` calls, until the route (after spur removal) is within the tolerance."""
    size = first
    best: Loop | None = None
    tries: list[tuple[float, float]] = []
    for __ in range(tries_max or activity.distance_tries):
        route = await client.route(waypoints_for(size), activity.profile_for(client.settings))
        raw_km = route.km or path_km(route.points)
        points, left = remove_spurs(route.points, activity.spurs)
        loop = Loop(
            points=points,
            # The distance is that of the points we return, not BRouter's: cut spurs shorten the loop.
            km=path_km(points) if len(points) != len(route.points) else raw_km,
            bearing=bearing,
            climb_m=route.climb_m,
            stats=route.stats,
            shares=activity.shares(route.stats),
            spurs_left=left,
            spurs_cut=len(route.points) - len(points),
            via=via,
        )
        if best is None or (loop.spurs_left, abs(loop.km - km)) < (best.spurs_left, abs(best.km - km)):
            best = loop
        if loop.km and not loop.spurs_left and abs(loop.km - km) / km <= activity.distance_tolerance:
            break
        if not loop.km:
            break
        tries.append((size, loop.km))
        size = next_radius(tries, km)
    return best


async def loop_at(
    client: BRouterClient, start: LatLon, km: float, bearing: float, activity: Activity, *, via: Poi | None = None
) -> Loop | None:
    """Best loop towards `bearing`, its radius refitted to the distance. With `via`, the first waypoint is that
    place and only the second one moves (60° further round) to fit the distance."""
    if via:
        fixed = (via.lat, via.lon)

        def waypoints(radius: float) -> list[LatLon]:
            return [start, fixed, offset(*start, radius, bearing + 60), start]

        first = haversine_km(start, fixed)  # a first try close to an equilateral triangle
    else:

        def waypoints(radius: float) -> list[LatLon]:
            return triangle(start, radius, bearing)

        first = radius_for(km, activity)
    return await fit_route(client, activity, km, bearing=bearing, first=first, waypoints_for=waypoints, via=via)


# Where along the line start → end the detour bends, and on which side: four candidates, all different.
DETOUR_POSITIONS = (0.35, 0.65)
DETOUR_MAX_RATIO = 3.0  # a wanted distance above this × the direct distance is not worth a detour


def detour_height_km(direct_km: float, crow_km: float, position: float) -> float:
    """Height of a via-point above the line, `position` of the way along it, that makes two straight legs of total
    `crow_km` (found by bisection: the total grows with the height)."""
    low, high = 0.0, crow_km
    for __ in range(40):
        mid = (low + high) / 2
        total = math.hypot(position * direct_km, mid) + math.hypot((1 - position) * direct_km, mid)
        low, high = (mid, high) if total < crow_km else (low, mid)
    return (low + high) / 2


async def point_to_point(
    client: BRouterClient, start: LatLon, end: LatLon, km: float, activity: Activity
) -> list[Loop]:
    """Routes from `start` to `end` of about `km`: the direct route when it is already long enough, else detours that
    bend left or right of the line, at two places along it, each refitted to the distance."""
    direct = await fit_route(
        client,
        activity,
        km,
        bearing=bearing_to(start, end),
        first=0.0,
        waypoints_for=lambda __: [start, end],
        tries_max=1,
    )  # one try: nothing to refit
    if direct is None or not direct.km:
        return []
    direct.kind = "point_to_point"
    if direct.km >= km * (1 - activity.distance_tolerance) or km > DETOUR_MAX_RATIO * direct.km:
        return [direct]
    crow = haversine_km(start, end)
    line = bearing_to(start, end)
    crow_total = km / activity.route_factor
    tasks = []
    for side in (-90, 90):
        for position in DETOUR_POSITIONS:
            foot = offset(*start, position * crow, line)

            def waypoints(height: float, foot=foot, side=side) -> list[LatLon]:
                return [start, offset(*foot, height, line + side), end]

            first = detour_height_km(crow, crow_total, position)
            tasks.append(
                fit_route(client, activity, km, bearing=(line + side) % 360, first=first, waypoints_for=waypoints)
            )
    found = [r for r in await asyncio.gather(*tasks, return_exceptions=True) if isinstance(r, Loop)]
    for loop in found:
        loop.kind = "point_to_point"
    return found


def via_candidates(
    start: LatLon, km: float, activity: Activity, pois: Sequence[Poi], wanted: Sequence[str]
) -> list[Poi]:
    """Places worth routing through: about a loop radius away, one kind after the other, in different directions."""
    radius = radius_for(km, activity)
    low, high = (radius * f for f in VIA_RADIUS_RANGE)
    near = [p for p in pois if low <= haversine_km(start, (p.lat, p.lon)) <= high]
    # Closest to the ideal radius first within each kind, kinds taking turns.
    by_kind = {
        kind: sorted(
            (p for p in near if p.kind == kind), key=lambda p: abs(haversine_km(start, (p.lat, p.lon)) - radius)
        )
        for kind in wanted
    }
    chosen: list[Poi] = []
    for rank_ in range(len(near)):
        for kind in wanted:
            if rank_ < len(by_kind[kind]) and len(chosen) < MAX_VIA_LOOPS:
                p = by_kind[kind][rank_]
                direction = bearing_to(start, (p.lat, p.lon))
                if all(bearing_gap(direction, bearing_to(start, (c.lat, c.lon))) >= 60 for c in chosen):
                    chosen.append(p)
    return chosen


def pick_distinct(loops: Sequence[Loop], count: int) -> list[Loop]:
    """Best-first selection of loops that really differ: bearings 90° apart, then 60° if that leaves too few."""
    picked: list[Loop] = []
    for gap in BEARING_GAPS:
        for lp in loops:
            if lp not in picked and all(bearing_gap(lp.bearing, p.bearing) >= gap for p in picked):
                picked.append(lp)
            if len(picked) == count:
                return picked
    return picked


def rank(
    loops: Sequence[Loop],
    *,
    target_km: float,
    activity: Activity,
    surface: Surface,
    count: int,
    done: set[Cell] | None = None,
    never_done: bool = False,
    target_climb_m: int | None = None,
    pois: Sequence[Poi] = (),
    wanted: Sequence[str] = (),
) -> list[Loop]:
    """Filter, score and pick. Pure function over already-routed loops, so the selection rules are easy to test."""
    kept = []
    for lp in loops:
        if lp.km <= 0:
            continue
        if lp.spurs_left:  # an invisible dead-end on the map: never returned
            logger.info("loop towards %.0f° dropped: %d dead-end spur(s) left", lp.bearing, lp.spurs_left)
            continue
        if activity.forbidden_ways and lp.stats.share(activity.forbidden_ways) > activity.forbidden_tolerance:
            continue
        lp.backtrack = backtrack(lp.points)
        lp.novelty = novelty(lp.points, done) if done is not None else None
        lp.passes = [p for p in pois if distance_to_path_km((p.lat, p.lon), lp.points) <= PASS_BY_KM]
        kept.append(lp)
    kept = [lp for lp in kept if lp.backtrack <= activity.max_backtrack] or kept  # no U-turns when possible
    if never_done:
        kept = [lp for lp in kept if (lp.novelty or 0) >= activity.never_done_min] or sorted(
            kept, key=lambda lp: lp.novelty or 0, reverse=True
        )[:1]  # none new enough: the single most novel one, flagged by its novelty
    for lp in kept:
        lp.score = activity.score(
            km=lp.km,
            target_km=target_km,
            shares=lp.shares,
            crossings=lp.crossings,
            backtrack=lp.backtrack,
            surface=surface,
            novelty=lp.novelty,
            never_done=never_done,
            climb_m=lp.climb_m,
            target_climb_m=target_climb_m,
            pass_by=len({p.kind for p in lp.passes} & set(wanted)) / len(set(wanted)) if wanted else 0.0,
        )
    # Loops within twice the tolerance (±10 %) of the distance come first; others only fill the remaining slots.
    band = 2 * activity.distance_tolerance
    kept.sort(key=lambda lp: (abs(lp.km - target_km) / target_km <= band, lp.score), reverse=True)
    return pick_distinct(kept, count)


async def generate(
    client: BRouterClient,
    start: LatLon,
    km: float,
    *,
    activity: Activity,
    surface: Surface = Surface.MIXED,
    count: int = 3,
    done: set[Cell] | None = None,
    never_done: bool = False,
    target_climb_m: int | None = None,
    pois: Sequence[Poi] = (),
    wanted: Sequence[str] = (),
    end: LatLon | None = None,
    budget_s: float = 45.0,
) -> list[Loop]:
    """Route all bearings concurrently (the client caps calls in flight), within `budget_s`. Bearings still running
    when the budget is spent are cancelled and ignored; results keep bearing order, so output is deterministic."""
    if end is not None:
        try:
            routes = await asyncio.wait_for(point_to_point(client, start, end, km, activity), timeout=budget_s)
        except TimeoutError:
            routes = []
        return rank(
            routes, target_km=km, activity=activity, surface=surface, count=count, done=done, never_done=never_done,
            target_climb_m=target_climb_m,
        )  # fmt: skip
    n = BEARINGS_NEVER_DONE if never_done else BEARINGS
    bearings = [i * 360 / n for i in range(n)]
    vias = via_candidates(start, km, activity, pois, wanted) if wanted else []
    tasks = [asyncio.create_task(loop_at(client, start, km, b, activity)) for b in bearings]
    tasks += [
        asyncio.create_task(loop_at(client, start, km, bearing_to(start, (p.lat, p.lon)), activity, via=p))
        for p in vias
    ]
    bearings += [bearing_to(start, (p.lat, p.lon)) for p in vias]
    began = time.monotonic()
    __, late = await asyncio.wait(tasks, timeout=budget_s)
    for t in late:
        t.cancel()
    loops = []
    for bearing, task in zip(bearings, tasks, strict=True):
        if task in late:
            logger.warning("loop towards %.0f° skipped: %.0f s budget spent", bearing, budget_s)
            continue
        try:
            loop = task.result()
        except Exception as exc:  # BRouter, network or parsing error: one bearing must not sink the request
            logger.warning("loop towards %.0f° failed: %r", bearing, exc)
            continue
        if loop:
            loops.append(loop)
    logger.info(
        "%d/%d bearings routed in %.1f s with %d BRouter calls",
        len(loops), n, time.monotonic() - began, client.calls,
    )  # fmt: skip
    return rank(
        loops,
        target_km=km,
        activity=activity,
        surface=surface,
        count=count,
        done=set() if done is None and never_done else done,  # nothing done yet: everything is new
        never_done=never_done,
        target_climb_m=target_climb_m,
        pois=pois,
        wanted=wanted,
    )

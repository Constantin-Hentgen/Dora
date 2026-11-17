"""Activities are configuration, not code branches.

An `Activity` says which BRouter profile to route with, how to turn a distance into a triangle, which OSM way classes
are good or bad for it, how to weigh them, and how strict to be about dead-ends and out-and-backs. Adding hiking
means adding an `Activity`, not touching the loop generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from dora_routes.brouter import WayStats
from dora_routes.config import Settings
from dora_routes.geo import SpurRules


class Surface(StrEnum):
    PAVED = "paved"
    UNPAVED = "unpaved"
    MIXED = "mixed"


# OSM highway classes, grouped by what you meet there.
PATHS = frozenset({"footway", "path", "track", "bridleway", "steps", "corridor"})  # no cars, rarely anyone
CROWDED = frozenset({"pedestrian", "living_street", "cycleway"})  # no cars, but people or bikes
CAR_STREETS = frozenset({"residential", "service", "unclassified", "road"})  # calm streets, still cars
BUSY_ROADS = frozenset(
    {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "trunk", "trunk_link"}
)


@dataclass(frozen=True)
class Shares:
    """Share of the loop's length (0-1) in each category. `deserted` is the headline number for running: no cars,
    no crowds, no bikes."""

    deserted: float
    calm: float
    busy: float
    crowded: float
    paved: float
    marked: float


@dataclass(frozen=True)
class Weights:
    """Score terms. The score is only used to rank loops of one request, so only ratios between weights matter.

    Running defaults come from months of use in Bob: a loop 10 % too long on paths beats an exact one in the streets
    (distance 2 vs deserted 1.5), and busy roads cost more than anything else a loop can gain."""

    distance: float = 2.0  # × relative distance error
    backtrack: float = 3.0  # × share of out-and-back
    marked: float = 0.4  # bonus for waymarked trails (validated by the community)
    deserted: float = 1.5
    calm: float = 0.3
    crowded: float = 0.8  # penalty
    busy: float = 2.0  # penalty
    crossings_per_km: float = 0.06  # penalty per crossing per km...
    crossings_cap: float = 15.0  # ...capped: in dense cities 50+ crossings is normal, don't let it drown the rest
    paved: float = 1.0  # bonus × paved share when the paved surface is asked
    unpaved: float = 1.5  # bonus × unpaved share when the unpaved surface is asked
    novelty: float = 0.5  # bonus × novelty when "never done" is asked


@dataclass(frozen=True)
class Activity:
    name: str
    brouter_profile: str
    # Profile to use when the BRouter is the public brouter.de, which only has its built-in profiles.
    public_brouter_profile: str | None = None
    # Streets make a loop ~25 % longer than the triangle through its waypoints, as the crow flies.
    route_factor: float = 1.25
    distance_km: tuple[float, float] = (5.0, 30.0)  # typical range, used for validation messages and defaults
    default_pace_min_per_km: float = 6.0  # to turn a duration into a distance
    quiet_ways: frozenset[str] = PATHS
    crowded_ways: frozenset[str] = CROWDED
    calm_ways: frozenset[str] = CAR_STREETS
    busy_ways: frozenset[str] = BUSY_ROADS
    # Way classes the activity must not use at all (e.g. roads for a trail-only hike). Loops using more than
    # `forbidden_tolerance` of them are dropped.
    forbidden_ways: frozenset[str] = frozenset()
    forbidden_tolerance: float = 0.02
    caption_crossings: bool = True  # street crossings matter when running; a hiker does not count them
    weights: Weights = field(default_factory=Weights)
    spurs: SpurRules = field(default_factory=SpurRules)
    max_backtrack: float = 0.08  # above: the loop contains an out-and-back; dropped when alternatives exist
    never_done_min: float = 0.8  # "never done" = at least 80 % of the loop outside past tracks
    distance_tolerance: float = 0.05  # ±5 % of the asked distance
    distance_tries: int = 3  # BRouter calls per bearing to refit the radius

    def profile_for(self, settings: Settings) -> str:
        """The BRouter profile to request: the custom one, unless BRouter is the public instance."""
        if self.public_brouter_profile and settings.uses_public_brouter:
            return self.public_brouter_profile
        return self.brouter_profile

    def shares(self, stats: WayStats) -> Shares:
        """Paths shared with bikes (`bicycle=designated`) are moved from deserted to crowded: on a narrow path, a
        stream of bikes is exactly what a runner looking for calm does not want."""
        bikes = stats.bike_share(self.quiet_ways)
        total = stats.total_m
        return Shares(
            deserted=max(0.0, stats.share(self.quiet_ways) - bikes),
            calm=stats.share(self.calm_ways),
            busy=stats.share(self.busy_ways),
            crowded=stats.share(self.crowded_ways) + bikes,
            paved=stats.paved_m / total if total else 1.0,
            marked=stats.marked_m / total if total else 0.0,
        )

    def score(
        self,
        *,
        km: float,
        target_km: float,
        shares: Shares,
        crossings: int,
        backtrack: float,
        surface: Surface,
        novelty: float | None = None,
        never_done: bool = False,
    ) -> float:
        """Higher is better. Close to the distance, no out-and-back, and first of all deserted (safety and calm:
        no cars, no crowds, no bikes, few streets to cross), whatever the surface; the asked surface then adds its
        own term."""
        w = self.weights
        per_km = crossings / km if km else 0.0
        score = (
            -w.distance * abs(km - target_km) / target_km
            - w.backtrack * backtrack
            + w.marked * shares.marked
            + w.deserted * shares.deserted
            + w.calm * shares.calm
            - w.crowded * shares.crowded
            - w.busy * shares.busy
            - w.crossings_per_km * min(per_km, w.crossings_cap)
        )
        if surface is Surface.PAVED:
            score += w.paved * shares.paved
        elif surface is Surface.UNPAVED:
            score += w.unpaved * (1 - shares.paved)
        if never_done and novelty is not None:
            score += w.novelty * novelty
        return score


RUNNING = Activity(
    name="running",
    # profiles/running-deserted.brf, baked into the BRouter image: costs for car roads, bike paths, pedestrian
    # streets and street crossings. The public brouter.de cannot load it, so it gets its hiking profile instead.
    brouter_profile="running-deserted",
    public_brouter_profile="hiking-mountain",
    distance_km=(5.0, 30.0),
    default_pace_min_per_km=6.0,
)

# Hiking and walking: waymarked trails first, away from cars; a short quiet road to link two trails is fine. Walking is
# the same activity at a slower pace (`pace_min_per_km`). A climb target is a later feature.
HIKING = Activity(
    name="hiking",
    brouter_profile="hiking-trails",
    public_brouter_profile="hiking-mountain",
    distance_km=(5.0, 40.0),
    default_pace_min_per_km=12.0,  # 5 km/h
    caption_crossings=False,
    weights=Weights(marked=1.2, crowded=0.4, busy=2.5, crossings_per_km=0.0),
)

ACTIVITIES: dict[str, Activity] = {a.name: a for a in (RUNNING, HIKING)}


def get_activity(name: str) -> Activity:
    try:
        return ACTIVITIES[name]
    except KeyError:
        raise ValueError(f"unknown activity {name!r}; available: {', '.join(ACTIVITIES)}") from None

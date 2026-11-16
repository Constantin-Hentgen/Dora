"""Compare two BRouter profiles for running on the same public start points.

    uv run --frozen python scripts/compare_profiles.py --before hiking-mountain --after running-deserted

Needs a BRouter that knows both profiles and has the tiles of the start points (the default points are all in tile
E0_N45, i.e. Paris and the Centre-Val de Loire / Champagne cities). Prints a Markdown table; paste it in the PR.

For each start point and profile it asks for the loops Dora would return (same distance, same scoring, same cleaning of
dead-end spurs), then averages what the user cares about over those loops:

- deserted: share of the distance on paths, tracks and footways, with no cars, no crowds and no bikes
- crowded / busy: share on pedestrian streets and cycleways / on tertiary or bigger roads
- crossings per km: street crossings and traffic lights you stop at
- error: relative distance error (how close to the wanted distance)
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass, replace

from dora_routes import RUNNING
from dora_routes.brouter import BRouterClient
from dora_routes.config import Settings
from dora_routes.loops import Loop, generate

# Public places only: city centres, parks, castles. No private addresses.
STARTS: dict[str, tuple[float, float]] = {
    "Paris, Notre-Dame": (48.8530, 2.3490),
    "Paris, Buttes-Chaumont": (48.8800, 2.3830),
    "Paris, Bois de Vincennes": (48.8350, 2.4400),
    "Sceaux, park": (48.7640, 2.3000),
    "Versailles, castle": (48.8049, 2.1204),
    "Saint-Germain-en-Laye, castle": (48.8980, 2.0950),
    "Rambouillet, castle": (48.6440, 1.8330),
    "Fontainebleau, castle": (48.4020, 2.6990),
    "Orléans, cathedral": (47.9020, 1.9100),
    "Tours, cathedral": (47.3960, 0.6990),
    "Le Mans, cathedral": (48.0080, 0.1990),
    "Reims, cathedral": (49.2535, 4.0340),
}


@dataclass
class Row:
    place: str
    loops: int
    deserted: float
    crowded: float
    busy: float
    crossings_per_km: float
    error: float
    seconds: float


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else float("nan")


def summarize(place: str, loops: list[Loop], km: float, seconds: float) -> Row:
    return Row(
        place=place,
        loops=len(loops),
        deserted=mean([lp.shares.deserted for lp in loops]),
        crowded=mean([lp.shares.crowded for lp in loops]),
        busy=mean([lp.shares.busy for lp in loops]),
        crossings_per_km=mean([lp.crossings_per_km for lp in loops]),
        error=mean([abs(lp.km - km) / km for lp in loops]),
        seconds=seconds,
    )


async def run_profile(settings: Settings, profile: str, km: float, count: int) -> list[Row]:
    activity = replace(RUNNING, brouter_profile=profile)
    rows = []
    async with BRouterClient(settings) as client:
        for place, start in STARTS.items():
            began = time.monotonic()
            budget = settings.request_timeout_s
            loops = await generate(client, start, km, activity=activity, count=count, budget_s=budget)
            rows.append(summarize(place, loops, km, time.monotonic() - began))
    return rows


def pct(x: float) -> str:
    return f"{100 * x:.0f} %"


def render(before: list[Row], after: list[Row], names: tuple[str, str]) -> str:
    out = [
        "| Start | Loops | Deserted | Crowded | Busy roads | Crossings/km | Distance error |",
        "|---|---|---|---|---|---|---|",
    ]

    def cell(b: float, a: float, fmt) -> str:
        return f"{fmt(b)} → {fmt(a)}"

    for b, a in zip(before, after, strict=True):
        out.append(
            f"| {b.place} | {b.loops} → {a.loops} | {cell(b.deserted, a.deserted, pct)} | "
            f"{cell(b.crowded, a.crowded, pct)} | {cell(b.busy, a.busy, pct)} | "
            f"{cell(b.crossings_per_km, a.crossings_per_km, lambda v: f'{v:.1f}')} | "
            f"{cell(b.error, a.error, pct)} |"
        )
    both = [(b, a) for b, a in zip(before, after, strict=True) if b.loops and a.loops]

    def avg(attr: str, i: int) -> float:
        return mean([getattr(pair[i], attr) for pair in both])

    out.append(
        f"| **Mean** ({len(both)} starts) | | **{cell(avg('deserted', 0), avg('deserted', 1), pct)}** | "
        f"{cell(avg('crowded', 0), avg('crowded', 1), pct)} | {cell(avg('busy', 0), avg('busy', 1), pct)} | "
        f"**{cell(avg('crossings_per_km', 0), avg('crossings_per_km', 1), lambda v: f'{v:.1f}')}** | "
        f"{cell(avg('error', 0), avg('error', 1), pct)} |"
    )
    out.append("")
    out.append(f"Before: `{names[0]}`. After: `{names[1]}`. Averages are over the loops Dora returns for each start.")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before", default="hiking-mountain", help="BRouter profile used as the baseline")
    parser.add_argument("--after", default="running-deserted", help="BRouter profile to evaluate")
    parser.add_argument("--km", type=float, default=8.0, help="wanted loop distance")
    parser.add_argument("--count", type=int, default=3, help="loops per start (as `count` in the API)")
    args = parser.parse_args()
    settings = Settings()
    before = asyncio.run(run_profile(settings, args.before, args.km, args.count))
    after = asyncio.run(run_profile(settings, args.after, args.km, args.count))
    print(render(before, after, (args.before, args.after)))


if __name__ == "__main__":
    main()

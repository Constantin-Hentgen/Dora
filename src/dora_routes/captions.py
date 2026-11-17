"""Short human captions, e.g. "6.2 km · 80 % away from cars and crowds · 3 crossings · 92 % never run".

The French wording is the one Bob's users already know."""

from __future__ import annotations

from typing import Literal

from dora_routes.activities import Activity, Surface
from dora_routes.loops import Loop

Lang = Literal["en", "fr"]

MARKED_MIN = 0.15  # below this, "on waymarked trails" is noise
CLIMB_MIN_M = 20

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "paved": "{pct} % paved",
        "unpaved": "{pct} % unpaved",
        "deserted": "{pct} % away from cars and crowds",
        "crossing": "{n} crossing",
        "crossings": "{n} crossings",
        "marked": "{pct} % on waymarked trails",
        "climb": "{m} m climb",
        "novelty": "{pct} % never done",
        "novelty.running": "{pct} % never run",
        "deserted.hiking": "{pct} % on trails away from cars",
        "novelty.hiking": "{pct} % never hiked",
    },
    "fr": {
        "paved": "{pct} % bitume",
        "unpaved": "{pct} % nature",
        "deserted": "{pct} % sans voitures ni foule",
        "crossing": "{n} traversée",
        "crossings": "{n} traversées",
        "marked": "{pct} % sur sentiers balisés",
        "climb": "D+ {m} m",
        "novelty": "{pct} % jamais fait",
        "novelty.running": "{pct} % jamais couru",
        "deserted.hiking": "{pct} % sur sentiers loin des voitures",
        "novelty.hiking": "{pct} % jamais randonné",
    },
}


def _km(km: float, lang: Lang) -> str:
    text = f"{km:.1f}"
    return (text.replace(".", ",") if lang == "fr" else text) + " km"


def _plural(n: int, lang: Lang) -> bool:
    return n > 1 if lang == "fr" else n != 1  # French: 0 and 1 are singular


def caption(loop: Loop, activity: Activity, surface: Surface = Surface.MIXED, lang: Lang = "en") -> str:
    t = TEXT[lang]

    def text(key: str) -> str:  # the activity's own wording when it has one
        return t.get(f"{key}.{activity.name}", t[key])

    pct = lambda share: f"{share * 100:.0f}"  # noqa: E731
    parts = [_km(loop.km, lang)]
    if surface is Surface.PAVED:
        parts.append(t["paved"].format(pct=pct(loop.shares.paved)))
    elif surface is Surface.UNPAVED:
        parts.append(t["unpaved"].format(pct=pct(1 - loop.shares.paved)))
    parts.append(text("deserted").format(pct=pct(loop.shares.deserted)))
    if activity.caption_crossings:
        parts.append(t["crossings" if _plural(loop.crossings, lang) else "crossing"].format(n=loop.crossings))
    if loop.shares.marked >= MARKED_MIN:
        parts.append(t["marked"].format(pct=pct(loop.shares.marked)))
    if loop.climb_m >= CLIMB_MIN_M:
        parts.append(t["climb"].format(m=loop.climb_m))
    if loop.novelty is not None:
        parts.append(text("novelty").format(pct=pct(loop.novelty)))
    return " · ".join(parts)

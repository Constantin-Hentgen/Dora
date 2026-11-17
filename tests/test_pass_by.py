"""Passing by a kind of place: Overpass lookup, routing through it, best effort when it fails."""

import httpx
import pytest
from helpers import FakeBRouter
from pydantic import ValidationError

from dora_routes import LoopRequest, find_loops_sync
from dora_routes.config import Settings
from dora_routes.geo import offset
from dora_routes.pois import KINDS, build_query

START = (48.644, 1.833)


def overpass_answer(kind_tags: dict[str, str], where: tuple[float, float], name: str = "Belvédère du Test"):
    return {"elements": [{"type": "node", "lat": where[0], "lon": where[1], "tags": {**kind_tags, "name": name}}]}


def transport(brouter: FakeBRouter, overpass) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "overpass-api.de":
            return overpass(request)
        return brouter(request)

    return httpx.MockTransport(handler)


def run(overpass, **fields):
    return find_loops_sync(
        start={"lat": START[0], "lon": START[1]}, distance_km=8, count=3, pass_by=["viewpoint"],
        transport=transport(FakeBRouter(), overpass), settings=Settings(), **fields,
    )  # fmt: skip


def test_loops_go_through_the_wanted_place() -> None:
    spot = offset(*START, 2.0, 100)
    result = run(lambda r: httpx.Response(200, json=overpass_answer({"tourism": "viewpoint"}, spot)))
    through = [lp for lp in result.loops if lp.passes]
    assert through, "no loop passes the viewpoint"
    assert through[0].passes[0].kind == "viewpoint"
    assert "via Belvédère du Test" in through[0].caption
    assert result.loops[0].passes, "a loop through the place ranks first"


def test_best_effort_when_overpass_is_down() -> None:
    result = run(lambda r: httpx.Response(504, text="busy"))
    assert result.loops and not any(lp.passes for lp in result.loops)


def test_best_effort_when_nothing_is_near() -> None:
    far = offset(*START, 40.0, 100)
    result = run(lambda r: httpx.Response(200, json=overpass_answer({"tourism": "viewpoint"}, far)))
    assert result.loops and not any(lp.via for lp in result.loops)


def test_places_of_another_kind_are_ignored() -> None:
    spot = offset(*START, 2.0, 100)
    result = run(lambda r: httpx.Response(200, json=overpass_answer({"amenity": "restaurant"}, spot)))
    assert not any(lp.passes for lp in result.loops)


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown kind"):
        LoopRequest(start={"lat": 48.8, "lon": 2.3}, distance_km=8, pass_by=["casino"])


def test_query_covers_every_tag_of_every_kind() -> None:
    q = build_query(["clearing", "supermarket"], (48.0, 2.0), 3.0)
    assert q.count("around:3000,48.00000,2.00000") == len(KINDS["clearing"]) + len(KINDS["supermarket"])
    assert '"shop"="supermarket"' in q and q.startswith("[out:json]")

"""The loop request, validated with pydantic. The library and the HTTP API (milestone 3) share it, so a request is
checked the same way whichever door it comes through."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dora_routes.activities import ACTIVITIES, Surface, get_activity
from dora_routes.geo import decode_polyline


class Coordinates(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class LoopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Coordinates | None = Field(None, description="Start and finish of the loops. Or give `place`.")
    place: str | None = Field(
        None, min_length=2, max_length=200, description="Free-text place, geocoded (e.g. 'bois de vincennes')."
    )
    near: Coordinates | None = Field(None, description="Where to look first when geocoding `place`.")
    distance_km: float | None = Field(None, ge=1, le=300, description="Wanted length. Or give `duration_min`.")
    duration_min: float | None = Field(None, gt=0, le=1440, description="Wanted duration, turned into a distance.")
    pace_min_per_km: float | None = Field(
        None, gt=0, le=30, description="Pace for `duration_min`; default per activity."
    )
    activity: str = Field("running", description=f"One of: {', '.join(ACTIVITIES)}.")
    surface: Surface = Surface.MIXED
    count: int = Field(3, ge=1, le=5, description="Number of distinct loops wanted.")
    avoid_tracks: list[str] = Field(
        default_factory=list, max_length=1000, description="Past activities as encoded polylines, for novelty."
    )
    never_done: bool = Field(False, description="Require at least 80 % of each loop outside `avoid_tracks`.")
    lang: Literal["en", "fr"] = "en"

    @field_validator("activity")
    @classmethod
    def _known_activity(cls, value: str) -> str:
        get_activity(value)
        return value

    @field_validator("avoid_tracks")
    @classmethod
    def _decodable(cls, tracks: list[str]) -> list[str]:
        for i, track in enumerate(tracks):
            try:
                decode_polyline(track)
            except (ValueError, IndexError) as exc:
                raise ValueError(f"avoid_tracks[{i}] is not a valid encoded polyline") from exc
        return tracks

    @model_validator(mode="after")
    def _one_of_each(self) -> Self:
        if (self.start is None) == (self.place is None):
            raise ValueError("give exactly one of `start` or `place`")
        if (self.distance_km is None) == (self.duration_min is None):
            raise ValueError("give exactly one of `distance_km` or `duration_min`")
        if self.distance_km is None and self.target_km() < 1:
            raise ValueError("the duration and pace give less than 1 km")
        return self

    def target_km(self) -> float:
        if self.distance_km is not None:
            return self.distance_km
        assert self.duration_min is not None
        pace = self.pace_min_per_km or get_activity(self.activity).default_pace_min_per_km
        return self.duration_min / pace


# ---------------------------------------------------------------------------
# Response models: shared by the HTTP service and the typed client
# ---------------------------------------------------------------------------


class Shares(BaseModel):
    """Share of the loop's length (0-1) by kind of way."""

    deserted: float = Field(description="Paths without cars, crowds or bikes: the running default.")
    paved: float
    crowded: float = Field(description="Pedestrian streets, cycleways and bike-shared paths.")
    busy: float = Field(description="Main roads (primary to tertiary, trunk and their links).")
    marked: float = Field(description="Waymarked hiking trails.")


class GeoJSONLineString(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]] = Field(description="[lon, lat] or [lon, lat, elevation] positions.")


class GeoJSONFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONLineString
    properties: dict[str, str | float | int | None] = Field(default_factory=dict)


class LoopOut(BaseModel):
    id: str = Field(description="Use with GET /v1/loops/{id}.gpx and .png while the result is cached.")
    distance_km: float
    climb_m: int
    bearing: float = Field(description="Direction the loop heads to from the start, in degrees (0 = north).")
    shares: Shares
    crossings: int = Field(description="Pedestrian crossings and traffic lights passed.")
    novelty: float | None = Field(None, description="Share never done before; set with avoid_tracks or never_done.")
    caption: str
    geojson: GeoJSONFeature
    gpx_url: str
    png_url: str
    gpx_base64: str | None = Field(None, description="With ?include=gpx.")
    png_base64: str | None = Field(None, description="With ?include=png.")


class PlaceOut(BaseModel):
    name: str
    lat: float
    lon: float


class LoopsResponse(BaseModel):
    start: Coordinates
    place: PlaceOut | None = None
    target_km: float
    loops: list[LoopOut]

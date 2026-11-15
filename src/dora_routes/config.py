"""Runtime configuration, read from environment variables (and an optional `.env` file).

Every variable is documented in `.env.example` and on the wiki "Configuration" page. Defaults are chosen so that
`docker compose up` works on a fresh machine and so that a plain `pip install dora-routes` user talks to a BRouter on
localhost rather than silently hammering the public instance.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from dora_routes import __version__

# Generic client name: never put a person's name or e-mail in requests to third-party services.
DEFAULT_USER_AGENT = "dora-routes/{version} (+https://github.com/Constantin-Hentgen/Dora)"

# Mainland France + Corsica. Brittany's western tip is at about -4.8°; Ouessant (-5.1°) would pull in a whole extra
# 5° column of mostly-Atlantic tiles, so it is left out of the default.
FRANCE_BBOX = (-4.8, 41.3, 9.6, 51.1)


class Settings(BaseSettings):
    """Service and library settings. Field names map to upper-case environment variables (`brouter_url` →
    `BROUTER_URL`)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    brouter_url: str = Field(
        "http://localhost:17777/brouter",
        description="BRouter HTTP endpoint. The public https://brouter.de/brouter is supported but strictly throttled.",
    )
    brouter_timeout_s: float = Field(15.0, gt=0, description="Timeout of a single BRouter call, in seconds.")
    request_timeout_s: float = Field(45.0, gt=0, description="Total time budget of one loop request, in seconds.")
    brouter_max_parallel: int = Field(
        4, ge=1, le=4, description="BRouter calls in flight at once. Never more than 4: brouter.de bans bursts."
    )
    nominatim_url: str = Field(
        "https://nominatim.openstreetmap.org/search", description="Nominatim search endpoint for place names."
    )
    tile_url: str = Field(
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        description="Map tile URL template. Point it at a self-hosted or commercial tile server for volume use.",
    )
    tile_cache_dir: str = Field(
        "~/.cache/dora-routes/tiles", description="Disk cache for map tiles (OSM tile policy: cache, low volume)."
    )
    map_size_px: int = Field(800, ge=200, le=2000, description="Width and height of the map image, in pixels.")
    user_agent: str = Field(DEFAULT_USER_AGENT, description="User-Agent sent to BRouter, Nominatim and tile servers.")
    log_level: str = Field("INFO", description="Python logging level.")

    segments_dir: str = Field("./segments4", description="Where BRouter rd5 segment files are stored.")
    segments_bbox: Annotated[tuple[float, float, float, float], NoDecode] = Field(
        FRANCE_BBOX, description="min_lon,min_lat,max_lon,max_lat of the area whose segments are downloaded."
    )
    segments_base_url: str = Field(
        "https://brouter.de/brouter/segments4/", description="Where rd5 segment files are downloaded from."
    )

    @field_validator("segments_bbox", mode="before")
    @classmethod
    def _parse_bbox(cls, value: object) -> object:
        """The environment gives the human-friendly `"-4.8,41.3,9.6,51.1"` form."""
        if isinstance(value, str):
            return tuple(float(part) for part in value.split(","))
        return value

    @field_validator("segments_bbox")
    @classmethod
    def _check_bbox(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        min_lon, min_lat, max_lon, max_lat = value
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("bbox must be min_lon,min_lat,max_lon,max_lat with min < max")
        return value

    @property
    def uses_public_brouter(self) -> bool:
        """The public brouter.de answers HTTP 403 when hammered: callers must throttle hard when this is true."""
        return (urlparse(self.brouter_url).hostname or "").endswith("brouter.de")

    def http_user_agent(self) -> str:
        return self.user_agent.format(version=__version__)


@lru_cache
def get_settings() -> Settings:
    """Settings are read once per process; tests clear the cache with `get_settings.cache_clear()`."""
    return Settings()

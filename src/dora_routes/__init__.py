"""Dora: loop routes (start = finish) of a chosen distance for running, hiking and walking.

The core library never imports a web framework; the HTTP service lives in `dora_routes.server` (extra `server`).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dora-routes")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.8.1"

from dora_routes.activities import ACTIVITIES, RUNNING, Activity, Surface, get_activity
from dora_routes.api import LoopsResult, PlaceNotFoundError, find_loops, find_loops_sync
from dora_routes.client import AsyncDoraClient, DoraClient, DoraError, ServiceUnavailableError
from dora_routes.gpx import to_gpx
from dora_routes.loops import Loop
from dora_routes.mapimage import render_map
from dora_routes.models import Coordinates, LoopRequest

__all__ = [
    "ACTIVITIES",
    "RUNNING",
    "Activity",
    "AsyncDoraClient",
    "Coordinates",
    "DoraClient",
    "DoraError",
    "Loop",
    "LoopRequest",
    "LoopsResult",
    "PlaceNotFoundError",
    "ServiceUnavailableError",
    "Surface",
    "__version__",
    "find_loops",
    "find_loops_sync",
    "get_activity",
    "render_map",
    "to_gpx",
]

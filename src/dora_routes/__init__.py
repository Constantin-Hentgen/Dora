"""Dora: loop routes (start = finish) of a chosen distance for running, hiking and cycling.

The core library never imports a web framework; the HTTP service lives in `dora_routes.server` (extra `server`).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dora-routes")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.1.0"

__all__ = ["__version__"]

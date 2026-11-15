"""loopwise: loop routes (start = finish) of a chosen distance for running, hiking and cycling.

The core library never imports a web framework; the HTTP service lives in `loopwise.server` (extra `server`).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("loopwise")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"

__all__ = ["__version__"]

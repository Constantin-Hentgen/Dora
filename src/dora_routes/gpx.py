"""GPX 1.1 export. The track points are the loop's points, unchanged: the same ones the map draws."""

from __future__ import annotations

from collections.abc import Sequence
from xml.sax.saxutils import escape

from dora_routes import __version__
from dora_routes.geo import Point

ODBL_URL = "https://opendatacommons.org/licenses/odbl/"


def to_gpx(points: Sequence[Point], name: str) -> bytes:
    """A single-track GPX with elevation when BRouter knows it, and the OpenStreetMap attribution the ODbL requires
    on derived data."""
    trkpts = "\n".join(
        f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}">'
        + (f"<ele>{ele:.0f}</ele>" if ele is not None else "")
        + "</trkpt>"
        for lat, lon, ele in points
    )
    title = escape(name)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<gpx version="1.1" creator="dora-routes {__version__}" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <metadata><name>{title}</name>"
        f'<copyright author="OpenStreetMap contributors"><license>{ODBL_URL}</license></copyright></metadata>\n'
        f"  <trk><name>{title}</name><trkseg>\n{trkpts}\n  </trkseg></trk>\n"
        "</gpx>\n"
    ).encode()

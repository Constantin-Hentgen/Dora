"""Map image: OpenStreetMap tiles stitched into a square PNG, with the loop drawn on top.

The OSM tile servers are run by volunteers. Their usage policy asks for an identifying User-Agent, low volume and
caching, so tiles are cached on disk for a week and fetched at most 2 at a time. For more than personal use, point
`TILE_URL` at a self-hosted or commercial tile server.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
import time
from collections.abc import Sequence
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from dora_routes.config import Settings
from dora_routes.geo import Point

logger = logging.getLogger(__name__)

TILE_PX = 256
MAX_ZOOM = 17
PADDING_PX = 40
TILE_TTL_S = 7 * 24 * 3600
TILE_PARALLEL = 2
ATTRIBUTION = "© OpenStreetMap contributors"
LINE_HALO, LINE_COLOR = "white", "#FF5A1F"
START_HALO, START_COLOR = "white", "#14968A"
MISSING_TILE = (230, 230, 230)


def project(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """WGS84 → Web Mercator pixel coordinates at `zoom` (the tile grid of every slippy map)."""
    n = TILE_PX * 2**zoom
    lat = max(min(lat, 85.0511), -85.0511)
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def fit_zoom(points: Sequence[Point], size_px: int) -> int:
    """Highest zoom at which the whole loop fits inside the image, padding included."""
    usable = size_px - 2 * PADDING_PX
    for zoom in range(MAX_ZOOM, 0, -1):
        xy = [project(p[0], p[1], zoom) for p in points]
        xs, ys = [x for x, __ in xy], [y for __, y in xy]
        if max(xs) - min(xs) <= usable and max(ys) - min(ys) <= usable:
            return zoom
    return 1


class TileCache:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.template = settings.tile_url
        # One sub-directory per tile server, so switching TILE_URL never mixes map styles.
        self.root = Path(settings.tile_cache_dir).expanduser() / hashlib.sha1(self.template.encode()).hexdigest()[:12]
        self.user_agent = settings.http_user_agent()
        self.transport = transport

    async def fetch_all(self, keys: Sequence[tuple[int, int, int]]) -> dict[tuple[int, int, int], Image.Image]:
        sem = asyncio.Semaphore(TILE_PARALLEL)
        async with httpx.AsyncClient(
            timeout=15, headers={"User-Agent": self.user_agent}, transport=self.transport, follow_redirects=True
        ) as client:

            async def one(key: tuple[int, int, int]) -> tuple[tuple[int, int, int], Image.Image]:
                async with sem:
                    return key, await self._tile(client, *key)

            return dict(await asyncio.gather(*(one(k) for k in keys)))

    async def _tile(self, client: httpx.AsyncClient, z: int, x: int, y: int) -> Image.Image:
        path = self.root / str(z) / str(x) / f"{y}.png"
        if path.exists() and time.time() - path.stat().st_mtime < TILE_TTL_S:
            return Image.open(path).convert("RGB")
        try:
            res = await client.get(self.template.format(z=z, x=x, y=y, s="a"))
            res.raise_for_status()
            img = Image.open(io.BytesIO(res.content)).convert("RGB")
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("tile %s/%s/%s unavailable: %s", z, x, y, exc)
            if path.exists():  # stale beats blank
                return Image.open(path).convert("RGB")
            return Image.new("RGB", (TILE_PX, TILE_PX), MISSING_TILE)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path, format="PNG")
        except OSError as exc:  # read-only cache: still render
            logger.warning("tile cache not writable at %s: %s", path, exc)
        return img


async def render_map(
    points: Sequence[Point], settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> bytes:
    """PNG of the loop on OSM tiles. `points` must be the loop's own points: the map shows what the GPX contains."""
    if not points:
        raise ValueError("cannot draw an empty loop")
    size = settings.map_size_px
    zoom = fit_zoom(points, size)
    xy = [project(p[0], p[1], zoom) for p in points]
    cx = (min(x for x, __ in xy) + max(x for x, __ in xy)) / 2
    cy = (min(y for __, y in xy) + max(y for __, y in xy)) / 2
    left, top = cx - size / 2, cy - size / 2
    n_tiles = 2**zoom
    keys = [
        (zoom, tx % n_tiles, ty)
        for tx in range(math.floor(left / TILE_PX), math.floor((left + size - 1) / TILE_PX) + 1)
        for ty in range(math.floor(top / TILE_PX), math.floor((top + size - 1) / TILE_PX) + 1)
        if 0 <= ty < n_tiles
    ]
    tiles = await TileCache(settings, transport).fetch_all(keys)

    image = Image.new("RGB", (size, size), MISSING_TILE)
    for tx in range(math.floor(left / TILE_PX), math.floor((left + size - 1) / TILE_PX) + 1):
        for ty in range(math.floor(top / TILE_PX), math.floor((top + size - 1) / TILE_PX) + 1):
            if (zoom, tx % n_tiles, ty) in tiles:
                image.paste(tiles[(zoom, tx % n_tiles, ty)], (round(tx * TILE_PX - left), round(ty * TILE_PX - top)))

    draw = ImageDraw.Draw(image)
    line = [(x - left, y - top) for x, y in xy]
    draw.line(line, fill=LINE_HALO, width=9, joint="curve")
    draw.line(line, fill=LINE_COLOR, width=5, joint="curve")
    sx, sy = line[0]
    draw.ellipse((sx - 9, sy - 9, sx + 9, sy + 9), fill=START_HALO)
    draw.ellipse((sx - 6, sy - 6, sx + 6, sy + 6), fill=START_COLOR)

    font = ImageFont.load_default(size=14)
    box = draw.textbbox((0, 0), ATTRIBUTION, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.rectangle((size - w - 12, size - h - 10, size, size), fill="white")
    draw.text((size - w - 6, size - h - 7), ATTRIBUTION, fill="#333333", font=font)

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()

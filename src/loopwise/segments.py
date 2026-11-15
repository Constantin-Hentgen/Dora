"""Download or update the BRouter `rd5` segment files covering a bounding box.

BRouter splits the planet into 5°×5° tiles named after their south-west corner (`E0_N45.rd5` covers lon 0–5°E,
lat 45–50°N). The public segment server rebuilds them roughly weekly from OpenStreetMap; refreshing monthly is plenty
for loop routing.

Designed for cron:

- only tiles that changed upstream are fetched (same size and `Last-Modified` → skipped), so a monthly run costs a
  few HEAD requests when nothing moved;
- each file is written to `<name>.part` and atomically renamed, so a running BRouter never opens a half-written tile;
- downloads are sequential with a generic User-Agent: no bursts on a free community server;
- the exit code is non-zero when a tile could not be refreshed, so cron mails you.

BRouter keeps segment files open, so restart it after an update (`docker compose restart brouter`).
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from enum import StrEnum
from pathlib import Path

import httpx

from loopwise.config import get_settings

logger = logging.getLogger(__name__)

TILE_DEG = 5
CHUNK_BYTES = 1 << 20
# Tiles are 50–400 MB: generous read timeout, but fail fast if the server is unreachable.
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=15.0)

BBox = tuple[float, float, float, float]


class TileStatus(StrEnum):
    UPDATED = "updated"
    CURRENT = "current"
    MISSING = "missing"  # no such tile upstream: open sea, nothing to route on
    FAILED = "failed"
    PLANNED = "planned"  # dry run


@dataclass(frozen=True)
class TileResult:
    name: str
    status: TileStatus
    detail: str = ""


def tile_name(lon: int, lat: int) -> str:
    """`(-5, 45)` → `"W5_N45.rd5"`: BRouter's name for the tile whose south-west corner is (lon, lat)."""
    return f"{'W' if lon < 0 else 'E'}{abs(lon)}_{'S' if lat < 0 else 'N'}{abs(lat)}.rd5"


def tiles_for_bbox(bbox: BBox) -> list[str]:
    """Names of the tiles covering `bbox` (min_lon, min_lat, max_lon, max_lat), west to east then south to north.

    A box edge lying exactly on a tile boundary does not pull in the next tile (`ceil` on the upper bound)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    lons = range(math.floor(min_lon / TILE_DEG) * TILE_DEG, math.ceil(max_lon / TILE_DEG) * TILE_DEG, TILE_DEG)
    lats = range(math.floor(min_lat / TILE_DEG) * TILE_DEG, math.ceil(max_lat / TILE_DEG) * TILE_DEG, TILE_DEG)
    return [tile_name(lon, lat) for lat in lats for lon in lons]


def _remote_mtime(response: httpx.Response) -> float | None:
    value = response.headers.get("last-modified")
    try:
        return parsedate_to_datetime(value).timestamp() if value else None
    except (TypeError, ValueError):
        return None


def sync_tile(client: httpx.Client, base_url: str, name: str, dest: Path, *, force: bool = False) -> TileResult:
    """Bring `dest/name` up to date with the server. Never leaves a partial file under the final name."""
    url = base_url.rstrip("/") + "/" + name
    path = dest / name
    try:
        head = client.head(url)
        if head.status_code == httpx.codes.NOT_FOUND:
            return TileResult(name, TileStatus.MISSING)
        head.raise_for_status()
        size = int(head.headers.get("content-length", -1))
        mtime = _remote_mtime(head)
        if not force and path.exists() and mtime is not None:
            stat = path.stat()
            if stat.st_size == size and abs(stat.st_mtime - mtime) < 1:
                return TileResult(name, TileStatus.CURRENT)
        part = path.with_name(name + ".part")
        try:
            with client.stream("GET", url) as response, part.open("wb") as out:
                response.raise_for_status()
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    out.write(chunk)
            got = part.stat().st_size
            if size >= 0 and got != size:
                raise OSError(f"truncated download: {got} of {size} bytes")
            if mtime is not None:  # remember the upstream date: next run compares against it
                os.utime(part, (mtime, mtime))
            part.replace(path)
        finally:
            part.unlink(missing_ok=True)
    except (httpx.HTTPError, OSError) as exc:
        return TileResult(name, TileStatus.FAILED, str(exc))
    return TileResult(name, TileStatus.UPDATED, f"{size / 1e6:.0f} MB" if size >= 0 else "")


def sync_segments(
    bbox: BBox,
    dest: Path,
    base_url: str,
    *,
    user_agent: str,
    force: bool = False,
    dry_run: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> list[TileResult]:
    """Sync every tile of `bbox` into `dest`, one at a time."""
    names = tiles_for_bbox(bbox)
    if dry_run:
        return [TileResult(name, TileStatus.PLANNED) for name in names]
    dest.mkdir(parents=True, exist_ok=True)
    results = []
    with httpx.Client(
        headers={"User-Agent": user_agent}, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True, transport=transport
    ) as client:
        for name in names:
            result = sync_tile(client, base_url, name, dest, force=force)
            log = logger.warning if result.status is TileStatus.FAILED else logger.info
            log("%s: %s %s", result.name, result.status, result.detail)
            results.append(result)
    return results


def exit_code(results: Sequence[TileResult], dest: Path, *, ok_if_present: bool) -> int:
    """0 when every tile is fresh. With `ok_if_present`, a failed refresh is tolerated when an older copy exists:
    that lets `docker compose up` start BRouter even if the segment server is briefly down."""
    if not any((dest / r.name).exists() for r in results):
        logger.error("no segment file available in %s: check the bounding box and the base URL", dest)
        return 1
    failed = [r for r in results if r.status is TileStatus.FAILED]
    if ok_if_present:
        failed = [r for r in failed if not (dest / r.name).exists()]
    return 1 if failed else 0


def _parse_bbox(text: str) -> BBox:
    parts = [float(p) for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("expected min_lon,min_lat,max_lon,max_lat")
    return parts[0], parts[1], parts[2], parts[3]


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="loopwise-segments", description="Download or update BRouter rd5 segment files for a bounding box."
    )
    parser.add_argument(
        "--bbox",
        type=_parse_bbox,
        default=settings.segments_bbox,
        help="min_lon,min_lat,max_lon,max_lat (default: SEGMENTS_BBOX, mainland France + Corsica)",
    )
    parser.add_argument("--dir", type=Path, default=Path(settings.segments_dir), help="default: SEGMENTS_DIR")
    parser.add_argument("--base-url", default=settings.segments_base_url, help="default: SEGMENTS_BASE_URL")
    parser.add_argument("--force", action="store_true", help="download even when the local copy looks current")
    parser.add_argument("--dry-run", action="store_true", help="list the tiles, download nothing")
    parser.add_argument(
        "--ok-if-present",
        action="store_true",
        help="exit 0 when a refresh fails but an older copy of the tile exists (for container start-up)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    results = sync_segments(
        args.bbox,
        args.dir,
        args.base_url,
        user_agent=settings.http_user_agent(),
        force=args.force,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print("\n".join(r.name for r in results))
        return 0
    counts = {s: sum(r.status is s for r in results) for s in TileStatus}
    logger.info("segments: %s", ", ".join(f"{n} {s}" for s, n in counts.items() if n))
    return exit_code(results, args.dir, ok_if_present=args.ok_if_present)


if __name__ == "__main__":
    sys.exit(main())

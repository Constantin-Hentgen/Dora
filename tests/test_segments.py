import os
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from dora_routes import segments
from dora_routes.config import FRANCE_BBOX
from dora_routes.segments import TileStatus, exit_code, sync_segments, tile_name, tiles_for_bbox

UA = "dora-routes/test"
STAMP = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


def test_tile_names_follow_brouter_convention() -> None:
    assert tile_name(-5, 45) == "W5_N45.rd5"
    assert tile_name(0, 45) == "E0_N45.rd5"
    assert tile_name(-75, -40) == "W75_S40.rd5"


def test_france_needs_nine_tiles() -> None:
    assert tiles_for_bbox(FRANCE_BBOX) == [
        "W5_N40.rd5", "E0_N40.rd5", "E5_N40.rd5",
        "W5_N45.rd5", "E0_N45.rd5", "E5_N45.rd5",
        "W5_N50.rd5", "E0_N50.rd5", "E5_N50.rd5",
    ]  # fmt: skip


def test_bbox_on_a_tile_boundary_does_not_pull_the_next_tile() -> None:
    assert tiles_for_bbox((0.0, 45.0, 5.0, 50.0)) == ["E0_N45.rd5"]
    assert tiles_for_bbox((2.2, 48.8, 2.5, 48.9)) == ["E0_N45.rd5"]  # Paris


class FakeServer:
    """Serves tiles from a dict; counts GETs to prove unchanged tiles are not downloaded again."""

    def __init__(self, tiles: dict[str, bytes], truncate: bool = False) -> None:
        self.tiles, self.truncate, self.gets = tiles, truncate, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == UA
        body = self.tiles.get(request.url.path.rsplit("/", 1)[-1])
        if body is None:
            return httpx.Response(404)
        headers = {"content-length": str(len(body)), "last-modified": format_datetime(STAMP, usegmt=True)}
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        self.gets += 1
        return httpx.Response(200, headers=headers, content=body[:-1] if self.truncate else body)


def run(tmp_path: Path, server: FakeServer, **kw) -> list[segments.TileResult]:
    return sync_segments(
        (0.5, 45.5, 6.0, 46.0), tmp_path, "https://seg.example/segments4/", user_agent=UA,
        transport=httpx.MockTransport(server), **kw,
    )  # fmt: skip


def test_downloads_then_skips_unchanged_tiles(tmp_path: Path) -> None:
    server = FakeServer({"E0_N45.rd5": b"abc", "E5_N45.rd5": b"defg"})
    first = run(tmp_path, server)
    assert [r.status for r in first] == [TileStatus.UPDATED, TileStatus.UPDATED]
    assert (tmp_path / "E5_N45.rd5").read_bytes() == b"defg"
    assert os.path.getmtime(tmp_path / "E0_N45.rd5") == STAMP.timestamp()
    second = run(tmp_path, server)
    assert [r.status for r in second] == [TileStatus.CURRENT, TileStatus.CURRENT] and server.gets == 2
    run(tmp_path, server, force=True)
    assert server.gets == 4


def test_sea_tile_is_missing_not_an_error(tmp_path: Path) -> None:
    results = run(tmp_path, FakeServer({"E0_N45.rd5": b"abc"}))
    assert [r.status for r in results] == [TileStatus.UPDATED, TileStatus.MISSING]
    assert exit_code(results, tmp_path, ok_if_present=False) == 0


def test_truncated_download_never_replaces_the_tile(tmp_path: Path) -> None:
    (tmp_path / "E0_N45.rd5").write_bytes(b"old")
    results = run(tmp_path, FakeServer({"E0_N45.rd5": b"newer", "E5_N45.rd5": b"x"}, truncate=True))
    assert results[0].status is TileStatus.FAILED and "truncated" in results[0].detail
    assert (tmp_path / "E0_N45.rd5").read_bytes() == b"old"
    assert not list(tmp_path.glob("*.part"))
    assert exit_code(results, tmp_path, ok_if_present=False) == 1  # cron must notice
    # The second tile has no older copy, so even container start-up mode fails.
    assert exit_code(results, tmp_path, ok_if_present=True) == 1


def test_ok_if_present_tolerates_a_failed_refresh_of_an_existing_tile(tmp_path: Path) -> None:
    (tmp_path / "E0_N45.rd5").write_bytes(b"old")
    (tmp_path / "E5_N45.rd5").write_bytes(b"old")
    results = run(tmp_path, FakeServer({"E0_N45.rd5": b"newer", "E5_N45.rd5": b"newer"}, truncate=True))
    assert exit_code(results, tmp_path, ok_if_present=True) == 0


def test_nothing_available_is_an_error(tmp_path: Path) -> None:
    results = run(tmp_path, FakeServer({}))
    assert exit_code(results, tmp_path, ok_if_present=True) == 1


def test_cli_dry_run_lists_tiles(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEGMENTS_BBOX", "2.2,48.8,2.5,48.9")
    assert segments.main(["--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "E0_N45.rd5"

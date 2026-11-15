"""Geometry ported from Bob: polylines, novelty, backtracking, dead-end spurs."""

import pytest
from helpers import line

from dora_routes.geo import (
    SpurRules,
    backtrack,
    bearing_gap,
    decode_polyline,
    find_spurs,
    haversine_km,
    novelty,
    offset,
    path_km,
    remove_spurs,
    visited_cells,
)


def test_decode_polyline_reference_example() -> None:
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert decode_polyline("") == []


def test_truncated_polyline_is_rejected() -> None:
    with pytest.raises(ValueError):
        decode_polyline("_p~iF~ps|U_")


def test_novelty_against_past_tracks() -> None:
    done = visited_cells([decode_polyline("_p~iF~ps|U_ulLnnqC")])
    assert novelty([(38.5, -120.2, None)], done) == 0.0
    assert novelty([(10.0, 10.0, None)], done) == 1.0
    # Sampled every ~20 m: a point in the middle of a long straight segment is still "done".
    assert novelty([(39.6, -120.575, None)], done) == 0.0


def test_offset_and_distance_agree() -> None:
    lat, lon = offset(48.85, 2.35, 2.0, 90)
    assert haversine_km((48.85, 2.35), (lat, lon)) == pytest.approx(2.0, rel=0.01)
    assert bearing_gap(350, 10) == 20 and bearing_gap(0, 180) == 180


def test_backtrack_detects_out_and_back() -> None:
    loop = line(48.8, 2.3, 0.001, 0, 40) + line(48.8, 2.31, 0.001, 0, 40)
    out = line(48.8, 2.3, 0.001, 0, 40)
    assert backtrack(loop) < 0.05 < backtrack(out + out[::-1])


def square_with_spur(spur_points: int = 6) -> tuple[list, int]:
    """Square loop (~0.9 km sides) with a ~200 m dead-end at one corner, run out and back."""
    side = 0.0004  # ~45 m between points
    a = line(48.80, 2.30, side, 0, 20)  # north
    corner = a[-1]
    spur_out = line(corner[0], corner[1], 0, -0.0006, spur_points)[1:]  # dead-end to the west
    spur_back = [*spur_out[::-1][1:], corner]
    b = line(corner[0], corner[1], 0, side * 1.5, 20)[1:]  # east
    c = line(b[-1][0], b[-1][1], -side, 0, 20)[1:]  # south
    d = line(c[-1][0], c[-1][1], 0, -side * 1.5, 20)[1:]  # west, back to start
    return a + spur_out + spur_back + b + c + d, len(spur_out) + len(spur_back)


def test_spur_is_detected_and_cut_locally() -> None:
    pts, spur_len = square_with_spur()
    assert len(find_spurs(pts)) == 1
    cleaned, left = remove_spurs(pts)
    assert left == 0 and len(cleaned) == len(pts) - spur_len
    assert round(path_km(pts) - path_km(cleaned), 2) == round(2 * path_km(pts[19:25]), 2)  # the out-and-back


def test_nested_spurs_need_a_second_pass() -> None:
    """A dead-end off a dead-end, the inner one longer than the rest of the outer one: the first pass only cuts part
    of it, and a spur is left that a second pass removes."""
    side = 0.0004
    a = line(48.80, 2.30, side, 0, 20)
    corner = a[-1]
    outer = line(corner[0], corner[1], 0, -0.0006, 8)[1:]  # ~45 m steps west
    mid = outer[3]
    inner_out = line(mid[0], mid[1], 0.0006, 0, 8)[1:]  # inner dead-end going north from the middle
    inner_back = [*inner_out[::-1][1:], mid]
    path_in = outer[:4] + inner_out + inner_back + outer[4:]
    path_back = [*outer[::-1][1:], corner]
    b = line(corner[0], corner[1], 0, side * 1.5, 20)[1:]
    c = line(b[-1][0], b[-1][1], -side, 0, 20)[1:]
    d = line(c[-1][0], c[-1][1], 0, -side * 1.5, 20)[1:]
    pts = a + path_in + path_back + b + c + d
    __, left_after_one_pass = remove_spurs(pts, SpurRules(passes=1))
    cleaned, left = remove_spurs(pts)
    assert left_after_one_pass == 1 and left == 0
    assert len(cleaned) == len(a) + len(b) + len(c) + len(d)


def test_a_block_loop_or_the_whole_loop_is_not_a_spur() -> None:
    square = (
        line(48.80, 2.30, 0.0004, 0, 6)
        + line(48.802, 2.30, 0, 0.0006, 6)[1:]
        + line(48.802, 2.303, -0.0004, 0, 6)[1:]
        + line(48.80, 2.303, 0, -0.0006, 6)[1:]
    )
    assert find_spurs(square) == []  # around the block: back by another way
    pts, __ = square_with_spur()
    assert (0, len(pts) - 1) not in find_spurs(pts)  # start = finish: that is the loop, not a dead-end

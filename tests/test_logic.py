"""Unit tests for pure helpers."""

from __future__ import annotations

from app.config import config
from app.geometry import (
    bbox_zone_hit,
    face_rank,
    point_in_polygon,
    valid_polygon,
)
from app.jsonutil import extract_json

SQUARE = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]


def test_point_in_polygon_inside() -> None:
    assert point_in_polygon((0.5, 0.5), SQUARE)
    assert point_in_polygon((0.21, 0.21), SQUARE)


def test_point_in_polygon_outside() -> None:
    assert not point_in_polygon((0.1, 0.5), SQUARE)
    assert not point_in_polygon((0.5, 0.9), SQUARE)


def test_point_in_polygon_degenerate() -> None:
    assert not point_in_polygon((0.5, 0.5), [])
    assert not point_in_polygon((0.5, 0.5), [[0.0, 0.0], [1.0, 1.0]])


def test_bbox_zone_hit_center() -> None:
    assert bbox_zone_hit((450, 450, 550, 550), SQUARE, 1000, 1000)


def test_bbox_zone_hit_corner() -> None:
    assert bbox_zone_hit((150, 150, 250, 250), SQUARE, 1000, 1000)


def test_bbox_zone_hit_miss() -> None:
    assert not bbox_zone_hit((0, 0, 100, 100), SQUARE, 1000, 1000)


def test_bbox_zone_hit_no_zone_means_full_frame() -> None:
    assert bbox_zone_hit((0, 0, 10, 10), [], 1000, 1000)


def test_valid_polygon() -> None:
    assert valid_polygon(SQUARE)
    assert valid_polygon([])
    assert not valid_polygon([[0.5, 0.5]])
    assert not valid_polygon([[1.5, 0.5], [0.5, 0.5], [0.1, 0.9]])
    assert not valid_polygon([[0.1, 0.1]] * 51)


def test_extract_json_plain() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced() -> None:
    text = '```json\n{"person_found": true, "name": "Alice", "confidence": 97}\n```'
    assert extract_json(text) == {
        "person_found": True,
        "name": "Alice",
        "confidence": 97,
    }


def test_extract_json_padded_prose() -> None:
    text = 'Sure! Here is the verdict: {"person_found": false, "name": null} done.'
    assert extract_json(text) == {"person_found": False, "name": None}


def test_extract_json_invalid() -> None:
    assert extract_json("") is None
    assert extract_json("no json here") is None
    assert extract_json("{broken") is None


def test_hls_window_math() -> None:
    expected = max(3, config.hls_window_seconds // config.hls_time)
    assert config.hls_list_size == expected
    assert config.hls_list_size * config.hls_time >= 30


def test_face_rank_prefers_larger_and_more_confident() -> None:
    small = face_rank((0, 0, 20, 20), 0.9)
    large = face_rank((0, 0, 80, 80), 0.9)
    assert large > small
    assert face_rank((0, 0, 40, 40), 0.95) > face_rank((0, 0, 40, 40), 0.5)


def test_face_rank_degenerate() -> None:
    assert face_rank((10, 10, 10, 10), 1.0) == 0.0
    assert face_rank((0, 0, 30, 30), 0.0) == 0.0

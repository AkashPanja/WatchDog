"""Confidence mapping tests (requires opencv + onnxruntime)."""

from __future__ import annotations

import pytest

pytest.importorskip("cv2")
pytest.importorskip("onnxruntime")

from app.face import similarity_to_confidence


def test_confidence_is_monotonic() -> None:
    values = [similarity_to_confidence(sim / 100) for sim in range(0, 101, 5)]
    assert values == sorted(values)


def test_confidence_bounds() -> None:
    assert 0.0 <= similarity_to_confidence(-5.0) <= 100.0
    assert 0.0 <= similarity_to_confidence(5.0) <= 100.0
    assert similarity_to_confidence(0.55) == pytest.approx(50.0, abs=0.2)


def test_confidence_default_threshold_reachable() -> None:
    assert similarity_to_confidence(0.75) >= 95.0

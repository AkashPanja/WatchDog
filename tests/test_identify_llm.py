"""Identification fallback tests (stubs vision deps when absent)."""

from __future__ import annotations

import asyncio
import base64
import sys
import types

try:  # pragma: no cover - real deployments have the real packages
    import cv2  # noqa: F401
    import numpy  # noqa: F401
except ImportError:
    numpy_stub = types.ModuleType("numpy")
    numpy_stub.array = lambda *args, **kwargs: list(args[0]) if args else []
    numpy_stub.asarray = lambda value, *args, **kwargs: value
    numpy_stub.ndarray = object
    numpy_stub.uint8 = int
    numpy_stub.float32 = float
    sys.modules["numpy"] = numpy_stub
    sys.modules["cv2"] = types.ModuleType("cv2")


GALLERY = [
    {
        "id": 1,
        "name": "Ann",
        "threshold": 95,
        "photos": [],
    }
]


def _verdict(name: str | None) -> dict:
    return {
        "person_found": name is not None,
        "name": name,
        "confidence": 91,
    }


def test_llm_identify_uses_head_crop_without_face(monkeypatch) -> None:
    from app import identify

    captured: dict = {}

    monkeypatch.setattr(identify.vision, "decode_jpeg", lambda data: object())
    monkeypatch.setattr(identify.face, "detect_faces", lambda image: [])
    monkeypatch.setattr(
        identify.vision,
        "persons_in_zone",
        lambda image, zone, confidence=0.3: [(0.0, 0.0, 10.0, 20.0, 0.9)],
    )
    monkeypatch.setattr(
        identify.face, "crop_person_head_bytes", lambda image, bbox: b"HEAD"
    )

    async def fake_llm(frame, references, face_crop=None):
        captured.update(frame=frame, references=references, face_crop=face_crop)
        return _verdict("Ann")

    monkeypatch.setattr(identify.llm, "identify_with_llm", fake_llm)

    result = asyncio.run(identify._identify_llm([b"frame"], GALLERY))
    assert captured["frame"] == b"HEAD"
    assert captured["face_crop"] is None
    assert result["name"] == "Ann"
    assert result["match"] is True
    assert result["source"] == "llm"
    assert result["face_crop_b64"] == base64.b64encode(b"HEAD").decode("ascii")


def test_llm_identify_falls_back_to_full_frame(monkeypatch) -> None:
    from app import identify

    captured: dict = {}

    monkeypatch.setattr(identify.vision, "decode_jpeg", lambda data: object())
    monkeypatch.setattr(identify.face, "detect_faces", lambda image: [])
    monkeypatch.setattr(
        identify.vision, "persons_in_zone", lambda image, zone, confidence=0.3: []
    )
    monkeypatch.setattr(identify, "_shrink", lambda data, width=900: b"FRAME")

    async def fake_llm(frame, references, face_crop=None):
        captured.update(frame=frame, face_crop=face_crop)
        return _verdict(None)

    monkeypatch.setattr(identify.llm, "identify_with_llm", fake_llm)

    result = asyncio.run(identify._identify_llm([b"frame"], GALLERY))
    assert captured["frame"] == b"FRAME"
    assert captured["face_crop"] is None
    assert result["match"] is False
    assert result["human_detected"] is False


def test_llm_identify_prefers_face_crop(monkeypatch) -> None:
    from app import identify

    captured: dict = {}

    monkeypatch.setattr(identify.vision, "decode_jpeg", lambda data: object())
    monkeypatch.setattr(
        identify.face,
        "detect_faces",
        lambda image: [{"bbox": [1, 1, 9, 9], "kps": [[0, 0]] * 5, "score": 0.9}],
    )
    monkeypatch.setattr(
        identify.face, "crop_face_bytes", lambda image, bbox: b"FACE"
    )
    monkeypatch.setattr(
        identify.face, "crop_face_b64", lambda image, bbox: "UFJFVklFVA=="
    )

    async def fake_llm(frame, references, face_crop=None):
        captured.update(frame=frame, face_crop=face_crop)
        return _verdict("Ann")

    monkeypatch.setattr(identify.llm, "identify_with_llm", fake_llm)

    result = asyncio.run(identify._identify_llm([b"frame"], GALLERY))
    assert captured["frame"] is None
    assert captured["face_crop"] == b"FACE"
    assert result["face_crop_b64"] == "UFJFVklFVA=="

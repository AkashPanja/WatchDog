"""Person identification: local, LLM or hybrid per-camera modes."""

from __future__ import annotations

import asyncio
import logging
import time

import cv2

from . import face, llm, vision
from .config import config
from .db import SessionLocal
from .models import Camera, Person
from .streams import manager

_LOGGER = logging.getLogger("watchdog.identify")

LLM_ESCALATION_MARGIN = 10.0
MAX_LLM_REFERENCES = 8
LLM_IMAGE_WIDTH = 900


class IdentifyError(Exception):
    """Raised when identification cannot run."""


def _gallery() -> list[dict]:
    with SessionLocal() as session:
        persons = session.query(Person).all()
        return [
            {
                "id": person.id,
                "name": person.name,
                "threshold": person.threshold,
                "photos": [
                    config.faces_dir / str(person.id) / photo.filename
                    for photo in person.photos
                ],
            }
            for person in persons
        ]


def _load_frame(camera_id: str) -> bytes:
    stream = manager.get_stream(camera_id)
    if stream is None:
        raise IdentifyError("camera stream is not running")
    frame, frame_ts = stream.latest_frame()
    if frame is None:
        raise IdentifyError("no video frame available yet")
    if time.monotonic() - frame_ts > config.frame_max_age * 2:
        raise IdentifyError("camera frame is stale")
    return frame


def _empty_result(source: str, human_detected: bool) -> dict:
    return {
        "match": False,
        "name": None,
        "confidence": 0.0,
        "threshold_used": None,
        "human_detected": human_detected,
        "face_crop_b64": None,
        "source": source,
    }


def _identify_python(image, gallery: list[dict]) -> dict:
    faces = face.detect_faces(image)
    human_detected = bool(faces)
    if not faces:
        try:
            human_detected = bool(vision.persons_in_zone(image, []))
        except Exception:  # noqa: BLE001 - best effort
            human_detected = False

    if not faces or not gallery:
        return _empty_result("python", human_detected)

    embedder = face.face_embedder()
    best: dict | None = None
    for candidate in faces:
        aligned = face.align_face(image, candidate["kps"])
        vector = embedder.embed_aligned(aligned) if aligned is not None else None
        if vector is None:
            continue
        for person in gallery:
            for photo in person["photos"]:
                reference = face.embed_photo(photo)
                if reference is None:
                    continue
                similarity = face.cosine_similarity(vector, reference)
                confidence = face.similarity_to_confidence(similarity)
                if best is None or confidence > best["confidence"]:
                    best = {
                        "name": person["name"],
                        "threshold": person["threshold"],
                        "confidence": confidence,
                        "similarity": round(similarity, 4),
                        "bbox": candidate["bbox"],
                    }

    if best is None:
        return _empty_result("python", human_detected)

    match = best["confidence"] >= best["threshold"]
    return {
        "match": match,
        "name": best["name"],
        "confidence": round(best["confidence"], 1),
        "threshold_used": best["threshold"] if match else None,
        "human_detected": human_detected,
        "face_crop_b64": face.crop_face_b64(image, best["bbox"]),
        "source": "python",
        "similarity": best["similarity"],
    }


def _shrink(data: bytes, width: int = LLM_IMAGE_WIDTH) -> bytes:
    image = vision.decode_jpeg(data)
    if image is None:
        return data
    height, current_width = image.shape[:2]
    if current_width <= width:
        return data
    scale = width / current_width
    resized = cv2.resize(image, (width, max(1, round(height * scale))))
    return vision.encode_jpeg(resized, 85)


def _reference_images(gallery: list[dict]) -> list[tuple[str, bytes]]:
    references: list[tuple[str, bytes]] = []
    for person in gallery:
        if len(references) >= MAX_LLM_REFERENCES:
            break
        for photo in person["photos"]:
            try:
                data = photo.read_bytes()
            except OSError:
                continue
            references.append((person["name"], _shrink(data)))
            break
    return references


async def _identify_llm(frame: bytes, gallery: list[dict]) -> dict:
    if not gallery:
        raise IdentifyError("no enrolled persons")
    verdict = await llm.identify_with_llm(
        _shrink(frame), _reference_images(gallery)
    )
    return _verdict_to_result(verdict, gallery, source="llm")


def _verdict_to_result(verdict: dict, gallery: list[dict], source: str) -> dict:
    by_name = {person["name"].lower(): person for person in gallery}
    name = verdict.get("name")
    try:
        confidence = float(verdict.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = round(min(100.0, max(0.0, confidence)), 1)
    person = by_name.get(str(name).lower()) if name else None
    found = bool(verdict.get("person_found")) and person is not None
    return {
        "match": found,
        "name": person["name"] if found else None,
        "confidence": confidence,
        "threshold_used": person["threshold"] if found else None,
        "human_detected": bool(verdict.get("person_found")),
        "face_crop_b64": None,
        "source": source,
    }


def identify(camera_id: str) -> dict:
    """Identify the person visible on a camera (blocking)."""
    frame = _load_frame(camera_id)
    gallery = _gallery()
    image = vision.decode_jpeg(frame)
    if image is None:
        raise IdentifyError("could not decode camera frame")

    with SessionLocal() as session:
        camera = session.get(Camera, camera_id)
        mode = camera.mode if camera is not None else "python"

    if mode == "llm":
        return asyncio.run(_identify_llm(frame, gallery))

    local = _identify_python(image, gallery)
    if mode != "hybrid" or local["match"]:
        return local

    threshold = None
    if local["name"]:
        threshold = next(
            (
                person["threshold"]
                for person in gallery
                if person["name"] == local["name"]
            ),
            None,
        )
    escalate = (
        not local["human_detected"]
        or not local["name"]
        or threshold is None
        or local["confidence"] < threshold - LLM_ESCALATION_MARGIN
    )
    if not escalate:
        return local

    try:
        remote = asyncio.run(_identify_llm(frame, gallery))
    except Exception as err:  # noqa: BLE001 - keep the local verdict
        _LOGGER.warning("LLM escalation failed for %s: %s", camera_id, err)
        return local
    return remote if remote["confidence"] >= local["confidence"] else local

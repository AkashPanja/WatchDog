"""Per-camera motion / human detection loop."""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time

import cv2
import numpy as np

from . import face, llm, vision, webhook
from .config import config
from .geometry import face_rank
from .limits import motion_limits

_LOGGER = logging.getLogger("watchdog.detector")

MOTION_ANALYSIS_WIDTH = 320
TRIGGER_FRAMES = 6


class CameraWorker(threading.Thread):
    """Watches one camera stream for motion and people inside the zone."""

    def __init__(self, stream, camera: dict) -> None:
        super().__init__(name=f"detect-{camera['id']}", daemon=True)
        self.stream = stream
        self.camera = camera
        self._stop = threading.Event()
        self._previous_gray: np.ndarray | None = None
        self._last_trigger = 0.0

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=5)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                _LOGGER.exception("detection error for %s", self.camera["id"])
                self._stop.wait(2.0)
            self._stop.wait(0.6)

    def _tick(self) -> None:
        frame_bytes, frame_ts = self.stream.latest_frame()
        if frame_bytes is None:
            return
        if time.monotonic() - frame_ts > config.frame_max_age:
            return

        image = vision.decode_jpeg(frame_bytes)
        if image is None:
            return

        height, width = image.shape[:2]
        analysis_w = min(MOTION_ANALYSIS_WIDTH, width)
        analysis_h = max(1, round(height * analysis_w / width))
        small = cv2.resize(image, (analysis_w, analysis_h))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask = vision.polygon_mask(analysis_w, analysis_h, self.camera["zone"])

        ratio = 0.0
        if self._previous_gray is not None:
            ratio = vision.motion_ratio(self._previous_gray, gray, mask)
        self._previous_gray = gray

        threshold, cooldown = motion_limits(self.camera)
        if ratio < threshold:
            return

        now = time.monotonic()
        if now - self._last_trigger < cooldown:
            return
        self._last_trigger = now

        snapshot = _snapshot_b64(image)
        webhook.notify(self.camera, "motion", round(ratio * 100, 1), snapshot)

        if self.camera["mode"] in ("python", "hybrid"):
            persons = vision.persons_in_zone(image, self.camera["zone"])
            if persons:
                best = round(max(person[4] for person in persons) * 100, 1)
                webhook.notify(self.camera, "human", best, snapshot)
        elif self.camera["mode"] == "llm":
            crop = self._trigger_crop(image)
            try:
                verdict = asyncio.run(llm.detect_human_with_llm(crop))
            except llm.LLMError as err:
                _LOGGER.warning("LLM detection failed for %s: %s", self.camera["id"], err)
                return
            if bool(verdict.get("person_found")):
                try:
                    confidence = float(verdict.get("confidence", 0))
                except (TypeError, ValueError):
                    confidence = 0.0
                webhook.notify(
                    self.camera, "human", round(min(100.0, max(0.0, confidence)), 1), snapshot
                )

    def _trigger_crop(self, image: np.ndarray) -> bytes | None:
        """Crop and zoom the best face found in the buffered trigger frames."""
        buffered = [data for data, _ in self.stream.recent_frames()][-TRIGGER_FRAMES:]
        best_rank = -1.0
        best_crop: bytes | None = None
        for data in buffered:
            candidate_frame = vision.decode_jpeg(data)
            if candidate_frame is None:
                continue
            detected = face.best_face(face.detect_faces(candidate_frame))
            if detected is None:
                continue
            rank = face_rank(tuple(detected["bbox"]), detected.get("score", 0.0))
            if rank > best_rank:
                best_rank = rank
                best_crop = face.crop_face_bytes(candidate_frame, detected["bbox"])
        if best_crop is not None:
            return best_crop
        persons = vision.persons_in_zone(image, self.camera["zone"], confidence=0.3)
        if persons:
            return face.crop_person_head_bytes(image, list(persons[0][:4]))
        return None


def _resize(image: np.ndarray, width: int) -> np.ndarray:
    height, current_width = image.shape[:2]
    if current_width <= width:
        return image
    scale = width / current_width
    return cv2.resize(image, (width, max(1, round(height * scale))))


def _snapshot_b64(image: np.ndarray) -> str:
    thumb = _resize(image, 640)
    return base64.b64encode(vision.encode_jpeg(thumb, 80)).decode("ascii")

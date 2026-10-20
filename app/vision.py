"""Local vision primitives: polygons, motion diff and human detection."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import store
from .geometry import bbox_zone_hit


def decode_jpeg(data: bytes) -> np.ndarray | None:
    if not data:
        return None
    array = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    return image


def encode_jpeg(image: np.ndarray, quality: int = 85) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buffer.tobytes()


def polygon_mask(
    width: int, height: int, polygon: list[list[float]]
) -> np.ndarray:
    """Binary mask (uint8) for a normalized polygon; full frame if empty."""
    mask = np.zeros((height, width), dtype=np.uint8)
    if len(polygon) < 3:
        mask[:] = 255
        return mask
    points = np.array(
        [[round(px * (width - 1)), round(py * (height - 1))] for px, py in polygon],
        dtype=np.int32,
    )
    cv2.fillPoly(mask, [points], 255)
    return mask


def motion_ratio(
    previous_gray: np.ndarray, current_gray: np.ndarray, mask: np.ndarray
) -> float:
    """Fraction of masked pixels whose brightness changed noticeably."""
    if previous_gray.shape != current_gray.shape:
        return 0.0
    previous = cv2.GaussianBlur(previous_gray, (21, 21), 0)
    current = cv2.GaussianBlur(current_gray, (21, 21), 0)
    delta = cv2.absdiff(previous, current)
    changed = (delta > 25) & (mask > 0)
    total = int((mask > 0).sum())
    if total == 0:
        return 0.0
    return float(changed.sum()) / float(total)


def draw_zone(image: np.ndarray, polygon: list[list[float]]) -> np.ndarray:
    """Draw the zone polygon on a copy of the frame."""
    if len(polygon) < 3:
        return image
    height, width = image.shape[:2]
    points = np.array(
        [[round(px * (width - 1)), round(py * (height - 1))] for px, py in polygon],
        dtype=np.int32,
    )
    canvas = image.copy()
    cv2.polylines(canvas, [points], True, (0, 255, 255), 3)
    return canvas


def nms(
    boxes: np.ndarray, scores: np.ndarray, threshold: float = 0.45, limit: int = 50
) -> list[int]:
    """Greedy non-maximum suppression. Returns kept indices."""
    if boxes.size == 0:
        return []
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0 and len(keep) < limit:
        index = int(order[0])
        keep.append(index)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[index, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[index, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[index, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[index, 3], boxes[rest, 3])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        area_index = (boxes[index, 2] - boxes[index, 0]) * (
            boxes[index, 3] - boxes[index, 1]
        )
        area_rest = (boxes[rest, 2] - boxes[rest, 0]) * (
            boxes[rest, 3] - boxes[rest, 1]
        )
        union = area_index + area_rest - inter
        iou = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
        order = rest[iou <= threshold]
    return keep


class HumanDetector:
    """YOLOv8n ONNX person detector."""

    def __init__(self, model_path: Path | None = None) -> None:
        import onnxruntime as ort

        path = model_path or store.ensure_yolo_model()
        self.session = ort.InferenceSession(
            str(path), providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        self.size = 640

    def detect(
        self, image: np.ndarray, confidence: float = 0.35
    ) -> list[tuple[float, float, float, float, float]]:
        height, width = image.shape[:2]
        scale = min(self.size / max(1, width), self.size / max(1, height))
        new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
        resized = cv2.resize(image, (new_w, new_h))
        canvas = np.zeros((self.size, self.size, 3), dtype=np.uint8)
        pad_x = (self.size - new_w) // 2
        pad_y = (self.size - new_h) // 2
        canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized

        blob = canvas[:, :, ::-1].astype(np.float32) / 255.0
        blob = blob.transpose(2, 0, 1)[np.newaxis, ...]

        output = self.session.run(None, {self.input_name: blob})[0]
        if output.ndim == 3:
            output = output[0]
        if output.shape[0] < output.shape[1]:
            output = output.T

        boxes = output[:, :4]
        class_scores = output[:, 4:]
        class_ids = class_scores.argmax(axis=1)
        scores = class_scores.max(axis=1)

        keep_mask = (class_ids == 0) & (scores >= confidence)
        if not keep_mask.any():
            return []

        raw = boxes[keep_mask]
        kept_scores = scores[keep_mask]
        cx, cy, bw, bh = raw[:, 0], raw[:, 1], raw[:, 2], raw[:, 3]
        x1 = cx - bw / 2
        y1 = cy - bh / 2
        x2 = cx + bw / 2
        y2 = cy + bh / 2
        stacked = np.stack([x1, y1, x2, y2], axis=1)

        results: list[tuple[float, float, float, float, float]] = []
        for index in nms(stacked, kept_scores):
            bx1 = float((stacked[index, 0] - pad_x) / scale)
            by1 = float((stacked[index, 1] - pad_y) / scale)
            bx2 = float((stacked[index, 2] - pad_x) / scale)
            by2 = float((stacked[index, 3] - pad_y) / scale)
            bx1 = max(0.0, min(bx1, width - 1.0))
            by1 = max(0.0, min(by1, height - 1.0))
            bx2 = max(0.0, min(bx2, width - 1.0))
            by2 = max(0.0, min(by2, height - 1.0))
            results.append((bx1, by1, bx2, by2, float(kept_scores[index])))
        return results


def persons_in_zone(
    image: np.ndarray,
    polygon: list[list[float]],
    confidence: float = 0.35,
) -> list[tuple[float, float, float, float, float]]:
    """Detect people whose bbox intersects the zone."""
    detector = _human_detector()
    height, width = image.shape[:2]
    return [
        box
        for box in detector.detect(image, confidence=confidence)
        if bbox_zone_hit(box[:4], polygon, width, height)
    ]


_HUMAN_DETECTOR: HumanDetector | None = None


def _human_detector() -> HumanDetector:
    global _HUMAN_DETECTOR
    if _HUMAN_DETECTOR is None:
        _HUMAN_DETECTOR = HumanDetector()
    return _HUMAN_DETECTOR

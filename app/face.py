"""Local face pipeline: SCRFD detection, alignment, ArcFace embeddings."""

from __future__ import annotations

import base64
import math
import threading
from pathlib import Path

import cv2
import numpy as np

from . import store
from .config import config
from .geometry import face_rank
from .vision import encode_jpeg

ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

SIMILARITY_CENTER = 0.55
SIMILARITY_SLOPE = 0.06


def similarity_to_confidence(similarity: float) -> float:
    """Map cosine similarity to a 0-100 calibrated confidence."""
    value = 100.0 / (1.0 + math.exp(-(similarity - SIMILARITY_CENTER) / SIMILARITY_SLOPE))
    return round(max(0.0, min(100.0, value)), 1)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-values))


class FaceDetector:
    """SCRFD (det_10g.onnx) face detector."""

    STRIDES = (8, 16, 32)
    ANCHORS_PER_POINT = 2

    def __init__(self, model_path: Path | None = None) -> None:
        import onnxruntime as ort

        det_path, _ = store.ensure_face_models()
        self.session = ort.InferenceSession(
            str(model_path or det_path), providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        self.size = 640

    def detect(
        self, image: np.ndarray, threshold: float = 0.5, max_faces: int = 10
    ) -> list[dict]:
        """Detect faces. Falls back to a 2x upscale for small faces."""
        faces = self._detect_once(image, threshold, max_faces)
        if not faces and max(image.shape[:2]) < 1600:
            scaled = cv2.resize(
                image,
                (image.shape[1] * 2, image.shape[0] * 2),
                interpolation=cv2.INTER_CUBIC,
            )
            upscaled = self._detect_once(scaled, threshold, max_faces)
            for face in upscaled:
                face["bbox"] = [value / 2.0 for value in face["bbox"]]
                face["kps"] = [
                    [point[0] / 2.0, point[1] / 2.0] for point in face["kps"]
                ]
            faces = upscaled
        faces.sort(key=lambda item: (item["bbox"][2] - item["bbox"][0]) * (item["bbox"][3] - item["bbox"][1]), reverse=True)
        return faces[:max_faces]

    def _detect_once(
        self, image: np.ndarray, threshold: float, max_faces: int
    ) -> list[dict]:
        height, width = image.shape[:2]
        scale = self.size / max(1, max(width, height))
        new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
        canvas = np.zeros((self.size, self.size, 3), dtype=np.uint8)
        canvas[:new_h, :new_w] = cv2.resize(image, (new_w, new_h))

        blob = (canvas[:, :, ::-1].astype(np.float32) - 127.5) / 128.0
        blob = blob.transpose(2, 0, 1)[np.newaxis, ...]

        outputs = self.session.run(None, {self.input_name: blob})
        feature_maps = len(outputs) // 3
        if feature_maps <= 0:
            return []

        candidates: list[dict] = []
        for index in range(feature_maps):
            scores = outputs[index]
            bbox_preds = outputs[index + feature_maps]
            kps_preds = outputs[index + feature_maps * 2]

            scores = np.asarray(scores).reshape(-1)
            if scores.size == 0:
                continue
            if float(scores.max()) > 1.5 or float(scores.min()) < 0.0:
                scores = _sigmoid(scores)

            stride = self.STRIDES[min(index, len(self.STRIDES) - 1)]
            fm_size = self.size // stride
            anchor_centers = np.stack(
                np.mgrid[:fm_size, :fm_size][::-1], axis=-1
            ).astype(np.float32)
            anchor_centers = (anchor_centers * stride).reshape(-1, 2)
            anchor_centers = np.repeat(anchor_centers, self.ANCHORS_PER_POINT, axis=0)

            bbox_preds = np.asarray(bbox_preds).reshape(-1, 4) * stride
            kps_preds = np.asarray(kps_preds).reshape(-1, 10) * stride

            pos = np.where(scores >= threshold)[0]
            if pos.size == 0:
                continue

            centers = anchor_centers[pos]
            distances = bbox_preds[pos]
            x1 = centers[:, 0] - distances[:, 0]
            y1 = centers[:, 1] - distances[:, 1]
            x2 = centers[:, 0] + distances[:, 2]
            y2 = centers[:, 1] + distances[:, 3]
            boxes = np.stack([x1, y1, x2, y2], axis=1) / scale

            kps = kps_preds[pos].reshape(-1, 5, 2)
            kps = np.stack(
                [
                    centers[:, 0:1] + kps[:, :, 0],
                    centers[:, 1:2] + kps[:, :, 1],
                ],
                axis=2,
            ) / scale

            for box, keypoints, score in zip(boxes, kps, scores[pos]):
                candidates.append(
                    {
                        "bbox": [float(v) for v in box],
                        "kps": [[float(p[0]), float(p[1])] for p in keypoints],
                        "score": float(score),
                    }
                )

        if not candidates:
            return []

        boxes = np.array([item["bbox"] for item in candidates], dtype=np.float32)
        scores = np.array([item["score"] for item in candidates], dtype=np.float32)
        keep = []
        order = scores.argsort()[::-1]
        while order.size > 0 and len(keep) < max_faces:
            best = int(order[0])
            keep.append(best)
            if order.size == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(boxes[best, 0], boxes[rest, 0])
            yy1 = np.maximum(boxes[best, 1], boxes[rest, 1])
            xx2 = np.minimum(boxes[best, 2], boxes[rest, 2])
            yy2 = np.minimum(boxes[best, 3], boxes[rest, 3])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            area_best = (boxes[best, 2] - boxes[best, 0]) * (boxes[best, 3] - boxes[best, 1])
            area_rest = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
            union = area_best + area_rest - inter
            iou = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
            order = rest[iou <= 0.4]

        return [candidates[i] for i in keep]


class FaceEmbedder:
    """ArcFace (w600k_r50.onnx) embedding extractor."""

    def __init__(self, model_path: Path | None = None) -> None:
        import onnxruntime as ort

        _, rec_path = store.ensure_face_models()
        self.session = ort.InferenceSession(
            str(model_path or rec_path), providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray | None:
        if aligned is None or aligned.size == 0:
            return None
        blob = cv2.dnn.blobFromImage(
            aligned,
            1.0 / 127.5,
            (112, 112),
            (127.5, 127.5, 127.5),
            swapRB=True,
        )
        output = self.session.run(None, {self.input_name: blob})[0]
        vector = np.asarray(output).reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if norm <= 0:
            return None
        return vector / norm

    def embed_image(self, image: np.ndarray) -> np.ndarray | None:
        """Embed the largest face, or a center crop fallback."""
        faces = _face_detector().detect(image)
        if faces:
            aligned = align_face(image, faces[0]["kps"])
            return self.embed_aligned(aligned)
        height, width = image.shape[:2]
        side = min(height, width)
        if side < 16:
            return None
        top = (height - side) // 2
        left = (width - side) // 2
        crop = image[top : top + side, left : left + side]
        crop = cv2.resize(crop, (112, 112))
        return self.embed_aligned(crop)


def align_face(image: np.ndarray, keypoints: list[list[float]]) -> np.ndarray | None:
    if len(keypoints) != 5:
        return None
    source = np.array(keypoints, dtype=np.float32)
    matrix, _ = cv2.estimateAffinePartial2D(source, ARCFACE_TEMPLATE, method=cv2.LMEDS)
    if matrix is None:
        return None
    return cv2.warpAffine(image, matrix, (112, 112), borderValue=0.0)


def crop_face_bytes(
    image: np.ndarray,
    bbox: list[float],
    padding: float = 0.4,
    target: int | None = None,
) -> bytes | None:
    """Crop a face with padding and zoom it up for vision models."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (float(value) for value in bbox)
    face_w = x2 - x1
    face_h = y2 - y1
    if face_w <= 1 or face_h <= 1:
        return None
    pad_x = face_w * padding
    pad_y = face_h * padding
    left = int(max(0, x1 - pad_x))
    top = int(max(0, y1 - pad_y))
    right = int(min(width - 1, x2 + pad_x))
    bottom = int(min(height - 1, y2 + pad_y))
    if right - left < 2 or bottom - top < 2:
        return None
    crop = image[top:bottom, left:right]
    size = target or config.face_crop_size
    scale = max(1.0, size / max(1, min(crop.shape[:2])))
    if scale > 1.0:
        crop = cv2.resize(
            crop,
            (int(crop.shape[1] * scale), int(crop.shape[0] * scale)),
            interpolation=cv2.INTER_CUBIC,
        )
    return encode_jpeg(crop, 90)


def crop_face_b64(image: np.ndarray, bbox: list[float], padding: float = 0.4) -> str | None:
    """Base64 JPEG preview of the cropped/zoomed face."""
    data = crop_face_bytes(image, bbox, padding=padding, target=240)
    return None if data is None else base64.b64encode(data).decode("ascii")


def crop_person_head_bytes(
    image: np.ndarray, bbox: list[float], target: int | None = None
) -> bytes | None:
    """Fallback crop: upper part of a person box (head area) when no face is found."""
    x1, y1, x2, y2 = (float(value) for value in bbox)
    height = max(1.0, y2 - y1)
    head_box = [x1, y1, x2, y1 + height * 0.45]
    return crop_face_bytes(image, head_box, padding=0.15, target=target)


def best_face(faces: list[dict]) -> dict | None:
    """Pick the highest ranked face (area x detector score)."""
    best: dict | None = None
    best_rank = -1.0
    for candidate in faces:
        rank = face_rank(tuple(candidate["bbox"]), candidate.get("score", 0.0))
        if rank > best_rank:
            best = candidate
            best_rank = rank
    return best


def best_face_crop(image: np.ndarray, target: int | None = None) -> bytes | None:
    """Detect the best face in a frame and return its zoomed crop."""
    candidate = best_face(detect_faces(image))
    if candidate is None:
        return None
    return crop_face_bytes(image, candidate["bbox"], target=target)


_face_detector_instance: FaceDetector | None = None
_embedder_instance: FaceEmbedder | None = None
_embedder_lock = threading.Lock()


def _face_detector() -> FaceDetector:
    global _face_detector_instance
    if _face_detector_instance is None:
        _face_detector_instance = FaceDetector()
    return _face_detector_instance


def detect_faces(image: np.ndarray) -> list[dict]:
    """Detect faces with the shared detector instance."""
    return _face_detector().detect(image)


def face_embedder() -> FaceEmbedder:
    global _embedder_instance
    with _embedder_lock:
        if _embedder_instance is None:
            _embedder_instance = FaceEmbedder()
    return _embedder_instance


_GALLERY_CACHE: dict[str, tuple[float, np.ndarray]] = {}
_GALLERY_LOCK = threading.Lock()


def embed_photo(path: Path) -> np.ndarray | None:
    """Embed a reference photo, cached by path + mtime."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _GALLERY_LOCK:
        cached = _GALLERY_CACHE.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
    image = cv2.imread(str(path))
    if image is None:
        return None
    vector = face_embedder().embed_image(image)
    if vector is None:
        return None
    with _GALLERY_LOCK:
        _GALLERY_CACHE[key] = (mtime, vector)
    return vector


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))

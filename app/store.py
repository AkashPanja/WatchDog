"""Settings persistence and model file downloads."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from .config import config
from .models import Setting

DEFAULT_SETTINGS: dict[str, str] = {
    "llm_provider": "openai",
    "llm_api_key": "",
    "llm_model": "",
    "llm_base_url": "",
}

YOLO_MODEL_URL = (
    "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.onnx"
)
FACE_MODEL_URL = (
    "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
)


def init_settings(session: Session) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        if session.get(Setting, key) is None:
            session.add(Setting(key=key, value=value))
    session.commit()


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row is not None else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    session.commit()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    tmp.replace(dest)


def ensure_yolo_model() -> Path:
    """Return the YOLOv8n ONNX model path, downloading it on first use."""
    path = config.models_dir / "yolov8n.onnx"
    if not path.exists():
        _download(YOLO_MODEL_URL, path)
    return path


def ensure_face_models() -> tuple[Path, Path]:
    """Return (detector, recognizer) ONNX paths from the buffalo_l pack."""
    det = config.models_dir / "det_10g.onnx"
    rec = config.models_dir / "w600k_r50.onnx"
    if det.exists() and rec.exists():
        return det, rec

    archive = config.models_dir / "buffalo_l.zip"
    if not archive.exists():
        _download(FACE_MODEL_URL, archive)

    extract_dir = config.models_dir / "buffalo_l"
    if not extract_dir.exists():
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extract_dir)
        for member in ("det_10g.onnx", "w600k_r50.onnx"):
            source = extract_dir / member
            if source.exists():
                shutil.copyfile(source, config.models_dir / member)
        shutil.rmtree(extract_dir, ignore_errors=True)

    archive.unlink(missing_ok=True)
    if not det.exists() or not rec.exists():
        raise RuntimeError("face models could not be unpacked from buffalo_l.zip")
    return det, rec

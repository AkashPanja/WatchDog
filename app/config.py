"""Runtime configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    """Process-wide settings."""

    def __init__(self) -> None:
        self.username = os.environ.get("APP_USERNAME", "").strip()
        self.password = os.environ.get("APP_PASSWORD", "")
        self.session_secret = os.environ.get("SESSION_SECRET", "").strip() or (
            f"watchdog::{self.username}::{self.password}"
        )
        self.session_max_age = _env_int("SESSION_MAX_AGE", 7 * 24 * 3600)
        self.cookie_secure = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
        self.data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        self.ffmpeg_bin = os.environ.get("FFMPEG_BIN", "ffmpeg")
        self.hls_time = max(1, _env_int("HLS_TIME", 2))
        self.hls_transcode = os.environ.get("HLS_TRANSCODE", "false").lower() == "true"
        self.hls_window_seconds = max(30, _env_int("HLS_WINDOW_SECONDS", 300))
        self.analysis_fps = max(1, _env_int("ANALYSIS_FPS", 2))
        self.stream_stall_seconds = max(0, _env_int("STREAM_STALL_SECONDS", 30))
        self.frame_buffer_frames = max(3, _env_int("FRAME_BUFFER_FRAMES", 15))
        self.face_crop_size = max(160, _env_int("FACE_CROP_SIZE", 512))
        self.analysis_width = max(320, _env_int("ANALYSIS_WIDTH", 1280))
        self.motion_threshold = _env_float("MOTION_THRESHOLD", 0.02)
        self.motion_cooldown = _env_float("MOTION_COOLDOWN", 15.0)
        self.webhook_timeout = _env_float("WEBHOOK_TIMEOUT", 10.0)
        self.webhook_retries = max(0, _env_int("WEBHOOK_RETRIES", 2))
        self.login_max_attempts = max(1, _env_int("LOGIN_MAX_ATTEMPTS", 10))
        self.login_window_seconds = max(30, _env_int("LOGIN_WINDOW_SECONDS", 300))
        self.llm_timeout = _env_float("LLM_TIMEOUT", 60.0)
        self.frame_max_age = _env_float("FRAME_MAX_AGE", 6.0)

    @property
    def hls_list_size(self) -> int:
        return max(3, self.hls_window_seconds // self.hls_time)

    @property
    def hls_dir(self) -> Path:
        return self.data_dir / "hls"

    @property
    def faces_dir(self) -> Path:
        return self.data_dir / "faces"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "watchdog.db"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.hls_dir, self.faces_dir, self.models_dir):
            path.mkdir(parents=True, exist_ok=True)


config = Config()

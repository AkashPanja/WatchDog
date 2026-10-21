"""ffmpeg-backed camera streams: live HLS + analysis frames."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque

from .config import config

_LOGGER = logging.getLogger("watchdog.streams")

MAX_BUFFER = 8 * 1024 * 1024


class CameraStream:
    """One ffmpeg process per camera: HLS playlist (files) + frame pipe (stdout).

    A single process is deliberate: many consumer cameras (Tapo, Reolink, …)
    allow only ONE RTSP viewer per stream, so a second ffmpeg connection to
    the same URL is dropped by the camera. Both outputs are muxed from one
    RTSP input here.
    """

    def __init__(self, camera_id: str, rtsp_url: str) -> None:
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.directory = config.hls_dir / camera_id
        self._process: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame: bytes | None = None
        self._frame_ts = 0.0
        self._recent: deque[tuple[bytes, float]] = deque(
            maxlen=config.frame_buffer_frames
        )
        self._lock = threading.Lock()

    def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        for stale in self.directory.glob("*.ts"):
            stale.unlink(missing_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._supervise, name=f"stream-{self.camera_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill(self._process)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._process = None

    def latest_frame(self) -> tuple[bytes | None, float]:
        """Return the newest JPEG frame and its capture monotonic timestamp."""
        with self._lock:
            return self._frame, self._frame_ts

    def recent_frames(self) -> list[tuple[bytes, float]]:
        """Buffered analysis frames (newest last) used on triggers."""
        with self._lock:
            return list(self._recent)

    def _kill(self, process: subprocess.Popen | None) -> None:
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                process.kill()
            except Exception:
                _LOGGER.debug("could not kill ffmpeg process", exc_info=True)

    def _start_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._kill(self._process)
        codec = (
            [
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-tune", "zerolatency",
                "-b:v", "2500k",
            ]
            if config.hls_transcode
            else ["-c:v", "copy"]
        )
        args = [
            config.ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            self.rtsp_url,
            # output 1: rolling HLS playlist (browser live view)
            "-map", "0:v:0",
            "-an",
            *codec,
            "-f",
            "hls",
            "-hls_time",
            str(config.hls_time),
            "-hls_list_size",
            str(config.hls_list_size),
            "-hls_flags",
            "delete_segments+independent_segments",
            "-hls_segment_filename",
            str(self.directory / "seg_%06d.ts"),
            str(self.directory / "index.m3u8"),
            # output 2: analysis frames on stdout (same RTSP connection)
            "-map", "0:v:0",
            "-an",
            "-vf",
            f"fps={config.analysis_fps},scale='min({config.analysis_width},iw)':-2",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "-q:v",
            "4",
            "-",
        ]
        self._process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

    def _supervise(self) -> None:
        while not self._stop.is_set():
            try:
                self._start_process()
                buffer = b""
                process = self._process
                if process is None or process.stdout is None:
                    time.sleep(2)
                    continue
                while not self._stop.is_set():
                    chunk = process.stdout.read(8192)
                    if not chunk:
                        break
                    buffer += chunk
                    while True:
                        end = buffer.find(b"\xff\xd9")
                        if end == -1:
                            break
                        frame = buffer[: end + 2]
                        buffer = buffer[end + 2 :]
                        if len(frame) > 1024:
                            with self._lock:
                                self._frame = frame
                                self._frame_ts = time.monotonic()
                                self._recent.append((frame, self._frame_ts))
                    if len(buffer) > MAX_BUFFER:
                        buffer = buffer[-1 << 20 :]
            except Exception:
                _LOGGER.exception("stream supervisor error for %s", self.camera_id)
            finally:
                self._kill(self._process)
                self._process = None
            if not self._stop.is_set():
                time.sleep(2)


def _camera_snapshot(camera) -> dict:
    """Plain dict handed to the stream/detector workers (never an ORM row)."""
    return {
        "id": camera.id,
        "name": camera.name,
        "mode": camera.mode,
        "rtsp_url": camera.rtsp_url,
        "zone": camera.zone_polygon,
        "webhook_url": camera.webhook_url,
        "webhook_secret": camera.webhook_secret,
        "motion_threshold": camera.motion_threshold,
        "motion_cooldown": camera.motion_cooldown,
    }


class StreamManager:
    """Owns all camera streams and detection workers."""

    def __init__(self) -> None:
        self._streams: dict[str, CameraStream] = {}
        self._workers: dict[str, object] = {}

    def start_all(self) -> None:
        from .db import SessionLocal
        from .models import Camera

        with SessionLocal() as session:
            cameras = session.query(Camera).all()
            camera_snapshots = [_camera_snapshot(camera) for camera in cameras]
        for snapshot in camera_snapshots:
            self._start(snapshot)

    def start_camera(self, camera) -> None:
        self._stop_camera(camera.id)
        self._start(_camera_snapshot(camera))

    def update_camera(self, camera) -> None:
        """Apply zone/mode/webhook changes without restarting ffmpeg."""
        worker = self._workers.get(camera.id)
        if worker is None:
            self.start_camera(camera)
            return
        worker.camera.update(_camera_snapshot(camera))  # type: ignore[attr-defined]

    def _start(self, snapshot: dict) -> None:
        from .detector import CameraWorker

        rtsp_url = snapshot.get("rtsp_url") or ""
        if not rtsp_url:
            return
        stream = CameraStream(snapshot["id"], rtsp_url)
        stream.start()
        worker = CameraWorker(stream, snapshot)
        worker.start()
        self._streams[snapshot["id"]] = stream
        self._workers[snapshot["id"]] = worker

    def stop_camera(self, camera_id: str) -> None:
        self._stop_camera(camera_id)

    def _stop_camera(self, camera_id: str) -> None:
        worker = self._workers.pop(camera_id, None)
        if worker is not None:
            worker.stop()  # type: ignore[attr-defined]
        stream = self._streams.pop(camera_id, None)
        if stream is not None:
            stream.stop()

    def get_stream(self, camera_id: str) -> CameraStream | None:
        return self._streams.get(camera_id)

    def stop_all(self) -> None:
        for camera_id in list(self._workers):
            self._stop_camera(camera_id)
        for camera_id in list(self._streams):
            self._stop_camera(camera_id)


manager = StreamManager()

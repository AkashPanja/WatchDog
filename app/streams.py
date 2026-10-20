"""ffmpeg-backed camera streams: live HLS + analysis frames."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from .config import config

_LOGGER = logging.getLogger("watchdog.streams")

MAX_BUFFER = 8 * 1024 * 1024


class CameraStream:
    """One ffmpeg HLS encoder plus one ffmpeg frame pipe per camera."""

    def __init__(self, camera_id: str, rtsp_url: str) -> None:
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.directory = config.hls_dir / camera_id
        self._hls_process: subprocess.Popen | None = None
        self._frames_process: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame: bytes | None = None
        self._frame_ts = 0.0
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
        self._kill(self._frames_process)
        self._kill(self._hls_process)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._hls_process = None
        self._frames_process = None

    def latest_frame(self) -> tuple[bytes | None, float]:
        """Return the newest JPEG frame and its capture monotonic timestamp."""
        with self._lock:
            return self._frame, self._frame_ts

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

    def _start_hls(self) -> None:
        if self._hls_process is not None and self._hls_process.poll() is None:
            return
        self._kill(self._hls_process)
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
        ]
        self._hls_process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _start_frames(self) -> None:
        if self._frames_process is not None and self._frames_process.poll() is None:
            return
        self._kill(self._frames_process)
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
        self._frames_process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

    def _supervise(self) -> None:
        while not self._stop.is_set():
            try:
                self._start_frames()
                self._start_hls()
                buffer = b""
                process = self._frames_process
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
                    if len(buffer) > MAX_BUFFER:
                        buffer = buffer[-1 << 20 :]
                    if self._hls_process is not None and self._hls_process.poll() is not None:
                        self._start_hls()
            except Exception:
                _LOGGER.exception("stream supervisor error for %s", self.camera_id)
            finally:
                self._kill(self._frames_process)
                self._frames_process = None
            if not self._stop.is_set():
                time.sleep(2)
        self._kill(self._hls_process)
        self._hls_process = None


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
            camera_snapshots = [
                {
                    "id": camera.id,
                    "name": camera.name,
                    "mode": camera.mode,
                    "rtsp_url": camera.rtsp_url,
                    "zone": camera.zone_polygon,
                    "webhook_url": camera.webhook_url,
                    "webhook_secret": camera.webhook_secret,
                }
                for camera in cameras
            ]
        for snapshot in camera_snapshots:
            self._start(snapshot)

    def start_camera(self, camera) -> None:
        self._stop_camera(camera.id)
        self._start(
            {
                "id": camera.id,
                "name": camera.name,
                "mode": camera.mode,
                "rtsp_url": camera.rtsp_url,
                "zone": camera.zone_polygon,
                "webhook_url": camera.webhook_url,
                "webhook_secret": camera.webhook_secret,
            }
        )

    def update_camera(self, camera) -> None:
        """Apply zone/mode/webhook changes without restarting ffmpeg."""
        worker = self._workers.get(camera.id)
        if worker is None:
            self.start_camera(camera)
            return
        worker.camera.update(  # type: ignore[attr-defined]
            {
                "name": camera.name,
                "mode": camera.mode,
                "zone": camera.zone_polygon,
                "webhook_url": camera.webhook_url,
                "webhook_secret": camera.webhook_secret,
            }
        )

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

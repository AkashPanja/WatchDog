"""Camera management, live stream, zone, webhook and identify endpoints."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import discover as discover_module
from .. import identify as identify_module
from ..config import config
from ..db import SessionLocal
from ..llm import DEFAULT_MODELS, PROVIDERS
from ..models import Camera
from ..onvif import OnvifError, resolve_rtsp
from ..store import get_setting
from ..streams import manager

router = APIRouter(prefix="/api/cameras")

SegmentName = re.compile(r"^(index\.m3u8|seg_\d{6}\.ts)$")
MODES = ("python", "llm", "hybrid")


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["python", "llm", "hybrid"] = "python"
    host: str = ""
    port: int = 80
    onvif_username: str = ""
    onvif_password: str = ""
    rtsp_url: str = ""
    motion_threshold: float | None = Field(default=None, gt=0, le=1)
    motion_cooldown: float | None = Field(default=None, ge=0, le=3600)


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    mode: Literal["python", "llm", "hybrid"] | None = None
    host: str | None = None
    port: int | None = None
    onvif_username: str | None = None
    onvif_password: str | None = None
    rtsp_url: str | None = None
    motion_threshold: float | None = Field(default=None, gt=0, le=1)
    motion_cooldown: float | None = Field(default=None, ge=0, le=3600)


class ZonePayload(BaseModel):
    polygon: list[list[float]] = Field(default_factory=list)


class WebhookPayload(BaseModel):
    url: str = ""
    secret: str = ""


class OnvifTestPayload(BaseModel):
    host: str
    port: int = 80
    username: str = ""
    password: str = ""


def _require_llm_for_mode(mode: str) -> None:
    if mode == "python":
        return
    with SessionLocal() as session:
        provider = get_setting(session, "llm_provider", "openai")
        api_key = get_setting(session, "llm_api_key", "")
        base_url = get_setting(session, "llm_base_url", "")
        model = get_setting(session, "llm_model", "") or DEFAULT_MODELS.get(
            provider, ""
        )
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="invalid LLM provider")
    if provider != "openai_compatible" and not api_key:
        raise HTTPException(
            status_code=400,
            detail="configure an LLM API key in Settings before selecting llm/hybrid",
        )
    if not model:
        raise HTTPException(status_code=400, detail="configure an LLM model in Settings")
    if provider == "openai_compatible" and not base_url:
        raise HTTPException(
            status_code=400,
            detail="configure an LLM base URL in Settings for OpenAI-compatible",
        )


def _camera_or_404(session, camera_id: str) -> Camera:
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    return camera


@router.get("")
def list_cameras() -> list[dict]:
    with SessionLocal() as session:
        cameras = session.query(Camera).order_by(Camera.created_at).all()
        return [camera.public_dict() for camera in cameras]


@router.get("/discover")
async def discover_cameras(timeout: float = 3.0) -> list[dict]:
    """Probe the local network for ONVIF cameras (WS-Discovery)."""
    return await asyncio.to_thread(discover_module.discover_devices, timeout)


@router.post("")
async def create_camera(payload: CameraCreate) -> dict:
    _require_llm_for_mode(payload.mode)

    rtsp_url = payload.rtsp_url.strip()
    if not rtsp_url:
        if not payload.host.strip():
            raise HTTPException(
                status_code=400, detail="provide an ONVIF host or an RTSP URL"
            )
        try:
            rtsp_url = await resolve_rtsp(
                payload.host.strip(),
                payload.port,
                payload.onvif_username,
                payload.onvif_password,
            )
        except OnvifError as err:
            raise HTTPException(
                status_code=400, detail=f"ONVIF connection failed: {err}"
            ) from err

    with SessionLocal() as session:
        camera = Camera(
            name=payload.name.strip(),
            mode=payload.mode,
            host=payload.host.strip(),
            port=payload.port,
            onvif_username=payload.onvif_username,
            onvif_password=payload.onvif_password,
            rtsp_url=rtsp_url,
            motion_threshold=payload.motion_threshold,
            motion_cooldown=payload.motion_cooldown,
        )
        session.add(camera)
        session.commit()
        session.refresh(camera)
        manager.start_camera(camera)
        return camera.public_dict()


@router.put("/{camera_id}")
async def update_camera(camera_id: str, payload: CameraUpdate) -> dict:
    with SessionLocal() as session:
        camera = _camera_or_404(session, camera_id)
        mode = payload.mode if payload.mode is not None else camera.mode
        _require_llm_for_mode(mode)
        restream = bool(payload.rtsp_url) and payload.rtsp_url.strip() != camera.rtsp_url
        if payload.name is not None:
            camera.name = payload.name.strip()
        camera.mode = mode
        if payload.host is not None and payload.host.strip() != camera.host:
            camera.host = payload.host.strip()
            restream = True
        if payload.port is not None and payload.port != camera.port:
            camera.port = payload.port
            restream = True
        if (
            payload.onvif_username is not None
            and payload.onvif_username != camera.onvif_username
        ):
            camera.onvif_username = payload.onvif_username
            restream = True
        if (
            payload.onvif_password is not None
            and payload.onvif_password != camera.onvif_password
        ):
            camera.onvif_password = payload.onvif_password
            restream = True
        if payload.rtsp_url is not None:
            camera.rtsp_url = payload.rtsp_url.strip()
        if "motion_threshold" in payload.model_fields_set:
            camera.motion_threshold = payload.motion_threshold
        if "motion_cooldown" in payload.model_fields_set:
            camera.motion_cooldown = payload.motion_cooldown
        if restream and camera.host and not payload.rtsp_url:
            try:
                camera.rtsp_url = await resolve_rtsp(
                    camera.host,
                    camera.port,
                    camera.onvif_username,
                    camera.onvif_password,
                )
            except OnvifError as err:
                raise HTTPException(
                    status_code=400, detail=f"ONVIF connection failed: {err}"
                ) from err
        elif restream and not camera.rtsp_url:
            if not camera.host:
                raise HTTPException(
                    status_code=400,
                    detail="provide an RTSP URL or ONVIF host to restart the stream",
                )
            try:
                camera.rtsp_url = await resolve_rtsp(
                    camera.host,
                    camera.port,
                    camera.onvif_username,
                    camera.onvif_password,
                )
            except OnvifError as err:
                raise HTTPException(
                    status_code=400, detail=f"ONVIF connection failed: {err}"
                ) from err
        session.commit()
        session.refresh(camera)
        if restream:
            manager.start_camera(camera)
        else:
            manager.update_camera(camera)
        return camera.public_dict()


@router.delete("/{camera_id}")
def delete_camera(camera_id: str) -> dict:
    with SessionLocal() as session:
        camera = _camera_or_404(session, camera_id)
        session.delete(camera)
        session.commit()
    manager.stop_camera(camera_id)
    shutil.rmtree(config.hls_dir / camera_id, ignore_errors=True)
    return {"deleted": camera_id}


@router.get("/{camera_id}/stream.m3u8")
def stream_playlist(camera_id: str) -> FileResponse:
    path = config.hls_dir / camera_id / "index.m3u8"
    if not path.exists():
        raise HTTPException(status_code=503, detail="stream is starting, retry shortly")
    return FileResponse(
        path,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache, no-store"},
    )


@router.get("/{camera_id}/hls/{filename}")
def hls_segment(camera_id: str, filename: str) -> FileResponse:
    if not SegmentName.match(filename):
        raise HTTPException(status_code=404, detail="not found")
    path = config.hls_dir / camera_id / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="segment not found")
    media_type = (
        "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t"
    )
    return FileResponse(
        path, media_type=media_type, headers={"Cache-Control": "no-cache"}
    )


@router.get("/{camera_id}/snapshot.jpg")
def snapshot(camera_id: str) -> Response:
    with SessionLocal() as session:
        _camera_or_404(session, camera_id)
    stream = manager.get_stream(camera_id)
    if stream is None:
        raise HTTPException(status_code=503, detail="camera stream is not running")
    frame, _ = stream.latest_frame()
    if frame is None:
        raise HTTPException(status_code=503, detail="no frame available yet")
    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.put("/{camera_id}/zone")
def set_zone(camera_id: str, payload: ZonePayload) -> dict:
    polygon = payload.polygon
    if polygon and len(polygon) < 3:
        raise HTTPException(status_code=400, detail="a zone needs at least 3 points")
    if len(polygon) > 50:
        raise HTTPException(status_code=400, detail="a zone can have at most 50 points")
    for point in polygon:
        if len(point) != 2 or not all(0.0 <= float(value) <= 1.0 for value in point):
            raise HTTPException(
                status_code=400, detail="zone points must be normalized to 0-1"
            )
    with SessionLocal() as session:
        camera = _camera_or_404(session, camera_id)
        camera.zone = json.dumps(
            [[round(float(x), 5), round(float(y), 5)] for x, y in polygon]
        )
        session.commit()
        session.refresh(camera)
        manager.update_camera(camera)
        return {"polygon": camera.zone_polygon}


@router.put("/{camera_id}/webhook")
def set_webhook(camera_id: str, payload: WebhookPayload) -> dict:
    with SessionLocal() as session:
        camera = _camera_or_404(session, camera_id)
        camera.webhook_url = payload.url.strip()
        camera.webhook_secret = payload.secret
        session.commit()
        session.refresh(camera)
        manager.update_camera(camera)
        return {
            "webhook_url": camera.webhook_url,
            "has_webhook_secret": bool(camera.webhook_secret),
        }


@router.post("/{camera_id}/identify")
def identify(camera_id: str) -> dict:
    with SessionLocal() as session:
        _camera_or_404(session, camera_id)
    try:
        return identify_module.identify(camera_id)
    except identify_module.IdentifyError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err
    except Exception as err:
        raise HTTPException(
            status_code=500, detail=f"identification failed: {err}"
        ) from err


@router.post("/test-onvif")
async def test_onvif(payload: OnvifTestPayload) -> dict:
    try:
        uri = await resolve_rtsp(
            payload.host.strip(),
            payload.port,
            payload.username,
            payload.password,
        )
    except OnvifError as err:
        raise HTTPException(
            status_code=400, detail=f"ONVIF connection failed: {err}"
        ) from err
    return {"rtsp_url": uri}


@router.get("/{camera_id}/{filename}")
def hls_segment_relative(camera_id: str, filename: str) -> FileResponse:
    """Serve playlist-relative segments: /stream.m3u8 resolves next to itself."""
    if not SegmentName.match(filename):
        raise HTTPException(status_code=404, detail="not found")
    if filename == "index.m3u8":
        return stream_playlist(camera_id)
    return hls_segment(camera_id, filename)

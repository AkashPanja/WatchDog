"""ONVIF discovery helpers: resolve an RTSP stream URI."""

from __future__ import annotations

import asyncio
import inspect


class OnvifError(Exception):
    """Raised when an ONVIF camera cannot be reached or queried."""


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def resolve_rtsp(
    host: str, port: int, username: str, password: str, timeout: float = 10.0
) -> str:
    """Ask an ONVIF camera for its first RTSP stream URI."""
    if not host:
        raise OnvifError("camera host is required")

    def _build():
        from onvif import ONVIFCamera

        return ONVIFCamera(host, int(port), username, password)

    try:
        camera = await asyncio.wait_for(asyncio.to_thread(_build), timeout=timeout)
        media = await asyncio.wait_for(
            _maybe_await(camera.create_media_service()), timeout=timeout
        )
        profiles = await asyncio.wait_for(
            _maybe_await(media.GetProfiles()), timeout=timeout
        )
        if not profiles:
            raise OnvifError("camera reported no media profiles")

        request = media.create_type("GetStreamUri")
        request.ProfileToken = profiles[0].token
        request.StreamSetup = {
            "Stream": "RTP-Unicast",
            "Transport": {"Protocol": "RTSP"},
        }
        result = await asyncio.wait_for(
            _maybe_await(media.GetStreamUri(request)), timeout=timeout
        )
        uri = getattr(result, "Uri", "") or ""
        if not uri:
            raise OnvifError("camera returned an empty stream URI")
        return uri
    except OnvifError:
        raise
    except Exception as err:  # - surface a readable message
        raise OnvifError(str(err)) from err

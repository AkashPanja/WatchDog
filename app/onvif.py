"""ONVIF helpers: resolve an RTSP stream URI."""

from __future__ import annotations

import asyncio
import inspect
import logging

_LOGGER = logging.getLogger(__name__)


class OnvifError(Exception):
    """Raised when an ONVIF camera cannot be reached or queried."""


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def resolve_rtsp(
    host: str, port: int, username: str, password: str, timeout: float = 10.0
) -> str:
    """Ask an ONVIF camera for its first RTSP stream URI.

    onvif-zeep-async must be driven from a running event loop: the camera
    constructor creates an aiohttp session, so it cannot run in a worker
    thread. All calls are wrapped in ``asyncio.wait_for`` for a hard timeout.
    """
    if not host:
        raise OnvifError("camera host is required")

    camera = None
    try:
        from onvif import ONVIFCamera

        camera = ONVIFCamera(host, int(port), username, password)
        await asyncio.wait_for(_maybe_await(camera.update_xaddrs()), timeout)
        media = await asyncio.wait_for(
            _maybe_await(camera.create_media_service()), timeout
        )
        profiles = await asyncio.wait_for(
            _maybe_await(media.GetProfiles()), timeout
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
            _maybe_await(media.GetStreamUri(request)), timeout
        )
        uri = getattr(result, "Uri", "") or ""
        if not uri:
            raise OnvifError("camera returned an empty stream URI")
        return uri
    except OnvifError:
        raise
    except Exception as err:  # - surface a readable message
        raise OnvifError(str(err)) from err
    finally:
        if camera is not None:
            try:
                await camera.close()
            except Exception as err:  # noqa: BLE001 - cleanup must not mask errors
                _LOGGER.debug("Failed to close ONVIF camera: %s", err)

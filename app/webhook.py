"""Outbound webhooks and event logging."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import httpx

from .config import config
from .db import SessionLocal
from .models import Event

_LOGGER = logging.getLogger("watchdog.webhook")

MAX_EVENTS = 200


def fire_webhook(url: str, secret: str, payload: dict) -> tuple[bool, str, int]:
    """POST the payload, retrying transient failures. Returns (ok, message, attempts)."""
    if not url:
        return False, "no webhook url", 0
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-WatchDog-Secret"] = secret
    attempts = config.webhook_retries + 1
    message = "not attempted"
    for attempt in range(1, attempts + 1):
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=config.webhook_timeout
            )
            if response.status_code < 400:
                return True, "ok", attempt
            message = f"HTTP {response.status_code}"
            if response.status_code != 429 and response.status_code < 500:
                return False, message, attempt
        except Exception as err:  # noqa: BLE001
            message = str(err)
        if attempt < attempts:
            time.sleep(min(2.0, 0.5 * (2 ** (attempt - 1))))
    return False, message, attempts


def record_event(
    camera_id: str, kind: str, detail: dict
) -> None:
    with SessionLocal() as session:
        event = Event(camera_id=camera_id, kind=kind, detail=json.dumps(detail))
        session.add(event)
        session.commit()
        newest = session.query(Event.id).order_by(Event.id.desc()).first()
        if newest is not None:
            threshold = newest[0] - MAX_EVENTS
            if threshold > 0:
                session.query(Event).filter(Event.id <= threshold).delete()
                session.commit()


def notify(
    camera: dict,
    kind: str,
    confidence: float | None,
    snapshot_b64: str | None,
) -> None:
    """Fire the per-camera webhook and log the event."""
    payload = {
        "event": kind,
        "camera_id": camera["id"],
        "camera_name": camera.get("name", ""),
        "timestamp": datetime.now(UTC).isoformat(),
        "confidence": confidence,
        "snapshot_b64": snapshot_b64,
    }
    detail: dict = {"confidence": confidence}
    url = camera.get("webhook_url") or ""
    if url:
        ok, message, attempts = fire_webhook(
            url, camera.get("webhook_secret") or "", payload
        )
        detail["webhook"] = {"ok": ok, "message": message, "attempts": attempts}
        if not ok:
            _LOGGER.warning(
                "webhook delivery failed for %s (%s): %s",
                camera.get("id"),
                kind,
                message,
            )
    else:
        detail["webhook"] = {"ok": False, "message": "no webhook url"}
    record_event(camera["id"], kind, detail)

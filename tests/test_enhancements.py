"""Unit tests: per-camera motion limits, migrations, webhook retries, login throttling."""

from __future__ import annotations

from sqlalchemy import create_engine

from app import auth, limits, webhook
from app.config import config
from app.db import CAMERA_ADDITIONS, ensure_columns
from app.models import Camera


def test_motion_limits_use_global_defaults(monkeypatch) -> None:
    monkeypatch.setattr(config, "motion_threshold", 0.02)
    monkeypatch.setattr(config, "motion_cooldown", 15.0)
    assert limits.motion_limits({"motion_threshold": None, "motion_cooldown": None}) == (
        0.02,
        15.0,
    )
    assert limits.motion_limits({}) == (0.02, 15.0)


def test_motion_limits_prefer_camera_overrides(monkeypatch) -> None:
    monkeypatch.setattr(config, "motion_threshold", 0.02)
    monkeypatch.setattr(config, "motion_cooldown", 15.0)
    camera = {"motion_threshold": 0.1, "motion_cooldown": 3}
    assert limits.motion_limits(camera) == (0.1, 3.0)


def test_motion_limits_clamp_bad_values(monkeypatch) -> None:
    monkeypatch.setattr(config, "motion_threshold", 0.02)
    monkeypatch.setattr(config, "motion_cooldown", 15.0)
    threshold, cooldown = limits.motion_limits(
        {"motion_threshold": "not-a-number", "motion_cooldown": -5}
    )
    assert threshold == 0.02
    assert cooldown == 0.0
    threshold, _ = limits.motion_limits({"motion_threshold": 0.0})
    assert threshold == 0.0001


def test_camera_public_dict_exposes_motion_overrides() -> None:
    camera = Camera(name="Tuned", motion_threshold=0.05, motion_cooldown=30)
    public = camera.public_dict()
    assert public["motion_threshold"] == 0.05
    assert public["motion_cooldown"] == 30
    assert "onvif_password" not in public


def test_ensure_columns_migrates_legacy_cameras_table() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE cameras (id VARCHAR(32) PRIMARY KEY, name VARCHAR(120))"
        )
    assert set(ensure_columns(engine, "cameras", CAMERA_ADDITIONS)) == set(
        CAMERA_ADDITIONS
    )
    assert ensure_columns(engine, "cameras", CAMERA_ADDITIONS) == []
    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(cameras)")
        }
    assert set(CAMERA_ADDITIONS) <= columns


def test_ensure_columns_ignores_missing_table() -> None:
    engine = create_engine("sqlite://")
    assert ensure_columns(engine, "cameras", CAMERA_ADDITIONS) == []


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_webhook_retries_transient_failures(monkeypatch) -> None:
    monkeypatch.setattr(config, "webhook_retries", 2)
    statuses = iter([503, 429, 200])
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response(next(statuses))

    monkeypatch.setattr(webhook.httpx, "post", fake_post)
    monkeypatch.setattr(webhook.time, "sleep", lambda _seconds: None)

    ok, message, attempts = webhook.fire_webhook(
        "http://receiver/hook", "secret", {"event": "motion"}
    )
    assert (ok, message, attempts) == (True, "ok", 3)
    assert calls[0]["headers"]["X-WatchDog-Secret"] == "secret"


def test_webhook_does_not_retry_client_errors(monkeypatch) -> None:
    monkeypatch.setattr(config, "webhook_retries", 3)
    calls: list[int] = []

    def fake_post(url, **kwargs):
        calls.append(1)
        return _Response(400)

    monkeypatch.setattr(webhook.httpx, "post", fake_post)
    monkeypatch.setattr(webhook.time, "sleep", lambda _seconds: None)

    ok, message, attempts = webhook.fire_webhook("http://receiver/hook", "", {})
    assert (ok, message, attempts) == (False, "HTTP 400", 1)
    assert len(calls) == 1


def test_webhook_exhausts_retries(monkeypatch) -> None:
    monkeypatch.setattr(config, "webhook_retries", 1)

    def fake_post(url, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(webhook.httpx, "post", fake_post)
    monkeypatch.setattr(webhook.time, "sleep", lambda _seconds: None)

    ok, message, attempts = webhook.fire_webhook("http://receiver/hook", "", {})
    assert ok is False
    assert message == "network down"
    assert attempts == 2


def test_webhook_skips_missing_url(monkeypatch) -> None:
    monkeypatch.setattr(config, "webhook_retries", 2)
    assert webhook.fire_webhook("", "", {}) == (False, "no webhook url", 0)


def test_login_throttle_blocks_after_limit(monkeypatch) -> None:
    monkeypatch.setattr(config, "login_max_attempts", 2)
    monkeypatch.setattr(config, "login_window_seconds", 60)
    clock = {"now": 1000.0}
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock["now"])
    throttle = auth.LoginThrottle()

    assert throttle.blocked("1.2.3.4") == 0.0
    throttle.failure("1.2.3.4")
    assert throttle.blocked("1.2.3.4") == 0.0
    throttle.failure("1.2.3.4")
    assert throttle.blocked("1.2.3.4") > 0.0
    assert throttle.blocked("5.6.7.8") == 0.0

    clock["now"] += 61
    assert throttle.blocked("1.2.3.4") == 0.0

    throttle.failure("1.2.3.4")
    throttle.reset("1.2.3.4")
    assert throttle.blocked("1.2.3.4") == 0.0

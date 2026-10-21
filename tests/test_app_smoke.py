"""HTTP smoke test: auth, persons, settings (stubs vision deps when absent)."""

from __future__ import annotations

import sys
import types

import pytest

try:  # pragma: no cover - real deployments have the real packages
    import cv2  # noqa: F401
    import numpy  # noqa: F401

    pytest.skip(
        "vision dependencies installed; stub smoke test skipped",
        allow_module_level=True,
    )
except ImportError:
    numpy_stub = types.ModuleType("numpy")
    numpy_stub.array = lambda *args, **kwargs: list(args[0]) if args else []
    numpy_stub.asarray = lambda value, *args, **kwargs: value
    numpy_stub.ndarray = object
    numpy_stub.uint8 = int
    numpy_stub.float32 = float
    sys.modules["numpy"] = numpy_stub
    sys.modules["cv2"] = types.ModuleType("cv2")


def test_http_smoke(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/cameras").status_code == 401

        login = client.post(
            "/api/login", json={"username": "tester", "password": "secret"}
        )
        assert login.status_code == 200
        assert client.get("/api/me").status_code == 200

        from app.api import cameras as cameras_api

        monkeypatch.setattr(
            cameras_api.discover_module,
            "discover_devices",
            lambda timeout=3.0: [
                {
                    "host": "10.0.0.5",
                    "onvif_port": 8000,
                    "name": "Smoke Cam",
                    "hardware": "fixture",
                    "xaddr": "http://10.0.0.5:8000/onvif/device_service",
                }
            ],
        )
        found = client.get("/api/cameras/discover")
        assert found.status_code == 200
        assert found.json()[0]["host"] == "10.0.0.5"

        person = client.post(
            "/api/persons", json={"name": "Smoke User", "threshold": 90}
        )
        assert person.status_code in (200, 409)
        people = client.get("/api/persons")
        assert people.status_code == 200
        assert any(entry["name"] == "Smoke User" for entry in people.json())

        settings = client.get("/api/settings")
        assert settings.status_code == 200
        assert settings.json()["provider"] in {"openai", "groq", "claude"}

        assert client.get("/api/cameras").json() == []
        bad_camera = client.post("/api/cameras", json={"name": "No source"})
        assert bad_camera.status_code == 400


def test_login_throttle_http(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.auth import login_throttle
    from app.config import config
    from app.main import app

    monkeypatch.setattr(config, "login_max_attempts", 3)
    login_throttle.clear()
    try:
        with TestClient(app) as client:
            for _ in range(3):
                bad = client.post(
                    "/api/login", json={"username": "tester", "password": "wrong"}
                )
                assert bad.status_code == 401
            blocked = client.post(
                "/api/login", json={"username": "tester", "password": "secret"}
            )
            assert blocked.status_code == 429
            assert int(blocked.headers["Retry-After"]) >= 1
    finally:
        login_throttle.clear()

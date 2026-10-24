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


def test_relative_hls_paths() -> None:
    from fastapi.testclient import TestClient

    from app.config import config
    from app.main import app

    directory = config.hls_dir / "smoke-cam"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "seg_000001.ts").write_bytes(b"TS")
    (directory / "index.m3u8").write_text("#EXTM3U\n")

    with TestClient(app) as client:
        client.post("/api/login", json={"username": "tester", "password": "secret"})
        segment = client.get("/api/cameras/smoke-cam/seg_000001.ts")
        assert segment.status_code == 200
        assert segment.content == b"TS"
        playlist = client.get("/api/cameras/smoke-cam/stream.m3u8")
        assert playlist.status_code == 200
        assert playlist.text.startswith("#EXTM3U")


def test_zone_validation_rejects_crossing_edges() -> None:
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.models import Camera

    with SessionLocal() as session:
        camera = Camera(name="Zone test", rtsp_url="")
        session.add(camera)
        session.commit()
        camera_id = camera.id
    try:
        with TestClient(app) as client:
            client.post(
                "/api/login", json={"username": "tester", "password": "secret"}
            )
            bowtie = [[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]]
            crossed = client.put(
                f"/api/cameras/{camera_id}/zone", json={"polygon": bowtie}
            )
            assert crossed.status_code == 400
            assert "cross" in crossed.json()["detail"]
            square = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
            saved = client.put(
                f"/api/cameras/{camera_id}/zone", json={"polygon": square}
            )
            assert saved.status_code == 200
            assert len(saved.json()["polygon"]) == 4
            two_points = client.put(
                f"/api/cameras/{camera_id}/zone",
                json={"polygon": [[0.1, 0.1], [0.2, 0.2]]},
            )
            assert two_points.status_code == 400
    finally:
        with SessionLocal() as session:
            camera = session.get(Camera, camera_id)
            if camera is not None:
                session.delete(camera)
                session.commit()

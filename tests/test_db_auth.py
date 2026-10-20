"""Database, auth, settings and event-log tests."""

from __future__ import annotations

import json

from app import auth, store, webhook
from app.db import SessionLocal, init_db
from app.models import Camera, Person, Setting


def test_database_roundtrip() -> None:
    init_db()
    with SessionLocal() as session:
        camera = Camera(
            name="Front door",
            mode="hybrid",
            zone=json.dumps([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]),
            webhook_secret="topsecret",
        )
        session.add(camera)
        session.commit()
        session.refresh(camera)

        assert camera.id and len(camera.id) == 32
        assert camera.zone_polygon == [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]
        public = camera.public_dict()
        assert "onvif_password" not in public
        assert "webhook_secret" not in public
        assert public["has_webhook_secret"] is True

        session.delete(camera)
        session.commit()


def test_person_unique_name() -> None:
    init_db()
    with SessionLocal() as session:
        session.query(Person).delete()
        session.add(Person(name="Alice", threshold=95.0))
        session.commit()
        assert session.query(Person).count() == 1


def test_session_roundtrip_and_tamper() -> None:
    token = auth.create_session("tester")
    assert auth.verify_session(token) == "tester"
    assert auth.verify_session(token + "x") is None
    assert auth.verify_session(None) is None


def test_credentials() -> None:
    assert auth.credentials_ok("tester", "secret")
    assert not auth.credentials_ok("tester", "wrong")
    assert not auth.credentials_ok("other", "secret")


def test_settings_defaults_and_update() -> None:
    init_db()
    with SessionLocal() as session:
        session.query(Setting).delete()
        session.commit()
        store.init_settings(session)
        assert store.get_setting(session, "llm_provider") == "openai"
        store.set_setting(session, "llm_provider", "groq")
        assert store.get_setting(session, "llm_provider") == "groq"


def test_event_log_keeps_only_recent() -> None:
    init_db()
    for index in range(3):
        webhook.record_event("camera-1", "motion", {"index": index})
    with SessionLocal() as session:
        from app.models import Event

        session.query(Event).delete()
        session.commit()
    webhook.record_event("camera-1", "human", {"confidence": 91.0})
    with SessionLocal() as session:
        from app.models import Event

        events = session.query(Event).order_by(Event.id.desc()).all()
        assert events and events[0].kind == "human"
        assert events[0].public_dict()["detail"]["confidence"] == 91.0

"""Event log endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..db import SessionLocal
from ..models import Event

router = APIRouter(prefix="/api/events")


@router.get("")
def list_events(limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    with SessionLocal() as session:
        events = (
            session.query(Event).order_by(Event.id.desc()).limit(limit).all()
        )
        return [event.public_dict() for event in events]

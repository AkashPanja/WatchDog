"""ORM models."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Camera(Base):
    """A configured camera."""

    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(120))
    mode: Mapped[str] = mapped_column(String(16), default="python")
    host: Mapped[str] = mapped_column(String(160), default="")
    port: Mapped[int] = mapped_column(Integer, default=80)
    onvif_username: Mapped[str] = mapped_column(String(120), default="")
    onvif_password: Mapped[str] = mapped_column(String(200), default="")
    rtsp_url: Mapped[str] = mapped_column(String(500), default="")
    zone: Mapped[str] = mapped_column(Text, default="[]")
    webhook_url: Mapped[str] = mapped_column(String(500), default="")
    webhook_secret: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def zone_polygon(self) -> list[list[float]]:
        try:
            value = json.loads(self.zone or "[]")
        except (TypeError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        points: list[list[float]] = []
        for item in value:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and all(isinstance(v, (int, float)) for v in item)
            ):
                points.append([float(item[0]), float(item[1])])
        return points

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "host": self.host,
            "port": self.port,
            "onvif_username": self.onvif_username,
            "has_onvif_password": bool(self.onvif_password),
            "rtsp_url": self.rtsp_url,
            "zone": self.zone_polygon,
            "webhook_url": self.webhook_url,
            "has_webhook_secret": bool(self.webhook_secret),
        }


class Person(Base):
    """An enrolled person."""

    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    threshold: Mapped[float] = mapped_column(Float, default=95.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    photos: Mapped[list[Photo]] = relationship(
        back_populates="person", cascade="all, delete-orphan", order_by="Photo.id"
    )

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "threshold": self.threshold,
            "photo_ids": [photo.id for photo in self.photos],
        }


class Photo(Base):
    """One reference photo for a person."""

    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    person: Mapped[Person] = relationship(back_populates="photos")


class Setting(Base):
    """Key/value application settings."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Event(Base):
    """Recent detection / webhook events."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def public_dict(self) -> dict:
        try:
            detail = json.loads(self.detail or "{}")
        except (TypeError, ValueError):
            detail = {}
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "kind": self.kind,
            "detail": detail,
            "created_at": self.created_at.isoformat(),
        }

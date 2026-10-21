"""Database engine and sessions."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import config


class Base(DeclarativeBase):
    """Declarative base."""


config.ensure_dirs()

engine = create_engine(
    f"sqlite:///{config.db_path}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _enable_foreign_keys(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


CAMERA_ADDITIONS = {
    "motion_threshold": "FLOAT",
    "motion_cooldown": "FLOAT",
}


def ensure_columns(target_engine, table: str, columns: dict[str, str]) -> list[str]:
    """Add missing columns to an existing SQLite table (lightweight migration)."""
    added: list[str] = []
    with target_engine.begin() as connection:
        existing = {
            row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")
        }
        if not existing:
            return added
        for name, ddl in columns.items():
            if name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                )
                added.append(name)
    return added


def init_db() -> None:
    """Create tables and migrate older databases in place."""
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)
    ensure_columns(engine, "cameras", CAMERA_ADDITIONS)

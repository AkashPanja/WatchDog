"""WatchDog: single-user camera guard with ONVIF, zones, webhooks and face ID."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .api import auth as auth_api
from .api import cameras as cameras_api
from .api import events as events_api
from .api import persons as persons_api
from .api import settings as settings_api
from .auth import COOKIE_NAME, verify_session
from .config import config
from .db import SessionLocal, init_db
from .store import init_settings
from .streams import manager

_LOGGER = logging.getLogger("watchdog")

STATIC_DIR = Path(__file__).parent / "static"
PUBLIC_API = {"/api/login", "/api/health"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not config.username or not config.password:
        raise RuntimeError(
            "APP_USERNAME and APP_PASSWORD must be set (docker-compose environment)"
        )
    config.ensure_dirs()
    init_db()
    with SessionLocal() as session:
        init_settings(session)
    manager.start_all()
    _LOGGER.info("WatchDog started on %s", config.data_dir)
    yield
    manager.stop_all()


app = FastAPI(title="WatchDog", lifespan=lifespan, docs_url=None, redoc_url=None)


class AuthMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated API calls; static assets stay public."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)
        if (
            path.startswith("/api")
            and path not in PUBLIC_API
            and verify_session(request.cookies.get(COOKIE_NAME)) is None
        ):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)


app.add_middleware(AuthMiddleware)

app.include_router(auth_api.router)
app.include_router(cameras_api.router)
app.include_router(persons_api.router)
app.include_router(settings_api.router)
app.include_router(events_api.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"}
    )


@app.middleware("http")
async def revalidate_static_assets(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

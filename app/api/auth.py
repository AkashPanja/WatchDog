"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import (
    COOKIE_NAME,
    create_session,
    credentials_ok,
    login_throttle,
    verify_session,
)
from ..config import config

router = APIRouter(prefix="/api")


class LoginPayload(BaseModel):
    username: str = ""
    password: str = ""


@router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response) -> dict:
    client = request.client.host if request.client else "unknown"
    retry_after = login_throttle.blocked(client)
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail="too many failed logins, try again later",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    if not credentials_ok(payload.username, payload.password):
        login_throttle.failure(client)
        raise HTTPException(status_code=401, detail="invalid username or password")
    login_throttle.reset(client)
    token = create_session(payload.username)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=config.session_max_age,
        httponly=True,
        samesite="lax",
        secure=config.cookie_secure,
        path="/",
    )
    return {"ok": True, "username": payload.username}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    username = verify_session(request.cookies.get(COOKIE_NAME))
    if username is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"username": username}

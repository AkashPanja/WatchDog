"""Single-user session authentication."""

from __future__ import annotations

import hmac

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import config

COOKIE_NAME = "watchdog_session"
_salt = "watchdog-session"
_serializer = URLSafeTimedSerializer(config.session_secret, salt=_salt)


def credentials_ok(username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username, config.username)
    pass_ok = hmac.compare_digest(password, config.password)
    return user_ok and pass_ok


def create_session(username: str) -> str:
    return _serializer.dumps({"username": username})


def verify_session(token: str | None) -> str | None:
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=config.session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    username = data.get("username") if isinstance(data, dict) else None
    if not username or username != config.username:
        return None
    return username

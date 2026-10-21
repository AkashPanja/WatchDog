"""Single-user session authentication."""

from __future__ import annotations

import hmac
import threading
import time
from collections import deque

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


class LoginThrottle:
    """In-memory failed-login limiter keyed by client address."""

    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        bucket = self._failures.get(key)
        if bucket is None:
            return deque()
        cutoff = now - config.login_window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            self._failures.pop(key, None)
            return deque()
        return bucket

    def blocked(self, key: str) -> float:
        """Seconds until the next login attempt is allowed (0 = allowed)."""
        now = time.monotonic()
        with self._lock:
            bucket = self._recent(key, now)
            if len(bucket) < config.login_max_attempts:
                return 0.0
            return max(0.0, bucket[0] + config.login_window_seconds - now)

    def failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._recent(key, now)
            bucket.append(now)
            self._failures[key] = bucket

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle()

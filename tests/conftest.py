"""Test environment defaults (must run before importing the app)."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="watchdog-test-"))
os.environ.setdefault("APP_USERNAME", "tester")
os.environ.setdefault("APP_PASSWORD", "secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")

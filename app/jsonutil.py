"""JSON helpers for LLM output."""

from __future__ import annotations

import json
import re


def extract_json(text: str) -> dict | None:
    """Extract the first JSON object from model output."""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start : end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None

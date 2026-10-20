"""LLM vision providers: OpenAI, OpenAI-compatible, Claude, Groq, OpenRouter."""

from __future__ import annotations

import base64

import httpx

from .config import config
from .db import SessionLocal
from .jsonutil import extract_json
from .store import get_setting

PROVIDERS = ("openai", "openai_compatible", "claude", "groq", "openrouter")

PROVIDER_LABELS = {
    "openai": "OpenAI",
    "openai_compatible": "OpenAI-compatible",
    "claude": "Claude (Anthropic)",
    "groq": "Groq",
    "openrouter": "OpenRouter",
}

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "openai_compatible": "",
    "claude": "claude-sonnet-4-5",
    "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
    "openrouter": "google/gemini-2.5-flash",
}

BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "claude": "https://api.anthropic.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class LLMError(Exception):
    """Raised when a provider call fails."""


def load_llm_settings() -> dict[str, str]:
    with SessionLocal() as session:
        provider = get_setting(session, "llm_provider", "openai")
        api_key = get_setting(session, "llm_api_key", "")
        model = get_setting(session, "llm_model", "")
        base_url = get_setting(session, "llm_base_url", "")
    provider = provider if provider in PROVIDERS else "openai"
    return {
        "provider": provider,
        "api_key": api_key,
        "model": model or DEFAULT_MODELS.get(provider, ""),
        "base_url": base_url or BASE_URLS.get(provider, ""),
    }


def _data_url(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


async def _post(url: str, headers: dict[str, str], payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=config.llm_timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        snippet = response.text[:400]
        raise LLMError(f"provider returned HTTP {response.status_code}: {snippet}")
    try:
        return response.json()
    except ValueError as err:
        raise LLMError("provider returned non-JSON response") from err


async def _call_openai_style(
    base_url: str, api_key: str, model: str, system: str, prompt: str, images: list[bytes]
) -> str:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append(
            {"type": "image_url", "image_url": {"url": _data_url(image)}}
        )
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 700,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = await _post(base_url.rstrip("/") + "/chat/completions", headers, payload)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as err:
        raise LLMError("unexpected provider response shape") from err


async def _call_claude(
    base_url: str, api_key: str, model: str, system: str, prompt: str, images: list[bytes]
) -> str:
    content: list[dict] = []
    for image in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(image).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": prompt})
    payload = {
        "model": model,
        "max_tokens": 700,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    data = await _post(base_url.rstrip("/") + "/messages", headers, payload)
    try:
        blocks = data.get("content") or []
        return "".join(
            block.get("text", "") for block in blocks if isinstance(block, dict)
        )
    except (AttributeError, TypeError) as err:
        raise LLMError("unexpected provider response shape") from err


async def vision_json(
    images: list[bytes], system: str, prompt: str
) -> dict:
    """Send images to the configured LLM and parse a JSON verdict."""
    settings = load_llm_settings()
    provider = settings["provider"]
    api_key = settings["api_key"]
    model = settings["model"]
    base_url = settings["base_url"]

    if not api_key and provider != "openai_compatible":
        raise LLMError("LLM API key is not configured in Settings")
    if not model:
        raise LLMError("LLM model is not configured")
    if not base_url:
        raise LLMError("LLM base URL is not configured (required for OpenAI-compatible)")

    if provider == "claude":
        text = await _call_claude(base_url, api_key, model, system, prompt, images)
    else:
        text = await _call_openai_style(base_url, api_key, model, system, prompt, images)

    parsed = extract_json(text)
    if parsed is None:
        raise LLMError("model did not return valid JSON")
    return parsed


IDENTIFY_SYSTEM = (
    "You are WatchDog, a face-match analyst for a private security camera. "
    "You receive enrolled reference photos first, in the order of the listed names, "
    "then a zoomed crop of the face detected in the live frame (when available). "
    "Compare only the faces. Reply with JSON only, no prose."
)

IDENTIFY_PROMPT_TEMPLATE = (
    "Enrolled references in image order: {names}.\n"
    "The last image{extra} is the cropped/zoomed live face.\n"
    'Return exactly: {{"person_found": true|false, "name": "<exact enrolled name or null>", '
    '"confidence": <integer 0-100>}}.\n'
    'Only return a name when the face clearly matches that enrolled person; otherwise '
    'person_found=false and name=null. Confidence reflects how certain you are.'
)

DETECT_SYSTEM = (
    "You monitor a private security camera. The yellow polygon marks the detection area. "
    "You are given a zoomed crop of the person/face found inside that area (if a "
    "detection exists). Judge from it. Reply with JSON only."
)

DETECT_PROMPT = (
    "Is a human present inside the yellow marked area? "
    'Return exactly: {"person_found": true|false, "confidence": <integer 0-100>}.'
)


async def identify_with_llm(
    frame: bytes | None,
    references: list[tuple[str, bytes]],
    face_crop: bytes | None = None,
) -> dict:
    """Match enrolled references against the zoomed live face crop only."""
    names = [name for name, _ in references]
    images = [image for _, image in references]
    if face_crop:
        images.append(face_crop)
    elif frame:
        images.append(frame)
    extra = " (the zoomed live face)" if face_crop else ""
    prompt = IDENTIFY_PROMPT_TEMPLATE.format(
        names=", ".join(names) if names else "none",
        extra=extra,
    )
    return await vision_json(images, IDENTIFY_SYSTEM, prompt)


async def detect_human_with_llm(
    crop: bytes | None = None
) -> dict:
    if crop is None:
        return {"person_found": False, "confidence": 0}
    return await vision_json([crop], DETECT_SYSTEM, DETECT_PROMPT)

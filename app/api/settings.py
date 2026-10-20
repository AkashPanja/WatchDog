"""LLM settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import llm
from ..db import SessionLocal
from ..store import get_setting, set_setting

router = APIRouter(prefix="/api/settings")


class SettingsUpdate(BaseModel):
    provider: str
    api_key: str | None = None
    model: str = ""
    base_url: str = ""


@router.get("")
def get_settings() -> dict:
    with SessionLocal() as session:
        provider = get_setting(session, "llm_provider", "openai")
        model = get_setting(session, "llm_model", "")
        base_url = get_setting(session, "llm_base_url", "")
        api_key = get_setting(session, "llm_api_key", "")
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "has_key": bool(api_key),
        "providers": [
            {"value": key, "label": label}
            for key, label in llm.PROVIDER_LABELS.items()
        ],
        "default_models": llm.DEFAULT_MODELS,
    }


@router.put("")
def update_settings(payload: SettingsUpdate) -> dict:
    provider = payload.provider.strip()
    if provider not in llm.PROVIDERS:
        raise HTTPException(status_code=400, detail="unknown provider")
    with SessionLocal() as session:
        set_setting(session, "llm_provider", provider)
        set_setting(session, "llm_model", payload.model.strip())
        set_setting(session, "llm_base_url", payload.base_url.strip())
        if payload.api_key is not None:
            set_setting(session, "llm_api_key", payload.api_key.strip())
    return {"ok": True}


@router.post("/test")
async def test_settings() -> dict:
    try:
        verdict = await llm.vision_json(
            [], "You are a health check.", 'Reply with JSON only: {"ok": true}'
        )
    except llm.LLMError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {"ok": True, "verdict": verdict}

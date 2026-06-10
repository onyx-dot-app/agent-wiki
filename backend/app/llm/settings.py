"""DB-backed LLM settings. Configured via the admin page; no env-var fallback."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import LLMSettings as LLMSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)


class LLMSettings(BaseModel):
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    provider: str
    model: str
    anthropic_api_key: str
    openai_api_key: str
    gemini_api_key: str
    ollama_base_url: str
    custom_api_key: str
    custom_base_url: str
    custom_display_name: str
    provider_models: dict[str, list[str]]
    ingest_selector_model: str  # empty or same as model → stage 3 skipped


_EMPTY = LLMSettings(
    provider="",
    model="",
    anthropic_api_key="",
    openai_api_key="",
    gemini_api_key="",
    ollama_base_url="",
    custom_api_key="",
    custom_base_url="",
    custom_display_name="",
    provider_models={},
    ingest_selector_model="",
)


def get() -> LLMSettings:
    with session() as s:
        row = s.get(LLMSettingsRow, 1)
        if row is None:
            return _EMPTY
        return LLMSettings(
            provider=row.provider,
            model=row.model,
            anthropic_api_key=row.anthropic_api_key,
            openai_api_key=row.openai_api_key,
            gemini_api_key=row.gemini_api_key,
            ollama_base_url=row.ollama_base_url,
            custom_api_key=row.custom_api_key,
            custom_base_url=row.custom_base_url,
            custom_display_name=row.custom_display_name,
            provider_models=row.provider_models or {},
            ingest_selector_model=row.ingest_selector_model or "",
        )


def upsert(
    *,
    provider: str,
    model: str,
    anthropic_api_key: str,
    openai_api_key: str,
    gemini_api_key: str,
    ollama_base_url: str,
    custom_api_key: str,
    custom_base_url: str,
    custom_display_name: str,
    provider_models: dict[str, list[str]] | None = None,
    ingest_selector_model: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(LLMSettingsRow, 1)
        if row is None:
            s.add(
                LLMSettingsRow(
                    id=1,
                    provider=provider,
                    model=model,
                    anthropic_api_key=anthropic_api_key,
                    openai_api_key=openai_api_key,
                    gemini_api_key=gemini_api_key,
                    ollama_base_url=ollama_base_url,
                    custom_api_key=custom_api_key,
                    custom_base_url=custom_base_url,
                    custom_display_name=custom_display_name,
                    provider_models=provider_models or {},
                    ingest_selector_model=ingest_selector_model or "",
                    updated_at=now,
                )
            )
        else:
            row.provider = provider
            row.model = model
            row.anthropic_api_key = anthropic_api_key
            row.openai_api_key = openai_api_key
            row.gemini_api_key = gemini_api_key
            row.ollama_base_url = ollama_base_url
            row.custom_api_key = custom_api_key
            row.custom_base_url = custom_base_url
            row.custom_display_name = custom_display_name
            if provider_models is not None:
                row.provider_models = provider_models
            if ingest_selector_model is not None:
                row.ingest_selector_model = ingest_selector_model
            row.updated_at = now
    log.info("llm_settings upserted provider=%s model=%s ingest_selector_model=%s", provider, model, ingest_selector_model or "none")

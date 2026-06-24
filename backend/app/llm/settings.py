"""DB-backed LLM settings. Configured via the admin page; no env-var fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import LLMSettings as LLMSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)


class LLMSettings(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        protected_namespaces=(),
        from_attributes=True,
    )

    provider: str = ""
    model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    ollama_base_url: str = ""
    custom_api_key: str = ""
    custom_base_url: str = ""
    custom_display_name: str = ""
    bedrock_aws_region: str = ""
    bedrock_endpoint_url: str = ""
    bedrock_aws_access_key_id: str = ""
    bedrock_aws_secret_access_key: str = ""
    bedrock_aws_session_token: str = ""
    provider_models: dict[str, list[str]] = Field(default_factory=dict)
    ingest_selector_model: str = ""  # empty or same as model → stage 3 skipped


_EMPTY = LLMSettings()


def get() -> LLMSettings:
    with session() as s:
        row = s.get(LLMSettingsRow, 1)
        if row is None:
            return _EMPTY
        return LLMSettings.model_validate(row)


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
    bedrock_aws_region: str,
    bedrock_endpoint_url: str,
    bedrock_aws_access_key_id: str,
    bedrock_aws_secret_access_key: str,
    bedrock_aws_session_token: str,
    provider_models: dict[str, list[str]] | None = None,
    ingest_selector_model: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(LLMSettingsRow, 1)
        if row is None:
            row = LLMSettingsRow(id=1, provider_models={}, ingest_selector_model="")
            s.add(row)
        row.provider = provider
        row.model = model
        row.anthropic_api_key = anthropic_api_key
        row.openai_api_key = openai_api_key
        row.gemini_api_key = gemini_api_key
        row.ollama_base_url = ollama_base_url
        row.custom_api_key = custom_api_key
        row.custom_base_url = custom_base_url
        row.custom_display_name = custom_display_name
        row.bedrock_aws_region = bedrock_aws_region
        row.bedrock_endpoint_url = bedrock_endpoint_url
        row.bedrock_aws_access_key_id = bedrock_aws_access_key_id
        row.bedrock_aws_secret_access_key = bedrock_aws_secret_access_key
        row.bedrock_aws_session_token = bedrock_aws_session_token
        if provider_models is not None:
            row.provider_models = provider_models
        if ingest_selector_model is not None:
            row.ingest_selector_model = ingest_selector_model
        row.updated_at = now
    log.info(
        "llm_settings upserted provider=%s model=%s ingest_selector_model=%s",
        provider,
        model,
        ingest_selector_model or "none",
    )

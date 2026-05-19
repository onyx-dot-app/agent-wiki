"""HTTP-shape models for the admin endpoints (request + response)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


# --------------------------------------------------------------------------- #
# Requests                                                                    #
# --------------------------------------------------------------------------- #


class UpdateUserRequest(BaseModel):
    is_admin: bool


class LLMConfigRequest(BaseModel):
    """Empty-string secrets / ollama_base_url mean 'leave existing untouched';
    explicit ``null`` clears them. The blueprint resolves that semantic."""

    # `model` is a real field, so silence pydantic's ``model_*`` namespace warning.
    model_config = ConfigDict(protected_namespaces=())

    provider: str | None = None
    model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    ollama_base_url: str | None = None
    provider_models: dict[str, list[str]] | None = None
    ingest_selector_model: str | None = None


class WebConfigRequest(BaseModel):
    serper_api_key: str | None = None
    firecrawl_api_key: str | None = None


class IngestConfigRequest(BaseModel):
    max_doc_chars: int


class BraintrustConfigRequest(BaseModel):
    """Empty-string ``api_key`` means 'leave existing untouched'; explicit
    ``null`` clears it. The blueprint resolves that semantic. ``enabled``
    is rejected by the blueprint when the resolved key/project would be
    empty — the UI mirrors that gating, but we re-check server-side."""

    project: str
    api_key: str | None = None
    enabled: bool = False


class OkResponse(BaseModel):
    ok: bool = True


# --------------------------------------------------------------------------- #
# Responses                                                                   #
# --------------------------------------------------------------------------- #


class AdminUserView(BaseModel):
    id: str
    email: str
    name: str | None
    is_admin: bool
    created_at: str


class AdminUserListResponse(BaseModel):
    users: list[AdminUserView]


class LLMView(BaseModel):
    """Admin view of the LLM settings — keys are redacted to a hint, never
    returned in full."""

    # `model` is a real field, so silence pydantic's ``model_*`` namespace warning.
    model_config = ConfigDict(protected_namespaces=())

    provider: str
    model: str
    anthropic_api_key_set: bool
    openai_api_key_set: bool
    gemini_api_key_set: bool
    anthropic_api_key_hint: str
    openai_api_key_hint: str
    gemini_api_key_hint: str
    # Ollama doesn't have an API key — surface the base URL directly.
    ollama_base_url: str
    provider_models: dict[str, list[str]]
    ingest_selector_model: str


class WebView(BaseModel):
    """Admin view of the web search/crawl settings."""

    search_provider: str = "serper"
    crawl_provider: str = "firecrawl"
    serper_api_key_set: bool
    firecrawl_api_key_set: bool
    serper_api_key_hint: str
    firecrawl_api_key_hint: str


class IngestView(BaseModel):
    max_doc_chars: int
    api_key: str | None


class RegenerateKeyResponse(BaseModel):
    api_key: str


class BraintrustView(BaseModel):
    """Admin view of the Braintrust tracing settings."""

    project: str
    api_key_set: bool
    api_key_hint: str
    enabled: bool

"""HTTP-shape models for the admin endpoints (request + response)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Requests                                                                    #
# --------------------------------------------------------------------------- #


class UpdateUserRequest(BaseModel):
    """Both optional — PATCH whichever field(s) are present."""

    is_admin: bool | None = None
    is_active: bool | None = None


class InviteUsersRequest(BaseModel):
    emails: list[str]


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
    custom_api_key: str | None = None
    custom_base_url: str | None = None
    # Plain set-on-sent (not a secret) — "" clears, unlike the key fields.
    custom_display_name: str | None = None
    bedrock_aws_region: str | None = None
    bedrock_endpoint_url: str | None = None
    bedrock_aws_access_key_id: str | None = None
    bedrock_aws_secret_access_key: str | None = None
    bedrock_aws_session_token: str | None = None
    bedrock_aws_bearer_token: str | None = None
    provider_models: dict[str, list[str]] | None = None
    ingest_selector_model: str | None = None


class ProviderTestRequest(BaseModel):
    """Model to preflight; empty/absent falls back to the first saved model
    for that provider, then the active model."""

    # `model` is a real field, so silence pydantic's ``model_*`` namespace warning.
    model_config = ConfigDict(protected_namespaces=())

    model: str | None = None


class WebConfigRequest(BaseModel):
    serper_api_key: str | None = None
    firecrawl_api_key: str | None = None


class IngestConfigRequest(BaseModel):
    max_doc_chars: int
    # Outbound Onyx origin for Craft launches. Omit to preserve the stored
    # value; an explicit empty string clears it.
    onyx_base_url: str | None = None
    # Auto-update health knobs. Omit to preserve the stored value (like
    # onyx_base_url); 0 disables either.
    warn_update_threshold_default: int | None = Field(default=None, ge=0)
    auto_update_cap: int | None = Field(default=None, ge=0)


class SlackAppConfigRequest(BaseModel):
    """Set the Slack app OAuth credentials. ``client_secret`` follows the
    masked-secret convention: omitted or empty keeps the stored value, null
    clears it."""

    client_id: str = ""
    client_secret: str | None = None


class EmailSmtpConfigRequest(BaseModel):
    """Set the outbound SMTP account. ``password`` follows the masked-secret
    convention: omitted or empty keeps the stored value, null clears it."""

    host: str = ""
    port: int = 587
    username: str = ""
    password: str | None = None
    from_address: str = ""


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
    is_active: bool
    # "active" | "inactive" — derived from is_active for the status column.
    status: str
    created_at: str
    updated_at: str
    # Names of the groups this user belongs to (for the admin users table).
    groups: list[str] = Field(default_factory=list)


class InvitedUserView(BaseModel):
    email: str


class UserCounts(BaseModel):
    active: int
    inactive: int
    invited: int


class AdminUserListResponse(BaseModel):
    users: list[AdminUserView]
    invited: list[InvitedUserView] = Field(default_factory=list)
    counts: UserCounts


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
    custom_api_key_set: bool
    custom_api_key_hint: str
    custom_base_url: str
    custom_display_name: str
    # AWS Bedrock — region + endpoint shown in the clear; AWS keys redacted.
    bedrock_aws_region: str
    bedrock_endpoint_url: str
    bedrock_aws_access_key_id_set: bool
    bedrock_aws_access_key_id_hint: str
    bedrock_aws_secret_access_key_set: bool
    bedrock_aws_secret_access_key_hint: str
    bedrock_aws_session_token_set: bool
    bedrock_aws_bearer_token_set: bool
    bedrock_aws_bearer_token_hint: str
    provider_models: dict[str, list[str]]
    ingest_selector_model: str


class ProviderTestResult(BaseModel):
    """Redacted preflight diagnostics for any provider — never includes credentials."""

    model_config = ConfigDict(protected_namespaces=())

    ok: bool
    base_url: str
    auth_present: bool
    model: str
    # "ok" or a translated, redaction-safe error message.
    models_endpoint: str
    completion: str


class WebView(BaseModel):
    """Admin view of the web search/crawl settings."""

    search_provider: str = "serper"
    crawl_provider: str = "firecrawl"
    serper_api_key_set: bool
    firecrawl_api_key_set: bool
    serper_api_key_hint: str
    firecrawl_api_key_hint: str


class IngestView(BaseModel):
    """The raw ingest key is show-once via RegenerateKeyResponse — reads
    only ever get set/hint."""

    max_doc_chars: int
    api_key_set: bool
    api_key_hint: str
    onyx_base_url: str | None
    warn_update_threshold_default: int
    auto_update_cap: int


class RegenerateKeyResponse(BaseModel):
    api_key: str


class SlackAppView(BaseModel):
    client_id: str
    client_secret_set: bool
    client_secret_hint: str


class EmailSmtpView(BaseModel):
    host: str
    port: int
    username: str
    password_set: bool
    password_hint: str
    from_address: str


class EmailTestRequest(BaseModel):
    """Recipient for the admin test send; empty means the acting admin."""

    to: str = ""


class EmailTestResponse(BaseModel):
    ok: bool
    detail: str


class BraintrustView(BaseModel):
    """Admin view of the Braintrust tracing settings."""

    project: str
    api_key_set: bool
    api_key_hint: str
    enabled: bool

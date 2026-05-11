"""FastAPI port of ``app/api/llm.py`` (Phase 2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import User
from app.auth.deps import require_user
from app.llm import providers as llm_providers
from app.llm import settings as llm_settings
from app.llm.errors import LLMError
from app.models.llm import AvailableProvider, AvailableProvidersResponse, LLMStatusResponse

router = APIRouter()


@router.get("/status", response_model=LLMStatusResponse)
def status(_user: User = Depends(require_user)) -> LLMStatusResponse:
    s = llm_settings.get()
    provider = llm_providers.get(s.provider) if s.provider else None
    configured = False
    if provider is not None and s.model:
        try:
            provider.check_configured(s)
            configured = True
        except LLMError:
            configured = False
    return LLMStatusResponse(configured=configured, provider=s.provider, model=s.model)


_PROVIDER_LABELS = {
    "anthropic": ("Anthropic", "claude-sonnet-4-6"),
    "openai": ("OpenAI", "gpt-5.5"),
    "gemini": ("Gemini", "gemini-3.1-pro-preview"),
    "ollama": ("Ollama", "llama3.1"),
}

_PROVIDER_DEFAULT_MODELS: dict[str, list[str]] = {
    "anthropic": ["claude-sonnet-4-6", "claude-opus-4-7", "claude-opus-4-6", "claude-haiku-4-5"],
    "openai": ["gpt-5.5", "gpt-5.4", "gpt-5.2"],
    "gemini": ["gemini-3.1-pro-preview", "gemini-3-flash-preview"],
    "ollama": ["llama3.1", "llama3.2", "mistral", "phi3", "qwen2.5", "deepseek-r1"],
}


@router.get("/available", response_model=AvailableProvidersResponse)
def available(_user: User = Depends(require_user)) -> AvailableProvidersResponse:
    """Return providers that have credentials configured and are ready to use."""
    s = llm_settings.get()
    result: list[AvailableProvider] = []
    checks = [
        ("anthropic", bool(s.anthropic_api_key)),
        ("openai", bool(s.openai_api_key)),
        ("gemini", bool(s.gemini_api_key)),
        ("ollama", bool(s.ollama_base_url)),
    ]
    for name, has_creds in checks:
        if not has_creds:
            continue
        p = llm_providers.get(name)
        if p is None:
            continue
        try:
            p.check_configured(s)
        except LLMError:
            continue
        label, default_model = _PROVIDER_LABELS.get(name, (name, ""))
        saved = s.provider_models.get(name, [])
        models = saved if saved else _PROVIDER_DEFAULT_MODELS.get(name, [default_model])
        result.append(AvailableProvider(
            provider=name,
            label=label,
            default_model=default_model,
            models=models,
        ))
    return AvailableProvidersResponse(providers=result)

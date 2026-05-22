"""Override the (provider, model, keys) used by `app.llm.client` per run.

The override lives in a ``ContextVar`` so it's task-local: nested overrides
(e.g. a judge call inside a subject run) and concurrent runs cannot leak
settings into each other the way a module-level monkey-patch would. The
resolver is installed onto ``app.llm.client.get_llm_settings`` once on
first use; the install is idempotent and a no-op outside the eval CLI.
"""

from __future__ import annotations

import contextvars
import os
from collections.abc import Generator
from contextlib import contextmanager

from app.llm import client as llm_client
from app.llm.settings import LLMSettings
from app.llm.settings import get as _real_get


_PROVIDER_PREFIXES: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude-",),
    "openai": ("gpt-", "o1", "o3", "o4"),
    "gemini": ("gemini-",),
    "ollama": ("llama", "mistral", "qwen", "deepseek", "phi"),
}


_override_var: contextvars.ContextVar[LLMSettings | None] = contextvars.ContextVar(
    "eval_llm_settings_override", default=None
)


def _resolve_settings() -> LLMSettings:
    override = _override_var.get()
    if override is not None:
        return override
    return _real_get()


def _install() -> None:
    if llm_client.get_llm_settings is _resolve_settings:
        return
    llm_client.get_llm_settings = _resolve_settings  # type: ignore[assignment]


def resolve_provider(model: str) -> str:
    for provider, prefixes in _PROVIDER_PREFIXES.items():
        if any(model.startswith(p) for p in prefixes):
            return provider
    return ""


def _env_key(provider: str) -> str:
    var_map = {
        "anthropic": ("EVAL_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        "openai": ("EVAL_OPENAI_API_KEY", "OPENAI_API_KEY"),
        "gemini": ("EVAL_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    }
    for var in var_map.get(provider, ()):
        value = os.environ.get(var, "")
        if value:
            return value
    return ""


def build_settings(provider: str, model: str) -> LLMSettings:
    return LLMSettings(
        provider=provider,
        model=model,
        anthropic_api_key=_env_key("anthropic") if provider == "anthropic" else "",
        openai_api_key=_env_key("openai") if provider == "openai" else "",
        gemini_api_key=_env_key("gemini") if provider == "gemini" else "",
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        provider_models={},
        ingest_selector_model="",
    )


@contextmanager
def use_model(provider: str, model: str) -> Generator[None]:
    _install()
    token = _override_var.set(build_settings(provider, model))
    try:
        yield
    finally:
        _override_var.reset(token)


def configured_models(models: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in models:
        provider = resolve_provider(m)
        if not provider:
            continue
        if provider == "ollama" or _env_key(provider):
            out.append((provider, m))
    return out

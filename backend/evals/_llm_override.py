"""Pick the (provider, model, keys) used for a single eval run.

Background: ``app.llm.client`` looks up provider + keys via
``app.llm.settings.get()``, which reads the ``llm_settings`` row from the
database. That's right for the running app but wrong for an eval — we want
to drive the same agent code across a matrix of models without touching
the DB or the admin UI.

This module exposes a context manager that monkey-patches the symbol
``app.llm.client.get_llm_settings`` for the duration of the block. Keys
come from env vars (``EVAL_*`` prefer, then plain ``ANTHROPIC_API_KEY``
etc), so an eval run can be a plain shell command:

    EVAL_ANTHROPIC_API_KEY=sk-... uv run python -m evals.wiki_updater.run ...

Provider for a given model id is resolved by ``resolve_provider``. Add new
mappings there when adding a new family.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

from app.llm import client as llm_client
from app.llm.settings import LLMSettings


_PROVIDER_PREFIXES: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude-",),
    "openai": ("gpt-", "o1", "o3", "o4"),
    "gemini": ("gemini-",),
    "ollama": ("llama", "mistral", "qwen", "deepseek", "phi"),
}


def resolve_provider(model: str) -> str:
    """Return the provider name for a model id, or empty string if unknown."""
    for provider, prefixes in _PROVIDER_PREFIXES.items():
        if any(model.startswith(p) for p in prefixes):
            return provider
    return ""


def _env_key(provider: str) -> str:
    """Read the API key for ``provider`` from env, preferring the EVAL_ prefix.

    Returns empty string if no key is configured anywhere — caller should
    treat that as "skip this model".
    """
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
    """Build a frozen ``LLMSettings`` for one (provider, model) pair.

    Reads keys from env. Keys for providers not selected here are left
    blank; ``check_configured`` on the selected provider only inspects its
    own slot, so the other empties don't affect behavior.
    """
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
    """Run the wrapped block with ``client.get_llm_settings`` overridden.

    Restores the original lookup on exit, even on exception.
    """
    settings = build_settings(provider, model)
    original = llm_client.get_llm_settings

    def _override() -> LLMSettings:
        return settings

    llm_client.get_llm_settings = _override  # type: ignore[assignment]
    try:
        yield
    finally:
        llm_client.get_llm_settings = original  # type: ignore[assignment]


def configured_models(models: list[str]) -> list[tuple[str, str]]:
    """Filter a model list to those whose provider has a key configured.

    Returns ``[(provider, model), ...]``. Models with unknown providers or
    missing keys are dropped; the caller logs the drop.
    """
    out: list[tuple[str, str]] = []
    for m in models:
        provider = resolve_provider(m)
        if not provider:
            continue
        if provider == "ollama":
            out.append((provider, m))
            continue
        if _env_key(provider):
            out.append((provider, m))
    return out

"""Plural seam for LLM provider implementations.

Each backend (Anthropic, OpenAI, Gemini, Ollama) lives in its own module
in this directory and exposes a module-level ``PROVIDER`` instance that
satisfies :class:`Provider`. Adding a backend = drop a new module and
register it here. ``app.llm.client`` is a thin facade over the registry.

Why a registry instead of an if/elif in ``client.py``: every if-branch
forced ``client.py`` to grow when we added a backend, and tests that
patched the SDK seam had to know the branching layout. With one module
per backend, ``client.py`` just dispatches and tests patch the seam in
the provider module they care about.

The Provider interface is the **only** contract the rest of the app
relies on. Provider modules must:

* Translate the normalized message shape (see ``client.py``) to/from
  whatever the SDK expects.
* Translate the normalized tool spec ``{name, description, input_schema}``
  to/from the SDK shape (input_schema is JSON Schema; providers may need
  to drop unsupported keywords, but should not change semantics).
* Yield ``StreamEvent`` dicts in the order they arrive: ``text_delta``,
  ``tool_call`` (after the args JSON is fully assembled), and exactly one
  terminal ``done`` event with ``stop_reason`` + ``usage``.
* Map SDK-specific exceptions to ``LLMError`` (raised by the SDK SDK,
  caught in the provider's ``stream``).
"""

from __future__ import annotations

from typing import Any, Iterator, Protocol

from app.llm.settings import LLMSettings

StreamEvent = dict[str, Any]
# Event shapes (see app.llm.client for the public-facing copy):
#   {"type": "text_delta",  "text": str}
#   {"type": "tool_call",   "id": str, "name": str, "arguments": dict}
#   {"type": "done",        "stop_reason": str,
#                            "usage": {"input_tokens": int, "output_tokens": int}}


class Provider(Protocol):
    """Single backend behind the LLM seam.

    Implementations are module-level singletons exposed as ``PROVIDER``.
    Keep them stateless past the cached SDK client — settings come in on
    every call so the admin UI can flip provider/model live without a
    process restart.
    """

    name: str
    """Stable identifier; matches ``llm_settings.provider`` rows."""

    def check_configured(self, settings: LLMSettings) -> None:
        """Raise ``LLMError(code='not_configured', ...)`` if creds are missing.

        ``client.stream`` calls this before invoking ``stream`` so the
        not-configured error has a friendly admin-page-pointing message.
        """
        ...

    def test_connection(self, settings: LLMSettings, *, model: str) -> dict[str, Any]:
        """Preflight the provider's SAVED credentials: a cheap listing probe when
        the backend has one (non-fatal if unsupported) plus a minimal 1-token
        completion against ``model``. Returns a redacted diagnostics dict (keys:
        ok, base_url, auth_present, model, models_endpoint, completion) — NEVER
        credentials."""
        ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        settings: LLMSettings,
    ) -> Iterator[StreamEvent]:
        """Stream a single completion. See module docstring for the contract."""
        ...


PROVIDERS: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    """Add a provider to the registry. Called from each provider module at import."""
    PROVIDERS[provider.name] = provider


def get(name: str) -> Provider | None:
    return PROVIDERS.get(name)


def names() -> list[str]:
    return sorted(PROVIDERS.keys())


# Eager registration so unknown-provider checks in client.py reflect what's
# actually compiled in. Keep imports at the bottom — each module references
# `register` from this module.
from app.llm.providers import anthropic as _anthropic  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]
from app.llm.providers import bedrock as _bedrock  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]
from app.llm.providers import custom as _custom  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]
from app.llm.providers import gemini as _gemini  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]
from app.llm.providers import ollama as _ollama  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]
from app.llm.providers import openai as _openai  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]

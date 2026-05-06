"""Single entry point for LLM calls. Provider chosen via CONFIG.llm_provider."""
from __future__ import annotations

from typing import Any

from app.config import CONFIG


def complete(messages: list[dict[str, Any]], *, model: str | None = None, tools: list | None = None) -> dict:
    """Run a single LLM completion. Returns provider-normalized dict."""
    model = model or CONFIG.llm_model
    if CONFIG.llm_provider == "anthropic":
        return _anthropic_complete(messages, model=model, tools=tools)
    if CONFIG.llm_provider == "openai":
        return _openai_complete(messages, model=model, tools=tools)
    raise ValueError(f"unknown LLM provider: {CONFIG.llm_provider}")


def _anthropic_complete(messages, *, model, tools):
    # TODO: import anthropic, build client with CONFIG.anthropic_api_key,
    # call messages.create with prompt caching enabled, normalize response.
    raise NotImplementedError


def _openai_complete(messages, *, model, tools):
    # TODO: openai client, chat.completions.create, normalize response.
    raise NotImplementedError

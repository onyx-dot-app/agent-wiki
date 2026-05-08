"""DB-backed LLM settings. Configured via the admin page; no env-var fallback."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.db.sqlite import connect

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    anthropic_api_key: str
    openai_api_key: str
    gemini_api_key: str
    ollama_base_url: str


def get() -> LLMSettings:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT provider, model, anthropic_api_key, openai_api_key, "
            "gemini_api_key, ollama_base_url "
            "FROM llm_settings WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return LLMSettings(
            provider="",
            model="",
            anthropic_api_key="",
            openai_api_key="",
            gemini_api_key="",
            ollama_base_url="",
        )
    return LLMSettings(
        provider=row["provider"],
        model=row["model"],
        anthropic_api_key=row["anthropic_api_key"],
        openai_api_key=row["openai_api_key"],
        gemini_api_key=row["gemini_api_key"],
        ollama_base_url=row["ollama_base_url"],
    )


def upsert(
    *,
    provider: str,
    model: str,
    anthropic_api_key: str,
    openai_api_key: str,
    gemini_api_key: str,
    ollama_base_url: str,
) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO llm_settings ("
            "  id, provider, model, anthropic_api_key, openai_api_key, "
            "  gemini_api_key, ollama_base_url, updated_at"
            ") VALUES (1, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  provider=excluded.provider, "
            "  model=excluded.model, "
            "  anthropic_api_key=excluded.anthropic_api_key, "
            "  openai_api_key=excluded.openai_api_key, "
            "  gemini_api_key=excluded.gemini_api_key, "
            "  ollama_base_url=excluded.ollama_base_url, "
            "  updated_at=datetime('now')",
            (
                provider,
                model,
                anthropic_api_key,
                openai_api_key,
                gemini_api_key,
                ollama_base_url,
            ),
        )
    finally:
        conn.close()
    log.info("llm_settings upserted provider=%s model=%s", provider, model)

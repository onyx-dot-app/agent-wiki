"""DB-backed LLM settings. Configured via the admin page; no env-var fallback."""
from __future__ import annotations

from dataclasses import dataclass

from app.db.sqlite import connect


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    anthropic_api_key: str
    openai_api_key: str


def get() -> LLMSettings:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT provider, model, anthropic_api_key, openai_api_key FROM llm_settings WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return LLMSettings(provider="", model="", anthropic_api_key="", openai_api_key="")
    return LLMSettings(
        provider=row["provider"],
        model=row["model"],
        anthropic_api_key=row["anthropic_api_key"],
        openai_api_key=row["openai_api_key"],
    )


def upsert(*, provider: str, model: str, anthropic_api_key: str, openai_api_key: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO llm_settings (id, provider, model, anthropic_api_key, openai_api_key, updated_at) "
            "VALUES (1, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  provider=excluded.provider, "
            "  model=excluded.model, "
            "  anthropic_api_key=excluded.anthropic_api_key, "
            "  openai_api_key=excluded.openai_api_key, "
            "  updated_at=datetime('now')",
            (provider, model, anthropic_api_key, openai_api_key),
        )
    finally:
        conn.close()

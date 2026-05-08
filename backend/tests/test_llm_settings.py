"""Tests for app/llm/settings.py — DB-backed LLM configuration."""
from __future__ import annotations

from app.llm import settings as llm_settings


def _upsert(**overrides) -> None:
    """Upsert with empty defaults for fields the test doesn't care about.

    Keeps test bodies focused on the field-under-test instead of a long
    list of empty-string positional arguments.
    """
    base = {
        "provider": "",
        "model": "",
        "anthropic_api_key": "",
        "openai_api_key": "",
        "gemini_api_key": "",
        "ollama_base_url": "",
    }
    base.update(overrides)
    llm_settings.upsert(**base)


def test_get_returns_empty_defaults_when_no_row(tmp_db):
    s = llm_settings.get()
    assert s.provider == ""
    assert s.model == ""
    assert s.anthropic_api_key == ""
    assert s.openai_api_key == ""
    assert s.gemini_api_key == ""
    assert s.ollama_base_url == ""


def test_upsert_then_get_round_trip(tmp_db):
    _upsert(
        provider="anthropic",
        model="claude-opus-4-7",
        anthropic_api_key="sk-ant-test",
    )

    s = llm_settings.get()
    assert s.provider == "anthropic"
    assert s.model == "claude-opus-4-7"
    assert s.anthropic_api_key == "sk-ant-test"
    assert s.openai_api_key == ""


def test_upsert_overwrites_existing_row(tmp_db):
    _upsert(provider="anthropic", model="claude-opus-4-7", anthropic_api_key="sk-ant-old")
    _upsert(provider="openai", model="gpt-4o", openai_api_key="sk-openai-new")

    s = llm_settings.get()
    assert s.provider == "openai"
    assert s.model == "gpt-4o"
    assert s.anthropic_api_key == ""
    assert s.openai_api_key == "sk-openai-new"


def test_upsert_round_trips_gemini_and_ollama(tmp_db):
    _upsert(
        provider="gemini",
        model="gemini-2.5-pro",
        gemini_api_key="AIza-test",
        ollama_base_url="http://localhost:11434",
    )

    s = llm_settings.get()
    assert s.provider == "gemini"
    assert s.gemini_api_key == "AIza-test"
    assert s.ollama_base_url == "http://localhost:11434"


def test_settings_row_is_singleton(tmp_db):
    """The migration constrains id=1; upsert must keep exactly one row."""
    _upsert(provider="anthropic", model="m1", anthropic_api_key="a")
    _upsert(provider="openai", model="m2", openai_api_key="b")

    from app.db.sqlite import connect

    conn = connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM llm_settings").fetchone()["c"]
    finally:
        conn.close()
    assert count == 1

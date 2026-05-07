"""Tests for app/llm/settings.py — DB-backed LLM configuration."""
from __future__ import annotations

from app.llm import settings as llm_settings


def test_get_returns_empty_defaults_when_no_row(tmp_db):
    s = llm_settings.get()
    assert s.provider == ""
    assert s.model == ""
    assert s.anthropic_api_key == ""
    assert s.openai_api_key == ""


def test_upsert_then_get_round_trip(tmp_db):
    llm_settings.upsert(
        provider="anthropic",
        model="claude-opus-4-7",
        anthropic_api_key="sk-ant-test",
        openai_api_key="",
    )

    s = llm_settings.get()
    assert s.provider == "anthropic"
    assert s.model == "claude-opus-4-7"
    assert s.anthropic_api_key == "sk-ant-test"
    assert s.openai_api_key == ""


def test_upsert_overwrites_existing_row(tmp_db):
    llm_settings.upsert(
        provider="anthropic",
        model="claude-opus-4-7",
        anthropic_api_key="sk-ant-old",
        openai_api_key="",
    )
    llm_settings.upsert(
        provider="openai",
        model="gpt-4o",
        anthropic_api_key="",
        openai_api_key="sk-openai-new",
    )

    s = llm_settings.get()
    assert s.provider == "openai"
    assert s.model == "gpt-4o"
    assert s.anthropic_api_key == ""
    assert s.openai_api_key == "sk-openai-new"


def test_settings_row_is_singleton(tmp_db):
    """The migration constrains id=1; upsert must keep exactly one row."""
    llm_settings.upsert(
        provider="anthropic", model="m1", anthropic_api_key="a", openai_api_key=""
    )
    llm_settings.upsert(
        provider="openai", model="m2", anthropic_api_key="", openai_api_key="b"
    )

    from app.db.sqlite import connect

    conn = connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM llm_settings").fetchone()["c"]
    finally:
        conn.close()
    assert count == 1

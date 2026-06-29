"""Tests for app/llm/settings.py — DB-backed LLM configuration."""

from __future__ import annotations

from typing import Any

from app.db.models import LLMSettings as LLMSettingsRow
from app.llm import settings as llm_settings

from tests._seed import count_rows


def _upsert(**overrides: Any) -> None:
    """Upsert with empty defaults for fields the test doesn't care about.

    Keeps test bodies focused on the field-under-test instead of a long
    list of empty-string positional arguments.
    """
    base: dict[str, Any] = {
        "provider": "",
        "model": "",
        "anthropic_api_key": "",
        "openai_api_key": "",
        "gemini_api_key": "",
        "ollama_base_url": "",
        "custom_api_key": "",
        "custom_base_url": "",
        "custom_display_name": "",
        "bedrock_aws_region": "",
        "bedrock_endpoint_url": "",
        "bedrock_aws_access_key_id": "",
        "bedrock_aws_secret_access_key": "",
        "bedrock_aws_session_token": "",
        "bedrock_aws_bearer_token": "",
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


def test_provider_models_defaults_to_empty_dict_not_none(tmp_db) -> None:
    assert llm_settings.get().provider_models == {}

    _upsert(provider="openai", model="gpt-4o")

    assert llm_settings.get().provider_models == {}


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
    """The schema constrains id=1; upsert must keep exactly one row."""
    _upsert(provider="anthropic", model="m1", anthropic_api_key="a")
    _upsert(provider="openai", model="m2", openai_api_key="b")
    assert count_rows(LLMSettingsRow) == 1

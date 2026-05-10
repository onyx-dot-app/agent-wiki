"""Tests for app/tracing/settings.py — DB-backed Braintrust configuration."""
from __future__ import annotations

from app.db.models import BraintrustSettings as BraintrustSettingsRow
from app.tracing import settings as braintrust_settings

from tests._seed import count_rows


def test_get_returns_empty_defaults_when_no_row(tmp_db):
    s = braintrust_settings.get()
    assert s.project == ""
    assert s.api_key == ""
    assert s.enabled is False


def test_upsert_then_get_round_trip(tmp_db):
    braintrust_settings.upsert(project="agent-wiki", api_key="sk-bt-test", enabled=True)
    s = braintrust_settings.get()
    assert s.project == "agent-wiki"
    assert s.api_key == "sk-bt-test"
    assert s.enabled is True


def test_upsert_overwrites_existing_row(tmp_db):
    braintrust_settings.upsert(project="p1", api_key="k1", enabled=True)
    braintrust_settings.upsert(project="p2", api_key="k2", enabled=False)
    s = braintrust_settings.get()
    assert s.project == "p2"
    assert s.api_key == "k2"
    assert s.enabled is False


def test_settings_row_is_singleton(tmp_db):
    """Schema constrains id=1; upsert must keep exactly one row."""
    braintrust_settings.upsert(project="p1", api_key="k1", enabled=True)
    braintrust_settings.upsert(project="p2", api_key="k2", enabled=True)
    assert count_rows(BraintrustSettingsRow) == 1

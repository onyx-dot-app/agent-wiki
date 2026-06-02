"""Tests for app/slack/settings.py — DB-backed Slack webhook configuration."""
from __future__ import annotations

from app.db.models import SlackSettings as SlackSettingsRow
from app.slack import settings as slack_settings

from tests._seed import count_rows

_HOOK = "https://hooks.slack.com/services/T00/B00/XXXXXXXX"


def test_get_returns_empty_defaults_when_no_row(tmp_db):
    s = slack_settings.get()
    assert s.webhook_url == ""
    assert s.enabled is False


def test_upsert_then_get_round_trip(tmp_db):
    slack_settings.upsert(webhook_url=_HOOK, enabled=True)
    s = slack_settings.get()
    assert s.webhook_url == _HOOK
    assert s.enabled is True


def test_upsert_overwrites_existing_row(tmp_db):
    slack_settings.upsert(webhook_url=_HOOK + "1", enabled=True)
    slack_settings.upsert(webhook_url=_HOOK + "2", enabled=False)
    s = slack_settings.get()
    assert s.webhook_url == _HOOK + "2"
    assert s.enabled is False


def test_settings_row_is_singleton(tmp_db):
    """Schema constrains id=1; upsert must keep exactly one row."""
    slack_settings.upsert(webhook_url=_HOOK + "1", enabled=True)
    slack_settings.upsert(webhook_url=_HOOK + "2", enabled=True)
    assert count_rows(SlackSettingsRow) == 1

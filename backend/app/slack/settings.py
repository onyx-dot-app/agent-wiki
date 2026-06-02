"""DB-backed Slack webhook settings. Configured from /admin/slack.

Mirrors ``app/tracing/settings.py``: a singleton row holding the incoming
webhook URL and an enabled flag. Read at trigger-fire time by the Slack
dispatcher in ``app/tasks/triggers.py``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import SlackSettings as SlackSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)


class SlackSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    webhook_url: str
    enabled: bool


_EMPTY = SlackSettings(webhook_url="", enabled=False)


def get() -> SlackSettings:
    with session() as s:
        row = s.get(SlackSettingsRow, 1)
        if row is None:
            return _EMPTY
        return SlackSettings(webhook_url=row.webhook_url, enabled=row.enabled)


def upsert(*, webhook_url: str, enabled: bool) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(SlackSettingsRow, 1)
        if row is None:
            s.add(
                SlackSettingsRow(
                    id=1,
                    webhook_url=webhook_url,
                    enabled=enabled,
                    updated_at=now,
                )
            )
        else:
            row.webhook_url = webhook_url
            row.enabled = enabled
            row.updated_at = now
    log.info("slack_settings upserted webhook_set=%s enabled=%s", bool(webhook_url), enabled)

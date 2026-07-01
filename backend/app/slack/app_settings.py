"""DB-backed Slack app OAuth credentials. Configured from /admin/slack."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import SlackAppSettings as SlackAppSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)


class SlackAppSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_id: str
    client_secret: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


_EMPTY = SlackAppSettings(client_id="", client_secret="")


def get() -> SlackAppSettings:
    with session() as s:
        row = s.get(SlackAppSettingsRow, 1)
        if row is None:
            return _EMPTY
        return SlackAppSettings(client_id=row.client_id, client_secret=row.client_secret)


def upsert(*, client_id: str, client_secret: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(SlackAppSettingsRow, 1)
        if row is None:
            s.add(
                SlackAppSettingsRow(
                    id=1, client_id=client_id, client_secret=client_secret, updated_at=now
                )
            )
        else:
            row.client_id = client_id
            row.client_secret = client_secret
            row.updated_at = now
    log.info("slack_app_settings upserted client_id_set=%s secret_set=%s",
             bool(client_id), bool(client_secret))

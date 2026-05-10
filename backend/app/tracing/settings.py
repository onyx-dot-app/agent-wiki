"""DB-backed Braintrust tracing settings. Configured from /admin/braintrust."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import BraintrustSettings as BraintrustSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)


class BraintrustSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    project: str
    api_key: str
    enabled: bool


_EMPTY = BraintrustSettings(project="", api_key="", enabled=False)


def get() -> BraintrustSettings:
    with session() as s:
        row = s.get(BraintrustSettingsRow, 1)
        if row is None:
            return _EMPTY
        return BraintrustSettings(
            project=row.project,
            api_key=row.api_key,
            enabled=row.enabled,
        )


def upsert(*, project: str, api_key: str, enabled: bool) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(BraintrustSettingsRow, 1)
        if row is None:
            s.add(
                BraintrustSettingsRow(
                    id=1,
                    project=project,
                    api_key=api_key,
                    enabled=enabled,
                    updated_at=now,
                )
            )
        else:
            row.project = project
            row.api_key = api_key
            row.enabled = enabled
            row.updated_at = now
    log.info(
        "braintrust_settings upserted project=%s key_set=%s enabled=%s",
        project, bool(api_key), enabled,
    )

"""DB-backed wiki-wide settings. Configured from /admin/app-settings.

Singleton (``id=1``) row holding the auto-update health knobs — the default
per-page warning threshold and the global hard cap. ``0`` disables either.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import AppSettings as AppSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)


class AppSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    warn_update_threshold_default: int
    auto_update_cap: int


# Matches the column server_defaults — used until an admin saves a row.
_DEFAULTS = AppSettings(warn_update_threshold_default=10, auto_update_cap=0)


def get() -> AppSettings:
    with session() as s:
        row = s.get(AppSettingsRow, 1)
        if row is None:
            return _DEFAULTS
        return AppSettings(
            warn_update_threshold_default=row.warn_update_threshold_default,
            auto_update_cap=row.auto_update_cap,
        )


def upsert(*, warn_update_threshold_default: int, auto_update_cap: int) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(AppSettingsRow, 1)
        if row is None:
            s.add(
                AppSettingsRow(
                    id=1,
                    warn_update_threshold_default=warn_update_threshold_default,
                    auto_update_cap=auto_update_cap,
                    updated_at=now,
                )
            )
        else:
            row.warn_update_threshold_default = warn_update_threshold_default
            row.auto_update_cap = auto_update_cap
            row.updated_at = now
    log.info(
        "app_settings upserted warn_default=%d auto_update_cap=%d",
        warn_update_threshold_default,
        auto_update_cap,
    )

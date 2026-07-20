"""Org-wide AI-management (Auto Organize) settings — the
``ai_management_settings`` singleton (id=1).

``enabled`` is the master kill switch. Free functions over the row returning a
frozen pydantic model, mirroring ``app/ingest/settings.py``. The switch is a
feature gate only — it never touches per-page ``ai_management_allowed`` policy,
so disabling then re-enabling resumes exactly where the per-page opt-ins left
off.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import AIManagementSettings as AIManagementSettingsRow
from app.db.session import session

DEFAULT_ENABLED = True


class AIManagementSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    updated_at: str | None
    updated_by_user_id: str | None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get() -> AIManagementSettings:
    """Current settings, or the defaults when no row exists yet (feature on)."""
    with session() as s:
        row = s.get(AIManagementSettingsRow, 1)
        if row is None:
            return AIManagementSettings(
                enabled=DEFAULT_ENABLED,
                updated_at=None,
                updated_by_user_id=None,
            )
        return AIManagementSettings(
            enabled=row.enabled,
            updated_at=row.updated_at,
            updated_by_user_id=row.updated_by_user_id,
        )


def is_enabled() -> bool:
    """Master kill switch — the one check every detection/execution entry point
    gates on. Defaults to True (the feature is on until an admin turns it off)."""
    return get().enabled


def update(
    *,
    enabled: bool | None = None,
    updated_by_user_id: str | None = None,
) -> AIManagementSettings:
    """Patch semantics: only fields passed change. Returns the resulting settings."""
    with session() as s:
        row = s.get(AIManagementSettingsRow, 1)
        if row is None:
            row = AIManagementSettingsRow(id=1)
            s.add(row)
        if enabled is not None:
            row.enabled = enabled
        row.updated_at = _now()
        row.updated_by_user_id = updated_by_user_id
    return get()

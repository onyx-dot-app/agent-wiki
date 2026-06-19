"""DB-backed ingest settings. Configured from /admin/ingest."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import IngestSettings as IngestSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)

DEFAULT_MAX_DOC_CHARS = 100_000


class IngestSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_doc_chars: int
    api_key: str | None
    # Outbound half of the "Onyx Connection" admin page — the public Onyx
    # origin for Craft launches. None = Craft unavailable.
    onyx_base_url: str | None


def get() -> IngestSettings:
    with session() as s:
        row = s.get(IngestSettingsRow, 1)
        if row is None:
            return IngestSettings(max_doc_chars=DEFAULT_MAX_DOC_CHARS, api_key=None, onyx_base_url=None)
        return IngestSettings(max_doc_chars=row.max_doc_chars, api_key=row.api_key, onyx_base_url=row.onyx_base_url)


def get_onyx_base_url() -> str | None:
    """The admin-configured Onyx origin, or None when not set."""
    return get().onyx_base_url


def upsert(*, max_doc_chars: int) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(IngestSettingsRow, 1)
        if row is None:
            s.add(IngestSettingsRow(id=1, max_doc_chars=max_doc_chars, updated_at=now))
        else:
            row.max_doc_chars = max_doc_chars
            row.updated_at = now
    log.info("ingest_settings upserted max_doc_chars=%d", max_doc_chars)


def regenerate_key() -> str:
    """Generate a new API key, persist it, and return it."""
    key = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(IngestSettingsRow, 1)
        if row is None:
            s.add(IngestSettingsRow(
                id=1, max_doc_chars=DEFAULT_MAX_DOC_CHARS, api_key=key, updated_at=now,
            ))
        else:
            row.api_key = key
            row.updated_at = now
    log.info("ingest_settings: api_key regenerated")
    return key

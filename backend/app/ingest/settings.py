"""DB-backed ingest settings. Configured from /admin/ingest."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import IngestSettings as IngestSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)

DEFAULT_MAX_DOC_CHARS = 100_000


class IngestSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_doc_chars: int


def get() -> IngestSettings:
    with session() as s:
        row = s.get(IngestSettingsRow, 1)
        if row is None:
            return IngestSettings(max_doc_chars=DEFAULT_MAX_DOC_CHARS)
        return IngestSettings(max_doc_chars=row.max_doc_chars)


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

"""DB-backed ingest settings. Configured from /admin/ingest."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.db.sqlite import connect

log = logging.getLogger(__name__)

DEFAULT_MAX_DOC_CHARS = 100_000


@dataclass(frozen=True)
class IngestSettings:
    max_doc_chars: int


def get() -> IngestSettings:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT max_doc_chars FROM ingest_settings WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return IngestSettings(max_doc_chars=DEFAULT_MAX_DOC_CHARS)
    return IngestSettings(max_doc_chars=int(row["max_doc_chars"]))


def upsert(*, max_doc_chars: int) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO ingest_settings (id, max_doc_chars, updated_at) "
            "VALUES (1, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  max_doc_chars=excluded.max_doc_chars, "
            "  updated_at=datetime('now')",
            (max_doc_chars,),
        )
    finally:
        conn.close()
    log.info("ingest_settings upserted max_doc_chars=%d", max_doc_chars)

"""DB-backed web search/crawl settings. Configured from /admin/web."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.db.sqlite import connect

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebSettings:
    serper_api_key: str
    firecrawl_api_key: str


def get() -> WebSettings:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT serper_api_key, firecrawl_api_key FROM web_settings WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return WebSettings(serper_api_key="", firecrawl_api_key="")
    return WebSettings(
        serper_api_key=row["serper_api_key"],
        firecrawl_api_key=row["firecrawl_api_key"],
    )


def upsert(*, serper_api_key: str, firecrawl_api_key: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO web_settings (id, serper_api_key, firecrawl_api_key, updated_at) "
            "VALUES (1, ?, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  serper_api_key=excluded.serper_api_key, "
            "  firecrawl_api_key=excluded.firecrawl_api_key, "
            "  updated_at=datetime('now')",
            (serper_api_key, firecrawl_api_key),
        )
    finally:
        conn.close()
    log.info(
        "web_settings upserted serper_set=%s firecrawl_set=%s",
        bool(serper_api_key), bool(firecrawl_api_key),
    )

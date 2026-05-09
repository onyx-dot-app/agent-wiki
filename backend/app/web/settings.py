"""DB-backed web search/crawl settings. Configured from /admin/web."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.db.models import WebSettings as WebSettingsRow
from app.db.session import session

log = logging.getLogger(__name__)


class WebSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    serper_api_key: str
    firecrawl_api_key: str


def get() -> WebSettings:
    with session() as s:
        row = s.get(WebSettingsRow, 1)
        if row is None:
            return WebSettings(serper_api_key="", firecrawl_api_key="")
        return WebSettings(
            serper_api_key=row.serper_api_key,
            firecrawl_api_key=row.firecrawl_api_key,
        )


def upsert(*, serper_api_key: str, firecrawl_api_key: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(WebSettingsRow, 1)
        if row is None:
            s.add(
                WebSettingsRow(
                    id=1,
                    serper_api_key=serper_api_key,
                    firecrawl_api_key=firecrawl_api_key,
                    updated_at=now,
                )
            )
        else:
            row.serper_api_key = serper_api_key
            row.firecrawl_api_key = firecrawl_api_key
            row.updated_at = now
    log.info(
        "web_settings upserted serper_set=%s firecrawl_set=%s",
        bool(serper_api_key), bool(firecrawl_api_key),
    )

"""Firecrawl crawl client. https://www.firecrawl.dev/

Implements ``CrawlProvider``. Each URL is fetched independently via the
v2 ``/scrape`` endpoint; failures degrade to ``scrape_successful=False``
rather than aborting the whole batch.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from app.web.models import CrawlProvider, WebContent

log = logging.getLogger(__name__)

_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
_REQUEST_TIMEOUT_SECONDS = 55
_DEFAULT_MAX_WORKERS = 5


class FirecrawlApiError(RuntimeError):
    pass


class FirecrawlClient(CrawlProvider):
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("Firecrawl api_key is required")
        self._headers: dict[str, str | bytes] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def contents(self, urls: Sequence[str]) -> Sequence[WebContent]:
        if not urls:
            return []
        max_workers = min(_DEFAULT_MAX_WORKERS, len(urls))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(self._fetch_one_safe, urls))

    def test_connection(self) -> None:
        """Raises ``FirecrawlApiError`` if the configured key is rejected."""
        result = self._fetch_one("https://example.com")
        if not result.scrape_successful:
            raise FirecrawlApiError("firecrawl returned no content for example.com")

    def _fetch_one_safe(self, url: str) -> WebContent:
        try:
            return self._fetch_one(url)
        except Exception as exc:
            log.warning("firecrawl fetch failed url=%s err=%s", url, exc)
            return WebContent(
                title="",
                link=url,
                full_content="",
                published_date=None,
                scrape_successful=False,
            )

    def _fetch_one(self, url: str) -> WebContent:
        response = requests.post(
            _SCRAPE_URL,
            headers=self._headers,
            json={"url": url, "formats": ["markdown"]},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code == 401 or response.status_code == 403:
            raise FirecrawlApiError("firecrawl rejected the API key")
        if 400 <= response.status_code < 500:
            return WebContent(
                title="",
                link=url,
                full_content="",
                published_date=None,
                scrape_successful=False,
            )
        if response.status_code >= 500:
            raise FirecrawlApiError(
                f"firecrawl returned status {response.status_code}"
            )

        body: dict[str, Any] = response.json()
        text, title = _extract(body)
        return WebContent(
            title=title,
            link=url,
            full_content=text,
            published_date=None,
            scrape_successful=bool(text),
        )


def _extract(body: dict[str, Any]) -> tuple[str, str]:
    data: dict[str, Any] = body.get("data") or {}
    metadata: dict[str, Any] = data.get("metadata") or body.get("metadata") or {}
    text: str = (
        data.get("markdown")
        or data.get("content")
        or body.get("markdown")
        or body.get("content")
        or ""
    )
    title: str = metadata.get("title") or body.get("title") or ""
    return text, title

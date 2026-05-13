"""Serper search client. https://serper.dev/

Implements ``SearchProvider``. Only the Google search endpoint is used —
crawl is delegated to Firecrawl.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import requests

from app.web.models import SearchProvider, WebSearchResult

log = logging.getLogger(__name__)

_SEARCH_URL = "https://google.serper.dev/search"
_REQUEST_TIMEOUT_SECONDS = 60


class SerperApiError(RuntimeError):
    pass


class SerperClient(SearchProvider):
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("Serper api_key is required")
        self._headers: dict[str, str | bytes] = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        }

    def search(self, query: str, *, num_results: int = 10) -> Sequence[WebSearchResult]:
        if not query.strip():
            return []

        try:
            response = requests.post(
                _SEARCH_URL,
                headers=self._headers,
                json={"q": query, "num": num_results},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SerperApiError(f"serper request failed: {exc}") from exc

        if response.status_code == 401 or response.status_code == 403:
            raise SerperApiError("serper rejected the API key")
        if response.status_code >= 400:
            raise SerperApiError(
                f"serper returned status {response.status_code}: {response.text[:200]}"
            )

        body: dict[str, Any] = response.json()
        organic: list[dict[str, Any]] = body.get("organic") or []

        results: list[WebSearchResult] = []
        for item in organic:
            link = (item.get("link") or "").strip()
            if not link:
                continue
            results.append(
                WebSearchResult(
                    title=(item.get("title") or "").strip(),
                    link=link,
                    snippet=(item.get("snippet") or "").strip(),
                )
            )
        return results

    def test_connection(self) -> None:
        """Raises ``SerperApiError`` if the configured key cannot reach Serper."""
        self.search("ping", num_results=1)

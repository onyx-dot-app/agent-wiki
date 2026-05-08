"""Web search + web crawl.

The package exposes a small, provider-agnostic surface:

    from app import web
    results = web.search("flask routing")
    pages = web.fetch(["https://example.com"])

Search is fixed to Serper. Crawl is fixed to Firecrawl. API keys are
stored per-install in ``web_settings`` and configured from the admin UI;
there is no env-var fallback (matches ``app.llm.settings``).

To swap or add a provider, change the factories below — call sites do
not branch on provider.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.web import settings as web_settings
from app.web.firecrawl import FirecrawlClient
from app.web.models import (
    SearchProvider,
    CrawlProvider,
    WebContent,
    WebSearchResult,
)
from app.web.serper import SerperClient


class WebProviderNotConfigured(RuntimeError):
    """Raised when a provider's API key has not been set."""


def search_provider() -> SearchProvider:
    s = web_settings.get()
    if not s.serper_api_key:
        raise WebProviderNotConfigured(
            "Serper API key is not configured (set it on /admin/web)."
        )
    return SerperClient(api_key=s.serper_api_key)


def crawl_provider() -> CrawlProvider:
    s = web_settings.get()
    if not s.firecrawl_api_key:
        raise WebProviderNotConfigured(
            "Firecrawl API key is not configured (set it on /admin/web)."
        )
    return FirecrawlClient(api_key=s.firecrawl_api_key)


def search(query: str, *, num_results: int = 10) -> list[WebSearchResult]:
    return list(search_provider().search(query, num_results=num_results))


def fetch(urls: Sequence[str]) -> list[WebContent]:
    return list(crawl_provider().contents(urls))


__all__ = [
    "WebContent",
    "WebSearchResult",
    "WebProviderNotConfigured",
    "search",
    "fetch",
    "search_provider",
    "crawl_provider",
]

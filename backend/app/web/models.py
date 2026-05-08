"""Provider-agnostic shapes for web search + crawl.

Both providers normalize to the same return types so callers don't branch
on which backend is in use.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel


class WebSearchResult(BaseModel):
    title: str
    link: str
    snippet: str
    published_date: datetime | None = None


class WebContent(BaseModel):
    title: str
    link: str
    full_content: str
    published_date: datetime | None = None
    scrape_successful: bool = True


class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, *, num_results: int = 10) -> Sequence[WebSearchResult]:
        ...


class CrawlProvider(ABC):
    @abstractmethod
    def contents(self, urls: Sequence[str]) -> Sequence[WebContent]:
        ...

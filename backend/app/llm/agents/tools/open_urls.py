"""Handler for the `open_urls` tool. Spec lives in `open_urls.json`.

Multi-URL fetch over ``app.web.fetch`` (Firecrawl today). Firecrawl's
``contents`` already runs the fetches concurrently, so callers should
batch URLs into one tool call instead of issuing parallel `open_urls`
invocations.
"""
from __future__ import annotations

import logging
from typing import Any, cast

from app import web

log = logging.getLogger(__name__)

MAX_URLS = 10


def handle(args: dict[str, Any]) -> Any:
    raw = args.get("urls")
    if not isinstance(raw, list) or not raw:
        return {"error": "urls is required (non-empty list of http(s) URLs)"}
    items = cast(list[Any], raw)

    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            return {"error": "each url must be a non-empty string"}
        url = item.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return {"error": f"url must start with http:// or https://: {url!r}"}
        cleaned.append(url)

    if len(cleaned) > MAX_URLS:
        return {"error": f"too many urls (max {MAX_URLS})"}

    try:
        results = web.fetch(cleaned)
    except web.WebProviderNotConfigured as exc:
        return {"error": str(exc)}
    except Exception as exc:
        log.exception("open_urls failed urls=%r", cleaned)
        return {"error": f"fetch failed: {exc}"}

    return {
        "results": [
            {
                "title": r.title,
                "link": r.link,
                "full_content": r.full_content,
                "published_date": r.published_date.isoformat() if r.published_date else None,
                "scrape_successful": r.scrape_successful,
            }
            for r in results
        ]
    }

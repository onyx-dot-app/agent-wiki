"""Handler for the `open_url` tool. Spec lives in `open_url.json`.

Single-URL fetch over ``app.web.fetch`` (Firecrawl today). Multiple URLs
in one turn = multiple parallel tool calls; we keep the schema scalar so
the model doesn't have to remember to wrap one URL in an array.
"""
from __future__ import annotations

import logging
from typing import Any

from app import web

log = logging.getLogger(__name__)


def handle(args: dict[str, Any]) -> Any:
    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        return {"error": "url is required"}
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"error": "url must start with http:// or https://"}

    try:
        results = web.fetch([url])
    except web.WebProviderNotConfigured as exc:
        return {"error": str(exc)}
    except Exception as exc:
        log.exception("open_url failed url=%r", url)
        return {"error": f"fetch failed: {exc}"}

    if not results:
        return {"error": "fetch returned no content"}

    r = results[0]
    return {
        "title": r.title,
        "link": r.link,
        "full_content": r.full_content,
        "published_date": r.published_date.isoformat() if r.published_date else None,
        "scrape_successful": r.scrape_successful,
    }

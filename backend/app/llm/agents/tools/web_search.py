"""Handler for the `web_search` tool. Spec lives in `web_search.json`.

Provider-agnostic: calls ``app.web.search``. The actual backend (Serper
today) is selected by ``app.web.search_provider`` and configured in the
admin UI; if no key is set, we surface a helpful error to the model.
"""
from __future__ import annotations

import logging
from typing import Any

from app import web

log = logging.getLogger(__name__)

DEFAULT_NUM = 10
MAX_NUM = 20


def handle(args: dict[str, Any]) -> Any:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"error": "query is required"}

    raw = args.get("num_results")
    try:
        num = int(raw) if raw is not None else DEFAULT_NUM
    except (TypeError, ValueError):
        num = DEFAULT_NUM
    num = max(1, min(num, MAX_NUM))

    try:
        results = web.search(query.strip(), num_results=num)
    except web.WebProviderNotConfigured as exc:
        return {"error": str(exc)}
    except Exception as exc:
        log.exception("web_search failed query=%r", query)
        return {"error": f"web search failed: {exc}"}

    return {
        "results": [
            {
                "title": r.title,
                "link": r.link,
                "snippet": r.snippet,
                "published_date": (
                    r.published_date.isoformat() if r.published_date else None
                ),
            }
            for r in results
        ]
    }

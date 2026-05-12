"""Handler for the `search_wiki` tool. Spec lives in `search_wiki.json`.

Discovery layer over the search index. Returns
``{path, title, snippet, score}`` per hit. The ``snippet`` is a
match-aware extraction (~64 tokens of context around the densest cluster
of matched terms, with matches wrapped in ``**...**``). Full bodies
come from ``read_page``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.exc import OperationalError

from app.auth import current_user
from app.wiki import search as wiki_search

DEFAULT_LIMIT = 10
MAX_LIMIT = 20


def handle(args: dict[str, Any]) -> Any:
    query = args.get("query")
    query = query.strip() if isinstance(query, str) else ""
    if not query:
        return {"error": "query is required"}

    raw_limit = args.get("limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else DEFAULT_LIMIT
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    # Agent inherits the calling user's visibility — chat tools run inside
    # an authenticated request, so ``current_user()`` returns the same
    # principal the request was authenticated as.
    user = current_user()
    try:
        rows = wiki_search.search(
            query,
            limit=limit,
            user_id=user.id if user else None,
            is_admin=bool(user and user.is_admin),
        )
    except OperationalError as exc:
        return {
            "error": (
                f"Search backend rejected the query: {exc}. Try plain words "
                "separated by spaces, or quoted phrases."
            ),
        }

    if not rows:
        return {"results": [], "note": "no matches"}

    results = [
        {"path": r.path, "title": r.title, "snippet": r.snippet, "score": r.score}
        for r in rows
    ]
    return {"results": results}

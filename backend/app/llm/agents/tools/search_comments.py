"""Handler for the `search_comments` tool. Spec lives in `search_comments.json`.

Full-text search over wiki *comment* threads — discussion (decisions, feedback,
questions, @mentions), distinct from document content (`search_wiki`). Returns
``{doc_path, thread_root_id, snippet, link}`` per hit, where ``link`` deep-links
to the thread (the page handler focuses ``?comment=<thread_root_id>``).

Visibility mirrors `search_wiki`: the agent inherits the calling user's read
access via ``current_user()``, so chat only ever surfaces comments that user
can already see. This tool is chat-only; it is not part of the ingestion
candidate search (that path queries the document index, never comments).
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.auth import current_user
from app.db import comment_fts

DEFAULT_LIMIT = 10
MAX_LIMIT = 20


def _thread_link(doc_path: str, thread_root_id: str) -> str:
    """`/app/wiki/<encoded path>?comment=<root>` — the shipped deep-link route."""
    encoded = "/".join(quote(seg) for seg in doc_path.split("/") if seg)
    return f"/app/wiki/{encoded}?comment={thread_root_id}"


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

    user = current_user()
    hits = comment_fts.search(
        query,
        limit=limit,
        user_id=user.id if user else None,
        is_admin=bool(user and user.is_admin),
    )
    if not hits:
        return {"results": [], "note": "no matching comments"}

    return {
        "results": [
            {
                "doc_path": h.doc_path,
                "thread_root_id": h.thread_root_id,
                "snippet": h.snippet,
                "link": _thread_link(h.doc_path, h.thread_root_id),
            }
            for h in hits
        ]
    }

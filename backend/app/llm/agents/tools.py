"""Tools the chat agent can call.

Each tool exposes:
  * a `spec` dict in the normalized shape `app.llm.client.complete` accepts
    (`name`, `description`, `input_schema` JSON Schema), and
  * a function `(args: dict) -> Any` that takes the model's parsed arguments
    and returns a JSON-serializable result.

`run_chat_loop` looks tools up by name via `dispatch_chat_tool`. Tool errors
become `{"error": ...}` content fed back to the model so it can self-correct.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Callable

from app.wiki import git as wiki_git
from app.wiki import search as wiki_search

log = logging.getLogger(__name__)

WIKI_SEARCH_DEFAULT_LIMIT = 10
WIKI_SEARCH_MAX_LIMIT = 10


WIKI_SEARCH_SPEC: dict[str, Any] = {
    "name": "wiki_search",
    "description": (
        "Full-text bm25 search over the wiki. Use this whenever the user asks "
        "about anything that could plausibly live in a wiki document. Returns "
        "the top-ranked matches with each document's full markdown body, so a "
        "single call gives you everything you need to answer. Bodies can be "
        "long — read them carefully and cite the `path` of any doc you draw "
        "from. If nothing relevant comes back, say so rather than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query. Plain words separated by spaces are AND'd "
                    "together. Use \"double quotes\" for exact phrases. Avoid "
                    "punctuation like ? or : that the FTS5 parser may reject."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Max results (default {WIKI_SEARCH_DEFAULT_LIMIT}, max "
                    f"{WIKI_SEARCH_MAX_LIMIT}). Each result includes the full "
                    "doc body — keep this small."
                ),
                "minimum": 1,
                "maximum": WIKI_SEARCH_MAX_LIMIT,
            },
        },
        "required": ["query"],
    },
}


def wiki_search_tool(args: dict[str, Any]) -> Any:
    query = args.get("query")
    query = query.strip() if isinstance(query, str) else ""
    if not query:
        return {"error": "query is required"}

    raw_limit = args.get("limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else WIKI_SEARCH_DEFAULT_LIMIT
    except (TypeError, ValueError):
        limit = WIKI_SEARCH_DEFAULT_LIMIT
    limit = max(1, min(limit, WIKI_SEARCH_MAX_LIMIT))

    try:
        rows = wiki_search.search(query, limit=limit)
    except sqlite3.OperationalError as exc:
        return {
            "error": (
                f"FTS5 rejected the query: {exc}. Try plain words separated by "
                "spaces, or quoted phrases."
            ),
        }

    if not rows:
        return {"results": [], "note": "no matches"}

    results: list[dict[str, Any]] = []
    for r in rows:
        path = r["path"]
        try:
            body = wiki_git.read_file(path)
        except Exception as exc:
            # FTS row exists but git can't read the file — index is stale.
            # Surface the error per-result so the model can still use the rest.
            log.warning("wiki_search: failed to read %s: %s", path, exc)
            results.append(
                {"path": path, "title": r["title"], "error": "could not read file"}
            )
            continue
        results.append({"path": path, "title": r["title"], "body": body})

    return {"results": results}


CHAT_TOOL_SPECS: list[dict[str, Any]] = [WIKI_SEARCH_SPEC]

_DISPATCH: dict[str, Callable[[dict[str, Any]], Any]] = {
    WIKI_SEARCH_SPEC["name"]: wiki_search_tool,
}


def dispatch_chat_tool(name: str, args: dict[str, Any]) -> Any:
    fn = _DISPATCH.get(name)
    if fn is None:
        raise ValueError(f"unknown tool: {name}")
    return fn(args)

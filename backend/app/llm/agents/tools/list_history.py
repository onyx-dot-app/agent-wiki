"""Handler for the `list_history` tool. Spec lives in `list_history.json`.

Thin wrapper over ``wiki_git.history``. Drops the commit body from the
result (often noisy) and trims to the fields callers actually need.
"""
from __future__ import annotations

from typing import Any

from app.wiki import utils as wiki_utils
from app.wiki import git as wiki_git

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = wiki_utils.validate_doc_path(args.get("path"))
    except wiki_utils.ToolError as exc:
        return {"error": str(exc)}

    raw_limit = args.get("limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else DEFAULT_LIMIT
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    from app.auth import PermissionDenied, require_can

    try:
        require_can("read", rel)
    except PermissionDenied as exc:
        return {"error": str(exc)}

    rows = wiki_git.history(rel, limit=limit)
    if not rows:
        return {"path": rel, "history": [], "note": "no history"}
    return {
        "path": rel,
        "history": [
            {"sha": r.sha, "author": r.author, "ts": r.ts, "message": r.message}
            for r in rows
        ],
    }

"""Handler for the `find_user` tool. Spec lives in `find_user.json`.

Resolves a person to their user id so the agent can @mention them in a comment
or reply. The agent can't know a user's opaque id, so it searches by name or
email here, then embeds the canonical token `@[Display Name](mention:<user_id>)`
in the comment/reply body where the mention should appear.

Case-insensitive substring match on name/email (same lookup the share
typeahead uses). Returns only public-safe fields. Any signed-in user may
search — the chat request is already authenticated as a real user — so there's
no per-page ACL gate here (a user id isn't page-scoped).
"""
from __future__ import annotations

from typing import Any

from app.auth import users as users_repo

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 20


def handle(args: dict[str, Any]) -> Any:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"error": "query is required — a name or email to look up"}

    raw_limit = args.get("limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else _DEFAULT_LIMIT
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    rows = users_repo.search(query.strip(), limit)
    return {
        "users": [
            {"id": r["id"], "name": r.get("name"), "email": r["email"]} for r in rows
        ]
    }

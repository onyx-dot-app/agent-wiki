"""Handler for the `create_trigger` tool. Spec lives in `create_trigger.json`."""
from __future__ import annotations

from typing import Any

from app.auth import current_user
from app.triggers import repo as triggers_repo
from app.wiki import filesystem


def handle(args: dict[str, Any]) -> Any:
    user = current_user()
    if user is None:
        return {"error": "no authenticated user"}

    raw_scope = args.get("scope_path", "")
    nl = args.get("nl_description")
    message = args.get("message")
    destination = args.get("destination", None)

    if not isinstance(nl, str) or not nl.strip():
        return {"error": "nl_description is required"}
    if not isinstance(message, str) or not message.strip():
        return {"error": "message is required"}
    if destination not in triggers_repo.SUPPORTED_DESTINATIONS:
        return {
            "error": (
                f"destination {destination!r} not supported in v0 — only null "
                "(Event Log)"
            )
        }

    if not isinstance(raw_scope, str):
        return {"error": "scope_path must be a string"}
    scope = raw_scope.strip()
    if scope:
        try:
            scope = filesystem.safe_rel_path(scope)
        except ValueError as exc:
            return {"error": f"invalid scope_path: {exc}"}

    try:
        trigger = triggers_repo.create(
            owner_user_id=user.id,
            scope_path=scope,
            nl_description=nl.strip(),
            message=message.strip(),
            destination=destination,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"trigger": trigger}

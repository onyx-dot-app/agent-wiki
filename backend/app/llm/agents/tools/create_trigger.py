"""Handler for the `create_trigger` tool. Spec lives in `create_trigger.json`."""
from __future__ import annotations

from typing import Any

from app.auth import current_user
from app.triggers import repo as triggers_repo
from app.triggers import storage as triggers_storage
from app.wiki import acl as wiki_acl


def handle(args: dict[str, Any]) -> Any:
    user = current_user()
    if user is None:
        return {"error": "no authenticated user"}

    raw_scope = args.get("scope_path", "")
    nl = args.get("trigger_nl_condition")
    message = args.get("trigger_fire_message")
    destination = args.get("destination", triggers_repo.DEFAULT_DESTINATION)

    if not isinstance(nl, str) or not nl.strip():
        return {"error": "trigger_nl_condition is required"}
    if not isinstance(message, str) or not message.strip():
        return {"error": "trigger_fire_message is required"}

    if not isinstance(raw_scope, str):
        return {"error": "scope_path must be a string"}
    try:
        scope = triggers_storage.normalize_scope_path(raw_scope)
    except ValueError as exc:
        return {"error": f"invalid scope_path: {exc}"}

    if not wiki_acl.can(user.id, user.is_admin, "read", scope):
        return {"error": f"you do not have read access to scope_path {scope!r}"}

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

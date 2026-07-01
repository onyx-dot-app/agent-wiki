"""Handler for the `update_trigger` tool. Spec lives in `update_trigger.json`.

Partial updates: only the fields the model passes are changed. Ownership
is enforced — a user can only update triggers they own.
"""
from __future__ import annotations

from typing import Any

from app.auth import current_user
from app.wiki import utils as wiki_utils
from app.triggers import repo as triggers_repo
from app.triggers import storage as triggers_storage
from app.wiki import acl as wiki_acl

# Sentinel mirroring the one in triggers.repo so we can distinguish
# "destination omitted" from "destination explicitly set to null".
_UNSET = object()


def handle(args: dict[str, Any]) -> Any:
    user = current_user()
    if user is None:
        return {"error": "no authenticated user"}

    trigger_id = args.get("trigger_id")
    if not isinstance(trigger_id, str) or not trigger_id.strip():
        return {"error": "trigger_id is required"}

    existing = triggers_repo.get(trigger_id)
    if existing is None:
        return {"error": f"trigger not found: {trigger_id}"}
    if existing["owner_user_id"] != user.id:
        return {"error": "you do not own this trigger"}

    kwargs: dict[str, Any] = {}

    if "scope_path" in args:
        raw = args["scope_path"]
        if not isinstance(raw, str):
            return {"error": "scope_path must be a string"}
        try:
            scope = triggers_storage.normalize_scope_path(raw)
        except ValueError as exc:
            return {"error": f"invalid scope_path: {exc}"}
        kwargs["scope_path"] = scope

    if "trigger_nl_condition" in args:
        nl = args["trigger_nl_condition"]
        if not isinstance(nl, str) or not nl.strip():
            return {"error": "trigger_nl_condition cannot be empty"}
        kwargs["nl_description"] = nl.strip()

    if "trigger_fire_message" in args:
        msg = args["trigger_fire_message"]
        if not isinstance(msg, str) or not msg.strip():
            return {"error": "trigger_fire_message cannot be empty"}
        kwargs["message"] = msg.strip()

    if "enabled" in args:
        enabled = args["enabled"]
        if not isinstance(enabled, bool):
            return {"error": "enabled must be a boolean"}
        kwargs["enabled"] = enabled

    if not kwargs:
        return {"trigger": existing, "note": "no fields to update"}

    # Require read access against whichever scope ends up sticking — the new
    # one if rebinding, otherwise the existing one (in case ACLs were
    # revoked after the trigger was created).
    final_scope = kwargs.get("scope_path", existing["scope_path"])
    if not wiki_acl.can(user.id, user.is_admin, "read", final_scope):
        return {
            "error": f"you do not have read access to scope_path {final_scope!r}"
        }

    try:
        updated = triggers_repo.update(trigger_id, actor=wiki_utils.author_string(), **kwargs)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"trigger": updated}

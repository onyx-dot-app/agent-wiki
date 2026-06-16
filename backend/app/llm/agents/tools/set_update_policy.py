"""Handler for the `set_update_policy` tool. Spec lives in `set_update_policy.json`.

Lets an agent set the per-page / per-folder update policy — whether
connector/ingestion auto-update is disabled, and the free-text `update_instruction`
the updater honors. PATCH semantics mirror `PATCH /api/update-policy`: only the
fields present in the call change; an empty `update_instruction` (or an explicit
null) clears it so the scope inherits from an ancestor again.

Gated by `require_can("write", path)` against the calling user (`current_user()`,
the principal the chat/MCP request authenticated as) — the same permission as
editing the page/folder. Page paths must already exist; folder (and root) paths
may be set ahead of their children, which then inherit the policy.
"""
from __future__ import annotations

from typing import Any

from app.auth import PermissionDenied, current_user, require_can
from app.wiki import update_policy, utils as wiki_utils


def handle(args: dict[str, Any]) -> Any:
    raw_path = args.get("path")
    if not isinstance(raw_path, str):
        return {"error": 'path is required (a page `.md`, a folder, or "" for the wiki root)'}
    try:
        norm = update_policy.normalize_path(raw_path)
    except ValueError as exc:
        return {"error": str(exc)}

    kind = update_policy.kind_for_path(norm)
    # A page policy must target an existing page; folder/root policies may be set
    # ahead of their children (future pages inherit them).
    if kind == "page" and not wiki_utils.file_exists(norm):
        return {"error": f"file not found: {norm}"}

    # PATCH: only change settings the caller actually provided. Key present =
    # intent to set; an empty string (or null) for the instruction clears it.
    patch: dict[str, Any] = {}
    if "ingestion_auto_update_disabled" in args:
        v = args["ingestion_auto_update_disabled"]
        if v is not None and not isinstance(v, bool):
            return {"error": "ingestion_auto_update_disabled must be a boolean"}
        patch["ingestion_auto_update_disabled"] = v
    if "update_instruction" in args:
        v = args["update_instruction"]
        if v is not None and not isinstance(v, str):
            return {"error": "update_instruction must be a string"}
        patch["update_instruction"] = v
    if not patch:
        return {
            "error": "provide at least one of `ingestion_auto_update_disabled` "
            "or `update_instruction` to change"
        }

    try:
        require_can("write", norm)
    except PermissionDenied as exc:
        return {"error": str(exc)}

    user = current_user()
    update_policy.set_policy(norm, actor_user_id=user.id if user else None, **patch)

    explicit_row = update_policy.get(norm)
    explicit = (
        {
            "ingestion_auto_update_disabled": explicit_row["ingestion_auto_update_disabled"],
            "update_instruction": explicit_row["update_instruction"],
        }
        if explicit_row is not None
        else None
    )
    effective = update_policy.resolve_for_path(norm)
    return {
        "path": norm,
        "kind": kind,
        # This scope's own row (None once it carries no settings and inherits fully).
        "explicit": explicit,
        # What actually applies here after folder→page inheritance.
        "effective": {
            "ingestion_auto_update_disabled": effective.ingestion_auto_update_disabled,
            "update_instruction": effective.update_instruction,
        },
    }

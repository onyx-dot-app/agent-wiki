"""Handler for the `set_update_policy` tool. Spec lives in `set_update_policy.json`.

Lets an agent set the per-page / per-folder update policy — whether
connector/ingestion auto-update is disabled, the free-text `update_instruction`
the updater honors, and whether AI auto-management is allowed for the scope
(`ai_management_allowed`). PATCH semantics mirror `PATCH /api/update-policy`: only the
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

    # Permission first — checking page existence before auth would let a caller
    # without access distinguish "file not found" from "forbidden" and so
    # enumerate private pages. Only reveal existence once write is confirmed.
    try:
        require_can("write", norm)
    except PermissionDenied as exc:
        return {"error": str(exc)}

    # A page policy must target an existing page; folder/root policies may be set
    # ahead of their children (future pages inherit them).
    if kind == "page" and not wiki_utils.file_exists(norm):
        return {"error": f"file not found: {norm}"}

    # PATCH: only change settings the caller actually provided. Key present =
    # intent to set; an empty string (or null) for the instruction clears it.
    patch: dict[str, Any] = {}
    if "ingestion_auto_update_disabled" in args:
        disabled = args["ingestion_auto_update_disabled"]
        if disabled is not None and not isinstance(disabled, bool):
            return {"error": "ingestion_auto_update_disabled must be a boolean"}
        patch["ingestion_auto_update_disabled"] = disabled
    if "update_instruction" in args:
        instruction = args["update_instruction"]
        if instruction is not None and not isinstance(instruction, str):
            return {"error": "update_instruction must be a string"}
        patch["update_instruction"] = instruction
    if "ai_management_allowed" in args:
        ai_allowed = args["ai_management_allowed"]
        if ai_allowed is not None and not isinstance(ai_allowed, bool):
            return {"error": "ai_management_allowed must be a boolean"}
        patch["ai_management_allowed"] = ai_allowed
    if not patch:
        return {
            "error": "provide at least one of `ingestion_auto_update_disabled`, "
            "`update_instruction`, or `ai_management_allowed` to change"
        }

    user = current_user()
    # Use set_policy's returned row directly — re-fetching would add a query and
    # open a TOCTOU window where a concurrent write disagrees with what we wrote.
    explicit_row = update_policy.set_policy(
        norm, actor_user_id=user.id if user else None, **patch
    )
    explicit = (
        {
            "ingestion_auto_update_disabled": explicit_row["ingestion_auto_update_disabled"],
            "update_instruction": explicit_row["update_instruction"],
            "ai_management_allowed": explicit_row["ai_management_allowed"],
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
            "ai_management_allowed": effective.ai_management_allowed,
        },
    }

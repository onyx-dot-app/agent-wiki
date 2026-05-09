"""Handler for the `write_doc` tool. Spec lives in `write_doc.json`.

Full-body overwrite (or create). Use sparingly — prefer ``edit_doc`` for
surgical changes.
"""
from __future__ import annotations

from typing import Any

from app.llm.agents.tools import _doc_helpers as h


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
        body = args.get("body")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        if not isinstance(body, str):
            raise h.ToolError("body is required (string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise h.ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise h.ToolError("base_sha must be a string when provided")

        existed = h.file_exists(rel)
        if existed:
            # Order matters: read-before-write is the primary signal —
            # if the agent didn't even see the doc, there's no point
            # asking for the sha it would have remembered. base_sha is
            # the secondary check, enforced because full-body overwrite
            # has no fuzzy `old_string` chain to fall back on if HEAD
            # drifted out from under the agent.
            h.assert_read_before_write(rel)
            if base_sha is None:
                return {
                    "error": "base_sha_required_for_overwrite",
                    "message": (
                        "write_doc on an existing file requires base_sha "
                        "(the sha you last read). Re-read the doc and "
                        "pass its sha as base_sha."
                    ),
                }
            stale = h.assert_base_sha(rel, base_sha)
            if stale is not None:
                return stale
            old = h.read_existing(rel)
        else:
            old = ""

        change_kind = "edit" if existed else "create"
        sha = h.commit_and_fan_out(rel, body, commit_message.strip(), change_kind=change_kind)

        return {
            "path": rel,
            "sha": sha,
            "created": not existed,
            "diff": h.unified_diff(old, body, rel),
            "broken_links": h.broken_links(rel, body),
        }
    except h.ToolError as exc:
        return {"error": str(exc)}

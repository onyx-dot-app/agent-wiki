"""Handler for the `multi_edit` tool. Spec lives in `multi_edit.json`.

Atomic batch edits: all replaces apply to the running body in memory, and
only on full success do we commit. Any failure aborts with no write.
"""
from __future__ import annotations

from typing import Any, cast

from app.llm.agents.tools import _doc_helpers as h
from app.wiki import edit as wiki_edit


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
        edits_raw = args.get("edits")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        if not isinstance(edits_raw, list) or not edits_raw:
            raise h.ToolError("edits must be a non-empty array")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise h.ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise h.ToolError("base_sha must be a string when provided")
        edits = cast(list[Any], edits_raw)

        if not h.file_exists(rel):
            raise h.ToolError(f"file not found: {rel}")
        h.assert_read_before_write(rel)

        stale = h.assert_base_sha(rel, base_sha)
        if stale is not None:
            return stale

        old_body = h.read_existing(rel)
        body = old_body
        for i, edit in enumerate(edits):
            if not isinstance(edit, dict):
                raise h.ToolError(f"edit #{i + 1}: must be an object")
            edit_dict = cast(dict[str, Any], edit)
            old_string = edit_dict.get("old_string")
            new_string = edit_dict.get("new_string")
            if not isinstance(old_string, str) or old_string == "":
                raise h.ToolError(f"edit #{i + 1}: old_string is required and non-empty")
            if not isinstance(new_string, str):
                raise h.ToolError(f"edit #{i + 1}: new_string is required (string)")
            replace_all = bool(edit_dict.get("replace_all", False))
            try:
                body = wiki_edit.replace(body, old_string, new_string, replace_all)
            except wiki_edit.ReplaceError as exc:
                raise h.ToolError(f"edit #{i + 1}: {exc}")

        if body == old_body:
            raise h.ToolError("edits produced no change")

        sha = h.commit_and_fan_out(rel, body, commit_message.strip(), change_kind="edit")

        return {
            "path": rel,
            "sha": sha,
            "applied_count": len(edits),
            "diff": h.unified_diff(old_body, body, rel),
            "broken_links": h.broken_links(rel, body),
        }
    except h.ToolError as exc:
        return {"error": str(exc)}

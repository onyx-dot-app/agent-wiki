"""Handler for the `edit_doc` tool. Spec lives in `edit_doc.json`.

Surgical find-and-replace using the fuzzy chain in ``app.wiki.edit``.
"""
from __future__ import annotations

from typing import Any

from app.llm.agents.tools import _doc_helpers as h
from app.wiki import edit as wiki_edit


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        activity_ttl = h.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(old_string, str) or old_string == "":
            raise h.ToolError("old_string is required and must be non-empty")
        if not isinstance(new_string, str):
            raise h.ToolError("new_string is required (string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise h.ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise h.ToolError("base_sha must be a string when provided")
        replace_all = bool(args.get("replace_all", False))

        if not h.file_exists(rel):
            raise h.ToolError(f"file not found: {rel}")

        base_body = h.read_existing(rel)
        try:
            new_body = wiki_edit.replace(base_body, old_string, new_string, replace_all)
        except wiki_edit.ReplaceError as exc:
            stale = h.assert_base_sha(rel, base_sha)
            if stale is not None:
                return stale
            raise h.ToolError(str(exc))

        try:
            result = h.commit_with_ai_rebase(
                rel, commit_message.strip(),
                change_kind="edit",
                base_body=base_body,
                new_body=new_body,
                activity_ttl=activity_ttl,
            )
        except h.AiRebaseMaxRetriesError as exc:
            return {
                "error": "stale_base",
                "message": "concurrent edits kept landing; max retries exceeded",
                "current_sha": exc.current_sha,
            }

        if result is None:
            raise h.ToolError("edit produced no change")

        return {
            "path": rel,
            "sha": result.sha,
            "diff": h.unified_diff(result.old_body, result.new_body, rel),
            "broken_links": h.broken_links(rel, result.new_body),
        }
    except h.ToolError as exc:
        return {"error": str(exc)}

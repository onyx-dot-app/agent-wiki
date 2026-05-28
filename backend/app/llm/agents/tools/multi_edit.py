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
        activity_ttl = h.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(edits_raw, list) or not edits_raw:
            raise h.ToolError("edits must be a non-empty array")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise h.ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise h.ToolError("base_sha must be a string when provided")
        edits = cast(list[Any], edits_raw)

        # Validate edit shapes up front before touching disk.
        parsed: list[tuple[str, str, bool]] = []
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
            parsed.append((old_string, new_string, bool(edit_dict.get("replace_all", False))))

        if not h.file_exists(rel):
            raise h.ToolError(f"file not found: {rel}")

        base_body = h.read_existing(rel)
        new_body = base_body
        try:
            for i, (old_string, new_string, replace_all) in enumerate(parsed):
                try:
                    new_body = wiki_edit.replace(new_body, old_string, new_string, replace_all)
                except wiki_edit.ReplaceError as exc:
                    raise wiki_edit.ReplaceError(f"edit #{i + 1}: {exc}") from exc
        except wiki_edit.ReplaceError as exc:
            stale = h.assert_base_sha(rel, base_sha)
            if stale is not None:
                return stale
            raise h.ToolError(str(exc))

        if new_body == base_body:
            raise h.ToolError("edits produced no change")

        try:
            result = h.commit_with_ai_rebase(
                rel, commit_message.strip(),
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
            raise h.ToolError("edits produced no change")

        return {
            "path": rel,
            "sha": result.sha,
            "applied_count": len(parsed),
            "diff": h.unified_diff(result.old_body, result.new_body, rel),
            "broken_links": h.broken_links(rel, result.new_body),
        }
    except h.ToolError as exc:
        return {"error": str(exc)}

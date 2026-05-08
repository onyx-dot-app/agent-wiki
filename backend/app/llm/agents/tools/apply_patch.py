"""Handler for the `apply_patch` tool. Spec lives in `apply_patch.json`.

Line-anchored unified-diff editor. Calls into ``app.wiki.patch.apply``
which does the parsing, line-anchored match, and fuzzy fallback. This
handler is the tool-side adapter: argument validation, read-before-write
enforcement, optional ``base_sha`` staleness check, commit + fan-out.
"""
from __future__ import annotations

from typing import Any

from app.llm.agents.tools import _doc_helpers as h
from app.wiki import git as wiki_git
from app.wiki import patch as wiki_patch


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
        patch = args.get("patch")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        if not isinstance(patch, str) or not patch.strip():
            raise h.ToolError("patch is required (non-empty string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise h.ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise h.ToolError("base_sha must be a string when provided")

        if not h.file_exists(rel):
            raise h.ToolError(f"file not found: {rel}")
        h.assert_read_before_write(rel)

        head_sha = wiki_git.head_sha_for_path(rel)
        if base_sha and base_sha != head_sha:
            return {
                "error": "stale_base",
                "base_sha": base_sha,
                "current_sha": head_sha,
                "message": (
                    "the file has changed since base_sha; re-read with "
                    "read_doc and rebase your patch"
                ),
            }

        old_body = h.read_existing(rel)
        try:
            new_body = wiki_patch.apply(old_body, patch)
        except wiki_patch.PatchError as exc:
            raise h.ToolError(str(exc))

        if new_body == old_body:
            raise h.ToolError(
                "patch produced no change (every hunk was a no-op)"
            )

        sha = h.commit_and_fan_out(rel, new_body, commit_message.strip(), change_kind="edit")

        return {
            "path": rel,
            "sha": sha,
            "diff": h.unified_diff(old_body, new_body, rel),
            "broken_links": h.broken_links(rel, new_body),
        }
    except h.ToolError as exc:
        return {"error": str(exc)}

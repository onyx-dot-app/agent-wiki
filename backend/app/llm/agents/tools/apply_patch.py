"""Handler for the `apply_patch` tool. Spec lives in `apply_patch.json`.

Line-anchored unified-diff editor. Calls into ``app.wiki.patch.apply``
which does the parsing, line-anchored match, and fuzzy fallback. This
handler is the tool-side adapter: argument validation, optional
``base_sha`` staleness check, commit + fan-out.
"""
from __future__ import annotations

from typing import Any

from app.wiki import utils as wiki_utils
from app.llm.agents.tools.errors import ToolError
from app.wiki import patch as wiki_patch
from app.models.wiki import ChangeKind


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
        patch = args.get("patch")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        activity_ttl = wiki_utils.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(patch, str) or not patch.strip():
            raise ToolError("patch is required (non-empty string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise ToolError("base_sha must be a string when provided")

        if not wiki_utils.file_exists(path):
            raise ToolError(f"file not found: {path}")

        stale = wiki_utils.assert_base_sha(path, base_sha)
        if stale is not None:
            return stale

        old_body = wiki_utils.read_existing(path)
        try:
            new_body = wiki_patch.apply(old_body, patch)
        except wiki_patch.PatchError as exc:
            raise ToolError(str(exc))

        if new_body == old_body:
            raise ToolError(
                "patch produced no change (every hunk was a no-op)"
            )

        # Intentionally no commit_with_ai_merge here: unified diff hunks are
        # line-anchored, so a 3-way merge after a concurrent edit would produce
        # wrong offsets. Callers should pass base_sha and retry on stale_base.
        sha = wiki_utils.commit_and_fan_out(
            path, new_body, commit_message.strip(),
            change_kind=ChangeKind.EDIT, activity_ttl=activity_ttl,
        )

        return {
            "path": path,
            "sha": sha,
            "diff": wiki_utils.unified_diff(old_body, new_body, path),
            "broken_links": wiki_utils.broken_links(path, new_body),
        }
    except ToolError as exc:
        return {"error": str(exc)}

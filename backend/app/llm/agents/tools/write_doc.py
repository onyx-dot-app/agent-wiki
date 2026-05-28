"""Handler for the `write_doc` tool. Spec lives in `write_doc.json`.

Full-body overwrite (or create). Use sparingly — prefer ``edit_doc`` for
surgical changes.
"""
from __future__ import annotations

from typing import Any

from app.wiki import utils as wiki_utils
from app.wiki import git as wiki_git
from app.models.wiki import ChangeKind


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = wiki_utils.validate_doc_path(args.get("path"))
        body = args.get("body")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        activity_ttl = wiki_utils.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(body, str):
            raise wiki_utils.ToolError("body is required (string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise wiki_utils.ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise wiki_utils.ToolError("base_sha must be a string when provided")

        existed = wiki_utils.file_exists(rel)
        if existed:
            # Full-body overwrite requires base_sha so we can 3-way merge
            # if a concurrent commit landed between when the agent read the
            # doc and when it calls write_doc.
            if base_sha is None:
                return {
                    "error": "base_sha_required_for_overwrite",
                    "message": (
                        "write_doc on an existing file requires base_sha "
                        "(the sha you last read). Re-read the doc and "
                        "pass its sha as base_sha."
                    ),
                }
            base_body = wiki_git.read_file(rel, ref=base_sha)
            try:
                result = wiki_utils.commit_with_ai_rebase(
                    rel, commit_message.strip(),
                    base_body=base_body,
                    new_body=body,
                    activity_ttl=activity_ttl,
                )  # always ChangeKind.EDIT — new files take the else branch below
            except wiki_utils.AiRebaseMaxRetriesException as exc:
                return {
                    "error": "stale_base",
                    "message": "concurrent edits kept landing; max retries exceeded",
                    "current_sha": exc.current_sha,
                }
            if result is None:
                return {"path": rel, "sha": wiki_git.head_sha_for_path(rel), "no_change": True}
            return {
                "path": rel,
                "sha": result.sha,
                "created": False,
                "diff": wiki_utils.unified_diff(result.old_body, result.new_body, rel),
                "broken_links": wiki_utils.broken_links(rel, result.new_body),
            }
        else:
            sha = wiki_utils.commit_and_fan_out(
                rel, body, commit_message.strip(),
                change_kind=ChangeKind.CREATE, activity_ttl=activity_ttl,
            )
            return {
                "path": rel,
                "sha": sha,
                "created": True,
                "diff": wiki_utils.unified_diff("", body, rel),
                "broken_links": wiki_utils.broken_links(rel, body),
            }
    except wiki_utils.ToolError as exc:
        return {"error": str(exc)}

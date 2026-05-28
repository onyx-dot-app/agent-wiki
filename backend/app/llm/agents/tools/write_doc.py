"""Handler for the `write_doc` tool. Spec lives in `write_doc.json`.

Full-body overwrite (or create). Use sparingly — prefer ``edit_doc`` for
surgical changes.
"""
from __future__ import annotations

from typing import Any

from app.llm.agents.tools import _doc_helpers as h
from app.wiki import git as wiki_git


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
        body = args.get("body")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        activity_ttl = h.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(body, str):
            raise h.ToolError("body is required (string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise h.ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise h.ToolError("base_sha must be a string when provided")

        existed = h.file_exists(rel)
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
            # generate_body merges the agent's rewrite with whatever is
            # currently on HEAD. When HEAD == base_sha (no concurrent
            # change) merge_content trivially returns ``body`` unchanged.
            base_body = wiki_git.read_file(rel, ref=base_sha)

            def generate_body(current: str) -> str | None:
                mr = wiki_git.merge_content(base_body, current, body)
                if mr.clean:
                    return mr.merged
                from app.llm.agents import merge_conflict_update
                return merge_conflict_update.merge(
                    wiki_path=rel,
                    base_body=base_body,
                    current_body=current,
                    draft_body=body,
                )

            change_kind = "edit"
        else:
            generate_body = lambda current: body
            change_kind = "create"

        try:
            result = h.commit_with_retry(
                rel, commit_message.strip(),
                change_kind=change_kind,
                generate_body=generate_body,
                activity_ttl=activity_ttl,
            )
        except h.MaxRetriesError as exc:
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
            "created": not existed,
            "diff": h.unified_diff(result.old_body, result.new_body, rel),
            "broken_links": h.broken_links(rel, result.new_body),
        }
    except h.ToolError as exc:
        return {"error": str(exc)}

"""Handler for the `read_doc` tool. Spec lives in `read_doc.json`.

The MCP-aware read tool. Differs from ``read_page`` only in that it
accepts an optional ``sha`` for historical reads.

Read-before-write semantics: a HEAD read marks the path as "seen" so
write tools accept edits to it. A historical read does NOT — the agent
saw the old body, not the current one.
"""
from __future__ import annotations

import subprocess
from typing import Any

from app.llm.agents._session import seen_doc_paths
from app.llm.agents.tools import _doc_helpers as h
from app.wiki import git as wiki_git


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
    except h.ToolError as exc:
        return {"error": str(exc)}

    raw_sha = args.get("sha")
    if raw_sha is not None and not isinstance(raw_sha, str):
        return {"error": "sha must be a string"}
    sha = raw_sha.strip() if isinstance(raw_sha, str) and raw_sha.strip() else None

    head_sha = wiki_git.head_sha_for_path(rel)
    if sha is None and not h.file_exists(rel):
        return {"error": f"file not found: {rel}"}

    ref = sha or "HEAD"
    try:
        body = wiki_git.read_file(rel, ref=ref)
    except subprocess.CalledProcessError:
        return {
            "error": (
                f"sha_not_found: {rel} not present at {ref}"
                if sha
                else f"could not read {rel}"
            )
        }
    except Exception as exc:  # pragma: no cover — git wrapper is well-defined
        return {"error": f"could not read {rel}@{ref}: {exc}"}

    is_head = sha is None or sha == head_sha
    if is_head:
        _mark_seen(rel)
        h.mark_doc_read(rel)
        # Frontmatter may have just been re-rendered; re-read so the model
        # sees the current body. Historical reads (sha != HEAD) don't
        # re-register and are returned as-was.
        try:
            body = wiki_git.read_file(rel, ref="HEAD")
        except Exception:  # pragma: no cover
            pass

    return {
        "path": rel,
        "body": body,
        "sha": sha or head_sha,
        "is_head": is_head,
    }


def _mark_seen(rel: str) -> None:
    """Self-register as a read so write tools accept edits to ``rel``.

    Mirrors what the chat loop does for ``read_page`` via
    ``_record_seen_paths``. Doing it inline here keeps ``read_doc`` usable
    from contexts that don't go through ``run_chat_loop`` (the MCP server,
    direct dispatch in tests).
    """
    seen = seen_doc_paths.get()
    if seen is None:
        return
    seen.add(rel)

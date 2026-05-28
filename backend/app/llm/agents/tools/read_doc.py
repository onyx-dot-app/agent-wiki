"""Handler for the `read_doc` tool. Spec lives in `read_doc.json`.

The MCP-aware read tool. Differs from ``read_page`` only in that it
accepts an optional ``sha`` for historical reads.
"""
from __future__ import annotations

import subprocess
from typing import Any

from app.wiki import utils as wiki_utils
from app.wiki import agent_activity, git as wiki_git


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = wiki_utils.validate_doc_path(args.get("path"))
    except wiki_utils.ToolError as exc:
        return {"error": str(exc)}

    raw_sha = args.get("sha")
    if raw_sha is not None and not isinstance(raw_sha, str):
        return {"error": "sha must be a string"}
    sha = raw_sha.strip() if isinstance(raw_sha, str) and raw_sha.strip() else None

    head_sha = wiki_git.head_sha_for_path(rel)
    if sha is None and not wiki_utils.file_exists(rel):
        return {"error": f"file not found: {rel}"}

    from app.auth import PermissionDenied, require_can

    try:
        require_can("read", rel)
    except PermissionDenied as exc:
        return {"error": str(exc)}

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
    agents: list[dict[str, Any]] = []
    if is_head:
        wiki_utils.mark_doc_read(rel)
        agents = [r.model_dump() for r in agent_activity.list_for_doc(rel)]

    return {
        "path": rel,
        "body": body,
        "sha": sha or head_sha,
        "is_head": is_head,
        "agents": agents,
    }

"""Handler for the `read_page` tool. Spec lives in `read_page.json`.

Returns the full body of a single wiki doc plus the active agent
activity rows for that path. Registers a ``read`` activity in the
registry so co-occupant agents see each other.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.wiki import utils as wiki_utils
from app.wiki import agent_activity, git as wiki_git


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = wiki_utils.validate_doc_path(args.get("path"))
    except wiki_utils.ToolError as exc:
        return {"error": str(exc)}

    if not wiki_utils.file_exists(rel):
        return {"error": f"file not found: {rel}"}

    from app.auth import PermissionDenied, require_can

    try:
        require_can("read", rel)
    except PermissionDenied as exc:
        return {"error": str(exc)}

    try:
        body = wiki_git.read_file(rel)
    except Exception as exc:
        return {"error": f"could not read {rel}: {exc}"}

    wiki_utils.mark_doc_read(rel)

    return {
        "path": rel,
        "title": _derive_title(rel, body),
        "body": body,
        "agents": [r.model_dump() for r in agent_activity.list_for_doc(rel)],
    }


def _derive_title(path: str, body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return Path(path).stem

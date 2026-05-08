"""Handler for the `read_page` tool. Spec lives in `read_page.json`.

Returns the full body of a single wiki doc and registers the path as
"read" in the chat session (via the loop's ``_record_seen_paths`` hook,
which keys off the ``path`` field in this tool's result).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.llm.agents.tools import _doc_helpers as h
from app.wiki import git as wiki_git


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
    except h.ToolError as exc:
        return {"error": str(exc)}

    if not h.file_exists(rel):
        return {"error": f"file not found: {rel}"}

    try:
        body = wiki_git.read_file(rel)
    except Exception as exc:
        return {"error": f"could not read {rel}: {exc}"}

    h.mark_doc_read(rel)
    # The frontmatter just got re-rendered — re-read so the model sees what
    # the file now contains on disk.
    try:
        body = wiki_git.read_file(rel)
    except Exception:  # pragma: no cover — already read once above
        pass

    return {
        "path": rel,
        "title": _derive_title(rel, body),
        "body": body,
    }


def _derive_title(path: str, body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return Path(path).stem

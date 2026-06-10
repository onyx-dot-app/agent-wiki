"""Shared link builders for the comment tools.

Underscore-prefixed and paired with no `.json`, so the tool registry's loader
(which imports `<name>.py` for each `<name>.json`) never treats it as a tool.
"""
from __future__ import annotations

from urllib.parse import quote


def thread_link(doc_path: str, thread_root_id: str) -> str:
    """`/app/wiki/<encoded path>?comment=<root>` — the shipped deep-link route
    that opens a page focused on a comment thread. Each path segment and the id
    are url-encoded."""
    encoded = "/".join(quote(seg) for seg in doc_path.split("/") if seg)
    return f"/app/wiki/{encoded}?comment={quote(thread_root_id, safe='')}"

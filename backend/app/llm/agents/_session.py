"""Per-chat-loop state shared with tool handlers.

Tools that need to know "what has the model seen this conversation?" read
from these context vars. The chat loop in ``chat.py`` owns the lifecycle:
``set()`` on entry, ``reset()`` on exit. Tool handlers MUST tolerate the
default (``None``) so they remain usable outside a loop (tests, direct
invocation).
"""
from __future__ import annotations

from contextvars import ContextVar

# Wiki paths the model has read this turn (today: via ``search_wiki``
# results). Doc-edit tools refuse to write to existing paths not in this
# set, mirroring opencode's read-before-write enforcement.
seen_doc_paths: ContextVar[set[str] | None] = ContextVar(
    "seen_doc_paths", default=None
)

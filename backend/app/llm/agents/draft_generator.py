"""One-shot draft generator backing the home "Start writing with AI" input.

Turns a free-text prompt into a complete Markdown draft plus a title, for the
user to review and create. Non-conversational: a single ``complete`` call, no
tools, no chat loop. Tests patch ``app.llm.client.complete``.
"""

from __future__ import annotations

import logging
import re

from app.llm import client
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

_MAX_TOKENS = 4000
_TITLE_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def generate(prompt: str, *, model: str | None = None) -> dict[str, str]:
    """Generate a draft for ``prompt``. Returns ``{title, body}``."""
    messages = [
        {"role": "system", "content": load_prompt("draft_generator.system")},
        {"role": "user", "content": prompt},
    ]
    with trace_flow("agent.draft_generator", prompt=prompt):
        result = client.complete(messages, model=model, max_tokens=_MAX_TOKENS)
    body = result.text.strip()
    return {"title": _title_from(body), "body": body}


def _title_from(body: str) -> str:
    """The first ``# `` heading, else the first non-empty line, else fallback."""
    match = _TITLE_RE.search(body)
    if match:
        return match.group(1).strip()
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return "Untitled"

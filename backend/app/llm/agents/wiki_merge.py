"""3-way wiki merge agent — reconciles a user draft with a concurrent HEAD edit.

Single ``complete()`` call. The system prompt instructs the model to produce
the merged markdown body only — no preamble, no fenced wrapper.
"""
from __future__ import annotations

import logging

from app.llm import client
from app.llm.agents.common import strip_outer_fence
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)


def merge(
    wiki_path: str,
    base_body: str,
    current_body: str,
    draft_body: str,
) -> str:
    """Return the merged markdown body.

    ``base_body``    — content at the common ancestor SHA
    ``current_body`` — current HEAD content
    ``draft_body``   — user's in-progress draft
    """
    system = load_prompt("wiki_merge.system")
    user_msg = (
        f"## Base\n\n{base_body}\n\n"
        f"## Current\n\n{current_body}\n\n"
        f"## Draft\n\n{draft_body}"
    )
    with trace_flow("agent.wiki_merge", wiki_path=wiki_path):
        result = client.complete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        )
    return strip_outer_fence(result.text.strip())

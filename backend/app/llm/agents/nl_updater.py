"""NL instruction updater agent — applies a natural-language instruction to a wiki page.

Single-shot LLM call. The system prompt constrains the output to either
the literal token ``NO_CHANGE`` or the full new page body in markdown —
no preamble, no fenced block.
"""
from __future__ import annotations

import logging
from typing import Any

from app.llm import client
from app.llm.agents.common import NO_CHANGE_SENTINEL, strip_outer_fence
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)


def process_instruction(wiki_path: str, current_body: str, payload: dict[str, Any], source: str) -> str | None:
    """Apply a natural-language instruction to a wiki page.

    Decides whether the instruction warrants a change and, if so, returns the
    full new page body. Returns ``None`` if no change is needed.

    Caller (a background task or the ``update_doc_nl`` tool) is responsible for
    committing the result. This function does no I/O beyond the LLM call.
    """
    system = load_prompt("wiki_updater.mcp.system")
    input = load_prompt("wiki_updater.mcp.input").format(
        wiki_path=wiki_path,
        source=source,
        current_body=current_body,
        payload=payload,
    )
    with trace_flow("agent.nl_updater", wiki_path=wiki_path, source=source):
        result = client.complete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": input},
            ],
        )
    text = result.text.strip()
    if not text:
        log.warning("nl_updater returned empty text for %s", wiki_path)
        return None
    if text == NO_CHANGE_SENTINEL or text.startswith(NO_CHANGE_SENTINEL + "\n"):
        return None
    return strip_outer_fence(text)

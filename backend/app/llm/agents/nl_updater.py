"""NL instruction updater agent — applies a natural-language instruction to a wiki page.

Single-shot LLM call. The system prompt constrains the output to either
the literal token ``NO_CHANGE`` or the full new page body in markdown —
no preamble, no fenced block.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import OperationalError

from app.llm import client
from app.llm.agents.common import NO_CHANGE_SENTINEL, strip_outer_fence, today_str
from app.llm.prompts import load_prompt
from app.tracing import trace_flow
from app.wiki import update_policy

log = logging.getLogger(__name__)


def process_instruction(wiki_path: str, current_body: str, payload: dict[str, Any], source: str) -> str | None:
    """Apply a natural-language instruction to a wiki page.

    Decides whether the instruction warrants a change and, if so, returns the
    full new page body. Returns ``None`` if no change is needed.

    Caller (a background task or the ``update_doc_nl`` tool) is responsible for
    committing the result. This function does a best-effort policy lookup (one DB
    read) and one LLM call; it does no other I/O.
    """
    # Per-page update instruction is advisory — if the policy store is
    # unreachable (e.g. the offline eval harness with no DB) proceed without it
    # rather than failing the update.
    try:
        instruction = update_policy.resolve_for_path(wiki_path).update_instruction
    except OperationalError:
        # DB unreachable (e.g. the offline eval harness with no DB). The
        # instruction is advisory, so proceed without it rather than fail the
        # update. Other exceptions are real bugs and propagate.
        log.warning(
            "nl_updater: update policy DB unreachable for %s; proceeding without it",
            wiki_path,
            exc_info=True,
        )
        instruction = None
    instruction_section = (
        "--- Update instruction for this page ---\n"
        f"{instruction}\n"
        "--- End instruction ---\n\n"
        if instruction
        else ""
    )
    system = load_prompt("wiki_updater.mcp.system")
    input = load_prompt("wiki_updater.mcp.input").format(
        wiki_path=wiki_path,
        source=source,
        today=today_str(),
        current_body=current_body,
        payload=payload,
        update_instruction=instruction_section,
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

"""The agent that reconciles a doc with new info from a connector update.

Single-shot LLM call. The system prompt
(``app/llm/prompts/document_updater.system.md``) constrains the output to
either the literal token ``NO_CHANGE`` or the full new doc body in
markdown — no preamble, no fenced block.

Open questions tracked in
``local_data/wiki/agents/document-updater.md``:
  * Cost — every connector update fans out to this agent. v0 accepts it;
    later: dedupe / debounce / batch by doc.
  * Bloat resistance — encoded in the system prompt's hard rules.
"""
from __future__ import annotations

import logging
from typing import Any

from app.llm import client
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

NO_CHANGE_SENTINEL = "NO_CHANGE"


def run(doc_id: str, current_body: str, payload: dict[str, Any], source: str) -> str | None:
    """Return a new doc body, or ``None`` if no update is warranted.

    Caller (a background task or the ``update_doc_nl`` tool) is responsible for
    committing the new body. This function does no I/O beyond the LLM
    call.
    """
    system = load_prompt("document_updater.system")
    user = load_prompt("document_updater.user").format(
        doc_id=doc_id,
        source=source,
        current_body=current_body,
        payload=payload,
    )
    with trace_flow("agent.document_updater", doc_id=doc_id, source=source):
        result = client.complete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    text = result.text.strip()
    if not text:
        log.warning("document_updater returned empty text for %s", doc_id)
        return None
    if text == NO_CHANGE_SENTINEL or text.startswith(NO_CHANGE_SENTINEL + "\n"):
        return None
    # Defensive: strip a single leading/trailing markdown fence if the
    # model added one despite the prompt. Don't strip nested fences —
    # those are part of the body.
    return _strip_outer_fence(text)


def _strip_outer_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    first_nl = text.find("\n")
    if first_nl == -1:
        return text
    if not text.rstrip().endswith("```"):
        return text
    inner = text[first_nl + 1 :].rstrip()
    if inner.endswith("```"):
        inner = inner[: -3].rstrip()
    return inner + "\n" if text.endswith("\n") else inner

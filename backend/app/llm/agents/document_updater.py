"""The agent that reconciles a doc with new info from a connector update.

Open questions (track in docs/architecture.md):
  * v0 just runs a single LLM pass over the full doc. We need to avoid
    bloating docs over time AND avoid throwing out important context.
  * Watch the cost — every connector update fans out to this agent.
  * Later: dedupe related updates, batch by doc, defer with a debounce window.
"""
from __future__ import annotations

from app.llm import client
from app.llm.prompts import load_prompt


def run(doc_id: str, current_body: str, payload: dict, source: str) -> str | None:
    """Return a new doc body, or None if no update is warranted."""
    system = load_prompt("document_updater.system")
    user = load_prompt("document_updater.user").format(
        doc_id=doc_id,
        source=source,
        current_body=current_body,
        payload=payload,
    )
    # TODO: call client.complete; parse out either a "no_change" sentinel or
    # the new body. Reject deltas that look like a wholesale rewrite unless
    # the agent justifies it.
    raise NotImplementedError

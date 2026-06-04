"""One-shot draft reviser backing the drafting chat's live edits.

Stateless: given the current (unsaved) draft body and an instruction, returns
the full revised body. The editor's content is the only state, so iterative
edits compose without conversation memory. Tests patch ``app.llm.client.complete``.
"""

from __future__ import annotations

import logging

from app.llm import client
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

_MAX_TOKENS = 4000


def revise(body: str, instruction: str, *, model: str | None = None) -> str:
    """Apply ``instruction`` to ``body``; return the full revised document."""
    user = "\n".join(
        [
            "Current document:",
            "",
            "```markdown",
            body,
            "```",
            "",
            "Instruction:",
            "",
            instruction,
        ]
    )
    messages = [
        {"role": "system", "content": load_prompt("draft_reviser.system")},
        {"role": "user", "content": user},
    ]
    with trace_flow("agent.draft_reviser", instruction=instruction):
        result = client.complete(messages, model=model, max_tokens=_MAX_TOKENS)
    return result.text.strip()

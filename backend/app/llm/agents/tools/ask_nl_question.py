"""Handler for the `ask_nl_question` tool. Spec lives in `ask_nl_question.json`.

Dispatches to the wiki Q&A sub-agent (``app.llm.agents.wiki_qa``). The
sub-agent runs its own LLM loop with a curated read-only toolset and
returns a synthesized answer plus the doc paths it grounded on.
"""
from __future__ import annotations

import logging
from typing import Any

from app.llm.agents import wiki_qa
from app.llm.errors import LLMError

log = logging.getLogger(__name__)


def handle(args: dict[str, Any]) -> Any:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"error": "query is required (non-empty string)"}

    try:
        result = wiki_qa.run(query.strip())
    except LLMError as exc:
        log.warning("ask_nl_question LLM error: %s", exc)
        return {"error": f"llm_error: {exc}"}

    return {
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
    }

"""Shared constants and utilities for LLM agents."""
from __future__ import annotations

NO_CHANGE_SENTINEL = "NO_CHANGE"
IRRELEVANT_SENTINEL = "IRRELEVANT"


def strip_outer_fence(text: str) -> str:
    """Strip a single leading/trailing markdown fence if the model added one
    despite the prompt. Does not strip nested fences — those are part of the body."""
    if not text.startswith("```"):
        return text
    first_nl = text.find("\n")
    if first_nl == -1:
        return text
    if not text.rstrip().endswith("```"):
        return text
    inner = text[first_nl + 1 :].rstrip()
    if inner.endswith("```"):
        inner = inner[:-3].rstrip()
    return inner + "\n" if text.endswith("\n") else inner

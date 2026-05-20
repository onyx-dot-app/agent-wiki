"""Shared constants and utilities for LLM agents."""
from __future__ import annotations

from app.ingest.models import WikiUpdateCandidate

NO_CHANGE_SENTINEL = "NO_CHANGE"
IRRELEVANT_SENTINEL = "IRRELEVANT"


def batch_by_chars(
    candidates: list[WikiUpdateCandidate],
    budget: int,
) -> list[list[WikiUpdateCandidate]]:
    """Return a single batch when all candidates fit; otherwise split greedily."""
    if sum(len(c.body) for c in candidates) <= budget:
        return [candidates]

    batches: list[list[WikiUpdateCandidate]] = []
    current: list[WikiUpdateCandidate] = []
    current_chars = 0
    for c in candidates:
        if current and current_chars + len(c.body) > budget:
            batches.append(current)
            current = [c]
            current_chars = len(c.body)
        else:
            current.append(c)
            current_chars += len(c.body)
    if current:
        batches.append(current)
    return batches


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

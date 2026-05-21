"""Shared constants and utilities for LLM agents."""
from __future__ import annotations

import logging
from typing import NamedTuple

from app.ingest.models import WikiUpdateCandidate

log = logging.getLogger(__name__)


class TextEdit(NamedTuple):
    find: str
    replace: str


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


def rstrip_lines(text: str) -> str:
    """Strip trailing whitespace from each line. Preserves a trailing newline if present.

    Assumes LF or CRLF line endings. Bare CR (``\\r``-only, pre-OS X Mac) is treated
    as a line separator by ``splitlines()`` and replaced with ``\\n`` on rejoin, which
    breaks the monotone subsequence property that ``_map_norm_span_to_orig`` relies on.
    Wiki markdown content never contains bare CR in practice.
    """
    lines = text.splitlines()
    normalized = "\n".join(line.rstrip() for line in lines)
    if text.endswith("\n"):
        normalized += "\n"
    return normalized


def _map_norm_span_to_orig(
    original: str, normalized: str, norm_start: int, norm_end: int
) -> tuple[int, int]:
    """Map a [norm_start, norm_end) span in a normalized string back to original.

    Single pass — captures both endpoints without restarting the walk.
    Works because normalized is derived from original by only removing characters
    (trailing whitespace per line), so the mapping is monotone.
    """
    o = n = 0
    orig_start = 0
    while n < norm_end and o < len(original):
        if n == norm_start:
            orig_start = o
        if n < len(normalized) and original[o] == normalized[n]:
            o += 1
            n += 1
        else:
            o += 1  # character was stripped in normalization, skip in original
    return orig_start, o


def _fuzzy_replace(body: str, find: str, replace: str) -> str | None:
    """Replace ``find`` in ``body`` using trailing-whitespace-normalized matching.

    Returns the new body string, or None if no match was found.
    """
    norm_body = rstrip_lines(body)
    norm_find = rstrip_lines(find)
    pos = norm_body.find(norm_find)
    if pos == -1:
        return None
    orig_start, orig_end = _map_norm_span_to_orig(body, norm_body, pos, pos + len(norm_find))
    # Consume trailing whitespace on the last matched line that normalization
    # stripped from the body but the find text didn't include.
    while orig_end < len(body) and body[orig_end] in " \t\r":
        orig_end += 1
    return body[:orig_start] + replace + body[orig_end:]


def apply_edits(body: str, edits: list[TextEdit]) -> str | None:
    """Apply (find, replace) pairs to body. Returns new body or None if unchanged.

    Falls back to ``_fuzzy_replace`` (trailing-whitespace-normalized matching)
    when the exact FIND text is not present — recovers from the common case where
    the model quotes text with slightly different trailing spaces or line endings.

    We use this targeted normalization rather than a general fuzzy-match library
    (e.g. diff-match-patch) deliberately: a library with a non-zero match threshold
    can apply an edit to the wrong location, silently corrupting wiki content.
    That false-positive failure mode is much worse than a skipped edit, which is
    recoverable on the next ingest cycle. Trailing-whitespace variance is the
    dominant real-world deviation; anything further off (wrong words, transposed
    phrases) most likely indicates the model hallucinated the context, and skipping
    is the right response.
    """
    result = body
    for find_text, replace_text in edits:
        if find_text in result:
            result = result.replace(find_text, replace_text, 1)
            continue
        new = _fuzzy_replace(result, find_text, replace_text)
        if new is None:
            log.warning(
                "apply_edits: FIND text not found in body, skipping: %r",
                find_text[:60],
            )
            continue
        log.debug("apply_edits: used normalized match for FIND: %r", find_text[:60])
        result = new
    return result if result != body else None


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

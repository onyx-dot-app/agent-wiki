"""Build the BEFORE/AFTER snippets we hand to the NL evaluator.

The evaluator is conservative on token budget. We default to a unified diff
with a few lines of context, but fall back to the full bodies when the diff
covers most of the file (no useful signal in the diff context anymore) or
when the file is new.
"""
from __future__ import annotations

import difflib

# A diff covering more than this fraction of the file isn't a useful summary;
# fall back to passing the bodies straight through.
_DIFF_DENSITY_FALLBACK = 0.5
# Hard cap so we never blow up the LLM context. Per-side, characters.
_BODY_CHAR_BUDGET = 8000


def build_payload(before: str, after: str, *, change_kind: str) -> tuple[str, str]:
    """Return ``(before_snippet, after_snippet)`` to hand to ``natural_language.matches``.

    For ``change_kind == "create"`` we always pass the full (truncated) bodies,
    since there is no diff to summarize.
    """
    before_t = _truncate(before)
    after_t = _truncate(after)

    if change_kind == "create" or not before:
        return "", after_t

    diff = _unified_diff(before, after)
    if diff and _diff_density(diff, after) <= _DIFF_DENSITY_FALLBACK:
        return before_t, f"<unified diff>\n{_truncate(diff)}\n</unified diff>\n\nFULL AFTER (for context):\n{after_t}"
    return before_t, after_t


def _unified_diff(before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            n=2,
        )
    )


def _diff_density(diff: str, after: str) -> float:
    if not after:
        return 1.0
    return len(diff) / max(len(after), 1)


def _truncate(s: str) -> str:
    if len(s) <= _BODY_CHAR_BUDGET:
        return s
    return s[: _BODY_CHAR_BUDGET] + f"\n…[truncated {len(s) - _BODY_CHAR_BUDGET} chars]"

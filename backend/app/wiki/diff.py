"""Parse + structure git unified diffs for the wiki diff viewer.

Pure helpers — the only I/O is delegating raw text to
``app.wiki.git.diff_for_commit``. Everything else is string-shaping
for the FastAPI response model in ``app.models.file_system``.
"""

from __future__ import annotations

import re

from app.models.file_system import WordDiff

_WORD_SPLIT_RE = re.compile(r"(\s+)")


def _split_words(s: str) -> list[str]:
    """Split on whitespace runs, keeping the whitespace tokens as
    elements so a round-trip ``"".join(...)`` reproduces the input."""
    return [tok for tok in _WORD_SPLIT_RE.split(s) if tok != ""]


def _word_diff(removed: str, added: str) -> WordDiff:  # pyright: ignore[reportUnusedFunction]
    """Diff two strings at word granularity.

    Counts the longest leading + trailing word runs that match between
    ``removed`` and ``added``; the middle becomes the struck-through /
    green portion. Identical inputs collapse to a pure ``prefix``.
    """
    a = _split_words(removed)
    b = _split_words(added)

    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1

    tail = 0
    while (
        tail < len(a) - head
        and tail < len(b) - head
        and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]
    ):
        tail += 1

    a_end = len(a) - tail
    b_end = len(b) - tail
    prefix = "".join(a[:head])
    suffix = "".join(a[a_end:]) if tail else ""
    removed_mid = "".join(a[head:a_end])
    added_mid = "".join(b[head:b_end])

    # When one side has no middle, a trailing whitespace token on the
    # other side belongs in the common suffix, not in the added/removed
    # span. Greedy head/tail can't see that because the empty side has
    # no token to match against.
    if not removed_mid and added_mid and added_mid[-1:].isspace():
        trimmed = added_mid.rstrip()
        suffix = added_mid[len(trimmed) :] + suffix
        added_mid = trimmed
    elif not added_mid and removed_mid and removed_mid[-1:].isspace():
        trimmed = removed_mid.rstrip()
        suffix = removed_mid[len(trimmed) :] + suffix
        removed_mid = trimmed

    # Symmetric leading-whitespace handler: when one side has no middle,
    # a leading whitespace token on the other side belongs in the common
    # prefix, not in the added/removed span.
    if not removed_mid and added_mid and added_mid[:1].isspace():
        trimmed = added_mid.lstrip()
        prefix = prefix + added_mid[: len(added_mid) - len(trimmed)]
        added_mid = trimmed
    elif not added_mid and removed_mid and removed_mid[:1].isspace():
        trimmed = removed_mid.lstrip()
        prefix = prefix + removed_mid[: len(removed_mid) - len(trimmed)]
        removed_mid = trimmed

    return WordDiff(
        prefix=prefix,
        removed=removed_mid,
        added=added_mid,
        suffix=suffix,
    )

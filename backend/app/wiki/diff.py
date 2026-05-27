"""Parse + structure git unified diffs for the wiki diff viewer.

Pure helpers — the only I/O is delegating raw text to
``app.wiki.git.diff_for_commit``. Everything else is string-shaping
for the FastAPI response model in ``app.models.file_system``.
"""

from __future__ import annotations

import re

from app.models.file_system import DiffHunk, DiffLine, WordDiff

_WORD_SPLIT_RE = re.compile(r"(\s+)")

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def _split_words(s: str) -> list[str]:
    """Split on whitespace runs, keeping the whitespace tokens as
    elements so a round-trip ``"".join(...)`` reproduces the input."""
    return [tok for tok in _WORD_SPLIT_RE.split(s) if tok != ""]


def _parse_unified(text: str) -> list[DiffHunk]:  # pyright: ignore[reportUnusedFunction]
    """Parse ``git show`` style unified diff text into structured hunks.

    File-header lines (``diff --git``, ``index``, ``new file mode``,
    ``deleted file mode``, ``---``, ``+++``) are skipped. The parser
    is lenient — anything that doesn't match a hunk header outside a
    hunk is ignored so it survives the small differences between
    ``git diff`` and ``git show`` envelopes.
    """
    hunks: list[DiffHunk] = []
    current: DiffHunk | None = None
    old_lineno = 0
    new_lineno = 0

    for raw in text.splitlines():
        header = _HUNK_HEADER_RE.match(raw)
        if header is not None:
            old_start = int(header.group("old_start"))
            old_count_str = header.group("old_count")
            old_count = int(old_count_str) if old_count_str is not None else 1
            new_start = int(header.group("new_start"))
            new_count_str = header.group("new_count")
            new_count = int(new_count_str) if new_count_str is not None else 1
            current = DiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                lines=[],
            )
            hunks.append(current)
            old_lineno = old_start
            new_lineno = new_start
            continue

        if current is None:
            continue  # still inside the file-header preamble

        if raw.startswith("+++") or raw.startswith("---"):
            # Defensive: per-file headers inside a multi-file diff would
            # land here; they don't belong to any hunk.
            continue

        if raw.startswith("+"):
            current.lines.append(
                DiffLine(
                    kind="add",
                    text=raw[1:],
                    word_diff=None,
                    old_lineno=None,
                    new_lineno=new_lineno,
                ),
            )
            new_lineno += 1
        elif raw.startswith("-"):
            current.lines.append(
                DiffLine(
                    kind="remove",
                    text=raw[1:],
                    word_diff=None,
                    old_lineno=old_lineno,
                    new_lineno=None,
                ),
            )
            old_lineno += 1
        elif raw.startswith(" "):
            current.lines.append(
                DiffLine(
                    kind="context",
                    text=raw[1:],
                    word_diff=None,
                    old_lineno=old_lineno,
                    new_lineno=new_lineno,
                ),
            )
            old_lineno += 1
            new_lineno += 1
        elif raw.startswith("\\"):
            # `\ No newline at end of file` — record nothing.
            continue

    return hunks


def _word_diff(removed: str, added: str) -> WordDiff:
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


def _promote_word_diff(hunk: DiffHunk) -> DiffHunk:  # pyright: ignore[reportUnusedFunction]
    """Collapse a hunk with exactly one ``remove`` directly followed by
    exactly one ``add`` (and no other adds/removes) into a single
    ``kind="word"`` line. Returns the hunk unchanged otherwise.
    """
    removes = [i for i, line in enumerate(hunk.lines) if line.kind == "remove"]
    adds = [i for i, line in enumerate(hunk.lines) if line.kind == "add"]
    if len(removes) != 1 or len(adds) != 1:
        return hunk
    remove_idx = removes[0]
    add_idx = adds[0]
    if add_idx != remove_idx + 1:
        return hunk

    removed_text = hunk.lines[remove_idx].text or ""
    added_text = hunk.lines[add_idx].text or ""
    word = _word_diff(removed_text, added_text)

    merged = DiffLine(
        kind="word",
        text=None,
        word_diff=word,
        old_lineno=hunk.lines[remove_idx].old_lineno,
        new_lineno=hunk.lines[add_idx].new_lineno,
    )
    new_lines = hunk.lines[:remove_idx] + [merged] + hunk.lines[add_idx + 1 :]
    return hunk.model_copy(update={"lines": new_lines})

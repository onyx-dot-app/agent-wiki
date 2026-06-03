"""Parse + structure git unified diffs for the wiki diff viewer.

Pure helpers — the only I/O is delegating raw text to
``app.wiki.git.diff_for_commit``. Everything else is string-shaping
for the FastAPI response model in ``app.models.file_system``.
"""

from __future__ import annotations

import re

from app.models.file_system import DiffHunk, DiffLine, FileDiffResponse, WordDiff
from app.wiki import git as wiki_git
from app.wiki.git import UnknownSha

_WORD_SPLIT_RE = re.compile(r"(\s+)")

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def _split_words(s: str) -> list[str]:
    """Split on whitespace runs, keeping the whitespace tokens as
    elements so a round-trip ``"".join(...)`` reproduces the input."""
    return [tok for tok in _WORD_SPLIT_RE.split(s) if tok != ""]


def _parse_unified(text: str) -> list[DiffHunk]:
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


def _promote_word_diff(hunk: DiffHunk) -> DiffHunk:
    """Collapse every adjacent 1×remove + 1×add edit block into a single
    ``kind="word"`` line. Blocks with multiple removes or adds stay as-is.

    Walks the hunk so multiple independent single-line replacements in
    the same hunk each get the inline strikethrough treatment, rather
    than only the case where the hunk consists solely of one pair.
    """
    new_lines: list[DiffLine] = []
    i = 0
    while i < len(hunk.lines):
        line = hunk.lines[i]
        if line.kind != "remove":
            new_lines.append(line)
            i += 1
            continue

        rem_start = i
        while i < len(hunk.lines) and hunk.lines[i].kind == "remove":
            i += 1
        rem_end = i
        add_start = i
        while i < len(hunk.lines) and hunk.lines[i].kind == "add":
            i += 1
        add_end = i

        if rem_end - rem_start == 1 and add_end - add_start == 1:
            rem = hunk.lines[rem_start]
            add = hunk.lines[add_start]
            rem_text = rem.text or ""
            add_text = add.text or ""
            word = _word_diff(rem_text, add_text)
            # If the two lines share basically no content (whole-line
            # replacement), inline word-diff devolves into "strikethrough
            # one phrase, green another" — visually inconsistent with
            # neighboring block add/remove bands. Demote to block so the
            # diff reads uniformly.
            if len(word.prefix) + len(word.suffix) < 4:
                new_lines.extend(hunk.lines[rem_start:add_end])
                continue
            new_lines.append(
                DiffLine(
                    kind="word",
                    text=None,
                    word_diff=word,
                    old_lineno=rem.old_lineno,
                    new_lineno=add.new_lineno,
                ),
            )
        else:
            new_lines.extend(hunk.lines[rem_start:add_end])

    return hunk.model_copy(update={"lines": new_lines})


def parse_commit_diff(sha: str, rel: str) -> FileDiffResponse:
    """Build a structured diff for ``sha`` vs its parent, scoped to ``rel``.

    ``rel`` is the file's *current* path. The history panel lists commits via
    ``git log --follow``, so it includes commits from before a rename, when the
    file lived at a different name. Diffing those against the current ``rel``
    would touch nothing ("sha does not touch path"); resolve the name the file
    had at ``sha`` (via ``path_at_ref``) and diff that instead — same fix the
    read-at-ref path uses.

    First-commit (no parent) → ``parent_sha`` is None, ``is_creation`` is
    True, every line is an ``add``. ``rel`` genuinely not touched by ``sha`` →
    returns an empty-hunks response; callers (the API route) should translate
    that into 404 for end users. Unknown SHA (passes hex regex but doesn't
    resolve in the repo) → same empty-hunks response, same 404 at the route.
    """
    effective_rel: str = wiki_git.path_at_ref(rel, sha) or rel
    try:
        parent = wiki_git.parent_sha(sha)
        # Pass unified=99_999 so the full doc body lands in `context`
        # lines around the hunks; the FE renders the whole file with
        # +/- highlights on changed lines instead of just hunk windows.
        raw = wiki_git.diff_for_commit(sha, effective_rel, unified=99_999)
    except UnknownSha:
        return FileDiffResponse(
            path=rel,
            sha=sha,
            parent_sha=None,
            hunks=[],
            is_creation=False,
        )
    hunks = _parse_unified(raw)
    hunks = [_promote_word_diff(h) for h in hunks]
    return FileDiffResponse(
        path=rel,
        sha=sha,
        parent_sha=parent,
        hunks=hunks,
        is_creation=parent is None,
    )

"""Fuzzy find-and-replace primitives for wiki edits.

Pure logic — no I/O, no git. Callers (the doc-edit tools) read a body,
call ``replace()``, and write the result back.

Ported from opencode (``packages/opencode/src/tool/edit.ts``), which
sources its strategies from the cline and gemini-cli projects.

Strategy chain — tried in order until one yields a candidate that
appears exactly once in the content:

    SimpleReplacer
    LineTrimmedReplacer
    BlockAnchorReplacer
    WhitespaceNormalizedReplacer
    IndentationFlexibleReplacer
    EscapeNormalizedReplacer
    TrimmedBoundaryReplacer
    ContextAwareReplacer
    MultiOccurrenceReplacer

A "candidate" is a substring of ``content`` that we'd treat as a match
for ``find``. The outer ``replace()`` accepts a candidate iff:
  * ``replace_all`` is true (all occurrences swapped), or
  * the candidate appears exactly once in ``content`` (no ambiguity).

If no replacer yields any candidate: ``ReplaceNotFound``.
If candidates exist but every one is ambiguous: ``ReplaceAmbiguous``.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #


class ReplaceError(Exception):
    """Base for replace failures."""


class ReplaceNotFound(ReplaceError):
    pass


class ReplaceAmbiguous(ReplaceError):
    pass


class ReplaceNoOp(ReplaceError):
    """``old_string`` and ``new_string`` are identical."""


# --------------------------------------------------------------------------- #
# Levenshtein                                                                 #
# --------------------------------------------------------------------------- #


def _levenshtein(a: str, b: str) -> int:
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


# --------------------------------------------------------------------------- #
# Replacer strategies                                                         #
# --------------------------------------------------------------------------- #
#
# Each replacer is a generator: ``replacer(content, find) -> Iterator[str]``
# yielding candidate substrings of ``content`` that should be treated as
# matches for ``find``.

Replacer = Iterable[str]


def simple_replacer(content: str, find: str) -> Iterator[str]:
    yield find


def line_trimmed_replacer(content: str, find: str) -> Iterator[str]:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if not search_lines:
        return

    for i in range(0, len(original_lines) - len(search_lines) + 1):
        matches = True
        for j in range(len(search_lines)):
            if original_lines[i + j].strip() != search_lines[j].strip():
                matches = False
                break
        if not matches:
            continue

        match_start = sum(len(original_lines[k]) + 1 for k in range(i))
        match_end = match_start
        for k in range(len(search_lines)):
            match_end += len(original_lines[i + k])
            if k < len(search_lines) - 1:
                match_end += 1
        yield content[match_start:match_end]


# Block-anchor thresholds (mirror opencode's edit.ts).
_SINGLE_CANDIDATE_THRESHOLD = 0.0
_MULTIPLE_CANDIDATES_THRESHOLD = 0.3


def block_anchor_replacer(content: str, find: str) -> Iterator[str]:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if len(search_lines) < 3:
        return
    if search_lines[-1] == "":
        search_lines.pop()
    if len(search_lines) < 3:
        return

    first = search_lines[0].strip()
    last = search_lines[-1].strip()
    search_size = len(search_lines)

    candidates: list[tuple[int, int]] = []
    for i, line in enumerate(original_lines):
        if line.strip() != first:
            continue
        for j in range(i + 2, len(original_lines)):
            if original_lines[j].strip() == last:
                candidates.append((i, j))
                break

    if not candidates:
        return

    def _yield(start: int, end: int) -> Iterator[str]:
        match_start = sum(len(original_lines[k]) + 1 for k in range(start))
        match_end = match_start
        for k in range(start, end + 1):
            match_end += len(original_lines[k])
            if k < end:
                match_end += 1
        yield content[match_start:match_end]

    if len(candidates) == 1:
        start, end = candidates[0]
        actual_size = end - start + 1
        lines_to_check = min(search_size - 2, actual_size - 2)
        if lines_to_check > 0:
            similarity = 0.0
            for j in range(1, min(search_size - 1, actual_size - 1)):
                orig = original_lines[start + j].strip()
                srch = search_lines[j].strip()
                max_len = max(len(orig), len(srch))
                if max_len == 0:
                    continue
                dist = _levenshtein(orig, srch)
                similarity += (1 - dist / max_len) / lines_to_check
                if similarity >= _SINGLE_CANDIDATE_THRESHOLD:
                    break
        else:
            similarity = 1.0

        if similarity >= _SINGLE_CANDIDATE_THRESHOLD:
            yield from _yield(start, end)
        return

    best: tuple[int, int] | None = None
    best_sim = -1.0
    for start, end in candidates:
        actual_size = end - start + 1
        lines_to_check = min(search_size - 2, actual_size - 2)
        if lines_to_check > 0:
            similarity = 0.0
            for j in range(1, min(search_size - 1, actual_size - 1)):
                orig = original_lines[start + j].strip()
                srch = search_lines[j].strip()
                max_len = max(len(orig), len(srch))
                if max_len == 0:
                    continue
                dist = _levenshtein(orig, srch)
                similarity += 1 - dist / max_len
            similarity /= lines_to_check
        else:
            similarity = 1.0
        if similarity > best_sim:
            best_sim = similarity
            best = (start, end)

    if best is not None and best_sim >= _MULTIPLE_CANDIDATES_THRESHOLD:
        yield from _yield(*best)


_WS_RE = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def whitespace_normalized_replacer(content: str, find: str) -> Iterator[str]:
    normalized_find = _normalize_ws(find)
    if not normalized_find:
        return

    lines = content.split("\n")

    # Single-line matches.
    for line in lines:
        if _normalize_ws(line) == normalized_find:
            yield line
            continue
        if normalized_find in _normalize_ws(line):
            words = find.strip().split()
            if not words:
                continue
            pattern = r"\s+".join(re.escape(w) for w in words)
            try:
                m = re.search(pattern, line)
            except re.error:
                continue
            if m:
                yield m.group(0)

    # Multi-line matches.
    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(0, len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i : i + len(find_lines)])
            if _normalize_ws(block) == normalized_find:
                yield block


def _remove_indentation(text: str) -> str:
    lines = text.split("\n")
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return text
    min_indent = min(len(ln) - len(ln.lstrip()) for ln in non_empty)
    return "\n".join(ln if not ln.strip() else ln[min_indent:] for ln in lines)


def indentation_flexible_replacer(content: str, find: str) -> Iterator[str]:
    normalized_find = _remove_indentation(find)
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(0, len(content_lines) - len(find_lines) + 1):
        block = "\n".join(content_lines[i : i + len(find_lines)])
        if _remove_indentation(block) == normalized_find:
            yield block


_ESCAPE_MAP = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "'": "'",
    '"': '"',
    "`": "`",
    "\\": "\\",
    "\n": "\n",
    "$": "$",
}
_ESCAPE_RE = re.compile(r"\\(n|t|r|'|\"|`|\\|\n|\$)")


def _unescape(s: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group(1)], s)


def escape_normalized_replacer(content: str, find: str) -> Iterator[str]:
    unescaped_find = _unescape(find)
    if unescaped_find in content:
        yield unescaped_find

    lines = content.split("\n")
    find_lines = unescaped_find.split("\n")
    for i in range(0, len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i : i + len(find_lines)])
        if _unescape(block) == unescaped_find:
            yield block


def trimmed_boundary_replacer(content: str, find: str) -> Iterator[str]:
    trimmed = find.strip()
    if trimmed == find:
        return
    if trimmed and trimmed in content:
        yield trimmed

    lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(0, len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i : i + len(find_lines)])
        if block.strip() == trimmed:
            yield block


def context_aware_replacer(content: str, find: str) -> Iterator[str]:
    find_lines = find.split("\n")
    if len(find_lines) < 3:
        return
    if find_lines[-1] == "":
        find_lines.pop()
    if len(find_lines) < 3:
        return

    content_lines = content.split("\n")
    first = find_lines[0].strip()
    last = find_lines[-1].strip()

    for i in range(len(content_lines)):
        if content_lines[i].strip() != first:
            continue
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() != last:
                continue
            block_lines = content_lines[i : j + 1]
            if len(block_lines) != len(find_lines):
                break
            matching = 0
            non_empty = 0
            for k in range(1, len(block_lines) - 1):
                bl = block_lines[k].strip()
                fl = find_lines[k].strip()
                if bl or fl:
                    non_empty += 1
                    if bl == fl:
                        matching += 1
            if non_empty == 0 or matching / non_empty >= 0.5:
                yield "\n".join(block_lines)
                break
            break


def multi_occurrence_replacer(content: str, find: str) -> Iterator[str]:
    if not find:
        return
    start = 0
    while True:
        idx = content.find(find, start)
        if idx == -1:
            return
        yield find
        start = idx + len(find)


_REPLACERS = (
    simple_replacer,
    line_trimmed_replacer,
    block_anchor_replacer,
    whitespace_normalized_replacer,
    indentation_flexible_replacer,
    escape_normalized_replacer,
    trimmed_boundary_replacer,
    context_aware_replacer,
    multi_occurrence_replacer,
)


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def replace(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Apply a fuzzy find-and-replace and return the new content.

    Raises ``ReplaceNoOp`` if the strings are identical, ``ReplaceNotFound``
    if no strategy yields a candidate, ``ReplaceAmbiguous`` if every
    candidate appears more than once and ``replace_all`` is False.
    """
    if old_string == new_string:
        raise ReplaceNoOp("old_string and new_string must be different")

    found_any = False
    for replacer in _REPLACERS:
        for candidate in replacer(content, old_string):
            idx = content.find(candidate)
            if idx == -1:
                continue
            found_any = True
            if replace_all:
                return content.replace(candidate, new_string)
            last = content.rfind(candidate)
            if idx != last:
                continue
            return content[:idx] + new_string + content[idx + len(candidate) :]

    if not found_any:
        raise ReplaceNotFound("old_string not found in content")
    raise ReplaceAmbiguous(
        "old_string matched multiple times — provide more surrounding context "
        "to identify the intended match, or pass replace_all=true"
    )

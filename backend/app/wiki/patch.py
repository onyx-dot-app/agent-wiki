"""Apply a unified-diff patch to a wiki body.

Pure logic, no I/O. The doc-edit tool ``apply_patch`` reads the current
body, calls ``apply()``, and writes the result back through the same
``commit_and_fan_out`` seam every other write tool uses.

Two-stage match per hunk:

1. **Line-anchored.** The hunk header (``@@ -L,N +L,M @@``) names a line
   range. Verify the ``before`` lines (context + ``-`` lines) sit at that
   range in the current body, then splice in the ``after`` lines (context
   + ``+`` lines). A cumulative offset accounts for line shifts caused by
   earlier hunks.
2. **Fuzzy fallback.** If the line anchor fails (lines drifted, agent
   computed against a stale view, etc.), reuse the same fuzzy chain
   ``edit_doc`` already does — ``app.wiki.edit.replace(body, before,
   after)``. Pure-insertion hunks (no context, only ``+``) need the line
   anchor to succeed; there's no `before` to fuzzy-match against.

Atomic: any hunk failure raises ``PatchError`` and the caller's body is
untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.wiki import edit as wiki_edit


class PatchError(Exception):
    """Patch could not be applied (parse failure or hunk mismatch)."""


@dataclass
class Hunk:
    original_start: int  # 1-indexed line where the hunk applies in the original
    original_count: int  # number of original lines covered (context + removals)
    new_start: int
    new_count: int
    lines: list[tuple[str, str]]  # (kind, line-with-newline). kind in " -+"


_HUNK_HEADER = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def parse(patch: str) -> list[Hunk]:
    """Parse a unified diff into a list of hunks. Raises ``PatchError``.

    Tolerates leading file headers (``--- a/x``, ``+++ b/x``, ``diff
    --git ...``) and stops a hunk body when it hits the next ``@@`` or a
    line that doesn't start with ``space``/``-``/``+``/``\\``.
    """
    raw = patch.splitlines(keepends=True)
    hunks: list[Hunk] = []
    i = 0
    while i < len(raw):
        m = _HUNK_HEADER.match(raw[i])
        if not m:
            i += 1
            continue
        os_, oc_, ns_, nc_ = m.groups()
        original_count = int(oc_) if oc_ is not None else 1
        new_count = int(nc_) if nc_ is not None else 1
        hunk = Hunk(
            original_start=int(os_),
            original_count=original_count,
            new_start=int(ns_),
            new_count=new_count,
            lines=[],
        )
        i += 1
        seen_old = 0
        seen_new = 0
        while i < len(raw):
            line = raw[i]
            if not line:
                i += 1
                continue
            if line.startswith("@@"):
                break
            kind = line[0]
            if kind == "\\":  # "\ No newline at end of file" — ignore
                i += 1
                continue
            if kind not in (" ", "-", "+"):
                break
            text = line[1:]
            hunk.lines.append((kind, text))
            if kind in (" ", "-"):
                seen_old += 1
            if kind in (" ", "+"):
                seen_new += 1
            i += 1
            if seen_old >= original_count and seen_new >= new_count:
                break
        if not hunk.lines:
            raise PatchError(
                f"hunk @@ -{hunk.original_start} +{hunk.new_start} @@ is empty"
            )
        hunks.append(hunk)
    if not hunks:
        raise PatchError("no hunks found in patch")
    return hunks


def _before_after_lines(hunk: Hunk) -> tuple[list[str], list[str]]:
    before = [text for kind, text in hunk.lines if kind in (" ", "-")]
    after = [text for kind, text in hunk.lines if kind in (" ", "+")]
    return before, after


def apply(content: str, patch: str) -> str:
    """Apply ``patch`` (unified diff) to ``content``. Atomic.

    Returns the new content. Raises ``PatchError`` if any hunk fails to
    apply — the caller's content is unchanged.
    """
    hunks = parse(patch)
    body_lines = content.splitlines(keepends=True)
    offset = 0  # net line shift from prior hunks (new_count - original_count cumulative)

    for idx, hunk in enumerate(hunks, start=1):
        before, after = _before_after_lines(hunk)
        target = hunk.original_start - 1 + offset  # 0-indexed slice start

        # Stage 1: line-anchored apply.
        if (
            0 <= target <= len(body_lines)
            and body_lines[target : target + len(before)] == before
        ):
            body_lines = (
                body_lines[:target] + after + body_lines[target + len(before) :]
            )
            offset += len(after) - len(before)
            continue

        # Stage 2: fuzzy fallback. Pure-insertion hunks (empty `before`) can't
        # use this — they have no anchor to match.
        if not before:
            raise PatchError(
                f"hunk #{idx} (@@ -{hunk.original_start} +{hunk.new_start} @@): "
                f"pure insertion did not match line {hunk.original_start} "
                f"(adjusted to {target + 1}); no fuzzy fallback possible"
            )

        body = "".join(body_lines)
        before_str = "".join(before)
        after_str = "".join(after)
        try:
            new_body = wiki_edit.replace(body, before_str, after_str)
        except wiki_edit.ReplaceError as exc:
            raise PatchError(
                f"hunk #{idx} (@@ -{hunk.original_start} +{hunk.new_start} @@): "
                f"{exc}"
            )
        body_lines = new_body.splitlines(keepends=True)
        offset += len(after) - len(before)

    return "".join(body_lines)

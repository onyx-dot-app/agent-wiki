"""Position-based re-anchoring for wiki page comments.

A comment anchors to a half-open character range ``[start, end)`` into a page
body at a known commit (``anchor_sha``). When the page changes, we re-derive
the range against the new body from the *exact* diff between the two versions
— never a fuzzy text search.

The two endpoints are mapped **independently**, so an edit *inside* the span
resizes it: a deletion shrinks the highlight, an insertion grows it. The
comment is treated as *orphaned* only when its span collapses to empty
(``start >= end`` after mapping) — i.e. nothing the endpoints can straddle
survived.

What collapses, and what doesn't:

- A pure **deletion** of the anchored text → orphan.
- A **clean replacement** (the span is exactly one replaced region with no
  characters in common) → orphan.
- A **rewrite that keeps incidental characters** (shared words, spaces, common
  letters — the realistic agent-rewrite case) does *not* collapse; the comment
  **migrates onto the surviving/replacement text** instead of orphaning. This
  is a deliberate consequence of using the exact diff rather than a fuzzy
  similarity threshold: we never decide "close enough is gone." Tightening this
  to orphan heavy rewrites would require coarser (word-level) granularity or a
  survival threshold; deferred on purpose.

Boundary rule: ``start`` biases toward the content that follows it and ``end``
toward the content that precedes it, so text inserted exactly at either edge
lands *outside* the highlight rather than silently extending it.

Offsets are Unicode **code-point** indices into the body. The frontend must
count the same way (iterate code points, not UTF-16 units) for astral
characters to line up; wiki markdown is overwhelmingly BMP, so this only
matters for emoji and similar.

Pure module — no I/O. Callers pass the two bodies in; ``app/wiki/git.py``
reads them at the relevant refs.
"""
from __future__ import annotations

from collections.abc import Sequence
from difflib import SequenceMatcher

# Endpoint association: +1 sticks to following content, -1 to preceding.
_START = 1
_END = -1

# (tag, i1, i2, j1, j2) as returned by SequenceMatcher.get_opcodes().
_Opcode = tuple[str, int, int, int, int]


def _map_pos(opcodes: Sequence[_Opcode], pos: int, assoc: int) -> int:
    """Map a single position from old to new coordinates.

    ``assoc`` picks the tie-break at a boundary several opcodes touch:
    ``_START`` (+1) prefers the later opcode, ``_END`` (-1) the earlier one.
    Inside a changed (non-``equal``) opcode the position collapses to the
    far edge for ``_START`` and the near edge for ``_END`` — so a position
    swallowed by a deletion lands on the deletion point from both sides.
    """
    candidates = [op for op in opcodes if op[1] <= pos <= op[2]]
    if not candidates:  # defensive — opcodes always tile [0, len(old)]
        return opcodes[-1][4] if opcodes else 0
    tag, i1, _i2, j1, j2 = candidates[-1] if assoc > 0 else candidates[0]
    if tag == "equal":
        return j1 + (pos - i1)
    return j2 if assoc > 0 else j1


def remap_range(
    old_body: str, new_body: str, start: int, end: int
) -> tuple[int, int] | None:
    """Map ``[start, end)`` from ``old_body`` onto ``new_body``.

    Returns the new ``(start, end)`` tuple, or ``None`` when the span
    collapsed (the anchored text was entirely removed) — i.e. the comment
    should be orphaned.
    """
    if not 0 <= start <= end <= len(old_body):
        raise ValueError(
            f"range [{start}, {end}) out of bounds for body of len {len(old_body)}"
        )
    if old_body == new_body:
        return (start, end)
    opcodes = SequenceMatcher(None, old_body, new_body, autojunk=False).get_opcodes()
    new_start = _map_pos(opcodes, start, _START)
    new_end = _map_pos(opcodes, end, _END)
    if new_start >= new_end:
        return None
    return (new_start, new_end)

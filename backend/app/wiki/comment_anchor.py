"""Position-based re-anchoring for wiki page comments.

A comment anchors to a half-open character range ``[start, end)`` into a page
body at a known commit (``anchor_sha``). When the page changes, we re-derive the
range against the new body from the *exact* diff between the two versions —
never a fuzzy text search.

**Endpoints — character-precise.** The two ends are mapped independently through
a character-level diff, so an edit *inside* the span resizes it (a deletion
shrinks the highlight, an insertion grows it) and an unchanged span keeps its
exact offsets. ``start`` biases toward following content and ``end`` toward
preceding content, so text inserted at an edge lands outside the highlight.

**Orphan decision — word level.** Whether to *trust* the remapped span is judged
on a separate **word/token** diff, not the character diff. A character diff
treats coincidental shared letters and spaces as "preserved," which makes a
rewritten span look half-survived and yields confidently-wrong anchors; a token
diff makes a genuine rewrite register as a clean replacement. Tokens that
survive unchanged count in full; a token *edited in place* (a typo fix, a
pluralization, "weekly"→"biweekly") gets **partial credit** by how similar the
old and new run are, so an ordinary human edit inside the span keeps its anchor
instead of orphaning. A comment is orphaned (shows its ``quoted_text`` tombstone
instead of a highlight) when:

- the span collapses to empty (``start >= end``): the whole region was removed;
- too little of the remapped span carries over (below ``_MIN_PRESERVED``): the
  alignment landed mostly on rewritten/unrelated text; or
- the survivor is only whitespace: nothing real is left to anchor to.

Orphaning is the safe failure mode — better a tombstone than a confidently wrong
anchor.

Offsets are Unicode **code-point** indices into the body. The frontend must
count the same way (iterate code points, not UTF-16 units) for astral
characters to line up; wiki markdown is overwhelmingly BMP, so this only matters
for emoji and similar.

Pure module — no I/O. Callers pass the two bodies in; ``app/wiki/git.py`` reads
them at the relevant refs.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

# A token is a run of whitespace or a run of non-whitespace; concatenating all
# token texts reproduces the body exactly.
_TOKEN_RE = re.compile(r"\s+|\S+")

# Endpoint association: +1 sticks to following content, -1 to preceding.
_START = 1
_END = -1

# A remapped span must carry over at least this fraction from the old span —
# unchanged tokens at full weight, in-place-edited tokens at their char-level
# similarity — to be trusted; below it the alignment has likely landed on
# rewritten text the comment never referred to, so we orphan. The one tunable
# knob — raise it to orphan more aggressively, lower it to keep more migrated
# anchors.
_MIN_PRESERVED = 0.5

# (tag, i1, i2, j1, j2) as returned by SequenceMatcher.get_opcodes().
_Opcode = tuple[str, int, int, int, int]


def _map_pos(opcodes: Sequence[_Opcode], pos: int, assoc: int) -> int:
    """Map a single character position from old to new coordinates.

    ``assoc`` picks the tie-break at a boundary several opcodes touch: ``_START``
    (+1) prefers the later opcode, ``_END`` (-1) the earlier one. Inside a
    changed (non-``equal``) opcode the position collapses to the far edge for
    ``_START`` and the near edge for ``_END`` — so a position swallowed by a
    deletion lands on the deletion point from both sides."""
    candidates = [op for op in opcodes if op[1] <= pos <= op[2]]
    if not candidates:  # defensive — opcodes always tile [0, len(old)]
        return opcodes[-1][4] if opcodes else 0
    tag, i1, _i2, j1, j2 = candidates[-1] if assoc > 0 else candidates[0]
    if tag == "equal":
        return j1 + (pos - i1)
    return j2 if assoc > 0 else j1


def _word_preserved_fraction(old_body: str, new_body: str, new_start: int, new_end: int) -> float:
    """How much of the new span ``[new_start, new_end)`` carries over from
    ``old_body``, scored at word/whitespace-token granularity.

    Diffing at token granularity (not characters) is what stops a genuine
    rewrite from looking half-survived: difflib won't align two unrelated runs
    on coincidental shared letters/spaces when the unit is a whole token.

    Tokens that survive **unchanged** (``equal`` opcodes) count in full. A token
    run that was **edited in place** (``replace`` opcode) gets *partial* credit
    equal to the character-level similarity between the old and new run — so a
    one-letter typo fix, a pluralization, or "weekly"→"biweekly" still reads as
    mostly-preserved (the comment follows the edit), while a run replaced by
    unrelated text scores near zero and orphans. Inserted/deleted runs count for
    nothing."""
    span = new_end - new_start
    if span <= 0:
        return 0.0
    old_tokens = [m.group() for m in _TOKEN_RE.finditer(old_body)]
    new_tokens = [(m.group(), m.start()) for m in _TOKEN_RE.finditer(new_body)]
    new_len = len(new_body)

    def token_start(idx: int) -> int:
        return new_tokens[idx][1] if idx < len(new_tokens) else new_len

    opcodes = SequenceMatcher(
        None, old_tokens, [t[0] for t in new_tokens], autojunk=False
    ).get_opcodes()
    kept = 0.0
    for tag, i1, i2, j1, j2 in opcodes:
        if tag not in ("equal", "replace"):
            continue  # insert / delete preserve nothing
        lo = max(new_start, token_start(j1))
        hi = min(new_end, token_start(j2))
        overlap = hi - lo
        if overlap <= 0:
            continue
        if tag == "equal":
            kept += overlap
        else:  # replace — credit the in-place edit by how similar the runs are
            old_run = "".join(old_tokens[i1:i2])
            new_run = "".join(t[0] for t in new_tokens[j1:j2])
            similarity = SequenceMatcher(None, old_run, new_run, autojunk=False).ratio()
            kept += similarity * overlap
    return kept / span


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c == "_"


def _snap_to_words(body: str, start: int, end: int) -> tuple[int, int]:
    """Expand ``[start, end)`` outward over adjacent word characters so a
    remapped anchor never lands mid-word. Stops at whitespace and punctuation,
    so a span ending before "." doesn't swallow the period."""
    while start > 0 and _is_word_char(body[start - 1]):
        start -= 1
    while end < len(body) and _is_word_char(body[end]):
        end += 1
    return start, end


def resolve_exact_span(
    body: str, approx_start: int, approx_end: int, quoted_text: str
) -> tuple[int, int]:
    """Correct an approximate ``[start, end)`` span against the *real*
    markdown-source ``body``, using ``quoted_text`` to locate the true span.

    The frontend's span is only an estimate: it comes from ``textOffsets.ts``'s
    plain-text offset mapper, which strips markdown syntax (a heading's
    plain-text content is ``"Heading"``, not ``"# Heading"``), so it
    under-counts by exactly that syntax's overhead. Left uncorrected, the
    stored offsets silently drift from the real source — and
    ``remap_range`` above is an *exact* character diff between two bodies,
    with no fuzzy matching, so an already-wrong starting span only ever
    compounds through every future remap.

    Returns the approximate span unchanged if ``quoted_text`` is empty, or
    isn't found anywhere in ``body`` at all (nothing to correct against —
    never worse than the caller's own estimate). When ``quoted_text``
    occurs more than once, picks the occurrence whose start is closest to
    ``approx_start``: the estimate is off by the syntax overhead *before*
    the span, so the true position is always nearby, never far.
    """
    if not quoted_text:
        return approx_start, approx_end
    if body[approx_start:approx_end] == quoted_text:
        return approx_start, approx_end  # already exact — no syntax preceded it
    starts: list[int] = []
    idx = body.find(quoted_text)
    while idx != -1:
        starts.append(idx)
        idx = body.find(quoted_text, idx + 1)
    if not starts:
        return approx_start, approx_end
    best = min(starts, key=lambda s: abs(s - approx_start))
    return best, best + len(quoted_text)


def remap_range(
    old_body: str, new_body: str, start: int, end: int
) -> tuple[int, int] | None:
    """Map ``[start, end)`` from ``old_body`` onto ``new_body``.

    Returns the new ``(start, end)`` tuple, or ``None`` when the comment should
    be orphaned (span removed, mostly rewritten, or whitespace-only survivor).
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
        return None  # whole span deleted/replaced
    # Snap to whole-word boundaries so an edited word inside the span re-anchors
    # cleanly (e.g. "variable"->"parameter" anchors to the full word; pulling in
    # the surrounding preserved word also lifts the survival fraction for in-place
    # edits like "weekly"->"biweekly").
    new_start, new_end = _snap_to_words(new_body, new_start, new_end)
    if _word_preserved_fraction(old_body, new_body, new_start, new_end) < _MIN_PRESERVED:
        return None  # alignment landed on mostly-rewritten text — orphan, don't mislead
    if not new_body[new_start:new_end].strip():
        return None  # only whitespace survived — nothing real to anchor to
    return (new_start, new_end)

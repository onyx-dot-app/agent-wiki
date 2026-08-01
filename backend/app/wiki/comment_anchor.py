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

import logging
import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import cast

from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

# A token is a run of whitespace or a run of non-whitespace; concatenating all
# token texts reproduces the body exactly.
_TOKEN_RE = re.compile(r"\s+|\S+")

# Caps that keep one remap CPU-bounded. A character-level SequenceMatcher costs
# ~O(len_a * len_b) in the worst case (small alphabet, long chains), which
# measured out to minutes of pure CPU on a ~190k-char page — long enough to
# stall the single-threaded documents queue until the pod was killed. A changed
# hunk whose side product exceeds the cap keeps one coarse `replace` opcode
# instead (endpoints collapse to the hunk edges — the same outcome as a full
# rewrite of that hunk). 4M ≈ a 2000x2000-char hunk ≈ ~0.1s of diffing.
_MAX_HUNK_CHAR_PRODUCT = 4_000_000
# Same guard for the whole-body token diff that scores span survival. Above the
# cap the survival guard is skipped (spans are kept on the strength of the
# endpoint mapping alone); ~10k tokens per side is far past any page we host.
_MAX_TOKEN_PRODUCT = 100_000_000

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


class BodyDiff(BaseModel):
    """Precomputed diff artifacts for one ``(old_body, new_body)`` pair.

    Everything here depends only on the two bodies, never on the span being
    remapped — so a caller re-anchoring many spans between the same two
    versions (``anchor_remap`` batches by ``anchor_sha``) computes this once
    instead of paying both diffs again for every span.

    ``token_opcodes`` is ``None`` when the token diff was skipped under
    ``_MAX_TOKEN_PRODUCT``; the survival guard is then bypassed.
    """

    model_config = ConfigDict(frozen=True)

    char_opcodes: list[_Opcode]
    token_opcodes: list[_Opcode] | None
    old_tokens: list[str]
    new_tokens: list[str]
    new_token_starts: list[int]


def _char_opcodes(old_body: str, new_body: str) -> list[_Opcode]:
    """Character-coordinate opcodes for old→new, via a line-level pre-pass.

    A raw character-level ``SequenceMatcher`` over two whole bodies is
    quadratic in body length. Lines are high-entropy elements, so the line
    diff stays fast at any body size; character precision is only needed
    *inside* changed hunks, which ordinary edits keep small. Equal lines map
    positions exactly, so anchors in unchanged text get identical results to
    the whole-body character diff. The returned opcodes tile
    ``[0, len(old_body)]`` × ``[0, len(new_body)]`` exactly like
    ``SequenceMatcher.get_opcodes()``.
    """
    old_lines = old_body.splitlines(keepends=True)
    new_lines = new_body.splitlines(keepends=True)
    line_ops = SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes()
    out: list[_Opcode] = []
    i_char = j_char = 0  # running char offsets; line opcodes tile in order
    for tag, i1, i2, j1, j2 in line_ops:
        a_len = sum(len(line) for line in old_lines[i1:i2])
        b_len = sum(len(line) for line in new_lines[j1:j2])
        if tag == "replace":
            if a_len * b_len > _MAX_HUNK_CHAR_PRODUCT:
                log.warning(
                    "char diff hunk over cap (%d x %d chars) — keeping coarse replace",
                    a_len, b_len,
                )
                out.append(("replace", i_char, i_char + a_len, j_char, j_char + b_len))
            else:
                sub = SequenceMatcher(
                    None,
                    old_body[i_char:i_char + a_len],
                    new_body[j_char:j_char + b_len],
                    autojunk=False,
                ).get_opcodes()
                out.extend(
                    (t, i_char + s1, i_char + s2, j_char + t1, j_char + t2)
                    for t, s1, s2, t1, t2 in sub
                )
        else:  # equal / delete / insert need no character alignment
            out.append((tag, i_char, i_char + a_len, j_char, j_char + b_len))
        i_char += a_len
        j_char += b_len
    return out


def body_diff(old_body: str, new_body: str) -> BodyDiff:
    """Compute the char- and token-level diffs ``remap_range`` needs."""
    old_tokens = [m.group() for m in _TOKEN_RE.finditer(old_body)]
    new_tokens: list[str] = []
    new_token_starts: list[int] = []
    for m in _TOKEN_RE.finditer(new_body):
        new_tokens.append(m.group())
        new_token_starts.append(m.start())

    token_opcodes: list[_Opcode] | None
    if len(old_tokens) * len(new_tokens) > _MAX_TOKEN_PRODUCT:
        log.warning(
            "token diff over cap (%d x %d tokens) — skipping survival guard",
            len(old_tokens), len(new_tokens),
        )
        token_opcodes = None
    else:
        token_opcodes = cast(
            "list[_Opcode]",
            SequenceMatcher(None, old_tokens, new_tokens, autojunk=False).get_opcodes(),
        )
    return BodyDiff(
        char_opcodes=_char_opcodes(old_body, new_body),
        token_opcodes=token_opcodes,
        old_tokens=old_tokens,
        new_tokens=new_tokens,
        new_token_starts=new_token_starts,
    )


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


def _word_preserved_fraction(diff: BodyDiff, new_body_len: int, new_start: int, new_end: int) -> float:
    """How much of the new span ``[new_start, new_end)`` carries over from
    the old body, scored at word/whitespace-token granularity.

    Diffing at token granularity (not characters) is what stops a genuine
    rewrite from looking half-survived: difflib won't align two unrelated runs
    on coincidental shared letters/spaces when the unit is a whole token.

    Tokens that survive **unchanged** (``equal`` opcodes) count in full. A token
    run that was **edited in place** (``replace`` opcode) gets *partial* credit
    equal to the character-level similarity between the old and new run — so a
    one-letter typo fix, a pluralization, or "weekly"→"biweekly" still reads as
    mostly-preserved (the comment follows the edit), while a run replaced by
    unrelated text scores near zero and orphans. Inserted/deleted runs count for
    nothing.

    When the token diff was skipped under ``_MAX_TOKEN_PRODUCT``
    (``diff.token_opcodes is None``) the guard passes unconditionally: the
    endpoint mapping is still exact, and keeping the span beats orphaning
    every anchor on a page too large to score."""
    span = new_end - new_start
    if span <= 0:
        return 0.0
    if diff.token_opcodes is None:
        return 1.0

    def token_start(idx: int) -> int:
        return diff.new_token_starts[idx] if idx < len(diff.new_token_starts) else new_body_len

    kept = 0.0
    for tag, i1, i2, j1, j2 in diff.token_opcodes:
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
            old_run = "".join(diff.old_tokens[i1:i2])
            new_run = "".join(diff.new_tokens[j1:j2])
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
    old_body: str, new_body: str, start: int, end: int, *, diff: BodyDiff | None = None
) -> tuple[int, int] | None:
    """Map ``[start, end)`` from ``old_body`` onto ``new_body``.

    Returns the new ``(start, end)`` tuple, or ``None`` when the comment should
    be orphaned (span removed, mostly rewritten, or whitespace-only survivor).

    ``diff`` is the precomputed ``body_diff(old_body, new_body)``; pass it when
    remapping several spans between the same two bodies so the diffs are paid
    once, not once per span.
    """
    if not 0 <= start <= end <= len(old_body):
        raise ValueError(
            f"range [{start}, {end}) out of bounds for body of len {len(old_body)}"
        )
    if old_body == new_body:
        return (start, end)
    if diff is None:
        diff = body_diff(old_body, new_body)

    new_start = _map_pos(diff.char_opcodes, start, _START)
    new_end = _map_pos(diff.char_opcodes, end, _END)

    if new_start >= new_end:
        return None  # whole span deleted/replaced
    # Snap to whole-word boundaries so an edited word inside the span re-anchors
    # cleanly (e.g. "variable"->"parameter" anchors to the full word; pulling in
    # the surrounding preserved word also lifts the survival fraction for in-place
    # edits like "weekly"->"biweekly").
    new_start, new_end = _snap_to_words(new_body, new_start, new_end)
    if _word_preserved_fraction(diff, len(new_body), new_start, new_end) < _MIN_PRESERVED:
        return None  # alignment landed on mostly-rewritten text — orphan, don't mislead
    if not new_body[new_start:new_end].strip():
        return None  # only whitespace survived — nothing real to anchor to
    return (new_start, new_end)

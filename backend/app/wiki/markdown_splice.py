"""Targeted-splice checkpoint engine — turn a live ``pycrdt`` Yjs doc back
into markdown text while leaving every byte outside an actually-touched
region identical to the markdown body the session started from.

This is the highest-risk piece of the whole live-doc design: re-serializing
untouched content through the codec risks reformatting it in a way that
silently orphans comment/provenance anchors (``app/wiki/comment_anchor.py``
et al. re-anchor via a textual diff against the *previous* committed body —
a serializer that isn't byte-identical for regions nobody touched reads as
"everything changed" to that diff). See
``Engineering Projects/Agent Wiki Project/design/Co-Editing.md`` for the
full design rationale.

Two pieces:

- ``TouchedTracker`` subscribes to a session's ``Doc`` for its lifetime and
  records, at whatever granularity a change actually occurred, which
  top-level blocks (``markdown_yjs.BLOCK_ID_ATTR``) or table rows
  (``ROW_ID_ATTR``) were mutated — via ``observe_deep``, so it sees every
  mutation regardless of origin (a local edit, or a remote peer's raw Yjs
  update applied by ``pycrdt-websocket``), not just ones that flowed through
  our own code.
- ``checkpoint_body`` walks the live doc's current top-level children once,
  in document order, choosing per block: a byte-verbatim slice of
  ``base_body`` for anything untouched-and-still-present, or a fresh
  ``markdown_yjs.serialize_block``/``serialize_row`` for anything touched,
  new, or (for a table) structurally changed. A block whose id has no match
  in ``base_body`` (created during this session) is always serialized; a
  block present in ``base_body`` but no longer in the live doc is simply
  never emitted — deletion needs no special-casing.
"""

from __future__ import annotations

import difflib

from pycrdt import Doc, Subscription, XmlElement, XmlFragment

from app.wiki.markdown_blocks import BlockRange, top_level_block_ranges
from app.wiki.markdown_yjs import (
    BLOCK_ID_ATTR,
    ROOT_XML_KEY,
    ROW_ID_ATTR,
    build_block_element,
    serialize_block,
    serialize_row,
)

_ROW_TAGS = ("tableRow", "tableSeparator")


class TouchedTracker:
    """Tracks which blocks/rows of a session's live doc have been mutated
    since the tracker was created (or last ``reset``).

    One tracker per active co-edit session, created alongside the session's
    ``Doc`` and reset after each successful checkpoint — so "touched" always
    means "touched since the ``base_body`` a pending checkpoint will diff
    against."
    """

    def __init__(self, doc: Doc) -> None:
        self._root = doc.get(ROOT_XML_KEY, type=XmlFragment)
        self.touched_block_ids: set[str] = set()
        self.touched_row_ids: dict[str, set[str]] = {}
        self._subscription: Subscription = self._root.observe_deep(self._on_events)

    def _on_events(self, events: list[object]) -> None:
        for event in events:
            self._record(event.target)  # type: ignore[attr-defined]

    def _record(self, node: object) -> None:
        block_id, row_id = _classify_target(node)
        if block_id is None:
            # A structural change targeting the root fragment itself (a
            # whole top-level block inserted/deleted) — checkpoint_body
            # handles new/gone blocks by diffing ids against base_body, no
            # tracking needed.
            return
        if row_id is not None:
            self.touched_row_ids.setdefault(block_id, set()).add(row_id)
        else:
            self.touched_block_ids.add(block_id)

    def reset(self) -> None:
        """Clear touched state after a successful checkpoint commits."""
        self.touched_block_ids.clear()
        self.touched_row_ids.clear()

    def stop(self) -> None:
        """Unsubscribe when the session ends (its ``Doc`` is being dropped)."""
        self._root.unobserve(self._subscription)


def _classify_target(node: object) -> tuple[str | None, str | None]:
    """Walk up from a changed node to the top-level block that contains it.

    Returns ``(block_id, row_id)`` — ``row_id`` set only when the change is
    scoped to a single table row/separator, so the caller can splice just
    that row rather than re-serializing the whole table. ``(None, None)``
    when ``node`` has no parent (the root fragment itself — a whole-block
    insert/delete, tracked implicitly, not explicitly). Works unchanged for
    a change inside a ``hardBreak`` leaf — it's just another node to walk up
    from, no different from any other inline child.
    """
    current = node
    row_id: str | None = None
    while True:
        parent = current.parent  # type: ignore[attr-defined]
        if parent is None:
            return None, None
        if isinstance(parent, XmlFragment):
            if not isinstance(current, XmlElement):
                return None, None
            return dict(current.attributes).get(BLOCK_ID_ATTR), row_id
        if isinstance(current, XmlElement) and current.tag in _ROW_TAGS:
            row_id = dict(current.attributes).get(ROW_ID_ATTR)
        current = parent


def checkpoint_body(base_body: str, doc: Doc, tracker: TouchedTracker) -> str:
    """Reconstruct the markdown body to commit at checkpoint time.

    ``base_body`` is the body the live doc was seeded from (session start,
    or the body as of the last checkpoint after a ``tracker.reset()``).

    No separator is ever synthesized between blocks here: each touched/new
    block's own ``serialize_block`` output already supplies its own complete
    boundary (a block with content ends in exactly one newline; an empty one
    contributes nothing), so parts are concatenated directly. There is
    deliberately no "canonical gap" concept anywhere in this module — every
    single newline the user enters is its own block (``BlockKind.BLANK_LINE``
    in ``markdown_blocks.py``), with nothing implicit synthesized on top.
    """
    orig_blocks = top_level_block_ranges(base_body)
    orig_by_id = {b.block_id: b for b in orig_blocks}
    leading_gap_start: dict[str, int] = {}
    prev_end = 0
    for b in orig_blocks:
        leading_gap_start[b.block_id] = prev_end
        prev_end = b.end
    tail_start = prev_end

    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    parts: list[str] = []
    for child in root.children:
        if not isinstance(child, XmlElement):
            raise NotImplementedError(f"unexpected top-level child: {type(child)!r}")
        block_id = dict(child.attributes).get(BLOCK_ID_ATTR)
        orig = orig_by_id.get(block_id) if block_id else None

        if orig is not None and child.tag == "table" and block_id not in tracker.touched_block_ids:
            gap = base_body[leading_gap_start[block_id] : orig.start]
            parts.append(gap + _splice_table(base_body, orig, child, tracker.touched_row_ids.get(block_id, set())))
            continue

        touched = (
            orig is None
            or block_id in tracker.touched_block_ids
            or block_id in tracker.touched_row_ids
        )
        if not touched:
            assert orig is not None and block_id is not None
            parts.append(base_body[leading_gap_start[block_id] : orig.end])
            continue

        parts.append(serialize_block(child))

    parts.append(base_body[tail_start:])
    return "".join(parts)


def restamp_block_ids(doc: Doc, body: str) -> None:
    """Re-number every top-level block's ``_blockId`` (and, for tables, each
    row's ``_rowId``) to match ``top_level_block_ranges(body)``'s own
    positional scheme (``b0``, ``b1``, ... in document order, rows
    ``<block_id>:r<index>``/``<block_id>:sep``) — call once right after a
    checkpoint advances ``base_body`` to ``body`` (i.e. whenever the doc
    itself is kept, or spliced via ``apply_markdown_diff``, rather than
    reseeded fresh from markdown text — see
    ``app/tasks/coedit_checkpoint.py``'s ``_reconcile_room`` fast path and
    ``app/wiki/coedit_checkpoint.py``'s ``checkpoint_session``, the only
    call sites).

    Ids are stamped once when a block is created and never otherwise
    touched, so they silently drift out of sync with ``body``'s own
    (freshly re-derived, every parse) numbering the moment a top-level
    block is inserted or removed anywhere before another block in document
    order. Left unfixed, the next checkpoint's ``checkpoint_body`` matches
    an untouched live block against the *wrong* ``base_body`` range —
    confirmed in review to silently drop or duplicate content (insert a
    block, checkpoint, edit a different block, checkpoint again). A
    reseed (``coedit_room.reseed``, or ``seed_doc_from_markdown`` for a
    diverged snapshot) doesn't need this call — building fresh from
    markdown text via ``top_level_block_ranges`` already assigns ids this
    same way, by construction.

    Naive positional assignment (``doc`` child ``i`` -> ``b{i}``) is only
    correct when ``doc``'s children correspond 1:1, in order, with
    ``top_level_block_ranges(body)``. That's false whenever reparsing
    ``body`` as plain text merges two things the doc still models as
    separate elements — confirmed in review: two adjacent same-kind
    containers (e.g. two ``bulletList`` elements with no blank line between
    them) parse back as *one* CommonMark list, so doc-child-count can
    exceed reparsed-block-count. Positional assignment then silently
    mis-pairs every child from that point on, corrupting a later
    checkpoint's diff exactly like the original bug this function exists to
    fix. Handled here by diffing each child's own serialization against
    each reparsed block's raw text (``difflib``, not a bare index) — an
    "equal"/"replace" run of one-or-more children maps onto whichever
    reparsed block(s) it actually corresponds to, so multiple children can
    correctly collapse onto the same id instead of drifting apart.
    """
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    children = [c for c in root.children if isinstance(c, XmlElement)]
    blocks = top_level_block_ranges(body)
    old_texts = [serialize_block(c) for c in children]
    new_texts = [body[b.start : b.end] for b in blocks]
    matcher = difflib.SequenceMatcher(a=old_texts, b=new_texts, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            continue  # a reparsed block with no corresponding doc child — nothing to restamp
        for offset, child in enumerate(children[i1:i2]):
            j = min(j1 + offset, j2 - 1) if j2 > j1 else max(j1 - 1, 0)
            block_id = f"b{j}"
            if dict(child.attributes).get(BLOCK_ID_ATTR) != block_id:
                child.attributes[BLOCK_ID_ATTR] = block_id
            if child.tag == "table":
                _restamp_rows(child, block_id)


def _restamp_rows(table_el: XmlElement, block_id: str) -> None:
    row_index = 0
    for row_child in table_el.children:
        if not isinstance(row_child, XmlElement):
            continue
        if row_child.tag == "tableSeparator":
            row_id = f"{block_id}:sep"
        else:
            row_id = f"{block_id}:r{row_index}"
            row_index += 1
        if dict(row_child.attributes).get(ROW_ID_ATTR) != row_id:
            row_child.attributes[ROW_ID_ATTR] = row_id


def _splice_table(
    base_body: str, orig: BlockRange, table_el: XmlElement, touched_row_ids: set[str]
) -> str:
    """Row-level splice for an untouched-at-the-structure table: rows not in
    ``touched_row_ids`` are sliced verbatim from ``base_body``; touched rows
    (and the header separator, if touched) are re-serialized."""
    orig_rows = {r.row_id: r for r in orig.rows}
    if orig.separator is not None:
        orig_rows[orig.separator.row_id] = orig.separator
    parts: list[str] = []
    for row_el in table_el.children:
        row_id = dict(row_el.attributes).get(ROW_ID_ATTR)
        orig_row = orig_rows.get(row_id) if row_id else None
        if orig_row is None or row_id in touched_row_ids:
            parts.append(serialize_row(row_el))
        else:
            parts.append(base_body[orig_row.start : orig_row.end])
    return "".join(parts)


def apply_markdown_diff(doc: Doc, old_body: str, new_body: str) -> bool:
    """Mutate ``doc``'s top-level children in place so it represents
    ``new_body``, preserving the existing CRDT lineage (item/client ids) for
    every block whose text is unchanged, instead of discarding the whole
    lineage the way ``seed_doc_from_markdown(new_body)`` would.

    For the one caller this exists for
    (``coedit_checkpoint.checkpoint_session``'s diverged branch — the AI/3-way
    merge folded in content from an out-of-band commit, so the committed
    result differs from what ``doc`` held): re-seeding a fresh ``Doc`` from
    ``new_body`` text was confirmed in review to silently drop any
    concurrent edit logged during the checkpoint's git-commit-plus-merge
    window. Those updates were generated against ``doc``'s *current*
    lineage (still live and accepting edits the whole time — checkpointing
    never touches any process's live room) — applying them to an unrelated
    fresh lineage is a structural no-op, not an error, so the edit just
    silently vanishes. Splicing onto the existing lineage instead means any
    such update stays valid.

    Only replaces the specific top-level blocks that actually changed
    (found via ``difflib`` over each side's block texts — reusing
    ``top_level_block_ranges`` on both, not the doc's own possibly-drifted
    ids), built fresh via ``markdown_yjs.build_block_element``; every
    unchanged block's existing ``XmlElement`` is left untouched in place
    (pycrdt refuses to re-insert an already-integrated element, so this
    also couldn't be done as "clear everything, rebuild, keeping some old
    objects" even if we wanted to). Does not restamp block/row ids — call
    ``restamp_block_ids(doc, new_body)`` after, same as every other caller
    that keeps ``doc`` rather than reseeding.

    Returns ``False`` (``doc`` left completely untouched) when ``doc``'s
    children don't correspond 1:1, in order, with
    ``top_level_block_ranges(old_body)`` — the same positional drift
    ``restamp_block_ids`` guards against — since without that
    correspondence there's no reliable way to know which existing child a
    given old block-range belongs to. The caller should fall back to a
    fresh reseed in that case: no worse than before this function existed,
    just not improved for that (rarer, compounding-bug) case.
    """
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    children = [c for c in root.children if isinstance(c, XmlElement)]
    old_blocks = top_level_block_ranges(old_body)
    if len(children) != len(old_blocks):
        return False

    new_blocks = top_level_block_ranges(new_body)
    old_texts = [old_body[b.start : b.end] for b in old_blocks]
    new_texts = [new_body[b.start : b.end] for b in new_blocks]
    opcodes = difflib.SequenceMatcher(a=old_texts, b=new_texts, autojunk=False).get_opcodes()

    # Reverse order: mutating the tail of root.children first keeps every
    # not-yet-processed i1/i2 (computed against the pre-mutation sequence)
    # valid — an insert/delete earlier in the list would otherwise shift
    # every later index out from under the remaining opcodes.
    with doc.transaction():
        for tag, i1, i2, j1, j2 in reversed(opcodes):
            if tag == "equal":
                continue
            del root.children[i1:i2]
            insert_at = i1
            for block in new_blocks[j1:j2]:
                el, finishers = build_block_element(new_body, block)
                root.children.insert(insert_at, el)
                insert_at += 1
                for finish in finishers:
                    finish()
    return True

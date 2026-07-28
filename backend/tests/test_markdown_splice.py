"""Tests for app/wiki/markdown_splice.py — the targeted-splice checkpoint
engine. The byte-stability assertions here are the riskiest piece of the
AgentWikiEditor design (see the Co-Editing design doc): checkpointing a
session where only one region changed must leave every other byte of the
committed body identical to pre-session HEAD.
"""

from __future__ import annotations

from pycrdt import Doc, XmlElement, XmlFragment, XmlText

from app.wiki.markdown_splice import (
    TouchedTracker,
    apply_markdown_diff,
    checkpoint_body,
    restamp_block_ids,
)
from app.wiki.markdown_yjs import BLOCK_ID_ATTR, ROOT_XML_KEY, seed_doc_from_markdown

_SAMPLE = """# Heading

Paragraph one has some **bold** text.

- item one
- item two

| a | b |
| --- | --- |
| 1 | 2 |
| 3 | 4 |

Final paragraph.
"""


def _root(doc):
    return doc.get(ROOT_XML_KEY, type=XmlFragment)


def test_no_edits_round_trips_byte_identical() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    tracker = TouchedTracker(doc)
    assert checkpoint_body(_SAMPLE, doc, tracker) == _SAMPLE


def test_blank_line_count_round_trips_byte_identical() -> None:
    """A run of blank lines between two blocks is invisible to markdown-it
    (no token at all - see BlockKind.BLANK_LINE's docstring), so this is the
    regression test for a real bug: reopening a page used to silently
    collapse any blank-line count beyond the implicit single-line default.
    Every one of these must round-trip an untouched session byte-for-byte,
    same guarantee as every other block kind."""
    bodies = [
        "a\n\na\n",  # the canonical single-blank-line case - no BLANK_LINE blocks at all
        "a\n\n\na\n",  # one extra blank line
        "a\n\n\n\n\n\n\na\n",  # several extra blank lines
        "a\n\n\na",  # no trailing newline on the file itself
        "# Heading\n\n\n\nParagraph.\n",  # blank-line run after a non-paragraph block
    ]
    for body in bodies:
        doc = seed_doc_from_markdown(body)
        tracker = TouchedTracker(doc)
        assert checkpoint_body(body, doc, tracker) == body, f"failed for {body!r}"


def test_split_paragraph_with_cleared_block_id_does_not_duplicate() -> None:
    """Regression test for the "a\\na\\na" -> 5 a's bug: ProseMirror's
    default node-split copies a node's own attrs onto both halves, so
    without the frontend's UniqueBlockIdentity safety net
    (frontend/src/lib/editor/blocks.ts) both resulting top-level paragraphs
    would claim the same _blockId, and this module's block_id -> BlockRange
    lookup would resolve both to the same original range - duplicating
    content. This asserts the *fixed* shape (the second half's _blockId
    cleared to None, exactly what that safety net produces) round-trips
    correctly rather than duplicating "a"."""
    base_body = "a\na\na\n"  # one paragraph, three lines via soft breaks
    doc = seed_doc_from_markdown(base_body)
    tracker = TouchedTracker(doc)
    root = _root(doc)

    with doc.transaction():
        para = root.children[0]
        text_node = para.children[0]
        full_text = text_node.to_py()  # "a\na\na"
        before, after = full_text[:3], full_text[3:]  # "a\na", "\na"
        del text_node[0 : len(full_text)]
        text_node += before
        # No _blockId/_nl attrs - matches what UniqueBlockIdentity produces
        # for the newly-split-off second half.
        new_para = XmlElement("paragraph", {}, contents=[XmlText(after)])
        root.children.insert(1, new_para)

    result = checkpoint_body(base_body, doc, tracker)
    assert result.count("a") == 3, f"expected 3 a's, got {result.count('a')}: {result!r}"


def test_brand_new_adjacent_paragraphs_get_no_synthesized_gap() -> None:
    """No newline is ever synthesized between blocks, deliberately (the
    "no magic" architecture - see markdown_blocks.BlockKind.BLANK_LINE and
    this module's docstring): three brand-new top-level paragraphs typed via
    "a, Enter, a, Enter, a" with no blank-line spacer between any of them
    serialize with a bare newline between each, exactly what was typed, no
    more. Confirmed against the first checkpoint of a brand-new page
    (`base_body=""`, matching `change_kind=CREATE`)."""
    doc = Doc()
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    with doc.transaction():
        for _ in range(3):
            root.children.append(XmlElement("paragraph", {}, contents=[XmlText("a")]))
    tracker = TouchedTracker(doc)

    result = checkpoint_body("", doc, tracker)

    assert result == "a\na\na\n", f"got {result!r}"
    # Reparsing must still keep them as three separate blocks: the three
    # lines form one soft-broken paragraph_open token, but
    # top_level_block_ranges splits every soft-break line into its own
    # block, so this still converges back to the original structure.
    reparsed = seed_doc_from_markdown(result)
    reparsed_root = _root(reparsed)
    assert len(reparsed_root.children) == 3
    assert [c.children[0].to_py() for c in reparsed_root.children] == ["a", "a", "a"]


def test_fresh_empty_spacer_paragraph_contributes_exactly_one_newline() -> None:
    """A brand-new *empty* paragraph (a real blank-line spacer from pressing
    Enter twice, not yet seeded from committed markdown) contributes exactly
    one newline and nothing else - same as every other block, empty or not
    (see serialize_block's docstring: there's no separate gap-synthesis
    mechanism to lean on, so an empty block's own newline is the only thing
    that can represent it as a real blank line)."""
    doc = Doc()
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    with doc.transaction():
        for text in ["a", "a", "a", "", "a"]:
            root.children.append(XmlElement("paragraph", {}, contents=[XmlText(text)]))
    tracker = TouchedTracker(doc)

    result = checkpoint_body("", doc, tracker)

    assert result == "a\na\na\n\na\n", f"got {result!r}"
    reparsed = seed_doc_from_markdown(result)
    reparsed_root = _root(reparsed)
    assert [c.children[0].to_py() if c.children else None for c in reparsed_root.children] == [
        "a",
        "a",
        "a",
        "",
        "a",
    ]


def test_editing_one_paragraph_leaves_every_other_byte_identical() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)

    para = root.children[2]  # "Paragraph one has some **bold** text." (children[1] is the blank-line block)
    with doc.transaction():
        para.children[0].insert(0, "EDITED: ")

    new_body = checkpoint_body(_SAMPLE, doc, tracker)

    assert "EDITED: Paragraph one" in new_body
    # Every block except the edited paragraph is untouched — byte-identical
    # to the source, including the surrounding heading/list/table/paragraph
    # and the blank-line gaps between them.
    heading_and_gap = "# Heading\n\n"
    assert new_body.startswith(heading_and_gap)
    tail = _SAMPLE[_SAMPLE.index("- item one") :]
    assert new_body.endswith(tail)


def test_editing_table_cell_only_touches_that_row() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)

    table = next(c for c in root.children if c.tag == "table")
    row0 = table.children[0]  # header row "| a | b |"
    with doc.transaction():
        row0.children[0].insert(0, "EDITED ")

    new_body = checkpoint_body(_SAMPLE, doc, tracker)

    assert "EDITED | a | b |" in new_body
    # The rest of the table (separator + both body rows) is untouched.
    assert "| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n" in new_body
    # Everything before and after the table is untouched too.
    before_table = _SAMPLE[: _SAMPLE.index("| a | b |")]
    assert new_body.startswith(before_table)
    after_table = _SAMPLE[_SAMPLE.index("Final paragraph.") :]
    assert new_body.endswith(after_table)


def test_editing_two_separate_blocks_leaves_middle_untouched() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)

    heading = root.children[0]
    final_para = list(root.children)[-1]
    with doc.transaction():
        heading.children[0].insert(0, "NEW ")
    with doc.transaction():
        final_para.children[0].insert(0, "NEW ")

    new_body = checkpoint_body(_SAMPLE, doc, tracker)

    middle = _SAMPLE[_SAMPLE.index("Paragraph one") : _SAMPLE.index("Final paragraph.")]
    assert middle in new_body
    assert "NEW Heading" in new_body
    assert "NEW Final paragraph." in new_body


def test_inserting_a_new_top_level_block_leaves_existing_blocks_untouched() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)

    new_para = XmlElement("paragraph", {"_blockId": "new0"}, contents=[])
    with doc.transaction():
        root.children.append(new_para)
    from pycrdt import XmlText

    with doc.transaction():
        new_para.children.append(XmlText("Brand new paragraph."))

    new_body = checkpoint_body(_SAMPLE, doc, tracker)

    assert new_body.startswith(_SAMPLE.rstrip("\n"))
    assert "Brand new paragraph." in new_body


def test_deleting_a_top_level_block_omits_it_and_leaves_rest_untouched() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)

    list_block = next(c for c in root.children if c.tag == "bulletList")
    with doc.transaction():
        idx = list(root.children).index(list_block)
        del root.children[idx]

    new_body = checkpoint_body(_SAMPLE, doc, tracker)

    assert "item one" not in new_body
    assert "# Heading" in new_body
    assert "| a | b |" in new_body
    assert "Final paragraph." in new_body


def test_reset_clears_touched_state_for_next_checkpoint() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)

    para = root.children[2]  # "Paragraph one has some **bold** text." (children[1] is the blank-line block)
    with doc.transaction():
        para.children[0].insert(0, "FIRST: ")
    first_checkpoint = checkpoint_body(_SAMPLE, doc, tracker)
    tracker.reset()

    # No further edits — the second checkpoint against the new base should be
    # byte-identical (nothing touched since reset).
    second_checkpoint = checkpoint_body(first_checkpoint, doc, tracker)
    assert second_checkpoint == first_checkpoint


def test_concurrent_editing_convergence_via_update_apply() -> None:
    """Two peers editing the same doc via raw Yjs updates (the actual
    pycrdt-websocket wire path, not our own API) converge, and the
    tracker on the receiving end still sees the touch — proving touched
    tracking works for updates applied from bytes, not just local edits."""
    from pycrdt import Doc

    doc_a = seed_doc_from_markdown(_SAMPLE)
    doc_b = Doc()
    doc_b.apply_update(doc_a.get_update())

    tracker_b = TouchedTracker(doc_b)
    root_a = _root(doc_a)
    para_a = root_a.children[2]  # "Paragraph one has some **bold** text." (children[1] is the blank-line block)
    with doc_a.transaction():
        para_a.children[0].insert(0, "FROM PEER A: ")

    update = doc_a.get_update()
    doc_b.apply_update(update)

    new_body = checkpoint_body(_SAMPLE, doc_b, tracker_b)
    assert "FROM PEER A: Paragraph one" in new_body
    assert new_body.startswith("# Heading\n\n")


_NESTED_SAMPLE = """# Heading

Paragraph one.

- item one
- item two
- item three

> A quote paragraph.

```python
x = 1
```

Final paragraph.
"""


def test_editing_deep_inside_a_list_item_touches_only_the_whole_list() -> None:
    """Block-level granularity for containers (per the splice-granularity
    decision — table rows are the one exception): an edit anywhere inside a
    list, however deeply nested, marks the whole top-level list touched,
    everything else stays untouched."""
    doc = seed_doc_from_markdown(_NESTED_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)
    list_block = next(c for c in root.children if c.tag == "bulletList")
    item2_para = list_block.children[1].children[0]
    assert item2_para.tag == "paragraph"
    with doc.transaction():
        item2_para.children[0].insert(0, "EDITED ")

    new_body = checkpoint_body(_NESTED_SAMPLE, doc, tracker)
    assert "EDITED item two" in new_body
    before_list = _NESTED_SAMPLE[: _NESTED_SAMPLE.index("- item one")]
    assert new_body.startswith(before_list)
    after_list = _NESTED_SAMPLE[_NESTED_SAMPLE.index("> A quote") :]
    assert new_body.endswith(after_list)


def test_editing_inside_blockquote_leaves_everything_before_it_untouched() -> None:
    doc = seed_doc_from_markdown(_NESTED_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)
    bq = next(c for c in root.children if c.tag == "blockquote")
    bq_para = bq.children[0]
    with doc.transaction():
        bq_para.children[0].insert(0, "EDITED ")

    new_body = checkpoint_body(_NESTED_SAMPLE, doc, tracker)
    assert "EDITED A quote" in new_body
    before_bq = _NESTED_SAMPLE[: _NESTED_SAMPLE.index("> A quote")]
    assert new_body.startswith(before_bq)


def test_editing_inside_code_block_leaves_everything_before_it_untouched() -> None:
    doc = seed_doc_from_markdown(_NESTED_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)
    code = next(c for c in root.children if c.tag == "codeBlock")
    with doc.transaction():
        code.children[0].insert(0, "y = 2\n")

    new_body = checkpoint_body(_NESTED_SAMPLE, doc, tracker)
    assert "y = 2\nx = 1" in new_body
    assert new_body.startswith(_NESTED_SAMPLE[: _NESTED_SAMPLE.index("```python")])


# --- restamp_block_ids / apply_markdown_diff --------------------------------- #


def test_restamp_assigns_shared_id_when_reparse_merges_adjacent_lists() -> None:
    """Regression test (review): two doc children that reparse into *one*
    CommonMark block (adjacent same-kind containers with no blank line
    between them — plain markdown text can't distinguish "two lists" from
    "one list with more items") must both land on that one block's id, not
    drift onto distinct positional ids (b0, b1) that no longer correspond
    to anything in the next base_body's own reparse — the exact drift that
    caused checkpoint_body to duplicate content (see the module docstring
    on restamp_block_ids)."""
    from app.wiki.markdown_blocks import top_level_block_ranges
    from app.wiki.markdown_yjs import build_block_element

    doc = Doc()
    root = _root(doc)
    new_list_body, old_list_body = "- new\n", "- old\n"
    with doc.transaction():
        for body, blocks in (
            (new_list_body, top_level_block_ranges(new_list_body)),
            (old_list_body, top_level_block_ranges(old_list_body)),
        ):
            el, finishers = build_block_element(body, blocks[0])
            root.children.append(el)
            for f in finishers:
                f()

    cp1_body = "- new\n- old\n"
    # Sanity: this is the drift condition itself — one reparsed block, two
    # doc children.
    assert len(top_level_block_ranges(cp1_body)) == 1
    assert len(root.children) == 2

    restamp_block_ids(doc, cp1_body)
    ids = [dict(c.attributes).get(BLOCK_ID_ATTR) for c in root.children]
    assert ids == ["b0", "b0"], ids

    # Editing only the second child must not duplicate the first — both
    # children sharing "b0" means touching either marks the shared id
    # touched, so checkpoint_body re-serializes both (harmless for the
    # unedited one) instead of slicing child 0 verbatim from a stale range
    # that (pre-fix) covered the *whole* former text.
    tracker = TouchedTracker(doc)
    old_item_text = root.children[1].children[0].children[0].children[0]
    with doc.transaction():
        del old_item_text[0:3]
        old_item_text += "EDITED"

    cp2_body = checkpoint_body(cp1_body, doc, tracker)
    assert cp2_body == "- new\n- EDITED\n", cp2_body


def test_apply_markdown_diff_preserves_lineage_of_untouched_blocks() -> None:
    """The whole point of apply_markdown_diff over a fresh
    seed_doc_from_markdown reseed: a block the diff doesn't touch keeps its
    *original* CRDT item ids, so a Yjs update logged against the pre-diff
    doc still integrates after the diff runs. (Not asserted via Python
    object identity on ``root.children[0]`` — pycrdt's XmlChildrenView
    hands back a fresh wrapper object on every access regardless of
    mutation, confirmed directly, so "is" across two separate accesses
    proves nothing either way; the late-update-integrates check below is
    the real, meaningful assertion.)"""
    old_body = "one\n\ntwo\n\nthree\n"
    doc = seed_doc_from_markdown(old_body)

    new_body = "one\n\nTWO\n\nthree\n"
    assert apply_markdown_diff(doc, old_body, new_body) is True
    assert checkpoint_body(new_body, doc, TouchedTracker(doc)) == new_body

    # A late Yjs update generated against the pre-diff doc's lineage — e.g.
    # a concurrent edit to the untouched "one" paragraph landing in the
    # window between reading `old_body` and this diff running (the review
    # repro) — must still integrate correctly afterward, since the
    # untouched paragraph's own item ids never changed.
    other_doc = Doc()
    other_doc.apply_update(seed_doc_from_markdown(old_body).get_update())
    other_root = _root(other_doc)
    with other_doc.transaction():
        other_root.children[0].children[0].insert(0, "EDITED-")
    late_update = other_doc.get_update()

    doc.apply_update(late_update)
    assert checkpoint_body(new_body, doc, TouchedTracker(doc)) == "EDITED-one\n\nTWO\n\nthree\n"


def test_apply_markdown_diff_returns_false_on_child_count_drift() -> None:
    """When doc's children don't correspond 1:1 to a fresh parse of their
    own old_body (the restamp drift condition), there's no safe pairing —
    the caller must fall back to a fresh reseed rather than risk splicing
    the wrong replacement onto the wrong child."""
    from app.wiki.markdown_blocks import top_level_block_ranges
    from app.wiki.markdown_yjs import build_block_element

    doc = Doc()
    root = _root(doc)
    with doc.transaction():
        for body in ("- new\n", "- old\n"):
            el, finishers = build_block_element(body, top_level_block_ranges(body)[0])
            root.children.append(el)
            for f in finishers:
                f()
    old_body = "- new\n- old\n"  # 2 doc children, but this reparses to 1 block
    assert apply_markdown_diff(doc, old_body, "- new\n- old\n- more\n") is False
    assert len(root.children) == 2  # untouched — no partial mutation on the bail-out path


def test_editing_paragraph_after_link_reference_definition_preserves_it() -> None:
    """Regression test (review): touching the paragraph *after* a link
    reference definition used to delete the definition and leave a
    corrupted fragment behind (the definition produces no markdown-it
    token, so it was previously miscounted as blank-line filler — see
    test_markdown_blocks.py). Editing the trailing paragraph must leave the
    definition's own text completely untouched."""
    base_body = "See [spec][ref] here.\n\n[ref]: https://example.com/spec\n\nTrailing paragraph.\n"
    doc = seed_doc_from_markdown(base_body)
    tracker = TouchedTracker(doc)
    root = _root(doc)
    # Not root.children[-1]: pycrdt's XmlChildrenView.__getitem__ passes a
    # negative index straight through to the Rust binding without
    # translating it to a positive one first, raising OverflowError
    # (confirmed directly against the installed pycrdt) rather than
    # indexing from the end the way a plain Python list would.
    trailing_para = root.children[len(root.children) - 1]
    assert trailing_para.tag == "paragraph"
    with doc.transaction():
        trailing_para.children[0].insert(0, "EDITED ")

    new_body = checkpoint_body(base_body, doc, tracker)
    assert "[ref]: https://example.com/spec\n" in new_body
    assert "EDITED Trailing paragraph.\n" in new_body
    assert new_body == (
        "See [spec][ref] here.\n\n[ref]: https://example.com/spec\n\nEDITED Trailing paragraph.\n"
    )

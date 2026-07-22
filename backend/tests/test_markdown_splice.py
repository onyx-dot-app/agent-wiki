"""Tests for app/wiki/markdown_splice.py — the targeted-splice checkpoint
engine. The byte-stability assertions here are the single riskiest piece of
the onyx-editor migration (see plans/onyx-editor.md's risk register #1):
checkpointing a session where only one region changed must leave every other
byte of the committed body identical to pre-session HEAD.
"""

from __future__ import annotations

from pycrdt import XmlElement, XmlFragment

from app.wiki.markdown_splice import TouchedTracker, checkpoint_body
from app.wiki.markdown_yjs import ROOT_XML_KEY, seed_doc_from_markdown

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


def test_editing_one_paragraph_leaves_every_other_byte_identical() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    tracker = TouchedTracker(doc)
    root = _root(doc)

    para = root.children[1]  # "Paragraph one has some **bold** text."
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

    new_para = XmlElement(
        "paragraph", {"_blockId": "new0", "_nl": "1"}, contents=[]
    )
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

    para = root.children[1]
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
    para_a = root_a.children[1]
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

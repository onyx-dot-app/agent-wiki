"""Tests for app/wiki/markdown_yjs.py — the markdown <-> Yjs codec."""

from __future__ import annotations

import pytest
from pycrdt import XmlFragment

from app.wiki.markdown_yjs import (
    BLOCK_ID_ATTR,
    ROOT_XML_KEY,
    ROW_ID_ATTR,
    find_by_block_id,
    find_by_row_id,
    reconstruct_body,
    seed_doc_from_markdown,
)

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


def test_seed_doc_produces_one_element_per_top_level_block() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    root = _root(doc)
    tags = [c.tag for c in root.children]
    assert tags == ["heading", "paragraph", "bulletList", "table", "paragraph"]


def test_block_ids_are_positional_and_stable() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    root = _root(doc)
    ids = [dict(c.attributes).get(BLOCK_ID_ATTR) for c in root.children]
    assert ids == ["b0", "b1", "b2", "b3", "b4"]


def test_reconstruct_body_round_trips_simple_body() -> None:
    body = "# Heading\n\nJust a paragraph.\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_reconstruct_body_round_trips_bold_italic_code_link() -> None:
    body = "A **bold** and *italic* and `code` and [a link](https://x.example) end.\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_reconstruct_body_round_trips_table() -> None:
    body = "| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_reconstruct_body_round_trips_code_quote_and_raw_thematic_break() -> None:
    body = "Intro.\n\n```python\nx = 1\n```\n\n> a quote\n\n---\n\nOutro.\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_bullet_list_structure_and_nesting() -> None:
    doc = seed_doc_from_markdown("- outer one\n  - inner\n- outer two\n")
    root = _root(doc)
    lst = root.children[0]
    assert lst.tag == "bulletList"
    items = list(lst.children)
    assert [i.tag for i in items] == ["listItem", "listItem"]
    # First item: a paragraph plus a nested bulletList.
    first_item_children = list(items[0].children)
    assert [c.tag for c in first_item_children] == ["paragraph", "bulletList"]
    nested = first_item_children[1]
    assert nested.children[0].tag == "listItem"


def test_ordered_list_start_attribute_and_serialization() -> None:
    body = "3. first\n4. second\n"
    doc = seed_doc_from_markdown(body)
    root = _root(doc)
    lst = root.children[0]
    assert lst.tag == "orderedList"
    assert dict(lst.attributes)["start"] == "3"
    # Content is preserved; loose-vs-tight spacing is not (documented,
    # semantics-preserving normalization — see markdown_yjs.py).
    got = reconstruct_body(doc)
    assert "3. first" in got and "4. second" in got


def test_task_list_structure_and_checked_attribute() -> None:
    doc = seed_doc_from_markdown("- [ ] todo\n- [x] done\n")
    root = _root(doc)
    lst = root.children[0]
    assert lst.tag == "taskList"
    items = list(lst.children)
    assert [i.tag for i in items] == ["taskItem", "taskItem"]
    assert dict(items[0].attributes)["checked"] == "false"
    assert dict(items[1].attributes)["checked"] == "true"
    # The checkbox marker itself must not leak into the item's paragraph text.
    assert items[0].children[0].children[0].to_py() == "todo"


def test_mixed_task_and_plain_items_stays_plain_bullet_list() -> None:
    """Tiptap's taskList schema requires uniformly-taskItem children — a
    list where only some items carry a checkbox marker can't become one, so
    it stays a plain bulletList (the marker text is then literal, same as
    pre-checkbox-support behavior)."""
    doc = seed_doc_from_markdown("- [ ] marked\n- unmarked\n")
    root = _root(doc)
    lst = root.children[0]
    assert lst.tag == "bulletList"
    assert [i.tag for i in lst.children] == ["listItem", "listItem"]


def test_task_list_reserialization_is_content_correct_and_idempotent() -> None:
    body = "- [ ] buy milk\n- [x] walk dog\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    twice = reconstruct_body(seed_doc_from_markdown(once))
    assert once == twice
    assert "- [ ] buy milk" in once and "- [x] walk dog" in once


def test_task_list_nesting_and_inside_blockquote() -> None:
    body = "- [ ] top\n  - [x] nested\n\n> - [ ] quoted\n"
    doc = seed_doc_from_markdown(body)
    root = _root(doc)
    top_list = root.children[0]
    assert top_list.tag == "taskList"
    nested = list(top_list.children[0].children)
    assert [c.tag for c in nested] == ["paragraph", "taskList"]

    bq = root.children[1]
    assert bq.tag == "blockquote"
    assert bq.children[0].tag == "taskList"


def test_list_reserialization_is_content_correct_and_idempotent() -> None:
    """The tight->loose list normalization (see markdown_yjs.py) means
    output isn't byte-identical to a tight-list source, but re-parsing the
    output must be a no-op (a well-defined normal form, not drift)."""
    body = "- item one\n- item two\n- item three\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    twice = reconstruct_body(seed_doc_from_markdown(once))
    assert once == twice
    assert "item one" in once and "item two" in once and "item three" in once


def test_blockquote_multi_paragraph_round_trips_exactly() -> None:
    body = "> para one\n>\n> para two\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_blockquote_containing_list() -> None:
    doc = seed_doc_from_markdown("> - a\n> - b\n")
    root = _root(doc)
    bq = root.children[0]
    assert bq.tag == "blockquote"
    inner_list = bq.children[0]
    assert inner_list.tag == "bulletList"
    assert len(list(inner_list.children)) == 2


def test_code_block_language_and_content_stored_without_fence_syntax() -> None:
    doc = seed_doc_from_markdown("```python\nx = 1\ny = 2\n```\n")
    root = _root(doc)
    block = root.children[0]
    assert block.tag == "codeBlock"
    attrs = dict(block.attributes)
    assert attrs["language"] == "python"
    assert block.children[0].to_py() == "x = 1\ny = 2\n"


def test_code_block_round_trips_exactly_when_fenced() -> None:
    body = "```python\nx = 1\ny = 2\n```\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_indented_code_block_becomes_fenced_but_content_preserved() -> None:
    body = "    x = 1\n    y = 2\n"
    doc = seed_doc_from_markdown(body)
    got = reconstruct_body(doc)
    assert got == "```\nx = 1\ny = 2\n```\n"
    # Idempotent: re-parsing the fenced output is a no-op.
    assert reconstruct_body(seed_doc_from_markdown(got)) == got


def test_code_block_fence_extends_past_backticks_in_content() -> None:
    doc = seed_doc_from_markdown("```\nhas ``` inside\n```\n")
    root = _root(doc)
    block = root.children[0]
    assert block.children[0].to_py() == "has ``` inside\n"
    got = reconstruct_body(doc)
    # Must round-trip back to the same content when re-parsed (the fence
    # itself is allowed to differ in length, just must be valid).
    doc2 = seed_doc_from_markdown(got)
    assert doc2.get(ROOT_XML_KEY, type=XmlFragment).children[0].children[0].to_py() == (
        "has ``` inside\n"
    )


def test_heading_level_preserved() -> None:
    doc = seed_doc_from_markdown("### Sub-heading\n\nBody.\n")
    root = _root(doc)
    heading = root.children[0]
    assert dict(heading.attributes)["level"] == "3"
    assert reconstruct_body(doc) == "### Sub-heading\n\nBody.\n"


def test_find_by_block_id() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    found = find_by_block_id(doc, "b1")
    assert found is not None
    assert found.tag == "paragraph"
    assert find_by_block_id(doc, "nonexistent") is None


def test_find_by_row_id() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    root = _root(doc)
    table = next(c for c in root.children if c.tag == "table")
    first_row_id = dict(table.children[0].attributes)[ROW_ID_ATTR]

    found = find_by_row_id(doc, first_row_id)
    assert found is not None
    assert dict(found.attributes)[ROW_ID_ATTR] == first_row_id
    assert find_by_row_id(doc, "nonexistent") is None


def test_unrecognized_inline_construct_raises_not_implemented() -> None:
    # Hard line breaks (backslash) aren't in _KNOWN_INLINE_TYPES.
    body = "A line\\\nbreak.\n"
    with pytest.raises(NotImplementedError):
        seed_doc_from_markdown(body)


def test_mark_edit_via_format_round_trips() -> None:
    """Editing a mark through pycrdt's XmlText.format (not just seeding from
    markdown) still serializes correctly — the live-editing path, not just
    the initial parse."""
    doc = seed_doc_from_markdown("Plain text here.\n")
    root = _root(doc)
    para = root.children[0]
    xt = para.children[0]
    with doc.transaction():
        xt.format(0, 5, {"bold": True})
    assert reconstruct_body(doc) == "**Plain** text here.\n"

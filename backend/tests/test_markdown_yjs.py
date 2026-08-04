"""Tests for app/wiki/markdown_yjs.py — the markdown <-> Yjs codec."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pycrdt import Doc, XmlElement, XmlFragment, XmlText

from app.wiki.markdown_blocks import gfm_parser
from app.wiki.markdown_yjs import (
    BLOCK_ID_ATTR,
    ROOT_XML_KEY,
    ROW_ID_ATTR,
    find_by_block_id,
    find_by_row_id,
    _inline_runs,
    reconstruct_body,
    reconstruct_body_with_block_map,
    seed_doc_from_markdown,
    serialize_block,
    serialize_row,
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
    # Every blank line between blocks is its own element too (an empty
    # paragraph, BlockKind.BLANK_LINE's element shape) - no newline is ever
    # an implicit, "free" gap.
    assert tags == [
        "heading",
        "paragraph",
        "paragraph",
        "paragraph",
        "bulletList",
        "paragraph",
        "table",
        "paragraph",
        "paragraph",
    ]


def test_block_ids_are_positional_and_stable() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    root = _root(doc)
    ids = [dict(c.attributes).get(BLOCK_ID_ATTR) for c in root.children]
    assert ids == ["b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8"]


def test_reconstruct_body_round_trips_simple_body() -> None:
    body = "# Heading\n\nJust a paragraph.\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_empty_heading_does_not_raise() -> None:
    """`# ` with no title text after it — a real state while editing (type
    `#`, hit space, haven't typed a title yet), not a hypothetical. Used to
    raise StopIteration: parsing the stripped (empty) heading text standalone
    produces zero markdown-it tokens at all, unlike parsing the unstripped
    `"# "` line, which still produces an `inline` token with empty content."""
    doc = seed_doc_from_markdown("# \n")
    root = _root(doc)
    assert root.children[0].tag == "heading"
    assert reconstruct_body(doc) == "# \n"


def test_reconstruct_body_round_trips_bold_italic_code_link() -> None:
    body = "A **bold** and *italic* and `code` and [a link](https://x.example) end.\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_bare_image_round_trips_exactly() -> None:
    body = "![alt text](/api/wiki/media/abc123)\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_image_with_title_round_trips_exactly() -> None:
    body = '![alt](s.png "a \\"quoted\\" title")\n'
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_image_with_escaped_bracket_in_alt_round_trips_exactly_and_is_idempotent() -> None:
    body = "![esc\\]bracket](s.png)\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    assert once == body
    twice = reconstruct_body(seed_doc_from_markdown(once))
    assert twice == once


def test_image_with_escaped_emphasis_like_alt_round_trips_exactly_and_is_idempotent() -> None:
    body = "![a \\*x\\* b](s.png)\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    assert once == body
    twice = reconstruct_body(seed_doc_from_markdown(once))
    assert twice == once


def test_image_src_fragment_is_stored_verbatim_and_round_trips_exactly() -> None:
    body = "![pic](img.png#w=640)\n"
    doc = seed_doc_from_markdown(body)
    root = _root(doc)
    para = root.children[0]
    image = para.children[0]
    assert isinstance(image, XmlElement)
    assert dict(image.attributes)["src"] == "img.png#w=640"
    assert reconstruct_body(doc) == body


def test_image_mid_paragraph_round_trips_exactly() -> None:
    body = "Before ![alt](img.png) after.\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_multiple_images_in_one_paragraph_round_trip_exactly() -> None:
    body = "![a](a.png) middle ![b](b.png)\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_image_adjacent_to_link_and_emphasis_round_trips_exactly() -> None:
    body = "[link](https://x.example)![img](i.png)*em*\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_image_inside_emphasis_is_content_correct_and_idempotent() -> None:
    # A leaf (image) splitting an emphasized run forces each side to re-wrap
    # independently. Edge whitespace must land outside the "*" markers via
    # _wrap_with_delimiter or the halves stop re-parsing as emphasis.
    body = "*before ![a](i.png) after*\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    twice = reconstruct_body(seed_doc_from_markdown(once))
    assert once != body
    assert once == twice
    assert "before" in once and "after" in once
    assert "![a](i.png)" in once
    assert "*" in once


def test_image_in_list_item_and_blockquote_survives_round_trip() -> None:
    body = "- item ![a](i.png)\n\n> quote ![b](q.png)\n"
    doc = seed_doc_from_markdown(body)
    root = _root(doc)
    assert reconstruct_body(doc) == body

    # root.children[1] is the blank-line paragraph between the two blocks
    # (every blank line is its own block on this branch), so the blockquote
    # is at index 2, not 1.
    list_para = root.children[0].children[0].children[0]
    assert any(
        isinstance(child, XmlElement) and child.tag == "image" for child in list_para.children
    )

    quote_para = root.children[2].children[0]
    assert any(
        isinstance(child, XmlElement) and child.tag == "image" for child in quote_para.children
    )


def test_image_is_a_sibling_leaf_with_expected_attributes() -> None:
    doc = seed_doc_from_markdown('before ![shown](s.png "caption") after\n')
    root = _root(doc)
    para = root.children[0]
    children = list(para.children)

    assert len(children) == 3
    assert isinstance(children[0], XmlText)
    assert isinstance(children[1], XmlElement)
    assert isinstance(children[2], XmlText)
    assert children[1].tag == "image"
    assert dict(children[1].attributes) == {
        "src": "s.png",
        "alt": "shown",
        "title": "caption",
    }


def test_untitled_image_has_no_title_attribute_and_titled_media_stores_unescaped_title() -> None:
    untitled = seed_doc_from_markdown("![alt](s.png)\n")
    untitled_image = _root(untitled).children[0].children[0]
    assert isinstance(untitled_image, XmlElement)
    assert "title" not in dict(untitled_image.attributes)

    titled = seed_doc_from_markdown('![alt](s.png "a \\"quoted\\" title")\n')
    titled_image = _root(titled).children[0].children[0]
    assert isinstance(titled_image, XmlElement)
    assert dict(titled_image.attributes)["title"] == 'a "quoted" title'


def test_empty_alt_image_round_trips_exactly() -> None:
    body = "![](s.png)\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_image_src_with_balanced_parens_round_trips_byte_stable() -> None:
    body = "![x](a(1).png)\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_live_edited_image_src_with_space_serializes_reparseable() -> None:
    # Only a live session can produce a spaced src (attrs set editor-side,
    # never through markdown-it). It must serialize to the angle-bracket
    # form so the checkpoint output still parses as an image.
    doc = seed_doc_from_markdown("![x](ok.png)\n")
    para = _root(doc).children[0]
    image = next(c for c in para.children if getattr(c, "tag", None) == "image")
    with doc.transaction():
        image.attributes["src"] = "a b.png"
    once = reconstruct_body(doc)
    assert "![x](<a b.png>)" in once
    reseeded = seed_doc_from_markdown(once)
    re_para = _root(reseeded).children[0]
    assert any(getattr(c, "tag", None) == "image" for c in re_para.children)


def test_inline_code_stores_backticks_as_literal_text() -> None:
    # The flanking backticks must be real characters in the stored XmlText,
    # not stripped-then-resynthesized on serialize — matching the frontend's
    # InlineCode mark (blocks.ts), which relies on that for caret placement.
    # A pure string round-trip test wouldn't catch a regression back to the
    # old "hidden syntax" shape (same output string either way), so this
    # asserts the doc's own stored content directly.
    doc = seed_doc_from_markdown("Some `code` here.\n")
    para = _root(doc).children[0]
    text_content = "".join(t for t, _ in para.children[0].diff())
    assert text_content == "Some `code` here."


def test_inline_code_with_interior_backtick_uses_longer_fence() -> None:
    # CommonMark requires a longer fence when the content itself contains a
    # backtick, e.g. content "a`b" needs a double-backtick fence.
    body = "Code with a backtick: ``a`b`` end.\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body
    para = _root(doc).children[0]
    text_content = "".join(t for t, _ in para.children[0].diff())
    assert "``a`b``" in text_content


def test_inline_code_mark_on_plain_text_without_backticks_gets_a_fresh_fence() -> None:
    # Reachable via toggleCode/Mod-e on already-selected plain text (blocks.ts)
    # - the "code" mark can land on a run with zero embedded backtick
    # characters, unlike a run built from the backtick InputRule. Built via
    # _build_live_paragraph since only a live editing session, not the
    # forward markdown parser, can produce this shape.
    doc = _build_live_paragraph("hello")
    with doc.transaction():
        _root(doc).children[0].children[0].format(0, 5, {"code": True})  # type: ignore[union-attr]
    assert reconstruct_body(doc) == "`hello`\n"


def test_inline_code_repairs_an_interior_backtick_from_a_live_edit() -> None:
    # A live edit can land a new backtick inside an already-marked span
    # (typing while the cursor sits between two already-marked characters
    # carries the active marks over like any other character), breaking the
    # stored text's own embedded fence - "`co`de`" naively reparses with the
    # first "`" pair as the span ("co") and "de`" spilling out as plain text.
    # Must repair to a longer fence instead of reparsing wrong or crashing.
    doc = _build_live_paragraph("`co`de`")
    with doc.transaction():
        _root(doc).children[0].children[0].format(0, 7, {"code": True})  # type: ignore[union-attr]
    once = reconstruct_body(doc)
    reseeded = seed_doc_from_markdown(once)
    reseeded_para = _root(reseeded).children[0]
    assert reseeded_para.tag == "paragraph"
    text_content = "".join(t for t, _ in reseeded_para.children[0].diff())
    assert text_content == once.rstrip("\n")
    # And the content must actually still read "co`de" - not "co" with
    # "de`" silently dropped or spilled out as unmarked plain text.
    assert "co`de" in once


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
    # Booleans, not the strings "true"/"false". A string-valued attribute is
    # truthy in JavaScript even when it says "false", so an unchecked markdown
    # box rendered as a *ticked* box in the Tiptap client.
    assert dict(items[0].attributes)["checked"] is False
    assert dict(items[1].attributes)["checked"] is True
    # The checkbox marker itself must not leak into the item's paragraph text.
    assert items[0].children[0].children[0].to_py() == "todo"


def test_mixed_task_and_plain_items_becomes_a_task_list() -> None:
    """One marker is enough to make the list a taskList; the unmarked items
    are plain listItems in that same list."""
    doc = seed_doc_from_markdown("- [ ] marked\n- unmarked\n")
    root = _root(doc)
    lst = root.children[0]
    assert lst.tag == "taskList"
    assert [i.tag for i in lst.children] == ["taskItem", "listItem"]
    # The marker is consumed for the task item and left alone for the plain
    # one — a listItem's text is its own content, markers and all.
    assert lst.children[0].children[0].children[0].to_py() == "marked"
    assert lst.children[1].children[0].children[0].to_py() == "unmarked"


def test_a_list_with_no_markers_stays_a_bullet_list() -> None:
    doc = seed_doc_from_markdown("- one\n- two\n")
    lst = _root(doc).children[0]
    assert lst.tag == "bulletList"
    assert [i.tag for i in lst.children] == ["listItem", "listItem"]


def test_mixed_list_round_trips_byte_identically() -> None:
    """The serializer picks the marker per item rather than per list, so a
    mixed list survives a round trip unchanged in both directions."""
    for body in (
        "- [x] marked\n\n- unmarked\n",
        "- [x] top\n\n- mid\n\n  - [ ] nested\n\n  - also plain\n",
    ):
        once = reconstruct_body(seed_doc_from_markdown(body))
        assert once == body
        assert once == reconstruct_body(seed_doc_from_markdown(once))


def test_mixed_list_keeps_its_checkbox_markers() -> None:
    """The marker text a mixed list can't promote to a taskItem still has to
    come back out as a marker: escaping it ("\\[x\\]") is a content edit that
    no longer reads as a checkbox to anything, here or downstream."""
    body = "- [x] marked\n\n- unmarked\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    assert once == body
    assert once == reconstruct_body(seed_doc_from_markdown(once))


def test_ordered_list_keeps_its_checkbox_markers() -> None:
    body = "1. [ ] marked\n\n2. unmarked\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    assert once == body


def test_only_a_leading_marker_escapes_differently_from_other_brackets() -> None:
    """The exemption is positional — a bracket run anywhere else in the item,
    including one that would re-parse as a link, still escapes."""
    body = "- text \\[x\\] mid\n\n- \\[see\\](/a) not a link\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    assert once == body


def test_a_uniform_list_of_escaped_markers_promotes_to_task_items() -> None:
    """markdown-it resolves "\\[x\\]" to the text "[x]" before the codec sees a
    token, so an escaped marker and a live one are the same string by the time
    promotion is decided, and an all-escaped list becomes real taskItems.
    Pinned because it's what makes the serializer's choice below consistent
    rather than arbitrary."""
    doc = seed_doc_from_markdown("- \\[x\\] a\n\n- \\[x\\] b\n")
    lst = _root(doc).children[0]
    assert lst.tag == "taskList"
    assert [i.tag for i in lst.children] == ["taskItem", "taskItem"]


def test_an_escaped_marker_in_a_mixed_list_normalizes_to_a_live_one() -> None:
    """Same erasure one fork over: the item stays a plain listItem, and its
    text is indistinguishable from an item that was written "[x]" bare, so
    both serialize to the live marker. A single normalization pass — the
    output is then stable."""
    once = reconstruct_body(seed_doc_from_markdown("- \\[x\\] a\n\n- plain\n"))
    assert once == "- [x] a\n\n- plain\n"
    assert once == reconstruct_body(seed_doc_from_markdown(once))


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

    # root.children[1] is the blank-line block between the two top-level
    # constructs - every blank line is its own block now.
    bq = root.children[2]
    assert bq.tag == "blockquote"
    assert bq.children[0].tag == "taskList"


def test_list_reserialization_is_content_correct_and_idempotent() -> None:
    """Re-parsing the output must be a no-op (a well-defined normal form,
    not drift)."""
    body = "- item one\n- item two\n- item three\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    twice = reconstruct_body(seed_doc_from_markdown(once))
    assert once == twice
    assert "item one" in once and "item two" in once and "item three" in once


def test_nested_emphasis_keeps_the_continuing_mark_open() -> None:
    """A mark carried by both neighbouring runs stays open in the position
    it already holds, whatever ``_NESTING_MARK_ORDER`` says — italic around
    a bold word round-trips as written instead of closing and reopening the
    italic at each boundary, whose abutted delimiters (``****``) CommonMark
    reads as one unmatchable run."""
    for body in (
        "*a **b** c*\n",
        "**a *b* c**\n",
        "*a **b***\n",
        "**a *b***\n",
        "**bold [link](x) more**\n",
        "~~a *b* c~~\n",
        "> *the **Design** section is under **Status**.*\n",
    ):
        assert reconstruct_body(seed_doc_from_markdown(body)) == body


def test_emphasis_crossing_opens_with_the_underscore_spelling() -> None:
    """When one emphasis span ends exactly where another begins, the opener
    would abut a same-character closer into an ambiguous delimiter run — it
    opens with the underscore spelling instead, which re-parses to the same
    marks and is byte-stable from then on."""
    for src, want in (
        ("**a***b*\n", "**a**_b_\n"),
        ("*a***b**\n", "*a*__b__\n"),
    ):
        one = reconstruct_body(seed_doc_from_markdown(src))
        assert one == want
        assert reconstruct_body(seed_doc_from_markdown(one)) == one


def test_tight_list_round_trips_byte_identically() -> None:
    """A tight list (no blank lines between items) keeps its shape, so a
    checkpoint that touches one commits only the edit, never a whole-list
    spacing reflow."""
    for body in (
        "- one\n- two\n- three\n",
        "- [ ] todo\n- [x] done\n- [ ] later\n",
        "1. first\n2. second\n",
        "- top\n  - nested tight\n- next\n",
        "- [ ] top\n  - [x] nested\n- [ ] after\n",
    ):
        assert reconstruct_body(seed_doc_from_markdown(body)) == body


def test_loose_list_round_trips_byte_identically() -> None:
    for body in (
        "- one\n\n- two\n",
        "- [ ] todo\n\n- [x] done\n",
        "1. first\n\n2. second\n",
    ):
        assert reconstruct_body(seed_doc_from_markdown(body)) == body


def test_paragraphless_list_stays_unstamped_and_serializes_loose() -> None:
    """A list whose items hold only nested blocks (no direct paragraphs)
    carries no tight/loose signal — markdown-it's marker is paragraph
    hiddenness — so it stays unstamped and serializes loose, the safe
    direction. A wrongly-tight stamp would strip a loose source's blank
    lines."""
    loose = "- ```py\n  x\n  ```\n\n- ```py\n  y\n  ```\n"
    doc = seed_doc_from_markdown(loose)
    lst = _root(doc).children[0]
    assert isinstance(lst, XmlElement)
    assert dict(lst.attributes).get("tight") is None
    assert reconstruct_body(doc) == loose


def test_mixed_spacing_normalizes_loose_and_converges() -> None:
    """CommonMark makes looseness a whole-list property: one blank line
    anywhere makes the entire list loose, so mixed spacing has no faithful
    representation. It normalizes to uniform loose in one pass and is
    byte-stable from then on."""
    body = "- one\n- two\n\n- three\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    assert once == "- one\n\n- two\n\n- three\n"
    assert reconstruct_body(seed_doc_from_markdown(once)) == once


def test_editor_added_second_paragraph_falls_back_to_loose() -> None:
    """The tight attribute lives on the list node, so nothing clears it when
    an edit gives an item a second paragraph. Serializing that tightly would
    merge the paragraphs on the next parse — the serializer must fall back
    to loose for the whole list instead, and the result must re-parse to the
    same two paragraphs."""
    doc = seed_doc_from_markdown("- one\n- two\n")
    lst = _root(doc).children[0]
    assert isinstance(lst, XmlElement)
    assert dict(lst.attributes).get("tight") == "true"
    with doc.transaction():
        item = lst.children[0]
        item.children.append(XmlElement("paragraph", {}, contents=[XmlText("second para")]))  # pyright: ignore[reportAttributeAccessIssue]
    out = reconstruct_body(doc)
    assert out == "- one\n\n  second para\n\n- two\n"
    reparsed = _root(seed_doc_from_markdown(out)).children[0]
    assert isinstance(reparsed, XmlElement)
    first_item_tags = [c.tag for c in reparsed.children[0].children]  # pyright: ignore[reportAttributeAccessIssue]
    assert first_item_tags == ["paragraph", "paragraph"]


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


def test_task_item_continuation_indents_to_the_bullet_not_the_checkbox() -> None:
    """A task item's later blocks belong at the item's CommonMark content
    column — right after "- ". Indenting them under the whole "- [x] "
    marker instead put them 4 columns further out, which is an indented
    code block on the next parse: a checkpoint rewrote a task item's nested
    list into code, and the page then wouldn't open at all (the codec had
    no support for a code block inside a list item). The indentation of the
    output is what matters here — and the first case is tight, so it keeps
    its no-blank-line shape byte-for-byte."""
    assert reconstruct_body(seed_doc_from_markdown("- [ ] top\n  - [x] nested\n")) == (
        "- [ ] top\n  - [x] nested\n"
    )
    for body in (
        "- [ ] top\n\n  a second paragraph\n",
        "- [ ] top\n\n  ```py\n  x = 1\n  ```\n",
    ):
        assert reconstruct_body(seed_doc_from_markdown(body)) == body


def test_code_block_inside_a_list_item_round_trips() -> None:
    body = "- item\n\n  ```py\n  x = 1\n  ```\n"
    doc = seed_doc_from_markdown(body)
    item = _root(doc).children[0].children[0]
    assert [c.tag for c in item.children] == ["paragraph", "codeBlock"]
    assert dict(list(item.children)[1].attributes)["language"] == "py"
    assert reconstruct_body(doc) == body


def test_indented_code_block_inside_a_list_item_becomes_fenced() -> None:
    """Sub-bullets indented 4+ columns past their item's content column are
    an indented code block, not a nested list — what CommonMark says, and
    what a real page in the wiki turned out to contain."""
    body = "- item\n\n      - not a nested bullet\n"
    doc = seed_doc_from_markdown(body)
    item = _root(doc).children[0].children[0]
    assert [c.tag for c in item.children] == ["paragraph", "codeBlock"]
    got = reconstruct_body(doc)
    assert got == "- item\n\n  ```\n  - not a nested bullet\n  ```\n"
    assert reconstruct_body(seed_doc_from_markdown(got)) == got


def test_heading_and_thematic_break_inside_a_list_item() -> None:
    body = "- item\n\n  ## nested heading\n\n  ---\n"
    doc = seed_doc_from_markdown(body)
    item = _root(doc).children[0].children[0]
    assert [c.tag for c in item.children] == ["paragraph", "heading", "thematic_break"]
    assert dict(list(item.children)[1].attributes)["level"] == "2"
    assert reconstruct_body(doc) == body


def test_nested_thematic_break_normalizes_to_dashes() -> None:
    # A nested break is rebuilt from tokens, not from a source slice (a
    # blockquote's would carry its "> " prefixes), so it loses the source's
    # own spelling. It carries no content, so every spelling is the same
    # block — but the result still has to be stable, not drift each pass.
    got = reconstruct_body(seed_doc_from_markdown("> quote\n>\n> ***\n"))
    assert got == "> quote\n>\n> ---\n"
    assert reconstruct_body(seed_doc_from_markdown(got)) == got


def test_table_inside_a_list_item_keeps_rows_and_alignment() -> None:
    body = "- item\n\n  | a | b |\n  | :--- | ---: |\n  | 1 | 2 |\n"
    doc = seed_doc_from_markdown(body)
    item = _root(doc).children[0].children[0]
    table = list(item.children)[1]
    assert table.tag == "table"
    assert [c.tag for c in table.children] == ["tableRow", "tableSeparator", "tableRow"]
    assert reconstruct_body(doc) == body


def test_table_inside_a_blockquote_re_pads_cells() -> None:
    # Cells are re-emitted with single-space padding — a nested table's rows
    # are rebuilt from their cells rather than sliced verbatim out of the
    # source, which inside a blockquote would carry the "> " prefixes.
    got = reconstruct_body(
        seed_doc_from_markdown("> |  a  |  b  |\n> | --- | --- |\n> | 1 | 2 |\n")
    )
    assert got == "> | a | b |\n> | --- | --- |\n> | 1 | 2 |\n"
    assert reconstruct_body(seed_doc_from_markdown(got)) == got


def test_pipe_in_a_nested_table_cell_stays_escaped() -> None:
    body = "- item\n\n  | a \\| b | c |\n  | --- | --- |\n  | 1 | 2 |\n"
    doc = seed_doc_from_markdown(body)
    table = list(_root(doc).children[0].children[0].children)[1]
    assert serialize_row(list(table.children)[0]) == "| a \\| b | c |\n"
    assert reconstruct_body(doc) == body


def test_pipe_inside_a_code_span_in_a_nested_table_cell_gains_no_backslash() -> None:
    """GFM requires a cell's pipe to be escaped even inside a code span, and
    the table tokenizer unescapes it while splitting cells — before inline
    parsing runs. So a cell's token content holds source-level text with bare
    pipes either way, and re-escaping every one of them reproduces the source
    spelling exactly rather than leaking a backslash into the code span."""
    body = "- item\n\n  | h1 | h2 |\n  | --- | --- |\n  | b `\\|` az | y |\n"
    once = reconstruct_body(seed_doc_from_markdown(body))
    assert once == body
    # The rendered code span still holds one bare pipe, and repeated
    # checkpoints don't accumulate backslashes in front of it.
    code_spans = [
        child.content
        for token in gfm_parser().parse(once)
        if token.type == "inline"
        for child in (token.children or [])
        if child.type == "code_inline"
    ]
    assert code_spans == ["|"]
    assert reconstruct_body(seed_doc_from_markdown(once)) == once


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


def test_setext_h1_becomes_level_1_atx() -> None:
    # Setext (underline) headings have no leading hashes at all — a
    # hand-rolled "count leading #" parse mislevels this to 0, which then
    # serializes with no "#" marker, silently demoting the heading to a
    # paragraph. Canonicalizing to ATX form (not byte-identical to the
    # setext source) is the same "correct over byte-identical" tradeoff
    # this module already makes for lists/hard-breaks.
    doc = seed_doc_from_markdown("Title\n=====\n\nBody.\n")
    root = _root(doc)
    heading = root.children[0]
    assert heading.tag == "heading"
    assert dict(heading.attributes)["level"] == "1"
    assert reconstruct_body(doc) == "# Title\n\nBody.\n"


def test_setext_h2_becomes_level_2_atx() -> None:
    doc = seed_doc_from_markdown("Sub\n---\n\nBody.\n")
    root = _root(doc)
    heading = root.children[0]
    assert dict(heading.attributes)["level"] == "2"
    assert reconstruct_body(doc) == "## Sub\n\nBody.\n"


def test_setext_heading_does_not_leak_underline_into_title_text() -> None:
    # The underline line must not become part of the heading's own inline
    # content (a second, compounding failure mode alongside the wrong
    # level: parsing "Title\n=====" as a whole would fold "=====" into the
    # title text instead of recognizing it as the setext marker).
    doc = seed_doc_from_markdown("Title\n=====\n")
    root = _root(doc)
    heading = root.children[0]
    assert heading.children[0].to_py() == "Title"


def test_escaped_mark_delimiters_round_trip_as_literal_text() -> None:
    # markdown-it resolves `\*x\*` to the literal text "*x*" at parse time
    # (correctly not treated as emphasis) — reserializing that text
    # verbatim hands back active syntax on the next parse. Same failure
    # mode for `\[text\](url)` and a leading `\#`.
    cases = [
        "A \\*literal\\* asterisk.\n",
        "A \\_literal\\_ underscore.\n",
        "A \\`literal\\` backtick.\n",
        "A \\[link\\](x) escaped.\n",
        "\\# not a heading\n",
    ]
    for raw in cases:
        once = reconstruct_body(seed_doc_from_markdown(raw))
        assert once == raw, f"expected stable round-trip for {raw!r}, got {once!r}"
        # And re-parsing the output must not activate the escaped syntax —
        # a second round-trip must be byte-identical to the first.
        twice = reconstruct_body(seed_doc_from_markdown(once))
        assert twice == once


def test_escaping_does_not_corrupt_inline_code_content() -> None:
    # Inline code spans are verbatim — CommonMark never processes escapes
    # inside them. Escaping backslash/backtick/etc. in code content would
    # corrupt the code's actual text, not just its markdown presentation.
    body = "Code with a literal backslash: `a\\\\b` and stars `*x*`.\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_escaped_block_start_markers_round_trip_as_literal_text() -> None:
    # A leading "-"/"+"/">"/"1." in a paragraph's serialized text is only
    # ambiguous as a *block*-start marker — _escape_inline_text's mark-
    # delimiter escaping is position-independent and doesn't cover this ("*" is the
    # one marker character that's already covered there, for the unrelated
    # emphasis reason). Without _escape_block_start_ambiguity, checkpointing
    # a touched paragraph beginning with one of these silently turns it into
    # a real bullet/blockquote/ordered-list item on the next parse.
    cases = [
        "\\- not a bullet\n",
        "\\+ not a bullet\n",
        "\\> not a quote\n",
        "\\* not a bullet\n",
        "1\\. not a list\n",
        "1\\) not a list\n",
    ]
    for raw in cases:
        once = reconstruct_body(seed_doc_from_markdown(raw))
        assert once == raw, f"expected stable round-trip for {raw!r}, got {once!r}"
        twice = reconstruct_body(seed_doc_from_markdown(once))
        assert twice == once


def test_ordered_marker_escape_lands_on_the_delimiter_not_the_digits() -> None:
    # Every other marker character this module escapes (#, >, -, +, ~, `)
    # is ASCII punctuation, so a backslash placed directly before it is a
    # valid CommonMark escape. A digit is not punctuation — a backslash
    # before "1" is never consumed on reparse and survives as a literal
    # extra character instead of protecting anything (confirmed against
    # the forward parse: "\1. item" reparses to the *content* "\1. item",
    # not "1. item"). The delimiter right after the digits ("." or ")")
    # *is* punctuation, so that's where the escape has to go instead.
    for text in ("First line\n1. item", "First line\n1) item", "First line\n10. item"):
        doc = _build_live_paragraph(text)
        once = reconstruct_body(doc)
        reseeded = seed_doc_from_markdown(once)
        children = list(reseeded.get(ROOT_XML_KEY, type=XmlFragment).children)
        tags = [c.tag for c in children]
        # Every newline is its own block boundary now (no soft breaks within
        # one block) - the soft-break line becomes its own paragraph.
        assert tags == ["paragraph", "paragraph"], (
            f"expected two paragraphs for {text!r}, got tags={tags}"
        )
        twice = reconstruct_body(reseeded)
        assert once == twice, f"not stable from round 1 for {text!r}: {once!r} != {twice!r}"
        content = "\n".join("".join(t for t, _ in c.children[0].diff()) for c in children)
        assert content == text, f"expected content {text!r} preserved exactly, got {content!r}"


def test_mid_text_block_marker_characters_are_left_alone() -> None:
    # Only the block-start position is ambiguous — a dash or "1." elsewhere
    # in a paragraph was never going to be misread as a marker, so it
    # shouldn't gain a spurious escape.
    cases = [
        "A dash - mid sentence, not a marker.\n",
        "A number 1. mid sentence, not a marker.\n",
        "Just a dash alone: -\n",
    ]
    for raw in cases:
        assert reconstruct_body(seed_doc_from_markdown(raw)) == raw


def test_escaped_thematic_break_dash_run_stays_a_paragraph() -> None:
    # A paragraph whose whole text is an escaped dash run ("\---") is the
    # sharpest case: an unescaped "---" alone on a line isn't just
    # misclassified like the bullet/blockquote cases, it's a thematic
    # break — content-free — so reactivating it doesn't just change the
    # block type, it silently discards the paragraph's text entirely.
    # "***"/"_ _ _" thematic breaks need no dedicated case: every "*"/"_"
    # is already escaped unconditionally by _escape_inline_text for the emphasis
    # reason, which breaks the run regardless of position.
    for raw in ("\\---\n", "\\- - -\n"):
        doc = seed_doc_from_markdown(raw)
        root = _root(doc)
        assert root.children[0].tag == "paragraph"
        once = reconstruct_body(doc)
        assert once == raw
        twice = reconstruct_body(seed_doc_from_markdown(once))
        assert twice == once


def test_dash_run_with_trailing_content_does_not_need_escaping() -> None:
    # CommonMark requires a thematic break to be the *whole* line — "---"
    # followed by other text on the same line is never ambiguous, so no
    # escape should be added (and, separately, checkpointing an already-
    # unescaped "--- and more" must not spuriously start escaping it).
    body = "--- and more text on the line\n"
    doc = seed_doc_from_markdown(body)
    root = _root(doc)
    assert root.children[0].tag == "paragraph"
    assert reconstruct_body(doc) == body


def test_two_dashes_is_not_enough_for_a_thematic_break() -> None:
    # Below the 3-dash threshold — not a break, not a bullet (no whitespace
    # right after the first dash) — plain literal text, no escape needed.
    body = "-- not enough dashes for a break\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_escaped_block_start_markers_inside_list_item_stay_literal() -> None:
    # _serialize_block_sequence (list item / blockquote content) used to
    # call _serialize_inline_children directly, bypassing
    # _escape_block_start_ambiguity entirely — a list item or blockquote's
    # own first line is just as much a fresh block-start position as the
    # top of the document. Without the escaping, each of these reactivates
    # as a real nested list/heading/break — the literal text the author
    # typed silently becomes a different block.
    cases = [
        "- \\- nested item text\n",
        "- \\# nested heading text\n",
        "- \\---\n",
    ]
    for raw in cases:
        doc = seed_doc_from_markdown(raw)
        root = _root(doc)
        assert root.children[0].tag == "bulletList"
        item = root.children[0].children[0]
        assert item.tag == "listItem"
        assert item.children[0].tag == "paragraph"
        once = reconstruct_body(doc)
        assert once == raw
        twice = reconstruct_body(seed_doc_from_markdown(once))
        assert twice == once


def test_escaped_block_start_markers_inside_blockquote_stay_literal() -> None:
    cases = [
        "> \\> nested quote text\n",
        "> \\# nested heading text\n",
        "> \\---\n",
    ]
    for raw in cases:
        doc = seed_doc_from_markdown(raw)
        root = _root(doc)
        assert root.children[0].tag == "blockquote"
        assert root.children[0].children[0].tag == "paragraph"
        once = reconstruct_body(doc)
        assert once == raw
        twice = reconstruct_body(seed_doc_from_markdown(once))
        assert twice == once


def test_nested_dash_run_with_trailing_content_does_not_need_escaping() -> None:
    # Mirrors the top-level case: a dash run is only a thematic break when
    # it's the *whole* line — trailing content after it was never ambiguous
    # inside a list item/blockquote either, so no escape should be added
    # (and an already-unescaped one must round-trip unchanged, not gain one).
    for raw in ("- --- nested break text\n", "> --- nested break text\n"):
        assert reconstruct_body(seed_doc_from_markdown(raw)) == raw


def test_escaped_block_start_marker_on_a_later_soft_break_line() -> None:
    # _escape_block_start_ambiguity used to check only text[0] — the very
    # first character of the whole (possibly multi-line) paragraph text.
    # A paragraph's *second* line, produced by a soft break, is just as
    # much a fresh block-start position once re-emitted: unconditionally
    # at the top level (confirmed against the forward parse: "line one\n#
    # line two" splits into a paragraph + a separate heading block), and
    # doubly so once a list item's continuation indent or a blockquote's
    # per-line "> " prefix (_serialize_blockquote adds that to *every*
    # line, including a paragraph's internal soft breaks) gets added on
    # top. Reactivating any of these turns text the author typed literally
    # into a different block on the next seed of the checkpointed body.
    cases = [
        "first line\n\\# second line\n",
        "first line\n\\- second line\n",
        "- first line\n  \\# second line\n",
        "> first line\n> \\# second line\n",
        "> first line\n> \\- second line\n",
    ]
    for raw in cases:
        once = reconstruct_body(seed_doc_from_markdown(raw))
        assert once == raw, f"expected stable round-trip for {raw!r}, got {once!r}"
        twice = reconstruct_body(seed_doc_from_markdown(once))
        assert twice == once


def test_escaped_setext_underline_on_a_later_line_stays_literal() -> None:
    # A setext heading underline (one or more contiguous "=" for level 1,
    # or "-" for level 2 — a different, lower threshold than a thematic
    # break's 3+) only ever reactivates a *preceding* line, i.e. exactly
    # the per-line position _escape_line_start already runs at. "=" had no
    # handling at all; a bare "-"/"--" (below the thematic-break floor,
    # and not matching the bullet check either since nothing follows)
    # fell through every existing check.
    cases = [
        "My Title\n\\===\n",
        "My Title\n\\-\n",
        "My Title\n\\--\n",
        "- My Title\n  \\===\n",
        "> My Title\n> \\===\n",
    ]
    for raw in cases:
        once = reconstruct_body(seed_doc_from_markdown(raw))
        assert once == raw, f"expected stable round-trip for {raw!r}, got {once!r}"
        twice = reconstruct_body(seed_doc_from_markdown(once))
        assert twice == once


def _build_live_paragraph(text: str) -> Doc:
    """A paragraph built directly (not via seed_doc_from_markdown), to
    simulate what a user typing plain prose in the live editor actually
    produces — critically, one that never went through the forward
    markdown parser, so text that *happens* to look like table source
    (e.g. "a | b" + a soft break + "---|---") was never classified as a
    table in the first place. Round-tripping this through checkpoint
    serialization is the only way this specific reactivation is
    reachable; seeding directly from that same markdown text would
    already (correctly) parse it as a table, not a paragraph."""
    doc = Doc()
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    para = XmlElement("paragraph", {BLOCK_ID_ATTR: "b0"}, contents=[])
    with doc.transaction():
        root.children.append(para)
    with doc.transaction():
        para.children.append(XmlText(text))
    return doc


def test_paragraph_reactivates_as_table_without_escaping() -> None:
    # A plain paragraph a user actually typed — "a | b" then a soft break
    # then "---|---" — serializes with neither "|" nor the bare dash run
    # escaped by anything else in this module, and a GFM table delimiter
    # row needs only 1+ dashes per cell (no thematic-break-style 3+
    # floor), so the checkpointed output reactivates as a table on the
    # next parse.
    doc = _build_live_paragraph("a | b\n---|---")
    once = reconstruct_body(doc)
    reseeded = seed_doc_from_markdown(once)
    tags = [c.tag for c in reseeded.get(ROOT_XML_KEY, type=XmlFragment).children]
    # Every newline is its own block boundary now, so the two lines split
    # into two paragraphs - neither reactivates as a table (the dash run
    # is escaped as a thematic break, which also blocks the delimiter-row
    # read, coincidentally but correctly).
    assert tags == ["paragraph", "paragraph"], (
        f"expected two paragraphs, no table reactivation, got tags={tags} for body={once!r}"
    )


def test_table_delimiter_row_escaping_is_stable() -> None:
    for text in ("a | b\n---|---", "a | b\n--|--", "a | b\n:--|--:"):
        doc = _build_live_paragraph(text)
        once = reconstruct_body(doc)
        twice = reconstruct_body(seed_doc_from_markdown(once))
        assert once == twice
        tags = [
            c.tag for c in seed_doc_from_markdown(once).get(ROOT_XML_KEY, type=XmlFragment).children
        ]
        assert tags == ["paragraph", "paragraph"]


def test_paragraph_line_of_tildes_does_not_reactivate_as_a_fence() -> None:
    # "~" isn't in _escape_inline_text's char set at all (only
    # \`*_[] are), unlike backtick fences — a plain paragraph line typed
    # as "~~~" reactivates as a fenced code block opener on the next
    # parse, which is more severe than the other cases: a fence doesn't
    # just misclassify one block, it swallows everything up to the *next*
    # matching fence (or EOF) as raw content.
    doc = _build_live_paragraph("some text:\n~~~\nmore text")
    once = reconstruct_body(doc)
    twice = reconstruct_body(seed_doc_from_markdown(once))
    assert once == twice
    tags = [
        c.tag for c in seed_doc_from_markdown(once).get(ROOT_XML_KEY, type=XmlFragment).children
    ]
    # Every newline is its own block boundary now - three lines, three
    # paragraphs, none of them reactivating as a fence.
    assert tags == ["paragraph", "paragraph", "paragraph"]


def test_paragraph_line_of_backticks_does_not_reactivate_as_a_fence() -> None:
    # Backtick is already in _escape_inline_text's char set, so this is
    # already protected as a side effect (confirmed, not assumed) — kept
    # as an explicit regression test alongside the tilde case rather than
    # relying on that being obvious from reading _serialize_inline_text alone.
    doc = _build_live_paragraph("some text:\n```\nmore text")
    once = reconstruct_body(doc)
    tags = [
        c.tag for c in seed_doc_from_markdown(once).get(ROOT_XML_KEY, type=XmlFragment).children
    ]
    assert tags == ["paragraph", "paragraph", "paragraph"]


def test_indented_block_markers_do_not_reactivate() -> None:
    # Up to 3 leading spaces are insignificant to CommonMark — a heading,
    # blockquote, bullet, ordered marker, thematic break, and fence opener
    # all still reactivate through 1-3 spaces of indent the same as at
    # column 0. _escape_line_start's checks used to require the marker at
    # true column 0 and missed every one of these.
    #
    # The escape backslash goes immediately before the marker character,
    # not before the line's leading spaces — CommonMark's backslash escape
    # only applies to punctuation, never to whitespace (confirmed against
    # the spec and the forward parse), so a backslash before a space is
    # never consumed and would survive into the reparsed content as a
    # literal extra character instead of protecting anything. The
    # documented tradeoff (AGENT_WIKI_MARKDOWN_STANDARD.md §6): the
    # indentation itself doesn't survive the checkpoint — CommonMark
    # strips it as insignificant regardless of what this module emits, so
    # dropping it here is a no-op as far as final content goes, not an
    # extra loss. Checked via round 1 vs round 2 (an earlier version of
    # this fix escaped in a position CommonMark doesn't treat as an
    # escape at all, so it took an extra round to settle and, worse,
    # settled on content with a stray backslash baked in — round-1
    # fidelity is the real bar, not eventual stability).
    cases = [
        ("First line\n  # Heading", "First line\n# Heading"),
        ("First line\n > Quote", "First line\n> Quote"),
        ("First line\n   - item", "First line\n- item"),
        ("First line\n  + item", "First line\n+ item"),
        ("First line\n  1. item", "First line\n1. item"),
        ("First line\n   ---", "First line\n---"),
        ("First line\n  ~~~\ncode\n~~~", "First line\n~~~\ncode\n~~~"),
    ]
    for text, expected_content in cases:
        doc = _build_live_paragraph(text)
        once = reconstruct_body(doc)
        reseeded = seed_doc_from_markdown(once)
        children = list(reseeded.get(ROOT_XML_KEY, type=XmlFragment).children)
        tags = [c.tag for c in children]
        # Every newline is its own block boundary now - each line the
        # original text split into is its own paragraph.
        expected_tags = ["paragraph"] * expected_content.count("\n") + ["paragraph"]
        assert tags == expected_tags, f"expected {expected_tags} for {text!r}, got tags={tags}"
        twice = reconstruct_body(reseeded)
        assert once == twice, f"not stable from round 1 for {text!r}: {once!r} != {twice!r}"
        content = "\n".join("".join(t for t, _ in c.children[0].diff()) for c in children)
        assert content == expected_content, (
            f"expected the leading indentation trimmed for {text!r}, got {content!r}"
        )


def test_indented_first_line_does_not_reactivate_as_code_block() -> None:
    # 4+ columns of leading indentation on a block's *first* line (4 spaces,
    # or fewer spaces then a tab, which reaches the next 4-column stop)
    # reactivates as an indented code block — a different, narrower case
    # than the 1-3-space marker checks above, since indented code can't
    # interrupt an already-started paragraph. There's no marker character
    # here to escape at all (the ambiguity is the indentation itself, and
    # CommonMark can't escape whitespace), so the leading run is dropped
    # outright rather than backslash-prefixed — same documented tradeoff
    # and same round-1 fidelity bar as the marker cases above.
    cases = [
        ("    looks like code\nsecond line", "looks like code\nsecond line"),
        ("\ttab indented\nsecond line", "tab indented\nsecond line"),
    ]
    for text, expected_content in cases:
        doc = _build_live_paragraph(text)
        once = reconstruct_body(doc)
        reseeded = seed_doc_from_markdown(once)
        children = list(reseeded.get(ROOT_XML_KEY, type=XmlFragment).children)
        tags = [c.tag for c in children]
        assert tags == ["paragraph", "paragraph"], (
            f"expected two paragraphs for {text!r}, got tags={tags}"
        )
        twice = reconstruct_body(reseeded)
        assert once == twice, f"not stable from round 1 for {text!r}: {once!r} != {twice!r}"
        content = "\n".join("".join(t for t, _ in c.children[0].diff()) for c in children)
        assert content == expected_content, (
            f"expected the leading indentation trimmed for {text!r}, got {content!r}"
        )


def test_indented_continuation_line_stays_safe_without_escaping() -> None:
    # A *continuation* line (not the block's first line) with 4+ leading
    # spaces is already safe without any escaping at all — indented code
    # can't interrupt a paragraph, so it stays part of the same paragraph
    # either way. Asserts the byte-identical (mod the trailing newline
    # reconstruct_body always appends) round trip, not just the tag, to
    # confirm no unnecessary backslash gets inserted here.
    text = "First line\n    still one paragraph"
    doc = _build_live_paragraph(text)
    once = reconstruct_body(doc)
    assert once == text + "\n"
    tags = [
        c.tag for c in seed_doc_from_markdown(once).get(ROOT_XML_KEY, type=XmlFragment).children
    ]
    # Every newline is its own block boundary now - the continuation line
    # becomes its own paragraph. It stays safe without any backslash escape
    # (there's nothing to escape - the ambiguity is 4+ leading columns of
    # indentation, which _strip_indented_code_ambiguity_for_parse strips
    # on reparse, same documented tradeoff as the marker-escape cases).
    assert tags == ["paragraph", "paragraph"]


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
    # This parser config folds every inline construct into a token type this
    # codec encodes, so exercise the fail-loud guard with a synthetic unknown
    # child instead of real markdown.
    body = SimpleNamespace(
        children=[SimpleNamespace(type="strikethrough_open", content="", children=None)]
    )
    with pytest.raises(NotImplementedError):
        _inline_runs(body)


def test_hard_line_break_backslash_form_round_trips_to_canonical_form() -> None:
    """Both CommonMark hard-break spellings (trailing backslash, trailing
    double-space) parse; output always canonicalizes to the double-space
    form — same "correct, not necessarily byte-identical" tradeoff as list/
    code-block normalization elsewhere in this module."""
    doc = seed_doc_from_markdown("Line one\\\nLine two\n")
    assert reconstruct_body(doc) == "Line one  \nLine two\n"


def test_hard_line_break_double_space_form_round_trips_exactly() -> None:
    body = "Line one  \nLine two\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_hard_line_break_distinct_from_soft_break() -> None:
    """A bare newline (softbreak) must round-trip as a bare newline, not
    grow a hard-break's trailing spaces — the two must stay distinguishable
    through the codec, not collapse into the same representation."""
    body = "Soft\nbreak stays soft.\n\nHard  \nbreak stays hard.\n"
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_hard_line_break_is_a_sibling_leaf_not_text() -> None:
    """A hard break is a `hardBreak` XmlElement sibling of the surrounding
    XmlText runs (matching how y-prosemirror represents any PM leaf/atom
    node), not a character embedded inside a text run."""
    doc = seed_doc_from_markdown("Before\\\nAfter\n")
    root = _root(doc)
    para = root.children[0]
    children = list(para.children)
    tags_or_text = [c.tag if hasattr(c, "tag") else "text" for c in children]
    assert "hardBreak" in tags_or_text


def test_hard_line_break_inside_bold_mark() -> None:
    """A hard break inside emphasis is valid CommonMark — the mark doesn't
    have to elegantly span the break (each text run around it re-wraps
    independently), it just must round-trip to something that re-parses
    with the same effective content and marks."""
    body = "**bold  \ncontinues bold**\n"
    doc = seed_doc_from_markdown(body)
    once = reconstruct_body(doc)
    twice = reconstruct_body(seed_doc_from_markdown(once))
    assert once == twice
    assert "bold" in once and "continues bold" in once


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


def test_reconstruct_body_with_block_map_spans_match_serialized_text() -> None:
    doc = seed_doc_from_markdown(_SAMPLE)
    body, spans = reconstruct_body_with_block_map(doc)
    assert body == reconstruct_body(doc)
    assert [s.block_id for s in spans] == [
        "b0",
        "b1",
        "b2",
        "b3",
        "b4",
        "b5",
        "b6",
        "b7",
        "b8",
    ]
    for s in spans:
        assert body[s.start : s.end]
    # Spans are in document order and non-overlapping, same invariant as
    # markdown_blocks.top_level_block_ranges.
    for prev, nxt in zip(spans, spans[1:]):
        assert prev.end <= nxt.start


def test_reconstruct_body_with_block_map_finds_offset_within_touched_block() -> None:
    doc = seed_doc_from_markdown("First paragraph.\n\nSecond paragraph.\n")
    body, spans = reconstruct_body_with_block_map(doc)
    # b1 is the blank-line block between the two paragraphs now.
    second = next(s for s in spans if s.block_id == "b2")
    assert body[second.start : second.end] == "Second paragraph.\n"


# --- nested-mark serialization ------------------------------------------- #


def test_bold_text_with_nested_italic_word_round_trips_validly() -> None:
    """Regression test (review): a run that's a strict subset of its
    neighbor's marks (bold continues, italic starts and ends in the
    middle) used to close and reopen the shared "**" at every run
    boundary regardless of continuity, producing invalid, unbalanced
    delimiter runs ("*****") instead of properly nested ones."""
    body = "A **bold _and_ italic** word.\n"
    doc = seed_doc_from_markdown(body)
    out = reconstruct_body(doc)
    assert "*****" not in out
    assert out == "A **bold *and* italic** word.\n"
    # Stable under a second round trip (idempotent, not compounding further
    # the way the pre-fix bug did on repeated touches).
    doc2 = seed_doc_from_markdown(out)
    assert reconstruct_body(doc2) == out


def test_italic_quoted_code_run_stays_stable_across_touches() -> None:
    """The exact corpus repro from review (Features/Triggers and Events.md):
    a code span inside italics, adjacent to quote characters, used to grow
    an extra pair of asterisks on every touch instead of staying stable."""
    body = 'a b *"x `c` y"*.\n'
    doc = seed_doc_from_markdown(body)
    out1 = reconstruct_body(doc)
    assert out1 == body
    doc2 = seed_doc_from_markdown(out1)
    out2 = reconstruct_body(doc2)
    assert out2 == out1


def test_three_overlapping_marks_nest_properly() -> None:
    """bold+strike+italic all overlapping in a staggered pattern — each
    mark's delimiter must open/close at exactly its own boundary, properly
    nested (inner marks close before outer ones), not per-run independent
    wrapping."""
    doc = seed_doc_from_markdown("plain\n")
    root = _root(doc)
    xt = root.children[0].children[0]
    with doc.transaction():
        xt.format(0, 5, {"bold": True})
        xt.format(1, 4, {"bold": True, "strike": True})
        xt.format(2, 3, {"bold": True, "strike": True, "italic": True})
    out = reconstruct_body(doc)
    # Re-parsing must reproduce byte-identical marks — the real invariant
    # (valid, round-trippable markdown), not a hardcoded expected string.
    doc2 = seed_doc_from_markdown(out)
    assert reconstruct_body(doc2) == out


def test_link_title_round_trips() -> None:
    """Regression test (review): a link's title used to be dropped at
    parse time (only href was ever captured), so it was already gone
    before serialization ever ran — real data loss, not just a formatting
    choice."""
    body = 'A [t](http://example.com "Title") x.\n'
    doc = seed_doc_from_markdown(body)
    assert reconstruct_body(doc) == body


def test_adjacent_links_with_different_hrefs_do_not_merge() -> None:
    doc = seed_doc_from_markdown("plain\n")
    root = _root(doc)
    xt = root.children[0].children[0]
    with doc.transaction():
        xt.format(0, 2, {"link": {"href": "http://a.example"}})
        xt.format(2, 5, {"link": {"href": "http://b.example"}})
    out = reconstruct_body(doc)
    assert out == "[pl](http://a.example)[ain](http://b.example)\n"


def test_empty_code_block_serializes_instead_of_crashing() -> None:
    # An empty code block has no text child at all, and indexing it raised
    # IndexError out of pycrdt — a checkpoint that crashed and retried forever,
    # so the page could never be saved again. Reached by inserting a code block
    # and typing nothing.
    doc = seed_doc_from_markdown("```python\nx = 1\n```\n")
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    block = root.children[0]
    assert isinstance(block, XmlElement)
    with doc.transaction():
        while len(block.children):
            del block.children[0]

    assert serialize_block(block) == "```python\n```\n"
    # And the whole-document path a checkpoint actually takes.
    assert reconstruct_body(doc) == "```python\n```\n"


def test_empty_table_row_serializes_instead_of_crashing() -> None:
    doc = seed_doc_from_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    table = root.children[0]
    assert isinstance(table, XmlElement)
    row = list(table.children)[-1]
    assert isinstance(row, XmlElement)
    with doc.transaction():
        while len(row.children):
            del row.children[0]

    assert serialize_row(row) == ""
    reconstruct_body(doc)  # must not raise


def test_checkbox_toggled_in_the_editor_serializes_as_checked() -> None:
    # What a Tiptap client writes through y-prosemirror is the ProseMirror
    # attribute value — a real bool. The serializer compared it against the
    # string "true", so `True == "true"` was False and every box checked in the
    # editor serialized back to `- [ ]`: the checkbox looked saved and wasn't.
    doc = seed_doc_from_markdown("- [ ] todo\n")
    lst = _root(doc).children[0]
    assert isinstance(lst, XmlElement)
    with doc.transaction():
        lst.children[0].attributes["checked"] = True  # pyright: ignore[reportAttributeAccessIssue]

    assert reconstruct_body(doc) == "- [x] todo\n"


def test_checkbox_accepts_the_legacy_string_attribute() -> None:
    # Snapshots written before the switch to booleans hold "true"/"false", and a
    # node parsed from `data-checked` HTML can too. Both must still read
    # correctly — and note "false" can't be handled by truthiness, since a
    # non-empty string is truthy.
    doc = seed_doc_from_markdown("- [ ] one\n- [ ] two\n")
    lst = _root(doc).children[0]
    assert isinstance(lst, XmlElement)
    with doc.transaction():
        lst.children[0].attributes["checked"] = "true"  # pyright: ignore[reportAttributeAccessIssue]
        lst.children[1].attributes["checked"] = "false"  # pyright: ignore[reportAttributeAccessIssue]

    assert reconstruct_body(doc) == "- [x] one\n- [ ] two\n"


def test_code_block_closing_fence_starts_its_own_line() -> None:
    # The editor stores code text with no trailing newline, and the closing fence
    # was appended directly to it ("daskjqwer```"). CommonMark doesn't read that
    # as a fence, so the block stopped being a code block on the next round trip
    # — visible in an exported page before this was fixed.
    doc = seed_doc_from_markdown("```\ndaskjqwer\n```\n")
    block = _root(doc).children[0]
    assert isinstance(block, XmlElement)
    with doc.transaction():
        while len(block.children):
            del block.children[0]
        block.children.append(XmlText("daskjqwer"))  # pyright: ignore[reportArgumentType]

    assert reconstruct_body(doc) == "```\ndaskjqwer\n```\n"
    # And it survives a further round trip as a code block, rather than decaying
    # into a paragraph of literal backticks.
    again = seed_doc_from_markdown(reconstruct_body(doc))
    assert _root(again).children[0].tag == "codeBlock"


def test_image_alt_with_a_newline_still_serializes_as_an_image() -> None:
    # A live session can set an alt straight from a filename, and a label that
    # spans lines re-parses as literal text, losing the image entirely.
    doc = seed_doc_from_markdown("![placeholder](s.png)\n")
    image = _root(doc).children[0].children[0]
    assert isinstance(image, XmlElement)
    with doc.transaction():
        image.attributes["alt"] = "line1\nline2"  # pyright: ignore[reportArgumentType]

    body = reconstruct_body(doc)
    assert body == "![line1 line2](s.png)\n"
    again = seed_doc_from_markdown(body)
    reparsed = _root(again).children[0].children[0]
    assert isinstance(reparsed, XmlElement)
    assert reparsed.tag == "image"


def _find_images(doc: Doc) -> list[XmlElement]:
    found: list[XmlElement] = []

    def walk(node: Any) -> None:
        children = getattr(node, "children", None)
        for child in list(children or []):
            if isinstance(child, XmlElement):
                if child.tag == "image":
                    found.append(child)
                walk(child)

    walk(_root(doc))
    return found


def test_same_origin_image_srcs_survive_and_foreign_ones_do_not() -> None:
    # A foreign src makes every reader's browser announce itself to that host,
    # so it must not reach committed markdown from either direction. Relative
    # paths resolve here and are ordinary content.
    for body in (
        "![a](/api/wiki/media/abc123#w=225)\n",
        "![a](s.png)\n",
        "![a](docs/diagram.png)\n",
    ):
        assert reconstruct_body(seed_doc_from_markdown(body)) == body

    for body in (
        "before ![x](https://evil.example.com/t.png) after\n",
        "![x](//evil.example.com/t.png)\n",
        "![x](http://evil.example.com/t.png)\n",
        "![x](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)\n",
    ):
        doc = seed_doc_from_markdown(body)
        # No image element means nothing a renderer would fetch. A data: URI
        # never gets that far: markdown-it's link validator rejects it, so it
        # stays inert text.
        assert not _find_images(doc)
        out = reconstruct_body(doc)
        assert "evil.example.com" not in out
        assert not _find_images(seed_doc_from_markdown(out))


def test_a_foreign_src_set_on_a_live_node_is_not_serialized() -> None:
    # A collaborator's document can hold any src, so serialization enforces the
    # rule too, not only the parse side.
    doc = seed_doc_from_markdown("![a](/api/wiki/media/abc123)\n")
    image = _root(doc).children[0].children[0]
    assert isinstance(image, XmlElement)
    with doc.transaction():
        image.attributes["src"] = "https://evil.example.com/t.png"  # pyright: ignore[reportArgumentType]

    assert "evil.example.com" not in reconstruct_body(doc)


def test_two_seeds_of_one_body_share_no_lineage() -> None:
    """Seeding the same markdown twice produces documents that are textually
    identical and, to Yjs, entirely unrelated.

    This is why a session's document identity has to survive a reconnect (see
    `coedit.open_session`'s reuse rule). Yjs merges by item id — `(client_id,
    clock)` — never by content, and `Doc()` mints a random client id per
    instance. So a client still holding the first lineage answers the second
    one's sync offer with its whole document, and the two copies cannot
    collapse into each other: the page ends up in the doc twice, and each
    further cycle doubles it again.
    """
    body = "- A\n\n- B\n\n- C\n"
    client_doc = seed_doc_from_markdown(body)
    server_doc = seed_doc_from_markdown(body)
    assert client_doc.client_id != server_doc.client_id

    # Exactly what the browser sends when it answers the server's SYNC_STEP1:
    # every update the server's state vector says it is missing.
    reply = client_doc.get_update(server_doc.get_state())
    server_doc.apply_update(reply)

    assert reconstruct_body(server_doc) == body + body  # the bug, made explicit


def test_a_shared_lineage_survives_the_same_exchange() -> None:
    """The same exchange against the *same* lineage is a no-op — which is what
    reusing the session's existing snapshot buys."""
    body = "- A\n\n- B\n\n- C\n"
    server_doc = seed_doc_from_markdown(body)
    client_doc = Doc()
    client_doc.apply_update(server_doc.get_update())  # synced, as a real client is

    server_doc.apply_update(client_doc.get_update(server_doc.get_state()))
    assert reconstruct_body(server_doc) == body


def test_lazy_continuation_list_markers_survive() -> None:
    """A paragraph line beginning with an ordered-list marker that can't
    interrupt a paragraph (start != 1) is a lazy continuation in the
    original document, but the per-line block split reparses it standalone
    — where it *is* a list, and the marker was consumed into list syntax
    and lost. The parse-side guard escapes it like the write side does; the
    text survives, converging to the escaped spelling."""
    src = "some paragraph text.\n5. bought milk\n6. sold eggs\n"
    once = reconstruct_body(seed_doc_from_markdown(src))
    assert "5\\. bought milk" in once
    assert "6\\. sold eggs" in once
    assert reconstruct_body(seed_doc_from_markdown(once)) == once

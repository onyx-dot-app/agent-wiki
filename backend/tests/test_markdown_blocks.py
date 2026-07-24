"""Pure unit tests for app/wiki/markdown_blocks.py — no DB/git fixtures."""

from __future__ import annotations

from app.wiki.markdown_blocks import BlockKind, top_level_block_ranges

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


def test_top_level_block_ranges_reconstructs_body_verbatim() -> None:
    blocks = top_level_block_ranges(_SAMPLE)
    # Blocks are in document order and non-overlapping; concatenating the
    # slices they cover plus the gaps between them must reconstruct the
    # original body exactly. This is the hard invariant the splicer depends
    # on: if this drifts, offsets are wrong and splicing corrupts content.
    assert blocks[0].start == 0
    assert blocks[-1].end <= len(_SAMPLE)
    for prev, nxt in zip(blocks, blocks[1:]):
        assert prev.end <= nxt.start


def test_block_kinds_classified_correctly() -> None:
    blocks = top_level_block_ranges(_SAMPLE)
    kinds = [b.kind for b in blocks]
    assert kinds == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.LIST,
        BlockKind.TABLE,
        BlockKind.PARAGRAPH,
    ]


def test_block_ids_are_positional_and_deterministic() -> None:
    blocks_a = top_level_block_ranges(_SAMPLE)
    blocks_b = top_level_block_ranges(_SAMPLE)
    assert [b.block_id for b in blocks_a] == [b.block_id for b in blocks_b]
    assert [b.block_id for b in blocks_a] == ["b0", "b1", "b2", "b3", "b4"]


def test_table_block_has_row_ranges() -> None:
    blocks = top_level_block_ranges(_SAMPLE)
    table = next(b for b in blocks if b.kind is BlockKind.TABLE)
    assert len(table.rows) == 3  # header + 2 body rows
    assert [r.row_id for r in table.rows] == ["b3:r0", "b3:r1", "b3:r2"]
    for row in table.rows:
        assert table.start <= row.start < row.end <= table.end
    # Each row's text is a distinct slice of the table's text.
    row_texts = [_SAMPLE[r.start : r.end] for r in table.rows]
    assert row_texts[0] != row_texts[1] != row_texts[2]


def test_block_text_slices_match_expected_content() -> None:
    blocks = top_level_block_ranges(_SAMPLE)
    heading = blocks[0]
    assert _SAMPLE[heading.start : heading.end].strip() == "# Heading"
    final_paragraph = blocks[-1]
    assert "Final paragraph." in _SAMPLE[final_paragraph.start : final_paragraph.end]


def test_empty_body_has_no_blocks() -> None:
    assert top_level_block_ranges("") == []
    assert top_level_block_ranges("   \n\n  ") == []


def test_single_paragraph() -> None:
    body = "Just one paragraph.\n"
    blocks = top_level_block_ranges(body)
    assert len(blocks) == 1
    assert blocks[0].kind is BlockKind.PARAGRAPH
    assert body[blocks[0].start : blocks[0].end] == body


def test_paragraph_containing_image_stays_a_single_paragraph_block() -> None:
    body = "Before ![alt](img.png) after.\n"
    blocks = top_level_block_ranges(body)
    assert len(blocks) == 1
    assert blocks[0].kind is BlockKind.PARAGRAPH
    assert body[blocks[0].start : blocks[0].end] == body


def test_code_block_kind() -> None:
    body = "Intro.\n\n```python\nx = 1\n```\n\nOutro.\n"
    blocks = top_level_block_ranges(body)
    kinds = [b.kind for b in blocks]
    assert kinds == [BlockKind.PARAGRAPH, BlockKind.CODE_BLOCK, BlockKind.PARAGRAPH]


def test_thematic_break_and_blockquote_kinds() -> None:
    body = "Para.\n\n---\n\n> quoted line\n"
    blocks = top_level_block_ranges(body)
    kinds = [b.kind for b in blocks]
    assert kinds == [BlockKind.PARAGRAPH, BlockKind.THEMATIC_BREAK, BlockKind.BLOCKQUOTE]


def test_list_block_span_excludes_trailing_blank_line() -> None:
    """markdown-it-py's bullet_list_open/ordered_list_open `.map` swallows
    the blank line after the list into the token's own span; paragraph/
    heading never do. A block's span must mean the same thing regardless of
    kind — "just this block's own text" — since markdown_yjs.py and
    markdown_splice.py's gap-ownership model both depend on that
    uniformity (see _trim_trailing_blank_lines)."""
    body = "- item one\n- item two\n\nNext paragraph.\n"
    blocks = top_level_block_ranges(body)
    list_block = blocks[0]
    assert body[list_block.start : list_block.end] == "- item one\n- item two\n"
    # The blank line belongs to nobody's span — it's the gap before the
    # next block, same as a heading/paragraph pair.
    assert body[list_block.end : blocks[1].start] == "\n"

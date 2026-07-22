"""Top-level markdown block/row boundary parsing — pure text, no CRDT.

This is the shared foundation for the targeted-splice checkpoint engine
(``markdown_splice.py``) and the markdown<->Yjs codec (``markdown_yjs.py``).
It only answers "where do the top-level blocks (and, for tables, rows) sit in
this markdown string" — parsing is done once per body via ``markdown-it-py``
(the same GFM config already used by ``evals/scorers.py``), no new
markdown-parsing dependency.

Offsets are Unicode code-point indices (Python ``str`` indices already are
code-points), matching the discipline ``comment_anchor.py`` documents for
anchor offsets — nothing here should leak UTF-16 units.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from enum import Enum

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict


class BlockKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    BLOCKQUOTE = "blockquote"
    CODE_BLOCK = "code_block"
    TABLE = "table"
    THEMATIC_BREAK = "thematic_break"
    HTML_BLOCK = "html_block"
    # Anything markdown-it-py emits at level 0 that we don't have a specific
    # kind for. Range-tracking still works for an OTHER block; the markdown
    # <-> Yjs codec is the layer that must fail loud (NotImplementedError)
    # rather than silently mis-serialize one.
    OTHER = "other"


_KIND_BY_TOKEN_TYPE: dict[str, BlockKind] = {
    "heading_open": BlockKind.HEADING,
    "paragraph_open": BlockKind.PARAGRAPH,
    "bullet_list_open": BlockKind.LIST,
    "ordered_list_open": BlockKind.LIST,
    "blockquote_open": BlockKind.BLOCKQUOTE,
    "table_open": BlockKind.TABLE,
    "hr": BlockKind.THEMATIC_BREAK,
    "fence": BlockKind.CODE_BLOCK,
    "code_block": BlockKind.CODE_BLOCK,
    "html_block": BlockKind.HTML_BLOCK,
}


def gfm_parser() -> MarkdownIt:
    """The one GFM config used everywhere a wiki page's markdown gets parsed
    for structure (matches ``evals/scorers.py``'s ``markdown_valid`` scorer)."""
    return MarkdownIt("commonmark", {"html": False}).enable("table")


class RowRange(BaseModel):
    """A table row's character span within its page body. ``row_id`` is
    positional within its table (``<block_id>:r<index>``), same determinism
    contract as ``BlockRange.block_id`` below."""

    model_config = ConfigDict(frozen=True)

    row_id: str
    start: int
    end: int


class BlockRange(BaseModel):
    """A top-level block's character span within its page body.

    ``block_id`` is deterministic and purely positional (``b0``, ``b1``, ...
    in document order) — never content-hashed or random. The checkpoint
    worker recomputes identical ids from ``base_body`` alone with no
    persisted id-mapping, so two parses of the same text must always agree.

    ``separator`` (tables only) is the span between the header row and the
    first body row (or the block end, if there is no body) — the GFM
    delimiter line (``| --- | --- |``). It is not a ``tr_open`` token, so it
    is invisible to ``rows``; without capturing it separately, reconstructing
    a table purely by concatenating row text would silently drop it.
    """

    model_config = ConfigDict(frozen=True)

    block_id: str
    kind: BlockKind
    start: int
    end: int
    rows: tuple[RowRange, ...] = ()
    separator: RowRange | None = None


def _line_offsets(body: str) -> list[int]:
    """``offsets[i]`` = the character offset where line ``i`` (0-indexed)
    starts. ``markdown-it-py`` token ``.map`` values are ``[start_line,
    end_line)`` line ranges; this converts those to character offsets."""
    offsets = [0]
    for line in body.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _trim_trailing_blank_lines(body: str, start: int, end: int) -> int:
    """Some container tokens' ``.map`` (verified: ``bullet_list_open``/
    ``ordered_list_open``) includes the blank line(s) *after* the
    construct; ``paragraph_open``/``heading_open`` never do. Trimming
    uniformly here means every block's span carries the same meaning —
    "just this block's own text," with any blank-line gap always belonging
    to whatever comes after (or the trailing tail) — which is the
    invariant both the markdown<->Yjs codec's block-boundary NL handling
    and the checkpoint splice engine's gap-ownership model depend on
    (``markdown_yjs.py``, ``markdown_splice.py``). Keeps at least one line
    so a block never collapses to nothing.
    """
    lines = body[start:end].splitlines(keepends=True)
    while len(lines) > 1 and lines[-1].strip() == "":
        lines.pop()
    return start + sum(len(line) for line in lines)


def top_level_block_ranges(body: str) -> list[BlockRange]:
    """Top-level block boundaries of ``body``, in document order.

    A top-level block is any token markdown-it-py emits at nesting level 0
    that carries a line ``.map`` (an ``*_open`` token, or a self-closing one
    like ``hr``/``fence``) — nested content (list items, table cells, inline
    marks) all sit at level > 0 and is naturally excluded by that filter, no
    manual open/close tracking needed. Table blocks additionally carry
    per-row ``RowRange``s (matching ``tr_open`` tokens carry ``.map`` too,
    confirmed against the installed markdown-it-py).
    """
    if not body.strip():
        return []
    tokens = gfm_parser().parse(body)
    offsets = _line_offsets(body)
    blocks: list[BlockRange] = []
    for idx, token in enumerate(tokens):
        if token.level != 0 or token.map is None:
            continue
        start_line, end_line = token.map
        start, end = offsets[start_line], offsets[end_line]
        end = _trim_trailing_blank_lines(body, start, end)
        kind = _KIND_BY_TOKEN_TYPE.get(token.type, BlockKind.OTHER)
        block_id = f"b{len(blocks)}"

        rows: tuple[RowRange, ...] = ()
        separator: RowRange | None = None
        if kind is BlockKind.TABLE:
            # The matching table_close is the next level-0 token; every
            # tr_open strictly between the two is a row of this table.
            end_idx = next(
                (j for j in range(idx + 1, len(tokens)) if tokens[j].level == 0),
                len(tokens),
            )
            row_ranges: list[RowRange] = []
            for t in tokens[idx + 1 : end_idx]:
                if t.type == "tr_open" and t.map is not None:
                    r_start, r_end = offsets[t.map[0]], offsets[t.map[1]]
                    row_ranges.append(
                        RowRange(
                            row_id=f"{block_id}:r{len(row_ranges)}", start=r_start, end=r_end
                        )
                    )
            rows = tuple(row_ranges)
            if rows:
                sep_start = rows[0].end
                sep_end = rows[1].start if len(rows) > 1 else end
                if sep_end > sep_start:
                    separator = RowRange(row_id=f"{block_id}:sep", start=sep_start, end=sep_end)

        blocks.append(
            BlockRange(
                block_id=block_id, kind=kind, start=start, end=end, rows=rows, separator=separator
            )
        )
    return blocks


class DiffOpKind(str, Enum):
    EQUAL = "equal"
    REPLACE = "replace"
    INSERT = "insert"
    DELETE = "delete"


class BlockDiffOp(BaseModel):
    """One opcode from a block-level diff between two parses of a page.

    Used by the compat shim (``coedit.apply_range_op``) to translate a plain
    old-text/new-text range edit into "these specific blocks changed", so
    only the changed blocks' ``XmlElement``s need rebuilding in the live Yjs
    doc rather than the whole document.
    """

    model_config = ConfigDict(frozen=True)

    kind: DiffOpKind
    old_blocks: tuple[BlockRange, ...]
    new_blocks: tuple[BlockRange, ...]


def diff_blocks(
    old_body: str, old_blocks: list[BlockRange], new_body: str, new_blocks: list[BlockRange]
) -> list[BlockDiffOp]:
    """Block-level diff by block *text* content, not ``block_id`` — ids are
    purely positional within a single parse and aren't meaningful across an
    edit's before/after (a block inserted at position 2 shifts every later
    id)."""
    old_texts = [old_body[b.start : b.end] for b in old_blocks]
    new_texts = [new_body[b.start : b.end] for b in new_blocks]
    matcher = SequenceMatcher(None, old_texts, new_texts, autojunk=False)
    return [
        BlockDiffOp(
            kind=DiffOpKind(tag),
            old_blocks=tuple(old_blocks[i1:i2]),
            new_blocks=tuple(new_blocks[j1:j2]),
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    ]

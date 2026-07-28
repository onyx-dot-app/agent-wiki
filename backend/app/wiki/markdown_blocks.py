"""Top-level markdown block/row boundary parsing — pure text, no CRDT.

This is the shared foundation for the targeted-splice checkpoint engine
(``markdown_splice.py``) and the markdown<->Yjs codec (``markdown_yjs.py``).
It only answers "where do the top-level blocks (and, for tables, rows) sit in
this markdown string" — parsing is done once per body via ``markdown-it-py``.

Offsets are Unicode code-point indices (Python ``str`` indices already are
code-points) — nothing here should leak UTF-16 units.
"""

from __future__ import annotations

import re
from enum import Enum

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict

# A footnote-definition line (`[^label]: body`) — not a construct our
# gfm_parser() config recognizes (no footnote plugin enabled), so
# markdown-it-py tokenizes it as an ordinary paragraph. Detected here by
# pattern alone, before any tokenizing/escaping decision downstream, so it
# can be routed to an opaque passthrough block instead — see its call site
# in top_level_block_ranges.
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^[^\]\s]+\]:")


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
    # One single blank line between two blocks (or before the first one).
    # Blank lines produce no markdown-it token at all (confirmed: a run of
    # them is invisible to the tokenizer), so without this they're silently
    # dropped every time a doc is re-seeded from committed markdown, even
    # though a live edit session preserves them fine (each is a real,
    # separately-addressable empty paragraph node the whole time it's live).
    # No newline is ever implicit or "free" - see top_level_block_ranges and
    # EDITOR_STYLING_TRIGGERS.md: every single newline the user enters is
    # its own block boundary, full stop, so every blank line gets one of
    # these, including the first.
    BLANK_LINE = "blank_line"


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
    for structure. ``html: False`` matches
    ``frontend/src/lib/editor/AGENT_WIKI_MARKDOWN_STANDARD.md`` §5 — raw HTML tags are never
    parsed as tags, so ``html_block``/``html_inline`` tokens never appear;
    ``BlockKind.HTML_BLOCK`` above is unreachable in practice, kept only
    because ``markdown-it-py`` still exposes the token type name.
    ``strikethrough`` matches the frontend's Strike mark (StarterKit
    default, see ``EDITOR_STYLING_TRIGGERS.md`` §3) — enabled so ``~~text~~``
    round-trips as a real mark instead of literal tildes."""
    return MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")


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

    Every blank line before a block (including before the very first one)
    gets its own ``BlockRange`` — see ``BlockKind.BLANK_LINE``. No newline is
    ever implicit: N raw newlines between two blocks always means N blank
    lines, none of them "free." Not applied after the last block: trailing-
    at-EOF content already survives checkpointing unconditionally
    (``markdown_splice.checkpoint_body``'s verbatim tail slice), so there's
    nothing lossy to fix there.

    A ``paragraph_open`` token spanning multiple physical lines (CommonMark's
    own soft-break rule, joining consecutive non-blank lines into one
    paragraph) is split into one ``BlockRange`` *per line* instead of one
    for the whole span — every single newline the user enters is its own
    block boundary here, no exceptions, matching the live editor's own
    one-node-per-line model exactly. This deliberately does not (yet) apply
    inside list items, blockquotes, or code blocks — those keep today's
    behavior until they get the same treatment separately.
    """
    if not body.strip():
        return []
    tokens = gfm_parser().parse(body)
    offsets = _line_offsets(body)
    blocks: list[BlockRange] = []
    prev_end = 0
    for idx, token in enumerate(tokens):
        if token.level != 0 or token.map is None:
            continue
        start_line, end_line = token.map
        start, end = offsets[start_line], offsets[end_line]
        end = _trim_trailing_blank_lines(body, start, end)

        gap_cursor = prev_end
        for line in body[prev_end:start].splitlines(keepends=True):
            if line == "\n":
                blocks.append(
                    BlockRange(
                        block_id=f"b{len(blocks)}",
                        kind=BlockKind.BLANK_LINE,
                        start=gap_cursor,
                        end=gap_cursor + 1,
                    )
                )
                gap_cursor += 1
                continue
            # A non-blank line inside what would otherwise be a run of
            # blank-line gaps: something markdown-it-py consumed without
            # emitting any token at all — a link reference definition is
            # the common case (it's metadata, not rendered content, so it
            # produces no block-level token; confirmed against the
            # installed markdown-it-py). Treating the whole gap as "just
            # blank lines" left this text uncovered by any block, which
            # checkpoint_body then silently dropped the moment a
            # neighboring block was touched (confirmed in review — a real
            # data-loss bug, not cosmetic). Capture it as its own opaque
            # block instead: build_block_element's fallback branch already
            # round-trips an OTHER-kind block byte-for-byte verbatim.
            blocks.append(
                BlockRange(
                    block_id=f"b{len(blocks)}",
                    kind=BlockKind.OTHER,
                    start=gap_cursor,
                    end=gap_cursor + len(line),
                )
            )
            gap_cursor += len(line)

        kind = _KIND_BY_TOKEN_TYPE.get(token.type, BlockKind.OTHER)

        if kind is BlockKind.PARAGRAPH and end_line == start_line + 1:
            raw = body[start:end]
            if _FOOTNOTE_DEF_RE.match(raw):
                # A footnote definition (`[^label]: body`) — our
                # gfm_parser() has no footnote plugin, so this tokenizes as
                # a plain paragraph, and the normal paragraph path would
                # feed its text through inline-mark escaping, corrupting
                # `[^label]:` into `\[^label\]:` the moment anything in
                # this block is touched (confirmed in review). Route it
                # through the same opaque, byte-verbatim passthrough as a
                # link reference definition instead — not real footnote
                # *support* (no inline `[^label]` reference handling, no
                # multi-line body), just no longer silently damaging one
                # single-line definition's own text.
                blocks.append(
                    BlockRange(block_id=f"b{len(blocks)}", kind=BlockKind.OTHER, start=start, end=end)
                )
                prev_end = end
                continue

        if kind is BlockKind.PARAGRAPH:
            # Split on every line ending EXCEPT a hard break (CommonMark:
            # 2+ trailing spaces, or a trailing backslash, immediately
            # before the line ending) - a hard break is never ambiguous the
            # way a bare newline (soft break) is, so those lines stay
            # joined in one block, exactly as already built (hardBreak
            # sibling element) and tested.
            seg_start_line = start_line
            for line_no in range(start_line, end_line):
                line_text = body[offsets[line_no] : offsets[line_no + 1]]
                without_nl = line_text[:-1] if line_text.endswith("\n") else line_text
                is_hard_break = line_no < end_line - 1 and (
                    without_nl.endswith("\\") or without_nl.endswith("  ")
                )
                if is_hard_break:
                    continue
                blocks.append(
                    BlockRange(
                        block_id=f"b{len(blocks)}",
                        kind=BlockKind.PARAGRAPH,
                        start=offsets[seg_start_line],
                        end=offsets[line_no + 1],
                    )
                )
                seg_start_line = line_no + 1
            prev_end = end
            continue

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
                        RowRange(row_id=f"{block_id}:r{len(row_ranges)}", start=r_start, end=r_end)
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
        prev_end = end
    return blocks

"""Markdown <-> Yjs codec: the live document representation for co-editing.

Every top-level block (``markdown_blocks.top_level_block_ranges``) becomes an
``XmlElement`` in a ``pycrdt`` doc's root ``XmlFragment``, tagged with a
stable, positional ``_blockId`` attribute. Structural treatment (real
ProseMirror-shaped nodes, editable node-by-node, not opaque text) covers:
``heading``, ``paragraph`` (inline content — text runs + bold/italic/code/
link marks, represented via a ``pycrdt.XmlText``'s ``.format()`` runs, plus
an explicit ``hardBreak`` leaf element interspersed as a sibling wherever a
hard line break occurs — y-prosemirror maps a PM leaf/atom node to an empty
sibling ``XmlElement``, not to a text mark, since a break is a node boundary,
not formatting), ``bulletList``/``orderedList``/``listItem`` (arbitrarily
nested — CommonMark's own grammar is already recursive here, so supporting
depth costs about the same as supporting one level), ``taskList``/
``taskItem`` (a GFM checkbox list — a plain bullet list whose items *all*
start with a ``[ ]``/``[x]`` marker; a mixed list stays a regular
``bulletList`` since a taskList's children must be uniformly taskItems — see
``_build_list``), ``blockquote`` (a sequence of paragraph/list/blockquote
children, so multi-paragraph quotes and quotes containing lists work), and
``codeBlock`` (a ``language`` attribute + plain text content, fence syntax
stripped — not stored as opaque markup). Tables get row-level structure
(each row is its own ``XmlElement`` tagged ``_rowId``, so a single-cell edit
only reflows its own row, not the whole table) but each row's *content* is
still stored as opaque verbatim text, not decomposed into cells —
deliberately simpler than per-cell reconstruction with recomputed column
padding, and still achieves the byte-stability goal for every row that isn't
touched. Real per-cell table editing, images, footnotes, and emoji
shortcodes are explicitly out of scope for this pass (see
``docs/AGENT_WIKI_MARKDOWN_STANDARD.md``'s deferred items); thematic break
and html block stay opaque verbatim, tagged ``_raw="1"``.

Unrecognized inline constructs (an image, GFM strikethrough — anything this
module doesn't have an explicit encoder for) raise ``NotImplementedError``
rather than silently drop or mis-serialize content — the byte-stability
requirement this whole engine exists for is only meaningful if failures are
loud, never silent. Same for a list item that isn't a clean sequence of
paragraph/list/blockquote children (e.g. a list item containing a table) —
unsupported, raises rather than mis-encodes.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pycrdt import Doc, XmlElement, XmlFragment, XmlText
from pydantic import BaseModel, ConfigDict

from app.wiki.markdown_blocks import BlockKind, BlockRange, gfm_parser, top_level_block_ranges

# Root-fragment key. Must match the frontend PM schema's y-prosemirror
# `field` config exactly (see frontend/src/lib/editor/schema.ts).
ROOT_XML_KEY = "prosemirror"

_NL_ATTR = "_nl"  # "1" if this block's raw text ends with a trailing newline
_RAW_ATTR = "_raw"  # "1" for an opaque verbatim-text block
BLOCK_ID_ATTR = "_blockId"
ROW_ID_ATTR = "_rowId"

_KNOWN_INLINE_TYPES = {
    "text",
    "softbreak",
    "hardbreak",
    "strong_open",
    "strong_close",
    "em_open",
    "em_close",
    "code_inline",
    "link_open",
    "link_close",
}

# Applied innermost-first when wrapping a diff run back into markdown syntax
# (i.e. iterated in reverse of this tuple) — outer to inner: link, bold,
# italic, code. Handles arbitrary combinations deterministically; not every
# combination is meaningfully round-trippable in CommonMark (e.g. code spans
# can't semantically nest other marks), but this never raises — an
# unsupported *combination* degrades to best-effort markup, only a fully
# unrecognized inline *construct* (see _KNOWN_INLINE_TYPES) raises.
_MARK_WRAP_ORDER = ("link", "bold", "italic", "code")

# A text segment ("text", plain_text, mark_runs) or a hard-break leaf
# ("hardbreak", None, None) — see module docstring for why a hard break is a
# sibling element, not foldable into a text run's marks.
_Segment = tuple[Literal["text", "hardbreak"], str | None, list[tuple[int, int, dict[str, Any]]] | None]


def _inline_runs(inline_token: Any) -> list[_Segment]:
    """Walk a markdown-it ``inline`` token's children into ordered segments —
    text runs (with their mark spans) interspersed with hard-break leaves."""
    segments: list[_Segment] = []
    parts: list[str] = []
    pos = 0
    runs: list[tuple[int, int, dict[str, Any]]] = []
    active: dict[str, Any] = {}

    def _flush_text() -> None:
        nonlocal parts, pos, runs
        if parts:
            segments.append(("text", "".join(parts), runs))
        parts, pos, runs = [], 0, []

    def _emit(content: str, attrs: dict[str, Any]) -> None:
        nonlocal pos
        if content and attrs:
            runs.append((pos, pos + len(content), dict(attrs)))
        parts.append(content)
        pos += len(content)

    for child in inline_token.children or []:
        if child.type not in _KNOWN_INLINE_TYPES:
            raise NotImplementedError(f"unrecognized inline construct: {child.type!r}")
        if child.type == "hardbreak":
            _flush_text()
            segments.append(("hardbreak", None, None))
        elif child.type == "text":
            _emit(child.content, active)
        elif child.type == "softbreak":
            _emit("\n", active)
        elif child.type == "strong_open":
            active = {**active, "bold": True}
        elif child.type == "strong_close":
            active = {k: v for k, v in active.items() if k != "bold"}
        elif child.type == "em_open":
            active = {**active, "italic": True}
        elif child.type == "em_close":
            active = {k: v for k, v in active.items() if k != "italic"}
        elif child.type == "code_inline":
            _emit(child.content, {**active, "code": True})
        elif child.type == "link_open":
            # y-prosemirror treats a mark's XmlText-format value as that
            # mark's *attrs object* — verified directly against the real
            # library. A bare href string decodes as an attrs object with
            # every string index as a key, silently producing an empty
            # href. Must be `{href: ...}`.
            active = {**active, "link": {"href": child.attrs.get("href", "")}}
        elif child.type == "link_close":
            active = {k: v for k, v in active.items() if k != "link"}

    _flush_text()
    return segments


def _escape_inline_text(text: str) -> str:
    """Escape characters markdown-it consumes at parse time — `\\*x\\*`
    parses to the *text* `*x*` with no italic mark (correctly, since it was
    escaped), but re-serializing that text verbatim hands back active
    syntax on the next parse (`*x*` now reads as real emphasis). Same
    failure mode for `` \\` ``, `\\_`, `\\[`, `\\]`. Backslash itself goes
    first, so escaping the other characters doesn't get double-escaped."""
    for ch in "\\`*_[]":
        text = text.replace(ch, "\\" + ch)
    return text


def _wrap_run(text: str, attrs: dict[str, Any] | None) -> str:
    attrs = attrs or {}
    # Inline code spans are verbatim — CommonMark never processes escapes
    # inside them, so escaping here would corrupt the code's actual text
    # (a literal backslash would become part of the visible content).
    if "code" not in attrs:
        text = _escape_inline_text(text)
    if not attrs:
        return text
    result = text
    for mark in reversed(_MARK_WRAP_ORDER):
        if mark not in attrs:
            continue
        if mark == "code":
            result = f"`{result}`"
        elif mark == "italic":
            result = f"*{result}*"
        elif mark == "bold":
            result = f"**{result}**"
        elif mark == "link":
            # attrs["link"] is {"href": ...} (matching y-prosemirror's
            # mark-attrs convention) — but also accept a bare string
            # defensively in case something upstream ever writes the old
            # shape.
            link_attrs = attrs["link"]
            href = link_attrs["href"] if isinstance(link_attrs, dict) else link_attrs
            result = f"[{result}]({href})"
    return result


def _serialize_inline_text(xt: XmlText) -> str:
    return "".join(_wrap_run(text, attrs) for text, attrs in xt.diff())


def _serialize_inline_children(children: list[Any]) -> str:
    """Inverse of ``_element_from_segments``: walks a paragraph/heading's
    ``contents`` list, which may interleave ``XmlText`` runs with
    ``hardBreak`` leaf elements."""
    parts: list[str] = []
    for child in children:
        if isinstance(child, XmlText):
            parts.append(_serialize_inline_text(child))
        elif isinstance(child, XmlElement) and child.tag == "hardBreak":
            # Canonical form regardless of whether the source used trailing
            # double-space or backslash syntax — same "correct, not
            # necessarily byte-identical" tradeoff already accepted
            # elsewhere in this module for touched blocks.
            parts.append("  \n")
        else:
            raise NotImplementedError(f"unrecognized inline child: {child!r}")
    return "".join(parts)


def _element_from_segments(
    tag: str, attrs: dict[str, str], segments: list[_Segment]
) -> tuple[XmlElement, list[Any]]:
    """Build a heading/paragraph element from ``_inline_runs`` segments.
    Marks are applied via ``.format()`` once each text node is integrated
    (pycrdt requires integration before ``.insert``/``.format``), so this
    returns finisher callbacks to run post-integration, same pattern as the
    rest of this module."""
    contents: list[Any] = []
    finishers: list[Any] = []
    for kind, text, runs in segments:
        if kind == "hardbreak":
            contents.append(XmlElement("hardBreak", {}))
        else:
            xt = XmlText(text or "")
            contents.append(xt)
            if runs:
                finishers.append(lambda xt=xt, runs=runs: _apply_runs(xt, runs))
    if not contents:
        contents = [XmlText("")]
    return XmlElement(tag, attrs, contents=contents), finishers


def _make_inline_element(
    tag: str, attrs: dict[str, str], line: str
) -> tuple[XmlElement, list[Any]]:
    """Build a heading/paragraph element: parse ``line`` standalone to get
    its inline token, then delegate to ``_element_from_segments``.

    ``line`` can be empty — e.g. a heading block whose text was stripped
    down to nothing (``"# "`` with no title after it, a real state while
    editing, not a hypothetical). Parsing an empty string standalone
    produces zero tokens at all (no ``inline`` token, unlike parsing the
    unstripped ``"# "`` line, which still gets one with empty content) —
    confirmed directly. A block with genuinely empty inline content is
    just an empty text node with no mark runs, not an error."""
    mini_tokens = gfm_parser().parse(line)
    inline_token = next((t for t in mini_tokens if t.type == "inline"), None)
    if inline_token is None:
        return XmlElement(tag, attrs, contents=[XmlText("")]), []
    return _element_from_segments(tag, attrs, _inline_runs(inline_token))


def _apply_runs(xt: XmlText, runs: list[tuple[int, int, dict[str, Any]]]) -> None:
    for start, end, attrs in runs:
        if start < end:
            xt.format(start, end, attrs)


def _matching_close(tokens: list[Any], open_idx: int, close_type: str) -> int:
    """Index of the token that closes ``tokens[open_idx]`` — the next token
    of ``close_type`` at the same nesting level."""
    level = tokens[open_idx].level
    j = open_idx + 1
    while not (tokens[j].type == close_type and tokens[j].level == level):
        j += 1
    return j


def _build_paragraph_from_inline(inline_token: Any) -> tuple[XmlElement, list[Any]]:
    return _element_from_segments("paragraph", {}, _inline_runs(inline_token))


def _build_block_sequence(tokens: list[Any], start: int, end: int) -> tuple[list[XmlElement], list[Any]]:
    """Build a sequence of paragraph/list/blockquote child elements from a
    flat markdown-it token range ``[start, end)`` — used for list-item and
    blockquote content, recursing into ``_build_list``/``_build_blockquote``
    for nested containers. This is what lets a list item contain multiple
    paragraphs or a nested list, and a blockquote contain multiple
    paragraphs or a list, with the same code path either way."""
    children: list[XmlElement] = []
    finishers: list[Any] = []
    i = start
    while i < end:
        t = tokens[i]
        if t.type == "paragraph_open":
            el, para_finishers = _build_paragraph_from_inline(tokens[i + 1])
            children.append(el)
            finishers.extend(para_finishers)
            i += 3  # paragraph_open, inline, paragraph_close
            continue
        if t.type in ("bullet_list_open", "ordered_list_open"):
            close_idx = _matching_close(tokens, i, t.type.replace("_open", "_close"))
            el, list_finishers = _build_list(tokens, i, close_idx + 1)
            children.append(el)
            finishers.extend(list_finishers)
            i = close_idx + 1
            continue
        if t.type == "blockquote_open":
            close_idx = _matching_close(tokens, i, "blockquote_close")
            el, bq_finishers = _build_blockquote(tokens, i, close_idx + 1)
            children.append(el)
            finishers.extend(bq_finishers)
            i = close_idx + 1
            continue
        raise NotImplementedError(f"unsupported nested block construct: {t.type!r}")
    return children, finishers


# GFM task-list marker: "[ ] "/"[x] "/"[X] " at the very start of a list
# item's first paragraph. No dedicated task-list plugin is enabled on
# `gfm_parser()` (see markdown_blocks.py), so this is recognized as plain
# inline text and matched by hand rather than via a token type.
_TASK_MARKER_RE = re.compile(r"^\[([ xX])\](?:\s+|$)")


def _list_item_task_marker(tokens: list[Any], item_start: int, item_end: int) -> re.Match[str] | None:
    """If a list item's first block is a paragraph beginning with a
    checkbox marker, the match against that paragraph's leading text run;
    else ``None``. ``item_start``/``item_end`` bracket the item's own
    content tokens (i.e. *excluding* its `list_item_open`/`_close`)."""
    if item_end - item_start < 3 or tokens[item_start].type != "paragraph_open":
        return None
    inline = tokens[item_start + 1]
    if inline.type != "inline" or not inline.children:
        return None
    first_child = inline.children[0]
    if first_child.type != "text":
        return None
    return _TASK_MARKER_RE.match(first_child.content)


def _build_list(
    tokens: list[Any], start: int, end: int, *, extra_attrs: dict[str, str] | None = None
) -> tuple[XmlElement, list[Any]]:
    open_tok = tokens[start]
    ordered = open_tok.type == "ordered_list_open"

    item_ranges: list[tuple[int, int]] = []
    i = start + 1
    while i < end - 1:  # exclude the outer list's own close token
        t = tokens[i]
        if t.type == "list_item_open":
            close_idx = _matching_close(tokens, i, "list_item_close")
            item_ranges.append((i + 1, close_idx))
            i = close_idx + 1
            continue
        raise NotImplementedError(f"unexpected token inside list: {t.type!r}")

    # A bullet list becomes a task list only when *every* item carries a
    # checkbox marker — a taskList schema requires uniform taskItem
    # children, so a mixed list (some items marked, some not) can't become
    # one; it stays a plain bulletList with the literal "[ ] "/"[x] " text
    # visible.
    task_matches = (
        None if ordered else [_list_item_task_marker(tokens, s, e) for s, e in item_ranges]
    )
    is_task_list = bool(item_ranges) and task_matches is not None and all(task_matches)

    attrs = dict(extra_attrs or {})
    if is_task_list:
        tag = "taskList"
    else:
        tag = "orderedList" if ordered else "bulletList"
        if ordered:
            attrs["start"] = str(open_tok.attrs.get("start", 1))

    items: list[XmlElement] = []
    finishers: list[Any] = []
    for idx, (item_start, item_end) in enumerate(item_ranges):
        if is_task_list:
            match = task_matches[idx]  # type: ignore[index]
            assert match is not None
            checked = match.group(1).lower() == "x"
            first_text = tokens[item_start + 1].children[0]
            first_text.content = first_text.content[match.end() :]
            item_children, item_finishers = _build_block_sequence(tokens, item_start, item_end)
            items.append(
                XmlElement(
                    "taskItem", {"checked": "true" if checked else "false"}, contents=item_children
                )
            )
        else:
            item_children, item_finishers = _build_block_sequence(tokens, item_start, item_end)
            items.append(XmlElement("listItem", {}, contents=item_children))
        finishers.extend(item_finishers)

    return XmlElement(tag, attrs, contents=items), finishers


def _build_blockquote(
    tokens: list[Any], start: int, end: int, *, extra_attrs: dict[str, str] | None = None
) -> tuple[XmlElement, list[Any]]:
    children, finishers = _build_block_sequence(tokens, start + 1, end - 1)
    return XmlElement("blockquote", dict(extra_attrs or {}), contents=children), finishers


def _build_code_block(raw: str, attrs: dict[str, str]) -> XmlElement:
    tok = next(t for t in gfm_parser().parse(raw) if t.type in ("fence", "code_block"))
    language = tok.info.strip() if tok.type == "fence" else ""
    return XmlElement(
        "codeBlock", {**attrs, "language": language}, contents=[XmlText(tok.content)]
    )


def _build_heading(raw: str, attrs: dict[str, str]) -> tuple[XmlElement, list[Any]]:
    """Parses ``raw`` through the real tokenizer rather than hand-stripping
    a leading ``#`` run — a setext heading (``Title\\n=====\\n``) has no
    leading hashes at all, and a hand-rolled ATX-only strip both mis-levels
    it (falls through to ``level=0``, which then serializes with no ``#``
    marker at all — silently demoting the heading to a paragraph) and
    leaves the underline line embedded in the parsed title text. The
    tokenizer's own ``heading_open.tag`` (``"h1"``..``"h6"``) is correct for
    both styles, and its ``inline`` token's content already excludes
    whichever prefix/underline syntax produced it — including the
    empty-title case (``"# "`` with nothing after it still yields an
    ``inline`` token with empty content when parsing the *unstripped* raw
    block, unlike parsing a manually-stripped empty string standalone,
    which yields no inline token at all)."""
    tokens = gfm_parser().parse(raw)
    open_idx = next(i for i, t in enumerate(tokens) if t.type == "heading_open")
    level = int(tokens[open_idx].tag[1:])
    inline_token = tokens[open_idx + 1]
    return _element_from_segments(
        "heading", {**attrs, "level": str(level)}, _inline_runs(inline_token)
    )


def _build_block_element(body: str, block: BlockRange) -> tuple[XmlElement, list[Any]]:
    """Returns the (prelim) element plus a list of "finish" callbacks to run
    once the element is integrated into a doc (mark ``.format()`` calls need
    an already-integrated ``XmlText``)."""
    raw = body[block.start : block.end]
    trailing_nl = raw.endswith("\n")
    nl_attr = "1" if trailing_nl else "0"

    if block.kind is BlockKind.HEADING:
        return _build_heading(raw, {BLOCK_ID_ATTR: block.block_id, _NL_ATTR: nl_attr})

    if block.kind is BlockKind.PARAGRAPH:
        line = raw[:-1] if trailing_nl else raw
        return _make_inline_element(
            "paragraph", {BLOCK_ID_ATTR: block.block_id, _NL_ATTR: nl_attr}, line
        )

    if block.kind is BlockKind.LIST:
        tokens = gfm_parser().parse(raw)
        el, finishers = _build_list(
            tokens, 0, len(tokens), extra_attrs={BLOCK_ID_ATTR: block.block_id, _NL_ATTR: nl_attr}
        )
        return el, finishers

    if block.kind is BlockKind.BLOCKQUOTE:
        tokens = gfm_parser().parse(raw)
        el, finishers = _build_blockquote(
            tokens, 0, len(tokens), extra_attrs={BLOCK_ID_ATTR: block.block_id, _NL_ATTR: nl_attr}
        )
        return el, finishers

    if block.kind is BlockKind.CODE_BLOCK:
        el = _build_code_block(raw, {BLOCK_ID_ATTR: block.block_id, _NL_ATTR: nl_attr})
        return el, []

    if block.kind is BlockKind.TABLE:
        row_children: list[XmlElement] = []
        for row in block.rows:
            row_children.append(
                XmlElement(
                    "tableRow",
                    {ROW_ID_ATTR: row.row_id},
                    contents=[XmlText(body[row.start : row.end])],
                )
            )
            if block.separator is not None and row.row_id == block.rows[0].row_id:
                row_children.append(
                    XmlElement(
                        "tableSeparator",
                        {ROW_ID_ATTR: block.separator.row_id},
                        contents=[XmlText(body[block.separator.start : block.separator.end])],
                    )
                )
        el = XmlElement("table", {BLOCK_ID_ATTR: block.block_id}, contents=row_children)
        return el, []

    # Opaque verbatim passthrough: thematic_break, html_block, other.
    el = XmlElement(
        block.kind.value,
        {BLOCK_ID_ATTR: block.block_id, _RAW_ATTR: "1"},
        contents=[XmlText(raw)],
    )
    return el, []


def seed_doc_from_markdown(body: str) -> Doc:
    """Build a fresh ``pycrdt.Doc`` whose root ``XmlFragment`` represents
    every top-level block of ``body``, in document order."""
    doc = Doc()
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    blocks = top_level_block_ranges(body)
    with doc.transaction():
        for block in blocks:
            el, finishers = _build_block_element(body, block)
            root.children.append(el)
            for finish in finishers:
                finish()
    return doc


def find_by_block_id(doc: Doc, block_id: str) -> XmlElement | None:
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    for child in root.children:
        if isinstance(child, XmlElement) and dict(child.attributes).get(BLOCK_ID_ATTR) == block_id:
            return child
    return None


def find_by_row_id(doc: Doc, row_id: str) -> XmlElement | None:
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    for child in root.children:
        if not isinstance(child, XmlElement) or child.tag != "table":
            continue
        for row in child.children:
            if dict(row.attributes).get(ROW_ID_ATTR) == row_id:
                return row
    return None


def serialize_row(row: XmlElement) -> str:
    return row.children[0].to_py()  # type: ignore[return-value]


def _serialize_block_sequence(children: list[XmlElement], indent: str) -> str:
    """Inverse of ``_build_block_sequence``: paragraph/list/blockquote
    children joined by blank lines, with every line after the very first
    indented by ``indent`` so continuation lines / nested constructs align
    under the parent marker or blockquote ``>``."""
    parts: list[str] = []
    for child in children:
        if child.tag == "paragraph":
            parts.append(_serialize_inline_children(list(child.children)))
        elif child.tag in ("bulletList", "orderedList", "taskList"):
            parts.append(_serialize_list(child).rstrip("\n"))
        elif child.tag == "blockquote":
            parts.append(_serialize_blockquote(child).rstrip("\n"))
        else:
            raise NotImplementedError(f"unsupported nested block in sequence: tag={child.tag!r}")
    combined = "\n\n".join(parts)
    lines = combined.split("\n")
    indented = [lines[0]] + [(indent + line if line else line) for line in lines[1:]]
    return "\n".join(indented)


def _serialize_list(node: XmlElement) -> str:
    """Serializes every list as CommonMark "loose" style (blank line between
    items), even if the source was "tight" (no blank lines) — tight vs.
    loose is purely an HTML-rendering distinction (whether item content gets
    wrapped in ``<p>``); the item *text* is identical either way, so this
    never changes a page's actual content. Not byte-identical for a touched
    list, same accepted tradeoff as ordered-list renumbering — only
    untouched blocks carry the byte-stability guarantee
    (``markdown_splice.py``), which never calls this serializer at all.
    """
    ordered = node.tag == "orderedList"
    is_task = node.tag == "taskList"
    attrs = dict(node.attributes)
    start = int(attrs.get("start", "1")) if ordered else 1
    lines: list[str] = []
    for idx, item in enumerate(node.children):
        if is_task:
            checked = dict(item.attributes).get("checked") == "true"
            marker = f"- [{'x' if checked else ' '}] "
        elif ordered:
            marker = f"{start + idx}. "
        else:
            marker = "- "
        body = _serialize_block_sequence(list(item.children), " " * len(marker))  # type: ignore[arg-type]
        lines.append(marker + body)
    return "\n\n".join(lines) + "\n"


def _serialize_blockquote(node: XmlElement) -> str:
    body = _serialize_block_sequence(list(node.children), "")  # type: ignore[arg-type]
    lines = body.split("\n")
    return "\n".join(("> " + line if line else ">") for line in lines) + "\n"


def _serialize_code_block(node: XmlElement) -> str:
    """Always emits fenced syntax, even if the source was an indented code
    block — semantically identical, and simpler/more robust than
    reconstructing 4-space indentation. Same non-byte-identical-but-correct
    tradeoff as ``_serialize_list``."""
    attrs = dict(node.attributes)
    language = attrs.get("language", "")
    content = node.children[0].to_py()  # type: ignore[union-attr]
    fence = "```"
    while fence in content:
        fence += "`"
    return f"{fence}{language}\n{content}{fence}\n"


# Ordered-list marker: 1-9 digits + "." or ")" + a space or end of line —
# matches CommonMark's own marker grammar (see the list-building side,
# `_build_list`, which reads `open_tok.attrs["start"]` rather than
# re-deriving this pattern; this is the inverse direction, detecting the
# pattern in already-serialized plain text).
_ORDERED_MARKER_RE = re.compile(r"^\d{1,9}[.)](\s|$)")

# A thematic break is a *whole line* of 3+ "-" (optionally space/tab
# separated) and nothing else — "---" alone reactivates, but "--- and more
# text" doesn't (confirmed against the forward parse, not assumed: a
# trailing non-marker character on the line makes it an ordinary
# paragraph). "*"/"_" thematic breaks ("***", "_ _ _") need no matching
# case — every "*"/"_" is already escaped unconditionally by `_wrap_run`
# for the emphasis/italic reason, which breaks the run regardless of
# position.
_THEMATIC_BREAK_DASH_RE = re.compile(r"^-(?:[ \t]*-){2,}[ \t]*(?:\n|$)")


def _escape_block_start_ambiguity(text: str) -> str:
    """A paragraph's serialized text starting with a character or pattern
    that's only special as a *block*-start marker (heading ``#``, bullet
    ``-``/``+``, thematic break ``---``, blockquote ``>``, ordered-list
    ``1.``) must stay escaped, or the next parse reinterprets this
    paragraph as a different block type entirely. Unlike ``_wrap_run``'s
    mark-delimiter escaping (position-independent — a ``*``/``_``/`` ` ``/
    ``[``/``]`` is ambiguous anywhere in the text, already handled there),
    these are only ambiguous at the very start of the block, which is
    exactly the one position this function runs at. ``*`` as a bullet
    marker doesn't need a case here — it's already escaped unconditionally
    by ``_wrap_run`` for the emphasis reason, which covers this position
    too as a side effect."""
    if not text:
        return text
    if text[0] == "#":
        return "\\" + text
    if text[0] == "-" and _THEMATIC_BREAK_DASH_RE.match(text):
        return "\\" + text
    if text[0] in "-+" and (len(text) == 1 or text[1].isspace()):
        return "\\" + text
    if text[0] == ">":
        return "\\" + text
    if _ORDERED_MARKER_RE.match(text):
        return "\\" + text
    return text


def serialize_block(node: XmlElement) -> str:
    attrs = dict(node.attributes)
    trailing = attrs.get(_NL_ATTR) == "1"

    if node.tag == "heading":
        level = int(attrs["level"])
        text = _serialize_inline_children(list(node.children))
        return "#" * level + " " + text + ("\n" if trailing else "")
    if node.tag == "paragraph":
        text = _escape_block_start_ambiguity(_serialize_inline_children(list(node.children)))
        return text + ("\n" if trailing else "")
    if node.tag in ("bulletList", "orderedList", "taskList"):
        text = _serialize_list(node)
        return text if trailing else text.rstrip("\n")
    if node.tag == "blockquote":
        text = _serialize_blockquote(node)
        return text if trailing else text.rstrip("\n")
    if node.tag == "codeBlock":
        text = _serialize_code_block(node)
        return text if trailing else text.rstrip("\n")
    if node.tag == "table":
        return "".join(serialize_row(row) for row in node.children)
    if attrs.get(_RAW_ATTR) == "1":
        return serialize_row(node)

    raise NotImplementedError(f"unrecognized block construct: tag={node.tag!r}")


class BlockSpan(BaseModel):
    """One top-level block's character span within a
    ``reconstruct_body``-style reserialize, keyed by its stable
    ``_blockId``. Lets a caller translate a flat offset into that
    reserialized text back into ``{block_id, offset_within_block}`` against
    the *same* live tree the text was just serialized from — critical for
    comment/source anchor resolution, where matching block ids computed
    from two independently-parsed document versions would not correspond to
    the same block (see
    ``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``)."""

    model_config = ConfigDict(frozen=True)

    block_id: str
    start: int
    end: int


def reconstruct_body(doc: Doc) -> str:
    """Full whole-document reserialize — a last-resort fallback. Never used
    by the normal targeted-splice checkpoint path (see
    ``markdown_splice.py``), since a whole-doc reserialize doesn't guarantee
    byte-stability for untouched regions if any block's serializer doesn't
    exactly reproduce its source (safe-but-lossy: correct output, not
    necessarily byte-identical).
    """
    return reconstruct_body_with_block_map(doc)[0]


def reconstruct_body_with_block_map(doc: Doc) -> tuple[str, list[BlockSpan]]:
    """Like ``reconstruct_body``, but also returns each top-level block's
    character span within the returned text. See ``BlockSpan``.

    Each block's own serialized text already carries its own trailing
    newline (``BlockRange.end`` is inclusive of it, per
    ``markdown_blocks.py``) — bare concatenation would silently merge
    adjacent blocks (e.g. two paragraphs collapse into one) rather than just
    losing exact spacing, so a blank-line separator is inserted between
    blocks whose own text doesn't already end in one (a raw/opaque block's
    captured span sometimes already includes its trailing blank line;
    headings/paragraphs never do, per ``_build_block_element``). A table's
    span covers its whole reserialized text (every row concatenated) — rows
    aren't tracked individually, matching this codec's row-level (not
    per-cell) granularity everywhere else.
    """
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    parts: list[str] = []
    spans: list[BlockSpan] = []
    pos = 0
    for child in root.children:
        if parts and not parts[-1].endswith("\n\n"):
            parts.append("\n")
            pos += 1
        text = serialize_block(child)  # type: ignore[arg-type]
        block_id = dict(child.attributes).get(BLOCK_ID_ATTR)  # type: ignore[union-attr]
        start = pos
        parts.append(text)
        pos += len(text)
        if block_id is not None:
            spans.append(BlockSpan(block_id=block_id, start=start, end=pos))
    return "".join(parts), spans

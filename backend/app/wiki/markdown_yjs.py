"""Markdown <-> Yjs codec: the live document representation for co-editing.

Every top-level block (``markdown_blocks.top_level_block_ranges``) becomes an
``XmlElement`` in a ``pycrdt`` doc's root ``XmlFragment``, tagged with a
stable, positional ``_blockId`` attribute. Structural treatment (real
Tiptap-shaped nodes, editable node-by-node, not opaque text) covers:
``heading``, ``paragraph`` (inline content — text runs + bold/italic/code/
link marks — represented directly in a ``pycrdt.XmlText`` via ``.format()``
runs), ``bulletList``/``orderedList``/``listItem`` (arbitrarily nested —
CommonMark's own grammar is already recursive here, so supporting depth cost
about the same as supporting one level) and ``blockquote`` (a sequence of
paragraph/list/blockquote children, so multi-paragraph quotes and quotes
containing lists work), and ``codeBlock`` (a ``language`` attribute + plain
text content, fence syntax stripped — not stored as opaque markup). Tables
get row-level structure (each row is its own ``XmlElement`` tagged
``_rowId``, matching the design doc's requirement that a single-cell edit
only reflows its own row, not the whole table) but each row's *content* is
still stored as opaque verbatim text, not decomposed into cells —
deliberately simpler than per-cell reconstruction with recomputed column
padding, and still achieves the byte-stability goal for every row that
isn't touched (see ``plans/onyx-editor.md``'s "Splice-granularity
decision"). Real per-cell table editing, images, and thematic
break/html-block structure are explicitly out of scope for this pass
(tables/images/slash-commands are absent from the CM6 editor being
replaced too — not a regression); thematic break and html block stay
opaque verbatim, tagged ``_raw="1"``.

Unrecognized inline constructs (an image, a hard line break, GFM
strikethrough — anything this module doesn't have an explicit encoder for)
raise ``NotImplementedError`` rather than silently drop or mis-serialize
content — the byte-stability requirement this whole engine exists for is
only meaningful if failures are loud, never silent. Same for a list item
that isn't a clean sequence of paragraph/list/blockquote children (e.g. a
list item containing a table) — unsupported, raises rather than mis-encodes.
"""

from __future__ import annotations

from typing import Any

from pycrdt import Doc, XmlElement, XmlFragment, XmlText

from app.wiki.markdown_blocks import BlockKind, BlockRange, gfm_parser, top_level_block_ranges

# Placeholder root-fragment key. Phase 2's Tiptap `Collaboration` extension
# config must be set to match this exactly — confirm/adjust together with
# the frontend work when that schema exists; not guessed here.
ROOT_XML_KEY = "prosemirror"

_NL_ATTR = "_nl"  # "1" if this block's raw text ends with a trailing newline
_RAW_ATTR = "_raw"  # "1" for an opaque verbatim-text block
BLOCK_ID_ATTR = "_blockId"
ROW_ID_ATTR = "_rowId"

_KNOWN_INLINE_TYPES = {
    "text",
    "softbreak",
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


def _inline_runs(inline_token: Any) -> tuple[str, list[tuple[int, int, dict[str, Any]]]]:
    """Walk a markdown-it ``inline`` token's children, returning the plain
    text content and the (start, end, attrs) mark runs within it."""
    parts: list[str] = []
    pos = 0
    runs: list[tuple[int, int, dict[str, Any]]] = []
    active: dict[str, Any] = {}

    def _emit(content: str, attrs: dict[str, Any]) -> None:
        nonlocal pos
        if content and attrs:
            runs.append((pos, pos + len(content), dict(attrs)))
        parts.append(content)
        pos += len(content)

    for child in inline_token.children or []:
        if child.type not in _KNOWN_INLINE_TYPES:
            raise NotImplementedError(f"unrecognized inline construct: {child.type!r}")
        if child.type == "text":
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
            # y-prosemirror (which Tiptap's Collaboration extension uses to
            # read a Y.Doc back into a ProseMirror doc) treats a mark's
            # XmlText-format value as that mark's *attrs object* — verified
            # directly against the real library, not assumed. A bare href
            # string decodes as an attrs object with every string index as
            # a key (e.g. `{0: "h", 1: "t", ...}`), silently producing an
            # empty href. Must be `{href: ...}`, matching Tiptap's own Link
            # mark's attrs shape exactly.
            active = {**active, "link": {"href": child.attrs.get("href", "")}}
        elif child.type == "link_close":
            active = {k: v for k, v in active.items() if k != "link"}

    return "".join(parts), runs


def _wrap_run(text: str, attrs: dict[str, Any] | None) -> str:
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
            # attrs["link"] is {"href": ...} (an object, matching Tiptap's
            # Link mark and y-prosemirror's mark-attrs convention) — but a
            # live Tiptap client's own edits land here the same way via
            # pycrdt-websocket, so also accept a bare string defensively in
            # case something upstream ever writes the old shape.
            link_attrs = attrs["link"]
            href = link_attrs["href"] if isinstance(link_attrs, dict) else link_attrs
            result = f"[{result}]({href})"
    return result


def _serialize_inline(xt: XmlText) -> str:
    return "".join(_wrap_run(text, attrs) for text, attrs in xt.diff())


def _make_inline_element(
    tag: str, attrs: dict[str, str], line: str
) -> tuple[XmlElement, list[tuple[int, int, dict[str, Any]]]]:
    """Build a heading/paragraph element: parse ``line`` standalone to get
    its inline token, then build the (plain-text, mark-runs) pair. Marks are
    applied via ``.format()`` once the text node is integrated (pycrdt
    requires integration before ``.insert``/``.format`` — see
    ``_build_block_element``'s finisher-callback pattern, which this feeds)."""
    mini_tokens = gfm_parser().parse(line)
    inline_token = next(t for t in mini_tokens if t.type == "inline")
    plain_text, runs = _inline_runs(inline_token)
    el = XmlElement(tag, attrs, contents=[XmlText(plain_text)])
    return el, runs


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
    plain_text, runs = _inline_runs(inline_token)
    el = XmlElement("paragraph", {}, contents=[XmlText(plain_text)])
    return el, [lambda el=el, runs=runs: _apply_runs(el.children[0], runs)]


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


def _build_list(
    tokens: list[Any], start: int, end: int, *, extra_attrs: dict[str, str] | None = None
) -> tuple[XmlElement, list[Any]]:
    open_tok = tokens[start]
    ordered = open_tok.type == "ordered_list_open"
    tag = "orderedList" if ordered else "bulletList"
    attrs = dict(extra_attrs or {})
    if ordered:
        attrs["start"] = str(open_tok.attrs.get("start", 1))

    items: list[XmlElement] = []
    finishers: list[Any] = []
    i = start + 1
    while i < end - 1:  # exclude the outer list's own close token
        t = tokens[i]
        if t.type == "list_item_open":
            close_idx = _matching_close(tokens, i, "list_item_close")
            item_children, item_finishers = _build_block_sequence(tokens, i + 1, close_idx)
            items.append(XmlElement("listItem", {}, contents=item_children))
            finishers.extend(item_finishers)
            i = close_idx + 1
            continue
        raise NotImplementedError(f"unexpected token inside list: {t.type!r}")

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


def _heading_level_and_line(raw: str) -> tuple[int, str]:
    line = raw.rstrip("\n")
    hashes = 0
    while hashes < len(line) and line[hashes] == "#":
        hashes += 1
    rest = line[hashes:]
    if rest.startswith(" "):
        rest = rest[1:]
    return hashes, rest


def _build_block_element(body: str, block: BlockRange) -> tuple[XmlElement, list[Any]]:
    """Returns the (prelim) element plus a list of "finish" callbacks to run
    once the element is integrated into a doc (mark ``.format()`` calls need
    an already-integrated ``XmlText``)."""
    raw = body[block.start : block.end]
    trailing_nl = raw.endswith("\n")
    nl_attr = "1" if trailing_nl else "0"

    if block.kind is BlockKind.HEADING:
        level, line = _heading_level_and_line(raw)
        el, runs = _make_inline_element(
            "heading", {BLOCK_ID_ATTR: block.block_id, "level": str(level), _NL_ATTR: nl_attr}, line
        )
        return el, [lambda el=el, runs=runs: _apply_runs(el.children[0], runs)]

    if block.kind is BlockKind.PARAGRAPH:
        line = raw[:-1] if trailing_nl else raw
        el, runs = _make_inline_element(
            "paragraph", {BLOCK_ID_ATTR: block.block_id, _NL_ATTR: nl_attr}, line
        )
        return el, [lambda el=el, runs=runs: _apply_runs(el.children[0], runs)]

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
            parts.append(_serialize_inline(child.children[0]))  # type: ignore[arg-type]
        elif child.tag in ("bulletList", "orderedList"):
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
    list, same accepted tradeoff as ordered-list renumbering (see
    ``plans/onyx-editor.md``'s splice-granularity decision) — only untouched
    blocks carry the byte-stability guarantee (``markdown_splice.py``),
    which never calls this serializer at all.
    """
    ordered = node.tag == "orderedList"
    attrs = dict(node.attributes)
    start = int(attrs.get("start", "1")) if ordered else 1
    lines: list[str] = []
    for idx, item in enumerate(node.children):
        marker = f"{start + idx}. " if ordered else "- "
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


def serialize_block(node: XmlElement) -> str:
    attrs = dict(node.attributes)
    trailing = attrs.get(_NL_ATTR) == "1"

    if node.tag == "heading":
        level = int(attrs["level"])
        return "#" * level + " " + _serialize_inline(node.children[0]) + ("\n" if trailing else "")  # type: ignore[arg-type]
    if node.tag == "paragraph":
        return _serialize_inline(node.children[0]) + ("\n" if trailing else "")  # type: ignore[arg-type]
    if node.tag in ("bulletList", "orderedList"):
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


def reconstruct_body(doc: Doc) -> str:
    """Full whole-document reserialize — the compat shim's read path and the
    checkpoint engine's last-resort fallback. Never used by the normal
    targeted-splice checkpoint path (see ``markdown_splice.py``), since a
    whole-doc reserialize doesn't guarantee byte-stability for untouched
    regions if any block's serializer doesn't exactly reproduce its source
    (safe-but-lossy: correct output, not necessarily byte-identical).

    Each block's own serialized text already carries its own trailing
    newline (``BlockRange.end`` is inclusive of it, per
    ``markdown_blocks.py``) — bare concatenation would silently merge
    adjacent blocks (e.g. two paragraphs collapse into one) rather than just
    losing exact spacing, so a blank-line separator is inserted between
    blocks whose own text doesn't already end in one (a raw/opaque block's
    captured span sometimes already includes its trailing blank line;
    headings/paragraphs never do, per ``_build_block_element``).
    """
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    parts: list[str] = []
    for child in root.children:
        text = serialize_block(child)  # type: ignore[arg-type]
        if parts and not parts[-1].endswith("\n\n"):
            parts.append("\n")
        parts.append(text)
    return "".join(parts)

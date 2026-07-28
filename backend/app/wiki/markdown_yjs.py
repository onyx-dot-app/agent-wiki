"""Markdown <-> Yjs codec: the live document representation for co-editing.

Every top-level block (``markdown_blocks.top_level_block_ranges``) becomes an
``XmlElement`` in a ``pycrdt`` doc's root ``XmlFragment``, tagged with a
stable, positional ``_blockId`` attribute. Structural treatment (real
ProseMirror-shaped nodes, editable node-by-node, not opaque text) covers:
``heading``, ``paragraph`` (inline content — text runs + bold/italic/code/
link marks, represented via a ``pycrdt.XmlText``'s ``.format()`` runs, plus
explicit ``hardBreak`` and ``image`` leaf elements interspersed as siblings
wherever a hard line break or image occurs — y-prosemirror maps a PM leaf/
atom node to an empty sibling ``XmlElement``, not to a text mark, since a
break is a node boundary, not formatting), ``bulletList``/``orderedList``/
``listItem`` (arbitrarily nested — CommonMark's own grammar is already
recursive here, so supporting depth costs about the same as supporting one
level), ``taskList``/
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
touched — cells aren't decomposed or individually editable. Thematic break
and html block stay opaque verbatim, tagged ``_raw="1"``.

Unrecognized inline constructs (GFM strikethrough — anything this module
doesn't have an explicit encoder for) raise ``NotImplementedError`` rather
than silently drop or mis-serialize content — the byte-stability requirement
this whole engine exists for is only meaningful if failures are loud, never
silent. Same for a list item that isn't a clean sequence of paragraph/list/
blockquote children (e.g. a list item containing a table) — unsupported,
raises rather than mis-encodes.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pycrdt import Doc, XmlElement, XmlFragment, XmlText
from pydantic import BaseModel, ConfigDict

from app.wiki.markdown_blocks import BlockKind, BlockRange, gfm_parser, top_level_block_ranges

# Root-fragment key. Must match the frontend's Collaboration extension
# `field` config exactly (see frontend/src/lib/tiptapEditor/extensions.ts).
ROOT_XML_KEY = "prosemirror"

_RAW_ATTR = "_raw"  # "1" for an opaque verbatim-text block
BLOCK_ID_ATTR = "_blockId"
ROW_ID_ATTR = "_rowId"

_KNOWN_INLINE_TYPES = {
    "text",
    "softbreak",
    "hardbreak",
    "image",
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
# unrecognized inline *construct* (see _KNOWN_INLINE_TYPES) raises. "code" is
# still listed here for that documentation value (and skipped defensively if
# it ever does combine with something else), but in practice it's handled
# entirely by `_wrap_code_run` before this loop runs — see `_wrap_run` — not
# by a plain wrap-with-backticks step the way the other three are, since its
# delimiter is stored as literal text already, not synthesized here.
_MARK_WRAP_ORDER = ("link", "bold", "italic", "code")

# A text segment or a leaf segment. The 4th slot carries a leaf's attrs
# dict, used for images and ``None`` for text runs or hard breaks.
_Segment = tuple[
    Literal["text", "hardbreak", "image"],
    str | None,
    list[tuple[int, int, dict[str, Any]]] | None,
    dict[str, str] | None,
]


def _image_alt_text(children: list[Any] | None) -> str:
    return "".join(
        "\n" if child.type in ("softbreak", "hardbreak") else str(getattr(child, "content", ""))
        for child in (children or [])
    )


def _image_attrs(image_token: Any) -> dict[str, str]:
    attrs = image_token.attrs or {}
    image_attrs = {
        "src": str(attrs.get("src", "")),
        "alt": _image_alt_text(image_token.children),
    }
    if "title" in attrs:
        image_attrs["title"] = str(attrs["title"])
    return image_attrs


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
            segments.append(("text", "".join(parts), runs, None))
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
            segments.append(("hardbreak", None, None, None))
        elif child.type == "image":
            _flush_text()
            segments.append(("image", None, None, _image_attrs(child)))
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
            # The flanking backtick fence is stored as literal text inside
            # the mark, not stripped - matching the frontend's own
            # InlineCode mark (blocks.ts), which keeps the backticks as
            # real DOM characters on purpose (a mark's boundary is an
            # otherwise zero-width, ambiguous caret position). Both ends of
            # the shared Yjs doc need to agree on this shape, since a live
            # session's doc *is* the same CRDT structure this seeds.
            # `child.markup` is the exact fence markdown-it matched (1+
            # backticks - a longer one only when the source deliberately
            # used it, e.g. content containing an inner single backtick).
            fence = child.markup or "`"
            _emit(f"{fence}{child.content}{fence}", {**active, "code": True})
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


def _contains_backtick_run(text: str, length: int) -> bool:
    """Whether ``text`` contains a contiguous run of backticks at least
    ``length`` long anywhere inside it — the condition that makes a fence of
    exactly ``length`` backticks ambiguous as a delimiter (the next parse's
    leftmost-match scan would close on that interior run instead of the
    intended trailing fence)."""
    run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        if run >= length:
            return True
    return False


def _wrap_code_run(text: str) -> str:
    """Serializes a code-marked run. ``text`` is normally already a
    complete, self-delimited code span (``fence + content + fence``, one
    contiguous run of backticks on each end) — matching what ``_inline_runs``
    builds when seeding from markdown and what the frontend's own
    ``InlineCode`` mark builds on live typing (``blocks.ts``): keeping the
    delimiters as real characters is deliberate there, so the common case
    here is to pass ``text`` straight through unchanged, not add another
    layer of backticks on top.

    Two cases need real handling instead of pass-through:

    - ``text`` has no backtick at all: the "code" mark reached this run
      without ever going through a backtick-based conversion (e.g. via the
      ``toggleCode``/``Mod-e`` command on already-existing plain text) — a
      fresh fence has to be added.
    - ``text``'s own leading/trailing backticks no longer safely delimit —
      e.g. a live edit landed a new backtick inside an already-marked span
      (typing while the cursor sits between two already-marked characters
      picks up the active marks like any other character), so the stored
      text's interior now contains a run as long as its own edges. Repaired
      with the same fence-length-bumping idea ``_serialize_code_block``
      uses for fenced code blocks: strip the (no-longer-trustworthy) edges
      and rebuild a fence guaranteed longer than anything left inside.

    Not handled: markdown deliberately using a longer fence than strictly
    needed so its content can itself start/end with fewer backticks than
    the fence (e.g. fence "``" wrapping content "`x`"). Flattened into one
    literal string, that's indistinguishable on the next parse from a
    single longer run — a narrow, pre-existing ambiguity in representing a
    code span as flat text at all, not something introduced here."""
    leading = len(text) - len(text.lstrip("`"))
    trailing = len(text) - len(text.rstrip("`"))
    if leading and leading == trailing and len(text) >= 2 * leading:
        inner = text[leading : len(text) - trailing]
        if not _contains_backtick_run(inner, leading):
            return text
    else:
        inner = text
    inner = inner.strip("`")
    fence = "`"
    while fence in inner:
        fence += "`"
    pad = " " if inner[:1] == "`" or inner[-1:] == "`" else ""
    return f"{fence}{pad}{inner}{pad}{fence}"


# Edge whitespace must stay outside emphasis delimiters when a leaf split
# forces the run to re-wrap in pieces.
def _wrap_with_delimiter(text: str, delimiter: str) -> str:
    core = text.strip()
    if not core:
        return f"{delimiter}{text}{delimiter}"
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{delimiter}{core}{delimiter}{trail}"


def _wrap_run(text: str, attrs: dict[str, Any] | None) -> str:
    attrs = attrs or {}
    # Inline code spans are verbatim — CommonMark never processes escapes
    # inside them, so escaping here would corrupt the code's actual text
    # (a literal backslash would become part of the visible content).
    if "code" in attrs:
        text = _wrap_code_run(text)
    else:
        text = _escape_inline_text(text)
    if not attrs:
        return text
    result = text
    for mark in reversed(_MARK_WRAP_ORDER):
        if mark not in attrs or mark == "code":
            continue
        if mark == "italic":
            result = _wrap_with_delimiter(result, "*")
        elif mark == "bold":
            result = _wrap_with_delimiter(result, "**")
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


def _escape_title(title: str) -> str:
    return title.replace("\\", "\\\\").replace('"', '\\"')


def _image_destination(src: str) -> str:
    # A bare destination cannot carry whitespace, control characters, angle
    # brackets, or unbalanced parens and still re-parse as an image, and a
    # live session can set src attrs that never passed through markdown-it's
    # normalization. Those spellings get the angle-bracket form, brackets
    # escaped inside it, which markdown-it normalizes on the next parse
    # (idempotent after that). Balanced parens stay bare: markdown-it accepts
    # and stores them raw, so wrapping them would break byte stability.
    depth = 0
    unbalanced = False
    for ch in src:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                unbalanced = True
                break
    unbalanced = unbalanced or depth != 0
    if not src or unbalanced or any(ch.isspace() or ch in "<>" for ch in src):
        escaped = src.replace("\\", "\\\\").replace("<", "\\<").replace(">", "\\>")
        return f"<{escaped}>"
    return src


def _serialize_image(node: XmlElement) -> str:
    attrs = dict(node.attributes)
    src = _image_destination(attrs.get("src", ""))
    alt = _escape_inline_text(attrs.get("alt", ""))
    title = attrs.get("title")
    if title is not None:
        return f'![{alt}]({src} "{_escape_title(title)}")'
    return f"![{alt}]({src})"


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
        elif isinstance(child, XmlElement) and child.tag == "image":
            parts.append(_serialize_image(child))
        else:
            raise NotImplementedError(f"unrecognized inline child: {child!r}")
    return "".join(parts)


def _serialize_paragraph_text(children: list[Any]) -> str:
    """A paragraph's own serialized text, block-start-escaped. Strips a
    leading/trailing literal "\\n" first — the only way one ends up there is
    a stranded softbreak character: splitting a multi-line paragraph
    (Enter mid-text, not through any of this editor's own state machines —
    just Tiptap's default paragraph split) leaves the *old* softbreak's
    "\\n" as an ordinary leading/trailing character in whichever half didn't
    consume it, since that split has no markdown-syntax awareness at all.
    Left in, it stacks with this block's own trailing newline (every block
    contributes exactly one — see ``serialize_block``) to produce an extra,
    unwanted blank line around the split point. A leading/trailing "\\n" is
    never meaningful paragraph content either way (an *interior* one still
    is — a real softbreak — and is untouched here, since ``str.strip`` only
    touches the ends)."""
    return _escape_block_start_ambiguity(_serialize_inline_children(children).strip("\n"))


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
    for kind, text, runs, image_attrs in segments:
        if kind == "hardbreak":
            contents.append(XmlElement("hardBreak", {}))
        elif kind == "image":
            assert image_attrs is not None
            contents.append(XmlElement("image", image_attrs))
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


def _build_block_sequence(
    tokens: list[Any], start: int, end: int
) -> tuple[list[XmlElement], list[Any]]:
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


def _list_item_task_marker(
    tokens: list[Any], item_start: int, item_end: int
) -> re.Match[str] | None:
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
    return XmlElement("codeBlock", {**attrs, "language": language}, contents=[XmlText(tok.content)])


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
    an already-integrated ``XmlText``).

    No ``_nl`` attribute anywhere below, deliberately: every block's own
    trailing newline is no longer conditional on anything stored — see
    ``serialize_block``, which now always emits exactly one, unconditionally,
    for every block kind. Trying to track "does this block's raw text end in
    a newline" as stamped, frozen state was the root cause of a whole family
    of bugs (a stored value going stale relative to a block's actual current
    content); the fix is to never store the decision at all.
    """
    raw = body[block.start : block.end]

    if block.kind is BlockKind.BLANK_LINE:
        # Always an empty paragraph - matches exactly what a live Enter-press
        # on an empty paragraph already produces, so it round-trips through
        # the same, unmodified `serialize_block` paragraph branch with no
        # special-casing there.
        return (
            XmlElement("paragraph", {BLOCK_ID_ATTR: block.block_id}, contents=[XmlText("")]),
            [],
        )

    if block.kind is BlockKind.HEADING:
        return _build_heading(raw, {BLOCK_ID_ATTR: block.block_id})

    if block.kind is BlockKind.PARAGRAPH:
        line = raw[:-1] if raw.endswith("\n") else raw
        line = _strip_indented_code_ambiguity_for_parse(line)
        return _make_inline_element("paragraph", {BLOCK_ID_ATTR: block.block_id}, line)

    if block.kind is BlockKind.LIST:
        tokens = gfm_parser().parse(raw)
        el, finishers = _build_list(
            tokens, 0, len(tokens), extra_attrs={BLOCK_ID_ATTR: block.block_id}
        )
        return el, finishers

    if block.kind is BlockKind.BLOCKQUOTE:
        tokens = gfm_parser().parse(raw)
        el, finishers = _build_blockquote(
            tokens, 0, len(tokens), extra_attrs={BLOCK_ID_ATTR: block.block_id}
        )
        return el, finishers

    if block.kind is BlockKind.CODE_BLOCK:
        el = _build_code_block(raw, {BLOCK_ID_ATTR: block.block_id})
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
            # Same block-start ambiguity as a top-level paragraph
            # (serialize_block) — a list item or blockquote's own first
            # line is just as much a fresh block-start position as the
            # top of the document, so a literal leading "-"/">"/"#"/"---"
            # needs the same escaping, not just the top-level case.
            parts.append(_serialize_paragraph_text(list(child.children)))
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
# pattern in already-serialized plain text). Digits captured in their own
# group — unlike every other marker character this module escapes, a digit
# is not ASCII punctuation, so a backslash placed before it is never
# consumed as an escape on the next parse (confirmed against the forward
# parse: `\1. item` keeps its backslash as a literal character instead of
# protecting the marker). The delimiter (`.`/`)`) immediately after the
# digits *is* punctuation, so that's the character that actually needs the
# backslash — see the escape call site below.
_ORDERED_MARKER_RE = re.compile(r"^(\d{1,9})([.)])(\s|$)")

# A thematic break is a *whole line* of 3+ "-" (optionally space/tab
# separated) and nothing else — "---" alone reactivates, but "--- and more
# text" doesn't (confirmed against the forward parse, not assumed: a
# trailing non-marker character on the line makes it an ordinary
# paragraph). "*"/"_" thematic breaks ("***", "_ _ _") need no matching
# case — every "*"/"_" is already escaped unconditionally by `_wrap_run`
# for the emphasis/italic reason, which breaks the run regardless of
# position.
_THEMATIC_BREAK_DASH_RE = re.compile(r"^-(?:[ \t]*-){2,}[ \t]*(?:\n|$)")

# A setext heading underline: one or more *contiguous* "=" (level 1) or "-"
# (level 2), only trailing space/tab, nothing else — a different threshold
# from a thematic break (which needs 3+ dashes, and tolerates spaces
# *between* them): a setext underline needs only one dash/equals, but no
# internal spaces. It only ever reactivates a *preceding* line (there's no
# rule for a bare "-\n" as a heading without a paragraph line above it),
# which is exactly the per-line position this module already escapes at.
_SETEXT_UNDERLINE_RE = re.compile(r"^(?:-+|=+)[ \t]*$")

# A GFM table delimiter row: 1+ cells of optional ":", 1+ "-", optional
# ":", separated by "|", with optional leading/trailing "|" and whitespace
# around each cell. A paragraph whose *first* line contains "|" followed by
# a line matching this shape reactivates as a table header on the next
# parse — confirmed against the forward parse (not assumed): a plain
# paragraph typed as "a | b" + soft break + "---|---" is indistinguishable
# from real table source once serialized, since neither "|" nor a bare
# dash run were escaped by anything else in this module.
_TABLE_DELIMITER_ROW_RE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$"
)

# A fence opener: 3+ backticks or 3+ tildes at the start of the line — unlike
# a thematic break/setext underline/table row, a fence opener is *not*
# whole-line-only: trailing content (the info string, e.g. "```python") is
# valid and still opens the fence (confirmed against the forward parse: 2
# backticks doesn't, 3+ does, with or without trailing text). Reactivating
# one is the most severe case in this module — a fenced code block swallows
# everything up to the *next* matching fence (or EOF) as raw content, not
# just misclassifying the one paragraph.
_FENCE_OPENER_RE = re.compile(r"^(?:`{3,}|~{3,})")

# An indented code block: 4+ columns of leading indentation (4 spaces, or
# fewer spaces then a tab — a tab always reaches the next 4-column stop, so
# 0-3 spaces followed by one is equivalent to 4 bare spaces; confirmed
# against the forward parse). Unlike every check above, this only matters on
# a block's *first* line: an indented code block can't interrupt an
# already-started paragraph (confirmed: a later soft-break line with 4+
# leading spaces stays part of the same paragraph), so continuation lines
# are already safe without any escaping.
_INDENTED_CODE_FIRST_LINE_RE = re.compile(r"^(?: {4}| {0,3}\t)")


def _strip_indented_code_ambiguity_for_parse(line: str) -> str:
    """Parse-side mirror of ``_escape_block_start_ambiguity``'s first-line
    handling, below.

    ``top_level_block_ranges`` splits a multi-line paragraph into one
    ``BlockRange`` per soft-break line (every newline is its own block
    boundary now - see that function's docstring). A continuation line with
    4+ leading columns of indentation was always safe as part of a bigger
    paragraph (an indented code block can't interrupt one already started),
    but once split out it becomes its own block and gets reparsed standalone
    by ``_make_inline_element`` - exactly the indented-code-block trigger,
    which has no ``inline`` token at all, so the paragraph's content would
    silently come back empty instead of just losing its indentation. Strip
    the same leading run stripped on the write side so the round-trip stays
    symmetric: a promoted continuation line still parses as a paragraph."""
    lines = line.split("\n")
    if lines[0] and _INDENTED_CODE_FIRST_LINE_RE.match(lines[0]):
        lines[0] = lines[0].lstrip(" \t")
    return "\n".join(lines)


def _escape_block_start_ambiguity(text: str) -> str:
    """A paragraph's serialized text starting with a character or pattern
    that's only special as a *block*-start marker (heading ``#``, bullet
    ``-``/``+``, thematic break ``---``, blockquote ``>``, ordered-list
    ``1.``) must stay escaped, or the next parse reinterprets this
    paragraph as a different block type entirely. Unlike ``_wrap_run``'s
    mark-delimiter escaping (position-independent — a ``*``/``_``/`` ` ``/
    ``[``/``]`` is ambiguous anywhere in the text, already handled there),
    these are only ambiguous at the very start of *a line*, which is why
    this checks every line a soft break produces, not just ``text[0]`` —
    a multi-line paragraph's second line becomes just as much a fresh
    block-start position once it's re-emitted, whether that's the plain
    top level, prefixed with a list item's continuation indent (which
    CommonMark still parses as a block start through up to 3 spaces), or
    with a blockquote's repeated ``> `` (``_serialize_blockquote`` adds
    that per line unconditionally, including a paragraph's internal soft
    breaks). ``*`` as a bullet marker doesn't need a case here — it's
    already escaped unconditionally by ``_wrap_run`` for the emphasis
    reason, which covers every line's start position too as a side
    effect.

    The first line additionally gets the indented-code-block check
    (``_INDENTED_CODE_FIRST_LINE_RE``) first, since that ambiguity only
    exists on a block's first line, not on every line the way the marker
    checks do — its leading whitespace run is stripped *before*
    ``_escape_line_start`` runs on that line, so whatever's left (e.g. a
    literal ``#`` that was hiding behind 4 leading spaces) still gets
    marker-escaped by the same pass, rather than being left live."""
    lines = text.split("\n")
    if lines[0] and _INDENTED_CODE_FIRST_LINE_RE.match(lines[0]):
        lines[0] = lines[0].lstrip(" \t")
    return "\n".join(_escape_line_start(line) for line in lines)


def _escape_line_start(line: str) -> str:
    if not line:
        return line
    # Up to 3 leading spaces are insignificant to CommonMark — "  # Heading"
    # is still an ATX heading, "   - item" is still a bullet (confirmed
    # against the forward parse: every marker below reactivates through 1-3
    # leading spaces the same as at column 0) — so the checks below look
    # past them, not just at column 0. A 4th leading space (or a tab, which
    # advances to the next 4-column stop) tips into indented-code territory
    # instead — a different, narrower case handled separately by
    # _INDENTED_CODE_FIRST_LINE_RE, so a line whose leading run is that long
    # is left alone here.
    #
    # The escape backslash below is placed immediately before the marker
    # character (``rest``), not before the dropped indentation (``line``) —
    # CommonMark's backslash escape applies only to punctuation, never to
    # whitespace, so a backslash placed before a space is never consumed on
    # the next parse; it would survive as a literal extra character instead
    # of protecting anything. Escaping the marker in its own escapable
    # position is the only construct that round-trips, at the documented
    # cost of the leading whitespace itself not surviving (see
    # AGENT_WIKI_MARKDOWN_STANDARD.md §6) — CommonMark strips that
    # insignificant indentation on reparse regardless of whether this
    # function keeps or drops it from the emitted text.
    indent_len = min(3, len(line) - len(line.lstrip(" ")))
    rest = line[indent_len:]
    if not rest or rest[0] in " \t":
        return line
    if rest[0] == "#":
        return "\\" + rest
    if rest[0] in "-=" and _SETEXT_UNDERLINE_RE.match(rest):
        return "\\" + rest
    if rest[0] == "-" and _THEMATIC_BREAK_DASH_RE.match(rest):
        return "\\" + rest
    if rest[0] in "-+" and (len(rest) == 1 or rest[1].isspace()):
        return "\\" + rest
    if rest[0] == ">":
        return "\\" + rest
    ordered_match = _ORDERED_MARKER_RE.match(rest)
    if ordered_match:
        # Backslash goes after the digits, before the delimiter — see
        # _ORDERED_MARKER_RE's comment for why escaping the digits
        # themselves (as every other call site here escapes its marker
        # character) doesn't work for this one construct.
        digits = ordered_match.group(1)
        return digits + "\\" + rest[len(digits) :]
    if _TABLE_DELIMITER_ROW_RE.match(rest):
        return "\\" + rest
    if _FENCE_OPENER_RE.match(rest):
        return "\\" + rest
    return line


def serialize_block(node: XmlElement) -> str:
    """No stored newline state anywhere in this function, deliberately: every
    block - including an empty paragraph (a ``BlockKind.BLANK_LINE`` spacer,
    or a fresh one from an Enter press with nothing typed) - contributes
    exactly its own text plus one trailing newline, unconditionally, with no
    exception for empty content. `checkpoint_body` concatenates every
    block's own output directly with no separator of its own added on top
    (see its module docstring) - it's the block's own newline that supplies
    the entire boundary, so an empty block still needs to contribute one:
    it represents exactly one blank line, not the absence of one."""
    attrs = dict(node.attributes)

    if node.tag == "heading":
        level = int(attrs["level"])
        text = _serialize_inline_children(list(node.children))
        return "#" * level + " " + text + "\n"
    if node.tag == "paragraph":
        return _serialize_paragraph_text(list(node.children)) + "\n"
    if node.tag in ("bulletList", "orderedList", "taskList"):
        return _serialize_list(node)
    if node.tag == "blockquote":
        return _serialize_blockquote(node)
    if node.tag == "codeBlock":
        return _serialize_code_block(node)
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

    No separator is synthesized between blocks: each block's own
    ``serialize_block`` output already supplies its complete boundary (one
    trailing newline if it has content, nothing if it doesn't — see that
    function), and any actual blank line the document contains is its own
    separate, empty ``BlockKind.BLANK_LINE`` block, not something inferred
    here. Bare concatenation is therefore already correct — two blocks with
    truly nothing between them are supposed to read back as one soft-broken
    paragraph, not silently gain a separator that was never there. A table's
    span covers its whole reserialized text (every row concatenated) — rows
    aren't tracked individually, matching this codec's row-level (not
    per-cell) granularity everywhere else.
    """
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    parts: list[str] = []
    spans: list[BlockSpan] = []
    pos = 0
    for child in root.children:
        text = serialize_block(child)  # type: ignore[arg-type]
        block_id = dict(child.attributes).get(BLOCK_ID_ATTR)  # type: ignore[union-attr]
        start = pos
        parts.append(text)
        pos += len(text)
        if block_id is not None:
            spans.append(BlockSpan(block_id=block_id, start=start, end=pos))
    return "".join(parts), spans

"""Markdown <-> Yjs codec: the live document representation for co-editing.

Every top-level block (``markdown_blocks.top_level_block_ranges``) becomes an
``XmlElement`` in a ``pycrdt`` doc's root ``XmlFragment``, tagged with a
stable, positional ``_blockId`` attribute. Structural treatment (real
ProseMirror-shaped nodes, editable node-by-node, not opaque text) covers:
``heading``, ``paragraph`` (inline content — text runs + bold/italic/strike/
code/link marks, represented via a ``pycrdt.XmlText``'s ``.format()`` runs, plus
explicit ``hardBreak`` and ``image`` leaf elements interspersed as siblings
wherever a hard line break or image occurs — y-prosemirror maps a PM leaf/atom node to an empty
sibling ``XmlElement``, not to a text mark, since a break is a node boundary,
not formatting), ``bulletList``/``orderedList``/``listItem`` (arbitrarily
nested — CommonMark's own grammar is already recursive here, so supporting
depth costs about the same as supporting one level), ``taskList``/
``taskItem`` (a GFM checkbox list — a bullet list where *at least one* item
starts with a ``[ ]``/``[x]`` marker; items without one stay plain
``listItem`` children of the same ``taskList``, since GFM lets a list mix the
two and the editor's schema holds that mix — see ``_build_list``),
``blockquote`` (a sequence of paragraph/list/blockquote
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

A list item or blockquote holds a sequence of these same blocks — not just
paragraphs and nested lists, but a code block, heading, thematic break or
table too, since CommonMark nests all of them and a code block inside a
bullet is ordinary on a docs page. The nested forms are built from tokens
rather than source slices, so two of them normalize (a thematic break to
``---``, a table's cell padding to one space) — see ``_build_block_sequence``.

Unrecognized inline constructs (anything this module doesn't have an
explicit encoder for) raise ``NotImplementedError`` rather than
silently drop or mis-serialize content — the byte-stability
requirement this whole engine exists for is only meaningful if failures are
loud, never silent. Same for any block construct nested in a list item or
blockquote that has no branch in ``_build_block_sequence`` — unsupported,
raises rather than mis-encodes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal

from pycrdt import Doc, XmlElement, XmlFragment, XmlText
from pydantic import BaseModel, ConfigDict

from app.wiki import media_store
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
    "s_open",
    "s_close",
}

# Nesting order for the marks _serialize_inline_text treats as a delimiter
# stack shared *across* adjacent runs — outer to inner: link, bold, strike,
# italic. "code" isn't here: a code span's own fence is self-delimiting per
# its own run's text (_wrap_code_run) and doesn't participate in this
# cross-run nesting — matching CommonMark, where a code span can't
# semantically nest (or be nested inside) other marks anyway.
_NESTING_MARK_ORDER = ("link", "bold", "strike", "italic")
_SYMMETRIC_MARK_DELIMS = {"italic": "*", "bold": "**", "strike": "~~"}
# The equivalent underscore spellings, used only when an emphasis opener
# would directly abut a same-character closer (see the delimiter-collision
# fallback in ``_serialize_inline_text``).
_ALT_EMPHASIS_DELIMS = {"*": "_", "**": "__"}

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
            attrs = _image_attrs(child)
            # A third-party src is dropped rather than carried, so it never
            # round-trips back out to a reader's browser.
            if media_store.is_same_origin_src(attrs.get("src", "")):
                _flush_text()
                segments.append(("image", None, None, attrs))
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
        elif child.type == "s_open":
            active = {**active, "strike": True}
        elif child.type == "s_close":
            active = {k: v for k, v in active.items() if k != "strike"}
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
            link_attrs: dict[str, Any] = {"href": child.attrs.get("href", "")}
            title = child.attrs.get("title")
            if title:
                # Dropping this on parse (only href was ever captured) was
                # a real data-loss bug (confirmed in review): a link's
                # title survived nowhere in the CRDT doc at all, so it was
                # already gone by the time serialization ran, not just
                # omitted there.
                link_attrs["title"] = title
            active = {**active, "link": link_attrs}
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


def _link_href_title(link_attrs: Any) -> tuple[str, str | None]:
    # {"href": ..., "title": ...} (matching y-prosemirror's mark-attrs
    # convention) — but also accept a bare string defensively in case
    # something upstream ever writes the old href-only shape.
    if isinstance(link_attrs, dict):
        return link_attrs.get("href", ""), link_attrs.get("title")
    return link_attrs, None


# A run's per-mark identity: the mark name for the three symmetric marks,
# or an (href, title) tuple for "link" — a plain "link:{href}" *string* key
# would collide with itself the moment href contains its own ":" (which an
# "http://..." URL always does), so this uses a tuple instead of any
# string-encoding scheme.
_MarkKey = str | tuple[str, str, str | None]


def _mark_key(mark: str, attrs: dict[str, Any]) -> _MarkKey:
    """An opaque per-run identity for ``mark``, used to decide whether two
    adjacent runs are "the same" open span (stay merged, no close/reopen)
    or genuinely different (must close and reopen at the boundary) — for
    "link" specifically, two adjacent runs both marked "link" but pointing
    at *different* hrefs/titles are different links, not one span covering
    both destinations, so the href/title ride along in the key itself
    rather than just the mark name."""
    if mark != "link":
        return mark
    href, title = _link_href_title(attrs["link"])
    return ("link", href, title)


def _open_delim(key: _MarkKey) -> str:
    if isinstance(key, tuple):
        return "["
    return _SYMMETRIC_MARK_DELIMS[key]


def _close_delim(key: _MarkKey) -> str:
    if isinstance(key, tuple):
        _, href, title = key
        title_part = f' "{title}"' if title else ""
        return f"]({href}{title_part})"
    return _SYMMETRIC_MARK_DELIMS[key]


def _close_after(out: str, closers: Iterable[str]) -> str:
    """Append closing delimiters, keeping trailing whitespace outside them.

    CommonMark only closes emphasis on a delimiter preceded by non-whitespace,
    so `*before *` is literal text, not emphasis. A leaf (an image, a hard
    break) splitting a marked run forces a close mid-run, which is exactly
    where that trailing space appears.

    Takes the closer strings themselves rather than mark keys: openers are
    chosen per instance (an emphasis mark can open with either spelling —
    see the collision fallback in ``_serialize_inline_text``), and only the
    caller's stack knows which one to match.
    """
    closing = "".join(closers)
    if not closing:
        return out
    body = out.rstrip()
    return body + closing + out[len(body) :]


def _serialize_inline_text(xt: XmlText) -> str:
    """Serializes one ``XmlText``'s runs (``xt.diff()``) back to markdown.

    Each run used to be wrapped in its marks' delimiters independently
    (iterating ``_MARK_WRAP_ORDER`` per run) — for two *adjacent* runs that
    both carry the same mark (e.g. bold text with an italic word in the
    middle: "bold ", "and" [bold+italic], " italic", all bold), that closed
    and reopened the shared delimiter at every run boundary regardless of
    whether the mark was ever actually interrupted, producing doubled-up,
    unbalanced delimiter runs (`*****` between "bold" and "bold+italic"
    text) — invalid markdown, not just cosmetic, and in at least one shape
    compounding further on every subsequent touch (confirmed in review).

    Fixed by tracking a single stack of currently-open marks across the
    *whole* run sequence — the standard "properly nested delimiters"
    approach: at each run boundary, keep the longest prefix of the open
    stack that the next run still carries, close everything after it
    (innermost first — a mark can't close while something opened after it
    is still open, that's what "nested" means), then open whatever the
    next run newly wants, in ``_NESTING_MARK_ORDER``. Which mark nests
    outside which is this serializer's own choice, so a mark carried by
    both neighbouring runs stays open in the position it already holds —
    continuity, not a fixed order — and a genuinely continuous mark never
    closes at all.

    "code" is handled separately, per run (``_wrap_code_run``) — a code
    span's own fence is already self-delimiting per its own text and
    doesn't participate in this cross-run nesting.
    """
    # Each open mark remembers the closer it was opened with, because the
    # opener is chosen per instance (see the delimiter-collision fallback
    # below) and the closer must match it.
    open_marks: list[tuple[_MarkKey, str]] = []
    out = ""
    for text, attrs in xt.diff():
        attrs = attrs or {}
        ordered = [_mark_key(m, attrs) for m in _NESTING_MARK_ORDER if m in attrs]
        want = set(ordered)
        # Keep every already-open mark this run still carries, in the order
        # they are open — not in ``_NESTING_MARK_ORDER``. The order marks
        # nest in is this serializer's own choice, and continuity is the
        # correct choice: for italic text with a bold word inside, closing
        # the italic to reopen it bold-first abuts the two spans' delimiter
        # runs into one (``***Status****.*``), which CommonMark reads as a
        # single unmatchable run — the emphasis is lost and the asterisks
        # go literal on the next parse. Keeping the continuing mark open
        # reproduces the nesting the source actually had.
        common = 0
        while common < len(open_marks) and open_marks[common][0] in want:
            common += 1
        out = _close_after(out, (close for _, close in reversed(open_marks[common:])))
        open_marks = open_marks[:common]
        kept = {key for key, _ in open_marks}
        # Inline code spans are verbatim — CommonMark never processes
        # escapes inside them, so escaping here would corrupt the code's
        # actual text (a literal backslash would become part of the
        # visible content).
        rendered = _wrap_code_run(text) if "code" in attrs else _escape_inline_text(text)
        # An opener must be followed by non-whitespace, so any leading
        # space stays in front of it.
        lead = len(rendered) - len(rendered.lstrip())
        prefix = out + rendered[:lead]
        opening = ""
        for key in ordered:
            if key in kept:
                continue
            delim = _open_delim(key)
            # A genuine mark crossing (one span ends exactly where another
            # begins) still abuts a closer and an opener of the same
            # character into one ambiguous delimiter run. Emphasis has an
            # equivalent spelling to break the tie with; a strike run has
            # no alternate and keeps its (pre-existing) ambiguity.
            last = (prefix + opening)[-1:]
            if delim[0] == last and delim in _ALT_EMPHASIS_DELIMS:
                delim = _ALT_EMPHASIS_DELIMS[delim]
            opening += delim
            open_marks.append((key, delim if not isinstance(key, tuple) else _close_delim(key)))
        out = prefix + opening + rendered[lead:]
    return _close_after(out, (close for _, close in reversed(open_marks)))


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
    # A collaborator's document can hold any src, so the origin rule applies on
    # the way out too, not only where markdown is parsed.
    if not media_store.is_same_origin_src(attrs.get("src", "")):
        return ""
    src = _image_destination(attrs.get("src", ""))
    # A label cannot span lines and still parse as an image, and a live
    # session can set an alt from a filename that carries one.
    raw_alt = attrs.get("alt", "").replace("\r", " ").replace("\n", " ")
    alt = _escape_inline_text(raw_alt)
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
                finishers.append(lambda xt=xt, text=text, runs=runs: _apply_runs(xt, text, runs))
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


def _apply_runs(xt: XmlText, text: str, runs: list[tuple[int, int, dict[str, Any]]]) -> None:
    """``runs``' offsets are character offsets into ``text`` (Python string
    indexing, from ``_inline_runs``), but ``XmlText.format()`` indexes in
    UTF-8 *bytes* — verified directly against pycrdt. Any multi-byte
    character before a mark (an em dash, a curly quote, an emoji) shifts
    the mark boundary if applied as-is, silently corrupting existing
    bold/italic/etc. the instant a page containing one is opened (measured
    over a real wiki: 10.5% of content blocks affected). Convert to byte
    offsets first."""
    for start, end, attrs in runs:
        if start < end:
            byte_start = len(text[:start].encode("utf-8"))
            byte_end = len(text[:end].encode("utf-8"))
            xt.format(byte_start, byte_end, attrs)


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
    """Build a sequence of child block elements from a flat markdown-it token
    range ``[start, end)`` — used for list-item and blockquote content,
    recursing into ``_build_list``/``_build_blockquote`` for nested
    containers. This is what lets a list item contain multiple paragraphs or
    a nested list, and a blockquote contain multiple paragraphs or a list,
    with the same code path either way.

    Every block construct CommonMark can nest here has a branch, so the set
    matches ``serialize_block``'s (the inverse direction) rather than being a
    subset of it: a page is only openable in the live editor if this function
    can represent all of it, and a code block or heading inside a bullet is
    ordinary markdown. Unlike the top-level path, nested constructs are built
    from tokens alone, never from a source slice: the same token ``.map``
    line numbers address the *undecorated* source, so inside a blockquote a
    slice would carry the ``> `` prefixes along with the content. That costs
    verbatim fidelity for the two constructs the top level stores as opaque
    source text — a nested thematic break normalizes to ``---`` and a nested
    table's cells are re-emitted with single-space padding — the same
    correct-not-byte-identical tradeoff ``_serialize_list`` already makes for
    a touched list."""
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
        if t.type in ("fence", "code_block"):
            children.append(_code_block_from_token(t, {}))
            i += 1
            continue
        if t.type == "heading_open":
            el, heading_finishers = _element_from_segments(
                "heading", {"level": str(int(t.tag[1:]))}, _inline_runs(tokens[i + 1])
            )
            children.append(el)
            finishers.extend(heading_finishers)
            i += 3  # heading_open, inline, heading_close
            continue
        if t.type == "hr":
            children.append(_thematic_break_element({}))
            i += 1
            continue
        if t.type == "table_open":
            close_idx = _matching_close(tokens, i, "table_close")
            table_el, table_finishers = _build_nested_table(tokens, i, close_idx + 1)
            children.append(table_el)
            finishers.extend(table_finishers)
            i = close_idx + 1
            continue
        raise NotImplementedError(f"unsupported nested block construct: {t.type!r}")
    return children, finishers


# GFM task-list marker: "[ ] "/"[x] "/"[X] " at the very start of a list
# item's first paragraph. No dedicated task-list plugin is enabled on
# `gfm_parser()` (see markdown_blocks.py), so this is recognized as plain
# inline text and matched by hand rather than via a token type.
_TASK_MARKER_RE = re.compile(r"^\[([ xX])\](?:\s+|$)")

# The same marker in the spelling `_escape_inline_text` gives it, which is how
# it reaches the front of a plain list item's serialized body. See the
# un-escaping call site in `_serialize_list`.
_ESCAPED_TASK_MARKER_RE = re.compile(r"^\\\[([ xX])\\\](\s|$)")


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

    # A bullet list is a task list when at least one item carries a checkbox
    # marker. Marked items become taskItem children, with the marker consumed
    # into the `checked` attribute; unmarked items stay plain listItem
    # children of that same taskList, markers being the only thing that
    # distinguishes the two. GFM lets a list mix them, and the editor's
    # taskList schema holds both (`MixedTaskList`). An ordered list is never
    # a task list.
    task_matches = (
        None if ordered else [_list_item_task_marker(tokens, s, e) for s, e in item_ranges]
    )
    is_task_list = bool(item_ranges) and task_matches is not None and any(task_matches)

    attrs = dict(extra_attrs or {})
    if is_task_list:
        tag = "taskList"
    else:
        tag = "orderedList" if ordered else "bulletList"
        if ordered:
            attrs["start"] = str(open_tok.attrs.get("start", 1))

    # A tight list is recorded as such so ``_serialize_list`` can keep its
    # shape. The signal is markdown-it's: a tight item's paragraph tokens
    # are hidden, and CommonMark makes looseness a whole-list property, so
    # per list they are all hidden or all visible. Only paragraphs exactly
    # two levels below this list's open token count — a nested list's
    # paragraphs are deeper and carry that list's own flag, judged by its
    # own recursive ``_build_list`` call. A list with no direct paragraphs
    # at all (items holding only nested blocks, e.g. fenced code) has no
    # signal to read and stays unstamped — serialized loose, the safe
    # direction, since a wrongly-tight stamp would strip its blank lines.
    para_level = open_tok.level + 2
    direct_paras = [
        t
        for s, e in item_ranges
        for t in tokens[s:e]
        if t.type == "paragraph_open" and t.level == para_level
    ]
    if direct_paras and all(t.hidden for t in direct_paras):
        attrs["tight"] = "true"

    items: list[XmlElement] = []
    finishers: list[Any] = []
    for idx, (item_start, item_end) in enumerate(item_ranges):
        match = task_matches[idx] if is_task_list else None  # type: ignore[index]
        if match is not None:
            checked = match.group(1).lower() == "x"
            first_text = tokens[item_start + 1].children[0]
            first_text.content = first_text.content[match.end() :]
            item_children, item_finishers = _build_block_sequence(tokens, item_start, item_end)
            items.append(XmlElement("taskItem", {"checked": checked}, contents=item_children))
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


def _code_block_from_token(tok: Any, attrs: dict[str, str]) -> XmlElement:
    """An already-tokenized code block as a ``codeBlock`` element. The token
    carries the content already stripped of whichever syntax produced it —
    the fence lines, or an indented block's 4-column indent — so this works
    the same for a top-level block and one nested in a list item, where no
    source slice is available (see ``_build_block_sequence``)."""
    language = tok.info.strip() if tok.type == "fence" else ""
    return XmlElement("codeBlock", {**attrs, "language": language}, contents=[XmlText(tok.content)])


def _build_code_block(raw: str, attrs: dict[str, str]) -> XmlElement:
    tok = next(t for t in gfm_parser().parse(raw) if t.type in ("fence", "code_block"))
    return _code_block_from_token(tok, attrs)


def _thematic_break_element(attrs: dict[str, str]) -> XmlElement:
    """A thematic break as an opaque verbatim block holding the canonical
    ``---`` spelling. The top-level path keeps the source's own spelling
    (``***``, ``___``, a longer dash run) by storing its raw slice; a nested
    one has no slice to store, and a thematic break carries no content, so
    every spelling is the same block."""
    return XmlElement(
        BlockKind.THEMATIC_BREAK.value, {**attrs, _RAW_ATTR: "1"}, contents=[XmlText("---\n")]
    )


# GFM delimiter-row cell per column alignment, keyed by the ``style``
# attribute markdown-it-py puts on a ``th``/``td`` token. A column with no
# alignment gets no style attribute at all.
_ALIGNMENT_DELIMITERS = {
    "text-align:left": ":---",
    "text-align:center": ":---:",
    "text-align:right": "---:",
}


def _table_row_line(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |\n"


_ALIGN_FROM_STYLE = {
    "text-align:left": "left",
    "text-align:center": "center",
    "text-align:right": "right",
}

_DELIMITER_FOR_ALIGN = {
    "left": ":---",
    "center": ":---:",
    "right": "---:",
    None: "---",
}


def _table_rows_from_tokens(
    tokens: list[Any], start: int, end: int, row_ids: list[str] | None
) -> tuple[list[XmlElement], list[Any]]:
    """Build ``tableRow`` elements holding real cells, one per source row.

    Cells carry their inline runs, so a cell's marks round-trip like any other
    inline content. Column alignment rides the header cells' ``align`` rather
    than a stored delimiter row, which is regenerated on the way out.
    """
    rows: list[XmlElement] = []
    finishers: list[Any] = []
    cells: list[XmlElement] = []
    is_header = False
    for i in range(start, end):
        token = tokens[i]
        if token.type == "tr_open":
            cells = []
            continue
        if token.type == "tr_close":
            row_id = row_ids[len(rows)] if row_ids and len(rows) < len(row_ids) else None
            attrs = {ROW_ID_ATTR: row_id} if row_id else {}
            rows.append(XmlElement("tableRow", attrs, contents=cells))
            continue
        if token.type in ("th_open", "td_open"):
            is_header = token.type == "th_open"
            align = _ALIGN_FROM_STYLE.get(str((token.attrs or {}).get("style", "")))
            tag = "tableHeader" if is_header else "tableCell"
            cell, cell_finishers = _element_from_segments(
                tag, {"align": align} if align else {}, _inline_runs(tokens[i + 1])
            )
            cells.append(cell)
            finishers.extend(cell_finishers)
    return rows, finishers


def _build_table(raw: str, block: BlockRange) -> tuple[XmlElement, list[Any]]:
    """A top-level GFM table as ``table > tableRow > tableCell|tableHeader``.

    Re-parses its own slice, the same way every other block kind's builder does:
    a table's source is syntactically self-contained, so the parse is exact.
    """
    tokens = gfm_parser().parse(raw)
    # Row ids stay positional and header-inclusive, matching the ids
    # ``markdown_blocks`` derives, so ``markdown_splice`` keeps pairing a live
    # row to its committed range.
    row_ids = [row.row_id for row in block.rows]
    rows, finishers = _table_rows_from_tokens(tokens, 0, len(tokens), row_ids)
    el = XmlElement("table", {BLOCK_ID_ATTR: block.block_id}, contents=rows)
    return el, finishers


def _build_nested_table(tokens: list[Any], start: int, end: int) -> tuple[XmlElement, list[Any]]:
    """A table nested in a list item or blockquote, in the same cell shape the
    top-level path builds. The editor's schema knows one table vocabulary, so a
    nested table in any other shape is a node it would drop on sync.

    Rows carry no ``_rowId``: those ids are positional within a *top-level*
    block, and a nested table has no block id of its own to derive them from.
    Only ``find_by_row_id`` needs them and it looks at top-level tables only.
    """
    rows, finishers = _table_rows_from_tokens(tokens, start, end, None)
    return XmlElement("table", {}, contents=rows), finishers


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


def build_block_element(body: str, block: BlockRange) -> tuple[XmlElement, list[Any]]:
    """Build a single top-level block's ``XmlElement`` from its span in
    ``body``. Public (no leading underscore) because
    ``markdown_splice.apply_markdown_diff`` also calls this directly, to
    splice individual replacement blocks into an existing ``Doc`` rather
    than rebuilding one from scratch via ``seed_doc_from_markdown``.

    Returns the (prelim) element plus a list of "finish" callbacks to run
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
        line = _guard_paragraph_line_for_parse(line)
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
        return _build_table(raw, block)

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
            el, finishers = build_block_element(body, block)
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


def _cell_text(cell: XmlElement) -> str:
    """A cell's inline markdown with its pipes escaped. A bare pipe would split
    the cell in two on the next parse, which is the one way a cell edit can
    silently become two cells."""
    return _serialize_inline_children(list(cell.children)).replace("|", "\\|")


def _serialize_table(node: XmlElement) -> str:
    """Rows joined as GFM, with the delimiter row regenerated from the header
    cells' alignment. The delimiter is derived rather than stored, so it can
    never disagree with the columns it describes.

    Rows whose children are text rather than cells are emitted verbatim: a
    document seeded before cells existed still serializes, and still commits.
    """
    lines: list[str] = []
    for index, row in enumerate(node.children):
        if not isinstance(row, XmlElement):
            continue
        lines.append(serialize_row(row))
        if index == 0 and any(isinstance(c, XmlElement) for c in row.children):
            lines.append(serialize_delimiter(row))
    return "".join(lines)


def serialize_row(row: XmlElement) -> str:
    """A row as a GFM line, from its cells.

    Falls back to the child's own text for a row that still holds one: a
    document seeded before cells existed keeps serializing, and keeps
    committing, until its next reseed. Empty-safe for the same reason as
    ``_serialize_code_block``: delete a row's text and the XmlText child goes
    with it, leaving a childless element whose ``children[0]`` raises.
    """
    cells = [c for c in row.children if isinstance(c, XmlElement)]
    if cells:
        return _table_row_line([_cell_text(c) for c in cells])
    kids = list(row.children)
    return kids[0].to_py() if kids else ""  # type: ignore[return-value]


def serialize_delimiter(header_row: XmlElement) -> str:
    """The `| --- |` line for a header row, from its cells' own alignment."""
    cells = [c for c in header_row.children if isinstance(c, XmlElement)]
    aligns = [dict(c.attributes).get("align") for c in cells]
    return _table_row_line([_DELIMITER_FOR_ALIGN.get(a, "---") for a in aligns])


def _serialize_block_sequence(children: list[XmlElement], indent: str, sep: str = "\n\n") -> str:
    """Inverse of ``_build_block_sequence``: block children joined by blank
    lines, with every line after the very first indented by ``indent`` so
    continuation lines / nested constructs align under the parent marker or
    blockquote ``>``.

    Each child goes through ``serialize_block``, the same function the top
    level uses, so the two directions can't drift apart into a nested
    construct that seeds but won't serialize. That includes a paragraph's
    block-start escaping: a list item or blockquote's own first line is just
    as much a fresh block-start position as the top of the document, so a
    literal leading ``-``/``>``/``#``/``---`` needs the same escaping there.
    Each block's own trailing newline is dropped here because the join
    below supplies the separation instead.

    ``sep`` is ``"\\n"`` for a tight list item's blocks (no blank between a
    tight item's paragraph and its nested list — see ``_serialize_list``,
    which only passes it when ``_tight_item_safe`` proved the reparse keeps
    the blocks apart) and the default blank-line join everywhere else."""
    parts = [serialize_block(child).rstrip("\n") for child in children]
    combined = sep.join(parts)
    lines = combined.split("\n")
    indented = [lines[0]] + [(indent + line if line else line) for line in lines[1:]]
    return "\n".join(indented)


# Blocks that may directly follow a tight item's first paragraph with no
# blank line and still reparse as their own block: constructs CommonMark
# lets interrupt a paragraph. Notably absent: ``paragraph`` (two need a
# blank between them or they merge), ``horizontalRule`` (``---`` under a
# paragraph line is a setext heading, not a rule), ``table`` (a delimiter
# row can't interrupt), and ``orderedList`` unless it starts at 1 (checked
# separately — only ``1.`` may interrupt).
_TIGHT_INTERRUPTERS = frozenset({"bulletList", "taskList", "codeBlock", "heading", "blockquote"})


def _tight_item_safe(item: XmlElement) -> bool:
    """Whether this item's blocks survive a tight (no-blank-line) join.

    A tight list parsed from markdown always satisfies this — a blank line
    inside an item would have made the whole list loose. What can't is a doc
    the editor mutated after parsing: the ``tight`` attribute lives on the
    list node, so nothing clears it when an edit gives an item a second
    paragraph. Serializing that tightly would merge the paragraphs on the
    next parse; the caller falls back to loose for the whole list instead
    (one visible reflow, after which the reparse records ``loose`` and the
    round trip is stable again)."""
    blocks = [c for c in item.children if isinstance(c, XmlElement)]
    for later in blocks[1:]:
        if later.tag == "orderedList":
            if dict(later.attributes).get("start", "1") != "1":
                return False
            continue
        if later.tag not in _TIGHT_INTERRUPTERS:
            return False
    return True


def _serialize_list(node: XmlElement) -> str:
    """Serializes a list in the style its ``tight`` attribute records —
    single newlines between a tight list's items, blank lines for a loose
    one — so editing one item leaves the rest of the list's spacing as
    written. A list with no attribute (built by the editor rather than the
    parser) serializes loose, the safe direction. Not byte-identical for
    every touched list (ordered-list renumbering, marker style) — only
    untouched blocks carry the byte-stability guarantee
    (``markdown_splice.py``), which never calls this serializer at all.
    """
    ordered = node.tag == "orderedList"
    attrs = dict(node.attributes)
    start = int(attrs.get("start", "1")) if ordered else 1
    tight = _is_tight(attrs.get("tight")) and all(
        _tight_item_safe(item)  # type: ignore[arg-type]
        for item in node.children
        if isinstance(item, XmlElement)
    )
    item_sep = "\n" if tight else "\n\n"
    block_sep = "\n" if tight else "\n\n"
    lines: list[str] = []
    for idx, item in enumerate(node.children):
        # Per item, not per list: a taskList holds plain listItems alongside
        # taskItems (see `_build_list`), and each one carries its own marker.
        is_task = item.tag == "taskItem"
        if is_task:
            checked = _is_checked(dict(item.attributes).get("checked"))
            marker = f"- [{'x' if checked else ' '}] "
            # A task item's continuation indent is the width of "- " alone,
            # not of the whole marker: `gfm_parser()` runs no task-list
            # plugin, so "[x] " is the first paragraph's own text (see
            # `_list_item_task_marker`) and CommonMark puts the item's
            # content column right after the bullet. Indenting the item's
            # later blocks under the checkbox instead put them 4 columns
            # past that content column — an indented code block on the next
            # parse, which is a live round trip: a task item with a nested
            # list or a second paragraph got rewritten into code the first
            # time anything on the page was checkpointed.
            indent = " " * len("- ")
        else:
            marker = f"{start + idx}. " if ordered else "- "
            indent = " " * len(marker)
        body = _serialize_block_sequence(list(item.children), indent, block_sep)  # type: ignore[arg-type]
        if not is_task:
            # A literal "[x] " opening a plain list item is a checkbox marker
            # the parse declined to promote — the item is in an ordered list,
            # which is never promoted (see `_build_list`) — not decorative
            # text. `_escape_inline_text` escapes every
            # "["/"]" it sees, which here would rewrite a marker that GFM
            # readers and a later uniform version of this same list still act on
            # into permanently inert text, an edit nothing in the editor shows
            # (the item renders identically either way). Bare, it re-parses to
            # exactly the text it was serialized from, so the round trip stays
            # byte-stable *and* the marker survives.
            #
            # This also normalizes a marker the source deliberately escaped,
            # and can't do otherwise: markdown-it resolves "\[x\]" to the text
            # "[x]" before this codec sees a token, so by here the two
            # spellings are one string and one of them has to be picked for
            # both. Live is the same choice `_build_list` makes on the other
            # side of the fork — a list whose items *all* carry an escaped
            # marker promotes to real taskItems — so the two paths agree
            # rather than making escaping mean opposite things in a mixed
            # list and a uniform one.
            body = _ESCAPED_TASK_MARKER_RE.sub(r"[\1]\2", body, count=1)
        lines.append(marker + body)
    return item_sep.join(lines) + "\n"


def _is_tight(value: object) -> bool:
    """Whether a list's ``tight`` attribute means tight. Same two-writer
    tolerance as ``_is_checked`` below: this codec writes the string
    ``"true"``; a node that round-tripped through a y-prosemirror client can
    hand back a real bool."""
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value == "true"


def _is_checked(value: object) -> bool:
    """Whether a ``taskItem``'s ``checked`` attribute means checked.

    Tolerant on purpose, because two writers disagree on the type. This codec
    writes a real ``bool``; a Tiptap client goes through ``y-prosemirror``, which
    stores the ProseMirror attribute value as-is — a ``bool`` normally, but the
    string ``"true"``/``"false"`` if the node was built from an older snapshot or
    parsed from ``data-checked`` HTML.

    A strict ``== "true"`` silently lost every box checked in the editor: the
    value was ``True``, the comparison was ``False``, and the box serialized back
    to ``- [ ]``. Verified directly against pycrdt, not assumed.

    Note the string case cannot be handled by truthiness — ``"false"`` is a
    non-empty string, so it would read as checked. That asymmetry is also why
    this codec now writes booleans: ``"false"`` is truthy in JavaScript too, so
    a string-valued attribute made an *unchecked* markdown box render as ticked
    in the editor.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "checked", "")
    return False


def _serialize_blockquote(node: XmlElement) -> str:
    body = _serialize_block_sequence(list(node.children), "")  # type: ignore[arg-type]
    lines = body.split("\n")
    return "\n".join(("> " + line if line else ">") for line in lines) + "\n"


def _serialize_code_block(node: XmlElement) -> str:
    """Always emits fenced syntax, even if the source was an indented code
    block — semantically identical, and simpler/more robust than
    reconstructing 4-space indentation. Same non-byte-identical-but-correct
    tradeoff as ``_serialize_list``.

    An *empty* code block has no text child at all, not a child holding "".
    Indexing it raised ``IndexError`` straight out of pycrdt, which surfaced as
    a checkpoint that crashed and retried forever: the page could never be
    saved again, and every editor on it saw "could not save". Trivially reached
    — insert a code block from the slash menu, type nothing, save."""
    attrs = dict(node.attributes)
    language = attrs.get("language", "")
    kids = list(node.children)
    content = kids[0].to_py() if kids else ""  # type: ignore[union-attr]
    fence = "```"
    while fence in content:
        fence += "`"
    # The closing fence has to start its own line, and the editor stores code
    # text without a trailing newline — nobody types a blank last line — so
    # without this the fence glued itself to the final line ("daskjqwer```"),
    # which CommonMark doesn't read as a fence at all: the block silently stopped
    # being a code block on the next round trip. Seen in an exported page.
    if content and not content.endswith("\n"):
        content += "\n"
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
# case — every "*"/"_" is already escaped unconditionally by
# `_escape_inline_text` (called from `_serialize_inline_text`) for the
# emphasis/italic reason, which breaks the run regardless of position.
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


def _guard_paragraph_line_for_parse(line: str) -> str:
    """Parse-side mirror of ``_escape_block_start_ambiguity``, below.

    ``top_level_block_ranges`` splits a multi-line paragraph into one
    ``BlockRange`` per soft-break line (every newline is its own block
    boundary now - see that function's docstring). Inside the original
    paragraph those lines were mere continuations, but once split out each
    is reparsed standalone by ``_make_inline_element`` — a fresh block-start
    position where CommonMark's lazy-continuation protections no longer
    apply. Two consequences, same shape:

    - a line with 4+ leading columns becomes an indented code block, which
      has no ``inline`` token at all, so the paragraph's content silently
      came back empty. The leading run is stripped, matching the write
      side's first-line handling.
    - a line starting with a block marker becomes that block: ``5. bought
      milk`` — safe inside a paragraph, since an ordered list not starting
      at 1 can't interrupt one — reparses standalone as an ordered *list*,
      whose inline token no longer contains the consumed ``5. `` (observed
      as lost list numbers on a real page). The marker is escaped with the
      same per-line escape serialization uses, which the parse consumes
      straight back to the original text."""
    lines = line.split("\n")
    if lines[0] and _INDENTED_CODE_FIRST_LINE_RE.match(lines[0]):
        lines[0] = lines[0].lstrip(" \t")
    return "\n".join(_escape_line_start(ln) for ln in lines)


def _escape_block_start_ambiguity(text: str) -> str:
    """A paragraph's serialized text starting with a character or pattern
    that's only special as a *block*-start marker (heading ``#``, bullet
    ``-``/``+``, thematic break ``---``, blockquote ``>``, ordered-list
    ``1.``) must stay escaped, or the next parse reinterprets this
    paragraph as a different block type entirely. Unlike
    ``_escape_inline_text``'s mark-delimiter escaping (position-independent
    — a ``*``/``_``/`` ` ``/``[``/``]`` is ambiguous anywhere in the text,
    already handled there, called from ``_serialize_inline_text``), these
    are only ambiguous at the very start of *a line*, which is why this
    checks every line a soft break produces, not just ``text[0]`` — a
    multi-line paragraph's second line becomes just as much a fresh
    block-start position once it's re-emitted, whether that's the plain
    top level, prefixed with a list item's continuation indent (which
    CommonMark still parses as a block start through up to 3 spaces), or
    with a blockquote's repeated ``> `` (``_serialize_blockquote`` adds
    that per line unconditionally, including a paragraph's internal soft
    breaks). ``*`` as a bullet marker doesn't need a case here — it's
    already escaped unconditionally by ``_escape_inline_text`` for the
    emphasis reason, which covers every line's start position too as a
    side effect.

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
        return _serialize_table(node)
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

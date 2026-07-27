# Agent Wiki Markdown Standard

Version 1.0. Governs the markdown dialect supported by `AgentWikiEditor` and
by every backend component that parses or serializes wiki page content
(`backend/app/wiki/markdown_blocks.py`, `backend/app/wiki/markdown_yjs.py`).

The key words **MUST**, **MUST NOT**, **SHALL**, **SHALL NOT**, and **MAY** in
this document are to be interpreted as in RFC 2119.

## 1. Standards

| Standard                       | Reference                                                          | Adherence                                                                                                           |
| ------------------------------ | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| CommonMark                     | [spec.commonmark.org](https://spec.commonmark.org), latest release | Full, with one deliberate exception — §5.                                                                           |
| GitHub Flavored Markdown (GFM) | [github.github.com/gfm](https://github.github.com/gfm)             | Full, except tagfilter — superseded by §5.                                                                          |
| Agent Wiki Extensions          | This document, §4                                                  | Deliberate, non-standard additions adopted where CommonMark and GFM both fall short of a high-value editor feature. |

No other markdown dialect, flavor, or informal convention is in scope.

## 2. CommonMark — Full Compliance (Mandatory; One Exception — §5)

The following CM constructs MUST be supported. (Raw HTML passthrough is
also core CommonMark, but is excluded — see §5.)

1. ATX headings (levels 1–6, `#` through `######`)
2. Setext headings (`===`/`---` underline form)
3. Thematic breaks (`---`, `***`, `___`)
4. Emphasis and strong emphasis (`*`/`_`, `**`/`__`)
5. List items — ordered and unordered
6. Nested list items via indentation
7. Code spans (single-backtick inline code)
8. Indented code blocks
9. Fenced code blocks (backtick or tilde fenced, with optional info string)
10. Block quotes (`>`)
11. Inline links
12. Reference-style links (including link reference definitions)
13. Autolinks (angle-bracket form, e.g. `<https://example.com>`)
14. Hard line breaks (trailing double-space or backslash)
15. Images (`![alt](src)`)

## 3. GFM — Adopted Extensions

The following GFM constructs MUST be supported:

1. Tables
2. Strikethrough (`~~text~~`)
3. Task list items (`- [ ]` / `- [x]`)
4. Extended autolinks (bare URLs and email addresses, no delimiters required)

GFM's fifth construct, Disallowed Raw HTML ("tagfilter"), modifies raw-HTML-
passthrough behavior. It is not adopted as such — §5 excludes raw HTML
passthrough entirely, which supersedes it.

## 4. Agent Wiki Extensions (Beyond CommonMark and GFM)

The following constructs MUST be supported despite being absent from both
referenced standards:

1. Footnotes (`[^label]` reference + `[^label]: text` definition)
2. Emoji shortcodes (`:emoji-name:`). Literal Unicode emoji characters require
   no separate support — CommonMark text content is Unicode, so a literal
   emoji character is already ordinary text.
3. Blank-line count between two blocks MUST survive a checkpoint round trip
   (save, then reopen), even though CommonMark attaches no semantic meaning
   to it — 1 blank line and 5 blank lines between two paragraphs parse to
   the identical AST and render identically, so a strict CommonMark parser
   has nowhere to record how many there were. No newline is ever implicit or
   "free": every single blank line, including the first one before/between
   blocks, becomes its own trackable pseudo-block (`BlockKind.BLANK_LINE` in
   `markdown_blocks.py`), seeded as a real, empty, directly-editable
   paragraph node — indistinguishable from one the user typed by pressing
   Enter. Nothing is ever synthesized on top of what the doc's blocks
   actually contain; a checkpoint's separator between two blocks is always
   exactly the newline each block's own serialization contributes, never an
   inferred minimum.
4. A CommonMark soft line break (a single newline joining two physical
   lines into one paragraph) is NOT honored as a within-block join. Every
   single newline the user enters (or that a paragraph happens to contain
   when read from existing content) is its own top-level block boundary —
   one paragraph node per line, full stop. This applies at the top level
   only (not yet inside list items, blockquotes, or code blocks — those
   still join a multi-line span into one block, unchanged). The only
   construct that still keeps two physical lines inside one block is a hard
   line break (§2 item 14) — trailing double-space or backslash — since
   that's an explicit, unambiguous choice the user made, not an inferred
   one. This does not change a document's rendered output or its
   byte-for-byte serialized text (concatenating each one-line block's own
   output reproduces the exact same lines); it only changes how finely the
   live editor can address and independently edit each line.

## 5. Explicitly Excluded

The following constructs MUST NOT be supported:

1. Raw HTML tags (block and inline), as defined by CommonMark's raw-HTML
   grammar — open tags, closing tags, comments, processing instructions,
   declarations, CDATA sections. MUST be treated as ordinary text, MUST NOT
   be parsed as a tag, and MUST NOT be rendered as a live element. This is a
   deliberate divergence from CommonMark, which mandates raw HTML
   passthrough.

   Autolinks (§2 item 13, e.g. `<https://example.com>`) are a distinct
   CommonMark construct — matched by a URI/email pattern, not the tag-name
   grammar above — and are unaffected by this exclusion; they MUST still
   render as links.

## 6. Checkpoint Fidelity (Live-Editing Codec)

`markdown_yjs.py`'s checkpoint serialization guarantees byte-for-byte
stability only for blocks untouched since the last commit (see
`markdown_splice.py`). A touched block's checkpoint MUST reparse to the same
block type and content, but MAY normalize the following rather than
preserve it byte-for-byte:

1. Leading whitespace (1-3 columns, or a 4+-column/tab run on what would
   otherwise be a block's own first line) at the start of a paragraph line
   MAY be trimmed when it precedes text that would otherwise reparse as a
   block-start marker (heading `#`, blockquote `>`, list bullet `-`/`+`, an
   ordered marker, a thematic break, a setext underline, a fence opener, or
   an indented code block). CommonMark's backslash escape applies only to
   punctuation, never to whitespace, so trimming that whitespace is the
   only way to guarantee the line's block type is unchanged by the next
   parse — escaping just the marker and leaving the whitespace in front of
   it round-trips the marker characters as literal text but not the
   indentation that preceded them. This applies symmetrically on both the
   write path (`_escape_block_start_ambiguity`) and the read path
   (`_strip_indented_code_ambiguity_for_parse`): a paragraph line that was
   always safe as a _continuation_ line of a bigger paragraph (indented
   code can't interrupt one already started) becomes its own top-level
   block once §4 item 4's per-line splitting promotes it to a block's own
   first line, so the same 4+-column-indent ambiguity that write-side
   escaping already handled has to be handled again on reparse.

2. Every block, once touched or newly created, always ends in exactly one
   trailing newline in the checkpointed output — including the very last
   block in the file, even if the source file's own last line never had a
   trailing newline to begin with. There is no stored, conditional state
   anywhere that decides whether a block "needs" one (`serialize_block`
   emits it unconditionally, for every block kind, always); trying to track
   that as stamped state per block was the root cause of an entire class of
   staleness bugs (a stored decision drifting out of sync with a block's
   actual current content). CommonMark attaches no meaning to whether a
   file ends in `\n` either way, so this is a normalization, not a
   reparse-safety requirement — unlike item 1 above.

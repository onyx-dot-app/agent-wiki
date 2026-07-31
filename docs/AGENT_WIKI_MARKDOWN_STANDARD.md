# Agent Wiki Markdown Standard

Version 1.0. Governs the markdown dialect supported by `AgentWikiEditor` and
by every backend component that parses or serializes wiki page content
(`backend/app/wiki/markdown_blocks.py`, `backend/app/wiki/markdown_yjs.py`).

The key words **MUST**, **MUST NOT**, **SHALL**, **SHALL NOT**, and **MAY** in
this document are to be interpreted as in RFC 2119.

## 1. Standards

| Standard | Reference | Adherence |
|---|---|---|
| CommonMark | [spec.commonmark.org](https://spec.commonmark.org), latest release | Full, with one deliberate exception — §5. |
| GitHub Flavored Markdown (GFM) | [github.github.com/gfm](https://github.github.com/gfm) | Full, except tagfilter — superseded by §5. |
| Agent Wiki Extensions | This document, §4 | Deliberate, non-standard additions adopted where CommonMark and GFM both fall short of a high-value editor feature. |

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

1. Leading whitespace (1-3 columns) at the start of a paragraph line MAY be
   trimmed when it precedes text that would otherwise reparse as a
   block-start marker (heading `#`, blockquote `>`, list bullet `-`/`+`, an
   ordered marker, a thematic break, a setext underline, or a fence
   opener). CommonMark's backslash escape applies only to punctuation, never
   to whitespace, so trimming that whitespace is the only way to guarantee
   the line's block type is unchanged by the next parse — escaping just the
   marker and leaving the whitespace in front of it round-trips the marker
   characters as literal text but not the indentation that preceded them.

2. A block nested inside a list item or blockquote is rebuilt from parsed
   tokens, not from a slice of the source: the same token line numbers
   address the undecorated source, so a slice taken inside a blockquote
   would carry its `> ` prefixes into the content. Content MUST survive
   unchanged; the syntax that spelled it MAY not. Specifically, a nested
   thematic break MAY be re-emitted as `---` whatever spelling the source
   used, and a nested table's cells MAY be re-emitted with single-space
   padding.

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

1. Raw HTML passthrough (block and inline). Any literal `<...>` sequence in
   source text MUST be treated as ordinary text — MUST NOT be parsed as an
   HTML tag, and MUST NOT be rendered as a live element. This is a
   deliberate divergence from CommonMark, which mandates raw HTML
   passthrough.

# Editor Styling Triggers

Version 1.0. Governs _how_ a construct may be created in the live
`AgentWikiEditor` (Tiptap) — not _what_ markdown it may produce. That's
[`AGENT_WIKI_MARKDOWN_STANDARD.md`](./AGENT_WIKI_MARKDOWN_STANDARD.md)'s job:
it specifies the dialect a page's markdown text must parse/serialize as, and
says nothing about editor UX. This doc is the missing layer — every
construct the standard requires falls into exactly one of two trigger
mechanisms below, and a construct MUST NOT be reachable by any mechanism not
listed for it here.

The key words **MUST**, **MUST NOT**, and **MAY** are to be interpreted as in
RFC 2119.

## 1. The two mechanisms

- **Text-shortcut-based** — typing a recognized markdown pattern converts it
  live, with no other interaction (e.g. `# ` at the start of a line becomes
  an H1; `**word**` becomes bold). Implemented as a Tiptap `InputRule` (or,
  for the two constructs with a custom revert-on-backspace state machine —
  headings and the divider — a hand-built keyboard-shortcut pair; see
  `blocks.ts`).
- **UI-based** — reachable only through an explicit user action, today the
  `/` slash command menu (`commandMenu.tsx`). No amount of typing markdown
  syntax converts it; the raw characters stay inert text.

## 2. Policy

| Construct                      | Trigger                 | Status                                                                                                                                                                                   |
| ------------------------------ | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bold / italic                  | Text-shortcut           | Implemented (StarterKit default)                                                                                                                                                         |
| Strikethrough                  | Text-shortcut           | Implemented on the frontend; **backend cannot round-trip it yet** — see open gap below                                                                                                   |
| Inline code span               | Text-shortcut           | Implemented (StarterKit default)                                                                                                                                                         |
| Headings (`#` … `######`)      | Text-shortcut           | Implemented, custom state machine (`HeadingBackspace` in `blocks.ts`)                                                                                                                    |
| Thematic break / ruler (`---`) | Text-shortcut           | Implemented, custom state machine (`ThematicBreak` in `blocks.ts`)                                                                                                                       |
| Blockquote (`>`)               | Text-shortcut           | Implemented (StarterKit default)                                                                                                                                                         |
| Bullet list / ordered list     | Text-shortcut           | Implemented (StarterKit default)                                                                                                                                                         |
| Fenced code block (` ``` `)    | Text-shortcut           | Implemented (StarterKit default)                                                                                                                                                         |
| Task list / checkbox (`[ ] `)  | Text-shortcut           | Implemented (`TaskItem` default input rule) — triggers on bare `[ ] `/`[x] `, not `- [ ] `; see note below                                                                               |
| Emoji shortcode (`:name:`)     | Text-shortcut           | Not yet implemented (neither the parser nor the editor recognizes shortcodes today)                                                                                                      |
| Links                          | UI-only (slash command) | **Not yet compliant** — `Link`'s `autolink`/`linkOnPaste` are still on, and there's no slash-command entry to create one deliberately. Open follow-up.                                   |
| Tables                         | UI-only (slash command) | No text-shortcut exists (correct), but there's also no slash-command entry yet — the opaque-row table shape has no sensible "insert blank table" seed to build one from. Open follow-up. |
| Images                         | UI-only (slash command) | Deferred entirely — not implemented on either end                                                                                                                                        |
| Footnotes                      | UI-only (slash command) | Deferred entirely — not implemented on either end                                                                                                                                        |

Note on the checkbox trigger: bullet list's own shortcut (`- ` alone) fires
the instant that two-character sequence is typed, before `[ ] ` can ever
follow it — there's no lookahead in a live `InputRule`. So `- [ ] ` and bare
`[ ] ` can't both work as independent shortcuts; the editor uses bare
`[ ] `/`[x] ` (same convention Notion/Slack use for the same reason). The
backend still always emits correct `- [ ] `/`- [x] ` GFM syntax on
checkpoint regardless of which shortcut produced the checkbox.

## 3. Rationale for the split

Text-shortcut constructs share two properties: the pattern is universal
Markdown muscle memory (every construct in that column has one obvious,
unambiguous typed form), and creating one needs no information beyond the
characters just typed — no external target, no second value to collect.

UI-only constructs break at least one of those. A link needs a URL — a
value that doesn't exist in the typed text itself, and autolinking on a bare
URL or pasted text is a real live-typing hazard (a URL pasted mid-sentence,
or typed as plain reference text, silently becomes a link the user didn't
ask for). A table needs a row/column shape to seed. An image needs a source.
A footnote needs a paired definition elsewhere in the document. All four
require the editor to collect something beyond "what the user just typed,"
which a live `InputRule` firing mid-keystroke can't do — hence UI-only,
funneled through the slash command menu (or, for links, whatever explicit
UI is designed for it — see the open follow-up above).

## 4. Adding a new construct

When a new construct is implemented, add it to §2's table before it ships —
don't let a StarterKit default (which usually ships with its own input rule
already active) silently grant a construct a text-shortcut that was never
decided on. If a bundled extension's default input rule doesn't match the
intended trigger for that construct, disable it explicitly in
`extensions.ts` rather than leaving it live by omission.

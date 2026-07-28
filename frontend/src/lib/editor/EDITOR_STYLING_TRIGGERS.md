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
  for constructs with a custom backspace behavior — see §2 — a hand-built
  keyboard-shortcut pair; see `blocks.ts`).
- **UI-based** — reachable only through an explicit user action, today the
  `/` slash command menu (`commandMenu.tsx`) or, for images, pasting or
  dropping the file itself. No amount of typing markdown
  syntax converts it; the raw characters stay inert text.

## 2. Backspace behavior for shortcut conversions

A text-shortcut construct that converts a _node type_ (heading, thematic
break, and — once built — task list/checkbox; not a mark like bold/code)
has to decide what happens when Backspace empties it back out. Two named
behaviors:

- **backspace-undo-text-styling** — reverts the construct back to the
  literal characters that triggered it (an emptied divider becomes its own
  stored `---`/`***`/`___` source text), continuing from there as ordinary
  character-by-character backspacing. Used by `ThematicBreak` in
  `blocks.ts`.
- **backspace-delete-text-styling** — deletes the styling outright: converts
  straight to a plain empty paragraph (cursor staying on the same line), no
  source text ever reappears — a second Backspace then kills that line the
  same way backspacing any other empty paragraph does. Used by headings
  (`HeadingBackspace` in `blocks.ts`) and task list/checkbox
  (`TaskItemBackspace` in `blocks.ts`).

Neither is inherently more correct than the other — it's a per-construct
product decision, not a technical default. Left undecided, Tiptap core's own
fallback chain (`undoInputRule`, then `clearNodes`-if-empty, both bound by
its always-on `Keymap` extension) produces a _mix_ of both depending on
timing: `undoInputRule` only fires if Backspace is the very next keystroke
after the conversion, reverting to source text (backspace-undo-text-styling,
but only in that one-shot window); `clearNodes` is the fallback once that
window has passed, and deletes instead (backspace-delete-text-styling). That
inconsistency is exactly why every node-converting construct needs an
explicit choice here rather than inheriting whichever one the default
happens to land on.

## 3. Policy

| Construct                      | Trigger                 | Status                                                                                                                                                                                     |
| ------------------------------ | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Bold / italic                  | Text-shortcut           | Implemented (StarterKit default)                                                                                                                                                           |
| Strikethrough                  | Text-shortcut           | Implemented (StarterKit default) — backend round-trips it as `~~text~~` (`markdown_yjs.py`, `gfm_parser`'s `strikethrough` rule)                                                           |
| Inline code span               | Text-shortcut           | Implemented, custom mark (`InlineCode` in `blocks.ts`, replacing StarterKit's default `code`) — flanking backticks kept as literal rendered text, not hidden syntax                        |
| Headings (`#` … `######`)      | Text-shortcut           | Implemented, custom state machine (`HeadingBackspace` in `blocks.ts`) — backspace-delete-text-styling                                                                                      |
| Thematic break / ruler (`---`) | Text-shortcut           | Implemented, custom state machine (`ThematicBreak` in `blocks.ts`) — backspace-undo-text-styling                                                                                           |
| Blockquote (`>`)               | Text-shortcut           | Implemented (StarterKit default)                                                                                                                                                           |
| Bullet list / ordered list     | Text-shortcut           | Implemented (StarterKit default)                                                                                                                                                           |
| Fenced code block (` ``` `)    | Text-shortcut           | Implemented (StarterKit default)                                                                                                                                                           |
| Task list / checkbox (`[ ] `)  | Text-shortcut           | Implemented (`TaskItem` default input rule) — triggers on bare `[ ] `/`[x] `, not `- [ ] `; see note below. Backspace: `TaskItemBackspace` in `blocks.ts` — backspace-delete-text-styling. |
| Emoji shortcode (`:name:`)     | Text-shortcut           | Not yet implemented (neither the parser nor the editor recognizes shortcodes today)                                                                                                        |
| Links                          | UI-only (slash command) | **Not yet compliant** — `Link`'s `autolink`/`linkOnPaste` are still on, and there's no slash-command entry to create one deliberately. Open follow-up.                                     |
| Tables                         | UI-only (slash command) | No text-shortcut exists (correct), but there's also no slash-command entry yet — the opaque-row table shape has no sensible "insert blank table" seed to build one from. Open follow-up.   |
| Images                         | UI-only (paste / drop)  | Editor side implemented (`images.ts`). Pasting or dropping an image file uploads it (`POST /api/wiki/images`) and inserts an `image` node, resizable via NodeView drag handles. Pasted rich HTML containing `<img>` also becomes this node (no upload), via `parseHTML` in `blocks.ts`. No text-shortcut or slash entry is offered. Backend endpoint: `POST /api/wiki/images` (`backend/app/api/images.py`). |
| Footnotes                      | UI-only (slash command) | Deferred entirely — not implemented on either end                                                                                                                                          |

Note on the checkbox trigger: bullet list's own shortcut (`- ` alone) fires
the instant that two-character sequence is typed, before `[ ] ` can ever
follow it — there's no lookahead in a live `InputRule`. So `- [ ] ` and bare
`[ ] ` can't both work as independent shortcuts; the editor uses bare
`[ ] `/`[x] ` (same convention Notion/Slack use for the same reason). The
backend still always emits correct `- [ ] `/`- [x] ` GFM syntax on
checkpoint regardless of which shortcut produced the checkbox.

## 4. Rationale for the split

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

## 5. Adding a new construct

When a new construct is implemented, add it to §2's table before it ships —
don't let a StarterKit default (which usually ships with its own input rule
already active) silently grant a construct a text-shortcut that was never
decided on. If a bundled extension's default input rule doesn't match the
intended trigger for that construct, disable it explicitly in
`extensions.ts` rather than leaving it live by omission.

/** Node/attribute definitions that make this editor's ProseMirror schema
 * actually match the backend's Yjs XML vocabulary (`app/wiki/
 * markdown_yjs.py`) — not scaffold work, a correctness requirement.
 *
 * Two distinct gaps this file closes, both confirmed by direct testing
 * against the real `prosemirror-model`/`y-prosemirror` packages (see the
 * originating conversation), not assumed:
 *
 * 1. `y-prosemirror` builds a ProseMirror node from a synced `Y.XmlElement`
 *    via `schema.node(el.nodeName, el.getAttributes(), children)`, and
 *    `NodeType.computeAttrs` silently *drops* any attribute the node type
 *    doesn't declare in its own `attrs` spec — it does not pass through
 *    unknown keys. StarterKit's `paragraph`/`heading`/`bulletList`/
 *    `orderedList`/`taskList`/`blockquote`/`codeBlock` don't know about
 *    `_blockId`/`_nl` (the backend's checkpoint-diffing identity), so
 *    without `BlockIdentity` below, every edit would silently strip them —
 *    breaking `markdown_splice.py`'s per-block byte-stability guarantee on
 *    the very next checkpoint.
 * 2. `el.nodeName` must match a node type *name* in the schema, or building
 *    the node throws outright. The backend emits `thematic_break`/
 *    `html_block`/`other` (opaque verbatim blocks — see its module
 *    docstring) as literal tags; StarterKit's rule node is named
 *    `horizontalRule`, and there's no default node for the other two at
 *    all. `ThematicBreak`/`HtmlBlock`/`OtherBlock` below exist purely to
 *    give the schema matching node names — same `content: "text*"` shape
 *    `@tiptap/extension-code-block` already uses for "this node's entire
 *    content is a flat text run," confirmed against the installed version.
 *
 * Table support rides the same two mechanisms: `table` is a `_blockId`-
 * carrying container (`content: "tableRow+"` in spirit, but see the actual
 * content expression below for why it's stricter), and `tableRow`/
 * `tableSeparator` are `_rowId`-carrying opaque text blocks — the backend
 * stores a table row's raw source text (pipes included) verbatim rather
 * than decomposing into cells, so a single-row edit reflows only that row.
 * `prosemirror-tables`' real per-cell grid model has no way to represent
 * that shape at all (confirmed during planning) — not used here on purpose,
 * not an oversight. No cell-editing UX (tab-between-cells, resize, merge)
 * follows from there being no cell structure to hang it off.
 */
import {
  Extension,
  InputRule,
  Mark,
  Node,
  mergeAttributes,
} from "@tiptap/core";
import { Fragment } from "@tiptap/pm/model";
import { TextSelection } from "@tiptap/pm/state";

/** Internal bookkeeping attrs never rendered into the DOM (`rendered:
 * false`) — they exist purely for the Yjs XML round trip, not for display
 * or HTML paste/import. */
function hiddenAttr() {
  return { default: null as string | null, rendered: false };
}

/** Retrofits `_blockId`/`_nl` onto StarterKit's built-in block node types —
 * see gap 1 above. Not declared per-node (can't extend a package's own
 * node definitions in place) — `addGlobalAttributes` is Tiptap's supported
 * mechanism for "this attribute applies across several existing node
 * types." */
export const BlockIdentity = Extension.create({
  name: "blockIdentity",
  addGlobalAttributes() {
    return [
      {
        types: [
          "paragraph",
          "heading",
          "bulletList",
          "orderedList",
          "taskList",
          "blockquote",
          "codeBlock",
        ],
        attributes: {
          _blockId: hiddenAttr(),
          _nl: hiddenAttr(),
        },
      },
    ];
  },
});

/** Backspace on an already-*empty* heading reverts it to a plain paragraph
 * containing the literal `"#"` markers, instead of Tiptap core's default
 * `clearNodes()` (bound via its own always-on `Keymap` extension, priority
 * 100 — same mechanism as every other core Backspace fallback), which only
 * changes the node's type back to paragraph and leaves it empty, with no
 * idea what markdown prefix produced the heading in the first place.
 *
 * Deliberately not `undoInputRule` (Tiptap's one-shot "undo the exact
 * conversion transaction" command, which core's Keymap also tries on
 * Backspace, before clearNodes): that only works if Backspace is pressed
 * *immediately* after the "# " conversion, before any other edit
 * invalidates its tracked state — type so much as one character into the
 * heading first and it can never fire again. This extension instead
 * reconstructs `"#".repeat(level)` from the heading's own `level` attr
 * every time, so backspacing out of an empty heading behaves the same way
 * regardless of how much was typed and erased first. Priority 200 (same
 * reasoning as ThematicBreak below) so this is checked before core's
 * Keymap gets a chance to run clearNodes instead. */
export const HeadingBackspace = Extension.create({
  name: "headingBackspace",
  priority: 200,
  addKeyboardShortcuts() {
    return {
      Backspace: ({ editor }) => {
        const { state } = editor;
        const { $from, empty } = state.selection;
        if (!empty || $from.parent.type.name !== "heading") return false;
        if ($from.parentOffset !== 0 || $from.parent.content.size !== 0)
          return false;
        const hashes = "#".repeat($from.parent.attrs.level as number);
        const blockStart = $from.before($from.depth);
        const blockEnd = $from.after($from.depth);
        const tr = state.tr.replaceWith(
          blockStart,
          blockEnd,
          state.schema.nodes.paragraph!.create(null, state.schema.text(hashes)),
        );
        tr.setSelection(
          TextSelection.create(tr.doc, blockStart + 1 + hashes.length),
        );
        editor.view.dispatch(tr);
        return true;
      },
    };
  },
});

/** Inline code with its flanking backtick characters kept as literal text
 * under the mark — not consumed syntax, unlike `@tiptap/extension-code`
 * (disabled in `extensions.ts`). A mark's boundary is a zero-width,
 * ambiguous caret position: two distinct ProseMirror positions render at
 * the same visual spot, a real ProseMirror/contenteditable limitation, not
 * something a mark option can fix. Keeping the backticks as real characters
 * gives that boundary actual width — a real DOM text node the browser's
 * native caret placement lands against unambiguously — so ordinary
 * character-by-character typing/backspacing needs no revert state machine
 * the way HeadingBackspace/ThematicBreak above do: deleting a backtick is
 * just deleting a character, since nothing here discards source characters
 * on conversion for a revert to ever have to reconstruct.
 *
 * `inclusive: false` (not set on the default extension) is what stops
 * typing immediately after the closing backtick from continuing to extend
 * the mark — a real, narrow ProseMirror mark-spec option, not custom logic.
 * `code: true` is load-bearing, not decorative: `prosemirror-inputrules`
 * skips every *other* input rule while the cursor carries a `code`-flagged
 * mark, which is what stops `**`/`~~`/etc. from firing inside a code span —
 * matching CommonMark's own rule that a code span can't contain nested
 * emphasis. */
export const InlineCode = Mark.create({
  name: "code",
  excludes: "_",
  code: true,
  inclusive: false,
  parseHTML() {
    return [{ tag: "code" }];
  },
  renderHTML() {
    return ["code", 0];
  },
  addCommands() {
    return {
      setCode:
        () =>
        ({ commands }: { commands: { setMark: (name: string) => boolean } }) =>
          commands.setMark(this.name),
      toggleCode:
        () =>
        ({
          commands,
        }: {
          commands: { toggleMark: (name: string) => boolean };
        }) =>
          commands.toggleMark(this.name),
      unsetCode:
        () =>
        ({
          commands,
        }: {
          commands: { unsetMark: (name: string) => boolean };
        }) =>
          commands.unsetMark(this.name),
    };
  },
  addKeyboardShortcuts() {
    return {
      "Mod-e": () => this.editor.commands.toggleCode(),
    };
  },
  addInputRules() {
    return [
      new InputRule({
        // Same shape as the default extension's own regex: an optional
        // single leading non-backtick context character (or start-of-line),
        // then a run with no interior backticks, closed by one backtick not
        // itself followed by another (so `` `` ``/``` ``` don't misfire
        // this). Unlike the default, the whole span — both backticks
        // included — becomes the mark's range; nothing gets deleted.
        find: /(^|[^`])`([^`]+)`(?!`)$/,
        handler: ({ state, range, match }) => {
          const leading = match[1] ?? "";
          const codeStart = range.from + leading.length;
          const { tr } = state;
          tr.addMark(codeStart, range.to, this.type.create());
          tr.removeStoredMark(this.type);
        },
      }),
    ];
  },
});

function createOpaqueBlock(name: string) {
  return Node.create({
    name,
    group: "block",
    content: "text*",
    marks: "",
    code: true,
    defining: true,
    addAttributes() {
      return {
        _blockId: hiddenAttr(),
        _raw: hiddenAttr(),
      };
    },
    renderHTML({ HTMLAttributes }) {
      // The DOM data-type is dash-separated, not `name` verbatim: Tailwind's
      // arbitrary-value bracket syntax treats `_` as an escaped space, so a
      // `[data-type=html_block]` selector in components.tsx's PROSE_CLASSES
      // compiles to the invalid `[data-type=html block]` — confirmed via a
      // real build failure, not a style nit. `name` itself (the schema/Yjs
      // tag) is unaffected; only this rendered attribute value changes.
      return [
        "div",
        mergeAttributes(HTMLAttributes, {
          "data-type": name.replace(/_/g, "-"),
        }),
        0,
      ];
    },
  });
}

/** A line consisting of nothing but 3+ of the same `-`/`_`/`*` character
 * (each optionally followed by spaces/tabs), 0-3 leading spaces — the
 * actual CommonMark thematic-break grammar (spec section 4.1), not a
 * simplified stand-in. Deliberately per-character-type (`-*` mixed with
 * `_` doesn't count) and deliberately permissive about interior spacing
 * (`- - -` is as valid as `---`) and trailing spaces, matching the spec
 * exactly rather than the common "just `---`" shorthand. */
const THEMATIC_BREAK_LINE_RE =
  /^ {0,3}(?:-[ \t]*){3,}$|^ {0,3}(?:_[ \t]*){3,}$|^ {0,3}(?:\*[ \t]*){3,}$/;

/** Matches the backend's literal XML tag for a `---`/`***`/`___` divider —
 * see gap 2 above. Replaces StarterKit's `horizontalRule` (disabled in
 * `extensions.ts`), not layered alongside it: only one node type can ever
 * own this content, and it has to be the one whose name the backend's Yjs
 * doc actually uses.
 *
 * Converts on Enter, once the *whole current line* is checked against
 * `THEMATIC_BREAK_LINE_RE` — not a character-triggered `InputRule` (the
 * `@tiptap/extension-horizontal-rule` precedent, and this node's own
 * earlier version). A thematic break is a line-level CommonMark construct:
 * unlike an ATX heading (`#` + space is unambiguous the instant it's
 * typed — nothing that could follow invalidates it), a line starting with
 * `---` is only decidable once the line is known to be *complete*, since
 * CommonMark still accepts interior/trailing spaces and more dashes
 * (`- - -`, `----------`) as the same construct, while any other trailing
 * content (`--- notes`) makes it plain text instead — there's no
 * character short of end-of-line that settles it either way. Verified
 * directly against a battery of these cases (bare `---`, `***`, `___`,
 * `- - -`, trailing-space, 10-dash, `--- x`, under-count `-- `/`- -`) via
 * the real regex + transaction logic, not assumed. */
export const ThematicBreak = createOpaqueBlock("thematic_break").extend({
  // Explicit, not relying on extension array order: Tiptap gives each
  // extension its own keymap plugin and checks them in priority order
  // (higher first, confirmed against the installed core's own doc comment
  // on ExtensionConfig.priority), so this must outrank StarterKit's default
  // paragraph Enter/Backspace handling (priority 100) or this node's own
  // Enter/Backspace bindings below would never be reached.
  priority: 200,
  // Lets arrow-key navigation and click-to-select treat the divider as one
  // unit rather than (invisible) text to move/click into — see renderHTML
  // below. Doesn't by itself fix Backspace-after (see addKeyboardShortcuts):
  // `content: "text*"` still makes ProseMirror's default joinBackward treat
  // this node as a textblock to merge into, `atom` or not — confirmed by
  // driving prosemirror-commands' joinBackward directly against a doc built
  // from this exact schema shape, not assumed from the docs.
  atom: true,
  // Unlike createOpaqueBlock's default renderHTML, a divider's text content
  // ("---\n", stored only so it round-trips through a checkpoint — see
  // addKeyboardShortcuts below) must never actually be visible: the base
  // renderHTML puts the content hole (the `0`) directly inside the visible
  // div, which rendered the literal "---" text *and* the styled rule on top
  // of it. The content hole moves into a zero-size wrapper here instead —
  // present in the DOM for ProseMirror's sake, invisible to the reader.
  renderHTML({ HTMLAttributes }) {
    return [
      "div",
      mergeAttributes(HTMLAttributes, { "data-type": "thematic-break" }),
      ["span", { style: "display: none" }, 0],
    ];
  },
  addKeyboardShortcuts() {
    return {
      Enter: ({ editor }) => {
        const { state } = editor;
        const { $from, empty } = state.selection;
        if (!empty || $from.parent.type.name !== "paragraph") return false;
        if (!THEMATIC_BREAK_LINE_RE.test($from.parent.textContent))
          return false;
        const blockStart = $from.before($from.depth);
        const blockEnd = $from.after($from.depth);
        const divider = state.schema.nodes.thematic_break!.create(
          null,
          state.schema.text("---\n"),
        );
        const freshParagraph = state.schema.nodes.paragraph!.create();
        const tr = state.tr.replaceWith(
          blockStart,
          blockEnd,
          Fragment.from([divider, freshParagraph]),
        );
        tr.setSelection(
          TextSelection.create(tr.doc, blockStart + divider.nodeSize + 1),
        );
        editor.view.dispatch(tr);
        return true;
      },
      // Backspace at the very start of a block immediately preceded by a
      // divider — not ProseMirror's default joinBackward, which (verified
      // directly) merges into the divider's hidden text content instead of
      // removing the node, since content: "text*" makes it a "textblock"
      // for merge purposes same as any paragraph.
      //
      // When the block the cursor is in is itself empty (the fresh
      // paragraph the Enter handler above creates right after converting),
      // this collapses *both* nodes — divider and that empty paragraph —
      // into one paragraph holding the divider's own stored text
      // (reconstructed from its text child, not hardcoded "---": a divider
      // seeded from the backend could carry "***\n" or "- - -\n" instead),
      // cursor at the end of it. Mirrors HeadingBackspace above: the
      // conversion unwinds back to literal source text on the way out,
      // continuing on from there as ordinary character-by-character
      // backspacing. If the block isn't empty (the user typed real content
      // after the divider), that reconstruction doesn't apply — just the
      // divider itself is removed, leaving what was typed alone.
      Backspace: ({ editor }) => {
        const { state } = editor;
        const { $from, empty } = state.selection;
        if (!empty || $from.parentOffset !== 0) return false;
        const posBefore = $from.before($from.depth);
        if (posBefore === 0) return false;
        const nodeBefore = state.doc.resolve(posBefore).nodeBefore;
        if (!nodeBefore || nodeBefore.type.name !== "thematic_break")
          return false;
        const dividerStart = posBefore - nodeBefore.nodeSize;

        if (
          $from.parent.type.name === "paragraph" &&
          $from.parent.content.size === 0
        ) {
          const blockEnd = $from.after($from.depth);
          const text = nodeBefore.textContent.replace(/\n$/, "");
          const replacement =
            text.length > 0
              ? state.schema.nodes.paragraph!.create(
                  null,
                  state.schema.text(text),
                )
              : state.schema.nodes.paragraph!.create();
          const tr = state.tr.replaceWith(dividerStart, blockEnd, replacement);
          // tr.setSelection(
          //   TextSelection.create(tr.doc, dividerStart + 1 + text.length),
          // );
          editor.view.dispatch(tr);
          return true;
        }

        return editor
          .chain()
          .deleteRange({ from: dividerStart, to: posBefore })
          .run();
      },
    };
  },
});

/** A raw HTML block (e.g. an embedded `<iframe>` or comment) — opaque
 * verbatim, same as a thematic break. */
export const HtmlBlock = createOpaqueBlock("html_block");

/** Anything `markdown_blocks.py` couldn't classify more specifically —
 * the backend's catch-all, kept opaque here for the same reason. */
export const OtherBlock = createOpaqueBlock("other");

/** A GFM table's header row is required, immediately followed by its
 * required delimiter row, then zero or more data rows — the shape
 * `markdown_yjs.py`'s TABLE branch always produces (a table with no header
 * isn't valid GFM to begin with). Stricter than a loose `tableRow+` on
 * purpose: there's no row-insert/delete UI for this opaque-row node (see
 * the module docstring), so the doc should never legitimately reach any
 * other shape. */
export const Table = Node.create({
  name: "table",
  group: "block",
  content: "tableRow tableSeparator tableRow*",
  defining: true,
  addAttributes() {
    return { _blockId: hiddenAttr() };
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "div",
      mergeAttributes(HTMLAttributes, { "data-type": "table" }),
      0,
    ];
  },
});

function createTableRowNode(name: string) {
  return Node.create({
    name,
    content: "text*",
    marks: "",
    code: true,
    defining: true,
    addAttributes() {
      return { _rowId: hiddenAttr() };
    },
    renderHTML({ HTMLAttributes }) {
      return ["div", mergeAttributes(HTMLAttributes, { "data-type": name }), 0];
    },
  });
}

/** A table row's raw source line (pipes included), verbatim — not
 * decomposed into cells. */
export const TableRow = createTableRowNode("tableRow");

/** The `| --- | --- |`-style delimiter row between a table's header and
 * its body, equally opaque — editing it is possible (it's still just
 * text) but not a supported/validated interaction; nothing stops a user
 * from breaking their own table's syntax here, same as they could in raw
 * markdown. */
export const TableSeparator = createTableRowNode("tableSeparator");

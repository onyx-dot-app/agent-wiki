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
  getMarkRange,
  mergeAttributes,
  type CommandProps,
} from "@tiptap/core";
import { Fragment } from "@tiptap/pm/model";
import { Plugin, TextSelection, type Transaction } from "@tiptap/pm/state";
import { canJoin } from "@tiptap/pm/transform";
import { TaskList } from "@tiptap/extension-task-list";
import {
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@tiptap/extension-table";
import { isSameOriginSrc } from "./media";
import { ScrollFollowTableView } from "@/lib/editor/table/ScrollFollowTableView";
import { clearSelectedCells } from "@/lib/editor/table/tableCommands";

/** A line consisting of nothing but 3+ of the same `-`/`_`/`*` character
 * (each optionally followed by spaces/tabs), 0-3 leading spaces — the
 * actual CommonMark thematic-break grammar (spec section 4.1), not a
 * simplified stand-in. Deliberately per-character-type (`-*` mixed with
 * `_` doesn't count) and deliberately permissive about interior spacing
 * (`- - -` is as valid as `---`) and trailing spaces, matching the spec
 * exactly rather than the common "just `---`" shorthand. Used by
 * `ThematicBreak` below. */
const THEMATIC_BREAK_LINE_RE =
  /^ {0,3}(?:-[ \t]*){3,}$|^ {0,3}(?:_[ \t]*){3,}$|^ {0,3}(?:\*[ \t]*){3,}$/;

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
const BlockIdentity = Extension.create({
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
      {
        // The codec's tight/loose list spacing (markdown_yjs.py) — declared
        // here so a node rebuilt by the editor doesn't silently drop it,
        // which would reflow the list loose on the next checkpoint.
        types: ["bulletList", "orderedList", "taskList"],
        attributes: {
          tight: hiddenAttr(),
        },
      },
    ];
  },
});

/** Adjacent same-type lists are joined into one — markdown cannot express
 * two lists back to back (a reparse reads them as a single list), so a doc
 * holding that shape disagrees with every future parse of its own file.
 *
 * That disagreement is not cosmetic; it is the engine of a live-corruption
 * loop. The checkpoint's `restamp_block_ids` maps blocks positionally
 * against the file's parse, so one file block covering two doc nodes gets
 * ONE id stamped onto both; `UniqueBlockIdentity` below then clears the
 * second node's id (its invariant); a cleared id reads as freshly-typed
 * content to `checkpoint_body`, which appends the node's serialization
 * while the base's verbatim range still contains it — duplicating that
 * fragment in the file, every checkpoint, forever (the restamp re-shares,
 * the clear re-fires). Joining removes the shape both sides are fighting
 * over, and the join itself marks the merged list as edited, so the next
 * checkpoint re-serializes it from the document and heals any duplicates
 * an earlier cycle already wrote into the file.
 *
 * Every boundary is joined in one appended transaction, later boundaries
 * mapped through the earlier joins, so a run of any length converges in a
 * single pass. */
const JoinAdjacentLists = Extension.create({
  name: "joinAdjacentLists",
  addProseMirrorPlugins() {
    const listTypes = new Set(["bulletList", "orderedList", "taskList"]);
    return [
      new Plugin({
        appendTransaction(transactions, _oldState, newState) {
          if (!transactions.some((tr) => tr.docChanged)) return null;
          const boundaries: number[] = [];
          let prevType: string | null = null;
          let pos = 0;
          newState.doc.forEach((node) => {
            const type = node.type.name;
            if (
              prevType === type &&
              listTypes.has(type) &&
              canJoin(newState.doc, pos)
            ) {
              boundaries.push(pos);
            }
            prevType = type;
            pos += node.nodeSize;
          });
          if (boundaries.length === 0) return null;
          const tr = newState.tr;
          for (const boundary of boundaries) {
            tr.join(tr.mapping.map(boundary));
          }
          return tr;
        },
      }),
    ];
  },
});

/** Safety net: no two top-level blocks may ever carry the same `_blockId` —
 * it's `markdown_splice.checkpoint_body`'s sole key for matching a live
 * block back to its position in the committed markdown
 * (`orig_by_id.get(block_id)`), and two top-level nodes sharing one id
 * resolve to the *same* original range, producing duplicated/extra content
 * on checkpoint (confirmed directly against the real backend — splitting a
 * paragraph containing a soft break, e.g. via Enter mid-text, is the most
 * common trigger).
 *
 * This isn't hypothetical, and it isn't narrowly a "handle Enter specially"
 * fix either: ProseMirror's default node-splitting copies a node's own
 * attrs onto *both* resulting halves unconditionally — there is no per-
 * attribute "clear on split" hook for node attrs the way marks have
 * `keepOnSplit`. Rather than chase every keyboard shortcut that could ever
 * cause a top-level split (today: plain Enter; tomorrow: anything else),
 * this corrects the invariant directly, in an `appendTransaction` that
 * runs after *every* transaction regardless of cause. Only the first
 * top-level node keeps a duplicated id; every later one is treated as new
 * content instead (matching exactly how a genuinely new, freshly-typed
 * block already behaves — `_blockId: null` routes it through
 * `checkpoint_body`'s `orig is None` fast path, the same path a fresh
 * empty paragraph already takes safely). */
const UniqueBlockIdentity = Extension.create({
  name: "uniqueBlockIdentity",
  addProseMirrorPlugins() {
    return [
      new Plugin({
        appendTransaction(transactions, _oldState, newState) {
          if (!transactions.some((tr) => tr.docChanged)) return null;
          const seen = new Set<string>();
          let tr: Transaction | null = null;
          newState.doc.forEach((node, offset) => {
            const blockId = node.attrs._blockId as string | null | undefined;
            if (!blockId) return;
            if (seen.has(blockId)) {
              tr = (tr ?? newState.tr).setNodeAttribute(
                offset,
                "_blockId",
                null,
              );
              // Not every top-level node type declares `_nl` (e.g. `table`
              // only has `_blockId`) - only clear it where the schema
              // actually has it, matching how a fresh node's attrs shape
              // looks.
              if ("_nl" in node.attrs) {
                tr = tr.setNodeAttribute(offset, "_nl", null);
              }
            } else {
              seen.add(blockId);
            }
          });
          return tr;
        },
      }),
    ];
  },
});

/** Backspace on an already-*empty* heading deletes the heading styling
 * outright — converts straight to a plain empty paragraph, cursor left in
 * place on the same line, never reverting to literal `"#"` markers first
 * (backspace-delete-text-styling, per EDITOR_STYLING_TRIGGERS.md §2 — same
 * choice as `TaskItemBackspace` below, for the same reason: less technical,
 * no markdown syntax exposed mid-edit for a construct with nothing left
 * typed in it). A *second* Backspace needs no special handling here at
 * all: once the node is a plain empty paragraph, Tiptap core's own default
 * Backspace chain (`joinBackward`) already merges an empty paragraph into
 * whatever precedes it — exactly "kill this line, cursor lands on the
 * previous one" for that second press, for free.
 *
 * Still has to be its own priority-200 extension rather than leaving even
 * the *first* step to that same core default: core's Keymap tries
 * `undoInputRule` before any of its merge/clear fallbacks, and that command
 * would revert to literal `"# "` text instead — but only in the narrow
 * window where Backspace is the very next keystroke after the "# "
 * conversion, before any other edit invalidates its tracked state; past
 * that window something else in the chain takes over instead. Same
 * inconsistent, timing-dependent trap `TaskItemBackspace` below already
 * documents avoiding for checkboxes — this extension avoids it the same
 * way, by outranking `undoInputRule` outright so the outcome never depends
 * on timing. Priority 200, same reasoning as `ThematicBreak` below (which
 * keeps the *other* behavior, backspace-undo-text-styling, deliberately —
 * see its own docstring). */
const HeadingBackspace = Extension.create({
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
        const blockStart = $from.before($from.depth);
        const blockEnd = $from.after($from.depth);
        const tr = state.tr.replaceWith(
          blockStart,
          blockEnd,
          state.schema.nodes.paragraph!.create(),
        );
        tr.setSelection(TextSelection.create(tr.doc, blockStart + 1));
        editor.view.dispatch(tr);
        return true;
      },
    };
  },
});

/** Backspace on an already-*empty* task item deletes the checkbox/list
 * styling outright — converts straight to a plain empty paragraph, never
 * reverting to literal `[ ] ` text (backspace-delete-text-styling, per
 * EDITOR_STYLING_TRIGGERS.md §2 — deliberately the opposite choice from
 * ThematicBreak's backspace-undo-text-styling below — see its docstring;
 * HeadingBackspace above used to make the same choice but was switched to
 * backspace-delete-text-styling too, for the same reason this construct
 * uses it).
 *
 * Neither `@tiptap/extension-task-item` nor `@tiptap/extension-task-list`
 * register any keyboard shortcuts of their own (confirmed directly against
 * the installed packages — neither file so much as mentions "Backspace").
 * `ListKeymap` (the sibling extension that *would* handle this, via its own
 * `handleBackspace` helper) isn't part of either package and isn't
 * registered in `extensions.ts`, so today Backspace on a task item falls
 * through entirely to Tiptap core's always-on `Keymap` extension, which
 * tries `undoInputRule` first on every Backspace — a one-shot "undo the
 * exact conversion transaction" that only fires if Backspace is the very
 * next keystroke after the `[ ] ` conversion, with zero edits in between.
 * In that narrow window it reverts to literal `[ ] ` text; past it, the
 * chain's later fallbacks (`clearNodes`, `joinBackward`) happen to already
 * land on a plain paragraph instead — an inconsistent mix depending purely
 * on timing, not a deliberate choice. Priority 200 (same reasoning as
 * HeadingBackspace/ThematicBreak) means this handler is checked first, so
 * `undoInputRule` never gets a chance to run for this construct — the
 * outcome is the same regardless of timing.
 *
 * `liftListItem` (Tiptap core, wrapping `prosemirror-schema-list`'s own
 * command of the same name) already handles first/middle/last-item
 * splitting correctly — not hand-rolled, since `prosemirror-schema-list` is
 * the well-tested primitive for exactly this "exit the list" operation,
 * and it's available as a core command regardless of whether `ListKeymap`
 * is installed. */
/** `taskList` widened to hold plain `listItem`s alongside `taskItem`s.
 *
 * GFM lets a single list mix checkbox and non-checkbox items freely — a TODO
 * page where some bullets are tasks and some are section labels ("Phase 2",
 * "@ mention someone") is the ordinary case, not an edge one. Tiptap's stock
 * `taskList` is `taskItem+`, which cannot represent that, so the backend codec
 * has to demote any mixed list to a plain `bulletList` and every `[x]` in it
 * renders as literal text instead of a checkbox (see `_build_list`'s uniformity
 * rule).
 *
 * The item types already coexist cleanly: `listItem` and `taskItem` both hold
 * `paragraph block*`, so nesting, lifting and splitting behave the same in
 * either, and `prosemirror-schema-list`'s commands operate on whichever type
 * they're given. The only thing the stock spec adds is the uniformity
 * constraint itself.
 *
 * The rendered `li` carries `data-checked` on task items and no attributes at
 * all on plain ones, which is what the stylesheet keys the checkbox row off —
 * a plain item in a task list gets an ordinary bullet, aligned to the same
 * text column so a mixed list reads as one list rather than two interleaved
 * ones. */
const MixedTaskList = TaskList.extend({
  content() {
    return `(${this.options.itemTypeName}|listItem)+`;
  },
});

const TaskItemBackspace = Extension.create({
  name: "taskItemBackspace",
  priority: 200,
  addKeyboardShortcuts() {
    return {
      Backspace: ({ editor }) => {
        const { state } = editor;
        const { $from, empty } = state.selection;
        if (!empty || $from.parent.type.name !== "paragraph") return false;
        if ($from.parentOffset !== 0 || $from.parent.content.size !== 0)
          return false;
        if ($from.node($from.depth - 1)?.type.name !== "taskItem") return false;
        return editor.chain().liftListItem("taskItem").run();
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
 * native caret placement lands against unambiguously.
 *
 * `inclusive: false` (not set on the default extension) is what stops
 * typing immediately after the closing backtick from continuing to extend
 * the mark — a real, narrow ProseMirror mark-spec option, not custom logic.
 * `code: true` is load-bearing, not decorative: `prosemirror-inputrules`
 * skips every *other* input rule while the cursor carries a `code`-flagged
 * mark, which is what stops `**`/`~~`/etc. from firing inside a code span —
 * matching CommonMark's own rule that a code span can't contain nested
 * emphasis. */
const InlineCode = Mark.create({
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
      // Backtick-aware, because this mark's own invariant (see the
      // appendTransaction net below) is that the marked run's first and
      // last characters ARE literal backticks: a plain toggleMark applies
      // a tickless mark that the net strips on the very next transaction —
      // the command must create the same shape the input rule creates, and
      // remove the ticks along with the mark on the way off.
      toggleCode:
        () =>
        ({ state, tr, dispatch }: CommandProps) => {
          const type = this.type;
          const { $from, $to, from, to, empty } = state.selection;
          const active = empty
            ? !!getMarkRange($from, type)
            : state.doc.rangeHasMark(from, to, type);
          if (active) {
            // The unwrap must cover the marked run's *own* bounds, not the
            // selection's: a selection that merely overlaps the run would
            // otherwise strip the mark from a slice and leave the literal
            // backticks behind — which the next checkpoint's reparse turns
            // straight back into a code span. When the selection starts
            // outside the run, resolve inside its first marked character.
            // (Several runs in one selection unwrap first-run-only — same
            // fuzziness as Tiptap's own mark toggles.)
            let range = getMarkRange($from, type);
            if (!range) {
              let marked: number | null = null;
              state.doc.nodesBetween(from, to, (node, pos) => {
                if (marked === null && node.isText && type.isInSet(node.marks))
                  marked = pos;
              });
              if (marked !== null)
                range = getMarkRange(state.doc.resolve(marked + 1), type);
            }
            if (!range) return false;
            if (!dispatch) return true;
            const { from: a, to: b } = range;
            tr.removeMark(a, b, type);
            if (state.doc.textBetween(b - 1, b) === "`") tr.delete(b - 1, b);
            if (state.doc.textBetween(a, a + 1) === "`") tr.delete(a, a + 1);
            return true;
          }
          // Wrapping needs a real single-block selection with no interior
          // backticks (the serializer's fence would misparse them).
          if (empty || !$from.sameParent($to)) return false;
          if (state.doc.textBetween(from, to).includes("`")) return false;
          if (!dispatch) return true;
          tr.insertText("`", to);
          tr.insertText("`", from);
          tr.addMark(from, to + 2, type.create());
          tr.removeStoredMark(type);
          return true;
        },
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
          // The trigger character (the closing backtick that just matched
          // the regex above) has NOT been inserted into the doc yet at
          // this point — an InputRule's handler fully owns the keystroke's
          // effect (confirmed directly against prosemirror-view's
          // handleTextInput call sites: they only fall back to their own
          // `deflt()` insertion when the plugin does *not* handle the
          // input), so it has to be inserted explicitly here or it's
          // silently dropped — a real bug this once had, not a
          // hypothetical: `tr.addMark` alone marked the already-existing
          // "`code" span but never inserted the "`" that triggered it,
          // leaving only the opening backtick ever visible.
          tr.insertText("`", range.to);
          tr.addMark(codeStart, range.to + 1, this.type.create());
          tr.removeStoredMark(this.type);
        },
      }),
    ];
  },
  addProseMirrorPlugins() {
    // Safety net: a code-marked run's own first and last characters must
    // still be a real backtick, or the mark comes off — same
    // "appendTransaction runs after every transaction regardless of cause"
    // reasoning as UniqueBlockIdentity above, and needed for the same kind
    // of reason: ProseMirror marks are independent of the text carrying
    // them, so backspacing out a flanking backtick (or Delete, cut, an
    // undo, a drag — any doc-changing transaction, not just one specific
    // keyboard shortcut) shrinks the marked range but does not itself
    // remove the mark from whatever's left. Left unhandled, deleting just
    // the "`" leaves the remaining text still styled as code with no visible
    // delimiter at all - confusing, and it wouldn't reparse as code either
    // (markdown_yjs.py's _wrap_code_run would reconstruct a *fresh* fence
    // around it on the next checkpoint, silently reintroducing the backtick
    // the user just deleted). Matches this file's backspace-delete-text-
    // styling convention (TaskItemBackspace, HeadingBackspace above): once
    // the delimiter breaks, the styling goes, immediately, not just on the
    // next full deletion.
    const codeType = this.type;
    return [
      new Plugin({
        appendTransaction(transactions, _oldState, newState) {
          if (!transactions.some((tr) => tr.docChanged)) return null;
          let tr: Transaction | null = null;
          newState.doc.descendants((node, pos) => {
            if (!node.isTextblock) return true;
            let runStart = -1;
            let runText = "";
            const flush = (runEnd: number) => {
              if (runStart < 0) return;
              if (
                runText.length < 2 ||
                !runText.startsWith("`") ||
                !runText.endsWith("`")
              ) {
                tr = (tr ?? newState.tr).removeMark(runStart, runEnd, codeType);
              }
              runStart = -1;
              runText = "";
            };
            let contentSize = 0;
            node.forEach((child, offset) => {
              const childPos = pos + 1 + offset;
              if (child.isText && codeType.isInSet(child.marks)) {
                if (runStart < 0) runStart = childPos;
                runText += child.text ?? "";
              } else {
                flush(childPos);
              }
              contentSize = offset + child.nodeSize;
            });
            flush(pos + 1 + contentSize);
            return false;
          });
          return tr;
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
const ThematicBreak = createOpaqueBlock("thematic_break").extend({
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
          // _raw: "1" matches what the backend stamps on every opaque block
          // it seeds from existing markdown (`_build_block_element` in
          // markdown_yjs.py) - serialize_block's opaque-block fallback
          // checks for this exact string, so a divider created live here
          // without it fails that check and falls through to serialize_block's
          // final `raise NotImplementedError` the next time a checkpoint
          // tries to serialize it.
          { _raw: "1" },
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
const HtmlBlock = createOpaqueBlock("html_block");

/** Anything `markdown_blocks.py` couldn't classify more specifically —
 * the backend's catch-all, kept opaque here for the same reason. */
const OtherBlock = createOpaqueBlock("other");

/** Re-declares `_blockId`/`_rowId`, which `@tiptap/extension-table` does not.
 * `computeAttrs` drops an undeclared attribute on the first edit, breaking
 * per-row byte stability at the next checkpoint. */
const TableIdentity = Extension.create({
  name: "tableIdentity",
  addGlobalAttributes() {
    return [
      { types: ["table"], attributes: { _blockId: hiddenAttr() } },
      { types: ["tableRow"], attributes: { _rowId: hiddenAttr() } },
    ];
  },
});

/** GFM cells are inline-only, so the schema says so rather than a renderer
 * stripping blocks after the fact. Overriding `content` alone keeps the
 * upstream attributes, `align` among them, which the checkpoint reads. */
const GfmCell = TableCell.extend({ content: "inline*" });
const GfmHeader = TableHeader.extend({ content: "inline*" });

/** Widths live on each cell's `colwidth` in the document, so a drag survives
 * reload but not a reseed: markdown has no width syntax. `cellMinWidth` is
 * also the floor for a column never sized. */
const WikiTable = Table.extend({
  addKeyboardShortcuts() {
    const clear = () =>
      clearSelectedCells(this.editor.state, this.editor.view.dispatch);
    return {
      ...this.parent?.(),
      Backspace: clear,
      Delete: clear,
      "Mod-Backspace": clear,
      "Mod-Delete": clear,
    };
  },
}).configure({
  resizable: true,
  handleWidth: 6,
  cellMinWidth: 64,
  lastColumnResizable: true,
  View: ScrollFollowTableView,
});

const MarkdownLink = Extension.create({
  name: "markdownLink",

  /** Cmd/Ctrl-click opens a link while editing.
   *
   * `openOnClick` stays false, so a plain click still just places the caret —
   * you have to be able to click into link text to fix a typo in it. Tiptap's
   * own click handler can't express "only with a modifier", and its
   * `"whenNotEditable"` option is a red herring here: it is coerced to `true`
   * before reaching the handler, which then bails on `!view.editable` anyway.
   *
   * Read-only viewers need nothing from this. `editable: false` means the
   * editor DOM isn't contenteditable, so anchors get native browser
   * behaviour — that path was never broken. */
  addProseMirrorPlugins() {
    return [
      new Plugin({
        props: {
          handleClick(view, _pos, event) {
            if (event.button !== 0 || !(event.metaKey || event.ctrlKey)) {
              return false;
            }
            const target = event.target as HTMLElement | null;
            const anchor = target?.closest?.("a") ?? null;
            if (!anchor || !view.dom.contains(anchor)) return false;
            const href = anchor.getAttribute("href");
            if (!href) return false;
            window.open(href, "_blank", "noopener,noreferrer");
            return true;
          },
        },
      }),
    ];
  },

  /** Mod-k strips the link from the selection (or from the link under the
   * caret), leaving the text — a keyboard peer of the hover editor's unlink
   * (`linkHover.ts`), handy for clearing a promoted URL you didn't want.
   *
   * Returns false when there's no link involved, so the shortcut falls through
   * to the browser instead of silently swallowing Cmd-K. */
  addKeyboardShortcuts() {
    return {
      "Mod-k": () => {
        if (!this.editor.isActive("link")) return false;
        return this.editor
          .chain()
          .focus()
          .extendMarkRange("link")
          .unsetMark("link")
          .run();
      },
    };
  },
});

/** The inline image node. Named `image` with exactly `{src, alt, title}` so
 * y-prosemirror name-matches the backend codec's leaf
 * (`app/wiki/markdown_yjs.py`) and round-trips every attribute. Width is
 * deliberately not an attr, see `images.ts` for the `#w=` src-fragment
 * scheme. Atom + draggable so it selects and moves as one unit. This
 * `renderHTML` is only the clipboard/serialization fallback, and `parseHTML`
 * is what lets a pasted `<img>` become this node. */
const Image = Node.create({
  name: "image",
  group: "inline",
  inline: true,
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      src: { default: "" },
      alt: { default: "" },
      title: { default: null },
    };
  },
  parseHTML() {
    return [
      {
        tag: "img[src]",
        // Only images this wiki serves. An external src pasted from a web page
        // would put a third-party URL in the shared document that every reader
        // then fetches, and that breaks for good once the source moves.
        getAttrs: (element) =>
          isSameOriginSrc((element as HTMLElement).getAttribute("src") ?? "")
            ? null
            : false,
      },
    ];
  },
  renderHTML({ HTMLAttributes }) {
    return ["img", mergeAttributes(HTMLAttributes)];
  },
});

export {
  BlockIdentity,
  JoinAdjacentLists,
  UniqueBlockIdentity,
  HeadingBackspace,
  MixedTaskList,
  TaskItemBackspace,
  InlineCode,
  ThematicBreak,
  HtmlBlock,
  OtherBlock,
  TableIdentity,
  WikiTable,
  TableRow,
  GfmCell,
  GfmHeader,
  MarkdownLink,
  Image,
};

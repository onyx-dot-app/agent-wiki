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
import { Extension, Node, mergeAttributes } from "@tiptap/core";

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
 * doc actually uses. */
export const ThematicBreak = createOpaqueBlock("thematic_break");

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

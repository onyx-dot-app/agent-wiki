/**
 * Raw ProseMirror node/mark schema for the AgentWikiEditor live doc — must
 * match `backend/app/wiki/markdown_yjs.py`'s node/mark names and attributes
 * exactly, since that's the codec `y-prosemirror` reads the live Y.Doc
 * through (a Y.Doc built by the Python backend decodes into a PM doc via
 * `yXmlFragmentToProseMirrorRootNode`, matching by node/mark *name*, not by
 * any schema-definition-time linkage).
 *
 * Node names: `heading`, `paragraph`, `bulletList`, `orderedList`,
 * `listItem`, `taskList`, `taskItem`, `blockquote`, `codeBlock`, `table`,
 * `tableRow`, `tableSeparator`, `hardBreak`, plus the opaque-verbatim kinds
 * `thematic_break`/`html_block`/`other`. Mark names: `bold`, `italic`,
 * `code`, `link` (`link`'s Yjs-format value is `{href}`, an attrs object —
 * not a bare string; verified directly against `y-prosemirror`, and the
 * exact bug this comment warns against was caught and fixed in
 * `markdown_yjs.py`'s `_inline_runs`).
 *
 * `_blockId`/`_nl` (or `_rowId` for table rows) ride on every *top-level*
 * block (positional id; `_nl` = "1" if the source line had a trailing
 * newline) but are absent on nested children (a list item's own paragraph,
 * etc.) — declared as optional string attrs with a default so a nested
 * instance never errors. They're wire-only bookkeeping, never rendered.
 *
 * One unavoidable wrinkle: Yjs `XmlElement` attributes are always strings
 * (the underlying XML data model), so `heading.level`, `orderedList.start`,
 * and `taskItem.checked` sync as *strings* even though this schema's own
 * `attrs` declarations give them number/boolean defaults for the rest of
 * the app to work with normally. Every read site that touches one of these
 * three attrs must coerce — see `coerceLevel`/`coerceStart`/`coerceChecked`
 * below, used by `toDOM` and anywhere else in this codebase that reads them
 * off a live (Yjs-synced) node.
 */

import { Schema } from "prosemirror-model";
import type { DOMOutputSpec, NodeSpec, MarkSpec } from "prosemirror-model";

export function coerceLevel(value: unknown): number {
  const n = Number(value);
  return n >= 1 && n <= 6 ? n : 1;
}

export function coerceStart(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 1;
}

export function coerceChecked(value: unknown): boolean {
  return value === true || value === "true";
}

const blockIdAttrs = {
  _blockId: { default: null as string | null },
  _nl: { default: "1" },
};

const rowIdAttrs = {
  _rowId: { default: null as string | null },
};

/** Opaque verbatim block: holds its raw markdown source as plain text
 * content, no rich structure. Used for `codeBlock` (marks stripped by
 * design — code semantics, not rich text), the table row kinds, and the
 * thematic-break/html-block/other passthrough kinds — all four are
 * "editable as raw text, not decomposed" by the backend codec's own
 * design (row-level tables, verbatim opaque blocks), so the schema mirrors
 * that rather than pretending to a richness the codec doesn't have. */
function opaqueTextBlock(
  extraAttrs: Record<string, { default: unknown }>,
): NodeSpec {
  return {
    content: "text*",
    marks: "",
    code: true,
    defining: true,
    attrs: { ...blockIdAttrs, ...extraAttrs },
  };
}

const nodes: Record<string, NodeSpec> = {
  doc: { content: "block+" },

  text: { group: "inline" },

  paragraph: {
    content: "inline*",
    group: "block",
    attrs: blockIdAttrs,
    parseDOM: [{ tag: "p" }],
    toDOM: (): DOMOutputSpec => ["p", 0],
  },

  heading: {
    content: "inline*",
    group: "block",
    defining: true,
    attrs: { level: { default: 1 }, ...blockIdAttrs },
    parseDOM: [1, 2, 3, 4, 5, 6].map((level) => ({
      tag: `h${level}`,
      attrs: { level },
    })),
    toDOM: (node): DOMOutputSpec => [`h${coerceLevel(node.attrs.level)}`, 0],
  },

  blockquote: {
    content: "block+",
    group: "block",
    defining: true,
    attrs: blockIdAttrs,
    parseDOM: [{ tag: "blockquote" }],
    toDOM: (): DOMOutputSpec => ["blockquote", 0],
  },

  codeBlock: {
    ...opaqueTextBlock({ language: { default: "" } }),
    group: "block",
    parseDOM: [{ tag: "pre", preserveWhitespace: "full" as const }],
    toDOM: (node): DOMOutputSpec => [
      "pre",
      node.attrs.language ? { "data-language": node.attrs.language } : {},
      ["code", 0],
    ],
  },

  bulletList: {
    content: "listItem+",
    group: "block",
    attrs: blockIdAttrs,
    parseDOM: [{ tag: "ul" }],
    toDOM: (): DOMOutputSpec => ["ul", 0],
  },

  orderedList: {
    content: "listItem+",
    group: "block",
    attrs: { start: { default: 1 }, ...blockIdAttrs },
    parseDOM: [{ tag: "ol" }],
    toDOM: (node): DOMOutputSpec => {
      const start = coerceStart(node.attrs.start);
      return start === 1 ? ["ol", 0] : ["ol", { start }, 0];
    },
  },

  listItem: {
    content: "paragraph block*",
    defining: true,
    parseDOM: [{ tag: "li" }],
    toDOM: (): DOMOutputSpec => ["li", 0],
  },

  taskList: {
    content: "taskItem+",
    group: "block",
    attrs: blockIdAttrs,
    parseDOM: [{ tag: 'ul[data-type="taskList"]' }],
    toDOM: (): DOMOutputSpec => ["ul", { "data-type": "taskList" }, 0],
  },

  // The checkbox itself is a custom NodeView (components.tsx), not toDOM —
  // toDOM here is only the non-interactive fallback (SSR-ish / no-JS path).
  taskItem: {
    content: "paragraph block*",
    defining: true,
    attrs: { checked: { default: "false" } },
    parseDOM: [{ tag: 'li[data-type="taskItem"]' }],
    toDOM: (node): DOMOutputSpec => [
      "li",
      {
        "data-type": "taskItem",
        "data-checked": String(coerceChecked(node.attrs.checked)),
      },
      0,
    ],
  },

  table: {
    content: "tableRow+",
    group: "block",
    attrs: blockIdAttrs,
    parseDOM: [{ tag: "table" }],
    toDOM: (): DOMOutputSpec => ["table", ["tbody", 0]],
  },

  // Row-level opaque per the backend codec's design — each row is one raw
  // verbatim markdown line, not decomposed into cells. A known v1
  // limitation (no real per-cell editing), not a silent gap.
  tableRow: {
    ...opaqueTextBlock(rowIdAttrs),
    parseDOM: [{ tag: 'tr[data-raw="1"]' }],
    toDOM: (): DOMOutputSpec => ["tr", { "data-raw": "1" }, ["td", 0]],
  },
  tableSeparator: {
    ...opaqueTextBlock(rowIdAttrs),
    parseDOM: [{ tag: 'tr[data-raw="separator"]' }],
    toDOM: (): DOMOutputSpec => ["tr", { "data-raw": "separator" }, ["td", 0]],
  },

  // Opaque verbatim passthrough — see module docstring. `html_block` is
  // unreachable in practice (the backend's markdown-it config has `html:
  // false`, matching docs/AGENT_WIKI_MARKDOWN_STANDARD.md §5's exclusion of
  // raw HTML) but is declared for schema-safety, matching the codec's own
  // BlockKind enum 1:1.
  // No toDOM/parseDOM here previously — ProseMirror throws immediately
  // ("... does not define toDOM") the moment it needs to render a node of
  // one of these types, which for `thematic_break` means every existing
  // `---` in any page. Rendered as a plain raw-text container (same
  // "editable as raw text, not decomposed" treatment as codeBlock above),
  // distinguished by `data-raw-block` since parseDOM otherwise can't tell
  // the three apart on paste/copy.
  thematic_break: {
    ...opaqueTextBlock({}),
    group: "block",
    parseDOM: [{ tag: 'div[data-raw-block="thematic_break"]' }],
    toDOM: (): DOMOutputSpec => [
      "div",
      { "data-raw-block": "thematic_break" },
      0,
    ],
  },
  html_block: {
    ...opaqueTextBlock({}),
    group: "block",
    parseDOM: [{ tag: 'div[data-raw-block="html_block"]' }],
    toDOM: (): DOMOutputSpec => ["div", { "data-raw-block": "html_block" }, 0],
  },
  other: {
    ...opaqueTextBlock({}),
    group: "block",
    parseDOM: [{ tag: 'div[data-raw-block="other"]' }],
    toDOM: (): DOMOutputSpec => ["div", { "data-raw-block": "other" }, 0],
  },

  hardBreak: {
    group: "inline",
    inline: true,
    selectable: false,
    parseDOM: [{ tag: "br" }],
    toDOM: (): DOMOutputSpec => ["br"],
  },
};

const marks: Record<string, MarkSpec> = {
  bold: {
    parseDOM: [{ tag: "strong" }, { tag: "b" }],
    toDOM: (): DOMOutputSpec => ["strong", 0],
  },
  italic: {
    parseDOM: [{ tag: "em" }, { tag: "i" }],
    toDOM: (): DOMOutputSpec => ["em", 0],
  },
  code: {
    parseDOM: [{ tag: "code" }],
    toDOM: (): DOMOutputSpec => ["code", 0],
  },
  link: {
    attrs: { href: { default: "" } },
    inclusive: false,
    parseDOM: [
      {
        tag: "a[href]",
        getAttrs: (dom) => ({
          href: (dom as HTMLElement).getAttribute("href") ?? "",
        }),
      },
    ],
    toDOM: (mark): DOMOutputSpec => [
      "a",
      { href: mark.attrs.href, rel: "noopener noreferrer" },
      0,
    ],
  },
};

export const agentWikiSchema = new Schema({ nodes, marks });

/**
 * Tiptap node/mark extensions for the onyx-editor live doc — must match
 * `backend/app/wiki/markdown_yjs.py`'s node/mark names and attributes
 * exactly, since that's the codec y-prosemirror reads the live Y.Doc
 * through. Verified directly against the real `y-prosemirror` package (not
 * assumed): a Y.Doc built by the Python backend decodes correctly through
 * `yXmlFragmentToProseMirrorRootNode` with node names `heading`/
 * `paragraph`/`bulletList`/`orderedList`/`listItem`/`blockquote`/
 * `codeBlock` and mark names `bold`/`italic`/`code`/`link` (with `link`'s
 * Yjs-format value being `{href}`, an attrs object — NOT a bare string;
 * that mismatch was a real bug caught by this verification and fixed in
 * markdown_yjs.py's `_inline_runs`).
 *
 * These `.extend()` Tiptap's *stock* node extensions rather than building
 * from `Node.create()` from scratch — confirmed the hard way (browser
 * testing): hand-built nodes have none of the stock ones' input rules
 * (typing "# " -> heading, "- " -> bullet list, "**x**" -> bold, etc.) or
 * keyboard shortcuts, and building the schema without an explicit
 * `topNode`/default block type made every Enter-created line default to
 * whichever custom node happened to be listed first (`heading`, so every
 * line became an H1). Extending the stock nodes keeps all of that
 * behavior; only `addAttributes()` is layered on top.
 *
 * `_blockId`/`_nl` ride on every *top-level* block (positional id;
 * "1" if the source line had a trailing newline) but are absent on nested
 * children (a list item's own paragraph, etc.) — declared as optional
 * string attrs with a default so parsing a nested instance never errors.
 *
 * One confirmed, unavoidable wrinkle: Yjs `XmlElement` attributes are
 * always strings (the underlying XML data model), so `heading.level` and
 * `orderedList.start` sync as *strings* even though Tiptap's stock
 * `Heading`/`OrderedList` normally store them as numbers. `Number(...)` is
 * applied at render/parse time here so the rest of the app (toolbar active
 * states, `toggleHeading({level: 2})`, etc.) still sees real numbers —
 * only the wire attribute itself is a string.
 */

import StockBlockquote from "@tiptap/extension-blockquote";
import StockBold from "@tiptap/extension-bold";
import StockCode from "@tiptap/extension-code";
import StockCodeBlock from "@tiptap/extension-code-block";
import StockHeading from "@tiptap/extension-heading";
import StockItalic from "@tiptap/extension-italic";
import StockLink from "@tiptap/extension-link";
import {
  BulletList as StockBulletList,
  ListItem as StockListItem,
  OrderedList as StockOrderedList,
} from "@tiptap/extension-list";
import StockParagraph from "@tiptap/extension-paragraph";

const blockAttrs = {
  _blockId: {
    default: null as string | null,
    parseHTML: () => null,
    renderHTML: () => ({}),
  },
  _nl: {
    default: "1",
    parseHTML: () => "1",
    renderHTML: () => ({}),
  },
};

export const Heading = StockHeading.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      // Overrides the stock numeric `level` attr — the wire value is
      // always a string (see module docstring), coerced back to a number
      // at the two boundaries that need it.
      level: {
        default: 1,
        parseHTML: (element: HTMLElement) => {
          const raw = element.getAttribute("level") ?? element.tagName.slice(1);
          return Number(raw) || 1;
        },
        renderHTML: (attrs: Record<string, unknown>) => ({
          level: String(attrs.level),
        }),
      },
      ...blockAttrs,
    };
  },
});

export const Paragraph = StockParagraph.extend({
  addAttributes() {
    return { ...this.parent?.(), ...blockAttrs };
  },
});

export const BulletList = StockBulletList.extend({
  addAttributes() {
    return { ...this.parent?.(), ...blockAttrs };
  },
});

export const OrderedList = StockOrderedList.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      start: {
        default: 1,
        parseHTML: (element: HTMLElement) =>
          Number(element.getAttribute("start")) || 1,
        renderHTML: (attrs: Record<string, unknown>) => ({
          start: String(attrs.start),
        }),
      },
      ...blockAttrs,
    };
  },
});

export const ListItem = StockListItem;

export const Blockquote = StockBlockquote.extend({
  addAttributes() {
    return { ...this.parent?.(), ...blockAttrs };
  },
});

export const CodeBlock = StockCodeBlock.extend({
  addAttributes() {
    return { ...this.parent?.(), ...blockAttrs };
  },
});

// Bold/Italic/Code marks carry no attrs — Tiptap's stock marks already use
// these exact names ("bold"/"italic"/"code") and need no customization.
// Link needs no customization either: verified directly that Tiptap's own
// Link mark stores `{href}` as its attrs object, which is exactly the
// shape `markdown_yjs.py`'s `_inline_runs` now encodes. Re-exported here
// so callers get every collaboration-schema extension from one module.
export const Bold = StockBold;
export const Italic = StockItalic;
export const Code = StockCode;
export const Link = StockLink;

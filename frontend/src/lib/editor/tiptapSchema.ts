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
 * always strings (the underlying XML data model), so `heading.level`,
 * `orderedList.start`, and `taskItem.checked` sync as *strings* even though
 * Tiptap's stock `Heading`/`OrderedList`/`TaskItem` normally store them as
 * numbers/booleans. Coercion is applied at render/parse time here so the
 * rest of the app (toolbar active states, `toggleHeading({level: 2})`,
 * etc.) still sees real numbers/booleans — only the wire attribute itself
 * is a string.
 */

import { mergeAttributes } from "@tiptap/core";
import StockBlockquote from "@tiptap/extension-blockquote";
import StockBold from "@tiptap/extension-bold";
import StockCode from "@tiptap/extension-code";
import StockCodeBlock from "@tiptap/extension-code-block";
import StockHeading, { type Level } from "@tiptap/extension-heading";
import StockItalic from "@tiptap/extension-italic";
import StockLink from "@tiptap/extension-link";
import {
  BulletList as StockBulletList,
  ListItem as StockListItem,
  OrderedList as StockOrderedList,
  TaskItem as StockTaskItem,
  TaskList as StockTaskList,
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
      // at the boundaries that need it.
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
  // Stock Heading's own renderHTML does
  // `this.options.levels.includes(node.attrs.level)` — a strict-equality
  // array check that's always false once `level` arrives as a string (any
  // node synced through Yjs, i.e. every heading on a real page — verified
  // the hard way: every heading rendered as h1 regardless of its actual
  // level). Re-declared here with an explicit `Number(...)` coercion
  // before that check; everything else matches the stock implementation.
  renderHTML({ node, HTMLAttributes }) {
    const level = (Number(node.attrs.level) || 1) as Level;
    const validLevel = this.options.levels.includes(level)
      ? level
      : this.options.levels[0];
    return [
      `h${validLevel}`,
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes),
      0,
    ];
  },
  // Same coercion for the two level-gated commands (setNode(), used by
  // SlashMenu, already passes a numeric level and isn't affected, but
  // anything calling toggleHeading/setHeading directly with a
  // string level — e.g. a future toolbar button reading node.attrs.level
  // back out — would hit the same bug addCommands()'s stock
  // `this.options.levels.includes(attributes.level)` has).
  addCommands() {
    const parentCommands = this.parent?.();
    return {
      ...parentCommands,
      setHeading:
        (attributes: { level: number | string }) =>
        ({
          commands,
        }: {
          commands: {
            setNode: (name: string, attrs?: Record<string, unknown>) => boolean;
          };
        }) => {
          const level = (Number(attributes.level) || 1) as Level;
          if (!this.options.levels.includes(level)) return false;
          return commands.setNode(this.name, { ...attributes, level });
        },
      toggleHeading:
        (attributes: { level: number | string }) =>
        ({
          commands,
        }: {
          commands: {
            toggleNode: (
              name: string,
              other: string,
              attrs?: Record<string, unknown>,
            ) => boolean;
          };
        }) => {
          const level = (Number(attributes.level) || 1) as Level;
          if (!this.options.levels.includes(level)) return false;
          return commands.toggleNode(this.name, "paragraph", {
            ...attributes,
            level,
          });
        },
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
  // Same string/number gap as Heading above: stock renderHTML's
  // `start !== 1` check runs against the string our own attr renderHTML
  // just produced, so it's always true and every list gets a redundant
  // (harmless but noisy) `start="1"` HTML attribute. Re-declared with a
  // Number(...) coercion before the check.
  renderHTML({ HTMLAttributes }) {
    const { start, type, ...attributesWithoutType } = HTMLAttributes;
    const attrs: Record<string, unknown> = mergeAttributes(
      this.options.HTMLAttributes,
      attributesWithoutType,
    );
    const startNum = Number(start) || 1;
    if (startNum !== 1) attrs.start = startNum;
    if (type && type !== "1") attrs.type = type;
    return ["ol", attrs, 0];
  },
});

export const ListItem = StockListItem;

export const TaskList = StockTaskList.extend({
  addAttributes() {
    return { ...this.parent?.(), ...blockAttrs };
  },
});

/** Same string/boolean gap as `level`/`start` above, but sharper: a
 * non-empty string like `"false"` is *truthy* in JS, so reading
 * `node.attrs.checked` directly (as stock TaskItem's `renderHTML` and
 * node view both do) would render every unchecked box, synced through
 * Yjs, as checked. Every read site below goes through this instead. */
function isChecked(value: unknown): boolean {
  return value === true || value === "true";
}

export const TaskItem = StockTaskItem.extend({
  addOptions() {
    return { ...this.parent!(), nested: true };
  },
  renderHTML({ node, HTMLAttributes }) {
    return [
      "li",
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        "data-type": this.name,
      }),
      [
        "label",
        [
          "input",
          {
            type: "checkbox",
            checked: isChecked(node.attrs.checked) ? "checked" : null,
          },
        ],
        ["span"],
      ],
      ["div", 0],
    ];
  },
  // Trimmed from stock: the onReadOnlyChecked/a11y-label options (unused
  // here) and its per-update HTML-attribute-diffing (our blockAttrs'
  // renderHTML always returns `{}`, so there's nothing to diff).
  addNodeView() {
    return ({ node, HTMLAttributes, getPos, editor }) => {
      const listItem = document.createElement("li");
      const checkboxWrapper = document.createElement("label");
      const checkboxStyler = document.createElement("span");
      const checkbox = document.createElement("input");
      const content = document.createElement("div");

      const label = (n: typeof node) =>
        `Task item checkbox for ${n.textContent || "empty task item"}`;

      checkboxWrapper.contentEditable = "false";
      checkbox.type = "checkbox";
      checkbox.ariaLabel = label(node);
      checkbox.addEventListener("mousedown", (event) => event.preventDefault());
      checkbox.addEventListener("change", (event) => {
        const checked = (event.target as HTMLInputElement).checked;
        if (editor.isEditable && typeof getPos === "function") {
          editor
            .chain()
            .focus(undefined, { scrollIntoView: false })
            .command(({ tr }) => {
              const position = getPos();
              if (typeof position !== "number") return false;
              const currentNode = tr.doc.nodeAt(position);
              tr.setNodeMarkup(position, undefined, {
                ...currentNode?.attrs,
                checked,
              });
              return true;
            })
            .run();
        }
      });

      Object.entries(this.options.HTMLAttributes).forEach(([key, value]) => {
        listItem.setAttribute(key, String(value));
      });

      listItem.dataset.checked = String(isChecked(node.attrs.checked));
      checkbox.checked = isChecked(node.attrs.checked);

      checkboxWrapper.append(checkbox, checkboxStyler);
      listItem.append(checkboxWrapper, content);

      Object.entries(HTMLAttributes).forEach(([key, value]) => {
        listItem.setAttribute(key, String(value));
      });

      return {
        dom: listItem,
        contentDOM: content,
        update: (updatedNode) => {
          if (updatedNode.type !== this.type) return false;
          listItem.dataset.checked = String(
            isChecked(updatedNode.attrs.checked),
          );
          checkbox.checked = isChecked(updatedNode.attrs.checked);
          checkbox.ariaLabel = label(updatedNode);
          return true;
        },
      };
    };
  },
});

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

import { Extension } from "@tiptap/core";

/** List node types a plain line can indent into. */
const LIST_TYPES = new Set(["bulletList", "orderedList", "taskList"]);

const LIST_TOGGLE = {
  bulletList: "toggleBulletList",
  orderedList: "toggleOrderedList",
  taskList: "toggleTaskList",
} as const;

/** Tab behavior for plain lines.
 *
 * Inside lists and code blocks, Tab already means something (sink the item /
 * literal input) and this extension stays out of the way. On a top-level
 * plain line, browsers' default Tab moves focus out of the editor entirely —
 * never useful mid-writing. Instead:
 *
 * - A line sitting directly below a list indents *into* it: the line becomes
 *   an item of the same list kind, and JoinAdjacentLists merges the two
 *   lists into one (the merged node keeps the first list's identity, so no
 *   duplicated block id is ever created — see its docstring). Further Tabs
 *   nest it deeper through the list's own Tab handling. This is the only
 *   indent markdown can express for a plain line — block-nesting outside a
 *   list has no markdown form.
 * - Anywhere else, Tab (and Shift-Tab) is swallowed so focus stays in the
 *   editor.
 */
export const TabIndent = Extension.create({
  name: "tabIndent",
  addKeyboardShortcuts() {
    return {
      Tab: () => {
        const { editor } = this;
        if (
          editor.isActive("bulletList") ||
          editor.isActive("orderedList") ||
          editor.isActive("taskList") ||
          editor.isActive("codeBlock")
        ) {
          return false; // the list/code handling owns Tab here
        }
        const { $from } = editor.state.selection;
        if ($from.depth === 1 && editor.isActive("paragraph")) {
          // Walk back over blank-line blocks (every blank line is its own
          // empty paragraph in this editor) to the nearest real block — a
          // blank between a list and the line being indented still reads
          // as one loose list in markdown, so it shouldn't break the join.
          let index = $from.index(0) - 1;
          while (index >= 0) {
            const prev = editor.state.doc.child(index);
            if (prev.type.name === "paragraph" && !prev.textContent.trim()) {
              index -= 1;
              continue;
            }
            const kind = prev.type.name;
            if (LIST_TYPES.has(kind)) {
              return editor
                .chain()
                [LIST_TOGGLE[kind as keyof typeof LIST_TOGGLE]]()
                .run();
            }
            break;
          }
        }
        return true; // swallow: never let Tab yank focus out of the editor
      },
      "Shift-Tab": () => {
        const { editor } = this;
        if (
          editor.isActive("bulletList") ||
          editor.isActive("orderedList") ||
          editor.isActive("taskList") ||
          editor.isActive("codeBlock")
        ) {
          return false;
        }
        return true;
      },
    };
  },
});

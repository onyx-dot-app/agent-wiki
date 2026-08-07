/** Guards that reordering a row actually reorders it. The gesture and the
 * command are separable, and an indicator that tracks while nothing moves
 * points at the command. */

import { describe, expect, it } from "vitest";
import { Editor } from "@tiptap/core";
import { Document } from "@tiptap/extension-document";
import { Paragraph } from "@tiptap/extension-paragraph";
import { Text } from "@tiptap/extension-text";
import {
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@tiptap/extension-table";
import { moveTableRow } from "@tiptap/pm/tables";

function tableEditor() {
  const editor = new Editor({
    extensions: [
      Document,
      Paragraph,
      Text,
      Table.configure({ resizable: false }),
      TableRow,
      TableHeader.extend({ content: "inline*" }),
      TableCell.extend({ content: "inline*" }),
    ],
    content: {
      type: "doc",
      content: [
        {
          type: "table",
          content: ["H", "a", "b"].map((label, row) => ({
            type: "tableRow",
            content: [
              {
                type: row === 0 ? "tableHeader" : "tableCell",
                content: [{ type: "text", text: label }],
              },
            ],
          })),
        },
      ],
    },
  });
  return editor;
}

function firstColumn(editor: Editor): string[] {
  const labels: string[] = [];
  editor.state.doc.descendants((node) => {
    if (node.type.name === "tableRow") labels.push(node.textContent);
    return true;
  });
  return labels;
}

describe("moveTableRow", () => {
  it("moves a row to a later index", () => {
    const editor = tableEditor();
    expect(firstColumn(editor)).toEqual(["H", "a", "b"]);

    const moved = moveTableRow({ from: 1, to: 2 })(
      editor.state,
      editor.view.dispatch,
    );

    expect(moved).toBe(true);
    expect(firstColumn(editor)).toEqual(["H", "b", "a"]);
    editor.destroy();
  });
});

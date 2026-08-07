/** Guards the table commands that mutate structure. Each writes several cells
 * from one `TableMap` snapshot, so every one of them has to map its positions
 * through the transaction or the second cell onward lands in the wrong place. */

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
import { TableMap, moveTableRow } from "@tiptap/pm/tables";

import {
  duplicateTrack,
  type HoveredCell,
} from "@/lib/editor/table/tableCommands";

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

function wideEditor() {
  return new Editor({
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
          content: [
            ["H1", "H2"],
            ["a1", "a2"],
            ["b1", "b2"],
          ].map((cells, row) => ({
            type: "tableRow",
            content: cells.map((label) => ({
              type: row === 0 ? "tableHeader" : "tableCell",
              content: [{ type: "text", text: label }],
            })),
          })),
        },
      ],
    },
  });
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

/** Grid contents, row by row, for asserting a whole table at once. */
function grid(editor: Editor): string[][] {
  const rows: string[][] = [];
  editor.state.doc.descendants((node) => {
    if (node.type.name !== "tableRow") return true;
    const cells: string[] = [];
    node.forEach((cell) => cells.push(cell.textContent));
    rows.push(cells);
    return false;
  });
  return rows;
}

/** The hovered-cell shape the grips pass in, built without a DOM. */
function cellAt(editor: Editor, row: number, col: number): HoveredCell {
  let context: HoveredCell | null = null;
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name !== "table" || context) return true;
    const map = TableMap.get(node);
    context = {
      cellPos: pos + 1 + map.map[row * map.width + col]!,
      rowIndex: row,
      colIndex: col,
    };
    return false;
  });
  if (!context) throw new Error("no table");
  return context;
}

describe("duplicateTrack", () => {
  it("copies every cell of a row, not just the first", () => {
    const editor = wideEditor();
    duplicateTrack(editor, cellAt(editor, 1, 0), "row");
    expect(grid(editor)).toEqual([
      ["H1", "H2"],
      ["a1", "a2"],
      ["a1", "a2"],
      ["b1", "b2"],
    ]);
    editor.destroy();
  });

  it("copies every cell of a column, not just the first", () => {
    const editor = wideEditor();
    duplicateTrack(editor, cellAt(editor, 1, 0), "column");
    expect(grid(editor)).toEqual([
      ["H1", "H1", "H2"],
      ["a1", "a1", "a2"],
      ["b1", "b1", "b2"],
    ]);
    editor.destroy();
  });
});

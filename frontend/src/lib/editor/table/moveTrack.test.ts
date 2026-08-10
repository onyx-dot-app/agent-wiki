/** Guards the table commands that mutate structure. Each writes several cells
 * from one `TableMap` snapshot, so every one of them has to map its positions
 * through the transaction or the second cell onward lands in the wrong place. */

import { describe, expect, it } from "vitest";
import { Editor } from "@tiptap/core";
import { Document } from "@tiptap/extension-document";
import { Paragraph } from "@tiptap/extension-paragraph";
import { Text } from "@tiptap/extension-text";
import { Table, TableRow } from "@tiptap/extension-table";
import { CellSelection, TableMap, moveTableRow } from "@tiptap/pm/tables";

import { GfmCell, GfmHeader } from "@/lib/editor/extensions/blocks";

import {
  cellSelectionRange,
  duplicateTrack,
  moveTrack,
  setColumnAlign,
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
      GfmHeader,
      GfmCell,
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
      GfmHeader,
      GfmCell,
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

/** Each header cell's `align`, left to right. */
function headerAligns(editor: Editor): (string | null)[] {
  const aligns: (string | null)[] = [];
  editor.state.doc.descendants((node) => {
    if (node.type.name !== "tableHeader") return true;
    aligns.push(node.attrs.align);
    return false;
  });
  return aligns;
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

  it("fills the duplicate of the last column, whose cells the insert moved", () => {
    const editor = wideEditor();
    duplicateTrack(editor, cellAt(editor, 1, 1), "column");
    expect(grid(editor)).toEqual([
      ["H1", "H2", "H2"],
      ["a1", "a2", "a2"],
      ["b1", "b2", "b2"],
    ]);
    editor.destroy();
  });

  it("carries the source column's alignment onto the duplicate", () => {
    const editor = wideEditor();
    const source = cellAt(editor, 1, 1);
    editor.commands.setTextSelection(source.cellPos + 1);
    setColumnAlign(editor.state, editor.view.dispatch, "center");

    duplicateTrack(editor, cellAt(editor, 1, 1), "column");

    // The delimiter is regenerated from the header cells, so a duplicate that
    // arrives unaligned flattens that column to `---` at the next checkpoint.
    expect(headerAligns(editor)).toEqual([null, "center", "center"]);
    editor.destroy();
  });

  it("carries alignment onto a duplicated row's cells", () => {
    const editor = wideEditor();
    const source = cellAt(editor, 1, 1);
    editor.commands.setTextSelection(source.cellPos + 1);
    setColumnAlign(editor.state, editor.view.dispatch, "right");

    duplicateTrack(editor, cellAt(editor, 1, 0), "row");

    const aligns: (string | null)[] = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name !== "tableCell") return true;
      aligns.push(node.attrs.align);
      return false;
    });
    // Column 1 of every body row, the duplicate included, or the new row
    // renders unaligned against the column it sits in.
    expect(aligns.filter((a) => a === "right")).toHaveLength(3);
    editor.destroy();
  });
});

describe("cellSelectionRange", () => {
  it("spans every selected cell, not the anchor and head positions", () => {
    const editor = wideEditor();
    const anchor = cellAt(editor, 1, 0);
    const head = cellAt(editor, 2, 1);
    const { state } = editor;
    editor.view.dispatch(
      state.tr.setSelection(
        CellSelection.create(state.doc, anchor.cellPos, head.cellPos),
      ),
    );

    const range = cellSelectionRange(editor.state)!;
    const quoted = editor.state.doc.textBetween(range.from, range.to, " ");

    // Every cell of the 2x2 rectangle, which is what the user marked. The
    // selection's own from/to would quote a run starting inside `a1`.
    for (const cell of ["a1", "a2", "b1", "b2"]) {
      expect(quoted).toContain(cell);
    }
    editor.destroy();
  });

  it("is null outside a cell selection, so ordinary text keeps its own range", () => {
    const editor = wideEditor();
    expect(cellSelectionRange(editor.state)).toBeNull();
    editor.destroy();
  });
});

describe("the header row is fixed at index 0", () => {
  it("refuses to move the header out of position", () => {
    const editor = wideEditor();
    const moved = moveTrack(editor, cellAt(editor, 0, 0), 0, 2, "row");
    expect(moved).toBe(false);
    expect(grid(editor)[0]).toEqual(["H1", "H2"]);
    editor.destroy();
  });

  it("refuses to drop a body row onto the header", () => {
    const editor = wideEditor();
    const moved = moveTrack(editor, cellAt(editor, 2, 0), 2, 0, "row");
    expect(moved).toBe(false);
    expect(grid(editor)[0]).toEqual(["H1", "H2"]);
    editor.destroy();
  });

  it("still reorders body rows", () => {
    const editor = wideEditor();
    expect(moveTrack(editor, cellAt(editor, 1, 0), 1, 2, "row")).toBe(true);
    expect(grid(editor)).toEqual([
      ["H1", "H2"],
      ["b1", "b2"],
      ["a1", "a2"],
    ]);
    editor.destroy();
  });
});

describe("the shipped cell schema", () => {
  // `align` is inherited, not declared here, and the checkpoint regenerates
  // the delimiter from it. Losing it flattens every aligned column, silently.
  it.each(["tableCell", "tableHeader"])("declares align on %s", (name) => {
    const editor = wideEditor();
    expect(editor.schema.nodes[name]!.spec.attrs).toHaveProperty("align");
    editor.destroy();
  });

  it("keeps cells inline-only, since GFM cells cannot hold blocks", () => {
    const editor = wideEditor();
    for (const name of ["tableCell", "tableHeader"]) {
      expect(editor.schema.nodes[name]!.spec.content).toBe("inline*");
    }
    editor.destroy();
  });
});

describe("setColumnAlign", () => {
  it("writes align onto every cell of the column, header included", () => {
    const editor = wideEditor();
    const cell = cellAt(editor, 1, 1);
    editor.commands.setTextSelection(cell.cellPos + 1);
    setColumnAlign(editor.state, editor.view.dispatch, "center");

    const aligns: (string | null)[] = [];
    editor.state.doc.descendants((node) => {
      const name = node.type.name;
      if (name === "tableCell" || name === "tableHeader") {
        aligns.push(node.attrs.align);
        return false;
      }
      return true;
    });
    // Column 1 of each of the three rows. The header's value is the one the
    // backend reads to regenerate the delimiter.
    expect(aligns.filter((a) => a === "center")).toHaveLength(3);
    editor.destroy();
  });
});

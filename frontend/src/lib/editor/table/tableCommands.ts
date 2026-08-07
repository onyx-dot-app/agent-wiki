/** Column-scoped table commands prosemirror-tables does not ship. GFM stores
 * alignment per column and the backend reads it off the header cell, so
 * alignment is set on every cell of a column rather than on a selection. */

import {
  CellSelection,
  TableMap,
  cellAround,
  moveTableColumn,
  moveTableRow,
} from "@tiptap/pm/tables";
import type { EditorState, Transaction } from "@tiptap/pm/state";
import type { Node as PMNode, ResolvedPos } from "@tiptap/pm/model";
import type { Editor } from "@tiptap/core";

export type ColumnAlign = "left" | "center" | "right" | null;

interface TableContext {
  table: PMNode;
  /** Position of the table node itself. */
  pos: number;
  /** Position of the table's first child, which `TableMap` offsets are relative to. */
  start: number;
  map: TableMap;
  colIndex: number;
  rowIndex: number;
}

/** Context for a resolved before-cell position. Indices come from `TableMap`,
 * so a merged cell reports the column it starts in rather than a DOM position
 * that would be off by its span. */
function contextFromCell($cell: ResolvedPos): TableContext | null {
  const table = $cell.node(-1);
  if (table?.type.name !== "table") return null;
  const start = $cell.start(-1);
  const map = TableMap.get(table);
  const rect = map.findCell($cell.pos - start);
  return {
    table,
    pos: $cell.before(-1),
    start,
    map,
    colIndex: rect.left,
    rowIndex: rect.top,
  };
}

/** The table and cell the selection sits in, or null when it is outside one. */
export function tableContext(state: EditorState): TableContext | null {
  const $cell = cellAround(state.selection.$from);
  return $cell ? contextFromCell($cell) : null;
}

export function setColumnAlign(
  state: EditorState,
  dispatch: ((tr: Transaction) => void) | undefined,
  align: ColumnAlign,
): boolean {
  const ctx = tableContext(state);
  if (!ctx) return false;
  if (!dispatch) return true;
  const tr = state.tr;
  for (const offset of trackCells(ctx, ctx.colIndex, "column")) {
    const cellPos = ctx.start + offset;
    const cell = tr.doc.nodeAt(cellPos);
    if (cell) tr.setNodeMarkup(cellPos, undefined, { ...cell.attrs, align });
  }
  dispatch(tr);
  return true;
}

/** The alignment already on the column holding the selection, for a control
 * that needs to show which option is active. */
export function currentColumnAlign(state: EditorState): ColumnAlign {
  const ctx = tableContext(state);
  if (!ctx) return null;
  const first = ctx.map.map[ctx.colIndex]!;
  const cell = state.doc.nodeAt(ctx.start + first);
  return (cell?.attrs.align as ColumnAlign) ?? null;
}

export interface HoveredCell {
  /** Document position of the cell, for `nodeDOM` and for placing the caret. */
  cellPos: number;
  rowIndex: number;
  colIndex: number;
}

/** The cell under the pointer, via `TableMap` rather than DOM child indices so
 * a merged cell reports the column it starts in. Probes the cell's centre: on a
 * border the pointer is ambiguous between two cells, the centre never is. */
export function hoveredCell(
  editor: Editor,
  clientX: number,
  clientY: number,
): HoveredCell | null {
  const target = document.elementFromPoint(clientX, clientY);
  const domCell = target instanceof Element ? target.closest("td, th") : null;
  if (!domCell) return null;
  const box = domCell.getBoundingClientRect();
  const at = editor.view.posAtCoords({
    left: box.left + box.width / 2,
    top: box.top + box.height / 2,
  });
  if (!at) return null;
  const $cell = cellAround(editor.state.doc.resolve(at.pos));
  if (!$cell) return null;
  const table = $cell.node(-1);
  const start = $cell.start(-1);
  const rect = TableMap.get(table).findCell($cell.pos - start);
  return { cellPos: $cell.pos, rowIndex: rect.top, colIndex: rect.left };
}

/** Viewport rects for a hovered cell and its table, read from live DOM so a
 * node view replaced by a peer's edit cannot leave a stale anchor behind. */
export function cellRectAt(
  editor: Editor,
  cell: HoveredCell,
): { cellRect: DOMRect; tableRect: DOMRect } | null {
  const dom = editor.view.nodeDOM(cell.cellPos);
  if (!(dom instanceof HTMLElement)) return null;
  const table = dom.closest("table");
  if (!table) return null;
  return {
    cellRect: dom.getBoundingClientRect(),
    tableRect: table.getBoundingClientRect(),
  };
}

/** Positions of every cell in a row or column, deduplicated because a spanning
 * cell appears once per track it covers. */
function trackCells(
  ctx: TableContext,
  index: number,
  axis: "row" | "column",
): number[] {
  const seen = new Set<number>();
  const { map, width, height } = ctx.map;
  if (axis === "row") {
    for (let col = 0; col < width; col++) seen.add(map[index * width + col]!);
  } else {
    for (let row = 0; row < height; row++) seen.add(map[row * width + index]!);
  }
  return [...seen];
}

/** Select a whole row or column, so ordinary formatting applies to all of it.
 * This is what clicking a grip does in Notion. */
export function selectTrack(
  editor: Editor,
  cell: HoveredCell,
  axis: "row" | "column",
): void {
  const ctx = tableContextAt(editor, cell);
  if (!ctx) return;
  const { state } = editor.view;
  const $anchor = state.doc.resolve(cell.cellPos);
  const selection =
    axis === "row"
      ? CellSelection.rowSelection($anchor)
      : CellSelection.colSelection($anchor);
  editor.view.dispatch(state.tr.setSelection(selection));
}

/** Copy a row or column into a new one after it. */
export function duplicateTrack(
  editor: Editor,
  cell: HoveredCell,
  axis: "row" | "column",
): void {
  const ctx = tableContextAt(editor, cell);
  if (!ctx) return;
  const index = axis === "row" ? cell.rowIndex : cell.colIndex;
  const source = trackCells(ctx, index, axis).map((offset) =>
    editor.state.doc.nodeAt(ctx.start + offset),
  );
  const chain = editor
    .chain()
    .focus()
    .setTextSelection(cell.cellPos + 1);
  (axis === "row" ? chain.addRowAfter() : chain.addColumnAfter()).run();

  // The fresh track is empty, so fill it from the source's content. Read the
  // map again: inserting shifted every position after the insertion point.
  const after = tableContextAt(editor, { ...cell, cellPos: cell.cellPos });
  if (!after) return;
  const targets = trackCells(after, index + 1, axis);
  const tr = editor.state.tr;
  targets.forEach((offset, i) => {
    const node = source[i];
    if (!node) return;
    // Mapped: each write resizes its cell, shifting every later position. The
    // offsets come from one map snapshot, so without this the second cell
    // onward writes into the wrong place.
    const pos = tr.mapping.map(after.start + offset);
    const target = tr.doc.nodeAt(pos);
    if (target)
      tr.replaceWith(pos + 1, pos + target.nodeSize - 1, node.content);
  });
  editor.view.dispatch(tr);
}

/** Empty a row or column without removing it. */
export function clearTrack(
  editor: Editor,
  cell: HoveredCell,
  axis: "row" | "column",
): void {
  const ctx = tableContextAt(editor, cell);
  if (!ctx) return;
  const index = axis === "row" ? cell.rowIndex : cell.colIndex;
  const tr = editor.state.tr;
  for (const offset of trackCells(ctx, index, axis)) {
    // Mapped for the same reason as `duplicateTrack`: offsets come from one
    // map snapshot and each delete shifts every position after it.
    const pos = tr.mapping.map(ctx.start + offset);
    const node = tr.doc.nodeAt(pos);
    if (node && node.content.size > 0)
      tr.delete(pos + 1, pos + node.nodeSize - 1);
  }
  editor.view.dispatch(tr);
}

/** Reorder a row or column. The upstream commands rebuild the whole table, so
 * they are the one place index arithmetic is not ours to get wrong. */
export function moveTrack(
  editor: Editor,
  cell: HoveredCell,
  from: number,
  to: number,
  axis: "row" | "column",
): boolean {
  if (from === to) return false;
  // The upstream commands resolve the table from the current selection, so a
  // grip drag has to seat the caret in the dragged track first. Without this
  // they find no table and refuse.
  editor.commands.setTextSelection(cell.cellPos + 1);
  const command = axis === "row" ? moveTableRow : moveTableColumn;
  return command({ from, to, select: true })(
    editor.state,
    editor.view.dispatch,
  );
}

/** Table context resolved from a known cell rather than from the selection. */
export function tableContextAt(
  editor: Editor,
  cell: HoveredCell,
): TableContext | null {
  const $cell = editor.state.doc.resolve(cell.cellPos);
  const table = $cell.node(-1);
  if (!table || table.type.name !== "table") return null;
  const pos = $cell.before(-1);
  return {
    table,
    pos,
    start: $cell.start(-1),
    map: TableMap.get(table),
    colIndex: cell.colIndex,
    rowIndex: cell.rowIndex,
  };
}

/** Every track boundary along an axis, for hit-testing a drag. */
export function trackRects(
  editor: Editor,
  cell: HoveredCell,
  axis: "row" | "column",
): DOMRect[] {
  const ctx = tableContextAt(editor, cell);
  if (!ctx) return [];
  const count = axis === "row" ? ctx.map.height : ctx.map.width;
  const rects: DOMRect[] = [];
  for (let i = 0; i < count; i++) {
    const offset =
      axis === "row" ? ctx.map.map[i * ctx.map.width]! : ctx.map.map[i]!;
    const dom = editor.view.nodeDOM(ctx.start + offset);
    if (dom instanceof HTMLElement) rects.push(dom.getBoundingClientRect());
  }
  return rects;
}

/** Empty every cell of a cell selection, keeping the grid intact. Bound ahead
 * of the extension's own handler, which deletes the whole table once every cell
 * is selected. */
export function clearSelectedCells(
  state: EditorState,
  dispatch: ((tr: Transaction) => void) | undefined,
): boolean {
  const { selection } = state;
  if (!(selection instanceof CellSelection)) return false;
  const tr = state.tr;
  selection.forEachCell((cell, pos) => {
    if (cell.content.size === 0) return;
    // Mapped: each delete shifts every position after it.
    tr.delete(tr.mapping.map(pos + 1), tr.mapping.map(pos + cell.nodeSize - 1));
  });
  if (!tr.docChanged) return false;
  dispatch?.(tr);
  return true;
}

/** The range covering every cell of a cell selection. Its own `from`/`to` are
 * anchor and head cell positions, so anything taking them quotes a run that
 * starts and ends mid-cell. Null outside a cell selection. */
export function cellSelectionRange(
  state: EditorState,
): { from: number; to: number } | null {
  const { selection } = state;
  if (!(selection instanceof CellSelection)) return null;
  let from = Infinity;
  let to = -Infinity;
  selection.forEachCell((cell, pos) => {
    from = Math.min(from, pos + 1);
    to = Math.max(to, pos + cell.nodeSize - 1);
  });
  return Number.isFinite(from) && to > from ? { from, to } : null;
}

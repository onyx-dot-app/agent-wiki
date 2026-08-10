/** Grips on the hovered row and column, a `+` on each far edge, drag to
 * reorder, and a menu per grip. Rects are read fresh from a ProseMirror
 * position every time: a peer's edit replaces the node view's DOM. */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Editor } from "@tiptap/core";
import { LineItemButton, Popover } from "@onyx-ai/opal/components";
import { SvgTrash } from "@onyx-ai/opal/icons";

import {
  cellRectAt,
  isHeaderRow,
  clearTrack,
  duplicateTrack,
  hoveredCell,
  moveTrack,
  selectTrack,
  setColumnAlign,
  trackRects,
  type ColumnAlign,
  type HoveredCell,
} from "@/lib/editor/table/tableCommands";

type Axis = "row" | "column";

interface Geometry {
  cell: HoveredCell;
  table: DOMRect;
  cellRect: DOMRect;
}

interface Drag {
  axis: Axis;
  cell: HoveredCell;
  from: number;
  to: number;
  rects: DOMRect[];
}

const GRIP = 10;
const GAP = 4;
/** Reach beyond the table that still counts as hovering it. Must clear the
 * grips and the `+` buttons, which sit `GRIP + GAP` out. */
const SAFE = 44;

const ALIGNMENTS: { label: string; value: ColumnAlign }[] = [
  { label: "Align left", value: "left" },
  { label: "Align center", value: "center" },
  { label: "Align right", value: "right" },
];

/** Notion's grip is a bar of six dots. */
function Dots({ axis }: { axis: Axis }) {
  return (
    <span
      className={`wiki-grip-dots wiki-grip-dots-${axis}`}
      aria-hidden="true"
    >
      {Array.from({ length: 6 }, (_, i) => (
        <i key={i} />
      ))}
    </span>
  );
}

export function TableGrips({ editor }: { editor: Editor | null }) {
  const [geo, setGeo] = useState<Geometry | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const menuOpen = useRef(false);
  const geoRef = useRef<Geometry | null>(null);
  geoRef.current = geo;
  const pointer = useRef<{ x: number; y: number } | null>(null);
  const dragRef = useRef<Drag | null>(null);
  dragRef.current = drag;

  /** Alive anywhere in a padded box around the table, not only over a cell.
   * The affordances render outside it, so clearing on leave would unmount them
   * as the pointer travels to one and they could never be clicked. */
  const update = useCallback(
    (event: PointerEvent | MouseEvent) => {
      if (!editor?.isEditable || menuOpen.current || dragRef.current) return;
      pointer.current = { x: event.clientX, y: event.clientY };
      const cell = hoveredCell(editor, event.clientX, event.clientY);
      if (cell) {
        const rects = cellRectAt(editor, cell);
        if (rects)
          setGeo({ cell, table: rects.tableRect, cellRect: rects.cellRect });
        return;
      }
      const current = geoRef.current;
      if (!current) return;
      const box = current.table;
      const inside =
        event.clientX >= box.left - SAFE &&
        event.clientX <= box.right + SAFE &&
        event.clientY >= box.top - SAFE &&
        event.clientY <= box.bottom + SAFE;
      if (!inside) setGeo(null);
    },
    [editor],
  );

  useEffect(() => {
    if (!editor) return;
    // On the document, not the editor element: the affordances live outside it.
    document.addEventListener("pointermove", update);
    document.addEventListener("mousemove", update);
    return () => {
      document.removeEventListener("pointermove", update);
      document.removeEventListener("mousemove", update);
    };
  }, [editor, update]);

  /** Placed from rects measured while hovering and rendered fixed, so a scroll
   * strands them over whatever slid underneath. The pointer goes too, so an
   * edit cannot re-derive them from a spot the reader has scrolled away from. */
  useEffect(() => {
    if (!editor) return;
    const clear = (event: Event) => {
      // Same guard as the pointer and resync paths. Unmounting the popover
      // never reports itself closed, so tearing one down here would strand
      // `menuOpen` and the grips would stop appearing entirely.
      if (menuOpen.current || dragRef.current) return;
      // Only a scroll that carries the editor strands them. The chat panel
      // scrolls itself on every streamed turn.
      const target = event.target;
      if (!(target instanceof Node) || !target.contains(editor.view.dom))
        return;
      pointer.current = null;
      setGeo(null);
    };
    // Capture: scroll does not bubble, and it is a descendant that moves.
    document.addEventListener("scroll", clear, true);
    return () => {
      document.removeEventListener("scroll", clear, true);
    };
  }, [editor]);

  /** Re-derived after every document change. Adding a row or column moves the
   * table, and a pointer that has not moved since would otherwise leave the
   * affordances sitting over the old layout. */
  useEffect(() => {
    if (!editor) return;
    const resync = () => {
      const at = pointer.current;
      if (!at || menuOpen.current || dragRef.current) return;
      const cell = hoveredCell(editor, at.x, at.y);
      if (!cell) return;
      const rects = cellRectAt(editor, cell);
      if (rects)
        setGeo({ cell, table: rects.tableRect, cellRect: rects.cellRect });
    };
    editor.on("update", resync);
    return () => {
      editor.off("update", resync);
    };
  }, [editor]);

  /** Listeners are bound once per gesture and read the live drag from a ref.
   * Re-subscribing on every hovered-index change would tear down the `pointerup`
   * between two moves, and the drop would never land. */
  useEffect(() => {
    if (!drag || !editor) return;
    const axis = drag.axis;
    const rects = drag.rects;
    const from = drag.from;
    const cell = drag.cell;
    const move = (e: PointerEvent) => {
      const pos = axis === "row" ? e.clientY : e.clientX;
      const found = rects.findIndex((r, i) => {
        const start = axis === "row" ? r.top : r.left;
        const end = axis === "row" ? r.bottom : r.right;
        if (pos >= start && pos <= end) return true;
        if (i === 0 && pos < start) return true;
        return i === rects.length - 1 && pos > end;
      });
      if (found === -1) return;
      if (axis === "row" && isHeaderRow(found)) return;
      const current = dragRef.current;
      if (!current || current.to === found) return;
      // Written straight to the ref as well as to state: `up` reads the ref,
      // and a drop landing before React flushes would otherwise see the index
      // the drag started on and move nothing.
      const next = { ...current, to: found };
      dragRef.current = next;
      setDrag(next);
    };
    const up = () => {
      const to = dragRef.current?.to ?? from;
      moveTrack(editor, cell, from, to, axis);
      setDrag(null);
      editor.view.focus();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    // Bound to the gesture, not to `to`: the ref carries the moving part.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag?.axis, drag?.from, drag?.cell, editor]);

  if (!editor || !geo) return null;

  const trackIndex = (axis: Axis) =>
    axis === "row" ? geo.cell.rowIndex : geo.cell.colIndex;

  const startDrag = (axis: Axis) => (e: React.PointerEvent) => {
    if (axis === "row" && isHeaderRow(geo.cell.rowIndex)) return;
    e.preventDefault();
    const rects = trackRects(editor, geo.cell, axis);
    if (rects.length) {
      setDrag({
        axis,
        cell: geo.cell,
        from: trackIndex(axis),
        to: trackIndex(axis),
        rects,
      });
    }
  };

  const act = (fn: () => void) => () => {
    fn();
    editor.view.focus();
  };

  /** Commands read the selection, so put the caret in the grip's own cell
   * before running one. */
  const onTrack = (fn: () => void) =>
    act(() => {
      editor.commands.setTextSelection(geo.cell.cellPos + 1);
      fn();
    });

  const grip = (axis: Axis) => {
    const style: React.CSSProperties =
      axis === "column"
        ? {
            left: geo.cellRect.left,
            top: geo.table.top - GRIP - GAP,
            width: geo.cellRect.width,
            height: GRIP,
          }
        : {
            left: geo.table.left - GRIP - GAP,
            top: geo.cellRect.top,
            width: GRIP,
            height: geo.cellRect.height,
          };
    return (
      <Popover key={axis} onOpenChange={(open) => (menuOpen.current = open)}>
        <Popover.Trigger asChild>
          <div
            className="wiki-table-grip"
            style={{ position: "fixed", zIndex: 30, ...style }}
            role="button"
            aria-label={axis === "row" ? "Row options" : "Column options"}
            contentEditable={false}
            onPointerDown={startDrag(axis)}
            onClick={() => selectTrack(editor, geo.cell, axis)}
          >
            <Dots axis={axis} />
          </div>
        </Popover.Trigger>
        <Popover.Content>
          <Popover.Menu>
            {[
              ...(axis === "column"
                ? ALIGNMENTS.map((a) => (
                    <LineItemButton
                      key={a.label}
                      title={a.label}
                      sizePreset="main-ui"
                      variant="section"
                      onClick={onTrack(() =>
                        setColumnAlign(
                          editor.state,
                          editor.view.dispatch,
                          a.value,
                        ),
                      )}
                    />
                  ))
                : []),
              ...(axis === "row" && isHeaderRow(geo.cell.rowIndex)
                ? []
                : [
                    <LineItemButton
                      key="before"
                      title={axis === "row" ? "Insert above" : "Insert left"}
                      sizePreset="main-ui"
                      variant="section"
                      onClick={onTrack(() =>
                        axis === "row"
                          ? editor.commands.addRowBefore()
                          : editor.commands.addColumnBefore(),
                      )}
                    />,
                  ]),
              <LineItemButton
                key="after"
                title={axis === "row" ? "Insert below" : "Insert right"}
                sizePreset="main-ui"
                variant="section"
                onClick={onTrack(() =>
                  axis === "row"
                    ? editor.commands.addRowAfter()
                    : editor.commands.addColumnAfter(),
                )}
              />,
              <LineItemButton
                key="duplicate"
                title="Duplicate"
                sizePreset="main-ui"
                variant="section"
                onClick={act(() => duplicateTrack(editor, geo.cell, axis))}
              />,
              <LineItemButton
                key="clear"
                title="Clear contents"
                sizePreset="main-ui"
                variant="section"
                onClick={act(() => clearTrack(editor, geo.cell, axis))}
              />,
              ...(axis === "row" && isHeaderRow(geo.cell.rowIndex)
                ? []
                : [
                    <LineItemButton
                      key="delete"
                      title={axis === "row" ? "Delete row" : "Delete column"}
                      icon={SvgTrash}
                      sizePreset="main-ui"
                      variant="section"
                      onClick={onTrack(() =>
                        axis === "row"
                          ? editor.commands.deleteRow()
                          : editor.commands.deleteColumn(),
                      )}
                    />,
                  ]),
            ]}
          </Popover.Menu>
        </Popover.Content>
      </Popover>
    );
  };

  /** The far-edge `+`: below the table adds a row, beside it adds a column. */
  const plus = (axis: Axis) => (
    <div
      key={`plus-${axis}`}
      className="wiki-table-plus"
      style={
        axis === "row"
          ? {
              position: "fixed",
              zIndex: 30,
              left: geo.table.left,
              top: geo.table.bottom + GAP,
              width: geo.table.width,
              height: GRIP,
            }
          : {
              position: "fixed",
              zIndex: 30,
              left: geo.table.right + GAP,
              top: geo.table.top,
              width: GRIP,
              height: geo.table.height,
            }
      }
      role="button"
      aria-label={axis === "row" ? "Add row" : "Add column"}
      contentEditable={false}
      onClick={onTrack(() =>
        axis === "row"
          ? editor.commands.addRowAfter()
          : editor.commands.addColumnAfter(),
      )}
    >
      <span aria-hidden="true">+</span>
    </div>
  );

  const target = drag ? drag.rects[drag.to] : null;

  return (
    <>
      {grip("column")}
      {grip("row")}
      {plus("row")}
      {plus("column")}
      {drag && target && (
        <div
          className="wiki-table-drop"
          style={
            drag.axis === "row"
              ? {
                  position: "fixed",
                  zIndex: 31,
                  left: geo.table.left,
                  top: drag.to >= drag.from ? target.bottom : target.top,
                  width: geo.table.width,
                  height: 2,
                }
              : {
                  position: "fixed",
                  zIndex: 31,
                  left: drag.to >= drag.from ? target.right : target.left,
                  top: geo.table.top,
                  width: 2,
                  height: geo.table.height,
                }
          }
        />
      )}
    </>
  );
}

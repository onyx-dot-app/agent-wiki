// Table node view that keeps the edge being dragged in view: a column drag
// grows the table rightward while its wrapper stays scrolled left, so the edge
// under the cursor is the first thing to leave the viewport. It also owns the
// `.tableShell` wrapper and the `data-fade-*` flags `editor.css` styles the cut
// edge from.
import { TableView } from "@tiptap/extension-table";
import type { Node as PMNode } from "@tiptap/pm/model";
import type { EditorView } from "@tiptap/pm/view";

export class ScrollFollowTableView extends TableView {
  /** The scrolling box TableView built. `dom` becomes a shell around it, so the
   *  cut edge can carry a border that the scroller's own fade would erase. */
  private scroller: HTMLElement;
  private widthObserver: ResizeObserver;
  private lastWidth = 0;
  /** Mount reads as a grow from zero, and following it would open every
   *  document with its wide tables already scrolled off their first column. */
  private measured = false;
  private firstMeasure: number;

  constructor(
    node: PMNode,
    cellMinWidth: number,
    view?: EditorView,
    HTMLAttributes: Record<string, unknown> = {},
  ) {
    super(node, cellMinWidth, view, HTMLAttributes);
    this.scroller = this.dom;
    const shell = document.createElement("div");
    shell.className = "tableShell";
    shell.appendChild(this.scroller);
    this.dom = shell;

    this.widthObserver = new ResizeObserver(() => this.followRightEdge());
    this.widthObserver.observe(this.table);
    this.scroller.addEventListener("scroll", this.markEdges, { passive: true });
    // The shell has no layout yet, so the first honest measurement is a frame
    // away. Without this the cut stays unmarked until something else resizes.
    this.firstMeasure = requestAnimationFrame(this.markEdges);
  }

  // A drag paints straight to the DOM and only commits widths on mouseup, so
  // the observer carries the live drag and this carries the commit.
  update(node: PMNode) {
    const accepted = super.update(node);
    if (accepted) this.followRightEdge();
    return accepted;
  }

  private followRightEdge() {
    const width = this.table.offsetWidth;
    const grew = this.measured && width > this.lastWidth;
    // Where the end sat before this growth. Only a reader already there is
    // watching the edge that moves. Following from anywhere else drags someone
    // widening a left or middle column away from the handle they are holding.
    const previousEnd = Math.max(0, this.lastWidth - this.scroller.clientWidth);
    const wasAtEnd = this.scroller.scrollLeft >= previousEnd - 1;
    this.lastWidth = width;
    this.measured = true;
    // Growth only. Pinning right while a column narrows would scroll the view
    // away from the column the user is working on.
    if (
      grew &&
      wasAtEnd &&
      this.scroller.scrollWidth > this.scroller.clientWidth
    ) {
      this.scroller.scrollLeft = this.scroller.scrollWidth;
    }
    this.markEdges();
  }

  /** Flags which sides still have table past them, so the cut edge fades out
   *  and takes a border instead of stopping dead. */
  private markEdges = () => {
    const max = this.scroller.scrollWidth - this.scroller.clientWidth;
    // Sub-pixel scroll positions never reach `max` exactly.
    this.dom.dataset.fadeStart = String(this.scroller.scrollLeft > 1);
    this.dom.dataset.fadeEnd = String(this.scroller.scrollLeft < max - 1);
  };

  destroy() {
    cancelAnimationFrame(this.firstMeasure);
    this.widthObserver.disconnect();
    this.scroller.removeEventListener("scroll", this.markEdges);
  }
}

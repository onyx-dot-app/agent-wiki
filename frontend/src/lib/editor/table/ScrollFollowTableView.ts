// Table node view that keeps the edge being dragged in view: a column drag
// grows the table rightward while its wrapper stays scrolled left, so the edge
// under the cursor is the first thing to leave the viewport.
import { TableView } from "@tiptap/extension-table";
import type { Node as PMNode } from "@tiptap/pm/model";
import type { EditorView } from "@tiptap/pm/view";

export class ScrollFollowTableView extends TableView {
  private widthObserver: ResizeObserver;
  private lastWidth: number;
  /** Mount reads as a grow from zero, and following it would open every
   *  document with its wide tables already scrolled off their first column. */
  private measured = false;

  constructor(
    node: PMNode,
    cellMinWidth: number,
    view?: EditorView,
    HTMLAttributes: Record<string, unknown> = {},
  ) {
    super(node, cellMinWidth, view, HTMLAttributes);
    this.lastWidth = this.table.offsetWidth;
    this.widthObserver = new ResizeObserver(() => this.followRightEdge());
    this.widthObserver.observe(this.table);
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
    this.lastWidth = width;
    this.measured = true;
    // Only growth follows. Pinning right while a column narrows would scroll
    // the view away from the column the user is working on.
    if (!grew) return;
    if (this.dom.scrollWidth > this.dom.clientWidth) {
      this.dom.scrollLeft = this.dom.scrollWidth;
    }
  }

  destroy() {
    this.widthObserver.disconnect();
  }
}

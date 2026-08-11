"use client";

/** Synthetic local text caret — a single overlay element we position ourselves,
 * so the caret can fade-blink and glide. The native caret exposes only
 * `caret-color` (no shape/blink/transition control), so we hide it
 * (`.ProseMirror { caret-color: transparent }`, gated to fine pointers in
 * editor.css) and draw this replacement.
 *
 * Local-only by construction: `yCursorPlugin` (presence.ts) renders *peers'*
 * carets and filters out the local client, so nothing else draws this one.
 *
 * A **persistent** DOM element, repositioned each update — NOT a
 * `Decoration.widget`: a widget's DOM is torn down and rebuilt whenever its
 * position changes, which would reset the CSS glide transition every move. The
 * element lives inside the `.editor-prose` scroller and is positioned in that
 * scroller's scroll-origin coordinate space (the `docSpaceRect` recipe in
 * `components.tsx`), so it scrolls natively with the text and the glide
 * transition fires only on real caret moves, never on scroll.
 *
 * Deliberately disabled on touch/coarse-pointer devices: hiding the native
 * caret would break the selection handles, the magnifier loupe, and long-press
 * selection, which all anchor to it. Only IME candidate windows still lean on
 * the native caret, so it's restored during composition (see below).
 *
 * **Rendering only.** This file has no opinion about caret motion. It asks
 * `coordsAtPos` where the cursor is and draws a rectangle there; ProseMirror
 * and the browser decide everything else, including which side of a soft wrap
 * the caret sits on. No key is handled here.
 *
 * Tracking that side ourselves has been tried and reverted twice — see git
 * history for `bias` / `affinityForKey`. Don't. */
import { Extension } from "@tiptap/core";
import { Plugin, TextSelection } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

// Stay solid this long after a move, then resume blinking — so typing/arrowing
// doesn't strobe.
const IDLE_BLINK_MS = 500;
// How long every caret move animates, small or large — the single glide-speed
// knob. Lower = quicker; a large jump covers more ground in this same time, so
// it reads as a faster streak rather than a longer glide (fixed duration, not
// distance-scaled). Reduced-motion no-ops the glide in CSS regardless.
const GLIDE_MS = 60;

interface CaretPos {
  x: number;
  y: number;
  height: number;
}

class CaretView {
  private el: HTMLElement;
  private scroller: HTMLElement | null = null;
  private resize: ResizeObserver | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private composing = false;
  private last: CaretPos | null = null;
  // Set on mousedown to disambiguate the caret's side by the click's x.
  private clickX: number | null = null;
  // Touch/coarse pointer: keep the native caret, render nothing.
  private readonly enabled: boolean;

  constructor(private view: EditorView) {
    this.el = document.createElement("div");
    this.el.className = "editor-caret";
    this.el.setAttribute("aria-hidden", "true");

    this.enabled =
      typeof window !== "undefined" &&
      window.matchMedia("(pointer: fine)").matches;
    if (!this.enabled) return;

    // `view.dom` isn't inside `.editor-prose` yet at construction time — same
    // deferred-lookup reason as the image plugin (images.ts).
    setTimeout(() => {
      this.scroller = this.view.dom.closest<HTMLElement>(".editor-prose");
      if (!this.scroller) return;
      this.scroller.appendChild(this.el);
      // Reflow/rewrap moves the caret without a transaction — snap (clear
      // `last`) rather than gliding.
      this.resize = new ResizeObserver(() => {
        this.last = null;
        this.render();
      });
      this.resize.observe(this.scroller);
      this.render();
    }, 0);

    this.view.dom.addEventListener("compositionstart", this.onComposeStart);
    this.view.dom.addEventListener("compositionend", this.onComposeEnd);
    this.view.dom.addEventListener("mousedown", this.onMouseDown);
    this.view.dom.addEventListener("focus", this.render);
    this.view.dom.addEventListener("blur", this.render);
  }

  update(): void {
    if (this.enabled) this.render();
  }

  private onComposeStart = () => {
    this.composing = true;
    this.render();
  };

  private onComposeEnd = () => {
    this.composing = false;
    this.render();
  };

  private onMouseDown = (event: MouseEvent) => {
    this.clickX = event.clientX;
  };

  /** Doc position → the scroller's scroll-origin space. Takes whatever side
   * `coordsAtPos` picks, except on a click, where the click's own x says which
   * end of an ambiguous position was meant. Subtracts the scroller's border
   * (`clientLeft/Top`) because `getBoundingClientRect` includes the border but
   * absolute positioning is relative to the padding box. */
  private caretPos(clickX: number | null): CaretPos | null {
    if (!this.scroller) return null;
    const head = this.view.state.selection.head;
    let coords: { left: number; top: number; bottom: number };
    try {
      if (clickX != null) {
        const before = this.view.coordsAtPos(head, -1);
        const after = this.view.coordsAtPos(head, 1);
        coords =
          Math.abs(before.left - clickX) <= Math.abs(after.left - clickX)
            ? before
            : after;
      } else {
        coords = this.view.coordsAtPos(head);
      }
    } catch {
      // The head can briefly fall out of range mid-edit, or sit against a
      // custom node view that can't be measured — hide rather than misplace.
      return null;
    }
    const wrap = this.scroller.getBoundingClientRect();
    return {
      x:
        coords.left -
        wrap.left -
        this.scroller.clientLeft +
        this.scroller.scrollLeft,
      y:
        coords.top -
        wrap.top -
        this.scroller.clientTop +
        this.scroller.scrollTop,
      height: coords.bottom - coords.top,
    };
  }

  private render = () => {
    // During IME composition the native caret must lead (candidate window
    // anchors to it) — show it, hide ours.
    this.view.dom.style.caretColor = this.composing ? "auto" : "";

    const clickX = this.clickX;
    this.clickX = null;

    const { selection } = this.view.state;
    // Only a plain collapsed text cursor gets a caret. A range selection shows
    // the native band; NodeSelection/GapCursor have their own rendering and
    // `coordsAtPos(head)` isn't meaningful for them.
    const active =
      this.view.hasFocus() &&
      this.view.editable &&
      !this.composing &&
      !!this.scroller &&
      selection instanceof TextSelection &&
      selection.empty;

    const pos = active ? this.caretPos(clickX) : null;
    if (!pos) {
      this.el.style.display = "none";
      // Re-appearance should snap, not glide from a stale position.
      this.last = null;
      return;
    }

    this.el.style.display = "";
    this.el.style.height = `${pos.height}px`;

    if (!this.last) {
      // First appearance — snap in rather than glide from nowhere. Every actual
      // move glides (below), regardless of distance. Reduced-motion is handled
      // in CSS (transition-property: none), which no-ops the glide there.
      this.el.style.transition = "none";
      this.el.style.transform = `translate(${pos.x}px, ${pos.y}px)`;
      void this.el.offsetWidth; // commit the un-transitioned move
      this.el.style.transition = "";
    } else {
      this.el.style.transitionDuration = `${GLIDE_MS}ms`;
      this.el.style.transform = `translate(${pos.x}px, ${pos.y}px)`;
    }
    this.last = pos;

    // Solid while moving; resume the fade-blink after a beat of stillness.
    this.el.classList.add("is-moving");
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(
      () => this.el.classList.remove("is-moving"),
      IDLE_BLINK_MS,
    );
  };

  destroy(): void {
    if (!this.enabled) return;
    this.view.dom.removeEventListener("compositionstart", this.onComposeStart);
    this.view.dom.removeEventListener("compositionend", this.onComposeEnd);
    this.view.dom.removeEventListener("mousedown", this.onMouseDown);
    this.view.dom.removeEventListener("focus", this.render);
    this.view.dom.removeEventListener("blur", this.render);
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.resize?.disconnect();
    this.view.dom.style.caretColor = "";
    this.el.remove();
  }
}

export const Caret = Extension.create({
  name: "caret",

  addProseMirrorPlugins() {
    return [new Plugin({ view: (view) => new CaretView(view) })];
  },
});

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
 * Owning the caret means owning **affinity** — the side of an ambiguous
 * position the caret sits on. A soft wrap is one document position with two
 * visual homes (end of visual line N, start of line N+1), and ProseMirror's
 * selection has no field for which one you mean, so `bias` reconstructs it.
 * Here that bit is not just cosmetic: a wrap is two caret stops, and the
 * arrow key that moves between them is consumed rather than moving the
 * document position (`handleKeyDown`). */
import { Extension } from "@tiptap/core";
import { Plugin, TextSelection } from "@tiptap/pm/state";
import type { EditorState } from "@tiptap/pm/state";
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
  // Reconstructed caret affinity (the bit the browser keeps but hides from the
  // DOM API): which side of an ambiguous position — a soft-wrap/bidi/mark
  // boundary — the caret belongs to, inferred from the last input's direction.
  // -1 = upstream (end of prev line), +1 = downstream (start of next line).
  private bias: number | null = null;
  // Direction recorded on keydown, resolved into `bias` by update() once
  // ProseMirror has actually moved the selection — the destination has to exist
  // before we can measure whether it landed on a wrap boundary.
  // `pendingHorizontal` marks Left/Right, the only keys the boundary inversion
  // applies to.
  private pending: number | null = null;
  private pendingHorizontal = false;
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

  update(_view: EditorView, prevState: EditorState): void {
    if (!this.enabled) return;

    const pending = this.pending;
    const horizontal = this.pendingHorizontal;
    this.pending = null;
    this.pendingHorizontal = false;

    const moved = prevState.selection.head !== this.view.state.selection.head;
    if (moved && pending != null) {
      // Resolve the recorded intent now that the selection has landed. At a
      // soft-wrap boundary the arriving caret belongs to the line it came
      // *from* — that's what makes the boundary two stops rather than one,
      // with handleKeyDown's flip supplying the second. Everywhere else the
      // affinity is simply the direction of travel.
      //
      // Horizontal keys only: Up/Down and Home/End already name the affinity
      // they want (Down wants the line it arrives on, End wants upstream even
      // though it travels right), so inverting them lands the caret a line off.
      this.bias =
        horizontal && this.isWrapBoundary(this.view.state.selection.head)
          ? -pending
          : pending;
    } else if (moved) {
      // The head moved with no key and no click behind it: a remote Yjs update
      // (ySyncPlugin restores the selection on every remote change), undo/redo,
      // or any programmatic setSelection. Whatever `bias` described, it
      // described a different position — and a stale one would spend the user's
      // next arrow press on a bogus flip. Drop it and re-derive from input.
      this.bias = null;
    }

    this.render();
  }

  /** Is `pos` a soft-wrap boundary — one document position with two visual
   * homes? Nothing in the model records where the browser chose to wrap, so it
   * has to be measured: ask for both sides and see if they landed on different
   * lines. (`view.endOfTextblock()` can't answer this — it reports *textblock*
   * edges, not wrap points.)
   *
   * Requires a step down *and* a jump left. An inline image or a taller mark
   * also shifts `.top` between the two sides; only a wrap throws the downstream
   * side back to the line's left edge. That second conjunct is also what keeps
   * the flip from ever stealing a key from gapcursor, tableEditing, or atom
   * traversal: those act only at textblock edges, and a position with content
   * to its left on one line and to its right on the next is by definition not
   * one. Deliberately narrow — a false positive costs a swallowed keystroke. */
  private isWrapBoundary(pos: number): boolean {
    try {
      const up = this.view.coordsAtPos(pos, -1);
      const down = this.view.coordsAtPos(pos, 1);
      return down.top > up.top + 1 && down.left < up.left;
    } catch {
      return false;
    }
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
    // The click's x is a more direct side signal than the last keyed direction.
    this.bias = null;
  };

  /** Two jobs, in order: record the input's direction so update() can infer
   * affinity once the selection lands, and consume the arrow key that crosses a
   * soft-wrap boundary in place.
   *
   * Must be a ProseMirror `handleKeyDown` prop rather than a DOM listener:
   * ProseMirror installs its own `keydown` listener in the EditorView
   * constructor, before any plugin view is constructed, so a listener added
   * from here would always run after PM's whole handler chain — too late to
   * preventDefault, and it would clobber `bias` on keys PM already consumed.
   *
   * Returns true only for a genuine boundary flip. Everything else falls
   * through to the plugins after us (tableEditing, gapcursor) and then PM's own
   * atom traversal, none of which must ever lose a key to this. */
  handleKeyDown = (event: KeyboardEvent): boolean => {
    const k = event.key;
    const left = k === "ArrowLeft";
    const right = k === "ArrowRight";

    if (left || k === "ArrowUp" || k === "End" || k === "Backspace") {
      this.pending = -1;
    } else if (
      right ||
      k === "ArrowDown" ||
      k === "Home" ||
      k === "Enter" ||
      k.length === 1 // a printable character
    ) {
      this.pending = 1;
    }
    // Modifiers, Escape, etc. leave the last inferred affinity in place.
    this.pendingHorizontal = left || right;

    // Cheap tests first — isWrapBoundary forces a layout read, and this keeps
    // it off the hot path of a held-down arrow.
    if (!this.enabled || !(left || right)) return false;
    // Never consume a modified arrow: Shift extends the selection, Cmd/Ctrl
    // jumps the line, Alt jumps the word. They still record intent above, which
    // is what makes Cmd+ArrowRight land upstream at a wrapped line end.
    if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey)
      return false;

    const dir = right ? 1 : -1;
    // `bias === -dir` — affinity pointing against the press — is exactly
    // "standing at the far end of a two-ended position". Anything else,
    // including a null (unknown) bias, falls through and moves normally: a
    // keystroke is never eaten on a guess.
    if (this.bias !== -dir) return false;
    const { selection } = this.view.state;
    if (!(selection instanceof TextSelection) || !selection.empty) return false;
    if (!this.isWrapBoundary(selection.head)) return false;

    // The flip sets bias to the direction just pressed, so the next press can't
    // flip again — a false-positive boundary costs one extra keypress, never a
    // stuck caret. Not correct inside RTL (bidi) runs, where the physical key
    // direction and the logical one diverge; same punt as before, and it
    // self-heals for the same reason.
    this.bias = dir;
    this.pending = null; // consumed here — no transaction, so no update()
    this.pendingHorizontal = false;
    this.render();
    return true;
  };

  /** Doc position → the scroller's scroll-origin space. The caret's side at an
   * ambiguous boundary is chosen by, in order: the click x (most direct), then
   * the reconstructed affinity `bias`, then `coordsAtPos`'s default. Subtracts
   * the scroller's border (`clientLeft/Top`) because `getBoundingClientRect`
   * includes the border but absolute positioning is relative to the padding box. */
  private caretPos(
    clickX: number | null,
    bias: number | null,
  ): CaretPos | null {
    if (!this.scroller) return null;
    const head = this.view.state.selection.head;
    let coords: { left: number; top: number; bottom: number };
    try {
      if (clickX != null) {
        const before = this.view.coordsAtPos(head, -1);
        const after = this.view.coordsAtPos(head, 1);
        const upstream =
          Math.abs(before.left - clickX) <= Math.abs(after.left - clickX);
        coords = upstream ? before : after;
        // Keep the side the click picked. The click's x is the most direct
        // affinity signal there is, and persisting it is what lets
        // click-then-arrow behave like arrow-then-arrow at a wrapped position —
        // otherwise `bias` is null there and the first press can't flip. Off a
        // boundary the two sides coincide, so whichever wins is harmless.
        this.bias = upstream ? -1 : 1;
      } else if (bias != null) {
        coords = this.view.coordsAtPos(head, bias);
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
    // `bias` persists across renders (it's the last known intent); `clickX` is
    // one-shot, consumed above.
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

    const pos = active ? this.caretPos(clickX, this.bias) : null;
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
    // The keydown prop and the plugin view have to be the same Plugin so the
    // prop can reach the instance; the closure is per-editor because
    // addProseMirrorPlugins runs once per editor.
    let caret: CaretView | null = null;
    return [
      new Plugin({
        view: (view) => (caret = new CaretView(view)),
        props: {
          handleKeyDown: (_view, event) => caret?.handleKeyDown(event) ?? false,
        },
      }),
    ];
  },
});

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

/** Which side of a soft-wrap boundary the caret sits on — the affinity bit the
 * browser keeps and hides from the DOM API. A wrap is one document position
 * with two visual homes, and this says which one to paint. */
type Affinity = "upstream" | "downstream";

/** Which way the last input travelled through the document. Not an `Affinity`:
 * at a wrap the caret lands on the side it came *from*, so the two are
 * opposites there and identical everywhere else. */
type Direction = "backward" | "forward";

/** `coordsAtPos`'s `side` argument, which is a signed number. The only place
 * either type above touches one. */
const SIDE: Record<Affinity, number> = { upstream: -1, downstream: 1 };

/** Which side of the fence a move lands on, given the way it travelled.
 *
 * Horizontal travel lands on the side it came *from* — you stay on the line you
 * were already on, and the arrow that crosses to the other side is the fence's
 * second stop (handleKeyDown's flip). Every other key names its own
 * destination: ArrowDown wants the line it arrives on, End wants the end of the
 * line even though it travels forward. Those map straight across. */
function arrivingAffinity(from: Direction, horizontal: boolean): Affinity {
  if (horizontal) return from === "forward" ? "upstream" : "downstream";
  return from === "forward" ? "downstream" : "upstream";
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
  // INVARIANT: non-null *only* while the caret is a collapsed cursor standing
  // at a wrap boundary. Enter the fence, set it; leave, clear it. Everywhere
  // else there is one place to paint and no side to record. That invariant is
  // what lets handleKeyDown decide the flip from this bit alone without
  // measuring — so it is re-derived on every move (update()) and dropped
  // whenever a reflow moves the wrap points (the ResizeObserver above).
  private bias: Affinity | null = null;
  // Direction recorded on keydown, resolved into `bias` by update() once
  // ProseMirror has actually moved the selection — the destination has to exist
  // before we can measure whether it landed on a wrap boundary.
  // `pendingHorizontal` marks Left/Right, the only keys that land on the side
  // they came from rather than the side they point at.
  private pending: Direction | null = null;
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
      // `last`) rather than gliding. It also moves the wrap points themselves,
      // so the fence the caret was standing in may not be there any more: drop
      // `bias` too, or the invariant ("non-null ⇒ at a boundary") quietly stops
      // holding and the next arrow press spends itself on a flip that isn't.
      this.resize = new ResizeObserver(() => {
        this.last = null;
        this.bias = null;
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

    const sel = this.view.state.selection;
    const collapsed = sel instanceof TextSelection && sel.empty;
    const moved = prevState.selection.head !== sel.head;
    if (moved || !collapsed) {
      // Enter the fence, set the bit; leave it, clear the bit. `bias` is
      // re-derived from scratch here on every move and never carried, which is
      // what makes the invariant true and lets handleKeyDown trust it without
      // measuring anything.
      //
      // It stays null unless all three hold: the caret actually moved (a
      // stationary head can't have entered anywhere), it's a collapsed cursor
      // (a range has no affinity), and there's a `pending` direction behind the
      // move. A null `pending` means no key and no click caused it — a remote
      // Yjs update, undo/redo, a programmatic setSelection — so there's nothing
      // to infer from and guessing would cost the user a keystroke.
      //
      // Which side of the fence you land on is the side you came *from*: that's
      // what makes the boundary two stops rather than one, with handleKeyDown's
      // flip supplying the second. Horizontal keys only — Up/Down and Home/End
      // already name the affinity they want (Down wants the line it arrives on,
      // End wants upstream even though it travels right), so inverting those
      // lands the caret a line off.
      this.bias =
        moved && collapsed && pending != null && this.isWrapBoundary(sel.head)
          ? arrivingAffinity(pending, horizontal)
          : null;
    }

    this.render();
  }

  /** Is `pos` inside the fence — a soft-wrap boundary, one document position
   * with two visual homes? Nothing in the model records where the browser chose
   * to wrap, so it has to be measured: ask for both sides and see where they
   * landed. (`view.endOfTextblock()` can't answer this — it reports *textblock*
   * edges, not wrap points.)
   *
   * Requires a step down *and* a jump left. A taller mark or an inline image
   * also shifts `.top` between the two sides; only a wrap throws the downstream
   * side back to the line's left edge. That second conjunct is also what keeps
   * the flip from stealing a key from gapcursor, tableEditing, or atom
   * traversal: those act only at textblock edges, and a position with content
   * to its left on one line and to its right on the next is by definition not
   * one. */
  private isWrapBoundary(pos: number): boolean {
    try {
      const up = this.view.coordsAtPos(pos, SIDE.upstream);
      const down = this.view.coordsAtPos(pos, SIDE.downstream);
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
      this.pending = "backward";
    } else if (
      right ||
      k === "ArrowDown" ||
      k === "Home" ||
      k === "Enter" ||
      k.length === 1 // a printable character
    ) {
      this.pending = "forward";
    }
    // Modifiers, Escape, etc. leave the last recorded direction in place.
    this.pendingHorizontal = left || right;

    if (!this.enabled || !(left || right)) return false;
    // Never consume a modified arrow: Shift extends the selection, Cmd/Ctrl
    // jumps the line, Alt jumps the word. They still record direction above,
    // which is what makes Cmd+ArrowRight land upstream at a wrapped line end.
    if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey)
      return false;

    // Crossing the fence. `bias` is non-null only while the caret stands at a
    // wrap boundary (see update()), so the bit alone answers both questions:
    // are we in a fence, and are we at the end of it this press points away
    // from. Nothing to re-measure. A null bias falls through and moves the
    // document normally — a keystroke is never eaten on a guess.
    //
    // Not correct inside RTL (bidi) runs, where the physical key direction and
    // the logical one diverge. Same punt as the rest of the file; the cost is
    // one extra press, since the flip lands on the far side and the next press
    // finds `bias` no longer pointing against it.
    const from: Affinity = right ? "upstream" : "downstream";
    if (this.bias !== from) return false;

    this.bias = right ? "downstream" : "upstream";
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
    bias: Affinity | null,
  ): CaretPos | null {
    if (!this.scroller) return null;
    const head = this.view.state.selection.head;
    let coords: { left: number; top: number; bottom: number };
    try {
      if (clickX != null) {
        const up = this.view.coordsAtPos(head, SIDE.upstream);
        const down = this.view.coordsAtPos(head, SIDE.downstream);
        const picked: Affinity =
          Math.abs(up.left - clickX) <= Math.abs(down.left - clickX)
            ? "upstream"
            : "downstream";
        coords = picked === "upstream" ? up : down;
        // Keep the side the click picked, but only if the click landed in a
        // fence — the same invariant update() maintains, checked against the
        // rects already in hand rather than a second layout read. Persisting it
        // is what lets click-then-arrow behave like arrow-then-arrow at a
        // wrapped position; off a boundary the two sides coincide and recording
        // a side would plant exactly the stale bit the invariant forbids.
        this.bias =
          down.top > up.top + 1 && down.left < up.left ? picked : null;
      } else if (bias != null) {
        coords = this.view.coordsAtPos(head, SIDE[bias]);
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
    // `bias` is whatever the last move left behind — non-null only if that move
    // ended inside a fence; `clickX` is one-shot, consumed above.
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

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
 * **We replaced the caret's pixels, not the caret.** `caret-color:
 * transparent` hides the native caret; it does not disable it. The browser
 * still owns where the cursor is, which visual line it sits on, and where every
 * key we don't handle sends it. This file draws a rectangle to match.
 *
 * That matters for **affinity** — which side of a soft wrap the caret is on. A
 * wrap is one document position with two visual homes (end of visual line N,
 * start of line N+1), and neither the DOM nor ProseMirror has a field for which
 * one you mean. The browser keeps that bit internally and exposes no way to
 * read or write it, so `bias` here is a *guess at* the browser's bit, inferred
 * from the key that caused the move, used only to pick which side to paint.
 *
 * The guess must **mirror** the browser, never lead it. An earlier version made
 * a wrap two caret stops — arriving painted the side you came from, and a
 * second arrow press flipped sides without moving the document. It worked, and
 * it broke everything that reads the browser's bit instead of ours: cmd-left at
 * a boundary no-opped, and Up/Down departed from the line the caret wasn't
 * visibly on (it also carries the goal column). There is no fix from inside
 * this file — two bits exist and we can only write one. Making a wrap two stops
 * requires owning navigation outright (Left/Right by grapheme cluster, Up/Down
 * with goal-column memory, line and word ops, Shift extension, bidi), the way
 * CodeMirror and Monaco do. ProseMirror deliberately delegates all of it to the
 * browser. See git history around `arrivingAffinity` for the working attempt. */
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

/** Which side of a soft-wrap boundary the caret sits on — end of visual line N
 * or start of line N+1. */
type Affinity = "upstream" | "downstream";

/** `coordsAtPos`'s `side` argument, which is a signed number. The only place
 * this type touches one. */
const SIDE: Record<Affinity, number> = { upstream: -1, downstream: 1 };

/** Guess the affinity the browser is about to pick, from the key causing the
 * move. Mirrors it; see the docstring for why leading it doesn't work.
 *
 * Most keys are just their direction of travel. The exceptions are the ones
 * that name a line *edge* rather than a direction: End and Cmd/Ctrl+ArrowRight
 * travel forward but mean "end of this visual line" (upstream), and Home and
 * Cmd/Ctrl+ArrowLeft mean the start of it (downstream). Returns null for keys
 * that don't move the caret, which leaves the last guess in place. */
function affinityForKey(event: KeyboardEvent): Affinity | null {
  const k = event.key;
  const lineOp = event.metaKey || event.ctrlKey;
  if (k === "End" || (lineOp && k === "ArrowRight")) return "upstream";
  if (k === "Home" || (lineOp && k === "ArrowLeft")) return "downstream";
  if (k === "ArrowLeft" || k === "ArrowUp" || k === "Backspace")
    return "upstream";
  if (
    k === "ArrowRight" ||
    k === "ArrowDown" ||
    k === "Enter" ||
    k.length === 1 // a printable character
  )
    return "downstream";
  return null;
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
  // Our guess at the browser's affinity, used only to pick which side of an
  // ambiguous position to paint. Null means "no idea" — paint coordsAtPos's
  // default. Only meaningful at a soft wrap; anywhere else both sides resolve
  // to the same point, so a stale value is harmless.
  private bias: Affinity | null = null;
  // Set on keydown, applied by update() once ProseMirror has actually moved the
  // selection. Two moments, because the key names the affinity of a position
  // that doesn't exist yet when the key is pressed.
  private pending: Affinity | null = null;
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
    this.pending = null;

    const sel = this.view.state.selection;
    const moved = prevState.selection.head !== sel.head;
    if (!(sel instanceof TextSelection) || !sel.empty) {
      // A range has no affinity, and no caret is painted for one.
      this.bias = null;
    } else if (moved) {
      // Apply the guess recorded at keydown. A null `pending` means no key
      // caused this move — a remote Yjs update (ySyncPlugin restores the
      // selection on every remote change), undo/redo, a programmatic
      // setSelection — so there's nothing to mirror and coordsAtPos's default
      // is as good as anything we'd invent.
      this.bias = pending;
    }

    this.render();
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

  /** Record which side the browser is about to put the caret on, for update()
   * to apply once the selection has actually moved. **Never consumes a key** —
   * always returns false. Navigation belongs to the browser (see the docstring);
   * this only watches it go by.
   *
   * Must be a ProseMirror prop rather than a DOM listener even so. PM installs
   * its own keydown listener in the EditorView constructor, before any plugin
   * view exists, so a listener added from here runs after PM's entire handler
   * chain — including after other plugins have consumed keys we would then
   * wrongly record, like the command menu's ArrowDown or a table's ArrowRight.
   *
   * Not correct inside RTL (bidi) runs, where the physical key direction and
   * the logical one diverge; the browser tracks embedding levels and we don't.
   * Cosmetic only — the caret paints a line off at a wrapped bidi boundary. */
  handleKeyDown = (event: KeyboardEvent): boolean => {
    if (this.enabled) this.pending = affinityForKey(event) ?? this.pending;
    return false;
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
        // Keep the side the click picked, so a later render without a fresh
        // click still paints it. Off a wrap the two sides coincide and the
        // choice is between identical points, so recording one is harmless.
        this.bias = picked;
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
    // `bias` is whatever the last move left behind; `clickX` is one-shot,
    // consumed above.
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

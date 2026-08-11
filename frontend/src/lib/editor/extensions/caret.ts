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
 * read or write it.
 *
 * We deliberately don't model it. Ordinary motion paints wherever `coordsAtPos`
 * defaults to, so arrowing right onto a wrap puts the caret at the start of
 * line N+1 rather than the end of line N — which reads wrong at first, but is
 * what the browser does natively and what Notion does too. It's the convention,
 * not a defect. Don't "fix" it.
 *
 * The one exception is a key that *names* a side: End and Cmd+ArrowRight mean
 * "end of this visual line", and the default would paint them at the start of
 * the next one, contradicting both the command and the native caret. Those keys
 * are honoured (`affinityForKey`). That is the whole of it — a lookup on the
 * key, no state machine, no inference from direction of travel.
 *
 * It has been tried. Guessing the bit from the last key worked but bought
 * nothing; *leading* it — making a wrap two caret stops, with an arrow press
 * that flips sides without moving the document — broke everything that reads
 * the browser's bit instead of ours: cmd-left at a boundary no-opped, and
 * Up/Down departed from the line the caret wasn't visibly on, since that bit
 * carries the goal column too. There is no fix from inside this file; two bits
 * exist and we can only write one. Doing it properly means owning navigation
 * outright (Left/Right by grapheme cluster, Up/Down with goal-column memory,
 * line and word ops, Shift extension, bidi), the way CodeMirror and Monaco do —
 * ProseMirror deliberately delegates all of it to the browser. Git history
 * around `arrivingAffinity` has the working attempt if it ever comes up. */
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

/** Which side of a soft-wrap boundary to measure — end of visual line N or
 * start of line N+1 — as `coordsAtPos`'s signed `side` argument. */
type Affinity = "upstream" | "downstream";
const SIDE: Record<Affinity, number> = { upstream: -1, downstream: 1 };

/** The side a key *names*, for the few that name one at all.
 *
 * Ordinary motion returns null and takes `coordsAtPos`'s default, which is the
 * convention at a wrap (see the docstring) — this is not affinity tracking and
 * must not grow into it. But a key that means "end of this visual line" lands
 * on a wrap boundary and the default paints it at the start of the *next* line,
 * contradicting both the command and the native caret. Those keys say which
 * side they meant, so use it.
 *
 * Meta only, not Ctrl: Cmd+Arrow is line-edge on macOS, while Ctrl+Arrow on
 * Windows/Linux is word motion, which can land anywhere and names nothing. */
function affinityForKey(event: KeyboardEvent): Affinity | null {
  const k = event.key;
  if (k === "End" || (event.metaKey && k === "ArrowRight")) return "upstream";
  if (k === "Home" || (event.metaKey && k === "ArrowLeft")) return "downstream";
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
  // The side a line-edge key named, applied on the next render. Null for every
  // other key, which is how ordinary motion keeps coordsAtPos's default.
  // `pending` is set at keydown and moves to `side` once the selection has
  // actually landed — the key names the side of a position that doesn't exist
  // yet when it's pressed.
  private pending: Affinity | null = null;
  private side: Affinity | null = null;
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
    if (!this.enabled) return;
    // Consume whatever the last key named — null for almost every key, which
    // clears any side a previous line-edge key set.
    this.side = this.pending;
    this.pending = null;
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
  };

  /** Note the side a line-edge key named, for update() to apply once the
   * selection has landed. **Never consumes a key** — always returns false;
   * navigation belongs to the browser (see the docstring).
   *
   * A ProseMirror prop rather than a DOM listener, because PM installs its own
   * keydown listener in the EditorView constructor, before any plugin view
   * exists — a listener added from here runs after PM's entire handler chain,
   * including after other plugins have consumed keys we'd then wrongly note. */
  handleKeyDown = (event: KeyboardEvent): boolean => {
    if (this.enabled) this.pending = affinityForKey(event);
    return false;
  };

  /** Doc position → the scroller's scroll-origin space. At an ambiguous
   * position the side comes from the click's x if there was one, then from a
   * line-edge key if one named a side, and otherwise from `coordsAtPos`'s own
   * default. Subtracts the scroller's border (`clientLeft/Top`) because
   * `getBoundingClientRect` includes the border but absolute positioning is
   * relative to the padding box. */
  private caretPos(
    clickX: number | null,
    side: Affinity | null,
  ): CaretPos | null {
    if (!this.scroller) return null;
    const head = this.view.state.selection.head;
    let coords: { left: number; top: number; bottom: number };
    try {
      if (clickX != null) {
        const up = this.view.coordsAtPos(head, SIDE.upstream);
        const down = this.view.coordsAtPos(head, SIDE.downstream);
        coords =
          Math.abs(up.left - clickX) <= Math.abs(down.left - clickX)
            ? up
            : down;
      } else if (side != null) {
        coords = this.view.coordsAtPos(head, SIDE[side]);
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

    const pos = active ? this.caretPos(clickX, this.side) : null;
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
    // The keydown prop and the plugin view must be the same Plugin so the prop
    // can reach the instance; the closure is per-editor because
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

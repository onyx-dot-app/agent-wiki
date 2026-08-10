"use client";

/** The link hover editor — the extension half. Hovering any link surfaces a
 * card (`LinkHoverCard`, in `./components.tsx`) showing its URL with fields to
 * edit the text + href, an unlink button, and an open-in-new-tab affordance.
 * It's the shared edit surface for every link however it was created: the
 * `/URL` command, a pasted <a>, or a promoted bare URL (typed or pasted). For
 * a promoted link — where the visible text equals the href — the card's text
 * field starts empty, inviting a label.
 *
 * Hover state is pure ephemeral UI, so it lives in this plugin's `view()`
 * instance (a `ReactRenderer` + DOM listeners on the editor), NOT in
 * ProseMirror document state — a mousemove must never dispatch a transaction.
 * Edits, on the other hand, are real document mutations and go through the
 * helpers below. The card is anchored to the hovered <a> element itself (a
 * live `getBoundingClientRect` source), so no Floating UI mount is needed. */
import { Extension, getMarkRange, type Editor } from "@tiptap/core";
import { Plugin } from "@tiptap/pm/state";
import { ReactRenderer } from "@tiptap/react";
import {
  LinkHoverCard,
  type HoveredLink,
  type LinkHoverCardProps,
} from "@/lib/editor/extensions/components";

/** Replace the link's `[from, to)` range with `text` re-marked to `href` —
 * one transaction covers both a relabel and a retarget. Mirrors the `/URL`
 * insert (`linkInput.ts`): `schema.text` + `removeStoredMark` so typing on
 * from the link's end doesn't extend it. An empty label falls back to the
 * href, matching the promoted `[url](url)` shape. */
export function applyLinkEdit(
  editor: Editor,
  from: number,
  to: number,
  href: string,
  text: string,
): void {
  const { state } = editor.view;
  const linkType = state.schema.marks.link;
  if (!linkType) return;
  const node = state.schema.text(text.trim() || href, [
    linkType.create({ href }),
  ]);
  const tr = state.tr.replaceWith(from, to, node);
  tr.removeStoredMark(linkType);
  editor.view.dispatch(tr);
  editor.view.focus();
}

/** Strip the link mark off `[from, to)`, leaving the text — the false-positive
 * escape hatch for a promoted URL, and the same effect as MarkdownLink's
 * Mod-k, applied to a specific range rather than the selection. */
export function unlinkRange(editor: Editor, from: number, to: number): void {
  const { state } = editor.view;
  const linkType = state.schema.marks.link;
  if (!linkType) return;
  editor.view.dispatch(state.tr.removeMark(from, to, linkType));
  editor.view.focus();
}

// Grace period before a hover card hides once the pointer leaves the link —
// long enough to cross the small gap into the card, short enough not to linger.
const HIDE_DELAY_MS = 200;

/** Owns the hover card's `ReactRenderer` and the hover lifecycle: which link
 * is under the pointer, the leave-grace timer, and a "pinned" latch that keeps
 * the card open while a field is focused (so editing survives the pointer
 * wandering off). */
class LinkHoverView {
  private renderer: ReactRenderer<unknown, LinkHoverCardProps>;
  private root: HTMLElement;
  private hovered: HoveredLink | null = null;
  private hideTimer: ReturnType<typeof setTimeout> | null = null;
  private pinned = false;

  constructor(private editor: Editor) {
    this.root = document.createElement("div");
    document.body.appendChild(this.root);
    this.renderer = new ReactRenderer(LinkHoverCard, {
      editor,
      props: this.props(),
    });
    this.root.appendChild(this.renderer.element);
    editor.view.dom.addEventListener("mouseover", this.onMouseOver);
    editor.view.dom.addEventListener("mouseout", this.onMouseOut);
  }

  private onMouseOver = (event: MouseEvent) => {
    const anchor = (event.target as HTMLElement | null)?.closest?.("a");
    if (!anchor || !this.editor.view.dom.contains(anchor)) return;
    const link = this.resolve(anchor);
    if (!link) return;
    this.cancelHide();
    // Same link already showing — don't re-push and clobber in-progress edits.
    if (
      this.hovered &&
      this.hovered.from === link.from &&
      this.hovered.to === link.to &&
      this.hovered.href === link.href
    ) {
      return;
    }
    this.hovered = link;
    this.render();
  };

  private onMouseOut = (event: MouseEvent) => {
    const anchor = (event.target as HTMLElement | null)?.closest?.("a");
    if (!anchor) return;
    // Moving within the same <a> (across its child text nodes) isn't a leave.
    const next = event.relatedTarget as HTMLElement | null;
    if (next && anchor.contains(next)) return;
    this.scheduleHide();
  };

  /** Resolve the <a> DOM element back to its link mark's doc range + attrs.
   * Read-only viewers get native anchors and no editor, so skip them. */
  private resolve(anchor: HTMLElement): HoveredLink | null {
    const view = this.editor.view;
    if (!view.editable) return null;
    const linkType = view.state.schema.marks.link;
    if (!linkType) return null;
    let pos: number;
    try {
      pos = view.posAtDOM(anchor, 0);
    } catch {
      return null;
    }
    if (pos < 0) return null;
    const size = view.state.doc.content.size;
    const range = getMarkRange(
      view.state.doc.resolve(Math.min(pos + 1, size)),
      linkType,
    );
    if (!range) return null;
    return {
      from: range.from,
      to: range.to,
      href: anchor.getAttribute("href") ?? "",
      text: view.state.doc.textBetween(range.from, range.to),
      el: anchor,
    };
  }

  private scheduleHide() {
    if (this.pinned) return;
    this.cancelHide();
    this.hideTimer = setTimeout(() => this.close(), HIDE_DELAY_MS);
  }

  private cancelHide() {
    if (this.hideTimer) {
      clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
  }

  private close = () => {
    this.cancelHide();
    this.pinned = false;
    this.hovered = null;
    this.render();
  };

  private props(): LinkHoverCardProps {
    return {
      link: this.hovered,
      onApply: (from, to, href, text) => {
        applyLinkEdit(this.editor, from, to, href, text);
        this.close();
      },
      onUnlink: (from, to) => {
        unlinkRange(this.editor, from, to);
        this.close();
      },
      onOpen: (href) => window.open(href, "_blank", "noopener,noreferrer"),
      onPointerEnter: () => this.cancelHide(),
      onPointerLeave: () => this.scheduleHide(),
      onPin: () => {
        this.pinned = true;
        this.cancelHide();
      },
      onClose: this.close,
    };
  }

  private render() {
    this.renderer.updateProps(this.props());
  }

  destroy() {
    this.editor.view.dom.removeEventListener("mouseover", this.onMouseOver);
    this.editor.view.dom.removeEventListener("mouseout", this.onMouseOut);
    this.cancelHide();
    this.renderer.destroy();
    this.root.remove();
  }
}

export const LinkHover = Extension.create({
  name: "linkHover",

  addProseMirrorPlugins() {
    const editor = this.editor;
    return [new Plugin({ view: () => new LinkHoverView(editor) })];
  },
});

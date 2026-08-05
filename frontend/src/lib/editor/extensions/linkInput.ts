"use client";

/** The `/URL` command's link-entry popover — the extension half. Typing `/`,
 * picking **URL**, deletes the slash text and opens a small floating form
 * (URL + optional display text) anchored at the caret; submitting inserts the
 * text carrying a `link` mark. It's insert-a-new-link only: the slash menu is
 * `startOfLine`-only (see `commandMenu.ts`), so there's never a selection to
 * wrap — retargeting/removing an existing link is `MarkdownLink`'s `Mod-k` and
 * (later) the right-click menu, not this.
 *
 * Shape mirrors `highlights.ts`: the open/anchor state lives in a ProseMirror
 * plugin, poked in and out through `tr.setMeta` by the plain helper functions
 * below (not Tiptap commands — no module augmentation to carry), and a plugin
 * `view()` renders the React form (`LinkInputPopover`, in `./components.tsx`)
 * through `@tiptap/react`'s `ReactRenderer`, the same bridge the slash menu
 * uses. Positioning is Opal's Radix `Popover` against a virtual caret anchor,
 * so — unlike the slash menu — there's no Floating UI `mount` here. */
import { Extension, type Editor } from "@tiptap/core";
import { Plugin, PluginKey, TextSelection } from "@tiptap/pm/state";
import { ReactRenderer } from "@tiptap/react";
import {
  LinkInputPopover,
  type LinkInputPopoverProps,
} from "@/lib/editor/extensions/components";

interface LinkInputState {
  open: boolean;
  anchorPos: number | null;
}

export const linkInputKey = new PluginKey<LinkInputState>("linkInput");

/** Open the popover anchored at the current caret. Callers delete the `/URL`
 * text first (via the command's range), so `selection.from` is already the
 * clean insertion point. */
export function openLinkInput(editor: Editor): void {
  const { state } = editor.view;
  editor.view.dispatch(
    state.tr.setMeta(linkInputKey, {
      open: true,
      anchorPos: state.selection.from,
    } satisfies LinkInputState),
  );
}

/** Close without inserting, and hand focus back to the editor. */
export function closeLinkInput(editor: Editor): void {
  editor.view.dispatch(
    editor.view.state.tr.setMeta(linkInputKey, { open: false }),
  );
  editor.view.focus();
}

/** Insert `text` linked to `href` at the stored anchor, then close. Mirrors
 * `MarkdownLink`'s `[text](url)` input rule exactly — `schema.text` with a
 * `link` mark, `removeStoredMark` so the next characters typed after the link
 * don't join it — plus a caret move to just past the inserted run. */
export function insertLink(
  editor: Editor,
  { href, text }: { href: string; text: string },
): void {
  const { state } = editor.view;
  const anchorPos = linkInputKey.getState(state)?.anchorPos;
  const linkType = state.schema.marks.link;
  // No anchor (popover already closed) or no link mark in the schema
  // (StarterKit's `link: false`) would make this a crash, not a no-op.
  if (anchorPos == null || !linkType) return;

  const node = state.schema.text(text, [linkType.create({ href })]);
  const tr = state.tr.insert(anchorPos, node);
  tr.removeStoredMark(linkType);
  tr.setSelection(TextSelection.create(tr.doc, anchorPos + node.nodeSize));
  tr.setMeta(linkInputKey, { open: false });
  editor.view.dispatch(tr);
  editor.view.focus();
}

/** Owns the `ReactRenderer` for the popover: mounts it once (rendering nothing
 * while closed), pushes fresh props only when open/anchor actually change so
 * ordinary typing doesn't churn it, and tears it down on editor destroy. */
class LinkInputView {
  private renderer: ReactRenderer<unknown, LinkInputPopoverProps>;
  private root: HTMLElement;
  private last: LinkInputState;

  constructor(private editor: Editor) {
    this.root = document.createElement("div");
    document.body.appendChild(this.root);
    this.last = this.read();
    this.renderer = new ReactRenderer(LinkInputPopover, {
      editor,
      props: this.props(this.last),
    });
    this.root.appendChild(this.renderer.element);
  }

  private read(): LinkInputState {
    const s = linkInputKey.getState(this.editor.state);
    return { open: s?.open ?? false, anchorPos: s?.anchorPos ?? null };
  }

  private props(state: LinkInputState): LinkInputPopoverProps {
    return {
      open: state.open,
      anchorPos: state.anchorPos,
      view: this.editor.view,
      onSubmit: (href, text) => insertLink(this.editor, { href, text }),
      onCancel: () => closeLinkInput(this.editor),
    };
  }

  update(): void {
    const next = this.read();
    if (
      next.open === this.last.open &&
      next.anchorPos === this.last.anchorPos
    ) {
      return;
    }
    this.last = next;
    this.renderer.updateProps(this.props(next));
  }

  destroy(): void {
    this.renderer.destroy();
    this.root.remove();
  }
}

export const LinkInput = Extension.create({
  name: "linkInput",

  addProseMirrorPlugins() {
    const editor = this.editor;
    return [
      new Plugin<LinkInputState>({
        key: linkInputKey,
        state: {
          init: () => ({ open: false, anchorPos: null }),
          apply(tr, value) {
            let next = value;
            const meta = tr.getMeta(linkInputKey) as
              | Partial<LinkInputState>
              | undefined;
            if (meta) {
              next = {
                open: meta.open ?? value.open,
                anchorPos:
                  meta.anchorPos !== undefined
                    ? meta.anchorPos
                    : value.anchorPos,
              };
            }
            // Keep the anchor pinned to its text if an edit lands before it
            // while the popover is open (e.g. a concurrent co-editor's insert).
            if (next.anchorPos != null && tr.docChanged) {
              next = { ...next, anchorPos: tr.mapping.map(next.anchorPos) };
            }
            return next;
          },
        },
        view: () => new LinkInputView(editor),
      }),
    ];
  },
});

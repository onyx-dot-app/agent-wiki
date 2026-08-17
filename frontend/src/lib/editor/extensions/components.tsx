"use client";

/** The editor extensions' React views — the components their `ReactRenderer`
 * bridges mount: the slash-command menu (`CommandMenu`, for `commandMenu.ts`),
 * the `/URL` link-entry popover (`LinkInputPopover`, for `linkInput.ts`), and
 * the link hover editor (`LinkHoverCard`, for `linkHover.ts`).
 * They live here, beside their extensions, rather than in the editor's shared
 * `components.tsx`: each is an implementation detail of one extension, not a
 * shared shell component. Keeping them in `extensions/` is also what stops
 * `extensions/` from importing back into the editor shell — the edge that
 * closed a circular dependency (`components.tsx → extensions/index →
 * extensions/commandMenu → components.tsx`). */
import {
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type Ref,
} from "react";
import type {
  SuggestionKeyDownProps,
  SuggestionProps,
} from "@tiptap/suggestion";
import type { EditorView } from "@tiptap/pm/view";
import {
  Button,
  InputTypeIn,
  LineItemButton,
  Popover,
  Text,
} from "@onyx-ai/opal/components";
import { SvgExternalLink, SvgTrash } from "@onyx-ai/opal/icons";
import type { CommandItem } from "@/lib/editor/extensions/types";

export interface CommandMenuHandle {
  onKeyDown: (props: SuggestionKeyDownProps) => boolean;
}
export type CommandMenuProps = {
  ref?: Ref<CommandMenuHandle>;
} & SuggestionProps<CommandItem>;
export function CommandMenu({ ref, items, command }: CommandMenuProps) {
  const [selected, setSelected] = useState(0);
  useEffect(() => setSelected(0), [items]);

  const select = (index: number) => {
    const item = items[index];
    if (item) command(item);
  };

  useImperativeHandle(ref, () => ({
    onKeyDown: ({ event }) => {
      if (event.key === "ArrowDown") {
        setSelected((s) => (items.length ? (s + 1) % items.length : 0));
        return true;
      }
      if (event.key === "ArrowUp") {
        setSelected((s) =>
          items.length ? (s - 1 + items.length) % items.length : 0,
        );
        return true;
      }
      if (event.key === "Enter") {
        select(selected);
        return true;
      }
      return false;
    },
  }));

  if (items.length === 0) return null;

  return (
    <div className="max-h-[320px] w-[220px] overflow-y-auto rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-00) p-1 shadow-[0px_2px_6px_var(--shadow-02),0px_0px_2px_var(--shadow-01)]">
      {items.map((item, i) => (
        // The wrapper carries the keyboard-selection highlight rather than
        // relying on LineItemButton's own "selected" state, which is a hover-
        // weight tint — too quiet for the thing Enter is about to apply. A
        // solid fill and nothing else, as Notion's menu does it.
        <div
          key={item.title}
          className={
            i === selected
              ? "rounded-(--radius-06) bg-(--background-tint-03)"
              : undefined
          }
        >
          <LineItemButton
            title={item.title}
            icon={item.icon}
            sizePreset="main-ui"
            variant="section"
            state={i === selected ? "selected" : "empty"}
            onClick={() => select(i)}
          />
        </div>
      ))}
    </div>
  );
}

/** Prepend a scheme so a bare `example.com` becomes a real link, but leave an
 * explicit one (`https:`, `mailto:`, …) untouched. Deliberately permissive —
 * no strict validation gate — because a mistyped link is fixed by unlinking,
 * not by blocking submit; the only hard stop is an empty URL. */
export function normalizeUrl(raw: string): string {
  const url = raw.trim();
  if (!url) return "";
  return /^[a-z][\w+.-]*:/i.test(url) ? url : `https://${url}`;
}

export interface LinkInputPopoverProps {
  open: boolean;
  /** Doc position the link inserts at; also where the popover anchors. */
  anchorPos: number | null;
  view: EditorView;
  onSubmit: (href: string, text: string) => void;
  onCancel: () => void;
}

/** The `/URL` link-entry form (see `linkInput.ts`). Opal `Popover` anchored to
 * a virtual element at the caret — no trigger button, we open it
 * programmatically — with URL (autofocused, required) over an optional display
 * text. Blank text falls back to the URL, matching `[url](url)`. The form
 * portals to `document.body`, so its keystrokes never reach ProseMirror; focus
 * handoff back to the editor is the caller's job (`onSubmit`/`onCancel`). */
export function LinkInputPopover({
  open,
  anchorPos,
  view,
  onSubmit,
  onCancel,
}: LinkInputPopoverProps) {
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const urlRef = useRef<HTMLInputElement>(null);

  // Fresh fields on each open — the previous link's values shouldn't linger.
  useEffect(() => {
    if (open) {
      setUrl("");
      setText("");
    }
  }, [open]);

  // A virtual anchor: Radix measures the caret through `coordsAtPos` on every
  // reposition, so the popover tracks the caret across scroll. Guarded because
  // the position can briefly fall out of range mid-edit.
  const virtualRef = useMemo(() => {
    if (anchorPos == null) return null;
    return {
      current: {
        getBoundingClientRect: () => {
          try {
            const c = view.coordsAtPos(anchorPos);
            return new DOMRect(c.left, c.top, 0, c.bottom - c.top);
          } catch {
            return new DOMRect(0, 0, 0, 0);
          }
        },
      },
    };
  }, [view, anchorPos]);

  const submit = () => {
    const href = normalizeUrl(url);
    if (!href) return;
    onSubmit(href, text.trim() || href);
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };

  return (
    <Popover
      open={open && virtualRef != null}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      {virtualRef ? <Popover.Anchor virtualRef={virtualRef} /> : null}
      <Popover.Content
        align="start"
        sideOffset={6}
        width="fit"
        // We own focus: autofocus the URL field on open, and don't let Radix
        // yank it to a (non-existent) trigger on close — the caller returns it
        // to the editor.
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          urlRef.current?.focus();
        }}
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        <div className="flex w-[280px] flex-col gap-3 p-1">
          <div className="flex flex-col gap-1.5">
            <Text font="main-ui-muted" color="text-03">
              URL
            </Text>
            <InputTypeIn
              ref={urlRef}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="https://…"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Text font="main-ui-muted" color="text-03">
              Text
            </Text>
            <InputTypeIn
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Defaults to the URL"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" prominence="secondary" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="action"
              disabled={!url.trim()}
              onClick={submit}
            >
              Add
            </Button>
          </div>
        </div>
      </Popover.Content>
    </Popover>
  );
}

/** A link under the pointer: its mark's doc range, current text + href, and the
 * live <a> element the card anchors to. Mirrors `linkHover.ts`'s own shape. */
export interface HoveredLink {
  from: number;
  to: number;
  href: string;
  text: string;
  el: HTMLElement;
}

export interface LinkHoverCardProps {
  link: HoveredLink | null;
  onApply: (from: number, to: number, href: string, text: string) => void;
  onUnlink: (from: number, to: number) => void;
  onOpen: (href: string) => void;
  /** Pointer entered/left the card — the view cancels/schedules its hide. */
  onPointerEnter: () => void;
  onPointerLeave: () => void;
  /** A field gained focus — pin the card open past a pointer leave. */
  onPin: () => void;
  onClose: () => void;
}

/** The link hover editor (see `linkHover.ts`). Anchored to the hovered <a>
 * itself, it shows the href and lets you edit the text + URL, unlink, or open
 * in a new tab. For a promoted link (text == href) the text field starts empty
 * to invite a label. It opens on hover, so it must NOT steal focus — the
 * pointer-enter/leave + pin callbacks keep it alive while you interact. */
export function LinkHoverCard({
  link,
  onApply,
  onUnlink,
  onOpen,
  onPointerEnter,
  onPointerLeave,
  onPin,
  onClose,
}: LinkHoverCardProps) {
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");

  // Re-seed the fields whenever a different link is hovered. A promoted link
  // (text == href) shows an empty label field rather than the raw URL twice.
  useEffect(() => {
    if (!link) return;
    setUrl(link.href);
    setText(link.text === link.href ? "" : link.text);
  }, [link]);

  const virtualRef = useMemo(
    () => (link ? { current: link.el } : null),
    [link],
  );

  const apply = () => {
    if (!link) return;
    const href = normalizeUrl(url);
    if (!href) return;
    onApply(link.from, link.to, href, text.trim() || href);
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      apply();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <Popover
      open={link != null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      {virtualRef ? <Popover.Anchor virtualRef={virtualRef} /> : null}
      <Popover.Content
        align="start"
        sideOffset={6}
        width="fit"
        // Appears on hover — never yank focus from the doc; the user clicks a
        // field to edit, and `onPin` keeps it open once they do.
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
        onPointerEnter={onPointerEnter}
        onPointerLeave={onPointerLeave}
      >
        <div className="flex w-[300px] flex-col gap-3 p-1">
          <div className="flex flex-col gap-1.5">
            <Text font="main-ui-muted" color="text-03">
              Text
            </Text>
            <InputTypeIn
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKeyDown}
              onFocus={onPin}
              placeholder="Add a label…"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Text font="main-ui-muted" color="text-03">
              URL
            </Text>
            <InputTypeIn
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={onKeyDown}
              onFocus={onPin}
              placeholder="https://…"
            />
          </div>
          <div className="flex items-center justify-between gap-2">
            <div className="flex gap-1">
              <Button
                type="button"
                prominence="secondary"
                icon={SvgExternalLink}
                tooltip="Open in new tab"
                disabled={!link?.href}
                onClick={() => link && onOpen(link.href)}
              />
              <Button
                type="button"
                prominence="secondary"
                icon={SvgTrash}
                tooltip="Remove link"
                onClick={() => link && onUnlink(link.from, link.to)}
              />
            </div>
            <Button
              type="button"
              variant="action"
              disabled={!url.trim()}
              onClick={apply}
            >
              Save
            </Button>
          </div>
        </div>
      </Popover.Content>
    </Popover>
  );
}

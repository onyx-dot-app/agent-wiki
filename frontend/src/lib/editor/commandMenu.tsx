"use client";

/** Slash command menu — typing `/` at the start of a block offers a list of
 * block types to insert, filtered as you keep typing, navigable with
 * arrow keys + Enter, dismissed with Escape. Built on `@tiptap/suggestion`,
 * the same utility `@tiptap/extension-mention`-style features use — not a
 * hand-rolled popup; positioning is Floating UI via `SuggestionProps.mount`,
 * confirmed against the installed package's own documented API.
 *
 * Table isn't offered here: there's no "insert a blank table" flow (the
 * backend's opaque-row table shape has no per-cell editing to seed —
 * see blocks.ts), so a command would have nothing sensible to insert.
 * `html_block`/`other` aren't offered either — they exist to represent
 * pre-existing opaque content synced in from the backend, not something a
 * user should hand-author.
 */
import { Extension } from "@tiptap/core";
import type { Editor, Range } from "@tiptap/core";
import { ReactRenderer } from "@tiptap/react";
import Suggestion, {
  type SuggestionKeyDownProps,
  type SuggestionProps,
} from "@tiptap/suggestion";
import { useEffect, useImperativeHandle, useState, type Ref } from "react";
import { LineItemButton } from "@onyx-ai/opal/components";
import {
  SvgCheckSquare,
  SvgCode,
  SvgHash,
  SvgImage,
  SvgListTree,
  SvgMinus,
  SvgQuoteStart,
  SvgTextLines,
} from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

import { canUploadImages, promptImageUpload } from "@/lib/editor/images";

interface CommandItem {
  title: string;
  icon: IconFunctionComponent;
  run: (editor: Editor, range: Range) => void;
  /** Omitted means always offered. */
  available?: (editor: Editor) => boolean;
}

const COMMANDS: CommandItem[] = [
  {
    title: "Text",
    icon: SvgTextLines,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).setNode("paragraph").run(),
  },
  {
    title: "Heading 1",
    icon: SvgHash,
    run: (editor, range) =>
      editor
        .chain()
        .focus()
        .deleteRange(range)
        .setNode("heading", { level: 1 })
        .run(),
  },
  {
    title: "Heading 2",
    icon: SvgHash,
    run: (editor, range) =>
      editor
        .chain()
        .focus()
        .deleteRange(range)
        .setNode("heading", { level: 2 })
        .run(),
  },
  {
    title: "Heading 3",
    icon: SvgHash,
    run: (editor, range) =>
      editor
        .chain()
        .focus()
        .deleteRange(range)
        .setNode("heading", { level: 3 })
        .run(),
  },
  {
    title: "Bullet List",
    icon: SvgListTree,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).toggleBulletList().run(),
  },
  {
    title: "Numbered List",
    icon: SvgListTree,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).toggleOrderedList().run(),
  },
  {
    title: "Task List",
    icon: SvgCheckSquare,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).toggleTaskList().run(),
  },
  {
    title: "Blockquote",
    icon: SvgQuoteStart,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).toggleBlockquote().run(),
  },
  {
    title: "Code Block",
    icon: SvgCode,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).toggleCodeBlock().run(),
  },
  {
    title: "Divider",
    icon: SvgMinus,
    run: (editor, range) => {
      const node = editor.schema.nodes.thematic_break!.create(
        // _raw: "1" matches what the backend stamps on every opaque block
        // (see blocks.ts's own Enter-conversion path) — serialize_block's
        // opaque-block fallback requires this exact attr, so a divider
        // created without it fails every checkpoint from here on with
        // NotImplementedError, permanently stranding edits in the update log.
        { _raw: "1" },
        editor.schema.text("---\n"),
      );
      editor
        .chain()
        .focus()
        .deleteRange(range)
        .insertContent(node.toJSON())
        .run();
    },
  },
  {
    title: "Image",
    icon: SvgImage,
    // A view with no page path cannot upload, so the picker would discard the
    // file it collected.
    available: (editor) => canUploadImages(editor.view),
    run: (editor, range) => {
      // Close the menu first: the OS dialog steals focus, and the leftover
      // "/image" text would otherwise survive in the doc behind it.
      editor.chain().focus().deleteRange(range).run();
      promptImageUpload(editor.view);
    },
  },
];

function filterCommands(query: string, editor: Editor): CommandItem[] {
  const available = COMMANDS.filter((c) => c.available?.(editor) ?? true);
  const q = query.trim().toLowerCase();
  if (!q) return available;
  return available.filter((c) => c.title.toLowerCase().includes(q));
}

interface CommandListHandle {
  onKeyDown: (props: SuggestionKeyDownProps) => boolean;
}

function CommandList({
  items,
  command,
  ref,
}: SuggestionProps<CommandItem> & { ref?: Ref<CommandListHandle> }) {
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

export const CommandMenu = Extension.create({
  name: "commandMenu",

  addProseMirrorPlugins() {
    return [
      Suggestion<CommandItem>({
        editor: this.editor,
        char: "/",
        // Only at the start of a (empty) block — the Notion-style
        // convention, and it keeps a literal "/" in running prose (a file
        // path, a fraction) from ever triggering the menu.
        startOfLine: true,
        allowedPrefixes: null,
        // Unsliced: the list is already short and the menu scrolls, and a cap
        // silently drops whichever command sorts last.
        items: ({ query, editor }) => filterCommands(query, editor),
        command: ({ editor, range, props }) => props.run(editor, range),
        render: () => {
          let component: ReactRenderer<
            CommandListHandle,
            SuggestionProps<CommandItem>
          >;
          let unmount: (() => void) | undefined;
          return {
            onStart: (props) => {
              component = new ReactRenderer(CommandList, {
                props,
                editor: props.editor,
              });
              if (!props.clientRect) return;
              unmount = props.mount(component.element);
            },
            onUpdate: (props) => {
              component.updateProps(props);
            },
            onKeyDown: (props) => {
              if (props.event.key === "Escape") {
                unmount?.();
                component.destroy();
                return true;
              }
              return component.ref?.onKeyDown(props) ?? false;
            },
            onExit: () => {
              unmount?.();
              component.destroy();
            },
          };
        },
      }),
    ];
  },
});

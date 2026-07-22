"use client";

/** Slash-command menu: type "/" at the start of a line to insert a block —
 * the open-source equivalent of what Tiptap's paid "Notion-like" template
 * ships (that template requires a commercial subscription; this is a
 * from-scratch build on the free `@tiptap/suggestion` primitive, following
 * its documented recipe).
 */

import { Extension, type Editor, type Range } from "@tiptap/core";
import { ReactRenderer } from "@tiptap/react";
import Suggestion, { type SuggestionProps } from "@tiptap/suggestion";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
} from "react";
import type { ComponentType } from "react";
import {
  SvgCode,
  SvgHash,
  SvgQuoteStart,
  SvgTextLines,
} from "@onyx-ai/opal/icons";

interface SlashCommandItem {
  title: string;
  description: string;
  icon: ComponentType<{ className?: string }>;
  run: (editor: Editor, range: Range) => void;
}

const SLASH_COMMANDS: SlashCommandItem[] = [
  {
    title: "Heading 1",
    description: "Big section heading",
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
    description: "Medium section heading",
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
    description: "Small section heading",
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
    title: "Bullet list",
    description: "A simple bulleted list",
    icon: SvgTextLines,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).toggleBulletList().run(),
  },
  {
    title: "Numbered list",
    description: "A list with numbering",
    icon: SvgTextLines,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).toggleOrderedList().run(),
  },
  {
    title: "Quote",
    description: "Capture a quote",
    icon: SvgQuoteStart,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).toggleBlockquote().run(),
  },
  {
    title: "Code block",
    description: "A block for code with monospaced font",
    icon: SvgCode,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).toggleCodeBlock().run(),
  },
];

interface SlashMenuListProps {
  items: SlashCommandItem[];
  command: (item: SlashCommandItem) => void;
}

export interface SlashMenuListHandle {
  onKeyDown: (props: { event: KeyboardEvent }) => boolean;
}

const SlashMenuList = forwardRef<SlashMenuListHandle, SlashMenuListProps>(
  function SlashMenuList({ items, command }, ref) {
    const [selected, setSelected] = useState(0);

    useEffect(() => setSelected(0), [items]);

    useImperativeHandle(ref, () => ({
      onKeyDown: ({ event }) => {
        if (items.length === 0) return false;
        if (event.key === "ArrowDown") {
          setSelected((i) => (i + 1) % items.length);
          return true;
        }
        if (event.key === "ArrowUp") {
          setSelected((i) => (i - 1 + items.length) % items.length);
          return true;
        }
        if (event.key === "Enter") {
          const item = items[selected];
          if (item) command(item);
          return true;
        }
        return false;
      },
    }));

    if (items.length === 0) {
      return null;
    }

    return (
      <div className="flex max-h-80 w-64 flex-col gap-0.5 overflow-y-auto rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-01) p-1 shadow-(--shadow-popover)">
        {items.map((item, i) => (
          <button
            key={item.title}
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => command(item)}
            className={`flex items-center gap-2 rounded-(--radius-04) px-2 py-1.5 text-left text-sm ${
              i === selected
                ? "bg-(--background-tint-03) text-(--text-05)"
                : "text-(--text-04) hover:bg-(--background-tint-02)"
            }`}
          >
            <item.icon className="size-4 shrink-0" />
            <span className="flex flex-col">
              <span className="text-(--text-05)">{item.title}</span>
              <span className="text-xs text-(--text-03)">
                {item.description}
              </span>
            </span>
          </button>
        ))}
      </div>
    );
  },
);

export const SlashCommand = Extension.create({
  name: "slashCommand",

  addProseMirrorPlugins() {
    return [
      Suggestion<SlashCommandItem>({
        editor: this.editor,
        char: "/",
        startOfLine: false,
        allowedPrefixes: null,
        items: ({ query }) =>
          SLASH_COMMANDS.filter((c) =>
            c.title.toLowerCase().includes(query.toLowerCase()),
          ),
        command: ({ editor, range, props }) => {
          props.run(editor, range);
        },
        render: () => {
          let renderer: ReactRenderer<SlashMenuListHandle, SlashMenuListProps>;
          let unmount: (() => void) | null = null;

          return {
            onStart: (props: SuggestionProps<SlashCommandItem>) => {
              renderer = new ReactRenderer(SlashMenuList, {
                props: { items: props.items, command: props.command },
                editor: props.editor,
              });
              unmount = props.mount(renderer.element, {});
            },
            onUpdate(props: SuggestionProps<SlashCommandItem>) {
              renderer.updateProps({
                items: props.items,
                command: props.command,
              });
            },
            onKeyDown(props) {
              if (props.event.key === "Escape") {
                unmount?.();
                return true;
              }
              return renderer.ref?.onKeyDown(props) ?? false;
            },
            onExit() {
              unmount?.();
              renderer.destroy();
            },
          };
        },
      }),
    ];
  },
});

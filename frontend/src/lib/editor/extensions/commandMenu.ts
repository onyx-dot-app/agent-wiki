"use client";

/** Slash command menu — typing `/` at the start of a block offers a list of
 * block types to insert, filtered as you keep typing, navigable with
 * arrow keys + Enter, dismissed with Escape. Built on `@tiptap/suggestion`,
 * the same utility `@tiptap/extension-mention`-style features use — not a
 * hand-rolled popup; positioning is Floating UI via `SuggestionProps.mount`,
 * confirmed against the installed package's own documented API.
 *
 * The menu UI (`CommandList`), its command set (`filterCommands`/`COMMANDS`)
 * and their types live in `components.tsx`; this module is only the Tiptap
 * `Suggestion` wiring that drives them.
 *
 * Table isn't offered here: there's no "insert a blank table" flow (the
 * backend's opaque-row table shape has no per-cell editing to seed —
 * see blocks.ts), so a command would have nothing sensible to insert.
 * `html_block`/`other` aren't offered either — they exist to represent
 * pre-existing opaque content synced in from the backend, not something a
 * user should hand-author.
 */
import { Extension } from "@tiptap/core";
import { ReactRenderer } from "@tiptap/react";
import Suggestion, { type SuggestionProps } from "@tiptap/suggestion";
import {
  CommandList,
  filterCommands,
  type CommandItem,
  type CommandListHandle,
} from "@/lib/editor/components";

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

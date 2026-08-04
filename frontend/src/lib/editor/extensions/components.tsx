"use client";

/** The slash-command menu's UI — the React component the `commandMenu`
 * extension renders through `@tiptap/react`'s `ReactRenderer`. It lives here,
 * beside its extension, rather than in the editor's shared `components.tsx`:
 * it's an implementation detail of the slash command (typed by `CommandItem`,
 * rendered only by that extension's `Suggestion` plugin), not a shared shell
 * component. Keeping it in `extensions/` is also what stops `extensions/` from
 * importing back into the editor shell — the edge that closed a circular
 * dependency (`components.tsx → extensions/index → extensions/commandMenu →
 * components.tsx`). */
import { useEffect, useImperativeHandle, useState, type Ref } from "react";
import type {
  SuggestionKeyDownProps,
  SuggestionProps,
} from "@tiptap/suggestion";
import { LineItemButton } from "@onyx-ai/opal/components";
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

"use client";

/** Floating formatting toolbar shown above a text selection — the open-
 * source equivalent of what Tiptap's paid "Notion-like" template ships
 * (that template itself isn't available without a commercial Tiptap
 * subscription; this is a from-scratch build against the free
 * `@tiptap/react/menus` `BubbleMenu`). Opal has no bold/italic glyphs in
 * its icon set (checked — only `SvgCode`/`SvgLink` exist among formatting-
 * adjacent icons), so bold/italic use plain styled "B"/"I" glyphs — the
 * "unusual one-off, pull colors from Opal tokens" case AGENTS.md's button
 * rules call out.
 */

import { InputTypeIn } from "@onyx-ai/opal/components";
import { SvgCode, SvgLink } from "@onyx-ai/opal/icons";
import type { Editor } from "@tiptap/react";
import { BubbleMenu } from "@tiptap/react/menus";
import { useState, type ReactNode } from "react";

interface BubbleToolbarProps {
  editor: Editor;
}

interface ToolbarButtonProps {
  active: boolean;
  onClick: () => void;
  label: string;
  children: ReactNode;
}

function ToolbarButton({
  active,
  onClick,
  label,
  children,
}: ToolbarButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={`flex size-7 items-center justify-center rounded-(--radius-04) text-sm ${
        active
          ? "bg-(--background-tint-03) text-(--text-05)"
          : "text-(--text-04) hover:bg-(--background-tint-01) hover:text-(--text-05)"
      }`}
    >
      {children}
    </button>
  );
}

export function BubbleToolbar({ editor }: BubbleToolbarProps) {
  const [linkEditing, setLinkEditing] = useState(false);
  const [linkValue, setLinkValue] = useState("");

  return (
    <BubbleMenu
      editor={editor}
      shouldShow={({ state }) => !state.selection.empty}
      options={{ onHide: () => setLinkEditing(false) }}
      className="flex items-center gap-0.5 rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-01) p-1 shadow-(--shadow-popover)"
    >
      {linkEditing ? (
        <div className="flex items-center gap-1 p-0.5">
          <InputTypeIn
            autoFocus
            value={linkValue}
            onChange={(e) => setLinkValue(e.target.value)}
            placeholder="https://…"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                const href = linkValue.trim();
                if (href) {
                  editor.chain().focus().setLink({ href }).run();
                } else {
                  editor.chain().focus().unsetLink().run();
                }
                setLinkEditing(false);
              } else if (e.key === "Escape") {
                setLinkEditing(false);
              }
            }}
          />
        </div>
      ) : (
        <>
          <ToolbarButton
            label="Bold"
            active={editor.isActive("bold")}
            onClick={() => editor.chain().focus().toggleBold().run()}
          >
            <span className="font-bold">B</span>
          </ToolbarButton>
          <ToolbarButton
            label="Italic"
            active={editor.isActive("italic")}
            onClick={() => editor.chain().focus().toggleItalic().run()}
          >
            <span className="italic">I</span>
          </ToolbarButton>
          <ToolbarButton
            label="Code"
            active={editor.isActive("code")}
            onClick={() => editor.chain().focus().toggleCode().run()}
          >
            <SvgCode className="size-4" />
          </ToolbarButton>
          <ToolbarButton
            label="Link"
            active={editor.isActive("link")}
            onClick={() => {
              setLinkValue((editor.getAttributes("link").href as string) ?? "");
              setLinkEditing(true);
            }}
          >
            <SvgLink className="size-4" />
          </ToolbarButton>
        </>
      )}
    </BubbleMenu>
  );
}

"use client";

import { useState, type SVGProps } from "react";
import {
  Button,
  Divider,
  InputTypeIn,
  LineItemButton,
  Popover,
} from "@onyx-ai/opal/components";
import {
  SvgBubbleText,
  SvgCheck,
  SvgChevronRight,
  SvgCode,
  SvgLink,
} from "@onyx-ai/opal/icons";
import type {
  BlockStyle,
  SelectionFormatState,
  ToggleMark,
} from "@/lib/editor/types";

const BLOCK_LABELS: Record<BlockStyle, string> = {
  paragraph: "Normal text",
  h1: "Heading 1",
  h2: "Heading 2",
  h3: "Heading 3",
  bulletList: "Bullet list",
  orderedList: "Numbered list",
  taskList: "To-do list",
};

const BLOCK_ORDER: BlockStyle[] = [
  "paragraph",
  "h1",
  "h2",
  "h3",
  "bulletList",
  "orderedList",
  "taskList",
];

/** The glyphs are typography samples of their own effect (a bold B, an
 * italic I, a struck S), matching the design mock — Opal has no
 * text-formatting icons, so these render as svg text through Button's
 * regular `icon` slot and inherit its states via currentColor. */
function GlyphBold(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" {...props}>
      <text
        x="8"
        y="12.5"
        textAnchor="middle"
        fontSize="13"
        fontWeight="700"
        fill="currentColor"
      >
        B
      </text>
    </svg>
  );
}

function GlyphItalic(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" {...props}>
      <text
        x="8"
        y="12.5"
        textAnchor="middle"
        fontSize="13"
        fontStyle="italic"
        fill="currentColor"
      >
        I
      </text>
    </svg>
  );
}

function GlyphStrike(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" {...props}>
      <text
        x="8"
        y="12.5"
        textAnchor="middle"
        fontSize="13"
        textDecoration="line-through"
        fill="currentColor"
      >
        S
      </text>
    </svg>
  );
}

const MARK_GLYPHS: Array<{
  mark: ToggleMark;
  tooltip: string;
  icon: (props: SVGProps<SVGSVGElement>) => React.JSX.Element;
}> = [
  { mark: "bold", tooltip: "Bold", icon: GlyphBold },
  { mark: "italic", tooltip: "Italic", icon: GlyphItalic },
  { mark: "strike", tooltip: "Strikethrough", icon: GlyphStrike },
];

interface SelectionToolbarProps {
  state: SelectionFormatState;
  onToggleMark: (mark: ToggleMark) => void;
  onSetBlock: (style: BlockStyle) => void;
  onSetLink: (href: string) => void;
  onComment: () => void;
}

/** Floating formatting menu for a non-collapsed editor selection: block
 * style switcher, inline marks, link, and Add Comment. The host owns
 * positioning (anchored above the selection, same spot the plain Comment
 * pill used) and re-renders it with a fresh `state` snapshot after every
 * action. */
export function SelectionToolbar({
  state,
  onToggleMark,
  onSetBlock,
  onSetLink,
  onComment,
}: SelectionToolbarProps) {
  const [styleOpen, setStyleOpen] = useState(false);
  const [linkDraft, setLinkDraft] = useState<string | null>(null);

  const submitLink = () => {
    if (linkDraft !== null && linkDraft.trim()) onSetLink(linkDraft.trim());
    setLinkDraft(null);
  };

  return (
    <div className="flex w-48 flex-col">
      <Popover open={styleOpen} onOpenChange={setStyleOpen}>
        <Popover.Trigger asChild>
          <span className="inline-flex w-full">
            <LineItemButton
              title={BLOCK_LABELS[state.block]}
              rightChildren={
                <SvgChevronRight className="h-4 w-4 self-center text-(--text-03)" />
              }
              sizePreset="main-ui"
              variant="section"
              onClick={() => setStyleOpen((v) => !v)}
            />
          </span>
        </Popover.Trigger>
        <Popover.Content width="fit" side="right" align="start" sideOffset={4}>
          <Popover.Menu>
            {BLOCK_ORDER.map((style) => (
              <LineItemButton
                key={style}
                title={BLOCK_LABELS[style]}
                icon={state.block === style ? SvgCheck : undefined}
                sizePreset="main-ui"
                variant="section"
                onClick={() => {
                  setStyleOpen(false);
                  onSetBlock(style);
                }}
              />
            ))}
          </Popover.Menu>
        </Popover.Content>
      </Popover>
      <div className="flex items-center gap-0.5 px-1 py-0.5">
        {MARK_GLYPHS.map(({ mark, tooltip, icon }) => (
          <Button
            key={mark}
            icon={icon}
            prominence={state.marks[mark] ? "secondary" : "tertiary"}
            size="md"
            tooltip={tooltip}
            onClick={() => onToggleMark(mark)}
          />
        ))}
        <Button
          icon={SvgCode}
          prominence={state.marks.code ? "secondary" : "tertiary"}
          size="md"
          tooltip="Code"
          onClick={() => onToggleMark("code")}
        />
        <Divider orientation="vertical" paddingParallel="xs" />
        <Button
          icon={SvgLink}
          prominence={state.link !== null ? "secondary" : "tertiary"}
          size="md"
          tooltip={state.link !== null ? "Remove link" : "Link"}
          onClick={() => {
            if (state.link !== null) onSetLink("");
            else setLinkDraft((v) => (v === null ? "" : null));
          }}
        />
      </div>
      {linkDraft !== null && (
        <div className="px-1 pb-1">
          <InputTypeIn
            autoFocus
            value={linkDraft}
            placeholder="https://…"
            onChange={(e) => setLinkDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitLink();
              } else if (e.key === "Escape") {
                setLinkDraft(null);
              }
            }}
          />
        </div>
      )}
      <Divider paddingParallel="sm" paddingPerpendicular="xs" />
      <LineItemButton
        title="Add Comment"
        icon={SvgBubbleText}
        sizePreset="main-ui"
        variant="section"
        onClick={onComment}
      />
    </div>
  );
}

"use client";

import { useEffect, useState, type KeyboardEvent, type Ref } from "react";
import { Button, InputTextArea, Text, Tooltip } from "@onyx-ai/opal/components";
import {
  SvgArrowUp,
  SvgBubbleText,
  SvgEditBig,
  SvgPlus,
  SvgSidebar,
  SvgX,
  SvgZap,
} from "@onyx-ai/opal/icons";
import {
  SvgAnthropic,
  SvgAws,
  SvgGemini,
  SvgOllama,
  SvgOpenai,
} from "@onyx-ai/opal/logos";
import { useAppFocus } from "@/hooks/useAppFocus";
import { useLLMStatus } from "@/lib/llm";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

/** Horizontal center of the doc column, so the bar floats over the content
 *  area rather than the viewport (side panels and the tree shift it). Null
 *  until measured, or when no content column exists. */
function useContentCenterX(pathKey: string | null): number | null {
  const [x, setX] = useState<number | null>(null);
  useEffect(() => {
    const el = document.querySelector("main");
    const measure = () => {
      if (!el) {
        setX(null);
        return;
      }
      const r = el.getBoundingClientRect();
      setX(r.left + r.width / 2);
    };
    measure();
    const ro = el ? new ResizeObserver(measure) : null;
    if (el && ro) ro.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", measure);
    };
    // pathKey re-attaches the observer when navigation swaps the main element.
  }, [pathKey]);
  return x;
}

const PROVIDER_LOGOS: Record<string, IconFunctionComponent> = {
  anthropic: SvgAnthropic,
  openai: SvgOpenai,
  gemini: SvgGemini,
  ollama: SvgOllama,
  bedrock: SvgAws,
};

/** Segmented Chat | Edit | Automations selector (mock 1829:64849): the
 *  active segment reads as a white card, the rest are icon-only. Chat is
 *  the only live mode today, so the other segments render disabled. */
export function ModeSelector() {
  return (
    <div className="flex items-center rounded-(--radius-12) bg-(--background-tint-03)">
      <Button icon={SvgBubbleText} prominence="secondary">
        Chat
      </Button>
      <Button
        icon={SvgEditBig}
        prominence="internal"
        tooltip="Edit mode coming soon"
        disabled
      />
      <Button
        icon={SvgZap}
        prominence="internal"
        tooltip="Automations coming soon"
        disabled
      />
    </div>
  );
}

/** Read-only chip naming the workspace-configured provider + model. */
export function ModelChip() {
  const { status } = useLLMStatus();
  if (!status?.model) return null;
  const Logo = PROVIDER_LOGOS[status.provider];
  return (
    <Tooltip tooltip="Model is set in Admin, Language Models" side="top">
      <div className="flex items-center gap-1 rounded-(--radius-12) bg-(--background-tint-00) p-2 shadow-(--shadow-chip)">
        {Logo && <Logo size={20} />}
        <Text font="main-ui-action" color="text-04">
          {status.model}
        </Text>
      </div>
    </Tooltip>
  );
}

interface ComposerProps {
  input: string;
  onInputChange: (v: string) => void;
  onSubmit: () => void;
  sending: boolean;
  /** Forwarded to the textarea so hosts can programmatically focus it. */
  inputRef?: Ref<HTMLTextAreaElement>;
}

/** Shared composer surface (mock 1790:52456): text area over an attach +
 *  send toolbar. Enter sends, Shift+Enter breaks the line. */
export function Composer({
  input,
  onInputChange,
  onSubmit,
  sending,
  inputRef,
}: ComposerProps) {
  const canSend = input.trim() !== "" && !sending;
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) onSubmit();
    }
  };
  return (
    <div className="flex w-full flex-col rounded-(--radius-16) bg-(--background-tint-00) shadow-(--shadow-chip)">
      {/* The internal variant is the borderless field, so the radius-16
          surface above stays the only chrome. */}
      <InputTextArea
        ref={inputRef}
        variant="internal"
        rows={1}
        autoResize
        maxRows={6}
        value={input}
        onChange={(e) => onInputChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask wiki or write with AI…"
      />
      <div className="flex items-center justify-between p-1">
        <Button
          icon={SvgPlus}
          prominence="tertiary"
          size="sm"
          tooltip="Attach files coming soon"
          disabled
        />
        <Button
          icon={SvgArrowUp}
          variant="action"
          size="sm"
          tooltip="Send"
          onClick={onSubmit}
          disabled={!canSend}
        />
      </div>
    </div>
  );
}

interface ChatBarProps {
  collapsed: boolean;
  onExpand: () => void;
  onCollapse: () => void;
  onDock: () => void;
  input: string;
  onInputChange: (v: string) => void;
  onSubmit: () => void;
  sending: boolean;
}

/** Bottom-center chat bar (mock 1790:52445): mode selector + dock/collapse
 *  controls + model chip over a composer box. Collapsed it shrinks to the
 *  three-icon mini pill (mock 1856:283736). The conversation itself lives in
 *  the docked chat panel. This bar only starts or continues it. */
export function ChatBar({
  collapsed,
  onExpand,
  onCollapse,
  onDock,
  input,
  onInputChange,
  onSubmit,
  sending,
}: ChatBarProps) {
  const { wikiPath } = useAppFocus();
  const centerX = useContentCenterX(wikiPath);
  const centerStyle = centerX !== null ? { left: centerX } : undefined;
  if (collapsed) {
    return (
      <div
        className="fixed bottom-6 left-1/2 z-[1000] -translate-x-1/2 rounded-(--radius-round) border border-(--border-01) bg-(--background-tint-01) p-1 shadow-(--shadow-bar)"
        style={centerStyle}
      >
        <div className="flex items-center gap-1">
          <Button
            icon={SvgBubbleText}
            prominence="tertiary"
            size="sm"
            tooltip="Chat"
            onClick={onExpand}
          />
          <Button
            icon={SvgEditBig}
            prominence="tertiary"
            size="sm"
            tooltip="Edit mode coming soon"
            disabled
          />
          <Button
            icon={SvgZap}
            prominence="tertiary"
            size="sm"
            tooltip="Automations coming soon"
            disabled
          />
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed bottom-6 left-1/2 z-[1000] w-[min(752px,calc(100vw-48px))] -translate-x-1/2"
      style={centerStyle}
    >
      <div className="flex flex-col gap-1 rounded-(--radius-20) border border-(--border-01) bg-(--background-tint-01) p-1 shadow-(--shadow-bar)">
        <div className="flex items-center">
          <div className="p-1">
            <ModeSelector />
          </div>
          <div className="mx-1 h-4 w-px bg-(--border-01)" />
          <Button
            icon={SvgSidebar}
            prominence="tertiary"
            size="sm"
            tooltip="Open chat panel"
            onClick={onDock}
          />
          <Button
            icon={SvgX}
            prominence="tertiary"
            size="sm"
            tooltip="Collapse"
            onClick={onCollapse}
          />
          <div className="min-w-2 flex-1" />
          <div className="p-1">
            <ModelChip />
          </div>
        </div>
        <Composer
          input={input}
          onInputChange={onInputChange}
          onSubmit={onSubmit}
          sending={sending}
        />
      </div>
    </div>
  );
}

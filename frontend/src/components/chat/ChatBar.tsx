"use client";

import { type FormEvent, type KeyboardEvent, type Ref } from "react";
import { Button, Text } from "@onyx-ai/opal/components";
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
import { useLLMStatus } from "@/lib/llm";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

const PROVIDER_LOGOS: Record<string, IconFunctionComponent> = {
  anthropic: SvgAnthropic,
  openai: SvgOpenai,
  gemini: SvgGemini,
  ollama: SvgOllama,
  bedrock: SvgAws,
};

/** Segmented Chat | Edit | Automations selector (mock 1829:64849). Chat is
 *  the only live mode today, so the other segments render disabled. */
export function ModeSelector() {
  return (
    <div className="flex items-center rounded-(--radius-12) bg-(--background-tint-03)">
      {/* raw-ok: no Opal Tabs variant fits. Contained is an equal-width grid, pill/underline are underline-indicator styles. The mock needs chip-style content-width segments (icon+label active, icon-only inactive). */}
      <button
        type="button"
        className="flex items-center gap-1 rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-00) p-2 shadow-(--shadow-chip)"
      >
        <SvgBubbleText size={20} className="text-(--text-04)" />
        <Text font="main-ui-action" color="text-04">
          Chat
        </Text>
      </button>
      {/* raw-ok: same segmented-control gap as above. */}
      <button
        type="button"
        disabled
        title="Edit mode coming soon"
        className="flex items-center rounded-(--radius-12) p-2 text-(--text-03)"
      >
        <SvgEditBig size={20} />
      </button>
      {/* raw-ok: same segmented-control gap as above. */}
      <button
        type="button"
        disabled
        title="Automations coming soon"
        className="flex items-center rounded-(--radius-12) p-2 text-(--text-03)"
      >
        <SvgZap size={20} />
      </button>
    </div>
  );
}

/** Read-only chip naming the workspace-configured provider + model. */
export function ModelChip() {
  const { status } = useLLMStatus();
  if (!status?.model) return null;
  const Logo = PROVIDER_LOGOS[status.provider];
  return (
    <div
      title="Model is set in Admin, Language Models"
      className="flex items-center gap-1 rounded-(--radius-12) bg-(--background-tint-00) p-2 shadow-(--shadow-chip)"
    >
      {Logo && <Logo size={20} />}
      <Text font="main-ui-action" color="text-04">
        {status.model}
      </Text>
    </div>
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
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (canSend) onSubmit();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) onSubmit();
    }
  };
  // raw-ok: plain form element wiring Enter/submit. No Opal form wrapper exists.
  return (
    <form
      onSubmit={submit}
      className="flex w-full flex-col rounded-(--radius-16) bg-(--background-tint-00) shadow-(--shadow-chip)"
    >
      {/* raw-ok: composer needs a toolbar row below the text inside one surface. InputTextArea offers only a top-right rightSection and paints .opal-input chrome that fights the mock's radius-16 chip. */}
      <textarea
        ref={inputRef}
        rows={1}
        value={input}
        onChange={(e) => onInputChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask wiki or write with AI…"
        className="w-full resize-none border-none bg-transparent px-3.5 pt-3 pb-2 text-base leading-6 text-(--text-05) outline-none placeholder:text-(--text-02)"
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
          type="submit"
          disabled={!canSend}
        />
      </div>
    </form>
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
  if (collapsed) {
    return (
      <div className="fixed bottom-6 left-1/2 z-[1000] -translate-x-1/2 rounded-(--radius-round) border border-(--border-01) bg-(--background-tint-01) p-1 shadow-(--shadow-bar)">
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
    <div className="fixed bottom-6 left-1/2 z-[1000] w-[min(752px,calc(100vw-48px))] -translate-x-1/2">
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

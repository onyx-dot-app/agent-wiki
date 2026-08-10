"use client";

// Shared visual pieces for wiki toolbar chrome, modes, responses, and input.
// Layout and styling follow the toolbar mock tokens.
import { useCallback, useEffect, useRef, useState } from "react";

import {
  Button,
  CopyButton,
  Divider,
  InputTypeIn,
  Popover,
  LineItemButton,
  OpenButton,
  Tabs,
  Tag,
  Text,
} from "@onyx-ai/opal/components";
import { Section, TagList } from "@onyx-ai/opal/layouts";
import { cn } from "@onyx-ai/opal/utils";
import {
  SvgArrowUp,
  SvgBubbleText,
  SvgFileText,
  SvgFold,
  SvgFolder,
  SvgPlus,
  SvgRefreshCw,
  SvgSearch,
  SvgSidebar,
  SvgSquare,
  SvgThumbsDown,
  SvgThumbsUp,
  SvgWorkflow,
  SvgX,
  SvgZap,
} from "@onyx-ai/opal/icons";
import ModelSelectorContent from "@/components/wiki/toolbar/ModelSelectorContent";
import {
  type LLMOption,
  buildLlmOptions,
  displayModelName,
  getModelIcon,
  useLlmAvailable,
} from "@/lib/llmOptions";
import type { ChatFeedback } from "@/lib/chat";
import { toast } from "@/hooks/useToast";
import { updateUserSettings, useUserSettings } from "@/lib/userSettings";
import { useWikiTree } from "@/lib/wiki/hooks";

/** Publishes an element's measured size as a root CSS property, so surfaces
 *  that cannot see this component's state still reserve room for it. */
export function usePublishedSize(
  property: string,
  axis: "height" | "width",
  active = true,
) {
  const ref = useRef<HTMLDivElement>(null);
  // Returned so a caller can republish before the observer's first delivery,
  // which lands after the strip has already painted at a stale size.
  const publish = useCallback(() => {
    const el = ref.current;
    if (!active || !el) return;
    const px = axis === "height" ? el.offsetHeight : el.offsetWidth;
    document.documentElement.style.setProperty(property, `${px}px`);
  }, [property, axis, active]);
  useEffect(() => {
    const el = ref.current;
    if (!active || !el) return;
    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    return () => {
      ro.disconnect();
      document.documentElement.style.removeProperty(property);
    };
  }, [property, active, publish]);
  return { ref, publish };
}

export type ToolbarMode = "chat" | "watch" | "launch";

export interface ToolbarContext {
  path: string;
  kind: "doc" | "dir";
  startLine?: number;
  endLine?: number;
}

const MODE_ICONS = {
  chat: SvgBubbleText,
  watch: SvgWorkflow,
  launch: SvgZap,
} as const;

const MODE_LABELS: Record<ToolbarMode, string> = {
  chat: "Chat",
  watch: "Watch",
  launch: "Launch",
};

/** Panel chrome: tint-01 surface, border-01, the toolbar shadow pair.
 *  Watch and Launch run flush: their rows pad themselves so the footer
 *  band reaches the panel edge (mock 2348:387908). */
interface ToolbarPanelProps {
  folded?: boolean;
  flush?: boolean;
  children: React.ReactNode;
}

export function ToolbarPanel({ folded, flush, children }: ToolbarPanelProps) {
  return (
    <Section
      gap={flush ? 0 : 0.25}
      padding={folded || flush ? 0 : 0.25}
      height="fit"
      width={folded ? "fit" : "full"}
      alignItems="start"
      className="my-1 overflow-hidden rounded-12 bg-background-tint-01 shadow-[inset_0_0_0_1px_var(--border-01),0px_2px_12px_0px_var(--shadow-02),0px_0px_4px_1px_var(--shadow-01)]"
    >
      {children}
    </Section>
  );
}

/** Mode selector: active tab = white pill with icon + label, inactive =
 *  icon only (mock Tabs 2288:88250). Contained variant + the
 *  .chat-mode-tabs overrides in globals.css pin the exact chrome. */
interface ModeTabsProps {
  tabs: ToolbarMode[];
  active: ToolbarMode;
  onChange: (m: ToolbarMode) => void;
}

export function ModeTabs({ tabs, active, onChange }: ModeTabsProps) {
  return (
    <Section
      gap={0}
      padding={0}
      width="fit"
      height="fit"
      className="chat-mode-tabs"
    >
      <Tabs
        variant="contained"
        value={active}
        onValueChange={(value) => {
          if (value === "chat" || value === "watch" || value === "launch") {
            onChange(value);
          }
        }}
      >
        <Tabs.List>
          {tabs.map((m) => (
            <Tabs.Trigger key={m} value={m} icon={MODE_ICONS[m]}>
              {active === m ? MODE_LABELS[m] : undefined}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs>
    </Section>
  );
}

/** Fold controls (mock 2361:65094 idle, 2293:92820 during a turn):
 *  optional divider, expand-to-panel, fold, plus new-session when a turn
 *  is active. */
interface FoldClusterProps {
  onExpand?: () => void;
  onFold: () => void;
  onClose?: () => void;
  withDivider?: boolean;
}

export function FoldCluster({
  onExpand,
  onFold,
  onClose,
  withDivider,
}: FoldClusterProps) {
  return (
    <Section
      flexDirection="row"
      gap={0.25}
      padding={0.5}
      width="fit"
      height="fit"
    >
      {withDivider && <Divider orientation="vertical" paddingParallel="sm" />}
      {onExpand && (
        <Button
          icon={SvgSidebar}
          prominence="internal"
          size="md"
          tooltip="Open Sidebar"
          onClick={onExpand}
        />
      )}
      <Button
        icon={SvgFold}
        prominence="internal"
        size="md"
        tooltip="Fold toolbar"
        onClick={onFold}
      />
      {onClose && (
        <Button
          icon={SvgX}
          prominence="internal"
          size="md"
          tooltip="New session"
          onClick={onClose}
        />
      )}
    </Section>
  );
}

/** Attached wiki context as a removable chip: "Project Doc (line 6 - 9)"
 *  (mock Tag 2348:387948). */
interface ContextChipProps {
  context: ToolbarContext;
  onRemove?: () => void;
}

export function ContextChip({ context, onRemove }: ContextChipProps) {
  const base = docBaseName(context.path);
  const range =
    context.startLine != null && context.endLine != null
      ? ` (line ${context.startLine} - ${context.endLine})`
      : "";
  return (
    <Tag
      icon={context.kind === "dir" ? SvgFolder : SvgFileText}
      title={`${base}${range}`}
      size="md"
      onRemove={onRemove}
      truncate
    />
  );
}

interface ThinkingShimmerProps {
  label?: string;
}

export function ThinkingShimmer({ label = "Thinking…" }: ThinkingShimmerProps) {
  // raw-ok: bg-clip-text gradient needs className. Opal Text is WithoutStyles and drops it (d.ts TextProps).
  return <span className="chat-thinking-shimmer">{label}</span>;
}

/** Search-query chips shown in the long thinking state, capped with a
 *  "+N more" affordance (mock 2293:93763). */
interface QueryChipsProps {
  queries: string[];
  max?: number;
}

export function QueryChips({ queries, max = 3 }: QueryChipsProps) {
  return (
    <TagList
      items={queries.map((q) => ({ id: q, label: q }))}
      maxVisible={max}
      overflowIcon={SvgSearch}
    />
  );
}

export function docBaseName(path: string): string {
  return path.split("/").pop()?.replace(/\.md$/, "") || path;
}

/** Wiki pages a turn actually read, rendered as chips at the end of the
 *  actions row (mock Sources cluster in 2293:92578). */
interface SourceChipsProps {
  paths: string[];
  max?: number;
}

export function SourceChips({ paths, max = 3 }: SourceChipsProps) {
  if (paths.length === 0) return null;
  return (
    <TagList
      items={paths.map((p) => ({ id: p, label: docBaseName(p) }))}
      maxVisible={max}
      overflowIcon={SvgFileText}
    />
  );
}

interface ResponseActionsProps {
  text: string;
  feedback?: ChatFeedback | null;
  onFeedback?: (f: ChatFeedback) => void;
  onRetry?: () => void;
}

export function ResponseActions({
  text,
  feedback,
  onFeedback,
  onRetry,
}: ResponseActionsProps) {
  return (
    <Section flexDirection="row" gap={0} padding={0} width="fit" height="fit">
      <CopyButton getCopyText={() => text} />
      {onFeedback && (
        <>
          <Button
            icon={SvgThumbsUp}
            prominence="internal"
            size="lg"
            tooltip="Good response"
            variant={feedback === "up" ? "action" : "default"}
            onClick={() => onFeedback("up")}
          />
          <Button
            icon={SvgThumbsDown}
            prominence="internal"
            size="lg"
            tooltip="Bad response"
            variant={feedback === "down" ? "action" : "default"}
            onClick={() => onFeedback("down")}
          />
        </>
      )}
      {onRetry && (
        <Button
          icon={SvgRefreshCw}
          prominence="internal"
          size="lg"
          tooltip="Retry"
          onClick={onRetry}
        />
      )}
    </Section>
  );
}

/** Model picker (mock Model Bar 2288:88266): provider logo + model name +
 *  chevron over the shared selector body. Selecting writes the per-user
 *  chat model override. */
export function ModelBar() {
  const { settings, mutate } = useUserSettings();
  const { providers, isLoading } = useLlmAvailable();
  const [open, setOpen] = useState(false);

  const options = buildLlmOptions(providers);
  const current = settings?.chat_model ?? options[0]?.modelName ?? null;
  const currentProvider =
    settings?.chat_provider ??
    options.find((o) => o.modelName === current)?.provider ??
    options[0]?.provider;
  const Logo = getModelIcon(currentProvider ?? "");

  if (!current) return null;

  async function pick(option: LLMOption) {
    setOpen(false);
    try {
      await updateUserSettings({
        chat_provider: option.provider,
        chat_model: option.modelName,
      });
      await mutate();
    } catch {
      toast.error("Couldn't update the chat model.");
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        {/* Mock Model Bar pill (2288:88266): white, radius 12, soft ring. */}
        <Section
          gap={0}
          padding={0}
          width="fit"
          height="fit"
          className="chat-model-pill"
        >
          <OpenButton
            variant="select-light"
            icon={Logo}
            labelFont="main-ui-action"
            labelColor="text-04"
          >
            {displayModelName(current)}
          </OpenButton>
        </Section>
      </Popover.Trigger>
      <Popover.Content width="md" align="end">
        <ModelSelectorContent
          providers={providers}
          isLoading={isLoading}
          onSelect={(option) => void pick(option)}
          isSelected={(option) =>
            option.provider === currentProvider && option.modelName === current
          }
        />
      </Popover.Content>
    </Popover>
  );
}

/** The white input card (mock Input 2288:88267): chromeless textarea on
 *  top, action row below with attach + context chip left and the
 *  send/stop CTA right. */
interface ComposerProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  placeholder: string;
  streaming?: boolean;
  contexts?: ToolbarContext[];
  onRemoveContext?: (path: string) => void;
  autoFocus?: boolean;
  /** Watch and Launch submit from their own footer button, so the inline
   *  send would be a second control for the same action. */
  hideSend?: boolean;
  /** Rows to reserve, for multi-line placeholders like Watch's. */
  rows?: number;
  /** Enables the attach picker: add another wiki page as a context chip. */
  onAttachContext?: (path: string) => void;
}

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  placeholder,
  streaming,
  contexts,
  onRemoveContext,
  autoFocus,
  hideSend,
  rows = 1,
  onAttachContext,
}: ComposerProps) {
  const [attachOpen, setAttachOpen] = useState(false);
  const [attachQuery, setAttachQuery] = useState("");
  const { entries } = useWikiTree();
  // Pages only: the tree also carries trigger sidecar files. Anything already
  // on a chip drops out so the picker can't produce a duplicate.
  const attachedPaths = new Set((contexts ?? []).map((c) => c.path));
  const attachable = entries
    .filter(
      (e) =>
        e.path.endsWith(".md") &&
        !(e.path.split("/").pop() ?? "").startsWith(".") &&
        !attachedPaths.has(e.path) &&
        (!attachQuery.trim() ||
          e.path.toLowerCase().includes(attachQuery.toLowerCase())),
    )
    .slice(0, 8);
  const canSend = value.trim().length > 0 && !streaming;
  return (
    <Section
      gap={0}
      padding={0}
      height="fit"
      alignItems="stretch"
      className="overflow-hidden rounded-12 bg-background-neutral-00 shadow-[0px_0px_2px_1px_var(--shadow-01)]"
    >
      {/* raw-ok: the card owns the chrome and the caret runs borderless
          edge to edge, which InputTextArea's opal-input wrapper (its own
          padding and focus ring) does not allow. */}
      <textarea
        value={value}
        aria-label={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (canSend) onSubmit();
          }
        }}
        placeholder={placeholder}
        rows={rows}
        autoFocus={autoFocus}
        className="max-h-40 w-full resize-none bg-transparent px-[14px] pt-3 pb-2 text-[16px] leading-6 text-text-05 outline-none placeholder:text-text-02"
      />
      <Section
        flexDirection="row"
        justifyContent="between"
        gap={0.25}
        padding={0.25}
        height="fit"
      >
        <Section
          flexDirection="row"
          gap={0.25}
          padding={0}
          width="fit"
          height="fit"
        >
          {onAttachContext ? (
            <Popover open={attachOpen} onOpenChange={setAttachOpen}>
              <Popover.Trigger asChild>
                <Section gap={0} padding={0} width="fit" height="fit">
                  <Button
                    icon={SvgPlus}
                    prominence="internal"
                    size="lg"
                    tooltip="Attach a wiki page"
                    onClick={() => setAttachOpen((v) => !v)}
                  />
                </Section>
              </Popover.Trigger>
              <Popover.Content align="start" width="md">
                <Section
                  gap={0.25}
                  padding={0.25}
                  height="fit"
                  alignItems="stretch"
                >
                  <InputTypeIn
                    searchIcon
                    value={attachQuery}
                    onChange={(e) => setAttachQuery(e.target.value)}
                    placeholder="Search pages..."
                    aria-label="Search pages"
                  />
                  {attachable.map((entry) => (
                    <LineItemButton
                      key={entry.path}
                      sizePreset="main-ui"
                      variant="body"
                      icon={SvgFileText}
                      title={docBaseName(entry.path)}
                      onClick={() => {
                        onAttachContext(entry.path);
                        setAttachOpen(false);
                        setAttachQuery("");
                      }}
                    />
                  ))}
                </Section>
              </Popover.Content>
            </Popover>
          ) : (
            <Button
              icon={SvgPlus}
              prominence="internal"
              size="lg"
              tooltip="Attach a wiki page"
              disabled
            />
          )}
          {(contexts ?? []).map((c) => (
            <ContextChip
              key={c.path}
              context={c}
              onRemove={onRemoveContext && (() => onRemoveContext(c.path))}
            />
          ))}
        </Section>
        {/* CTA colors are the mock's pair (idle #ccc, armed near-black,
            2361:65107 vs 2293:92515), painted on the wrapper because the
            Opal variant owns the button bg. */}
        {streaming && onStop ? (
          <Section
            gap={0}
            padding={0}
            width="fit"
            height="fit"
            className="chat-cta chat-cta-on"
          >
            <Button
              icon={SvgSquare}
              prominence="internal"
              size="lg"
              tooltip="Stop"
              onClick={onStop}
            />
          </Section>
        ) : hideSend ? null : (
          <Section
            gap={0}
            padding={0}
            width="fit"
            height="fit"
            className={cn("chat-cta", canSend && "chat-cta-on")}
          >
            <Button
              icon={SvgArrowUp}
              prominence="internal"
              size="lg"
              tooltip="Send"
              disabled={!canSend}
              onClick={onSubmit}
            />
          </Section>
        )}
      </Section>
    </Section>
  );
}

"use client";

// Full-height chat side panel (mocks 1828:60338, 2361:63861): the whole
// thread, Chat History, and the Watch/Launch forms docked right. The
// floating toolbar is the entry point, this panel is the full experience.
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  Button,
  CompactMarkdown,
  Divider,
  IconContainer,
  InputTypeIn,
  LineItemButton,
  Popover,
  Text,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import {
  SvgArrowUpRight,
  SvgBubbleText,
  SvgEditBig,
  SvgFileText,
  SvgFold,
  SvgHistory,
  SvgX,
} from "@onyx-ai/opal/icons";
import { SvgOnyxLogo } from "@onyx-ai/opal/logos";

import {
  Composer,
  ModeTabs,
  ModelBar,
  QueryChips,
  ResponseActions,
  SourceChips,
  ThinkingShimmer,
  docBaseName,
  usePublishedSize,
  type ToolbarContext,
  type ToolbarMode,
} from "@/components/wiki/toolbar/chatParts";
import { ToolbarLaunch } from "@/components/wiki/toolbar/ToolbarLaunch";
import { ToolbarWatch } from "@/components/wiki/toolbar/ToolbarWatch";
import type { WikiChat } from "@/components/wiki/toolbar/useWikiChat";
import { useChatSessions, type ChatSession } from "@/lib/chat";
import {
  editsFromItems,
  queryChipsFromItems,
  sourcesFromItems,
  thinkingSeconds,
} from "@/lib/chatState";
import { wikiPath } from "@/lib/wikiHref";

/** Section label in the history menu (2365:65950): 12/16 text-03 with the
 *  rule filling the rest of the row. */
interface HistoryGroupLabelProps {
  children: string;
}

function HistoryGroupLabel({ children }: HistoryGroupLabelProps) {
  return (
    <Section
      flexDirection="row"
      justifyContent="start"
      gap={0.25}
      padding={0.25}
      height="fit"
      alignItems="center"
    >
      <Text font="secondary-body" color="text-03" nowrap>
        {children}
      </Text>
      <Divider />
    </Section>
  );
}

interface ChatSidePanelProps {
  chat: WikiChat;
  tabs: ToolbarMode[];
  contexts: ToolbarContext[];
  onRemoveContext?: (path: string) => void;
  onAttachContext?: (path: string) => void;
  onDock: () => void;
  onClose: () => void;
}

export function ChatSidePanel({
  chat,
  tabs,
  contexts,
  onRemoveContext,
  onAttachContext,
  onDock,
  onClose,
}: ChatSidePanelProps) {
  const router = useRouter();
  // The doc column reserves this so its text stops at the panel's edge instead
  // of running underneath. FileView cannot read `panelOpen`, which lives in the
  // toolbar below it, so the panel announces its own width.
  const { ref: panelRef, publish: republishPanelWidth } = usePublishedSize(
    "--wiki-chat-panel-width",
    "width",
  );
  const [mode, setMode] = useState<ToolbarMode>("chat");
  const [draft, setDraft] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyQuery, setHistoryQuery] = useState("");
  const currentPath = contexts[0]?.path ?? null;
  const { sessions } = useChatSessions(currentPath, historyOpen);
  // The mock caps the menu at 10 chats, the full list belongs to a
  // dedicated history page (2365:65548 annotation).
  const historySessions = (sessions ?? [])
    .filter(
      (s) =>
        !historyQuery ||
        (s.title ?? "").toLowerCase().includes(historyQuery.toLowerCase()),
    )
    .slice(0, 10);
  const thisPageSessions = historySessions.filter((s) => s.touches_path);
  const otherSessions = historySessions.filter((s) => !s.touches_path);

  // The thread should follow new turns like a chat log.
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    scroller?.scrollTo({ top: scroller.scrollHeight });
  }, [scroller, chat.items.length, chat.sending]);

  useEffect(() => {
    republishPanelWidth();
  });

  function submit() {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    chat.send(text);
  }

  function renderHistoryRow(s: ChatSession) {
    const label = s.title || "Untitled chat";
    return (
      <LineItemButton
        key={s.id}
        sizePreset="main-ui"
        variant="section"
        icon={SvgBubbleText}
        title={label}
        // A long title truncates to one line, so the row carries the full
        // text on hover.
        tooltip={label}
        tooltipSide="left"
        rounding="sm"
        onClick={() => {
          chat.loadSession(s.id);
          setMode("chat");
          setHistoryOpen(false);
        }}
      />
    );
  }

  const lastAssistant = [...chat.items]
    .reverse()
    .find((i) => i.kind === "assistant");
  const turnQueries = queryChipsFromItems(chat.items);

  return (
    <Section
      ref={panelRef}
      gap={0}
      padding={0}
      height="full"
      width={30}
      alignItems="stretch"
      className="fixed inset-y-0 right-0 z-40 border-l border-border-01 bg-background-tint-01"
    >
      {/* Header (mock 2361:63920): mode tabs left, history and window
          controls right. */}
      <Section
        flexDirection="row"
        justifyContent="between"
        gap={0}
        padding={0.5}
        height="fit"
        alignItems="center"
      >
        <ModeTabs tabs={tabs} active={mode} onChange={setMode} />
        <Section
          flexDirection="row"
          gap={0.25}
          padding={0}
          width="fit"
          height="fit"
        >
          {/* New and history are chat affordances, other tabs hide them. */}
          {mode === "chat" && (
            <Button
              icon={SvgEditBig}
              prominence="internal"
              size="md"
              tooltip="New chat"
              onClick={chat.newSession}
            />
          )}
          <Popover
            open={historyOpen && mode === "chat"}
            onOpenChange={setHistoryOpen}
          >
            <Popover.Trigger asChild>
              <Section gap={0} padding={0} width="fit" height="fit">
                {/* Labeled per the More Chat mock's header pill. */}
                {mode === "chat" && (
                  <Button
                    icon={SvgHistory}
                    prominence="tertiary"
                    size="md"
                    onClick={() => setHistoryOpen((v) => !v)}
                  >
                    Chat History
                  </Button>
                )}
              </Section>
            </Popover.Trigger>
            <Popover.Content align="end" width="fit">
              {/* Contextual Menu - Select (2365:65548): 270 inner, 280 with
                  the Popover chrome. Search row with the full-history link,
                  12/16 labels, 14/20 SemiBold rows, 10-chat cap. */}
              <Section
                gap={0.25}
                padding={0.25}
                width={16.875}
                height="fit"
                alignItems="stretch"
                className="chat-history-menu"
              >
                <InputTypeIn
                  searchIcon
                  variant="internal"
                  value={historyQuery}
                  onChange={(e) => setHistoryQuery(e.target.value)}
                  placeholder="Search chats…"
                  aria-label="Search chats"
                  rightChildren={
                    <Button
                      icon={SvgArrowUpRight}
                      prominence="internal"
                      size="sm"
                      tooltip="Open full chat history"
                      href="/app/chats"
                    />
                  }
                />
                {historySessions.length === 0 && (
                  <Section gap={0} padding={0.5} height="fit">
                    <Text font="secondary-body" color="text-03">
                      No chats yet.
                    </Text>
                  </Section>
                )}
                {thisPageSessions.length > 0 && (
                  <>
                    <HistoryGroupLabel>This Page</HistoryGroupLabel>
                    {thisPageSessions.map(renderHistoryRow)}
                  </>
                )}
                {otherSessions.length > 0 && (
                  <>
                    <HistoryGroupLabel>Recent Chats</HistoryGroupLabel>
                    {otherSessions.map(renderHistoryRow)}
                  </>
                )}
              </Section>
            </Popover.Content>
          </Popover>
          <Button
            icon={SvgFold}
            prominence="internal"
            size="md"
            tooltip="Dock to toolbar"
            onClick={onDock}
          />
          <Button
            icon={SvgX}
            prominence="internal"
            size="md"
            tooltip="Close"
            onClick={onClose}
          />
        </Section>
      </Section>

      {mode === "chat" ? (
        <>
          <Section
            ref={setScroller}
            gap={0}
            padding={0}
            justifyContent="start"
            alignItems="stretch"
            className="min-h-0 flex-1 overflow-y-auto"
          >
            <Section
              gap={0.75}
              padding={0.75}
              height="fit"
              alignItems="stretch"
            >
              {chat.items.map((item, i) => {
                if (item.kind === "user") {
                  return (
                    <Section
                      key={`user-${i}`}
                      gap={0}
                      padding={0.5}
                      height="fit"
                      width="fit"
                      className="self-end rounded-12 bg-background-tint-02"
                    >
                      <Text font="main-content-body" color="text-04">
                        {item.content}
                      </Text>
                    </Section>
                  );
                }
                if (item.kind !== "assistant") return null;
                // Everything since the turn's user message belongs to it.
                let start = 0;
                for (let j = i - 1; j >= 0; j--) {
                  if (chat.items[j].kind === "user") {
                    start = j + 1;
                    break;
                  }
                }
                const turn = chat.items.slice(start, i + 1);
                const seconds = thinkingSeconds(chat.items, i);
                const edits = editsFromItems(turn);
                const sources = sourcesFromItems(turn);
                return (
                  <Section
                    key={item.id ?? `assistant-${i}`}
                    gap={0.25}
                    padding={0}
                    height="fit"
                    alignItems="start"
                  >
                    <Section
                      flexDirection="row"
                      gap={0.25}
                      padding={0}
                      width="fit"
                      height="fit"
                      alignItems="center"
                    >
                      <IconContainer
                        size="main-ui"
                        avatar="icon"
                        icon={SvgOnyxLogo}
                      />
                      {seconds !== null && seconds >= 2 && (
                        <Text font="secondary-body" color="text-03">
                          {`Thought for ${seconds}s`}
                        </Text>
                      )}
                    </Section>
                    <CompactMarkdown>{item.content}</CompactMarkdown>
                    {edits.map((p) => (
                      // Edit card (mock 1828:61808 "Edited 45 lines" row):
                      // white card, doc icon, blue View. Revert waits on
                      // its endpoint.
                      <Section
                        key={p}
                        flexDirection="row"
                        justifyContent="between"
                        alignItems="center"
                        gap={0.25}
                        padding={0.25}
                        className="rounded-12 bg-background-tint-00 shadow-[0px_0px_2px_1px_var(--shadow-01)]"
                      >
                        <Section
                          flexDirection="row"
                          gap={0.25}
                          padding={0.25}
                          width="fit"
                          height="fit"
                          alignItems="center"
                        >
                          <SvgFileText size={16} />
                          <Text font="main-ui-body" color="text-03">
                            {`Edited ${docBaseName(p)}`}
                          </Text>
                        </Section>
                        <Section
                          gap={0}
                          padding={0}
                          width="fit"
                          height="fit"
                          className="chat-edit-view"
                        >
                          <Button
                            icon={SvgArrowUpRight}
                            prominence="tertiary"
                            size="sm"
                            onClick={() => router.push(wikiPath(p))}
                          >
                            View
                          </Button>
                        </Section>
                      </Section>
                    ))}
                    {item.content && (
                      <Section
                        flexDirection="row"
                        justifyContent="between"
                        gap={0.25}
                        padding={0}
                        height="fit"
                        alignItems="center"
                      >
                        <ResponseActions
                          text={item.content}
                          feedback={item.feedback}
                          onFeedback={
                            item.id
                              ? (feedback) =>
                                  chat.rate(
                                    item.id!,
                                    item.feedback === feedback
                                      ? null
                                      : feedback,
                                  )
                              : undefined
                          }
                          onRetry={
                            i === chat.items.length - 1 ? chat.retry : undefined
                          }
                        />
                        <SourceChips paths={sources} />
                      </Section>
                    )}
                  </Section>
                );
              })}
              {chat.thinking && (
                /* Thinking Header (2293:93763): white card, the shimmer on a
                   28px step line, search chips wrapping beneath it. */
                <Section
                  gap={0}
                  padding={0.25}
                  height="fit"
                  alignItems="stretch"
                  className="rounded-12 bg-background-tint-00"
                >
                  <Section
                    gap={0}
                    padding={0.25}
                    height="fit"
                    alignItems="start"
                    justifyContent="center"
                    className="min-h-7"
                  >
                    <ThinkingShimmer />
                  </Section>
                  {turnQueries.length > 0 && (
                    <Section
                      gap={0}
                      padding={0.25}
                      height="fit"
                      alignItems="stretch"
                    >
                      <QueryChips queries={turnQueries} />
                    </Section>
                  )}
                </Section>
              )}
              {chat.error && (
                <Text font="secondary-body" color="status-error-05">
                  {chat.error}
                </Text>
              )}
            </Section>
          </Section>
          <Section gap={0.25} padding={0.5} height="fit" alignItems="stretch">
            {/* Model row rides right (mock Models row is justify-end). The
                composer's own CTA is the only stop control here. */}
            <Section
              flexDirection="row"
              justifyContent="end"
              gap={0.25}
              padding={0}
              height="fit"
              alignItems="center"
            >
              <ModelBar />
            </Section>
            <Composer
              value={draft}
              onChange={setDraft}
              onSubmit={submit}
              placeholder={
                lastAssistant ? "Reply…" : "Ask wiki or write with AI…"
              }
              contexts={contexts}
              onRemoveContext={onRemoveContext}
              onAttachContext={onAttachContext}
              streaming={chat.sending}
              onStop={chat.stop}
            />
          </Section>
        </>
      ) : (
        <Section
          gap={0}
          padding={0}
          justifyContent="start"
          alignItems="stretch"
          className="min-h-0 flex-1 overflow-y-auto"
        >
          <Section gap={0} padding={0.5} height="fit" alignItems="stretch">
            {mode === "watch" ? (
              <ToolbarWatch context={contexts[0] ?? null} />
            ) : (
              <ToolbarLaunch context={contexts[0] ?? null} />
            )}
          </Section>
        </Section>
      )}
    </Section>
  );
}

"use client";

// Floating wiki AI toolbar with host-configured modes and a folded pill.
// Expanded content stays anchored to the bottom of the content column.
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, IconLoader } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgBubbleText, SvgWorkflow, SvgZap } from "@onyx-ai/opal/icons";

import {
  FoldCluster,
  ModeTabs,
  ModelBar,
  ToolbarPanel,
  usePublishedSize,
  type ToolbarContext,
  type ToolbarMode,
} from "@/components/wiki/toolbar/chatParts";
import { ChatSidePanel } from "@/components/wiki/toolbar/ChatSidePanel";
import { ToolbarChat } from "@/components/wiki/toolbar/ToolbarChat";
import { ToolbarLaunch } from "@/components/wiki/toolbar/ToolbarLaunch";
import { ToolbarWatch } from "@/components/wiki/toolbar/ToolbarWatch";
import { useRemovableToolbarContext } from "@/components/wiki/toolbar/useToolbarContext";
import { useWikiChat } from "@/components/wiki/toolbar/useWikiChat";

const STORAGE_KEY_FOLDED = "wiki-toolbar:folded";

const MODE_TOOLTIPS: Record<ToolbarMode, string> = {
  chat: "Chat",
  watch: "Watch",
  launch: "Launch",
};

function modeIcon(m: ToolbarMode) {
  if (m === "watch") return SvgWorkflow;
  if (m === "launch") return SvgZap;
  return SvgBubbleText;
}

/** Bottom-docked toolbar strip. `column` renders in flow as the column's
 *  last child, sticky-pinned, sharing its exact box (the mock's strip is a
 *  Doc Section child). `float` overlays hosts without a parent column. */
interface WikiToolbarDockProps {
  tabs?: ToolbarMode[];
  context?: ToolbarContext | null;
  /** Reading-column width for float mode. Column mode takes the parent's. */
  width?: "sm" | "sm-md";
  defaultFolded?: boolean;
  surface?: string;
  variant?: "float" | "column";
  /** Anchored float: fixed strip spanning the matched, page-padded column. */
  anchorSelector?: string;
}

export function WikiToolbarDock({
  tabs,
  context,
  width = "sm-md",
  defaultFolded,
  surface,
  variant = "float",
  anchorSelector,
}: WikiToolbarDockProps) {
  const [anchorBox, setAnchorBox] = useState<{
    left: number;
    width: number;
  } | null>(null);
  useEffect(() => {
    if (!anchorSelector) return;
    let ro: ResizeObserver | null = null;
    let timer: number | null = null;
    let measure = () => {};
    const attach = () => {
      const el = document.querySelector(anchorSelector);
      // The editor mounts after this dock, so poll until the column exists.
      if (!(el instanceof HTMLElement)) {
        timer = window.setTimeout(attach, 150);
        return;
      }
      measure = () => {
        const r = el.getBoundingClientRect();
        setAnchorBox({ left: r.left, width: r.width });
      };
      measure();
      ro = new ResizeObserver(() => measure());
      ro.observe(el);
      window.addEventListener("resize", measure);
    };
    attach();
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      ro?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [anchorSelector]);

  // Column docks sit in flow and take their own room, so only the fixed and
  // absolute variants publish a height for `.dock-clearance` to reserve.
  const { ref: overlayDockRef, publish: republishDockHeight } =
    usePublishedSize(
      "--wiki-dock-height",
      "height",
      anchorSelector ? anchorBox !== null : variant === "float",
    );

  if (anchorSelector) {
    if (!anchorBox) return null;
    return (
      // raw-ok: style-only fixed shell, geometry comes from the measured anchor column, no layout classes.
      <div
        ref={overlayDockRef}
        style={{
          // The panel's own 4px margin supplies the mock's bottom gap.
          position: "fixed",
          bottom: 0,
          left: anchorBox.left,
          width: anchorBox.width,
          zIndex: 30,
          pointerEvents: "none",
        }}
      >
        <Section
          gap={0}
          padding={0}
          height="fit"
          alignItems="center"
          className="pointer-events-auto"
        >
          <WikiToolbar
            tabs={tabs}
            context={context}
            defaultFolded={defaultFolded}
            surface={surface}
            onGeometryChange={republishDockHeight}
          />
        </Section>
      </div>
    );
  }
  if (variant === "column") {
    return (
      <Section
        gap={0}
        padding={0}
        height="fit"
        alignItems="center"
        className="pointer-events-none sticky bottom-0 z-30"
      >
        <Section
          gap={0}
          padding={0}
          height="fit"
          alignItems="center"
          className="pointer-events-auto w-full"
        >
          <WikiToolbar
            tabs={tabs}
            context={context}
            defaultFolded={defaultFolded}
            surface={surface}
          />
        </Section>
      </Section>
    );
  }
  return (
    <Section
      ref={overlayDockRef}
      alignItems="center"
      justifyContent="end"
      gap={0}
      padding={0}
      height="fit"
      className="pointer-events-none absolute inset-x-0 bottom-0 z-30"
    >
      <Section
        gap={0}
        padding={0.5}
        height="fit"
        alignItems="center"
        className={`rail-inset pointer-events-auto mx-auto w-full ${
          width === "sm"
            ? "max-w-(--app-container-sm)"
            : "max-w-(--app-container-sm-md)"
        }`}
      >
        <WikiToolbar
          tabs={tabs}
          context={context}
          defaultFolded={defaultFolded}
          surface={surface}
          onGeometryChange={republishDockHeight}
        />
      </Section>
    </Section>
  );
}

interface WikiToolbarProps {
  tabs?: ToolbarMode[];
  context?: ToolbarContext | null;
  /** First-visit fold state. The surface's stored preference wins. */
  defaultFolded?: boolean;
  /** Fold preference is remembered per surface, so folding on a doc
   *  page never folds home. */
  surface?: string;
  /** Overlay docks reserve this strip's height as scroll space. Folding,
   *  attaching context, and a turn arriving all resize it. */
  onGeometryChange?: () => void;
}

export function WikiToolbar({
  tabs = ["chat"],
  context,
  defaultFolded = true,
  surface = "wiki",
  onGeometryChange,
}: WikiToolbarProps) {
  const [folded, setFolded] = useState(defaultFolded);
  const [mode, setMode] = useState<ToolbarMode>("chat");
  const [panelOpen, setPanelOpen] = useState(false);
  const {
    contexts: chatContexts,
    removeContext: removeChatContext,
    attachContext: attachChatContext,
  } = useRemovableToolbarContext(context);

  // Fold state persists per browser and per surface. The surface default
  // applies until the reader folds or unfolds on that surface.
  const storageKey = `${STORAGE_KEY_FOLDED}:${surface}`;
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(storageKey);
      setFolded(stored === null ? defaultFolded : stored === "1");
    } catch {
      setFolded(defaultFolded);
    }
  }, [defaultFolded, storageKey]);
  const setFoldedPersistent = useCallback(
    (next: boolean) => {
      setFolded(next);
      try {
        window.localStorage.setItem(storageKey, next ? "1" : "0");
      } catch {}
    },
    [storageKey],
  );

  // Deliberately every render: the strip's height is a function of fold state,
  // mode, context chips, and the live turn, and no single dependency list
  // spans them.
  useEffect(() => {
    onGeometryChange?.();
  });

  const unfold = useCallback(
    () => setFoldedPersistent(false),
    [setFoldedPersistent],
  );

  const chat = useWikiChat({
    contextPaths: chatContexts.map((c) => c.path),
    onActivate: unfold,
  });

  const hasActiveTurn = chat.items.length > 0 || chat.sending;

  // A turn finishing while folded lights the badge (mock 2385:75589).
  // Unfolding acknowledges it.
  const [unseenReply, setUnseenReply] = useState(false);
  const prevSending = useRef(chat.sending);
  useEffect(() => {
    if (prevSending.current && !chat.sending && folded) setUnseenReply(true);
    prevSending.current = chat.sending;
  }, [chat.sending, folded]);
  useEffect(() => {
    if (!folded) setUnseenReply(false);
  }, [folded]);

  function foldedChatTooltip() {
    if (chat.sending) {
      return chat.thinking ? "Chat - Thinking…" : "Chat - Responding…";
    }
    return unseenReply ? "Chat - New Message" : MODE_TOOLTIPS.chat;
  }

  // The panel owns the conversation while open, the toolbar yields.
  if (panelOpen) {
    return (
      <ChatSidePanel
        chat={chat}
        tabs={tabs}
        contexts={chatContexts}
        onRemoveContext={removeChatContext}
        onAttachContext={attachChatContext}
        onDock={() => setPanelOpen(false)}
        onClose={() => {
          setPanelOpen(false);
          setFoldedPersistent(true);
        }}
      />
    );
  }

  return (
    <Section
      gap={0}
      padding={0}
      height="fit"
      width={folded ? "fit" : "full"}
      alignItems="center"
    >
      {folded ? (
        <ToolbarPanel folded>
          <Section
            flexDirection="row"
            gap={0}
            padding={0.25}
            width="fit"
            height="fit"
          >
            {tabs.map((m) => {
              const busy = m === "chat" && chat.sending;
              return (
                // raw-ok: positioning anchor for the new-message badge dot.
                <span
                  key={m}
                  className={`relative ${busy ? "chat-fold-progress" : ""}`}
                >
                  <Button
                    icon={
                      busy
                        ? (props) => (
                            <IconLoader
                              size={props.size ?? 20}
                              color="text-04"
                            />
                          )
                        : modeIcon(m)
                    }
                    prominence="internal"
                    size="lg"
                    tooltip={
                      m === "chat" ? foldedChatTooltip() : MODE_TOOLTIPS[m]
                    }
                    onClick={() => {
                      setMode(m);
                      setFoldedPersistent(false);
                    }}
                  />
                  {m === "chat" && unseenReply && !chat.sending && (
                    // raw-ok: 8px badge dot (mock 2385:76105), no Opal badge primitive in the pinned version.
                    <span className="absolute top-1.5 right-0.5 size-2 rounded-full bg-(--action-link-05)" />
                  )}
                </span>
              );
            })}
          </Section>
        </ToolbarPanel>
      ) : (
        <ToolbarPanel flush={mode !== "chat"}>
          {/* Idle title (2361:65088): tabs, divider, fold, models right.
              During a turn (2293:92814) the fold cluster moves to the row
              end and the model bar drops to the actions row. */}
          <Section
            flexDirection="row"
            justifyContent="between"
            gap={0}
            padding={mode !== "chat" ? 0.25 : 0}
            height="fit"
          >
            <Section
              flexDirection="row"
              gap={0}
              padding={0}
              width="fit"
              height="fit"
            >
              <Section padding={0.25} width="fit" height="fit">
                <ModeTabs tabs={tabs} active={mode} onChange={setMode} />
              </Section>
              {!hasActiveTurn && (
                <FoldCluster
                  withDivider
                  onExpand={() => setPanelOpen(true)}
                  onFold={() => setFoldedPersistent(true)}
                />
              )}
            </Section>
            {hasActiveTurn ? (
              <FoldCluster
                onExpand={() => setPanelOpen(true)}
                onFold={() => setFoldedPersistent(true)}
                onClose={chat.newSession}
              />
            ) : (
              <Section
                flexDirection="row"
                gap={0.25}
                padding={0.25}
                width="fit"
                height="fit"
              >
                {mode === "chat" && <ModelBar />}
              </Section>
            )}
          </Section>
          {mode === "chat" && (
            <ToolbarChat
              chat={chat}
              contexts={chatContexts}
              onRemoveContext={removeChatContext}
              onAttachContext={attachChatContext}
            />
          )}
          {mode === "watch" && <ToolbarWatch context={context} />}
          {mode === "launch" && <ToolbarLaunch context={context} />}
        </ToolbarPanel>
      )}
    </Section>
  );
}

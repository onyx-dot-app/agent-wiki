"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { remarkBareSpaceLinks } from "@/lib/remarkBareSpaceLinks";

import styles from "./ChatWidget.module.css";

import { Button } from "@onyx-ai/opal/components";
import {
  SvgCheck,
  SvgDocFile,
  SvgEdit,
  SvgFold,
  SvgHistory,
  SvgX,
  SvgXCircle,
} from "@onyx-ai/opal/icons";
import { ChatBar } from "@/components/chat/ChatBar";

import { apiFetch, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  createSession,
  getSession,
  streamDraftingInit,
  streamMessage,
  type ChatSession,
  type PersistedChatMessage,
} from "@/lib/chat";
import { useDrafting, type DraftingState } from "@/lib/drafting";
import { useAppFocus } from "@/hooks/useAppFocus";
import { ChatHistoryPanel } from "@/components/chat/ChatHistoryPanel";
import { presentTool } from "@/lib/tools";
import { reviseDraft } from "@/lib/wiki";

// Items in the chat transcript. Tool calls are first-class entries
// (rather than a transient hint) so they stay visible in the scrollback
// alongside the assistant's text — when the agent runs ``search_wiki``
// or ``edit_doc`` between two paragraphs, the user can scroll back and
// see exactly what happened.
type ToolState = "running" | "done" | "error";
type ChatItem =
  | { kind: "user"; content: string }
  | { kind: "assistant"; content: string }
  | { kind: "tool"; id: string; name: string; state: ToolState };

type StreamEvent =
  | { type: "text_delta"; text: string }
  | {
      type: "tool_call";
      id: string;
      name: string;
      arguments: Record<string, unknown>;
    }
  | { type: "tool_result"; id: string; name: string; content: string }
  | { type: "iteration_done" }
  | { type: "done" }
  | { type: "error"; code: string; message: string }
  | { type: "session_created"; session_id: string };

type Mode = "closed" | "widget" | "expanded";

const STORAGE_KEY_MODE = "chat-widget:mode";
const STORAGE_KEY_WIDTH = "chat-widget:expanded-width";
const STORAGE_KEY_SESSION = "chat-widget:session-id";
const DEFAULT_EXPANDED_WIDTH = 480;
const MIN_EXPANDED_WIDTH = 280;

export function ChatWidget() {
  const { user } = useAuth();
  const { drafting, expandTick, getDraftBody, applyDraftBody } = useDrafting();
  // The wiki page the user currently has open (null off the wiki), sent with
  // each message so the chat agent knows what they're looking at.
  const currentWikiPath = useAppFocus().wikiPath;
  const [mode, setMode] = useState<Mode>("closed");
  const [expandedWidth, setExpandedWidth] = useState<number>(
    DEFAULT_EXPANDED_WIDTH,
  );
  const [items, setItems] = useState<ChatItem[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const resizingRef = useRef(false);
  const hydratedSessionRef = useRef(false);
  // Drafting bookkeeping. ``draftingKey`` is a string we derive from
  // the active drafting state (``tpl:<id>`` or ``blank``) — used so
  // brief null→same-key flips (NewDocView → FileViewer hand-off) don't
  // trigger a re-init, and so picking a different template (or
  // switching between blank and a template) DOES trigger one. The
  // pre-drafting snapshot is restored when the user leaves drafting so
  // their regular conversation isn't lost.
  const [draftingKey, setDraftingKey] = useState<string | null>(null);
  // What the drafting banner renders. Tracks ``drafting`` while it's
  // active but is cleared by the deactivation below — in the same render
  // as the mode/conversation restore — so leaving drafting is one visual
  // step (banner gone + panel collapsed + chat swapped), not the banner
  // vanishing on its own first.
  const [draftingBanner, setDraftingBanner] = useState<DraftingState | null>(
    null,
  );
  const preDraftingRef = useRef<{
    sessionId: string | null;
    items: ChatItem[];
  } | null>(null);
  // Mode the widget was in before drafting force-expanded it ("closed" or
  // "widget"). Restored when drafting ends so the doc-creation flow doesn't
  // permanently commandeer the chat. Any manual mode change (bar
  // expand/collapse/dock, panel collapse/close) clears it: the user's
  // explicit choice wins over the automatic restore.
  const preDraftingModeRef = useRef<Mode | null>(null);

  const setModeManually = useCallback((m: Mode) => {
    preDraftingModeRef.current = null;
    setMode(m);
  }, []);

  // Hydrate persisted UI state on mount.
  useEffect(() => {
    try {
      const m = window.localStorage.getItem(STORAGE_KEY_MODE);
      if (m === "widget" || m === "expanded" || m === "closed") setMode(m);
      const w = window.localStorage.getItem(STORAGE_KEY_WIDTH);
      if (w) {
        const n = parseInt(w, 10);
        if (!Number.isNaN(n)) setExpandedWidth(clampWidth(n));
      }
      const sid = window.localStorage.getItem(STORAGE_KEY_SESSION);
      if (sid) setSessionId(sid);
    } catch {
      // ignore
    }
  }, []);

  // Persist effects skip their first run: on mount they'd write the
  // defaults ("closed", 480) over the stored values before the hydrate
  // effect's setState has re-rendered — under StrictMode's double effect
  // pass that clobber is then read back, losing the persisted state.
  const skipPersistModeRef = useRef(true);
  useEffect(() => {
    if (skipPersistModeRef.current) {
      skipPersistModeRef.current = false;
      return;
    }
    try {
      window.localStorage.setItem(STORAGE_KEY_MODE, mode);
    } catch {
      // ignore
    }
  }, [mode]);

  const skipPersistWidthRef = useRef(true);
  useEffect(() => {
    if (skipPersistWidthRef.current) {
      skipPersistWidthRef.current = false;
      return;
    }
    try {
      window.localStorage.setItem(STORAGE_KEY_WIDTH, String(expandedWidth));
    } catch {
      // ignore
    }
  }, [expandedWidth]);

  useEffect(() => {
    // Drafting sessions are hidden + ephemeral — don't persist their id
    // to localStorage, otherwise refreshing would resurrect a session
    // the user can't browse to. The pre-drafting id, if any, stays in
    // localStorage from the prior write and gets restored on unmount.
    if (draftingKey !== null) return;
    try {
      if (sessionId)
        window.localStorage.setItem(STORAGE_KEY_SESSION, sessionId);
      else window.localStorage.removeItem(STORAGE_KEY_SESSION);
    } catch {
      // ignore
    }
  }, [sessionId, draftingKey]);

  // Hydrate the active session's messages when the widget first opens with
  // a stored session id. Runs once per page load — switching sessions via
  // the history panel does its own load.
  useEffect(() => {
    if (mode === "closed") return;
    if (hydratedSessionRef.current) return;
    if (!sessionId) {
      hydratedSessionRef.current = true;
      return;
    }
    hydratedSessionRef.current = true;
    void (async () => {
      try {
        const detail = await getSession(sessionId);
        setItems(itemsFromPersisted(detail.messages));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          // Session was deleted on another device — start fresh.
          setSessionId(null);
          setItems([]);
        } else {
          setError(formatError(e));
        }
      }
    })();
  }, [mode, sessionId]);

  useEffect(() => {
    if (mode === "closed") return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [items, sending, mode]);

  // Resize handling for expanded mode. Panel is anchored to the right edge,
  // so dragging the left handle leftward grows the panel.
  useEffect(() => {
    if (mode !== "expanded") return;
    function onMove(e: MouseEvent) {
      if (!resizingRef.current) return;
      setExpandedWidth(clampWidth(window.innerWidth - e.clientX));
    }
    function onUp() {
      if (resizingRef.current) {
        resizingRef.current = false;
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
      }
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [mode]);

  // Pop into expanded mode whenever the drafting context fires an
  // expand request (e.g. just after a template-seeded doc was created).
  // We watch the tick rather than ``drafting`` itself so re-visiting an
  // already-drafting page doesn't keep yanking the widget open.
  useEffect(() => {
    if (expandTick === 0) return;
    setMode((prev) => {
      // Remember what the user had before the automatic expand so we can
      // put it back when drafting ends. Only the first capture per
      // drafting episode counts, and an already-expanded widget needs no
      // restore.
      if (preDraftingModeRef.current === null && prev !== "expanded") {
        preDraftingModeRef.current = prev;
      }
      return "expanded";
    });
  }, [expandTick]);

  // Drafting orchestration. When the wiki page raises a new drafting
  // state we (a) save the current chat as the pre-drafting snapshot,
  // (b) swap to a fresh hidden session, and (c) call the init endpoint
  // so the agent kicks off with template-aware guiding questions (or a
  // generic "what do you want to work on" prime for blank). When the
  // page leaves drafting we restore the snapshot.
  //
  // We watch a derived ``desiredKey`` rather than ``templateId``
  // directly so that "blank" is a distinct watch value (template_A →
  // blank → template_A all re-trigger init), and so a deleted template
  // id (null) doesn't collide with the "no drafting" state.
  const desiredKey: string | null =
    drafting === null
      ? null
      : drafting.kind === "template"
        ? `tpl:${drafting.templateId ?? "deleted"}`
        : drafting.prompt
          ? `ai:${drafting.prompt}`
          : "blank";

  // Keep the banner in sync while drafting is active (path/template name
  // can refine on the NewDocView → FileViewer hand-off). When ``drafting``
  // goes null we deliberately hold the last value — the deactivation
  // below clears it together with the mode restore.
  useEffect(() => {
    if (drafting !== null) setDraftingBanner(drafting);
  }, [drafting]);

  useEffect(() => {
    // Activate immediately when a drafting state arrives.
    if (desiredKey !== null && drafting !== null) {
      if (desiredKey === draftingKey) return; // already in sync
      if (preDraftingRef.current === null) {
        // First entry into drafting — remember the prior conversation.
        preDraftingRef.current = { sessionId, items };
      }
      setDraftingKey(desiredKey);
      setError(null);
      setSessionId(null);
      const tidForInit =
        drafting.kind === "template" ? drafting.templateId : null;
      const promptForInit =
        drafting.kind === "blank" ? (drafting.prompt ?? null) : null;
      // For the AI flow, show the user's prompt as the first turn; the reducer
      // then pushes the assistant's reply as events arrive. Otherwise start
      // empty (the hidden seed primes a kickoff that lands as a text_delta).
      setItems(promptForInit ? [{ kind: "user", content: promptForInit }] : []);
      setSending(true);
      void (async () => {
        try {
          await streamDraftingInit(
            tidForInit,
            (raw) => {
              const ev = raw as StreamEvent;
              if (ev.type === "session_created") {
                setSessionId(ev.session_id);
              } else if (ev.type === "error") {
                setError(ev.message);
                setItems((prev) => markRunningToolsAsError(prev));
              } else {
                setItems((prev) => reduceEvent(prev, ev));
              }
            },
            { prompt: promptForInit },
          );
        } catch (e) {
          setError(formatError(e));
          setItems((prev) => markRunningToolsAsError(prev));
        } finally {
          setSending(false);
        }
      })();
      return;
    }

    // Deactivate. Null always means drafting really ended — the
    // NewDocView → FileViewer create hand-off keeps the drafting state
    // alive across navigation instead of passing through null — so we
    // tear down synchronously. Everything reverts in one batched render
    // (banner gone + mode restored + conversation swapped), in the same
    // paint as the page change that ended drafting, rather than the chat
    // visibly collapsing a beat after the page already moved on.
    if (draftingKey === null) return;
    const snapshot = preDraftingRef.current;
    preDraftingRef.current = null;
    setDraftingKey(null);
    if (snapshot) {
      setSessionId(snapshot.sessionId);
      setItems(snapshot.items);
    } else {
      setSessionId(null);
      setItems([]);
    }
    // If drafting force-expanded the widget (and the user never
    // touched the mode since), drop back to whatever it was before,
    // the expanded bar or the collapsed pill.
    const priorMode = preDraftingModeRef.current;
    preDraftingModeRef.current = null;
    if (priorMode !== null) setMode(priorMode);
    // Cleared here (not via the ``drafting`` sync effect) so the banner,
    // mode, and conversation all revert in one render — see the
    // ``draftingBanner`` declaration.
    setDraftingBanner(null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desiredKey]);

  // When expanded, reserve real layout space on the right so the page is
  // pushed left rather than being overlaid by the panel. Layout effect so
  // the padding lands in the same paint as the panel itself — with a plain
  // effect the collapse painted first and the page reflowed a frame later
  // (panel gone, gap still there → visible two-step).
  useLayoutEffect(() => {
    if (mode !== "expanded") {
      document.body.style.paddingRight = "";
      return;
    }
    document.body.style.paddingRight = `${expandedWidth}px`;
    return () => {
      document.body.style.paddingRight = "";
    };
  }, [mode, expandedWidth]);

  const sendUserMessage = useCallback(
    async (text: string) => {
      setError(null);
      setSending(true);

      // Optimistically place the user turn. We don't pre-push an empty
      // assistant; ``reduceEvent`` opens one when the first text_delta
      // arrives. Until then, the "…" placeholder below covers the gap.
      setItems((prev) => [...prev, { kind: "user", content: text }]);

      // Unsaved new-doc drafting (kind "blank", no path) with content in the
      // editor: live-edit it via the stateless reviser and write the result
      // straight back to the editor — no saved-doc chat session involved.
      const body = getDraftBody();
      if (
        drafting?.kind === "blank" &&
        drafting.path === null &&
        body !== null &&
        body.trim() !== ""
      ) {
        try {
          const res = await reviseDraft(body, text);
          applyDraftBody(res.body);
          setItems((prev) => [
            ...prev,
            {
              kind: "assistant",
              content: "Done — I've updated the draft in the editor.",
            },
          ]);
        } catch (err) {
          setError(formatError(err));
        } finally {
          setSending(false);
        }
        return;
      }

      // Lazily create a server-side session on first send so empty
      // sessions don't pile up if the user opens and closes the widget.
      let activeId = sessionId;
      let createdSession: ChatSession | null = null;
      if (!activeId) {
        try {
          createdSession = await createSession();
          activeId = createdSession.id;
          setSessionId(activeId);
        } catch (e) {
          setError(formatError(e));
          setSending(false);
          // Roll back optimistic insert.
          setItems((prev) => prev.slice(0, -1));
          return;
        }
      }

      let streamFailed = false;
      try {
        await streamMessage(
          activeId,
          text,
          (raw) => {
            const ev = raw as StreamEvent;
            if (ev.type === "error") {
              streamFailed = true;
              setError(ev.message);
              setItems((prev) => markRunningToolsAsError(prev));
              return;
            }
            setItems((prev) => reduceEvent(prev, ev));
          },
          { currentPath: currentWikiPath },
        );
      } catch (err) {
        streamFailed = true;
        setError(formatError(err));
        setItems((prev) => markRunningToolsAsError(prev));
      } finally {
        setSending(false);
        if (streamFailed) {
          // Drop a trailing empty assistant the reducer never got to
          // fill — otherwise the bubble would render as an empty box.
          setItems((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.kind === "assistant" && last.content === "") {
              return prev.slice(0, -1);
            }
            return prev;
          });
        } else {
          // Stream finished cleanly — refresh history so a freshly-
          // generated title can show up in the panel.
          setHistoryRefreshKey((k) => k + 1);
        }
      }
    },
    [sessionId, drafting, getDraftBody, applyDraftBody, currentWikiPath],
  );

  const onSend = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const text = input.trim();
      if (!text || sending) return;
      setInput("");
      await sendUserMessage(text);
    },
    [input, sending, sendUserMessage],
  );

  const onRetry = useCallback(async () => {
    if (sending || items.length === 0) return;
    const last = items[items.length - 1];
    if (last.kind !== "user") return;
    // Drop the prior user message and re-send it. The backend already
    // persisted it on the first attempt, so resending would double it;
    // instead we just kick off a retry against the same content with
    // the existing history intact: pop the user msg, then send.
    setItems((prev) => prev.slice(0, -1));
    await sendUserMessage(last.content);
  }, [sending, items, sendUserMessage]);

  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    resizingRef.current = true;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
  }, []);

  const onSelectSession = useCallback(
    async (id: string) => {
      setHistoryOpen(false);
      setError(null);
      if (id === sessionId) return;
      try {
        const detail = await getSession(id);
        setSessionId(id);
        setItems(itemsFromPersisted(detail.messages));
      } catch (e) {
        setError(formatError(e));
      }
    },
    [sessionId],
  );

  const onNewChat = useCallback(() => {
    setHistoryOpen(false);
    setError(null);
    setSessionId(null);
    setItems([]);
    inputRef.current?.focus();
  }, []);

  // A send from the bar docks the panel first so the streamed reply has
  // somewhere visible to land.
  const sendFromBar = useCallback(() => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setModeManually("expanded");
    void sendUserMessage(text);
  }, [input, sending, sendUserMessage, setModeManually]);

  if (!user) return null;

  if (mode !== "expanded") {
    return (
      <ChatBar
        collapsed={mode === "closed"}
        onExpand={() => setModeManually("widget")}
        onCollapse={() => setModeManually("closed")}
        onDock={() => setModeManually("expanded")}
        input={input}
        onInputChange={setInput}
        onSubmit={sendFromBar}
        sending={sending}
      />
    );
  }

  return (
    <div
      role="dialog"
      aria-label="Chat"
      className="fixed top-0 right-0 z-[1000] h-screen border-l border-(--border-02) bg-(--background-tint-00) shadow-(--shadow-panel)"
      style={{ width: expandedWidth }}
    >
      {/* Inner clipped surface. Keeps the history panel's slide animation
          contained and lets it cover the chat header. The resize handle
          lives outside this so it can extend past the left edge. */}
      <div className="relative flex h-full w-full flex-col overflow-hidden">
        <header className="flex shrink-0 items-center gap-2 border-b border-(--border-01) bg-(--background-tint-01) px-3 py-[10px]">
          <div className="text-sm font-semibold">Chat</div>
          <div className="flex-1" />
          <Button
            icon={SvgEdit}
            prominence="tertiary"
            size="sm"
            tooltip="New chat"
            onClick={onNewChat}
            disabled={sending || (sessionId === null && items.length === 0)}
          />
          <Button
            icon={SvgHistory}
            prominence="tertiary"
            size="sm"
            tooltip="History"
            onClick={() => setHistoryOpen((v) => !v)}
          />
          <Button
            icon={SvgFold}
            prominence="tertiary"
            size="sm"
            tooltip="Collapse to bar"
            onClick={() => setModeManually("widget")}
          />
          <Button
            icon={SvgX}
            prominence="tertiary"
            size="sm"
            tooltip="Close"
            onClick={() => setModeManually("closed")}
          />
        </header>

        {draftingBanner && <DraftingBanner state={draftingBanner} />}

        <div
          ref={scrollRef}
          className="flex min-h-0 flex-1 flex-col gap-[10px] overflow-y-auto p-3"
        >
          {items.length === 0 && (
            <p className="m-0 text-[13px] text-(--text-03)">
              Hi, I can help create pages, make changes, explain things, help
              you create triggers, or explain how this wiki works. Ask me
              anything!
            </p>
          )}
          {items.map((it, i) => {
            if (it.kind === "tool") return <ToolStatus key={i} item={it} />;
            // Skip the optimistic empty assistant bubble — the "…" placeholder
            // below renders in its place while we wait for the first delta.
            if (it.kind === "assistant" && it.content === "") return null;
            return <Bubble key={i} role={it.kind} content={it.content} />;
          })}
          {sending && shouldShowEllipsis(items) && (
            <Bubble role="assistant" content="…" muted />
          )}
        </div>

        {error && (
          <div
            role="alert"
            className="mx-3 mb-2 flex items-start gap-2 rounded-(--radius-04) border border-(--status-error-02) bg-(--status-error-01) px-[10px] py-2 text-xs text-(--status-text-error-05)"
          >
            <div className="flex-1 whitespace-pre-wrap">{error}</div>
            {items.length > 0 && items[items.length - 1].kind === "user" && (
              <div className="shrink-0">
                <Button
                  variant="danger"
                  prominence="secondary"
                  size="sm"
                  disabled={sending}
                  onClick={onRetry}
                >
                  Retry
                </Button>
              </div>
            )}
          </div>
        )}

        <form
          onSubmit={onSend}
          className="flex shrink-0 gap-[6px] border-t border-(--border-01) p-[10px]"
        >
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void onSend(e as unknown as FormEvent);
              }
            }}
            placeholder="Send a message…"
            rows={2}
            disabled={sending}
            className="box-border min-w-0 flex-1 resize-none rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) p-2 font-[inherit] text-[13px] text-(--text-05) outline-none focus:border-(--border-05)"
          />
          <Button
            type="submit"
            variant="action"
            prominence="primary"
            disabled={sending || !input.trim()}
          >
            Send
          </Button>
        </form>

        <ChatHistoryPanel
          open={historyOpen}
          activeSessionId={sessionId}
          onSelect={(id) => void onSelectSession(id)}
          onNewChat={onNewChat}
          onClose={() => setHistoryOpen(false)}
          refreshKey={historyRefreshKey}
        />
      </div>

      <div
        onMouseDown={startResize}
        title="Drag to resize"
        aria-label="Resize chat panel"
        role="separator"
        className="absolute top-0 left-[-3px] z-[1001] h-full w-[6px] cursor-col-resize"
      />
    </div>
  );
}

function clampWidth(n: number): number {
  // Leave at least ~80px of page visible so the user can still see / click
  // the AppShell sidebar without collapsing the panel.
  const max =
    typeof window !== "undefined"
      ? Math.max(MIN_EXPANDED_WIDTH, window.innerWidth - 80)
      : 1200;
  return Math.min(max, Math.max(MIN_EXPANDED_WIDTH, n));
}

// Apply one streamed event to the chat transcript. ``text_delta`` either
// extends the trailing assistant bubble or opens a new one (when the
// previous item was a tool call between iterations). ``tool_call``
// pushes a status line; ``tool_result`` flips its matching line to
// ``done``. The ``iteration_done``/``done`` terminators don't need
// explicit handling — the next ``text_delta`` will naturally start a
// fresh assistant bubble because the trailing item is a tool, not text.
function reduceEvent(items: ChatItem[], ev: StreamEvent): ChatItem[] {
  if (ev.type === "text_delta") {
    const last = items[items.length - 1];
    if (last && last.kind === "assistant") {
      const next = items.slice();
      next[next.length - 1] = { ...last, content: last.content + ev.text };
      return next;
    }
    return [...items, { kind: "assistant", content: ev.text }];
  }
  if (ev.type === "tool_call") {
    // Hidden tools (e.g. ``load_skill`` plumbing) never enter the
    // transcript. A later ``tool_result`` for the same id will no-op
    // through the ``map`` below, so we don't need a second guard.
    if (presentTool(ev.name).hidden) return items;
    return [
      ...items,
      { kind: "tool", id: ev.id, name: ev.name, state: "running" },
    ];
  }
  if (ev.type === "tool_result") {
    return items.map((it) =>
      it.kind === "tool" && it.id === ev.id ? { ...it, state: "done" } : it,
    );
  }
  return items;
}

// Convert persisted server-side messages back into ChatItems for replay
// on session load. Assistant rows carry the full event log under
// ``events``; we replay those so tool calls reappear inline. Legacy
// rows without an event log fall back to a single content bubble.
function itemsFromPersisted(messages: PersistedChatMessage[]): ChatItem[] {
  let out: ChatItem[] = [];
  for (const m of messages) {
    if (m.role === "user") {
      out = [...out, { kind: "user", content: m.content }];
      continue;
    }
    if (!m.events || m.events.length === 0) {
      out = [...out, { kind: "assistant", content: m.content }];
      continue;
    }
    for (const raw of m.events) {
      out = reduceEvent(out, raw as StreamEvent);
    }
  }
  return out;
}

// Show the "…" placeholder only when there's no live signal yet — i.e.
// no text streaming into an assistant bubble and no tool running.
function shouldShowEllipsis(items: ChatItem[]): boolean {
  const last = items[items.length - 1];
  if (!last) return true;
  if (last.kind === "assistant" && last.content !== "") return false;
  if (last.kind === "tool" && last.state === "running") return false;
  return true;
}

// When the stream fails mid-flight, any tool whose ``tool_result`` we
// never received is stuck "running". Flip those to "error" so the
// transcript doesn't lie about ongoing activity.
function markRunningToolsAsError(items: ChatItem[]): ChatItem[] {
  return items.map((it) =>
    it.kind === "tool" && it.state === "running"
      ? { ...it, state: "error" }
      : it,
  );
}

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 503) return `${err.message} (server status 503)`;
    if (err.status === 429) return `${err.message} (rate limited)`;
    if (err.status === 504 || err.status === 502)
      return `${err.message} (provider error ${err.status})`;
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return "Failed to send message";
}

function DraftingBanner({ state }: { state: DraftingState }) {
  const docName = state.path
    ? (state.path.split("/").pop() ?? state.path)
    : null;
  const templateName = state.kind === "template" ? state.templateName : null;
  return (
    <div
      role="status"
      className="flex shrink-0 items-start gap-2 border-b border-(--border-01) bg-(--background-tint-03) px-3 py-2 text-xs text-(--text-05)"
    >
      <span className="mt-[1px] flex shrink-0">
        <SvgDocFile size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-semibold">Drafting initial version</div>
        <div
          className="mt-[2px] text-(--text-04)"
          style={{ overflowWrap: "anywhere" }}
        >
          {docName && templateName ? (
            <>
              Helping draft <strong>{docName}</strong> from the{" "}
              <em>{templateName}</em> template.
            </>
          ) : docName ? (
            <>
              Helping draft <strong>{docName}</strong>.
            </>
          ) : templateName ? (
            <>
              Helping draft a new doc from the <em>{templateName}</em> template.
            </>
          ) : (
            <>Helping draft a new doc.</>
          )}
        </div>
      </div>
    </div>
  );
}

const markdownComponents: Components = {
  p: ({ children }) => <p className={styles.markdownP}>{children}</p>,
  ul: ({ children }) => <ul className={styles.markdownUl}>{children}</ul>,
  ol: ({ children }) => <ol className={styles.markdownOl}>{children}</ol>,
  li: ({ children }) => <li className={styles.markdownLi}>{children}</li>,
  h1: ({ children }) => <h1 className={styles.markdownHeading}>{children}</h1>,
  h2: ({ children }) => <h2 className={styles.markdownHeading}>{children}</h2>,
  h3: ({ children }) => <h3 className={styles.markdownHeading}>{children}</h3>,
  h4: ({ children }) => <h4 className={styles.markdownHeading}>{children}</h4>,
  code: ({ children }) => (
    <code className={styles.markdownCode}>{children}</code>
  ),
  pre: ({ children }) => <pre className={styles.markdownPre}>{children}</pre>,
};

function Bubble({
  role,
  content,
  muted = false,
}: {
  role: "user" | "assistant";
  content: string;
  muted?: boolean;
}) {
  const isUser = role === "user";
  const renderMarkdown = !isUser && !muted;
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} min-w-0`}>
      <div
        className={`max-w-[85%] min-w-0 rounded-(--radius-08) px-3 py-2 text-[13px] leading-[1.5] ${
          isUser
            ? "bg-(--background-tint-inverted-00) text-(--text-inverted-05)"
            : "bg-(--background-tint-02) text-(--text-05)"
        } ${renderMarkdown ? "whitespace-normal" : "whitespace-pre-wrap"} ${muted ? "opacity-60" : "opacity-100"}`}
        // Without this, a long unbroken token (URL, path, hash) blows
        // past the 85% max-width and pushes the layout horizontally.
        style={{ overflowWrap: "anywhere" }}
      >
        {renderMarkdown ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkBareSpaceLinks]}
            components={markdownComponents}
          >
            {content}
          </ReactMarkdown>
        ) : (
          content
        )}
      </div>
    </div>
  );
}

// Inline status line for a tool call. Lowest visual weight by design —
// tools are ambient activity, not the main content. The state icon
// (spinner / check / ×) carries the running/done/error semantics, so
// the label itself stays state-neutral — a gerund phrase like
// "Searching the wiki" that reads naturally with either a running
// spinner or a completed check. Labels come from ``presentTool`` so
// they live in a single registry (``src/lib/tools.ts``) — unknown
// tools fall back to their raw name.
function ToolStatus({ item }: { item: Extract<ChatItem, { kind: "tool" }> }) {
  const { label } = presentTool(item.name);
  return (
    <div className="flex items-center gap-2 pl-1 text-xs text-(--text-03) italic">
      <ToolStateIcon state={item.state} />
      <span>{label}</span>
    </div>
  );
}

function ToolStateIcon({ state }: { state: ToolState }) {
  if (state === "running") {
    return (
      <span
        aria-label="running"
        className={`inline-block h-[10px] w-[10px] rounded-full border-2 border-(--border-01) ${styles.toolSpinner}`}
      />
    );
  }
  if (state === "error") {
    return (
      <span className="flex text-(--status-text-error-05)">
        <SvgXCircle size={12} />
      </span>
    );
  }
  return (
    <span className="flex text-(--status-text-success-05)">
      <SvgCheck size={12} />
    </span>
  );
}

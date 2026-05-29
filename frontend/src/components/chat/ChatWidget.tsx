"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@onyx-ai/opal/components";
import {
  SvgBubbleText,
  SvgCheck,
  SvgDocFile,
  SvgEdit,
  SvgExpand,
  SvgFold,
  SvgHistory,
  SvgX,
  SvgXCircle,
} from "@onyx-ai/opal/icons";

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
import { ChatHistoryPanel } from "@/components/chat/ChatHistoryPanel";
import { color, radius, shadow } from "@/lib/theme";
import { presentTool } from "@/lib/tools";

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
  | { type: "tool_call"; id: string; name: string; arguments: Record<string, unknown> }
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
  const { drafting, expandTick } = useDrafting();
  const [mode, setMode] = useState<Mode>("closed");
  const [expandedWidth, setExpandedWidth] = useState<number>(DEFAULT_EXPANDED_WIDTH);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
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
  const preDraftingRef = useRef<{ sessionId: string | null; items: ChatItem[] } | null>(
    null,
  );

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

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY_MODE, mode);
    } catch {
      // ignore
    }
  }, [mode]);

  useEffect(() => {
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
      if (sessionId) window.localStorage.setItem(STORAGE_KEY_SESSION, sessionId);
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
    setMode("expanded");
  }, [expandTick]);

  // Drafting orchestration. When the wiki page raises a new drafting
  // state we (a) save the current chat as the pre-drafting snapshot,
  // (b) swap to a fresh hidden session, and (c) call the init endpoint
  // so the agent kicks off with template-aware guiding questions (or a
  // generic "what do you want to work on" prime for blank). When the
  // page leaves drafting we restore the snapshot. Brief null
  // transitions (NewDocView → FileViewer hand-off) are debounced so we
  // don't flap.
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
        : "blank";
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
      // Start empty; the reducer will push items as events arrive
      // (text_delta creates an assistant bubble, tool_call pushes a
      // tool status line, etc.).
      setItems([]);
      setSending(true);
      const tidForInit =
        drafting.kind === "template" ? drafting.templateId : null;
      void (async () => {
        try {
          await streamDraftingInit(tidForInit, (raw) => {
            const ev = raw as StreamEvent;
            if (ev.type === "session_created") {
              setSessionId(ev.session_id);
            } else if (ev.type === "error") {
              setError(ev.message);
              setItems((prev) => markRunningToolsAsError(prev));
            } else {
              setItems((prev) => reduceEvent(prev, ev));
            }
          });
        } catch (e) {
          setError(formatError(e));
          setItems((prev) => markRunningToolsAsError(prev));
        } finally {
          setSending(false);
        }
      })();
      return;
    }

    // Deactivate, but debounce so a brief null between page hand-offs
    // (NewDocView unmounts before FileViewer mounts) doesn't tear down
    // the drafting conversation only to spin up another one immediately.
    if (draftingKey === null) return;
    const handle = window.setTimeout(() => {
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
      setError(null);
    }, 300);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desiredKey]);

  // When expanded, reserve real layout space on the right so the page is
  // pushed left rather than being overlaid by the panel.
  useEffect(() => {
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
        await streamMessage(activeId, text, (raw) => {
          const ev = raw as StreamEvent;
          if (ev.type === "error") {
            streamFailed = true;
            setError(ev.message);
            setItems((prev) => markRunningToolsAsError(prev));
            return;
          }
          setItems((prev) => reduceEvent(prev, ev));
        });
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
    [sessionId],
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

  const onSelectSession = useCallback(async (id: string) => {
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
  }, [sessionId]);

  const onNewChat = useCallback(() => {
    setHistoryOpen(false);
    setError(null);
    setSessionId(null);
    setItems([]);
  }, []);

  if (!user) return null;

  if (mode === "closed") {
    return (
      <button
        onClick={() => setMode("widget")}
        title="Open chat"
        aria-label="Open chat"
        style={{
          position: "fixed",
          right: 20,
          bottom: 20,
          width: 48,
          height: 48,
          borderRadius: radius.lg,
          background: color.accent.bg,
          color: color.accent.fg,
          border: "none",
          cursor: "pointer",
          boxShadow: shadow.fab,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
        }}
      >
        <SvgBubbleText size={24} />
      </button>
    );
  }

  const isExpanded = mode === "expanded";
  const containerStyle: React.CSSProperties = isExpanded
    ? {
        position: "fixed",
        top: 0,
        right: 0,
        height: "100vh",
        width: expandedWidth,
        background: color.bg.page,
        borderLeft: `1px solid ${color.border.strong}`,
        boxShadow: shadow.panel,
        zIndex: 1000,
      }
    : {
        position: "fixed",
        right: 20,
        bottom: 20,
        // Clamp width and height so the widget never overflows on
        // narrow phones. `calc(100vw - 24px)` leaves 4px of breathing
        // room either side of the right:20 anchor.
        width: "min(380px, calc(100vw - 24px))",
        height: "min(560px, calc(100vh - 80px))",
        background: color.bg.page,
        border: `1px solid ${color.border.default}`,
        borderRadius: radius.lg,
        boxShadow: shadow.modal,
        zIndex: 1000,
      };

  return (
    <div style={containerStyle} role="dialog" aria-label="Chat">
      {/* Inner clipped surface — keeps the history panel's slide animation
          contained and lets it cover the chat header. The resize handle in
          expanded mode lives outside this so it can extend past the left edge. */}
      <div
        style={{
          position: "relative",
          height: "100%",
          width: "100%",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          borderRadius: isExpanded ? 0 : radius.lg,
        }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 12px",
            borderBottom: `1px solid ${color.border.subtle}`,
            background: color.bg.panel,
            flexShrink: 0,
          }}
        >
          <div style={{ fontWeight: 600, fontSize: 14 }}>Chat</div>
          <div style={{ flex: 1 }} />
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
            icon={isExpanded ? SvgFold : SvgExpand}
            prominence="tertiary"
            size="sm"
            tooltip={isExpanded ? "Collapse" : "Expand"}
            onClick={() => setMode(isExpanded ? "widget" : "expanded")}
          />
          <Button
            icon={SvgX}
            prominence="tertiary"
            size="sm"
            tooltip="Close"
            onClick={() => setMode("closed")}
          />
        </header>

        {drafting && <DraftingBanner state={drafting} />}

        <div
          ref={scrollRef}
          style={{
            flex: 1,
            overflowY: "auto",
            padding: 12,
            display: "flex",
            flexDirection: "column",
            gap: 10,
            minHeight: 0,
          }}
        >
          {items.length === 0 && (
            <p style={{ color: color.text.muted, fontSize: 13, margin: 0 }}>
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
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 8,
              margin: "0 12px 8px",
              padding: "8px 10px",
              background: color.state.danger.bg,
              border: `1px solid ${color.state.danger.border}`,
              color: color.state.danger.fg,
              borderRadius: radius.sm,
              fontSize: 12,
            }}
          >
            <div style={{ flex: 1, whiteSpace: "pre-wrap" }}>{error}</div>
            {items.length > 0 && items[items.length - 1].kind === "user" && (
              <button
                onClick={onRetry}
                disabled={sending}
                style={{
                  padding: "3px 8px",
                  background: color.bg.page,
                  border: `1px solid ${color.state.danger.border}`,
                  borderRadius: radius.xs,
                  color: color.state.danger.fg,
                  cursor: sending ? "not-allowed" : "pointer",
                  fontSize: 11,
                  fontWeight: 600,
                  flexShrink: 0,
                }}
              >
                Retry
              </button>
            )}
          </div>
        )}

        <form
          onSubmit={onSend}
          style={{
            display: "flex",
            gap: 6,
            padding: 10,
            borderTop: `1px solid ${color.border.subtle}`,
            flexShrink: 0,
          }}
        >
          <textarea
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
            style={{
              flex: 1,
              minWidth: 0,
              boxSizing: "border-box",
              resize: "none",
              padding: 8,
              border: `1px solid ${color.border.default}`,
              borderRadius: radius.sm,
              fontFamily: "inherit",
              fontSize: 13,
              color: color.text.primary,
              background: color.bg.page,
            }}
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            style={{
              padding: "0 14px",
              background: color.accent.bg,
              color: color.accent.fg,
              border: "none",
              borderRadius: radius.sm,
              cursor: sending || !input.trim() ? "not-allowed" : "pointer",
              opacity: sending || !input.trim() ? 0.5 : 1,
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            Send
          </button>
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

      {isExpanded && (
        <div
          onMouseDown={startResize}
          title="Drag to resize"
          aria-label="Resize chat panel"
          role="separator"
          style={{
            position: "absolute",
            top: 0,
            left: -3,
            width: 6,
            height: "100%",
            cursor: "col-resize",
            zIndex: 1001,
          }}
        />
      )}
    </div>
  );
}

function clampWidth(n: number): number {
  // Leave at least ~80px of page visible so the user can still see / click
  // the AppShell sidebar without collapsing the panel.
  const max = typeof window !== "undefined" ? Math.max(MIN_EXPANDED_WIDTH, window.innerWidth - 80) : 1200;
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
    return [...items, { kind: "tool", id: ev.id, name: ev.name, state: "running" }];
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
    it.kind === "tool" && it.state === "running" ? { ...it, state: "error" } : it,
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
  const docName = state.path ? state.path.split("/").pop() ?? state.path : null;
  const templateName = state.kind === "template" ? state.templateName : null;
  return (
    <div
      role="status"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "8px 12px",
        background: color.accent.subtleBg,
        borderBottom: `1px solid ${color.accent.subtleBorder}`,
        color: color.accent.subtleFg,
        fontSize: 12,
        flexShrink: 0,
      }}
    >
      <span style={{ flexShrink: 0, marginTop: 1, display: "flex" }}>
        <SvgDocFile size={16} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>Drafting initial version</div>
        <div style={{ marginTop: 2, color: color.text.secondary, overflowWrap: "anywhere" }}>
          {docName && templateName ? (
            <>
              Helping draft <strong>{docName}</strong> from the{" "}
              <em>{templateName}</em> template.
            </>
          ) : docName ? (
            <>Helping draft <strong>{docName}</strong>.</>
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
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", minWidth: 0 }}>
      <div
        className={renderMarkdown ? "markdown markdown-chat" : undefined}
        style={{
          maxWidth: "85%",
          minWidth: 0,
          padding: "8px 12px",
          borderRadius: radius.md,
          background: isUser ? color.accent.bg : color.bg.sunken,
          color: isUser ? color.accent.fg : color.text.primary,
          // User messages preserve newlines via pre-wrap; assistant
          // messages flow through react-markdown which produces real
          // block elements, so pre-wrap would just inject blank gaps.
          whiteSpace: renderMarkdown ? "normal" : "pre-wrap",
          // Without this, a long unbroken token (URL, path, hash) blows
          // past the 85% max-width and pushes the layout horizontally.
          overflowWrap: "anywhere",
          fontSize: 13,
          lineHeight: 1.5,
          opacity: muted ? 0.6 : 1,
        }}
      >
        {renderMarkdown ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
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
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        paddingLeft: 4,
        fontSize: 12,
        color: color.text.muted,
        fontStyle: "italic",
      }}
    >
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
        style={{
          display: "inline-block",
          width: 10,
          height: 10,
          borderRadius: "50%",
          border: `2px solid ${color.border.default}`,
          borderTopColor: color.text.secondary,
          animation: "chat-tool-spin 0.7s linear infinite",
        }}
      >
        <style>{`@keyframes chat-tool-spin { to { transform: rotate(360deg); } }`}</style>
      </span>
    );
  }
  if (state === "error") {
    return (
      <span style={{ color: color.state.danger.fg, display: "flex" }}>
        <SvgXCircle size={12} />
      </span>
    );
  }
  return (
    <span style={{ color: color.state.success.fg, display: "flex" }}>
      <SvgCheck size={12} />
    </span>
  );
}


"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  createSession,
  getSession,
  streamMessage,
  type ChatSession,
} from "@/lib/chat";
import { ChatHistoryPanel } from "@/components/chat/ChatHistoryPanel";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

type StreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "tool_call"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; content: string }
  | { type: "iteration_done" }
  | { type: "done" }
  | { type: "error"; code: string; message: string };

type Mode = "closed" | "widget" | "expanded";

const STORAGE_KEY_MODE = "chat-widget:mode";
const STORAGE_KEY_WIDTH = "chat-widget:expanded-width";
const STORAGE_KEY_SESSION = "chat-widget:session-id";
const DEFAULT_EXPANDED_WIDTH = 480;
const MIN_EXPANDED_WIDTH = 280;

export function ChatWidget() {
  const { user } = useAuth();
  const [mode, setMode] = useState<Mode>("closed");
  const [expandedWidth, setExpandedWidth] = useState<number>(DEFAULT_EXPANDED_WIDTH);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [toolHint, setToolHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const resizingRef = useRef(false);
  const hydratedSessionRef = useRef(false);

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
    try {
      if (sessionId) window.localStorage.setItem(STORAGE_KEY_SESSION, sessionId);
      else window.localStorage.removeItem(STORAGE_KEY_SESSION);
    } catch {
      // ignore
    }
  }, [sessionId]);

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
        setMessages(
          detail.messages.map((m) => ({ role: m.role, content: m.content })),
        );
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          // Session was deleted on another device — start fresh.
          setSessionId(null);
          setMessages([]);
        } else {
          setError(formatError(e));
        }
      }
    })();
  }, [mode, sessionId]);

  useEffect(() => {
    if (mode === "closed") return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending, mode]);

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
      setToolHint(null);

      // Optimistically place the user + empty-assistant pair.
      setMessages((prev) => [
        ...prev,
        { role: "user", content: text },
        { role: "assistant", content: "" },
      ]);

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
          setMessages((prev) => prev.slice(0, -2));
          return;
        }
      }

      let streamFailed = false;
      try {
        await streamMessage(activeId, text, (raw) => {
          const ev = raw as StreamEvent;
          switch (ev.type) {
            case "text_delta":
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (!last || last.role !== "assistant") return prev;
                next[next.length - 1] = { ...last, content: last.content + ev.text };
                return next;
              });
              break;
            case "tool_call":
              setToolHint(`${humanizeTool(ev.name)}…`);
              break;
            case "tool_result":
            case "iteration_done":
            case "done":
              setToolHint(null);
              break;
            case "error":
              streamFailed = true;
              setError(ev.message);
              break;
          }
        });
      } catch (err) {
        streamFailed = true;
        setError(formatError(err));
      } finally {
        setSending(false);
        setToolHint(null);
        if (streamFailed) {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.role === "assistant" && last.content === "") return prev.slice(0, -1);
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
    if (sending || messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (last.role !== "user") return;
    // Drop the prior user message and re-send it. The backend already
    // persisted it on the first attempt, so resending would double it;
    // instead we just kick off a retry against the same content with
    // the existing history intact: pop the user msg, then send.
    setMessages((prev) => prev.slice(0, -1));
    await sendUserMessage(last.content);
  }, [sending, messages, sendUserMessage]);

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
      setMessages(
        detail.messages.map((m) => ({ role: m.role, content: m.content })),
      );
    } catch (e) {
      setError(formatError(e));
    }
  }, [sessionId]);

  const onNewChat = useCallback(() => {
    setHistoryOpen(false);
    setError(null);
    setSessionId(null);
    setMessages([]);
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
          width: 56,
          height: 56,
          borderRadius: "50%",
          background: "#6366f1",
          color: "white",
          border: "none",
          cursor: "pointer",
          boxShadow: "0 6px 20px rgba(0,0,0,0.18)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
        }}
      >
        <ChatBubbleIcon />
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
        background: "white",
        borderLeft: "1px solid #e5e7eb",
        boxShadow: "-4px 0 24px rgba(0,0,0,0.08)",
        display: "flex",
        flexDirection: "column",
        zIndex: 1000,
      }
    : {
        position: "fixed",
        right: 20,
        bottom: 20,
        width: 380,
        height: 560,
        maxHeight: "calc(100vh - 40px)",
        background: "white",
        border: "1px solid #e5e7eb",
        borderRadius: 12,
        boxShadow: "0 12px 32px rgba(0,0,0,0.18)",
        display: "flex",
        flexDirection: "column",
        zIndex: 1000,
        overflow: "hidden",
      };

  return (
    <div style={containerStyle} role="dialog" aria-label="Chat">
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 12px",
          borderBottom: "1px solid #eee",
          background: "#fafafa",
          flexShrink: 0,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 14, flex: 1 }}>Chat</div>
        <IconButton
          title="New chat"
          onClick={onNewChat}
          disabled={sending || (sessionId === null && messages.length === 0)}
        >
          <NewChatIcon />
        </IconButton>
        <IconButton
          title="History"
          onClick={() => setHistoryOpen((v) => !v)}
        >
          <HistoryIcon />
        </IconButton>
        <IconButton
          title={isExpanded ? "Collapse" : "Expand"}
          onClick={() => setMode(isExpanded ? "widget" : "expanded")}
        >
          {isExpanded ? <CollapseIcon /> : <ExpandIcon />}
        </IconButton>
        <IconButton title="Close" onClick={() => setMode("closed")}>
          <CloseIcon />
        </IconButton>
      </header>

      <div style={{ position: "relative", flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div
          ref={scrollRef}
          style={{
            flex: 1,
            overflowY: "auto",
            padding: 12,
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {messages.length === 0 && (
            <p style={{ color: "#888", fontSize: 13, margin: 0 }}>
              Hi, I can help create pages, make changes, explain things, help
              you create triggers, or explain how this wiki works. Ask me
              anything!
            </p>
          )}
          {messages.map((m, i) => (
            <Bubble key={i} role={m.role} content={m.content} />
          ))}
          {sending && toolHint && (
            <div style={{ paddingLeft: 4, color: "#6b7280", fontStyle: "italic", fontSize: 13 }}>
              {toolHint}
            </div>
          )}
          {sending && !toolHint && messages[messages.length - 1]?.content === "" && (
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
              background: "#fef2f2",
              border: "1px solid #fecaca",
              color: "#991b1b",
              borderRadius: 6,
              fontSize: 12,
            }}
          >
            <div style={{ flex: 1, whiteSpace: "pre-wrap" }}>{error}</div>
            {messages.length > 0 && messages[messages.length - 1].role === "user" && (
              <button
                onClick={onRetry}
                disabled={sending}
                style={{
                  padding: "3px 8px",
                  background: "white",
                  border: "1px solid #fecaca",
                  borderRadius: 4,
                  color: "#991b1b",
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
            borderTop: "1px solid #eee",
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
              border: "1px solid #ddd",
              borderRadius: 6,
              fontFamily: "inherit",
              fontSize: 13,
            }}
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            style={{
              padding: "0 14px",
              background: "#6366f1",
              color: "white",
              border: "none",
              borderRadius: 6,
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

function humanizeTool(name: string): string {
  switch (name) {
    case "wiki_search":
      return "Searching the wiki";
    default:
      return `Running ${name}`;
  }
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
  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div
        style={{
          maxWidth: "85%",
          padding: "8px 12px",
          borderRadius: 10,
          background: isUser ? "#6366f1" : "#f3f4f6",
          color: isUser ? "white" : "#111",
          whiteSpace: "pre-wrap",
          fontSize: 13,
          lineHeight: 1.5,
          opacity: muted ? 0.6 : 1,
        }}
      >
        {content}
      </div>
    </div>
  );
}

function IconButton({
  children,
  onClick,
  title,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      disabled={disabled}
      style={{
        width: 28,
        height: 28,
        border: "none",
        background: "transparent",
        borderRadius: 4,
        cursor: disabled ? "not-allowed" : "pointer",
        color: "#4b5563",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        opacity: disabled ? 0.4 : 1,
      }}
      onMouseEnter={(e) => {
        if (!disabled) e.currentTarget.style.background = "#eef2ff";
      }}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      {children}
    </button>
  );
}

function ChatBubbleIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 12a8 8 0 0 1-11.5 7.2L4 21l1.8-5.5A8 8 0 1 1 21 12z" />
    </svg>
  );
}
function NewChatIcon() {
  // Pencil-on-paper compose glyph — universally read as "new message".
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
    </svg>
  );
}
function HistoryIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}
function ExpandIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M9 3H3v6" />
      <path d="M3 3l7 7" />
      <path d="M15 21h6v-6" />
      <path d="M21 21l-7-7" />
    </svg>
  );
}
function CollapseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 14h6v6" />
      <path d="M10 14l-7 7" />
      <path d="M20 10h-6V4" />
      <path d="M14 10l7-7" />
    </svg>
  );
}
function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M18 6L6 18" />
      <path d="M6 6l12 12" />
    </svg>
  );
}

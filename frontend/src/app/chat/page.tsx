"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { AppShell } from "@/components/common/AppShell";
import { ApiError, apiStream } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";

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

export default function ChatPage() {
  const { user, loading } = useRequireAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [toolHint, setToolHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending]);

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;

  async function sendHistory(history: ChatMessage[]) {
    setError(null);
    setSending(true);
    setToolHint(null);
    // Append a streaming-assistant placeholder; deltas mutate its content.
    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);
    let streamFailed = false;

    try {
      await apiStream(
        "/chat/messages",
        {
          method: "POST",
          body: JSON.stringify({ messages: history }),
        },
        (raw) => {
          const ev = raw as StreamEvent;
          switch (ev.type) {
            case "text_delta":
              appendDelta(ev.text);
              break;
            case "tool_call":
              setToolHint(`${humanizeTool(ev.name)}…`);
              break;
            case "tool_result":
            case "iteration_done":
              setToolHint(null);
              break;
            case "done":
              setToolHint(null);
              break;
            case "error":
              streamFailed = true;
              setError(ev.message);
              break;
          }
        },
      );
    } catch (err) {
      streamFailed = true;
      setError(formatError(err));
    } finally {
      setSending(false);
      setToolHint(null);
      if (streamFailed) {
        // Remove the empty (or partially-filled) placeholder so the user sees
        // their last user message + the error, and can retry.
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.role === "assistant" && last.content === "") return prev.slice(0, -1);
          return prev;
        });
      }
    }
  }

  function appendDelta(text: string) {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (!last || last.role !== "assistant") return prev;
      next[next.length - 1] = { ...last, content: last.content + text };
      return next;
    });
  }

  async function onSend(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    const next: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    await sendHistory(next);
  }

  async function onRetry() {
    if (sending || messages.length === 0) return;
    if (messages[messages.length - 1].role !== "user") return;
    await sendHistory(messages);
  }

  return (
    <AppShell>
      <main
        style={{
          height: "100vh",
          display: "flex",
          flexDirection: "column",
          maxWidth: 820,
          margin: "0 auto",
          padding: "24px 24px 0",
        }}
      >
        <h1 style={{ margin: 0, fontSize: 20 }}>Chat</h1>
        <div
          ref={scrollRef}
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "16px 0",
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          {messages.length === 0 && (
            <p style={{ color: "#888", fontSize: 14 }}>
              Ask anything. The chat agent can answer questions from the wiki.
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
              gap: 12,
              padding: "10px 12px",
              background: "#fef2f2",
              border: "1px solid #fecaca",
              color: "#991b1b",
              borderRadius: 6,
              fontSize: 13,
              marginBottom: 8,
            }}
          >
            <div style={{ flex: 1, whiteSpace: "pre-wrap" }}>{error}</div>
            {messages.length > 0 && messages[messages.length - 1].role === "user" && (
              <button
                onClick={onRetry}
                disabled={sending}
                style={{
                  padding: "4px 10px",
                  background: "white",
                  border: "1px solid #fecaca",
                  borderRadius: 4,
                  color: "#991b1b",
                  cursor: sending ? "not-allowed" : "pointer",
                  fontSize: 12,
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
            gap: 8,
            padding: "12px 0 16px",
            borderTop: "1px solid #eee",
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
              padding: 10,
              border: "1px solid #ddd",
              borderRadius: 8,
              fontFamily: "inherit",
              fontSize: 14,
            }}
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            style={{
              padding: "0 18px",
              background: "#6366f1",
              color: "white",
              border: "none",
              borderRadius: 8,
              cursor: sending || !input.trim() ? "not-allowed" : "pointer",
              opacity: sending || !input.trim() ? 0.5 : 1,
              fontWeight: 600,
            }}
          >
            Send
          </button>
        </form>
      </main>
    </AppShell>
  );
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
    if (err.status === 504 || err.status === 502) return `${err.message} (provider error ${err.status})`;
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
          maxWidth: "78%",
          padding: "10px 14px",
          borderRadius: 12,
          background: isUser ? "#6366f1" : "#f3f4f6",
          color: isUser ? "white" : "#111",
          whiteSpace: "pre-wrap",
          fontSize: 14,
          lineHeight: 1.5,
          opacity: muted ? 0.6 : 1,
        }}
      >
        {content}
      </div>
    </div>
  );
}

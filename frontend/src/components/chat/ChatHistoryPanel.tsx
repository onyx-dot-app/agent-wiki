"use client";

import { useCallback, useEffect, useState } from "react";

import {
  type ChatSession,
  deleteSession,
  listSessions,
} from "@/lib/chat";

interface Props {
  open: boolean;
  activeSessionId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onClose: () => void;
  /** Bumped by the parent when something might have changed the list
   * (after a stream completes, after a title arrives). Forces a refetch. */
  refreshKey: number;
}

export function ChatHistoryPanel({
  open,
  activeSessionId,
  onSelect,
  onNewChat,
  onClose,
  refreshKey,
}: Props) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await listSessions();
      setSessions(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load history");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void refresh();
  }, [open, refreshKey, refresh]);

  const onDelete = useCallback(
    async (id: string, e: React.MouseEvent) => {
      e.stopPropagation();
      const prev = sessions;
      setSessions((s) => s.filter((row) => row.id !== id));
      try {
        await deleteSession(id);
        if (id === activeSessionId) onNewChat();
      } catch {
        setSessions(prev);
        setError("Failed to delete session");
      }
    },
    [sessions, activeSessionId, onNewChat],
  );

  return (
    <div
      aria-hidden={!open}
      style={{
        position: "absolute",
        inset: 0,
        background: "white",
        display: "flex",
        flexDirection: "column",
        transform: open ? "translateX(0)" : "translateX(-100%)",
        transition: "transform 180ms ease-out",
        zIndex: 2,
      }}
    >
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
        <button
          onClick={onClose}
          title="Back to chat"
          aria-label="Back to chat"
          style={iconButtonStyle}
        >
          <BackIcon />
        </button>
        <div style={{ fontWeight: 600, fontSize: 14, flex: 1 }}>History</div>
        <button
          onClick={onNewChat}
          style={{
            padding: "5px 10px",
            background: "#6366f1",
            color: "white",
            border: "none",
            borderRadius: 6,
            cursor: "pointer",
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          + New chat
        </button>
      </header>

      <div style={{ flex: 1, overflowY: "auto", padding: 8 }}>
        {error && (
          <div
            role="alert"
            style={{
              margin: "4px 4px 8px",
              padding: "8px 10px",
              background: "#fef2f2",
              border: "1px solid #fecaca",
              color: "#991b1b",
              borderRadius: 6,
              fontSize: 12,
            }}
          >
            {error}
          </div>
        )}
        {loading && sessions.length === 0 && (
          <p style={{ color: "#888", fontSize: 13, padding: 8, margin: 0 }}>Loading…</p>
        )}
        {!loading && sessions.length === 0 && !error && (
          <p style={{ color: "#888", fontSize: 13, padding: 8, margin: 0 }}>
            No past conversations yet.
          </p>
        )}
        {sessions.map((s) => {
          const active = s.id === activeSessionId;
          return (
            <SessionRow
              key={s.id}
              session={s}
              active={active}
              onClick={() => onSelect(s.id)}
              onDelete={(e) => onDelete(s.id, e)}
            />
          );
        })}
      </div>
    </div>
  );
}

function SessionRow({
  session,
  active,
  onClick,
  onDelete,
}: {
  session: ChatSession;
  active: boolean;
  onClick: () => void;
  onDelete: (e: React.MouseEvent) => void;
}) {
  const [hover, setHover] = useState(false);
  const title = session.title ?? "Untitled chat";
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "8px 10px",
        borderRadius: 6,
        cursor: "pointer",
        background: active ? "#eef2ff" : hover ? "#f5f5f5" : "transparent",
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: active ? 600 : 500,
            color: "#111",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {title}
        </div>
        <div style={{ fontSize: 11, color: "#888" }}>{formatRelative(session.updated_at)}</div>
      </div>
      {hover && (
        <button
          onClick={onDelete}
          title="Delete"
          aria-label="Delete chat"
          style={{
            ...iconButtonStyle,
            width: 24,
            height: 24,
            color: "#9ca3af",
          }}
        >
          <TrashIcon />
        </button>
      )}
    </div>
  );
}

const iconButtonStyle: React.CSSProperties = {
  width: 28,
  height: 28,
  border: "none",
  background: "transparent",
  borderRadius: 4,
  cursor: "pointer",
  color: "#4b5563",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

function formatRelative(ts: string): string {
  // ts is "YYYY-MM-DD HH:MM:SS" UTC. Treat as UTC by appending 'Z'.
  const d = new Date(ts.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return ts;
  const diffMs = Date.now() - d.getTime();
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return d.toLocaleDateString();
}

function BackIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M19 12H5" />
      <path d="M12 19l-7-7 7-7" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    </svg>
  );
}

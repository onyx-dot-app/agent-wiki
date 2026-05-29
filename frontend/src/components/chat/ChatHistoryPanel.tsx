"use client";

import { useCallback, useEffect, useState } from "react";

import { SvgArrowLeft, SvgTrash } from "@onyx-ai/opal/icons";

import {
  type ChatSession,
  deleteSession,
  listSessions,
} from "@/lib/chat";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { color, radius } from "@/lib/theme";

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
        background: color.bg.page,
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
          borderBottom: `1px solid ${color.border.subtle}`,
          background: color.bg.panel,
          flexShrink: 0,
        }}
      >
        <button
          onClick={onClose}
          title="Back to chat"
          aria-label="Back to chat"
          style={iconButtonStyle}
        >
          <SvgArrowLeft size={16} />
        </button>
        <div style={{ fontWeight: 600, fontSize: 14, flex: 1, color: color.text.primary }}>History</div>
        <button
          onClick={onNewChat}
          style={{
            padding: "5px 10px",
            background: color.accent.bg,
            color: color.accent.fg,
            border: "none",
            borderRadius: radius.sm,
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
              background: color.state.danger.bg,
              border: `1px solid ${color.state.danger.border}`,
              color: color.state.danger.fg,
              borderRadius: radius.sm,
              fontSize: 12,
            }}
          >
            {error}
          </div>
        )}
        {loading && sessions.length === 0 && (
          <div style={{ padding: 8 }}>
            <LoadingSpinner />
          </div>
        )}
        {!loading && sessions.length === 0 && !error && (
          <p style={{ color: color.text.muted, fontSize: 13, padding: 8, margin: 0 }}>
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
        borderRadius: radius.sm,
        cursor: "pointer",
        background: active ? color.bg.active : hover ? color.bg.hover : "transparent",
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: active ? 600 : 500,
            color: color.text.primary,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {title}
        </div>
        <div style={{ fontSize: 11, color: color.text.muted }}>{formatRelative(session.updated_at)}</div>
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
            color: color.text.faint,
          }}
        >
          <SvgTrash size={14} />
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
  borderRadius: radius.xs,
  cursor: "pointer",
  color: color.text.secondary,
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


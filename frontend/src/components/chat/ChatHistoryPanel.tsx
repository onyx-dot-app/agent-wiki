"use client";

import { useCallback, useEffect, useState } from "react";

import { SvgArrowLeft, SvgTrash } from "@onyx-ai/opal/icons";

import {
  type ChatSession,
  deleteSession,
  listSessions,
} from "@/lib/chat";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";

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
      className="absolute inset-0 bg-(--background-tint-00) flex flex-col z-[2]"
      style={{
        transform: open ? "translateX(0)" : "translateX(-100%)",
        transition: "transform 180ms ease-out",
      }}
    >
      <header
        className="flex items-center gap-2 px-3 py-[10px] border-b border-(--border-01) bg-(--background-tint-01) shrink-0"
      >
        <button
          onClick={onClose}
          title="Back to chat"
          aria-label="Back to chat"
          className="w-7 h-7 border-none bg-transparent rounded-(--border-radius-04) cursor-pointer text-(--text-04) flex items-center justify-center"
        >
          <SvgArrowLeft size={16} />
        </button>
        <div className="font-semibold text-sm flex-1 text-(--text-05)">History</div>
        <button
          onClick={onNewChat}
          className="py-[5px] px-[10px] bg-(--background-tint-inverted-00) text-(--text-inverted-05) border-none rounded-(--border-radius-04) cursor-pointer text-xs font-semibold"
        >
          + New chat
        </button>
      </header>

      <div className="flex-1 overflow-y-auto p-2">
        {error && (
          <div
            role="alert"
            className="mx-1 mt-1 mb-2 py-2 px-[10px] bg-(--status-error-01) border border-(--status-error-02) text-(--status-text-error-05) rounded-(--border-radius-04) text-xs"
          >
            {error}
          </div>
        )}
        {loading && sessions.length === 0 && (
          <div className="p-2">
            <LoadingSpinner />
          </div>
        )}
        {!loading && sessions.length === 0 && !error && (
          <p className="text-(--text-03) text-[13px] p-2 m-0">
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
      className={`flex items-center gap-[6px] py-2 px-[10px] rounded-(--border-radius-04) cursor-pointer ${
        active
          ? "bg-(--background-tint-04)"
          : hover
            ? "bg-(--background-tint-03)"
            : "bg-transparent"
      }`}
    >
      <div className="flex-1 min-w-0">
        <div
          className={`text-[13px] ${active ? "font-semibold" : "font-medium"} text-(--text-05) whitespace-nowrap overflow-hidden text-ellipsis`}
        >
          {title}
        </div>
        <div className="text-[11px] text-(--text-03)">{formatRelative(session.updated_at)}</div>
      </div>
      {hover && (
        <button
          onClick={onDelete}
          title="Delete"
          aria-label="Delete chat"
          className="w-6 h-6 border-none bg-transparent rounded-(--border-radius-04) cursor-pointer text-(--text-02) flex items-center justify-center"
        >
          <SvgTrash size={14} />
        </button>
      )}
    </div>
  );
}

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

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/common/Button";
import { useAuth } from "@/lib/auth";
import {
  createComment,
  deleteComment,
  editComment,
  listComments,
  reopenThread,
  replyToComment,
  resolveThread,
} from "@/lib/comments";
import type { CommentDraft } from "@/lib/commentAnchor";
import { absoluteTime, relativeTime } from "@/lib/time";
import type { CommentThreadView, CommentView } from "@/types";

import styles from "./CommentsPanel.module.css";

export type { CommentDraft };

interface Props {
  path: string;
  headSha: string | null;
  draft: CommentDraft | null;
  onDraftConsumed: () => void;
  onThreadsChange?: (threads: CommentThreadView[]) => void;
  onClose: () => void;
  fullHeight?: boolean;
}

function authorLabel(authorUserId: string | null, selfId: string | undefined): string {
  if (authorUserId && authorUserId === selfId) return "You";
  return "User";
}

/** Backend timestamps are "YYYY-MM-DD HH:MM:SS" in UTC (not ISO). Normalize so
 * Date parses them as UTC, not local. */
function toIso(ts: string): string {
  return ts.includes("T") ? ts : `${ts.replace(" ", "T")}Z`;
}

export function CommentsPanel({
  path,
  headSha,
  draft,
  onDraftConsumed,
  onThreadsChange,
  onClose,
  fullHeight,
}: Props) {
  const { user } = useAuth();
  const [threads, setThreads] = useState<CommentThreadView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onThreadsChangeRef = useRef(onThreadsChange);
  onThreadsChangeRef.current = onThreadsChange;

  const refresh = useCallback(async () => {
    try {
      const t = await listComments(path);
      setThreads(t);
      onThreadsChangeRef.current?.(t);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load comments");
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  // Returns true on success so callers can clear/close their input only when
  // the action actually went through.
  const run = useCallback(
    async (fn: () => Promise<unknown>): Promise<boolean> => {
      setBusy(true);
      try {
        await fn();
        await refresh();
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "action failed");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const total = threads.length;

  return (
    <div className={`${styles.panel} ${fullHeight ? styles.fullHeight : ""}`}>
      <div className={styles.header}>
        <span className={styles.title}>
          Comments<span className={styles.count}>{total ? ` (${total})` : ""}</span>
        </span>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close comments">
          ×
        </Button>
      </div>

      <div className={styles.scroll}>
        {error && <div className={styles.error}>{error}</div>}

        {draft && (
          <DraftComposer
            draft={draft}
            disabled={busy || !headSha}
            onCancel={onDraftConsumed}
            onSubmit={async (body) => {
              if (!headSha) {
                setError("page version unknown — reload and retry");
                return;
              }
              const ok = await run(() =>
                createComment({
                  path,
                  anchorSha: headSha,
                  startOffset: draft.startOffset,
                  endOffset: draft.endOffset,
                  quotedText: draft.quotedText,
                  body,
                }),
              );
              if (ok) onDraftConsumed();
            }}
          />
        )}

        {loading ? (
          <div className={styles.empty}>Loading…</div>
        ) : total === 0 && !draft ? (
          <div className={styles.empty}>
            No comments yet. Select text in the page to add one.
          </div>
        ) : (
          threads.map((t) => (
            <Thread
              key={t.root.id}
              thread={t}
              selfId={user?.id}
              isAdmin={!!user?.is_admin}
              busy={busy}
              onReply={(body) => run(() => replyToComment(t.root.id, body))}
              onResolve={() => run(() => resolveThread(t.root.id))}
              onReopen={() => run(() => reopenThread(t.root.id))}
              onEdit={(id, body) => run(() => editComment(id, body))}
              onDelete={(id) => run(() => deleteComment(id))}
            />
          ))
        )}
      </div>
    </div>
  );
}

function DraftComposer({
  draft,
  disabled,
  onSubmit,
  onCancel,
}: {
  draft: CommentDraft;
  disabled: boolean;
  onSubmit: (body: string) => void;
  onCancel: () => void;
}) {
  const [body, setBody] = useState("");
  return (
    <div className={styles.draft}>
      <div className={styles.quote}>{draft.quotedText}</div>
      <textarea
        className={styles.textarea}
        placeholder="Add a comment…"
        value={body}
        autoFocus
        onChange={(e) => setBody(e.target.value)}
      />
      <div className={styles.composeRow}>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={disabled || !body.trim()}
          onClick={() => onSubmit(body.trim())}
        >
          Comment
        </Button>
      </div>
    </div>
  );
}

function Thread({
  thread,
  selfId,
  isAdmin,
  busy,
  onReply,
  onResolve,
  onReopen,
  onEdit,
  onDelete,
}: {
  thread: CommentThreadView;
  selfId: string | undefined;
  isAdmin: boolean;
  busy: boolean;
  onReply: (body: string) => Promise<boolean>;
  onResolve: () => void;
  onReopen: () => void;
  onEdit: (id: string, body: string) => Promise<boolean>;
  onDelete: (id: string) => void;
}) {
  const { root } = thread;
  const [replyOpen, setReplyOpen] = useState(false);
  const [replyBody, setReplyBody] = useState("");
  const resolved = root.status === "resolved";

  return (
    <div className={`${styles.thread} ${resolved ? styles.threadResolved : ""}`}>
      <div
        className={`${styles.quote} ${root.status === "orphaned" ? styles.quoteOrphaned : ""}`}
      >
        {root.status === "orphaned" ? "(text removed) " : ""}
        {root.quoted_text}
      </div>

      <Comment
        comment={root}
        canModify={isAdmin || root.author_user_id === selfId}
        selfId={selfId}
        busy={busy}
        onEdit={onEdit}
        onDelete={onDelete}
      />

      <div className={styles.actions}>
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => setReplyOpen((v) => !v)}>
          Reply
        </Button>
        {resolved ? (
          <Button variant="ghost" size="sm" disabled={busy} onClick={onReopen}>
            Reopen
          </Button>
        ) : (
          <Button variant="ghost" size="sm" disabled={busy} onClick={onResolve}>
            Resolve
          </Button>
        )}
      </div>

      {(thread.replies.length > 0 || replyOpen) && (
        <div className={styles.replies}>
          {thread.replies.map((r) => (
            <Comment
              key={r.id}
              comment={r}
              canModify={isAdmin || r.author_user_id === selfId}
              selfId={selfId}
              busy={busy}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
          {replyOpen && (
            <div>
              <textarea
                className={styles.textarea}
                placeholder="Reply…"
                value={replyBody}
                autoFocus
                onChange={(e) => setReplyBody(e.target.value)}
              />
              <div className={styles.composeRow}>
                <Button variant="ghost" size="sm" onClick={() => setReplyOpen(false)}>
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy || !replyBody.trim()}
                  onClick={async () => {
                    // Clear/close only on success so a failed reply isn't lost.
                    if (await onReply(replyBody.trim())) {
                      setReplyBody("");
                      setReplyOpen(false);
                    }
                  }}
                >
                  Reply
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Comment({
  comment,
  canModify,
  selfId,
  busy,
  onEdit,
  onDelete,
}: {
  comment: CommentView;
  canModify: boolean;
  selfId: string | undefined;
  busy: boolean;
  onEdit: (id: string, body: string) => Promise<boolean>;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(comment.body);

  return (
    <div>
      <div className={styles.metaRow}>
        <span className={styles.author}>{authorLabel(comment.author_user_id, selfId)}</span>
        <span className={styles.time} title={absoluteTime(toIso(comment.created_at))}>
          {relativeTime(toIso(comment.created_at), "short")}
        </span>
        {comment.status === "resolved" && (
          <span className={`${styles.badge} ${styles.badgeResolved}`}>resolved</span>
        )}
        {comment.status === "orphaned" && (
          <span className={`${styles.badge} ${styles.badgeOrphaned}`}>orphaned</span>
        )}
      </div>

      {editing ? (
        <div>
          <textarea
            className={styles.textarea}
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className={styles.composeRow}>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setDraft(comment.body);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={busy || !draft.trim()}
              onClick={async () => {
                if (await onEdit(comment.id, draft.trim())) setEditing(false);
              }}
            >
              Save
            </Button>
          </div>
        </div>
      ) : (
        <div className={styles.body}>{comment.body}</div>
      )}

      {canModify && !editing && (
        <div className={styles.actions}>
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => setEditing(true)}>
            Edit
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={busy}
            onClick={() => onDelete(comment.id)}
          >
            Delete
          </Button>
        </div>
      )}
    </div>
  );
}

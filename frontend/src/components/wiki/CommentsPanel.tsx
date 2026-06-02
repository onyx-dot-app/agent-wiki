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

  // Hand the latest threads up without making `refresh` depend on the (possibly
  // unstable) callback identity — avoids a refetch loop.
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

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await fn();
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : "action failed");
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
        <button className={styles.closeBtn} onClick={onClose} aria-label="Close comments">
          ×
        </button>
      </div>

      <div className={styles.scroll}>
        {error && <div className={styles.error}>{error}</div>}

        {draft && (
          <DraftComposer
            draft={draft}
            disabled={busy || !headSha}
            onCancel={onDraftConsumed}
            onSubmit={(body) =>
              run(async () => {
                if (!headSha) throw new Error("page version unknown — reload and retry");
                await createComment({
                  path,
                  anchorSha: headSha,
                  startOffset: draft.startOffset,
                  endOffset: draft.endOffset,
                  quotedText: draft.quotedText,
                  body,
                });
                onDraftConsumed();
              })
            }
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
  onReply: (body: string) => void;
  onResolve: () => void;
  onReopen: () => void;
  onEdit: (id: string, body: string) => void;
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
        <button className={styles.linkBtn} disabled={busy} onClick={() => setReplyOpen((v) => !v)}>
          Reply
        </button>
        {resolved ? (
          <button className={styles.linkBtn} disabled={busy} onClick={onReopen}>
            Reopen
          </button>
        ) : (
          <button className={styles.linkBtn} disabled={busy} onClick={onResolve}>
            Resolve
          </button>
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
                  onClick={() => {
                    onReply(replyBody.trim());
                    setReplyBody("");
                    setReplyOpen(false);
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
  onEdit: (id: string, body: string) => void;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(comment.body);

  return (
    <div>
      <div className={styles.metaRow}>
        <span className={styles.author}>{authorLabel(comment.author_user_id, selfId)}</span>
        <span className={styles.time}>{comment.created_at}</span>
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
              onClick={() => {
                onEdit(comment.id, draft.trim());
                setEditing(false);
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
          <button className={styles.linkBtn} disabled={busy} onClick={() => setEditing(true)}>
            Edit
          </button>
          <button
            className={`${styles.linkBtn} ${styles.linkBtnDanger}`}
            disabled={busy}
            onClick={() => onDelete(comment.id)}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

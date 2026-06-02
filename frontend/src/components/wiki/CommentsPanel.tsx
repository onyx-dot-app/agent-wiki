"use client";

import { Button, Text } from "@onyx-ai/opal/components";
import { useCallback, useState } from "react";

import { useAuth } from "@/lib/auth";
import {
  createComment,
  deleteComment,
  editComment,
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
  /** Threads are owned by the page (so highlights stay in sync); the panel
   * renders them and calls `onChanged` after a mutation to trigger a refetch. */
  threads: CommentThreadView[];
  onChanged: () => void | Promise<void>;
  onDraftConsumed: () => void;
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
  threads,
  onChanged,
  onDraftConsumed,
  onClose,
  fullHeight,
}: Props) {
  const { user } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Returns true on success so callers can clear/close their input only when
  // the action actually went through.
  const run = useCallback(
    async (fn: () => Promise<unknown>): Promise<boolean> => {
      setBusy(true);
      try {
        await fn();
        await onChanged();
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "action failed");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [onChanged],
  );

  const total = threads.length;

  return (
    <div className={`${styles.panel} ${fullHeight ? styles.fullHeight : ""}`}>
      <div className={styles.header}>
        <Text font="main-ui-action" color="text-04">
          {`Comments${total ? ` (${total})` : ""}`}
        </Text>
        <Button prominence="tertiary" size="sm" onClick={onClose} aria-label="Close comments">
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

        {total === 0 && !draft ? (
          <Text font="secondary-body" color="text-03">
            No comments yet. Select text in the page to add one.
          </Text>
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
        <Button prominence="tertiary" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="action"
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
  // One flat conversation (Google-Docs style): the root and every reply render
  // uniformly, appended in order — no nesting/indentation.
  const conversation = [root, ...thread.replies];

  return (
    <div className={`${styles.thread} ${resolved ? styles.threadResolved : ""}`}>
      <div
        className={`${styles.quote} ${root.status === "orphaned" ? styles.quoteOrphaned : ""}`}
      >
        {root.status === "orphaned" ? "(text removed) " : ""}
        {root.quoted_text}
      </div>

      <div className={styles.threadBody}>
        {conversation.map((c) => (
          <Comment
            key={c.id}
            comment={c}
            canModify={isAdmin || c.author_user_id === selfId}
            selfId={selfId}
            busy={busy}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        ))}
      </div>

      {replyOpen ? (
        <div className={styles.replyBox}>
          <textarea
            className={styles.textarea}
            placeholder="Reply…"
            value={replyBody}
            autoFocus
            onChange={(e) => setReplyBody(e.target.value)}
          />
          <div className={styles.composeRow}>
            <Button prominence="tertiary" size="sm" onClick={() => setReplyOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="action"
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
      ) : (
        <div className={styles.actions}>
          <Button prominence="tertiary" size="sm" disabled={busy} onClick={() => setReplyOpen(true)}>
            Reply
          </Button>
          {resolved ? (
            <Button prominence="tertiary" size="sm" disabled={busy} onClick={onReopen}>
              Reopen
            </Button>
          ) : (
            <Button prominence="tertiary" size="sm" disabled={busy} onClick={onResolve}>
              Resolve
            </Button>
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
        <Text font="main-ui-action" color="text-04">
          {authorLabel(comment.author_user_id, selfId)}
        </Text>
        <span className={styles.time} title={absoluteTime(toIso(comment.created_at))}>
          <Text font="secondary-body" color="text-03">
            {relativeTime(toIso(comment.created_at), "short")}
          </Text>
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
              prominence="tertiary"
              size="sm"
              onClick={() => {
                setDraft(comment.body);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
            <Button
              variant="action"
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
          <Button prominence="tertiary" size="sm" disabled={busy} onClick={() => setEditing(true)}>
            Edit
          </Button>
          <Button variant="danger" size="sm" disabled={busy} onClick={() => onDelete(comment.id)}>
            Delete
          </Button>
        </div>
      )}
    </div>
  );
}

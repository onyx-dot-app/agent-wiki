"use client";

import {
  Button,
  LineItemButton,
  Popover,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgCheck,
  SvgEdit,
  SvgLink,
  SvgMoreHorizontal,
  SvgTrash,
  SvgX,
} from "@onyx-ai/opal/icons";
import { type MouseEvent, useCallback, useEffect, useState } from "react";

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
  /** Selected thread (its span gets the orange highlight in the doc). */
  activeId: string | null;
  onActivate: (id: string | null) => void;
  onDraftConsumed: () => void;
  onClose: () => void;
  fullHeight?: boolean;
}

function authorLabel(
  authorUserId: string | null,
  authorDisplay: string | null,
  selfId: string | undefined,
): string {
  if (authorUserId && authorUserId === selfId) return "You";
  return authorDisplay ?? "User";
}

/** Backend timestamps are "YYYY-MM-DD HH:MM:SS" in UTC (not ISO). Normalize so
 * Date parses them as UTC, not local. */
function toIso(ts: string): string {
  return ts.includes("T") ? ts : `${ts.replace(" ", "T")}Z`;
}

/** Deep-link to a specific thread: the page route reads `?comment=<id>`, opens
 * the panel, and scrolls to the thread's anchored span. Each path segment is
 * encoded since wiki paths contain spaces. */
function commentLink(path: string, rootId: string): string {
  const encoded = path
    .split("/")
    .filter(Boolean)
    .map((s) => encodeURIComponent(s))
    .join("/");
  return `${window.location.origin}/app/wiki/${encoded}?comment=${rootId}`;
}

export function CommentsPanel({
  path,
  headSha,
  draft,
  threads,
  onChanged,
  activeId,
  onActivate,
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

  const [showResolved, setShowResolved] = useState(false);

  // Order threads to match the doc: by their referenced position (start_offset)
  // top-to-bottom. Orphaned threads ("Original content deleted") have no live
  // anchor, so they sink to the bottom, ordered among themselves by creation.
  const orderedThreads = [...threads].sort((a, b) => {
    const ao = a.root.status === "orphaned" ? null : a.root.start_offset;
    const bo = b.root.status === "orphaned" ? null : b.root.start_offset;
    if (ao === null && bo === null)
      return a.root.created_at.localeCompare(b.root.created_at);
    if (ao === null) return 1;
    if (bo === null) return -1;
    return ao - bo;
  });

  // Resolved threads drop out of the main list (Google-Docs style) — they're
  // "done", so they shouldn't clutter the doc. They stay reachable (to reopen)
  // behind a toggle.
  const openThreads = orderedThreads.filter(
    (t) => t.root.status !== "resolved",
  );
  const resolvedThreads = orderedThreads.filter(
    (t) => t.root.status === "resolved",
  );

  // If the active thread (clicked, or arrived via a `?comment=` deep-link) is
  // resolved, expand the resolved section so it's actually visible — otherwise
  // activating it would silently do nothing.
  const activeIsResolved = resolvedThreads.some((t) => t.root.id === activeId);
  useEffect(() => {
    if (activeIsResolved) setShowResolved(true);
  }, [activeIsResolved]);

  const renderThread = (t: CommentThreadView) => (
    <Thread
      key={t.root.id}
      thread={t}
      path={path}
      selfId={user?.id}
      isAdmin={!!user?.is_admin}
      busy={busy}
      active={t.root.id === activeId}
      onActivate={() => onActivate(t.root.id)}
      onReply={(body) => run(() => replyToComment(t.root.id, body))}
      onResolve={() => run(() => resolveThread(t.root.id))}
      onReopen={() => run(() => reopenThread(t.root.id))}
      onEdit={(id, body) => run(() => editComment(id, body))}
      onDelete={(id) => run(() => deleteComment(id))}
    />
  );

  return (
    <div className={`${styles.panel} ${fullHeight ? styles.fullHeight : ""}`}>
      <div className={styles.headerRow}>
        <div className={styles.headerTitle}>
          <Text font="main-ui-action" color="text-04">
            Comments
          </Text>
        </div>
        <Button
          icon={SvgX}
          prominence="tertiary"
          size="sm"
          tooltip="Close comments"
          onClick={onClose}
        />
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

        {openThreads.length === 0 && resolvedThreads.length === 0 && !draft ? (
          <Text font="secondary-body" color="text-03">
            No comments yet. Select text in the page to add one.
          </Text>
        ) : (
          <>
            {openThreads.map(renderThread)}

            {resolvedThreads.length > 0 && (
              <>
                <div className={styles.resolvedToggle}>
                  <Button
                    prominence="tertiary"
                    size="sm"
                    onClick={() => setShowResolved((v) => !v)}
                  >
                    {showResolved
                      ? `Hide resolved (${resolvedThreads.length})`
                      : `Show resolved (${resolvedThreads.length})`}
                  </Button>
                </div>
                {showResolved && resolvedThreads.map(renderThread)}
              </>
            )}
          </>
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
  path,
  selfId,
  isAdmin,
  busy,
  active,
  onActivate,
  onReply,
  onResolve,
  onReopen,
  onEdit,
  onDelete,
}: {
  thread: CommentThreadView;
  path: string;
  selfId: string | undefined;
  isAdmin: boolean;
  busy: boolean;
  active: boolean;
  onActivate: () => void;
  onReply: (body: string) => Promise<boolean>;
  onResolve: () => void;
  onReopen: () => void;
  onEdit: (id: string, body: string) => Promise<boolean>;
  onDelete: (id: string) => void;
}) {
  const { root } = thread;
  const [replyOpen, setReplyOpen] = useState(false);
  const [replyBody, setReplyBody] = useState("");
  const [copied, setCopied] = useState(false);
  const resolved = root.status === "resolved";

  // Copy a deep-link to this thread. Stop propagation so it doesn't also
  // activate the thread (which would scroll the doc out from under the click).
  const copyLink = (e: MouseEvent) => {
    e.stopPropagation();
    void navigator.clipboard
      .writeText(commentLink(path, root.id))
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        /* clipboard blocked — no-op */
      });
  };
  // One flat conversation (Google-Docs style): the root and every reply render
  // uniformly, appended in order — no nesting/indentation.
  const conversation = [root, ...thread.replies];

  return (
    // Clicking the thread selects it (its span gets the orange highlight). The
    // commented text itself lives as a highlight in the doc, so no quote box.
    <div
      className={`${styles.thread} ${resolved ? styles.threadResolved : ""} ${active ? styles.threadActive : ""}`}
      onClick={onActivate}
    >
      {root.status === "orphaned" && (
        <div className={styles.orphanedNote}>Original content deleted</div>
      )}

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
            <Button
              prominence="tertiary"
              size="sm"
              onClick={() => setReplyOpen(false)}
            >
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
          <Button
            prominence="tertiary"
            size="sm"
            disabled={busy}
            onClick={() => setReplyOpen(true)}
          >
            Reply
          </Button>
          {resolved ? (
            <Button
              prominence="tertiary"
              size="sm"
              disabled={busy}
              onClick={onReopen}
            >
              Reopen
            </Button>
          ) : (
            <Button
              prominence="tertiary"
              size="sm"
              disabled={busy}
              onClick={onResolve}
            >
              Resolve
            </Button>
          )}
          <Button
            icon={copied ? SvgCheck : SvgLink}
            prominence="tertiary"
            size="sm"
            tooltip={copied ? "Link copied" : "Copy link to comment"}
            onClick={copyLink}
          />
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
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className={styles.comment}>
      <div className={styles.metaRow}>
        <Text font="main-ui-action" color="text-04">
          {authorLabel(comment.author_user_id, comment.author_display, selfId)}
        </Text>
        <span
          className={styles.time}
          title={absoluteTime(toIso(comment.created_at))}
        >
          <Text font="secondary-body" color="text-03">
            {relativeTime(toIso(comment.created_at), "short")}
          </Text>
        </span>
        <span className={styles.metaRight}>
          {canModify && !editing && (
            // Overflow menu (Google-Docs style) keeps Edit/Delete off the card
            // until hovered, so comments stay compact. Forced visible while open.
            <span
              className={`${styles.kebab} ${menuOpen ? styles.kebabOpen : ""}`}
            >
              <Popover open={menuOpen} onOpenChange={setMenuOpen}>
                {/* Radix renders its own <button> here (no asChild) so the
                    trigger's onClick/ref/data-state are guaranteed to wire up —
                    OPAL's Button isn't a Radix Slot and drops them. */}
                <Popover.Trigger
                  className={styles.kebabBtn}
                  aria-label="Comment actions"
                  onClick={(e) => e.stopPropagation()}
                >
                  <SvgMoreHorizontal />
                </Popover.Trigger>
                <Popover.Content width="fit" align="end">
                  <Popover.Menu>
                    <LineItemButton
                      title="Edit"
                      icon={SvgEdit}
                      sizePreset="main-ui"
                      variant="section"
                      onClick={() => {
                        setMenuOpen(false);
                        setEditing(true);
                      }}
                    />
                    <LineItemButton
                      title="Delete"
                      color="danger"
                      icon={SvgTrash}
                      sizePreset="main-ui"
                      variant="section"
                      onClick={() => {
                        setMenuOpen(false);
                        onDelete(comment.id);
                      }}
                    />
                  </Popover.Menu>
                </Popover.Content>
              </Popover>
            </span>
          )}
        </span>
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
    </div>
  );
}

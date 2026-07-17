"use client";

import {
  Button,
  Divider,
  EndOfList,
  IconContainer,
  InputTypeIn,
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
import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "@/lib/auth";
import { shareableWikiUrl } from "@/lib/wikiHref";
import {
  createComment,
  deleteComment,
  editComment,
  reopenThread,
  replyToComment,
  resolveThread,
} from "@/lib/comments";
import type { CommentDraft } from "@/lib/fileview/commentAnchor";
import {
  detokenizeMentions,
  parseBody,
  tokenizeMentions,
} from "@/lib/commentMentions";
import { absoluteTime, relativeTime } from "@/lib/time";
import type { CommentThreadView, CommentView } from "@/types";

import { MentionTextarea } from "./MentionTextarea";

export type { CommentDraft };

interface Props {
  path: string;
  headSha: string | null;
  draft: CommentDraft | null;
  /** Threads are owned by the page (so highlights stay in sync). The panel
   * renders them and calls `onChanged` after a mutation to trigger a refetch. */
  threads: CommentThreadView[];
  onChanged: () => void | Promise<void>;
  /** Selected thread (its span gets the orange highlight in the doc). */
  activeId: string | null;
  onActivate: (id: string | null) => void;
  onDraftConsumed: () => void;
  /** Renders the title + close header and the standalone card chrome when
   * set. Omit when a host surface (e.g. the tabbed doc side panel) already
   * frames, names, and dismisses the panel. */
  onClose?: () => void;
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
 * the panel, and scrolls to the thread's anchored span. Uses the durable
 * id-based URL so the link survives a page rename/move. */
function commentLink(path: string, rootId: string): Promise<string> {
  return shareableWikiUrl(path, `comment=${rootId}`);
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
  const [search, setSearch] = useState("");

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

  // Resolved threads drop out of the main list (Google-Docs style): they're
  // "done", so they shouldn't clutter the list. They stay reachable (to
  // reopen) behind the foldable Resolved divider.
  // Search filters whole threads: a hit anywhere (any body or author) keeps
  // the full conversation visible for context.
  const q = search.trim().toLowerCase();
  const matchesSearch = (t: CommentThreadView) =>
    q === "" ||
    [t.root, ...t.replies].some(
      (c) =>
        c.body.toLowerCase().includes(q) ||
        (c.author_display ?? "").toLowerCase().includes(q),
    );
  const openThreads = orderedThreads.filter(
    (t) => t.root.status !== "resolved" && matchesSearch(t),
  );
  const resolvedThreads = orderedThreads.filter(
    (t) => t.root.status === "resolved" && matchesSearch(t),
  );
  const totalComments = threads.reduce((n, t) => n + 1 + t.replies.length, 0);

  // If the active thread (clicked, or arrived via a `?comment=` deep-link) is
  // resolved, expand the resolved section so it's actually visible. Otherwise
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
    <div
      className={`flex min-h-0 flex-col gap-2 ${
        fullHeight ? "h-full w-full" : ""
      } ${onClose ? "w-full max-w-[400px] rounded-(--radius-12) bg-(--background-tint-01) p-2" : ""}`}
    >
      {onClose && (
        <div className="flex shrink-0 items-center gap-1 p-1">
          <div className="min-w-0 flex-1">
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
      )}

      {threads.length > 0 && (
        <div className="shrink-0">
          <InputTypeIn
            searchIcon
            clearButton
            placeholder="Search comments…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-1 pb-2">
        {error && (
          <div className="py-1 text-xs text-(--status-text-error-05)">
            {error}
          </div>
        )}

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
            {q !== ""
              ? "No comments match your search."
              : "No comments yet. Select text in the page to add one."}
          </Text>
        ) : (
          <>
            {openThreads.map(renderThread)}

            {resolvedThreads.length > 0 && (
              <Divider
                title={`Resolved (${resolvedThreads.length})`}
                foldable
                open={showResolved}
                onOpenChange={setShowResolved}
              >
                <div className="flex flex-col gap-4 pt-2">
                  {resolvedThreads.map(renderThread)}
                </div>
              </Divider>
            )}

            {totalComments > 0 && (
              <EndOfList
                title={`${totalComments} Comment${totalComments === 1 ? "" : "s"}`}
              />
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
  // "@Name" → userId for every mention picked this session; used to tokenize
  // the body on submit.
  const mentions = useRef<Record<string, string>>({});
  return (
    <div className="rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-02) p-2.5">
      <div className="mb-1.5 rounded-(--radius-04) border-l-[3px] border-(--border-01) bg-(--background-tint-03) px-2 py-1 text-xs [word-break:break-word] whitespace-pre-wrap text-(--text-04)">
        {draft.quotedText}
      </div>
      <MentionTextarea
        placeholder="Add a comment…"
        value={body}
        autoFocus
        onChange={setBody}
        onPickMention={(d, id) => {
          mentions.current[d] = id;
        }}
      />
      <div className="mt-1.5 flex justify-end gap-2">
        <Button prominence="tertiary" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="action"
          size="sm"
          disabled={disabled || !body.trim()}
          onClick={() =>
            onSubmit(tokenizeMentions(body.trim(), mentions.current))
          }
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
  const replyMentions = useRef<Record<string, string>>({});
  const resolved = root.status === "resolved";

  // One flat conversation (Google-Docs style): the root and every reply render
  // uniformly, appended in order — no nesting/indentation.
  const conversation = [root, ...thread.replies];

  return (
    // Clicking the thread selects it (its span gets the orange highlight). The
    // commented text itself lives as a highlight in the doc, so no quote box.
    <div
      className={`cursor-pointer rounded-(--radius-12) border bg-(--background-tint-00) p-3.5 shadow-(--shadow-sm) ${
        resolved ? "opacity-65" : ""
      } ${
        active
          ? "border-(--background-tint-inverted-00) ring-1 ring-(--background-tint-inverted-00)"
          : "border-(--border-01)"
      }`}
      onClick={onActivate}
    >
      {root.status === "orphaned" && (
        <div className="mb-2 text-xs text-(--text-03) italic">
          Original content deleted
        </div>
      )}

      <div className="flex flex-col gap-3">
        {conversation.map((c) => (
          <Comment
            key={c.id}
            comment={c}
            path={path}
            canModify={isAdmin || c.author_user_id === selfId}
            selfId={selfId}
            busy={busy}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        ))}
      </div>

      {replyOpen ? (
        <div className="mt-2.5">
          <MentionTextarea
            placeholder="Reply…"
            value={replyBody}
            autoFocus
            onChange={setReplyBody}
            onPickMention={(d, id) => {
              replyMentions.current[d] = id;
            }}
          />
          <div className="mt-1.5 flex justify-end gap-2">
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
                const ok = await onReply(
                  tokenizeMentions(replyBody.trim(), replyMentions.current),
                );
                if (ok) {
                  setReplyBody("");
                  replyMentions.current = {};
                  setReplyOpen(false);
                }
              }}
            >
              Reply
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap gap-1.5">
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
        </div>
      )}
    </div>
  );
}

function Comment({
  comment,
  path,
  canModify,
  selfId,
  busy,
  onEdit,
  onDelete,
}: {
  comment: CommentView;
  path: string;
  canModify: boolean;
  selfId: string | undefined;
  busy: boolean;
  onEdit: (id: string, body: string) => Promise<boolean>;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const editMentions = useRef<Record<string, string>>({});
  const [menuOpen, setMenuOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  // Edit in the readable display form; (de)tokenize at the boundary so the
  // textarea shows "@Name" while storage keeps the mention's user id. Re-seed
  // from the current stored body each time edit opens, so a re-edit after an
  // external change starts from fresh content.
  const openEdit = () => {
    const d = detokenizeMentions(comment.body);
    setDraft(d.text);
    editMentions.current = d.map;
    setEditing(true);
  };

  // Copy a deep-link to this comment's thread (anchors live on the root, so all
  // comments in a thread share its link). Doesn't close the menu — the swapped
  // title/icon is the "done" feedback.
  const copyLink = async () => {
    // Durable id-based deep-link (survives rename/move); the ?comment= anchor
    // rides along. A transient id-resolve failure skips the copy rather than
    // handing over a fragile path link.
    let url: string;
    try {
      url = await commentLink(path, comment.thread_root_id);
    } catch {
      return;
    }
    void navigator.clipboard
      .writeText(url)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        /* clipboard blocked — no-op */
      });
  };

  return (
    <div className="group/comment">
      <div className="mb-1 flex min-h-6 items-center gap-2">
        <IconContainer avatar="user" name={comment.author_display ?? "User"} />
        <Text font="main-ui-action" color="text-04">
          {authorLabel(comment.author_user_id, comment.author_display, selfId)}
        </Text>
        <span title={absoluteTime(toIso(comment.created_at))}>
          <Text font="secondary-body" color="text-03">
            {relativeTime(toIso(comment.created_at), "short")}
          </Text>
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {!editing && (
            // Overflow menu (Google-Docs style): actions stay off the card
            // until hover/focus (touch devices always show them), forced
            // visible while open. "Copy link" is for everyone (read access).
            // Edit/Delete only for the author/admin.
            <span
              className={`transition-opacity group-focus-within/comment:opacity-100 group-hover/comment:opacity-100 [@media(hover:none)]:opacity-100 ${
                menuOpen ? "opacity-100" : "opacity-0"
              }`}
            >
              <Popover open={menuOpen} onOpenChange={setMenuOpen}>
                {/* Radix renders its own <button> here (no asChild) so the
                    trigger's onClick/ref/data-state wire up directly. OPAL's
                    Button isn't a Radix Slot, so forwarding through it isn't
                    guaranteed. */}
                <Popover.Trigger
                  className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-(--radius-04) text-(--text-03) hover:bg-(--background-tint-03) hover:text-(--text-05) [&>svg]:h-4 [&>svg]:w-4"
                  aria-label="Comment actions"
                  onClick={(e) => e.stopPropagation()}
                >
                  <SvgMoreHorizontal />
                </Popover.Trigger>
                <Popover.Content width="fit" align="end">
                  <Popover.Menu>
                    <LineItemButton
                      title={copied ? "Link copied" : "Copy link"}
                      icon={copied ? SvgCheck : SvgLink}
                      sizePreset="main-ui"
                      variant="section"
                      onClick={copyLink}
                    />
                    {canModify && (
                      <>
                        <LineItemButton
                          title="Edit"
                          icon={SvgEdit}
                          sizePreset="main-ui"
                          variant="section"
                          onClick={() => {
                            setMenuOpen(false);
                            openEdit();
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
                      </>
                    )}
                  </Popover.Menu>
                </Popover.Content>
              </Popover>
            </span>
          )}
        </span>
      </div>

      {editing ? (
        <div>
          <MentionTextarea
            value={draft}
            autoFocus
            onChange={setDraft}
            onPickMention={(d, id) => {
              editMentions.current[d] = id;
            }}
          />
          <div className="mt-1.5 flex justify-end gap-2">
            <Button
              prominence="tertiary"
              size="sm"
              onClick={() => setEditing(false)}
            >
              Cancel
            </Button>
            <Button
              variant="action"
              size="sm"
              disabled={busy || !draft.trim()}
              onClick={async () => {
                const next = tokenizeMentions(
                  draft.trim(),
                  editMentions.current,
                );
                if (await onEdit(comment.id, next)) setEditing(false);
              }}
            >
              Save
            </Button>
          </div>
        </div>
      ) : (
        <div className="text-[13px] [word-break:break-word] whitespace-pre-wrap text-(--text-05)">
          {parseBody(comment.body).map((seg, i) =>
            seg.kind === "mention" ? (
              <span
                key={i}
                className="rounded-(--radius-04) bg-(--background-tint-03) px-[3px] font-medium text-(--text-05)"
              >
                @{seg.name}
              </span>
            ) : (
              <span key={i}>{seg.text}</span>
            ),
          )}
        </div>
      )}
    </div>
  );
}

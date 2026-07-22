"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";
import {
  Divider,
  EndOfList,
  SelectButton,
  Text,
} from "@onyx-ai/opal/components";
import { SvgMenu } from "@onyx-ai/opal/icons";
import { Section } from "@onyx-ai/opal/layouts";

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
import type { CommentDraft } from "@/lib/editor/comments";
import type { CoeditorHandle } from "@/lib/editor/components";
import { tokenizeMentions } from "@/lib/commentMentions";
import { useIsMobile } from "@/lib/viewport";
import type { CommentThreadView, CommentView } from "@/types";

import { CommentMarginRail } from "./CommentMarginRail";
import { PanelSearchField } from "./PanelSearch";
import {
  CommentInput,
  CommentMessage,
  NewCommentComposer,
  isNewComment,
} from "./commentCards";

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
  /** Hovered thread, so the page can light its doc highlight (mock 1855). */
  onHoverThread?: (id: string | null) => void;
  /** The live editor, required by anchored mode to track doc positions. */
  editorRef?: RefObject<CoeditorHandle | null>;
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

/** Deep-link to a specific thread: the page route reads `?comment=<id>`, opens
 * the panel, and scrolls to the thread's anchored span. Uses the durable
 * id-based URL so the link survives a page rename/move. */
function commentLink(path: string, rootId: string): Promise<string> {
  return shareableWikiUrl(path, `comment=${rootId}`);
}

// Searchable text for a thread across every message.
function threadHaystack(t: CommentThreadView): string {
  return [t.root, ...t.replies]
    .flatMap((c) => [c.author_display, c.body, c.quoted_text])
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

/**
 * Comments tab with the mock's two modes. Anchored, the default (mock
 * 1855:281270): a transparent body where cards track their doc positions
 * beside the text, only the search row carries chrome. List (mock
 * 1856:285030): a bordered panel holding the search bar, thread cards
 * ordered by document position, a Resolved section, and the end-of-list
 * count. Threads expand in place (mock 778:262971) with a reply input
 * under the expanded card. Orphaned and resolved threads only appear in
 * list mode, which anchored users reach through the search-row toggle.
 * Mobile always lists, the sheet covers the doc the cards would track.
 */
export function CommentsPanel({
  path,
  headSha,
  draft,
  threads,
  onChanged,
  activeId,
  onActivate,
  onHoverThread,
  editorRef,
  onDraftConsumed,
  onClose: _onClose,
  fullHeight: _fullHeight,
}: Props) {
  const { user } = useAuth();
  const isMobile = useIsMobile();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [listView, setListView] = useState(false);
  const listMode = listView || isMobile || !editorRef;

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

  const q = query.trim().toLowerCase();
  const searched = q
    ? threads.filter((t) => threadHaystack(t).includes(q))
    : threads;

  // Order threads to match the doc: by their referenced position (start_offset)
  // top-to-bottom. Orphaned threads ("Original content deleted") have no live
  // anchor, so they sink to the bottom, ordered among themselves by creation.
  const orderedThreads = [...searched].sort((a, b) => {
    const ao = a.root.status === "orphaned" ? null : a.root.start_offset;
    const bo = b.root.status === "orphaned" ? null : b.root.start_offset;
    if (ao === null && bo === null)
      return a.root.created_at.localeCompare(b.root.created_at);
    if (ao === null) return 1;
    if (bo === null) return -1;
    return ao - bo;
  });

  const openThreads = orderedThreads.filter(
    (t) => t.root.status !== "resolved",
  );
  const resolvedThreads = orderedThreads.filter(
    (t) => t.root.status === "resolved",
  );

  // A `?comment=` deep-link can land on a resolved thread; scroll it into
  // view once threads have loaded so activation is visible.
  useEffect(() => {
    if (!activeId) return;
    document
      .querySelector(`[data-thread-id="${activeId}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [activeId, threads.length]);

  const renderThread = (t: CommentThreadView) => (
    <ThreadCard
      key={t.root.id}
      thread={t}
      path={path}
      selfId={user?.id}
      isAdmin={!!user?.is_admin}
      busy={busy}
      active={t.root.id === activeId}
      onActivate={() => onActivate(t.root.id)}
      onHoverChange={(h) => onHoverThread?.(h ? t.root.id : null)}
      run={run}
    />
  );

  // Shared by the anchored composer and the list composer, clearing the
  // draft only when the create actually landed.
  const submitDraft = async (body: string) => {
    if (!draft) return;
    if (!headSha) {
      setError("page version unknown, reload and retry");
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
  };

  const searchRow = (
    <Section
      flexDirection="row"
      justifyContent="start"
      alignItems="center"
      height="fit"
      gap={0.25}
      className="shrink-0"
    >
      <PanelSearchField
        value={query}
        onChange={setQuery}
        placeholder="Search comments…"
      />
      {!isMobile && editorRef && (
        <SelectButton
          icon={SvgMenu}
          state={listView ? "selected" : "empty"}
          tooltip={listView ? "Anchored view" : "List view"}
          onClick={() => setListView((v) => !v)}
        />
      )}
    </Section>
  );

  if (!listMode) {
    // Anchored mode (mock 1855): transparent body, chromed search row, cards
    // positioned inline with the doc through the shared anchor engine.
    return (
      <Section
        justifyContent="start"
        alignItems="stretch"
        height="auto"
        gap={0.25}
        className="min-h-0 flex-1"
      >
        <div className="shrink-0 rounded-(--radius-12) border border-(--border-01) p-1">
          {searchRow}
        </div>
        {error && (
          <div className="px-2 py-1 text-xs text-(--status-text-error-05)">
            {error}
          </div>
        )}
        <CommentMarginRail
          inPanel
          threads={searched}
          draft={draft}
          editorRef={editorRef!}
          activeId={activeId}
          onActivate={onActivate}
          onHoverThread={onHoverThread}
          selfName={user?.name || user?.email || "You"}
          path={path}
          selfId={user?.id}
          isAdmin={!!user?.is_admin}
          busy={busy}
          run={run}
          onSubmitDraft={(body) => void submitDraft(body)}
          onCancelDraft={onDraftConsumed}
        />
      </Section>
    );
  }

  return (
    <Section
      justifyContent="start"
      alignItems="stretch"
      height="auto"
      gap={0}
      padding={0.25}
      className="min-h-0 flex-1 overflow-clip rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-01)"
    >
      {searchRow}
      <Section
        justifyContent="start"
        alignItems="stretch"
        height="auto"
        gap={0.25}
        className="scroll-fade-bottom scroll-y-hidden min-h-0 flex-1 overflow-y-auto"
      >
        {error && (
          <div className="px-2 py-1 text-xs text-(--status-text-error-05)">
            {error}
          </div>
        )}

        {draft && (
          <NewCommentComposer
            selfName={user?.name || user?.email || "You"}
            disabled={busy || !headSha}
            onCancel={onDraftConsumed}
            onSubmit={(body) => void submitDraft(body)}
          />
        )}

        {threads.length === 0 && !draft && (
          <div className="p-3">
            <Text font="secondary-body" color="text-03">
              No comments yet. Select text in the page to add one.
            </Text>
          </div>
        )}

        {threads.length > 0 && searched.length === 0 && (
          <div className="p-3">
            <Text font="secondary-body" color="text-03">
              No comments match.
            </Text>
          </div>
        )}

        {openThreads.map(renderThread)}

        {resolvedThreads.length > 0 && (
          <>
            <div className="pt-1">
              <Divider title="Resolved" />
            </div>
            {resolvedThreads.map(renderThread)}
          </>
        )}

        {searched.length > 0 && (
          <div className="px-4 py-2">
            <EndOfList
              title={`${searched.length} Comment${searched.length === 1 ? "" : "s"}`}
            />
          </div>
        )}
      </Section>
    </Section>
  );
}

/** One thread card (mock 1856 list rows, mock 669 anchored): collapsed
 *  shows the root message, active/expanded shows the whole conversation with
 *  a reply input below (mock 778:262971). Unread = white card with the blue
 *  marker, read sits on tint-01, resolved on tint-02. Anchored cards round
 *  at 12 instead of the list's 8. */
export function ThreadCard({
  thread,
  path,
  selfId,
  isAdmin,
  busy,
  active,
  anchored,
  onActivate,
  onHoverChange,
  run,
}: {
  thread: CommentThreadView;
  path: string;
  selfId: string | undefined;
  isAdmin: boolean;
  busy: boolean;
  active: boolean;
  anchored?: boolean;
  onActivate: () => void;
  onHoverChange?: (hovering: boolean) => void;
  run: (fn: () => Promise<unknown>) => Promise<boolean>;
}) {
  const { root } = thread;
  const resolved = root.status === "resolved";
  const conversation = [root, ...thread.replies];
  const latest = conversation[conversation.length - 1]!;
  const unread = !resolved && isNewComment(latest.created_at);

  const [replyBody, setReplyBody] = useState("");
  const [replyMentions] = useState<Record<string, string>>({});

  // Active threads expand to the full conversation, collapsed cards show
  // only the root message (mock 1856 stacks both forms).
  const expanded = active;
  const shown = expanded ? conversation : [root];

  const bg = resolved
    ? "bg-(--background-tint-02)"
    : unread || expanded
      ? "bg-(--background-tint-00)"
      : "bg-(--background-tint-01)";
  const shadow = expanded
    ? "shadow-(--shadow-box-01)"
    : "shadow-(--shadow-box-00) hover:shadow-(--shadow-box-01) hover:bg-(--background-tint-00)";

  const messageActions = (c: CommentView) => ({
    onResolve: () => void run(() => resolveThread(root.id)),
    onReopen: () => void run(() => reopenThread(root.id)),
    onCopyLink: async () => {
      // Durable id-based deep-link (survives rename/move). A transient
      // id-resolve failure skips the copy rather than handing over a
      // fragile path link.
      try {
        const url = await commentLink(path, root.id);
        await navigator.clipboard.writeText(url);
        return true;
      } catch {
        return false;
      }
    },
    onEdit: (body: string) => run(() => editComment(c.id, body)),
    onDelete: () => void run(() => deleteComment(c.id)),
  });

  return (
    <div className="flex w-full shrink-0 flex-col" data-thread-id={root.id}>
      {/* raw-ok: the card is a selectable region hosting nested buttons and inputs, which a native button cannot contain */}
      <div
        role="button"
        tabIndex={0}
        onClick={onActivate}
        onMouseEnter={() => onHoverChange?.(true)}
        onMouseLeave={() => onHoverChange?.(false)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.target === e.currentTarget) {
            e.preventDefault();
            onActivate();
          }
        }}
        className={`group/comment flex w-full cursor-pointer flex-col overflow-clip text-left ${anchored ? "rounded-(--radius-12)" : "rounded-(--radius-08)"} ${bg} ${shadow}`}
      >
        {root.status === "orphaned" && (
          <div className="px-2 pt-1 text-[12px] leading-4 text-(--text-03)">
            Original content deleted
          </div>
        )}
        {shown.map((c) => (
          <CommentMessage
            key={c.id}
            comment={c}
            authorName={authorLabel(c.author_user_id, c.author_display, selfId)}
            isRoot={c.id === root.id}
            resolved={resolved}
            unread={unread && !expanded}
            emphasized={expanded}
            canModify={isAdmin || c.author_user_id === selfId}
            busy={busy}
            actions={messageActions(c)}
          />
        ))}
      </div>
      {expanded && !resolved && (
        <div className="pt-1 pb-3">
          <CommentInput
            placeholder="Reply…"
            value={replyBody}
            onChange={setReplyBody}
            onPickMention={(d, id) => {
              replyMentions[d] = id;
            }}
            disabled={busy}
            submitTooltip="Send reply"
            onSubmit={async () => {
              // Clear only on success so a failed reply isn't lost.
              const ok = await run(() =>
                replyToComment(
                  root.id,
                  tokenizeMentions(replyBody.trim(), replyMentions),
                ),
              );
              if (ok) setReplyBody("");
            }}
          />
        </div>
      )}
    </div>
  );
}

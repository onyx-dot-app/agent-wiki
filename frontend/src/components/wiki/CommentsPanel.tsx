"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";
import {
  Divider,
  EndOfList,
  SelectButton,
  Text,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";

import { useAuth } from "@/lib/auth";
import { createComment } from "@/lib/comments";
import type { CoeditorHandle, CommentDraft } from "@/lib/tiptapEditor/types";
import { useIsMobile } from "@/lib/viewport";
import type { CommentThreadView } from "@/types";

import { CommentMarginRail } from "./CommentMarginRail";
import { SvgListLines } from "./icons";
import { PanelSearchField } from "./PanelSearch";
import { NewCommentComposer, ThreadCard } from "./commentCards";

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
  /** List/anchored mode is page-owned: the page hides the editor's native
   * scrollbar while anchored mode shows the viewport-edge one. */
  listView: boolean;
  onListViewChange: (v: boolean) => void;
  onDraftConsumed: () => void;
  onClose: () => void;
  fullHeight?: boolean;
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
 * Comments tab with the mock's two modes: anchored (default, mock 1855,
 * cards track doc positions, only the search row is chromed) and list
 * (mock 1856, bordered shell with a Resolved section), toggled from the
 * search row. Orphaned and resolved threads appear only in list mode.
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
  listView,
  onListViewChange,
  onDraftConsumed,
  onClose: _onClose,
  fullHeight: _fullHeight,
}: Props) {
  const { user } = useAuth();
  const isMobile = useIsMobile();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
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
      onDeactivate={() => onActivate(null)}
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
          icon={SvgListLines}
          state={listView ? "selected" : "empty"}
          tooltip={listView ? "Anchored view" : "List view"}
          onClick={() => onListViewChange(!listView)}
        />
      )}
    </Section>
  );

  if (!listMode) {
    // Anchored mode (mock 1855). The doc's scrollbar renders at the
    // viewport edge, the page hides the native one (panel-anchored).
    return (
      <Section
        justifyContent="start"
        alignItems="stretch"
        height="auto"
        gap={0.25}
        className="relative min-h-0 flex-1"
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

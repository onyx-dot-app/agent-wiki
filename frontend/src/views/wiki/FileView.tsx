"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  Button,
  Divider,
  LineItemButton,
  OpenButton,
  Popover,
  PopoverMenu,
  SelectButton,
} from "@onyx-ai/opal/components";
import { Content } from "@onyx-ai/opal/layouts";
import { cn } from "@onyx-ai/opal/utils";
import {
  SvgChevronLeft,
  SvgChevronRight,
  SvgExternalLink,
  SvgDocFile,
  SvgFolder,
  SvgHistory,
  SvgMoreHorizontal,
  SvgShare,
  SvgSidebar,
  SvgSparkle,
  SvgX,
} from "@onyx-ai/opal/icons";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { TriggerPanel } from "@/components/triggers/TriggerPanel";
import { WatchingPanel } from "@/components/wiki/WatchingPanel";
import { DocPanel, type DocPanelTab } from "@/components/wiki/DocPanel";
import { DiffView } from "@/components/wiki/DiffView";
import {
  UpdateHistoryRail,
  VersionHistoryList,
} from "@/components/wiki/VersionHistoryList";
import { RunAgentPanel } from "@/components/wiki/RunAgentPanel";
import { ShareDialog } from "@/components/wiki/ShareDialog";
import { CommentsPanel } from "@/components/wiki/CommentsPanel";
import { sourceKey, SourcesPanel } from "@/components/wiki/SourcesPanel";
import type { AnchoredHighlightTarget } from "@/lib/editor/highlights";
import { SWR_KEYS } from "@/lib/swr-keys";
import { Path2ReviewBanner } from "@/components/wiki/Path2ReviewBanner";
import { UpdateHealthBanner } from "@/components/wiki/UpdateHealthBanner";
import { UpdatePolicyPanel } from "@/components/wiki/UpdatePolicyPanel";
import { CommentMarginRail } from "@/components/wiki/CommentMarginRail";
import { toast } from "@/hooks/useToast";
import { useLeftPanel } from "@/providers/LeftPanelProvider";
import { craftFailureMessage } from "@/lib/craft";
import {
  closeSession,
  useAgentSessions,
  type AgentSessionSummary,
} from "@/lib/launchers";
import { apiFetch } from "@/lib/api";
import { deleteTrigger, useTriggers, type Trigger } from "@/lib/triggers";
import { wikiHref, resolveIds, revalidateWiki } from "@/lib/wikiHref";
import { createComment, listComments } from "@/lib/comments";
import type {
  CommentDraft,
  CommentHighlightTarget,
} from "@/lib/editor/comments";
import { pageTitle } from "@/lib/wiki/utils";
import { useAuth } from "@/lib/auth";
import {
  Coeditor,
  CoeditPresenceBar,
  type CoeditorHandle,
} from "@/lib/editor/components";
import { useCoeditSession } from "@/lib/editor/hooks";
import {
  useAgentsBarHost,
  useHeaderActionsHost,
  useHeaderCrumbHost,
  useRightPanelHost,
} from "@/providers/WikiHeaderActionsProvider";
import { useDrafting } from "@/lib/drafting";
import {
  getDraftState,
  getTemplate,
  listTemplateSummaries,
  setDraftTemplate,
  type DocumentTemplateSummary,
} from "@/lib/templates";
import { absoluteTime, longDateTime, relativeTime } from "@/lib/time";
import { useIsMobile } from "@/lib/viewport";
import { fetchFileDiff, fetchFileHistory } from "@/lib/wiki/svc";
import type { CommitInfo, FileDiffResponse } from "@/lib/wiki/types";
import type {
  CommentThreadView,
  DocumentActivity,
  DocumentActivityResponse,
  SourceRef,
  SourceSpan,
} from "@/types";

// Local shape for the /wiki/file API response — mirrored from page.tsx.
interface FileResponse {
  path: string;
  body: string;
  ref?: string;
  head_sha?: string | null;
  // Whether the caller may edit this page. False renders the live session as
  // a pure viewer: read-only editor, no cursor/checkpoint sends.
  can_write?: boolean;
  /** Ingested documents credited to this page (the Sources tab list). */
  sources?: SourceRef[];
}

// Minimal doc-entry shape needed by collectFolders / DestinationSelect.
interface DocEntry {
  path: string;
  updated_at: string;
}

interface DocTitleProps {
  path: string;
  /** Renders the title inline-editable (Opal's `Content editable`) and calls
   * back with the committed new title. Omit to render a static title. */
  onRename?: (newTitle: string) => void;
}

/** Renders the page title (inline-editable when `onRename` is given) and a
 * divider below it. Capped at the same `max-w-[768px]` and centered the same
 * way as the editor column below it, so the title and the doc text share one
 * left margin instead of drifting apart. */
export function DocTitle({ path, onRename }: DocTitleProps) {
  return (
    <div className="rail-inset mx-auto flex w-full max-w-[768px] flex-col gap-6 pb-6">
      <Content
        icon={SvgDocFile}
        sizePreset="headline"
        variant="heading"
        title={pageTitle(path)}
        editable={!!onRename}
        onTitleChange={onRename}
      />
      <Divider paddingParallel="fit" paddingPerpendicular="fit" />
    </div>
  );
}

interface FileViewProps {
  path: string;
}

export function FileView({ path }: FileViewProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isMobile = useIsMobile();
  const host = useHeaderActionsHost();
  const crumbHost = useHeaderCrumbHost();
  const agentsBarHost = useAgentsBarHost();
  const rightHost = useRightPanelHost();
  const { isActivitiesOpen, toggleActivities } = useLeftPanel();
  const { setDrafting, requestExpand } = useDrafting();
  const { user } = useAuth();
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Watching tab editor: null = the watcher list, otherwise the editor is
  // showing (trigger null = creating a new watcher on this page).
  const [watcherEditor, setWatcherEditor] = useState<{
    trigger: Trigger | null;
  } | null>(null);
  const [runAgentOpen, setRunAgentOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const confirmDialog = useConfirm();
  const { refresh: refreshTriggers } = useTriggers();
  // History state. `viewingSha` is null when looking at the working-tree
  // (latest) version, otherwise it's the version whose diff fills the doc
  // column. `historyOpen` is version mode: the rail becomes the Update
  // History surface and the header collapses to the version chip.
  // `historyListOpen` is the milder state: the Updates tab's history card
  // expanded into its inline version list.
  const [headSha, setHeadSha] = useState<string | null>(null);
  // From the file read's `can_write` — false renders the live session as a
  // pure viewer (read-only editor, no write calls). Optimistic `true` until
  // the read lands; the server rejects any write regardless.
  const [canWrite, setCanWrite] = useState(true);
  const [sources, setSources] = useState<SourceRef[]>([]);
  const [viewingSha, setViewingSha] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyListOpen, setHistoryListOpen] = useState(false);
  const [commits, setCommits] = useState<CommitInfo[] | null>(null);
  const [diffData, setDiffData] = useState<FileDiffResponse | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);
  // Active tab of the tabbed side panel (null = closed). The rail holds one
  // occupant at a time: this panel or history mode, never both.
  const [panelTab, setPanelTab] = useState<DocPanelTab | null>(null);
  // Comments. `commentDraft` is a pending text selection being composed;
  // `selTool` is the floating "Comment" affordance shown on select.
  // `commentsListView` lives here (not in the panel) because anchored mode
  // also swaps the editor's native scrollbar for the viewport-edge one.
  const [commentDraft, setCommentDraft] = useState<CommentDraft | null>(null);
  const [commentsListView, setCommentsListView] = useState(false);
  const [commentThreads, setCommentThreads] = useState<CommentThreadView[]>([]);
  const [activeCommentId, setActiveCommentId] = useState<string | null>(null);
  const [selTool, setSelTool] = useState<{
    x: number;
    y: number;
    draft: CommentDraft;
  } | null>(null);
  const coeditorRef = useRef<CoeditorHandle | null>(null);
  // `viewingVersion`: a history version is displayed in the main pane (no
  // live editor — DiffView instead). `viewingOld`: that version is not the
  // newest commit for this file (`headSha` tracks `commits[0]`), so the
  // Restore/Back actions replace the Exit action and the chip shows a
  // relative age. Computed early: the comment effects and the coedit
  // session below both key off it.
  const viewingVersion = viewingSha !== null;
  const viewingOld = viewingVersion && viewingSha !== headSha;
  const segments = path.split("/");
  const parentSlug = segments.slice(0, -1).join("/");
  const currentBasename = segments[segments.length - 1] ?? path;
  const currentBasenameNoExt = currentBasename.replace(/\.md$/i, "");
  // Page owns the comment threads (so highlights render even with the panel
  // closed). Auto-open the panel once per path on unresolved comments.
  const autoOpenedPathRef = useRef<string | null>(null);
  // A `?comment=<id>` deep-link is focused once per id (reset on path change).
  const focusedCommentRef = useRef<string | null>(null);

  // Opening the panel closes history mode, the other rail occupant. Every
  // entry point routes through here.
  const openPanel = useCallback((tab: DocPanelTab) => {
    setHistoryOpen(false);
    setPanelTab(tab);
  }, []);

  const closePanel = useCallback(() => {
    setPanelTab(null);
    setCommentDraft(null);
  }, []);

  const openComments = useCallback(() => openPanel("comments"), [openPanel]);

  const refreshComments = useCallback(async () => {
    try {
      const t = await listComments(path);
      setCommentThreads(t);
      // Auto-open once per page only when something needs attention — i.e. an
      // unresolved thread (open or orphaned, matching the panel's main list).
      // A page whose comments are all resolved stays closed.
      const hasUnresolved = t.some(
        (thread) => thread.root.status !== "resolved",
      );
      if (hasUnresolved && autoOpenedPathRef.current !== path) {
        autoOpenedPathRef.current = path;
        openComments();
      }
    } catch {
      // comments are non-critical chrome; ignore load failures
    }
  }, [path, openComments]);

  useEffect(() => {
    autoOpenedPathRef.current = null;
    focusedCommentRef.current = null;
    setCommentThreads([]);
    void refreshComments();
  }, [refreshComments]);

  const sourcesTabOpen = panelTab === "sources";

  // Comment thread spans to highlight in the editor. Cleared while viewing
  // an old commit (DiffView, no live editor) and while the Sources tab has
  // the doc, so the two highlight families never mix. The active/hovered
  // ids ride a separate prop: the editor maps these offsets through local
  // edits, and a hover or selection flip must not re-send the unmapped ones.
  // Hovering a card lights its doc highlight like selection does (mock
  // 1855 annotation: "Hover highlight - match the hovered comment").
  const [hoveredCommentId, setHoveredCommentId] = useState<string | null>(null);
  const commentHighlights = useMemo<CommentHighlightTarget[]>(() => {
    if (viewingVersion || sourcesTabOpen) return [];
    return commentThreads
      .map((t) => t.root)
      .filter(
        // Resolved threads disappear from the panel, so drop their doc
        // highlight too; orphaned ones have no live span to paint.
        (r) =>
          r.status !== "orphaned" &&
          r.status !== "resolved" &&
          r.start_offset !== null &&
          r.end_offset !== null,
      )
      .map((r) => ({
        id: r.id,
        startOffset: r.start_offset as number,
        endOffset: r.end_offset as number,
      }));
  }, [commentThreads, viewingVersion, sourcesTabOpen]);
  const activeCommentIds = useMemo(
    () =>
      [activeCommentId, hoveredCommentId].filter((id): id is string => !!id),
    [activeCommentId, hoveredCommentId],
  );

  // Source-attributed spans light up in the doc while the Sources tab is
  // open (mock 1832:81274). Fetched lazily per head sha since the server
  // remaps offsets to HEAD, so an old version never fetches.
  const { data: sourceSpans } = useSWR(
    sourcesTabOpen && headSha && !viewingVersion
      ? SWR_KEYS.sourceSpans(path, headSha)
      : null,
    () =>
      apiFetch<SourceSpan[]>(
        `/wiki/source-spans?path=${encodeURIComponent(path)}`,
      ),
    // Offsets are only valid for the doc revision they were fetched for, so
    // a path or head change must blank the highlights, never reuse them.
    { keepPreviousData: false },
  );
  const sourceHighlights = useMemo<AnchoredHighlightTarget[]>(() => {
    if (!sourcesTabOpen) return [];
    return (sourceSpans ?? []).map((sp) => ({
      id: sourceKey(sp),
      startOffset: sp.start_offset,
      endOffset: sp.end_offset,
    }));
  }, [sourcesTabOpen, sourceSpans]);
  const activateSource = useCallback(
    (key: string) => {
      const span = (sourceSpans ?? []).find((sp) => sourceKey(sp) === key);
      if (span) coeditorRef.current?.scrollToOffset(span.start_offset);
    },
    [sourceSpans],
  );
  // Attribution runs both ways: hovering a card lights only that source's
  // spans, and a caret inside a span lights its card (the editor reports
  // caret-source ids against its live-mapped offsets).
  const [hoveredSourceKey, setHoveredSourceKey] = useState<string | null>(null);
  const [caretSourceKeys, setCaretSourceKeys] = useState<string[]>([]);
  const activeSourceIds = useMemo(
    () =>
      hoveredSourceKey && !caretSourceKeys.includes(hoveredSourceKey)
        ? [hoveredSourceKey, ...caretSourceKeys]
        : caretSourceKeys,
    [hoveredSourceKey, caretSourceKeys],
  );

  // The doc area hosting the lane, measured at interaction time so the
  // panel fallback keys off the same width as the lane's container query.
  const docRowRef = useRef<HTMLDivElement | null>(null);

  // Only open threads with a live anchor can float in the margin.
  const marginThreadCount = useMemo(
    () =>
      commentThreads.filter(
        (t) => t.root.status === "open" && t.root.start_offset !== null,
      ).length,
    [commentThreads],
  );

  // Renaming is its own action now (Opal's `Content editable` on DocTitle),
  // decoupled from the coedit session/checkpointing — no more "rename at
  // Save time."
  const handleRename = useCallback(
    async (newTitle: string) => {
      const trimmed = newTitle.trim().replace(/^\/+|\/+$/g, "");
      const noExt = trimmed.replace(/\.md$/i, "");
      if (!noExt || noExt.includes("/") || noExt === currentBasenameNoExt)
        return;
      setError(null);
      try {
        const finalName = noExt + ".md";
        const newRel = parentSlug ? `${parentSlug}/${finalName}` : finalName;
        await apiFetch("/wiki/move", {
          method: "POST",
          body: JSON.stringify({ old_path: path, new_path: newRel }),
        });
        // The id URL survives a rename, so the open page's id→path resolve now
        // points at the old path. Revalidate every wiki cache (including that
        // resolve and the content read) so the page follows to its new path,
        // then route to the renamed doc's durable id URL — falling back to the
        // path URL only for an id-less page.
        await revalidateWiki();
        const id = (await resolveIds([newRel]))[newRel];
        router.replace(id ? wikiHref(id) : `/app/wiki/${newRel}`);
      } catch (e) {
        setError(e instanceof Error ? e.message : "rename failed");
      }
    },
    [path, parentSlug, currentBasenameNoExt, router],
  );

  // The page's live session: everyone viewing the live editor joins it for
  // presence plus real-time updates, and editing is just a capability inside
  // it (`canWrite`, ops write-gated server-side). Left whenever a version
  // diff is showing (including the current version's). See
  // `useCoeditSession`'s `enabled` doc. No explicit Save. Teardown
  // (checkpoint + leave) fires from the hook itself on that
  // transition/unmount, not from a button here.
  const coedit = useCoeditSession({
    path,
    enabled: !viewingVersion,
    committedBody: body,
    myUserId: user?.id ?? null,
    canWrite,
    onEnd: () => {
      void refreshComments();
      void refreshDraftState();
    },
  });

  // The floating margin lane shows on the live desktop doc with the panel
  // closed and something to anchor. Drives both the overlay and the
  // .rail-reserved content reservation.
  const railActive =
    !!coedit.session &&
    !viewingVersion &&
    panelTab === null &&
    !isMobile &&
    (marginThreadCount > 0 || commentDraft !== null);

  // Anchored panel mode hides the editor's native scrollbar (it would sit
  // at the doc/panel boundary) in favor of the panel's viewport-edge one.
  const anchoredPanelActive =
    !!coedit.session &&
    !viewingVersion &&
    panelTab === "comments" &&
    !commentsListView &&
    !isMobile;

  // Select a thread (its span gets the orange highlight) and scroll the
  // editor to bring that span into view. Only an explicit click runs this —
  // keying a scroll off `activeCommentId` alone would also re-scroll on
  // every comment refetch while a thread stays selected.
  const activateComment = useCallback(
    (id: string | null) => {
      setActiveCommentId(id);
      if (!id || viewingVersion) return;
      const root = commentThreads.find((t) => t.root.id === id)?.root;
      if (
        !root ||
        root.status === "orphaned" ||
        root.status === "resolved" ||
        root.start_offset === null
      )
        return;
      coeditorRef.current?.scrollToOffset(root.start_offset);
    },
    [commentThreads, viewingVersion],
  );

  // Deep-link: `?comment=<id>` opens the panel, selects that thread, and
  // scrolls to its anchored span — the shareable-link counterpart to
  // click-to-focus. Runs once per id (focusedCommentRef), and only once the
  // thread is loaded and the editor has mounted (`coedit.session`).
  useEffect(() => {
    const target = searchParams?.get("comment");
    if (!target || loading || viewingVersion || !coedit.session) return;
    if (focusedCommentRef.current === target) return;
    const root = commentThreads.find((t) => t.root.id === target)?.root;
    if (!root) return; // not loaded yet, or not a thread on this page
    focusedCommentRef.current = target;
    setActiveCommentId(target);
    openComments();
    if (
      root.status === "orphaned" ||
      root.status === "resolved" ||
      root.start_offset === null
    )
      return; // selected, but no live span to scroll to
    coeditorRef.current?.scrollToOffset(root.start_offset);
  }, [
    searchParams,
    commentThreads,
    loading,
    viewingVersion,
    coedit.session,
    openComments,
  ]);

  // Selecting text in the editor offers a floating "Comment" affordance
  // anchored above the selection — fed by the Coeditor's selection reporting
  // instead of a DOM `mouseup`/`selectionchange` handler.
  // The margin rail's mutation runner mirrors the panel's success gate;
  // failures surface as toasts since the rail has no error slot.
  const [marginBusy, setMarginBusy] = useState(false);
  const runMargin = useCallback(
    async (fn: () => Promise<unknown>): Promise<boolean> => {
      setMarginBusy(true);
      try {
        await fn();
        await refreshComments();
        return true;
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Comment action failed");
        return false;
      } finally {
        setMarginBusy(false);
      }
    },
    [refreshComments],
  );

  const submitMarginDraft = useCallback(
    async (body: string) => {
      const draft = commentDraft;
      if (!draft) return;
      if (!headSha) {
        toast.error("Page version unknown, reload and retry");
        return;
      }
      const ok = await runMargin(() =>
        createComment({
          path,
          anchorSha: headSha,
          startOffset: draft.startOffset,
          endOffset: draft.endOffset,
          quotedText: draft.quotedText,
          body,
        }),
      );
      if (ok) setCommentDraft(null);
    },
    [commentDraft, headSha, path, runMargin],
  );

  const handleSelectionForComment = useCallback(
    (draft: CommentDraft | null, coords: { x: number; y: number } | null) => {
      setSelTool(draft && coords ? { x: coords.x, y: coords.y, draft } : null);
    },
    [],
  );

  // Active-agents panel (collapsible chip near the top of the doc).
  // We always know the count (so the chip can label "Active agents (N)"),
  // but the entry list only renders when the user expands it.
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [agents, setAgents] = useState<DocumentActivity[]>([]);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  // Inline template gallery state. We show clickable cards above the
  // editor whenever the draft is "empty enough" — either truly blank
  // or still matching the body of the template the user just applied
  // (so they can keep swapping without losing work). Once they edit on
  // top of a template, the gallery disappears.
  const [templates, setTemplates] = useState<DocumentTemplateSummary[] | null>(
    null,
  );
  const [appliedTemplateBody, setAppliedTemplateBody] = useState<string | null>(
    null,
  );
  const [appliedTemplateId, setAppliedTemplateId] = useState<string | null>(
    null,
  );
  const [applyingTemplateId, setApplyingTemplateId] = useState<string | null>(
    null,
  );
  const loadLatest = useCallback(() => {
    setLoading(true);
    setError(null);
    setViewingSha(null);
    setDiffData(null);
    apiFetch<FileResponse>(`/wiki/file?path=${encodeURIComponent(path)}`)
      .then((r) => {
        setBody(r.body);
        setHeadSha(r.head_sha ?? null);
        setCanWrite(r.can_write ?? true);
        setSources(r.sources ?? []);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load"))
      .finally(() => setLoading(false));
  }, [path]);

  useEffect(() => {
    loadLatest();
    setHistoryOpen(false);
    setHistoryListOpen(false);
    setWatcherEditor(null);
    setCommits(null);
  }, [loadLatest]);

  // Active external agent sessions on this page — surfaced in the
  // Active agents bar alongside read/write activity.
  const { sessions: agentSessions, refresh: refreshSessions } =
    useAgentSessions(path);
  const activeSessions = agentSessions.filter(
    (s) =>
      s.status === "active" ||
      s.status === "idle" ||
      // Onyx Craft (in_app) lifecycle states worth surfacing on the page.
      (s.tool_id === "onyx-craft" &&
        (s.status === "provisioning" ||
          s.status === "ready" ||
          s.status === "failed")),
  );

  const handleCloseSession = useCallback(
    async (id: string) => {
      if (
        !(await confirmDialog({
          title: "Close this agent session?",
          confirmLabel: "Close session",
        }))
      )
        return;
      try {
        await closeSession(id, "user_clicked");
      } catch (err) {
        alert(err instanceof Error ? err.message : "Failed to close session");
      } finally {
        await refreshSessions();
      }
    },
    [refreshSessions, confirmDialog],
  );
  // Fetch template summaries once; the gallery uses them as its menu
  // and falls back to "no templates configured" when the list is empty.
  useEffect(() => {
    let cancelled = false;
    listTemplateSummaries()
      .then((rows) => {
        if (!cancelled) setTemplates(rows);
      })
      .catch(() => {
        if (!cancelled) setTemplates([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ``?new=1`` is set by NewDocView after first-create. The doc body
  // was already chosen there, so we land in the rendered view (not
  // edit mode); the param's only remaining job is to expand the chat
  // widget so the assistant is right there on the freshly-created doc.
  const isFreshDoc = searchParams?.get("new") === "1";

  // Drafting state: per-doc template-seeded draft tracked server-side.
  // We re-fetch when the path changes and after each save (the server
  // clears the row once the body diverges from the template snapshot).
  // ``?new=1`` triggers a one-shot expand of the chat widget so the
  // assistant is immediately available while drafting.
  const refreshDraftState = useCallback(async () => {
    try {
      const state = await getDraftState(path);
      setDrafting(
        state
          ? {
              kind: "template",
              path: state.path,
              templateName: state.template_name,
              templateId: state.template_id,
            }
          : null,
      );
      return state;
    } catch {
      setDrafting(null);
      return null;
    }
  }, [path, setDrafting]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await refreshDraftState();
      if (!cancelled && isFreshDoc) requestExpand();
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshDraftState, isFreshDoc, requestExpand]);

  // Clear drafting context on unmount so the banner doesn't follow the
  // user to other pages.
  useEffect(() => {
    return () => setDrafting(null);
  }, [setDrafting]);

  const refreshAgents = useCallback(() => {
    setAgentsError(null);
    apiFetch<DocumentActivityResponse>(
      `/wiki/file/activity?path=${encodeURIComponent(path)}`,
    )
      .then((r) => setAgents(r.agents))
      .catch((e) =>
        setAgentsError(
          e instanceof Error ? e.message : "failed to load activity",
        ),
      );
  }, [path]);

  useEffect(() => {
    refreshAgents();
    setAgentsOpen(false);
    // Refresh on window focus so the chip count tracks reality after
    // the user comes back from another tab. The endpoint is cheap.
    function onFocus() {
      refreshAgents();
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshAgents]);

  const refreshHistory = useCallback(async () => {
    setHistoryError(null);
    try {
      const r = await fetchFileHistory(path);
      setCommits(r.commits);
      setHeadSha(r.head_sha);
      return r;
    } catch (e) {
      setHistoryError(
        e instanceof Error ? e.message : "failed to load history",
      );
      return null;
    }
  }, [path]);

  // The Updates tab's Total Edits summary needs the commit count, so the
  // history loads as soon as the tab opens, not only on card expand.
  useEffect(() => {
    if (panelTab === "updates" && commits === null) void refreshHistory();
  }, [panelTab, commits, refreshHistory]);

  function closeHistory() {
    setHistoryOpen(false);
    // Exiting version mode goes back to the live editor, which rejoins the
    // coedit session (`enabled: !viewingVersion`), and lands on the Updates
    // tab, where the version list lives.
    if (viewingSha !== null) {
      setViewingSha(null);
      setDiffData(null);
    }
    setPanelTab("updates");
  }

  async function toggleHistory() {
    if (historyOpen) {
      closeHistory();
      return;
    }
    // Entering version mode lands on the newest commit's diff rather than
    // leaving the rendered body up until the user clicks a row.
    const loaded = commits ?? (await refreshHistory())?.commits ?? null;
    const newest = loaded?.[0];
    if (newest) {
      void onPickCommit(newest.sha);
      return;
    }
    // No commits: still take the rail so the empty list explains itself.
    setHistoryOpen(true);
    setPanelTab(null);
    setCommentDraft(null);
  }

  // Viewing any version = version mode: the rail becomes the Update History
  // surface and the tabbed panel yields (one rail occupant). Callers must
  // load commits before picking.
  async function onPickCommit(sha: string) {
    setHistoryOpen(true);
    setPanelTab(null);
    setCommentDraft(null);
    if (sha === viewingSha) return;
    setLoading(true);
    setError(null);
    try {
      const r = await fetchFileDiff(path, sha);
      setDiffData(r);
      setViewingSha(sha);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load version");
    } finally {
      setLoading(false);
    }
  }

  function toggleHistoryList() {
    const next = !historyListOpen;
    setHistoryListOpen(next);
    if (next && commits === null) void refreshHistory();
  }

  // Restore = new commit of the viewed body. base_sha HEAD makes it a diff
  // onto the latest version, so concurrent edits merge (true conflicts 409).
  async function restoreVersion() {
    const sha = viewingSha;
    if (!sha || restoring) return;
    setRestoring(true);
    setError(null);
    try {
      const r = await apiFetch<FileResponse>(
        `/wiki/file?path=${encodeURIComponent(path)}&ref=${encodeURIComponent(sha)}`,
      );
      await apiFetch("/wiki/file", {
        method: "PUT",
        body: JSON.stringify({ path, body: r.body, base_sha: headSha }),
      });
      setHistoryOpen(false);
      setPanelTab("updates");
      loadLatest();
      void refreshHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to restore version");
    } finally {
      setRestoring(false);
    }
  }

  async function onPickTemplate(template: DocumentTemplateSummary) {
    setApplyingTemplateId(template.id);
    setError(null);
    try {
      const full = await getTemplate(template.id);
      coedit.setDoc(full.body);
      setAppliedTemplateBody(full.body);
      setAppliedTemplateId(template.id);
      await setDraftTemplate(path, template.id);
      await refreshDraftState();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to apply template");
    } finally {
      setApplyingTemplateId(null);
    }
  }

  async function onPickBlank() {
    coedit.setDoc("");
    setAppliedTemplateBody(null);
    setAppliedTemplateId(null);
    setError(null);
    try {
      await setDraftTemplate(path, null);
      // Don't go through ``refreshDraftState`` — the server only knows
      // about template-backed drafts (``document_drafts``), so it would
      // return null and clear drafting. Set blank-drafting locally
      // instead so the chat widget spins up a generic kickoff session.
      setDrafting({ kind: "blank", path });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to clear template");
    }
  }

  // Page actions live in the single pinned header (WikiHeader), not a second
  // header inside the scroll area. They portal into its right-aligned slot.
  // The panel toggle is a SelectButton so an open panel shows the selected
  // tint, the others are tertiary icon Buttons. There is no Edit/Done button
  // since the editor is always live and autosaves, with `saveStatus` as the
  // only feedback for that.
  const panelOpen = panelTab !== null;

  // Version mode strips the header down to crumbs + version chip (mock
  // 1912:355117 hides every action). Back to Current / Restore / Exit live
  // in the rail's own header row instead.
  const viewingCommit = commits?.find((c) => c.sha === viewingSha) ?? null;
  const versionActions = viewingOld ? (
    <>
      <Button
        prominence="secondary"
        disabled={loading}
        onClick={() => headSha && void onPickCommit(headSha)}
      >
        Back to Current
      </Button>
      <Button
        variant="action"
        disabled={restoring}
        onClick={() => void restoreVersion()}
      >
        {restoring ? "Restoring…" : "Restore This Version"}
      </Button>
    </>
  ) : (
    <Button prominence="tertiary" rightIcon={SvgX} onClick={closeHistory}>
      Exit Update History
    </Button>
  );

  // Dismissible version chip joining the breadcrumbs (mock chip in
  // 1912:355220): history glyph, spelled-out timestamp, relative age or
  // "(Current)", and an x back to the live editor.
  const versionChip = viewingCommit ? (
    <span className="flex items-center overflow-hidden rounded-(--radius-08) bg-(--background-tint-02) px-1 py-[2px]">
      <span className="flex size-4 shrink-0 items-center justify-center text-(--text-04)">
        <SvgHistory size={13} />
      </span>
      <span className="flex items-baseline gap-[2px] px-[2px] text-[14px] leading-5 whitespace-nowrap">
        <span className="max-w-40 truncate font-medium text-(--text-04)">
          {longDateTime(viewingCommit.ts)}
        </span>
        <span className="max-w-40 truncate text-(--text-03)">
          {viewingOld
            ? `(${relativeTime(viewingCommit.ts, "long")})`
            : "(Current)"}
        </span>
      </span>
      <Button
        size="2xs"
        prominence="tertiary"
        icon={SvgX}
        tooltip="Exit update history"
        onClick={closeHistory}
      />
    </span>
  ) : null;

  const versionList = (
    <VersionHistoryList
      commits={commits}
      error={historyError}
      headSha={headSha}
      viewingSha={viewingSha}
      selfName={user?.name ?? null}
      onPick={(sha) => void onPickCommit(sha)}
    />
  );

  // One element for both hosts: the desktop right-rail portal and the
  // mobile sheet render the identical rail.
  const historyRail = (
    <UpdateHistoryRail
      commits={commits}
      error={historyError}
      headSha={headSha}
      viewingSha={viewingSha}
      selfName={user?.name ?? null}
      onPick={(sha) => void onPickCommit(sha)}
      headerActions={versionActions}
    />
  );

  const headerActions =
    !loading && !error && !historyOpen ? (
      <>
        {/* Save-in-flight and failure feedback only. The mock's header
            carries no idle "Saved" label, so the saved state renders
            nothing. */}
        {!viewingVersion && coedit.saveStatus !== "saved" && (
          <span className="mr-1 text-[12px] text-(--text-03)">
            {coedit.saveStatus === "saving" ? "Saving…" : "Couldn't save"}
          </span>
        )}
        <Button
          icon={SvgShare}
          prominence="tertiary"
          tooltip="Share"
          onClick={() => setShareOpen(true)}
        />
        <Popover open={moreOpen} onOpenChange={setMoreOpen}>
          <Popover.Trigger asChild>
            <span className="inline-flex">
              <Button
                icon={SvgMoreHorizontal}
                prominence="tertiary"
                tooltip="More"
              />
            </span>
          </Popover.Trigger>
          <Popover.Content width="fit" align="end">
            <PopoverMenu>
              <LineItemButton
                title="Run Agent"
                icon={SvgSparkle}
                sizePreset="main-ui"
                variant="section"
                onClick={() => {
                  setMoreOpen(false);
                  setRunAgentOpen(true);
                }}
              />
              <LineItemButton
                title="Version history"
                icon={SvgHistory}
                sizePreset="main-ui"
                variant="section"
                onClick={() => {
                  setMoreOpen(false);
                  void toggleHistory();
                }}
              />
            </PopoverMenu>
          </Popover.Content>
        </Popover>
        <span className="panel-toggle inline-flex">
          <SelectButton
            icon={SvgSidebar}
            state={panelOpen ? "selected" : "empty"}
            tooltip={panelOpen ? "Close panel" : "Open panel"}
            onClick={() => (panelOpen ? closePanel() : openPanel("updates"))}
          />
        </span>
      </>
    ) : null;

  // One body per tab, shared by the desktop rail portal and the mobile sheet.
  const panelBody = (() => {
    switch (panelTab) {
      case "updates":
        return (
          // The mock's Panel body (1790:52468) pads the card stack 8px/4px.
          <div className="scroll-y-hidden flex min-h-0 flex-1 flex-col overflow-y-auto px-2 py-1">
            <UpdatePolicyPanel
              path={path}
              onShowHistory={toggleHistoryList}
              historyOpen={historyListOpen}
              historyList={versionList}
              totalEdits={commits ? commits.length : null}
              fullHeight
            />
          </div>
        );
      case "comments":
        return (
          // Same card-stack host as the other tabs. The panel scrolls inside
          // its bordered shell.
          <div className="flex min-h-0 flex-1 flex-col px-2 py-1">
            <CommentsPanel
              path={path}
              headSha={headSha}
              draft={commentDraft}
              threads={commentThreads}
              onChanged={refreshComments}
              activeId={activeCommentId}
              onActivate={activateComment}
              onHoverThread={setHoveredCommentId}
              editorRef={viewingVersion ? undefined : coeditorRef}
              listView={commentsListView}
              onListViewChange={setCommentsListView}
              onDraftConsumed={() => setCommentDraft(null)}
              onClose={closePanel}
              fullHeight
            />
          </div>
        );
      case "sources":
        return (
          <div className="flex min-h-0 flex-1 flex-col px-2 py-1">
            <SourcesPanel
              sources={sources}
              activeKeys={caretSourceKeys}
              onActivateSource={viewingVersion ? undefined : activateSource}
              onHoverSource={viewingVersion ? undefined : setHoveredSourceKey}
            />
          </div>
        );
      case "watching":
        return (
          // The watcher list scrolls inside its bordered panel. Only the
          // docked editor needs this wrapper to scroll.
          <div
            className={`flex min-h-0 flex-1 flex-col px-2 py-1 ${
              watcherEditor ? "scroll-y-hidden overflow-y-auto" : ""
            }`}
          >
            {watcherEditor ? (
              <TriggerPanel
                open
                docked
                initial={watcherEditor.trigger ?? { scope_path: path }}
                lockScope={!watcherEditor.trigger}
                onClose={() => setWatcherEditor(null)}
                onSaved={() => {
                  setWatcherEditor(null);
                  void refreshTriggers();
                }}
                onDelete={
                  watcherEditor.trigger
                    ? async () => {
                        const t = watcherEditor.trigger!;
                        if (
                          !(await confirmDialog({
                            title: "Delete this watcher?",
                            body: `"${t.nl_description}"`,
                            confirmLabel: "Delete",
                          }))
                        )
                          return;
                        await deleteTrigger(t.id);
                        await refreshTriggers();
                        setWatcherEditor(null);
                      }
                    : undefined
                }
              />
            ) : (
              <WatchingPanel
                path={path}
                onNew={() => setWatcherEditor({ trigger: null })}
                onEdit={(t) => setWatcherEditor({ trigger: t })}
              />
            )}
          </div>
        );
      default:
        return null;
    }
  })();

  return (
    <main
      className={cn(
        "box-border flex h-full min-h-0 flex-col",
        isMobile ? "px-3 py-4" : "px-8 py-6",
      )}
    >
      {host?.el && headerActions && createPortal(headerActions, host.el)}
      {historyOpen &&
        versionChip &&
        crumbHost?.el &&
        createPortal(versionChip, crumbHost.el)}

      {agentsBarHost?.el &&
        createPortal(
          <ActiveAgentsBar
            agents={agents}
            sessions={activeSessions}
            error={agentsError}
            open={agentsOpen}
            onToggle={() => setAgentsOpen((v) => !v)}
            onCloseSession={handleCloseSession}
          />,
          agentsBarHost.el,
        )}

      {/* The margin-comments lane overlays the right 360px of the doc area
          while every centered block reserves that width via .rail-reserved
          (globals.css), so the editor keeps its full-width scroller with
          the scrollbar at the window edge and content never slides under
          the cards. */}
      <div
        ref={docRowRef}
        className={`@container relative flex min-h-0 min-w-0 flex-1 flex-col ${
          railActive ? "rail-reserved" : ""
        } ${anchoredPanelActive ? "comments-anchored" : ""}`}
      >
        <DocTitle
          path={path}
          onRename={viewingVersion ? undefined : handleRename}
        />

        <ShareDialog
          path={path}
          open={shareOpen}
          onClose={() => setShareOpen(false)}
        />

        <RunAgentPanel
          open={runAgentOpen}
          onClose={() => setRunAgentOpen(false)}
          wikiPath={path || null}
        />

        {error && (
          <div className="mb-3 rounded-(--radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
            {error}
          </div>
        )}

        {loading && <LoadingSpinner />}

        {!loading && !error && (
          <>
            {/* The live editor when showing the current version, or DiffView
              when viewing an old commit. The editor pins the viewport (its own
              internal scroll) but spans full width so the scrollbar sits flush
              at the far-right edge; the text is capped + centered. */}
            {viewingVersion && diffData ? (
              <div className="flex min-h-0 flex-1 justify-center">
                <div className="flex min-h-0 w-full max-w-[768px] min-w-0 flex-1 overflow-hidden">
                  <DiffView data={diffData!} />
                </div>
              </div>
            ) : (
              // Live editor: the full-width editor owns the scroll, so its
              // scrollbar sits flush at the far-right edge while the text stays
              // capped + centered by the editor theme. `--cm-gutter` mirrors the
              // negative margin (which cancels `<main>`'s padding) so the editor
              // text keeps the same side gutter as the DocTitle at every
              // breakpoint. The banners stay capped + centered above it, aligned
              // with the text and DocTitle (`empty:hidden` drops the gap when
              // none render).
              <div
                className={`flex min-h-0 flex-1 flex-col gap-3 ${
                  isMobile
                    ? "-mx-3 [--cm-gutter:0.75rem]"
                    : "-mx-8 [--cm-gutter:2rem]"
                }`}
              >
                <div
                  className={`rail-inset mx-auto flex w-full max-w-[768px] min-w-0 shrink-0 flex-col gap-3 empty:hidden ${
                    isMobile ? "px-3" : ""
                  }`}
                >
                  <UpdateHealthBanner
                    path={path}
                    onOpenPolicy={() => openPanel("updates")}
                  />
                  <Path2ReviewBanner path={path} canWrite={canWrite} />
                  <CoeditPresenceBar
                    participants={coedit.participants}
                    peers={coedit.peers}
                    typing={coedit.typing}
                    selfUserId={user?.id ?? null}
                  />
                  {(() => {
                    // Cards visible while the body is still "empty enough"
                    // to discard without losing user work: truly blank, or
                    // still verbatim equal to the template the user just
                    // applied (so they can keep swapping templates).
                    const isBlank = coedit.buffer.trim() === "";
                    const matchesApplied =
                      appliedTemplateBody !== null &&
                      coedit.buffer === appliedTemplateBody;
                    const showGallery =
                      (isBlank || matchesApplied) &&
                      templates !== null &&
                      templates.length > 0;
                    if (!showGallery) return null;
                    return (
                      <TemplateGallery
                        templates={templates!}
                        activeId={matchesApplied ? appliedTemplateId : null}
                        applyingId={applyingTemplateId}
                        blankActive={isBlank && appliedTemplateId === null}
                        onPick={(t) => void onPickTemplate(t)}
                        onBlank={() => void onPickBlank()}
                      />
                    );
                  })()}
                </div>
                {coedit.session ? (
                  <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                    <Coeditor
                      key={coedit.session.id}
                      ref={coeditorRef}
                      session={coedit.session}
                      peers={coedit.peers}
                      onSelectionChange={coedit.reportSelection}
                      onCaretCleared={coedit.reportCaretCleared}
                      getCaretSeq={coedit.getCaretSeq}
                      onServerFrame={coedit.onServerFrame}
                      reportDoc={coedit.reportDoc}
                      registerFlush={coedit.registerFlush}
                      registerSetDoc={coedit.registerSetDoc}
                      registerCatchUp={coedit.registerCatchUp}
                      readOnly={!canWrite}
                      commentHighlights={commentHighlights}
                      activeCommentIds={activeCommentIds}
                      sourceHighlights={sourceHighlights}
                      activeSourceIds={activeSourceIds}
                      onSourceCaret={setCaretSourceKeys}
                      onSelectionForComment={handleSelectionForComment}
                      placeholder="Start typing, or pick a template above…"
                    />
                  </div>
                ) : coedit.joinError ? (
                  // The join handshake itself failed and there is no read-only
                  // fallback to fall back to, so this has to be an actionable
                  // dead end, not a permanent "Connecting…".
                  <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 text-[13px] text-(--text-03)">
                    <span>
                      Couldn't connect to the live session: {coedit.joinError}
                    </span>
                    <Button size="sm" onClick={coedit.retryJoin}>
                      Retry
                    </Button>
                  </div>
                ) : (
                  // Joining the session. The editor mounts once we have its
                  // start version + doc.
                  <div className="flex min-h-0 flex-1 items-center justify-center text-[13px] text-(--text-03)">
                    Connecting…
                  </div>
                )}
              </div>
            )}
            {/* Desktop side panels dock at the app's right edge (full height,
              beside the header) by portaling into the shell's right-panel host,
              so they never eat into the reading column above. Mobile keeps the
              fixed sheet rendered below. */}
            {historyOpen &&
              !isMobile &&
              rightHost?.el &&
              createPortal(historyRail, rightHost.el)}
            {panelTab &&
              !isMobile &&
              rightHost?.el &&
              createPortal(
                <DocPanel tab={panelTab} onTabChange={setPanelTab}>
                  {panelBody}
                </DocPanel>,
                rightHost.el,
              )}
          </>
        )}
        {railActive && (
          <CommentMarginRail
            threads={commentThreads}
            draft={commentDraft}
            editorRef={coeditorRef}
            activeId={activeCommentId}
            onActivate={activateComment}
            onHoverThread={setHoveredCommentId}
            selfName={user?.name || user?.email || "You"}
            path={path}
            selfId={user?.id}
            isAdmin={!!user?.is_admin}
            busy={marginBusy}
            run={runMargin}
            onSubmitDraft={(body) => void submitMarginDraft(body)}
            onCancelDraft={() => setCommentDraft(null)}
          />
        )}
      </div>
      {historyOpen && isMobile && (
        // Mobile: render the version-mode rail as a fixed slide-in sheet
        // over the content rather than a side column that would squeeze
        // the body to nothing on a 375px screen.
        <>
          <div
            onClick={closeHistory}
            aria-hidden
            className="fixed inset-0 z-[60] bg-(--mask-03)"
          />
          <div className="fixed top-0 right-0 bottom-0 z-[70] flex w-[min(360px,100vw)] bg-(--background-tint-00) shadow-(--shadow-panel)">
            {historyRail}
          </div>
        </>
      )}
      {panelTab && isMobile && (
        <>
          <div
            onClick={closePanel}
            aria-hidden
            className="fixed inset-0 z-[60] bg-(--mask-03)"
          />
          <div className="fixed top-0 right-0 bottom-0 z-[70] flex w-[min(360px,100vw)] bg-(--background-tint-00) shadow-(--shadow-panel)">
            <DocPanel tab={panelTab} onTabChange={setPanelTab}>
              {panelBody}
            </DocPanel>
          </div>
        </>
      )}
      {selTool && (
        <div
          onMouseDown={(e) => e.preventDefault()}
          className="fixed z-[80] -translate-x-1/2 -translate-y-full rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-01) p-1 shadow-(--shadow-popover)"
          style={{
            left: selTool.x,
            top: selTool.y - 8,
          }}
        >
          <Button
            prominence="tertiary"
            size="sm"
            onClick={() => {
              setCommentDraft(selTool.draft);
              // Match the lane's 920px container query at click time:
              // anywhere the lane can't show, the draft routes to the panel.
              const rowWide = (docRowRef.current?.clientWidth ?? 0) >= 920;
              if (panelTab !== null || isMobile || !rowWide) openComments();
              setSelTool(null);
              window.getSelection()?.removeAllRanges();
            }}
          >
            💬 Comment
          </Button>
        </div>
      )}
    </main>
  );
}

interface ActiveAgentsBarProps {
  agents: DocumentActivity[];
  sessions: AgentSessionSummary[];
  error: string | null;
  open: boolean;
  onToggle: () => void;
  onCloseSession: (id: string) => void;
}

function ActiveAgentsBar({
  agents,
  sessions,
  error,
  open,
  onToggle,
  onCloseSession,
}: ActiveAgentsBarProps) {
  const count = agents.length + sessions.length;
  const expandable = count > 0;
  return (
    <div className="mb-3 overflow-hidden rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-01)">
      <button
        onClick={expandable ? onToggle : undefined}
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        className={cn(
          "flex w-full items-center gap-2 border-none bg-transparent px-3 py-2 text-left text-[13px]",
          expandable
            ? "cursor-pointer text-(--text-05)"
            : "cursor-default text-(--text-03)",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "flex shrink-0 transition-transform duration-[120ms] ease-in-out",
            open ? "rotate-90" : "rotate-0",
            !expandable ? "text-(--text-02)" : "text-(--text-03)",
          )}
        >
          <SvgChevronRight size={10} />
        </span>
        <span className="font-medium">
          {expandable ? "Active agents" : "No agents active"}
        </span>
        {expandable && (
          <span className="rounded-full bg-(--background-tint-03) px-[6px] py-[1px] text-[11px] font-semibold text-(--text-05)">
            {count}
          </span>
        )}
        {error && (
          <span className="ml-auto text-xs text-(--status-text-error-05)">
            {error}
          </span>
        )}
      </button>
      {expandable && open && (
        <ul className="m-0 list-none border-t border-(--border-01) bg-(--background-tint-00) p-0">
          {sessions.map((s, i) => (
            <ActiveSessionRow
              key={s.id}
              s={s}
              isLast={agents.length === 0 && i === sessions.length - 1}
              onClose={() => onCloseSession(s.id)}
            />
          ))}
          {agents.map((a, i) => (
            <ActiveAgentRow
              key={`${a.owner_display}-${a.agent_name ?? ""}-${
                a.activity
              }-${i}`}
              a={a}
              isLast={i === agents.length - 1}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

interface ActiveAgentRowProps {
  a: DocumentActivity;
  isLast: boolean;
}

function ActiveAgentRow({ a, isLast }: ActiveAgentRowProps) {
  return (
    <li
      className={cn(
        "flex items-center gap-[10px] overflow-hidden px-3 py-[10px] text-[13px] whitespace-nowrap",
        !isLast && "border-b border-(--border-01)",
      )}
    >
      <span className="shrink-0 rounded-(--radius-04) bg-(--background-tint-03) px-[6px] py-[1px] text-[10px] font-semibold tracking-[0.3px] text-(--text-05) uppercase">
        {a.activity}
      </span>

      <span className="shrink-0 font-medium text-(--text-05)">
        {a.owner_display}
      </span>
      {a.agent_name ? (
        <span className="shrink-0 text-(--text-03)">
          {"·"} {a.agent_name}
        </span>
      ) : null}

      {a.description ? (
        <span
          className="min-w-0 grow overflow-hidden text-ellipsis text-(--text-04) italic"
          title={a.description}
        >
          {"“"}
          {a.description}
          {"”"}
        </span>
      ) : (
        <span className="flex-1" />
      )}

      <span
        className="shrink-0 text-[11px] text-(--text-02)"
        title={`Started ${absoluteTime(
          a.registered_at,
        )} · Expires ${absoluteTime(a.expires_at)}`}
      >
        {relativeTime(a.registered_at, "short")} {"·"} expires{" "}
        {relativeTime(a.expires_at, "short")}
      </span>
    </li>
  );
}

interface ActiveSessionRowProps {
  s: AgentSessionSummary;
  isLast: boolean;
  onClose: () => void;
}

function ActiveSessionRow({ s, isLast, onClose }: ActiveSessionRowProps) {
  return (
    <li
      className={cn(
        "flex items-center gap-[10px] overflow-hidden px-3 py-[10px] text-[13px] whitespace-nowrap",
        !isLast && "border-b border-(--border-01)",
      )}
    >
      <span className="shrink-0 rounded-(--radius-04) bg-(--background-tint-03) px-[6px] py-[1px] text-[10px] font-semibold tracking-[0.3px] text-(--text-05) uppercase">
        {s.status}
      </span>

      <span className="shrink-0 font-medium text-(--text-05)">{s.tool_id}</span>

      {s.tool_id === "onyx-craft" && s.status === "failed" ? (
        <span
          className="min-w-0 grow overflow-hidden text-ellipsis text-(--status-text-error-05)"
          title={craftFailureMessage(s.failure_reason)}
        >
          {craftFailureMessage(s.failure_reason)}
        </span>
      ) : (
        <>
          <span className="flex-1" />
          <span
            className="shrink-0 text-[11px] text-(--text-02)"
            title={`Started ${absoluteTime(s.started_at)}`}
          >
            started {relativeTime(s.started_at, "short")}
          </span>
        </>
      )}

      {s.tool_id === "onyx-craft" && s.status === "ready" && s.external_url && (
        <Button
          type="button"
          variant="default"
          size="sm"
          icon={SvgExternalLink}
          onClick={() =>
            window.open(
              s.external_url as string,
              "_blank",
              "noopener,noreferrer",
            )
          }
        >
          Open Craft
        </Button>
      )}

      <Button type="button" variant="default" size="sm" onClick={onClose}>
        Close
      </Button>
    </li>
  );
}

interface TemplateGalleryProps {
  templates: DocumentTemplateSummary[];
  activeId: string | null;
  applyingId: string | null;
  blankActive: boolean;
  onPick: (t: DocumentTemplateSummary) => void;
  onBlank: () => void;
}

export function TemplateGallery({
  templates,
  activeId,
  applyingId,
  blankActive,
  onPick,
  onBlank,
}: TemplateGalleryProps) {
  // Always a single-row strip — the picker never wraps to a second
  // line. On wide screens the user scrolls / clicks chevrons through
  // the row; on narrow screens the same layout becomes a swipe strip.
  return (
    <div className="flex flex-col gap-[10px] rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-01) p-[14px]">
      <div className="flex items-baseline gap-2">
        <span className="text-[13px] font-semibold text-(--text-05)">
          Start from a template
        </span>
        <span className="text-xs text-(--text-03)">
          Scroll or use the arrows to browse — tap to apply.
        </span>
      </div>
      <TemplateStrip
        templates={templates}
        activeId={activeId}
        applyingId={applyingId}
        blankActive={blankActive}
        onPick={onPick}
        onBlank={onBlank}
      />
    </div>
  );
}

interface TemplateStripProps {
  templates: DocumentTemplateSummary[];
  activeId: string | null;
  applyingId: string | null;
  blankActive: boolean;
  onPick: (t: DocumentTemplateSummary) => void;
  onBlank: () => void;
}

function TemplateStrip({
  templates,
  activeId,
  applyingId,
  blankActive,
  onPick,
  onBlank,
}: TemplateStripProps) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const [edges, setEdges] = useState<{ left: boolean; right: boolean }>({
    left: false,
    right: false,
  });

  const recomputeEdges = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const atStart = el.scrollLeft <= 1;
    const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1;
    setEdges({ left: !atStart, right: !atEnd });
  }, []);

  useEffect(() => {
    recomputeEdges();
    const el = scrollerRef.current;
    if (!el) return;
    el.addEventListener("scroll", recomputeEdges, { passive: true });
    window.addEventListener("resize", recomputeEdges);
    return () => {
      el.removeEventListener("scroll", recomputeEdges);
      window.removeEventListener("resize", recomputeEdges);
    };
  }, [recomputeEdges, templates.length]);

  const scrollBy = useCallback((dx: number) => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollBy({ left: dx, behavior: "smooth" });
  }, []);

  // One full card width per click feels right — the user advances by
  // a card rather than by a viewport, so they never lose their place.
  const CARD_WIDTH = 200;
  const STEP = CARD_WIDTH + 8; // card width + gap

  // The "Blank" template is the empty-start entry point — route the blank card
  // through it (so its auto-update policy applies) when present, and drop it
  // from the list so it isn't shown twice. Falls back to a plain blank page
  // when no Blank template is seeded.
  const blankTemplate = templates.find((t) => t.name === "Blank");
  const rest = templates.filter((t) => t.name !== "Blank");
  const blankCardActive =
    blankActive || (blankTemplate != null && activeId === blankTemplate.id);

  return (
    <div className="relative">
      <div
        ref={scrollerRef}
        className="scroll-x-hidden flex snap-x snap-mandatory gap-2 overflow-x-auto pb-[2px]"
        style={{ WebkitOverflowScrolling: "touch" }}
      >
        <div className="w-[200px] shrink-0 snap-start">
          <TemplateCard
            title="Blank document"
            description="Empty file — just start typing."
            active={blankCardActive}
            busy={false}
            onClick={() => (blankTemplate ? onPick(blankTemplate) : onBlank())}
          />
        </div>
        {rest.map((t) => (
          <div key={t.id} className="w-[200px] shrink-0 snap-start">
            <TemplateCard
              title={t.name}
              description={t.description}
              note={
                t.ingestion_auto_update_disabled ? "Auto-update off" : undefined
              }
              active={activeId === t.id}
              busy={applyingId === t.id}
              onClick={() => onPick(t)}
            />
          </div>
        ))}
      </div>
      {edges.left && (
        <StripArrow direction="left" onClick={() => scrollBy(-STEP)} />
      )}
      {edges.right && (
        <StripArrow direction="right" onClick={() => scrollBy(STEP)} />
      )}
    </div>
  );
}

interface StripArrowProps {
  direction: "left" | "right";
  onClick: () => void;
}

function StripArrow({ direction, onClick }: StripArrowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={direction === "left" ? "Scroll left" : "Scroll right"}
      className={cn(
        "absolute top-1/2 -translate-y-1/2",
        direction === "left" ? "left-1" : "right-1",
        "flex h-7 w-7 cursor-pointer items-center justify-center rounded-full border border-(--border-01) bg-(--background-tint-00) p-0 text-(--text-04) shadow-(--shadow-sm)",
      )}
    >
      {direction === "left" ? (
        <SvgChevronLeft size={14} />
      ) : (
        <SvgChevronRight size={14} />
      )}
    </button>
  );
}

interface TemplateCardProps {
  title: string;
  description: string | null;
  note?: string;
  active: boolean;
  busy: boolean;
  onClick: () => void;
}

function TemplateCard({
  title,
  description,
  note,
  active,
  busy,
  onClick,
}: TemplateCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={cn(
        "box-border flex h-full min-h-[64px] w-full flex-col gap-1 rounded-(--radius-04) border px-3 py-[10px] text-left text-(--text-05) transition-[background,border-color] duration-[80ms] ease-in-out",
        busy ? "cursor-wait opacity-[0.7]" : "cursor-pointer",
        "border-(--border-01)",
        active ? "bg-(--background-tint-03)" : "bg-(--background-tint-00)",
      )}
      onMouseEnter={(e) => {
        if (!active && !busy) {
          e.currentTarget.style.background = "var(--background-tint-03)";
          e.currentTarget.style.borderColor = "var(--border-02)";
        }
      }}
      onMouseLeave={(e) => {
        if (!active && !busy) {
          e.currentTarget.style.background = "";
          e.currentTarget.style.borderColor = "";
        }
      }}
    >
      <div className="text-[13px] font-semibold">{title}</div>
      {description && (
        <div className="line-clamp-2 text-xs text-(--text-03)">
          {description}
        </div>
      )}
      {note && <div className="text-[11px] text-(--text-02)">{note}</div>}
    </button>
  );
}

/** Every folder path in the tree (plus root ""), for the destination picker. */
export function collectFolders(entries: DocEntry[]): string[] {
  const set = new Set<string>([""]);
  for (const e of entries) {
    const parts = e.path.split("/");
    parts.pop();
    let cur = "";
    for (const p of parts) {
      cur = cur ? `${cur}/${p}` : p;
      set.add(cur);
    }
  }
  return [...set].sort((a, b) => a.localeCompare(b));
}

interface DestinationSelectProps {
  value: string;
  folders: string[];
  onChange: (v: string) => void;
  disabled: boolean;
}

/** Folder picker for the new-doc destination. "" is the wiki root ("Home"). */
export function DestinationSelect({
  value,
  folders,
  onChange,
  disabled,
}: DestinationSelectProps) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <span className="inline-flex max-w-full">
          <OpenButton
            variant="select-light"
            size="md"
            rounding="sm"
            disabled={disabled}
          >
            {value === "" ? "Home" : value}
          </OpenButton>
        </span>
      </Popover.Trigger>
      <Popover.Content width="fit" align="start" sideOffset={4}>
        <div className="max-h-[320px] max-w-[360px] min-w-[200px] overflow-y-auto">
          <PopoverMenu>
            {folders.map((f) => (
              <LineItemButton
                key={f || "__root__"}
                icon={SvgFolder}
                title={f === "" ? "Home" : f}
                sizePreset="main-ui"
                variant="body"
                state={value === f ? "selected" : "empty"}
                onClick={() => {
                  onChange(f);
                  setOpen(false);
                }}
              />
            ))}
          </PopoverMenu>
        </div>
      </Popover.Content>
    </Popover>
  );
}

interface FilenameRowProps {
  parent: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  autoFocus?: boolean;
  placeholder?: string;
}

export function FilenameRow({
  parent,
  value,
  onChange,
  disabled,
  autoFocus = false,
  placeholder = "filename",
}: FilenameRowProps) {
  return (
    <div className="flex shrink-0 items-stretch overflow-hidden rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00)">
      {parent && (
        <span className="flex items-center border-r border-(--border-01) bg-(--background-tint-02) px-[10px] font-mono text-[13px] text-(--text-04)">
          {parent}/
        </span>
      )}
      <input
        // eslint-disable-next-line jsx-a11y/no-autofocus
        autoFocus={autoFocus}
        value={value.replace(/\.md$/i, "")}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        spellCheck={false}
        className="flex-1 border-none bg-transparent px-[10px] py-2 font-mono text-sm outline-none"
      />
      <span
        aria-hidden
        className="flex items-center border-l border-(--border-01) bg-(--background-tint-02) px-[10px] font-mono text-[13px] font-semibold text-(--text-04)"
      >
        .md
      </span>
    </div>
  );
}

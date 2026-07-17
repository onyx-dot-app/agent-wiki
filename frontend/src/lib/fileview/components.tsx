"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
import {
  SvgChevronLeft,
  SvgChevronRight,
  SvgEdit,
  SvgExternalLink,
  SvgDocFile,
  SvgFolder,
  SvgHistory,
  SvgShare,
  SvgSidebar,
  SvgSparkle,
  SvgWorkflow,
} from "@onyx-ai/opal/icons";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { TriggerPanel } from "@/components/triggers/TriggerPanel";
import { TriggersSidePanel } from "@/components/wiki/TriggersSidePanel";
import { DiffView } from "@/components/wiki/DiffView";
import { DocPresence } from "@/components/wiki/DocPresence";
import { HistoryPanel } from "@/components/wiki/HistoryPanel";
import { RunAgentPanel } from "@/components/wiki/RunAgentPanel";
import { ShareDialog } from "@/components/wiki/ShareDialog";
import { CommentsPanel } from "@/components/wiki/CommentsPanel";
import { DocSidePanel, type DocPanelTab } from "@/components/wiki/DocSidePanel";
import { UpdateHealthBanner } from "@/components/wiki/UpdateHealthBanner";
import { UpdatePolicyPanel } from "@/components/wiki/UpdatePolicyPanel";
import { useLeftPanel } from "@/providers/LeftPanelProvider";
import { deleteTrigger, useTriggers, type Trigger } from "@/lib/triggers";
import { craftFailureMessage } from "@/lib/craft";
import {
  closeSession,
  useAgentSessions,
  type AgentSessionSummary,
} from "@/lib/launchers";
import { apiFetch } from "@/lib/api";
import { wikiHref, resolveIds, revalidateWiki } from "@/lib/wikiHref";
import { listComments } from "@/lib/comments";
import {
  paintCommentHighlights,
  scrollCommentIntoView,
  selectionToAnchor,
  type CommentDraft,
} from "@/lib/fileview/commentAnchor";
import { remarkBareSpaceLinks } from "@/lib/remarkBareSpaceLinks";
import { rehypeSourcePos } from "@/lib/fileview/rehypeSourcePos";
import { pageTitle } from "@/lib/fileview/utils";
import { useAuth } from "@/lib/auth";
import { Coeditor } from "@/lib/coeditor/components";
import type { CoeditParticipant } from "@/lib/coeditor/types";
import { useCoeditSession } from "@/lib/coeditor/hooks";
import {
  useAgentsBarHost,
  useHeaderActionsHost,
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
import { absoluteTime, relativeTime } from "@/lib/time";
import { useIsMobile } from "@/lib/viewport";
import {
  type CommitInfo,
  fetchFileDiff,
  fetchFileHistory,
  type FileDiffResponse,
} from "@/lib/wiki";
import type {
  CommentThreadView,
  DocumentActivity,
  DocumentActivityResponse,
} from "@/types";

// Local shape for the /wiki/file API response — mirrored from page.tsx.
interface FileResponse {
  path: string;
  body: string;
  ref?: string;
  head_sha?: string | null;
}

// Minimal doc-entry shape needed by collectFolders / DestinationSelect.
interface DocEntry {
  path: string;
  updated_at: string;
}

interface DocTitleProps {
  path: string;
}

/** Renders the page title and a divider below it. */
export function DocTitle({ path }: DocTitleProps) {
  return (
    <div className="flex flex-col gap-6 px-4 pb-6">
      <Content
        icon={SvgDocFile}
        sizePreset="headline"
        variant="heading"
        title={pageTitle(path)}
      />
      <Divider paddingParallel="fit" paddingPerpendicular="fit" />
    </div>
  );
}

interface MarkdownRendererProps {
  body: string;
}

/** Memoized markdown renderer for wiki file content.
 *
 * Encapsulates the full plugin stack: GFM tables/task lists, space-link
 * re-encoding, and source-position annotation for comment anchoring.
 * `React.memo` means the article only re-renders when `body` changes, which
 * preserves CSS Highlight API ranges across unrelated re-renders.
 */
export const MarkdownRenderer = memo(function MarkdownRenderer({
  body,
}: MarkdownRendererProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBareSpaceLinks]}
      rehypePlugins={[rehypeSourcePos]}
    >
      {body}
    </ReactMarkdown>
  );
});

interface FileViewProps {
  path: string;
}

export function FileView({ path }: FileViewProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isMobile = useIsMobile();
  const host = useHeaderActionsHost();
  const agentsBarHost = useAgentsBarHost();
  const rightHost = useRightPanelHost();
  const { isActivitiesOpen, toggleActivities } = useLeftPanel();
  const { refresh: refreshTriggers } = useTriggers();
  const { setDrafting, requestExpand } = useDrafting();
  const { user } = useAuth();
  const [body, setBody] = useState("");
  const [editing, setEditing] = useState(false);
  const [filenameDraft, setFilenameDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggerModalOpen, setTriggerModalOpen] = useState(false);
  const [triggerStatus, setTriggerStatus] = useState<string | null>(null);
  const [automationsOpen, setAutomationsOpen] = useState(false);
  const [editingTrigger, setEditingTrigger] = useState<Trigger | null>(null);
  const [runAgentOpen, setRunAgentOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const confirmDialog = useConfirm();
  // History state. `viewingSha` is null when looking at the working-tree
  // (latest) version; otherwise it's the sha being viewed and is what we
  // pass back as `base_sha` on save so the server records a rollback.
  const [headSha, setHeadSha] = useState<string | null>(null);
  const [viewingSha, setViewingSha] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [commits, setCommits] = useState<CommitInfo[] | null>(null);
  const [diffData, setDiffData] = useState<FileDiffResponse | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  // One tabbed side panel hosts the per-doc surfaces (null = closed).
  const [panelTab, setPanelTab] = useState<DocPanelTab | null>(null);
  // Comments (render-mode). `commentDraft` is a pending text selection being
  // composed; `selTool` is the floating "Comment" affordance shown on select.
  const [commentDraft, setCommentDraft] = useState<CommentDraft | null>(null);
  const [commentThreads, setCommentThreads] = useState<CommentThreadView[]>([]);
  const [activeCommentId, setActiveCommentId] = useState<string | null>(null);
  const [selTool, setSelTool] = useState<{
    x: number;
    y: number;
    draft: CommentDraft;
  } | null>(null);
  const articleRef = useRef<HTMLElement | null>(null);
  // Page owns the comment threads (so highlights render even with the panel
  // closed). Auto-open the Comments tab once per path when a page has
  // unresolved comments.
  const autoOpenedPathRef = useRef<string | null>(null);
  // A `?comment=<id>` deep-link is focused once per id (reset on path change).
  const focusedCommentRef = useRef<string | null>(null);

  // The side panel is mutually exclusive with the triggers surfaces. Every
  // path that opens one closes the other (every opener routes through
  // here). The version-history list lives inside the Updates tab and
  // collapses with the panel, so opening the panel leaves `historyOpen`
  // alone.
  const openPanel = useCallback((tab: DocPanelTab) => {
    setAutomationsOpen(false);
    setTriggerModalOpen(false);
    setEditingTrigger(null);
    setPanelTab(tab);
    // A pending selection draft only makes sense on the Comments tab.
    if (tab !== "comments") setCommentDraft(null);
  }, []);
  // Closing the panel also collapses the history list. An active diff
  // stays in the doc area (closing chrome never switches the document).
  const closePanel = useCallback(() => {
    setPanelTab(null);
    setCommentDraft(null);
    setHistoryOpen(false);
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

  // MarkdownRenderer is React.memo'd on body — CSS Highlight API ranges painted
  // over its text nodes stay valid across unrelated re-renders.
  const renderedBody = <MarkdownRenderer body={body} />;

  // Paint Google-Docs-style highlights over the rendered article for each
  // (non-orphaned) anchored comment. Cleared in edit/diff mode (no article).
  //
  // On a fresh load the comments and the doc body arrive on separate renders,
  // and react-markdown commits its text nodes a tick later than this effect
  // runs — so a single synchronous (or even rAF-deferred) paint finds no text
  // to range over and silently no-ops, which is why nothing was highlighted
  // until a click happened to re-run the paint against a settled DOM. We make
  // it robust by (a) painting now, (b) painting on the next frame, and (c)
  // watching the article subtree with a MutationObserver and repainting whenever
  // react-markdown finally swaps in / replaces the rendered nodes. The CSS
  // Custom Highlight API doesn't mutate the DOM, so this never loops.
  useEffect(() => {
    const el = articleRef.current;
    if (!el) return;
    const clear = editing || viewingSha;
    const targets = clear
      ? []
      : commentThreads
          .map((t) => t.root)
          .filter(
            // Resolved threads disappear from the panel, so drop their doc
            // highlight too; orphaned ones have no live span to paint.
            (r) =>
              r.status !== "orphaned" &&
              r.status !== "resolved" &&
              r.start_offset !== null &&
              r.end_offset !== null &&
              r.quoted_text !== null,
          )
          .map((r) => ({
            startOffset: r.start_offset as number,
            endOffset: r.end_offset as number,
            quotedText: r.quoted_text as string,
            active: r.id === activeCommentId,
          }));

    let cancelled = false;
    let raf = 0;
    let attempts = 0;
    const MAX_ATTEMPTS = 60; // ~1s at 60fps, then give up (some spans may be unmappable)
    const tick = () => {
      if (cancelled) return;
      const painted = paintCommentHighlights(el, body, targets);
      attempts += 1;
      // The markdown text nodes commit a tick after React renders, so the first
      // paint can find nothing to range over. Retry per-frame until every target
      // lands (or we hit the cap) — this is what makes highlights appear on a
      // fresh load instead of only after a click re-ran the paint.
      if (painted < targets.length && attempts < MAX_ATTEMPTS) {
        raf = requestAnimationFrame(tick);
      }
    };
    tick();

    // Repaint when react-markdown later swaps nodes (e.g. an edit/remap rerenders
    // the body). Reset the retry budget so the fresh DOM gets a full set of tries.
    const observer = new MutationObserver(() => {
      attempts = 0;
      cancelAnimationFrame(raf);
      tick();
    });
    observer.observe(el, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
    // `loading` gates whether <article> is mounted: when the body is primed by
    // the SWR cache before `loading` flips, `body` doesn't change on mount, so
    // without this dep the effect wouldn't re-run and the article would mount
    // with no paint (highlights only appeared after a click). Re-run on mount.
  }, [commentThreads, body, editing, viewingSha, activeCommentId, loading]);

  // Select a thread (its span gets the orange highlight) and scroll the doc to
  // bring that span into view. Only an explicit click runs this — keying a
  // scroll off `activeCommentId` alone would also re-scroll on every comment
  // refetch while a thread stays selected. Deferred a frame so the layout (and
  // the active repaint) settles before we measure the range.
  const activateComment = useCallback(
    (id: string | null) => {
      setActiveCommentId(id);
      if (!id || editing || viewingSha) return;
      const el = articleRef.current;
      if (!el) return;
      const root = commentThreads.find((t) => t.root.id === id)?.root;
      if (
        !root ||
        root.status === "orphaned" ||
        root.status === "resolved" ||
        root.start_offset === null ||
        root.end_offset === null
      )
        return;
      requestAnimationFrame(() => {
        scrollCommentIntoView(el, body, root.start_offset!, root.end_offset!);
      });
    },
    [commentThreads, body, editing, viewingSha],
  );

  // Deep-link: `?comment=<id>` opens the panel, selects that thread, and scrolls
  // to its anchored span — the shareable-link counterpart to click-to-focus.
  // Runs once per id (focusedCommentRef), and only once the thread is loaded.
  // The scroll retries per-frame because on a fresh load react-markdown commits
  // its text nodes a tick after this effect runs (same reason the highlight
  // paint retries).
  useEffect(() => {
    const target = searchParams?.get("comment");
    if (!target || loading || editing || viewingSha) return;
    if (focusedCommentRef.current === target) return;
    const root = commentThreads.find((t) => t.root.id === target)?.root;
    if (!root) return; // not loaded yet, or not a thread on this page
    focusedCommentRef.current = target;
    setActiveCommentId(target);
    openComments();
    if (
      root.status === "orphaned" ||
      root.status === "resolved" ||
      root.start_offset === null ||
      root.end_offset === null
    )
      return; // selected, but no live span to scroll to
    const el = articleRef.current;
    if (!el) return;
    let raf = 0;
    let attempts = 0;
    const tick = () => {
      if (scrollCommentIntoView(el, body, root.start_offset!, root.end_offset!))
        return;
      if (++attempts < 60) raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [
    searchParams,
    commentThreads,
    loading,
    editing,
    viewingSha,
    body,
    openComments,
  ]);

  // On a text selection in the rendered article, offer a floating "Comment"
  // affordance anchored above the selection (render mode only).
  const onArticleMouseUp = useCallback(() => {
    const el = articleRef.current;
    const sel = window.getSelection();
    if (!el || !sel || sel.rangeCount === 0) {
      setSelTool(null);
      return;
    }
    const draft = selectionToAnchor(el, body);
    if (!draft) {
      setSelTool(null);
      return;
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    setSelTool({ x: rect.left + rect.width / 2, y: rect.top, draft });
  }, [body]);

  useEffect(() => {
    const onSel = () => {
      const s = window.getSelection();
      if (!s || s.isCollapsed) setSelTool(null);
    };
    document.addEventListener("selectionchange", onSel);
    return () => document.removeEventListener("selectionchange", onSel);
  }, []);

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
    setEditing(false);
    setViewingSha(null);
    setDiffData(null);
    apiFetch<FileResponse>(`/wiki/file?path=${encodeURIComponent(path)}`)
      .then((r) => {
        setBody(r.body);
        setHeadSha(r.head_sha ?? null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load"))
      .finally(() => setLoading(false));
  }, [path]);

  useEffect(() => {
    loadLatest();
    setHistoryOpen(false);
    setCommits(null);
  }, [loadLatest]);

  // SWR revalidates the doc so MCP-driven edit_doc writes appear
  // without manual refresh. Skip when the user is editing the
  // textarea or viewing an old commit — both would get clobbered.
  const liveKey =
    !editing && viewingSha === null
      ? `/wiki/file?path=${encodeURIComponent(path)}`
      : null;
  const { data: liveDoc } = useSWR<FileResponse>(liveKey, {
    refreshInterval: 1500,
    revalidateOnFocus: true,
    dedupingInterval: 0,
  });

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
  useEffect(() => {
    if (!liveDoc) return;
    // Re-check the gate at apply time — an in-flight fetch from before
    // the user toggled `editing` can still resolve here and would
    // otherwise clobber the live session buffer. While editing, the
    // co-edit SSE stream is the source of truth, not this poll.
    if (editing || viewingSha !== null) return;
    // The read is session-aware: while a co-edit session is open, /wiki/file
    // serves the live buffer (a quick HEAD+buffer merge), so this poll reflects
    // saved edits immediately — no dependency on the async checkpoint commit.
    setBody((prev) => (prev === liveDoc.body ? prev : liveDoc.body));
    setHeadSha(liveDoc.head_sha ?? null);
  }, [liveDoc, editing, viewingSha]);

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

  function closeHistory() {
    setHistoryOpen(false);
    // Collapsing the history list exits history mode entirely, back to the
    // rendered latest version. Skip while editing: `viewingSha` is the fork
    // base the save needs, and the textarea is showing the user's draft anyway.
    if (!editing && viewingSha !== null) {
      setViewingSha(null);
      setDiffData(null);
    }
  }

  async function toggleHistory() {
    // Close only when the list is actually visible. With history open but
    // another tab focused, the toggle refocuses Updates instead of
    // silently discarding the diff.
    if (historyOpen && panelTab === "updates") {
      closeHistory();
      return;
    }
    setHistoryOpen(true);
    // The list renders inside the Updates tab, so opening history opens
    // the panel there (which also closes the trigger surfaces).
    openPanel("updates");
    // Opening history: show the newest commit's diff immediately rather
    // than leaving the rendered body up until the user clicks a row.
    const loaded = commits ?? (await refreshHistory())?.commits ?? null;
    const newest = loaded?.[0];
    if (newest && viewingSha === null) {
      void onPickCommit(newest.sha);
    }
  }

  async function onPickCommit(sha: string) {
    if (sha === viewingSha) return;
    setLoading(true);
    setError(null);
    setEditing(false);
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

  // Live co-editing session: clicking Edit joins the page's session and binds
  // the textarea to the shared buffer; Save checkpoints, Cancel discards, a
  // disconnect leaves. Only the current version is co-edited (the Edit button is
  // hidden while viewing an old commit).
  const coedit = useCoeditSession({
    path,
    enabled: editing,
    committedBody: body,
    myUserId: user?.id ?? null,
    onEnd: () => {
      setEditing(false);
      setViewingSha(null);
      // Don't refetch the body here. onSave sets it optimistically; the SWR
      // poll then reconciles against the session-aware read (which serves the
      // live buffer while the session is open), so there's no stale flash and
      // no wait on the async checkpoint commit.
      void refreshComments();
      void refreshDraftState();
    },
  });

  const segments = path.split("/");
  const parentSlug = segments.slice(0, -1).join("/");
  const currentBasename = segments[segments.length - 1] ?? path;
  const currentBasenameNoExt = currentBasename.replace(/\.md$/i, "");
  const trimmedFilename = filenameDraft.trim().replace(/^\/+|\/+$/g, "");
  const filenameNoExt = trimmedFilename.replace(/\.md$/i, "");
  const filenameValid = !!filenameNoExt && !filenameNoExt.includes("/");
  const renamed =
    editing && filenameValid && filenameNoExt !== currentBasenameNoExt;
  // `viewingVersion`: a history version is displayed in the main pane.
  // `viewingOld`: that version is not the newest commit for this file
  // (`headSha` tracks `commits[0]`), so the warning banner applies.
  const viewingVersion = viewingSha !== null;
  const viewingOld = viewingVersion && viewingSha !== headSha;
  // No navigate-away guard: editing writes to the shared session buffer, which
  // is durable in Postgres and commits at session close — leaving the page
  // never loses edits, so there's nothing to warn about.

  function startEdit() {
    // Entering edit mode joins the page's co-edit session (see the
    // useCoeditSession hook, gated on `editing`), which seeds the buffer from
    // current HEAD. The Edit button is hidden while viewing an old commit, so
    // editing always targets the current version.
    setFilenameDraft(currentBasenameNoExt);
    setError(null);
    setEditing(true);
  }

  async function onDone() {
    if (!filenameValid) {
      setError("Filename cannot be empty or contain '/'.");
      return;
    }
    setSaving(true);
    setError(null);
    // Snapshot what we're committing so the viewer shows it immediately; the
    // checkpoint commit runs on a worker, but the session-aware read serves the
    // live buffer meanwhile, so the SWR poll keeps showing this content.
    const finalText = coedit.buffer;
    try {
      // Commit the session buffer to git (checkpoint), then leave. onEnd exits
      // edit mode + refreshes comments/drafting; co-editing removes the
      // human-vs-human conflict, so there's no 409 path here. On a rename-only
      // save the buffer is unchanged, so the checkpoint is a no-op server-side
      // (version == checkpointed_version) — no empty commit before the move.
      await coedit.save();
      setBody(finalText);
      setDiffData(null);
      if (historyOpen) refreshHistory();
      else setCommits(null);
      if (renamed) {
        const finalName = filenameNoExt + ".md";
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
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
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
  // header inside the scroll area — they portal into its right-aligned slot.
  // The panel toggles are icon SelectButtons so the open panel shows the
  // selected tint. The presence cluster leads, the rest are tertiary icon
  // Buttons with the lone solid CTA (Edit). Editing swaps in the single
  // labelled Done CTA.
  const headerActions = editing ? (
    // One exit: "Done" leaves the session (committing the shared buffer). There
    // is no Cancel — the buffer is the live shared document, so a single
    // participant can't discard everyone's in-progress edits.
    <Button variant="action" onClick={onDone} disabled={saving}>
      {saving ? "Finishing…" : "Done"}
    </Button>
  ) : !loading && !error ? (
    <>
      <DocPresence
        path={path}
        participants={coedit.participants}
        onOpenUpdates={() => openPanel("updates")}
      />
      <Button
        icon={SvgSparkle}
        prominence="tertiary"
        tooltip="Run Agent"
        onClick={() => setRunAgentOpen(true)}
      />
      <SelectButton
        icon={SvgWorkflow}
        state={automationsOpen ? "selected" : "empty"}
        tooltip="Triggers"
        onClick={() => {
          if (automationsOpen || triggerModalOpen) {
            setAutomationsOpen(false);
            setTriggerModalOpen(false);
            setEditingTrigger(null);
            return;
          }
          setHistoryOpen(false);
          setPanelTab(null);
          setCommentDraft(null);
          setAutomationsOpen(true);
        }}
      />
      <Button
        icon={SvgShare}
        prominence="tertiary"
        tooltip="Share"
        onClick={() => setShareOpen(true)}
      />
      <SelectButton
        icon={SvgHistory}
        state={historyOpen ? "selected" : "empty"}
        tooltip="History"
        onClick={toggleHistory}
      />
      {/* Comments and update policy live in the side panel's tabs. The
          toggle opens the panel on Updates and closes it wholesale. */}
      <SelectButton
        icon={SvgSidebar}
        state={panelTab ? "selected" : "empty"}
        tooltip="Side panel"
        onClick={() => (panelTab ? closePanel() : openPanel("updates"))}
      />
      {/* Editing always targets current HEAD via a co-edit session, so hide
          Edit while viewing an older commit — "Back to latest" first. */}
      {!viewingOld && (
        <Button
          icon={SvgEdit}
          variant="action"
          tooltip="Edit"
          onClick={startEdit}
        />
      )}
    </>
  ) : null;

  // Shared between the desktop right-rail portal and the mobile sheet.
  const sidePanel = panelTab && (
    <DocSidePanel
      tab={panelTab}
      onTabChange={openPanel}
      updates={
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <UpdatePolicyPanel path={path} onShowHistory={toggleHistory} />
          {historyOpen && (
            // Version list expands under the policy cards (mock 1855:273363).
            // The fixed-height clipped wrapper hosts HistoryPanel's
            // full-height layout as an in-tab card.
            <div className="flex min-h-[480px] w-full flex-1 flex-col overflow-hidden rounded-(--radius-12) border border-(--border-01)">
              <HistoryPanel
                commits={commits}
                error={historyError}
                headSha={headSha}
                viewingSha={viewingSha}
                onPick={onPickCommit}
                onClose={closeHistory}
                fullHeight
              />
            </div>
          )}
        </div>
      }
      comments={
        <CommentsPanel
          path={path}
          headSha={headSha}
          draft={commentDraft}
          threads={commentThreads}
          onChanged={refreshComments}
          activeId={activeCommentId}
          onActivate={activateComment}
          onDraftConsumed={() => setCommentDraft(null)}
          fullHeight
        />
      }
    />
  );

  return (
    <main
      className={`box-border flex h-full min-h-0 flex-col ${isMobile ? "px-3 py-4" : "px-8 py-6"}`}
    >
      {host?.el && headerActions && createPortal(headerActions, host.el)}

      {agentsBarHost?.el &&
        !editing &&
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

      {!editing && <DocTitle path={path} />}

      {!editing && triggerStatus && (
        <div className="mb-3 text-xs text-(--text-04)">{triggerStatus}</div>
      )}

      <TriggerPanel
        open={triggerModalOpen && (isMobile || !rightHost?.el)}
        initial={editingTrigger ?? { scope_path: path }}
        lockScope={!editingTrigger}
        onDelete={
          editingTrigger
            ? async () => {
                if (
                  !(await confirmDialog({
                    title: "Delete this trigger?",
                    body: `"${editingTrigger.nl_description}"`,
                    confirmLabel: "Delete",
                  }))
                )
                  return;
                await deleteTrigger(editingTrigger.id);
                await refreshTriggers();
                setTriggerModalOpen(false);
                setEditingTrigger(null);
              }
            : undefined
        }
        onClose={() => {
          setTriggerModalOpen(false);
          setEditingTrigger(null);
        }}
        onSaved={(t) => {
          setTriggerStatus(
            editingTrigger
              ? `Updated trigger for ${t.scope_path}`
              : `Created trigger for ${t.scope_path}`,
          );
          void refreshTriggers();
        }}
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

      {viewingOld && !loading && !error && (
        <div className="mb-3 flex items-center gap-3 rounded-(--radius-08) border border-(--status-warning-02) bg-(--status-warning-01) px-3 py-2 text-[13px] text-(--status-text-warning-05)">
          <span>
            Viewing an older version
            {viewingSha ? ` (${viewingSha.slice(0, 7)})` : ""}.
          </span>
          <div className="flex-1" />
          <Button size="sm" onClick={loadLatest}>
            Back to latest
          </Button>
        </div>
      )}

      {loading && <LoadingSpinner />}

      {!loading && !error && (
        <>
          {editing || (viewingVersion && diffData) ? (
            // Editor / diff: fixed-height, capped + centered column with its own
            // internal scroll (you edit against a pinned viewport, not a growing
            // page).
            <div className="flex min-h-0 flex-1 justify-center">
              <div className="flex w-full max-w-[768px] min-w-0 flex-col gap-3">
                {editing ? (
                  <>
                    <FilenameRow
                      parent={parentSlug}
                      value={filenameDraft}
                      onChange={setFilenameDraft}
                      disabled={saving}
                    />
                    <CoeditPresenceBar
                      participants={coedit.participants}
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
                    {coedit.session ? (
                      <Coeditor
                        key={coedit.session.id}
                        session={coedit.session}
                        peers={coedit.peers}
                        onSelectionChange={coedit.reportSelection}
                        onServerFrame={coedit.onServerFrame}
                        reportDoc={coedit.reportDoc}
                        registerFlush={coedit.registerFlush}
                        registerSetDoc={coedit.registerSetDoc}
                        placeholder="Start typing, or pick a template above…"
                      />
                    ) : (
                      // Joining the session; the editor mounts once we have its
                      // start version + doc.
                      <div className="flex min-h-0 flex-1 items-center justify-center text-[13px] text-(--text-03)">
                        Connecting…
                      </div>
                    )}
                  </>
                ) : (
                  <div className="flex min-h-0 flex-1 overflow-hidden">
                    <DiffView
                      data={diffData!}
                      commit={
                        commits?.find((c) => c.sha === viewingSha) ?? undefined
                      }
                      loadBody={async () => {
                        const sha = viewingSha;
                        if (!sha) return "";
                        const r = await apiFetch<FileResponse>(
                          `/wiki/file?path=${encodeURIComponent(
                            path,
                          )}&ref=${encodeURIComponent(sha)}`,
                        );
                        return r.body;
                      }}
                    />
                  </div>
                )}
              </div>
            </div>
          ) : (
            // Render mode: the whole main-content area scrolls with the
            // scrollbar flush at the far right edge (the negative margin cancels
            // <main>'s horizontal padding, re-applied inside so the text keeps
            // its gutter); the article text stays capped + centered.
            <div
              className={`min-h-0 flex-1 overflow-y-auto ${isMobile ? "-mx-3 px-3" : "-mx-8 px-8"}`}
            >
              <div className="mx-auto w-full max-w-[768px]">
                <UpdateHealthBanner
                  path={path}
                  onOpenPolicy={() => openPanel("updates")}
                />
              </div>
              <article
                ref={articleRef}
                className="markdown mx-auto w-full max-w-[768px]"
                onMouseUp={onArticleMouseUp}
              >
                {renderedBody}
              </article>
            </div>
          )}
          {/* Desktop side panels dock at the app's right edge (full height,
              beside the header) by portaling into the shell's right-panel host,
              so they never eat into the reading column above. Mobile keeps the
              fixed sheet rendered below. */}
          {panelTab &&
            !isMobile &&
            rightHost?.el &&
            createPortal(
              <div className="flex h-full w-[360px] bg-(--background-tint-00)">
                {sidePanel}
              </div>,
              rightHost.el,
            )}
          {automationsOpen &&
            !isMobile &&
            rightHost?.el &&
            createPortal(
              <div className="flex h-full w-[480px] flex-col border-l border-(--border-01) bg-(--background-tint-01) p-2">
                <TriggersSidePanel path={path} onStatus={setTriggerStatus} />
              </div>,
              rightHost.el,
            )}
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
            {sidePanel}
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
              openComments();
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
        className={`flex w-full items-center gap-2 border-none bg-transparent px-3 py-2 text-left text-[13px] ${expandable ? "cursor-pointer text-(--text-05)" : "cursor-default text-(--text-03)"}`}
      >
        <span
          aria-hidden
          className={`flex shrink-0 transition-transform duration-[120ms] ease-in-out ${open ? "rotate-90" : "rotate-0"} ${!expandable ? "text-(--text-02)" : "text-(--text-03)"}`}
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
      className={
        `flex items-center gap-[10px] overflow-hidden px-3 py-[10px] text-[13px] whitespace-nowrap` +
        (isLast ? `` : ` border-b border-(--border-01)`)
      }
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
      className={
        `flex items-center gap-[10px] overflow-hidden px-3 py-[10px] text-[13px] whitespace-nowrap` +
        (isLast ? `` : ` border-b border-(--border-01)`)
      }
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
      className={`absolute top-1/2 -translate-y-1/2 ${direction === "left" ? "left-1" : "right-1"} flex h-7 w-7 cursor-pointer items-center justify-center rounded-full border border-(--border-01) bg-(--background-tint-00) p-0 text-(--text-04) shadow-(--shadow-sm)`}
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
      className={`box-border flex h-full min-h-[64px] w-full flex-col gap-1 rounded-(--radius-04) border px-3 py-[10px] text-left text-(--text-05) transition-[background,border-color] duration-[80ms] ease-in-out ${busy ? "cursor-wait opacity-[0.7]" : "cursor-pointer"} ${active ? "border-(--border-01) bg-(--background-tint-03)" : "border-(--border-01) bg-(--background-tint-00)"}`}
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

interface CoeditPresenceBarProps {
  participants: CoeditParticipant[];
  typing: string[];
  selfUserId: string | null;
}

// Co-editing presence while editing: who else is in the session and who's
// typing right now. Remote carets aren't drawn in a plain textarea (that waits
// for CodeMirror) — but edits already merge into the shared buffer live, so
// this makes the collaboration visible. Renders nothing when you're alone.
export function CoeditPresenceBar({
  participants,
  typing,
  selfUserId,
}: CoeditPresenceBarProps) {
  const others = participants.filter((p) => p.user_id !== selfUserId);
  if (others.length === 0) return null;
  const typingSet = new Set(typing);
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-(--text-03)">
      <span
        className="inline-block h-[7px] w-[7px] rounded-full bg-(--status-success-05)"
        aria-hidden
      />
      {others.map((p) => (
        <span key={p.user_id} className="inline-flex items-center gap-1">
          <span className="font-medium text-(--text-04)">{p.user_display}</span>
          {typingSet.has(p.user_id) && (
            <span className="text-(--text-03) italic">typing…</span>
          )}
        </span>
      ))}
      <span>also editing</span>
    </div>
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

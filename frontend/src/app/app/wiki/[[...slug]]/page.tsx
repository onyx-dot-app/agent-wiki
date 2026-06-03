"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { diffLines } from "diff";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { Button, SelectButton } from "@onyx-ai/opal/components";
import {
  SvgArrowLeft,
  SvgChevronLeft,
  SvgChevronRight,
  SvgDocFile,
  SvgEdit,
  SvgFolder,
  SvgFolderPlus,
  SvgPlus,
  SvgShare,
  SvgTrash,
  SvgWorkflow,
} from "@onyx-ai/opal/icons";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { PageHeader } from "@/components/common/PageHeader";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { DiffView } from "@/components/wiki/DiffView";
import { HistoryPanel } from "@/components/wiki/HistoryPanel";
import { RunAgentPanel } from "@/components/wiki/RunAgentPanel";
import {
  closeSession,
  useAgentSessions,
  type AgentSessionSummary,
} from "@/lib/launchers";
import { ShareDialog } from "@/components/wiki/ShareDialog";
import { CommentsPanel } from "@/components/wiki/CommentsPanel";
import { apiFetch, ApiError } from "@/lib/api";
import { listComments } from "@/lib/comments";
import {
  paintCommentHighlights,
  selectionToAnchor,
  type CommentDraft,
} from "@/lib/commentAnchor";
import { rehypeSourcePos } from "@/lib/rehypeSourcePos";
import { useRequireAuth } from "@/lib/auth";
import { useDrafting } from "@/lib/drafting";
import { rememberWikiPath } from "@/lib/lastViewed";
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

interface DocEntry {
  path: string;
  updated_at: string;
}

interface ListResponse {
  entries: DocEntry[];
}

interface FileResponse {
  path: string;
  body: string;
  ref?: string;
  head_sha?: string | null;
}

interface DraftResponse {
  path: string;
  base_sha: string;
  content: string;
  updated_at: string;
}

interface ConflictState {
  draftBody: string;
  currentBody: string;
  currentSha: string;
  baseSha: string;
}

export default function WikiRoute() {
  const { user, loading } = useRequireAuth();
  const isMobile = useIsMobile();
  const params = useParams<{ slug?: string[] }>();
  const searchParams = useSearchParams();
  const isNewMode = searchParams?.get("new") === "1";
  const rawSlugParts = (params?.slug ?? []) as string[];
  // Next.js may hand back percent-encoded segments (e.g. "local%20testing").
  // Decode so labels and downstream API paths use literal characters.
  const slugParts = rawSlugParts.map((s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  });
  const slugPath = slugParts.join("/");
  const isFile = slugPath.endsWith(".md");

  // Remember the most recent wiki path so the "Last viewed" landing
  // setting has something to fall back to.
  useEffect(() => {
    rememberWikiPath("/app/wiki" + (slugPath ? "/" + slugPath : ""));
  }, [slugPath]);

  if (loading || !user)
    return (
      <main className={isMobile ? "p-4" : "p-8"}>
        <LoadingSpinner center />
      </main>
    );

  return isFile ? (
    <FileViewer path={slugPath} />
  ) : isNewMode ? (
    <NewDocView dir={slugPath} />
  ) : (
    <Explorer dir={slugPath} />
  );
}

function Explorer({ dir }: { dir: string }) {
  const router = useRouter();
  const isMobile = useIsMobile();
  const {
    data,
    error: listError,
    mutate: mutatePaths,
  } = useSWR<ListResponse>("/wiki");
  const entries = data?.entries ?? [];
  const [mutationError, setMutationError] = useState<string | null>(null);
  const error =
    mutationError ?? (listError instanceof Error ? listError.message : null);
  const setError = setMutationError;
  // Force the cache to revalidate from the server. Used after writes
  // (create / delete / move) to pull in the new tree.
  const refresh = useCallback(() => {
    void mutatePaths();
  }, [mutatePaths]);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  // Folders still use an inline filename form; new docs route to
  // NewDocView where filename + template + body are chosen together.
  const [creating, setCreating] = useState<"folder" | null>(null);
  const [newName, setNewName] = useState("");
  const [createBusy, setCreateBusy] = useState(false);
  const [triggerModalOpen, setTriggerModalOpen] = useState(false);
  const [triggerStatus, setTriggerStatus] = useState<string | null>(null);
  const [sort, setSort] = useState<"name-asc" | "name-desc" | "recent">(
    "name-asc",
  );
  const [renaming, setRenaming] = useState<string | null>(null);
  const [dragSource, setDragSource] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [sharePath, setSharePath] = useState<string | null>(null);

  const { subdirs, files } = useMemo(() => {
    const prefix = dir ? dir + "/" : "";
    // Folder mtime = max of descendant entries' timestamps.
    const dirMtime = new Map<string, string>();
    const fileList: { name: string; updated_at: string }[] = [];
    for (const e of entries) {
      if (!e.path.startsWith(prefix)) continue;
      const rest = e.path.slice(prefix.length);
      if (!rest) continue;
      const slash = rest.indexOf("/");
      if (slash === -1) {
        if (rest.endsWith(".md"))
          fileList.push({ name: rest, updated_at: e.updated_at });
      } else {
        const name = rest.slice(0, slash);
        const cur = dirMtime.get(name);
        if (!cur || (e.updated_at && e.updated_at > cur)) {
          dirMtime.set(name, e.updated_at);
        }
      }
    }
    const dirList = [...dirMtime.entries()].map(([name, updated_at]) => ({
      name,
      updated_at,
    }));
    const byName =
      (asc: boolean) => (a: { name: string }, b: { name: string }) =>
        asc ? a.name.localeCompare(b.name) : b.name.localeCompare(a.name);
    // Newest first; empty timestamps sink to the bottom.
    const byRecent = (a: { updated_at: string }, b: { updated_at: string }) => {
      if (!a.updated_at && !b.updated_at) return 0;
      if (!a.updated_at) return 1;
      if (!b.updated_at) return -1;
      return b.updated_at.localeCompare(a.updated_at);
    };
    const cmp = sort === "recent" ? byRecent : byName(sort === "name-asc");
    return {
      subdirs: dirList.sort(cmp),
      files: fileList.sort(cmp),
    };
  }, [entries, dir, sort]);

  const segments = dir ? dir.split("/") : [];

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    const raw = newName.trim();
    if (!raw) return;
    // Only folders go through this inline form; new docs are handled by
    // NewDocView, which the +New document button routes to directly.
    if (creating !== "folder") return;
    setCreateBusy(true);
    setError(null);
    try {
      const folderName = raw.replace(/\/+$/, "");
      const fullPath = (dir ? dir + "/" : "") + folderName;
      await apiFetch("/wiki/folder", {
        method: "POST",
        body: JSON.stringify({ path: fullPath }),
      });
      setNewName("");
      setCreating(null);
      refresh();
      router.push(`/app/wiki/${fullPath}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "create failed");
    } finally {
      setCreateBusy(false);
    }
  }

  async function onDelete(rel: string) {
    if (!confirm(`Delete ${rel}? This cannot be undone.`)) return;
    setBusyPath(rel);
    setError(null);
    try {
      await apiFetch(`/wiki/file?path=${encodeURIComponent(rel)}`, {
        method: "DELETE",
      });
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    } finally {
      setBusyPath(null);
    }
  }

  async function onMove(srcRel: string, destDir: string) {
    const base = srcRel.split("/").pop() ?? srcRel;
    const newRel = destDir ? `${destDir}/${base}` : base;
    if (newRel === srcRel) return;
    // Block dropping a folder onto itself or any of its descendants.
    if (destDir === srcRel || destDir.startsWith(srcRel + "/")) {
      setError("Cannot move a folder into itself.");
      return;
    }
    setBusyPath(srcRel);
    setError(null);
    try {
      await apiFetch("/wiki/move", {
        method: "POST",
        body: JSON.stringify({ old_path: srcRel, new_path: newRel }),
      });
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "move failed");
    } finally {
      setBusyPath(null);
    }
  }

  async function onRenameSubmit(rel: string, rawName: string) {
    const trimmed = rawName.trim().replace(/^\/+|\/+$/g, "");
    if (!trimmed || trimmed.includes("/")) {
      setError("Name cannot be empty or contain '/'.");
      return;
    }
    const segs = rel.split("/");
    const parent = segs.slice(0, -1).join("/");
    const isFile = rel.endsWith(".md");
    const finalName =
      isFile && !trimmed.endsWith(".md") ? trimmed + ".md" : trimmed;
    const newRel = parent ? `${parent}/${finalName}` : finalName;
    if (newRel === rel) {
      setRenaming(null);
      return;
    }
    setBusyPath(rel);
    setError(null);
    try {
      await apiFetch("/wiki/move", {
        method: "POST",
        body: JSON.stringify({ old_path: rel, new_path: newRel }),
      });
      setRenaming(null);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "rename failed");
    } finally {
      setBusyPath(null);
    }
  }

  return (
    <main
      className={`h-screen overflow-y-auto ${isMobile ? "py-4 px-3" : "py-6 px-8"}`}
    >
      <PageHeader
        title={
          <Breadcrumbs
            segments={segments}
            onDropToCrumb={(crumbPath) => {
              if (dragSource && crumbPath !== dir)
                onMove(dragSource, crumbPath);
              setDragSource(null);
              setDropTarget(null);
            }}
            dropTarget={dropTarget}
            onCrumbDragOver={(crumbPath) => setDropTarget(crumbPath)}
            onCrumbDragLeave={() => setDropTarget(null)}
            currentDir={dir}
          />
        }
        actions={
          <>
            <Button
              icon={SvgWorkflow}
              onClick={() => setTriggerModalOpen(true)}
            >
              Trigger
            </Button>
            <Button
              icon={SvgFolderPlus}
              onClick={() => {
                setNewName("");
                setCreating((v) => (v === "folder" ? null : "folder"));
              }}
            >
              New folder
            </Button>
            <Button
              icon={SvgPlus}
              onClick={() => router.push(`/app/wiki/${dir}?new=1`)}
            >
              New document
            </Button>
          </>
        }
      />

      <TriggerModal
        open={triggerModalOpen}
        initial={{ scope_path: dir || "/" }}
        lockScope
        onClose={() => setTriggerModalOpen(false)}
        onSaved={(t) => setTriggerStatus(`Created trigger for ${t.scope_path}`)}
      />

      {triggerStatus && (
        <div className="text-xs text-(--text-04) mb-3">{triggerStatus}</div>
      )}

      {creating && (
        <form
          onSubmit={onCreate}
          className="flex gap-2 mb-4 p-3 bg-(--background-tint-01) border border-(--border-01) rounded-(--border-radius-08)"
        >
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="folder-name (or subdir/folder-name)"
            disabled={createBusy}
            className="flex-1 p-2 border border-(--border-01) rounded-(--border-radius-04) text-sm"
          />
          <Button
            type="submit"
            variant="action"
            disabled={createBusy || !newName.trim()}
          >
            Create folder
          </Button>
          <Button
            type="button"
            onClick={() => {
              setCreating(null);
              setNewName("");
            }}
          >
            Cancel
          </Button>
        </form>
      )}

      {error && (
        <div className="p-[10px] bg-(--status-error-01) text-(--status-text-error-05) rounded-(--border-radius-04) text-[13px] mb-3">
          {error}
        </div>
      )}

      {subdirs.length === 0 && files.length === 0 && !error && (
        <p className="text-(--text-03) text-sm">
          This folder is empty. Create a document to get started.
        </p>
      )}

      {(subdirs.length > 0 || files.length > 0) && (
        <SortBar value={sort} onChange={setSort} />
      )}

      <ul className="list-none p-0 m-0">
        {(() => {
          const dirEntries = subdirs.map((d) => ({ ...d, isFile: false }));
          const fileEntries = files.map((f) => ({ ...f, isFile: true }));
          // Folders always above docs; ordering within each group is set by `sort`.
          const ordered = [...dirEntries, ...fileEntries];
          return ordered.map(({ name, updated_at, isFile }) => {
            const childPath = (dir ? dir + "/" : "") + name;
            return (
              <Row
                key={(isFile ? "f:" : "d:") + name}
                icon={
                  isFile ? <SvgDocFile size={20} aria-hidden /> : <SvgFolder size={20} aria-hidden />
                }
                label={name}
                updatedAt={updated_at}
                href={`/app/wiki/${childPath}`}
                path={childPath}
                isFile={isFile}
                busy={busyPath === childPath}
                onDelete={() => onDelete(childPath)}
                onShare={() => setSharePath(childPath)}
                renaming={renaming === childPath}
                onStartRename={() => setRenaming(childPath)}
                onCancelRename={() => setRenaming(null)}
                onSubmitRename={(v) => onRenameSubmit(childPath, v)}
                onDragStart={() => setDragSource(childPath)}
                onDragEnd={() => {
                  setDragSource(null);
                  setDropTarget(null);
                }}
                dropActive={!isFile && dropTarget === childPath}
                onFolderDragOver={
                  isFile
                    ? undefined
                    : () => {
                        if (dragSource && dragSource !== childPath) {
                          setDropTarget(childPath);
                        }
                      }
                }
                onFolderDragLeave={
                  isFile
                    ? undefined
                    : () =>
                        setDropTarget((cur) => (cur === childPath ? null : cur))
                }
                onFolderDrop={
                  isFile
                    ? undefined
                    : () => {
                        if (dragSource && dragSource !== childPath) {
                          onMove(dragSource, childPath);
                        }
                        setDragSource(null);
                        setDropTarget(null);
                      }
                }
              />
            );
          });
        })()}
      </ul>
      {sharePath && (
        <ShareDialog path={sharePath} open onClose={() => setSharePath(null)} />
      )}
    </main>
  );
}

function NewDocView({ dir }: { dir: string }) {
  const router = useRouter();
  const isMobile = useIsMobile();
  const { setDrafting, requestExpand } = useDrafting();
  const [filename, setFilename] = useState("");
  const [draft, setDraft] = useState("");
  const [templates, setTemplates] = useState<DocumentTemplateSummary[] | null>(
    null,
  );
  const [appliedTemplateId, setAppliedTemplateId] = useState<string | null>(
    null,
  );
  const [appliedTemplateBody, setAppliedTemplateBody] = useState<string | null>(
    null,
  );
  const [applyingTemplateId, setApplyingTemplateId] = useState<string | null>(
    null,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Pop the chat widget open once on mount — the assistant can help
  // while the user picks a template / drafts initial content.
  useEffect(() => {
    requestExpand();
  }, [requestExpand]);

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

  // Sync drafting context with the current pick — including the
  // initial "no template chosen yet" state, which maps to ``blank``
  // drafting so the chat kicks off the moment +New routes into this
  // view. Picking a template later swaps ``desiredKey`` from "blank"
  // to "tpl:<id>" and the chat widget re-inits a fresh session for it.
  useEffect(() => {
    if (appliedTemplateId) {
      if (!templates) return; // wait until we can resolve the name
      const t = templates.find((x) => x.id === appliedTemplateId);
      setDrafting({
        kind: "template",
        path: null,
        templateId: appliedTemplateId,
        templateName: t?.name ?? null,
      });
    } else {
      setDrafting({ kind: "blank", path: null });
    }
  }, [appliedTemplateId, templates, setDrafting]);
  useEffect(() => {
    return () => setDrafting(null);
  }, [setDrafting]);

  const trimmedFilename = filename.trim().replace(/^\/+|\/+$/g, "");
  const filenameNoExt = trimmedFilename.replace(/\.md$/i, "");
  const filenameValid = !!filenameNoExt && !filenameNoExt.includes("/");
  const canCreate = filenameValid && !saving;

  async function onPickTemplate(template: DocumentTemplateSummary) {
    setApplyingTemplateId(template.id);
    setError(null);
    try {
      const full = await getTemplate(template.id);
      setDraft(full.body);
      setAppliedTemplateBody(full.body);
      setAppliedTemplateId(template.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to apply template");
    } finally {
      setApplyingTemplateId(null);
    }
  }

  function onPickBlank() {
    setDraft("");
    setAppliedTemplateBody(null);
    setAppliedTemplateId(null);
    // Kick the chat widget into blank-drafting mode so it spins up a
    // hidden session with the generic "what would you like to work on"
    // prime, the same way a template pick spins up a template-aware
    // session.
    setDrafting({ kind: "blank", path: null });
  }

  async function onCreate() {
    if (!filenameValid) {
      setError("Filename cannot be empty or contain '/'.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const name = filenameNoExt + ".md";
      const fullPath = (dir ? dir + "/" : "") + name;
      await apiFetch("/wiki/file", {
        method: "PUT",
        body: JSON.stringify({ path: fullPath, body: draft }),
      });
      // If a template was applied, record the draft row so the chat
      // banner + template system prompt persist on the saved doc.
      if (appliedTemplateId) {
        await setDraftTemplate(fullPath, appliedTemplateId);
      }
      router.push(`/app/wiki/${fullPath}?new=1`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "create failed");
      setSaving(false);
    }
  }

  const isBlank = draft.trim() === "";
  const matchesApplied =
    appliedTemplateBody !== null && draft === appliedTemplateBody;
  const showGallery =
    (isBlank || matchesApplied) && templates !== null && templates.length > 0;
  const parentSlug = dir;

  return (
    <main
      className={`h-screen flex flex-col box-border gap-3 ${isMobile ? "py-4 px-3" : "py-6 px-8"}`}
    >
      <PageHeader
        title="New document"
        actions={
          <>
            <Button
              onClick={() => router.push(`/app/wiki/${dir}`)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button
              variant="action"
              onClick={() => void onCreate()}
              disabled={!canCreate}
              title={
                !filenameValid && !saving
                  ? "Give the file a name first."
                  : undefined
              }
            >
              {saving ? "Creating…" : "Create"}
            </Button>
          </>
        }
      />

      <FilenameRow
        parent={parentSlug}
        value={filename}
        onChange={setFilename}
        disabled={saving}
        autoFocus
        placeholder="filename for the new doc"
      />

      {error && (
        <div className="p-[10px] bg-(--status-error-01) text-(--status-text-error-05) rounded-(--border-radius-04) text-[13px]">
          {error}
        </div>
      )}

      {showGallery && (
        <TemplateGallery
          templates={templates!}
          activeId={matchesApplied ? appliedTemplateId : null}
          applyingId={applyingTemplateId}
          blankActive={isBlank && appliedTemplateId === null}
          onPick={(t) => void onPickTemplate(t)}
          onBlank={onPickBlank}
        />
      )}

      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        spellCheck={false}
        placeholder={
          templates && templates.length > 0
            ? "Start typing, or pick a template above…"
            : "Start typing your new document…"
        }
        className="flex-1 min-h-0 w-full box-border p-4 border border-(--border-01) rounded-(--border-radius-08) font-mono text-sm leading-[1.6] resize-none outline-none"
      />
    </main>
  );
}

function TemplateGallery({
  templates,
  activeId,
  applyingId,
  blankActive,
  onPick,
  onBlank,
}: {
  templates: DocumentTemplateSummary[];
  activeId: string | null;
  applyingId: string | null;
  blankActive: boolean;
  onPick: (t: DocumentTemplateSummary) => void;
  onBlank: () => void;
}) {
  // Always a single-row strip — the picker never wraps to a second
  // line. On wide screens the user scrolls / clicks chevrons through
  // the row; on narrow screens the same layout becomes a swipe strip.
  return (
    <div className="flex flex-col gap-[10px] p-[14px] bg-(--background-tint-01) border border-(--border-01) rounded-(--border-radius-08)">
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

function TemplateStrip({
  templates,
  activeId,
  applyingId,
  blankActive,
  onPick,
  onBlank,
}: {
  templates: DocumentTemplateSummary[];
  activeId: string | null;
  applyingId: string | null;
  blankActive: boolean;
  onPick: (t: DocumentTemplateSummary) => void;
  onBlank: () => void;
}) {
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

  return (
    <div className="relative">
      <div
        ref={scrollerRef}
        className="scroll-x-hidden flex gap-2 overflow-x-auto pb-[2px] snap-x snap-mandatory"
        style={{ WebkitOverflowScrolling: "touch" }}
      >
        <div className="shrink-0 snap-start w-[200px]">
          <TemplateCard
            title="Blank document"
            description="Empty file — just start typing."
            active={blankActive}
            busy={false}
            onClick={onBlank}
          />
        </div>
        {templates.map((t) => (
          <div key={t.id} className="shrink-0 snap-start w-[200px]">
            <TemplateCard
              title={t.name}
              description={t.description}
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

function StripArrow({
  direction,
  onClick,
}: {
  direction: "left" | "right";
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={direction === "left" ? "Scroll left" : "Scroll right"}
      className={`absolute top-1/2 -translate-y-1/2 ${direction === "left" ? "left-1" : "right-1"} w-7 h-7 rounded-full bg-(--background-tint-00) border border-(--border-01) shadow-(--shadow-sm) cursor-pointer text-(--text-04) flex items-center justify-center p-0`}
    >
      {direction === "left" ? (
        <SvgChevronLeft size={14} />
      ) : (
        <SvgChevronRight size={14} />
      )}
    </button>
  );
}

function TemplateCard({
  title,
  description,
  active,
  busy,
  onClick,
}: {
  title: string;
  description: string | null;
  active: boolean;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={`text-left py-[10px] px-3 rounded-(--border-radius-04) text-(--text-05) w-full h-full box-border min-h-[64px] flex flex-col gap-1 transition-[background,border-color] duration-[80ms] ease-in-out border ${busy ? "opacity-[0.7] cursor-wait" : "cursor-pointer"} ${active ? "bg-(--background-tint-03) border-(--border-01)" : "bg-(--background-tint-00) border-(--border-01)"}`}
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
        <div className="text-xs text-(--text-03) line-clamp-2">
          {description}
        </div>
      )}
    </button>
  );
}

type SortMode = "name-asc" | "name-desc" | "recent";

function SortBar({
  value,
  onChange,
}: {
  value: SortMode;
  onChange: (v: SortMode) => void;
}) {
  return (
    <div className="flex items-center gap-2 mb-2 text-xs text-(--text-03)">
      <label htmlFor="wiki-sort">Sort:</label>
      <select
        id="wiki-sort"
        value={value}
        onChange={(e) => onChange(e.target.value as SortMode)}
        className="py-1 px-2 border border-(--border-01) rounded-(--border-radius-04) bg-(--background-tint-00) text-(--text-05) text-xs"
      >
        <option value="name-asc">Name (A → Z)</option>
        <option value="name-desc">Name (Z → A)</option>
        <option value="recent">Recently updated</option>
      </select>
    </div>
  );
}

function Row({
  icon,
  label,
  updatedAt,
  href,
  path,
  isFile,
  busy,
  onDelete,
  onShare,
  renaming,
  onStartRename,
  onCancelRename,
  onSubmitRename,
  onDragStart,
  onDragEnd,
  dropActive,
  onFolderDragOver,
  onFolderDragLeave,
  onFolderDrop,
}: {
  icon: React.ReactNode;
  label: string;
  updatedAt: string;
  href: string;
  path: string;
  isFile: boolean;
  busy: boolean;
  onDelete: () => void;
  onShare?: () => void;
  renaming: boolean;
  onStartRename: () => void;
  onCancelRename: () => void;
  onSubmitRename: (newName: string) => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  dropActive: boolean;
  onFolderDragOver?: () => void;
  onFolderDragLeave?: () => void;
  onFolderDrop?: () => void;
}) {
  const router = useRouter();
  const [hover, setHover] = useState(false);
  const [draft, setDraft] = useState(label);

  useEffect(() => {
    if (renaming) setDraft(label);
  }, [renaming, label]);

  // The whole row acts as the click target *and* the drag source.
  // Clicks navigate via router.push(href) instead of relying on a
  // child <Link>, which previously left a dead zone around the icon
  // and trailing whitespace where the cursor showed "grab" but didn't
  // navigate. Drags from the action buttons (rename/delete) are
  // suppressed so a careless drag near the right edge doesn't kick off
  // a move operation; their clicks stop propagation so they don't
  // double-fire row navigation.
  return (
    <li
      draggable={!renaming}
      onClick={(e) => {
        if (renaming) return;
        // If the click landed on a button or inside the rename form,
        // let that element handle it — we don't want delete/rename
        // taps to also navigate into the doc.
        const target = e.target as HTMLElement;
        if (target.closest("button, form, input")) return;
        router.push(href);
      }}
      onDragStart={(e) => {
        // Cancel drags that start on the action buttons so the user
        // can mash on rename/delete without yanking the row into a
        // move state.
        const target = e.target as HTMLElement;
        if (target.closest("button, form, input")) {
          e.preventDefault();
          return;
        }
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", path);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onDragOver={
        onFolderDragOver
          ? (e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              onFolderDragOver();
            }
          : undefined
      }
      onDragLeave={onFolderDragLeave}
      onDrop={
        onFolderDrop
          ? (e) => {
              e.preventDefault();
              onFolderDrop();
            }
          : undefined
      }
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className={`flex items-center py-[10px] px-3 border-b border-(--border-01) ${dropActive ? "bg-(--background-tint-03) outline outline-2 outline-(--background-tint-inverted-00)" : hover ? "bg-(--background-tint-02)" : "bg-transparent"} ${busy ? "opacity-50" : "opacity-100"} ${renaming ? "cursor-default" : "cursor-pointer"}`}
    >
      <span className="text-(--text-03) flex mr-[10px]">{icon}</span>
      {renaming ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSubmitRename(draft);
          }}
          className="flex flex-1 gap-1.5"
        >
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                onCancelRename();
              }
            }}
            disabled={busy}
            className="flex-1 py-1 px-2 border border-(--border-01) rounded-(--border-radius-04) text-sm"
          />
          <Button
            type="submit"
            size="sm"
            variant="action"
            disabled={busy || !draft.trim()}
          >
            Save
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={onCancelRename}
            disabled={busy}
          >
            Cancel
          </Button>
        </form>
      ) : (
        // The label is a plain span — the click target is the parent
        // <li>. flex: 1 keeps it stretching to fill the space between
        // icon and action buttons, so any click on the label area
        // still hits the row's onClick.
        <span className="flex items-center gap-[10px] flex-1 text-(--text-05) text-sm">
          {label}
        </span>
      )}
      {!renaming && (
        <>
          <span className="text-xs text-(--text-02) mr-2 whitespace-nowrap">
            {updatedAt ? relativeTime(updatedAt, "short") : "—"}
          </span>
          {onShare && (
            <button
              onClick={onShare}
              disabled={busy}
              title="Share"
              aria-label={`Share ${label}`}
              className={`bg-transparent border-none p-[6px] flex items-center ${busy ? "cursor-not-allowed" : "cursor-pointer"} ${hover ? "text-(--text-04)" : "text-transparent"}`}
            >
              <SvgShare size={16} />
            </button>
          )}
          <button
            onClick={onStartRename}
            disabled={busy}
            title="Rename"
            aria-label={`Rename ${label}`}
            className={`bg-transparent border-none p-[6px] flex items-center ${busy ? "cursor-not-allowed" : "cursor-pointer"} ${hover ? "text-(--text-04)" : "text-transparent"}`}
          >
            <SvgEdit size={16} />
          </button>
          <button
            onClick={onDelete}
            disabled={busy}
            title="Delete"
            aria-label={`Delete ${label}`}
            className={`bg-transparent border-none p-[6px] flex items-center ${busy ? "cursor-not-allowed" : "cursor-pointer"} ${hover ? "text-(--status-text-error-05)" : "text-transparent"}`}
          >
            <SvgTrash size={16} />
          </button>
        </>
      )}
    </li>
  );
}

function Breadcrumbs({
  segments,
  onDropToCrumb,
  onCrumbDragOver,
  onCrumbDragLeave,
  dropTarget,
  currentDir,
}: {
  segments: string[];
  onDropToCrumb?: (crumbPath: string) => void;
  onCrumbDragOver?: (crumbPath: string) => void;
  onCrumbDragLeave?: () => void;
  dropTarget?: string | null;
  currentDir?: string;
}) {
  // Use a sentinel for the root crumb so we can track its drop state without
  // collision with a real path of "".
  const ROOT = "__root__";
  const crumbs = [{ label: "Wiki", href: "/app/wiki", path: "" }];
  segments.forEach((seg, i) => {
    const path = segments.slice(0, i + 1).join("/");
    crumbs.push({ label: seg, href: `/app/wiki/${path}`, path });
  });
  return (
    <nav className="flex items-center gap-1.5 text-sm flex-wrap">
      {crumbs.map((c, i) => {
        const last = i === crumbs.length - 1;
        const targetKey = c.path === "" ? ROOT : c.path;
        const droppable = onDropToCrumb && c.path !== currentDir;
        const active = !!droppable && dropTarget === targetKey;
        const dropHandlers: Record<string, unknown> = droppable
          ? {
              onDragOver: (e: React.DragEvent) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                onCrumbDragOver?.(targetKey);
              },
              onDragLeave: () => onCrumbDragLeave?.(),
              onDrop: (e: React.DragEvent) => {
                e.preventDefault();
                onDropToCrumb?.(c.path);
              },
            }
          : {};
        const activeClass = active
          ? "bg-(--background-tint-03) outline outline-2 outline-(--background-tint-inverted-00) rounded-(--border-radius-04) py-[2px] px-[6px]"
          : "";
        return (
          <span key={c.href} className="flex items-center gap-1.5">
            {i > 0 && <span className="text-(--text-02)">/</span>}
            {last ? (
              <span
                className={`font-semibold ${activeClass}`}
                {...dropHandlers}
              >
                {c.label}
              </span>
            ) : (
              <Link
                href={c.href}
                className={`text-(--text-05) underline ${activeClass}`}
                {...dropHandlers}
              >
                {c.label}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}

function FileViewer({ path }: { path: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isMobile = useIsMobile();
  const { setDrafting, requestExpand } = useDrafting();
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [filenameDraft, setFilenameDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggerModalOpen, setTriggerModalOpen] = useState(false);
  const [triggerStatus, setTriggerStatus] = useState<string | null>(null);
  const [runAgentOpen, setRunAgentOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  // History state. `viewingSha` is null when looking at the working-tree
  // (latest) version; otherwise it's the sha being viewed and is what we
  // pass back as `base_sha` on save so the server records a rollback.
  const [headSha, setHeadSha] = useState<string | null>(null);
  const [viewingSha, setViewingSha] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [commits, setCommits] = useState<CommitInfo[] | null>(null);
  const [diffData, setDiffData] = useState<FileDiffResponse | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  // Comments (render-mode). `commentDraft` is a pending text selection being
  // composed; `selTool` is the floating "Comment" affordance shown on select.
  const [commentsOpen, setCommentsOpen] = useState(false);
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
  // closed). Auto-open the panel once per path when a page has comments.
  const autoOpenedPathRef = useRef<string | null>(null);

  const refreshComments = useCallback(async () => {
    try {
      const t = await listComments(path);
      setCommentThreads(t);
      if (t.length > 0 && autoOpenedPathRef.current !== path) {
        autoOpenedPathRef.current = path;
        setCommentsOpen(true);
      }
    } catch {
      // comments are non-critical chrome; ignore load failures
    }
  }, [path]);

  useEffect(() => {
    autoOpenedPathRef.current = null;
    setCommentThreads([]);
    void refreshComments();
  }, [refreshComments]);

  // Render the markdown once per body change and reuse the element on every
  // unrelated re-render (panel open/close, active-comment change, …). A fresh
  // <ReactMarkdown> element would let React reconcile and replace the article's
  // text nodes, which silently invalidates the CSS Highlight Ranges painted
  // over them — the bug where highlights vanished until you clicked a comment.
  const renderedBody = useMemo(
    () => (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSourcePos]}
      >
        {body}
      </ReactMarkdown>
    ),
    [body],
  );

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
  // Conflict resolution: set when a save returns 409.
  const [conflict, setConflict] = useState<ConflictState | null>(null);
  // Resume banner: set when entering edit mode and a matching draft exists.
  const [pendingResumeDraft, setPendingResumeDraft] =
    useState<DraftResponse | null>(null);
  const [resuming, setResuming] = useState(false);
  const [consolidating, setConsolidating] = useState(false);
  // Debounce timer ref for auto-saving the draft to the server.
  const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Incremented on each startEdit() call and on cancel; lets async
  // continuations inside startEdit bail out if editing was cancelled first.
  const editSessionRef = useRef(0);

  const loadLatest = useCallback(() => {
    setLoading(true);
    setError(null);
    setEditing(false);
    setViewingSha(null);
    setDiffData(null);
    apiFetch<FileResponse>(`/wiki/file?path=${encodeURIComponent(path)}`)
      .then((r) => {
        setBody(r.body);
        setDraft(r.body);
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
    (s) => s.status === "active" || s.status === "idle",
  );

  const handleCloseSession = useCallback(
    async (id: string) => {
      if (!confirm("Close this agent session?")) return;
      try {
        await closeSession(id, "user_clicked");
      } catch (err) {
        alert(err instanceof Error ? err.message : "Failed to close session");
      } finally {
        await refreshSessions();
      }
    },
    [refreshSessions],
  );
  useEffect(() => {
    if (!liveDoc) return;
    // Re-check the gate at apply time — an in-flight fetch from before
    // the user toggled `editing` can still resolve here and would
    // otherwise clobber their textarea draft.
    if (editing || viewingSha !== null) return;
    setBody((prev) => (prev === liveDoc.body ? prev : liveDoc.body));
    setDraft((prev) => (prev === liveDoc.body ? prev : liveDoc.body));
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

  // Auto-save the draft to the server while the user is editing.
  // Debounced 5s so we don't hammer the API on every keystroke.
  // Only fires when the draft differs from the saved body.
  useEffect(() => {
    if (!editing) return;
    if (draft === body) return;
    const baseSha = viewingSha ?? headSha;
    if (!baseSha) return;
    if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
    autoSaveTimer.current = setTimeout(() => {
      void apiFetch("/wiki/file/autosave", {
        method: "PUT",
        body: JSON.stringify({ path, base_sha: baseSha, content: draft }),
      }).catch(() => {
        // Auto-save failures are silent — the user still has the draft
        // locally and can save manually.
      });
    }, 5000);
    return () => {
      if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
    };
  }, [draft, editing, path, headSha, viewingSha]);

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

  async function toggleHistory() {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (!next) return;
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

  const segments = path.split("/");
  const parentSlug = segments.slice(0, -1).join("/");
  const backHref = parentSlug ? `/app/wiki/${parentSlug}` : "/app/wiki";
  const currentBasename = segments[segments.length - 1] ?? path;
  const currentBasenameNoExt = currentBasename.replace(/\.md$/i, "");
  const trimmedFilename = filenameDraft.trim().replace(/^\/+|\/+$/g, "");
  const filenameNoExt = trimmedFilename.replace(/\.md$/i, "");
  const filenameValid = !!filenameNoExt && !filenameNoExt.includes("/");
  const renamed =
    editing && filenameValid && filenameNoExt !== currentBasenameNoExt;
  const bodyChanged = editing && draft !== body;
  const dirty = editing && (bodyChanged || renamed);
  const viewingOld = viewingSha !== null;

  // Guard against losing unsaved edits when the user navigates away.
  // - beforeunload: tab close, refresh, typing a URL — browser shows a
  //   native confirm dialog (custom message is ignored on modern browsers).
  // - click capture: in-app links (back arrow, breadcrumbs, sidebar nav)
  //   don't fire beforeunload, so we intercept anchor clicks and confirm
  //   inline. Programmatic router.push (e.g. on rename-save) bypasses this
  //   on purpose — those navigations are intentional.
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const onDocClick = (e: MouseEvent) => {
      if (e.defaultPrevented) return;
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)
        return;
      const target = e.target as HTMLElement | null;
      const anchor = target?.closest("a");
      if (!anchor) return;
      const href = anchor.getAttribute("href");
      if (!href || href.startsWith("#")) return;
      if (anchor.target && anchor.target !== "_self") return;
      const url = new URL(href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname) return;
      if (
        !window.confirm("You have unsaved changes. Discard them and leave?")
      ) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("click", onDocClick, true);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("click", onDocClick, true);
    };
  }, [dirty]);

  async function startEdit() {
    setFilenameDraft(currentBasenameNoExt);
    setError(null);
    setEditing(true);
    const session = ++editSessionRef.current;
    // Check for an existing in-progress draft from a previous session.
    try {
      const saved = await apiFetch<DraftResponse | null>(
        `/wiki/file/autosave?path=${encodeURIComponent(path)}`,
      );
      if (editSessionRef.current !== session) return;
      if (!saved) return;
      // Show the Resume banner regardless of whether the draft is stale.
      // Rebase (if needed) runs when the user clicks Resume.
      setPendingResumeDraft(saved);
    } catch {
      // Draft fetch failure is non-fatal — user just starts fresh.
    }
  }

  async function onSave() {
    if (!filenameValid) {
      setError("Filename cannot be empty or contain '/'.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (bodyChanged) {
        const baseSha = viewingSha ?? headSha;
        await apiFetch("/wiki/file", {
          method: "PUT",
          body: JSON.stringify({
            path,
            body: draft,
            ...(baseSha ? { base_sha: baseSha } : {}),
          }),
        });
      }
      if (renamed) {
        const finalName = filenameNoExt + ".md";
        const newRel = parentSlug ? `${parentSlug}/${finalName}` : finalName;
        await apiFetch("/wiki/move", {
          method: "POST",
          body: JSON.stringify({ old_path: path, new_path: newRel }),
        });
        // Navigation will remount FileViewer with the new path; loadLatest
        // there resets editing/body/headSha. Bail out before touching state.
        router.push(`/app/wiki/${newRel}`);
        return;
      }
      setEditing(false);
      setViewingSha(null);
      setConflict(null);
      setPendingResumeDraft(null);
      // Optimistically show what the user submitted. The fetch below may
      // overwrite with the auto-merged body, but if it fails the viewer
      // still shows the content that was just committed rather than the
      // stale pre-edit body.
      setBody(draft);
      setDraft(draft);
      setDiffData(null);
      // History changed (new commit + possible deprecations) — refetch.
      if (historyOpen) refreshHistory();
      else setCommits(null);
      // Pick up the committed body and head_sha. Overwrite the optimistic
      // draft above with the actual merged result when the server auto-merged
      // concurrent edits. Failures are silent — the optimistic value is a
      // correct fallback since the PUT already succeeded.
      try {
        const fresh = await apiFetch<FileResponse>(
          `/wiki/file?path=${encodeURIComponent(path)}`,
        );
        setHeadSha(fresh.head_sha ?? null);
        setBody(fresh.body);
        setDraft(fresh.body);
      } catch {
        // fresh fetch failed — body already shows the local draft
      }
      // The commit re-anchored comments server-side; refetch so the panel +
      // highlights reflect the drift (won't re-open the panel — guarded).
      void refreshComments();
      // The server clears the draft row when the body diverges from
      // the template snapshot — re-sync our context so the chat
      // banner disappears at the same moment.
      await refreshDraftState();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // Conflict: page changed since we opened it. Fetch current HEAD
        // and show the conflict resolution panel.
        try {
          const current = await apiFetch<FileResponse>(
            `/wiki/file?path=${encodeURIComponent(path)}`,
          );
          setConflict({
            draftBody: draft,
            currentBody: current.body,
            currentSha: current.head_sha ?? headSha ?? "",
            baseSha: viewingSha ?? headSha ?? "",
          });
        } catch {
          setError("Save conflict — could not load current version.");
        }
      } else {
        setError(e instanceof Error ? e.message : "save failed");
      }
    } finally {
      setSaving(false);
    }
  }

  async function onPickTemplate(template: DocumentTemplateSummary) {
    setApplyingTemplateId(template.id);
    setError(null);
    try {
      const full = await getTemplate(template.id);
      setDraft(full.body);
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
    setDraft("");
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

  function onCancel() {
    editSessionRef.current++;
    setDraft(body);
    setFilenameDraft(currentBasenameNoExt);
    setEditing(false);
    setError(null);
    setConflict(null);
    setPendingResumeDraft(null);
    setResuming(false);
    setConsolidating(false);
    if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
    // Server-side draft is intentionally kept on cancel so the user can
    // resume from the same point next time they enter edit mode.
  }

  async function onAiConsolidate() {
    if (!conflict) return;
    setConsolidating(true);
    setError(null);
    try {
      const result = await apiFetch<{ merged: string }>("/wiki/file/merge", {
        method: "POST",
        body: JSON.stringify({
          path,
          base_sha: conflict.baseSha,
          current_body: conflict.currentBody,
          draft_body: conflict.draftBody,
        }),
      });
      setDraft(result.merged);
      setHeadSha(conflict.currentSha);
      setViewingSha(null);
      setConflict(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "AI consolidation failed");
    } finally {
      setConsolidating(false);
    }
  }

  async function onKeepMine() {
    if (!conflict) return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/wiki/file", {
        method: "PUT",
        body: JSON.stringify({
          path,
          body: conflict.draftBody,
          base_sha: conflict.currentSha,
        }),
      });
      setDraft(conflict.draftBody);
      setBody(conflict.draftBody);
      setConflict(null);
      setEditing(false);
      if (historyOpen) refreshHistory();
      else setCommits(null);
      const fresh = await apiFetch<FileResponse>(
        `/wiki/file?path=${encodeURIComponent(path)}`,
      );
      setHeadSha(fresh.head_sha ?? null);
      await refreshDraftState();
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  function onUseCurrent() {
    if (!conflict) return;
    setDraft(conflict.currentBody);
    setBody(conflict.currentBody);
    setHeadSha(conflict.currentSha);
    setViewingSha(null);
    setConflict(null);
    setEditing(false);
    void apiFetch(`/wiki/file/autosave?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    }).catch(() => {});
  }

  return (
    <main
      className={`h-screen box-border flex flex-col min-h-0 ${isMobile ? "py-4 px-3" : "py-6 px-8"}`}
    >
      <header
        className={`flex items-center mb-4 flex-wrap ${isMobile ? "gap-2" : "gap-3"}`}
      >
        <Link
          href={backHref}
          title="Back"
          aria-label="Back"
          className="flex items-center justify-center w-8 h-8 rounded-(--border-radius-08) border border-(--border-01) text-(--text-04) no-underline shrink-0"
        >
          <SvgArrowLeft size={18} />
        </Link>
        <Breadcrumbs segments={segments} />
        <div className="flex-1" />
        {!editing && !loading && !error && (
          <>
            <div className="flex gap-2">
              <Button onClick={() => setRunAgentOpen(true)}>Run Agent</Button>
              <Button
                icon={SvgWorkflow}
                onClick={() => setTriggerModalOpen(true)}
              >
                Trigger
              </Button>
            </div>
            <div className="flex gap-2">
              <Button onClick={() => setShareOpen(true)}>Share</Button>
              <SelectButton
                state={historyOpen ? "selected" : "empty"}
                onClick={toggleHistory}
              >
                History
              </SelectButton>
              <SelectButton
                state={commentsOpen ? "selected" : "empty"}
                onClick={() => setCommentsOpen((v) => !v)}
              >
                Comments
              </SelectButton>
            </div>
            <Button variant="action" onClick={startEdit}>
              Edit
            </Button>
          </>
        )}
        {editing && (
          <>
            <Button onClick={onCancel} disabled={saving}>
              Cancel
            </Button>
            <Button
              variant="action"
              onClick={onSave}
              disabled={saving || !dirty}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </>
        )}
      </header>

      {!editing && (
        <ActiveAgentsBar
          agents={agents}
          sessions={activeSessions}
          error={agentsError}
          open={agentsOpen}
          onToggle={() => setAgentsOpen((v) => !v)}
          onCloseSession={handleCloseSession}
        />
      )}

      {!editing && triggerStatus && (
        <div className="text-xs text-(--text-04) mb-3">{triggerStatus}</div>
      )}

      <TriggerModal
        open={triggerModalOpen}
        initial={{ scope_path: path }}
        lockScope
        onClose={() => setTriggerModalOpen(false)}
        onSaved={(t) => setTriggerStatus(`Created trigger for ${t.scope_path}`)}
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
        <div className="p-[10px] bg-(--status-error-01) text-(--status-text-error-05) rounded-(--border-radius-04) text-[13px] mb-3">
          {error}
        </div>
      )}

      {viewingOld && !loading && !error && (
        <div className="flex items-center gap-3 py-2 px-3 mb-3 bg-(--status-warning-01) border border-(--status-warning-02) rounded-(--border-radius-08) text-[13px] text-(--status-text-warning-05)">
          <span>
            Viewing an older version
            {viewingSha ? ` (${viewingSha.slice(0, 7)})` : ""}.
            {editing
              ? " Saving will replace the current version and mark the in-between revisions as deprecated."
              : " Click Edit to fork from this version."}
          </span>
          <div className="flex-1" />
          <Button size="sm" onClick={loadLatest}>
            Back to latest
          </Button>
        </div>
      )}

      {editing && pendingResumeDraft && (
        <div className="flex items-center gap-3 py-2 px-3 mb-3 bg-(--status-info-01) border border-(--status-info-02) rounded-(--border-radius-08) text-[13px] text-(--status-text-info-05)">
          <span>You have unsaved changes from a previous session.</span>
          <div className="flex-1" />
          <Button
            size="sm"
            disabled={resuming}
            onClick={() => {
              if (pendingResumeDraft.base_sha === headSha) {
                // Fresh draft — restore directly.
                setDraft(pendingResumeDraft.content);
                setPendingResumeDraft(null);
                return;
              }
              // Stale draft — attempt 3-way rebase first.
              setResuming(true);
              void apiFetch<DraftResponse>("/wiki/file/autosave/rebase", {
                method: "POST",
                body: JSON.stringify({ path }),
              })
                .then((rebased) => {
                  setDraft(rebased.content);
                  setPendingResumeDraft(null);
                })
                .catch((e: unknown) => {
                  if (e instanceof ApiError && e.status === 409) {
                    const detail = e.data as {
                      current_body: string;
                      draft_body: string;
                      current_sha: string;
                    };
                    setConflict({
                      draftBody: detail.draft_body,
                      currentBody: detail.current_body,
                      currentSha: detail.current_sha,
                      baseSha: pendingResumeDraft.base_sha,
                    });
                    setPendingResumeDraft(null);
                  }
                })
                .finally(() => setResuming(false));
            }}
          >
            {resuming ? "Rebasing…" : "Resume"}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setPendingResumeDraft(null);
              void apiFetch(
                `/wiki/file/autosave?path=${encodeURIComponent(path)}`,
                { method: "DELETE" },
              ).catch(() => {});
            }}
          >
            Discard
          </Button>
        </div>
      )}

      {editing && conflict && (
        <div className="mb-3 border border-(--status-warning-02) rounded-(--border-radius-08) overflow-hidden">
          <div className="flex items-center gap-3 py-2 px-3 bg-(--status-warning-01) text-(--status-text-warning-05) text-[13px]">
            <span>This page was updated while you were editing.</span>
            <div className="flex-1" />
            <Button
              size="sm"
              onClick={() => void onKeepMine()}
              disabled={saving}
            >
              Keep mine
            </Button>
            <Button size="sm" onClick={onUseCurrent}>
              Use current
            </Button>
            <Button
              size="sm"
              onClick={() => void onAiConsolidate()}
              disabled={consolidating || saving}
            >
              {consolidating ? "Merging…" : "Merge with AI"}
            </Button>
            <Button size="sm" onClick={() => setConflict(null)}>
              Edit manually
            </Button>
          </div>
          <div className="grid grid-cols-2 gap-0">
            {(() => {
              const currentHunks = diffLines(
                conflict.draftBody,
                conflict.currentBody,
              );
              const draftHunks = diffLines(
                conflict.currentBody,
                conflict.draftBody,
              );
              const preClass =
                "m-0 text-xs leading-[1.5] font-mono whitespace-pre-wrap break-words max-h-[240px] overflow-y-auto";
              const labelClass =
                "text-[11px] font-semibold text-(--text-03) mb-[6px] uppercase tracking-[0.05em]";
              return (
                <>
                  <div className="p-3 border-r border-(--border-01)">
                    <div className={labelClass}>Current version</div>
                    <pre className={preClass}>
                      {currentHunks.map((part, i) => (
                        <span
                          key={i}
                          className={`${part.added ? "bg-(--status-success-01)" : "bg-transparent"} ${part.removed ? "text-transparent select-none" : "text-(--text-04)"}`}
                        >
                          {part.value}
                        </span>
                      ))}
                    </pre>
                  </div>
                  <div className="p-3">
                    <div className={labelClass}>Your draft</div>
                    <pre className={preClass}>
                      {draftHunks.map((part, i) => (
                        <span
                          key={i}
                          className={`${part.added ? "bg-(--status-warning-01)" : "bg-transparent"} ${part.removed ? "text-transparent select-none" : "text-(--text-04)"}`}
                        >
                          {part.value}
                        </span>
                      ))}
                    </pre>
                  </div>
                </>
              );
            })()}
          </div>
        </div>
      )}

      {loading && <LoadingSpinner />}

      {!loading && !error && (
        <div className="flex-1 min-h-0 flex gap-4">
          <div className="flex-1 min-w-0 flex flex-col gap-3">
            {editing ? (
              <>
                <FilenameRow
                  parent={parentSlug}
                  value={filenameDraft}
                  onChange={setFilenameDraft}
                  disabled={saving}
                />
                {(() => {
                  // Cards visible while the body is still "empty enough"
                  // to discard without losing user work: truly blank, or
                  // still verbatim equal to the template the user just
                  // applied (so they can keep swapping templates).
                  const isBlank = draft.trim() === "";
                  const matchesApplied =
                    appliedTemplateBody !== null &&
                    draft === appliedTemplateBody;
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
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  spellCheck={false}
                  placeholder="Start typing, or pick a template above…"
                  className="flex-1 min-h-0 w-full box-border p-4 border border-(--border-01) rounded-(--border-radius-08) font-mono text-sm leading-[1.6] resize-none outline-none"
                />
              </>
            ) : viewingOld && diffData ? (
              <div className="flex-1 min-h-0 overflow-hidden flex">
                <DiffView
                  data={diffData}
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
            ) : (
              <article
                ref={articleRef}
                className="markdown flex-1 min-h-0 overflow-y-auto"
                onMouseUp={onArticleMouseUp}
              >
                {renderedBody}
              </article>
            )}
          </div>
          {historyOpen && !isMobile && (
            <HistoryPanel
              commits={commits}
              error={historyError}
              headSha={headSha}
              viewingSha={viewingSha}
              onPick={onPickCommit}
              onClose={() => setHistoryOpen(false)}
            />
          )}
          {commentsOpen && !isMobile && (
            <CommentsPanel
              path={path}
              headSha={headSha}
              draft={commentDraft}
              threads={commentThreads}
              onChanged={refreshComments}
              activeId={activeCommentId}
              onActivate={setActiveCommentId}
              onDraftConsumed={() => setCommentDraft(null)}
              onClose={() => {
                setCommentsOpen(false);
                setCommentDraft(null);
              }}
            />
          )}
        </div>
      )}
      {historyOpen && isMobile && (
        // Mobile: render history as a fixed slide-in sheet over the
        // markdown content rather than a 320px side-panel that would
        // squeeze the body to nothing on a 375px screen.
        <>
          <div
            onClick={() => setHistoryOpen(false)}
            aria-hidden
            className="fixed inset-0 bg-(--mask-03) z-[60]"
          />
          <div className="fixed top-0 right-0 bottom-0 z-[70] flex shadow-(--shadow-panel) w-[min(360px,100vw)]">
            <HistoryPanel
              commits={commits}
              error={historyError}
              headSha={headSha}
              viewingSha={viewingSha}
              onPick={(sha) => {
                onPickCommit(sha);
                setHistoryOpen(false);
              }}
              onClose={() => setHistoryOpen(false)}
              fullHeight
            />
          </div>
        </>
      )}
      {commentsOpen && isMobile && (
        <>
          <div
            onClick={() => setCommentsOpen(false)}
            aria-hidden
            className="fixed inset-0 bg-(--mask-03) z-[60]"
          />
          <div className="fixed top-0 right-0 bottom-0 z-[70] flex shadow-(--shadow-panel) w-[min(360px,100vw)]">
            <CommentsPanel
              path={path}
              headSha={headSha}
              draft={commentDraft}
              threads={commentThreads}
              onChanged={refreshComments}
              activeId={activeCommentId}
              onActivate={setActiveCommentId}
              onDraftConsumed={() => setCommentDraft(null)}
              onClose={() => {
                setCommentsOpen(false);
                setCommentDraft(null);
              }}
              fullHeight
            />
          </div>
        </>
      )}
      {selTool && (
        <div
          onMouseDown={(e) => e.preventDefault()}
          className="fixed -translate-x-1/2 -translate-y-full z-[80] bg-(--background-tint-01) border border-(--border-01) rounded-(--border-radius-08) shadow-(--shadow-popover) p-1"
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
              setCommentsOpen(true);
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

function ActiveAgentsBar({
  agents,
  sessions,
  error,
  open,
  onToggle,
  onCloseSession,
}: {
  agents: DocumentActivity[];
  sessions: AgentSessionSummary[];
  error: string | null;
  open: boolean;
  onToggle: () => void;
  onCloseSession: (id: string) => void;
}) {
  const count = agents.length + sessions.length;
  const expandable = count > 0;
  return (
    <div className="mb-3 border border-(--border-01) rounded-(--border-radius-08) bg-(--background-tint-01) overflow-hidden">
      <button
        onClick={expandable ? onToggle : undefined}
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        className={`w-full text-left py-2 px-3 bg-transparent border-none text-[13px] flex items-center gap-2 ${expandable ? "cursor-pointer text-(--text-05)" : "cursor-default text-(--text-03)"}`}
      >
        <span
          aria-hidden
          className={`shrink-0 flex transition-transform duration-[120ms] ease-in-out ${open ? "rotate-90" : "rotate-0"} ${!expandable ? "text-(--text-02)" : "text-(--text-03)"}`}
        >
          <SvgChevronRight size={10} />
        </span>
        <span className="font-medium">
          {expandable ? "Active agents" : "No agents active"}
        </span>
        {expandable && (
          <span className="text-[11px] font-semibold py-[1px] px-[6px] rounded-full bg-(--background-tint-03) text-(--text-05)">
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
        <ul className="list-none p-0 m-0 border-t border-(--border-01) bg-(--background-tint-00)">
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

function ActiveAgentRow({
  a,
  isLast,
}: {
  a: DocumentActivity;
  isLast: boolean;
}) {
  return (
    <li
      className={
        `py-[10px] px-3 text-[13px] flex items-center gap-[10px] whitespace-nowrap overflow-hidden` +
        (isLast ? `` : ` border-b border-(--border-01)`)
      }
    >
      <span className="shrink-0 text-[10px] font-semibold py-[1px] px-[6px] rounded-(--border-radius-04) bg-(--background-tint-03) text-(--text-05) uppercase tracking-[0.3px]">
        {a.activity}
      </span>

      <span className="font-medium text-(--text-05) shrink-0">
        {a.owner_display}
      </span>
      {a.agent_name ? (
        <span className="text-(--text-03) shrink-0">
          {"·"} {a.agent_name}
        </span>
      ) : null}

      {a.description ? (
        <span
          className="text-(--text-04) italic overflow-hidden text-ellipsis min-w-0 grow"
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
        className="text-[11px] text-(--text-02) shrink-0"
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

function ActiveSessionRow({
  s,
  isLast,
  onClose,
}: {
  s: AgentSessionSummary;
  isLast: boolean;
  onClose: () => void;
}) {
  return (
    <li
      className={
        `py-[10px] px-3 text-[13px] flex items-center gap-[10px] whitespace-nowrap overflow-hidden` +
        (isLast ? `` : ` border-b border-(--border-01)`)
      }
    >
      <span className="shrink-0 text-[10px] font-semibold py-[1px] px-[6px] rounded-(--border-radius-04) bg-(--background-tint-03) text-(--text-05) uppercase tracking-[0.3px]">
        {s.status}
      </span>

      <span className="font-medium text-(--text-05) shrink-0">{s.tool_id}</span>

      <span className="flex-1" />

      <span
        className="text-[11px] text-(--text-02) shrink-0"
        title={`Started ${absoluteTime(s.started_at)}`}
      >
        started {relativeTime(s.started_at, "short")}
      </span>

      <Button type="button" variant="default" size="sm" onClick={onClose}>
        Close
      </Button>
    </li>
  );
}

function FilenameRow({
  parent,
  value,
  onChange,
  disabled,
  autoFocus = false,
  placeholder = "filename",
}: {
  parent: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  autoFocus?: boolean;
  placeholder?: string;
}) {
  return (
    <div className="flex items-stretch border border-(--border-01) rounded-(--border-radius-04) bg-(--background-tint-00) overflow-hidden shrink-0">
      {parent && (
        <span className="flex items-center px-[10px] bg-(--background-tint-02) border-r border-(--border-01) text-(--text-04) font-mono text-[13px]">
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
        className="flex-1 py-2 px-[10px] border-none outline-none text-sm font-mono bg-transparent"
      />
      <span
        aria-hidden
        className="flex items-center px-[10px] bg-(--background-tint-02) border-l border-(--border-01) text-(--text-04) font-mono text-[13px] font-semibold"
      >
        .md
      </span>
    </div>
  );
}

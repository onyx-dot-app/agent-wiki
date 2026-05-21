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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";

import { AppShell } from "@/components/common/AppShell";
import { Button } from "@/components/common/Button";
import { PageHeader } from "@/components/common/PageHeader";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { ActiveSessionsList } from "@/components/wiki/ActiveSessionsList";
import { RunAgentModal } from "@/components/wiki/RunAgentModal";
import { ShareDialog } from "@/components/wiki/ShareDialog";
import { FolderIcon, FileIcon } from "@/components/wiki/WikiIcons";
import { apiFetch } from "@/lib/api";
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
import { color, radius, shadow } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";
import type { DocumentActivity, DocumentActivityResponse } from "@/types";

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

interface CommitInfo {
  sha: string;
  author: string;
  ts: string;
  message: string;
  body?: string;
}

interface HistoryResponse {
  path: string;
  head_sha: string | null;
  commits: CommitInfo[];
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
    rememberWikiPath("/wiki" + (slugPath ? "/" + slugPath : ""));
  }, [slugPath]);

  if (loading || !user)
    return <main style={{ padding: isMobile ? 16 : 32 }}>Loading…</main>;

  return (
    <AppShell>
      {isFile ? (
        <FileViewer path={slugPath} />
      ) : isNewMode ? (
        <NewDocView dir={slugPath} />
      ) : (
        <Explorer dir={slugPath} />
      )}
    </AppShell>
  );
}

function Explorer({ dir }: { dir: string }) {
  const router = useRouter();
  const isMobile = useIsMobile();
  const { data, error: listError, mutate: mutatePaths } = useSWR<ListResponse>("/wiki");
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
  const [sort, setSort] = useState<"name-asc" | "name-desc" | "recent">("name-asc");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [dragSource, setDragSource] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);

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
      router.push(`/wiki/${fullPath}`);
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
      await apiFetch(`/wiki/file?path=${encodeURIComponent(rel)}`, { method: "DELETE" });
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
      style={{
        padding: isMobile ? "16px 12px" : "24px 32px",
        height: "100vh",
        overflowY: "auto",
      }}
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
            <Button onClick={() => setTriggerModalOpen(true)}>+ Trigger</Button>
            <Button
              onClick={() => {
                setNewName("");
                setCreating((v) => (v === "folder" ? null : "folder"));
              }}
            >
              + New folder
            </Button>
            <Button
              variant="primary"
              onClick={() => router.push(`/wiki/${dir}?new=1`)}
            >
              + New document
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
        <div style={{ fontSize: 12, color: color.text.secondary, marginBottom: 12 }}>{triggerStatus}</div>
      )}

      {creating && (
        <form
          onSubmit={onCreate}
          style={{
            display: "flex",
            gap: 8,
            marginBottom: 16,
            padding: 12,
            background: color.bg.panel,
            border: `1px solid ${color.border.default}`,
            borderRadius: radius.md,
          }}
        >
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="folder-name (or subdir/folder-name)"
            disabled={createBusy}
            style={{
              flex: 1,
              padding: 8,
              border: `1px solid ${color.border.default}`,
              borderRadius: radius.sm,
              fontSize: 14,
            }}
          />
          <Button
            type="submit"
            variant="primary"
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
        <div
          style={{
            padding: 10,
            background: color.state.danger.bg,
            color: color.state.danger.fg,
            borderRadius: radius.sm,
            fontSize: 13,
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      )}

      {subdirs.length === 0 && files.length === 0 && !error && (
        <p style={{ color: color.text.muted, fontSize: 14 }}>
          This folder is empty. Create a document to get started.
        </p>
      )}

      {(subdirs.length > 0 || files.length > 0) && (
        <SortBar value={sort} onChange={setSort} />
      )}

      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
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
                icon={isFile ? <FileIcon /> : <FolderIcon />}
                label={name}
                updatedAt={updated_at}
                href={`/wiki/${childPath}`}
                path={childPath}
                isFile={isFile}
                busy={busyPath === childPath}
                onDelete={() => onDelete(childPath)}
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
    </main>
  );
}

function NewDocView({ dir }: { dir: string }) {
  const router = useRouter();
  const isMobile = useIsMobile();
  const { setDrafting, requestExpand } = useDrafting();
  const [filename, setFilename] = useState("");
  const [draft, setDraft] = useState("");
  const [templates, setTemplates] = useState<DocumentTemplateSummary[] | null>(null);
  const [appliedTemplateId, setAppliedTemplateId] = useState<string | null>(null);
  const [appliedTemplateBody, setAppliedTemplateBody] = useState<string | null>(null);
  const [applyingTemplateId, setApplyingTemplateId] = useState<string | null>(null);
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
      router.push(`/wiki/${fullPath}?new=1`);
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
      style={{
        padding: isMobile ? "16px 12px" : "24px 32px",
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        boxSizing: "border-box",
        gap: 12,
      }}
    >
      <PageHeader
        title="New document"
        actions={
          <>
            <Button
              onClick={() => router.push(`/wiki/${dir}`)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
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
        <div
          style={{
            padding: 10,
            background: color.state.danger.bg,
            color: color.state.danger.fg,
            borderRadius: radius.sm,
            fontSize: 13,
          }}
        >
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
        style={{
          flex: 1,
          minHeight: 0,
          width: "100%",
          boxSizing: "border-box",
          padding: 16,
          border: `1px solid ${color.border.default}`,
          borderRadius: radius.md,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 14,
          lineHeight: 1.6,
          resize: "none",
          outline: "none",
        }}
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
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: 14,
        background: color.bg.panel,
        border: `1px solid ${color.border.default}`,
        borderRadius: radius.md,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: color.text.primary }}>
          Start from a template
        </span>
        <span style={{ fontSize: 12, color: color.text.muted }}>
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
    <div style={{ position: "relative" }}>
      <div
        ref={scrollerRef}
        className="scroll-x-hidden"
        style={{
          display: "flex",
          gap: 8,
          overflowX: "auto",
          scrollSnapType: "x mandatory",
          WebkitOverflowScrolling: "touch",
          paddingBottom: 2, // leave room for focus rings
        }}
      >
        <div style={{ flex: "0 0 auto", scrollSnapAlign: "start", width: CARD_WIDTH }}>
          <TemplateCard
            title="Blank document"
            description="Empty file — just start typing."
            active={blankActive}
            busy={false}
            onClick={onBlank}
          />
        </div>
        {templates.map((t) => (
          <div
            key={t.id}
            style={{ flex: "0 0 auto", scrollSnapAlign: "start", width: CARD_WIDTH }}
          >
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
      style={{
        position: "absolute",
        top: "50%",
        transform: "translateY(-50%)",
        ...(direction === "left" ? { left: 4 } : { right: 4 }),
        width: 28,
        height: 28,
        borderRadius: radius.pill,
        background: color.bg.page,
        border: `1px solid ${color.border.default}`,
        boxShadow: shadow.sm,
        cursor: "pointer",
        color: color.text.secondary,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
      }}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        {direction === "left" ? <path d="M15 18l-6-6 6-6" /> : <path d="M9 6l6 6-6 6" />}
      </svg>
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
      style={{
        textAlign: "left",
        padding: "10px 12px",
        background: active ? color.accent.subtleBg : color.bg.page,
        border: `1px solid ${active ? color.accent.subtleBorder : color.border.default}`,
        borderRadius: radius.sm,
        cursor: busy ? "wait" : "pointer",
        color: color.text.primary,
        // Fill the wrapper (grid cell or strip slot) so adjacent cards
        // align even when their description text differs in length.
        width: "100%",
        height: "100%",
        boxSizing: "border-box",
        minHeight: 64,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        opacity: busy ? 0.7 : 1,
        transition: "background 80ms ease, border-color 80ms ease",
      }}
      onMouseEnter={(e) => {
        if (!active && !busy) {
          e.currentTarget.style.background = color.bg.hover;
          e.currentTarget.style.borderColor = color.border.strong;
        }
      }}
      onMouseLeave={(e) => {
        if (!active && !busy) {
          e.currentTarget.style.background = color.bg.page;
          e.currentTarget.style.borderColor = color.border.default;
        }
      }}
    >
      <div style={{ fontSize: 13, fontWeight: 600 }}>{title}</div>
      {description && (
        <div
          style={{
            fontSize: 12,
            color: color.text.muted,
            overflow: "hidden",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
          }}
        >
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
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        marginBottom: 8,
        fontSize: 12,
        color: color.text.muted,
      }}
    >
      <label htmlFor="wiki-sort">Sort:</label>
      <select
        id="wiki-sort"
        value={value}
        onChange={(e) => onChange(e.target.value as SortMode)}
        style={{
          padding: "4px 8px",
          border: `1px solid ${color.border.default}`,
          borderRadius: radius.sm,
          background: color.bg.page,
          color: color.text.primary,
          fontSize: 12,
        }}
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
      style={{
        display: "flex",
        alignItems: "center",
        padding: "10px 12px",
        borderBottom: `1px solid ${color.border.subtle}`,
        background: dropActive
          ? color.accent.subtleBg
          : hover
            ? color.bg.sunken
            : "transparent",
        outline: dropActive ? `2px solid ${color.accent.bg}` : undefined,
        opacity: busy ? 0.5 : 1,
        // Click is the primary action; drag is secondary. Pointer
        // matches the row's main affordance and lines up with how
        // every other list row in the app feels. Drag still works
        // from anywhere in the row regardless of cursor style.
        cursor: renaming ? "default" : "pointer",
      }}
    >
      <span
        style={{ color: color.text.muted, display: "flex", marginRight: 10 }}
      >
        {icon}
      </span>
      {renaming ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSubmitRename(draft);
          }}
          style={{ display: "flex", flex: 1, gap: 6 }}
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
            style={{
              flex: 1,
              padding: "4px 8px",
              border: `1px solid ${color.border.default}`,
              borderRadius: radius.sm,
              fontSize: 14,
            }}
          />
          <Button
            type="submit"
            size="sm"
            variant="primary"
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
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flex: 1,
            color: color.text.primary,
            fontSize: 14,
          }}
        >
          {label}
        </span>
      )}
      {!renaming && (
        <>
          <span
            style={{
              fontSize: 12,
              color: color.text.faint,
              marginRight: 8,
              whiteSpace: "nowrap",
            }}
          >
            {updatedAt ? formatRelative(updatedAt) : "—"}
          </span>
          <button
            onClick={onStartRename}
            disabled={busy}
            title="Rename"
            aria-label={`Rename ${label}`}
            style={{
              background: "transparent",
              border: "none",
              color: hover ? color.text.secondary : "transparent",
              cursor: busy ? "not-allowed" : "pointer",
              padding: 6,
              display: "flex",
              alignItems: "center",
            }}
          >
            <PencilIcon />
          </button>
          <button
            onClick={onDelete}
            disabled={busy}
            title="Delete"
            aria-label={`Delete ${label}`}
            style={{
              background: "transparent",
              border: "none",
              color: hover ? color.state.danger.fg : "transparent",
              cursor: busy ? "not-allowed" : "pointer",
              padding: 6,
              display: "flex",
              alignItems: "center",
            }}
          >
            <TrashIcon />
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
  const crumbs = [{ label: "Wiki", href: "/wiki", path: "" }];
  segments.forEach((seg, i) => {
    const path = segments.slice(0, i + 1).join("/");
    crumbs.push({ label: seg, href: `/wiki/${path}`, path });
  });
  return (
    <nav
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: 14,
        flexWrap: "wrap",
      }}
    >
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
        const activeStyle: React.CSSProperties = active
          ? {
              background: color.accent.subtleBg,
              outline: `2px solid ${color.accent.bg}`,
              borderRadius: radius.sm,
              padding: "2px 6px",
            }
          : {};
        return (
          <span
            key={c.href}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            {i > 0 && <span style={{ color: color.text.faint }}>/</span>}
            {last ? (
              <span
                style={{ fontWeight: 600, ...activeStyle }}
                {...dropHandlers}
              >
                {c.label}
              </span>
            ) : (
              <Link
                href={c.href}
                style={{
                  color: color.text.primary,
                  textDecoration: "underline",
                  ...activeStyle,
                }}
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
  const [historyError, setHistoryError] = useState<string | null>(null);
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
  const [templates, setTemplates] = useState<DocumentTemplateSummary[] | null>(null);
  const [appliedTemplateBody, setAppliedTemplateBody] = useState<string | null>(null);
  const [appliedTemplateId, setAppliedTemplateId] = useState<string | null>(null);
  const [applyingTemplateId, setApplyingTemplateId] = useState<string | null>(null);

  const loadLatest = useCallback(() => {
    setLoading(true);
    setError(null);
    setEditing(false);
    setViewingSha(null);
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

  const refreshHistory = useCallback(() => {
    setHistoryError(null);
    apiFetch<HistoryResponse>(`/wiki/file/history?path=${encodeURIComponent(path)}`)
      .then((r) => {
        setCommits(r.commits);
        setHeadSha(r.head_sha);
      })
      .catch((e) =>
        setHistoryError(
          e instanceof Error ? e.message : "failed to load history",
        ),
      );
  }, [path]);

  function toggleHistory() {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next && commits === null) refreshHistory();
  }

  async function onPickCommit(sha: string) {
    if (sha === viewingSha) return;
    setLoading(true);
    setError(null);
    setEditing(false);
    try {
      const r = await apiFetch<FileResponse>(
        `/wiki/file?path=${encodeURIComponent(path)}&ref=${encodeURIComponent(sha)}`
      );
      setBody(r.body);
      setDraft(r.body);
      setHeadSha(r.head_sha ?? headSha);
      setViewingSha(sha);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load version");
    } finally {
      setLoading(false);
    }
  }

  const segments = path.split("/");
  const parentSlug = segments.slice(0, -1).join("/");
  const backHref = parentSlug ? `/wiki/${parentSlug}` : "/wiki";
  const currentBasename = segments[segments.length - 1] ?? path;
  const currentBasenameNoExt = currentBasename.replace(/\.md$/i, "");
  const trimmedFilename = filenameDraft.trim().replace(/^\/+|\/+$/g, "");
  const filenameNoExt = trimmedFilename.replace(/\.md$/i, "");
  const filenameValid = !!filenameNoExt && !filenameNoExt.includes("/");
  const renamed =
    editing && filenameValid && filenameNoExt !== currentBasenameNoExt;
  const bodyChanged = editing && draft !== body;
  const dirty = editing && (bodyChanged || renamed);
  const viewingOld = viewingSha !== null && viewingSha !== headSha;

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

  function startEdit() {
    setFilenameDraft(currentBasenameNoExt);
    setError(null);
    setEditing(true);
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
        router.push(`/wiki/${newRel}`);
        return;
      }
      setBody(draft);
      setEditing(false);
      setViewingSha(null);
      // History changed (new commit + possible deprecations) — refetch.
      if (historyOpen) refreshHistory();
      else setCommits(null);
      // Pick up the new head_sha for subsequent edits.
      const fresh = await apiFetch<FileResponse>(
        `/wiki/file?path=${encodeURIComponent(path)}`
      );
      setHeadSha(fresh.head_sha ?? null);
      // The server clears the draft row when the body diverges from
      // the template snapshot — re-sync our context so the chat
      // banner disappears at the same moment.
      await refreshDraftState();
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
    setDraft(body);
    setFilenameDraft(currentBasenameNoExt);
    setEditing(false);
    setError(null);
  }

  return (
    <main
      style={{
        padding: isMobile ? "16px 12px" : "24px 32px",
        height: "100vh",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          // Tighter gap on mobile so wrapped button rows don't waste
          // vertical space; the spacer below still pushes the action
          // buttons onto their own row(s) below the breadcrumbs.
          gap: isMobile ? 8 : 12,
          marginBottom: 16,
          flexWrap: "wrap",
        }}
      >
        <Link
          href={backHref}
          title="Back"
          aria-label="Back"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 32,
            height: 32,
            borderRadius: radius.md,
            border: `1px solid ${color.border.default}`,
            color: color.text.secondary,
            textDecoration: "none",
            flexShrink: 0,
          }}
        >
          <BackIcon />
        </Link>
        <Breadcrumbs segments={segments} />
        <div style={{ flex: 1 }} />
        {!editing && !loading && !error && (
          <>
            <div style={{ display: "flex", gap: 8 }}>
              <Button onClick={() => setRunAgentOpen(true)}>Run Agent</Button>
              <Button onClick={() => setTriggerModalOpen(true)}>
                + Trigger
              </Button>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Button onClick={() => setShareOpen(true)}>Share</Button>
              <Button
                onClick={toggleHistory}
                aria-pressed={historyOpen}
                style={
                  historyOpen
                    ? {
                        background: color.accent.subtleBg,
                        color: color.accent.subtleFg,
                        borderColor: color.accent.subtleBorder,
                      }
                    : undefined
                }
              >
                History
              </Button>
            </div>
            <Button variant="primary" onClick={startEdit}>
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
              variant="primary"
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
          error={agentsError}
          open={agentsOpen}
          onToggle={() => setAgentsOpen((v) => !v)}
        />
      )}

      {!editing && triggerStatus && (
        <div
          style={{
            fontSize: 12,
            color: color.text.secondary,
            marginBottom: 12,
          }}
        >
          {triggerStatus}
        </div>
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

      <ActiveSessionsList wikiPath={path} />

      <RunAgentModal
        open={runAgentOpen}
        onClose={() => setRunAgentOpen(false)}
        wikiPath={path || null}
      />

      {error && (
        <div
          style={{
            padding: 10,
            background: color.state.danger.bg,
            color: color.state.danger.fg,
            borderRadius: radius.sm,
            fontSize: 13,
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      )}

      {viewingOld && !loading && !error && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "8px 12px",
            marginBottom: 12,
            background: color.state.warning.bg,
            border: `1px solid ${color.state.warning.border}`,
            borderRadius: radius.md,
            fontSize: 13,
            color: color.state.warning.fg,
          }}
        >
          <span>
            Viewing an older version
            {viewingSha ? ` (${viewingSha.slice(0, 7)})` : ""}.
            {editing
              ? " Saving will replace the current version and mark the in-between revisions as deprecated."
              : " Click Edit to fork from this version."}
          </span>
          <div style={{ flex: 1 }} />
          <Button size="sm" onClick={loadLatest}>
            Back to latest
          </Button>
        </div>
      )}

      {loading && <p>Loading…</p>}

      {!loading && !error && (
        <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 16 }}>
          <div
            style={{
              flex: 1,
              minWidth: 0,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
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
                    appliedTemplateBody !== null && draft === appliedTemplateBody;
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
                  style={{
                    flex: 1,
                    minHeight: 0,
                    width: "100%",
                    boxSizing: "border-box",
                    padding: 16,
                    border: `1px solid ${color.border.default}`,
                    borderRadius: radius.md,
                    fontFamily:
                      "ui-monospace, SFMono-Regular, Menlo, monospace",
                    fontSize: 14,
                    lineHeight: 1.6,
                    resize: "none",
                    outline: "none",
                  }}
                />
              </>
            ) : (
              <article
                className="markdown"
                style={{ flex: 1, minHeight: 0, overflowY: "auto" }}
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {body}
                </ReactMarkdown>
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
              onPickLatest={loadLatest}
              onClose={() => setHistoryOpen(false)}
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
            style={{
              position: "fixed",
              inset: 0,
              background: color.overlay,
              zIndex: 60,
            }}
          />
          <div
            style={{
              position: "fixed",
              top: 0,
              right: 0,
              bottom: 0,
              width: "min(360px, 100vw)",
              zIndex: 70,
              display: "flex",
              boxShadow: shadow.panel,
            }}
          >
            <HistoryPanel
              commits={commits}
              error={historyError}
              headSha={headSha}
              viewingSha={viewingSha}
              onPick={(sha) => {
                onPickCommit(sha);
                setHistoryOpen(false);
              }}
              onPickLatest={() => {
                loadLatest();
                setHistoryOpen(false);
              }}
              onClose={() => setHistoryOpen(false)}
              fullHeight
            />
          </div>
        </>
      )}
    </main>
  );
}

function HistoryPanel({
  commits,
  error,
  headSha,
  viewingSha,
  onPick,
  onPickLatest,
  onClose,
  fullHeight = false,
}: {
  commits: CommitInfo[] | null;
  error: string | null;
  headSha: string | null;
  viewingSha: string | null;
  onPick: (sha: string) => void;
  onPickLatest: () => void;
  onClose: () => void;
  /** When true (mobile sheet mode), fill the entire host container
   *  edge-to-edge instead of rendering as a fixed-width rounded card. */
  fullHeight?: boolean;
}) {
  const latestActive = viewingSha === null;
  return (
    <aside
      style={{
        width: fullHeight ? "100%" : 320,
        height: fullHeight ? "100%" : undefined,
        flexShrink: 0,
        border: fullHeight ? "none" : `1px solid ${color.border.default}`,
        borderLeft: fullHeight
          ? `1px solid ${color.border.default}`
          : undefined,
        borderRadius: fullHeight ? 0 : radius.md,
        background: color.bg.panel,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          padding: "10px 12px",
          borderBottom: `1px solid ${color.border.subtle}`,
          fontSize: 13,
          fontWeight: 600,
          color: color.text.secondary,
        }}
      >
        <span>History</span>
        <div style={{ flex: 1 }} />
        <button
          onClick={onClose}
          aria-label="Close history"
          style={{
            background: "transparent",
            border: "none",
            color: color.text.muted,
            cursor: "pointer",
            fontSize: 16,
            lineHeight: 1,
            padding: 4,
          }}
        >
          ×
        </button>
      </div>
      <div style={{ overflowY: "auto", flex: 1 }}>
        {error && (
          <div
            style={{ padding: 12, fontSize: 12, color: color.state.danger.fg }}
          >
            {error}
          </div>
        )}
        {!error && commits === null && (
          <div style={{ padding: 12, fontSize: 12, color: color.text.muted }}>
            Loading…
          </div>
        )}
        {!error && commits && commits.length === 0 && (
          <div style={{ padding: 12, fontSize: 12, color: color.text.muted }}>
            No history yet.
          </div>
        )}
        {!error && commits && commits.length > 0 && (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            <CommitRow
              active={latestActive}
              onClick={onPickLatest}
              title="Latest (working tree)"
              subtitle={headSha ? headSha.slice(0, 7) : ""}
              meta=""
            />
            {commits.map((c) => {
              const { url, title: srcTitle } = parseSourceMeta(c.body);
              return (
                <CommitRow
                  key={c.sha}
                  active={!latestActive && viewingSha === c.sha}
                  onClick={() => onPick(c.sha)}
                  title={c.message || "(no message)"}
                  subtitle={`${c.sha.slice(0, 7)} · ${c.author}`}
                  meta={formatTs(c.ts)}
                  sourceUrl={url}
                  sourceTitle={srcTitle}
                />
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}

function CommitRow({
  active,
  onClick,
  title,
  subtitle,
  meta,
  sourceUrl,
  sourceTitle,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  subtitle: string;
  meta: string;
  sourceUrl?: string | null;
  sourceTitle?: string | null;
}) {
  return (
    <li style={{ borderBottom: `1px solid ${color.border.subtle}` }}>
      <button
        onClick={onClick}
        style={{
          width: "100%",
          textAlign: "left",
          padding: "10px 12px 6px",
          background: active ? color.accent.subtleBg : "transparent",
          color: color.text.primary,
          border: "none",
          cursor: "pointer",
          display: "block",
        }}
      >
        <div
          style={{
            fontSize: 13,
            fontWeight: active ? 600 : 500,
            lineHeight: 1.35,
          }}
        >
          {title}
        </div>
        <div style={{ fontSize: 11, color: color.text.muted, marginTop: 4 }}>
          {subtitle}
          {meta ? ` · ${meta}` : ""}
        </div>
      </button>
      {(sourceTitle || sourceUrl) && (
        <div
          style={{
            padding: "0 12px 8px",
            background: active ? color.accent.subtleBg : "transparent",
          }}
        >
          {sourceUrl ? (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: "inline-block",
                fontSize: 11,
                color: color.accent.subtleFg,
                textDecoration: "underline",
                textUnderlineOffset: 2,
                maxWidth: "100%",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {sourceTitle ?? sourceUrl}
            </a>
          ) : (
            <span
              style={{
                display: "inline-block",
                fontSize: 11,
                color: color.text.muted,
                maxWidth: "100%",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {sourceTitle}
            </span>
          )}
        </div>
      )}
    </li>
  );
}

function parseSourceMeta(body?: string): { url: string | null; title: string | null } {
  let url: string | null = null;
  let title: string | null = null;
  for (const line of (body ?? "").split("\n")) {
    if (!url) {
      const m = line.match(/^Source:\s*(\S+)/);
      if (m) url = /^https?:\/\//i.test(m[1]) ? m[1] : null;
    }
    if (!title) {
      const m = line.match(/^Title:\s*(.+)/);
      if (m) title = m[1].trim();
    }
  }
  return { url, title };
}

function formatTs(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const diffMs = d.getTime() - Date.now();
  const past = diffMs <= 0;
  const abs = Math.abs(diffMs);
  const sec = Math.round(abs / 1000);
  let value: string;
  if (sec < 45) value = "just now";
  else if (sec < 90) value = "1m";
  else if (sec < 3600) value = `${Math.round(sec / 60)}m`;
  else if (sec < 86400) value = `${Math.round(sec / 3600)}h`;
  else value = `${Math.round(sec / 86400)}d`;
  if (value === "just now") return value;
  return past ? `${value} ago` : `in ${value}`;
}

function ActiveAgentsBar({
  agents,
  error,
  open,
  onToggle,
}: {
  agents: DocumentActivity[];
  error: string | null;
  open: boolean;
  onToggle: () => void;
}) {
  const count = agents.length;
  const expandable = count > 0;
  return (
    <div
      style={{
        marginBottom: 12,
        border: `1px solid ${color.border.default}`,
        borderRadius: radius.md,
        background: color.bg.panel,
        overflow: "hidden",
      }}
    >
      <button
        onClick={expandable ? onToggle : undefined}
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        style={{
          width: "100%",
          textAlign: "left",
          padding: "8px 12px",
          background: "transparent",
          border: "none",
          cursor: expandable ? "pointer" : "default",
          fontSize: 13,
          color: expandable ? color.text.primary : color.text.muted,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <Chevron open={open} disabled={!expandable} />
        <span style={{ fontWeight: 500 }}>
          {expandable ? "Active agents" : "No agents active"}
        </span>
        {expandable && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "1px 6px",
              borderRadius: radius.pill,
              background: color.accent.subtleBg,
              color: color.accent.subtleFg,
            }}
          >
            {count}
          </span>
        )}
        {error && (
          <span
            style={{
              marginLeft: "auto",
              fontSize: 12,
              color: color.state.danger.fg,
            }}
          >
            {error}
          </span>
        )}
      </button>
      {expandable && open && (
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            borderTop: `1px solid ${color.border.default}`,
            background: color.bg.page,
          }}
        >
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

function Chevron({ open, disabled }: { open: boolean; disabled: boolean }) {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      aria-hidden
      style={{
        flexShrink: 0,
        color: disabled ? color.text.faint : color.text.muted,
        transform: open ? "rotate(90deg)" : "rotate(0deg)",
        transition: "transform 120ms ease",
      }}
    >
      <path
        d="M3 1.5l4 3.5-4 3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
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
      style={{
        padding: "10px 12px",
        borderBottom: isLast ? "none" : `1px solid ${color.border.subtle}`,
        fontSize: 13,
        display: "flex",
        alignItems: "center",
        gap: 10,
        whiteSpace: "nowrap",
        overflow: "hidden",
      }}
    >
      <span
        style={{
          flexShrink: 0,
          fontSize: 10,
          fontWeight: 600,
          padding: "1px 6px",
          borderRadius: radius.xs,
          background: color.accent.subtleBg,
          color: color.accent.subtleFg,
          textTransform: "uppercase",
          letterSpacing: 0.3,
        }}
      >
        {a.activity}
      </span>

      <span
        style={{ fontWeight: 500, color: color.text.primary, flexShrink: 0 }}
      >
        {a.owner_display}
      </span>
      {a.agent_name ? (
        <span style={{ color: color.text.muted, flexShrink: 0 }}>
          · {a.agent_name}
        </span>
      ) : null}

      {a.description ? (
        <span
          style={{
            color: color.text.secondary,
            fontStyle: "italic",
            overflow: "hidden",
            textOverflow: "ellipsis",
            minWidth: 0,
            flex: "1 1 auto",
          }}
          title={a.description}
        >
          “{a.description}”
        </span>
      ) : (
        <span style={{ flex: 1 }} />
      )}

      <span
        style={{ fontSize: 11, color: color.text.faint, flexShrink: 0 }}
        title={`Started ${formatTs(a.registered_at)} · Expires ${formatTs(
          a.expires_at,
        )}`}
      >
        {formatRelative(a.registered_at)} · expires{" "}
        {formatRelative(a.expires_at)}
      </span>
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
    <div
      style={{
        display: "flex",
        alignItems: "stretch",
        border: `1px solid ${color.border.default}`,
        borderRadius: radius.sm,
        background: color.bg.page,
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      {parent && (
        <span
          style={{
            display: "flex",
            alignItems: "center",
            padding: "0 10px",
            background: color.bg.sunken,
            borderRight: `1px solid ${color.border.default}`,
            color: color.text.secondary,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 13,
          }}
        >
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
        style={{
          flex: 1,
          padding: "8px 10px",
          border: "none",
          outline: "none",
          fontSize: 14,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          background: "transparent",
        }}
      />
      <span
        aria-hidden
        style={{
          display: "flex",
          alignItems: "center",
          padding: "0 10px",
          background: color.bg.sunken,
          borderLeft: `1px solid ${color.border.default}`,
          color: color.text.secondary,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 13,
          fontWeight: 600,
        }}
      >
        .md
      </span>
    </div>
  );
}

function BackIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <path d="M19 12H5" />
      <path d="M12 19l-7-7 7-7" />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4z" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    </svg>
  );
}

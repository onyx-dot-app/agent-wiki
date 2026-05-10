"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";

import { AppShell } from "@/components/common/AppShell";
import { Button } from "@/components/common/Button";
import { PageHeader } from "@/components/common/PageHeader";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { RunAgentModal } from "@/components/wiki/RunAgentModal";
import { ShareDialog } from "@/components/wiki/ShareDialog";
import { FolderIcon, FileIcon } from "@/components/wiki/WikiIcons";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
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

  if (loading || !user) return <main style={{ padding: isMobile ? 16 : 32 }}>Loading…</main>;

  return (
    <AppShell>
      {isFile ? <FileViewer path={slugPath} /> : <Explorer dir={slugPath} />}
    </AppShell>
  );
}

function Explorer({ dir }: { dir: string }) {
  const router = useRouter();
  const isMobile = useIsMobile();
  const { data, error: listError, mutate: mutatePaths } = useSWR<ListResponse>("/documents");
  const entries = data?.entries ?? [];
  const [mutationError, setMutationError] = useState<string | null>(null);
  const error = mutationError ?? (listError instanceof Error ? listError.message : null);
  const setError = setMutationError;
  // Force the cache to revalidate from the server. Used after writes
  // (create / delete / move) to pull in the new tree.
  const refresh = useCallback(() => {
    void mutatePaths();
  }, [mutatePaths]);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [creating, setCreating] = useState<"doc" | "folder" | null>(null);
  const [newName, setNewName] = useState("");
  const [createBusy, setCreateBusy] = useState(false);
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
        if (rest.endsWith(".md")) fileList.push({ name: rest, updated_at: e.updated_at });
      } else {
        const name = rest.slice(0, slash);
        const cur = dirMtime.get(name);
        if (!cur || (e.updated_at && e.updated_at > cur)) {
          dirMtime.set(name, e.updated_at);
        }
      }
    }
    const dirList = [...dirMtime.entries()].map(([name, updated_at]) => ({ name, updated_at }));
    const byName = (asc: boolean) => (a: { name: string }, b: { name: string }) =>
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
    setCreateBusy(true);
    setError(null);
    try {
      if (creating === "folder") {
        const folderName = raw.replace(/\/+$/, "");
        const fullPath = (dir ? dir + "/" : "") + folderName;
        await apiFetch("/documents/folder", {
          method: "POST",
          body: JSON.stringify({ path: fullPath }),
        });
        setNewName("");
        setCreating(null);
        refresh();
        router.push(`/wiki/${fullPath}`);
      } else {
        // Strip any user-typed .md so we don't end up with foo.md.md when the
        // suffix adornment also adds it.
        const stripped = raw.replace(/\.md$/i, "");
        if (!stripped) {
          setError("Filename cannot be empty.");
          setCreateBusy(false);
          return;
        }
        const name = stripped + ".md";
        const fullPath = (dir ? dir + "/" : "") + name;
        await apiFetch("/documents/file", {
          method: "PUT",
          body: JSON.stringify({ path: fullPath, body: `# ${name.replace(/\.md$/, "")}\n` }),
        });
        setNewName("");
        setCreating(null);
        refresh();
        router.push(`/wiki/${fullPath}`);
      }
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
      await apiFetch(`/documents/file?path=${encodeURIComponent(rel)}`, { method: "DELETE" });
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
      await apiFetch("/documents/move", {
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
    const finalName = isFile && !trimmed.endsWith(".md") ? trimmed + ".md" : trimmed;
    const newRel = parent ? `${parent}/${finalName}` : finalName;
    if (newRel === rel) {
      setRenaming(null);
      return;
    }
    setBusyPath(rel);
    setError(null);
    try {
      await apiFetch("/documents/move", {
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
    <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", height: "100vh", overflowY: "auto" }}>
      <PageHeader
        title={
          <Breadcrumbs
            segments={segments}
            onDropToCrumb={(crumbPath) => {
              if (dragSource && crumbPath !== dir) onMove(dragSource, crumbPath);
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
              onClick={() => {
                setNewName("");
                setCreating((v) => (v === "folder" ? null : "folder"));
              }}
            >
              + New folder
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setNewName("");
                setCreating((v) => (v === "doc" ? null : "doc"));
              }}
            >
              + New document
            </Button>
          </>
        }
      />

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
          {creating === "doc" ? (
            <div
              style={{
                flex: 1,
                display: "flex",
                alignItems: "stretch",
                border: `1px solid ${color.border.default}`,
                borderRadius: radius.sm,
                background: color.bg.page,
                overflow: "hidden",
              }}
            >
              <input
                autoFocus
                value={newName.replace(/\.md$/i, "")}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="filename (or subdir/filename)"
                disabled={createBusy}
                style={{
                  flex: 1,
                  padding: 8,
                  border: "none",
                  outline: "none",
                  fontSize: 14,
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
          ) : (
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
          )}
          <Button
            type="submit"
            variant="primary"
            disabled={createBusy || !newName.trim()}
          >
            {creating === "folder" ? "Create folder" : "Create document"}
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
        <p style={{ color: color.text.muted, fontSize: 14 }}>This folder is empty. Create a document to get started.</p>
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
                  isFile ? undefined : () => setDropTarget((cur) => (cur === childPath ? null : cur))
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
  const [hover, setHover] = useState(false);
  const [draft, setDraft] = useState(label);

  useEffect(() => {
    if (renaming) setDraft(label);
  }, [renaming, label]);

  return (
    <li
      draggable={!renaming}
      onDragStart={(e) => {
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
        background: dropActive ? color.accent.subtleBg : hover ? color.bg.sunken : "transparent",
        outline: dropActive ? `2px solid ${color.accent.bg}` : undefined,
        opacity: busy ? 0.5 : 1,
        cursor: renaming ? "default" : "grab",
      }}
    >
      <span style={{ color: color.text.muted, display: "flex", marginRight: 10 }}>{icon}</span>
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
          <Button type="submit" size="sm" variant="primary" disabled={busy || !draft.trim()}>
            Save
          </Button>
          <Button type="button" size="sm" onClick={onCancelRename} disabled={busy}>
            Cancel
          </Button>
        </form>
      ) : (
        <Link
          href={href}
          draggable={false}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flex: 1,
            color: color.text.primary,
            textDecoration: "none",
            fontSize: 14,
          }}
        >
          <span>{label}</span>
        </Link>
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
    <nav style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, flexWrap: "wrap" }}>
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
          <span key={c.href} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {i > 0 && <span style={{ color: color.text.faint }}>/</span>}
            {last ? (
              <span style={{ fontWeight: 600, ...activeStyle }} {...dropHandlers}>
                {c.label}
              </span>
            ) : (
              <Link
                href={c.href}
                style={{ color: color.text.primary, textDecoration: "underline", ...activeStyle }}
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
  const isMobile = useIsMobile();
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

  const loadLatest = useCallback(() => {
    setLoading(true);
    setError(null);
    setEditing(false);
    setViewingSha(null);
    apiFetch<FileResponse>(`/documents/file?path=${encodeURIComponent(path)}`)
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

  const refreshAgents = useCallback(() => {
    setAgentsError(null);
    apiFetch<DocumentActivityResponse>(
      `/documents/file/activity?path=${encodeURIComponent(path)}`,
    )
      .then((r) => setAgents(r.agents))
      .catch((e) =>
        setAgentsError(e instanceof Error ? e.message : "failed to load activity"),
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
    apiFetch<HistoryResponse>(`/documents/file/history?path=${encodeURIComponent(path)}`)
      .then((r) => {
        setCommits(r.commits);
        setHeadSha(r.head_sha);
      })
      .catch((e) => setHistoryError(e instanceof Error ? e.message : "failed to load history"));
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
        `/documents/file?path=${encodeURIComponent(path)}&ref=${encodeURIComponent(sha)}`
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
  const renamed = editing && filenameValid && filenameNoExt !== currentBasenameNoExt;
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
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const anchor = target?.closest("a");
      if (!anchor) return;
      const href = anchor.getAttribute("href");
      if (!href || href.startsWith("#")) return;
      if (anchor.target && anchor.target !== "_self") return;
      const url = new URL(href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname) return;
      if (!window.confirm("You have unsaved changes. Discard them and leave?")) {
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
        await apiFetch("/documents/file", {
          method: "PUT",
          body: JSON.stringify({ path, body: draft, ...(baseSha ? { base_sha: baseSha } : {}) }),
        });
      }
      if (renamed) {
        const finalName = filenameNoExt + ".md";
        const newRel = parentSlug ? `${parentSlug}/${finalName}` : finalName;
        await apiFetch("/documents/move", {
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
        `/documents/file?path=${encodeURIComponent(path)}`
      );
      setHeadSha(fresh.head_sha ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
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
              <Button onClick={() => setTriggerModalOpen(true)}>+ Trigger</Button>
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
            <Button variant="primary" onClick={onSave} disabled={saving || !dirty}>
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
        <div style={{ fontSize: 12, color: color.text.secondary, marginBottom: 12 }}>{triggerStatus}</div>
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

      <RunAgentModal open={runAgentOpen} onClose={() => setRunAgentOpen(false)} />

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
            Viewing an older version{viewingSha ? ` (${viewingSha.slice(0, 7)})` : ""}.
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
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
            {editing ? (
              <>
                <FilenameRow
                  parent={parentSlug}
                  value={filenameDraft}
                  onChange={setFilenameDraft}
                  disabled={saving}
                />
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  spellCheck={false}
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
              </>
            ) : (
              <article
                className="markdown"
                style={{ flex: 1, minHeight: 0, overflowY: "auto" }}
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
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
            style={{ position: "fixed", inset: 0, background: color.overlay, zIndex: 60 }}
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
        borderLeft: fullHeight ? `1px solid ${color.border.default}` : undefined,
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
          <div style={{ padding: 12, fontSize: 12, color: color.state.danger.fg }}>{error}</div>
        )}
        {!error && commits === null && (
          <div style={{ padding: 12, fontSize: 12, color: color.text.muted }}>Loading…</div>
        )}
        {!error && commits && commits.length === 0 && (
          <div style={{ padding: 12, fontSize: 12, color: color.text.muted }}>No history yet.</div>
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
            {commits.map((c) => (
              <CommitRow
                key={c.sha}
                active={!latestActive && viewingSha === c.sha}
                onClick={() => onPick(c.sha)}
                title={c.message || "(no message)"}
                subtitle={`${c.sha.slice(0, 7)} · ${c.author}`}
                meta={formatTs(c.ts)}
              />
            ))}
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
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  subtitle: string;
  meta: string;
}) {
  return (
    <li>
      <button
        onClick={onClick}
        style={{
          width: "100%",
          textAlign: "left",
          padding: "10px 12px",
          background: active ? color.accent.subtleBg : "transparent",
          color: color.text.primary,
          border: "none",
          borderBottom: `1px solid ${color.border.subtle}`,
          cursor: "pointer",
          display: "block",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: active ? 600 : 500, lineHeight: 1.35 }}>
          {title}
        </div>
        <div style={{ fontSize: 11, color: color.text.muted, marginTop: 4 }}>
          {subtitle}
          {meta ? ` · ${meta}` : ""}
        </div>
      </button>
    </li>
  );
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
          <span style={{ marginLeft: "auto", fontSize: 12, color: color.state.danger.fg }}>
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
              key={`${a.owner_display}-${a.agent_name ?? ""}-${a.activity}-${i}`}
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

      <span style={{ fontWeight: 500, color: color.text.primary, flexShrink: 0 }}>
        {a.owner_display}
      </span>
      {a.agent_name ? (
        <span style={{ color: color.text.muted, flexShrink: 0 }}>· {a.agent_name}</span>
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
        title={`Started ${formatTs(a.registered_at)} · Expires ${formatTs(a.expires_at)}`}
      >
        {formatRelative(a.registered_at)} · expires {formatRelative(a.expires_at)}
      </span>
    </li>
  );
}

function FilenameRow({
  parent,
  value,
  onChange,
  disabled,
}: {
  parent: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
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
        value={value.replace(/\.md$/i, "")}
        onChange={(e) => onChange(e.target.value)}
        placeholder="filename"
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
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M19 12H5" />
      <path d="M12 19l-7-7 7-7" />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4z" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    </svg>
  );
}

"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";

import { AppShell } from "@/components/common/AppShell";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { RunAgentModal } from "@/components/wiki/RunAgentModal";
import { ShareDialog } from "@/components/wiki/ShareDialog";
import { WikiSearch } from "@/components/wiki/WikiSearch";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { DocumentActivity, DocumentActivityResponse } from "@/types";

interface ListResponse {
  paths: string[];
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

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;

  return (
    <AppShell>
      <div
        style={{
          padding: "16px 32px 0",
          display: "flex",
          alignItems: "center",
          gap: 16,
        }}
      >
        <WikiSearch />
      </div>
      {isFile ? <FileViewer path={slugPath} /> : <Explorer dir={slugPath} />}
    </AppShell>
  );
}

function Explorer({ dir }: { dir: string }) {
  const router = useRouter();
  const { data, error: listError, mutate: mutatePaths } = useSWR<ListResponse>("/documents");
  const paths = data?.paths ?? [];
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
  const [groupOrder, setGroupOrder] = useState<"folders" | "docs">("folders");
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [dragSource, setDragSource] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const { subdirs, files } = useMemo(() => {
    const prefix = dir ? dir + "/" : "";
    const dirSet = new Set<string>();
    const fileList: string[] = [];
    for (const p of paths) {
      if (!p.startsWith(prefix)) continue;
      const rest = p.slice(prefix.length);
      if (!rest) continue;
      const slash = rest.indexOf("/");
      if (slash === -1) {
        if (rest.endsWith(".md")) fileList.push(rest);
      } else {
        dirSet.add(rest.slice(0, slash));
      }
    }
    const cmp = (a: string, b: string) =>
      direction === "asc" ? a.localeCompare(b) : b.localeCompare(a);
    return {
      subdirs: [...dirSet].sort(cmp),
      files: fileList.sort(cmp),
    };
  }, [paths, dir, direction]);

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
    <main style={{ padding: "24px 32px", height: "100vh", overflowY: "auto" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20, gap: 8, flexWrap: "wrap" }}>
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
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => {
              setNewName("");
              setCreating((v) => (v === "folder" ? null : "folder"));
            }}
            style={{
              padding: "8px 14px",
              background: "transparent",
              color: "#374151",
              border: "1px solid #ddd",
              borderRadius: 8,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            + New folder
          </button>
          <button
            onClick={() => {
              setNewName("");
              setCreating((v) => (v === "doc" ? null : "doc"));
            }}
            style={{
              padding: "8px 14px",
              background: "#6366f1",
              color: "white",
              border: "none",
              borderRadius: 8,
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            + New document
          </button>
        </div>
      </header>

      {creating && (
        <form
          onSubmit={onCreate}
          style={{
            display: "flex",
            gap: 8,
            marginBottom: 16,
            padding: 12,
            background: "#f9fafb",
            border: "1px solid #e5e5e5",
            borderRadius: 8,
          }}
        >
          {creating === "doc" ? (
            <div
              style={{
                flex: 1,
                display: "flex",
                alignItems: "stretch",
                border: "1px solid #ddd",
                borderRadius: 6,
                background: "white",
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
                  background: "#f3f4f6",
                  borderLeft: "1px solid #e5e7eb",
                  color: "#4b5563",
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
              style={{ flex: 1, padding: 8, border: "1px solid #ddd", borderRadius: 6, fontSize: 14 }}
            />
          )}
          <button
            type="submit"
            disabled={createBusy || !newName.trim()}
            style={{
              padding: "0 16px",
              background: "#6366f1",
              color: "white",
              border: "none",
              borderRadius: 6,
              cursor: createBusy ? "not-allowed" : "pointer",
              fontWeight: 600,
              fontSize: 13,
              opacity: createBusy ? 0.6 : 1,
            }}
          >
            {creating === "folder" ? "Create folder" : "Create document"}
          </button>
          <button
            type="button"
            onClick={() => {
              setCreating(null);
              setNewName("");
            }}
            style={{
              padding: "0 12px",
              background: "transparent",
              border: "1px solid #ddd",
              borderRadius: 6,
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            Cancel
          </button>
        </form>
      )}

      {error && (
        <div style={{ padding: 10, background: "#fef2f2", color: "#991b1b", borderRadius: 6, fontSize: 13, marginBottom: 12 }}>
          {error}
        </div>
      )}

      {subdirs.length === 0 && files.length === 0 && !error && (
        <p style={{ color: "#888", fontSize: 14 }}>This folder is empty. Create a document to get started.</p>
      )}

      {(subdirs.length > 0 || files.length > 0) && (
        <SortBar
          groupOrder={groupOrder}
          setGroupOrder={setGroupOrder}
          direction={direction}
          setDirection={setDirection}
        />
      )}

      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {(() => {
          const dirEntries = subdirs.map((name) => ({ name, isFile: false }));
          const fileEntries = files.map((name) => ({ name, isFile: true }));
          const ordered =
            groupOrder === "folders"
              ? [...dirEntries, ...fileEntries]
              : [...fileEntries, ...dirEntries];
          return ordered.map(({ name, isFile }) => {
            const childPath = (dir ? dir + "/" : "") + name;
            return (
              <Row
                key={(isFile ? "f:" : "d:") + name}
                icon={isFile ? <FileIcon /> : <FolderIcon />}
                label={name}
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

function SortBar({
  groupOrder,
  setGroupOrder,
  direction,
  setDirection,
}: {
  groupOrder: "folders" | "docs";
  setGroupOrder: (v: "folders" | "docs") => void;
  direction: "asc" | "desc";
  setDirection: (v: "asc" | "desc") => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        marginBottom: 8,
        fontSize: 12,
        color: "#6b7280",
      }}
    >
      <span>Sort:</span>
      <Segmented
        value={groupOrder}
        onChange={setGroupOrder}
        options={[
          { value: "folders", label: "Folders first" },
          { value: "docs", label: "Docs first" },
        ]}
      />
      <Segmented
        value={direction}
        onChange={setDirection}
        options={[
          { value: "asc", label: "A → Z" },
          { value: "desc", label: "Z → A" },
        ]}
      />
    </div>
  );
}

function Segmented<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div
      style={{
        display: "inline-flex",
        border: "1px solid #e5e5e5",
        borderRadius: 6,
        overflow: "hidden",
      }}
    >
      {options.map((opt, i) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            style={{
              padding: "4px 10px",
              background: active ? "#eef2ff" : "white",
              color: active ? "#4338ca" : "#374151",
              border: "none",
              borderLeft: i === 0 ? "none" : "1px solid #e5e5e5",
              cursor: "pointer",
              fontSize: 12,
              fontWeight: active ? 600 : 500,
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function Row({
  icon,
  label,
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
        borderBottom: "1px solid #f1f1f1",
        background: dropActive ? "#eef2ff" : hover ? "#f9fafb" : "transparent",
        outline: dropActive ? "2px solid #6366f1" : undefined,
        opacity: busy ? 0.5 : 1,
        cursor: renaming ? "default" : "grab",
      }}
    >
      <span style={{ color: "#6b7280", display: "flex", marginRight: 10 }}>{icon}</span>
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
            style={{ flex: 1, padding: "4px 8px", border: "1px solid #ddd", borderRadius: 6, fontSize: 14 }}
          />
          <button
            type="submit"
            disabled={busy || !draft.trim()}
            style={{
              padding: "0 10px",
              background: "#6366f1",
              color: "white",
              border: "none",
              borderRadius: 6,
              cursor: busy ? "not-allowed" : "pointer",
              fontSize: 12,
              fontWeight: 600,
            }}
          >
            Save
          </button>
          <button
            type="button"
            onClick={onCancelRename}
            disabled={busy}
            style={{
              padding: "0 10px",
              background: "transparent",
              border: "1px solid #ddd",
              borderRadius: 6,
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            Cancel
          </button>
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
            color: "#111",
            textDecoration: "none",
            fontSize: 14,
          }}
        >
          <span>{label}</span>
        </Link>
      )}
      {!renaming && (
        <>
          <button
            onClick={onStartRename}
            disabled={busy}
            title="Rename"
            aria-label={`Rename ${label}`}
            style={{
              background: "transparent",
              border: "none",
              color: hover ? "#374151" : "transparent",
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
              color: hover ? "#dc2626" : "transparent",
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
        const active = droppable && dropTarget === targetKey;
        const dropHandlers = droppable
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
        const crumbStyle: React.CSSProperties = active
          ? {
              background: "#eef2ff",
              outline: "2px solid #6366f1",
              borderRadius: 6,
              padding: "2px 6px",
            }
          : {};
        return (
          <span key={c.href} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {i > 0 && <span style={{ color: "#9ca3af" }}>/</span>}
            {last ? (
              <span style={{ fontWeight: 600, ...crumbStyle }} {...dropHandlers}>
                {c.label}
              </span>
            ) : (
              <Link
                href={c.href}
                style={{ color: "#6366f1", textDecoration: "none", ...crumbStyle }}
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
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reindexBusy, setReindexBusy] = useState(false);
  const [reindexStatus, setReindexStatus] = useState<string | null>(null);
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

  async function onReindex() {
    setReindexBusy(true);
    setReindexStatus(null);
    try {
      await apiFetch("/documents/reindex", {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      setReindexStatus("Queued reindex job");
    } catch (e) {
      setReindexStatus(e instanceof Error ? e.message : "reindex failed");
    } finally {
      setReindexBusy(false);
    }
  }

  async function onSave() {
    setSaving(true);
    setError(null);
    try {
      const baseSha = viewingSha ?? headSha;
      await apiFetch("/documents/file", {
        method: "PUT",
        body: JSON.stringify({ path, body: draft, ...(baseSha ? { base_sha: baseSha } : {}) }),
      });
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
    setEditing(false);
    setError(null);
  }

  async function onRename() {
    const segs = path.split("/");
    const currentName = segs[segs.length - 1];
    const parent = segs.slice(0, -1).join("/");
    const input = prompt("Rename document to:", currentName);
    if (input === null) return;
    const trimmed = input.trim().replace(/^\/+|\/+$/g, "");
    if (!trimmed || trimmed.includes("/")) {
      setError("Name cannot be empty or contain '/'.");
      return;
    }
    const finalName = trimmed.endsWith(".md") ? trimmed : trimmed + ".md";
    const newRel = parent ? `${parent}/${finalName}` : finalName;
    if (newRel === path) return;
    setError(null);
    try {
      await apiFetch("/documents/move", {
        method: "POST",
        body: JSON.stringify({ old_path: path, new_path: newRel }),
      });
      router.push(`/wiki/${newRel}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "rename failed");
    }
  }

  const segments = path.split("/");
  const parentSlug = segments.slice(0, -1).join("/");
  const backHref = parentSlug ? `/wiki/${parentSlug}` : "/wiki";
  const dirty = editing && draft !== body;
  const viewingOld = viewingSha !== null && viewingSha !== headSha;

  return (
    <main
      style={{
        padding: "24px 32px",
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
          gap: 12,
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
            borderRadius: 8,
            border: "1px solid #e5e5e5",
            color: "#374151",
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
            <button
              onClick={() => setRunAgentOpen(true)}
              style={secondaryBtn}
            >
              Run Agent
            </button>
            <button
              onClick={() => setTriggerModalOpen(true)}
              style={secondaryBtn}
            >
              + Trigger
            </button>
            <button onClick={() => setShareOpen(true)} style={secondaryBtn}>
              Share
            </button>
            <button onClick={onRename} style={secondaryBtn}>
              Rename
            </button>
            <button
              onClick={onReindex}
              disabled={reindexBusy}
              style={{ ...secondaryBtn, opacity: reindexBusy ? 0.6 : 1 }}
            >
              {reindexBusy ? "Queuing…" : "Reindex"}
            </button>
            <button
              onClick={toggleHistory}
              style={{
                ...secondaryBtn,
                ...(historyOpen
                  ? { background: "#eef2ff", color: "#4338ca", borderColor: "#c7d2fe" }
                  : null),
              }}
              aria-pressed={historyOpen}
            >
              History
            </button>
            <button onClick={() => setEditing(true)} style={primaryBtn}>
              Edit
            </button>
          </>
        )}
        {editing && (
          <>
            <button onClick={onCancel} disabled={saving} style={secondaryBtn}>
              Cancel
            </button>
            <button
              onClick={onSave}
              disabled={saving || !dirty}
              style={{ ...primaryBtn, opacity: saving || !dirty ? 0.6 : 1 }}
            >
              {saving ? "Saving…" : "Save"}
            </button>
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

      {!editing && reindexStatus && (
        <div style={{ fontSize: 12, color: "#4b5563", marginBottom: 12 }}>{reindexStatus}</div>
      )}

      {!editing && triggerStatus && (
        <div style={{ fontSize: 12, color: "#4b5563", marginBottom: 12 }}>{triggerStatus}</div>
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
            background: "#fef2f2",
            color: "#991b1b",
            borderRadius: 6,
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
            background: "#fffbeb",
            border: "1px solid #fde68a",
            borderRadius: 8,
            fontSize: 13,
            color: "#92400e",
          }}
        >
          <span>
            Viewing an older version{viewingSha ? ` (${viewingSha.slice(0, 7)})` : ""}.
            {editing
              ? " Saving will replace the current version and mark the in-between revisions as deprecated."
              : " Click Edit to fork from this version."}
          </span>
          <div style={{ flex: 1 }} />
          <button
            onClick={loadLatest}
            style={{ ...secondaryBtn, padding: "4px 10px", fontSize: 12 }}
          >
            Back to latest
          </button>
        </div>
      )}

      {loading && <p>Loading…</p>}

      {!loading && !error && (
        <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 16 }}>
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
            {editing ? (
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
                  border: "1px solid #ddd",
                  borderRadius: 8,
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                  fontSize: 14,
                  lineHeight: 1.6,
                  resize: "none",
                  outline: "none",
                }}
              />
            ) : (
              <article
                className="markdown"
                style={{ flex: 1, minHeight: 0, overflowY: "auto" }}
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
              </article>
            )}
          </div>
          {historyOpen && (
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
}: {
  commits: CommitInfo[] | null;
  error: string | null;
  headSha: string | null;
  viewingSha: string | null;
  onPick: (sha: string) => void;
  onPickLatest: () => void;
  onClose: () => void;
}) {
  const latestActive = viewingSha === null;
  return (
    <aside
      style={{
        width: 320,
        flexShrink: 0,
        border: "1px solid #e5e5e5",
        borderRadius: 8,
        background: "#fafafa",
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
          borderBottom: "1px solid #eee",
          fontSize: 13,
          fontWeight: 600,
          color: "#374151",
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
            color: "#6b7280",
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
          <div style={{ padding: 12, fontSize: 12, color: "#991b1b" }}>{error}</div>
        )}
        {!error && commits === null && (
          <div style={{ padding: 12, fontSize: 12, color: "#6b7280" }}>Loading…</div>
        )}
        {!error && commits && commits.length === 0 && (
          <div style={{ padding: 12, fontSize: 12, color: "#6b7280" }}>No history yet.</div>
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
          background: active ? "#eef2ff" : "transparent",
          color: active ? "#3730a3" : "#111",
          border: "none",
          borderBottom: "1px solid #f1f1f1",
          cursor: "pointer",
          display: "block",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: active ? 600 : 500, lineHeight: 1.35 }}>
          {title}
        </div>
        <div style={{ fontSize: 11, color: active ? "#4338ca" : "#6b7280", marginTop: 4 }}>
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
  const summary = count === 0 ? "No agents active" : `Active agents (${count})`;
  return (
    <div
      style={{
        marginBottom: 12,
        border: "1px solid #e5e5e5",
        borderRadius: 8,
        background: count > 0 ? "#f0f9ff" : "#fafafa",
        overflow: "hidden",
      }}
    >
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{
          width: "100%",
          textAlign: "left",
          padding: "8px 12px",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          fontSize: 13,
          fontWeight: 600,
          color: count > 0 ? "#0c4a6e" : "#6b7280",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span style={{ display: "inline-block", width: 8 }}>{open ? "▾" : "▸"}</span>
        <span>{summary}</span>
        {error && <span style={{ color: "#991b1b", fontWeight: 500 }}> — {error}</span>}
      </button>
      {open && count > 0 && (
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            borderTop: "1px solid #e0f2fe",
            background: "white",
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

function ActiveAgentRow({
  a,
  isLast,
}: {
  a: DocumentActivity;
  isLast: boolean;
}) {
  const wrote = a.activity === "wrote";
  const pillStyle: React.CSSProperties = {
    flexShrink: 0,
    fontSize: 11,
    fontWeight: 600,
    padding: "2px 8px",
    borderRadius: 999,
    textTransform: "uppercase",
    letterSpacing: 0.3,
    background: wrote ? "#fef3c7" : "#dbeafe",
    color: wrote ? "#92400e" : "#1e40af",
  };
  return (
    <li
      style={{
        padding: "8px 12px",
        borderBottom: isLast ? "none" : "1px solid #f1f5f9",
        fontSize: 13,
        display: "flex",
        alignItems: "center",
        gap: 10,
        whiteSpace: "nowrap",
        overflow: "hidden",
      }}
    >
      <span style={pillStyle}>{a.activity}</span>

      <span style={{ fontWeight: 600, color: "#0f172a", flexShrink: 0 }}>
        {a.owner_display}
      </span>
      {a.agent_name ? (
        <span style={{ color: "#64748b", flexShrink: 0 }}>· {a.agent_name}</span>
      ) : null}

      {a.description ? (
        <span
          style={{
            color: "#475569",
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
        style={{ fontSize: 12, color: "#475569", flexShrink: 0 }}
        title={`Started ${formatTs(a.registered_at)}`}
      >
        {formatRelative(a.registered_at)}
      </span>
      <span
        style={{
          fontSize: 11,
          color: "#94a3b8",
          flexShrink: 0,
          paddingLeft: 4,
          borderLeft: "1px solid #e2e8f0",
          marginLeft: 4,
        }}
        title={`Expires ${formatTs(a.expires_at)}`}
      >
        expires {formatRelative(a.expires_at)}
      </span>
    </li>
  );
}

const primaryBtn: React.CSSProperties = {
  padding: "8px 14px",
  background: "#6366f1",
  color: "white",
  border: "none",
  borderRadius: 8,
  cursor: "pointer",
  fontWeight: 600,
  fontSize: 13,
};

const secondaryBtn: React.CSSProperties = {
  padding: "8px 14px",
  background: "transparent",
  color: "#374151",
  border: "1px solid #ddd",
  borderRadius: 8,
  cursor: "pointer",
  fontWeight: 600,
  fontSize: 13,
};

function BackIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M19 12H5" />
      <path d="M12 19l-7-7 7-7" />
    </svg>
  );
}

function FolderIcon() {
  // Filled amber folder with a tab — clearly differentiated from documents.
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
      <path
        d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"
        fill="#fbbf24"
        stroke="#b45309"
        strokeWidth="1.25"
        strokeLinejoin="round"
      />
      <path
        d="M3 9h18v1H3z"
        fill="#b45309"
        opacity="0.35"
      />
    </svg>
  );
}

function FileIcon() {
  // Markdown-style document with a folded corner and content lines, in blue.
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
      <path
        d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"
        fill="#dbeafe"
        stroke="#2563eb"
        strokeWidth="1.25"
        strokeLinejoin="round"
      />
      <path
        d="M14 3v5h5"
        fill="#bfdbfe"
        stroke="#2563eb"
        strokeWidth="1.25"
        strokeLinejoin="round"
      />
      <path
        d="M8 13h8M8 16h8M8 19h5"
        stroke="#2563eb"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
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

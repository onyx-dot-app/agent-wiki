"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AppShell } from "@/components/common/AppShell";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";

interface ListResponse {
  paths: string[];
}

interface FileResponse {
  path: string;
  body: string;
}

export default function WikiRoute() {
  const { user, loading } = useRequireAuth();
  const params = useParams<{ slug?: string[] }>();
  const slugParts = (params?.slug ?? []) as string[];
  const slugPath = slugParts.join("/");
  const isFile = slugPath.endsWith(".md");

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;

  return (
    <AppShell>
      {isFile ? <FileViewer path={slugPath} /> : <Explorer dir={slugPath} />}
    </AppShell>
  );
}

function Explorer({ dir }: { dir: string }) {
  const router = useRouter();
  const [paths, setPaths] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createBusy, setCreateBusy] = useState(false);
  const [groupOrder, setGroupOrder] = useState<"folders" | "docs">("folders");
  const [direction, setDirection] = useState<"asc" | "desc">("asc");

  const refresh = useCallback(() => {
    apiFetch<ListResponse>("/documents")
      .then((r) => setPaths(r.paths))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to list"));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

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
    let name = newName.trim();
    if (!name) return;
    if (!name.endsWith(".md")) name += ".md";
    const fullPath = (dir ? dir + "/" : "") + name;
    setCreateBusy(true);
    setError(null);
    try {
      await apiFetch("/documents/file", {
        method: "PUT",
        body: JSON.stringify({ path: fullPath, body: `# ${name.replace(/\.md$/, "")}\n` }),
      });
      setNewName("");
      setCreating(false);
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
      await apiFetch(`/documents/file?path=${encodeURIComponent(rel)}`, { method: "DELETE" });
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    } finally {
      setBusyPath(null);
    }
  }

  return (
    <main style={{ padding: "24px 32px", height: "100vh", overflowY: "auto" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <Breadcrumbs segments={segments} />
        <button
          onClick={() => setCreating((v) => !v)}
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
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="filename.md (or subdir/filename.md)"
            disabled={createBusy}
            style={{ flex: 1, padding: 8, border: "1px solid #ddd", borderRadius: 6, fontSize: 14 }}
          />
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
            Create
          </button>
          <button
            type="button"
            onClick={() => {
              setCreating(false);
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
                busy={busyPath === childPath}
                onDelete={() => onDelete(childPath)}
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
  busy,
  onDelete,
}: {
  icon: React.ReactNode;
  label: string;
  href: string;
  busy: boolean;
  onDelete: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <li
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "center",
        padding: "10px 12px",
        borderBottom: "1px solid #f1f1f1",
        background: hover ? "#f9fafb" : "transparent",
        opacity: busy ? 0.5 : 1,
      }}
    >
      <Link
        href={href}
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
        <span style={{ color: "#6b7280", display: "flex" }}>{icon}</span>
        <span>{label}</span>
      </Link>
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
    </li>
  );
}

function Breadcrumbs({ segments }: { segments: string[] }) {
  const crumbs = [{ label: "Wiki", href: "/wiki" }];
  segments.forEach((seg, i) => {
    crumbs.push({
      label: seg,
      href: `/wiki/${segments.slice(0, i + 1).join("/")}`,
    });
  });
  return (
    <nav style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, flexWrap: "wrap" }}>
      {crumbs.map((c, i) => {
        const last = i === crumbs.length - 1;
        return (
          <span key={c.href} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {i > 0 && <span style={{ color: "#9ca3af" }}>/</span>}
            {last ? (
              <span style={{ fontWeight: 600 }}>{c.label}</span>
            ) : (
              <Link href={c.href} style={{ color: "#6366f1", textDecoration: "none" }}>
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

  useEffect(() => {
    setLoading(true);
    setError(null);
    setEditing(false);
    apiFetch<FileResponse>(`/documents/file?path=${encodeURIComponent(path)}`)
      .then((r) => {
        setBody(r.body);
        setDraft(r.body);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load"))
      .finally(() => setLoading(false));
  }, [path]);

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
      await apiFetch("/documents/file", {
        method: "PUT",
        body: JSON.stringify({ path, body: draft }),
      });
      setBody(draft);
      setEditing(false);
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

  const segments = path.split("/");
  const parentSlug = segments.slice(0, -1).join("/");
  const backHref = parentSlug ? `/wiki/${parentSlug}` : "/wiki";
  const dirty = editing && draft !== body;

  return (
    <main
      style={{
        padding: "24px 32px",
        height: "100vh",
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
              onClick={() => setTriggerModalOpen(true)}
              style={secondaryBtn}
            >
              + Trigger
            </button>
            <button
              onClick={onReindex}
              disabled={reindexBusy}
              style={{ ...secondaryBtn, opacity: reindexBusy ? 0.6 : 1 }}
            >
              {reindexBusy ? "Queuing…" : "Reindex"}
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

      {loading && <p>Loading…</p>}

      {!loading && !error && editing && (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          style={{
            flex: 1,
            minHeight: 0,
            width: "100%",
            maxWidth: 820,
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
      )}

      {!loading && !error && !editing && (
        <article
          className="markdown"
          style={{ flex: 1, minHeight: 0, overflowY: "auto" }}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </article>
      )}
    </main>
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
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
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

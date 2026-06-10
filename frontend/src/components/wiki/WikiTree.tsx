"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import useSWR, { useSWRConfig } from "swr";

import {
  Button,
  InputTypeIn,
  SidebarTab,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgFileText,
  SvgFolder,
  SvgFolderOpen,
  SvgFolderPlus,
  SvgMoreHorizontal,
  SvgPlus,
} from "@onyx-ai/opal/icons";

import { apiFetch } from "@/lib/api";
import { useActiveFolder } from "@/providers/WikiItemActionsProvider";
import WikiItemMenu from "@/components/wiki/WikiItemActions";
import styles from "@/components/wiki/WikiTree.module.css";

interface Entry {
  path: string;
  updated_at: string;
}

// Persist which folders are expanded so the tree restores on refresh.
const EXPANDED_KEY = "wiki:expandedFolders";

/** Direct child folders + files of `dir`, derived from the flat path list. */
function childrenOf(entries: Entry[], dir: string) {
  const prefix = dir ? dir + "/" : "";
  const folders = new Set<string>();
  const files: string[] = [];
  for (const e of entries) {
    if (!e.path.startsWith(prefix)) continue;
    const rest = e.path.slice(prefix.length);
    if (!rest) continue;
    const slash = rest.indexOf("/");
    if (slash === -1) {
      if (rest.endsWith(".md")) files.push(rest);
    } else {
      folders.add(rest.slice(0, slash));
    }
  }
  return {
    folders: [...folders].sort((a, b) => a.localeCompare(b)),
    files: files.sort((a, b) => a.localeCompare(b)),
  };
}

type IconComponent = React.ComponentProps<typeof SidebarTab>["icon"];

/**
 * One tree row: an OPAL SidebarTab plus the hover-/open-revealed "⋯" menu. The
 * row goes to its `selected` state while its menu is open, matching the mock's
 * selected `Projects` row (657:29879).
 */
function Row({
  icon,
  label,
  path,
  isFolder,
  active = false,
  onClick,
}: {
  icon: IconComponent;
  label: string;
  path: string;
  isFolder: boolean;
  active?: boolean;
  onClick: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className={styles.rowWrap}>
      <div className={styles.tab}>
        <SidebarTab
          variant="sidebar-light"
          icon={icon}
          selected={menuOpen || active}
          onClick={onClick}
        >
          {label}
        </SidebarTab>
      </div>
      {/* "⋯" overlays the leading file/folder icon, revealed on hover. */}
      <div className={styles.rowMenu} data-open={menuOpen || undefined}>
        <WikiItemMenu
          path={path}
          isFolder={isFolder}
          open={menuOpen}
          onOpenChange={setMenuOpen}
        >
          <button
            type="button"
            className={styles.moreBtn}
            aria-label="More actions"
            onClick={(e) => e.stopPropagation()}
          >
            <SvgMoreHorizontal size={16} />
          </button>
        </WikiItemMenu>
      </div>
    </div>
  );
}

/** A folder row that expands inline to show its children, indented one level
 * (8px) via `.nested` — matching the mock's "Folded" group. */
function FolderNode({
  entries,
  dir,
  name,
  onOpenFile,
  expanded,
  onToggle,
  activeFolder,
  onSetActive,
}: {
  entries: Entry[];
  dir: string;
  name: string;
  onOpenFile: (path: string) => void;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  activeFolder: string;
  onSetActive: (path: string) => void;
}) {
  const full = dir ? `${dir}/${name}` : name;
  const open = expanded.has(full);
  const { folders, files } = open
    ? childrenOf(entries, full)
    : { folders: [], files: [] };
  return (
    <>
      <Row
        icon={open ? SvgFolderOpen : SvgFolder}
        label={name}
        path={full}
        isFolder
        active={activeFolder === full}
        onClick={() => {
          onSetActive(full);
          onToggle(full);
        }}
      />
      {open && (folders.length > 0 || files.length > 0) && (
        <div className={styles.nested}>
          {folders.map((f) => (
            <FolderNode
              key={f}
              entries={entries}
              dir={full}
              name={f}
              onOpenFile={onOpenFile}
              expanded={expanded}
              onToggle={onToggle}
              activeFolder={activeFolder}
              onSetActive={onSetActive}
            />
          ))}
          {files.map((f) => (
            <Row
              key={f}
              icon={SvgFileText}
              label={f.replace(/\.md$/, "")}
              path={`${full}/${f}`}
              isFolder={false}
              onClick={() => onOpenFile(`${full}/${f}`)}
            />
          ))}
        </div>
      )}
    </>
  );
}

/**
 * Permanent directory sidebar — a nested, expandable tree. Per-row contextual
 * actions come from the surrounding WikiItemActionsProvider (shared with the
 * recent-pages grid). The list scrolls rather than the panel growing.
 */
export function WikiTree() {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const { data } = useSWR<{ entries: Entry[] }>("/wiki");
  const entries = data?.entries ?? [];
  const { folders, files } = childrenOf(entries, "");
  const openFile = (path: string) => router.push(`/app/wiki/${path}`);

  const [addingFolder, setAddingFolder] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The folder new pages/folders are created inside ("" = wiki root). State
  // lives in the provider so deletes can reset it. Clicking a folder row makes
  // it active; the active row is highlighted (`selected`).
  const { activeFolder, setActiveFolder } = useActiveFolder();
  const activeLabel = activeFolder.split("/").pop() ?? "";

  // Expanded folders, persisted to sessionStorage so they survive a refresh.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(EXPANDED_KEY);
      if (raw) setExpanded(new Set(JSON.parse(raw) as string[]));
    } catch {
      // sessionStorage unavailable (private mode) — start collapsed.
    }
  }, []);
  const toggleFolder = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      try {
        sessionStorage.setItem(EXPANDED_KEY, JSON.stringify([...next]));
      } catch {
        // ignore persistence failure
      }
      return next;
    });
  };

  // Expand a folder (idempotent) so a freshly-created child is visible.
  const expandFolder = (path: string) => {
    if (!path) return;
    setExpanded((prev) => {
      if (prev.has(path)) return prev;
      const next = new Set(prev).add(path);
      try {
        sessionStorage.setItem(EXPANDED_KEY, JSON.stringify([...next]));
      } catch {
        // ignore persistence failure
      }
      return next;
    });
  };

  const refresh = () => void mutate("/wiki");

  // New pages route to NewDocView for the active folder; new folders create
  // inside it. Both fall back to the wiki root when nothing is active.
  const newPage = () =>
    router.push(
      activeFolder ? `/app/wiki/${activeFolder}?new=1` : "/app/wiki?new=1",
    );

  async function createFolder(e: FormEvent) {
    e.preventDefault();
    const name = folderName.trim().replace(/^\/+|\/+$/g, "");
    if (!name) return;
    const path = activeFolder ? `${activeFolder}/${name}` : name;
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/wiki/folder", {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      setFolderName("");
      setAddingFolder(false);
      expandFolder(activeFolder);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "create failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <Text font="secondary-body" color="text-03">
          Directory
        </Text>
        <div className={styles.actions}>
          <Button
            prominence="tertiary"
            size="sm"
            icon={SvgPlus}
            tooltip={activeFolder ? `New page in ${activeLabel}` : "New page"}
            onClick={newPage}
          />
          <Button
            prominence="tertiary"
            size="sm"
            icon={SvgFolderPlus}
            tooltip={
              activeFolder ? `New folder in ${activeLabel}` : "New folder"
            }
            onClick={() => setAddingFolder((v) => !v)}
          />
        </div>
      </div>

      {addingFolder && (
        <form className={styles.folderForm} onSubmit={createFolder}>
          <InputTypeIn
            value={folderName}
            onChange={(e) => setFolderName(e.target.value)}
            placeholder={
              activeFolder
                ? `folder-name (in ${activeLabel})`
                : "folder-name (or subdir/folder-name)"
            }
            aria-label="New folder name"
            autoFocus
          />
        </form>
      )}
      {error && <div className={styles.folderError}>{error}</div>}

      <div className={styles.list}>
        {folders.map((f) => (
          <FolderNode
            key={f}
            entries={entries}
            dir=""
            name={f}
            onOpenFile={openFile}
            expanded={expanded}
            onToggle={toggleFolder}
            activeFolder={activeFolder}
            onSetActive={setActiveFolder}
          />
        ))}
        {files.map((f) => (
          <Row
            key={f}
            icon={SvgFileText}
            label={f.replace(/\.md$/, "")}
            path={f}
            isFolder={false}
            onClick={() => openFile(f)}
          />
        ))}
        {folders.length === 0 && files.length === 0 && (
          <div className={styles.empty}>
            <Text font="secondary-body" color="text-03">
              No pages yet
            </Text>
          </div>
        )}
      </div>
    </div>
  );
}

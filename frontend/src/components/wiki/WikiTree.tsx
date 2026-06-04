"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type FormEvent,
} from "react";
import { createPortal } from "react-dom";
import useSWR, { useSWRConfig } from "swr";

import {
  Button,
  Divider,
  InputTypeIn,
  LineItemButton,
  Popover,
  SidebarTab,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgEdit,
  SvgFileText,
  SvgFolder,
  SvgFolderIn,
  SvgFolderOpen,
  SvgFolderPlus,
  SvgLink,
  SvgMoreHorizontal,
  SvgPlus,
  SvgShare,
  SvgSparkle,
  SvgTrash,
} from "@onyx-ai/opal/icons";

import { apiFetch } from "@/lib/api";
import { RunAgentPanel } from "./RunAgentPanel";
import { ShareDialog } from "./ShareDialog";
import { MoveModal, RenameModal } from "./WikiItemModals";
import styles from "./WikiTree.module.css";

interface Entry {
  path: string;
  updated_at: string;
}

// Persist which folders are expanded so the tree restores on refresh.
const EXPANDED_KEY = "wiki:expandedFolders";

/** Per-row contextual-menu actions, provided once by WikiTree and consumed by
 * every RowMenu via context (avoids drilling through the recursive tree). */
interface RowActions {
  share: (path: string) => void;
  rename: (path: string) => void;
  move: (path: string) => void;
  copyLink: (path: string) => void;
  launchAgent: (path: string) => void;
  remove: (path: string, isFolder: boolean) => void;
}
const ActionsContext = createContext<RowActions | null>(null);

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

/** Every folder path in the tree (plus root ""), for the move-destination list. */
function collectFolders(entries: Entry[]): string[] {
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

type IconComponent = React.ComponentProps<typeof SidebarTab>["icon"];

/**
 * Per-row "⋯" actions menu (contextual menu node 657:41564). Spec: 160px wide,
 * compact line items, two dividers, Delete in danger red. Folders omit
 * "Launch Agent" (it adds a doc to an agent run). The trigger stops propagation
 * so it doesn't navigate/expand the row.
 */
function RowMenu({
  path,
  isFolder,
  open,
  onOpenChange,
}: {
  path: string;
  isFolder: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const actions = useContext(ActionsContext);
  const run = (fn?: (p: string) => void) => () => {
    onOpenChange(false);
    fn?.(path);
  };
  return (
    <div className={styles.rowMenu} data-open={open || undefined}>
      <Popover open={open} onOpenChange={onOpenChange}>
        <Popover.Trigger asChild>
          <button
            type="button"
            className={styles.moreBtn}
            aria-label="More actions"
            onClick={(e) => e.stopPropagation()}
          >
            <SvgMoreHorizontal size={16} />
          </button>
        </Popover.Trigger>
        <Popover.Content align="start" sideOffset={4} width="fit">
          <div className={styles.menu}>
            <LineItemButton
              variant="body"
              sizePreset="main-ui"
              icon={SvgShare}
              title="Share"
              onClick={run(actions?.share)}
            />
            <LineItemButton
              variant="body"
              sizePreset="main-ui"
              icon={SvgEdit}
              title="Rename"
              onClick={run(actions?.rename)}
            />
            <LineItemButton
              variant="body"
              sizePreset="main-ui"
              icon={SvgFolderIn}
              title="Move"
              onClick={run(actions?.move)}
            />
            <Divider />
            <LineItemButton
              variant="body"
              sizePreset="main-ui"
              icon={SvgLink}
              title="Copy Link"
              onClick={run(actions?.copyLink)}
            />
            {!isFolder && (
              <LineItemButton
                variant="body"
                sizePreset="main-ui"
                icon={SvgSparkle}
                title="Launch Agent"
                onClick={run(actions?.launchAgent)}
              />
            )}
            <Divider />
            <span className={styles.danger}>
              <LineItemButton
                variant="body"
                sizePreset="main-ui"
                icon={SvgTrash}
                title="Delete"
                onClick={() => {
                  onOpenChange(false);
                  actions?.remove(path, isFolder);
                }}
              />
            </span>
          </div>
        </Popover.Content>
      </Popover>
    </div>
  );
}

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
  onClick,
}: {
  icon: IconComponent;
  label: string;
  path: string;
  isFolder: boolean;
  onClick: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className={styles.rowWrap}>
      <SidebarTab
        variant="sidebar-light"
        icon={icon}
        selected={menuOpen}
        onClick={onClick}
      >
        {label}
      </SidebarTab>
      <RowMenu
        path={path}
        isFolder={isFolder}
        open={menuOpen}
        onOpenChange={setMenuOpen}
      />
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
}: {
  entries: Entry[];
  dir: string;
  name: string;
  onOpenFile: (path: string) => void;
  expanded: Set<string>;
  onToggle: (path: string) => void;
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
        onClick={() => onToggle(full)}
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
 * Permanent directory sidebar — a nested, expandable tree. Owns the per-row
 * contextual-menu actions (share / rename / move / copy link / launch agent /
 * delete), rendering the reused ShareDialog + RunAgentPanel and the Rename /
 * Move modals once. The list scrolls rather than the panel growing.
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

  // Contextual-menu targets — each holds the path of the row that opened it.
  const [sharePath, setSharePath] = useState<string | null>(null);
  const [renamePath, setRenamePath] = useState<string | null>(null);
  const [movePath, setMovePath] = useState<string | null>(null);
  const [agentPath, setAgentPath] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

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

  const refresh = () => void mutate("/wiki");

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2000);
    return () => clearTimeout(t);
  }, [toast]);

  const actions: RowActions = {
    share: setSharePath,
    rename: setRenamePath,
    move: setMovePath,
    launchAgent: setAgentPath,
    copyLink: (path) => {
      const encoded = path.split("/").map(encodeURIComponent).join("/");
      const url = `${window.location.origin}/app/wiki/${encoded}`;
      navigator.clipboard
        .writeText(url)
        .then(() => setToast("Link copied"))
        .catch(() => setToast("Couldn't copy link"));
    },
    remove: async (path, isFolder) => {
      const label = path.replace(/\.md$/, "").split("/").pop() ?? path;
      const message = isFolder
        ? `Delete folder "${label}" and everything in it? This cannot be undone.`
        : `Delete ${label}? This cannot be undone.`;
      if (!window.confirm(message)) return;
      try {
        await apiFetch(`/wiki/file?path=${encodeURIComponent(path)}`, {
          method: "DELETE",
        });
        refresh();
      } catch (e) {
        setToast(e instanceof Error ? e.message : "Delete failed");
      }
    },
  };

  async function createFolder(e: FormEvent) {
    e.preventDefault();
    const name = folderName.trim().replace(/\/+$/, "");
    if (!name) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/wiki/folder", {
        method: "POST",
        body: JSON.stringify({ path: name }),
      });
      setFolderName("");
      setAddingFolder(false);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "create failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ActionsContext.Provider value={actions}>
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
              tooltip="New page"
              onClick={() => router.push("/app/wiki?new=1")}
            />
            <Button
              prominence="tertiary"
              size="sm"
              icon={SvgFolderPlus}
              tooltip="New folder"
              onClick={() => setAddingFolder((v) => !v)}
            />
          </div>
        </div>

        {addingFolder && (
          <form className={styles.folderForm} onSubmit={createFolder}>
            <InputTypeIn
              value={folderName}
              onChange={(e) => setFolderName(e.target.value)}
              placeholder="folder-name (or subdir/folder-name)"
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

      {/* Overlays are portaled to <body> so they escape the sticky sidebar's
          stacking context (otherwise the page content paints over the scrim). */}
      {typeof document !== "undefined" &&
        createPortal(
          <>
            {sharePath !== null && (
              <ShareDialog
                path={sharePath}
                open
                onClose={() => setSharePath(null)}
              />
            )}
            {renamePath !== null && (
              <RenameModal
                path={renamePath}
                onClose={() => setRenamePath(null)}
                onDone={() => {
                  setRenamePath(null);
                  refresh();
                }}
              />
            )}
            {movePath !== null && (
              <MoveModal
                path={movePath}
                folders={collectFolders(entries)}
                onClose={() => setMovePath(null)}
                onDone={() => {
                  setMovePath(null);
                  refresh();
                }}
              />
            )}
            {/* RunAgentPanel renders its own scrim, so no separate backdrop. */}
            <RunAgentPanel
              open={agentPath !== null}
              onClose={() => setAgentPath(null)}
              wikiPath={agentPath}
            />
            {toast && <div className={styles.toast}>{toast}</div>}
          </>,
          document.body,
        )}
    </ActionsContext.Provider>
  );
}

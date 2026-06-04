"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import useSWR, { useSWRConfig } from "swr";

import { Divider, LineItemButton, Popover } from "@onyx-ai/opal/components";
import {
  SvgEdit,
  SvgFolderIn,
  SvgLink,
  SvgShare,
  SvgSparkle,
  SvgTrash,
} from "@onyx-ai/opal/icons";

import { apiFetch } from "@/lib/api";
import { RunAgentPanel } from "./RunAgentPanel";
import { ShareDialog } from "./ShareDialog";
import { MoveModal, RenameModal } from "./WikiItemModals";
import styles from "./WikiItemActions.module.css";

interface Entry {
  path: string;
  updated_at: string;
}

/** Per-item contextual-menu actions, owned by the provider and consumed by
 * every WikiItemMenu via context (sidebar tree rows + recent-page cards). */
interface RowActions {
  share: (path: string) => void;
  rename: (path: string) => void;
  move: (path: string) => void;
  copyLink: (path: string) => void;
  launchAgent: (path: string) => void;
  remove: (path: string, isFolder: boolean) => void;
}

const ActionsContext = createContext<RowActions | null>(null);
export function useRowActions(): RowActions {
  const ctx = useContext(ActionsContext);
  if (!ctx)
    throw new Error(
      "useRowActions must be used within WikiItemActionsProvider",
    );
  return ctx;
}

/** The folder new pages/folders are created inside ("" = wiki root). Lives here
 * (not in WikiTree) so deletes/renames/moves — owned by this provider — can
 * keep it coherent. */
interface ActiveFolder {
  activeFolder: string;
  setActiveFolder: (path: string) => void;
}
const ActiveFolderContext = createContext<ActiveFolder | null>(null);
export function useActiveFolder(): ActiveFolder {
  const ctx = useContext(ActiveFolderContext);
  if (!ctx)
    throw new Error(
      "useActiveFolder must be used within WikiItemActionsProvider",
    );
  return ctx;
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

/**
 * Per-item "⋯" actions menu (contextual menu node 657:41564). Spec: 160px wide,
 * compact line items, dividers, Delete in danger red. The first item navigates
 * to the page (file) or folder view. Folders omit "Launch Agent" (it adds a doc
 * to an agent run). The caller supplies the trigger as `children`.
 */
export function WikiItemMenu({
  path,
  isFolder,
  open,
  onOpenChange,
  align = "start",
  children,
}: {
  path: string;
  isFolder: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  align?: "start" | "center" | "end";
  children: ReactNode;
}) {
  const actions = useRowActions();
  const run = (fn: (p: string) => void) => () => {
    onOpenChange(false);
    fn(path);
  };
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <Popover.Trigger asChild>{children}</Popover.Trigger>
      <Popover.Content align={align} sideOffset={4} width="fit">
        <div className={styles.menu}>
          <LineItemButton
            variant="body"
            sizePreset="main-ui"
            icon={SvgShare}
            title="Share"
            onClick={run(actions.share)}
          />
          <LineItemButton
            variant="body"
            sizePreset="main-ui"
            icon={SvgEdit}
            title="Rename"
            onClick={run(actions.rename)}
          />
          <LineItemButton
            variant="body"
            sizePreset="main-ui"
            icon={SvgFolderIn}
            title="Move"
            onClick={run(actions.move)}
          />
          <Divider />
          <LineItemButton
            variant="body"
            sizePreset="main-ui"
            icon={SvgLink}
            title="Copy Link"
            onClick={run(actions.copyLink)}
          />
          {!isFolder && (
            <LineItemButton
              variant="body"
              sizePreset="main-ui"
              icon={SvgSparkle}
              title="Launch Agent"
              onClick={run(actions.launchAgent)}
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
                actions.remove(path, isFolder);
              }}
            />
          </span>
        </div>
      </Popover.Content>
    </Popover>
  );
}

/**
 * Owns the contextual-menu actions (go to / share / rename / move / copy link /
 * launch agent / delete) and renders the reused ShareDialog + RunAgentPanel and
 * the Rename / Move modals once, portaled to <body>. Wrap any surface that hosts
 * a WikiItemMenu (the directory sidebar and the recent-pages grid) in this so
 * they share one set of overlays.
 */
export function WikiItemActionsProvider({ children }: { children: ReactNode }) {
  const { mutate } = useSWRConfig();
  const { data } = useSWR<{ entries: Entry[] }>("/wiki");
  const entries = data?.entries ?? [];

  const [sharePath, setSharePath] = useState<string | null>(null);
  const [renamePath, setRenamePath] = useState<string | null>(null);
  const [movePath, setMovePath] = useState<string | null>(null);
  const [agentPath, setAgentPath] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [activeFolder, setActiveFolder] = useState("");

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2000);
    return () => clearTimeout(t);
  }, [toast]);

  // Revalidate the tree and every recent-pages grid after a write.
  const refresh = () => {
    void mutate("/wiki");
    void mutate(
      (key) => typeof key === "string" && key.startsWith("/wiki/recent"),
    );
  };

  // Keep the active folder pointing at the right path after a rename/move of it
  // (or an ancestor). No-op for file renames/moves (active folder never .md).
  const remapActiveFolder = (oldPath: string, newPath: string) =>
    setActiveFolder((prev) =>
      prev === oldPath
        ? newPath
        : prev.startsWith(`${oldPath}/`)
          ? newPath + prev.slice(oldPath.length)
          : prev,
    );

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
        // Clear the active folder if it (or an ancestor) was just deleted.
        setActiveFolder((prev) =>
          prev === path || prev.startsWith(`${path}/`) ? "" : prev,
        );
        refresh();
      } catch (e) {
        setToast(e instanceof Error ? e.message : "Delete failed");
      }
    },
  };

  return (
    <ActionsContext.Provider value={actions}>
      <ActiveFolderContext.Provider value={{ activeFolder, setActiveFolder }}>
        {children}
      </ActiveFolderContext.Provider>

      {/* Overlays are portaled to <body> so they escape any sticky/sidebar
          stacking context (otherwise page content paints over the scrim). */}
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
                onDone={(newPath) => {
                  remapActiveFolder(renamePath, newPath);
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
                onDone={(newPath) => {
                  remapActiveFolder(movePath, newPath);
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

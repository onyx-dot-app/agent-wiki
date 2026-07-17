"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import useSWR from "swr";

import { apiFetch } from "@/lib/api";
import { revalidateWiki, shareableWikiUrl } from "@/lib/wikiHref";
import { RunAgentPanel } from "@/components/wiki/RunAgentPanel";
import { ShareDialog } from "@/components/wiki/ShareDialog";
import { MoveModal, RenameModal } from "@/components/wiki/WikiItemModals";

interface Entry {
  path: string;
  updated_at: string;
}

interface RowActions {
  share: (path: string) => void;
  rename: (path: string) => void;
  move: (path: string) => void;
  copyLink: (path: string) => void;
  launchAgent: (path: string) => void;
  remove: (path: string, isFolder: boolean) => void;
  /** Route to the new-page flow scoped to a folder ("" = wiki root). */
  newPage: (dir: string) => void;
  /** Start the tree's inline folder-create row inside `dir` ("" = wiki root). */
  newFolder: (dir: string) => void;
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

interface FolderCreate {
  /** Folder the inline create-row is open in, null when closed. "" = wiki root. */
  creatingIn: string | null;
  setCreatingIn: (dir: string | null) => void;
}
const FolderCreateContext = createContext<FolderCreate | null>(null);
export function useFolderCreate(): FolderCreate {
  const ctx = useContext(FolderCreateContext);
  if (!ctx)
    throw new Error(
      "useFolderCreate must be used within WikiItemActionsProvider",
    );
  return ctx;
}

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
 * Owns the contextual-menu actions (share / rename / move / copy link /
 * launch agent / delete) and renders the reused ShareDialog + RunAgentPanel
 * and the Rename / Move modals once, portaled to <body>. Wrap any surface
 * that hosts a WikiItemMenu (the directory sidebar and the recent-pages grid)
 * in this so they share one set of overlays.
 */
interface WikiItemActionsProviderProps {
  children: ReactNode;
  active?: boolean;
}

export function WikiItemActionsProvider({
  children,
  active = true,
}: WikiItemActionsProviderProps) {
  const router = useRouter();
  const { data } = useSWR<{ entries: Entry[] }>(active ? "/wiki" : null);
  const entries = data?.entries ?? [];

  const [sharePath, setSharePath] = useState<string | null>(null);
  const [renamePath, setRenamePath] = useState<string | null>(null);
  const [movePath, setMovePath] = useState<string | null>(null);
  const [agentPath, setAgentPath] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [activeFolder, setActiveFolder] = useState("");
  const [creatingIn, setCreatingIn] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2000);
    return () => clearTimeout(t);
  }, [toast]);

  // Revalidate every wiki cache the tree and open document read, after a
  // rename / move / delete.
  const refresh = () => void revalidateWiki();

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
    newPage: (dir) =>
      router.push(dir ? `/app/wiki/${dir}?new=1` : "/app/wiki?new=1"),
    newFolder: setCreatingIn,
    copyLink: async (path) => {
      // Share the durable id-based URL so the link survives a later
      // rename/move (falls back to a path URL only when the page genuinely
      // has no live id). A transient resolve failure rejects rather than
      // copying a fragile link, so report it instead of a false success.
      let url: string;
      try {
        url = await shareableWikiUrl(path);
      } catch {
        setToast("Couldn't copy link");
        return;
      }
      navigator.clipboard
        .writeText(url)
        .then(() => setToast("Link copied"))
        .catch(() => setToast("Couldn't copy link"));
    },
    remove: async (path, isFolder) => {
      const label = path.replace(/\.md$/, "").split("/").pop() ?? path;
      const message = isFolder
        ? `Delete folder "${label}" and everything in it? It will be moved to Trash, where you can restore it.`
        : `Delete ${label}? It will be moved to Trash, where you can restore it.`;
      if (!window.confirm(message)) return;
      try {
        await apiFetch(`/wiki/file?path=${encodeURIComponent(path)}`, {
          method: "DELETE",
        });
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
        <FolderCreateContext.Provider value={{ creatingIn, setCreatingIn }}>
          {children}
        </FolderCreateContext.Provider>
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
            <RunAgentPanel
              open={agentPath !== null}
              onClose={() => setAgentPath(null)}
              wikiPath={agentPath}
            />
            {toast && (
              <div className="fixed bottom-8 left-1/2 z-(--z-toast) -translate-x-1/2 rounded-(--radius-08) bg-(--background-tint-04) px-3.5 py-2 text-[13px] text-text-05 shadow-(--shadow-popover)">
                {toast}
              </div>
            )}
          </>,
          document.body,
        )}
    </ActionsContext.Provider>
  );
}

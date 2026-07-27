"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  Button,
  Divider,
  EmptyMessageCard,
  IconContainer,
  InputTypeIn,
  LineItemButton,
  MessageCard,
  Popover,
  PopoverMenu,
  Text,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { cn } from "@onyx-ai/opal/utils";
import {
  SvgAlertTriangle,
  SvgArrowUpDown,
  SvgCheck,
  SvgChevronLeft,
  SvgDocFile,
  SvgFolder,
  SvgFolderPlus,
  SvgShare,
  SvgSidebar,
  SvgMoreHorizontal,
  SvgPlus,
  SvgTrash,
  SvgWorkflow,
  SvgZap,
} from "@onyx-ai/opal/icons";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { TriggerPanel } from "@/components/triggers/TriggerPanel";
import { WikiHome } from "@/components/wiki/WikiHome";
import { ShareDialog } from "@/components/wiki/ShareDialog";
import { StartNewPage } from "@/components/wiki/StartNewPage";
import { UpdatePolicyPanel } from "@/components/wiki/UpdatePolicyPanel";
import { DocPanel, type DocPanelTab } from "@/components/wiki/DocPanel";
import { SuggestionsCard } from "@/components/wiki/SuggestionsCard";
import { WatchingPanel } from "@/components/wiki/WatchingPanel";
import {
  AnchoredPanel,
  AutoGlyph,
  PolicyPopover,
} from "@/components/wiki/policyPanels";
import { useProposalsByPath } from "@/lib/autoOrganize";
import type { Trigger } from "@/lib/triggers";
import WikiItemMenu from "@/components/wiki/WikiItemActions";
import { useRowActions } from "@/providers/WikiItemActionsProvider";
import { apiFetch, ApiError } from "@/lib/api";
import { isDocId, wikiHref, wikiPath, revalidateWiki } from "@/lib/wikiHref";
import { formatRelative } from "@/lib/format";
import { restoreTrashed } from "@/lib/trash";
import {
  FileView,
  TemplateGallery,
  collectFolders,
  DestinationSelect,
  FilenameRow,
} from "@/views/wiki/FileView";
import { AI_DRAFT_KEY } from "@/lib/wiki/constants";
import { pageTitle, updateWarnLevel } from "@/lib/wiki/utils";
import {
  useUpdateHealth,
  useWikiTree,
  useDeletedTombstone,
  useDocIdResolve,
  usePathToId,
} from "@/lib/wiki/hooks";
import { useRequireAuth } from "@/lib/auth";
import { useHeaderActionsHost } from "@/providers/WikiHeaderActionsProvider";
import { useDrafting } from "@/lib/drafting";
import { rememberWikiPath } from "@/lib/lastViewed";
import { recordRecentDoc } from "@/lib/recents";
import {
  getTemplate,
  listTemplateSummaries,
  setDraftTemplate,
  type DocumentTemplateSummary,
} from "@/lib/templates";
import { relativeTime } from "@/lib/time";
import { useIsMobile } from "@/lib/viewport";

interface WikiUnknownLinkProps {
  status?: number;
}

/** A wiki URL that no longer points at a live doc — an unknown/expired id, or
 * a page that was deleted but is no longer in Trash (purged). A deleted page
 * still in Trash renders {@link WikiTombstone} instead. */
function WikiUnknownLink({ status }: WikiUnknownLinkProps) {
  const router = useRouter();
  return (
    <main className="flex h-full items-center justify-center p-8 pb-[16vh]">
      <div className="flex flex-col items-center gap-4">
        <EmptyMessageCard
          sizePreset="main-ui"
          icon={SvgAlertTriangle}
          title="This page isn't available"
          description={
            status === 404
              ? "The link may be broken, or the page was removed."
              : "The page it pointed to may have been moved or removed."
          }
        />
        <Button onClick={() => router.push("/app/wiki")}>
          Go to wiki home
        </Button>
      </div>
    </main>
  );
}

interface WikiTombstoneProps {
  path: string;
}

/** Tombstone for a deleted page/folder reached via its id URL: shows who/when
 * and offers Restore. Falls back to {@link WikiUnknownLink} when the item is no
 * longer in Trash (purged, or deleted before Trash shipped). */
function WikiTombstone({ path }: WikiTombstoneProps) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const { entry, error, isLoading } = useDeletedTombstone(path);

  if (isLoading)
    return (
      <main className="p-8">
        <LoadingSpinner center />
      </main>
    );
  if (error || !entry)
    return <WikiUnknownLink status={(error as ApiError)?.status} />;

  const label = pageTitle(entry.path);
  const restore = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await restoreTrashed(entry.trash_id);
      // Land on the restored page; the route resolves the path → id URL.
      // wikiPath encodes each segment (paths may contain spaces, #, %, …).
      router.push(wikiPath(res.path));
    } catch (e) {
      setErr(
        e instanceof ApiError && e.status === 409
          ? `A page already exists at ${entry.path}.`
          : e instanceof Error
            ? e.message
            : "Restore failed.",
      );
      setBusy(false);
    }
  };

  return (
    <main className="flex h-full items-center justify-center p-8 pb-[16vh]">
      <div className="flex flex-col items-center gap-4">
        <EmptyMessageCard
          sizePreset="main-ui"
          icon={SvgTrash}
          title={`"${label}" was deleted`}
          description={`Deleted by ${entry.trashed_by}${
            entry.trashed_at ? ` · ${formatRelative(entry.trashed_at)}` : ""
          }. Restore it to bring it back to ${entry.path}.`}
        />
        {err && <MessageCard variant="error" title={err} />}
        <div className="flex items-center gap-2">
          <Button
            prominence="secondary"
            onClick={() => router.push("/app/wiki")}
          >
            Go to wiki home
          </Button>
          {entry.can_restore && (
            <Button onClick={() => void restore()} disabled={busy}>
              {busy ? "Restoring…" : "Restore"}
            </Button>
          )}
        </div>
      </div>
    </main>
  );
}

interface ExplorerProps {
  dir: string;
}

function Explorer({ dir }: ExplorerProps) {
  const router = useRouter();
  const isMobile = useIsMobile();
  const host = useHeaderActionsHost();
  const rowActions = useRowActions();
  const { entries, error: listError, mutate: mutatePaths } = useWikiTree();
  const [mutationError, setMutationError] = useState<string | null>(null);
  const confirmDialog = useConfirm();
  const error =
    mutationError ?? (listError instanceof Error ? listError.message : null);
  const setError = setMutationError;
  // Force the cache to revalidate from the server after writes (create /
  // delete / move). Refreshes this listing *and* every wiki cache — including
  // the open doc's id→path resolve and content — so a rename/move is reflected
  // on screen instead of leaving the view on the old path.
  const refresh = useCallback(() => {
    void mutatePaths();
    void revalidateWiki();
  }, [mutatePaths]);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  // Folders still use an inline filename form; new docs route to
  // NewDocView where filename + template + body are chosen together.
  const [creating, setCreating] = useState<"folder" | null>(null);
  const [newName, setNewName] = useState("");
  const [createBusy, setCreateBusy] = useState(false);
  const [triggerModalOpen, setTriggerModalOpen] = useState(false);
  const [triggerStatus, setTriggerStatus] = useState<string | null>(null);
  const [sort, setSort] = useState<SortMode>("name-asc");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [dragSource, setDragSource] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [sharePath, setSharePath] = useState<string | null>(null);
  const [policyOpen, setPolicyOpen] = useState(false);
  const [panelTab, setPanelTab] = useState<DocPanelTab>("updates");
  const [panelOpen, setPanelOpen] = useState(true);
  const [moreOpen, setMoreOpen] = useState(false);
  const [editTrigger, setEditTrigger] = useState<Trigger | null>(null);
  // One floating surface at a time: the on-load suggestions popup or the
  // hover policy/suggestions stack (mocks 2236:78296 / 2283:84706).
  const [folderPanel, setFolderPanel] = useState<"hover" | "popup" | null>(
    null,
  );
  // X only hides the popup for this visit; suggestions persist in the
  // side panel and the popup returns on the next page open.
  const popupHidden = useRef(false);
  const clusterRef = useRef<HTMLDivElement | null>(null);
  const closeTimer = useRef<number>(0);
  const holdOpen = useCallback(
    () => window.clearTimeout(closeTimer.current),
    [],
  );
  const closeSoon = useCallback(() => {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(
      () => setFolderPanel((p) => (p === "hover" ? null : p)),
      150,
    );
  }, []);
  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

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
    if (
      !(await confirmDialog({
        title: `Delete ${rel}?`,
        body: "It will be moved to Trash, where you can restore it.",
        confirmLabel: "Delete",
      }))
    )
      return;
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

  // Folder-level actions portal into the single pinned header (mock
  // 705:142993): New Page + new-folder, then share / trigger / launch-agent
  // past a divider. The update policy lives inline in the side column. Mobile
  // keeps it behind a header button + drawer instead.
  const { health } = useUpdateHealth(dir || null);
  const warnLevel = updateWarnLevel(health);
  const { proposals } = useProposalsByPath(dir, !!dir);
  // The suggestions popup self-opens when pending proposals exist (mock
  // 2236:78296) until the X hides it for this visit.
  useEffect(() => {
    popupHidden.current = false;
    setFolderPanel(null);
  }, [dir]);
  useEffect(() => {
    if (proposals.length > 0 && !popupHidden.current) {
      setFolderPanel((prev) => (prev === null ? "popup" : prev));
    }
  }, [proposals.length]);
  const openSidePanel = useCallback(() => {
    setFolderPanel(null);
    if (isMobile) setPolicyOpen(true);
    else {
      setPanelOpen(true);
      setPanelTab("updates");
    }
  }, [isMobile]);
  // Mock annotation "Click to highlight folder": flash the listing row for
  // the proposal's first path segment under this folder.
  const highlightPath = useCallback(
    (paths: string[]) => {
      const target = paths.find((x) => x.startsWith(dir ? dir + "/" : ""));
      if (!target) return;
      const rel = dir ? target.slice(dir.length + 1) : target;
      const childPath = (dir ? dir + "/" : "") + rel.split("/")[0];
      const el = document.querySelector(
        `[data-wiki-row="${CSS.escape(childPath)}"]`,
      );
      if (!el) return;
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("wiki-row-flash");
      window.setTimeout(() => el.classList.remove("wiki-row-flash"), 2000);
    },
    [dir],
  );

  const headerActions = (
    <>
      <Button
        prominence="tertiary"
        rightIcon={SvgPlus}
        onClick={() => router.push(`/app/wiki/${dir}?new=1`)}
      >
        New Page
      </Button>
      <Button
        icon={SvgFolderPlus}
        prominence="tertiary"
        tooltip="New folder"
        onClick={() => {
          setNewName("");
          setCreating((v) => (v === "folder" ? null : "folder"));
        }}
      />
      <span aria-hidden className="mx-2 h-5 w-px bg-(--border-01)" />
      <Section ref={clusterRef} gap={0} width="fit" height="fit">
        {/* raw-ok: the Auto trigger is the mock's ringed avatar circle, not a standard icon button */}
        <button
          type="button"
          aria-label="AI auto-edits"
          className="flex cursor-pointer items-center justify-center px-[2px]"
          onClick={openSidePanel}
          onPointerEnter={() => {
            holdOpen();
            setFolderPanel("hover");
          }}
          onPointerLeave={closeSoon}
        >
          <Section gap={0} width="fit" height="fit" className="relative">
            <IconContainer size="main-content" avatar="icon" icon={AutoGlyph} />
            <span
              aria-hidden
              className={`pointer-events-none absolute inset-0 rounded-full border ${
                warnLevel === "over"
                  ? "border-(--status-warning-02)"
                  : warnLevel === "near"
                    ? "border-(--theme-amber-02)"
                    : "border-(--theme-blue-05)"
              }`}
            />
          </Section>
        </button>
      </Section>
      <Button
        icon={SvgShare}
        prominence="tertiary"
        tooltip="Share"
        onClick={() => setSharePath(dir)}
      />
      <Popover open={moreOpen} onOpenChange={setMoreOpen}>
        <Popover.Trigger asChild>
          <span className="inline-flex">
            <Button
              icon={SvgMoreHorizontal}
              prominence="tertiary"
              tooltip="More"
            />
          </span>
        </Popover.Trigger>
        <Popover.Content width="fit" align="end">
          <PopoverMenu>
            <LineItemButton
              title="Trigger"
              icon={SvgWorkflow}
              sizePreset="main-ui"
              variant="section"
              onClick={() => {
                setMoreOpen(false);
                setTriggerModalOpen(true);
              }}
            />
            <LineItemButton
              title="Launch Agent"
              icon={SvgZap}
              sizePreset="main-ui"
              variant="section"
              onClick={() => {
                setMoreOpen(false);
                rowActions.launchAgent(dir);
              }}
            />
          </PopoverMenu>
        </Popover.Content>
      </Popover>
      <Button
        icon={SvgSidebar}
        prominence="tertiary"
        tooltip={
          isMobile ? "Update Policy" : panelOpen ? "Close panel" : "Open panel"
        }
        onClick={() =>
          isMobile ? setPolicyOpen(true) : setPanelOpen((v) => !v)
        }
      />
    </>
  );

  const parentDir = dir.split("/").slice(0, -1).join("/");
  const cycleSort = () =>
    setSort((s) => SORT_ORDER[(SORT_ORDER.indexOf(s) + 1) % SORT_ORDER.length]);
  const sortLabel = SORT_LABEL[sort];

  return (
    <main className="flex h-full min-h-0">
      {host?.el && createPortal(headerActions, host.el)}

      <div
        className={`min-w-0 flex-1 overflow-y-auto ${isMobile ? "px-3 py-4" : "px-8 py-6"}`}
      >
        <TriggerPanel
          open={triggerModalOpen}
          initial={editTrigger ?? { scope_path: dir || "/" }}
          lockScope={!editTrigger}
          onClose={() => {
            setTriggerModalOpen(false);
            setEditTrigger(null);
          }}
          onSaved={(t) =>
            setTriggerStatus(`Created trigger for ${t.scope_path}`)
          }
        />

        {triggerStatus && (
          <div className="mb-3 text-xs text-(--text-04)">{triggerStatus}</div>
        )}

        {creating && (
          <form
            onSubmit={onCreate}
            className="mb-4 flex gap-2 rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-01) p-3"
          >
            <span className="min-w-0 flex-1">
              <InputTypeIn
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="folder-name (or subdir/folder-name)"
                aria-label="New folder name"
              />
            </span>
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
          <div className="mb-3 rounded-(--radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
            {error}
          </div>
        )}

        {subdirs.length === 0 && files.length === 0 && !error && (
          <p className="text-sm text-(--text-03)">
            This folder is empty. Create a document to get started.
          </p>
        )}

        {/* Table surface: header line + white rows on a tint container
            (mock's Table, tint-01 on radius-08, rows tint-00 on radius-12). */}
        <div className="rounded-(--radius-08) bg-(--background-tint-01) p-1">
          {(subdirs.length > 0 || files.length > 0) && (
            // Column header line (mock 709:144294): Name spans, Last Updated
            // carries the sort control cycling name ascending, descending, recent.
            <div className="flex items-center px-3 pt-1 pb-2">
              <span className="min-w-0 flex-1">
                <Text font="main-ui-action" color="text-04">
                  Name
                </Text>
              </span>
              <Text font="main-ui-action" color="text-04" nowrap>
                Last Updated
              </Text>
              <Button
                icon={SvgArrowUpDown}
                prominence="tertiary"
                size="sm"
                tooltip={`Sort: ${sortLabel}`}
                onClick={cycleSort}
              />
            </div>
          )}

          <ul className="m-0 list-none p-0">
            {dir && (
              <BackRow
                onClick={() =>
                  router.push(
                    parentDir ? `/app/wiki/${parentDir}` : "/app/wiki",
                  )
                }
              />
            )}
            {(() => {
              const dirEntries = subdirs.map((d) => ({ ...d, isFile: false }));
              const fileEntries = files.map((f) => ({ ...f, isFile: true }));
              // Folders always above docs. Ordering within each group is set by `sort`.
              const ordered = [...dirEntries, ...fileEntries];
              return ordered.map(({ name, updated_at, isFile }) => {
                const childPath = (dir ? dir + "/" : "") + name;
                return (
                  <Row
                    key={(isFile ? "f:" : "d:") + name}
                    icon={
                      isFile ? (
                        <SvgDocFile size={18} aria-hidden />
                      ) : (
                        <SvgFolder size={18} aria-hidden />
                      )
                    }
                    label={name}
                    updatedAt={updated_at}
                    href={`/app/wiki/${childPath}`}
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
                            setDropTarget((cur) =>
                              cur === childPath ? null : cur,
                            )
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
        </div>
        <div className="my-2">
          <Divider />
        </div>
        <StartNewPage dir={dir} />

        {sharePath && (
          <ShareDialog
            path={sharePath}
            open
            onClose={() => setSharePath(null)}
          />
        )}

        {folderPanel === "hover" && clusterRef.current && (
          // Hover previews the policy popover only; suggestions never
          // ride the hover — they self-show as the dismissible popup and
          // live in the side panel (Nik, 2026-07-27).
          <AnchoredPanel
            anchor={clusterRef.current}
            onDismiss={() => setFolderPanel(null)}
            hover={{ onEnter: holdOpen, onLeave: closeSoon }}
          >
            <PolicyPopover
              path={dir}
              canWrite
              kind="folder"
              onOpenUpdatesPanel={openSidePanel}
            />
          </AnchoredPanel>
        )}
        {folderPanel === "popup" && clusterRef.current && (
          <AnchoredPanel
            anchor={clusterRef.current}
            onDismiss={() => setFolderPanel(null)}
            chrome={false}
          >
            <SuggestionsCard
              path={dir}
              onClose={() => {
                popupHidden.current = true;
                setFolderPanel(null);
              }}
              onOpenPanel={openSidePanel}
              onHighlight={highlightPath}
            />
          </AnchoredPanel>
        )}
      </div>

      {/* Folder policy applies to every page under this folder. Desktop shows
          it inline in the side column (mock 1673:32813). Mobile keeps the
          header button + drawer. */}
      {!isMobile && panelOpen && (
        <aside className="flex w-[360px] shrink-0 flex-col overflow-y-auto">
          <DocPanel
            tab={panelTab}
            onTabChange={setPanelTab}
            tabs={["updates", "watching"]}
          >
            {panelTab === "updates" ? (
              <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-2 py-1">
                <UpdatePolicyPanel path={dir} />
                <SuggestionsCard path={dir} onHighlight={highlightPath} />
              </div>
            ) : (
              <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-2 py-1">
                <WatchingPanel
                  path={dir}
                  onNew={() => setTriggerModalOpen(true)}
                  onEdit={(t) => {
                    setEditTrigger(t);
                    setTriggerModalOpen(true);
                  }}
                />
              </div>
            )}
          </DocPanel>
        </aside>
      )}
      {policyOpen && isMobile && (
        <>
          <div
            onClick={() => setPolicyOpen(false)}
            aria-hidden
            className="fixed inset-0 z-[60] bg-(--mask-03)"
          />
          <div className="fixed top-0 right-0 bottom-0 z-[70] flex w-[min(400px,100vw)] shadow-(--shadow-panel)">
            <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto bg-(--background-tint-00)">
              <UpdatePolicyPanel
                path={dir}
                onClose={() => setPolicyOpen(false)}
                fullHeight
              />
              <div className="px-2 pb-2">
                <SuggestionsCard path={dir} />
              </div>
            </div>
          </div>
        </>
      )}
    </main>
  );
}

interface NewDocViewProps {
  dir: string;
}

function NewDocView({ dir }: NewDocViewProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isMobile = useIsMobile();
  const host = useHeaderActionsHost();
  const { setDrafting, requestExpand, registerDraftBridge } = useDrafting();
  // "Start writing with AI" hands a generated draft (+ the prompt) here via
  // sessionStorage, paired with ?ai=1. Read it synchronously so the editor and
  // the drafting chat seed on the first render (no blank→ai re-init flash).
  const isAiSeed = searchParams?.get("ai") === "1";
  const [aiSeed] = useState<{
    title?: string;
    body?: string;
    prompt?: string;
  } | null>(() => {
    if (!isAiSeed || typeof window === "undefined") return null;
    try {
      const raw = sessionStorage.getItem(AI_DRAFT_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });
  const [filename, setFilename] = useState(aiSeed?.title ?? "");
  const [draft, setDraft] = useState(aiSeed?.body ?? "");
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

  // Destination folder for the new doc — defaults to the route's folder but is
  // user-selectable (the AI flow lands at root, so the picker is how you place it).
  const [destDir, setDestDir] = useState(dir);
  const { entries } = useWikiTree();
  const folders = useMemo(() => collectFolders(entries), [entries]);

  // Drop the stash once consumed so it doesn't re-apply on remount / back-nav.
  useEffect(() => {
    if (aiSeed && typeof window !== "undefined") {
      try {
        sessionStorage.removeItem(AI_DRAFT_KEY);
      } catch {
        // ignore
      }
    }
  }, [aiSeed]);

  // Bridge the editor to the drafting chat so it can live-edit this unsaved
  // draft. Keep a ref of the latest body so the chat reads current content.
  const draftRef = useRef(draft);
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);
  useEffect(() => {
    registerDraftBridge({ get: () => draftRef.current, set: setDraft });
    return () => registerDraftBridge(null);
  }, [registerDraftBridge]);

  // Pop the chat widget open once on mount — the assistant can help while the
  // user drafts. For an AI-seeded draft the chat seeds with the user's prompt.
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
      setDrafting({ kind: "blank", path: null, prompt: aiSeed?.prompt });
    }
  }, [appliedTemplateId, templates, setDrafting, aiSeed]);
  // Clear drafting on unmount (cancel, sidebar nav, …) — the chat widget
  // tears its drafting session down synchronously on null, so the collapse
  // happens in the same paint as the page change. The one exception is
  // Create: it navigates to the doc it just made and FileView re-syncs
  // drafting from the server-side draft row, so passing through null there
  // would collapse the chat only to re-init it a moment later.
  const createHandoffRef = useRef(false);
  useEffect(() => {
    return () => {
      if (!createHandoffRef.current) setDrafting(null);
    };
  }, [setDrafting]);

  const trimmedFilename = filename.trim().replace(/^\/+|\/+$/g, "");
  const filenameNoExt = trimmedFilename.replace(/\.md$/i, "");
  const filenameValid = !!filenameNoExt && !filenameNoExt.includes("/");
  // Block Create while a template's body is still loading, so it can't be
  // saved before the template (and its policy) is applied.
  const canCreate = filenameValid && !saving && applyingTemplateId === null;

  async function onPickTemplate(template: DocumentTemplateSummary) {
    setApplyingTemplateId(template.id);
    // Set the applied id synchronously — the create request seeds the page's
    // policy from it, so it must be set before the (async) body load, not after.
    setAppliedTemplateId(template.id);
    setError(null);
    try {
      const full = await getTemplate(template.id);
      setDraft(full.body);
      setAppliedTemplateBody(full.body);
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

  // Deep-link from the home page: ``?template=<id>`` pre-applies that
  // template once the summaries load, so the landing's template cards
  // drop the user straight into a seeded draft.
  const templateParam = searchParams?.get("template") ?? null;
  const autoTemplateRef = useRef(false);
  useEffect(() => {
    if (autoTemplateRef.current) return;
    if (!templateParam || !templates) return;
    const match = templates.find((t) => t.id === templateParam);
    if (!match) return;
    autoTemplateRef.current = true;
    void onPickTemplate(match);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateParam, templates]);

  async function onCreate() {
    if (!filenameValid) {
      setError("Filename cannot be empty or contain '/'.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const name = filenameNoExt + ".md";
      const fullPath = (destDir ? destDir + "/" : "") + name;
      const created = await apiFetch<{ id?: string | null }>("/wiki/file", {
        method: "PUT",
        body: JSON.stringify({
          path: fullPath,
          body: draft,
          // Seeds the new page's update policy from the template; the draft
          // recorded below lands after the commit, too late for that.
          ...(appliedTemplateId ? { template_id: appliedTemplateId } : {}),
        }),
      });
      // If a template was applied, record the draft row so the chat
      // banner + template system prompt persist on the saved doc.
      if (appliedTemplateId) {
        await setDraftTemplate(fullPath, appliedTemplateId);
      }
      // Revalidate every wiki cache so the persistent Directory sidebar (and
      // any open folder listing) shows the new page without a full reload.
      void revalidateWiki();
      // Hand-off: keep the drafting state (and the chat's drafting
      // session) alive across the navigation — see the unmount cleanup.
      createHandoffRef.current = true;
      // Land on the new page's id URL directly (the create response carries
      // the minted id), so it's a clean /app/wiki/<id> like every other page.
      // Keep ?new=1 — FileView reads it to auto-open the assistant on a
      // freshly-created doc.
      const base = created.id ? wikiHref(created.id) : `/app/wiki/${fullPath}`;
      router.push(`${base}?new=1`);
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
  const parentSlug = destDir;

  // Cancel / Create portal into the single pinned header; the breadcrumb there
  // shows the destination folder, so this view renders no header of its own.
  const headerActions = (
    <>
      <Button onClick={() => router.push(`/app/wiki/${dir}`)} disabled={saving}>
        Cancel
      </Button>
      <Button
        variant="action"
        onClick={() => void onCreate()}
        disabled={!canCreate}
        tooltip={
          !filenameValid && !saving ? "Give the file a name first." : undefined
        }
      >
        {saving ? "Creating…" : "Create"}
      </Button>
    </>
  );

  return (
    <main
      className={cn(
        "box-border flex h-full flex-col gap-3",
        isMobile ? "px-3 py-4" : "px-8 py-6",
      )}
    >
      {host?.el && createPortal(headerActions, host.el)}

      <div className="flex shrink-0 items-center gap-2">
        <span className="text-[13px] text-(--text-04)">Folder</span>
        <DestinationSelect
          value={destDir}
          folders={folders}
          onChange={setDestDir}
          disabled={saving}
        />
      </div>

      <FilenameRow
        parent={parentSlug}
        value={filename}
        onChange={setFilename}
        disabled={saving}
        autoFocus
        placeholder="filename for the new doc"
      />

      {error && (
        <div className="rounded-(--radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
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
        className="box-border min-h-0 w-full flex-1 resize-none rounded-(--radius-08) border border-(--border-01) p-4 font-mono text-sm leading-[1.6] outline-none"
      />
    </main>
  );
}

type SortMode = "name-asc" | "name-desc" | "recent";
const SORT_ORDER: SortMode[] = ["name-asc", "name-desc", "recent"];
const SORT_LABEL: Record<SortMode, string> = {
  "name-asc": "Name (A → Z)",
  "name-desc": "Name (Z → A)",
  recent: "Recently updated",
};

/* Shared card-row chrome for the folder listing: borderless white cards on
   the table's tint background (mock rows 4857:368063). */
const ROW_CLASS =
  "mb-1 flex h-11 items-center rounded-(--radius-12) py-1 pr-2 pl-1";

/* The mock's row menu glyph is vertical dots. The published icon set has only
 * the horizontal variant, whose 90° rotation is identical geometry. Swap for
 * an SvgMoreVertical once @onyx-ai/opal ships one. Button sizes icons via the
 * style prop, so the transform must merge with it, never replace it. */
const MoreVertical: NonNullable<
  React.ComponentProps<typeof Button>["icon"]
> = ({ style, ...props }) => (
  <SvgMoreHorizontal
    {...props}
    style={{ ...style, transform: "rotate(90deg)" }}
  />
);

/* The mock's shaded leading box: a 36px tint square holding the row's
   file/folder glyph (Qualifier Container, tint-01 on radius-08). */
function RowQualifier({ children }: { children: React.ReactNode }) {
  return (
    <span className="mr-2 flex size-9 shrink-0 items-center justify-center rounded-(--radius-08) bg-(--background-tint-01) text-(--text-03)">
      {children}
    </span>
  );
}

function BackRow({ onClick }: { onClick: () => void }) {
  return (
    // raw-ok: the whole 44px table row is the click target. OPAL has no
    // full-width table-row button primitive (LineItemButton is menu-scale).
    <li
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick();
      }}
      className={`${ROW_CLASS} cursor-pointer bg-(--background-tint-00) hover:bg-(--background-tint-02)`}
    >
      <RowQualifier>
        <SvgChevronLeft size={18} aria-hidden />
      </RowQualifier>
      <span className="flex flex-1 items-center text-sm text-(--text-05)">
        Back
      </span>
    </li>
  );
}

interface RowProps {
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
}: RowProps) {
  const router = useRouter();
  const [hover, setHover] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
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
      data-wiki-row={path}
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
      className={cn(
        ROW_CLASS,
        dropActive
          ? "bg-(--background-tint-03) outline outline-2 outline-(--background-tint-inverted-00)"
          : hover || menuOpen
            ? "bg-(--background-tint-02)"
            : "bg-(--background-tint-00)",
        busy ? "opacity-50" : "opacity-100",
        renaming ? "cursor-default" : "cursor-pointer",
      )}
    >
      <RowQualifier>{icon}</RowQualifier>
      {renaming ? (
        // Mock rename state (890:418642): the name cell becomes an input with
        // its text preselected, confirmed by the check button or Enter.
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSubmitRename(draft);
          }}
          className="flex min-w-0 flex-1 items-center"
        >
          <InputTypeIn
            onFocus={(e) => e.currentTarget.select()}
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                onCancelRename();
              }
            }}
            aria-label={`Rename ${label}`}
            rightChildren={
              // Keep focus in the input across the click so Escape still
              // works if the request errors.
              <span onMouseDown={(e) => e.preventDefault()}>
                <Button
                  prominence="tertiary"
                  size="sm"
                  icon={SvgCheck}
                  aria-label="Save name"
                  disabled={busy || !draft.trim()}
                  onClick={() => onSubmitRename(draft)}
                />
              </span>
            }
          />
        </form>
      ) : (
        // The label is a plain span — the click target is the parent
        // <li>. flex: 1 keeps it stretching to fill the space between
        // icon and action buttons, so any click on the label area
        // still hits the row's onClick.
        <span className="flex min-w-0 flex-1 items-center gap-[10px] overflow-hidden text-sm text-ellipsis whitespace-nowrap text-(--text-05)">
          {label}
        </span>
      )}
      {!renaming && (
        <>
          <span className="w-[110px] shrink-0 text-xs whitespace-nowrap text-(--text-02)">
            {updatedAt ? relativeTime(updatedAt, "short") : "—"}
          </span>
          {/* White container pops the menu trigger off the grey hover row
              (mock's Button/Icon Button, tint-00 on radius-08). */}
          <span
            className={cn(
              "flex rounded-(--radius-08) bg-(--background-tint-00) transition-opacity duration-100",
              hover || menuOpen ? "opacity-100" : "opacity-0",
            )}
            onClick={(e) => e.stopPropagation()}
          >
            <WikiItemMenu
              path={path}
              isFolder={!isFile}
              open={menuOpen}
              onOpenChange={setMenuOpen}
              align="end"
              overrides={{ rename: onStartRename, remove: onDelete }}
            >
              <Button
                prominence="tertiary"
                icon={MoreVertical}
                disabled={busy}
                aria-label={`More actions for ${label}`}
              />
            </WikiItemMenu>
          </span>
        </>
      )}
    </li>
  );
}

export default function WikiPage() {
  const { user, loading } = useRequireAuth();
  const isMobile = useIsMobile();
  const router = useRouter();
  const params = useParams<{ slug?: string[] }>();
  const searchParams = useSearchParams();
  const isNewMode = searchParams?.get("new") === "1";
  const rawSlugParts = (params?.slug ?? []) as string[];

  // Every wiki URL is id-based: `/app/wiki/<id>` (Google-Docs style). The id is
  // stable, so the URL survives rename/move. A legacy `/app/wiki/<path>` URL
  // still resolves as input and is redirected to its id URL below.
  const first = rawSlugParts[0];
  // An id URL is always a single segment (`/app/wiki/<id>`); a multi-segment
  // slug is a legacy path even if its first segment looks like an id.
  const idMode = rawSlugParts.length === 1 && !!first && isDocId(first);
  const commentId = searchParams?.get("comment") ?? null;

  // id mode: resolve the id to its current path / kind / deleted state.
  const { resolved, error: resolveErr } = useDocIdResolve(
    idMode ? (first as string) : null,
  );

  // Next.js may hand back percent-encoded segments (e.g. "local%20testing").
  // Decode so labels and downstream API paths use literal characters.
  const decodedParts = rawSlugParts.map((s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  });
  const pathModePath = decodedParts.join("/");
  // A single 16-hex segment is treated as an id. When it doesn't resolve (404)
  // it's almost always an unknown/expired id — but it *could* be a legacy path
  // literally named like an id, so we resolve that path: a live id means it's a
  // real doc (redirect to its canonical id URL below); no id means the id is
  // simply unknown → the "unavailable" card. This is why an unknown id must NOT
  // fall through to the folder explorer (which would render an empty folder).
  const idResolve404 =
    idMode && (resolveErr as ApiError | undefined)?.status === 404;
  const {
    id: idFallback,
    error: idFallbackError,
    isLoading: idFallbackLoading,
  } = usePathToId(
    "id-fallback",
    idResolve404 && pathModePath ? pathModePath : null,
  );
  // The doc/folder path we're viewing: from the resolved id, or (legacy path
  // URL) straight from the URL. An unresolved id contributes no path.
  const effectivePath = idMode ? (resolved?.path ?? null) : pathModePath;
  const isFile = !!effectivePath && effectivePath.endsWith(".md");

  // Legacy path URL (`/app/wiki/<path>`): every real page/folder has a stable
  // id, so we resolve the path → redirect to its canonical id URL. A path that
  // resolves to *no* id doesn't exist → the render shows "unavailable" rather
  // than an empty folder explorer. The wiki root and the new-doc flow have no
  // path/id and are handled directly.
  const pathModeActive = !idMode && !!pathModePath && !isNewMode;
  const {
    id: pathId,
    error: pathIdError,
    isLoading: pathIdLoading,
  } = usePathToId("wiki-path-id", pathModeActive ? pathModePath : null);
  useEffect(() => {
    if (!pathModeActive || !pathId) return;
    const suffix = commentId ? `?comment=${encodeURIComponent(commentId)}` : "";
    router.replace(wikiHref(pathId) + suffix);
  }, [pathModeActive, pathId, commentId, router]);

  // A 16-hex id that didn't resolve but *is* a real doc's path (a page/folder
  // literally named like an id) → send it to its canonical id URL, preserving
  // a comment deep-link like the path redirect does.
  useEffect(() => {
    if (!idResolve404 || !idFallback) return;
    const suffix = commentId ? `?comment=${encodeURIComponent(commentId)}` : "";
    router.replace(wikiHref(idFallback) + suffix);
  }, [idResolve404, idFallback, commentId, router]);

  // Remember the most recent wiki path (for the "Last viewed" landing) and
  // feed the sidebar Recents when an actual doc is opened.
  useEffect(() => {
    if (effectivePath === null) return;
    rememberWikiPath("/app/wiki" + (effectivePath ? "/" + effectivePath : ""));
    if (isFile) void recordRecentDoc(effectivePath);
  }, [effectivePath, isFile]);

  // Tab title tracks the open doc (or folder); falls back to the app name.
  useEffect(() => {
    if (effectivePath === null) return;
    const last = effectivePath.split("/").pop() ?? "";
    const label = isFile ? pageTitle(effectivePath) : last;
    document.title = label || "agent-wiki";
    return () => {
      document.title = "agent-wiki";
    };
  }, [effectivePath, isFile]);

  if (loading || !user)
    return (
      <main className={isMobile ? "p-4" : "p-8"}>
        <LoadingSpinner center />
      </main>
    );

  if (idMode) {
    if (idResolve404) {
      // Unknown id. If it's actually a live doc's path we're mid-redirect (or
      // still checking) → spinner; otherwise the unavailable card (never the
      // empty folder explorer). If the fallback lookup itself failed (after
      // SWR's retries) we can't confirm the path is missing, so show the softer
      // generic copy rather than the definitive 404 "broken link" claim.
      if (idFallbackLoading || idFallback)
        return (
          <main className={isMobile ? "p-4" : "p-8"}>
            <LoadingSpinner center />
          </main>
        );
      return <WikiUnknownLink status={idFallbackError ? undefined : 404} />;
    }
    // Deleted page → tombstone panel (who/when + Restore); other resolve
    // failure → the generic unavailable card.
    if (resolved?.deleted_at) return <WikiTombstone path={resolved.path} />;
    if (resolveErr)
      return <WikiUnknownLink status={(resolveErr as ApiError)?.status} />;
    if (!resolved)
      return (
        <main className={isMobile ? "p-4" : "p-8"}>
          <LoadingSpinner center />
        </main>
      );
  }

  if (pathModeActive) {
    // Real page/folder → has an id → redirecting to its canonical id URL
    // (spinner). Still resolving → spinner. Resolved to no id → the path
    // doesn't exist → unavailable. On a transient resolve failure, fall through
    // to render-by-path below rather than falsely claim unavailable.
    if (pathIdLoading || pathId)
      return (
        <main className={isMobile ? "p-4" : "p-8"}>
          <LoadingSpinner center />
        </main>
      );
    if (!pathIdError && pathId === null)
      return <WikiUnknownLink status={404} />;
  }

  if (isFile) return <FileView path={effectivePath as string} />;
  if (isNewMode) return <NewDocView dir={pathModePath} />;
  // Wiki root with no doc open → the "Welcome to Onyx Wiki" landing.
  // Sub-folders still render the directory explorer.
  if (!effectivePath) return <WikiHome />;
  return <Explorer dir={effectivePath} />;
}

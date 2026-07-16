"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";

import {
  Button,
  Divider,
  InputTypeIn,
  SidebarTab,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgArrowWallRight,
  SvgCheck,
  SvgChevronDown,
  SvgChevronRight,
  SvgFileText,
  SvgFolder,
  SvgFolderOpen,
  SvgFolderPlus,
  SvgMoreHorizontal,
  SvgPlus,
  SvgTextLines,
} from "@onyx-ai/opal/icons";

import { apiFetch } from "@/lib/api";
import { revalidateWiki } from "@/lib/wikiHref";
import { useLeftPanel } from "@/providers/LeftPanelProvider";
import {
  useActiveFolder,
  useFolderCreate,
  useRowActions,
} from "@/providers/WikiItemActionsProvider";
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

type IconComponent = NonNullable<
  React.ComponentProps<typeof SidebarTab>["icon"]
>;

/** The mock's `arrow-wall-left` collapse glyph. The published icon set ships
 * only the right-facing variant, whose 180° rotation is the exact mirror.
 * Swap for SvgArrowWallLeft once @onyx-ai/opal publishes it. */
const ArrowWallLeft: IconComponent = (props) => (
  <SvgArrowWallRight {...props} style={{ transform: "rotate(180deg)" }} />
);

/** Invisible icon-slot filler so file rows keep the chevron column's exact
 * geometry and their glyph aligns with folder glyphs. */
const GlyphSpacer: IconComponent = (props) => (
  <svg {...props} aria-hidden="true" />
);

/**
 * One tree row. Folders lead with an expand chevron (icon slot) + a folder
 * glyph inline with the label. Files fill the chevron slot with a spacer so
 * their glyph aligns with folder glyphs. The "⋯" menu sits in SidebarTab's
 * right slot, revealed on the tab's own group hover or while its menu is open.
 */
function Row({
  chevron,
  glyph: Glyph,
  label,
  path,
  isFolder,
  active = false,
  onClick,
}: {
  chevron?: IconComponent;
  glyph: React.ComponentType<{ size?: number }>;
  label: string;
  path: string;
  isFolder: boolean;
  active?: boolean;
  onClick: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <SidebarTab
      variant="sidebar-light"
      icon={chevron ?? GlyphSpacer}
      selected={menuOpen || active}
      onClick={onClick}
      rightChildren={
        <span
          className={`flex transition-opacity duration-100 ${
            menuOpen
              ? "opacity-100"
              : "opacity-0 group-hover/SidebarTab:opacity-100 focus-within:opacity-100"
          }`}
          onClick={(e) => e.stopPropagation()}
        >
          <WikiItemMenu
            path={path}
            isFolder={isFolder}
            open={menuOpen}
            onOpenChange={setMenuOpen}
            align="start"
          >
            <Button
              prominence="tertiary"
              size="sm"
              icon={SvgMoreHorizontal}
              aria-label="More actions"
            />
          </WikiItemMenu>
        </span>
      }
    >
      <span className={styles.rowLabel}>
        <Glyph size={16} />
        <span>{label}</span>
      </span>
    </SidebarTab>
  );
}

/**
 * Inline folder-create row (mock 852:350261): a prefilled input, text
 * selected, confirmed with the check button / Enter, cancelled with Escape.
 */
function NewFolderRow({
  dir,
  onDone,
  onCancel,
}: {
  dir: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("New Folder");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.select(), []);

  async function submit() {
    const clean = name.trim().replace(/^\/+|\/+$/g, "");
    if (!clean || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/wiki/folder", {
        method: "POST",
        body: JSON.stringify({ path: dir ? `${dir}/${clean}` : clean }),
      });
      void revalidateWiki();
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "create failed");
      setBusy(false);
    }
  }

  return (
    <div className={styles.newFolderRow}>
      <InputTypeIn
        ref={inputRef}
        value={name}
        onChange={(e) => setName(e.target.value)}
        aria-label="New folder name"
        autoFocus
        onKeyDown={(e) => {
          if (e.key === "Enter") void submit();
          if (e.key === "Escape") onCancel();
        }}
        rightChildren={
          // Keep focus in the input across the click so Enter/Escape still work
          // if the request errors.
          <span onMouseDown={(e) => e.preventDefault()}>
            <Button
              prominence="tertiary"
              size="sm"
              icon={SvgCheck}
              aria-label="Create folder"
              disabled={busy}
              onClick={() => void submit()}
            />
          </span>
        }
      />
      {error && <div className={styles.folderError}>{error}</div>}
    </div>
  );
}

/** A folder row that expands inline to show its children, indented one level
 * with the mock's vertical guide line. */
function FolderNode({
  entries,
  dir,
  name,
  onOpenFile,
  expanded,
  searchOpen,
  onToggle,
  activeFolder,
  onSetActive,
  creatingIn,
  onCreateDone,
  onCreateCancel,
}: {
  entries: Entry[];
  dir: string;
  name: string;
  onOpenFile: (path: string) => void;
  expanded: Set<string>;
  searchOpen: boolean;
  onToggle: (path: string) => void;
  activeFolder: string;
  onSetActive: (path: string) => void;
  creatingIn: string | null;
  onCreateDone: () => void;
  onCreateCancel: () => void;
}) {
  const full = dir ? `${dir}/${name}` : name;
  const creatingHere = creatingIn === full;
  const open = searchOpen || expanded.has(full) || creatingHere;
  const { folders, files } = open
    ? childrenOf(entries, full)
    : { folders: [], files: [] };
  return (
    <>
      <Row
        chevron={open ? SvgChevronDown : SvgChevronRight}
        glyph={open ? SvgFolderOpen : SvgFolder}
        label={name}
        path={full}
        isFolder
        active={activeFolder === full}
        onClick={() => {
          onOpenFile(full); // navigate the main view to the folder listing
          onSetActive(full);
          onToggle(full);
        }}
      />
      {open && (
        <div className={styles.nested}>
          {creatingHere && (
            <NewFolderRow
              dir={full}
              onDone={onCreateDone}
              onCancel={onCreateCancel}
            />
          )}
          {folders.map((f) => (
            <FolderNode
              key={f}
              entries={entries}
              dir={full}
              name={f}
              onOpenFile={onOpenFile}
              expanded={expanded}
              searchOpen={searchOpen}
              onToggle={onToggle}
              activeFolder={activeFolder}
              onSetActive={onSetActive}
              creatingIn={creatingIn}
              onCreateDone={onCreateDone}
              onCreateCancel={onCreateCancel}
            />
          ))}
          {files.map((f) => (
            <Row
              key={f}
              glyph={SvgFileText}
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
 * Directory tree panel (mock 319:26456): "Wiki" header with a collapse
 * control, a search row with new-page / new-folder actions, then the nested
 * tree. Search filters the tree to matching paths with ancestors held open.
 */
export function WikiTree() {
  const router = useRouter();
  const { toggleTree } = useLeftPanel();
  const actions = useRowActions();
  const { data } = useSWR<{ entries: Entry[] }>("/wiki");
  const entries = data?.entries ?? [];

  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const visible = q
    ? entries.filter((e) => e.path.toLowerCase().includes(q))
    : entries;
  const { folders, files } = childrenOf(visible, "");
  // Navigates to a page or a folder. The same /app/wiki/<path> route renders
  // both (a folder shows its directory listing).
  const openFile = (path: string) => router.push(`/app/wiki/${path}`);

  // The folder new pages/folders are created inside ("" = wiki root). State
  // lives in the provider so deletes can reset it and the "⋯" menu can start
  // an inline create from any row.
  const { activeFolder, setActiveFolder } = useActiveFolder();
  const { creatingIn, setCreatingIn } = useFolderCreate();
  const activeLabel = activeFolder.split("/").pop() ?? "";

  // Expanded folders, persisted to sessionStorage so they survive a refresh.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(EXPANDED_KEY);
      if (raw) setExpanded(new Set(JSON.parse(raw) as string[]));
    } catch {
      // sessionStorage unavailable (private mode), start collapsed.
    }
  }, []);
  const persistExpanded = (next: Set<string>) => {
    try {
      sessionStorage.setItem(EXPANDED_KEY, JSON.stringify([...next]));
    } catch {
      // ignore persistence failure
    }
  };
  const toggleFolder = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      persistExpanded(next);
      return next;
    });
  };

  // Hold every ancestor of the create-target open so the inline row is
  // reachable when a row menu's New Folder fires from a collapsed subtree.
  useEffect(() => {
    if (!creatingIn) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      let cur = "";
      for (const seg of creatingIn.split("/").filter(Boolean)) {
        cur = cur ? `${cur}/${seg}` : seg;
        next.add(cur);
      }
      persistExpanded(next);
      return next;
    });
  }, [creatingIn]);

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.headerTitle}>
          {/* Mock icon is `list-tree` (SvgListTree once @onyx-ai/opal ships it,
              with SvgFold below becoming SvgArrowWallLeft). */}
          <SvgTextLines size={16} />
          <Text font="main-ui-action" color="text-05">
            Wiki Directory
          </Text>
        </span>
        <Button
          prominence="tertiary"
          icon={ArrowWallLeft}
          tooltip="Close Panel"
          onClick={toggleTree}
        />
      </div>

      <div className={styles.searchRow}>
        <InputTypeIn
          searchIcon
          clearButton
          placeholder="Search wiki directory..."
          aria-label="Search wiki directory"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button
          prominence="tertiary"
          icon={SvgPlus}
          tooltip={activeFolder ? `New page in ${activeLabel}` : "New page"}
          onClick={() => actions.newPage(activeFolder)}
        />
        <Button
          prominence="tertiary"
          icon={SvgFolderPlus}
          tooltip={activeFolder ? `New folder in ${activeLabel}` : "New folder"}
          onClick={() => setCreatingIn(activeFolder)}
        />
      </div>
      <Divider />

      <div className={styles.list}>
        {creatingIn === "" && (
          <NewFolderRow
            dir=""
            onDone={() => setCreatingIn(null)}
            onCancel={() => setCreatingIn(null)}
          />
        )}
        {folders.map((f) => (
          <FolderNode
            key={f}
            entries={visible}
            dir=""
            name={f}
            onOpenFile={openFile}
            expanded={expanded}
            searchOpen={q !== ""}
            onToggle={toggleFolder}
            activeFolder={activeFolder}
            onSetActive={setActiveFolder}
            creatingIn={creatingIn}
            onCreateDone={() => setCreatingIn(null)}
            onCreateCancel={() => setCreatingIn(null)}
          />
        ))}
        {files.map((f) => (
          <Row
            key={f}
            glyph={SvgFileText}
            label={f.replace(/\.md$/, "")}
            path={f}
            isFolder={false}
            onClick={() => openFile(f)}
          />
        ))}
        {folders.length === 0 && files.length === 0 && (
          <div className={styles.empty}>
            <Text font="secondary-body" color="text-03">
              {q ? "No matches" : "No pages yet"}
            </Text>
          </div>
        )}
      </div>
    </div>
  );
}

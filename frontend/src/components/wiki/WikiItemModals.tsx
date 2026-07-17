"use client";

import { useEffect, useState } from "react";

import {
  Button,
  InputTypeIn,
  SidebarTab,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgChevronDown,
  SvgChevronRight,
  SvgEdit,
  SvgFolder,
  SvgFolderIn,
  SvgX,
} from "@onyx-ai/opal/icons";
import { markdown } from "@onyx-ai/opal/utils";

import { apiFetch } from "@/lib/api";
import { lastSegment } from "@/lib/wiki/utils";

import styles from "./WikiItemModals.module.css";

const isFolderPath = (path: string) => !path.endsWith(".md");
const parentOf = (path: string) =>
  path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
const baseName = (path: string) => path.slice(path.lastIndexOf("/") + 1);

async function movePath(oldPath: string, newPath: string) {
  await apiFetch("/wiki/move", {
    method: "POST",
    body: JSON.stringify({ old_path: oldPath, new_path: newPath }),
  });
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
}

/** Rename a file or folder in place (same parent, new last segment). */
export function RenameModal({
  path,
  onClose,
  onDone,
}: {
  path: string;
  onClose: () => void;
  onDone: (newPath: string) => void;
}) {
  const folder = isFolderPath(path);
  const parent = parentOf(path);
  const current = folder ? baseName(path) : baseName(path).replace(/\.md$/, "");
  const [name, setName] = useState(current);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEscape(onClose);

  const submit = async () => {
    const clean = name.trim().replace(/\//g, "");
    if (!clean) return;
    if (clean === current) {
      onClose();
      return;
    }
    const newPath =
      (parent ? `${parent}/` : "") + clean + (folder ? "" : ".md");
    setBusy(true);
    setError(null);
    try {
      await movePath(path, newPath);
      onDone(newPath);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rename failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={styles.scrim}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className={styles.dialog} role="dialog" aria-modal="true">
        <header className={styles.header}>
          <span className={styles.headerIcon}>
            <SvgEdit size={20} />
          </span>
          <div className={styles.headerText}>
            <Text as="h2" font="main-content-emphasis">
              {markdown(`Rename *${lastSegment(path)}*`)}
            </Text>
          </div>
          <Button
            prominence="tertiary"
            size="sm"
            icon={SvgX}
            tooltip="Close"
            onClick={onClose}
          />
        </header>

        <div className={styles.content}>
          <InputTypeIn
            value={name}
            autoFocus
            placeholder={folder ? "Folder name" : "Page name"}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submit();
            }}
          />
          {error && (
            <Text font="secondary-body" color="text-02">
              {error}
            </Text>
          )}
        </div>

        <footer className={styles.footer}>
          <Button prominence="tertiary" size="md" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="action"
            size="md"
            disabled={busy || !name.trim()}
            onClick={() => void submit()}
          >
            {busy ? "Renaming…" : "Rename"}
          </Button>
        </footer>
      </div>
    </div>
  );
}

/** Direct child folders of `parent`, derived from the flat folder-path list. */
function subfoldersOf(folders: string[], parent: string): string[] {
  const prefix = parent ? `${parent}/` : "";
  const direct = new Set<string>();
  for (const f of folders) {
    if (!f || f === parent || !f.startsWith(prefix)) continue;
    const seg = f.slice(prefix.length).split("/")[0];
    if (seg) direct.add(prefix + seg);
  }
  return [...direct].sort((a, b) => a.localeCompare(b));
}

/** One expandable destination folder in the move tree. Folders with children
 * can be expanded to drill into nested subfolders; invalid destinations (the
 * item's current parent, or a moved folder's own subtree) are shown disabled
 * so they can still be navigated through. */
function DestNode({
  dest,
  folders,
  target,
  onSelect,
  canSelect,
  depth,
}: {
  dest: string;
  folders: string[];
  target: string | null;
  onSelect: (dest: string) => void;
  canSelect: (dest: string) => boolean;
  depth: number;
}) {
  const [open, setOpen] = useState(false);
  const subs = subfoldersOf(folders, dest);
  const selectable = canSelect(dest);
  return (
    <>
      <div
        className={styles.destRow}
        style={{ paddingLeft: `${depth * 16}px` }}
      >
        {subs.length > 0 ? (
          <Button
            prominence="tertiary"
            size="2xs"
            icon={open ? SvgChevronDown : SvgChevronRight}
            tooltip={open ? "Collapse" : "Expand"}
            onClick={() => setOpen((o) => !o)}
          />
        ) : (
          <span className={styles.destChevronSpacer} />
        )}
        <div className={styles.destTab}>
          <SidebarTab
            variant="sidebar-light"
            icon={SvgFolder}
            selected={target === dest}
            disabled={!selectable}
            onClick={() => selectable && onSelect(dest)}
          >
            {baseName(dest)}
          </SidebarTab>
        </div>
      </div>
      {open &&
        subs.map((s) => (
          <DestNode
            key={s}
            dest={s}
            folders={folders}
            target={target}
            onSelect={onSelect}
            canSelect={canSelect}
            depth={depth + 1}
          />
        ))}
    </>
  );
}

/** Move a file or folder into a different existing folder. */
export function MoveModal({
  path,
  folders,
  onClose,
  onDone,
}: {
  path: string;
  folders: string[];
  onClose: () => void;
  onDone: (newPath: string) => void;
}) {
  const folder = isFolderPath(path);
  const parent = parentOf(path);
  const base = baseName(path);

  // A folder is a valid destination unless it's the item's current parent
  // (no-op) or — for a folder being moved — itself or one of its descendants.
  const canSelect = (dest: string) =>
    dest !== parent &&
    !(folder && (dest === path || dest.startsWith(`${path}/`)));

  const rootFolders = subfoldersOf(folders, "");

  const [target, setTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEscape(onClose);

  const submit = async () => {
    if (target === null) return;
    const newPath = (target ? `${target}/` : "") + base;
    setBusy(true);
    setError(null);
    try {
      await movePath(path, newPath);
      onDone(newPath);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Move failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={styles.scrim}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`${styles.dialog} ${styles.dialogWide}`}
        role="dialog"
        aria-modal="true"
      >
        <header className={styles.header}>
          <span className={styles.headerIcon}>
            <SvgFolderIn size={20} />
          </span>
          <div className={styles.headerText}>
            <Text as="h2" font="main-content-emphasis">
              {markdown(`Move *${lastSegment(path)}*`)}
            </Text>
          </div>
          <Button
            prominence="tertiary"
            size="sm"
            icon={SvgX}
            tooltip="Close"
            onClick={onClose}
          />
        </header>

        <div className={styles.content}>
          <Text font="secondary-action" color="text-02">
            Move to
          </Text>
          <div className={styles.destList}>
            <div className={styles.destRow}>
              <span className={styles.destChevronSpacer} />
              <div className={styles.destTab}>
                <SidebarTab
                  variant="sidebar-light"
                  icon={SvgFolder}
                  selected={target === ""}
                  disabled={!canSelect("")}
                  onClick={() => canSelect("") && setTarget("")}
                >
                  Home
                </SidebarTab>
              </div>
            </div>
            {rootFolders.map((f) => (
              <DestNode
                key={f}
                dest={f}
                folders={folders}
                target={target}
                onSelect={setTarget}
                canSelect={canSelect}
                depth={0}
              />
            ))}
          </div>
          {error && (
            <Text font="secondary-body" color="text-02">
              {error}
            </Text>
          )}
        </div>

        <footer className={styles.footer}>
          <Button prominence="tertiary" size="md" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="action"
            size="md"
            disabled={busy || target === null}
            onClick={() => void submit()}
          >
            {busy ? "Moving…" : "Move"}
          </Button>
        </footer>
      </div>
    </div>
  );
}

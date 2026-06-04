"use client";

import { useMemo, useState } from "react";

import {
  Button,
  InputTypeIn,
  LineItemButton,
  OpenButton,
  Popover,
  PopoverMenu,
  Table,
  Text,
  createTableColumns,
} from "@onyx-ai/opal/components";
import { Content, IllustrationContent } from "@onyx-ai/opal/layouts";
import {
  SvgCheck,
  SvgEdit,
  SvgEye,
  SvgFileText,
  SvgFolder,
  SvgPlusCircle,
  SvgX,
} from "@onyx-ai/opal/icons";
import { SvgNoResult } from "@onyx-ai/opal/illustrations";

import type {
  Permission,
  ResourceKind,
  WikiPathEntry,
} from "@/lib/permissions";
import { lastSegment } from "@/lib/wiki";

import styles from "./groups.module.css";

export interface ShareDraft {
  resource_kind: ResourceKind;
  resource_path: string;
  permission: Permission;
  id?: string; // present for shares that already exist on the server
}

export function shareKey(s: {
  resource_kind: ResourceKind;
  resource_path: string;
}): string {
  return `${s.resource_kind}:${s.resource_path}`;
}

interface DocRow {
  id: string; // shareKey
  kind: ResourceKind;
  path: string;
  label: string;
}

const PAGE_SIZE = 10;
const tc = createTableColumns<DocRow>();

function docNameCell(label: string, row: DocRow) {
  return (
    <Content
      sizePreset="main-ui"
      variant="section"
      title={label}
      description={row.path}
    />
  );
}

const docColumns = [
  tc.qualifier({
    content: "icon",
    getContent: (row) => (row.kind === "folder" ? SvgFolder : SvgFileText),
  }),
  tc.column("label", { header: "Name", weight: 40, cell: docNameCell }),
  tc.actions({ showSorting: false }),
];

/** Every page + folder in the wiki, as selectable rows. Folders are derived
 * from the ancestor directories of every tracked path. */
function deriveDocRows(entries: WikiPathEntry[]): DocRow[] {
  const folders = new Set<string>();
  const pages: DocRow[] = [];
  for (const e of entries) {
    const base = e.path.split("/").pop() ?? "";
    if (base !== ".gitkeep" && e.path.endsWith(".md")) {
      pages.push({
        id: `page:${e.path}`,
        kind: "page",
        path: e.path,
        label: lastSegment(e.path) || e.path,
      });
    }
    const parts = e.path.split("/");
    parts.pop();
    let acc = "";
    for (const p of parts) {
      acc = acc ? `${acc}/${p}` : p;
      if (acc) folders.add(acc);
    }
  }
  const folderRows: DocRow[] = [...folders].sort().map((path) => ({
    id: `folder:${path}`,
    kind: "folder",
    path,
    label: lastSegment(path) || path,
  }));
  return [...folderRows, ...pages.sort((a, b) => a.path.localeCompare(b.path))];
}

/** Sharing editor matching the member-management UX: an "Add" mode that shows
 * every page/folder as a multi-select checkbox table, and a view mode listing
 * the shared docs with a per-row View/Edit toggle and remove. */
export function GroupSharesEditor({
  shares,
  onChange,
  wikiEntries,
  defaultAdding = false,
}: {
  shares: ShareDraft[];
  onChange: (next: ShareDraft[]) => void;
  wikiEntries: WikiPathEntry[];
  defaultAdding?: boolean;
}) {
  const [isAdding, setIsAdding] = useState(defaultAdding);
  const [search, setSearch] = useState("");

  const docRows = useMemo(() => deriveDocRows(wikiEntries), [wikiEntries]);
  const docById = useMemo(
    () => new Map(docRows.map((d) => [d.id, d])),
    [docRows],
  );

  const selectedKeys = useMemo(() => shares.map(shareKey), [shares]);
  const currentRowSelection = useMemo(
    () => Object.fromEntries(selectedKeys.map((k) => [k, true])),
    [selectedKeys],
  );
  // Shares whose doc isn't in the candidate list (e.g. a page that no longer
  // lists) — preserve them across selection changes.
  const hiddenShares = useMemo(
    () => shares.filter((s) => !docById.has(shareKey(s))),
    [shares, docById],
  );

  function onTableSelectionChange(keys: string[]) {
    const existing = new Map(shares.map((s) => [shareKey(s), s]));
    const next: ShareDraft[] = [];
    for (const key of keys) {
      const prev = existing.get(key);
      if (prev) {
        next.push(prev);
        continue;
      }
      const doc = docById.get(key);
      if (doc)
        next.push({
          resource_kind: doc.kind,
          resource_path: doc.path,
          permission: "read",
        });
    }
    onChange([...next, ...hiddenShares]);
  }

  function setPermission(key: string, permission: Permission) {
    onChange(
      shares.map((s) => (shareKey(s) === key ? { ...s, permission } : s)),
    );
  }

  function removeShare(key: string) {
    onChange(shares.filter((s) => shareKey(s) !== key));
  }

  const sharedSorted = useMemo(
    () =>
      [...shares].sort((a, b) =>
        a.resource_path.localeCompare(b.resource_path),
      ),
    [shares],
  );

  return (
    <div className={styles.fieldGroup}>
      <div className={styles.sectionHead}>
        <Text font="main-ui-action" color="text-04">
          {isAdding
            ? "Add pages & folders"
            : `Shared pages & folders (${shares.length})`}
        </Text>
        {isAdding ? (
          <Button
            prominence="secondary"
            size="sm"
            onClick={() => {
              setIsAdding(false);
              setSearch("");
            }}
          >
            Done
          </Button>
        ) : (
          <Button
            prominence="tertiary"
            size="sm"
            icon={SvgPlusCircle}
            onClick={() => setIsAdding(true)}
          >
            Add
          </Button>
        )}
      </div>

      {isAdding ? (
        <>
          <InputTypeIn
            searchIcon
            variant="internal"
            placeholder="Search pages and folders…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Table
            key="add-docs"
            data={docRows}
            columns={docColumns}
            getRowId={(r) => r.id}
            pageSize={PAGE_SIZE}
            searchTerm={search}
            selectionBehavior="multi-select"
            initialRowSelection={currentRowSelection}
            onSelectionChange={onTableSelectionChange}
            footer={{}}
            emptyState={
              <IllustrationContent
                illustration={SvgNoResult}
                title="No pages or folders"
                description="No pages or folders match your search."
              />
            }
          />
        </>
      ) : shares.length === 0 ? (
        <Text font="secondary-body" color="text-03">
          Nothing shared with this group yet. Add a page or folder to grant the
          group access.
        </Text>
      ) : (
        <div className={styles.sharesList}>
          {sharedSorted.map((s) => {
            const key = shareKey(s);
            const Icon = s.resource_kind === "folder" ? SvgFolder : SvgFileText;
            const label =
              s.resource_path === ""
                ? "Root folder"
                : lastSegment(s.resource_path) || s.resource_path;
            return (
              <div key={key} className={styles.shareRow}>
                <span className={styles.shareIcon}>
                  <Icon size={18} />
                </span>
                <div className={styles.shareText}>
                  <Text font="main-ui-body" nowrap>
                    {label}
                  </Text>
                  {s.resource_path && s.resource_path !== label && (
                    <Text font="secondary-body" color="text-03" nowrap>
                      {s.resource_path}
                    </Text>
                  )}
                </div>
                <PermSelect
                  value={s.permission}
                  onChange={(p) => setPermission(key, p)}
                />
                <Button
                  prominence="tertiary"
                  size="sm"
                  variant="danger"
                  icon={SvgX}
                  tooltip="Remove"
                  onClick={() => removeShare(key)}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PermSelect({
  value,
  onChange,
}: {
  value: Permission;
  onChange: (p: Permission) => void;
}) {
  const [open, setOpen] = useState(false);
  const label = value === "write" ? "Can edit" : "Can view";
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <span className={styles.pickerTrigger}>
          <OpenButton
            variant="select-light"
            size="sm"
            icon={value === "write" ? SvgEdit : SvgEye}
          >
            {label}
          </OpenButton>
        </span>
      </Popover.Trigger>
      <Popover.Content width="fit" align="end" sideOffset={4}>
        <PopoverMenu>
          <LineItemButton
            icon={SvgEye}
            title="Can view"
            sizePreset="main-ui"
            variant="body"
            state={value === "read" ? "selected" : "empty"}
            rightChildren={
              value === "read" ? <SvgCheck size={16} /> : undefined
            }
            onClick={() => {
              onChange("read");
              setOpen(false);
            }}
          />
          <LineItemButton
            icon={SvgEdit}
            title="Can edit"
            sizePreset="main-ui"
            variant="body"
            state={value === "write" ? "selected" : "empty"}
            rightChildren={
              value === "write" ? <SvgCheck size={16} /> : undefined
            }
            onClick={() => {
              onChange("write");
              setOpen(false);
            }}
          />
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}

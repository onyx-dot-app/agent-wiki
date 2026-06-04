"use client";

import { useMemo, useState } from "react";

import {
  Button,
  InputTypeIn,
  Table,
  Text,
  createTableColumns,
} from "@onyx-ai/opal/components";
import { Content, IllustrationContent } from "@onyx-ai/opal/layouts";
import { SvgMinusCircle, SvgPlusCircle, SvgUser, SvgUserShield } from "@onyx-ai/opal/icons";
import { SvgNoResult } from "@onyx-ai/opal/illustrations";
import type { IconProps } from "@onyx-ai/opal/types";

import { Avatar } from "@/components/common/Avatar";
import { displayName, initials } from "@/lib/users";

import styles from "./groups.module.css";

export interface MemberRow {
  id: string;
  email: string;
  name: string | null;
  is_admin: boolean;
}

const PAGE_SIZE = 10;
const tc = createTableColumns<MemberRow>();

function nameCell(_value: string, row: MemberRow) {
  return (
    <Content
      sizePreset="main-ui"
      variant="section"
      title={displayName(row)}
      description={row.name ? row.email : undefined}
    />
  );
}

function accountTypeCell(_value: boolean, row: MemberRow) {
  const Icon = row.is_admin ? SvgUserShield : SvgUser;
  return (
    <span className={styles.acctType}>
      <Icon size={16} />
      <Text font="main-ui-body" color="text-03">
        {row.is_admin ? "Admin" : "Basic"}
      </Text>
    </span>
  );
}

const baseColumns = [
  tc.qualifier({
    content: "icon",
    iconSize: "lg",
    getContent:
      (row) =>
      function RowAvatar(props: IconProps) {
        return <Avatar label={initials(row)} size={props.size ?? 28} title={displayName(row)} />;
      },
  }),
  tc.column("email", { header: "Name", weight: 30, cell: nameCell }),
  tc.column("is_admin", {
    header: "Account Type",
    weight: 18,
    enableSorting: false,
    cell: accountTypeCell,
  }),
];

const addColumns = [...baseColumns, tc.actions({ showSorting: false })];

/** Member management matching Onyx's EditGroupPage: an "Add" mode that shows
 * every user as a multi-select checkbox table, and a view mode listing
 * current members with a per-row remove. Controlled — the parent owns the
 * selected-ids state and commits it on save/create. */
export function GroupMembersTable({
  allRows,
  selectedIds,
  onSelectionChange,
  defaultAdding = false,
}: {
  allRows: MemberRow[];
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  defaultAdding?: boolean;
}) {
  const [isAdding, setIsAdding] = useState(defaultAdding);
  const [search, setSearch] = useState("");

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const memberRows = useMemo(
    () => allRows.filter((r) => selectedSet.has(r.id)),
    [allRows, selectedSet],
  );
  const currentRowSelection = useMemo(
    () => Object.fromEntries(selectedIds.map((id) => [id, true])),
    [selectedIds],
  );
  // Preserve any selected ids that aren't in the visible candidate list so a
  // partial onSelectionChange can't silently drop them.
  const hiddenIds = useMemo(() => {
    const visible = new Set(allRows.map((r) => r.id));
    return selectedIds.filter((id) => !visible.has(id));
  }, [allRows, selectedIds]);

  const viewColumns = useMemo(
    () => [
      ...baseColumns,
      tc.actions({
        showSorting: false,
        showColumnVisibility: false,
        cell: (row: MemberRow) => (
          <Button
            prominence="tertiary"
            size="sm"
            variant="danger"
            icon={SvgMinusCircle}
            tooltip="Remove"
            onClick={() => onSelectionChange(selectedIds.filter((id) => id !== row.id))}
          />
        ),
      }),
    ],
    [selectedIds, onSelectionChange],
  );

  return (
    <div className={styles.fieldGroup}>
      <div className={styles.sectionHead}>
        <Text font="main-ui-action" color="text-04">
          {isAdding ? "Add members" : `Members (${memberRows.length})`}
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

      <InputTypeIn
        searchIcon
        variant="internal"
        placeholder={isAdding ? "Search users…" : "Search members…"}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {isAdding ? (
        <Table
          key="add"
          data={allRows}
          columns={addColumns}
          getRowId={(r) => r.id}
          pageSize={PAGE_SIZE}
          searchTerm={search}
          selectionBehavior="multi-select"
          initialRowSelection={currentRowSelection}
          onSelectionChange={(ids) => onSelectionChange([...ids, ...hiddenIds])}
          footer={{}}
          emptyState={
            <IllustrationContent
              illustration={SvgNoResult}
              title="No users found"
              description="No users match your search."
            />
          }
        />
      ) : (
        <Table
          key="view"
          data={memberRows}
          columns={viewColumns}
          getRowId={(r) => r.id}
          pageSize={PAGE_SIZE}
          searchTerm={search}
          footer={{}}
          emptyState={
            <IllustrationContent
              illustration={SvgNoResult}
              title="No members"
              description="Add members to this group."
            />
          }
        />
      )}
    </div>
  );
}

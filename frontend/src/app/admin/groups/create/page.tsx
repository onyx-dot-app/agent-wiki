"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { Button, Divider, InputTypeIn, Text } from "@onyx-ai/opal/components";
import { SvgUsers } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";

import { RequireAdmin } from "@/components/RequireAdmin";
import {
  addGroupMember,
  createGroup,
  grantAcl,
  useWikiPaths,
} from "@/lib/permissions";
import { useAdminUsers } from "@/lib/users";

import { GroupMembersTable, type MemberRow } from "../GroupMembersTable";
import { GroupSharesEditor, type ShareDraft } from "../GroupSharesEditor";
import styles from "../groups.module.css";

export default function AdminCreateGroupPage() {
  return (
    <RequireAdmin>
      <CreateGroup />
    </RequireAdmin>
  );
}

function CreateGroup() {
  const router = useRouter();
  const { users } = useAdminUsers();
  const { entries: wikiEntries } = useWikiPaths();

  const [name, setName] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [shares, setShares] = useState<ShareDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const allRows = useMemo<MemberRow[]>(
    () =>
      users.map((u) => ({
        id: u.id,
        email: u.email,
        name: u.name,
        is_admin: u.is_admin,
      })),
    [users],
  );

  async function create() {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Group name is required.");
      return;
    }
    setBusy(true);
    setError(null);
    // Tracks the created group across the try/catch: createGroup commits the
    // row first, so if a later member/share step fails the group still exists.
    let createdId: string | null = null;
    try {
      const g = await createGroup(trimmed);
      createdId = g.id;
      for (const id of selectedIds) await addGroupMember(g.id, id);
      for (const s of shares) {
        await grantAcl({
          resource_kind: s.resource_kind,
          resource_path: s.resource_path,
          principal_kind: "group",
          principal_id: g.id,
          permission: s.permission,
        });
      }
      router.push(`/admin/groups/${g.id}`);
    } catch (e) {
      // If the group was already created, retrying here would re-POST and 409
      // (name taken), stranding the admin with an orphan group. Send them to
      // its edit page to finish against the real (partially-applied) state.
      if (createdId) {
        router.push(`/admin/groups/${createdId}`);
        return;
      }
      setError(e instanceof Error ? e.message : "Failed to create group");
      setBusy(false);
    }
  }

  const headerActions = (
    <span className={styles.headerActions}>
      <Button
        prominence="secondary"
        size="md"
        onClick={() => router.push("/admin/groups")}
        disabled={busy}
      >
        Cancel
      </Button>
      <Button
        variant="action"
        size="md"
        disabled={!name.trim() || busy}
        onClick={() => void create()}
      >
        {busy ? "Creating…" : "Create"}
      </Button>
    </span>
  );

  return (
    <SettingsLayouts.Root width="md">
      <SettingsLayouts.Header
        icon={SvgUsers}
        title="Create Group"
        backButton
        rightChildren={headerActions}
      />
      <SettingsLayouts.Body>
        <div className={styles.fieldGroup}>
          <Text font="main-ui-body" color="text-04">
            Group Name
          </Text>
          <InputTypeIn
            placeholder="Name your group"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <Divider />

        <GroupMembersTable
          allRows={allRows}
          selectedIds={selectedIds}
          onSelectionChange={setSelectedIds}
          defaultAdding
        />

        <Divider />

        <GroupSharesEditor
          shares={shares}
          onChange={setShares}
          wikiEntries={wikiEntries}
          defaultAdding
        />

        {error && (
          <Text font="secondary-body" color="text-02">
            {error}
          </Text>
        )}
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}

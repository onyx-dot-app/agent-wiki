"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useSWRConfig } from "swr";

import {
  Button,
  Card,
  Divider,
  InputTypeIn,
  Text,
} from "@onyx-ai/opal/components";
import { SvgTrash, SvgUsers } from "@onyx-ai/opal/icons";
import { IllustrationContent, SettingsLayouts } from "@onyx-ai/opal/layouts";
import { SvgNoResult } from "@onyx-ai/opal/illustrations";

import { RequireAdmin } from "@/components/RequireAdmin";
import {
  addGroupMember,
  deleteGroup,
  grantAcl,
  removeGroupMember,
  renameGroup,
  revokeAcl,
  useGroup,
  useGroupShares,
  useWikiPaths,
} from "@/lib/permissions";
import { useAdminUsers } from "@/lib/users";

import { GroupMembersTable, type MemberRow } from "../GroupMembersTable";
import {
  GroupSharesEditor,
  shareKey,
  type ShareDraft,
} from "../GroupSharesEditor";
import styles from "../groups.module.css";

export default function AdminEditGroupPage() {
  return (
    <RequireAdmin>
      <EditGroup />
    </RequireAdmin>
  );
}

function EditGroup() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const groupId = params.id;
  const { mutate } = useSWRConfig();

  const { group, members, isLoading, error, refresh } = useGroup(groupId);
  const { users } = useAdminUsers();
  const {
    shares: serverShares,
    isLoading: sharesLoading,
    refresh: refreshShares,
  } = useGroupShares(groupId);
  const { entries: wikiEntries } = useWikiPaths();

  // Editable local state, seeded once from the server.
  const [initialized, setInitialized] = useState(false);
  const [name, setName] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [shares, setShares] = useState<ShareDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [error2, setError2] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Server snapshots for diffing on save.
  const initialNameRef = useRef("");
  const initialMemberIdsRef = useRef<string[]>([]);
  const initialSharesRef = useRef<ShareDraft[]>([]);

  useEffect(() => {
    // Wait for BOTH group and shares to load — group can arrive first, and
    // seeding then would capture an empty share set and hide existing grants
    // (and a save would revoke them).
    if (initialized || !group || sharesLoading) return;
    setName(group.name);
    initialNameRef.current = group.name;
    const ids = members.map((m) => m.id);
    setSelectedIds(ids);
    initialMemberIdsRef.current = ids;
    const drafts: ShareDraft[] = serverShares.map((s) => ({
      resource_kind: s.resource_kind,
      resource_path: s.resource_path,
      permission: s.permission,
      id: s.id,
    }));
    setShares(drafts);
    initialSharesRef.current = drafts;
    setInitialized(true);
  }, [initialized, group, members, serverShares, sharesLoading]);

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

  const dirty = useMemo(() => {
    if (!initialized) return false;
    if (name.trim() !== initialNameRef.current) return true;
    const initMembers = new Set(initialMemberIdsRef.current);
    if (
      selectedIds.length !== initMembers.size ||
      selectedIds.some((id) => !initMembers.has(id))
    )
      return true;
    const initByKey = new Map(
      initialSharesRef.current.map((s) => [shareKey(s), s.permission]),
    );
    const locByKey = new Map(shares.map((s) => [shareKey(s), s.permission]));
    if (initByKey.size !== locByKey.size) return true;
    for (const [k, perm] of locByKey)
      if (initByKey.get(k) !== perm) return true;
    return false;
  }, [initialized, name, selectedIds, shares]);

  async function save() {
    const trimmed = name.trim();
    if (!trimmed) {
      setError2("Group name is required.");
      return;
    }
    setBusy(true);
    setError2(null);
    try {
      if (trimmed !== initialNameRef.current)
        await renameGroup(groupId, trimmed);

      const initMembers = new Set(initialMemberIdsRef.current);
      const localMembers = new Set(selectedIds);
      for (const id of selectedIds)
        if (!initMembers.has(id)) await addGroupMember(groupId, id);
      for (const id of initialMemberIdsRef.current)
        if (!localMembers.has(id)) await removeGroupMember(groupId, id);

      const initByKey = new Map(
        initialSharesRef.current.map((s) => [shareKey(s), s]),
      );
      const locByKey = new Map(shares.map((s) => [shareKey(s), s]));
      // revoke removed or permission-changed
      for (const [key, init] of initByKey) {
        const loc = locByKey.get(key);
        if ((!loc || loc.permission !== init.permission) && init.id)
          await revokeAcl(init.id);
      }
      // grant new or permission-changed
      for (const [key, loc] of locByKey) {
        const init = initByKey.get(key);
        if (!init || init.permission !== loc.permission) {
          await grantAcl({
            resource_kind: loc.resource_kind,
            resource_path: loc.resource_path,
            principal_kind: "group",
            principal_id: groupId,
            permission: loc.permission,
          });
        }
      }

      void mutate("/groups");
      await Promise.all([refresh(), refreshShares()]);
      router.push("/admin/groups");
    } catch (e) {
      setError2(e instanceof Error ? e.message : "Failed to save group");
      await Promise.all([refresh(), refreshShares()]);
      setBusy(false);
    }
  }

  function onDelete() {
    if (!group) return;
    if (!confirm(`Delete group "${group.name}"? Members aren't deleted.`))
      return;
    setIsDeleting(true);
    setError2(null);
    deleteGroup(groupId)
      .then(() => {
        void mutate("/groups");
        router.push("/admin/groups");
      })
      .catch((e) => {
        setError2(e instanceof Error ? e.message : "Failed to delete group");
        setIsDeleting(false);
      });
  }

  if (isLoading || (!initialized && !error && group)) {
    return (
      <SettingsLayouts.Root width="md">
        <SettingsLayouts.Header icon={SvgUsers} title="Edit Group" backButton />
        <SettingsLayouts.Body>
          <Text font="secondary-body" color="text-03">
            Loading…
          </Text>
        </SettingsLayouts.Body>
      </SettingsLayouts.Root>
    );
  }

  if (error || !group) {
    return (
      <SettingsLayouts.Root width="md">
        <SettingsLayouts.Header icon={SvgUsers} title="Group" backButton />
        <SettingsLayouts.Body>
          <IllustrationContent
            illustration={SvgNoResult}
            title="Group not found"
            description="This group doesn't exist or may have been deleted."
          />
        </SettingsLayouts.Body>
      </SettingsLayouts.Root>
    );
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
        disabled={!dirty || busy}
        onClick={() => void save()}
      >
        {busy ? "Saving…" : "Save Changes"}
      </Button>
    </span>
  );

  return (
    <SettingsLayouts.Root width="md">
      <SettingsLayouts.Header
        icon={SvgUsers}
        title="Edit Group"
        backButton
        rightChildren={headerActions}
      />
      <SettingsLayouts.Body>
        <div className={styles.fieldGroup}>
          <Text font="main-ui-body" color="text-04">
            Group Name
          </Text>
          <InputTypeIn
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name your group"
          />
        </div>

        <Divider />

        <GroupMembersTable
          allRows={allRows}
          selectedIds={selectedIds}
          onSelectionChange={setSelectedIds}
        />

        <Divider />

        <GroupSharesEditor
          shares={shares}
          onChange={setShares}
          wikiEntries={wikiEntries}
        />

        {error2 && (
          <Text font="secondary-body" color="text-02">
            {error2}
          </Text>
        )}

        <Card rounding="lg" padding="fit">
          <div className={styles.deleteCard}>
            <div className={styles.deleteText}>
              <Text font="main-ui-body">Delete this group</Text>
              <Text font="secondary-body" color="text-03">
                Members will lose access to anything shared with this group.
              </Text>
            </div>
            <Button
              variant="danger"
              prominence="secondary"
              size="md"
              icon={SvgTrash}
              disabled={busy || isDeleting}
              onClick={onDelete}
            >
              Delete Group
            </Button>
          </div>
        </Card>
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}

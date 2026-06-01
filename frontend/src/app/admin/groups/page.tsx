"use client";

import { useMemo, useState } from "react";

import {
  Button,
  InputTypeIn,
  LineItemButton,
  OpenButton,
  Popover,
  PopoverMenu,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgChevronLeft,
  SvgChevronRight,
  SvgPlus,
  SvgTrash,
  SvgUser,
  SvgUserPlus,
  SvgUsers,
  SvgX,
} from "@onyx-ai/opal/icons";

import { Avatar } from "@/components/common/Avatar";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { ApiError } from "@/lib/api";
import {
  addGroupMember,
  createGroup,
  deleteGroup,
  removeGroupMember,
  useGroup,
  useGroups,
  type Group,
} from "@/lib/permissions";
import { useIsMobile } from "@/lib/viewport";
import { displayName, initials, useAdminUsers } from "@/lib/users";

import styles from "./groups.module.css";

function groupSub(g: Group): string {
  const parts: string[] = [];
  if (g.folder_count > 0) {
    parts.push(`${g.folder_count} ${g.folder_count === 1 ? "folder" : "folders"}`);
  }
  if (g.page_count > 0) {
    parts.push(`${g.page_count} wiki ${g.page_count === 1 ? "page" : "pages"}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "No private pages";
}

export default function AdminGroupsPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main
        style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 880 }}
      >
        <BackLink />
        <PageHeader
          title="Groups"
          description="Groups bundle users so wiki pages can be shared with everyone at once."
        />
        <GroupsManager />
      </main>
    </RequireAdmin>
  );
}

function GroupsManager() {
  const { groups, error, isLoading, refresh } = useGroups();
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return groups.filter((g) => !q || g.name.toLowerCase().includes(q));
  }, [groups, query]);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setCreateError(null);
    try {
      const g = await createGroup(name.trim(), description.trim() || undefined);
      setName("");
      setDescription("");
      setCreating(false);
      await refresh();
      setSelected(g.id);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create group");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(g: Group) {
    if (!confirm(`Delete group "${g.name}"? Members aren't deleted.`)) return;
    await deleteGroup(g.id);
    if (selected === g.id) setSelected(null);
    await refresh();
  }

  if (selected) {
    const g = groups.find((x) => x.id === selected);
    return (
      <GroupDetail
        groupId={selected}
        onBack={() => setSelected(null)}
        onDelete={g ? () => void onDelete(g) : undefined}
      />
    );
  }

  if (error)
    return (
      <Text font="secondary-body" color="text-02">
        {error.message}
      </Text>
    );

  return (
    <>
      <div className={styles.toolbar}>
        <div className={styles.searchWrap}>
          <InputTypeIn
            searchIcon
            placeholder="Search groups…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button
          variant="action"
          size="md"
          rightIcon={SvgPlus}
          onClick={() => setCreating((v) => !v)}
        >
          New Group
        </Button>
      </div>

      {creating && (
        <form className={styles.createCard} onSubmit={onCreate}>
          <InputTypeIn
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Group name (e.g. Engineering)"
          />
          <InputTypeIn
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description (optional)"
          />
          <div className={styles.createRow}>
            <Button type="submit" variant="action" size="md" disabled={busy || !name.trim()}>
              Create
            </Button>
            <Button
              type="button"
              prominence="tertiary"
              size="md"
              onClick={() => setCreating(false)}
            >
              Cancel
            </Button>
          </div>
          {createError && (
            <Text font="secondary-body" color="text-02">
              {createError}
            </Text>
          )}
        </form>
      )}

      {isLoading ? (
        <Text font="secondary-body" color="text-03">
          Loading…
        </Text>
      ) : filtered.length === 0 ? (
        <Text font="secondary-body" color="text-03">
          {query ? "No groups match your search." : "No groups yet."}
        </Text>
      ) : (
        <div className={styles.cards}>
          {filtered.map((g) => (
            <div key={g.id} className={styles.card}>
              <LineItemButton
                icon={SvgUsers}
                title={g.name}
                description={groupSub(g)}
                sizePreset="main-content"
                variant="section"
                rightChildren={
                  <span className={styles.cardRight}>
                    <Text font="secondary-body" color="text-03">
                      {`${g.member_count} ${g.member_count === 1 ? "Member" : "Members"}`}
                    </Text>
                    <SvgChevronRight size={18} />
                  </span>
                }
                onClick={() => setSelected(g.id)}
              />
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function GroupDetail({
  groupId,
  onBack,
  onDelete,
}: {
  groupId: string;
  onBack: () => void;
  onDelete?: () => void;
}) {
  const { group, members, isLoading, refresh } = useGroup(groupId);
  const { users } = useAdminUsers();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const candidates = useMemo(() => {
    const memberIds = new Set(members.map((m) => m.id));
    return users.filter((u) => !memberIds.has(u.id));
  }, [users, members]);

  async function add(userId: string) {
    setBusy(true);
    setError(null);
    try {
      await addGroupMember(groupId, userId);
      setPickerOpen(false);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add member");
    } finally {
      setBusy(false);
    }
  }

  async function remove(userId: string) {
    setBusy(true);
    try {
      await removeGroupMember(groupId, userId);
      await refresh();
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (isLoading || !group)
    return (
      <Text font="secondary-body" color="text-03">
        Loading…
      </Text>
    );

  return (
    <div>
      <Button prominence="tertiary" size="sm" icon={SvgChevronLeft} onClick={onBack}>
        All groups
      </Button>
      <div className={styles.detailHead}>
        <SvgUsers size={22} />
        <Text as="h2" font="heading-h3">
          {group.name}
        </Text>
        <span className={styles.detailSpacer} />
        {onDelete && (
          <Button
            prominence="tertiary"
            size="sm"
            variant="danger"
            icon={SvgTrash}
            onClick={onDelete}
          >
            Delete group
          </Button>
        )}
      </div>
      {group.description && (
        <Text font="secondary-body" color="text-03">
          {group.description}
        </Text>
      )}

      <div className={styles.sectionTitle}>
        <Text font="main-ui-action" color="text-02">
          Add member
        </Text>
      </div>
      <div className={styles.addRow}>
        <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
          <Popover.Trigger asChild>
            <span className={styles.pickerTrigger}>
              <OpenButton variant="select-light" size="md" icon={SvgUserPlus}>
                {candidates.length ? "Choose a user…" : "No users to add"}
              </OpenButton>
            </span>
          </Popover.Trigger>
          <Popover.Content width="trigger" align="start" sideOffset={4}>
            <PopoverMenu>
              {candidates.map((u) => (
                <LineItemButton
                  key={u.id}
                  icon={SvgUser}
                  title={displayName({ name: u.name, email: u.email })}
                  description={u.email}
                  sizePreset="main-ui"
                  variant="section"
                  onClick={() => void add(u.id)}
                />
              ))}
            </PopoverMenu>
          </Popover.Content>
        </Popover>
      </div>
      {error && (
        <Text font="secondary-body" color="text-02">
          {error}
        </Text>
      )}

      <div className={styles.sectionTitle}>
        <Text font="main-ui-action" color="text-02">
          {`Members (${members.length})`}
        </Text>
      </div>
      {members.length === 0 ? (
        <Text font="secondary-body" color="text-03">
          No members yet.
        </Text>
      ) : (
        members.map((m) => (
          <div key={m.id} className={styles.memberRow}>
            <Avatar
              label={initials({ name: m.name, email: m.email })}
              size={28}
              title={displayName({ name: m.name, email: m.email })}
            />
            <div className={styles.memberText}>
              <Text font="main-ui-body" nowrap>
                {displayName({ name: m.name, email: m.email })}
              </Text>
              <Text font="secondary-body" color="text-03" nowrap>
                {m.email}
              </Text>
            </div>
            <Button
              prominence="tertiary"
              size="sm"
              variant="danger"
              icon={SvgX}
              onClick={() => void remove(m.id)}
              disabled={busy}
            >
              Remove
            </Button>
          </div>
        ))
      )}
    </div>
  );
}

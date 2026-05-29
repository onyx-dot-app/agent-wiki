"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@onyx-ai/opal/components";
import {
  SvgChevronLeft,
  SvgChevronRight,
  SvgPlus,
  SvgSearch,
  SvgTrash,
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
    return <GroupDetail groupId={selected} onBack={() => setSelected(null)} />;
  }

  if (error) return <div className={styles.error}>{error.message}</div>;

  return (
    <>
      <div className={styles.toolbar}>
        <span className={styles.search}>
          <SvgSearch size={16} />
          <input
            className={styles.searchInput}
            placeholder="Search groups…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </span>
        <Button
          variant="action"
          size="md"
          icon={SvgPlus}
          onClick={() => setCreating((v) => !v)}
        >
          New Group
        </Button>
      </div>

      {creating && (
        <form className={styles.createCard} onSubmit={onCreate}>
          <input
            className={styles.input}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Group name (e.g. Engineering)"
            autoFocus
          />
          <input
            className={styles.input}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description (optional)"
          />
          <div className={styles.createRow}>
            <Button
              type="submit"
              variant="action"
              size="md"
              disabled={busy || !name.trim()}
            >
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
          {createError && <div className={styles.error}>{createError}</div>}
        </form>
      )}

      {isLoading ? (
        <div className={styles.loading}>Loading…</div>
      ) : filtered.length === 0 ? (
        <div className={styles.empty}>
          {query ? "No groups match your search." : "No groups yet."}
        </div>
      ) : (
        <div className={styles.cards}>
          {filtered.map((g) => (
            <button
              key={g.id}
              type="button"
              className={styles.card}
              onClick={() => setSelected(g.id)}
            >
              <span className={styles.cardIcon}>
                <SvgUsers size={22} />
              </span>
              <span className={styles.cardText}>
                <span className={styles.cardName}>{g.name}</span>
                <span className={styles.cardSub}>{groupSub(g)}</span>
              </span>
              <span className={styles.cardRight}>
                {g.member_count} {g.member_count === 1 ? "Member" : "Members"}
                <SvgChevronRight size={18} />
              </span>
              <span
                className={styles.delete}
                role="button"
                tabIndex={0}
                aria-label={`Delete ${g.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  void onDelete(g);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.stopPropagation();
                    void onDelete(g);
                  }
                }}
              >
                <SvgTrash size={16} />
              </span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function GroupDetail({
  groupId,
  onBack,
}: {
  groupId: string;
  onBack: () => void;
}) {
  const { group, members, isLoading, refresh } = useGroup(groupId);
  const { users } = useAdminUsers();
  const [pickedId, setPickedId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const candidates = useMemo(() => {
    const memberIds = new Set(members.map((m) => m.id));
    return users.filter((u) => !memberIds.has(u.id));
  }, [users, members]);

  async function add() {
    if (!pickedId) return;
    setBusy(true);
    setError(null);
    try {
      await addGroupMember(groupId, pickedId);
      setPickedId("");
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
    return <div className={styles.loading}>Loading…</div>;

  return (
    <div>
      <Button prominence="tertiary" size="sm" icon={SvgChevronLeft} onClick={onBack}>
        All groups
      </Button>
      <div className={styles.detailHead}>
        <SvgUsers size={22} />
        <h2 className={styles.detailTitle}>{group.name}</h2>
      </div>
      {group.description && <p className={styles.detailDesc}>{group.description}</p>}

      <div className={styles.sectionTitle}>Add member</div>
      <div className={styles.addRow}>
        <select
          className={styles.input}
          value={pickedId}
          onChange={(e) => setPickedId(e.target.value)}
        >
          <option value="">Choose a user…</option>
          {candidates.map((u) => (
            <option key={u.id} value={u.id}>
              {u.email}
              {u.name ? ` (${u.name})` : ""}
            </option>
          ))}
        </select>
        <Button
          variant="action"
          size="md"
          icon={SvgUserPlus}
          onClick={() => void add()}
          disabled={!pickedId || busy}
        >
          Add
        </Button>
      </div>
      {error && <div className={styles.error}>{error}</div>}

      <div className={styles.sectionTitle}>Members ({members.length})</div>
      {members.length === 0 ? (
        <div className={styles.empty}>No members yet.</div>
      ) : (
        members.map((m) => (
          <div key={m.id} className={styles.memberRow}>
            <Avatar
              label={initials({ name: m.name, email: m.email })}
              size={28}
              title={displayName({ name: m.name, email: m.email })}
            />
            <span className={styles.memberText}>
              <span className={styles.memberName}>
                {displayName({ name: m.name, email: m.email })}
              </span>
              <span className={styles.memberSub}>{m.email}</span>
            </span>
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

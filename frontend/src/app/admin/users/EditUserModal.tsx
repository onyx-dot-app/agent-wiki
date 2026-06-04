"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  Button,
  InputTypeIn,
  LineItemButton,
  Popover,
  PopoverMenu,
  Text,
} from "@onyx-ai/opal/components";
import { SvgCheck, SvgLogOut, SvgUsers } from "@onyx-ai/opal/icons";

import {
  addGroupMember,
  removeGroupMember,
  useGroups,
} from "@/lib/permissions";
import { displayName } from "@/lib/users";

import styles from "./users.module.css";

export interface EditUserTarget {
  id: string;
  email: string;
  name: string | null;
  groups: string[]; // group names the user currently belongs to
}

/** Manage which groups a user belongs to. Mirrors Onyx's EditUserModal
 * (groups portion) — search + toggle, joined list with remove, save the
 * diff via add/remove member. agent-wiki has no roles, so only groups. */
export default function EditUserModal({
  user,
  onClose,
  onMutate,
}: {
  user: EditUserTarget;
  onClose: () => void;
  onMutate: () => void;
}) {
  const { groups: allGroups, isLoading: groupsLoading } = useGroups();

  // Seed membership ONCE groups have loaded — reading it synchronously at
  // mount would capture an empty set while /groups is still fetching, and
  // saving would then drop every group the user is in.
  const [memberIds, setMemberIds] = useState<Set<string>>(new Set());
  const [initialized, setInitialized] = useState(false);
  const initialMemberIdsRef = useRef<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialized || groupsLoading) return;
    const ids = new Set(
      allGroups.filter((g) => user.groups.includes(g.name)).map((g) => g.id),
    );
    setMemberIds(ids);
    initialMemberIdsRef.current = ids;
    setInitialized(true);
  }, [initialized, groupsLoading, allGroups, user.groups]);

  const dropdownGroups = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? allGroups.filter((g) => g.name.toLowerCase().includes(q)) : allGroups;
  }, [allGroups, search]);

  const joinedGroups = useMemo(
    () => allGroups.filter((g) => memberIds.has(g.id)),
    [allGroups, memberIds],
  );

  const hasChanges =
    initialized &&
    (memberIds.size !== initialMemberIdsRef.current.size ||
      [...memberIds].some((id) => !initialMemberIdsRef.current.has(id)));

  function toggle(groupId: string) {
    setMemberIds((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const toAdd = [...memberIds].filter((id) => !initialMemberIdsRef.current.has(id));
      const toRemove = [...initialMemberIdsRef.current].filter((id) => !memberIds.has(id));
      for (const gid of toAdd) await addGroupMember(gid, user.id);
      for (const gid of toRemove) await removeGroupMember(gid, user.id);
      onMutate();
      onClose();
    } catch (e) {
      onMutate();
      setError(e instanceof Error ? e.message : "Failed to update groups");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={styles.scrim}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-label="Edit groups">
        <header className={styles.dialogHead}>
          <span className={styles.pageIcon}>
            <SvgUsers size={20} />
          </span>
          <div className={styles.dialogHeadText}>
            <Text as="h2" font="main-content-emphasis">
              Edit Groups
            </Text>
            <Text font="secondary-body" color="text-03">
              {user.name ? `${displayName(user)} (${user.email})` : user.email}
            </Text>
          </div>
        </header>

        <div className={styles.dialogBody}>
          <Popover
            open={pickerOpen}
            onOpenChange={(o) => {
              setPickerOpen(o);
              if (!o) setSearch("");
            }}
          >
            <Popover.Anchor asChild>
              <div>
                <InputTypeIn
                  searchIcon
                  placeholder="Search groups to join…"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setPickerOpen(true);
                  }}
                  onFocus={() => setPickerOpen(true)}
                />
              </div>
            </Popover.Anchor>
            <Popover.Content
              width="trigger"
              align="start"
              sideOffset={4}
              onOpenAutoFocus={(e) => e.preventDefault()}
            >
              <PopoverMenu>
                {dropdownGroups.length === 0
                  ? [
                      <div key="empty" className={styles.menuEmpty}>
                        <Text font="secondary-body" color="text-03">
                          No groups found
                        </Text>
                      </div>,
                    ]
                  : dropdownGroups.map((g) => {
                      const isMember = memberIds.has(g.id);
                      return (
                        <LineItemButton
                          key={g.id}
                          icon={isMember ? SvgCheck : SvgUsers}
                          title={g.name}
                          description={`${g.member_count} ${g.member_count === 1 ? "user" : "users"}`}
                          sizePreset="main-ui"
                          variant="section"
                          state={isMember ? "selected" : "empty"}
                          onClick={() => toggle(g.id)}
                        />
                      );
                    })}
              </PopoverMenu>
            </Popover.Content>
          </Popover>

          <div className={styles.egJoined}>
            {joinedGroups.length === 0 ? (
              <Text font="secondary-body" color="text-03">
                {`${displayName(user)} is not in any groups.`}
              </Text>
            ) : (
              joinedGroups.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  className={styles.egRow}
                  onClick={() => toggle(g.id)}
                >
                  <span className={styles.egRowIcon}>
                    <SvgUsers size={18} />
                  </span>
                  <span className={styles.egRowText}>
                    <Text font="main-ui-body" nowrap>
                      {g.name}
                    </Text>
                    <Text font="secondary-body" color="text-03" nowrap>
                      {`${g.member_count} ${g.member_count === 1 ? "user" : "users"}`}
                    </Text>
                  </span>
                  <span className={styles.egRowRemove}>
                    <SvgLogOut size={16} />
                  </span>
                </button>
              ))
            )}
          </div>

          {error && (
            <Text font="secondary-body" color="text-02">
              {error}
            </Text>
          )}
        </div>

        <footer className={styles.dialogFoot}>
          <Button prominence="secondary" size="md" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="action"
            size="md"
            disabled={busy || !hasChanges}
            onClick={() => void save()}
          >
            {busy ? "Saving…" : "Save Changes"}
          </Button>
        </footer>
      </div>
    </div>
  );
}

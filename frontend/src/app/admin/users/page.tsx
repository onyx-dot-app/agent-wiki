"use client";

import { useMemo, useState } from "react";

import {
  Button,
  InputTypeIn,
  LineItemButton,
  OpenButton,
  Popover,
  PopoverMenu,
  Tag,
  Text,
} from "@onyx-ai/opal/components";
import { SvgTrash, SvgUser, SvgUserShield } from "@onyx-ai/opal/icons";

import { Avatar } from "@/components/common/Avatar";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useIsMobile } from "@/lib/viewport";
import {
  displayName,
  initials,
  useAdminUsers,
  type AdminUser,
} from "@/lib/users";

import styles from "./users.module.css";

export default function AdminUsersPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main
        style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 960 }}
      >
        <BackLink />
        <PageHeader
          title="Users"
          description="Promote or demote admins, or remove accounts. The last admin cannot be demoted or deleted."
        />
        <UsersTable />
      </main>
    </RequireAdmin>
  );
}

function UsersTable() {
  const { user: currentUser } = useAuth();
  const currentUserId = currentUser?.id ?? "";
  const isMobile = useIsMobile();
  const { users, error: loadError, refresh } = useAdminUsers();
  const [query, setQuery] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        u.email.toLowerCase().includes(q) ||
        (u.name ?? "").toLowerCase().includes(q),
    );
  }, [users, query]);

  async function setAdmin(u: AdminUser, makeAdmin: boolean) {
    if (u.is_admin === makeAdmin) return;
    setBusyId(u.id);
    setActionError(null);
    try {
      await apiFetch<AdminUser>(`/admin/users/${u.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_admin: makeAdmin }),
      });
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Failed to update user");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(u: AdminUser) {
    if (!confirm(`Delete ${u.email}? This cannot be undone.`)) return;
    setBusyId(u.id);
    setActionError(null);
    try {
      await apiFetch<void>(`/admin/users/${u.id}`, { method: "DELETE" });
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Failed to delete user");
    } finally {
      setBusyId(null);
    }
  }

  const cols = isMobile
    ? "1fr auto auto"
    : "minmax(0, 2fr) minmax(0, 1.5fr) 130px 110px 70px";

  return (
    <div>
      <div className={styles.stat}>
        <Text font="secondary-body" color="text-03">
          {`${users.length} ${users.length === 1 ? "active user" : "active users"}`}
        </Text>
      </div>
      <div className={styles.searchWrap}>
        <InputTypeIn
          searchIcon
          placeholder="Search users…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {(actionError || loadError) && (
        <div className={styles.errorRow}>
          <Text font="secondary-body" color="text-02">
            {actionError ?? loadError?.message ?? ""}
          </Text>
        </div>
      )}

      <div className={styles.table} style={{ ["--cols" as string]: cols }}>
        <div className={styles.headRow}>
          <Text font="secondary-action" color="text-03">
            Name
          </Text>
          {!isMobile && (
            <Text font="secondary-action" color="text-03">
              Groups
            </Text>
          )}
          <Text font="secondary-action" color="text-03">
            Account type
          </Text>
          {!isMobile && (
            <Text font="secondary-action" color="text-03">
              Created
            </Text>
          )}
          <span />
        </div>

        {filtered.length === 0 ? (
          <div className={styles.emptyRow}>
            <Text font="secondary-body" color="text-03">
              No users match your search.
            </Text>
          </div>
        ) : (
          filtered.map((u) => {
            const isSelf = u.id === currentUserId;
            const busy = busyId === u.id;
            return (
              <div key={u.id} className={styles.row}>
                <span className={styles.who}>
                  <Avatar label={initials(u)} size={32} title={displayName(u)} />
                  <span className={styles.whoText}>
                    <Text font="main-ui-body" nowrap>
                      {displayName(u)}
                    </Text>
                    <Text font="secondary-body" color="text-03" nowrap>
                      {u.email}
                    </Text>
                  </span>
                </span>

                {!isMobile && (
                  <span className={styles.tags}>
                    {u.groups.length === 0 ? (
                      <Text font="secondary-body" color="text-05">
                        —
                      </Text>
                    ) : (
                      u.groups.map((g) => <Tag key={g} title={g} color="gray" />)
                    )}
                  </span>
                )}

                <AccountTypeSelect
                  isAdmin={u.is_admin}
                  disabled={busy || (u.is_admin && isSelf)}
                  onChange={(makeAdmin) => void setAdmin(u, makeAdmin)}
                />

                {!isMobile && (
                  <Text font="secondary-body" color="text-03" nowrap>
                    {u.created_at.split(" ")[0]}
                  </Text>
                )}

                <span className={styles.actions}>
                  <Button
                    prominence="tertiary"
                    size="sm"
                    variant="danger"
                    icon={SvgTrash}
                    onClick={() => void remove(u)}
                    disabled={busy || isSelf}
                    tooltip={isSelf ? "You can't delete yourself" : "Delete user"}
                  />
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function AccountTypeSelect({
  isAdmin,
  disabled,
  onChange,
}: {
  isAdmin: boolean;
  disabled?: boolean;
  onChange: (makeAdmin: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <span className={styles.menuTrigger}>
          <OpenButton
            variant="select-light"
            size="sm"
            icon={isAdmin ? SvgUserShield : SvgUser}
            disabled={disabled}
          >
            {isAdmin ? "Admin" : "Basic"}
          </OpenButton>
        </span>
      </Popover.Trigger>
      <Popover.Content width="fit" align="start" sideOffset={4}>
        <PopoverMenu>
          <LineItemButton
            icon={SvgUser}
            title="Basic"
            sizePreset="main-ui"
            variant="section"
            state={!isAdmin ? "selected" : "empty"}
            onClick={() => {
              onChange(false);
              setOpen(false);
            }}
          />
          <LineItemButton
            icon={SvgUserShield}
            title="Admin"
            sizePreset="main-ui"
            variant="section"
            state={isAdmin ? "selected" : "empty"}
            onClick={() => {
              onChange(true);
              setOpen(false);
            }}
          />
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}

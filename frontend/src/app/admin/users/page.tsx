"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  Button,
  Card,
  FilterButton,
  InputTypeIn,
  LineItemButton,
  OpenButton,
  Popover,
  PopoverMenu,
  Tag,
  Text,
  Tooltip,
} from "@onyx-ai/opal/components";
import {
  SvgAlertTriangle,
  SvgCheck,
  SvgDownload,
  SvgMail,
  SvgMoreHorizontal,
  SvgUser,
  SvgUserPlus,
  SvgUsers,
  SvgUserShield,
  SvgX,
} from "@onyx-ai/opal/icons";

import { IllustrationContent, SettingsLayouts } from "@onyx-ai/opal/layouts";
import { SvgNoResult } from "@onyx-ai/opal/illustrations";

import { Avatar } from "@/components/common/Avatar";
import { RequireAdmin } from "@/components/RequireAdmin";
import { useAuth } from "@/lib/auth";
import { useIsMobile } from "@/lib/viewport";
import {
  cancelInvite,
  deleteUser,
  displayName,
  downloadUsersCsv,
  initials,
  inviteUsers,
  relativeTime,
  setUserActive,
  setUserAdmin,
  useAdminUsers,
  type AdminUser,
  type UserStatus,
} from "@/lib/users";

import EditUserModal, { type EditUserTarget } from "./EditUserModal";

import styles from "./users.module.css";

const PAGE_SIZE = 10;

// A table row is either a real account or a pending invite.
type Row =
  | { kind: "user"; id: string; user: AdminUser }
  | { kind: "invited"; id: string; email: string };

const STATUS_LABEL: Record<UserStatus, string> = {
  active: "Active",
  inactive: "Inactive",
  invited: "Invite Pending",
};

export default function AdminUsersPage() {
  const [inviteOpen, setInviteOpen] = useState(false);
  return (
    <RequireAdmin>
      <SettingsLayouts.Root width="lg">
        <SettingsLayouts.Header
          icon={SvgUser}
          title="Users & Requests"
          backButton
          rightChildren={
            <Button
              variant="action"
              size="md"
              icon={SvgUserPlus}
              onClick={() => setInviteOpen(true)}
            >
              Invite Users
            </Button>
          }
        />
        <SettingsLayouts.Body>
          <UsersTable />
        </SettingsLayouts.Body>
      </SettingsLayouts.Root>
      <InviteUsersModal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
      />
    </RequireAdmin>
  );
}

function UsersTable() {
  const { user: currentUser } = useAuth();
  const currentUserId = currentUser?.id ?? "";
  const isMobile = useIsMobile();
  const { users, invited, counts, error: loadError, refresh } = useAdminUsers();

  const [query, setQuery] = useState("");
  const [groupFilter, setGroupFilter] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<UserStatus | null>(null);
  const [page, setPage] = useState(0);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [editUser, setEditUser] = useState<EditUserTarget | null>(null);

  // Group names + how many users hold each — drives the Groups filter list.
  const groupCounts = useMemo(() => {
    const m = new Map<string, number>();
    users.forEach((u) =>
      u.groups.forEach((g) => m.set(g, (m.get(g) ?? 0) + 1)),
    );
    return m;
  }, [users]);
  const allGroups = useMemo(
    () => [...groupCounts.keys()].sort(),
    [groupCounts],
  );

  const statusCounts = useMemo(
    () => ({
      active: counts.active,
      inactive: counts.inactive,
      invited: counts.invited,
    }),
    [counts],
  );

  const rows = useMemo<Row[]>(() => {
    const userRows: Row[] = users.map((u) => ({
      kind: "user",
      id: u.id,
      user: u,
    }));
    const invitedRows: Row[] = invited.map((i) => ({
      kind: "invited",
      id: `invite:${i.email}`,
      email: i.email,
    }));
    return [...userRows, ...invitedRows];
  }, [users, invited]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      const status: UserStatus =
        r.kind === "invited" ? "invited" : r.user.status;
      if (statusFilter && status !== statusFilter) return false;
      if (groupFilter) {
        if (r.kind === "invited" || !r.user.groups.includes(groupFilter))
          return false;
      }
      if (!q) return true;
      if (r.kind === "invited") return r.email.toLowerCase().includes(q);
      return (
        r.user.email.toLowerCase().includes(q) ||
        (r.user.name ?? "").toLowerCase().includes(q)
      );
    });
  }, [rows, query, statusFilter, groupFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pageRows = filtered.slice(
    safePage * PAGE_SIZE,
    (safePage + 1) * PAGE_SIZE,
  );

  async function withBusy(id: string, fn: () => Promise<void>) {
    setBusyId(id);
    setActionError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusyId(null);
    }
  }

  const cols = isMobile
    ? "minmax(0,1fr) 120px 44px"
    : "minmax(0,2fr) minmax(0,1.4fr) 132px 120px 110px 44px";

  return (
    <div>
      {/* Stats */}
      <div className={styles.statsWrap}>
        <Card rounding="lg" padding="fit">
          <div className={styles.stats}>
            <StatCell
              value={counts.active}
              label="active users"
              onClick={() => {
                setStatusFilter("active");
                setPage(0);
              }}
            />
            <span className={styles.statDivider} />
            <StatCell
              value={counts.invited}
              label="pending invites"
              onClick={() => {
                setStatusFilter("invited");
                setPage(0);
              }}
            />
          </div>
        </Card>
      </div>

      <div className={styles.searchWrap}>
        <InputTypeIn
          searchIcon
          placeholder="Search users…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
          }}
        />
      </div>

      {/* Filters */}
      <div className={styles.filters}>
        <GroupFilter
          groups={allGroups}
          counts={groupCounts}
          value={groupFilter}
          onChange={(v) => {
            setGroupFilter(v);
            setPage(0);
          }}
        />
        <StatusFilter
          value={statusFilter}
          counts={statusCounts}
          onChange={(v) => {
            setStatusFilter(v);
            setPage(0);
          }}
        />
        <span className={styles.headSpacer} />
        <Button
          prominence="tertiary"
          size="sm"
          icon={SvgDownload}
          tooltip="Download CSV"
          onClick={() => void downloadUsersCsv()}
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
          {!isMobile && (
            <Text font="secondary-action" color="text-03">
              Account Type
            </Text>
          )}
          <Text font="secondary-action" color="text-03">
            Status
          </Text>
          {!isMobile && (
            <Text font="secondary-action" color="text-03">
              Last Updated
            </Text>
          )}
          <span />
        </div>

        {pageRows.length === 0 ? (
          <div className={styles.emptyRow}>
            <IllustrationContent
              illustration={SvgNoResult}
              title="No users found"
              description="Try adjusting your search or filters."
            />
          </div>
        ) : (
          pageRows.map((r) =>
            r.kind === "user" ? (
              <UserRowView
                key={r.id}
                u={r.user}
                isMobile={isMobile}
                isSelf={r.user.id === currentUserId}
                busy={busyId === r.id}
                onSetAdmin={(makeAdmin) =>
                  void withBusy(r.id, () => setUserAdmin(r.user.id, makeAdmin))
                }
                onSetActive={(active) =>
                  void withBusy(r.id, () => setUserActive(r.user.id, active))
                }
                onDelete={() =>
                  void withBusy(r.id, () => deleteUser(r.user.id))
                }
                onEditGroups={() =>
                  setEditUser({
                    id: r.user.id,
                    email: r.user.email,
                    name: r.user.name,
                    groups: r.user.groups,
                  })
                }
              />
            ) : (
              <InvitedRowView
                key={r.id}
                email={r.email}
                isMobile={isMobile}
                busy={busyId === r.id}
                onCancel={() =>
                  void withBusy(r.id, () => cancelInvite(r.email))
                }
              />
            ),
          )
        )}
      </div>

      <div className={styles.tableFoot}>
        <Text font="secondary-body" color="text-03">
          {filtered.length === 0
            ? "No users"
            : `Showing ${safePage * PAGE_SIZE + 1}–${Math.min((safePage + 1) * PAGE_SIZE, filtered.length)} of ${filtered.length}`}
        </Text>
        {totalPages > 1 && (
          <span className={styles.pager}>
            <Button
              prominence="tertiary"
              size="sm"
              disabled={safePage === 0}
              onClick={() => setPage(safePage - 1)}
            >
              Prev
            </Button>
            <Text font="secondary-body" color="text-03">
              {`${safePage + 1} / ${totalPages}`}
            </Text>
            <Button
              prominence="tertiary"
              size="sm"
              disabled={safePage >= totalPages - 1}
              onClick={() => setPage(safePage + 1)}
            >
              Next
            </Button>
          </span>
        )}
      </div>

      {editUser && (
        <EditUserModal
          user={editUser}
          onClose={() => setEditUser(null)}
          onMutate={refresh}
        />
      )}
    </div>
  );
}

function StatCell({
  value,
  label,
  onClick,
}: {
  value: number;
  label: string;
  onClick: () => void;
}) {
  return (
    <button type="button" className={styles.statCell} onClick={onClick}>
      <Text font="main-content-emphasis">{value.toLocaleString()}</Text>
      <Text font="secondary-body" color="text-03">
        {label}
      </Text>
    </button>
  );
}

// --------------------------------------------------------------------------- //
// Filters — Onyx FilterButton chrome (clearable X, count badges)              //
// --------------------------------------------------------------------------- //

function GroupFilter({
  groups,
  counts,
  value,
  onChange,
}: {
  groups: string[];
  counts: Map<string, number>;
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const filtered = search
    ? groups.filter((g) => g.toLowerCase().includes(search.toLowerCase()))
    : groups;
  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setSearch("");
      }}
    >
      <Popover.Trigger asChild>
        <FilterButton
          icon={SvgUsers}
          active={value !== null}
          onClear={() => onChange(null)}
        >
          {value ?? "All Groups"}
        </FilterButton>
      </Popover.Trigger>
      <Popover.Content width="fit" align="start" sideOffset={4}>
        <PopoverMenu>
          {groups.length > 6 && (
            <InputTypeIn
              searchIcon
              variant="internal"
              placeholder="Search groups…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          )}
          <LineItemButton
            icon={value === null ? SvgCheck : SvgUsers}
            title="All Groups"
            sizePreset="main-ui"
            variant="body"
            state={value === null ? "selected" : "empty"}
            onClick={() => {
              onChange(null);
              setOpen(false);
            }}
          />
          {filtered.map((g) => (
            <LineItemButton
              key={g}
              icon={value === g ? SvgCheck : SvgUsers}
              title={g}
              sizePreset="main-ui"
              variant="body"
              state={value === g ? "selected" : "empty"}
              rightChildren={
                <Text font="secondary-body" color="text-03">
                  {String(counts.get(g) ?? 0)}
                </Text>
              }
              onClick={() => {
                onChange(g);
                setOpen(false);
              }}
            />
          ))}
          {filtered.length === 0 && (
            <div className={styles.menuEmpty}>
              <Text font="secondary-body" color="text-03">
                No groups found
              </Text>
            </div>
          )}
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}

const STATUS_OPTIONS: {
  value: UserStatus;
  label: string;
  key: "active" | "inactive" | "invited";
}[] = [
  { value: "active", label: "Active", key: "active" },
  { value: "inactive", label: "Inactive", key: "inactive" },
  { value: "invited", label: "Invite Pending", key: "invited" },
];

function StatusFilter({
  value,
  counts,
  onChange,
}: {
  value: UserStatus | null;
  counts: { active: number; inactive: number; invited: number };
  onChange: (v: UserStatus | null) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <FilterButton
          icon={SvgUserShield}
          active={value !== null}
          onClear={() => onChange(null)}
        >
          {value ? STATUS_LABEL[value] : "All Status"}
        </FilterButton>
      </Popover.Trigger>
      <Popover.Content width="fit" align="start" sideOffset={4}>
        <PopoverMenu>
          <LineItemButton
            icon={value === null ? SvgCheck : SvgUser}
            title="All Status"
            sizePreset="main-ui"
            variant="body"
            state={value === null ? "selected" : "empty"}
            onClick={() => {
              onChange(null);
              setOpen(false);
            }}
          />
          {STATUS_OPTIONS.map((opt) => (
            <LineItemButton
              key={opt.value}
              icon={value === opt.value ? SvgCheck : SvgUser}
              title={opt.label}
              sizePreset="main-ui"
              variant="body"
              state={value === opt.value ? "selected" : "empty"}
              rightChildren={
                <Text font="secondary-body" color="text-03">
                  {String(counts[opt.key])}
                </Text>
              }
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
            />
          ))}
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}

// --------------------------------------------------------------------------- //
// Group chips — measured overflow ("+N"), tooltip with all groups             //
// --------------------------------------------------------------------------- //

function GroupsCell({
  groups,
  onClick,
}: {
  groups: string[];
  onClick?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [visibleCount, setVisibleCount] = useState<number | null>(null);

  // Re-enter the measurement phase whenever the group set changes.
  useLayoutEffect(() => {
    setVisibleCount(null);
  }, [groups]);

  // After the "render all" phase, measure how many pills fit.
  useLayoutEffect(() => {
    if (visibleCount !== null) return;
    const container = containerRef.current;
    if (!container || groups.length <= 1) {
      setVisibleCount(groups.length);
      return;
    }
    const tags = container.querySelectorAll<HTMLElement>("[data-group-tag]");
    if (tags.length === 0) return;
    const containerWidth = container.clientWidth;
    const gap = 4;
    const counterWidth = 34; // approximate "+N" pill width
    let used = 0;
    let count = 0;
    for (let i = 0; i < tags.length; i++) {
      const tagWidth = tags[i]!.offsetWidth;
      const gapBefore = count > 0 ? gap : 0;
      const hasMore = i < tags.length - 1;
      const reserve = hasMore ? gap + counterWidth : 0;
      if (used + gapBefore + tagWidth + reserve <= containerWidth) {
        used += gapBefore + tagWidth;
        count++;
      } else {
        break;
      }
    }
    setVisibleCount(Math.max(1, count));
  }, [visibleCount, groups]);

  // Re-measure on width changes (responsive / column resize).
  const lastWidthRef = useRef(0);
  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      if (Math.abs(width - lastWidthRef.current) < 1) return;
      lastWidthRef.current = width;
      setVisibleCount(null);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [groups]);

  const isMeasuring = visibleCount === null;
  const effectiveVisible = visibleCount ?? groups.length;
  const overflowCount = groups.length - effectiveVisible;
  const hasOverflow = !isMeasuring && overflowCount > 0;

  let inner: React.ReactNode;
  if (groups.length === 0) {
    inner = (
      <Text font="secondary-body" color="text-05">
        —
      </Text>
    );
  } else {
    const cell = (
      <div ref={containerRef} className={styles.tags}>
        {(isMeasuring ? groups : groups.slice(0, effectiveVisible)).map((g) => (
          <span key={g} data-group-tag className={styles.tagChip}>
            <Tag title={g} color="gray" />
          </span>
        ))}
        {hasOverflow && (
          <span className={styles.tagChip}>
            <Tag title={`+${overflowCount}`} color="gray" />
          </span>
        )}
      </div>
    );
    inner = hasOverflow ? (
      <Tooltip
        side="bottom"
        align="start"
        delayDuration={200}
        tooltip={
          <div className={styles.tagsTooltip}>
            {groups.map((g) => (
              <Tag key={g} title={g} color="gray" />
            ))}
          </div>
        }
      >
        {cell}
      </Tooltip>
    ) : (
      cell
    );
  }

  if (!onClick) return inner;
  return (
    <button
      type="button"
      className={styles.groupsCellButton}
      onClick={onClick}
      title="Edit groups"
    >
      {inner}
    </button>
  );
}

function StatusText({ status }: { status: UserStatus }) {
  return (
    <Text
      font="secondary-body"
      color={status === "active" ? "text-05" : "text-03"}
      nowrap
    >
      {STATUS_LABEL[status]}
    </Text>
  );
}

function UserRowView({
  u,
  isMobile,
  isSelf,
  busy,
  onSetAdmin,
  onSetActive,
  onDelete,
  onEditGroups,
}: {
  u: AdminUser;
  isMobile: boolean;
  isSelf: boolean;
  busy: boolean;
  onSetAdmin: (makeAdmin: boolean) => void;
  onSetActive: (active: boolean) => void;
  onDelete: () => void;
  onEditGroups: () => void;
}) {
  return (
    <Card rounding="lg" padding="fit">
      <div className={styles.row}>
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

        {!isMobile && <GroupsCell groups={u.groups} onClick={onEditGroups} />}

        {!isMobile && (
          <AccountTypeSelect
            isAdmin={u.is_admin}
            disabled={busy || (u.is_admin && isSelf)}
            onChange={onSetAdmin}
          />
        )}

        <StatusText status={u.status} />

        {!isMobile && (
          <Text font="secondary-body" color="text-03" nowrap>
            {relativeTime(u.updated_at)}
          </Text>
        )}

        <RowActions disabled={busy}>
          <LineItemButton
            icon={SvgUsers}
            title="Edit groups"
            sizePreset="main-ui"
            variant="body"
            onClick={onEditGroups}
          />
          <LineItemButton
            icon={u.is_active ? SvgX : SvgUser}
            title={u.is_active ? "Deactivate" : "Activate"}
            sizePreset="main-ui"
            variant="body"
            onClick={() => onSetActive(!u.is_active)}
          />
          <LineItemButton
            icon={SvgUserShield}
            title={u.is_admin ? "Make basic" : "Make admin"}
            sizePreset="main-ui"
            variant="body"
            onClick={() => onSetAdmin(!u.is_admin)}
          />
          <LineItemButton
            icon={SvgX}
            title="Delete user"
            sizePreset="main-ui"
            variant="body"
            onClick={() => {
              if (confirm(`Delete ${u.email}? This cannot be undone.`))
                onDelete();
            }}
          />
        </RowActions>
      </div>
    </Card>
  );
}

function InvitedRowView({
  email,
  isMobile,
  busy,
  onCancel,
}: {
  email: string;
  isMobile: boolean;
  busy: boolean;
  onCancel: () => void;
}) {
  return (
    <Card rounding="lg" padding="fit">
      <div className={styles.row}>
        <span className={styles.who}>
          <Avatar label={initials({ email })} size={32} title={email} />
          <span className={styles.whoText}>
            <Text font="main-ui-body" nowrap>
              {email}
            </Text>
            <Text font="secondary-body" color="text-03" nowrap>
              Invited
            </Text>
          </span>
        </span>
        {!isMobile && (
          <span className={styles.tags}>
            <Text font="secondary-body" color="text-05">
              —
            </Text>
          </span>
        )}
        {!isMobile && (
          <Text font="secondary-body" color="text-05" nowrap>
            Basic
          </Text>
        )}
        <StatusText status="invited" />
        {!isMobile && (
          <Text font="secondary-body" color="text-05" nowrap>
            —
          </Text>
        )}
        <RowActions disabled={busy}>
          <LineItemButton
            icon={SvgX}
            title="Cancel invite"
            sizePreset="main-ui"
            variant="body"
            onClick={onCancel}
          />
        </RowActions>
      </div>
    </Card>
  );
}

function RowActions({
  disabled,
  children,
}: {
  disabled?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const items: React.ReactNode[] = Array.isArray(children)
    ? children
    : [children];
  return (
    <span className={styles.actions}>
      <Popover open={open} onOpenChange={setOpen}>
        <Popover.Trigger asChild>
          <span className={styles.menuTrigger}>
            <Button
              prominence="tertiary"
              size="sm"
              icon={SvgMoreHorizontal}
              disabled={disabled}
              tooltip="Actions"
            />
          </span>
        </Popover.Trigger>
        <Popover.Content width="fit" align="end" sideOffset={4}>
          <PopoverMenu>{items}</PopoverMenu>
        </Popover.Content>
      </Popover>
    </span>
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
            variant="body"
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
            variant="body"
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

// --------------------------------------------------------------------------- //
// Invite modal — chip field (type email + Enter; chips are removable)         //
// --------------------------------------------------------------------------- //

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface Chip {
  id: string;
  label: string;
  error: boolean;
}

function parseEmails(value: string, existing: Chip[]): Chip[] {
  const entries = value
    .split(/[\s,;]+/)
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
  const next: Chip[] = [];
  for (const email of entries) {
    const dup =
      existing.some((c) => c.label === email) ||
      next.some((c) => c.label === email);
    if (!dup)
      next.push({ id: email, label: email, error: !EMAIL_REGEX.test(email) });
  }
  return next;
}

function InviteUsersModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { refresh } = useAdminUsers();
  const [chips, setChips] = useState<Chip[]>([]);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  function reset() {
    setChips([]);
    setValue("");
    setError(null);
    setBusy(false);
  }

  function close() {
    onClose();
    setTimeout(reset, 200);
  }

  function commit(raw: string) {
    const added = parseEmails(raw, chips);
    if (added.length > 0) setChips((prev) => [...prev, ...added]);
    setValue("");
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === "," || e.key === ";") {
      e.preventDefault();
      if (value.trim()) commit(value);
    } else if (e.key === "Backspace" && !value && chips.length > 0) {
      setChips((prev) => prev.slice(0, -1));
    }
  }

  function removeChip(id: string) {
    setChips((prev) => prev.filter((c) => c.id !== id));
  }

  async function submit() {
    const pending = value.trim();
    const all = pending ? [...chips, ...parseEmails(pending, chips)] : chips;
    if (pending) {
      setChips(all);
      setValue("");
    }
    const valid = all.filter((c) => !c.error).map((c) => c.label);
    if (valid.length === 0) {
      setError("Add at least one valid email address.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await inviteUsers(valid);
      await refresh();
      close();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to invite users");
    } finally {
      setBusy(false);
    }
  }

  const hasInvalid = chips.some((c) => c.error);

  return (
    <div
      className={styles.scrim}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) close();
      }}
    >
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label="Invite users"
      >
        <header className={styles.dialogHead}>
          <span className={styles.pageIcon}>
            <SvgMail size={20} />
          </span>
          <div className={styles.dialogHeadText}>
            <Text as="h2" font="main-content-emphasis">
              Invite Users
            </Text>
            <Text font="secondary-body" color="text-03">
              They&apos;ll be able to sign up with these email addresses.
            </Text>
          </div>
          <Button
            prominence="tertiary"
            size="sm"
            icon={SvgX}
            tooltip="Close"
            onClick={close}
            disabled={busy}
          />
        </header>
        <div className={styles.dialogBody}>
          <ChipField
            chips={chips}
            value={value}
            onChange={setValue}
            onKeyDown={onKeyDown}
            onBlur={() => value.trim() && commit(value)}
            onRemove={removeChip}
          />
          {hasInvalid && (
            <div className={styles.chipWarn}>
              <SvgAlertTriangle size={14} />
              <Text font="secondary-body" color="text-03">
                Some email addresses are invalid and will be skipped.
              </Text>
            </div>
          )}
          {error && (
            <Text font="secondary-body" color="text-02">
              {error}
            </Text>
          )}
        </div>
        <footer className={styles.dialogFoot}>
          <Button
            prominence="secondary"
            size="md"
            onClick={close}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            variant="action"
            size="md"
            disabled={busy || (chips.length > 0 && chips.every((c) => c.error))}
            onClick={() => void submit()}
          >
            {busy ? "Inviting…" : "Invite"}
          </Button>
        </footer>
      </div>
    </div>
  );
}

function ChipField({
  chips,
  value,
  onChange,
  onKeyDown,
  onBlur,
  onRemove,
}: {
  chips: Chip[];
  value: string;
  onChange: (v: string) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  onBlur: () => void;
  onRemove: (id: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className={styles.chipField} onClick={() => inputRef.current?.focus()}>
      {chips.map((c) => (
        <span
          key={c.id}
          className={
            c.error ? `${styles.chip} ${styles.chipError}` : styles.chip
          }
        >
          <Text font="secondary-body" color={c.error ? "text-02" : "text-04"}>
            {c.label}
          </Text>
          <button
            type="button"
            className={styles.chipRemove}
            aria-label={`Remove ${c.label}`}
            onClick={(e) => {
              e.stopPropagation();
              onRemove(c.id);
            }}
          >
            <SvgX size={12} />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        className={styles.chipInput}
        value={value}
        placeholder={chips.length === 0 ? "Add an email and press Enter" : ""}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
      />
    </div>
  );
}

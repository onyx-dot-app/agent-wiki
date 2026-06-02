"use client";

import { useEffect, useMemo, useState } from "react";

import {
  Button,
  Divider,
  InputTypeIn,
  LineItemButton,
  OpenButton,
  Popover,
  PopoverMenu,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgArrowExchange,
  SvgCheck,
  SvgEdit,
  SvgEye,
  SvgGlobe,
  SvgLink,
  SvgLock,
  SvgShare,
  SvgUser,
  SvgUsers,
  SvgUserShield,
  SvgX,
} from "@onyx-ai/opal/icons";

import { Avatar } from "@/components/common/Avatar";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  grantAcl,
  revokeAcl,
  useGroups,
  usePageAcl,
  type AclEntry,
  type Permission,
  type ResourceKind,
  type Visibility,
} from "@/lib/permissions";
import { displayName, initials, useUserSearch, type UserLite } from "@/lib/users";
import { lastSegment } from "@/lib/wiki";
import { markdown } from "@onyx-ai/opal/utils";

import { TransferModal } from "./TransferModal";
import styles from "./ShareDialog.module.css";

interface ShareDialogProps {
  path: string;
  open: boolean;
  onClose: () => void;
}

type PrincipalKind = "user" | "group";

interface GrantDraft {
  kind: PrincipalKind;
  id: string;
  permission: Permission;
  email?: string | null;
  name?: string | null;
  groupName?: string | null;
}

interface Baseline {
  grants: Map<string, GrantDraft>;
  general: Visibility;
  // All ACL row IDs per principal — a principal can hold multiple rows
  // (e.g. read + write); removing them must revoke every row, not just the
  // strongest, or the weaker one resurfaces on refresh.
  entryIdByKey: Map<string, string[]>;
  everyoneReadId: string | null;
  everyoneWriteId: string | null;
  inherited: AclEntry[];
}

const EMPTY_BASELINE: Baseline = {
  grants: new Map(),
  general: "private",
  entryIdByKey: new Map(),
  everyoneReadId: null,
  everyoneWriteId: null,
  inherited: [],
};

function keyFor(kind: PrincipalKind, id: string): string {
  return `${kind}:${id}`;
}

function deriveBaseline(
  acl: { path: string; entries: AclEntry[] } | null,
): Baseline {
  if (!acl) return EMPTY_BASELINE;
  const grants = new Map<string, GrantDraft>();
  const entryIdByKey = new Map<string, string[]>();
  let everyoneReadId: string | null = null;
  let everyoneWriteId: string | null = null;
  const inherited: AclEntry[] = [];

  for (const e of acl.entries) {
    const own = e.resource_path === acl.path;
    if (!own) {
      inherited.push(e);
      continue;
    }
    if (e.principal_kind === "everyone") {
      if (e.permission === "write") everyoneWriteId = e.id;
      else if (e.permission === "read") everyoneReadId = e.id;
      continue;
    }
    if (e.principal_kind !== "user" && e.principal_kind !== "group") continue;
    if (!e.principal_id) continue;
    const k = keyFor(e.principal_kind, e.principal_id);
    // Record every row ID for the principal so removal revokes them all.
    const ids = entryIdByKey.get(k) ?? [];
    ids.push(e.id);
    entryIdByKey.set(k, ids);
    // Display the strongest grant (write > read); upgrade read → write but
    // never downgrade.
    const existing = grants.get(k);
    if (!existing || (existing.permission !== "write" && e.permission === "write")) {
      grants.set(k, {
        kind: e.principal_kind,
        id: e.principal_id,
        permission: e.permission,
        email: e.principal_email,
        name: e.principal_name,
        groupName: e.group_name,
      });
    }
  }

  const general: Visibility = everyoneWriteId
    ? "public-write"
    : everyoneReadId
      ? "public-read"
      : "private";

  return {
    grants,
    general,
    entryIdByKey,
    everyoneReadId,
    everyoneWriteId,
    inherited,
  };
}

function grantsEqual(a: Map<string, GrantDraft>, b: Map<string, GrantDraft>) {
  if (a.size !== b.size) return false;
  for (const [k, v] of a) {
    const o = b.get(k);
    if (!o || o.permission !== v.permission) return false;
  }
  return true;
}

export function ShareDialog({ path, open, onClose }: ShareDialogProps) {
  const resourceKind: ResourceKind = path.endsWith(".md") ? "page" : "folder";
  const { acl, error, isLoading, refresh } = usePageAcl(open ? path : null);
  const { groups } = useGroups();
  const { user } = useAuth();

  const baseline = useMemo(() => deriveBaseline(acl), [acl]);

  const [grants, setGrants] = useState<Map<string, GrantDraft>>(new Map());
  // Scope (who) and the general permission (what) are independent: the
  // permission stays selectable even while "Only those invited" is chosen —
  // it only takes effect (as an `everyone` grant) once scope is "anyone".
  const [scope, setScope] = useState<"invited" | "anyone">("invited");
  const [generalPerm, setGeneralPerm] = useState<Permission>("read");
  const general: Visibility =
    scope === "invited"
      ? "private"
      : generalPerm === "write"
        ? "public-write"
        : "public-read";
  const [query, setQuery] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Reset working state to the loaded baseline whenever the ACL changes.
  // Does NOT clear saveError — a save failure re-pulls the ACL (changing
  // baseline), and the error must survive that refresh so the user sees it.
  useEffect(() => {
    if (!open) return;
    setGrants(new Map(baseline.grants));
    setScope(baseline.general === "private" ? "invited" : "anyone");
    setGeneralPerm(baseline.general === "public-write" ? "write" : "read");
  }, [baseline, open]);

  // Clear any stale save error only on (re)open, not on baseline refresh.
  useEffect(() => {
    if (open) setSaveError(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setPickerOpen(false);
    setCopied(false);
  }, [open]);

  // Escape to close.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const userSearchEnabled = open && pickerOpen;
  const { users: userResults } = useUserSearch(query, userSearchEnabled);

  const groupResults = useMemo(() => {
    const q = query.trim().toLowerCase();
    return groups.filter((g) => !q || g.name.toLowerCase().includes(q));
  }, [groups, query]);

  if (!open) return null;

  const ownerId = acl?.owner_user_id ?? null;
  const dirty = !grantsEqual(grants, baseline.grants) || general !== baseline.general;

  const addUser = (u: UserLite) => {
    const k = keyFor("user", u.id);
    if (grants.has(k) || u.id === ownerId) return;
    const next = new Map(grants);
    next.set(k, { kind: "user", id: u.id, permission: "read", email: u.email, name: u.name });
    setGrants(next);
    setQuery("");
    setPickerOpen(false);
  };
  const addGroup = (gid: string, name: string) => {
    const k = keyFor("group", gid);
    if (grants.has(k)) return;
    const next = new Map(grants);
    next.set(k, { kind: "group", id: gid, permission: "read", groupName: name });
    setGrants(next);
    setQuery("");
    setPickerOpen(false);
  };
  const setPermission = (k: string, permission: Permission) => {
    const cur = grants.get(k);
    if (!cur) return;
    const next = new Map(grants);
    next.set(k, { ...cur, permission });
    setGrants(next);
  };
  const removeGrant = (k: string) => {
    const next = new Map(grants);
    next.delete(k);
    setGrants(next);
  };

  const copyLink = async () => {
    try {
      const trimmed = path.replace(/\/+$/, "");
      const encodedPath = trimmed
        .split("/")
        .filter(Boolean)
        .map((segment) => encodeURIComponent(segment))
        .join("/");
      const targetPath = encodedPath ? `/app/wiki/${encodedPath}` : "/app/wiki";
      const shareUrl = `${window.location.origin}${targetPath}`;
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — no-op */
    }
  };

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const revokes: string[] = [];
      const adds: GrantDraft[] = [];
      const keys = new Set([...baseline.grants.keys(), ...grants.keys()]);
      for (const k of keys) {
        const o = baseline.grants.get(k);
        const n = grants.get(k);
        if (n && !o) adds.push(n);
        else if (o && !n) {
          revokes.push(...(baseline.entryIdByKey.get(k) ?? []));
        } else if (o && n && o.permission !== n.permission) {
          revokes.push(...(baseline.entryIdByKey.get(k) ?? []));
          adds.push(n);
        }
      }

      const desiredRead = general === "public-read";
      const desiredWrite = general === "public-write";
      if (baseline.everyoneReadId && !desiredRead) revokes.push(baseline.everyoneReadId);
      if (baseline.everyoneWriteId && !desiredWrite) revokes.push(baseline.everyoneWriteId);

      for (const id of revokes) await revokeAcl(id);
      for (const g of adds) {
        await grantAcl({
          resource_kind: resourceKind,
          resource_path: path,
          principal_kind: g.kind,
          principal_id: g.id,
          permission: g.permission,
        });
      }
      if (desiredRead && !baseline.everyoneReadId) {
        await grantAcl({
          resource_kind: resourceKind,
          resource_path: path,
          principal_kind: "everyone",
          principal_id: null,
          permission: "read",
        });
      }
      if (desiredWrite && !baseline.everyoneWriteId) {
        await grantAcl({
          resource_kind: resourceKind,
          resource_path: path,
          principal_kind: "everyone",
          principal_id: null,
          permission: "write",
        });
      }
      await refresh();
      onClose();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to save changes");
      // Re-pull the ACL so the baseline reflects whatever partially applied;
      // otherwise a retry re-issues already-committed grants/revokes and 404s.
      await refresh();
    } finally {
      setSaving(false);
    }
  };

  const forbidden = error instanceof ApiError && error.status === 403;
  const kindNoun = resourceKind === "folder" ? "folder" : "page";

  const pickerGroups = groupResults;
  const pickerUsers = userResults;
  const hasResults = pickerGroups.length > 0 || pickerUsers.length > 0;
  const showPicker = pickerOpen && hasResults;

  return (
    <div
      className={styles.scrim}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={`Share ${lastSegment(path)}`}
      >
        <header className={styles.header}>
          <span className={styles.headerIcon}>
            <SvgShare size={20} />
          </span>
          <div className={styles.headerText}>
            <Text as="h2" font="main-content-emphasis">
              {markdown(`Share *${lastSegment(path)}*`)}
            </Text>
            <Text font="secondary-body" color="text-03">
              {`Share this ${kindNoun} with people or groups`}
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

        {forbidden ? (
          <div className={styles.content}>
            <Text font="secondary-body" color="text-02">
              {`Only the owner or an admin can manage sharing for this ${kindNoun}.`}
            </Text>
          </div>
        ) : isLoading || !acl ? (
          <div className={styles.content}>
            <Text font="secondary-body" color="text-03">
              Loading…
            </Text>
          </div>
        ) : (
          <div className={styles.content}>
            <div className={styles.cardStack}>
            {/* Add people / groups — InputTypeIn anchors a portaled results menu */}
            <Popover
              open={showPicker}
              onOpenChange={(o) => {
                if (!o) setPickerOpen(false);
              }}
            >
              <Popover.Anchor asChild>
                <div className={styles.anchorWrap}>
                  <InputTypeIn
                    searchIcon
                    placeholder="Add users and groups"
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
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
                container={typeof document !== "undefined" ? document.body : undefined}
                onOpenAutoFocus={(e) => e.preventDefault()}
                onCloseAutoFocus={(e) => e.preventDefault()}
              >
                <PopoverMenu>
                  {pickerGroups.map((g) => {
                    const already = grants.has(keyFor("group", g.id));
                    return (
                      <LineItemButton
                        key={`g-${g.id}`}
                        icon={SvgUsers}
                        title={g.name}
                        description={`${g.member_count} ${g.member_count === 1 ? "user" : "users"}`}
                        sizePreset="main-ui"
                        variant="section"
                        rightChildren={
                          already ? (
                            <Text font="secondary-body" color="text-03">
                              Shared
                            </Text>
                          ) : undefined
                        }
                        onClick={() => {
                          if (!already) addGroup(g.id, g.name);
                        }}
                      />
                    );
                  })}
                  {pickerUsers.map((u) => {
                    const already =
                      grants.has(keyFor("user", u.id)) || u.id === ownerId;
                    return (
                      <LineItemButton
                        key={`u-${u.id}`}
                        icon={SvgUser}
                        title={displayName(u)}
                        description={u.email}
                        sizePreset="main-ui"
                        variant="section"
                        rightChildren={
                          already ? (
                            <Text font="secondary-body" color="text-03">
                              Shared
                            </Text>
                          ) : undefined
                        }
                        onClick={() => {
                          if (!already) addUser(u);
                        }}
                      />
                    );
                  })}
                </PopoverMenu>
              </Popover.Content>
            </Popover>

            {saveError && (
              <Text font="secondary-body" color="text-02">
                {saveError}
              </Text>
            )}

              {/* General access */}
              <div className={styles.generalRow}>
                <span className={styles.inheritedIcon}>
                  {scope === "invited" ? (
                    <SvgLock size={18} />
                  ) : (
                    <SvgGlobe size={18} />
                  )}
                </span>
                <ScopeSelect value={scope} onChange={setScope} />
                <PermSelect boxed value={generalPerm} onChange={setGeneralPerm} />
              </div>

              <Divider paddingParallel="fit" paddingPerpendicular="2xs" />

              {/* People with access */}
              <div className={styles.list}>
                {ownerId && (
                  <OwnerRow
                    ownerId={ownerId}
                    ownerEmail={acl.owner_email ?? null}
                    ownerName={acl.owner_name ?? null}
                    isYou={ownerId === user?.id}
                    canTransfer={ownerId === user?.id || Boolean(user?.is_admin)}
                    onTransfer={() => setTransferOpen(true)}
                  />
                )}

                {[...grants.values()].map((g) => {
                const k = keyFor(g.kind, g.id);
                const group =
                  g.kind === "group" ? groups.find((x) => x.id === g.id) : undefined;
                const name =
                  g.kind === "group"
                    ? g.groupName ?? group?.name ?? "Group"
                    : displayName({ name: g.name, email: g.email ?? g.id });
                const sub =
                  g.kind === "group"
                    ? group
                      ? `${group.member_count} ${group.member_count === 1 ? "user" : "users"}`
                      : "Group"
                    : g.email ?? "";
                return (
                  <div key={k} className={styles.row}>
                    <Avatar
                      label={(name[0] ?? "?").toUpperCase()}
                      icon={g.kind === "group" ? SvgUsers : undefined}
                      size={28}
                      title={name}
                    />
                    <div className={styles.rowText}>
                      <Text font="main-ui-body" nowrap>
                        {name}
                      </Text>
                      {sub && (
                        <Text font="secondary-body" color="text-03" nowrap>
                          {sub}
                        </Text>
                      )}
                    </div>
                    <PermSelect
                      value={g.permission}
                      onChange={(p) => setPermission(k, p)}
                      onRemove={() => removeGrant(k)}
                    />
                  </div>
                );
              })}

                {baseline.inherited.map((e) => (
                  <InheritedRow key={e.id} entry={e} groups={groups} />
                ))}
              </div>
            </div>
          </div>
        )}

        <footer className={styles.footer}>
          <Button
            prominence="secondary"
            size="md"
            icon={SvgLink}
            onClick={() => void copyLink()}
          >
            {copied ? "Copied" : "Copy Link"}
          </Button>
          <span className={styles.footerRight}>
            <Button prominence="secondary" size="md" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="action"
              size="md"
              disabled={!dirty || saving || forbidden}
              onClick={() => void save()}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </span>
        </footer>
      </div>

      {transferOpen && acl && (
        <TransferModal
          path={path}
          currentOwnerId={ownerId}
          open={transferOpen}
          onClose={() => setTransferOpen(false)}
          onTransferred={() => {
            setTransferOpen(false);
            void refresh();
          }}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Sub-components                                                              //
// --------------------------------------------------------------------------- //

function PermSelect({
  value,
  onChange,
  onRemove,
  disabled,
  boxed,
}: {
  value: Permission;
  onChange: (p: Permission) => void;
  onRemove?: () => void;
  disabled?: boolean;
  boxed?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const label = value === "write" ? "Edit" : "View";
  const icon = value === "write" ? SvgEdit : SvgEye;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <span className={boxed ? styles.permBox : styles.menuTrigger}>
          <OpenButton
            variant="select-light"
            size={boxed ? "lg" : "sm"}
            icon={icon}
            disabled={disabled}
            width={boxed ? "full" : undefined}
            justifyContent={boxed ? "between" : undefined}
            rounding={boxed ? "sm" : undefined}
          >
            {label}
          </OpenButton>
        </span>
      </Popover.Trigger>
      <Popover.Content width="fit" align="end" sideOffset={4}>
        <PopoverMenu>
          <LineItemButton
            icon={SvgEye}
            title="View"
            sizePreset="main-ui"
            variant="body"
            state={value === "read" ? "selected" : "empty"}
            rightChildren={value === "read" ? <SvgCheck size={16} /> : undefined}
            onClick={() => {
              onChange("read");
              setOpen(false);
            }}
          />
          <LineItemButton
            icon={SvgEdit}
            title="Edit"
            sizePreset="main-ui"
            variant="body"
            state={value === "write" ? "selected" : "empty"}
            rightChildren={value === "write" ? <SvgCheck size={16} /> : undefined}
            onClick={() => {
              onChange("write");
              setOpen(false);
            }}
          />
          {onRemove ? <Divider paddingParallel="fit" paddingPerpendicular="2xs" /> : null}
          {onRemove ? (
            <LineItemButton
              icon={SvgX}
              title="Remove access"
              sizePreset="main-ui"
              variant="body"
              onClick={() => {
                onRemove();
                setOpen(false);
              }}
            />
          ) : undefined}
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}

function ScopeSelect({
  value,
  onChange,
}: {
  value: "invited" | "anyone";
  onChange: (v: "invited" | "anyone") => void;
}) {
  const [open, setOpen] = useState(false);
  const label = value === "invited" ? "Only those invited" : "Anyone";
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <span className={styles.scopeBox}>
          <OpenButton
            variant="select-light"
            size="lg"
            width="full"
            justifyContent="between"
            rounding="sm"
          >
            {label}
          </OpenButton>
        </span>
      </Popover.Trigger>
      <Popover.Content width="trigger" align="start" sideOffset={4}>
        <PopoverMenu>
          <LineItemButton
            icon={SvgLock}
            title="Only those invited"
            sizePreset="main-ui"
            variant="body"
            state={value === "invited" ? "selected" : "empty"}
            onClick={() => {
              if (value !== "invited") onChange("invited");
              setOpen(false);
            }}
          />
          <LineItemButton
            icon={SvgGlobe}
            title="Anyone"
            sizePreset="main-ui"
            variant="body"
            state={value === "anyone" ? "selected" : "empty"}
            onClick={() => {
              if (value !== "anyone") onChange("anyone");
              setOpen(false);
            }}
          />
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}

function OwnerRow({
  ownerId,
  ownerEmail,
  ownerName,
  isYou,
  canTransfer,
  onTransfer,
}: {
  ownerId: string;
  ownerEmail: string | null;
  ownerName: string | null;
  isYou: boolean;
  canTransfer: boolean;
  onTransfer: () => void;
}) {
  const name = displayName({ name: ownerName, email: ownerEmail ?? ownerId });
  return (
    <div className={styles.row}>
      <Avatar
        label={initials({ name: ownerName, email: ownerEmail ?? ownerId })}
        size={28}
        title={name}
      />
      <div className={styles.rowText}>
        <Text font="main-ui-body" nowrap>
          {isYou ? `${name} (you)` : name}
        </Text>
        {ownerEmail && (
          <Text font="secondary-body" color="text-03" nowrap>
            {ownerEmail}
          </Text>
        )}
      </div>
      <span className={styles.rowRight}>
        <span className={styles.inheritedIcon}>
          <SvgUserShield size={16} />
        </span>
        <Text font="secondary-body" color="text-03">
          Owner
        </Text>
        {canTransfer && (
          <Button
            prominence="tertiary"
            size="sm"
            icon={SvgArrowExchange}
            tooltip="Transfer ownership"
            onClick={onTransfer}
          />
        )}
      </span>
    </div>
  );
}

function InheritedRow({
  entry,
  groups,
}: {
  entry: AclEntry;
  groups: { id: string; name: string }[];
}) {
  let name: string;
  let Icon = SvgUser;
  if (entry.principal_kind === "everyone") {
    name = "Anyone";
    Icon = SvgGlobe;
  } else if (entry.principal_kind === "group") {
    name =
      entry.group_name ??
      groups.find((g) => g.id === entry.principal_id)?.name ??
      "Group";
    Icon = SvgUsers;
  } else {
    name = displayName({
      name: entry.principal_name,
      email: entry.principal_email ?? entry.principal_id ?? "?",
    });
  }
  const where = entry.resource_path ? `folder "${entry.resource_path}"` : "root folder";
  return (
    <div className={styles.row}>
      <span className={styles.inheritedIcon}>
        <Icon size={16} />
      </span>
      <div className={styles.rowText}>
        <Text font="main-ui-body" nowrap>
          {name}
        </Text>
        <Text font="secondary-body" color="text-03" nowrap>
          {`${entry.permission === "write" ? "Can edit" : "Can view"} · inherited from ${where}`}
        </Text>
      </div>
      <span className={styles.rowRight}>
        <Text font="secondary-body" color="text-03">
          Inherited
        </Text>
      </span>
    </div>
  );
}

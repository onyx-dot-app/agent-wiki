"use client";

import { useEffect, useMemo, useState } from "react";

import {
  Button,
  LineItemButton,
  Popover,
  SelectButton,
} from "@onyx-ai/opal/components";
import {
  SvgArrowExchange,
  SvgCheck,
  SvgChevronDown,
  SvgEdit,
  SvgEye,
  SvgGlobe,
  SvgLink,
  SvgLock,
  SvgShare,
  SvgUser,
  SvgUsers,
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
  entryIdByKey: Map<string, string>;
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

function lastSegment(path: string): string {
  const clean = path.replace(/\/+$/, "");
  if (!clean) return "Wiki";
  const seg = clean.split("/").pop() ?? clean;
  return seg.endsWith(".md") ? seg.slice(0, -3) : seg;
}

function deriveBaseline(
  acl: { path: string; entries: AclEntry[] } | null,
): Baseline {
  if (!acl) return EMPTY_BASELINE;
  const grants = new Map<string, GrantDraft>();
  const entryIdByKey = new Map<string, string>();
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
    const existing = grants.get(k);
    // Collapse a duplicate principal to its strongest grant (write > read).
    if (existing && existing.permission === "write") continue;
    grants.set(k, {
      kind: e.principal_kind,
      id: e.principal_id,
      permission: e.permission,
      email: e.principal_email,
      name: e.principal_name,
      groupName: e.group_name,
    });
    entryIdByKey.set(k, e.id);
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
  const [general, setGeneral] = useState<Visibility>("private");
  const [query, setQuery] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Reset working state to the loaded baseline whenever the ACL changes.
  useEffect(() => {
    setGrants(new Map(baseline.grants));
    setGeneral(baseline.general);
    setSaveError(null);
  }, [baseline]);

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
  };
  const addGroup = (gid: string, name: string) => {
    const k = keyFor("group", gid);
    if (grants.has(k)) return;
    const next = new Map(grants);
    next.set(k, { kind: "group", id: gid, permission: "read", groupName: name });
    setGrants(next);
    setQuery("");
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
      await navigator.clipboard.writeText(window.location.href);
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
          const id = baseline.entryIdByKey.get(k);
          if (id) revokes.push(id);
        } else if (o && n && o.permission !== n.permission) {
          const id = baseline.entryIdByKey.get(k);
          if (id) revokes.push(id);
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
    } finally {
      setSaving(false);
    }
  };

  const forbidden = error instanceof ApiError && error.status === 403;
  const kindNoun = resourceKind === "folder" ? "folder" : "page";

  // Picker candidates, excluding owner + already-granted principals (shown
  // as "Shared" rather than addable).
  const addedKeys = grants;
  const pickerGroups = groupResults;
  const pickerUsers = userResults;
  const showPicker = pickerOpen && (pickerGroups.length > 0 || pickerUsers.length > 0);

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
            <h2 className={styles.title}>
              Share <span className={styles.titleName}>{lastSegment(path)}</span>
            </h2>
            <span className={styles.subtitle}>
              Share this {kindNoun} with people or groups
            </span>
          </div>
          <button className={styles.closeBtn} onClick={onClose} aria-label="Close">
            <SvgX size={18} />
          </button>
        </header>

        {forbidden ? (
          <div className={styles.content}>
            <div className={styles.error}>
              Only the owner or an admin can manage sharing for this {kindNoun}.
            </div>
          </div>
        ) : isLoading || !acl ? (
          <div className={styles.content}>
            <div className={styles.loading}>Loading…</div>
          </div>
        ) : (
          <div className={styles.content}>
            {/* Add people / groups */}
            <div className={styles.inputWrap}>
              <input
                className={styles.input}
                placeholder="Add users and groups"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onFocus={() => setPickerOpen(true)}
                onBlur={() => window.setTimeout(() => setPickerOpen(false), 120)}
              />
              {showPicker && (
                <div className={styles.results}>
                  {pickerGroups.map((g) => {
                    const already = addedKeys.has(keyFor("group", g.id));
                    return (
                      <button
                        key={`g-${g.id}`}
                        type="button"
                        className={styles.resultRow}
                        disabled={already}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          if (!already) addGroup(g.id, g.name);
                        }}
                      >
                        <Avatar label={(g.name[0] ?? "?").toUpperCase()} size={28} />
                        <span className={styles.resultText}>
                          <span className={styles.resultName}>{g.name}</span>
                          <span className={styles.resultSub}>
                            {g.member_count} {g.member_count === 1 ? "member" : "members"}
                          </span>
                        </span>
                        {already && <span className={styles.resultTag}>Shared</span>}
                      </button>
                    );
                  })}
                  {pickerUsers.map((u) => {
                    const already =
                      addedKeys.has(keyFor("user", u.id)) || u.id === ownerId;
                    return (
                      <button
                        key={`u-${u.id}`}
                        type="button"
                        className={styles.resultRow}
                        disabled={already}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          if (!already) addUser(u);
                        }}
                      >
                        <Avatar label={initials(u)} size={28} title={displayName(u)} />
                        <span className={styles.resultText}>
                          <span className={styles.resultName}>{displayName(u)}</span>
                          <span className={styles.resultSub}>{u.email}</span>
                        </span>
                        {already && <span className={styles.resultTag}>Shared</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* General access — the scope dropdown carries the lock/globe icon */}
            <div className={styles.generalRow}>
              <ScopeSelect
                value={general === "private" ? "invited" : "anyone"}
                onChange={(scope) =>
                  setGeneral(scope === "invited" ? "private" : "public-read")
                }
              />
              <span className={styles.generalSpacer} />
              <PermSelect
                value={general === "public-write" ? "write" : "read"}
                disabled={general === "private"}
                onChange={(p) => setGeneral(p === "write" ? "public-write" : "public-read")}
              />
            </div>

            {saveError && <div className={styles.error}>{saveError}</div>}

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
                      ? `${group.member_count} ${group.member_count === 1 ? "member" : "members"}`
                      : "Group"
                    : g.email ?? "";
                return (
                  <div key={k} className={styles.row}>
                    <Avatar
                      label={(name[0] ?? "?").toUpperCase()}
                      size={28}
                      title={name}
                    />
                    <span className={styles.rowText}>
                      <span className={styles.rowName}>
                        {g.kind === "group" ? (
                          <SvgUsers size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                        ) : null}
                        {name}
                      </span>
                      {sub && <span className={styles.rowSub}>{sub}</span>}
                    </span>
                    <span className={styles.rowRight}>
                      <PermSelect
                        value={g.permission}
                        onChange={(p) => setPermission(k, p)}
                        onRemove={() => removeGrant(k)}
                      />
                    </span>
                  </div>
                );
              })}

              {baseline.inherited.map((e) => (
                <InheritedRow key={e.id} entry={e} groups={groups} />
              ))}
            </div>
          </div>
        )}

        <footer className={styles.footer}>
          <Button
            prominence="tertiary"
            size="md"
            icon={SvgLink}
            onClick={() => void copyLink()}
          >
            Copy Link
          </Button>
          <span className={styles.footerRight}>
            {copied && <span className={styles.copied}>Copied</span>}
            <Button prominence="tertiary" size="md" onClick={onClose}>
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
}: {
  value: Permission;
  onChange: (p: Permission) => void;
  onRemove?: () => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const label = value === "write" ? "Edit" : "View";
  const icon = value === "write" ? SvgEdit : SvgEye;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <span className={styles.menuTrigger}>
          <SelectButton
            size="sm"
            variant="select-light"
            icon={icon}
            rightIcon={SvgChevronDown}
            disabled={disabled}
          >
            {label}
          </SelectButton>
        </span>
      </Popover.Trigger>
      <Popover.Content width="fit" align="end" sideOffset={4}>
        <Popover.Menu>
          {[
            <LineItemButton
              key="view"
              icon={SvgEye}
              title="View"
              state={value === "read" ? "selected" : "empty"}
              rightChildren={value === "read" ? <SvgCheck size={16} /> : undefined}
              onClick={() => {
                onChange("read");
                setOpen(false);
              }}
            />,
            <LineItemButton
              key="edit"
              icon={SvgEdit}
              title="Edit"
              state={value === "write" ? "selected" : "empty"}
              rightChildren={value === "write" ? <SvgCheck size={16} /> : undefined}
              onClick={() => {
                onChange("write");
                setOpen(false);
              }}
            />,
            onRemove ? null : undefined,
            onRemove ? (
              <LineItemButton
                key="remove"
                icon={SvgX}
                title="Remove access"
                color="danger"
                onClick={() => {
                  onRemove();
                  setOpen(false);
                }}
              />
            ) : undefined,
          ]}
        </Popover.Menu>
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
  const label = value === "invited" ? "Only those invited" : "Anyone signed in";
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <span className={styles.menuTrigger}>
          <SelectButton
            size="sm"
            variant="select-light"
            icon={value === "invited" ? SvgLock : SvgGlobe}
            rightIcon={SvgChevronDown}
          >
            {label}
          </SelectButton>
        </span>
      </Popover.Trigger>
      <Popover.Content width="fit" align="start" sideOffset={4}>
        <Popover.Menu>
          {[
            <LineItemButton
              key="invited"
              icon={SvgLock}
              title="Only those invited"
              state={value === "invited" ? "selected" : "empty"}
              onClick={() => {
                if (value !== "invited") {
                  onChange("invited");
                }
                setOpen(false);
              }}
            />,
            <LineItemButton
              key="anyone"
              icon={SvgGlobe}
              title="Anyone signed in"
              state={value === "anyone" ? "selected" : "empty"}
              onClick={() => {
                if (value !== "anyone") {
                  onChange("anyone");
                }
                setOpen(false);
              }}
            />,
          ]}
        </Popover.Menu>
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
      <Avatar label={initials({ name: ownerName, email: ownerEmail ?? ownerId })} size={28} title={name} />
      <span className={styles.rowText}>
        <span className={styles.rowName}>
          {name}
          {isYou ? " (you)" : ""}
        </span>
        {ownerEmail && <span className={styles.rowSub}>{ownerEmail}</span>}
      </span>
      <span className={styles.rowRight}>
        <span className={styles.ownerTag}>Owner</span>
        {canTransfer && (
          <button
            className={styles.transferBtn}
            onClick={onTransfer}
            aria-label="Transfer ownership"
            title="Transfer ownership"
          >
            <SvgArrowExchange size={16} />
          </button>
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
  let icon = <SvgUser size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />;
  if (entry.principal_kind === "everyone") {
    name = "Anyone signed in";
    icon = <SvgGlobe size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />;
  } else if (entry.principal_kind === "group") {
    name = entry.group_name ?? groups.find((g) => g.id === entry.principal_id)?.name ?? "Group";
    icon = <SvgUsers size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />;
  } else {
    name = displayName({ name: entry.principal_name, email: entry.principal_email ?? entry.principal_id ?? "?" });
  }
  const where = entry.resource_path ? `folder "${entry.resource_path}"` : "root folder";
  return (
    <div className={styles.row}>
      <span className={styles.rowText}>
        <span className={styles.rowName}>
          {icon}
          {name}
        </span>
        <span className={styles.rowSub}>
          {entry.permission === "write" ? "Can edit" : "Can view"} · inherited from {where}
        </span>
      </span>
      <span className={styles.rowRight}>
        <span className={styles.inheritedTag}>Inherited</span>
      </span>
    </div>
  );
}

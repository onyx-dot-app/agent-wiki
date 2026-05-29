"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { ApiError, apiFetch } from "@/lib/api";
import {
  grantAcl,
  revokeAcl,
  transferOwnership,
  useGroups,
  usePageAcl,
  visibility,
  type AclEntry,
  type Permission,
  type PrincipalKind,
} from "@/lib/permissions";
import { color, radius, shadow } from "@/lib/theme";

interface AdminUser {
  id: string;
  email: string;
  name: string | null;
}

interface ShareDialogProps {
  path: string;
  open: boolean;
  onClose: () => void;
}

export function ShareDialog({ path, open, onClose }: ShareDialogProps) {
  const { acl, error, isLoading, refresh } = usePageAcl(open ? path : null);
  const { groups } = useGroups();

  // Limited-scope user list — only used for principal selection in the
  // grant form. Falls back gracefully for non-admins (the endpoint is
  // admin-only); they can still grant to groups they're in or pick
  // 'everyone'.
  const [users, setUsers] = useState<AdminUser[]>([]);
  useEffect(() => {
    if (!open) return;
    void apiFetch<{ users: AdminUser[] }>("/admin/users")
      .then((r) => setUsers(r.users))
      .catch(() => setUsers([]));
  }, [open]);

  if (!open) return null;

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={modalStyle} onClick={(e) => e.stopPropagation()}>
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18 }}>Share</h2>
            <code style={{ fontSize: 12, color: color.text.muted }}>{path}</code>
          </div>
          <Button icon={SvgX} prominence="tertiary" size="sm" tooltip="Close" onClick={onClose} />
        </header>

        {error && (
          <div style={{ color: color.state.danger.fg, marginBottom: 12 }}>
            {error instanceof ApiError && error.status === 403
              ? "Only the owner or an admin can manage sharing for this page."
              : error.message}
          </div>
        )}

        {isLoading || !acl ? (
          <LoadingSpinner />
        ) : (
          <>
            <Section title="Visibility">
              <div style={{ fontSize: 14, color: color.text.secondary }}>
                {(() => {
                  const v = visibility(acl);
                  if (v === "private") {
                    return (
                      <span>
                        <strong>Private</strong> — only the owner and explicit grants below can access.
                      </span>
                    );
                  }
                  if (v === "public-read") {
                    return (
                      <span>
                        <strong>Public (read-only)</strong> — every signed-in user can read; only the owner and explicit write grants can edit.
                      </span>
                    );
                  }
                  return (
                    <span>
                      <strong>Public</strong> — every signed-in user can read and edit.
                    </span>
                  );
                })()}
              </div>
            </Section>

            <Section title="Owner">
              <OwnerControls
                path={path}
                ownerId={acl.owner_user_id}
                users={users}
                onChanged={() => void refresh()}
              />
            </Section>

            <Section title="Grants">
              <Grants
                entries={acl.entries}
                users={users}
                groups={groups}
                onRevoke={async (id) => {
                  await revokeAcl(id);
                  await refresh();
                }}
              />
            </Section>

            <Section title="Add grant">
              <GrantForm
                path={path}
                users={users}
                groups={groups}
                onGranted={() => void refresh()}
              />
            </Section>
          </>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Sub-components                                                              //
// --------------------------------------------------------------------------- //

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 20 }}>
      <h3 style={{ fontSize: 13, fontWeight: 600, color: color.text.secondary, margin: "0 0 8px 0" }}>
        {title}
      </h3>
      {children}
    </section>
  );
}

function OwnerControls({
  path,
  ownerId,
  users,
  onChanged,
}: {
  path: string;
  ownerId: string | null;
  users: AdminUser[];
  onChanged: () => void;
}) {
  const ownerLabel = useMemo(() => {
    if (!ownerId) return "—";
    const u = users.find((x) => x.id === ownerId);
    return u ? u.email : ownerId;
  }, [ownerId, users]);
  const [transferTo, setTransferTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function transfer() {
    setBusy(true);
    setErr(null);
    try {
      await transferOwnership(path, transferTo || null);
      setTransferTo("");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div style={{ fontSize: 14, marginBottom: 8 }}>{ownerLabel}</div>
      <div style={{ display: "flex", gap: 8 }}>
        <select value={transferTo} onChange={(e) => setTransferTo(e.target.value)} style={inputStyle}>
          <option value="">Transfer ownership to…</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.email}
            </option>
          ))}
        </select>
        <Button size="sm" onClick={() => void transfer()} disabled={busy || !transferTo}>
          Transfer
        </Button>
      </div>
      {err && <div style={{ color: color.state.danger.fg, marginTop: 6, fontSize: 13 }}>{err}</div>}
    </div>
  );
}

function Grants({
  entries,
  users,
  groups,
  onRevoke,
}: {
  entries: AclEntry[];
  users: AdminUser[];
  groups: { id: string; name: string }[];
  onRevoke: (id: string) => Promise<void>;
}) {
  if (entries.length === 0) {
    return <div style={{ fontSize: 13, color: color.text.muted }}>No explicit grants.</div>;
  }
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {entries.map((e) => (
        <li
          key={e.id}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "6px 0",
            borderBottom: `1px solid ${color.border.subtle}`,
            fontSize: 13,
          }}
        >
          <span>
            <PrincipalLabel
              kind={e.principal_kind}
              id={e.principal_id}
              users={users}
              groups={groups}
            />
            {" "}
            <span style={{ color: color.text.muted }}>
              · {e.permission}
              {e.resource_kind === "folder"
                ? ` · folder${e.resource_path ? ` "${e.resource_path}"` : " (root)"}`
                : ""}
            </span>
          </span>
          {e.resource_kind === "page" ? (
            <Button size="sm" variant="danger" onClick={() => void onRevoke(e.id)}>
              Revoke
            </Button>
          ) : (
            <span style={{ fontSize: 11, color: color.text.faint }}>inherited</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function PrincipalLabel({
  kind,
  id,
  users,
  groups,
}: {
  kind: PrincipalKind;
  id: string | null;
  users: AdminUser[];
  groups: { id: string; name: string }[];
}) {
  if (kind === "everyone") return <strong>Everyone</strong>;
  if (kind === "user") {
    const u = id ? users.find((x) => x.id === id) : null;
    return <span>👤 {u ? u.email : id ?? "?"}</span>;
  }
  if (kind === "group") {
    const g = id ? groups.find((x) => x.id === id) : null;
    return <span>👥 {g ? g.name : id ?? "?"}</span>;
  }
  return <span>{kind}</span>;
}

function GrantForm({
  path,
  users,
  groups,
  onGranted,
}: {
  path: string;
  users: AdminUser[];
  groups: { id: string; name: string }[];
  onGranted: () => void;
}) {
  const [principalKind, setPrincipalKind] = useState<PrincipalKind>("user");
  const [principalId, setPrincipalId] = useState("");
  const [permission, setPermission] = useState<Permission>("read");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      await grantAcl({
        resource_kind: "page",
        resource_path: path,
        principal_kind: principalKind,
        principal_id: principalKind === "everyone" ? null : principalId,
        permission,
      });
      setPrincipalId("");
      onGranted();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  const canSubmit =
    !busy && (principalKind === "everyone" || principalId !== "");

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", gap: 8 }}>
        <select
          value={principalKind}
          onChange={(e) => {
            setPrincipalKind(e.target.value as PrincipalKind);
            setPrincipalId("");
          }}
          style={inputStyle}
        >
          <option value="user">User</option>
          <option value="group">Group</option>
          <option value="everyone">Everyone</option>
        </select>
        {principalKind === "user" ? (
          <select value={principalId} onChange={(e) => setPrincipalId(e.target.value)} style={inputStyle}>
            <option value="">Pick a user…</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.email}
              </option>
            ))}
          </select>
        ) : principalKind === "group" ? (
          <select value={principalId} onChange={(e) => setPrincipalId(e.target.value)} style={inputStyle}>
            <option value="">Pick a group…</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        ) : (
          <span style={{ flex: 1, fontSize: 13, color: color.text.muted, alignSelf: "center" }}>
            All signed-in users
          </span>
        )}
        <select value={permission} onChange={(e) => setPermission(e.target.value as Permission)} style={inputStyle}>
          <option value="read">Read</option>
          <option value="write">Write</option>
        </select>
      </div>
      <Button onClick={() => void submit()} disabled={!canSubmit}>
        Grant access
      </Button>
      {err && <div style={{ color: color.state.danger.fg, fontSize: 13 }}>{err}</div>}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Styles                                                                      //
// --------------------------------------------------------------------------- //

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: color.overlay,
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "center",
  // 80px on tall viewports for a generous top gap; clamps down to 5vh
  // (~33px on a 667px-tall iPhone) so the modal doesn't get pushed off
  // screen on short phone viewports.
  paddingTop: "max(20px, min(80px, 5vh))",
  paddingLeft: 16,
  paddingRight: 16,
  zIndex: 50,
};
const modalStyle: React.CSSProperties = {
  background: color.bg.page,
  borderRadius: radius.lg,
  width: "min(560px, 100%)",
  maxHeight: "calc(100vh - 80px)",
  overflowY: "auto",
  padding: 24,
  boxShadow: shadow.modal,
};
const inputStyle: React.CSSProperties = {
  flex: 1,
  padding: "6px 10px",
  fontSize: 13,
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  background: color.bg.page,
};

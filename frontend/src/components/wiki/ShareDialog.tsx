"use client";

import { useEffect, useMemo, useState } from "react";

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
            <code style={{ fontSize: 12, color: "#666" }}>{path}</code>
          </div>
          <button onClick={onClose} style={iconBtnStyle}>×</button>
        </header>

        {error && (
          <div style={{ color: "crimson", marginBottom: 12 }}>
            {error instanceof ApiError && error.status === 403
              ? "Only the owner or an admin can manage sharing for this page."
              : error.message}
          </div>
        )}

        {isLoading || !acl ? (
          <div style={{ color: "#666", fontSize: 14 }}>Loading…</div>
        ) : (
          <>
            <Section title="Visibility">
              <div style={{ fontSize: 14, color: "#444" }}>
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
      <h3 style={{ fontSize: 13, fontWeight: 600, color: "#555", margin: "0 0 8px 0" }}>
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
        <button onClick={() => void transfer()} disabled={busy || !transferTo} style={btnStyle}>
          Transfer
        </button>
      </div>
      {err && <div style={{ color: "crimson", marginTop: 6, fontSize: 13 }}>{err}</div>}
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
    return <div style={{ fontSize: 13, color: "#666" }}>No explicit grants.</div>;
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
            borderBottom: "1px solid #f3f3f3",
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
            <span style={{ color: "#666" }}>
              · {e.permission}
              {e.resource_kind === "folder"
                ? ` · folder${e.resource_path ? ` "${e.resource_path}"` : " (root)"}`
                : ""}
            </span>
          </span>
          {e.resource_kind === "page" ? (
            <button onClick={() => void onRevoke(e.id)} style={{ ...btnStyle, color: "#b91c1c" }}>
              Revoke
            </button>
          ) : (
            <span style={{ fontSize: 11, color: "#999" }}>inherited</span>
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
          <span style={{ flex: 1, fontSize: 13, color: "#666", alignSelf: "center" }}>
            All signed-in users
          </span>
        )}
        <select value={permission} onChange={(e) => setPermission(e.target.value as Permission)} style={inputStyle}>
          <option value="read">Read</option>
          <option value="write">Write</option>
        </select>
      </div>
      <button onClick={() => void submit()} disabled={!canSubmit} style={btnStyle}>
        Grant access
      </button>
      {err && <div style={{ color: "crimson", fontSize: 13 }}>{err}</div>}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Styles                                                                      //
// --------------------------------------------------------------------------- //

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0, 0, 0, 0.4)",
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "center",
  paddingTop: 80,
  zIndex: 50,
};
const modalStyle: React.CSSProperties = {
  background: "white",
  borderRadius: 8,
  width: "min(560px, 90vw)",
  maxHeight: "calc(100vh - 120px)",
  overflowY: "auto",
  padding: 24,
  boxShadow: "0 12px 40px rgba(0,0,0,0.18)",
};
const inputStyle: React.CSSProperties = {
  flex: 1,
  padding: "6px 10px",
  fontSize: 13,
  border: "1px solid #d4d4d8",
  borderRadius: 4,
  background: "white",
};
const btnStyle: React.CSSProperties = {
  padding: "6px 12px",
  border: "1px solid #d4d4d8",
  background: "white",
  borderRadius: 4,
  cursor: "pointer",
  fontSize: 13,
};
const iconBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  fontSize: 24,
  cursor: "pointer",
  color: "#666",
  width: 32,
  height: 32,
  lineHeight: 1,
};

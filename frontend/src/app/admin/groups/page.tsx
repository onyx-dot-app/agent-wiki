"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/common/Button";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { ApiError, apiFetch } from "@/lib/api";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";
import {
  addGroupMember,
  createGroup,
  deleteGroup,
  removeGroupMember,
  useGroup,
  useGroups,
  type Group,
} from "@/lib/permissions";

interface AdminUser {
  id: string;
  email: string;
  name: string | null;
}

export default function AdminGroupsPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 960 }}>
        <BackLink />
        <PageHeader
          title="Groups"
          description="Groups bundle users so wiki pages can be shared with the whole group at once."
        />
        <GroupsManager />
      </main>
    </RequireAdmin>
  );
}

function GroupsManager() {
  const isMobile = useIsMobile();
  const { groups, error, isLoading, refresh } = useGroups();
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setCreateError(null);
    try {
      const g = await createGroup(name.trim(), description.trim() || undefined);
      setName("");
      setDescription("");
      await refresh();
      setSelected(g.id);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "failed");
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

  if (error) {
    return <div style={{ color: color.state.danger.fg }}>{error.message}</div>;
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "320px 1fr", gap: isMobile ? 16 : 24 }}>
      <div>
        <form onSubmit={onCreate} style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>New group</h3>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="name (e.g. eng)"
            style={inputStyle}
          />
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="description (optional)"
            style={{ ...inputStyle, marginTop: 6 }}
          />
          <Button
            type="submit"
            variant="primary"
            size="sm"
            disabled={busy || !name.trim()}
            style={{ marginTop: 8 }}
          >
            Create group
          </Button>
          {createError && (
            <div style={{ color: color.state.danger.fg, marginTop: 8, fontSize: 13 }}>{createError}</div>
          )}
        </form>

        <h3 style={{ fontSize: 14 }}>Groups</h3>
        {isLoading ? (
          <div style={{ color: color.text.muted, fontSize: 13 }}>Loading…</div>
        ) : groups.length === 0 ? (
          <div style={{ color: color.text.muted, fontSize: 13 }}>No groups yet.</div>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {groups.map((g) => (
              <li key={g.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0" }}>
                <Button
                  size="sm"
                  onClick={() => setSelected(g.id)}
                  style={{
                    flex: 1,
                    textAlign: "left",
                    background: selected === g.id ? color.accent.subtleBg : color.bg.page,
                    fontWeight: selected === g.id ? 600 : 400,
                  }}
                >
                  {g.name}
                </Button>
                <Button size="sm" variant="danger" onClick={() => void onDelete(g)}>
                  Delete
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        {selected ? <GroupDetail groupId={selected} /> : (
          <div style={{ color: color.text.muted, fontSize: 13 }}>Select a group to manage members.</div>
        )}
      </div>
    </div>
  );
}

function GroupDetail({ groupId }: { groupId: string }) {
  const { group, members, isLoading, refresh } = useGroup(groupId);
  const [allUsers, setAllUsers] = useState<AdminUser[]>([]);
  const [pickedId, setPickedId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void apiFetch<{ users: AdminUser[] }>("/admin/users")
      .then((r) => setAllUsers(r.users))
      .catch(() => {});
  }, []);

  const candidates = useMemo(() => {
    const memberIds = new Set(members.map((m) => m.id));
    return allUsers.filter((u) => !memberIds.has(u.id));
  }, [allUsers, members]);

  async function add() {
    if (!pickedId) return;
    setBusy(true);
    setError(null);
    try {
      await addGroupMember(groupId, pickedId);
      setPickedId("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
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

  if (isLoading || !group) return <div style={{ color: color.text.muted }}>Loading…</div>;

  return (
    <div>
      <h2 style={{ margin: 0 }}>{group.name}</h2>
      {group.description && <p style={{ marginTop: 4, color: color.text.muted }}>{group.description}</p>}

      <h3 style={{ fontSize: 14, marginTop: 24 }}>Add member</h3>
      <div style={{ display: "flex", gap: 8 }}>
        <select
          value={pickedId}
          onChange={(e) => setPickedId(e.target.value)}
          style={{ ...inputStyle, flex: 1 }}
        >
          <option value="">Choose a user…</option>
          {candidates.map((u) => (
            <option key={u.id} value={u.id}>
              {u.email} {u.name ? `(${u.name})` : ""}
            </option>
          ))}
        </select>
        <Button onClick={() => void add()} disabled={!pickedId || busy}>
          Add
        </Button>
      </div>
      {error && <div style={{ color: color.state.danger.fg, marginTop: 8, fontSize: 13 }}>{error}</div>}

      <h3 style={{ fontSize: 14, marginTop: 24 }}>Members ({members.length})</h3>
      {members.length === 0 ? (
        <div style={{ color: color.text.muted, fontSize: 13 }}>No members yet.</div>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {members.map((m) => (
            <li
              key={m.id}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 0",
                borderBottom: `1px solid ${color.border.subtle}`,
              }}
            >
              <span>
                {m.email} {m.name ? <span style={{ color: color.text.muted }}>({m.name})</span> : null}
              </span>
              <Button size="sm" variant="danger" onClick={() => void remove(m.id)} disabled={busy}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  fontSize: 14,
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  boxSizing: "border-box",
};

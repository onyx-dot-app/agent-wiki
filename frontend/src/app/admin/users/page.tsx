"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/common/AppShell";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";

interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  is_admin: boolean;
  created_at: string;
}

export default function AdminUsersPage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user && !user.is_admin) router.replace("/");
  }, [loading, user, router]);

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;
  if (!user.is_admin) return null;

  return (
    <AppShell>
      <main style={{ padding: 32, maxWidth: 960 }}>
        <BackLink />
        <h1 style={{ marginTop: 8 }}>Users</h1>
        <p style={{ color: "#666", marginTop: 0 }}>
          Promote or demote admins, or remove accounts. The last admin cannot be demoted or deleted.
        </p>
        <UsersTable currentUserId={user.id} />
      </main>
    </AppShell>
  );
}

function BackLink() {
  return (
    <Link href="/admin" style={{ fontSize: 13, color: "#4f46e5", textDecoration: "none" }}>
      ← Admin
    </Link>
  );
}

function UsersTable({ currentUserId }: { currentUserId: string }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    try {
      const r = await apiFetch<{ users: AdminUser[] }>("/admin/users");
      setUsers(r.users);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function toggleAdmin(u: AdminUser) {
    setBusyId(u.id);
    setError(null);
    try {
      await apiFetch<AdminUser>(`/admin/users/${u.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_admin: !u.is_admin }),
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to update");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(u: AdminUser) {
    if (!confirm(`Delete ${u.email}? This cannot be undone.`)) return;
    setBusyId(u.id);
    setError(null);
    try {
      await apiFetch<void>(`/admin/users/${u.id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to delete");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      {error && <div style={{ color: "crimson", marginBottom: 12 }}>{error}</div>}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e5e5" }}>
            <Th>Email</Th>
            <Th>Name</Th>
            <Th>Role</Th>
            <Th>Created</Th>
            <Th>Actions</Th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const isSelf = u.id === currentUserId;
            const busy = busyId === u.id;
            return (
              <tr key={u.id} style={{ borderBottom: "1px solid #f0f0f0" }}>
                <Td>{u.email}</Td>
                <Td>{u.name ?? "—"}</Td>
                <Td>
                  {u.is_admin ? (
                    <span style={{ color: "#3730a3", fontWeight: 600 }}>Admin</span>
                  ) : (
                    <span style={{ color: "#666" }}>User</span>
                  )}
                </Td>
                <Td>{u.created_at.split(" ")[0]}</Td>
                <Td>
                  <button
                    onClick={() => void toggleAdmin(u)}
                    disabled={busy || (u.is_admin && isSelf)}
                    title={u.is_admin && isSelf ? "Use another admin to demote yourself" : ""}
                    style={btnStyle}
                  >
                    {u.is_admin ? "Demote" : "Promote"}
                  </button>
                  <button
                    onClick={() => void remove(u)}
                    disabled={busy || isSelf}
                    style={{ ...btnStyle, marginLeft: 8, color: "#b91c1c" }}
                  >
                    Delete
                  </button>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const Th = ({ children }: { children: React.ReactNode }) => (
  <th style={{ padding: "10px 8px", fontSize: 13, fontWeight: 600, color: "#555" }}>{children}</th>
);
const Td = ({ children }: { children: React.ReactNode }) => (
  <td style={{ padding: "10px 8px", fontSize: 14 }}>{children}</td>
);
const btnStyle: React.CSSProperties = {
  padding: "6px 12px",
  border: "1px solid #d4d4d8",
  background: "white",
  borderRadius: 4,
  cursor: "pointer",
  fontSize: 13,
};

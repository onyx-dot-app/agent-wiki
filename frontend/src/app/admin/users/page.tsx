"use client";

import { useEffect, useState } from "react";

import { Button } from "@onyx-ai/opal/components";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { color } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  is_admin: boolean;
  created_at: string;
}

export default function AdminUsersPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 960 }}>
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
      {error && <div style={{ color: color.state.danger.fg, marginBottom: 12 }}>{error}</div>}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: `1px solid ${color.border.default}` }}>
            <Th>Email</Th>
            {!isMobile && <Th>Name</Th>}
            <Th>Role</Th>
            {!isMobile && <Th>Created</Th>}
            <Th>Actions</Th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const isSelf = u.id === currentUserId;
            const busy = busyId === u.id;
            return (
              <tr key={u.id} style={{ borderBottom: `1px solid ${color.border.subtle}` }}>
                <Td>{u.email}</Td>
                {!isMobile && <Td>{u.name ?? "—"}</Td>}
                <Td>
                  {u.is_admin ? (
                    <span style={{ color: color.text.primary, fontWeight: 600 }}>Admin</span>
                  ) : (
                    <span style={{ color: color.text.muted }}>User</span>
                  )}
                </Td>
                {!isMobile && <Td>{u.created_at.split(" ")[0]}</Td>}
                <Td>
                  <Button
                    size="sm"
                    onClick={() => void toggleAdmin(u)}
                    disabled={busy || (u.is_admin && isSelf)}
                    title={u.is_admin && isSelf ? "Use another admin to demote yourself" : ""}
                  >
                    {u.is_admin ? "Demote" : "Promote"}
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => void remove(u)}
                    disabled={busy || isSelf}
                    style={{ marginLeft: 8 }}
                  >
                    Delete
                  </Button>
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
  <th style={{ padding: "10px 8px", fontSize: 13, fontWeight: 600, color: color.text.secondary }}>{children}</th>
);
const Td = ({ children }: { children: React.ReactNode }) => (
  <td style={{ padding: "10px 8px", fontSize: 14 }}>{children}</td>
);

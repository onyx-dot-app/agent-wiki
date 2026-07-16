/** User lookup for the share / transfer typeaheads.
 *
 * Backed by `GET /api/users/search?q=` (any signed-in user) — distinct
 * from the admin-only `/admin/users`, so the sharing UI works for
 * non-admin owners too. Read path is SWR-keyed; dedupe identical queries.
 */
import useSWR from "swr";

import { apiFetch, apiFetchBlob } from "@/lib/api";
import { SWR_KEYS } from "@/lib/swr-keys";

export interface UserLite {
  id: string;
  email: string;
  name: string | null;
}

/** Typeahead search. Pass `enabled=false` to suspend the request (e.g.
 * when the picker is closed). SWR dedupes identical keys. */
export function useUserSearch(query: string, enabled = true) {
  const key = enabled ? SWR_KEYS.userSearch(query) : null;
  const { data, error, isLoading } = useSWR<{ users: UserLite[] }>(key);
  return {
    users: data?.users ?? [],
    error: error as Error | undefined,
    isLoading,
  };
}

export type UserStatus = "active" | "inactive" | "invited";

export interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  is_admin: boolean;
  is_active: boolean;
  status: "active" | "inactive";
  created_at: string;
  updated_at: string;
  groups: string[];
}

export interface InvitedUserRow {
  email: string;
}

export interface AdminUserCounts {
  active: number;
  inactive: number;
  invited: number;
}

interface AdminUsersResponse {
  users: AdminUser[];
  invited: InvitedUserRow[];
  counts: AdminUserCounts;
}

const EMPTY_COUNTS: AdminUserCounts = { active: 0, inactive: 0, invited: 0 };

/** Admin-only full user list (`/admin/users`) — accepted users (with status +
 * groups), pending invites, and counts. Distinct from `useUserSearch`, which
 * any signed-in user can hit. */
export function useAdminUsers() {
  const { data, error, isLoading, mutate } = useSWR<AdminUsersResponse>(
    SWR_KEYS.adminUsers,
  );
  return {
    users: data?.users ?? [],
    invited: data?.invited ?? [],
    counts: data?.counts ?? EMPTY_COUNTS,
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export async function setUserAdmin(
  userId: string,
  isAdmin: boolean,
): Promise<void> {
  await apiFetch(`/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify({ is_admin: isAdmin }),
  });
}

export async function setUserActive(
  userId: string,
  isActive: boolean,
): Promise<void> {
  await apiFetch(`/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify({ is_active: isActive }),
  });
}

export async function deleteUser(userId: string): Promise<void> {
  await apiFetch(`/admin/users/${userId}`, { method: "DELETE" });
}

export async function inviteUsers(emails: string[]): Promise<void> {
  await apiFetch("/admin/users/invite", {
    method: "PUT",
    body: JSON.stringify({ emails }),
  });
}

export async function cancelInvite(email: string): Promise<void> {
  await apiFetch(`/admin/users/invited?email=${encodeURIComponent(email)}`, {
    method: "DELETE",
  });
}

/** Download the users CSV via the binary arm of the api seam (apiFetchBlob). */
export async function downloadUsersCsv(): Promise<void> {
  const blob = await apiFetchBlob("/admin/users/download");
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "users.csv";
  a.click();
  URL.revokeObjectURL(url);
}

/** Compact relative time ("3h ago", "2d ago") from a "YYYY-MM-DD HH:MM:SS"
 * UTC timestamp, for the Last Updated column. */
export function relativeTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  const then = Date.parse(ts.includes("T") ? ts : ts.replace(" ", "T") + "Z");
  if (Number.isNaN(then)) return "—";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

/** Best display label for a user — name if set, else email. */
export function displayName(u: {
  name?: string | null;
  email: string;
}): string {
  return (u.name && u.name.trim()) || u.email;
}

/** Up to two uppercase initials for an avatar, derived from name or email. */
export function initials(u: { name?: string | null; email: string }): string {
  const base = (u.name && u.name.trim()) || u.email;
  const parts = base.split(/[\s@._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return (parts[0] ?? "?").charAt(0).toUpperCase();
  return (
    (parts[0] ?? "").charAt(0) + (parts[1] ?? "").charAt(0)
  ).toUpperCase();
}

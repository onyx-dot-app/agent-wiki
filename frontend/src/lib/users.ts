/** User lookup for the share / transfer typeaheads.
 *
 * Backed by `GET /api/users/search?q=` (any signed-in user) — distinct
 * from the admin-only `/admin/users`, so the sharing UI works for
 * non-admin owners too. Read path is SWR-keyed; dedupe identical queries.
 */
import useSWR from "swr";

export interface UserLite {
  id: string;
  email: string;
  name: string | null;
}

/** Typeahead search. Pass `enabled=false` to suspend the request (e.g.
 * when the picker is closed). SWR dedupes identical keys. */
export function useUserSearch(query: string, enabled = true) {
  const key = enabled
    ? `/users/search?q=${encodeURIComponent(query.trim())}`
    : null;
  const { data, error, isLoading } = useSWR<{ users: UserLite[] }>(key);
  return {
    users: data?.users ?? [],
    error: error as Error | undefined,
    isLoading,
  };
}

export interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  is_admin: boolean;
  created_at: string;
  groups: string[];
}

/** Admin-only full user list (`/admin/users`), including each user's
 * groups. Distinct from `useUserSearch`, which any signed-in user can hit. */
export function useAdminUsers() {
  const { data, error, isLoading, mutate } = useSWR<{ users: AdminUser[] }>(
    "/admin/users",
  );
  return {
    users: data?.users ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

/** Best display label for a user — name if set, else email. */
export function displayName(u: { name?: string | null; email: string }): string {
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

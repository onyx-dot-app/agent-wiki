/** Typed wrappers for the permission HTTP surface.
 *
 * Endpoints:
 *   GET    /api/groups                                — list visible groups
 *   POST   /api/groups                                — create (admin)
 *   GET    /api/groups/:id                            — group + members
 *   DELETE /api/groups/:id                            — delete (admin)
 *   PATCH  /api/groups/:id                            — rename (admin)
 *   POST   /api/groups/:id/members                    — add (admin)
 *   DELETE /api/groups/:id/members/:user_id           — remove (admin)
 *   GET    /api/groups/:id/shares                      — pages/folders shared with group (admin)
 *   GET    /api/wiki/acl?path=<path>                  — list grants (owner/admin)
 *   POST   /api/wiki/acl                              — create grant (owner/admin)
 *   DELETE /api/wiki/acl/:id                          — revoke (owner/admin)
 *   POST   /api/wiki/transfer-ownership               — transfer (owner/admin)
 *
 * Read paths (`useGroups`, `usePageAcl`) are SWR-keyed so the admin / share
 * UIs revalidate after mutations without a full reload.
 */
import useSWR from "swr";

import { apiFetch } from "@/lib/api";

export type PrincipalKind = "user" | "group" | "everyone";
export type ResourceKind = "page" | "folder";
export type Permission = "read" | "write";

export interface Group {
  id: string;
  name: string;
  description: string | null;
  created_by_user_id: string | null;
  created_at: string;
  // Aggregate counts for the groups list UI (0 when not provided, e.g.
  // the group-detail and create responses don't compute them).
  member_count: number;
  page_count: number;
  folder_count: number;
}

export interface GroupMember {
  id: string;
  email: string;
  name: string | null;
  is_admin: boolean;
}

/** A page or folder shared with a group (one ACL row, group-centric). */
export interface GroupShare {
  id: string;
  resource_kind: ResourceKind;
  resource_path: string;
  permission: Permission;
  created_at: string;
}

export interface AclEntry {
  id: string;
  resource_kind: ResourceKind;
  resource_path: string;
  principal_kind: PrincipalKind;
  principal_id: string | null;
  permission: Permission;
  granted_by_user_id: string | null;
  created_at: string;
  // Display enrichment resolved server-side (null for `everyone` or a
  // principal that no longer exists).
  principal_email?: string | null;
  principal_name?: string | null;
  group_name?: string | null;
}

export interface PageAcl {
  path: string;
  owner_user_id: string | null;
  owner_email?: string | null;
  owner_name?: string | null;
  entries: AclEntry[];
}

// --------------------------------------------------------------------------- //
// Groups                                                                      //
// --------------------------------------------------------------------------- //

export function useGroups() {
  const { data, error, isLoading, mutate } = useSWR<{ groups: Group[] }>(
    "/groups",
  );
  return {
    groups: data?.groups ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function useGroup(id: string | null) {
  const { data, error, isLoading, mutate } = useSWR<{
    group: Group;
    members: GroupMember[];
  }>(id ? `/groups/${id}` : null);
  return {
    group: data?.group ?? null,
    members: data?.members ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function createGroup(
  name: string,
  description?: string,
): Promise<Group> {
  return apiFetch<Group>("/groups", {
    method: "POST",
    body: JSON.stringify({ name, description: description ?? null }),
  });
}

export function renameGroup(id: string, name: string): Promise<Group> {
  return apiFetch<Group>(`/groups/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

/** All wiki paths (pages + folders), for the group sharing picker. Admin
 * sees everything. `path` ends in ".md" for pages; folders are the parent
 * directories. */
export interface WikiPathEntry {
  path: string;
  updated_at: string;
}

export function useWikiPaths() {
  const { data, error, isLoading } = useSWR<{ entries: WikiPathEntry[] }>("/wiki");
  return {
    entries: data?.entries ?? [],
    error: error as Error | undefined,
    isLoading,
  };
}

/** Pages and folders shared with a group — SWR-keyed so the group page
 * revalidates after a revoke. */
export function useGroupShares(id: string | null) {
  const { data, error, isLoading, mutate } = useSWR<{ shares: GroupShare[] }>(
    id ? `/groups/${id}/shares` : null,
  );
  return {
    shares: data?.shares ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function deleteGroup(id: string): Promise<void> {
  return apiFetch<void>(`/groups/${id}`, { method: "DELETE" });
}

export function addGroupMember(groupId: string, userId: string): Promise<void> {
  return apiFetch<void>(`/groups/${groupId}/members`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
  });
}

export function removeGroupMember(
  groupId: string,
  userId: string,
): Promise<void> {
  return apiFetch<void>(`/groups/${groupId}/members/${userId}`, {
    method: "DELETE",
  });
}

// --------------------------------------------------------------------------- //
// Wiki ACL                                                                    //
// --------------------------------------------------------------------------- //

export function usePageAcl(path: string | null) {
  const key = path ? `/wiki/acl?path=${encodeURIComponent(path)}` : null;
  const { data, error, isLoading, mutate } = useSWR<PageAcl>(key);
  return {
    acl: data ?? null,
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export interface GrantInput {
  resource_kind: ResourceKind;
  resource_path: string;
  principal_kind: PrincipalKind;
  principal_id: string | null;
  permission: Permission;
}

export function grantAcl(input: GrantInput): Promise<{ id: string }> {
  return apiFetch<{ id: string }>("/wiki/acl", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function revokeAcl(id: string): Promise<void> {
  return apiFetch<void>(`/wiki/acl/${id}`, { method: "DELETE" });
}

export function transferOwnership(
  path: string,
  newOwnerUserId: string | null,
): Promise<{ path: string; owner_user_id: string | null }> {
  return apiFetch<{ path: string; owner_user_id: string | null }>(
    "/wiki/transfer-ownership",
    {
      method: "POST",
      body: JSON.stringify({ path, new_owner_user_id: newOwnerUserId }),
    },
  );
}

// --------------------------------------------------------------------------- //
// UX helpers                                                                  //
// --------------------------------------------------------------------------- //

/** Page-level visibility derived from the ACL.
 *
 * - ``"public-write"`` — ``everyone`` has a write grant. Write is a strict
 *   superset of read (the resolver treats ``write`` as also granting
 *   ``read``), so this is also publicly readable.
 * - ``"public-read"`` — ``everyone`` has only a read grant. World-readable,
 *   not world-editable.
 * - ``"private"``    — no ``everyone`` grants at the page level. Only the
 *   owner and explicit user/group grants can access.
 *
 * Folder-level ``everyone`` grants that cascade in are treated as the
 * page's effective visibility too, so a public-read folder containing a
 * page without its own ``everyone`` rows still reads as ``public-read``.
 */
export type Visibility = "public-write" | "public-read" | "private";

export function visibility(acl: PageAcl | null): Visibility {
  if (!acl) return "private";
  let hasRead = false;
  let hasWrite = false;
  for (const e of acl.entries) {
    if (e.principal_kind !== "everyone") continue;
    if (e.permission === "write") hasWrite = true;
    else if (e.permission === "read") hasRead = true;
  }
  if (hasWrite) return "public-write";
  if (hasRead) return "public-read";
  return "private";
}

/** True when the page has no ``everyone`` grant of any kind. Write
 * implies read, so an ``everyone write`` grant alone still makes the
 * page public — both reads here and at the resolver. */
export function isPrivate(acl: PageAcl | null): boolean {
  return visibility(acl) === "private";
}

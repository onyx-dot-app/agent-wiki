"use client";

/**
 * RequireAdmin — access-control wrapper for admin-only pages.
 *
 * Composes with `useRequireAuth`, which already redirects unauthenticated
 * users to /login. On top of that, RequireAdmin redirects authenticated
 * non-admins to / so they can't reach admin surfaces even by typing the
 * URL directly.
 *
 * Renders nothing while the auth state is still loading, so there is no
 * flash of admin content before the redirect fires.
 *
 * Usage — wrap the page's default export:
 *
 *   export default function AdminFooPage() {
 *     return (
 *       <RequireAdmin>
 *         <FooContent />
 *       </RequireAdmin>
 *     );
 *   }
 */

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";

import { useRequireAuth } from "@/lib/auth";

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useRequireAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user && !user.is_admin) {
      router.replace("/");
    }
  }, [loading, user, router]);

  if (loading || !user || !user.is_admin) return null;

  return <>{children}</>;
}

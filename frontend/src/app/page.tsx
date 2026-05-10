"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";
import { LAST_WIKI_PATH_KEY } from "@/lib/lastViewed";

// Resolves the user's preferred landing page once auth has loaded.
// Logged-out users get punted to /login by useRequireAuth on the
// destination page — landing here unauthenticated is a transient blip
// that lasts only as long as /auth/me takes to resolve.
export default function HomePage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    const dest = resolveLanding(user?.settings?.default_landing ?? "wiki_home");
    router.replace(dest);
  }, [loading, user, router]);

  return null;
}

function resolveLanding(setting: string): string {
  if (setting === "recent") return "/events";
  if (setting === "last_viewed") {
    if (typeof window !== "undefined") {
      try {
        const last = window.localStorage.getItem(LAST_WIKI_PATH_KEY);
        if (last) return last;
      } catch {
        /* ignore */
      }
    }
    return "/wiki";
  }
  return "/wiki";
}

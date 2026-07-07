"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useRouter, usePathname } from "next/navigation";

import { ApiError, apiFetch } from "@/lib/api";
import type { UserSettings, UserSettingsUpdate } from "@/types";

const DEFAULT_USER_SETTINGS: UserSettings = {
  theme: "system",
  timezone: null,
  default_landing: "wiki_home",
  chat_provider: null,
  chat_model: null,
  notify_comment_email: false,
  notify_update_warning_email: false,
  work_role: null,
};

export interface AuthUser {
  id: string;
  email: string;
  name: string | null;
  is_admin: boolean;
  settings: UserSettings;
}

export interface AuthConfig {
  mode: "basic" | "oidc";
  signup_open: boolean;
}

interface AuthContextValue {
  user: AuthUser | null;
  config: AuthConfig | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name?: string) => Promise<void>;
  logout: () => Promise<void>;
  updateSettings: (partial: UserSettingsUpdate) => Promise<UserSettings>;
  updateProfile: (partial: { name: string }) => Promise<AuthUser>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [me, cfg] = await Promise.allSettled([
        apiFetch<AuthUser>("/auth/me"),
        apiFetch<AuthConfig>("/auth/config"),
      ]);
      if (me.status === "fulfilled") {
        setUser(withDefaultSettings(me.value));
      } else if (me.reason instanceof ApiError && me.reason.status === 401) {
        setUser(null);
      } else {
        throw me.reason;
      }
      if (cfg.status === "fulfilled") setConfig(cfg.value);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const me = await apiFetch<AuthUser>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setUser(withDefaultSettings(me));
  }, []);

  const signup = useCallback(
    async (email: string, password: string, name?: string) => {
      const me = await apiFetch<AuthUser>("/auth/signup", {
        method: "POST",
        body: JSON.stringify({ email, password, name }),
      });
      setUser(withDefaultSettings(me));
    },
    [],
  );

  const logout = useCallback(async () => {
    await apiFetch<void>("/auth/logout", { method: "POST" });
    setUser(null);
  }, []);

  const updateSettings = useCallback(
    async (partial: UserSettingsUpdate): Promise<UserSettings> => {
      const updated = await apiFetch<UserSettings>("/user/settings", {
        method: "PUT",
        body: JSON.stringify(partial),
      });
      setUser((prev) => (prev ? { ...prev, settings: updated } : prev));
      return updated;
    },
    [],
  );

  const updateProfile = useCallback(
    async (partial: { name: string }): Promise<AuthUser> => {
      const updated = await apiFetch<AuthUser>("/user/profile", {
        method: "PUT",
        body: JSON.stringify(partial),
      });
      const normalized = withDefaultSettings(updated);
      setUser(normalized);
      return normalized;
    },
    [],
  );

  return (
    <AuthContext.Provider
      value={{
        user,
        config,
        loading,
        login,
        signup,
        logout,
        updateSettings,
        updateProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// Older /auth/me responses or partial JSON shouldn't crash the UI —
// fill in any missing settings fields with defaults.
function withDefaultSettings(u: AuthUser): AuthUser {
  return {
    ...u,
    settings: { ...DEFAULT_USER_SETTINGS, ...(u.settings ?? {}) },
  };
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/**
 * Gate a page on auth. Redirects to /login if there's no user once loading
 * resolves. Use at the top of any page that requires login.
 */
export function useRequireAuth(): AuthContextValue {
  const auth = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  useEffect(() => {
    if (
      !auth.loading &&
      !auth.user &&
      pathname !== "/login" &&
      pathname !== "/signup"
    ) {
      // Keep the query string so deep links (e.g. ?tab=) survive the
      // round-trip. Read from location inside the effect — useSearchParams
      // would impose a Suspense boundary on every consumer at prerender.
      const qs = window.location.search;
      const next = encodeURIComponent(`${pathname || "/"}${qs}`);
      router.replace(`/login?next=${next}`);
    }
  }, [auth.loading, auth.user, pathname, router]);
  return auth;
}

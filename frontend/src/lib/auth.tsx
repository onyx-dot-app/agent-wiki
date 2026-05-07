"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useRouter, usePathname } from "next/navigation";

import { ApiError, apiFetch } from "@/lib/api";

export interface AuthUser {
  id: string;
  email: string;
  name: string | null;
  is_admin: boolean;
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
        setUser(me.value);
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
    setUser(me);
  }, []);

  const signup = useCallback(async (email: string, password: string, name?: string) => {
    const me = await apiFetch<AuthUser>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    });
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    await apiFetch<void>("/auth/logout", { method: "POST" });
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, config, loading, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  );
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
    if (!auth.loading && !auth.user && pathname !== "/login" && pathname !== "/signup") {
      const next = encodeURIComponent(pathname || "/");
      router.replace(`/login?next=${next}`);
    }
  }, [auth.loading, auth.user, pathname, router]);
  return auth;
}

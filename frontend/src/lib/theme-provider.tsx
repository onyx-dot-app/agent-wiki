"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { useAuth } from "@/lib/auth";

export type ThemeSetting = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

interface ThemeContextValue {
  setting: ThemeSetting;
  resolved: ResolvedTheme;
}

const ThemeContext = createContext<ThemeContextValue>({
  setting: "system",
  resolved: "light",
});

const STORAGE_KEY = "agent-wiki:theme";

// Inline script that runs before React hydrates so the user never sees a
// light-mode flash before the dark-mode preference applies. Renders into
// <head> via ``ThemeBootstrapScript``.
//
// Sets both ``data-theme`` attribute (our globals.css tokens) and the
// ``.dark`` class (Opal's CSS) so both theming systems stay in sync.
const bootstrapSource = `(()=>{try{var k=${JSON.stringify(STORAGE_KEY)};var s=localStorage.getItem(k);if(s!=='light'&&s!=='dark'&&s!=='system')s='system';var r=s;if(s==='system')r=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';var el=document.documentElement;el.setAttribute('data-theme',r);el.classList.toggle('dark',r==='dark');}catch(e){}})();`;

export function ThemeBootstrapScript() {
  return <script dangerouslySetInnerHTML={{ __html: bootstrapSource }} />;
}

function readStoredSetting(): ThemeSetting {
  if (typeof window === "undefined") return "system";
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* ignore */
  }
  return "system";
}

function applyTheme(resolved: ResolvedTheme) {
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-theme", resolved);
    document.documentElement.classList.toggle("dark", resolved === "dark");
  }
}

function resolveSystem(): ResolvedTheme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  // Pre-auth render: lazy-init from localStorage so the inline bootstrap
  // and React's first paint agree.
  const [setting, setSetting] = useState<ThemeSetting>(readStoredSetting);
  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(() =>
    resolveSystem(),
  );

  // Once the user logs in, prefer the server-side preference.
  const userSetting = user?.settings?.theme ?? null;
  useEffect(() => {
    if (userSetting && userSetting !== setting) {
      setSetting(userSetting);
    }
  }, [userSetting, setting]);

  // Persist locally so logged-out routes (login, signup) keep the choice.
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, setting);
    } catch {
      /* ignore */
    }
  }, [setting]);

  // Track OS preference when the user has chosen "system".
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemTheme(mq.matches ? "dark" : "light");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const resolved: ResolvedTheme = setting === "system" ? systemTheme : setting;

  useEffect(() => {
    applyTheme(resolved);
  }, [resolved]);

  const value = useMemo<ThemeContextValue>(
    () => ({ setting, resolved }),
    [setting, resolved],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}

// Direct setter used by the settings page when it eagerly applies the
// new value before the round-trip completes (so "Save" feels instant).
export function setLocalThemePreview(setting: ThemeSetting) {
  try {
    window.localStorage.setItem(STORAGE_KEY, setting);
  } catch {
    /* ignore */
  }
  const resolved = setting === "system" ? resolveSystem() : setting;
  applyTheme(resolved);
}

// Re-export the storage key for tests / explicit cleanup.
export const THEME_STORAGE_KEY = STORAGE_KEY;

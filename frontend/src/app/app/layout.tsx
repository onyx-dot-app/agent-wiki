"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { RootLayout } from "@onyx-ai/opal/layouts";
import { MessageCard } from "@onyx-ai/opal/components";
import { markdown } from "@onyx-ai/opal/utils";
import AppSidebar from "@/sections/sidebar/AppSidebar";
import { WikiItemActionsProvider } from "@/providers/WikiItemActionsProvider";
import { WikiTree } from "@/components/wiki/WikiTree";
import { useAuth } from "@/lib/auth";
import { useHealth } from "@/lib/health";
import { useLLMStatus } from "@/lib/llm";

const COLLAPSED_KEY = "agent-wiki:sidebar-collapsed";
const BANNER_HEALTH_POLL_MS = 15000;
const LLM_DISMISS_KEY = "llm-banner-dismissed";

function StatusBanner() {
  const { user, loading } = useAuth();
  const skip = loading || !user;
  const { health, error: healthError } = useHealth({
    refreshIntervalMs: skip ? undefined : BANNER_HEALTH_POLL_MS,
  });
  const { status: llmStatus } = useLLMStatus({ skip });
  const [llmDismissed, setLlmDismissed] = useState(false);

  useEffect(() => {
    if (
      typeof window !== "undefined" &&
      sessionStorage.getItem(LLM_DISMISS_KEY) === "1"
    ) {
      setLlmDismissed(true);
    }
  }, []);

  if (skip) return null;

  const isAdmin = !!user?.is_admin;

  if (!!healthError || health?.status === "degraded") {
    const unreachable = !!healthError;
    const body = unreachable
      ? "The frontend can't reach the backend. Some features will not work until it recovers."
      : "The backend isn't fully healthy. Background work like search indexing and scheduled triggers may be delayed until it recovers.";
    const suffix = isAdmin
      ? ` [View health details](/admin/health).${healthError?.message ? ` (${healthError.message})` : ""}`
      : " Please ask a workspace admin to investigate.";
    return (
      <MessageCard
        variant="error"
        title={unreachable ? "Backend unreachable." : "Backend degraded."}
        description={markdown(body + suffix)}
      />
    );
  }

  if (llmStatus?.configured === false && !llmDismissed) {
    const description = isAdmin
      ? "AI features are disabled until you add a provider and API key on the [LLM settings page](/admin/language-models)."
      : "AI features are disabled. Please ask a workspace admin to finish setup.";
    return (
      <MessageCard
        variant="warning"
        title="No language model is configured."
        description={markdown(description)}
        onClose={() => {
          if (typeof window !== "undefined")
            sessionStorage.setItem(LLM_DISMISS_KEY, "1");
          setLlmDismissed(true);
        }}
      />
    );
  }

  return null;
}

interface AppContentProps {
  children: ReactNode;
}

function AppContent({ children }: AppContentProps) {
  const pathname = usePathname();
  const isWiki = pathname.startsWith("/app/wiki");
  const inner = (
    <>
      {isWiki && (
        <RootLayout.LeftPanel>
          <WikiTree />
        </RootLayout.LeftPanel>
      )}
      <RootLayout.App>
        <StatusBanner />
        <RootLayout.MainContent>
          <div className="mx-auto w-full max-w-(--breakpoint-content-md)">
            {children}
          </div>
        </RootLayout.MainContent>
      </RootLayout.App>
    </>
  );
  return isWiki ? (
    <WikiItemActionsProvider>{inner}</WikiItemActionsProvider>
  ) : (
    inner
  );
}

interface LayoutProps {
  children: ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  const [folded, setFolded] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    const stored = window.localStorage.getItem(COLLAPSED_KEY);
    if (stored === "1") return true;
    if (stored === "0") return false;
    return window.innerWidth < 724;
  });

  function toggle() {
    setFolded((prev) => {
      const next = !prev;
      window.localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      return next;
    });
  }

  return (
    <RootLayout.Root>
      <RootLayout.Sidebar folded={folded} onFoldToggle={toggle}>
        <AppSidebar folded={folded} onFoldToggle={toggle} />
      </RootLayout.Sidebar>
      <AppContent>{children}</AppContent>
    </RootLayout.Root>
  );
}

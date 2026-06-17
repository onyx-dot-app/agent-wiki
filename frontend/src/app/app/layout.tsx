"use client";

import { useEffect, useState, type ReactNode } from "react";
import { RootLayout, SidebarStateProvider } from "@onyx-ai/opal/layouts";
import { MessageCard } from "@onyx-ai/opal/components";
import { markdown } from "@onyx-ai/opal/utils";
import AppSidebar from "@/sections/sidebar/AppSidebar";
import { WikiItemActionsProvider } from "@/providers/WikiItemActionsProvider";
import { LeftPanelProvider, useLeftPanel } from "@/providers/LeftPanelProvider";
import {
  WikiHeaderActionsProvider,
  useRightPanelHost,
} from "@/providers/WikiHeaderActionsProvider";
import { WikiTree } from "@/components/wiki/WikiTree";
import { WikiHeader } from "@/components/wiki/WikiHeader";
import ActivitiesPanel from "@/components/wiki/ActivitiesPanel";
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

// Full-height right column. Wiki routes portal their side panels (History /
// Comments / Update Policy) into this host instead of squeezing the document
// column. We use Opal's `RootLayout.RightPanel` for the panel chrome: with no
// slot-context provider it renders inline as a flex-row sibling of the app
// (Root is a flex row), so it docks at the far right and is zero-width when
// empty. Its children are a stable host div (portal target), so the panel never
// re-renders itself into a loop the way teleporting live content would.
function RightPanelHost() {
  const host = useRightPanelHost();
  return (
    <RootLayout.RightPanel>
      <div ref={host?.setEl} className="flex h-full" />
    </RootLayout.RightPanel>
  );
}

function AppContent({ children }: AppContentProps) {
  const { view, isOnWikiRoute } = useLeftPanel();
  return (
    <WikiHeaderActionsProvider>
      <WikiItemActionsProvider active={isOnWikiRoute}>
        {view !== null && (
          <RootLayout.LeftPanel>
            {view === "wiki-tree" && <WikiTree />}
            {view === "activities" && (
              <div className="h-full p-1">
                <ActivitiesPanel />
              </div>
            )}
          </RootLayout.LeftPanel>
        )}
        <RootLayout.App>
          <StatusBanner />
          {isOnWikiRoute && (
            <RootLayout.Header>
              <WikiHeader />
            </RootLayout.Header>
          )}
          <RootLayout.MainContent>{children}</RootLayout.MainContent>
        </RootLayout.App>
        <RightPanelHost />
      </WikiItemActionsProvider>
    </WikiHeaderActionsProvider>
  );
}

interface LayoutProps {
  children: ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  const [defaultFolded] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    const stored = window.localStorage.getItem(COLLAPSED_KEY);
    if (stored === "1") return true;
    if (stored === "0") return false;
    return window.innerWidth < 724;
  });

  return (
    <LeftPanelProvider>
      <SidebarStateProvider
        defaultFolded={defaultFolded}
        onFoldedChange={(folded) => {
          window.localStorage.setItem(COLLAPSED_KEY, folded ? "1" : "0");
        }}
      >
        <RootLayout.Root>
          <AppSidebar />
          <AppContent>{children}</AppContent>
        </RootLayout.Root>
      </SidebarStateProvider>
    </LeftPanelProvider>
  );
}

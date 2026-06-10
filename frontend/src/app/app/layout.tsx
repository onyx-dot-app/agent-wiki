"use client";

import { useState, type ReactNode } from "react";
import { RootLayout } from "@onyx-ai/opal/layouts";
import AppSidebar from "@/sections/sidebar/AppSidebar";
import { AppLayoutProvider, useAppLayout } from "@/providers/AppLayoutProvider";
import { WikiItemActionsProvider } from "@/providers/WikiItemActionsProvider";
import { StatusBanner } from "@/sections/app/StatusBanner";

const COLLAPSED_KEY = "agent-wiki:sidebar-collapsed";

interface AppContentProps {
  children: ReactNode;
}

function AppContent({ children }: AppContentProps) {
  const {
    headerContent,
    leftPanelContent,
    actionSidebarContent,
    isActionSidebarOpen,
  } = useAppLayout();
  return (
    <WikiItemActionsProvider>
      {leftPanelContent && (
        <RootLayout.LeftPanel>{leftPanelContent}</RootLayout.LeftPanel>
      )}
      <RootLayout.App>
        <StatusBanner />
        <RootLayout.Header>
          <div className="flex h-14 items-center px-4">{headerContent}</div>
        </RootLayout.Header>
        <RootLayout.MainContent>
          <div className="mx-auto w-full max-w-(--breakpoint-content-md)">
            {children}
          </div>
        </RootLayout.MainContent>
      </RootLayout.App>
      {isActionSidebarOpen && actionSidebarContent && (
        <RootLayout.RightPanel className="w-60 border-l border-border-01">
          {actionSidebarContent}
        </RootLayout.RightPanel>
      )}
    </WikiItemActionsProvider>
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
      <AppLayoutProvider>
        <AppContent>{children}</AppContent>
      </AppLayoutProvider>
    </RootLayout.Root>
  );
}

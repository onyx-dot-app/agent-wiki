"use client";

import { useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { RootLayout } from "@onyx-ai/opal/layouts";
import AppSidebar from "@/sections/sidebar/AppSidebar";
import { WikiItemActionsProvider } from "@/providers/WikiItemActionsProvider";
import { WikiTree } from "@/components/wiki/WikiTree";
import { StatusBanner } from "@/sections/app/StatusBanner";

const COLLAPSED_KEY = "agent-wiki:sidebar-collapsed";

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

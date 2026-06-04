"use client";

import { useState, type ReactNode } from "react";
import { RootLayout } from "@onyx-ai/opal/layouts";
import { AppSidebar } from "@/sections/sidebar/AppSidebar";
import { AppLayoutProvider } from "@/sections/app/AppLayoutContext";
import { AppContentLayout } from "@/sections/app/AppContentLayout";

const COLLAPSED_KEY = "agent-wiki:sidebar-collapsed";

export default function AppLayout({ children }: { children: ReactNode }) {
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
        <AppContentLayout>{children}</AppContentLayout>
      </AppLayoutProvider>
    </RootLayout.Root>
  );
}

"use client";

import type { ReactNode } from "react";
import { RootLayout } from "@onyx-ai/opal/layouts";
import styles from "./AppContentLayout.module.css";
import { StatusBanner } from "./StatusBanner";
import { useAppLayout } from "./AppLayoutContext";

export function AppContentLayout({ children }: { children: ReactNode }) {
  const { headerContent, actionSidebarContent, isActionSidebarOpen } = useAppLayout();
  return (
    <>
      <RootLayout.App>
        <StatusBanner />
        <RootLayout.Header>
          <div className={styles.headerBar}>{headerContent}</div>
        </RootLayout.Header>
        <RootLayout.MainContent>
          <div className={styles.mainInner}>{children}</div>
        </RootLayout.MainContent>
      </RootLayout.App>
      {isActionSidebarOpen && actionSidebarContent && (
        <RootLayout.RightPanel className={styles.rightPanel}>
          {actionSidebarContent}
        </RootLayout.RightPanel>
      )}
    </>
  );
}

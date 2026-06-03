"use client";

import type { ReactNode } from "react";
import styles from "./AppContentLayout.module.css";
import { StatusBanner } from "./StatusBanner";
import { useAppLayout } from "./AppLayoutContext";

export function AppContentLayout({ children }: { children: ReactNode }) {
  const { headerContent, actionSidebarContent, isActionSidebarOpen } = useAppLayout();
  return (
    <div className={styles.root}>
      <StatusBanner />
      <header className={styles.header}>{headerContent}</header>
      <div className={styles.body}>
        <div className={styles.actionSidebar} data-open={String(isActionSidebarOpen)}>
          {actionSidebarContent}
        </div>
        <main className={styles.main}>
          <div className={styles.mainInner}>{children}</div>
        </main>
      </div>
    </div>
  );
}

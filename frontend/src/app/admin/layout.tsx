"use client";

import type { ReactNode } from "react";
import { RootLayout, SidebarStateProvider } from "@onyx-ai/opal/layouts";
import AdminSidebar from "@/sections/sidebar/AdminSidebar";

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <SidebarStateProvider>
      <RootLayout.Root>
        <AdminSidebar />
        <RootLayout.App>
          <RootLayout.MainContent>{children}</RootLayout.MainContent>
        </RootLayout.App>
      </RootLayout.Root>
    </SidebarStateProvider>
  );
}

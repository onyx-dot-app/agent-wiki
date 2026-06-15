"use client";

import type { ReactNode } from "react";
import { SidebarStateProvider } from "@onyx-ai/opal/layouts";
import AdminSidebar from "@/sections/sidebar/AdminSidebar";

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <SidebarStateProvider>
      <div className="flex min-h-screen bg-(--background-tint-01)">
        <AdminSidebar />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </SidebarStateProvider>
  );
}

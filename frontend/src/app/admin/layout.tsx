"use client";

import type { ReactNode } from "react";
import { AdminSidebar } from "@/sections/sidebar/AdminSidebar";

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-(--background-tint-01)">
      <AdminSidebar />
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}

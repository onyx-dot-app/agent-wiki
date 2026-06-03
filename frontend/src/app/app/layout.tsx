import type { ReactNode } from "react";
import { AppSidebar } from "@/sections/sidebar/AppSidebar";
import { AppLayoutProvider } from "@/sections/app/AppLayoutContext";
import { AppContentLayout } from "@/sections/app/AppContentLayout";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden">
      <AppSidebar />
      <AppLayoutProvider>
        <AppContentLayout>{children}</AppContentLayout>
      </AppLayoutProvider>
    </div>
  );
}

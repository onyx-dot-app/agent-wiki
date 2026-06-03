import type { ReactNode } from "react";
import { AppShell } from "@/sections/app/AppShell";

export default function AppLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}

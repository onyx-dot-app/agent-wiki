"use client";

import { SidebarTab } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { usePathname } from "next/navigation";
import { ADMIN_NAV_ENTRIES } from "@/lib/nav/registry";
import UserMenu from "@/sections/sidebar/UserMenu";
import {
  SidebarBody,
  SidebarFooter,
  SidebarHeader,
  SidebarNavList,
} from "@/sections/sidebar/SidebarHeader";

export default function AdminSidebar() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 box-border flex h-screen w-(--sidebar-width-expanded) shrink-0 flex-col gap-4 overflow-hidden bg-background-tint-02 py-2">
      <SidebarHeader />

      <SidebarBody>
        <SidebarNavList>
          {ADMIN_NAV_ENTRIES.map((item) => {
            const active = pathname?.startsWith(item.href) ?? false;
            return (
              <SidebarTab
                key={item.href}
                href={item.href}
                selected={active}
                folded={false}
                icon={item.icon}
              >
                {item.label}
              </SidebarTab>
            );
          })}
        </SidebarNavList>
      </SidebarBody>

      {/* Footer: exit + account — same slot order as AppSidebar so the
          rows don't appear to swap when entering/leaving the admin panel. */}
      <SidebarFooter>
        <SidebarTab icon={SvgX} folded={false} href="/app/wiki">
          Exit Admin Panel
        </SidebarTab>
        <UserMenu />
      </SidebarFooter>
    </nav>
  );
}

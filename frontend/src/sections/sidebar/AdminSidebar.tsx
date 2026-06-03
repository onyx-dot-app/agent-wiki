"use client";

import { SidebarTab } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { SvgOnyxLogoTyped } from "@onyx-ai/opal/logos";
import { usePathname } from "next/navigation";
import { ADMIN_NAV_ENTRIES } from "@/lib/nav/registry";
import { UserMenu } from "./UserMenu";

export function AdminSidebar() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 box-border flex h-screen w-[248px] shrink-0 flex-col gap-4 overflow-hidden bg-background-tint-02 py-2">
      {/* Logo */}
      <div className="pt-3 px-3 flex items-center h-7">
        <SvgOnyxLogoTyped size={28} />
      </div>

      {/* Nav items */}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-x-hidden px-2">
        <div className="flex flex-col gap-px">
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
        </div>
      </div>

      {/* Footer: exit + account — same slot order as AppSidebar (panel
          toggle above, account pinned at the very bottom) so the rows
          don't appear to swap when entering/leaving the admin panel. */}
      <div className="flex shrink-0 flex-col gap-px px-2">
        <SidebarTab icon={SvgX} folded={false} href="/app/wiki">
          Exit Admin Panel
        </SidebarTab>
        <UserMenu />
      </div>
    </nav>
  );
}

"use client";

import { SidebarTab } from "@onyx-ai/opal/components";
import { SvgUser, SvgX } from "@onyx-ai/opal/icons";
import { SvgOnyxLogoTyped } from "@onyx-ai/opal/logos";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { ADMIN_NAV_ENTRIES } from "@/lib/nav/registry";

export function AdminSidebar() {
  const { user } = useAuth();
  const pathname = usePathname();
  const displayName = user?.name || user?.email || "";

  return (
    <nav className="flex flex-col h-screen w-[248px] box-border py-2 gap-4 shrink-0 sticky top-0 bg-background-tint-02 overflow-hidden">
      {/* Logo */}
      <div className="pt-3 px-3">
        <SvgOnyxLogoTyped size={28} />
      </div>

      {/* Nav items */}
      <div className="flex-1 min-h-0 flex flex-col gap-3 px-2 overflow-x-hidden">
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

      {/* Footer: account + exit */}
      <div className="flex flex-col gap-px px-2 shrink-0">
        <SidebarTab icon={SvgUser} folded={false}>
          {displayName || "Account"}
        </SidebarTab>
        <SidebarTab icon={SvgX} folded={false} href="/app/wiki">
          Exit Admin Panel
        </SidebarTab>
      </div>
    </nav>
  );
}

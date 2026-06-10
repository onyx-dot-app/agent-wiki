"use client";

import { SidebarTab, Text } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { SidebarLayouts, SidebarWrapper } from "@onyx-ai/opal/layouts";
import { SvgOnyxLogoTyped } from "@onyx-ai/opal/logos";
import { usePathname } from "next/navigation";
import { ADMIN_NAV_ENTRIES } from "@/lib/nav/registry";
import UserMenu from "@/sections/sidebar/UserMenu";

export default function AdminSidebar() {
  const pathname = usePathname();

  return (
    <SidebarWrapper
      logo={() => (
        <div className="flex items-center gap-2">
          <SvgOnyxLogoTyped size={28} />
          <Text font="heading-h3" color="text-03">
            Wiki
          </Text>
        </div>
      )}
    >
      <SidebarLayouts.Body scrollKey="admin-sidebar">
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
      </SidebarLayouts.Body>

      {/* Footer: exit + account — same slot order as AppSidebar so the
          rows don't appear to swap when entering/leaving the admin panel. */}
      <SidebarLayouts.Footer>
        <SidebarTab icon={SvgX} folded={false} href="/app/wiki">
          Exit Admin Panel
        </SidebarTab>
        <UserMenu />
      </SidebarLayouts.Footer>
    </SidebarWrapper>
  );
}

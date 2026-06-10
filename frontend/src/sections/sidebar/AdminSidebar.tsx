"use client";

import { InputTypeIn, SidebarTab, Text } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { SidebarLayouts, SidebarWrapper } from "@onyx-ai/opal/layouts";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ADMIN_NAV_GROUPS } from "@/lib/nav/registry";
import { sidebarLogo } from "@/sections/sidebar/shared";
import UserMenu from "@/sections/sidebar/UserMenu";

export default function AdminSidebar() {
  const pathname = usePathname();
  const [query, setQuery] = useState("");

  const lq = query.toLowerCase();
  const filteredGroups = ADMIN_NAV_GROUPS.map((group) => ({
    ...group,
    entries: lq
      ? group.entries.filter((e) => e.label.toLowerCase().includes(lq))
      : group.entries,
  })).filter((group) => group.entries.length > 0);

  return (
    <SidebarWrapper logo={sidebarLogo}>
      <SidebarLayouts.Body scrollKey="admin-sidebar">
        <div className="relative w-full">
          <InputTypeIn
            variant="internal"
            searchIcon
            clearButton
            placeholder="Search…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        <div className="flex flex-col gap-3">
          {filteredGroups.map((group) => (
            <div
              key={group.label ?? "__ungrouped"}
              className="flex flex-col gap-px"
            >
              {group.label && (
                <div className="px-2 pt-1 pb-0.5">
                  <Text font="secondary-body" color="text-02">
                    {group.label}
                  </Text>
                </div>
              )}
              {group.entries.map((item) => {
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
          ))}
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

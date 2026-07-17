"use client";

import { InputTypeIn, SidebarTab } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { SidebarLayouts } from "@onyx-ai/opal/layouts";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ADMIN_NAV_GROUPS } from "@/lib/nav/registry";
import { sidebarLogo } from "@/chromes/sidebar/shared";
import UserMenu from "@/chromes/sidebar/UserMenu";

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
    <SidebarLayouts.Root>
      <SidebarLayouts.Header logo={sidebarLogo}>
        <InputTypeIn
          variant="internal"
          searchIcon
          clearButton
          placeholder="Search…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </SidebarLayouts.Header>
      <SidebarLayouts.Body scrollKey="admin-sidebar">
        {filteredGroups.map((group) => (
          <SidebarLayouts.Section
            key={group.label ?? "__ungrouped"}
            title={group.label ?? undefined}
          >
            {group.entries.map((item) => (
              <SidebarTab
                key={item.href}
                href={item.href}
                selected={pathname?.startsWith(item.href) ?? false}
                folded={false}
                icon={item.icon}
              >
                {item.label}
              </SidebarTab>
            ))}
          </SidebarLayouts.Section>
        ))}
      </SidebarLayouts.Body>

      {/* Footer: exit + account — same slot order as AppSidebar so the
          rows don't appear to swap when entering/leaving the admin panel. */}
      <SidebarLayouts.Footer>
        <SidebarTab icon={SvgX} folded={false} href="/app/wiki">
          Exit Admin Panel
        </SidebarTab>
        <UserMenu />
      </SidebarLayouts.Footer>
    </SidebarLayouts.Root>
  );
}

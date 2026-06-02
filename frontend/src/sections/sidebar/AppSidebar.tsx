"use client";

import { Button, SidebarTab, Text } from "@onyx-ai/opal/components";
import {
  SvgFileText,
  SvgSearch,
  SvgSettings,
  SvgSidebar,
  SvgUser,
} from "@onyx-ai/opal/icons";
import { SvgOnyxLogoTyped } from "@onyx-ai/opal/logos";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import {
  WikiSearch,
  type WikiSearchHandle,
} from "@/components/wiki/WikiSearch";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { NAV_ENTRIES } from "@/lib/nav/registry";
import { MOBILE_BREAKPOINT, useIsMobile } from "@/lib/viewport";

const COLLAPSED_KEY = "agent-wiki:sidebar-collapsed";

function useWikiPages() {
  const { data } = useSWR(
    "/wiki",
    (key: string) =>
      apiFetch<{ entries: { path: string; updated_at: string }[] }>(key),
    { revalidateOnFocus: false },
  );
  return (data?.entries ?? [])
    .filter((e) => e.path.endsWith(".md"))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

export function AppSidebar() {
  const { user } = useAuth();
  const pathname = usePathname();
  const isMobile = useIsMobile();
  const pages = useWikiPages();

  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    const stored = window.localStorage.getItem(COLLAPSED_KEY);
    if (stored === "1") return true;
    if (stored === "0") return false;
    return window.innerWidth < MOBILE_BREAKPOINT;
  });

  const searchRef = useRef<WikiSearchHandle>(null);
  const isMobileDrawer = isMobile && !collapsed;

  function expandAndFocusSearch() {
    setCollapsed(false);
    setTimeout(() => searchRef.current?.focus(), 0);
  }

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  const displayName = user?.name || user?.email || "";

  return (
    <>
      {isMobileDrawer && (
        <div
          onClick={() => setCollapsed(true)}
          aria-hidden
          className="fixed inset-0 z-50 bg-(--mask-03)"
        />
      )}

      {/* Extra wrapper div required — without it the explicit widths don't
          properly apply during the CSS transition (same pattern as onyx). */}
      <div>
        <nav
          className={[
            "flex flex-col h-screen box-border py-2 gap-4 shrink-0 top-0",
            "bg-background-tint-02 overflow-hidden",
            "transition-[width] duration-200 ease-in-out",
            collapsed ? "w-[52px]" : "w-[248px]",
            isMobileDrawer
              ? "fixed left-0 z-[60] shadow-(--shadow-panel)"
              : "sticky",
          ].join(" ")}
        >
          {/* Logo + toggle */}
          <div className="flex flex-row justify-between items-start pt-3 px-2 shrink-0">
            {collapsed ? (
              <div className="px-1">
                <Button
                  icon={SvgSidebar}
                  prominence="tertiary"
                  size="md"
                  tooltip="Open Sidebar"
                  onClick={() => setCollapsed(false)}
                />
              </div>
            ) : (
              <>
                <div className="px-1">
                  <SvgOnyxLogoTyped size={28} />
                </div>
                <div className="px-1">
                  <Button
                    icon={SvgSidebar}
                    prominence="tertiary"
                    size="md"
                    tooltip="Close Sidebar"
                    onClick={() => setCollapsed(true)}
                  />
                </div>
              </>
            )}
          </div>

          {/* Content area */}
          <div className="flex-1 min-h-0 flex flex-col gap-3 px-2 overflow-x-hidden">
            {/* Search */}
            {collapsed ? (
              <Button
                icon={SvgSearch}
                prominence="tertiary"
                tooltip="Search"
                onClick={expandAndFocusSearch}
              />
            ) : (
              <WikiSearch ref={searchRef} />
            )}

            {/* Top nav */}
            <div className="flex flex-col gap-px">
              {NAV_ENTRIES.map((item) => {
                const active = pathname?.startsWith(item.href) ?? false;
                return (
                  <SidebarTab
                    key={item.href}
                    href={item.href}
                    selected={active}
                    folded={collapsed}
                    icon={item.icon}
                    tooltip={collapsed ? item.label : undefined}
                    onClick={() => {
                      if (isMobileDrawer) setCollapsed(true);
                    }}
                  >
                    {item.label}
                  </SidebarTab>
                );
              })}
            </div>

            {/* Recents — scrollable, hidden when collapsed */}
            {!collapsed && (
              <div className="flex-1 overflow-y-auto overflow-x-hidden">
                <div className="pl-2 mr-1.5 py-1 sticky top-0 bg-background-tint-02 z-10 flex items-center min-h-8">
                  <div className="p-0.5">
                    <Text font="secondary-body" color="text-02">
                      Recents
                    </Text>
                  </div>
                </div>
                <div className="flex flex-col gap-px">
                  {pages.map((page) => {
                    const label = (page.path.split("/").pop() ?? page.path).replace(
                      /\.md$/,
                      "",
                    );
                    const href = `/app/wiki/${page.path}`;
                    const active = pathname === href;
                    return (
                      <SidebarTab
                        key={page.path}
                        href={href}
                        selected={active}
                        folded={collapsed}
                        icon={SvgFileText}
                        tooltip={undefined}
                        nested
                        onClick={() => {
                          if (isMobileDrawer) setCollapsed(true);
                        }}
                      >
                        {label}
                      </SidebarTab>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Footer: Admin Panel + Account */}
          <div className="flex flex-col gap-px px-2 shrink-0">
            {user?.is_admin && (
              <SidebarTab
                icon={SvgSettings}
                folded={collapsed}
                tooltip={collapsed ? "Admin Panel" : undefined}
                href="/admin"
              >
                Admin Panel
              </SidebarTab>
            )}
            <SidebarTab
              icon={SvgUser}
              folded={collapsed}
              tooltip={collapsed ? displayName || "Account" : undefined}
              href="/app/settings"
            >
              {displayName || "Account"}
            </SidebarTab>
          </div>
        </nav>
      </div>
    </>
  );
}

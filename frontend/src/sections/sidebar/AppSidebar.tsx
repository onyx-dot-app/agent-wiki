"use client";

import { Button, SidebarTab, Text } from "@onyx-ai/opal/components";
import {
  SvgDocFile,
  SvgSearch,
  SvgSettings,
  SvgSidebar,
  SvgStar,
} from "@onyx-ai/opal/icons";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { Wordmark } from "@/components/common/Wordmark";
import {
  WikiSearch,
  type WikiSearchHandle,
} from "@/components/wiki/WikiSearch";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RECENTS_KEY, type RecentDocsResponse } from "@/lib/recents";
import {
  STARRED_KEY,
  starDoc,
  type StarredDocsResponse,
} from "@/lib/starred";
import { docLabel } from "./docLabel";
import { StarredList } from "./StarredList";
import { UserMenu } from "./UserMenu";
import { NAV_ENTRIES } from "@/lib/nav/registry";
import { MOBILE_BREAKPOINT, useIsMobile } from "@/lib/viewport";

const COLLAPSED_KEY = "agent-wiki:sidebar-collapsed";

// Recents = docs this user actually opened, newest first, served by
// the backend (recent_doc_views table). The server already drops
// deleted/no-longer-readable paths. Ordering is by the user's own
// views — updates from agents/triggers never reshuffle the list.
// recordRecentDoc() mutates RECENTS_KEY after each open, which
// revalidates this hook.
function useRecentPages() {
  const { data } = useSWR(
    RECENTS_KEY,
    (key: string) => apiFetch<RecentDocsResponse>(key),
    { revalidateOnFocus: false },
  );
  return data?.paths ?? [];
}

// Starred = docs the user pinned, in their drag-chosen order. Writes
// in lib/starred.ts mutate STARRED_KEY optimistically.
function useStarredPages() {
  const { data } = useSWR(
    STARRED_KEY,
    (key: string) => apiFetch<StarredDocsResponse>(key),
    { revalidateOnFocus: false },
  );
  return data?.paths ?? [];
}

export function AppSidebar() {
  const { user } = useAuth();
  const pathname = usePathname();
  const isMobile = useIsMobile();
  const starred = useStarredPages();
  // Starred docs are pinned in their own section — keep Recents free of
  // duplicates.
  const starredSet = new Set(starred);
  const pages = useRecentPages().filter((p) => !starredSet.has(p));

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
                <div className="px-1 flex items-center h-7">
                  <Wordmark />
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
              <WikiSearch
                ref={searchRef}
                onNavigate={() => {
                  if (isMobileDrawer) setCollapsed(true);
                }}
              />
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

            {/* Starred + Recents — scrollable, hidden when collapsed */}
            {!collapsed && (
              <div className="flex-1 overflow-y-auto overflow-x-hidden">
                {starred.length > 0 && (
                  <>
                    <div className="pl-2 mr-1.5 py-1 sticky top-0 bg-background-tint-02 z-10 flex items-center min-h-8">
                      <div className="p-0.5">
                        <Text font="secondary-body" color="text-02">
                          Starred
                        </Text>
                      </div>
                    </div>
                    <StarredList
                      paths={starred}
                      pathname={pathname}
                      onNavigate={() => {
                        if (isMobileDrawer) setCollapsed(true);
                      }}
                    />
                  </>
                )}
                <div className="pl-2 mr-1.5 py-1 sticky top-0 bg-background-tint-02 z-10 flex items-center min-h-8">
                  <div className="p-0.5">
                    <Text font="secondary-body" color="text-02">
                      Recents
                    </Text>
                  </div>
                </div>
                <div className="flex flex-col gap-px">
                  {pages.map((path) => {
                    const href = `/app/wiki/${path}`;
                    const active = pathname === href;
                    return (
                      <div key={path} className="group/recent">
                        <SidebarTab
                          href={href}
                          selected={active}
                          folded={collapsed}
                          icon={SvgDocFile}
                          tooltip={undefined}
                          nested
                          onClick={() => {
                            if (isMobileDrawer) setCollapsed(true);
                          }}
                          rightChildren={
                            <span className="opacity-0 group-hover/recent:opacity-100">
                              <Button
                                icon={SvgStar}
                                prominence="tertiary"
                                size="sm"
                                tooltip="Star"
                                onClick={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  void starDoc(path);
                                }}
                              />
                            </span>
                          }
                        >
                          {docLabel(path)}
                        </SidebarTab>
                      </div>
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
            <UserMenu
              folded={collapsed}
              onNavigate={() => {
                if (isMobileDrawer) setCollapsed(true);
              }}
            />
          </div>
        </nav>
      </div>
    </>
  );
}

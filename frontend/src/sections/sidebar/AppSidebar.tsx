"use client";

import { Button, SidebarTab, Text } from "@onyx-ai/opal/components";
import {
  SvgDocFile,
  SvgSearch,
  SvgSettings,
  SvgSidebar,
  SvgStar,
} from "@onyx-ai/opal/icons";
import { useSidebarFolded } from "@onyx-ai/opal/layouts";
import { SvgOnyxLogoTyped } from "@onyx-ai/opal/logos";
import { usePathname } from "next/navigation";
import { useRef } from "react";
import useSWR from "swr";
import {
  WikiSearch,
  type WikiSearchHandle,
} from "@/components/wiki/WikiSearch";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RECENTS_KEY, type RecentDocsResponse } from "@/lib/recents";
import { STARRED_KEY, starDoc, type StarredDocsResponse } from "@/lib/starred";
import { docLabel } from "@/sections/sidebar/docLabel";
import { StarredList } from "@/sections/sidebar/StarredList";
import UserMenu from "@/sections/sidebar/UserMenu";
import { NAV_ENTRIES } from "@/lib/nav/registry";
import { useIsMobile } from "@/lib/viewport";

function useRecentPages() {
  const { data } = useSWR(
    RECENTS_KEY,
    (key: string) => apiFetch<RecentDocsResponse>(key),
    { revalidateOnFocus: false },
  );
  return data?.paths ?? [];
}

function useStarredPages() {
  const { data } = useSWR(
    STARRED_KEY,
    (key: string) => apiFetch<StarredDocsResponse>(key),
    { revalidateOnFocus: false },
  );
  return data?.paths ?? [];
}

interface AppSidebarProps {
  folded: boolean;
  onFoldToggle: () => void;
}

export default function AppSidebar({ folded, onFoldToggle }: AppSidebarProps) {
  const { user } = useAuth();
  const pathname = usePathname();
  const isMobile = useIsMobile();
  const starred = useStarredPages();
  const starredSet = new Set(starred);
  const recents = useRecentPages();
  const pages = recents.filter((p) => !starredSet.has(p));
  const searchRef = useRef<WikiSearchHandle>(null);

  // effectiveFolded is always false on mobile — content renders expanded,
  // RootLayout.Sidebar handles the slide-in/out overlay instead.
  const effectiveFolded = useSidebarFolded();

  function expandAndFocusSearch() {
    if (folded) onFoldToggle();
    setTimeout(() => searchRef.current?.focus(), 0);
  }

  return (
    <nav
      className={[
        "box-border flex h-full shrink-0 flex-col gap-4 py-2",
        "overflow-hidden bg-background-tint-02",
        "transition-[width] duration-200 ease-in-out",
        effectiveFolded
          ? "w-(--sidebar-width-folded)"
          : "w-(--sidebar-width-expanded)",
      ].join(" ")}
    >
      {/* Logo + toggle */}
      <div className="flex shrink-0 flex-row items-start justify-between px-2 pt-3">
        {effectiveFolded ? (
          <div className="px-1">
            <Button
              icon={SvgSidebar}
              prominence="tertiary"
              size="md"
              tooltip="Open Sidebar"
              tooltipSide="right"
              onClick={onFoldToggle}
            />
          </div>
        ) : (
          <>
            <div className="flex h-7 items-center gap-2 px-1">
              <SvgOnyxLogoTyped size={28} />
              <Text font="heading-h3" color="text-03">
                Wiki
              </Text>
            </div>
            <div className="px-1">
              <Button
                icon={SvgSidebar}
                prominence="tertiary"
                size="md"
                tooltip="Close Sidebar"
                tooltipSide="right"
                onClick={onFoldToggle}
              />
            </div>
          </>
        )}
      </div>

      {/* Content area */}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-x-hidden px-2">
        {/* Search */}
        {effectiveFolded ? (
          <Button
            icon={SvgSearch}
            prominence="tertiary"
            tooltip="Search"
            tooltipSide="right"
            onClick={expandAndFocusSearch}
          />
        ) : (
          <WikiSearch
            ref={searchRef}
            onNavigate={() => {
              if (isMobile) onFoldToggle();
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
                folded={effectiveFolded}
                icon={item.icon}
                tooltip={effectiveFolded ? item.label : undefined}
                onClick={() => {
                  if (isMobile) onFoldToggle();
                }}
              >
                {item.label}
              </SidebarTab>
            );
          })}
        </div>

        {/* Starred + Recents — scrollable, hidden when folded */}
        {!effectiveFolded && (
          <div className="flex-1 overflow-x-hidden overflow-y-auto">
            {starred.length > 0 && (
              <>
                <div className="sticky top-0 z-10 mr-1.5 flex min-h-8 items-center bg-background-tint-02 py-1 pl-2">
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
                    if (isMobile) onFoldToggle();
                  }}
                />
              </>
            )}
            <div className="sticky top-0 z-10 mr-1.5 flex min-h-8 items-center bg-background-tint-02 py-1 pl-2">
              <div className="p-0.5">
                <Text font="secondary-body" color="text-02">
                  Recents
                </Text>
              </div>
            </div>
            <div className="flex flex-col gap-px">
              {recents.length === 0 && (
                <div className="px-2.5">
                  <Text font="secondary-body" color="text-01" as="p">
                    No recent pages. Create a page to get started.
                  </Text>
                </div>
              )}
              {pages.map((path) => {
                const href = `/app/wiki/${path}`;
                const active = pathname === href;
                return (
                  <div key={path} className="group/recent">
                    <SidebarTab
                      href={href}
                      selected={active}
                      folded={effectiveFolded}
                      icon={SvgDocFile}
                      tooltip={undefined}
                      nested
                      onClick={() => {
                        if (isMobile) onFoldToggle();
                      }}
                      rightChildren={
                        <span className="opacity-0 group-hover/recent:opacity-100">
                          <Button
                            icon={SvgStar}
                            prominence="tertiary"
                            size="sm"
                            tooltip="Star"
                            tooltipSide="right"
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
      <div className="flex shrink-0 flex-col gap-px px-2">
        {user?.is_admin && (
          <SidebarTab
            icon={SvgSettings}
            folded={effectiveFolded}
            tooltip={effectiveFolded ? "Admin Panel" : undefined}
            href="/admin"
          >
            Admin Panel
          </SidebarTab>
        )}
        <UserMenu
          folded={effectiveFolded}
          onNavigate={() => {
            if (isMobile) onFoldToggle();
          }}
        />
      </div>
    </nav>
  );
}

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
import { usePathname } from "next/navigation";
import { useRef } from "react";
import useSWR from "swr";
import { Wordmark } from "@/components/common/Wordmark";
import {
  WikiSearch,
  type WikiSearchHandle,
} from "@/components/wiki/WikiSearch";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RECENTS_KEY, type RecentDocsResponse } from "@/lib/recents";
import { STARRED_KEY, starDoc, type StarredDocsResponse } from "@/lib/starred";
import { docLabel } from "./docLabel";
import { StarredList } from "./StarredList";
import { UserMenu } from "./UserMenu";
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

export function AppSidebar({ folded, onFoldToggle }: AppSidebarProps) {
  const { user } = useAuth();
  const pathname = usePathname();
  const isMobile = useIsMobile();
  const starred = useStarredPages();
  const starredSet = new Set(starred);
  const pages = useRecentPages().filter((p) => !starredSet.has(p));
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
        "flex flex-col h-full box-border py-2 gap-4 shrink-0",
        "bg-background-tint-02 overflow-hidden",
        "transition-[width] duration-200 ease-in-out",
        effectiveFolded ? "w-[52px]" : "w-[248px]",
      ].join(" ")}
    >
      {/* Logo + toggle */}
      <div className="flex flex-row justify-between items-start pt-3 px-2 shrink-0">
        {effectiveFolded ? (
          <div className="px-1">
            <Button
              icon={SvgSidebar}
              prominence="tertiary"
              size="md"
              tooltip="Open Sidebar"
              onClick={onFoldToggle}
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
                onClick={onFoldToggle}
              />
            </div>
          </>
        )}
      </div>

      {/* Content area */}
      <div className="flex-1 min-h-0 flex flex-col gap-3 px-2 overflow-x-hidden">
        {/* Search */}
        {effectiveFolded ? (
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
                    if (isMobile) onFoldToggle();
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

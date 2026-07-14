"use client";

import { Button, SidebarTab, Text } from "@onyx-ai/opal/components";
import {
  SvgActivity,
  SvgDocFile,
  SvgNotificationBubble,
  SvgSearch,
  SvgSettings,
  SvgStar,
  SvgTrash,
} from "@onyx-ai/opal/icons";
import { SidebarLayouts, useSidebarState } from "@onyx-ai/opal/layouts";
import { sidebarLogo } from "@/sections/sidebar/shared";
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
import StarredList from "@/sections/sidebar/StarredList";
import UserMenu from "@/sections/sidebar/UserMenu";
import { NAV_ENTRIES } from "@/lib/nav/registry";
import { useIsMobile } from "@/lib/viewport";
import { useAppFocus } from "@/hooks/useAppFocus";
import { useLeftPanel } from "@/providers/LeftPanelProvider";
import { isNewActivity, useEvents } from "@/lib/activities";

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

export default function AppSidebar() {
  const { user } = useAuth();
  const isMobile = useIsMobile();
  const focus = useAppFocus();
  const { isActivitiesOpen, toggleActivities } = useLeftPanel();
  const { events: activityEvents } = useEvents(
    { kind: "trigger.fire", limit: 100 },
    { refreshInterval: 30_000 },
  );
  const hasNewActivities = activityEvents.some((ev) => isNewActivity(ev.ts));
  const starred = useStarredPages();
  const starredSet = new Set(starred);
  const recents = useRecentPages();
  const pages = recents.filter((p) => !starredSet.has(p));
  const searchRef = useRef<WikiSearchHandle>(null);
  const { folded, setFolded } = useSidebarState();

  const closeMobile = () => {
    if (isMobile) setFolded(true);
  };

  function expandAndFocusSearch() {
    if (folded) setFolded(false);
    setTimeout(() => searchRef.current?.focus(), 0);
  }

  return (
    <SidebarLayouts.Root foldable>
      <SidebarLayouts.Header logo={sidebarLogo}>
        {folded ? (
          <Button
            icon={SvgSearch}
            prominence="tertiary"
            tooltip="Search"
            tooltipSide="right"
            onClick={expandAndFocusSearch}
          />
        ) : (
          <WikiSearch ref={searchRef} onNavigate={closeMobile} />
        )}
        {NAV_ENTRIES.map((item) => (
          <SidebarTab
            key={item.href}
            href={item.href}
            selected={focus.matchesHref(item.href)}
            folded={folded}
            icon={item.icon}
            tooltip={folded ? item.label : undefined}
            onClick={closeMobile}
          >
            {item.label}
          </SidebarTab>
        ))}
      </SidebarLayouts.Header>

      <SidebarLayouts.Body scrollKey="app-sidebar">
        {starred.length > 0 && (
          <SidebarLayouts.Section title="Starred">
            <StarredList paths={starred} onNavigate={closeMobile} />
          </SidebarLayouts.Section>
        )}

        <SidebarLayouts.Section title="Recents">
          {recents.length === 0 && (
            <div className="px-2.5">
              <Text font="secondary-body" color="text-01" as="p">
                No recent pages. Create a page to get started.
              </Text>
            </div>
          )}
          {pages.map((path) => {
            const href = `/app/wiki/${path}`;
            return (
              <div key={path} className="group/recent">
                <SidebarTab
                  href={href}
                  selected={focus.matchesWikiPath(path)}
                  folded={folded}
                  icon={SvgDocFile}
                  nested
                  onClick={closeMobile}
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
        </SidebarLayouts.Section>
      </SidebarLayouts.Body>

      <SidebarLayouts.Footer>
        <SidebarTab
          icon={SvgActivity}
          folded={folded}
          tooltip={folded ? "Activities" : undefined}
          selected={isActivitiesOpen}
          onClick={toggleActivities}
          rightChildren={
            hasNewActivities ? <SvgNotificationBubble size={14} /> : undefined
          }
        >
          Activities
        </SidebarTab>
        <SidebarTab
          icon={SvgTrash}
          folded={folded}
          tooltip={folded ? "Trash" : undefined}
          selected={focus.matchesHref("/app/trash")}
          href="/app/trash"
        >
          Trash
        </SidebarTab>
        {user?.is_admin && (
          <SidebarTab
            icon={SvgSettings}
            folded={folded}
            tooltip={folded ? "Admin Panel" : undefined}
            href="/admin"
          >
            Admin Panel
          </SidebarTab>
        )}
        <UserMenu folded={folded} onNavigate={closeMobile} />
      </SidebarLayouts.Footer>
    </SidebarLayouts.Root>
  );
}

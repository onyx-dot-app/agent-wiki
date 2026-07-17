"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { Button, LineItemButton, Popover } from "@onyx-ai/opal/components";
import {
  SvgChevronRight,
  SvgFolder,
  SvgListTree,
  SvgMoreHorizontal,
} from "@onyx-ai/opal/icons";
import { NotificationBell } from "@/components/common/NotificationBell";
import { CraftNotifier } from "@/components/wiki/CraftNotifier";
import { useAppFocus } from "@/hooks/useAppFocus";
import { useLeftPanel } from "@/providers/LeftPanelProvider";
import { useHeaderActionsHost } from "@/providers/WikiHeaderActionsProvider";
import { resolveIds, wikiHref } from "@/lib/wikiHref";

function segmentLabel(segment: string): string {
  return segment.replace(/\.md$/, "").replace(/_/g, " ");
}

// Trailing crumbs kept visible when the path folds (the current page plus
// its nearest ancestors). Home always renders separately.
const FOLD_TRAIL = 4;

export function WikiHeader() {
  const router = useRouter();
  const { view, toggleTree } = useLeftPanel();
  const treeVisible = view === "wiki-tree";
  const { wikiPath } = useAppFocus();
  const host = useHeaderActionsHost();
  const [foldOpen, setFoldOpen] = useState(false);

  const segments = wikiPath ? wikiPath.split("/") : [];
  // Ancestor + self segment paths, resolved to ids so each crumb links to the
  // stable `/app/wiki/<id>` URL. Folder segments (no id) and paths that haven't
  // resolved yet fall back to a path URL, which the route canonicalizes.
  const segmentPaths = segments.map((_, i) =>
    segments.slice(0, i + 1).join("/"),
  );
  const { data: crumbIds } = useSWR(
    segmentPaths.length ? ["wiki-crumb-ids", segmentPaths.join("\n")] : null,
    () => resolveIds(segmentPaths),
  );
  const crumbs: Array<{ label: string; href: string }> = [
    { label: "Home", href: "/app/wiki" },
  ];
  segments.forEach((seg, i) => {
    const path = segmentPaths[i];
    const id = crumbIds?.[path];
    crumbs.push({
      label: segmentLabel(seg),
      href: id ? wikiHref(id) : `/app/wiki/${path}`,
    });
  });

  // Deep paths fold their middle ancestors into a "…" popover (root→leaf
  // order). The folder tree is the tool for deep hierarchies, crumbs only
  // hop nearby levels.
  const isFolded = crumbs.length > FOLD_TRAIL + 1;
  const folded = isFolded ? crumbs.slice(1, -FOLD_TRAIL) : [];
  const trail = isFolded ? crumbs.slice(-FOLD_TRAIL) : crumbs.slice(1);

  return (
    <div className="flex h-14 items-center gap-3 px-4">
      {/* The expand control lives here only while the tree is closed. When
          open, the collapse control sits in the tree panel's own header. */}
      {!treeVisible && (
        <Button
          icon={SvgListTree}
          prominence="tertiary"
          tooltip="Expand tree"
          onClick={toggleTree}
        />
      )}
      {/* min-w-0 + nowrap + overflow-hidden: one line, always. The container
          only clips, the per-crumb truncate classes render the ellipsis. */}
      <nav className="flex min-w-0 items-center gap-1.5 overflow-hidden text-sm whitespace-nowrap">
        <Link
          href={crumbs[0].href}
          className="shrink-0 text-(--text-03) hover:text-(--text-05)"
        >
          {crumbs[0].label}
        </Link>
        {folded.length > 0 && (
          <span className="flex shrink-0 items-center gap-1.5">
            <SvgChevronRight size={12} className="text-(--text-02)" />
            <Popover open={foldOpen} onOpenChange={setFoldOpen}>
              <Popover.Trigger asChild>
                <span className="inline-flex">
                  <Button
                    icon={SvgMoreHorizontal}
                    prominence="tertiary"
                    size="sm"
                    tooltip="Show full path"
                  />
                </span>
              </Popover.Trigger>
              <Popover.Content width="fit" align="start">
                <Popover.Menu>
                  {folded.map((c) => (
                    <LineItemButton
                      key={c.href}
                      title={c.label}
                      icon={SvgFolder}
                      sizePreset="main-ui"
                      variant="section"
                      onClick={() => {
                        setFoldOpen(false);
                        router.push(c.href);
                      }}
                    />
                  ))}
                </Popover.Menu>
              </Popover.Content>
            </Popover>
          </span>
        )}
        {trail.map((c, i) => {
          const last = i === trail.length - 1;
          return (
            <span
              key={c.href}
              className={`flex items-center gap-1.5 ${last ? "min-w-0" : "shrink-0"}`}
            >
              <SvgChevronRight size={12} className="text-(--text-02)" />
              {last ? (
                <span className="overflow-hidden font-semibold text-ellipsis text-(--text-05)">
                  {c.label}
                </span>
              ) : (
                <Link
                  href={c.href}
                  className="max-w-44 truncate text-(--text-03) hover:text-(--text-05)"
                >
                  {c.label}
                </Link>
              )}
            </span>
          );
        })}
      </nav>
      {/* Page-level actions portal here from the active wiki route (see
          WikiHeaderActionsProvider). Pushed right by the flex spacer. */}
      <div className="min-w-4 flex-1" />
      <div ref={host?.setEl} className="flex items-center gap-2" />
      <NotificationBell />
      <CraftNotifier />
    </div>
  );
}

"use client";

import Link from "next/link";
import { Button } from "@onyx-ai/opal/components";
import { SvgFolder } from "@onyx-ai/opal/icons";
import { NotificationBell } from "@/components/common/NotificationBell";
import { CraftNotifier } from "@/components/wiki/CraftNotifier";
import { useAppFocus } from "@/hooks/useAppFocus";
import { useLeftPanel } from "@/providers/LeftPanelProvider";
import { useHeaderActionsHost } from "@/providers/WikiHeaderActionsProvider";

function segmentLabel(segment: string): string {
  return segment.replace(/\.md$/, "").replace(/_/g, " ");
}

export function WikiHeader() {
  const { view, toggleTree } = useLeftPanel();
  const treeVisible = view === "wiki-tree";
  const { wikiPath } = useAppFocus();
  const host = useHeaderActionsHost();

  const segments = wikiPath ? wikiPath.split("/") : [];
  const crumbs: Array<{ label: string; href: string }> = [
    { label: "Wiki", href: "/app/wiki" },
  ];
  segments.forEach((seg, i) => {
    const path = segments.slice(0, i + 1).join("/");
    crumbs.push({ label: segmentLabel(seg), href: `/app/wiki/${path}` });
  });

  return (
    <div className="flex h-14 items-center gap-3 px-4">
      <Button
        icon={SvgFolder}
        prominence="tertiary"
        tooltip={treeVisible ? "Collapse tree" : "Expand tree"}
        onClick={toggleTree}
      />
      <nav className="flex flex-wrap items-center gap-1.5 text-sm">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <span key={c.href} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-(--text-02)">/</span>}
              {last ? (
                <span className="font-semibold text-(--text-05)">
                  {c.label}
                </span>
              ) : (
                <Link
                  href={c.href}
                  className="text-(--text-03) hover:text-(--text-05)"
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
      <div className="flex-1" />
      <div ref={host?.setEl} className="flex items-center gap-1" />
      <NotificationBell />
      <CraftNotifier />
    </div>
  );
}

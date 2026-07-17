"use client";

import Link from "next/link";
import useSWR from "swr";
import { Button } from "@onyx-ai/opal/components";
import { SvgFolder } from "@onyx-ai/opal/icons";
import { NotificationBell } from "@/components/common/NotificationBell";
import { CraftNotifier } from "@/components/wiki/CraftNotifier";
import { useAppFocus } from "@/hooks/useAppFocus";
import { useLeftPanel } from "@/providers/LeftPanelProvider";
import { useHeaderActionsHost } from "@/providers/WikiHeaderActionsProvider";
import { resolveIds, wikiHref } from "@/lib/wikiHref";

function segmentLabel(segment: string): string {
  return segment.replace(/\.md$/, "").replace(/_/g, " ");
}

export function WikiHeader() {
  const { view, toggleTree } = useLeftPanel();
  const treeVisible = view === "wiki-tree";
  const { wikiPath } = useAppFocus();
  const host = useHeaderActionsHost();

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
    { label: "Wiki", href: "/app/wiki" },
  ];
  segments.forEach((seg, i) => {
    const path = segmentPaths[i];
    const id = crumbIds?.[path];
    crumbs.push({
      label: segmentLabel(seg),
      href: id ? wikiHref(id) : `/app/wiki/${path}`,
    });
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

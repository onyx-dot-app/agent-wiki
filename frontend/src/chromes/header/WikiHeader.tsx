"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { Button, LineItemButton, Popover } from "@onyx-ai/opal/components";
import {
  SvgChevronRight,
  SvgFolder,
  SvgHome,
  SvgListTree,
  SvgMoreHorizontal,
} from "@onyx-ai/opal/icons";
import { CraftNotifier } from "@/components/wiki/CraftNotifier";
import { useAppFocus } from "@/hooks/useAppFocus";
import { useLeftPanel } from "@/providers/LeftPanelProvider";
import {
  useHeaderActionsHost,
  useHeaderCrumbHost,
} from "@/providers/WikiHeaderActionsProvider";
import { resolveIds, wikiHref } from "@/lib/wikiHref";

function segmentLabel(segment: string): string {
  return segment.replace(/\.md$/, "").replace(/_/g, " ");
}

// Trailing crumbs kept visible when the path folds (the current page plus
// its nearest ancestors). Home is not part of the trail.
const FOLD_TRAIL = 4;

/** Fold-menu row that reveals its full label via tooltip only when the
 *  fixed-width popover truncates it. */
function FoldedCrumb({
  label,
  onSelect,
}: {
  label: string;
  onSelect: () => void;
}) {
  const rowRef = useRef<HTMLDivElement>(null);
  const [clipped, setClipped] = useState(false);
  // Opal 0.1.17's ContentMd re-adds a native title attr on every render,
  // which fights the Opal tooltip. Stripped after renders and again on
  // hover, which rerenders through the tooltip's open state.
  const stripNativeTitle = () => {
    rowRef.current
      ?.querySelector("[class*='truncate']")
      ?.removeAttribute("title");
  };
  useEffect(() => {
    // The truncate class marks Opal's one-line title span, the measured element.
    const text = rowRef.current?.querySelector<HTMLElement>(
      "[class*='truncate']",
    );
    if (!text) return;
    // Shim for @onyx-ai/opal 0.1.17, delete when Opal ships the title-row
    // min-w-0 fix: the row refuses to shrink beside the icon, which pushes
    // the ellipsis outside the popover's clipped box.
    if (text.parentElement) text.parentElement.style.minWidth = "0";
    stripNativeTitle();
    setClipped(text.scrollWidth > text.clientWidth);
    // clipped in the deps reruns the strip after the gating rerender puts
    // the title attr back.
  }, [label, clipped]);
  return (
    // The ref sits on a wrapper because LineItemButton's ref prop does not
    // reach a DOM node in opal 0.1.17.
    <div ref={rowRef} onPointerEnter={stripNativeTitle}>
      <LineItemButton
        title={label}
        titleMaxLines={1}
        icon={SvgFolder}
        sizePreset="main-ui"
        variant="section"
        tooltip={clipped ? label : undefined}
        onClick={onSelect}
      />
    </div>
  );
}

export function WikiHeader() {
  const router = useRouter();
  const { view, toggleTree } = useLeftPanel();
  const { wikiPath } = useAppFocus();
  const host = useHeaderActionsHost();
  const crumbHost = useHeaderCrumbHost();
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
    <div className="flex h-13 items-center gap-3 px-2">
      {/* The expand control shows only while no left panel is open. The
          tree's collapse control sits in its own header, and while the
          activities panel occupies the slot a tree toggle would flip state
          invisibly underneath it. */}
      {view === null && (
        <Button
          icon={SvgListTree}
          prominence="tertiary"
          tooltip="Expand tree"
          onClick={toggleTree}
        />
      )}
      {/* min-w-0 + nowrap + overflow-hidden: one line, always. The container
          only clips, the per-crumb truncate classes render the ellipsis.
          Crumbs use the mock's 12/16 SemiBold caption style. */}
      <nav className="flex min-w-0 items-center gap-0.5 overflow-hidden text-xs font-semibold whitespace-nowrap">
        {/* Home renders as its glyph in a 28px button, only when there is a
            path to crumb back from. The entry stays in crumbs so the fold
            math counts it, and its label becomes the aria-label. */}
        {segments.length > 0 && (
          <Button
            icon={SvgHome}
            prominence="tertiary"
            size="md"
            href={crumbs[0].href}
            aria-label={crumbs[0].label}
          />
        )}
        {folded.length > 0 && (
          <span className="flex shrink-0 items-center gap-0.5">
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
              <Popover.Content width="lg" align="start">
                <Popover.Menu>
                  {folded.map((c) => (
                    <FoldedCrumb
                      key={c.href}
                      label={c.label}
                      onSelect={() => {
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
              className={`flex items-center gap-0.5 ${last ? "min-w-0" : "shrink-0"}`}
            >
              <SvgChevronRight size={12} className="text-(--text-02)" />
              {last ? (
                <span className="overflow-hidden text-ellipsis text-(--text-04)">
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
        {/* Version chip slot. The active wiki route portals a dismissible
            version chip here while viewing update history. */}
        <span ref={crumbHost?.setEl} className="flex shrink-0 items-center" />
      </nav>
      {/* Page-level actions portal here from the active wiki route (see
          WikiHeaderActionsProvider). Pushed right by the flex spacer. */}
      <div className="min-w-4 flex-1" />
      <div ref={host?.setEl} className="flex items-center gap-0" />
      <CraftNotifier />
    </div>
  );
}

"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import { Button, Tag, Text } from "@onyx-ai/opal/components";
import { SvgExternalLink } from "@onyx-ai/opal/icons";

import type {
  AnchoredHighlightTarget,
  CoeditorHandle,
} from "@/lib/tiptapEditor/types";
import { relativeTime } from "@/lib/time";
import type { SourceRef } from "@/types";

import { sourceIcon, sourceKey, sourceTypeLabel } from "./sources";

const ROW_HEIGHT = 32;
const ROW_GAP = 4;
// Chip rows center their 24px chip on the anchor line's center.
const CHIP_ROW_CENTER = 12;
// Sources anchored within one row height share a chip row.
const CLUSTER_SPAN = 28;
// Chips shown before the rest collapse into the "+N more" chip (mock
// 1832:82929 shows two).
const MAX_CHIPS = 2;

interface RailSource {
  key: string;
  source: SourceRef;
}

interface ChipRow {
  top: number;
  items: RailSource[];
}

/** One source's chip: connector icon in a white box plus the title, dark
 * inverted while hovered, pinned, or caret-active (mock hover state). */
function SourceChip({
  source,
  lit,
  onClick,
}: {
  source: SourceRef;
  lit: boolean;
  onClick: () => void;
}) {
  const Icon = sourceIcon(source.source_type);
  return (
    // raw-ok: Opal's Tag has no onClick, no inverted color, no icon-box slot
    <button
      type="button"
      onClick={onClick}
      className={`flex cursor-pointer items-center gap-1 overflow-clip rounded-(--radius-08) p-[4px] ${
        lit ? "bg-(--background-tint-inverted-03)" : "bg-(--background-tint-02)"
      }`}
    >
      <span
        className={`flex size-4 items-center justify-center rounded-(--radius-04) border bg-(--background-tint-00) ${
          lit
            ? "border-(--background-tint-inverted-03)"
            : "border-(--background-tint-02)"
        }`}
      >
        <Icon size={12} />
      </span>
      <span
        className={`max-w-[120px] truncate px-[2px] text-xs leading-4 ${
          lit ? "text-(--text-inverted-05)" : "text-(--text-04)"
        }`}
      >
        {source.source_title || source.source_url || "Untitled source"}
      </span>
    </button>
  );
}

/** The chip's hover/pinned card (mock Details): popover chrome hanging
 * below the chip with the contextual-menu width and the soft shadow pair.
 * Cards for chips deeper in a row hang right-aligned so the 280px width
 * stays inside the panel column. */
function SourceHoverCard({
  source,
  alignRight,
}: {
  source: SourceRef;
  alignRight?: boolean;
}) {
  const Icon = sourceIcon(source.source_type);
  const url = source.source_url;
  return (
    <div
      className={`absolute top-[28px] z-20 w-(--block-width-contextual-menu-medium-large) overflow-clip rounded-(--radius-12) border border-(--border-01) bg-(--background-neutral-00) shadow-[0px_2px_12px_0px_var(--shadow-02),0px_0px_4px_1px_var(--shadow-01)] ${
        alignRight ? "right-[-6px]" : "left-[-6px]"
      }`}
    >
      <div className="flex w-full flex-col p-1">
        <div className="flex min-h-7 w-full items-start gap-1 p-[2px]">
          <span className="flex size-5 shrink-0 items-center justify-center p-[2px]">
            <Icon size={16} />
          </span>
          <span className="min-w-0 flex-1 px-[2px]">
            <Text font="main-ui-action" color="text-04" maxLines={2}>
              {source.source_title || url || "Untitled source"}
            </Text>
          </span>
          {url && (
            <span className="shrink-0">
              <Button
                icon={SvgExternalLink}
                size="md"
                prominence="tertiary"
                tooltip="Open"
                onClick={(e) => {
                  e.stopPropagation();
                  window.open(url, "_blank", "noopener,noreferrer");
                }}
              />
            </span>
          )}
        </div>
        <div className="flex w-full flex-col gap-1 px-[2px] pb-1">
          <div className="flex flex-wrap items-center gap-1">
            {source.source_type && (
              <Tag title={sourceTypeLabel(source.source_type)} />
            )}
            <span className="px-1">
              <Text font="secondary-body" color="text-02" nowrap>
                {relativeTime(source.last_updated)}
              </Text>
            </span>
          </div>
          {source.source_snippet && (
            <span className="px-1">
              <Text font="secondary-body" color="text-03" maxLines={4}>
                {source.source_snippet}
              </Text>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/** Overflow chip: the hidden sources' icons stacked plus "+N more" (mock
 * 1853:241106). Clicking hands off to the list view. */
function MoreChip({
  hidden,
  onClick,
}: {
  hidden: SourceRef[];
  onClick?: () => void;
}) {
  return (
    // raw-ok: same clickable-tag gap as SourceChip, plus the stacked icon boxes
    <button
      type="button"
      onClick={onClick}
      className="flex cursor-pointer items-center gap-1 overflow-clip rounded-(--radius-08) bg-(--background-tint-02) p-[4px]"
    >
      <span className="flex items-center px-[2px]">
        {hidden.slice(0, 3).map((s, i) => {
          const Icon = sourceIcon(s.source_type);
          return (
            <span
              key={sourceKey(s) || i}
              className="-mr-1 flex size-4 items-center justify-center rounded-(--radius-04) border border-(--background-tint-02) bg-(--background-tint-00) last:mr-0"
            >
              <Icon size={12} />
            </span>
          );
        })}
      </span>
      <span className="px-[2px] text-xs leading-4 whitespace-nowrap text-(--text-04)">
        +{hidden.length} more
      </span>
    </button>
  );
}

/**
 * Anchored source chips for the Sources tab (mock 1832:81274): chip rows
 * track the doc position of each source's first attributed span, sources
 * anchored close together share a row, and overflow collapses into a
 * "+N more" chip. Hovering a chip reveals its card, clicking pins it.
 */
export function SourceAnchorRail({
  sources,
  targets,
  editorRef,
  activeKeys,
  onHoverSource,
  onActivateSource,
  onShowAll,
}: {
  sources: SourceRef[];
  /** The page's current highlight targets. Anchoring reads the editor's
   * live-mapped copies, this prop retriggers layout when they land. */
  targets: AnchoredHighlightTarget[];
  editorRef: RefObject<CoeditorHandle | null>;
  /** Source keys whose chips render dark (the caret sits in their spans). */
  activeKeys?: string[];
  onHoverSource?: (key: string | null) => void;
  onActivateSource?: (key: string) => void;
  /** Overflow chips hand off to the list view. */
  onShowAll?: () => void;
}) {
  const [rows, setRows] = useState<ChipRow[]>([]);
  const [pinnedKey, setPinnedKey] = useState<string | null>(null);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  // The layer translates via a direct style write inside the scroll event
  // so chips repaint in the same frame as the text (comment rail pattern).
  const layerRef = useRef<HTMLDivElement | null>(null);
  const originStatic = useRef(0);
  const rafId = useRef(0);

  const syncScroll = useCallback(() => {
    const editor = editorRef.current;
    const layer = layerRef.current;
    if (!editor || !layer) return;
    layer.style.transform = `translateY(${originStatic.current - editor.scrollTop()}px)`;
  }, [editorRef]);

  const relayout = useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;
    // Anchors come from the editor's live-mapped targets so chips ride
    // local and remote edits like the highlights do. First surviving
    // target per source wins, targets arrive ordered by offset.
    const anchorOffsets = new Map<string, number>();
    for (const t of editor.sourceTargets()) {
      if (t.id && t.startOffset < t.endOffset && !anchorOffsets.has(t.id))
        anchorOffsets.set(t.id, t.startOffset);
    }
    const anchored: { rail: RailSource; want: number }[] = [];
    for (const s of sources) {
      const key = sourceKey(s);
      const offset = key ? anchorOffsets.get(key) : undefined;
      if (offset === undefined) continue;
      const line = editor.anchorLine(offset);
      if (!line) continue;
      anchored.push({
        rail: { key, source: s },
        want: line.top + line.height / 2 - CHIP_ROW_CENTER,
      });
    }
    anchored.sort((a, b) => a.want - b.want);
    // Sources within a row height of the cluster's start share its row,
    // later rows push down on collision.
    const next: ChipRow[] = [];
    let cursor = 0;
    for (const { rail, want } of anchored) {
      const last = next[next.length - 1];
      if (last && want - last.top < CLUSTER_SPAN) {
        last.items.push(rail);
        continue;
      }
      const top = Math.max(want, cursor);
      next.push({ top, items: [rail] });
      cursor = top + ROW_HEIGHT + ROW_GAP;
    }
    setRows(next);
    const railTop = rootRef.current?.getBoundingClientRect().top ?? 0;
    originStatic.current = editor.scrollerTop() - railTop;
    syncScroll();
  }, [sources, targets, editorRef, syncScroll]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const scheduled = () => {
      cancelAnimationFrame(rafId.current);
      rafId.current = requestAnimationFrame(relayout);
    };
    // Synchronous first pass, rAF is throttled in occluded tabs.
    relayout();
    const unsub = editor.subscribeLayout((kind) => {
      if (kind === "scroll") syncScroll();
      else scheduled();
    });
    return () => {
      unsub();
      cancelAnimationFrame(rafId.current);
    };
  }, [relayout, syncScroll, editorRef]);

  // A pointer landing outside any chip row unpins the open card. Capture
  // phase, the editor stops pointer events from bubbling.
  useEffect(() => {
    if (!pinnedKey) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element | null;
      if (t && rootRef.current?.contains(t)) return;
      setPinnedKey(null);
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [pinnedKey]);

  const hover = (key: string | null) => {
    setHoveredKey(key);
    onHoverSource?.(key);
  };
  const isOpen = (key: string) => key === pinnedKey || key === hoveredKey;

  return (
    <div
      ref={rootRef}
      className="pointer-events-none relative min-h-0 min-w-0 flex-1 overflow-clip"
      // Chips live outside the editor's scroll container, wheel input over
      // them forwards to the doc (deltaMode 1 is line-based deltas).
      onWheel={(e) => {
        editorRef.current?.scrollBy(e.deltaY * (e.deltaMode === 1 ? 16 : 1));
      }}
    >
      <div ref={layerRef} className="absolute inset-0 will-change-transform">
        {rows.map((row) => {
          const shown = row.items.slice(0, MAX_CHIPS);
          const hidden = row.items.slice(MAX_CHIPS);
          const rowLit = row.items.some((it) => isOpen(it.key));
          return (
            <div
              key={row.items[0]!.key}
              className={`pointer-events-auto absolute inset-x-2 flex flex-wrap items-start gap-1 py-1 ${
                rowLit ? "z-10" : ""
              }`}
              style={{ top: row.top }}
            >
              {shown.map(({ key, source }, i) => {
                const open = isOpen(key);
                return (
                  <span
                    key={key}
                    className="relative"
                    onMouseEnter={() => hover(key)}
                    onMouseLeave={() => hover(null)}
                  >
                    <SourceChip
                      source={source}
                      lit={open || !!activeKeys?.includes(key)}
                      onClick={() => {
                        setPinnedKey(pinnedKey === key ? null : key);
                        onActivateSource?.(key);
                      }}
                    />
                    {open && (
                      <SourceHoverCard source={source} alignRight={i > 0} />
                    )}
                  </span>
                );
              })}
              {hidden.length > 0 && (
                <MoreChip
                  hidden={hidden.map((h) => h.source)}
                  onClick={onShowAll}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState, type RefObject } from "react";
import {
  Button,
  EndOfList,
  SelectButton,
  Tag,
  Text,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgExternalLink } from "@onyx-ai/opal/icons";

import type {
  AnchoredHighlightTarget,
  CoeditorHandle,
} from "@/lib/editor/types";
import { relativeTime } from "@/lib/time";
import { useIsMobile } from "@/lib/viewport";
import type { SourceRef } from "@/types";

import { SvgListLines } from "./icons";
import { PanelSearchField } from "./PanelSearch";
import { SourceAnchorRail } from "./SourceAnchorRail";
import { sourceIcon, sourceKey, sourceTypeLabel } from "./sources";

interface Props {
  sources: SourceRef[];
  /** The page's highlight targets, retriggering chip layout in anchored
   * mode when spans land or change. */
  targets?: AnchoredHighlightTarget[];
  /** The live editor, required by anchored mode to track doc positions. */
  editorRef?: RefObject<CoeditorHandle | null>;
  /** Anchored/list mode is page-owned: the page hides the editor's native
   * scrollbar while anchored mode shows the viewport-edge one. */
  listView: boolean;
  onListViewChange: (v: boolean) => void;
  /** Source keys whose cards light up (the caret sits in their spans). */
  activeKeys?: string[];
  /** Called with a card's source key to scroll the doc to that source's
   * first span. */
  onActivateSource?: (key: string) => void;
  /** Fires with the hovered card's source key (null on leave), so the
   * page can light that source's doc spans. */
  onHoverSource?: (key: string | null) => void;
}

interface SourceCardProps {
  source: SourceRef;
  /** Lights the card while the caret sits in one of its spans. */
  active?: boolean;
  /** Scrolls the doc to this source's first attributed span. */
  onActivate?: () => void;
  /** Hover state, so the page can light this source's doc spans. */
  onHoverChange?: (hovered: boolean) => void;
}

function SourceCard({
  source,
  active,
  onActivate,
  onHoverChange,
}: SourceCardProps) {
  const Icon = sourceIcon(source.source_type);
  const url = source.source_url;
  return (
    <div
      data-source-key={sourceKey(source) || undefined}
      // Same chrome as comment cards: rested tint with the flat shadow,
      // hover and the caret-active state lift to white and the raised one.
      className={`group/source w-full shrink-0 cursor-pointer rounded-(--radius-12) p-1 ${
        active
          ? "bg-(--background-tint-00) shadow-(--shadow-box-01)"
          : "bg-(--background-tint-01) shadow-(--shadow-box-00) hover:bg-(--background-tint-00) hover:shadow-(--shadow-box-01)"
      }`}
      onClick={onActivate}
      onMouseEnter={() => onHoverChange?.(true)}
      onMouseLeave={() => onHoverChange?.(false)}
    >
      <div className="flex min-h-7 items-start gap-1 p-[2px]">
        <span className="flex size-6 shrink-0 items-center justify-center">
          <Icon size={16} />
        </span>
        <span className="min-w-0 flex-1 px-[2px] pt-[2px]">
          <Text font="main-ui-action" color="text-04" maxLines={2}>
            {source.source_title || url || "Untitled source"}
          </Text>
        </span>
        {url && (
          <span
            className="invisible shrink-0 group-hover/source:visible"
            onClick={(e) => e.stopPropagation()}
          >
            <Button
              icon={SvgExternalLink}
              size="md"
              prominence="tertiary"
              tooltip="Open"
              onClick={() => window.open(url, "_blank", "noopener,noreferrer")}
            />
          </span>
        )}
      </div>
      <div className="flex flex-col gap-1 px-[2px] pb-1">
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
            <Text font="secondary-body" color="text-03" maxLines={3}>
              {source.source_snippet}
            </Text>
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Sources tab (mock 1837:103626): the ingested documents credited to this
 * page, previewing their content via the snippet captured at ingest when
 * one exists. Sources ride the page load, the panel itself fetches nothing.
 */
export function SourcesPanel({
  sources,
  targets,
  editorRef,
  listView,
  onListViewChange,
  activeKeys,
  onActivateSource,
  onHoverSource,
}: Props) {
  const isMobile = useIsMobile();
  const [query, setQuery] = useState("");
  const listMode = listView || isMobile || !editorRef;

  // A caret landing in a span brings its card into view (list mode).
  const firstActive = activeKeys?.[0];
  useEffect(() => {
    if (!firstActive || !listMode) return;
    document
      .querySelector(`[data-source-key="${CSS.escape(firstActive)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [firstActive, listMode]);

  const q = query.trim().toLowerCase();
  const shown = q
    ? sources.filter((s) =>
        [s.source_title, s.source_url, s.source_type, s.source_snippet]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(q),
      )
    : sources;

  const searchRow = (
    <Section
      flexDirection="row"
      justifyContent="start"
      alignItems="center"
      height="fit"
      gap={0.25}
      className="shrink-0"
    >
      <PanelSearchField
        value={query}
        onChange={setQuery}
        placeholder="Search sources…"
      />
      {!isMobile && editorRef && (
        <SelectButton
          icon={SvgListLines}
          state={listView ? "selected" : "empty"}
          tooltip={listView ? "Anchored view" : "List view"}
          onClick={() => onListViewChange(!listView)}
        />
      )}
    </Section>
  );

  if (!listMode) {
    // Anchored mode (mock 1832:81274): only the search row is chromed,
    // chips float on the page background tracking their doc spans.
    return (
      <Section
        justifyContent="start"
        alignItems="stretch"
        height="auto"
        gap={0.25}
        className="relative min-h-0 flex-1"
      >
        <div className="shrink-0 rounded-(--radius-12) border border-(--border-01) p-1">
          {searchRow}
        </div>
        <SourceAnchorRail
          sources={shown}
          targets={targets ?? []}
          editorRef={editorRef!}
          activeKeys={activeKeys}
          onHoverSource={onHoverSource}
          onActivateSource={onActivateSource}
          onShowAll={() => onListViewChange(true)}
        />
      </Section>
    );
  }

  return (
    <Section
      justifyContent="start"
      alignItems="stretch"
      height="auto"
      gap={0}
      padding={0.25}
      className="min-h-0 flex-1 overflow-clip rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-01)"
    >
      {searchRow}
      <Section
        justifyContent="start"
        alignItems="stretch"
        height="auto"
        gap={0.25}
        className="scroll-fade-bottom scroll-y-hidden min-h-0 flex-1 overflow-y-auto"
      >
        {sources.length === 0 && (
          <div className="p-3">
            <Text font="secondary-body" color="text-03">
              No sources yet. Content ingested from connected apps is credited
              here.
            </Text>
          </div>
        )}

        {sources.length > 0 && shown.length === 0 && (
          <div className="p-3">
            <Text font="secondary-body" color="text-03">
              No sources match.
            </Text>
          </div>
        )}

        {shown.map((s, i) => {
          const key = sourceKey(s);
          return (
            // Rows with no identity fields keep distinct keys via the index.
            <SourceCard
              key={key || `${s.last_updated}-${i}`}
              source={s}
              active={!!key && !!activeKeys?.includes(key)}
              onActivate={
                key && onActivateSource
                  ? () => onActivateSource(key)
                  : undefined
              }
              onHoverChange={
                key && onHoverSource
                  ? (h) => onHoverSource(h ? key : null)
                  : undefined
              }
            />
          );
        })}

        {shown.length > 0 && (
          <div className="px-4 py-2">
            <EndOfList
              title={`${shown.length} Source${shown.length === 1 ? "" : "s"}`}
            />
          </div>
        )}
      </Section>
    </Section>
  );
}

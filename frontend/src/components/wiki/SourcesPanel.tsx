"use client";

import { useState } from "react";
import { Button, EndOfList, Tag, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgExternalLink, SvgFileText, SvgGlobe } from "@onyx-ai/opal/icons";
import {
  SvgBraintrust,
  SvgConfluence,
  SvgGithub,
  SvgGmail,
  SvgGoogleDrive,
  SvgHubspot,
  SvgJira,
  SvgLinear,
  SvgNotion,
  SvgSalesforce,
  SvgSharepoint,
  SvgSlack,
  SvgTeams,
  SvgZendesk,
} from "@onyx-ai/opal/logos";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

import { relativeTime } from "@/lib/time";
import type { SourceRef, WriteProvenance } from "@/types";

import { PanelSearchField } from "./PanelSearch";

interface Props {
  sources: SourceRef[];
  /** Called with a card's source key to scroll the doc to that source's
   * first span. */
  onActivateSource?: (key: string) => void;
}

const SOURCE_ICONS: Record<string, IconFunctionComponent> = {
  braintrust: SvgBraintrust,
  confluence: SvgConfluence,
  github: SvgGithub,
  gmail: SvgGmail,
  google_drive: SvgGoogleDrive,
  hubspot: SvgHubspot,
  jira: SvgJira,
  linear: SvgLinear,
  notion: SvgNotion,
  salesforce: SvgSalesforce,
  sharepoint: SvgSharepoint,
  slack: SvgSlack,
  teams: SvgTeams,
  web: SvgGlobe,
  zendesk: SvgZendesk,
};

function sourceIcon(type: string | null): IconFunctionComponent {
  return (type && SOURCE_ICONS[type]) || SvgFileText;
}

// "google_drive" → "Google Drive", for the connector chip.
function sourceTypeLabel(type: string): string {
  return type
    .split(/[_-]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Stable card identity, mirroring the backend's dedupe key (document id,
 * falling back to url or title). Also joins a card to its doc spans. */
export function sourceKey(s: WriteProvenance): string {
  return s.source_document_id || s.source_url || s.source_title || "";
}

interface SourceCardProps {
  source: SourceRef;
  /** Scrolls the doc to this source's first attributed span. */
  onActivate?: () => void;
}

function SourceCard({ source, onActivate }: SourceCardProps) {
  const Icon = sourceIcon(source.source_type);
  const url = source.source_url;
  return (
    <div
      className="group/source w-full shrink-0 rounded-(--radius-12) p-1 hover:bg-(--background-tint-00)"
      onClick={onActivate}
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
export function SourcesPanel({ sources, onActivateSource }: Props) {
  const [query, setQuery] = useState("");

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

  return (
    <Section
      justifyContent="start"
      alignItems="stretch"
      height="auto"
      gap={0}
      padding={0.25}
      className="min-h-0 flex-1 overflow-clip rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-01)"
    >
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
      </Section>
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
              onActivate={
                key && onActivateSource
                  ? () => onActivateSource(key)
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
